"""민법의 기간 계산 규정(민사소송법 제170조가 준용)을 구현한다.

- 제157조: 초일 불산입. 기간이 오전 영시부터 시작하면 초일을 산입한다.
- 제159조: 기간 말일의 종료로 만료한다.
- 제160조: 주·월·연은 역(曆)으로 계산한다. 최후의 주·월·연에서 기산일에 해당한 날의 전일로 만료하고,
  최종 월에 해당일이 없으면 그 월의 말일로 만료한다.
- 제161조: 기간의 말일이 토요일 또는 공휴일이면 그 익일로 만료한다.
"""

from __future__ import annotations

import calendar as _cal
from dataclasses import dataclass, field
from datetime import date, timedelta
from enum import Enum

from lawca.deadlines.calendar import HolidayCalendar


class Unit(Enum):
    DAY = "일"
    WEEK = "주"
    MONTH = "월"
    YEAR = "년"


@dataclass(frozen=True)
class Period:
    amount: int
    unit: Unit

    def __post_init__(self) -> None:
        if self.amount < 1:
            raise ValueError(f"기간은 1 이상이어야 합니다: {self.amount}")

    def __str__(self) -> str:
        suffix = "개월" if self.unit is Unit.MONTH else self.unit.value
        return f"{self.amount}{suffix}"


@dataclass(frozen=True)
class DeadlineResult:
    event_date: date
    """기간을 여는 사건이 있은 날(송달일, 고지일 등)."""
    period: Period
    count_start: date
    """기산일."""
    nominal_end: date
    """토요일·공휴일 연장 전의 말일."""
    deadline: date
    """기간 만료일. 이날이 끝날 때 기간이 만료한다."""
    extended_over: tuple[tuple[date, str], ...]
    """민법 제161조로 건너뛴 날과 그 사유."""
    basis: tuple[str, ...]
    """계산에 적용한 조문."""
    warnings: tuple[str, ...] = field(default=())


def _add_months(start: date, months: int) -> tuple[date, bool]:
    """start에서 months개월 뒤의 해당일을 구한다. 해당일이 없으면 그 월의 말일과 False를 돌려준다."""
    index = start.month - 1 + months
    year, month = start.year + index // 12, index % 12 + 1
    last_day = _cal.monthrange(year, month)[1]
    if start.day > last_day:
        return date(year, month, last_day), False
    return date(year, month, start.day), True


def _nominal_end(count_start: date, period: Period) -> tuple[date, list[str]]:
    if period.unit is Unit.DAY:
        return count_start + timedelta(days=period.amount - 1), []
    if period.unit is Unit.WEEK:
        return count_start + timedelta(weeks=period.amount) - timedelta(days=1), ["민법 제160조 제1항·제2항"]
    months = period.amount * (12 if period.unit is Unit.YEAR else 1)
    corresponding, exists = _add_months(count_start, months)
    if exists:
        return corresponding - timedelta(days=1), ["민법 제160조 제1항·제2항"]
    return corresponding, ["민법 제160조 제1항·제3항"]


def compute_deadline(
    event_date: date,
    period: Period,
    calendar: HolidayCalendar,
    *,
    starts_at_midnight: bool = False,
    extra_basis: tuple[str, ...] = (),
    warnings: tuple[str, ...] = (),
) -> DeadlineResult:
    """event_date부터 period 동안의 기간 만료일을 계산한다.

    starts_at_midnight는 기간이 오전 영시부터 시작하는 경우(민법 제157조 단서)에만 True로 둔다.
    """
    if starts_at_midnight:
        count_start = event_date
        basis = ["민사소송법 제170조", "민법 제157조 단서"]
    else:
        count_start = event_date + timedelta(days=1)
        basis = ["민사소송법 제170조", "민법 제157조 본문"]

    nominal_end, calendar_basis = _nominal_end(count_start, period)
    basis += calendar_basis
    basis.append("민법 제159조")

    deadline = nominal_end
    extended: list[tuple[date, str]] = []
    while (reason := calendar.closed_reason(deadline)) is not None:
        extended.append((deadline, reason))
        deadline += timedelta(days=1)
    if extended:
        basis.append("민법 제161조")

    return DeadlineResult(
        event_date=event_date,
        period=period,
        count_start=count_start,
        nominal_end=nominal_end,
        deadline=deadline,
        extended_over=tuple(extended),
        basis=tuple(extra_basis) + tuple(basis),
        warnings=warnings,
    )
