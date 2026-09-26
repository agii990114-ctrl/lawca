"""추출 검증, 문서 종류별 제안, 예비 모델. Gemini는 호출하지 않는다."""

from datetime import date

import pytest

from lawca.extraction.schema import CourtDocument, DesignatedPeriod, DocumentType, Evidence, Party, TextField
from lawca.extraction.validate import validate
from lawca.workflow import checklist, suggest_deadlines

PAGE = (
    "서울중앙지방법원\n보 정 명 령\n사 건 2026가단51234 대여금\n원 고 홍길동\n피 고 김철수\n"
    "원고는 이 명령을 송달받은 날부터 7일 이내에 피고의 주소를 보정하시기 바랍니다.\n2026. 9. 15.\n판사 이영희"
)


def ev(quote: str, page: int = 1) -> Evidence:
    return Evidence(quote=quote, page=page)


def correction_order(**overrides) -> CourtDocument:
    fields = dict(
        document_type=DocumentType.CORRECTION_ORDER,
        document_type_evidence=ev("보 정 명 령"),
        court=TextField(value="서울중앙지방법원", evidence=ev("서울중앙지방법원")),
        case_number=TextField(value="2026가단51234", evidence=ev("2026가단51234")),
        case_name=TextField(value="대여금", evidence=ev("대여금")),
        parties=[Party(role="원고", name="홍길동", evidence=ev("원 고 홍길동"))],
        issued_date=TextField(value="2026-09-15", evidence=ev("2026. 9. 15.")),
        order_summary=TextField(value="피고 주소 보정", evidence=ev("피고의 주소를 보정하시기 바랍니다")),
        designated_period=DesignatedPeriod(amount=7, unit="일", evidence=ev("송달받은 날부터 7일 이내")),
    )
    return CourtDocument(**(fields | overrides))


TODAY = date(2026, 9, 26)


def fields_of(issues, level=None):
    return {i.field for i in issues if level is None or i.level == level}


def test_valid_document_has_no_issues():
    assert validate(correction_order(), [PAGE], TODAY) == []


def test_quote_matching_ignores_whitespace():
    # 원문은 "보 정 명 령", 근거는 공백 없이 적어도 일치로 본다
    doc = correction_order(document_type_evidence=ev("보정명령"))
    assert validate(doc, [PAGE], TODAY) == []


def test_quote_not_in_source_is_error():
    doc = correction_order(case_name=TextField(value="손해배상", evidence=ev("손해배상(기)")))
    assert "case_name" in fields_of(validate(doc, [PAGE], TODAY), "error")


def test_quote_on_other_page_is_warning():
    doc = correction_order(court=TextField(value="서울중앙지방법원", evidence=ev("서울중앙지방법원", page=2)))
    issues = validate(doc, [PAGE, "2쪽 내용"], TODAY)
    assert fields_of(issues) == {"court"}
    assert issues[0].level == "warning"


def test_malformed_case_number_is_error():
    doc = correction_order(case_number=TextField(value="가단51234", evidence=ev("2026가단51234")))
    assert "case_number" in fields_of(validate(doc, [PAGE], TODAY), "error")


def test_future_issue_date_is_error():
    doc = correction_order(issued_date=TextField(value="2026-10-01", evidence=ev("2026. 9. 15.")))
    assert "issued_date" in fields_of(validate(doc, [PAGE], TODAY), "error")


def test_scanned_pdf_cannot_be_cross_checked():
    issues = validate(correction_order(), [""], TODAY)
    assert [(i.field, i.level) for i in issues] == [("document", "warning")]


def test_correction_order_without_period_warns():
    doc = correction_order(designated_period=None)
    assert "designated_period" in fields_of(validate(doc, [PAGE], TODAY), "warning")


def test_suggestions_for_correction_order_use_document_period():
    suggestions = suggest_deadlines(correction_order())
    assert [(s.kind, str(s.period)) for s in suggestions] == [("designated", "7일")]
    assert checklist(correction_order())[-1] == "보정기한 안에 보정서 제출"


def test_suggestions_for_judgment_offer_appeal_and_final_appeal():
    doc = correction_order(document_type=DocumentType.JUDGMENT, designated_period=None)
    assert [s.rule_id for s in suggest_deadlines(doc)] == ["appeal", "final_appeal"]


# 예비 모델


def test_extractor_falls_back_when_model_is_unavailable(monkeypatch):
    from lawca.extraction.gemini import GeminiExtractor, ModelUnavailableError

    extractor = GeminiExtractor("test-key", ["busy", "ok"])
    calls = []

    def fake(model, pdf):
        calls.append(model)
        if model == "busy":
            raise ModelUnavailableError("busy")
        return correction_order()

    monkeypatch.setattr(extractor, "_extract_with", fake)
    extractor.extract(b"%PDF")
    assert calls == ["busy", "ok"]
    assert extractor.model == "ok"


def test_extractor_raises_when_every_model_is_unavailable(monkeypatch):
    from lawca.extraction.gemini import GeminiExtractor, ModelUnavailableError

    extractor = GeminiExtractor("test-key", ["a", "b"])

    def busy(model, pdf):
        raise ModelUnavailableError(model)

    monkeypatch.setattr(extractor, "_extract_with", busy)
    with pytest.raises(ModelUnavailableError):
        extractor.extract(b"%PDF")


# 정규화


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("2026. 9. 15.", "2026-09-15"),
        ("2026.09.15", "2026-09-15"),
        ("2026년 9월 15일", "2026-09-15"),
        ("2026-09-15", "2026-09-15"),
        ("2026. 2. 30.", "2026. 2. 30."),  # 없는 날짜는 그대로 두고 검증 단계가 잡는다
        ("9월 15일", "9월 15일"),
    ],
)
def test_normalize_korean_dates(raw, expected):
    from lawca.extraction.normalize import normalize_date

    assert normalize_date(raw) == expected


def test_normalize_keeps_evidence_and_fixes_values():
    from lawca.extraction.normalize import normalize

    doc = correction_order(
        issued_date=TextField(value="2026. 9. 15.", evidence=ev("2026. 9. 15.")),
        case_number=TextField(value="2026가단 51234", evidence=ev("2026가단51234")),
    )
    fixed = normalize(doc)
    assert fixed.issued_date.value == "2026-09-15" and fixed.issued_date.evidence.quote == "2026. 9. 15."
    assert fixed.case_number.value == "2026가단51234"
    assert validate(fixed, [PAGE], TODAY) == []
