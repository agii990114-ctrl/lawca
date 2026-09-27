"""캘린더 API: 기한과 일정(기일·직접 입력)을 한 달·한 주 단위로 보여 주고, 개인 구독 주소(ICS)를 낸다.

- 캘린더에는 확정한 것만 오른다: 진행 중인 기한, 확정한 기일, 직접 넣은 일정.
  완료·취소한 기한과 미확정 기일은 오르지 않는다(미확정 기일은 기한 목록의 '대기' 탭에서 확정한다).
- 일정을 고치거나 취소하는 것은 만든 사람이나 변호사만 한다. 미확정 기일의 확정은 누구나 한다.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import Response
from sqlalchemy.orm import Session

from lawca.api.deps import DB, Worker
from lawca.api.schemas import (
    SERVICE_LABELS,
    CalendarItemOut,
    CalendarOut,
    EventCreate,
    EventUpdate,
    FeedOut,
    HolidayOut,
)
from lawca.db import repo
from lawca.db.models import Deadline, Event, User
from lawca.deadlines import HolidayCalendar
from lawca.deadlines.ics import DEADLINE_ALARMS, IcsEvent, to_ics

router = APIRouter()
MAX_RANGE_DAYS = 100
FEED_PAST_DAYS = 60
FEED_FUTURE_DAYS = 400


# 기한·일정 → 캘린더 칸


def deadline_title(d: Deadline) -> str:
    case = f" · {d.case.case_number}" if d.case else ""
    return f"[만료] {d.label}{case}"


def deadline_details(d: Deadline) -> list[str]:
    lines = [
        f"문서: {d.document.document_type} ({d.document.file.name})",
        f"송달일: {d.event_date.isoformat()} ({SERVICE_LABELS.get(d.service_kind, d.service_kind)})",
        f"기간: {d.period_amount}{d.period_unit}",
        f"근거: {', '.join(d.basis)}",
    ]
    return lines + [f"주의: {w}" for w in d.warnings]


def deadline_item(d: Deadline, user: User) -> CalendarItemOut:
    return CalendarItemOut(
        id=str(d.id),
        source="deadline",
        day=d.deadline,
        time=None,
        title=deadline_title(d),
        label=d.label,
        status=d.status,
        case_number=d.case.case_number if d.case else None,
        court=d.case.court if d.case else None,
        case_name=d.case.case_name if d.case else None,
        location=None,
        memo="",
        created_by=d.confirmed_by,
        confirmed_by=d.confirmed_by,
        visibility="firm",
        file_id=str(d.document.file_id),
        filename=d.document.file.name,
        document_type=d.document.document_type,
        details=deadline_details(d),
        can_edit=True,
        document_id=str(d.document_id),
    )


def event_title(e: Event) -> str:
    case = f" · {e.case.case_number}" if e.case else ""
    prefix = "[기일] " if e.kind == "hearing" else ""
    return f"{prefix}{e.title}{case}"


def event_details(e: Event) -> list[str]:
    lines = []
    if e.kind == "hearing" and e.document is not None:
        lines.append(f"문서: {e.document.document_type} ({e.document.file.name})")
        lines.append("문서에서 읽은 기일입니다. 원문과 대조한 뒤 확정하세요." if e.status == "tentative" else "")
    if e.location:
        lines.append(f"장소: {e.location}")
    return [line for line in lines if line]


def can_edit(e: Event, user: User) -> bool:
    return user.role == "lawyer" or e.created_by == user.username


def event_item(e: Event, user: User) -> CalendarItemOut:
    return CalendarItemOut(
        id=str(e.id),
        source="hearing" if e.kind == "hearing" else "manual",
        day=e.day,
        time=e.at.strftime("%H:%M") if e.at else None,
        title=event_title(e),
        label=e.title,
        status=e.status,
        case_number=e.case.case_number if e.case else None,
        court=e.case.court if e.case else None,
        case_name=e.case.case_name if e.case else None,
        location=e.location,
        memo=e.memo,
        created_by=e.created_by,
        confirmed_by=e.confirmed_by,
        visibility=e.visibility,  # type: ignore[arg-type]
        file_id=str(e.document.file_id) if e.document else None,
        filename=e.document.file.name if e.document else None,
        document_type=e.document.document_type if e.document else None,
        details=event_details(e),
        can_edit=can_edit(e, user),
        document_id=str(e.document_id) if e.document_id else None,
    )


def _mine(item: CalendarItemOut, user: User) -> bool:
    return item.created_by == user.username or item.confirmed_by == user.username


def calendar_items(session: Session, user: User, start: date, end: date, mine: bool) -> list[CalendarItemOut]:
    deadlines = repo.list_deadlines(session, ["confirmed"], date_from=start, date_to=end)
    events = repo.list_events(session, start, end, user.username, statuses=["confirmed"])
    items = [deadline_item(d, user) for d in deadlines] + [event_item(e, user) for e in events]
    if mine:
        items = [i for i in items if _mine(i, user)]
    # 같은 날에는 기한 → 시각 없는 일정 → 시각 순
    return sorted(items, key=lambda i: (i.day, i.source != "deadline", i.time or "", i.title))


# 조회


@router.get("/api/calendar")
def calendar(
    user: Worker,
    session: DB,
    start: date,
    end: date,
    mine: Annotated[bool, Query(description="내가 확정했거나 만든 것만")] = False,
) -> CalendarOut:
    if end < start or (end - start).days > MAX_RANGE_DAYS:
        raise HTTPException(422, f"기간은 {MAX_RANGE_DAYS}일 이내여야 합니다.")
    holidays = HolidayCalendar.load_default().holidays_between(start, end)
    return CalendarOut(
        start=start,
        end=end,
        items=calendar_items(session, user, start, end, mine),
        holidays=[HolidayOut(day=d, name=name) for d, name in holidays],
    )


# 일정 만들기·고치기


def _time(value: str | None) -> time | None:
    return time.fromisoformat(value) if value else None


@router.post("/api/events", status_code=201)
def create_event(req: EventCreate, user: Worker, session: DB) -> CalendarItemOut:
    case = None
    if req.case_number and req.case_number.strip():
        case = repo.get_case(session, req.case_number)
        if case is None:
            raise HTTPException(404, f"사건을 찾을 수 없습니다: {req.case_number}")
    event = repo.create_event(
        session,
        title=req.title,
        day=req.day,
        at=_time(req.time),
        location=req.location,
        memo=req.memo,
        visibility=req.visibility,
        case=case,
    )
    session.commit()
    saved = repo.get_event(session, str(event.id), user.username)
    assert saved is not None
    return event_item(saved, user)


@router.patch("/api/events/{event_id}")
def update_event(event_id: str, req: EventUpdate, user: Worker, session: DB) -> CalendarItemOut:
    event = repo.get_event(session, event_id, user.username)
    if event is None or event.status == "cancelled":
        raise HTTPException(404, "일정을 찾을 수 없습니다.")
    confirming_only = req.model_dump(exclude_unset=True, exclude_defaults=True) == {"status": "confirmed"}
    if not (can_edit(event, user) or (confirming_only and event.status == "tentative")):
        raise HTTPException(403, "이 일정은 만든 사람이나 변호사만 고칠 수 있습니다.")
    changes: dict[str, object] = {}
    if req.title is not None:
        changes["title"] = req.title.strip()
    if req.day is not None:
        changes["day"] = req.day
    if req.clear_time:
        changes["at"] = None
    elif req.time is not None:
        changes["at"] = _time(req.time)
    if req.location is not None:
        changes["location"] = req.location.strip() or None
    if req.memo is not None:
        changes["memo"] = req.memo.strip()
    if req.status is not None:
        changes["status"] = req.status
    repo.update_event(session, event, changes)
    session.commit()
    return event_item(event, user)


# 구독 주소


@router.post("/api/calendar/feed")
def issue_feed(user: Worker, session: DB) -> FeedOut:
    """개인 구독 주소를 새로 만든다. 주소는 이때 한 번만 보여 주고, 다시 만들면 예전 주소는 끊긴다."""
    token = repo.issue_calendar_token(session, user)
    session.commit()
    return FeedOut(path=f"/api/calendar/feed/{token}.ics")


def feed_events(items: list[CalendarItemOut]) -> list[IcsEvent]:
    events = []
    for item in items:
        if item.status == "done":
            continue
        prefix = "(미확정) " if item.status == "tentative" else ""
        details = [
            *item.details,
            *([f"사건: {item.court or ''} {item.case_name or ''}".strip()] if item.case_number else []),
            *([f"메모: {item.memo}"] if item.memo else []),
        ]
        events.append(
            IcsEvent(
                uid=f"{item.id}@lawca",
                day=item.day,
                at=time.fromisoformat(item.time) if item.time else None,
                summary=prefix + item.title,
                description="\n".join(details),
                alarms=DEADLINE_ALARMS if item.source == "deadline" else (None if item.time else ()),
            )
        )
    return events


@router.get("/api/calendar/feed/{token}.ics")
def feed(token: str, session: DB) -> Response:
    """구독용 캘린더 파일. 로그인 쿠키 대신 주소의 토큰으로 사용자를 확인한다(캘린더 앱이 가져간다)."""
    user = repo.user_for_calendar_token(session, token)
    if user is None or user.role == "admin":
        raise HTTPException(404, "구독 주소가 올바르지 않습니다.")
    today = date.today()
    items = calendar_items(
        session, user, today - timedelta(days=FEED_PAST_DAYS), today + timedelta(days=FEED_FUTURE_DAYS), mine=False
    )
    body = to_ics(feed_events(items), datetime.now(timezone.utc), name=f"lawca · {user.name}")
    return Response(
        body.encode("utf-8"),
        media_type="text/calendar; charset=utf-8",
        headers={"Cache-Control": "no-store"},
    )
