"""자료실 저장과 검색. 커밋은 호출하는 쪽이 한다.

검색은 두 가지를 섞는다(Reciprocal Rank Fusion).
- 키워드: 검색어 낱말이 조각이나 제목에 들어 있는지와 트라이그램 유사도. 사건번호·서식 이름처럼 정확히 맞아야 하는 것.
  공백을 뺀 글로 비교해 "사 실 조 회 신 청 서"처럼 띄어 쓴 제목도 "사실조회신청서"로 찾힌다.
- 의미: bge-m3 임베딩의 코사인 거리. "주소를 모를 때 쓰는 서면"처럼 표현이 달라도 비슷한 내용. 제목을 붙여 임베딩한다.
한 문서에서 가장 잘 맞는 조각 하나를 대표로 보여 준다.

초안은 사건·서식마다 가장 최근 것만 둔다. 사람이 고쳐 낸 최종본을 올리면 그 초안 대신 최종본이 들어간다.
검토 전 초안은 검색 순위를 조금 낮춘다(검토를 마친 초안과 최종본, 직접 올린 서면을 먼저 보여 준다).
"""

from __future__ import annotations

import logging
import re
import uuid
from dataclasses import dataclass, field

from sqlalchemy import Float, Integer, cast, delete, func, literal, or_, select
from sqlalchemy.orm import Session, selectinload

from lawca.db import repo
from lawca.db.models import Case, Draft, File, LibraryChunk, LibraryDoc
from lawca.library import (
    KINDS,
    Embedder,
    EmbeddingUnavailable,
    chunk_pages,
    extract_pages,
)

log = logging.getLogger(__name__)
RRF_K = 60
CANDIDATES = 40
MIN_TERM = 2
# 의미 검색 기준(bge-m3 코사인 거리). 합성 서면 4건으로 맞춘 값이라 실제 자료가 쌓이면 다시 맞춘다.
# - 관련 있는 자료는 대개 0.45 이하, 관련 없는 자료는 0.55 이상이었다.
UNREVIEWED_DRAFT_WEIGHT = 0.7
SEMANTIC_MAX = 0.52
SEMANTIC_BAND = 0.04
"""가장 가까운 자료보다 이만큼 이상 먼 자료는 뺀다(관련 없는 자료가 줄줄이 붙지 않게)."""


def _embed(embedder: Embedder | None, texts: list[str]) -> list[list[float]] | None:
    if embedder is None or not texts:
        return None
    try:
        return embedder.embed(texts)
    except EmbeddingUnavailable as exc:
        log.warning("임베딩 없이 저장합니다: %s", exc)
        return None


def _fill(doc: LibraryDoc, pages: list[str], embedder: Embedder | None) -> None:
    pieces = chunk_pages(pages)
    # 조각만으로는 무슨 문서인지 모를 수 있어 제목을 붙여 임베딩한다(저장하는 글은 조각 그대로)
    vectors = _embed(embedder, [f"{doc.title}\n{p.text}" for p in pieces])
    doc.chunks = [
        LibraryChunk(seq=i, page=p.page, text=p.text, embedding=vectors[i] if vectors else None)
        for i, p in enumerate(pieces)
    ]
    doc.chunk_count = len(pieces)
    doc.embedded = bool(vectors) and len(pieces) > 0
    doc.embedding_model = embedder.model if vectors and embedder else None


def add(
    session: Session,
    *,
    title: str,
    kind: str,
    file: File,
    mime: str,
    case: Case | None,
    embedder: Embedder | None,
    document_id: uuid.UUID | None = None,
    draft_id: uuid.UUID | None = None,
) -> LibraryDoc:
    """파일의 글을 뽑아 조각으로 저장한다. 글이 없는 파일(스캔본)은 조각 없이 저장한다."""
    if kind not in KINDS:
        raise ValueError(f"알 수 없는 종류입니다: {kind}")
    doc = LibraryDoc(
        title=title.strip()[:300],
        kind=kind,
        file_id=file.id,
        case_id=case.id if case else None,
        document_id=document_id,
        draft_id=draft_id,
        created_by=repo.actor(session),
    )
    session.add(doc)
    _fill(doc, extract_pages(file.data, mime), embedder)
    session.flush()
    repo.audit(session, "library.add", "library_doc", doc.id, {"kind": kind, "title": doc.title, "chunks": doc.chunk_count})
    return doc


def reindex(session: Session, doc: LibraryDoc, embedder: Embedder | None) -> LibraryDoc:
    """임베딩이 빠진 문서를 다시 자르고 임베딩한다(Ollama가 꺼져 있을 때 올린 문서)."""
    _fill(doc, extract_pages(doc.file.data, doc.file.mime), embedder)
    session.flush()
    return doc


def index_court_document(session: Session, document_id: str, embedder: Embedder | None) -> LibraryDoc | None:
    """채팅에서 처리한 법원 문서를 자료실에 넣는다. 이미 있거나 글이 없으면 넘어간다."""
    document = repo.get_document(session, document_id)
    if document is None or session.scalars(select(LibraryDoc).where(LibraryDoc.document_id == document.id)).first():
        return None
    file = session.get(File, document.file_id)
    if file is None or file.mime != "application/pdf" or not any(p.strip() for p in extract_pages(file.data, file.mime)):
        return None  # 스캔본처럼 글이 없으면 검색할 것이 없다
    case = session.get(Case, document.case_id) if document.case_id else None
    number = f" {case.case_number}" if case else ""
    return add(
        session,
        title=f"{document.document_type}{number} ({file.name})",
        kind="court",
        file=file,
        mime=file.mime,
        case=case,
        embedder=embedder,
        document_id=document.id,
    )


def _drop_older_drafts(session: Session, draft: Draft) -> None:
    """같은 사건·같은 서식의 예전 초안 항목을 뺀다(최종본은 남긴다). 사건이 없는 초안은 건드리지 않는다."""
    if draft.case_id is None:
        return
    query = (
        select(LibraryDoc)
        .join(Draft, LibraryDoc.draft_id == Draft.id)
        .where(LibraryDoc.kind == "draft", Draft.case_id == draft.case_id, Draft.form_id == draft.form_id, Draft.id != draft.id)
    )
    for old in session.scalars(query):
        remove(session, old)


def index_draft(session: Session, draft_id: str, embedder: Embedder | None) -> LibraryDoc | None:
    parsed = repo._parse_id(draft_id)
    draft = session.get(Draft, parsed) if parsed else None
    if draft is None:
        return None
    file = session.get(File, draft.file_id)
    assert file is not None
    _drop_older_drafts(session, draft)
    return add(
        session, title=file.name.removesuffix(".docx"), kind="draft", file=file, mime=file.mime,
        case=draft.case, embedder=embedder, draft_id=draft.id,
    )


def index_final(session: Session, draft: Draft, file: File, mime: str, embedder: Embedder | None) -> LibraryDoc:
    """사람이 고쳐 낸 최종본을 자료실에 넣는다. 그 초안 항목과 같은 사건·서식의 예전 초안 항목은 뺀다."""
    for old in session.scalars(select(LibraryDoc).where(LibraryDoc.draft_id == draft.id)):
        remove(session, old)
    session.flush()
    _drop_older_drafts(session, draft)
    base = draft.file.name.removesuffix(".docx").removesuffix("_초안")
    return add(
        session, title=f"{base}_최종본", kind="filing", file=file, mime=mime, case=draft.case,
        embedder=embedder, draft_id=draft.id,
    )


def status_label(doc: LibraryDoc) -> str | None:
    """초안·최종본의 상태. 직접 올린 자료와 법원 문서는 None."""
    if doc.draft_id is None or doc.draft is None:
        return None
    if doc.kind != "draft":
        return "최종본"
    return "검토 완료 초안" if doc.draft.reviewed_by else "검토 전 초안"


def list_docs(session: Session, kind: str | None = None, limit: int = 200) -> list[LibraryDoc]:
    query = (
        select(LibraryDoc)
        .options(selectinload(LibraryDoc.case), selectinload(LibraryDoc.file), selectinload(LibraryDoc.draft))
        .order_by(LibraryDoc.created_at.desc())
        .limit(limit)
    )
    if kind:
        query = query.where(LibraryDoc.kind == kind)
    return list(session.scalars(query))


def get_doc(session: Session, doc_id: str) -> LibraryDoc | None:
    parsed = repo._parse_id(doc_id)
    if parsed is None:
        return None
    query = (
        select(LibraryDoc)
        .where(LibraryDoc.id == parsed)
        .options(selectinload(LibraryDoc.file), selectinload(LibraryDoc.case), selectinload(LibraryDoc.draft))
    )
    return session.scalars(query).first()


UPLOADED_KINDS = ("filing", "form", "other")


def remove(session: Session, doc: LibraryDoc) -> None:
    """자료실에서 뺀다. 자료실에 직접 올린 파일은 함께 지우고, 법원 문서·초안·최종본의 원본은 그대로 둔다."""
    repo.audit(session, "library.delete", "library_doc", doc.id, {"title": doc.title, "kind": doc.kind})
    file_id = doc.file_id if doc.kind in UPLOADED_KINDS and doc.draft_id is None else None
    session.execute(delete(LibraryChunk).where(LibraryChunk.doc_id == doc.id))
    session.delete(doc)
    session.flush()
    if file_id is not None:
        session.execute(delete(File).where(File.id == file_id))
        session.flush()


# 검색


@dataclass
class Hit:
    doc: LibraryDoc
    chunk: LibraryChunk
    score: float
    matched: set[str] = field(default_factory=set)
    """keyword | semantic"""


def terms_of(query: str) -> list[str]:
    """검색어 낱말. 조사가 붙은 말도 찾도록 두 글자 이상 낱말만 쓴다."""
    words = re.findall(r"[\w가-힣]+", query)
    return list(dict.fromkeys(w for w in words if len(w) >= MIN_TERM))[:8]


def _filters(query, kind: str | None, case_number: str | None):  # noqa: ANN001, ANN202
    query = query.join(LibraryDoc, LibraryChunk.doc_id == LibraryDoc.id)
    if kind:
        query = query.where(LibraryDoc.kind == kind)
    if case_number:
        number = re.sub(r"\s+", "", case_number)
        query = query.join(Case, LibraryDoc.case_id == Case.id).where(Case.case_number == number)
    return query


def search(
    session: Session,
    text: str,
    embedder: Embedder | None,
    *,
    kind: str | None = None,
    case_number: str | None = None,
    limit: int = 8,
) -> list[Hit]:
    text = text.strip()
    if not text:
        return []
    ranks: dict[int, float] = {}
    found: dict[int, set[str]] = {}

    terms = terms_of(text) or [text]
    compact_query = re.sub(r"\s+", "", text)
    title = func.regexp_replace(LibraryDoc.title, r"\s+", "", "g")
    # 낱말마다 본문이나 제목에 들어 있으면 1점. 제목에 들어 있으면 1점을 더 준다.
    in_title = [cast(title.ilike(f"%{t}%"), Integer) for t in terms]
    matched = [cast(or_(LibraryChunk.compact.ilike(f"%{t}%"), title.ilike(f"%{t}%")), Integer) for t in terms]
    matched_terms = sum(matched[1:], matched[0])
    similarity = func.word_similarity(compact_query, LibraryChunk.compact)
    keyword_score = matched_terms + sum(in_title[1:], in_title[0]) + similarity
    # 검색어 낱말의 절반 이상이 들어 있어야 한다("피고" 같은 흔한 낱말 하나만 맞는 자료는 빼려고)
    needed = max(1, (len(terms) + 1) // 2)
    keyword = _filters(
        select(LibraryChunk.id, keyword_score.label("score"))
        .where(or_(matched_terms >= needed, similarity > 0.5))
        .order_by(keyword_score.desc())
        .limit(CANDIDATES),
        kind,
        case_number,
    )
    for rank, (chunk_id, _) in enumerate(session.execute(keyword)):
        ranks[chunk_id] = ranks.get(chunk_id, 0) + 1 / (RRF_K + rank + 1)
        found.setdefault(chunk_id, set()).add("keyword")

    vector = _embed(embedder, [text])
    if vector:
        distance = LibraryChunk.embedding.op("<=>", return_type=Float)(literal(vector[0], LibraryChunk.embedding.type))
        semantic = _filters(
            select(LibraryChunk.id, distance.label("distance"))
            .where(LibraryChunk.embedding.isnot(None))
            .order_by(distance)
            .limit(CANDIDATES),
            kind,
            case_number,
        )
        rows = session.execute(semantic).all()
        cutoff = min(SEMANTIC_MAX, rows[0][1] + SEMANTIC_BAND) if rows else 0
        for rank, (chunk_id, dist) in enumerate(rows):
            if dist > cutoff:
                break
            ranks[chunk_id] = ranks.get(chunk_id, 0) + 1 / (RRF_K + rank + 1)
            found.setdefault(chunk_id, set()).add("semantic")

    if not ranks:
        return []
    chunks = {
        c.id: c
        for c in session.scalars(
            select(LibraryChunk)
            .where(LibraryChunk.id.in_(ranks))
            .options(
                selectinload(LibraryChunk.doc).selectinload(LibraryDoc.case),
                selectinload(LibraryChunk.doc).selectinload(LibraryDoc.file),
                selectinload(LibraryChunk.doc).selectinload(LibraryDoc.draft),
            )
        )
    }
    best: dict[uuid.UUID, Hit] = {}
    for chunk_id, score in ranks.items():
        chunk = chunks[chunk_id]
        if status_label(chunk.doc) == "검토 전 초안":
            score *= UNREVIEWED_DRAFT_WEIGHT
        current = best.get(chunk.doc_id)
        if current is None or score > current.score:
            best[chunk.doc_id] = Hit(chunk.doc, chunk, score, found[chunk_id])
    return sorted(best.values(), key=lambda h: -h.score)[:limit]


def snippet(text: str, terms: list[str], width: int = 180) -> str:
    """검색어가 처음 나오는 곳 둘레를 잘라 보여 준다."""
    flat = re.sub(r"\s+", " ", text)
    positions = [flat.find(t) for t in terms if flat.find(t) >= 0]
    start = max(0, min(positions) - width // 3) if positions else 0
    piece = flat[start : start + width]
    return ("…" if start > 0 else "") + piece + ("…" if start + width < len(flat) else "")


def kind_label(kind: str) -> str:
    return KINDS.get(kind, kind)



def hit_item(hit: Hit, terms: list[str]) -> dict[str, object]:
    """화면 카드(검색 결과·참고 서면)에 쓰는 한 줄."""
    return {
        "doc_id": str(hit.doc.id),
        "title": hit.doc.title,
        "kind": hit.doc.kind,
        "kind_label": kind_label(hit.doc.kind),
        "status_label": status_label(hit.doc),
        "snippet": snippet(hit.chunk.text, terms),
        "page": hit.chunk.page,
        "file_id": str(hit.doc.file_id),
        "filename": hit.doc.file.name,
        "case_number": hit.doc.case.case_number if hit.doc.case else None,
        "created_at": hit.doc.created_at.date().isoformat(),
        "matched": sorted(hit.matched),
    }


REFERENCE_KINDS = ("filing", "form", "draft", "other")


def references(
    session: Session,
    form_name: str,
    embedder: Embedder | None,
    *,
    exclude_draft_id: uuid.UUID | str | None = None,
    limit: int = 3,
    reviewed_only: bool = False,
) -> list[dict[str, object]]:
    """서식을 쓸 때 참고할 과거 서면. 서식 이름으로 찾고 법원 문서와 지금 만드는 초안은 뺀다.

    최종본·검토 완료 초안·직접 올린 서면이 앞에 온다(검토 전 초안은 점수를 낮춘다).
    """
    exclude = str(exclude_draft_id) if exclude_draft_id else None
    hits = [
        h
        for h in search(session, form_name, embedder, limit=12)
        if h.doc.kind in REFERENCE_KINDS
        and (exclude is None or str(h.doc.draft_id) != exclude)
        and not (reviewed_only and status_label(h.doc) == "검토 전 초안")
    ]
    terms = terms_of(form_name)
    return [hit_item(h, terms) for h in hits[:limit]]


MIN_NOTE_CHARS = 10
"""이보다 짧은 메모(예: "결론: 청구 인용")는 참고 문단을 찾지 않는다. 낱말 몇 개만 겹친 엉뚱한 서면이 붙는 것을 막는다."""


def paragraph_references(
    session: Session, notes: list[str], embedder: Embedder | None, *, total: int = 5
) -> list[tuple[list[int], Hit]]:
    """메모(줄)마다 자료실에서 비슷한 과거 문단을 하나씩 찾는다. 같은 문단이 여러 메모에 맞으면 메모 번호를 함께 붙인다.

    - 법원 문서(이 사건의 받은 문서 등)와 검토 전 lawca 초안은 참고로 쓰지 않는다.
    - 돌려주는 것은 ([메모 번호…], 검색 결과) 쌍이고, 서로 다른 문단 최대 total개다.
    """
    picked: dict[int, tuple[list[int], Hit]] = {}
    for number, note in enumerate(notes, start=1):
        if len(_compact_text(note)) < MIN_NOTE_CHARS:
            continue
        for hit in search(session, note, embedder, limit=10):
            if hit.doc.kind == "court" or status_label(hit.doc) == "검토 전 초안":
                continue
            if hit.chunk.id in picked:
                picked[hit.chunk.id][0].append(number)
            elif len(picked) < total:
                picked[hit.chunk.id] = ([number], hit)
            else:
                continue
            break
    return list(picked.values())


def _compact_text(text: str) -> str:
    return re.sub(r"\s+", "", text)


def docs_for_draft(session: Session, draft_id: uuid.UUID) -> list[LibraryDoc]:
    return list(session.scalars(select(LibraryDoc).where(LibraryDoc.draft_id == draft_id)))
