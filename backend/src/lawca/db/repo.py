"""저장·조회 함수. 커밋은 호출하는 쪽이 한다."""

from __future__ import annotations

import hashlib
import io
import re
import uuid
from datetime import date
from typing import Any

from pypdf import PdfReader
from pypdf.errors import PdfReadError
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from lawca.api.schemas import DocumentOut
from lawca.db.models import AuditLog, Case, Conversation, Document, File, Message, MessageFile, Party
from lawca.extraction.validate import CASE_NUMBER

PDF_MIME = "application/pdf"
SYSTEM_ACTOR = "system"


class UnsupportedFileError(ValueError):
    pass


def _parse_id(value: str) -> uuid.UUID | None:
    try:
        return uuid.UUID(value)
    except ValueError:
        return None


def audit(session: Session, action: str, target_type: str, target_id: object, detail: dict[str, Any]) -> None:
    session.add(
        AuditLog(actor=SYSTEM_ACTOR, action=action, target_type=target_type, target_id=str(target_id), detail=detail)
    )


# 파일


def count_pages(pdf: bytes) -> int | None:
    try:
        return len(PdfReader(io.BytesIO(pdf)).pages)
    except (PdfReadError, ValueError, OSError):
        return None


def save_file(session: Session, name: str, data: bytes) -> File:
    if not data.startswith(b"%PDF"):
        raise UnsupportedFileError("지금은 PDF 파일만 올릴 수 있습니다.")
    file = File(
        name=name,
        mime=PDF_MIME,
        size=len(data),
        pages=count_pages(data),
        sha256=hashlib.sha256(data).hexdigest(),
        data=data,
    )
    session.add(file)
    session.flush()
    return file


def get_file(session: Session, file_id: str) -> File | None:
    parsed = _parse_id(file_id)
    return session.get(File, parsed) if parsed else None


# 대화


def create_conversation(session: Session) -> Conversation:
    conversation = Conversation()
    session.add(conversation)
    session.flush()
    return conversation


def list_conversations(session: Session, limit: int = 100) -> list[Conversation]:
    query = select(Conversation).order_by(Conversation.updated_at.desc()).limit(limit)
    return list(session.scalars(query))


def get_conversation(session: Session, conversation_id: str) -> Conversation | None:
    parsed = _parse_id(conversation_id)
    if parsed is None:
        return None
    query = (
        select(Conversation)
        .where(Conversation.id == parsed)
        .options(selectinload(Conversation.messages).selectinload(Message.attachments).selectinload(MessageFile.file))
    )
    return session.scalars(query).first()


def add_message(
    session: Session,
    conversation: Conversation,
    role: str,
    text: str,
    *,
    files: list[File] = (),  # type: ignore[assignment]
    steps: list[Any] | None = None,
    cards: list[Any] | None = None,
    state: str = "done",
) -> Message:
    message = Message(
        conversation_id=conversation.id,
        role=role,
        text=text,
        steps=steps or [],
        cards=cards or [],
        state=state,
        attachments=[MessageFile(file_id=f.id, position=i) for i, f in enumerate(files)],
    )
    session.add(message)
    if role == "user" and conversation.title == "새 대화":
        conversation.title = (text.strip() or (files[0].name if files else "새 대화"))[:40]
    session.flush()
    return message


# 사건·문서


def _upsert_case(session: Session, result: DocumentOut) -> Case | None:
    """형식 검증을 통과한 사건번호가 있을 때만 사건을 찾거나 만든다."""
    doc = result.extraction
    if doc.case_number is None or any(i.field == "case_number" and i.level == "error" for i in result.issues):
        return None
    number = re.sub(r"\s+", "", doc.case_number.value)
    if not CASE_NUMBER.match(number):
        return None

    case = session.scalars(select(Case).where(Case.case_number == number)).first()
    if case is None:
        case = Case(
            case_number=number,
            court=doc.court.value if doc.court else None,
            case_name=doc.case_name.value if doc.case_name else None,
        )
        session.add(case)
        session.flush()
        audit(session, "case.create", "case", case.id, {"case_number": number, "source_file": result.file_id})

    known = {(p.role, p.name) for p in case.parties}
    for party in doc.parties:
        if (party.role, party.name) not in known:
            case.parties.append(Party(role=party.role, name=party.name))
            known.add((party.role, party.name))
    return case


def save_document(session: Session, result: DocumentOut) -> Document:
    doc = result.extraction
    issued: date | None = None
    if doc.issued_date is not None:
        try:
            issued = date.fromisoformat(doc.issued_date.value)
        except ValueError:
            issued = None
    case = _upsert_case(session, result)
    document = Document(
        file_id=uuid.UUID(result.file_id),
        case_id=case.id if case else None,
        document_type=doc.document_type.value,
        issued_date=issued,
        model=result.model,
        text_available=result.text_available,
        extraction=doc.model_dump(mode="json"),
        issues=[i.model_dump() for i in result.issues],
    )
    session.add(document)
    session.flush()
    audit(
        session,
        "document.create",
        "document",
        document.id,
        {"file_id": result.file_id, "document_type": document.document_type, "case_id": str(document.case_id)},
    )
    return document
