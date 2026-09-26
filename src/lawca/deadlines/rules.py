"""법정 기간 규칙(data/rules.toml)과 기한 계산 진입점."""

from __future__ import annotations

import tomllib
from dataclasses import dataclass
from datetime import date
from functools import cache
from importlib import resources

from lawca.deadlines.calendar import HolidayCalendar
from lawca.deadlines.period import DeadlineResult, Period, Unit, compute_deadline

DEEMED_ELECTRONIC_SERVICE_WARNING = (
    "전자소송 간주 송달(민사소송 등에서의 전자문서 이용 등에 관한 법률 제11조 제4항 단서)입니다. "
    "간주 송달일과 초일 산입 여부(민법 제157조 단서)는 이 계산이 판단하지 않았습니다. 확정 전에 확인하세요."
)


@dataclass(frozen=True)
class Rule:
    id: str
    name: str
    trigger: str
    period: Period
    unchangeable: bool
    """불변기간 여부. 불변기간은 법원이 늘이거나 줄일 수 없다(민사소송법 제172조 제1항)."""
    basis: tuple[str, ...]
    excerpt: str
    law_version: str
    verified_on: date
    note: str | None = None


@cache
def load_rules() -> dict[str, Rule]:
    raw = resources.files("lawca.deadlines").joinpath("data/rules.toml").read_text(encoding="utf-8")
    units = {unit.value: unit for unit in Unit}
    rules: dict[str, Rule] = {}
    for item in tomllib.loads(raw)["rule"]:
        rule = Rule(
            id=item["id"],
            name=item["name"],
            trigger=item["trigger"],
            period=Period(item["amount"], units[item["unit"]]),
            unchangeable=item["unchangeable"],
            basis=tuple(item["basis"]),
            excerpt=item["excerpt"],
            law_version=item["law_version"],
            verified_on=item["verified_on"],
            note=item.get("note"),
        )
        if rule.id in rules:
            raise ValueError(f"규칙 id가 중복됩니다: {rule.id}")
        rules[rule.id] = rule
    return rules


def _service_warnings(deemed_electronic_service: bool) -> tuple[str, ...]:
    return (DEEMED_ELECTRONIC_SERVICE_WARNING,) if deemed_electronic_service else ()


def compute_statutory_deadline(
    rule_id: str,
    event_date: date,
    calendar: HolidayCalendar | None = None,
    *,
    deemed_electronic_service: bool = False,
) -> DeadlineResult:
    """법정 기간(항소, 즉시항고 등)의 만료일을 계산한다. event_date는 규칙의 trigger에 해당하는 날이다."""
    rules = load_rules()
    if rule_id not in rules:
        raise KeyError(f"알 수 없는 규칙입니다: {rule_id}. 사용 가능: {', '.join(rules)}")
    rule = rules[rule_id]
    warnings = _service_warnings(deemed_electronic_service)
    if rule.note:
        warnings += (rule.note,)
    return compute_deadline(
        event_date,
        rule.period,
        calendar or HolidayCalendar.load_default(),
        extra_basis=rule.basis,
        warnings=warnings,
    )


def compute_designated_deadline(
    event_date: date,
    period: Period,
    calendar: HolidayCalendar | None = None,
    *,
    deemed_electronic_service: bool = False,
) -> DeadlineResult:
    """재판장·법원이 정한 기간(예: 보정명령의 '송달받은 날부터 7일 이내')의 만료일을 계산한다.

    period는 문서에 기재된 기간이다. 법정 기간이 아니므로 규칙 표를 쓰지 않는다.
    """
    return compute_deadline(
        event_date,
        period,
        calendar or HolidayCalendar.load_default(),
        extra_basis=("재판장 또는 법원이 정한 기간",),
        warnings=_service_warnings(deemed_electronic_service),
    )
