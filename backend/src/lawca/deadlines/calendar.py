"""관공서 공휴일 달력. 기간 만료일을 미룰지(민법 제161조) 판단하는 데 쓴다."""

from __future__ import annotations

import csv
from collections.abc import Iterable, Mapping
from datetime import date
from importlib import resources

SATURDAY = 5
SUNDAY = 6


class CalendarCoverageError(ValueError):
    """공휴일 데이터가 없는 연도의 날짜를 판단하려 할 때 발생한다.

    데이터가 없는 연도를 평일로 간주하면 만료일이 조용히 틀어지므로 계산을 거부한다.
    """


class HolidayCalendar:
    """관공서의 공휴일에 관한 규정 제2조의 공휴일과 토요일을 판단한다.

    일요일은 규정 제2조 제1호에 따라 항상 공휴일이다. 그 밖의 공휴일은 데이터로 받는다.
    """

    def __init__(self, holidays: Mapping[date, str], years: Iterable[int]) -> None:
        self._holidays = dict(holidays)
        self._years = frozenset(years)
        outside = sorted({d.year for d in self._holidays} - self._years)
        if outside:
            raise ValueError(f"공휴일 데이터에 범위 밖 연도가 있습니다: {outside}")

    @classmethod
    def load_default(cls) -> HolidayCalendar:
        """패키지에 포함된 kr_holidays.csv를 읽는다. 데이터가 있는 연도만 판단할 수 있다."""
        text = resources.files("lawca.deadlines").joinpath("data/kr_holidays.csv").read_text(encoding="utf-8")
        rows = csv.DictReader(line for line in text.splitlines() if not line.startswith("#"))
        holidays = {date.fromisoformat(row["date"]): row["name"] for row in rows}
        years = range(min(d.year for d in holidays), max(d.year for d in holidays) + 1)
        return cls(holidays, years)

    @property
    def years(self) -> frozenset[int]:
        return self._years

    def _check(self, day: date) -> None:
        if day.year not in self._years:
            covered = f"{min(self._years)}~{max(self._years)}년" if self._years else "없음"
            raise CalendarCoverageError(f"{day.year}년 공휴일 데이터가 없습니다(데이터 범위: {covered}).")

    def holiday_name(self, day: date) -> str | None:
        """공휴일이면 이름을, 아니면 None을 돌려준다. 토요일은 공휴일이 아니다."""
        self._check(day)
        if day in self._holidays:
            return self._holidays[day]
        if day.weekday() == SUNDAY:
            return "일요일"
        return None

    def holidays_between(self, start: date, end: date) -> list[tuple[date, str]]:
        """start~end(포함) 사이의 공휴일(일요일 제외). 데이터가 없는 연도는 건너뛴다."""
        return sorted((d, name) for d, name in self._holidays.items() if start <= d <= end)

    def closed_reason(self, day: date) -> str | None:
        """민법 제161조의 '토요일 또는 공휴일'이면 그 사유를, 아니면 None을 돌려준다."""
        name = self.holiday_name(day)
        if name is not None:
            return name
        if day.weekday() == SATURDAY:
            return "토요일"
        return None
