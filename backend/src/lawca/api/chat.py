"""채팅 응답 생성. 요청을 LangGraph 그래프(lawca.agent.graph)에 넘기고 그래프가 흘려보내는 이벤트를 그대로 전달한다.

이벤트(SSE의 data 한 줄이 이벤트 하나)
- status: 진행 단계. {"type": "status", "id", "label", "state": "running" | "done" | "error"}
- text: 응답 글의 조각. {"type": "text", "delta"}
- card: 결과 카드. {"type": "card", "card": {"kind": "document" | "deadlines" | "cases" | "deadline_calc", ...}}
- done: 응답 끝
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

from lawca.agent.graph import GRAPH, Deps
from lawca.agent.llm import Turn
from lawca.api.schemas import ChatRequest

Event = dict[str, Any]


def chat_events(req: ChatRequest, deps: Deps, history: list[Turn]) -> Iterator[Event]:
    if any(deps.get_file(fid) is None for fid in req.file_ids):
        yield {"type": "text", "delta": "첨부 파일을 찾지 못했습니다. 서버가 다시 시작되었다면 파일을 다시 올려 주세요."}
        yield {"type": "done"}
        return

    state = {"message": req.message, "file_ids": req.file_ids, "history": history, "tasks": [], "index": 0}
    for event in GRAPH.stream(state, {"configurable": {"deps": deps}}, stream_mode="custom"):
        yield event
    yield {"type": "done"}
