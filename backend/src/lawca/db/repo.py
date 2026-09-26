"""저장·조회 함수. 커밋은 호출하는 쪽이 한다."""

from __future__ import annotations

import hashlib
import io
import re
import uuid
from datetime import date, datetime, timezone
from typing import Any

from pypdf import PdfReader
from pypdf.errors import PdfReadError
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from lawca.api.schemas import DocumentOut
from lawca.db.models import AuditLog, Case, Conversation, Deadline, Document, File, Message, MessageFile, Party
from lawca.deadlines import DeadlineResult
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


# 기한


def find_document(session: Session, document_id: str | None, file_id: str | None) -> Document | None:
    """document_id로 찾고, 없으면 그 파일로 만든 가장 최근 문서를 찾는다."""
    if document_id and (parsed := _parse_id(document_id)):
        return session.get(Document, parsed)
    if file_id and (parsed := _parse_id(file_id)):
        query = select(Document).where(Document.file_id == parsed).order_by(Document.created_at.desc())
        return session.scalars(query).first()
    return None


def create_deadline(
    session: Session,
    document: Document,
    *,
    label: str,
    kind: str,
    rule_id: str | None,
    event_date: date,
    service_kind: str,
    result: DeadlineResult,
) -> Deadline:
    deadline = Deadline(
        document_id=document.id,
        case_id=document.case_id,
        label=label,
        kind=kind,
        rule_id=rule_id,
        period_amount=result.period.amount,
        period_unit=result.period.unit.value,
        event_date=event_date,
        service_kind=service_kind,
        count_start=result.count_start,
        nominal_end=result.nominal_end,
        deadline=result.deadline,
        extended_over=[{"day": d.isoformat(), "reason": why} for d, why in result.extended_over],
        basis=list(result.basis),
        warnings=list(result.warnings),
        status="confirmed",
        confirmed_by=SYSTEM_ACTOR,
    )
    session.add(deadline)
    session.flush()
    audit(
        session,
        "deadline.confirm",
        "deadline",
        deadline.id,
        {
            "label": label,
            "event_date": event_date.isoformat(),
            "service_kind": service_kind,
            "deadline": result.deadline.isoformat(),
            "document_id": str(document.id),
        },
    )
    return deadline


def _deadline_query():  # noqa: ANN202
    """기한과 함께 응답에 필요한 문서·파일·사건을 한 번에 읽는다."""
    return select(Deadline).options(
        selectinload(Deadline.document).selectinload(Document.file), selectinload(Deadline.case)
    )


def get_deadline(session: Session, deadline_id: uuid.UUID) -> Deadline | None:
    return session.scalars(_deadline_query().where(Deadline.id == deadline_id)).first()


def list_deadlines(
    session: Session, statuses: list[str] | None = None, document_id: str | None = None
) -> list[Deadline]:
    query = _deadline_query().order_by(Deadline.deadline, Deadline.created_at)
    if statuses:
        query = query.where(Deadline.status.in_(statuses))
    if document_id is not None:
        parsed = _parse_id(document_id)
        if parsed is None:
            return []
        query = query.where(Deadline.document_id == parsed)
    return list(session.scalars(query))


def set_deadline_status(session: Session, deadline_id: str, status: str) -> Deadline | None:
    parsed = _parse_id(deadline_id)
    deadline = session.get(Deadline, parsed) if parsed else None
    if deadline is None or deadline.status == status:
        return deadline
    audit(session, f"deadline.{status}", "deadline", deadline.id, {"from": deadline.status, "to": status})
    deadline.status = status
    deadline.status_changed_at = datetime.now(timezone.utc)
    session.flush()
    return deadline
