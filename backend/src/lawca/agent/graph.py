"""채팅 요청을 처리하는 LangGraph 그래프.

    START → route ─┬→ document      (PDF 첨부: 규칙으로 보냄)
                   ├→ query         (조회 에이전트)
                   ├→ draft_parse → draft_ask ⟲ → draft_render   (서식 작성, 되묻기)
                   ├→ out_of_scope  (법률 자문: 고정 응답)
                   └→ help          (기능 안내)
    각 작업이 끝나면 다음 작업으로 가고, 작업이 없으면 끝난다.

- 노드는 get_stream_writer()로 화면 이벤트(status·text·card)를 흘려보낸다.
- DB 세션, 모델 등 요청마다 달라지는 것은 config["configurable"]["deps"]로 받는다(체크포인트에 저장되지 않음).
- 되묻기는 interrupt로 멈추고, 체크포인터(thread_id = Job id)로 이어간다. 그래서 상태는 직렬화할 수 있는 값만 둔다.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import date
from functools import cache
from typing import Any, TypedDict

from langchain_core.runnables import RunnableConfig
from langgraph.config import get_stream_writer
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt
from sqlalchemy import select
from sqlalchemy.orm import Session

from lawca.agent import brief_flow
from lawca.agent import draft as drafting
from lawca.agent.brief import summarize_brief, summary_out
from lawca.agent.documents import analyze, summarize
from lawca.agent.llm import ChatModel, Turn, with_fallback
from lawca.agent.query_agent import run_query
from lawca.agent.router import route_text
from lawca.api.schemas import DocumentOut
from lawca.db import repo
from lawca.lawapi import LawApiError
from lawca.library import store as library_store
from lawca.citations import find as find_citations
from lawca.db.models import File, Party
from lawca.extraction.gemini import ExtractionError, Extractor, ModelUnavailableError
from lawca.extraction.schema import PARTY_FILINGS
from lawca.extraction.validate import has_text, pdf_text_pages

log = logging.getLogger(__name__)
GRAPH_VERSION = "2"
"""노드 구성을 바꾸면 올린다. 멈춰 있던 작업의 체크포인트가 새 그래프와 맞는지 판단하는 데 쓴다."""


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
    job_id: Any = None
    role: str = "clerk"
    """요청한 사용자의 역할(clerk|lawyer). 준비서면 본문 초안은 변호사만 받는다."""
    make_embedder: Callable[[], Any] = lambda: None
    """자료실 색인·검색에 쓰는 임베딩 모델(없으면 키워드만)."""
    make_law_api: Callable[[], Any] = lambda: None
    """국가법령정보센터 클라이언트(키가 없으면 None). 판례 검색에 쓴다."""


class ChatState(TypedDict, total=False):
    message: str
    file_ids: list[str]
    history: list[dict[str, str]]
    """[{"role": "user" | "model", "text": ...}]"""
    tasks: list[dict[str, str]]
    index: int
    draft: dict[str, Any] | None


HELP_TEXT = (
    "제가 도와드릴 수 있는 일입니다.\n\n"
    "- **법원 문서 처리**: 보정명령, 판결문, 결정문, 지급명령 PDF를 첨부하면 내용을 읽고, 송달일을 받아 기한을 계산하고, 할 일을 정리합니다.\n"
    "- **기한 조회**: \"이번 주 기한 알려줘\", \"2026가단51234 기한은?\"\n"
    "- **사건 찾기**: \"홍길동 사건 찾아줘\"\n"
    "- **만료일 계산**: \"9월 15일에 판결문을 받았으면 항소기한은?\"\n"
    "- **서식 작성**: 확정증명원 신청서, 송달증명원 신청서, 주소보정서, 사실조회신청서, 집행문부여 신청서. 예: \"2026가단51234 확정증명원 신청서 만들어 줘\"\n"
    "- **판례 검색(변호사)**: \"채무 승인 소멸시효 중단 판례 찾아줘\"라고 하면 국가법령정보센터에서 대법원 판례 후보를 찾습니다.\n"
    "- **준비서면 작성**: \"준비서면 작성해줘\"라고 하면 사건을 묻고, 변호사 메모와 자료실의 과거 서면으로 본문 초안을 씁니다(본문은 변호사만 받습니다)."
)

OUT_OF_SCOPE_TEXT = (
    "승소 가능성이나 불복 여부 같은 **법률 판단은 드릴 수 없습니다.** 담당 변호사님께 문의해 주세요.\n\n"
    "대신 관련 기한과 사건 기록을 조회해 드릴 수 있습니다."
)


def _deps(config: RunnableConfig) -> Deps:
    return config["configurable"]["deps"]


def _history(state: ChatState) -> list[Turn]:
    return [Turn(h["role"], h["text"]) for h in state.get("history", [])]  # type: ignore[arg-type]


def _separator(state: ChatState) -> str:
    return "\n\n" if state["index"] > 0 else ""


def _task(state: ChatState) -> dict[str, str]:
    return state["tasks"][state["index"]]


def route(state: ChatState, config: RunnableConfig) -> dict[str, Any]:
    write = get_stream_writer()
    if state["file_ids"]:
        return {"tasks": [{"label": "document", "request": state["message"]}], "index": 0}
    if not state["message"].strip():
        return {"tasks": [{"label": "help", "request": ""}], "index": 0}

    write({"type": "status", "id": "route", "label": "요청 파악 중", "state": "running"})
    try:
        models = _deps(config).make_models()
        tasks = with_fallback(models, lambda m: route_text(m, state["message"], _history(state)))
    except ModelsNotConfigured as exc:
        write({"type": "status", "id": "route", "label": "요청 파악 실패", "state": "error"})
        write({"type": "text", "delta": f"글 요청을 처리할 수 없습니다. {exc}"})
        return {"tasks": [], "index": 0}
    except (ModelUnavailableError, ExtractionError) as exc:
        write({"type": "status", "id": "route", "label": "요청 파악 실패", "state": "error"})
        write({"type": "text", "delta": f"요청을 처리하지 못했습니다. {exc} 잠시 뒤 다시 시도해 주세요."})
        return {"tasks": [], "index": 0}
    labels = {"query": "조회", "precedent": "판례 검색", "draft": "서식 작성", "out_of_scope": "법률 판단 요청", "help": "안내"}
    summary = ", ".join(labels[t.label] for t in tasks)
    write({"type": "status", "id": "route", "label": f"요청 파악: {summary}", "state": "done"})
    return {"tasks": [t.model_dump() for t in tasks], "index": 0}


NODE_FOR_LABEL = {"draft": "draft_parse"}


def next_task(state: ChatState) -> str:
    if state["index"] >= len(state["tasks"]):
        return END
    label = _task(state)["label"]
    return NODE_FOR_LABEL.get(label, label)


def _index(deps: Deps, add: Callable[[Any], Any]) -> None:
    """자료실에 넣는다. 실패해도 채팅 흐름은 멈추지 않는다(검색에서만 빠진다)."""
    try:
        add(deps.make_embedder())
        deps.session.commit()
    except Exception:  # noqa: BLE001
        deps.session.rollback()
        log.exception("자료실 색인 실패")


def _references(deps: Deps, draft: dict[str, Any], exclude: str | None = None) -> list[dict[str, Any]]:
    """같은 서식의 과거 서면을 자료실에서 찾는다. 찾지 못하거나 실패하면 빈 목록(초안 작성은 계속한다)."""
    form = drafting.load_forms().get(draft.get("form_id") or "")
    if form is None:
        return []
    try:
        # 준비서면은 본문을 쓸 때 검토 전 초안을 참고하지 않는다. 미리 보여 주는 목록도 같은 기준으로 맞춘다.
        return library_store.references(
            deps.session, form.name, deps.make_embedder(), exclude_draft_id=exclude, reviewed_only=form.id == "brief"
        )
    except Exception:  # noqa: BLE001
        deps.session.rollback()
        log.exception("참고 서면 검색 실패")
        return []


def _existing_note(session: Session, document_id: str | None, result: DocumentOut) -> str:
    """사건번호로 DB를 찾아, 같은 종류로 이미 진행 중인 기한이 있으면 알린다(새로 확정할 수 없다)."""
    if not document_id or not result.suggestions:
        return ""
    document = repo.get_document(session, document_id)
    if document is None:
        return ""
    lines = []
    for key in dict.fromkeys(s.key for s in result.suggestions):
        existing = repo.open_conflict(session, key=key, case_id=document.case_id, document_id=document.id)
        if existing is not None:
            lines.append(
                f"\n이 사건에는 이미 진행 중인 **{existing.label}**(만료 {existing.deadline.isoformat()}, "
                f"확정 {existing.confirmed_by})이 있어 새로 확정하지 않습니다. 고치려면 기한 목록에서 수정하세요."
            )
    return "".join(lines)


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
            _index(deps, lambda embedder: library_store.index_court_document(deps.session, document_id, embedder))
        write({"type": "status", "id": step, "label": f"{stored.name} 읽음 · {result.model}", "state": "done"})
        write({"type": "text", "delta": prefix + summarize(result) + _existing_note(deps.session, document_id, result)})
        write({"type": "card", "card": {"kind": "document", **result.model_dump(mode="json")}})
        if result.extraction.document_type in PARTY_FILINGS and document_id:
            _summarize_filing(deps, write, stored, document_id, result, prefix=step)
    return {"index": state["index"] + 1}


def _summarize_filing(deps: Deps, write: Callable[[dict[str, Any]], None], stored: File, document_id: str,
                      result: DocumentOut, prefix: str) -> None:
    """상대방 답변서·준비서면을 요약한다. 글이 없는 스캔본이나 모델 오류면 알리고 넘어간다."""
    doc = result.extraction
    step = f"{prefix}-summary"
    pages = pdf_text_pages(stored.data)
    if not has_text(pages):
        write({"type": "text", "delta": "\n\n스캔본이라 서면 내용을 요약하지 못했습니다(글자를 읽을 수 있는 PDF가 필요합니다)."})
        return
    write({"type": "status", "id": step, "label": f"{doc.document_type.value} 요약 중", "state": "running"})
    try:
        summary = summary_out(summarize_brief(deps.make_models(), pages), pages)
    except (ModelUnavailableError, ExtractionError, ModelsNotConfigured) as exc:
        write({"type": "status", "id": step, "label": "요약 실패", "state": "error"})
        write({"type": "text", "delta": f"\n\n서면을 요약하지 못했습니다. {exc}"})
        return
    document = repo.get_document(deps.session, document_id)
    if document is not None:
        repo.set_summary(deps.session, document, summary)
        deps.session.commit()
    write({"type": "status", "id": step, "label": f"{doc.document_type.value} 요약 완료", "state": "done"})
    unverified = sum(1 for c in summary["claims"] if not c["verified"])
    note = f" 원문에서 문구를 찾지 못한 주장이 {unverified}건 있으니 원문과 대조하세요." if unverified else ""
    write({"type": "text", "delta": f"\n\n상대방 주장 {len(summary['claims'])}건, 증거 {len(summary['evidence'])}건을 정리했습니다.{note}"})
    write(
        {
            "type": "card",
            "card": {
                "kind": "brief_summary",
                "document_id": document_id,
                "document_type": doc.document_type.value,
                "file_id": result.file_id,
                "filename": result.filename,
                "case_number": doc.case_number.value if doc.case_number else None,
                **summary,
            },
        }
    )


def query(state: ChatState, config: RunnableConfig) -> dict[str, Any]:
    write = get_stream_writer()
    deps = _deps(config)
    if state["index"] > 0:
        write({"type": "text", "delta": _separator(state)})
    try:
        run_query(
            deps.make_models(),
            _task(state)["request"] or state["message"],
            _history(state),
            deps.session,
            deps.today,
            write,
            step_prefix=f"q{state['index']}",
            embedder=deps.make_embedder(),
        )
    except (ModelUnavailableError, ExtractionError, ModelsNotConfigured) as exc:
        write({"type": "text", "delta": f"조회하지 못했습니다. {exc} 잠시 뒤 다시 시도해 주세요."})
    return {"index": state["index"] + 1}


# 판례 검색(변호사)


def precedent(state: ChatState, config: RunnableConfig) -> dict[str, Any]:
    """법률 용어만 남겨 국가법령정보센터에서 대법원 판례 후보를 찾는다. 변호사만 쓴다."""
    write = get_stream_writer()
    deps = _deps(config)
    sep = _separator(state)
    if deps.role != "lawyer":
        write({"type": "text", "delta": sep + "판례 검색은 변호사 계정에서만 할 수 있습니다. 담당 변호사님께 요청해 주세요."})
        return {"index": state["index"] + 1}
    api = deps.make_law_api()
    if api is None:
        write({"type": "text", "delta": sep + "국가법령정보센터 키가 없어 판례를 찾을 수 없습니다. 레포 루트 .env에 LAW_API를 넣어 주세요."})
        return {"index": state["index"] + 1}
    issue = _task(state)["request"] or state["message"]
    step = f"precedent-{state['index']}"
    # 어느 사건의 당사자 이름이든 밖으로 나가는 검색어에 들어가지 않게 한다
    names = list(deps.session.scalars(select(Party.name).distinct()))
    write({"type": "status", "id": step, "label": "대법원 판례 찾는 중", "state": "running"})
    try:
        found = find_citations(api, issue, [], names, deps.make_embedder())
    except LawApiError as exc:
        write({"type": "status", "id": step, "label": "판례 검색 실패", "state": "error"})
        write({"type": "text", "delta": sep + f"판례를 찾지 못했습니다. {exc}"})
        return {"index": state["index"] + 1}
    repo.audit(deps.session, "precedent.search", "chat", deps.job_id or "-", {"query": found.query, "results": len(found.items)})
    deps.session.commit()
    write({"type": "status", "id": step, "label": f"판례 후보 {len(found.items)}건", "state": "done"})
    if found.items:
        text = (
            f"국가법령정보센터에서 **{found.query}**(으)로 찾은 대법원 판례 후보 {len(found.items)}건입니다. "
            "뜻이 가까운 순이며, **원문을 확인한 뒤 인용하세요.**"
        )
    else:
        text = f"**{found.query}**(으)로 관련 대법원 판례를 찾지 못했습니다. 쟁점을 법률 용어로 바꿔 다시 요청해 보세요."
    write({"type": "text", "delta": sep + text})
    if found.items:
        write({"type": "card", "card": {"kind": "precedents", "query": found.query, "items": found.items, "statutes": found.statutes}})
    return {"index": state["index"] + 1}


# 서식 작성


def draft_parse(state: ChatState, config: RunnableConfig) -> dict[str, Any]:
    write = get_stream_writer()
    deps = _deps(config)
    step = f"draft-{state['index']}"
    write({"type": "status", "id": step, "label": "서식 요청 파악 중", "state": "running"})
    try:
        models = deps.make_models()
    except ModelsNotConfigured:
        models = []  # 모델 없이도 서식 이름·사건번호는 규칙으로 찾는다
    request = _task(state)["request"] or state["message"]
    draft = drafting.parse_request(models, request, _history(state), deps.session, deps.today)
    draft["role"] = deps.role
    form = drafting.load_forms().get(draft.get("form_id") or "")
    label = f"서식 요청 파악: {form.name if form else '서식 미정'}" + (f" · {draft['case_number']}" if draft.get("case_number") else "")
    write({"type": "status", "id": step, "label": label, "state": "done"})
    return {"draft": draft}


def draft_ask(state: ChatState, config: RunnableConfig) -> dict[str, Any]:
    """물어볼 것이 있으면 멈추고, 답을 받으면 반영한다. 재개하면 이 노드가 처음부터 다시 실행된다."""
    deps = _deps(config)
    draft = state["draft"] or {}
    question = drafting.build_question(draft, deps.session)
    if question is None:
        return {}
    if question["stage"] == "fields":
        # 항목을 채우는 동안 참고할 과거 서면(같은 서식). 되묻는 질문에 함께 싣는다.
        question = {**question, "references": _references(deps, draft)}
    answers = interrupt(question)
    return {"draft": drafting.apply_answers(draft, question, answers or {}, deps.session, deps.today)}


def after_ask(state: ChatState, config: RunnableConfig) -> str:
    return "draft_ask" if drafting.build_question(state["draft"] or {}, _deps(config).session) else "draft_render"


def _render_brief(state: ChatState, config: RunnableConfig) -> dict[str, Any]:
    """준비서면: 자료실에서 비슷한 과거 문단을 찾고(RAG), 변호사 메모를 풀어 본문 초안을 쓰고, DOCX 틀로 저장한다."""
    write = get_stream_writer()
    deps = _deps(config)
    draft = state["draft"] or {}
    values = draft["values"]
    case = repo.get_case(deps.session, draft["case_number"]) if draft.get("case_number") else None
    step = f"render-{state['index']}"
    lawyer = deps.role == "lawyer" and bool(values.get("notes", "").strip())
    brief = None
    model = None
    note = None
    if lawyer:
        write({"type": "status", "id": f"{step}-rag", "label": "자료실에서 비슷한 과거 문단 찾는 중", "state": "running"})
        prepared = brief_flow.prepare(deps.session, case, values, deps.make_embedder())
        write({"type": "status", "id": f"{step}-rag", "label": f"자료실에서 참고 문단 {len(prepared.hits)}건 찾음", "state": "done"})
        write({"type": "status", "id": f"{step}-write", "label": "본문 초안 쓰는 중", "state": "running"})
        try:
            models = deps.make_models()
            brief = brief_flow.write(models, prepared)
            model = getattr(models[0], "model", None)
            if brief_flow.unreadable_notes(values["notes"], brief):
                brief = None
                note = "메모를 서면 주장으로 읽지 못해 본문은 빈칸으로 두었습니다. 메모를 '주장: 근거(증거)' 꼴로 구체적으로 적어 다시 요청해 주세요."
                write({"type": "status", "id": f"{step}-write", "label": "메모를 읽지 못해 본문 생략", "state": "error"})
            else:
                write({"type": "status", "id": f"{step}-write", "label": f"본문 초안 완성 · {model}", "state": "done"})
        except (ModelsNotConfigured, ModelUnavailableError, ExtractionError) as exc:
            note = f"본문 초안을 만들지 못해 본문은 빈칸으로 두었습니다. {exc}"
            write({"type": "status", "id": f"{step}-write", "label": "본문 초안 실패", "state": "error"})
    else:
        prepared = brief_flow.prepare(deps.session, case, {**values, "notes": ""}, None)
        note = "본문 초안은 변호사만 받을 수 있어 본문은 빈칸으로 두었습니다. 변호사에게 요청하거나 워드에서 직접 쓰세요."
    write({"type": "status", "id": step, "label": "초안 저장 중", "state": "running"})
    card = brief_flow.finish(deps.session, prepared, brief, model=model, note=note, job_id=deps.job_id)
    _index(deps, lambda embedder: library_store.index_draft(deps.session, card["draft_id"], embedder))
    write({"type": "status", "id": step, "label": f"초안 완성: {card['filename']}", "state": "done"})

    lines = [f"{_separator(state)}**준비서면** 초안을 만들었습니다."]
    if brief is not None:
        notes = len(brief_flow.note_lines(values.get("notes", "")))
        if prepared.hits:
            lines.append(f"메모 {notes}줄을 자료실의 참고 문단 {len(prepared.hits)}건과 함께 풀어 본문 초안을 썼습니다.")
        else:
            lines.append(
                f"자료실에 비슷한 과거 문단이 없어 **참고 없이** 메모 {notes}줄과 상대방 서면 요약만으로 본문 초안을 썼습니다. "
                "과거 준비서면을 자료실에 올리거나, 초안을 검토 완료로 표시하거나 최종본을 올리면 다음부터 참고합니다."
            )
        flags = brief_flow.check_summary(card["brief"]["checks"])
        lines.append(f"확인할 것: {flags}." if flags else "자동 점검에서 걸린 것은 없습니다. 표현과 법리는 직접 검토하세요.")
    if note:
        lines.append(note)
    lines.append("근거와 점검 결과, 판례 후보는 **초안** 탭에서 볼 수 있습니다. 담당 변호사 검토 후 제출하세요.")
    text = lines[0] + "\n\n" + "\n\n".join(lines[1:]) + brief_flow.reference_list_text(card["references"])
    write({"type": "text", "delta": text})
    write({"type": "card", "card": card})
    return {"index": state["index"] + 1, "draft": None}


def draft_render(state: ChatState, config: RunnableConfig) -> dict[str, Any]:
    if (state["draft"] or {}).get("form_id") == "brief":
        return _render_brief(state, config)
    write = get_stream_writer()
    deps = _deps(config)
    step = f"render-{state['index']}"
    write({"type": "status", "id": step, "label": "초안 만드는 중", "state": "running"})
    card = drafting.save(state["draft"] or {}, deps.session, deps.job_id)
    _index(deps, lambda embedder: library_store.index_draft(deps.session, card["draft_id"], embedder))
    card = {**card, "references": _references(deps, state["draft"] or {}, exclude=card["draft_id"])}
    write({"type": "status", "id": step, "label": f"초안 완성: {card['filename']}", "state": "done"})
    note = f" 빈칸으로 둔 항목: {', '.join(card['blanks'])}." if card["blanks"] else ""
    write(
        {
            "type": "text",
            "delta": f"{_separator(state)}**{card['form_name']}** 초안을 만들었습니다.{note} "
            "내려받아 내용을 확인하고, 담당 변호사 검토 후 제출하세요.",
        }
    )
    write({"type": "card", "card": card})
    return {"index": state["index"] + 1, "draft": None}


def _fixed(text: str) -> Callable[[ChatState], dict[str, Any]]:
    def node(state: ChatState) -> dict[str, Any]:
        get_stream_writer()({"type": "text", "delta": _separator(state) + text})
        return {"index": state["index"] + 1}

    return node


def build_graph(checkpointer: Any = None):  # noqa: ANN201
    graph = StateGraph(ChatState)
    graph.add_node("route", route)
    graph.add_node("document", document)
    graph.add_node("query", query)
    graph.add_node("precedent", precedent)
    graph.add_node("draft_parse", draft_parse)
    graph.add_node("draft_ask", draft_ask)
    graph.add_node("draft_render", draft_render)
    graph.add_node("out_of_scope", _fixed(OUT_OF_SCOPE_TEXT))
    graph.add_node("help", _fixed(HELP_TEXT))
    routes = ["document", "query", "precedent", "draft_parse", "out_of_scope", "help", END]
    graph.add_edge(START, "route")
    graph.add_conditional_edges("route", next_task, routes)
    for name in ("document", "query", "precedent", "draft_render", "out_of_scope", "help"):
        graph.add_conditional_edges(name, next_task, routes)
    graph.add_edge("draft_parse", "draft_ask")
    graph.add_conditional_edges("draft_ask", after_ask, ["draft_ask", "draft_render"])
    return graph.compile(checkpointer=checkpointer)


@cache
def graph_for(checkpointer: Any):  # noqa: ANN201
    return build_graph(checkpointer)


def run(graph: Any, payload: Any, deps: Deps, thread_id: str) -> Iterator[dict[str, Any]]:
    """그래프를 실행하며 화면 이벤트를 흘려보낸다. 되묻기로 멈추면 {"type": "question"} 이벤트를 낸다."""
    config = {"configurable": {"deps": deps, "thread_id": thread_id}}
    for mode, chunk in graph.stream(payload, config, stream_mode=["custom", "updates"]):
        if mode == "custom":
            yield chunk
        elif isinstance(chunk, dict) and "__interrupt__" in chunk:
            for item in chunk["__interrupt__"]:
                # question_id로 같은 작업의 이전 질문과 새 질문(예: 잘못 답해 다시 물음)을 구분한다.
                yield {"type": "question", "question": {**item.value, "question_id": item.id}}


def start_input(message: str, file_ids: list[str], history: list[Turn]) -> ChatState:
    return {
        "message": message,
        "file_ids": file_ids,
        "history": [{"role": t.role, "text": t.text} for t in history],
        "tasks": [],
        "index": 0,
        "draft": None,
    }


def resume_input(answers: dict[str, str]) -> Command:
    return Command(resume=answers)
