"""기한 계산. LLM을 쓰지 않는 순수 함수로, 결과에는 적용한 조문을 함께 돌려준다."""

from lawca.deadlines.calendar import CalendarCoverageError, HolidayCalendar
from lawca.deadlines.period import DeadlineResult, Period, Unit, compute_deadline
from lawca.deadlines.rules import Rule, compute_designated_deadline, compute_statutory_deadline, load_rules

__all__ = [
    "CalendarCoverageError",
    "DeadlineResult",
    "HolidayCalendar",
    "Period",
    "Rule",
    "Unit",
    "compute_deadline",
    "compute_designated_deadline",
    "compute_statutory_deadline",
    "load_rules",
]
