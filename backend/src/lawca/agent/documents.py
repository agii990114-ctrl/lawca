"""문서 처리: 첨부한 법원 문서 PDF를 읽고 검증해 결과 카드를 만든다."""

from __future__ import annotations

from dataclasses import asdict
from datetime import date

from lawca.api.schemas import DocumentOut, IssueOut, PeriodOut, SuggestionOut
from lawca.db.models import File
from lawca.extraction.gemini import Extractor
from lawca.extraction.validate import has_text, pdf_text_pages, validate
from lawca.workflow import checklist, suggest_deadlines


def analyze(stored: File, extractor: Extractor, today: date) -> DocumentOut:
    doc = extractor.extract(stored.data)
    pages = pdf_text_pages(stored.data)
    return DocumentOut(
        file_id=str(stored.id),
        filename=stored.name,
        model=extractor.model,
        text_available=has_text(pages),
        extraction=doc,
        issues=[IssueOut(**asdict(i)) for i in validate(doc, pages, today)],
        suggestions=[
            SuggestionOut(
                kind=s.kind,
                label=s.label,
                rule_id=s.rule_id,
                period=PeriodOut.of(s.period) if s.period else None,
                note=s.note,
            )
            for s in suggest_deadlines(doc)
        ],
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
    if result.suggestions:
        lines.append("송달일은 문서에 적혀 있지 않습니다. 아래에 송달일을 입력하면 기한을 계산합니다.")
    return "\n\n".join(lines)
