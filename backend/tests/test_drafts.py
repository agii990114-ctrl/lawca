"""서식 작성 테스트: 서식 정의·검증, 되묻기(interrupt)와 재개, DOCX 초안, 입력값 기억."""

import io
from datetime import date

import pytest
from docx import Document
from sqlalchemy import select

from lawca.api.app import app, get_extractor_factory
from lawca.db.models import AuditLog, Case, Draft
from lawca.extraction.schema import Evidence, Party
from lawca.forms import LATER, clean_answer, initial_values, load_forms, missing_fields, render
from tests.fakes import FakeChatModel
from tests.test_extraction import correction_order
from tests.test_agent import use_models
from tests.test_api import FakeExtractor, chat, client, events_of, new_conversation, upload  # noqa: F401 (픽스처 재사용)

FINALITY = "certificate_of_finality"
CASE = {"court": "서울중앙지방법원", "case_number": "2026가단51234", "case_name": "대여금",
        "plaintiffs": "홍길동", "defendants": "김철수", "judgment_date": ""}


def docx_text(data: bytes) -> str:
    return "\n".join(p.text for p in Document(io.BytesIO(data)).paragraphs)


# 서식 정의와 검증(코드)


def test_forms_are_defined():
    assert set(load_forms()) == {"certificate_of_finality", "certificate_of_service", "address_correction",
                                 "fact_inquiry", "execution_clause", "brief"}


def test_find_form_by_alias():
    from lawca.forms import find_form
    assert find_form("사실조회 신청 좀").id == "fact_inquiry"
    assert find_form("2026가단51234 집행문 부여 신청서").id == "execution_clause"
    assert find_form("송달증명원 떼 줘").id == "certificate_of_service"


def test_missing_fields_and_conditional_requirement():
    form = load_forms()["address_correction"]
    values = initial_values(form, CASE, {"applicant": "원고 홍길동"}, date(2026, 9, 26))
    assert [f.key for f in missing_fields(form, values)] == ["method"]
    values["method"] = "새 주소로 송달"
    assert [f.key for f in missing_fields(form, values)] == ["new_address"]
    values["method"] = "공시송달"
    assert missing_fields(form, values) == []


def test_clean_answer_validates_by_type():
    form = load_forms()[FINALITY]
    assert clean_answer(form.field("judgment_date"), "2026. 9. 1.") == ("2026-09-01", None)
    assert clean_answer(form.field("judgment_date"), "어제")[1]
    assert clean_answer(form.field("finality_date"), LATER) == (LATER, None)
    assert clean_answer(form.field("applicant"), LATER)[1]  # 나중에 입력할 수 없는 항목
    service = load_forms()["certificate_of_service"]
    assert clean_answer(service.field("served_document"), "공소장")[1]
    assert clean_answer(service.field("copies"), "두 통")[1]


def test_render_fills_template_and_leaves_blanks():
    form = load_forms()[FINALITY]
    values = initial_values(form, CASE, {}, date(2026, 9, 26))
    values.update(applicant="원고 홍길동", judgment_date="2026-09-01", finality_date=LATER)
    text = docx_text(render(form, values))
    assert "2026가단51234" in text and "2026. 9. 1. 선고한 판결은 20    .    .    . 확정" in text
    assert "소송대리인" not in text  # 비어 있으면 줄을 뺀다
    assert "서울중앙지방법원  귀중" in text


def test_fact_inquiry_renders_multiline_items_and_blank_address():
    form = load_forms()["fact_inquiry"]
    values = initial_values(form, CASE, {"applicant": "원고 홍길동"}, date(2026, 9, 27))
    assert [f.key for f in missing_fields(form, values)] == ["institution", "institution_address", "purpose", "inquiry_items"]
    values.update(institution="에스케이텔레콤 주식회사", institution_address=LATER,
                  purpose="피고의 휴대전화 명의", inquiry_items="1. 가입자 성명\n2. 가입자 주소")
    assert missing_fields(form, values) == []
    text = docx_text(render(form, values))
    assert "명칭  에스케이텔레콤 주식회사" in text and "주소  ____________" in text
    assert "1. 가입자 성명\n2. 가입자 주소" in text  # 줄바꿈이 문서 줄바꿈으로 들어간다


def test_execution_clause_fills_parties_from_case():
    form = load_forms()["execution_clause"]
    values = initial_values(form, CASE | {"judgment_date": "2026-09-10"}, {"applicant": "원고 홍길동"}, date(2026, 9, 27))
    assert values["creditor"] == "홍길동" and values["debtor"] == "김철수" and values["title_document"] == "판결"
    assert missing_fields(form, values) == []
    assert clean_answer(form.field("title_document"), "지급명령")[1]  # 목록에 없는 집행권원
    text = docx_text(render(form, values))
    assert "2026. 9. 10.자 판결 정본에 집행문을 부여하여 주시기 바랍니다." in text
    assert "채  권  자    홍길동" in text


# 채팅으로 서식 만들기


@pytest.fixture
def case_ready(client):  # noqa: F811
    """보정명령을 처리해 2026가단51234 사건(원고 홍길동, 피고 김철수)을 만들어 둔다."""
    parties = [
        Party(role="원고", name="홍길동", evidence=Evidence(quote="원 고 홍길동", page=1)),
        Party(role="피고", name="김철수", evidence=Evidence(quote="피 고 김철수", page=1)),
    ]
    app.dependency_overrides[get_extractor_factory] = lambda: (lambda: FakeExtractor(correction_order(parties=parties)))
    chat(client, new_conversation(client), file_ids=[upload(client).json()["id"]])


def ask_for(client, text, form_request):  # noqa: F811
    use_models(FakeChatModel(tasks=[("draft", text)], form_request=form_request))
    conversation_id = new_conversation(client)
    return conversation_id, chat(client, conversation_id, text)


def question_of(events):
    return next(e["card"] for e in events if e["type"] == "card" and e["card"]["kind"] == "question")


def resume(client, job_id, answers):  # noqa: F811
    return client.post(f"/api/jobs/{job_id}/resume", json={"answers": answers})


def test_draft_asks_missing_fields_then_builds_docx(client, case_ready, db):  # noqa: F811
    conversation_id, events = ask_for(client, "2026가단51234 확정증명원 신청서 만들어 줘", (FINALITY, "2026가단51234", {}))
    question = question_of(events)
    assert question["stage"] == "fields"
    assert [f["key"] for f in question["fields"]] == ["applicant", "judgment_date", "finality_date"]
    assert question["fields"][2]["allow_later"] is True
    assert client.get(f"/api/jobs/{question['job_id']}").json()["status"] == "waiting"

    res = resume(client, question["job_id"], {"applicant": "원고 홍길동", "judgment_date": "2026. 9. 1.", "finality_date": LATER})
    events = events_of(res)
    card = next(e["card"] for e in events if e["type"] == "card" and e["card"]["kind"] == "draft")
    assert card["form_name"] == "확정증명원 신청서" and card["blanks"] == ["판결 확정일"]
    assert card["filename"] == "확정증명원 신청서_2026가단51234_초안.docx"

    download = client.get(f"/api/files/{card['file_id']}/content")
    assert download.headers["content-disposition"].startswith("attachment; filename*=UTF-8''")
    assert "2026. 9. 1. 선고한 판결은 20    .    .    . 확정" in docx_text(download.content)

    # 답은 사용자 메시지로 대화에 남고, 작업은 끝난다
    messages = client.get(f"/api/conversations/{conversation_id}").json()["messages"]
    assert [m["role"] for m in messages] == ["user", "assistant", "user", "assistant"]
    assert "판결 확정일: 나중에 입력" in messages[2]["text"]
    assert client.get(f"/api/jobs/{question['job_id']}").json()["status"] == "done"

    with db() as session:
        case = session.scalars(select(Case)).one()
        assert case.facts == {"applicant": "원고 홍길동", "judgment_date": "2026-09-01"}  # 나중에 입력한 값은 기억하지 않는다
        assert session.scalars(select(Draft)).one().blanks == ["판결 확정일"]
        actions = {a.action for a in session.scalars(select(AuditLog))}
        assert {"draft.create", "case.facts"} <= actions


def test_remembered_values_are_not_asked_again(client, case_ready):  # noqa: F811
    _, events = ask_for(client, "확정증명원", (FINALITY, "2026가단51234", {}))
    q = question_of(events)
    resume(client, q["job_id"], {"applicant": "원고 홍길동", "judgment_date": "2026-09-01", "finality_date": LATER})

    _, events = ask_for(client, "확정증명원 다시", (FINALITY, "2026가단51234", {}))
    assert [f["key"] for f in question_of(events)["fields"]] == ["finality_date"]


def test_values_in_request_are_used(client, case_ready):  # noqa: F811
    values = {"applicant": "원고 홍길동", "judgment_date": "2026-09-01", "finality_date": "2026-09-20"}
    _, events = ask_for(client, "확정증명원, 선고 9/1 확정 9/20", (FINALITY, "2026가단51234", values))
    card = next(e["card"] for e in events if e["type"] == "card")
    assert card["kind"] == "draft" and card["blanks"] == []


def test_invalid_answer_is_asked_again_with_error(client, case_ready):  # noqa: F811
    _, events = ask_for(client, "확정증명원", (FINALITY, "2026가단51234", {}))
    first = question_of(events)
    events = events_of(resume(client, first["job_id"], {"applicant": "원고 홍길동", "judgment_date": "지난주", "finality_date": LATER}))
    again = question_of(events)
    assert [f["key"] for f in again["fields"]] == ["judgment_date"]
    assert "날짜 형식" in again["errors"][0]
    # 같은 작업이 새 질문을 기다린다. question_id로 이전 질문과 구분한다.
    job = client.get(f"/api/jobs/{first['job_id']}").json()
    assert job["status"] == "waiting"
    assert job["question"]["question_id"] == again["question_id"]
    assert again["question_id"] != first["question_id"]


def test_answering_twice_is_rejected(client, case_ready):  # noqa: F811
    _, events = ask_for(client, "확정증명원", (FINALITY, "2026가단51234", {}))
    job_id = question_of(events)["job_id"]
    answers = {"applicant": "원고 홍길동", "judgment_date": "2026-09-01", "finality_date": LATER}
    assert resume(client, job_id, answers).status_code == 200
    assert resume(client, job_id, answers).status_code == 409


def test_unknown_form_and_case_are_asked_in_order(client, case_ready):  # noqa: F811
    _, events = ask_for(client, "서류 하나 만들어 줘", (None, None, {}))
    q = question_of(events)
    assert q["stage"] == "form" and "송달증명원 신청서" in q["fields"][0]["options"]

    q = question_of(events_of(resume(client, q["job_id"], {"form": "송달증명원 신청서"})))
    assert q["stage"] == "case"
    choice = next(o for o in q["fields"][0]["options"] if o.startswith("2026가단51234"))

    q = question_of(events_of(resume(client, q["job_id"], {"case": choice})))
    assert [f["key"] for f in q["fields"]] == ["applicant"]  # 송달받은 당사자는 사건에서 채운다

    events = events_of(resume(client, q["job_id"], {"applicant": "원고 홍길동"}))
    card = next(e["card"] for e in events if e["type"] == "card" and e["card"]["kind"] == "draft")
    assert dict((f["label"], f["value"]) for f in card["fields"])["송달받은 당사자"] == "김철수"


def test_execution_clause_via_chat(client, case_ready):  # noqa: F811
    _, events = ask_for(client, "2026가단51234 집행문부여 신청서", ("execution_clause", "2026가단51234", {}))
    q = question_of(events)
    assert [f["key"] for f in q["fields"]] == ["applicant", "judgment_date"]  # 채권자·채무자는 사건에서 채운다
    events = events_of(resume(client, q["job_id"], {"applicant": "원고 홍길동", "judgment_date": "2026-09-10"}))
    card = next(e["card"] for e in events if e["type"] == "card" and e["card"]["kind"] == "draft")
    assert card["form_name"] == "집행문부여 신청서" and card["blanks"] == []
    fields = dict((f["label"], f["value"]) for f in card["fields"])
    assert fields["채권자"] == "홍길동" and fields["채무자"] == "김철수"


def test_without_case_all_case_fields_are_asked(client):  # noqa: F811
    _, events = ask_for(client, "주소보정서", ("address_correction", None, {}))
    q = question_of(events)
    q = question_of(events_of(resume(client, q["job_id"], {"case": "사건 없이 직접 입력"})))
    keys = [f["key"] for f in q["fields"]]
    assert keys[:5] == ["court", "case_number", "case_name", "plaintiffs", "defendants"]


def test_rule_based_fallback_when_models_unavailable(client, case_ready):  # noqa: F811
    use_models(FakeChatModel("busy", unavailable=True))
    events = chat(client, new_conversation(client), "2026가단51234 송달증명원")
    # 라우터가 실패하면 요청 파악 실패로 끝난다(서식 노드까지 가지 않음)
    assert any(e["type"] == "status" and e["state"] == "error" for e in events)


def test_postgres_checkpointer_survives_new_graph_instance(test_factory):
    """실제 PostgreSQL 체크포인터로 멈췄다가, 새로 만든 그래프(서버 재시작 상황)에서 이어간다."""
    from typing import TypedDict

    from langgraph.checkpoint.postgres import PostgresSaver
    from langgraph.graph import END, START, StateGraph
    from langgraph.types import Command, interrupt
    from psycopg.rows import dict_row
    from psycopg_pool import ConnectionPool

    from tests.conftest import TEST_DATABASE_URL

    class S(TypedDict):
        answer: str

    def ask(state: S) -> dict:
        return {"answer": interrupt({"q": "확정일?"})}

    def build(saver):
        g = StateGraph(S)
        g.add_node("ask", ask)
        g.add_edge(START, "ask")
        g.add_edge("ask", END)
        return g.compile(checkpointer=saver)

    conninfo = TEST_DATABASE_URL.replace("postgresql+psycopg://", "postgresql://")
    kwargs = {"autocommit": True, "prepare_threshold": 0, "row_factory": dict_row}
    config = {"configurable": {"thread_id": "restart-test"}}
    with ConnectionPool(conninfo, kwargs=kwargs) as pool:
        saver = PostgresSaver(pool)
        saver.setup()
        with pool.connection() as conn:
            for table in ("checkpoint_writes", "checkpoint_blobs", "checkpoints"):
                conn.execute(f"DELETE FROM {table} WHERE thread_id = 'restart-test'")
        list(build(saver).stream({"answer": ""}, config))
    with ConnectionPool(conninfo, kwargs=kwargs) as pool:
        result = build(PostgresSaver(pool)).invoke(Command(resume="2026-09-20"), config)
    assert result == {"answer": "2026-09-20"}


def test_draft_is_indexed_in_library(client, case_ready):  # noqa: F811
    values = {"applicant": "원고 홍길동", "judgment_date": "2026-09-01", "finality_date": "2026-09-20"}
    ask_for(client, "확정증명원", (FINALITY, "2026가단51234", values))
    [doc] = client.get("/api/library", params={"kind": "draft"}).json()
    assert doc["title"] == "확정증명원 신청서_2026가단51234_초안" and doc["case_number"] == "2026가단51234"
    hits = client.get("/api/library/search", params={"q": "확정되었음을 증명"}).json()["hits"]
    assert hits and hits[0]["doc"]["kind"] == "draft"
