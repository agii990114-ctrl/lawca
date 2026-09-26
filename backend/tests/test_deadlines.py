"""기한 계산 테스트. 기대값은 코드가 아니라 조문을 손으로 적용해 구했다.

요일: 2026-09-01(화), 2026-09-05(토), 2026-09-10(목), 2026-04-17(금), 2026-05-01(금), 2025-05-01(목),
2026-07-03(금), 2026-07-17(금), 2026-02-28(토), 2026-03-01(일), 2026-08-16(일), 2026-09-19(토), 2029-02-28(수).
"""

from datetime import date

import pytest

from lawca.deadlines import (
    CalendarCoverageError,
    HolidayCalendar,
    Period,
    Unit,
    compute_deadline,
    compute_designated_deadline,
    compute_statutory_deadline,
    load_rules,
)

CAL = HolidayCalendar.load_default()
D = date.fromisoformat


def deadline(event: str, amount: int, unit: Unit, **kwargs) -> date:
    return compute_deadline(D(event), Period(amount, unit), CAL, **kwargs).deadline


# 민법 제157조: 초일 불산입


def test_days_exclude_first_day():
    # 9/1 송달, 7일 → 기산일 9/2, 말일 9/8(화)
    result = compute_deadline(D("2026-09-01"), Period(7, Unit.DAY), CAL)
    assert result.count_start == D("2026-09-02")
    assert result.deadline == D("2026-09-08")
    assert "민법 제157조 본문" in result.basis


def test_period_starting_at_midnight_includes_first_day():
    # 민법 제157조 단서: 오전 영시부터 시작하면 초일 산입 → 9/1~9/7
    result = compute_deadline(D("2026-09-01"), Period(7, Unit.DAY), CAL, starts_at_midnight=True)
    assert result.count_start == D("2026-09-01")
    assert result.deadline == D("2026-09-07")
    assert "민법 제157조 단서" in result.basis


# 민법 제160조: 역에 의한 계산


def test_weeks_end_on_day_before_corresponding_day():
    # 9/1 송달, 2주 → 기산일 9/2(수), 2주 뒤 해당일 9/16의 전일 9/15(화)
    assert deadline("2026-09-01", 2, Unit.WEEK) == D("2026-09-15")


def test_month_with_corresponding_day():
    # 기산일 1/28 → 2/28의 전일 2/27(금)
    assert deadline("2026-01-27", 1, Unit.MONTH) == D("2026-02-27")


def test_month_starting_on_first_day_ends_on_last_day():
    # 기산일 3/1(월의 처음) → 4/1의 전일 3/31(화)
    assert deadline("2026-02-28", 1, Unit.MONTH) == D("2026-03-31")


def test_month_without_corresponding_day_ends_on_month_end_then_extends():
    # 기산일 1/31 → 2월에 31일이 없음 → 2월 말일 2/28(토) [제160조 제3항]
    # → 3/1(일, 삼일절) → 3/2(월, 삼일절 대체공휴일) → 3/3(화) [제161조]
    result = compute_deadline(D("2026-01-30"), Period(1, Unit.MONTH), CAL)
    assert result.nominal_end == D("2026-02-28")
    assert result.deadline == D("2026-03-03")
    assert [day for day, _ in result.extended_over] == [D("2026-02-28"), D("2026-03-01"), D("2026-03-02")]
    assert "민법 제160조 제1항·제3항" in result.basis


def test_year_from_leap_day_ends_on_february_28():
    # 기산일 2028-02-29 → 2029년 2월에 29일이 없음 → 2029-02-28(수)
    cal = HolidayCalendar({}, range(2028, 2030))
    result = compute_deadline(D("2028-02-28"), Period(1, Unit.YEAR), cal)
    assert result.deadline == D("2029-02-28")


# 민법 제161조: 토요일·공휴일 연장


def test_saturday_and_sunday_extend_to_monday():
    # 9/5(토) 송달, 1주 → 말일 9/12(토) → 9/13(일) → 9/14(월)
    result = compute_deadline(D("2026-09-05"), Period(1, Unit.WEEK), CAL)
    assert result.deadline == D("2026-09-14")
    assert result.extended_over == ((D("2026-09-12"), "토요일"), (D("2026-09-13"), "일요일"))
    assert "민법 제161조" in result.basis


def test_chuseok_holidays_extend_deadline():
    # 9/10 송달, 2주 → 말일 9/24(추석 전날) → 9/25 추석 → 9/26(토) → 9/27(일) → 9/28(월)
    assert deadline("2026-09-10", 2, Unit.WEEK) == D("2026-09-28")


def test_substitute_holiday_extends_deadline():
    # 8/2 송달, 2주 → 말일 8/16(일) → 8/17(광복절 대체공휴일) → 8/18(화)
    assert deadline("2026-08-02", 2, Unit.WEEK) == D("2026-08-18")


def test_labor_day_is_holiday_from_2026():
    # 관공서 공휴일 규정 개정(2026. 5. 1. 시행)으로 노동절이 공휴일이 됨
    # 2026: 말일 5/1(금, 노동절) → 5/2(토) → 5/3(일) → 5/4(월)
    assert deadline("2026-04-17", 2, Unit.WEEK) == D("2026-05-04")
    # 2025: 5/1(목)은 공휴일이 아니므로 연장 없음
    assert deadline("2025-04-17", 2, Unit.WEEK) == D("2025-05-01")


def test_constitution_day_is_holiday_from_2026():
    # 말일 7/17(금, 제헌절) → 7/18(토) → 7/19(일) → 7/20(월)
    assert deadline("2026-07-03", 2, Unit.WEEK) == D("2026-07-20")


# 달력 범위


def test_date_outside_holiday_data_is_rejected():
    with pytest.raises(CalendarCoverageError):
        deadline("2030-01-02", 7, Unit.DAY)


def test_period_must_be_positive():
    with pytest.raises(ValueError):
        Period(0, Unit.DAY)


# 법정 기간 규칙


def test_every_rule_has_basis_and_verification():
    rules = load_rules()
    assert {"appeal", "final_appeal", "immediate_appeal", "payment_order_objection", "answer"} <= rules.keys()
    for rule in rules.values():
        assert rule.basis and rule.excerpt and rule.law_version
        assert rule.verified_on == D("2026-09-26")


def test_appeal_deadline():
    result = compute_statutory_deadline("appeal", D("2026-09-01"), CAL)
    assert result.deadline == D("2026-09-15")
    assert result.basis[0] == "민사소송법 제396조"


def test_immediate_appeal_deadline():
    # 9/10 고지, 1주 → 말일 9/17(목)
    assert compute_statutory_deadline("immediate_appeal", D("2026-09-10"), CAL).deadline == D("2026-09-17")


def test_answer_deadline_is_30_days():
    # 8/20 송달, 30일 → 기산일 8/21, 말일 9/19(토) → 9/20(일) → 9/21(월)
    result = compute_statutory_deadline("answer", D("2026-08-20"), CAL)
    assert result.nominal_end == D("2026-09-19")
    assert result.deadline == D("2026-09-21")
    assert any("공시송달" in w for w in result.warnings)


def test_unknown_rule_is_rejected():
    with pytest.raises(KeyError):
        compute_statutory_deadline("no_such_rule", D("2026-09-01"), CAL)


def test_deemed_electronic_service_is_flagged():
    result = compute_statutory_deadline("appeal", D("2026-09-01"), CAL, deemed_electronic_service=True)
    assert any("간주 송달" in w for w in result.warnings)


def test_designated_period_from_court_order():
    # 보정명령 "송달받은 날부터 7일 이내", 9/1 송달 → 9/8
    result = compute_designated_deadline(D("2026-09-01"), Period(7, Unit.DAY), CAL)
    assert result.deadline == D("2026-09-08")
    assert result.basis[0] == "재판장 또는 법원이 정한 기간"
