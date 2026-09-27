"""문서 API: 기한 목록의 '대기' 탭, 문서 상세 팝업, 사건 정보 고치기."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from sqlalchemy.orm import Session

from lawca.agent.documents import suggestions_out
from lawca.api.calendar import event_item
from lawca.api.deps import DB, Worker
from lawca.api.records import record_out
from lawca.api.schemas import (
    DocumentCaseUpdate,
    DocumentDetailOut,
    IssueOut,
    PendingDocumentOut,
    PendingOut,
)
from lawca.db import repo
from lawca.db.models import Document, User
from lawca.extraction.schema import CourtDocument

router = APIRouter()


def _document(session: Session, document_id: str) -> Document:
    document = repo.get_document_detail(session, document_id)
    if document is None:
        raise HTTPException(404, "문서를 찾을 수 없습니다.")
    return document


def detail_out(session: Session, document: Document, user: User) -> DocumentDetailOut:
    doc = CourtDocument.model_validate(document.extraction)
    case = document.case
    parties = (
        [{"role": p.role, "name": p.name} for p in case.parties]
        if case
        else [{"role": p.role, "name": p.name} for p in doc.parties]
    )
    return DocumentDetailOut(
        document_id=str(document.id),
        file_id=str(document.file_id),
        filename=document.file.name,
        document_type=document.document_type,
        model=document.model,
        created_at=document.created_at,
        case_number=case.case_number if case else (doc.case_number.value if doc.case_number else None),
        court=case.court if case else (doc.court.value if doc.court else None),
        case_name=case.case_name if case else (doc.case_name.value if doc.case_name else None),
        parties=parties,
        corrected=bool(document.corrections),
        extraction=doc,
        issues=[IssueOut(**i) for i in document.issues],
        suggestions=suggestions_out(doc),
        deadlines=[record_out(d) for d in repo.document_deadlines(session, document)],
        hearings=[event_item(e, user) for e in repo.document_events(session, document)],
        pending_dismissed=document.pending_dismissed_at is not None,
    )


@router.get("/api/deadlines/pending")
def pending(user: Worker, session: DB) -> PendingOut:
    """송달일을 넣어 기한을 확정할 문서(기한 후보가 있는데 아직 확정하지 않은 것)와 확정할 기일."""
    documents = []
    for d in repo.pending_documents(session):
        suggestions = suggestions_out(CourtDocument.model_validate(d.extraction))
        if not suggestions:
            continue
        documents.append(
            PendingDocumentOut(
                document_id=str(d.id),
                file_id=str(d.file_id),
                filename=d.file.name,
                document_type=d.document_type,
                case_number=d.case.case_number if d.case else None,
                court=d.case.court if d.case else None,
                issued_date=d.issued_date,
                created_at=d.created_at,
                suggestions=suggestions,
            )
        )
    hearings = [event_item(e, user) for e in repo.list_tentative_hearings(session, user.username)]
    return PendingOut(documents=documents, hearings=hearings)


@router.get("/api/documents/{document_id}")
def document_detail(document_id: str, user: Worker, session: DB) -> DocumentDetailOut:
    return detail_out(session, _document(session, document_id), user)


@router.patch("/api/documents/{document_id}/case")
def correct_case(document_id: str, req: DocumentCaseUpdate, user: Worker, session: DB) -> DocumentDetailOut:
    """잘못 읽은 사건번호·법원·사건명을 고친다. 문서와 그 기한·기일이 고친 사건으로 옮겨 간다."""
    document = _document(session, document_id)
    try:
        repo.correct_document_case(
            session,
            document,
            case_number=req.case_number,
            court=(req.court or "").strip() or None,
            case_name=(req.case_name or "").strip() or None,
        )
    except repo.CaseConflict as exc:
        d = exc.deadline
        raise HTTPException(
            409,
            f"{d.case.case_number if d.case else '그 사건'}에 이미 진행 중인 {d.label}(만료 {d.deadline.isoformat()})이 있어 옮길 수 없습니다.",
        ) from exc
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    session.commit()
    session.expire_all()
    return detail_out(session, _document(session, document_id), user)


@router.post("/api/documents/{document_id}/dismiss-pending", status_code=204)
def dismiss_pending(document_id: str, _: Worker, session: DB) -> None:
    """기한을 잡지 않을 문서를 '대기' 목록에서 뺀다."""
    document = repo.get_document(session, document_id)
    if document is None:
        raise HTTPException(404, "문서를 찾을 수 없습니다.")
    repo.dismiss_pending(session, document)
    session.commit()
