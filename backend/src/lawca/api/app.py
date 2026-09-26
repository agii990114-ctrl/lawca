"""lawca HTTP API. 로드맵 2단계: 문서 추출, 기한 계산, 체크리스트.

문서는 메모리에만 보관한다. 서버를 다시 띄우면 사라진다(DB는 다음 단계).
"""

from __future__ import annotations

import uuid
from dataclasses import asdict
from datetime import date
from typing import Annotated, Literal

from fastapi import Depends, FastAPI, File, HTTPException, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel

from lawca.config import Settings, get_settings
from lawca.deadlines import (
    CalendarCoverageError,
    DeadlineResult,
    Period,
    Unit,
    compute_designated_deadline,
    compute_statutory_deadline,
    load_rules,
)
from lawca.extraction.gemini import ExtractionError, Extractor, GeminiExtractor, ModelUnavailableError
from lawca.extraction.schema import CourtDocument
from lawca.extraction.validate import has_text, pdf_text_pages, validate
from lawca.workflow import checklist, suggest_deadlines

app = FastAPI(title="lawca API", version="0.1.0")

_documents: dict[str, tuple[str, bytes]] = {}


def get_extractor(settings: Annotated[Settings, Depends(get_settings)]) -> Extractor:
    if not settings.gemini_api_key:
        raise HTTPException(503, "Gemini API 키가 설정되지 않았습니다. 레포 루트 .env에 GEMINI_API를 넣으세요.")
    return GeminiExtractor(settings.gemini_api_key, settings.gemini_models)


class PeriodOut(BaseModel):
    amount: int
    unit: Literal["일", "주", "월", "년"]
    label: str

    @classmethod
    def of(cls, period: Period) -> PeriodOut:
        return cls(amount=period.amount, unit=period.unit.value, label=str(period))


class IssueOut(BaseModel):
    field: str
    level: Literal["error", "warning"]
    message: str


class SuggestionOut(BaseModel):
    kind: Literal["statutory", "designated"]
    label: str
    rule_id: str | None
    period: PeriodOut | None
    note: str | None


class DocumentOut(BaseModel):
    id: str
    filename: str
    model: str
    text_available: bool
    extraction: CourtDocument
    issues: list[IssueOut]
    suggestions: list[SuggestionOut]
    checklist: list[str]


class DeadlineRequest(BaseModel):
    event_date: date
    rule_id: str | None = None
    period: PeriodOut | None = None
    deemed_electronic_service: bool = False


class ExtendedDay(BaseModel):
    day: date
    reason: str


class DeadlineOut(BaseModel):
    event_date: date
    period: PeriodOut
    count_start: date
    nominal_end: date
    deadline: date
    extended_over: list[ExtendedDay]
    basis: list[str]
    warnings: list[str]

    @classmethod
    def of(cls, r: DeadlineResult) -> DeadlineOut:
        return cls(
            event_date=r.event_date,
            period=PeriodOut.of(r.period),
            count_start=r.count_start,
            nominal_end=r.nominal_end,
            deadline=r.deadline,
            extended_over=[ExtendedDay(day=d, reason=why) for d, why in r.extended_over],
            basis=list(r.basis),
            warnings=list(r.warnings),
        )


@app.get("/api/health")
def health(settings: Annotated[Settings, Depends(get_settings)]) -> dict[str, object]:
    return {
        "status": "ok",
        "models": settings.gemini_models,
        "gemini_configured": bool(settings.gemini_api_key),
    }


@app.get("/api/rules")
def rules() -> list[dict[str, object]]:
    return [
        {**asdict(rule), "period": PeriodOut.of(rule.period).model_dump(), "verified_on": rule.verified_on.isoformat()}
        for rule in load_rules().values()
    ]


@app.post("/api/documents")
def upload_document(
    file: Annotated[UploadFile, File()],
    extractor: Annotated[Extractor, Depends(get_extractor)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> DocumentOut:
    pdf = file.file.read(settings.max_upload_mb * 1024 * 1024 + 1)
    if len(pdf) > settings.max_upload_mb * 1024 * 1024:
        raise HTTPException(413, f"{settings.max_upload_mb}MB를 넘는 파일은 올릴 수 없습니다.")
    if not pdf.startswith(b"%PDF"):
        raise HTTPException(415, "PDF 파일만 올릴 수 있습니다.")

    try:
        doc = extractor.extract(pdf)
    except ModelUnavailableError as exc:
        raise HTTPException(503, str(exc)) from exc
    except ExtractionError as exc:
        raise HTTPException(502, str(exc)) from exc

    pages = pdf_text_pages(pdf)
    doc_id = uuid.uuid4().hex
    _documents[doc_id] = (file.filename or "document.pdf", pdf)
    return DocumentOut(
        id=doc_id,
        filename=file.filename or "document.pdf",
        model=extractor.model,
        text_available=has_text(pages),
        extraction=doc,
        issues=[IssueOut(**asdict(i)) for i in validate(doc, pages, date.today())],
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


@app.get("/api/documents/{doc_id}/pdf")
def document_pdf(doc_id: str) -> Response:
    if doc_id not in _documents:
        raise HTTPException(404, "문서를 찾을 수 없습니다. 서버를 다시 띄우면 문서가 사라집니다.")
    _, pdf = _documents[doc_id]
    return Response(pdf, media_type="application/pdf")


@app.post("/api/deadlines")
def deadline(req: DeadlineRequest) -> DeadlineOut:
    if (req.rule_id is None) == (req.period is None):
        raise HTTPException(422, "rule_id와 period 중 하나만 보내야 합니다.")
    try:
        if req.rule_id is not None:
            result = compute_statutory_deadline(
                req.rule_id, req.event_date, deemed_electronic_service=req.deemed_electronic_service
            )
        else:
            assert req.period is not None
            units = {u.value: u for u in Unit}
            result = compute_designated_deadline(
                req.event_date,
                Period(req.period.amount, units[req.period.unit]),
                deemed_electronic_service=req.deemed_electronic_service,
            )
    except KeyError as exc:
        raise HTTPException(404, str(exc.args[0])) from exc
    except (CalendarCoverageError, ValueError) as exc:
        raise HTTPException(422, str(exc)) from exc
    return DeadlineOut.of(result)
