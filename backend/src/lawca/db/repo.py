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
from lawca.db.models import AuditLog, Case, Conversation, Deadline, Document, Draft, File, Job, Message, MessageFile, Party
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


def save_file(session: Session, name: str, data: bytes, mime: str | None = None) -> File:
    """mime을 주지 않으면 사용자가 올린 파일로 보고 PDF만 받는다. 서버가 만든 파일(초안 등)은 mime을 준다."""
    if mime is None and not data.startswith(b"%PDF"):
        raise UnsupportedFileError("지금은 PDF 파일만 올릴 수 있습니다.")
    file = File(
        name=name,
        mime=mime or PDF_MIME,
        size=len(data),
        pages=count_pages(data) if (mime or PDF_MIME) == PDF_MIME else None,
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
    session: Session,
    statuses: list[str] | None = None,
    document_id: str | None = None,
    *,
    date_from: date | None = None,
    date_to: date | None = None,
    case_number: str | None = None,
) -> list[Deadline]:
    query = _deadline_query().order_by(Deadline.deadline, Deadline.created_at)
    if date_from is not None:
        query = query.where(Deadline.deadline >= date_from)
    if date_to is not None:
        query = query.where(Deadline.deadline <= date_to)
    if case_number:
        query = query.join(Case, Deadline.case_id == Case.id).where(Case.case_number == re.sub(r"\s+", "", case_number))
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


# 사건 검색


def search_cases(session: Session, text: str, limit: int = 20) -> list[Case]:
    """사건번호·사건명·법원·당사자 이름에 text가 들어간 사건."""
    pattern = f"%{text.strip()}%"
    compact = f"%{re.sub(r'\s+', '', text)}%"
    query = (
        select(Case)
        .outerjoin(Party, Party.case_id == Case.id)
        .where(
            Case.case_number.ilike(compact)
            | Case.case_name.ilike(pattern)
            | Case.court.ilike(pattern)
            | Party.name.ilike(pattern)
        )
        .options(selectinload(Case.parties), selectinload(Case.documents).selectinload(Document.file))
        .distinct()
        .order_by(Case.created_at.desc())
        .limit(limit)
    )
    return list(session.scalars(query))


def get_case(session: Session, case_number: str) -> Case | None:
    query = (
        select(Case)
        .where(Case.case_number == re.sub(r"\s+", "", case_number))
        .options(selectinload(Case.parties), selectinload(Case.documents).selectinload(Document.file))
    )
    return session.scalars(query).first()


# 작업(Job)


def create_job(session: Session, conversation_id: uuid.UUID, graph_version: str) -> Job:
    job = Job(conversation_id=conversation_id, status="running", graph_version=graph_version)
    session.add(job)
    session.flush()
    return job


def get_job(session: Session, job_id: str) -> Job | None:
    parsed = _parse_id(job_id)
    return session.get(Job, parsed) if parsed else None


def finish_job(session: Session, job: Job, status: str, question: dict[str, Any] | None = None) -> None:
    job.status = status
    job.question = question
    session.flush()


# 서식용 사건 값


def case_values(case: Case) -> dict[str, str]:
    """서식의 from_case 항목에 넣을 값."""
    def names(role: str) -> str:
        return ", ".join(p.name for p in case.parties if p.role == role)

    judgments = sorted(
        (d for d in case.documents if d.document_type == "판결" and d.issued_date),
        key=lambda d: d.issued_date,  # type: ignore[arg-type, return-value]
    )
    return {
        "court": case.court or "",
        "case_number": case.case_number,
        "case_name": case.case_name or "",
        "plaintiffs": names("원고"),
        "defendants": names("피고"),
        "judgment_date": judgments[-1].issued_date.isoformat() if judgments else "",  # type: ignore[union-attr]
    }


def remember_facts(session: Session, case: Case, facts: dict[str, str]) -> None:
    changed = {k: v for k, v in facts.items() if v and case.facts.get(k) != v}
    if not changed:
        return
    case.facts = {**case.facts, **changed}
    audit(session, "case.facts", "case", case.id, {"set": changed})
    session.flush()


def recent_cases(session: Session, limit: int = 20) -> list[Case]:
    query = select(Case).options(selectinload(Case.parties)).order_by(Case.created_at.desc()).limit(limit)
    return list(session.scalars(query))


def save_draft(
    session: Session,
    *,
    case: Case | None,
    form_id: str,
    file: File,
    values: dict[str, str],
    blanks: list[str],
    job_id: uuid.UUID | None,
) -> Draft:
    draft = Draft(
        case_id=case.id if case else None,
        form_id=form_id,
        file_id=file.id,
        values=values,
        blanks=blanks,
        job_id=job_id,
    )
    session.add(draft)
    session.flush()
    audit(session, "draft.create", "draft", draft.id, {"form_id": form_id, "file_id": str(file.id), "blanks": blanks})
    return draft
