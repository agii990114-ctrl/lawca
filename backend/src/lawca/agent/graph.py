"""채팅 요청을 처리하는 LangGraph 그래프.

    START → route ─┬→ document     (PDF 첨부: 규칙으로 보냄)
                   ├→ query        (조회 에이전트)
                   ├→ draft        (서식 작성: 다음 단계에서 구현)
                   ├→ out_of_scope (법률 자문: 고정 응답)
                   └→ help         (기능 안내)
    각 작업이 끝나면 다음 작업으로 가고, 작업이 없으면 끝난다.

노드는 get_stream_writer()로 화면 이벤트(status·text·card)를 흘려보낸다.
DB 세션, 모델 등 요청마다 달라지는 것은 config["configurable"]["deps"]로 받는다.
되묻기(interrupt)와 체크포인터는 서식 작성 단계에서 붙인다.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from typing import Any, TypedDict

from langchain_core.runnables import RunnableConfig
from langgraph.config import get_stream_writer
from langgraph.graph import END, START, StateGraph
from sqlalchemy.orm import Session

from lawca.agent.documents import analyze, summarize
from lawca.agent.llm import ChatModel, Turn, with_fallback
from lawca.agent.query_agent import run_query
from lawca.agent.router import route_text
from lawca.api.schemas import DocumentOut
from lawca.db.models import File
from lawca.extraction.gemini import ExtractionError, Extractor, ModelUnavailableError


class ModelsNotConfigured(RuntimeError):
    """LLM을 쓸 수 없을 때(예: API 키 없음)."""


@dataclass
class Deps:
    session: Session
    today: date
    get_file: Callable[[str], File | None]
    make_extractor: Callable[[], Extractor]
    on_document: Callable[[DocumentOut], str | None]
    make_models: Callable[[], list[ChatModel]]


class ChatState(TypedDict):
    message: str
    file_ids: list[str]
    history: list[Turn]
    tasks: list[dict[str, str]]
    index: int


HELP_TEXT = (
    "제가 도와드릴 수 있는 일입니다.\n\n"
    "- **법원 문서 처리**: 보정명령, 판결문, 결정문, 지급명령 PDF를 첨부하면 내용을 읽고, 송달일을 받아 기한을 계산하고, 할 일을 정리합니다.\n"
    "- **기한 조회**: \"이번 주 기한 알려줘\", \"2026가단51234 기한은?\"\n"
    "- **사건 찾기**: \"홍길동 사건 찾아줘\"\n"
    "- **만료일 계산**: \"9월 15일에 판결문을 받았으면 항소기한은?\"\n\n"
    "서식 작성은 준비 중입니다."
)

OUT_OF_SCOPE_TEXT = (
    "승소 가능성이나 불복 여부 같은 **법률 판단은 드릴 수 없습니다.** 담당 변호사님께 문의해 주세요.\n\n"
    "대신 관련 기한과 사건 기록을 조회해 드릴 수 있습니다."
)

DRAFT_TEXT = (
    "서식 작성은 아직 준비 중입니다. 다음 단계에서 필요한 정보를 되물어 가며 초안을 만들어 드리도록 할 예정입니다.\n\n"
    "지금은 법원 문서를 첨부해 기한을 정리하거나, 기한과 사건을 조회할 수 있습니다."
)


def _deps(config: RunnableConfig) -> Deps:
    return config["configurable"]["deps"]


def _separator(state: ChatState) -> str:
    return "\n\n" if state["index"] > 0 else ""


def route(state: ChatState, config: RunnableConfig) -> dict[str, Any]:
    write = get_stream_writer()
    if state["file_ids"]:
        return {"tasks": [{"label": "document", "request": state["message"]}], "index": 0}
    if not state["message"].strip():
        return {"tasks": [{"label": "help", "request": ""}], "index": 0}

    write({"type": "status", "id": "route", "label": "요청 파악 중", "state": "running"})
    try:
        models = _deps(config).make_models()
        tasks = with_fallback(models, lambda m: route_text(m, state["message"], state["history"]))
    except ModelsNotConfigured as exc:
        write({"type": "status", "id": "route", "label": "요청 파악 실패", "state": "error"})
        write({"type": "text", "delta": f"글 요청을 처리할 수 없습니다. {exc}"})
        return {"tasks": [], "index": 0}
    except (ModelUnavailableError, ExtractionError) as exc:
        write({"type": "status", "id": "route", "label": "요청 파악 실패", "state": "error"})
        write({"type": "text", "delta": f"요청을 처리하지 못했습니다. {exc} 잠시 뒤 다시 시도해 주세요."})
        return {"tasks": [], "index": 0}
    labels = {"query": "조회", "draft": "서식 작성", "out_of_scope": "법률 판단 요청", "help": "안내"}
    summary = ", ".join(labels[t.label] for t in tasks)
    write({"type": "status", "id": "route", "label": f"요청 파악: {summary}", "state": "done"})
    return {"tasks": [t.model_dump() for t in tasks], "index": 0}


def next_task(state: ChatState) -> str:
    if state["index"] >= len(state["tasks"]):
        return END
    return state["tasks"][state["index"]]["label"]


def document(state: ChatState, config: RunnableConfig) -> dict[str, Any]:
    write = get_stream_writer()
    deps = _deps(config)
    try:
        extractor = deps.make_extractor()
    except Exception as exc:  # noqa: BLE001 - 키 미설정 등은 사용자에게 그대로 알린다
        write({"type": "text", "delta": f"문서를 읽을 수 없습니다. {getattr(exc, 'detail', exc)}"})
        return {"index": state["index"] + 1}

    files = [deps.get_file(fid) for fid in state["file_ids"]]
    for index, stored in enumerate(f for f in files if f is not None):
        step = f"read-{stored.id}"
        prefix = "\n\n" if index else ""
        write({"type": "status", "id": step, "label": f"{stored.name} 읽는 중", "state": "running"})
        try:
            result = analyze(stored, extractor, deps.today)
        except (ModelUnavailableError, ExtractionError) as exc:
            write({"type": "status", "id": step, "label": f"{stored.name} 읽기 실패", "state": "error"})
            write({"type": "text", "delta": f"{prefix}**{stored.name}**을(를) 읽지 못했습니다. {exc}"})
            continue
        document_id = deps.on_document(result)
        if document_id:
            result = result.model_copy(update={"document_id": document_id})
        write({"type": "status", "id": step, "label": f"{stored.name} 읽음 · {result.model}", "state": "done"})
        write({"type": "text", "delta": prefix + summarize(result)})
        write({"type": "card", "card": {"kind": "document", **result.model_dump(mode="json")}})
    return {"index": state["index"] + 1}


def query(state: ChatState, config: RunnableConfig) -> dict[str, Any]:
    write = get_stream_writer()
    deps = _deps(config)
    task = state["tasks"][state["index"]]
    if state["index"] > 0:
        write({"type": "text", "delta": _separator(state)})
    try:
        run_query(
            deps.make_models(),
            task["request"] or state["message"],
            state["history"],
            deps.session,
            deps.today,
            write,
            step_prefix=f"q{state['index']}",
        )
    except (ModelUnavailableError, ExtractionError) as exc:
        write({"type": "text", "delta": f"조회하지 못했습니다. {exc} 잠시 뒤 다시 시도해 주세요."})
    return {"index": state["index"] + 1}


def _fixed(text: str) -> Callable[[ChatState], dict[str, Any]]:
    def node(state: ChatState) -> dict[str, Any]:
        get_stream_writer()({"type": "text", "delta": _separator(state) + text})
        return {"index": state["index"] + 1}

    return node


def build_graph():  # noqa: ANN201
    graph = StateGraph(ChatState)
    graph.add_node("route", route)
    graph.add_node("document", document)
    graph.add_node("query", query)
    graph.add_node("draft", _fixed(DRAFT_TEXT))
    graph.add_node("out_of_scope", _fixed(OUT_OF_SCOPE_TEXT))
    graph.add_node("help", _fixed(HELP_TEXT))
    routes = ["document", "query", "draft", "out_of_scope", "help", END]
    graph.add_edge(START, "route")
    graph.add_conditional_edges("route", next_task, routes)
    for name in ("document", "query", "draft", "out_of_scope", "help"):
        graph.add_conditional_edges(name, next_task, routes)
    return graph.compile()


GRAPH = build_graph()
