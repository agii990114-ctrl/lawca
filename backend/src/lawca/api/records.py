"""DB 행을 API 응답 형식으로 바꾼다. API와 에이전트 도구가 함께 쓴다."""

from __future__ import annotations

from lawca.api.schemas import SERVICE_LABELS, DeadlineRecordOut, PeriodOut
from lawca.db.repo import key_of
from lawca.db.models import Deadline
from lawca.deadlines import Period, Unit

UNITS = {u.value: u for u in Unit}


def record_out(d: Deadline) -> DeadlineRecordOut:
    return DeadlineRecordOut(
        id=str(d.id),
        status=d.status,  # type: ignore[arg-type]
        key=key_of(d),
        label=d.label,
        kind=d.kind,
        rule_id=d.rule_id,
        period=PeriodOut.of(Period(d.period_amount, UNITS[d.period_unit])),
        event_date=d.event_date,
        service_kind=d.service_kind,
        service_label=SERVICE_LABELS.get(d.service_kind, d.service_kind),
        count_start=d.count_start,
        nominal_end=d.nominal_end,
        deadline=d.deadline,
        extended_over=d.extended_over,
        basis=d.basis,
        warnings=d.warnings,
        created_at=d.created_at,
        confirmed_by=d.confirmed_by,
        status_changed_at=d.status_changed_at,
        document_id=str(d.document_id),
        document_type=d.document.document_type,
        file_id=str(d.document.file_id),
        filename=d.document.file.name,
        case_number=d.case.case_number if d.case else None,
        court=d.case.court if d.case else None,
        case_name=d.case.case_name if d.case else None,
    )
