"""기한 규칙: 같은 사건·같은 종류는 하나만 진행, 진행 중 기한 고치기, 복원·삭제, 문서 상세·사건 정보 고치기."""

from sqlalchemy import select

from lawca.api.app import app, get_extractor_factory
from lawca.db.models import AuditLog
from lawca.extraction.schema import TextField
from tests.conftest import login, make_user
from tests.test_api import FakeExtractor, chat, client, new_conversation, upload  # noqa: F401 (픽스처 재사용)
from tests.test_deadline_records import confirm, document  # noqa: F401
from tests.test_extraction import correction_order, ev

APPEAL = {"rule_id": "appeal", "period": None}


def second_document(client, **overrides):  # noqa: F811
    """같은 사건(또는 overrides로 다른 사건)의 보정명령을 한 번 더 처리한다."""
    app.dependency_overrides[get_extractor_factory] = lambda: (lambda: FakeExtractor(correction_order(**overrides)))
    events = chat(client, new_conversation(client), file_ids=[upload(client, name="again.pdf").json()["id"]])
    card = next(e["card"] for e in events if e["type"] == "card")
    text = "".join(e["delta"] for e in events if e["type"] == "text")
    return card, text


# 같은 사건·같은 종류는 하나만


def test_same_kind_in_same_case_is_rejected(client, document):  # noqa: F811
    first = confirm(client, document).json()
    again = confirm(client, document, event_date="2026-09-20")
    assert again.status_code == 409 and "기한 목록에서 수정" in again.json()["detail"]
    assert confirm(client, document, **APPEAL).status_code == 201  # 종류가 다르면 된다

    # 같은 사건의 다른 문서도 막힌다. 채팅에서 읽을 때 이미 진행 중인 기한을 알린다.
    card, text = second_document(client)
    assert "이미 진행 중인 **보정기한**" in text
    assert confirm(client, card).status_code == 409

    # 완료하면 같은 종류를 새로 확정할 수 있다
    client.patch(f"/api/deadlines/{first['id']}", json={"status": "done"})
    assert confirm(client, card).status_code == 201


def test_other_case_is_not_blocked(client, document):  # noqa: F811
    confirm(client, document)
    card, text = second_document(client, case_number=TextField(value="2026가단99999", evidence=ev("2026가단51234")))
    assert "이미 진행 중" not in text
    assert confirm(client, card).status_code == 201


def test_restore_is_blocked_when_same_kind_is_open(client, document):  # noqa: F811
    first = confirm(client, document).json()
    client.patch(f"/api/deadlines/{first['id']}", json={"status": "cancelled"})
    second = confirm(client, document, event_date="2026-09-20").json()
    restore = client.patch(f"/api/deadlines/{first['id']}", json={"status": "confirmed"})
    assert restore.status_code == 409
    client.patch(f"/api/deadlines/{second['id']}", json={"status": "cancelled"})
    assert client.patch(f"/api/deadlines/{first['id']}", json={"status": "confirmed"}).status_code == 200


# 진행 중 기한 고치기


def test_terms_update_recomputes_in_place(client, document, db):  # noqa: F811
    record = confirm(client, document).json()
    url = f"/api/deadlines/{record['id']}/terms"
    res = client.patch(url, json={"event_date": "2026-10-05", "service_kind": "paper", "label": "주소보정 기한",
                                  "period": {"amount": 10, "unit": "일", "label": "10일"}})
    assert res.status_code == 200
    body = res.json()
    # 10/5 송달, 10일 → 10/15(목)
    assert (body["deadline"], body["label"], body["service_kind"], body["period"]["amount"]) == ("2026-10-15", "주소보정 기한", "paper", 10)
    assert len(client.get("/api/deadlines").json()) == 1  # 새로 만들지 않는다
    with db() as session:
        log = session.scalars(select(AuditLog).where(AuditLog.action == "deadline.update")).one()
        assert log.detail["before"]["deadline"] == "2026-09-28" and log.actor == "clerk1"


def test_terms_update_rules(client, document):  # noqa: F811
    appeal = confirm(client, document, **APPEAL).json()
    url = f"/api/deadlines/{appeal['id']}/terms"
    period = {"amount": 30, "unit": "일", "label": "30일"}
    assert client.patch(url, json={"event_date": "2026-09-01", "service_kind": "paper", "period": period}).status_code == 422
    assert client.patch(url, json={"event_date": "2026-09-01", "service_kind": "paper"}).status_code == 200
    client.patch(f"/api/deadlines/{appeal['id']}", json={"status": "done"})
    assert client.patch(url, json={"event_date": "2026-09-02", "service_kind": "paper"}).status_code == 409


# 완료·취소·삭제


def test_status_times_and_delete(client, document, db):  # noqa: F811
    record = confirm(client, document).json()
    assert record["confirmed_by"] == "clerk1" and record["status_changed_at"] is None
    done = client.patch(f"/api/deadlines/{record['id']}", json={"status": "done"}).json()
    assert done["status_changed_at"]  # 완료 시각
    assert client.patch(f"/api/deadlines/{record['id']}", json={"status": "cancelled"}).status_code == 200

    make_user(db, "lawyer1", "lawyer")
    login(client, "lawyer1")
    other = confirm(client, document, **APPEAL).json()
    assert client.delete(f"/api/deadlines/{other['id']}").status_code == 409  # 취소한 것만 지운다
    assert client.delete(f"/api/deadlines/{record['id']}").status_code == 204
    assert [d["id"] for d in client.get("/api/deadlines").json()] == [other["id"]]
    assert client.patch(f"/api/deadlines/{record['id']}", json={"status": "confirmed"}).status_code == 404


# 문서 상세와 사건 정보 고치기


def test_document_detail(client, document):  # noqa: F811
    confirm(client, document)
    detail = client.get(f"/api/documents/{document['document_id']}").json()
    assert (detail["document_type"], detail["case_number"], detail["court"]) == ("보정명령", "2026가단51234", "서울중앙지방법원")
    assert detail["parties"] == [{"role": "원고", "name": "홍길동"}] and detail["corrected"] is False
    assert [d["label"] for d in detail["deadlines"]] == ["보정기한"]
    assert detail["suggestions"][0]["key"] == "designated:보정명령"
    assert client.get("/api/documents/nope").status_code == 404


def test_correct_case_moves_deadlines(client, document):  # noqa: F811
    record = confirm(client, document).json()
    url = f"/api/documents/{document['document_id']}/case"
    assert client.patch(url, json={"case_number": "가단123"}).status_code == 422
    res = client.patch(url, json={"case_number": "2026가단 777", "court": "수원지방법원", "case_name": "손해배상(기)"})
    assert res.status_code == 200
    detail = res.json()
    assert (detail["case_number"], detail["court"], detail["corrected"]) == ("2026가단777", "수원지방법원", True)
    moved = client.get("/api/deadlines", params={"case_number": "2026가단777"}).json()
    assert [d["id"] for d in moved] == [record["id"]]


def test_correct_case_conflict(client, document):  # noqa: F811
    confirm(client, document)
    card, _ = second_document(client, case_number=TextField(value="2026가단99999", evidence=ev("2026가단51234")))
    confirm(client, card)
    res = client.patch(f"/api/documents/{card['document_id']}/case", json={"case_number": "2026가단51234"})
    assert res.status_code == 409 and "이미 진행 중인" in res.json()["detail"]
