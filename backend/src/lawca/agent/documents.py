"""문서 처리: 첨부한 법원 문서 PDF를 읽고 검증해 결과 카드를 만든다."""

from __future__ import annotations

from dataclasses import asdict
from datetime import date

from lawca.api.schemas import DocumentOut, IssueOut, PeriodOut, SuggestionOut
from lawca.db.models import File
from lawca.extraction.gemini import Extractor
from lawca.extraction.normalize import normalize
from lawca.extraction.schema import CourtDocument
from lawca.extraction.validate import has_text, pdf_text_pages, validate
from lawca.workflow import checklist, suggest_deadlines


def suggestions_out(doc: CourtDocument) -> list[SuggestionOut]:
    return [
        SuggestionOut(
            kind=s.kind,
            key=s.rule_id or f"designated:{doc.document_type.value}",
            label=s.label,
            rule_id=s.rule_id,
            period=PeriodOut.of(s.period) if s.period else None,
            note=s.note,
        )
        for s in suggest_deadlines(doc)
    ]


def analyze(stored: File, extractor: Extractor, today: date) -> DocumentOut:
    doc = normalize(extractor.extract(stored.data))
    pages = pdf_text_pages(stored.data)
    return DocumentOut(
        file_id=str(stored.id),
        filename=stored.name,
        model=extractor.model,
        text_available=has_text(pages),
        extraction=doc,
        issues=[IssueOut(**asdict(i)) for i in validate(doc, pages, today)],
        suggestions=suggestions_out(doc),
        checklist=checklist(doc),
    )


def summarize(result: DocumentOut) -> str:
    doc = result.extraction
    lines = [f"**{result.filename}**: **{doc.document_type.value}**(으)로 읽었습니다."]
    where = " ".join(v.value for v in (doc.court, doc.case_number, doc.case_name) if v is not None)
    if where:
        lines.append(f"사건: {where}")
    errors = sum(1 for i in result.issues if i.level == "error")
    if result.issues:
        lines.append(f"확인이 필요한 항목이 {len(result.issues)}건 있습니다(오류 {errors}건 포함).")
    if doc.hearing is not None:
        when = " ".join(v for v in (doc.hearing.date, doc.hearing.time) if v)
        lines.append(
            f"{doc.hearing.kind or '기일'}({when})을 읽었습니다. 기한 목록의 **대기** 탭에서 원문과 대조해 "
            "확정하면 캘린더에 올라갑니다."
        )
    if result.suggestions:
        lines.append("송달일은 문서에 적혀 있지 않습니다. 아래에 송달일을 입력하면 기한을 계산합니다.")
    return "\n\n".join(lines)
