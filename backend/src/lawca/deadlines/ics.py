"""일정을 캘린더 파일(iCalendar, RFC 5545)로 만든다. 순수 함수이며 외부 라이브러리를 쓰지 않는다.

- 기한은 종일 일정으로 넣는다. 만료일 당일이 끝날 때 기간이 만료하므로 시각을 붙이지 않는다.
  알림: 3일 전, 전날 오전 9시(종일 일정의 시작 0시 기준 -15시간).
- 시각이 있는 일정(기일 등)은 한국 시간을 UTC로 바꿔 1시간짜리로 넣는다(한국은 일광 절약 시간이 없다).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone

LINE_LIMIT = 75  # RFC 5545 3.1: 한 줄은 CRLF를 빼고 75옥텟 이하
KST = timezone(timedelta(hours=9))
DEADLINE_ALARMS = (("-P3D", "3일 뒤 만료: "), ("-PT15H", "내일 만료: "))
TIMED_ALARMS = (("-P1D", "내일: "), ("-PT2H", "2시간 뒤: "))


@dataclass(frozen=True)
class IcsEvent:
    uid: str
    day: date
    summary: str
    description: str
    at: time | None = None
    """시각(한국 시간). 없으면 종일 일정."""
    alarms: tuple[tuple[str, str], ...] | None = None
    """(TRIGGER, 알림 글 앞머리). None이면 종일 일정은 기한 알림, 시각 일정은 전날·2시간 전 알림."""


def escape(text: str) -> str:
    """TEXT 값의 특수 문자를 이스케이프한다(RFC 5545 3.3.11)."""
    return text.replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\r\n", "\\n").replace("\n", "\\n")


def fold(line: str) -> str:
    """75옥텟을 넘는 줄을 접는다. UTF-8 글자 중간에서 자르지 않는다."""
    parts: list[str] = []
    current = ""
    limit = LINE_LIMIT
    for char in line:
        if len((current + char).encode("utf-8")) > limit:
            parts.append(current)
            current = char
            limit = LINE_LIMIT - 1  # 이어지는 줄은 맨 앞 공백 1옥텟을 쓴다
        else:
            current += char
    parts.append(current)
    return "\r\n ".join(parts)


def _utc(day: date, at: time) -> str:
    return datetime.combine(day, at, KST).astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def to_ics(events: list[IcsEvent], generated_at: datetime, name: str = "lawca 기한") -> str:
    stamp = generated_at.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//lawca//deadlines//KO",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        f"X-WR-CALNAME:{escape(name)}",
    ]
    for event in events:
        lines += ["BEGIN:VEVENT", f"UID:{event.uid}", f"DTSTAMP:{stamp}"]
        if event.at is None:
            lines += [
                f"DTSTART;VALUE=DATE:{event.day:%Y%m%d}",
                f"DTEND;VALUE=DATE:{event.day + timedelta(days=1):%Y%m%d}",
            ]
            alarms = event.alarms if event.alarms is not None else DEADLINE_ALARMS
        else:
            start = datetime.combine(event.day, event.at)
            end = start + timedelta(hours=1)
            lines += [f"DTSTART:{_utc(event.day, event.at)}", f"DTEND:{_utc(end.date(), end.time())}"]
            alarms = event.alarms if event.alarms is not None else TIMED_ALARMS
        lines += [
            f"SUMMARY:{escape(event.summary)}",
            f"DESCRIPTION:{escape(event.description)}",
            "TRANSP:TRANSPARENT" if event.at is None else "TRANSP:OPAQUE",
        ]
        for trigger, prefix in alarms:
            lines += [
                "BEGIN:VALARM",
                "ACTION:DISPLAY",
                f"DESCRIPTION:{escape(prefix + event.summary)}",
                f"TRIGGER:{trigger}",
                "END:VALARM",
            ]
        lines.append("END:VEVENT")
    lines.append("END:VCALENDAR")
    return "".join(fold(line) + "\r\n" for line in lines)
