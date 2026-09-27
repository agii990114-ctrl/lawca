"""문서 종류별 기본 동작: 제안할 기한과 기본 체크리스트. 규칙 기반이며 LLM을 쓰지 않는다."""

from __future__ import annotations

from dataclasses import dataclass

from lawca.deadlines import Period, Unit, load_rules
from lawca.extraction.schema import CourtDocument, DocumentType

UNITS = {unit.value: unit for unit in Unit}


@dataclass(frozen=True)
class DeadlineSuggestion:
    kind: str
    """'statutory'(법정 기간) 또는 'designated'(문서가 정한 기간)."""
    label: str
    rule_id: str | None = None
    period: Period | None = None
    note: str | None = None


STATUTORY_BY_TYPE: dict[DocumentType, list[tuple[str, str | None]]] = {
    DocumentType.JUDGMENT: [
        ("appeal", "1심 판결이면 항소입니다."),
        ("final_appeal", "항소심 판결이면 상고입니다."),
    ],
    DocumentType.DECISION: [("immediate_appeal", "즉시항고를 할 수 있는 결정인지 확인하세요.")],
    DocumentType.PAYMENT_ORDER: [("payment_order_objection", None)],
    DocumentType.SETTLEMENT_RECOMMENDATION: [("settlement_recommendation_objection", None)],
    DocumentType.COMPLAINT_COPY: [("answer", None)],
}

CHECKLIST_BY_TYPE: dict[DocumentType, list[str]] = {
    DocumentType.CORRECTION_ORDER: [
        "보정명령 내용을 담당 변호사에게 보고",
        "보정에 필요한 서류 확인·준비(명령 내용 참조)",
        "보정기한 안에 보정서 제출",
    ],
    DocumentType.JUDGMENT: [
        "판결 주문을 담당 변호사에게 보고",
        "항소(상고) 여부 결정 요청",
        "불복기간 기한 등록",
    ],
    DocumentType.DECISION: [
        "결정 내용을 담당 변호사에게 보고",
        "불복 가능 여부와 기간 확인",
    ],
    DocumentType.PAYMENT_ORDER: [
        "지급명령 내용을 담당 변호사에게 보고",
        "이의신청 여부 결정 요청",
        "이의신청 기한 등록",
    ],
    DocumentType.SETTLEMENT_RECOMMENDATION: [
        "화해권고결정 내용을 담당 변호사에게 보고",
        "이의신청 여부 결정 요청",
        "이의신청 기한 등록",
    ],
    DocumentType.COMPLAINT_COPY: [
        "소장 부본 수령을 담당 변호사에게 보고",
        "답변서 제출 기한 등록",
    ],
    DocumentType.HEARING_NOTICE: [
        "캘린더에 올라간 기일(미확정)을 원문과 대조해 확정",
        "담당 변호사에게 기일 보고",
    ],
    DocumentType.OTHER: ["문서 내용을 담당 변호사에게 보고"],
}


def suggest_deadlines(doc: CourtDocument) -> list[DeadlineSuggestion]:

    rules = load_rules()
    suggestions = [
        DeadlineSuggestion("statutory", rules[rule_id].name, rule_id=rule_id, period=rules[rule_id].period, note=note)
        for rule_id, note in STATUTORY_BY_TYPE.get(doc.document_type, [])
    ]
    if doc.designated_period is not None:
        period = Period(doc.designated_period.amount, UNITS[doc.designated_period.unit])
        suggestions.insert(0, DeadlineSuggestion("designated", f"문서가 정한 기간({period})", period=period))
    return suggestions


def checklist(doc: CourtDocument) -> list[str]:
    return list(CHECKLIST_BY_TYPE.get(doc.document_type, CHECKLIST_BY_TYPE[DocumentType.OTHER]))
