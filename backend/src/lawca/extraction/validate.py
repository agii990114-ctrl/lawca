"""추출값 검증. LLM을 쓰지 않는다.

- 형식 검증: 사건번호, 날짜
- 원문 대조: LLM이 댄 근거 문구가 PDF 텍스트에 실제로 있는지
"""

from __future__ import annotations

import io
import re
from dataclasses import dataclass
from datetime import date
from typing import Literal

from pypdf import PdfReader
from pypdf.errors import PdfReadError

from lawca.extraction.schema import CourtDocument, DocumentType, Evidence

CASE_NUMBER = re.compile(r"^\d{4}[가-힣]{1,3}\d{1,7}$")
WHITESPACE = re.compile(r"\s+")


@dataclass(frozen=True)
class Issue:
    field: str
    level: Literal["error", "warning"]
    message: str


def pdf_text_pages(pdf: bytes) -> list[str]:
    """PDF의 쪽별 텍스트. 읽지 못하면 빈 목록을 돌려준다."""
    try:
        reader = PdfReader(io.BytesIO(pdf))
        return [page.extract_text() or "" for page in reader.pages]
    except (PdfReadError, ValueError, OSError):
        return []


def _normalize(text: str) -> str:
    return WHITESPACE.sub("", text)


def has_text(pages: list[str]) -> bool:
    return any(_normalize(p) for p in pages)


def _check_evidence(field: str, evidence: Evidence, pages: list[str]) -> Issue | None:
    quote = _normalize(evidence.quote)
    if not quote:
        return Issue(field, "warning", "근거 문구가 비어 있습니다.")
    normalized = [_normalize(p) for p in pages]
    if 1 <= evidence.page <= len(normalized) and quote in normalized[evidence.page - 1]:
        return None
    found = [i + 1 for i, text in enumerate(normalized) if quote in text]
    if found:
        return Issue(field, "warning", f"근거 문구가 {evidence.page}쪽이 아니라 {found[0]}쪽에 있습니다.")
    return Issue(field, "error", f"근거 문구를 원문에서 찾지 못했습니다: “{evidence.quote}”")


def _evidences(doc: CourtDocument) -> list[tuple[str, Evidence]]:
    items: list[tuple[str, Evidence]] = [("document_type", doc.document_type_evidence)]
    for name in ("court", "case_number", "case_name", "issued_date", "order_summary", "designated_period"):
        value = getattr(doc, name)
        if value is not None:
            items.append((name, value.evidence))
    items += [(f"parties[{i}]", p.evidence) for i, p in enumerate(doc.parties)]
    return items


def validate(doc: CourtDocument, pages: list[str], today: date) -> list[Issue]:
    issues: list[Issue] = []

    if doc.case_number is None:
        issues.append(Issue("case_number", "warning", "사건번호를 찾지 못했습니다. 직접 입력하세요."))
    elif not CASE_NUMBER.match(_normalize(doc.case_number.value)):
        issues.append(Issue("case_number", "error", f"사건번호 형식이 아닙니다: {doc.case_number.value}"))

    if doc.court is None:
        issues.append(Issue("court", "warning", "법원명을 찾지 못했습니다."))

    if doc.issued_date is not None:
        try:
            issued = date.fromisoformat(doc.issued_date.value)
        except ValueError:
            issues.append(Issue("issued_date", "error", f"날짜 형식이 아닙니다: {doc.issued_date.value}"))
        else:
            if issued > today:
                issues.append(Issue("issued_date", "error", f"발령일이 오늘 이후입니다: {issued}"))

    if doc.document_type is DocumentType.CORRECTION_ORDER and doc.designated_period is None:
        issues.append(Issue("designated_period", "warning", "보정기간을 문서에서 찾지 못했습니다. 직접 입력하세요."))

    if has_text(pages):
        issues += [issue for field, ev in _evidences(doc) if (issue := _check_evidence(field, ev, pages))]
    else:
        issues.append(
            Issue("document", "warning", "텍스트가 없는 PDF(스캔본)라 근거 문구를 원문과 대조하지 못했습니다.")
        )
    return issues
