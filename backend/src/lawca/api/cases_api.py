"""사건 API: 사건 목록·상세, 증거 목록(갑호증·을호증), 준비서면 틀."""

from __future__ import annotations

import re
from datetime import date, datetime
from typing import Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from lawca.agent.brief import evidence_label, number_key
from lawca.agent.brief_writer import BriefInputs, body_text, check, note_lines, write_brief
from lawca.api.deps import DB, Lawyer, Worker
from lawca.api.embedding import EmbedderFactory
from lawca.api.llm_deps import LawApiDep, ModelsFactory
from lawca.citations import find as find_citations
from lawca.lawapi import LawApiError
from lawca.api.records import record_out
from lawca.api.schemas import DeadlineRecordOut
from lawca.db import repo
from lawca.db.models import Case, EvidenceItem
from lawca.extraction.gemini import ExtractionError, ModelUnavailableError
from lawca.forms import render_brief
from lawca.library import DOCX_MIME
from lawca.library import store as library_store

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


class BriefRequest(BaseModel):
    side: Literal["원고", "피고"]
    title: str = Field(default="준비서면", min_length=1, max_length=40)
    agent: str = Field(default="", max_length=100, description="소송대리인. 비우면 당사자 본인 이름")
    body: str = Field(default="", max_length=50000, description="변호사가 쓴 본문. 비우면 자리표시")
    evidence_ids: list[str] = []
    attachments: list[str] = []
    filed_on: date | None = None


class BriefOut(BaseModel):
    draft_id: str
    file_id: str
    filename: str
    size: int
    evidence: list[str]
    body_empty: bool


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


# 준비서면 틀


def _paragraphs(body: str) -> list[str]:
    """빈 줄로 문단을 나눈다. 한 문단 안의 줄바꿈은 그대로 둔다."""
    return [p.strip() for p in re.split(r"\n\s*\n", body.strip()) if p.strip()]


@router.post("/api/cases/{case_number}/brief")
def make_brief(case_number: str, req: BriefRequest, _: Worker, session: DB, make_embedder: EmbedderFactory) -> BriefOut:
    """준비서면 틀(DOCX)을 만든다. 사건 정보·입증방법·첨부서류는 기록에서 채우고, 본문은 변호사가 쓴 글을 넣는다."""
    case = _case(session, case_number)
    names = {role: ", ".join(p.name for p in case.parties if p.role == role) for role in ("원고", "피고")}
    ours = "갑" if req.side == "원고" else "을"
    chosen = [repo.get_evidence(session, eid) for eid in req.evidence_ids]
    evidence = [e for e in chosen if e is not None and e.case_id == case.id]
    if len(evidence) != len(req.evidence_ids):
        raise HTTPException(404, "이 사건의 증거가 아닌 것이 있습니다.")
    if any(e.side != ours for e in evidence):
        raise HTTPException(422, f"{req.side} 준비서면에는 {ours}호증만 넣습니다.")
    evidence.sort(key=lambda e: number_key(e.number))
    attachments = ([f"위 입증방법    각 1통"] if evidence else []) + [a.strip() for a in req.attachments if a.strip()]
    agent = req.agent.strip()
    body = _paragraphs(req.body)
    data = render_brief(
        {
            "title": req.title.strip(),
            "side": req.side,
            "representative": "소송대리인" if agent else "",
            "signer": agent or names[req.side],
            "body": body,
            "evidence": [{"label": evidence_label(e.side, e.number), "title": e.title} for e in evidence],
            "attachments": attachments,
            "court": case.court or "",
            "case_number": case.case_number,
            "case_name": case.case_name or "",
            "plaintiffs": names["원고"],
            "defendants": names["피고"],
            "filed_on": (req.filed_on or date.today()).isoformat(),
        }
    )
    filename = f"{req.title.strip()}_{case.case_number}_초안.docx"
    file = repo.save_file(session, filename, data, mime=DOCX_MIME)
    values = {"side": req.side, "title": req.title, "agent": agent, "evidence": [str(e.id) for e in evidence],
              "body_paragraphs": len(body)}
    draft = repo.save_draft(session, case=case, form_id="brief", file=file, values=values,
                            blanks=[] if body else ["본문"], job_id=None)
    repo.remember_facts(session, case, {"our_side": req.side, **({"brief_agent": agent} if agent else {})})
    session.commit()
    try:
        library_store.index_draft(session, str(draft.id), make_embedder())
        session.commit()
    except Exception:  # noqa: BLE001 - 자료실 색인 실패는 초안 작성을 막지 않는다
        session.rollback()
    return BriefOut(
        draft_id=str(draft.id), file_id=str(file.id), filename=filename, size=len(data),
        evidence=[evidence_label(e.side, e.number) for e in evidence], body_empty=not body,
    )


# 준비서면 본문 초안(변호사)


class BriefBodyRequest(BaseModel):
    side: Literal["원고", "피고"]
    notes: str = Field(min_length=1, max_length=10000, description="변호사 메모. 한 줄에 한 가지")
    document_id: str | None = Field(default=None, description="반박할 상대방 서면. 비우면 가장 최근 요약")


class BriefBodyOut(BaseModel):
    body: str
    """준비서면 틀 본문 칸에 넣을 글."""
    sections: list[dict]
    open_points: list[str]
    checks: dict
    opponent_document: str | None
    model: str
    citation_needs: list[dict]
    """[인용 확인 필요] 자리마다 쟁점과 검색어(순서대로)."""
    examples_used: list[dict]
    """문체 참고로 쓴 자료실의 과거 준비서면."""


def _opponent(case: Case, document_id: str | None):  # noqa: ANN202
    with_summary = sorted((d for d in case.documents if d.summary), key=lambda d: d.created_at, reverse=True)
    if document_id:
        return next((d for d in with_summary if str(d.id) == document_id), None)
    return with_summary[0] if with_summary else None


@router.post("/api/cases/{case_number}/brief/body")
def brief_body(
    case_number: str, req: BriefBodyRequest, user: Lawyer, session: DB, make_models: ModelsFactory, make_embedder: EmbedderFactory
) -> BriefBodyOut:
    """변호사 메모를 준비서면 본문 초안으로 풀어 쓴다(변호사만). 저장하지 않고, 변호사가 고쳐 틀에 넣는다."""
    case = _case(session, case_number)
    opponent = _opponent(case, req.document_id)
    if req.document_id and opponent is None:
        raise HTTPException(404, "요약이 있는 상대방 서면을 찾을 수 없습니다.")
    names = {role: ", ".join(p.name for p in case.parties if p.role == role) for role in ("원고", "피고")}
    record = (
        f"사건 {case.case_number} {case.case_name or ''} / {case.court or ''} / 원고 {names['원고']} / 피고 {names['피고']}"
        f" / 우리는 {req.side} 측"
    )
    claims = [f"{c['point']}: {c['detail']}" for c in (opponent.summary or {}).get("claims", [])] if opponent else []
    evidence = [
        f"{evidence_label(e.side, e.number)} {e.title}" + (f"({e.note})" if e.note else "")
        for e in repo.list_evidence(session, case)
    ]
    examples = _style_examples(session, case, claims, make_embedder())
    inputs = BriefInputs(record=record, opponent=claims, evidence=evidence, notes=req.notes,
                         examples=tuple(text for _, text in examples))
    models = make_models()
    try:
        draft = write_brief(models, inputs)
    except (ModelUnavailableError, ExtractionError) as exc:
        raise HTTPException(503, f"본문 초안을 만들지 못했습니다. {exc}") from exc
    repo.audit(session, "brief.body_draft", "case", case.id, {"notes_lines": len(note_lines(req.notes)),
                                                              "opponent": str(opponent.id) if opponent else None})
    session.commit()
    return BriefBodyOut(
        body=body_text(draft),
        sections=[s.model_dump() for s in draft.sections],
        open_points=draft.open_points,
        checks=check(inputs, draft),
        opponent_document=f"{opponent.document_type} ({opponent.file.name})" if opponent else None,
        model=getattr(models[0], "model", "?"),
        citation_needs=[n.model_dump() for n in draft.citation_needs],
        examples_used=[{"doc_id": str(doc.id), "title": doc.title} for doc, _ in examples],
    )


STYLE_EXAMPLES = 2
EXAMPLE_CHARS = 1500


def _style_examples(session: Session, case: Case, claims: list[str], embedder) -> list[tuple]:  # noqa: ANN001
    """문체 참고용 과거 준비서면(자료실): 최종본·검토 완료 초안·직접 올린 서면만.

    자료실의 준비서면을 모두 모아, 임베딩이 있으면 상대방 주장과 뜻이 가까운 순으로 고르고 없으면 최근 것부터 고른다.
    """
    docs = [
        d
        for d in library_store.list_docs(session, limit=200)
        if d.kind in ("filing", "draft", "other")
        and ("준비서면" in d.title or (d.draft is not None and d.draft.form_id == "brief"))
        and library_store.status_label(d) != "검토 전 초안"
        and d.chunk_count > 0
    ]
    texts = {d.id: "\n".join(c.text for c in d.chunks)[:EXAMPLE_CHARS] for d in docs}
    if embedder is not None and claims and len(docs) > STYLE_EXAMPLES:
        try:
            vectors = embedder.embed([" ".join(claims)[:1000], *[texts[d.id] for d in docs]])
            dot = lambda a, b: sum(x * y for x, y in zip(a, b))  # noqa: E731 - 정규화된 벡터라 내적이 코사인
            docs = [d for _, d in sorted(zip((dot(vectors[0], v) for v in vectors[1:]), docs), key=lambda t: -t[0])]
        except Exception:  # noqa: BLE001 - 임베딩을 못 쓰면 최근 순
            pass
    return [(d, texts[d.id]) for d in docs[:STYLE_EXAMPLES] if texts[d.id].strip()]


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
