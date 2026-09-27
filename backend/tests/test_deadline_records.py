"""기한 확정·목록·상태 변경·내보내기 API 테스트. 테스트 DB를 쓴다."""

import pytest
from sqlalchemy import select

from lawca.db.models import AuditLog
from tests.conftest import login, make_user
from tests.test_api import chat, client, new_conversation, upload  # noqa: F401 (픽스처 재사용)

DESIGNATED_7_DAYS = {"amount": 7, "unit": "일", "label": "7일"}


@pytest.fixture
def document(client):  # noqa: F811
    """보정명령을 처리해 문서 카드를 얻는다."""
    events = chat(client, new_conversation(client), file_ids=[upload(client).json()["id"]])
    return next(e["card"] for e in events if e["type"] == "card")


def confirm(client, document, **overrides):  # noqa: F811
    body = {
        "document_id": document["document_id"],
        "label": "보정기한",
        "event_date": "2026-09-17",
        "period": DESIGNATED_7_DAYS,
        "service_kind": "electronic_confirmed",
    } | overrides
    return client.post("/api/deadlines/confirm", json=body)


def test_card_carries_document_id(document):
    assert document["document_id"]


def test_confirm_saves_server_computed_deadline(client, document, db):  # noqa: F811
    res = confirm(client, document)
    assert res.status_code == 201
    record = res.json()
    # 9/17 송달, 7일 → 말일 9/24(추석 전날) → 추석 연휴·주말 → 9/28(월)
    assert (record["deadline"], record["nominal_end"], record["count_start"]) == ("2026-09-28", "2026-09-24", "2026-09-18")
    assert record["status"] == "confirmed"
    assert (record["case_number"], record["document_type"]) == ("2026가단51234", "보정명령")
    assert record["service_label"] == "전자소송 확인일"
    with db() as session:
        assert [a.action for a in session.scalars(select(AuditLog).where(AuditLog.target_type == "deadline"))] == [
            "deadline.confirm"
        ]


def test_confirm_ignores_client_supplied_dates(client, document):  # noqa: F811
    # 화면이 만료일을 보내도 서버는 받지 않고 다시 계산한다
    record = confirm(client, document, deadline="2099-01-01").json()
    assert record["deadline"] == "2026-09-28"


def test_confirm_statutory_rule(client, document):  # noqa: F811
    record = confirm(client, document, label="항소", period=None, rule_id="appeal", event_date="2026-09-10").json()
    assert (record["deadline"], record["kind"], record["rule_id"]) == ("2026-09-28", "statutory", "appeal")


def test_deemed_service_keeps_warning(client, document):  # noqa: F811
    record = confirm(client, document, service_kind="electronic_deemed").json()
    assert any("간주 송달" in w for w in record["warnings"])


def test_confirm_by_file_id_for_older_cards(client, document):  # noqa: F811
    res = confirm(client, document, document_id=None, file_id=document["file_id"])
    assert res.status_code == 201


def test_confirm_unknown_document_is_404(client):  # noqa: F811
    res = client.post(
        "/api/deadlines/confirm",
        json={"document_id": "nope", "label": "x", "event_date": "2026-09-17", "period": DESIGNATED_7_DAYS,
              "service_kind": "paper"},
    )
    assert res.status_code == 404


def test_list_is_sorted_by_deadline_and_filterable(client, document):  # noqa: F811
    confirm(client, document, label="늦은 기한", event_date="2026-10-20")
    confirm(client, document, label="이른 기한", event_date="2026-09-01")
    labels = [d["label"] for d in client.get("/api/deadlines").json()]
    assert labels == ["이른 기한", "늦은 기한"]
    by_doc = client.get("/api/deadlines", params={"document_id": document["document_id"]}).json()
    assert len(by_doc) == 2


def test_status_change_is_audited_and_filters_list(client, document, db):  # noqa: F811
    record = confirm(client, document).json()
    res = client.patch(f"/api/deadlines/{record['id']}", json={"status": "done"})
    assert res.json()["status"] == "done"
    assert client.get("/api/deadlines", params={"status": "confirmed"}).json() == []
    assert len(client.get("/api/deadlines", params={"status": "confirmed,done"}).json()) == 1
    with db() as session:
        actions = [a.action for a in session.scalars(select(AuditLog).where(AuditLog.target_type == "deadline"))]
        assert actions == ["deadline.confirm", "deadline.done"]


def test_unknown_deadline_status_change_is_404(client):  # noqa: F811
    assert client.patch("/api/deadlines/nope", json={"status": "done"}).status_code == 404


def test_ics_export_contains_only_confirmed(client, document, db):  # noqa: F811
    keep = confirm(client, document, label="보정기한").json()
    drop = confirm(client, document, label="취소할 기한").json()
    make_user(db, "lawyer1", "lawyer")
    login(client, "lawyer1")  # 취소는 변호사만 한다
    assert client.patch(f"/api/deadlines/{drop['id']}", json={"status": "cancelled"}).status_code == 200
    res = client.get("/api/deadlines/export.ics")
    assert res.headers["content-type"].startswith("text/calendar")
    body = res.text
    assert f"UID:{keep['id']}@lawca" in body
    assert drop["id"] not in body
    assert "DTSTART;VALUE=DATE:20260928" in body
    assert "보정기한 · 2026가단51234" in body.replace("\r\n ", "")


def test_csv_export_opens_in_excel(client, document):  # noqa: F811
    confirm(client, document)
    res = client.get("/api/deadlines/export.csv")
    raw = res.content
    assert raw.startswith("﻿".encode())  # 엑셀이 UTF-8로 인식하도록 BOM
    rows = raw.decode("utf-8-sig").splitlines()
    assert rows[0].startswith("만료일,요일,기한,상태")
    assert rows[1].startswith("2026-09-28,월,보정기한,확정,2026가단51234")
