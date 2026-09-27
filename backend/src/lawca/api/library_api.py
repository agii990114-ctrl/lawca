"""자료실 API: 과거 서면·서식 올리기, 목록, 삭제, 다시 색인, 검색."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from pydantic import BaseModel

from lawca.api.deps import DB, Worker
from lawca.api.embedding import EmbedderFactory
from lawca.config import Settings, get_settings
from lawca.db import repo
from lawca.db.models import LibraryDoc, User
from lawca.library import UnsupportedLibraryFile, detect_mime
from lawca.library import store

router = APIRouter()
Kind = Literal["filing", "form", "court", "draft", "other"]


class LibraryDocOut(BaseModel):
    id: str
    title: str
    kind: Kind
    kind_label: str
    file_id: str
    filename: str
    mime: str
    case_number: str | None
    created_by: str
    created_at: datetime
    chunk_count: int
    embedded: bool
    can_delete: bool


class SearchHitOut(BaseModel):
    doc: LibraryDocOut
    snippet: str
    page: int | None
    matched: list[str]
    """keyword(낱말이 맞음) | semantic(뜻이 비슷함)"""


class SearchOut(BaseModel):
    query: str
    semantic: bool
    """의미 검색까지 했는지(임베딩 모델을 쓸 수 없으면 키워드만)."""
    hits: list[SearchHitOut]


def can_delete(doc: LibraryDoc, user: User) -> bool:
    return user.role == "lawyer" or doc.created_by == user.username


def doc_out(doc: LibraryDoc, user: User) -> LibraryDocOut:
    return LibraryDocOut(
        id=str(doc.id),
        title=doc.title,
        kind=doc.kind,  # type: ignore[arg-type]
        kind_label=store.kind_label(doc.kind),
        file_id=str(doc.file_id),
        filename=doc.file.name,
        mime=doc.file.mime,
        case_number=doc.case.case_number if doc.case else None,
        created_by=doc.created_by,
        created_at=doc.created_at,
        chunk_count=doc.chunk_count,
        embedded=doc.embedded,
        can_delete=can_delete(doc, user),
    )


@router.get("/api/library")
def library(user: Worker, session: DB, kind: Kind | None = None) -> list[LibraryDocOut]:
    return [doc_out(d, user) for d in store.list_docs(session, kind)]


@router.post("/api/library", status_code=201)
def upload(
    file: Annotated[UploadFile, File()],
    user: Worker,
    session: DB,
    make_embedder: EmbedderFactory,
    settings: Annotated[Settings, Depends(get_settings)],
    kind: Annotated[Kind, Form()] = "filing",
    title: Annotated[str | None, Form()] = None,
    case_number: Annotated[str | None, Form()] = None,
) -> LibraryDocOut:
    """과거 서면·서식을 올린다. 글을 뽑아 조각으로 나누고 임베딩해 검색할 수 있게 한다."""
    if kind in ("court", "draft"):
        raise HTTPException(422, "법원 문서와 lawca 초안은 채팅에서 처리하면 자동으로 들어갑니다.")
    limit = settings.max_upload_mb * 1024 * 1024
    data = file.file.read(limit + 1)
    if len(data) > limit:
        raise HTTPException(413, f"{settings.max_upload_mb}MB를 넘는 파일은 올릴 수 없습니다.")
    name = file.filename or "문서"
    try:
        mime = detect_mime(name, data)
    except UnsupportedLibraryFile as exc:
        raise HTTPException(415, str(exc)) from exc
    case = None
    if case_number and case_number.strip():
        case = repo.get_case(session, case_number)
        if case is None:
            raise HTTPException(404, f"사건을 찾을 수 없습니다: {case_number}")
    stored = repo.save_file(session, name, data, mime=mime)
    try:
        doc = store.add(
            session,
            title=(title or "").strip() or name.rsplit(".", 1)[0],
            kind=kind,
            file=stored,
            mime=mime,
            case=case,
            embedder=make_embedder(),
        )
    except UnsupportedLibraryFile as exc:
        raise HTTPException(415, str(exc)) from exc
    if doc.chunk_count == 0:
        session.rollback()
        raise HTTPException(422, "글을 읽지 못했습니다(스캔본 PDF는 아직 지원하지 않습니다). 글자를 복사할 수 있는 PDF나 DOCX로 올려 주세요.")
    session.commit()
    saved = store.get_doc(session, str(doc.id))
    assert saved is not None
    return doc_out(saved, user)


def _doc(session, doc_id: str) -> LibraryDoc:
    doc = store.get_doc(session, doc_id)
    if doc is None:
        raise HTTPException(404, "자료를 찾을 수 없습니다.")
    return doc


@router.delete("/api/library/{doc_id}", status_code=204)
def delete(doc_id: str, user: Worker, session: DB) -> None:
    """자료실에서 뺀다(올린 사람이나 변호사). 직접 올린 파일은 함께 지우고, 법원 문서·초안 기록은 그대로 둔다."""
    doc = _doc(session, doc_id)
    if not can_delete(doc, user):
        raise HTTPException(403, "올린 사람이나 변호사만 지울 수 있습니다.")
    store.remove(session, doc)
    session.commit()


@router.post("/api/library/{doc_id}/reindex")
def reindex(doc_id: str, user: Worker, session: DB, make_embedder: EmbedderFactory) -> LibraryDocOut:
    """임베딩이 빠진 자료를 다시 색인한다(Ollama가 꺼져 있을 때 올린 자료)."""
    doc = _doc(session, doc_id)
    store.reindex(session, doc, make_embedder())
    session.commit()
    return doc_out(_doc(session, doc_id), user)


@router.get("/api/library/search")
def search(
    q: str,
    user: Worker,
    session: DB,
    make_embedder: EmbedderFactory,
    kind: Kind | None = None,
    case_number: str | None = None,
    limit: int = 10,
) -> SearchOut:
    embedder = make_embedder()
    hits = store.search(session, q, embedder, kind=kind, case_number=case_number, limit=min(limit, 30))
    terms = store.terms_of(q)
    return SearchOut(
        query=q,
        semantic=embedder is not None,
        hits=[
            SearchHitOut(doc=doc_out(h.doc, user), snippet=store.snippet(h.chunk.text, terms), page=h.chunk.page, matched=sorted(h.matched))
            for h in hits
        ],
    )

