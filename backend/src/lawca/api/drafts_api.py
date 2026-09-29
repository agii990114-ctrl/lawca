"""초안 API: 채팅에서 만든 서식·준비서면 초안을 목록으로 보고, 준비서면 초안에 판례 인용을 넣는다."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from lawca.agent import brief_flow
from lawca.api.deps import DB, Lawyer, Worker
from lawca.api.embedding import EmbedderFactory
from lawca.db import repo
from lawca.db.models import Case, Draft
from lawca.forms import LATER, load_forms
from lawca.library import store as library_store

router = APIRouter()


class DraftListItem(BaseModel):
    id: str
    form_id: str
    form_name: str
    case_number: str | None
    case_name: str | None
    filename: str
    file_id: str
    size: int
    created_by: str | None
    created_at: datetime
    reviewed_by: str | None
    reviewed_at: datetime | None
    final_file_id: str | None
    final_filename: str | None
    status_label: str
    """최종본 | 검토 완료 | 검토 전"""
    blanks: list[str]


class DraftDetail(DraftListItem):
    fields: list[dict[str, str]]
    brief: dict[str, Any] | None
    """준비서면이면 본문 섹션·근거·점검·참고한 문서·판례 자리."""


class CitationInsert(BaseModel):
    index: int = Field(ge=0)
    citation: str = Field(min_length=2, max_length=300)


def _status(d: Draft) -> str:
    if d.final_file_id:
        return "최종본"
    return "검토 완료" if d.reviewed_by else "검토 전"


def item_out(d: Draft) -> DraftListItem:
    form = load_forms().get(d.form_id)
    return DraftListItem(
        id=str(d.id),
        form_id=d.form_id,
        form_name=form.name if form else d.form_id,
        case_number=d.case.case_number if d.case else None,
        case_name=d.case.case_name if d.case else None,
        filename=d.file.name,
        file_id=str(d.file_id),
        size=d.file.size,
        created_by=d.created_by,
        created_at=d.created_at,
        reviewed_by=d.reviewed_by,
        reviewed_at=d.reviewed_at,
        final_file_id=str(d.final_file_id) if d.final_file_id else None,
        final_filename=d.final_file.name if d.final_file else None,
        status_label=_status(d),
        blanks=d.blanks,
    )


def detail_out(d: Draft) -> DraftDetail:
    form = load_forms().get(d.form_id)
    values = d.values
    fields = []
    if form is not None and d.form_id != "brief":
        fields = [
            {"label": f.label, "value": "(빈칸)" if values.get(f.key) == LATER else str(values.get(f.key, ""))}
            for f in form.fields
            if values.get(f.key)
        ]
    elif form is not None:
        fields = [
            {"label": f.label, "value": str(values.get(f.key, ""))}
            for f in form.fields
            if f.key in ("our_side", "title", "agent") and values.get(f.key)
        ]
    return DraftDetail(**item_out(d).model_dump(), fields=fields, brief=values.get("_brief"))


def _load(session: Session, draft_id: str) -> Draft:
    draft = repo.get_draft(session, draft_id)
    if draft is None:
        raise HTTPException(404, "초안을 찾을 수 없습니다.")
    return draft


@router.get("/api/drafts")
def list_drafts(_: Worker, session: DB, case_number: str | None = None, form_id: str | None = None) -> list[DraftListItem]:
    """작성한 초안(서식·준비서면)을 최근 것부터. 초안은 법인 전체가 함께 본다."""
    query = (
        select(Draft)
        .options(selectinload(Draft.case), selectinload(Draft.file), selectinload(Draft.final_file))
        .order_by(Draft.created_at.desc())
        .limit(200)
    )
    if case_number:
        query = query.join(Case, Draft.case_id == Case.id).where(Case.case_number == case_number.replace(" ", ""))
    if form_id:
        query = query.where(Draft.form_id == form_id)
    return [item_out(d) for d in session.scalars(query)]


@router.get("/api/drafts/{draft_id}/detail")
def draft_detail(draft_id: str, _: Worker, session: DB) -> DraftDetail:
    return detail_out(_load(session, draft_id))


@router.post("/api/drafts/{draft_id}/citation")
def insert_citation(draft_id: str, req: CitationInsert, _: Lawyer, session: DB, make_embedder: EmbedderFactory) -> DraftDetail:
    """준비서면 초안의 [인용 확인 필요] 자리에 변호사가 고른 판례 인용을 넣고 DOCX를 다시 만든다."""
    draft = _load(session, draft_id)
    if draft.form_id != "brief" or "_brief" not in draft.values:
        raise HTTPException(422, "준비서면 초안이 아닙니다.")
    try:
        brief_flow.apply_citation(session, draft, req.index, req.citation)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    session.commit()
    # 자료실의 이 초안 항목을 고친 본문으로 다시 색인한다(최종본이 있으면 최종본을 건드리지 않는다)
    if draft.final_file_id is None:
        try:
            for old in library_store.docs_for_draft(session, draft.id):
                library_store.remove(session, old)
            library_store.index_draft(session, str(draft.id), make_embedder())
            session.commit()
        except Exception:  # noqa: BLE001 - 색인 실패는 인용 넣기를 막지 않는다
            session.rollback()
    session.refresh(draft)
    return detail_out(draft)
