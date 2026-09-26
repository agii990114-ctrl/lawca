"""조회 에이전트: 도구 호출 루프로 기한·사건을 조회하고 답을 스트리밍한다."""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import date
from typing import Any

from sqlalchemy.orm import Session

from lawca.agent.llm import ChatModel, TextDelta, ToolCall, Turn, TurnEnd, all_unavailable
from lawca.agent.tools import TOOLS, run_tool
from lawca.extraction.gemini import ModelUnavailableError

Emit = Callable[[dict[str, Any]], None]
MAX_STEPS = 6
WEEKDAYS = "월화수목금토일"

SYSTEM = """\
당신은 한국 법무법인 사무원을 돕는 lawca입니다. 오늘은 {today}({weekday}요일)입니다.

- 기한·사건·문서 질문은 반드시 도구로 조회해서 답합니다. 기억이나 추측으로 답하지 않습니다.
- 날짜를 직접 계산하지 않습니다. "이번 주"처럼 기간을 말하면 list_deadlines의 range로 고르고,
  만료일 계산은 compute_deadline을 씁니다.
- 조회 결과는 화면에 카드로 함께 표시됩니다. 답변은 핵심만 짧게 한국어로 씁니다. 날짜를 말할 때는 도구 결과의 값을 그대로 씁니다.
- 결과가 없으면 없다고 말하고, 기한은 문서 카드에서 확정해야 목록에 나온다고 안내합니다.
- compute_deadline 결과는 저장되지 않은 계산값이라고 알립니다.
- 사건을 특정할 수 없으면 추측하지 말고 어떤 사건인지 되묻습니다.
- 법률 판단(승소 가능성, 불복 여부 등)은 하지 않습니다.
"""


def run_query(
    models: list[ChatModel],
    message: str,
    history: list[Turn],
    session: Session,
    today: date,
    emit: Emit,
    step_prefix: str = "q",
) -> str:
    """조회를 실행하고 실제로 쓴 모델 이름을 돌려준다.

    모델 서버가 혼잡하면 다음 모델로 처음부터 다시 실행한다(도구는 읽기 전용이라 안전하다).
    답을 쓰기 시작한 뒤에 혼잡해지면 다시 실행하지 않고 오류를 알린다.
    """
    system = SYSTEM.format(today=today.isoformat(), weekday=WEEKDAYS[today.weekday()])
    emitted_cards: set[str] = set()
    wrote_text = False

    def attempt(model: ChatModel) -> None:
        nonlocal wrote_text
        turns = [*history, Turn("user", message)]
        for step in range(MAX_STEPS):
            calls: list[ToolCall] = []
            raw: Any = None
            for chunk in model.stream(system, turns, TOOLS):
                if isinstance(chunk, ToolCall):
                    calls.append(chunk)
                elif isinstance(chunk, TextDelta):
                    wrote_text = True
                    emit({"type": "text", "delta": chunk.text})
                elif isinstance(chunk, TurnEnd):
                    raw = chunk.raw
            turns.append(Turn("model", raw=raw))
            if not calls:
                return
            results = []
            for index, call in enumerate(calls):
                step_id = f"{step_prefix}-{step}-{index}"
                emit({"type": "status", "id": step_id, "label": "조회 중", "state": "running"})
                outcome = run_tool(call.name, call.args, session, today)
                state = "error" if "error" in outcome.result else "done"
                emit({"type": "status", "id": step_id, "label": outcome.label, "state": state})
                key = json.dumps([call.name, call.args], sort_keys=True, ensure_ascii=False)
                if outcome.card is not None and key not in emitted_cards:
                    emitted_cards.add(key)
                    emit({"type": "card", "card": outcome.card})
                results.append((call.name, outcome.result))
            turns.append(Turn("tool", tool_results=results))
        emit({"type": "text", "delta": "\n\n조회 단계가 너무 길어져 멈췄습니다. 요청을 나눠서 다시 물어봐 주세요."})

    failures: list[ModelUnavailableError] = []
    for model in models:
        try:
            attempt(model)
            return model.model
        except ModelUnavailableError as exc:
            failures.append(exc)
            if wrote_text:
                break
    raise all_unavailable(failures)
