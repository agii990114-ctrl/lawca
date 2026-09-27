"""캘린더 테스트: 기일 추출·정규화, 캘린더 조회, 일정 권한, 송달일 입력 대기, 구독 주소."""

from datetime import date, datetime, time

import pytest
from fastapi.testclient import TestClient

from lawca.api.app import app, get_extractor_factory
from lawca.deadlines import HolidayCalendar
from lawca.deadlines.ics import IcsEvent, to_ics
from lawca.extraction.normalize import normalize, normalize_time
from lawca.extraction.schema import DocumentType, Hearing, TextField
from lawca.extraction.validate import validate
from tests.conftest import login, make_user
from tests.test_api import FakeExtractor, chat, client, new_conversation, upload  # noqa: F401 (픽스처 재사용)
from tests.test_deadline_records import confirm, document  # noqa: F401
from tests.test_extraction import correction_order, ev

OCTOBER = {"start": "2026-09-27", "end": "2026-11-07"}


def hearing_notice(**hearing):
    fields = {"date": "2026. 10. 15.", "time": "오후 2시 30분", "kind": "변론기일", "place": "제303호 법정",
              "evidence": ev("2026. 10. 15. 14:30")} | hearing
    return correction_order(
        document_type=DocumentType.HEARING_NOTICE,
        document_type_evidence=ev("기일통지서"),
        issued_date=TextField(value="2026-09-01", evidence=ev("2026. 9. 1.")),
        designated_period=None,
        hearing=Hearing(**fields),
    )


# 순수 함수


def test_normalize_hearing_date_and_time():
    doc = normalize(hearing_notice())
    assert (doc.hearing.date, doc.hearing.time) == ("2026-10-15", "14:30")
    assert [normalize_time(t) for t in ("오전 10시", "9:05", "오후 12시", "밤")] == ["10:00", "09:05", "12:00", "밤"]


def test_hearing_validation():
    page = "기일통지서\n2026. 10. 15. 14:30\n서울중앙지방법원 2026가단51234 대여금 원 고 홍길동 2026. 9. 1."
    assert not [i for i in validate(normalize(hearing_notice()), [page], date(2026, 9, 27)) if i.field == "hearing"]
    bad = normalize(hearing_notice(date="다음 달"))
    assert any(i.field == "hearing" and i.level == "error" for i in validate(bad, [page], date(2026, 9, 27)))
    missing = hearing_notice().model_copy(update={"hearing": None})
    assert any(i.field == "hearing" for i in validate(missing, [page], date(2026, 9, 27)))


def test_holidays_between():
    names = dict(HolidayCalendar.load_default().holidays_between(date(2026, 9, 20), date(2026, 10, 10)))
    assert names[date(2026, 9, 25)] == "추석" and names[date(2026, 10, 9)] == "한글날"


def test_timed_ics_event_is_converted_to_utc():
    body = to_ics([IcsEvent("x@lawca", date(2026, 10, 15), "변론기일", "", at=time(14, 30))], datetime(2026, 9, 27))
    assert "DTSTART:20261015T053000Z" in body and "DTEND:20261015T063000Z" in body
    assert "TRIGGER:-PT2H" in body


# 기일 → 캘린더


@pytest.fixture
def hearing_chat(client):  # noqa: F811
    app.dependency_overrides[get_extractor_factory] = lambda: (lambda: FakeExtractor(normalize(hearing_notice())))
    return chat(client, new_conversation(client), file_ids=[upload(client).json()["id"]])


def items(client, **params):  # noqa: F811
    return client.get("/api/calendar", params=OCTOBER | params).json()["items"]


def test_hearing_notice_adds_tentative_event(client, hearing_chat):  # noqa: F811
    text = "".join(e["delta"] for e in hearing_chat if e["type"] == "text")
    assert "미확정" in text
    [item] = items(client)
    assert item["source"] == "hearing" and item["status"] == "tentative"
    assert (item["day"], item["time"], item["location"]) == ("2026-10-15", "14:30", "제303호 법정")
    assert item["title"] == "[기일] 변론기일 · 2026가단51234" and item["created_by"] == "clerk1"


def test_anyone_confirms_hearing_but_only_creator_or_lawyer_edits(client, hearing_chat, db):  # noqa: F811
    event_id = items(client)[0]["id"]
    make_user(db, "clerk2", "clerk")
    make_user(db, "lawyer1", "lawyer")
    other = TestClient(app)
    login(other, "clerk2")
    assert other.patch(f"/api/events/{event_id}", json={"day": "2026-10-16"}).status_code == 403
    confirmed = other.patch(f"/api/events/{event_id}", json={"status": "confirmed"})
    assert confirmed.status_code == 200 and confirmed.json()["confirmed_by"] == "clerk2"
    assert other.patch(f"/api/events/{event_id}", json={"status": "confirmed"}).status_code == 403  # 이미 확정됨

    login(other, "lawyer1")
    moved = other.patch(f"/api/events/{event_id}", json={"day": "2026-10-22", "time": "10:00"}).json()
    assert (moved["day"], moved["time"]) == ("2026-10-22", "10:00")
    assert client.patch(f"/api/events/{event_id}", json={"status": "cancelled"}).status_code == 200  # 만든 사람
    assert items(client) == []


def test_confirmed_deadline_is_on_the_calendar(client, document):  # noqa: F811
    confirm(client, document)
    [item] = items(client)
    assert item["source"] == "deadline" and item["day"] == "2026-09-28" and item["time"] is None
    assert item["title"].startswith("[만료] 보정기한") and item["can_edit"] is False  # 사무원
    assert any(line.startswith("근거:") for line in item["details"])


def test_calendar_returns_holidays_and_limits_range(client):  # noqa: F811
    body = client.get("/api/calendar", params={"start": "2026-10-01", "end": "2026-10-31"}).json()
    assert {"day": "2026-10-09", "name": "한글날"} in body["holidays"]
    assert client.get("/api/calendar", params={"start": "2026-01-01", "end": "2026-12-31"}).status_code == 422


# 직접 입력 일정


def test_manual_events_and_visibility(client, db):  # noqa: F811
    firm = client.post("/api/events", json={"title": "의뢰인 상담", "day": "2026-10-05", "time": "15:00",
                                            "case_number": "2026가단51234"})
    assert firm.status_code == 404  # 없는 사건
    firm = client.post("/api/events", json={"title": "의뢰인 상담", "day": "2026-10-05", "time": "15:00"})
    private = client.post("/api/events", json={"title": "개인 메모", "day": "2026-10-06", "visibility": "private"})
    assert firm.status_code == private.status_code == 201
    assert firm.json()["status"] == "confirmed" and firm.json()["can_edit"] is True
    assert client.post("/api/events", json={"title": "x", "day": "2026-10-06", "time": "25:00"}).status_code == 422

    make_user(db, "clerk2", "clerk")
    other = TestClient(app)
    login(other, "clerk2")
    assert [i["title"] for i in items(other)] == ["의뢰인 상담"]
    assert items(other)[0]["can_edit"] is False
    assert other.patch(f"/api/events/{private.json()['id']}", json={"status": "cancelled"}).status_code == 404
    assert [i["title"] for i in items(client, mine="true")] == ["의뢰인 상담", "개인 메모"]
    assert items(other, mine="true") == []


# 송달일 입력 대기


def test_pending_documents(client, document):  # noqa: F811
    [pending] = client.get("/api/calendar/pending").json()
    assert pending["document_id"] == document["document_id"] and pending["suggestions"]
    confirm(client, document)
    assert client.get("/api/calendar/pending").json() == []


def test_dismiss_pending_and_hearing_notices_are_not_pending(client, document, hearing_chat):  # noqa: F811
    assert [p["document_type"] for p in client.get("/api/calendar/pending").json()] == ["보정명령"]
    assert client.post(f"/api/documents/{document['document_id']}/dismiss-pending").status_code == 204
    assert client.get("/api/calendar/pending").json() == []


# 구독 주소


def test_feed_token(client, document, hearing_chat):  # noqa: F811
    confirm(client, document)
    path = client.post("/api/calendar/feed").json()["path"]
    feed = TestClient(app).get(path)  # 로그인 쿠키 없이
    assert feed.status_code == 200 and feed.headers["content-type"].startswith("text/calendar")
    body = feed.text.replace("\r\n ", "")
    assert "SUMMARY:[만료] 보정기한 · 2026가단51234" in body
    assert "SUMMARY:(미확정) [기일] 변론기일 · 2026가단51234" in body
    assert "DTSTART:20261015T053000Z" in body

    new_path = client.post("/api/calendar/feed").json()["path"]
    assert TestClient(app).get(path).status_code == 404  # 다시 만들면 예전 주소는 끊긴다
    assert TestClient(app).get(new_path).status_code == 200
    assert TestClient(app).get("/api/calendar/feed/nope.ics").status_code == 404
