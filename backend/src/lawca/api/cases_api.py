"""사건 API: 사건 목록·상세, 증거 목록(갑호증·을호증), 판례 후보. 준비서면 작성은 채팅(lawca.agent.brief_flow)에서 한다."""

from __future__ import annotations

import re
from datetime import date, datetime
from typing import Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from lawca.agent.brief import evidence_label, number_key
from lawca.api.deps import DB, Lawyer, Worker
from lawca.api.embedding import EmbedderFactory
from lawca.api.llm_deps import LawApiDep
from lawca.citations import find as find_citations
from lawca.lawapi import LawApiError
from lawca.api.records import record_out
from lawca.api.schemas import DeadlineRecordOut
from lawca.db import repo
from lawca.db.models import Case, EvidenceItem

router = APIRouter()
Side = Literal["갑", "을", "병"]
NUMBER = re.compile(r"^\d{1,3}(-\d{1,3})?$")


class CaseSummaryOut(BaseModel):
    case_number: str
    court: str | None
    case_name: str | None
    parties: list[dict[str, str]]
    documents: int
    open_deadlines: int
    evidence: int


class CaseDocumentOut(BaseModel):
    document_id: str
    document_type: str
    issued_date: date | None
    filename: str
    file_id: str
    created_at: datetime
    summary: dict | None


class EvidenceOut(BaseModel):
    id: str
    side: Side
    number: str
    label: str
    title: str
    note: str
    submitted_on: date | None
    from_summary: bool
    created_by: str


class CaseDetailOut(CaseSummaryOut):
    facts: dict
    document_list: list[CaseDocumentOut]
    deadlines: list[DeadlineRecordOut]
    evidence_list: list[EvidenceOut]


class EvidenceCreate(BaseModel):
    side: Side
    number: str | None = Field(default=None, description="비우면 다음 번호")
    title: str = Field(min_length=1, max_length=300)
    note: str = Field(default="", max_length=2000)


class EvidenceUpdate(BaseModel):
    number: str | None = None
    title: str | None = Field(default=None, min_length=1, max_length=300)
    note: str | None = Field(default=None, max_length=2000)
    submitted_on: date | None = None
    clear_submitted: bool = False


class EvidenceBulk(BaseModel):
    source_document_id: str | None = None
    items: list[dict[str, str]]
    """[{side, number, title}] — 상대방 서면 요약의 증거."""


def _case(session: Session, case_number: str) -> Case:
    case = repo.get_case(session, case_number)
    if case is None:
        raise HTTPException(404, f"사건을 찾을 수 없습니다: {case_number}")
    return case


def _parties(case: Case) -> list[dict[str, str]]:
    return [{"role": p.role, "name": p.name} for p in case.parties]


def evidence_out(e: EvidenceItem) -> EvidenceOut:
    return EvidenceOut(
        id=str(e.id), side=e.side, number=e.number, label=evidence_label(e.side, e.number), title=e.title,  # type: ignore[arg-type]
        note=e.note, submitted_on=e.submitted_on, from_summary=e.source_document_id is not None, created_by=e.created_by,
    )


def summary_out(session: Session, case: Case) -> CaseSummaryOut:
    return CaseSummaryOut(
        case_number=case.case_number,
        court=case.court,
        case_name=case.case_name,
        parties=_parties(case),
        documents=len(case.documents),
        open_deadlines=len(repo.list_deadlines(session, ["confirmed"], case_number=case.case_number)),
        evidence=len(repo.list_evidence(session, case)),
    )


def _number(value: str) -> str:
    number = re.sub(r"\s+", "", value).replace("의", "-")
    if not NUMBER.match(number):
        raise HTTPException(422, f"증거 번호 형식이 아닙니다: {value} (예: 3, 2-1)")
    return number


@router.get("/api/cases")
def cases(_: Worker, session: DB, q: str | None = None) -> list[CaseSummaryOut]:
    rows = repo.search_cases(session, q, limit=100) if q and q.strip() else repo.recent_cases(session, limit=100)
    full = [repo.get_case(session, c.case_number) for c in rows]
    return [summary_out(session, c) for c in full if c is not None]


@router.get("/api/cases/{case_number}")
def case_detail(case_number: str, _: Worker, session: DB) -> CaseDetailOut:
    case = _case(session, case_number)
    documents = sorted(case.documents, key=lambda d: d.created_at, reverse=True)
    return CaseDetailOut(
        **summary_out(session, case).model_dump(),
        facts=case.facts,
        document_list=[
            CaseDocumentOut(
                document_id=str(d.id), document_type=d.document_type, issued_date=d.issued_date, filename=d.file.name,
                file_id=str(d.file_id), created_at=d.created_at, summary=d.summary,
            )
            for d in documents
        ],
        deadlines=[record_out(d) for d in repo.list_deadlines(session, ["confirmed", "done"], case_number=case.case_number)],
        evidence_list=[evidence_out(e) for e in repo.list_evidence(session, case)],
    )


# 증거 목록


@router.post("/api/cases/{case_number}/evidence", status_code=201)
def add_evidence(case_number: str, req: EvidenceCreate, _: Worker, session: DB) -> EvidenceOut:
    case = _case(session, case_number)
    number = _number(req.number) if req.number and req.number.strip() else repo.next_evidence_number(session, case, req.side)
    if repo.find_evidence(session, case, req.side, number):
        raise HTTPException(409, f"이미 있는 번호입니다: {evidence_label(req.side, number)}")
    item = repo.add_evidence(session, case, side=req.side, number=number, title=req.title, note=req.note)
    session.commit()
    return evidence_out(item)


@router.post("/api/cases/{case_number}/evidence/bulk")
def add_evidence_bulk(case_number: str, req: EvidenceBulk, _: Worker, session: DB) -> dict[str, list[str]]:
    """상대방 서면 요약의 증거를 한꺼번에 넣는다. 이미 있는 번호는 건너뛴다."""
    case = _case(session, case_number)
    source = repo._parse_id(req.source_document_id) if req.source_document_id else None
    added, skipped = [], []
    for raw in req.items:
        side, title = raw.get("side", ""), (raw.get("title") or "").strip()
        if side not in ("갑", "을", "병") or not title:
            continue
        number = _number(raw.get("number", ""))
        label = evidence_label(side, number)
        if repo.find_evidence(session, case, side, number):
            skipped.append(label)
            continue
        repo.add_evidence(session, case, side=side, number=number, title=title, source_document_id=source)
        added.append(label)
    session.commit()
    return {"added": added, "skipped": skipped}


def _evidence(session: Session, evidence_id: str) -> EvidenceItem:
    item = repo.get_evidence(session, evidence_id)
    if item is None:
        raise HTTPException(404, "증거를 찾을 수 없습니다.")
    return item


@router.patch("/api/evidence/{evidence_id}")
def update_evidence(evidence_id: str, req: EvidenceUpdate, _: Worker, session: DB) -> EvidenceOut:
    item = _evidence(session, evidence_id)
    changes: dict[str, object] = {}
    if req.number is not None:
        number = _number(req.number)
        other = repo.find_evidence(session, item.case, item.side, number)
        if other is not None and other.id != item.id:
            raise HTTPException(409, f"이미 있는 번호입니다: {evidence_label(item.side, number)}")
        changes["number"] = number
    if req.title is not None:
        changes["title"] = req.title.strip()
    if req.note is not None:
        changes["note"] = req.note.strip()
    if req.clear_submitted:
        changes["submitted_on"] = None
    elif req.submitted_on is not None:
        changes["submitted_on"] = req.submitted_on
    repo.update_evidence(session, item, changes)
    session.commit()
    return evidence_out(item)


@router.delete("/api/evidence/{evidence_id}", status_code=204)
def delete_evidence(evidence_id: str, _: Worker, session: DB) -> None:
    repo.delete_evidence(session, _evidence(session, evidence_id))
    session.commit()


# 판례 후보(변호사)


class CitationRequest(BaseModel):
    issue: str = Field(min_length=2, max_length=300)
    keywords: list[str] = Field(default=[], max_length=8)


class CitationOut(BaseModel):
    query: str
    """국가법령정보센터에 실제로 보낸 검색어(이름·숫자를 뺀 법률 용어)."""
    items: list[dict]
    statutes: list[dict]


@router.post("/api/cases/{case_number}/citations")
def citations(case_number: str, req: CitationRequest, _: Lawyer, session: DB, api: LawApiDep,
              make_embedder: EmbedderFactory) -> CitationOut:
    """[인용 확인 필요] 자리의 쟁점으로 대법원 판례 후보를 찾는다. 사건 당사자 이름은 검색어에서 뺀다."""
    case = _case(session, case_number)
    forbidden = [p.name for p in case.parties]
    try:
        found = find_citations(api, req.issue, req.keywords, forbidden, make_embedder())
    except LawApiError as exc:
        raise HTTPException(502, str(exc)) from exc
    repo.audit(session, "citation.search", "case", case.id, {"query": found.query, "results": len(found.items)})
    session.commit()
    return CitationOut(query=found.query, items=found.items, statutes=found.statutes)
