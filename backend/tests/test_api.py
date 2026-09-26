"""HTTP API 테스트. 테스트 DB를 쓰고, Gemini는 가짜 추출기로 바꾼다."""

import json

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from lawca.api.app import app, get_extractor_factory, get_models_factory
from lawca.db.models import AuditLog, Case, Document, Message
from lawca.extraction.gemini import ModelUnavailableError
from lawca.extraction.schema import CourtDocument
from tests.fakes import FakeChatModel
from tests.test_extraction import correction_order


class FakeExtractor:
    model = "fake"

    def __init__(self, doc: CourtDocument) -> None:
        self.doc = doc

    def extract(self, pdf: bytes) -> CourtDocument:
        return self.doc


@pytest.fixture
def client(db):
    app.dependency_overrides[get_extractor_factory] = lambda: (lambda: FakeExtractor(correction_order()))
    app.dependency_overrides[get_models_factory] = lambda: (lambda: [FakeChatModel(tasks=[("help", "")])])
    yield TestClient(app)
    app.dependency_overrides.pop(get_extractor_factory, None)
    app.dependency_overrides.pop(get_models_factory, None)


def events_of(res) -> list[dict]:
    return [json.loads(line[len("data: ") :]) for line in res.text.splitlines() if line.startswith("data: ")]


def upload(client, name="order.pdf", data=b"%PDF-1.4 fake"):
    return client.post("/api/files", files={"file": (name, data, "application/pdf")})


def new_conversation(client) -> str:
    return client.post("/api/conversations").json()["id"]


def chat(client, conversation_id, message="", file_ids=()):
    res = client.post("/api/chat", json={"conversation_id": conversation_id, "message": message, "file_ids": list(file_ids)})
    return events_of(res)


# 파일


def test_upload_file_is_stored_and_served(client):
    body = upload(client).json()
    assert (body["name"], body["size"], body["mime"]) == ("order.pdf", 13, "application/pdf")
    assert client.get(f"/api/files/{body['id']}/content").content == b"%PDF-1.4 fake"


def test_upload_rejects_non_pdf(client):
    assert upload(client, "a.txt", b"hello").status_code == 415


def test_unknown_file_is_404(client):
    assert client.get("/api/files/not-a-uuid/content").status_code == 404


# 대화와 채팅


def test_chat_with_pdf_streams_and_persists_everything(client, db):
    conversation_id = new_conversation(client)
    file_id = upload(client).json()["id"]
    events = chat(client, conversation_id, "이 보정명령 처리해 줘", [file_id])

    assert [e["type"] for e in events] == ["status", "status", "text", "card", "done"]
    card = events[3]["card"]
    assert card["kind"] == "document" and card["file_id"] == file_id

    # 대화를 다시 불러오면 사용자 메시지(첨부 포함)와 답변(단계·카드 포함)이 그대로 있다
    detail = client.get(f"/api/conversations/{conversation_id}").json()
    assert detail["title"] == "이 보정명령 처리해 줘"
    user, reply = detail["messages"]
    assert (user["role"], user["text"], user["attachments"][0]["id"]) == ("user", "이 보정명령 처리해 줘", file_id)
    assert reply["role"] == "assistant" and reply["state"] == "done"
    assert [s["state"] for s in reply["steps"]] == ["done"]
    assert reply["cards"][0]["extraction"]["case_number"]["value"] == "2026가단51234"

    # 사건과 문서가 저장되고 감사 기록이 남는다
    with db() as session:
        case = session.scalars(select(Case)).one()
        assert (case.case_number, case.court) == ("2026가단51234", "서울중앙지방법원")
        assert {(p.role, p.name) for p in case.parties} == {("원고", "홍길동")}
        document = session.scalars(select(Document)).one()
        assert (document.case_id, document.document_type) == (case.id, "보정명령")
        assert {a.action for a in session.scalars(select(AuditLog))} == {"case.create", "document.create"}


def test_same_case_is_not_duplicated(client, db):
    conversation_id = new_conversation(client)
    for _ in range(2):
        chat(client, conversation_id, file_ids=[upload(client).json()["id"]])
    with db() as session:
        assert len(session.scalars(select(Case)).all()) == 1
        assert len(session.scalars(select(Document)).all()) == 2


def test_title_falls_back_to_file_name(client):
    conversation_id = new_conversation(client)
    chat(client, conversation_id, file_ids=[upload(client, "판결문.pdf").json()["id"]])
    assert client.get("/api/conversations").json()[0]["title"] == "판결문.pdf"


def test_chat_text_only_goes_through_router(client):
    events = chat(client, new_conversation(client), "뭘 할 수 있어?")
    assert [e["type"] for e in events] == ["status", "status", "text", "done"]
    assert "PDF" in events[2]["delta"]


def test_chat_with_unknown_file_asks_to_reupload(client):
    events = chat(client, new_conversation(client), file_ids=["nope"])
    assert "다시 올려" in events[0]["delta"]


def test_chat_with_unknown_conversation_is_404(client):
    res = client.post("/api/chat", json={"conversation_id": "nope", "message": "hi"})
    assert res.status_code == 404


def test_conversations_are_listed_newest_first(client):
    first = new_conversation(client)
    chat(client, first, "첫 대화")
    second = new_conversation(client)
    chat(client, second, "둘째 대화")
    assert [c["id"] for c in client.get("/api/conversations").json()][:2] == [second, first]


def test_chat_reports_unavailable_model_and_saves_reply(client, db):
    class Busy:
        model = "busy"

        def extract(self, pdf):
            raise ModelUnavailableError("모델이 혼잡합니다.")

    app.dependency_overrides[get_extractor_factory] = lambda: (lambda: Busy())
    conversation_id = new_conversation(client)
    events = chat(client, conversation_id, file_ids=[upload(client).json()["id"]])
    assert [e.get("state") for e in events if e["type"] == "status"] == ["running", "error"]
    with db() as session:
        reply = session.scalars(select(Message).where(Message.role == "assistant")).one()
        assert "혼잡" in reply.text
        assert session.scalars(select(Document)).first() is None


# 기한 계산(DB 없음)


def test_deadline_endpoint_statutory():
    body = TestClient(app).post("/api/deadlines", json={"event_date": "2026-09-10", "rule_id": "appeal"}).json()
    assert body["deadline"] == "2026-09-28"
    assert [d["reason"] for d in body["extended_over"]][:2] == ["추석 전날", "추석"]


def test_deadline_endpoint_designated():
    res = TestClient(app).post(
        "/api/deadlines", json={"event_date": "2026-09-01", "period": {"amount": 7, "unit": "일", "label": "7일"}}
    )
    assert res.json()["deadline"] == "2026-09-08"


def test_deadline_endpoint_requires_exactly_one_period_source():
    assert TestClient(app).post("/api/deadlines", json={"event_date": "2026-09-01"}).status_code == 422


def test_deadline_outside_calendar_is_422():
    res = TestClient(app).post("/api/deadlines", json={"event_date": "2030-01-02", "rule_id": "appeal"})
    assert res.status_code == 422
