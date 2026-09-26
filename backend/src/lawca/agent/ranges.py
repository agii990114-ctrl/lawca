"""'이번 주' 같은 기간 표현을 날짜 범위로 바꾼다. LLM은 이름만 고르고 날짜 계산은 코드가 한다.

주는 월요일부터 일요일까지로 본다.
"""

from __future__ import annotations

from datetime import date, timedelta

RANGES: dict[str, str] = {
    "upcoming": "오늘 이후 전체",
    "overdue": "이미 지난 기한",
    "today": "오늘",
    "this_week": "이번 주",
    "next_week": "다음 주",
    "next_7_days": "오늘부터 7일",
    "next_30_days": "오늘부터 30일",
    "this_month": "이번 달",
    "all": "전체",
    "custom": "직접 지정",
}


def resolve_range(
    name: str, today: date, date_from: str | None = None, date_to: str | None = None
) -> tuple[date | None, date | None, str]:
    """(시작일, 종료일, 설명)을 돌려준다. None은 그쪽 끝이 열려 있다는 뜻이다."""
    monday = today - timedelta(days=today.weekday())
    if name == "upcoming":
        return today, None, f"{today.isoformat()} 이후"
    if name == "overdue":
        return None, today - timedelta(days=1), f"{today.isoformat()} 이전(지난 기한)"
    if name == "today":
        return today, today, today.isoformat()
    if name == "this_week":
        return monday, monday + timedelta(days=6), f"이번 주({monday.isoformat()} ~ {(monday + timedelta(days=6)).isoformat()})"
    if name == "next_week":
        start = monday + timedelta(days=7)
        return start, start + timedelta(days=6), f"다음 주({start.isoformat()} ~ {(start + timedelta(days=6)).isoformat()})"
    if name == "next_7_days":
        end = today + timedelta(days=6)
        return today, end, f"{today.isoformat()} ~ {end.isoformat()}"
    if name == "next_30_days":
        end = today + timedelta(days=29)
        return today, end, f"{today.isoformat()} ~ {end.isoformat()}"
    if name == "this_month":
        start = today.replace(day=1)
        end = (start + timedelta(days=32)).replace(day=1) - timedelta(days=1)
        return start, end, f"이번 달({start.isoformat()} ~ {end.isoformat()})"
    if name == "custom":
        start = date.fromisoformat(date_from) if date_from else None
        end = date.fromisoformat(date_to) if date_to else None
        return start, end, f"{date_from or '처음'} ~ {date_to or '끝'}"
    if name == "all":
        return None, None, "전체"
    raise ValueError(f"알 수 없는 기간입니다: {name}")
