"""채팅 응답 생성. 요청을 LangGraph 그래프(lawca.agent.graph)에 넘기고 그래프가 흘려보내는 이벤트를 그대로 전달한다.

이벤트(SSE의 data 한 줄이 이벤트 하나)
- status: 진행 단계. {"type": "status", "id", "label", "state": "running" | "done" | "error"}
- text: 응답 글의 조각. {"type": "text", "delta"}
- card: 결과 카드. {"type": "card", "card": {"kind": "document" | "deadlines" | "cases" | "deadline_calc" | "draft" | "question", ...}}
- question: 되묻기로 멈춤(API가 question 카드로 바꿔 보낸다)
- done: 응답 끝
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

from lawca.agent.graph import Deps, resume_input, run, start_input
from lawca.agent.llm import Turn
from lawca.api.schemas import ChatRequest

Event = dict[str, Any]


def chat_events(req: ChatRequest, deps: Deps, history: list[Turn], graph: Any, thread_id: str) -> Iterator[Event]:
    if any(deps.get_file(fid) is None for fid in req.file_ids):
        yield {"type": "text", "delta": "첨부 파일을 찾지 못했습니다. 서버가 다시 시작되었다면 파일을 다시 올려 주세요."}
        yield {"type": "done"}
        return
    yield from run(graph, start_input(req.message, req.file_ids, history), deps, thread_id)
    yield {"type": "done"}


def resume_events(answers: dict[str, str], deps: Deps, graph: Any, thread_id: str) -> Iterator[Event]:
    yield from run(graph, resume_input(answers), deps, thread_id)
    yield {"type": "done"}


def answer_summary(question: dict[str, Any], answers: dict[str, str]) -> str:
    """되묻기 답을 대화에 남길 사용자 메시지로 만든다."""
    lines = []
    for field in question.get("fields", []):
        value = answers.get(field["key"], "")
        if value == "__later__":
            value = "나중에 입력"
        if value:
            lines.append(f"{field['label']}: {value}")
    return "\n".join(lines) or "(입력 없음)"
