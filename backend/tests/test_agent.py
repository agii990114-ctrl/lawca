"""LangGraph 라우터·조회 에이전트 테스트. 가짜 모델을 쓰고 DB는 테스트 DB를 쓴다."""

from datetime import date

import pytest

from lawca.agent.llm import TextDelta, ToolCall
from lawca.agent.ranges import resolve_range
from lawca.agent.tools import run_tool
from lawca.api.app import app, get_models_factory
from tests.fakes import FakeChatModel
from tests.test_api import chat, client, new_conversation, upload  # noqa: F401 (픽스처 재사용)


def use_models(*models):
    app.dependency_overrides[get_models_factory] = lambda: (lambda: list(models))


@pytest.fixture
def confirmed(client):  # noqa: F811
    """보정명령을 처리하고 보정기한을 확정해 둔다(만료일 2026-09-28)."""
    events = chat(client, new_conversation(client), file_ids=[upload(client).json()["id"]])
    card = next(e["card"] for e in events if e["type"] == "card")
    client.post(
        "/api/deadlines/confirm",
        json={
            "document_id": card["document_id"],
            "label": "보정명령 기한",
            "event_date": "2026-09-17",
            "period": {"amount": 7, "unit": "일", "label": "7일"},
            "service_kind": "electronic_confirmed",
        },
    )
    return card


# 기간 해석(코드가 날짜를 계산)


def test_this_week_is_monday_to_sunday():
    # 2026-09-26은 토요일
    assert resolve_range("this_week", date(2026, 9, 26))[:2] == (date(2026, 9, 21), date(2026, 9, 27))
    assert resolve_range("next_week", date(2026, 9, 26))[:2] == (date(2026, 9, 28), date(2026, 10, 4))


def test_this_month_and_overdue():
    assert resolve_range("this_month", date(2026, 2, 10))[:2] == (date(2026, 2, 1), date(2026, 2, 28))
    assert resolve_range("overdue", date(2026, 9, 26))[:2] == (None, date(2026, 9, 25))


def test_unknown_range_is_rejected():
    with pytest.raises(ValueError):
        resolve_range("someday", date(2026, 9, 26))


# 도구


def test_list_deadlines_tool_filters_by_range(confirmed, db):
    with db() as session:
        hit = run_tool("list_deadlines", {"range": "custom", "date_from": "2026-09-28", "date_to": "2026-09-28"}, session, date(2026, 9, 26))
        miss = run_tool("list_deadlines", {"range": "next_week"}, session, date(2026, 10, 10))
    assert hit.result["count"] == 1
    assert hit.result["items"][0]["days_left"] == 2
    assert hit.card["kind"] == "deadlines" and hit.card["items"][0]["deadline"] == "2026-09-28"
    assert miss.result["count"] == 0


def test_search_and_get_case_tools(confirmed, db):
    with db() as session:
        found = run_tool("search_cases", {"query": "홍길동"}, session, date(2026, 9, 26))
        detail = run_tool("get_case", {"case_number": "2026가단 51234"}, session, date(2026, 9, 26))
        missing = run_tool("get_case", {"case_number": "2026가단1"}, session, date(2026, 9, 26))
    assert [c["case_number"] for c in found.result["items"]] == ["2026가단51234"]
    assert found.result["items"][0]["open_deadlines"] == 1
    assert detail.result["found"] and detail.result["deadlines"][0]["deadline"] == "2026-09-28"
    assert missing.result == {"found": False, "case_number": "2026가단1"}


def test_compute_deadline_tool_is_not_saved(db):
    with db() as session:
        out = run_tool("compute_deadline", {"event_date": "2026-09-10", "rule_id": "appeal"}, session, date(2026, 9, 26))
        bad = run_tool("compute_deadline", {"event_date": "2030-01-02", "rule_id": "appeal"}, session, date(2026, 9, 26))
    assert out.result["deadline"] == "2026-09-28" and out.result["saved"] is False
    assert out.card["kind"] == "deadline_calc"
    assert "error" in bad.result


# 채팅 전체 흐름


def test_help_route_explains_capabilities(client):  # noqa: F811
    use_models(FakeChatModel(tasks=[("help", "")]))
    events = chat(client, new_conversation(client), "안녕")
    assert [e["type"] for e in events] == ["status", "status", "text", "done"]
    assert "법원 문서 처리" in events[2]["delta"]


def test_legal_advice_is_declined(client):  # noqa: F811
    use_models(FakeChatModel(tasks=[("out_of_scope", "이길 수 있을까")]))
    events = chat(client, new_conversation(client), "이 사건 이길 수 있을까?")
    assert "법률 판단은 드릴 수 없습니다" in "".join(e.get("delta", "") for e in events)


def test_query_calls_tool_streams_answer_and_shows_card(client, confirmed):  # noqa: F811
    model = FakeChatModel(
        tasks=[("query", "기한 전체 보여줘")],
        script=[
            [ToolCall("list_deadlines", {"range": "all"})],
            [TextDelta("확정한 기한은 "), TextDelta("1건입니다.")],
        ],
    )
    use_models(model)
    events = chat(client, new_conversation(client), "기한 전체 보여줘")
    cards = [e["card"] for e in events if e["type"] == "card"]
    text = "".join(e["delta"] for e in events if e["type"] == "text")
    assert cards[0]["kind"] == "deadlines" and cards[0]["items"][0]["label"] == "보정명령 기한"
    assert text == "확정한 기한은 1건입니다."
    tool_steps = [e for e in events if e["type"] == "status" and e["id"].startswith("q")]
    assert tool_steps[-1]["state"] == "done" and tool_steps[-1]["label"] == "기한 조회: 전체"
    # 두 번째 모델 호출에는 도구 결과가 들어간다
    assert model.seen_turns[-1][-1].role == "tool"


def test_multiple_tasks_run_in_order(client, confirmed):  # noqa: F811
    use_models(
        FakeChatModel(
            tasks=[("query", "기한"), ("draft", "확정증명원")],
            script=[[TextDelta("조회 결과입니다.")]],
        )
    )
    events = chat(client, new_conversation(client), "기한 알려주고 확정증명원도 만들어 줘")
    text = "".join(e["delta"] for e in events if e["type"] == "text")
    assert text.startswith("조회 결과입니다.") and "서식 작성은 아직 준비 중" in text


def test_busy_model_falls_back_to_next(client):  # noqa: F811
    use_models(FakeChatModel("busy", unavailable=True), FakeChatModel("ok", tasks=[("help", "")]))
    events = chat(client, new_conversation(client), "안녕")
    assert events[1]["state"] == "done"


def test_all_models_busy_reports_error(client):  # noqa: F811
    use_models(FakeChatModel("a", unavailable=True), FakeChatModel("b", unavailable=True))
    events = chat(client, new_conversation(client), "안녕")
    assert events[1]["state"] == "error"
    assert "다시 시도" in events[2]["delta"]


def test_previous_turns_are_sent_as_history(client):  # noqa: F811
    model = FakeChatModel(tasks=[("help", "")])
    use_models(model)
    conversation_id = new_conversation(client)
    chat(client, conversation_id, "첫 질문")
    chat(client, conversation_id, "두 번째 질문")
    last = model.seen_turns[-1]
    assert [t.role for t in last] == ["user", "model", "user"]
    assert (last[0].text, last[-1].text) == ("첫 질문", "두 번째 질문")
