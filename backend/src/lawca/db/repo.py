"""저장·조회 함수. 커밋은 호출하는 쪽이 한다."""

from __future__ import annotations

import hashlib
import io
import re
import uuid
from datetime import date, datetime, time, timedelta, timezone
from typing import Any

from pypdf import PdfReader
from pypdf.errors import PdfReadError
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session, selectinload

from lawca.api.schemas import DocumentOut
from lawca.auth import SESSION_TTL, hash_password, new_token, token_hash, verify_password
from lawca.db.models import (
    AuditLog,
    Case,
    Conversation,
    Deadline,
    Document,
    Draft,
    Event,
    EvidenceItem,
    File,
    Job,
    LibraryDoc,
    Message,
    MessageFile,
    Party,
    User,
    UserSession,
)
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


def actor(session: Session) -> str:
    """이 세션으로 일하는 사람. API가 로그인한 사용자의 아이디를 session.info에 넣는다."""
    return session.info.get("actor", SYSTEM_ACTOR)


def audit(session: Session, action: str, target_type: str, target_id: object, detail: dict[str, Any]) -> None:
    session.add(
        AuditLog(actor=actor(session), action=action, target_type=target_type, target_id=str(target_id), detail=detail)
    )


# 사용자·로그인


def _now() -> datetime:
    return datetime.now(timezone.utc)


def find_user(session: Session, username: str) -> User | None:
    """삭제하지 않은 사용자. 아이디는 대소문자를 가리지 않는다."""
    query = select(User).where(func.lower(User.username) == username.strip().lower(), User.deleted_at.is_(None))
    return session.scalars(query).first()


def get_user(session: Session, user_id: str) -> User | None:
    parsed = _parse_id(user_id)
    user = session.get(User, parsed) if parsed else None
    return user if user is not None and user.deleted_at is None else None


def list_users(session: Session) -> list[User]:
    return list(session.scalars(select(User).where(User.deleted_at.is_(None)).order_by(User.created_at)))


def create_user(session: Session, username: str, name: str, role: str, password: str) -> User:
    user = User(username=username.strip(), name=name.strip(), role=role, password_hash=hash_password(password))
    session.add(user)
    session.flush()
    audit(session, "user.create", "user", user.id, {"username": user.username, "role": role})
    return user


def delete_user(session: Session, user: User) -> None:
    """사용자를 지운다. 기록을 남기려고 행은 두고 deleted_at만 채운 뒤 로그인 세션을 모두 끊는다."""
    user.deleted_at = _now()
    session.execute(delete(UserSession).where(UserSession.user_id == user.id))
    audit(session, "user.delete", "user", user.id, {"username": user.username})
    session.flush()


def set_password(session: Session, user: User, password: str, *, by_admin: bool) -> None:
    """비밀번호를 바꾸고 그 사용자의 다른 로그인 세션을 끊는다."""
    user.password_hash = hash_password(password)
    session.execute(delete(UserSession).where(UserSession.user_id == user.id))
    audit(session, "user.password_reset" if by_admin else "user.password_change", "user", user.id, {})
    session.flush()


def authenticate(session: Session, username: str, password: str) -> User | None:
    user = find_user(session, username)
    if user is None:
        verify_password(password, _DUMMY_HASH)  # 없는 아이디도 같은 시간이 걸리게 한다
        return None
    return user if verify_password(password, user.password_hash) else None


_DUMMY_HASH = hash_password("lawca-dummy-password-1")


def start_session(session: Session, user: User) -> str:
    """로그인 세션을 만들고 쿠키에 넣을 토큰을 돌려준다."""
    token = new_token()
    session.add(UserSession(token_hash=token_hash(token), user_id=user.id, expires_at=_now() + SESSION_TTL))
    session.execute(delete(UserSession).where(UserSession.expires_at < _now()))  # 만료된 세션 정리
    user.last_login_at = _now()
    session.info["actor"] = user.username
    audit(session, "user.login", "user", user.id, {})
    session.flush()
    return token


def user_for_token(session: Session, token: str) -> User | None:
    row = session.get(UserSession, token_hash(token))
    if row is None or row.expires_at < _now() or row.user.deleted_at is not None:
        return None
    return row.user


def end_session(session: Session, token: str) -> None:
    session.execute(delete(UserSession).where(UserSession.token_hash == token_hash(token)))


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


def create_conversation(session: Session, user_id: uuid.UUID | None = None) -> Conversation:
    conversation = Conversation(user_id=user_id)
    session.add(conversation)
    session.flush()
    return conversation


def list_conversations(session: Session, user_id: uuid.UUID, limit: int = 100) -> list[Conversation]:
    query = (
        select(Conversation).where(Conversation.user_id == user_id).order_by(Conversation.updated_at.desc()).limit(limit)
    )
    return list(session.scalars(query))


def get_conversation(session: Session, conversation_id: str, user_id: uuid.UUID) -> Conversation | None:
    """user_id의 대화. 남의 대화는 없는 것으로 본다."""
    parsed = _parse_id(conversation_id)
    if parsed is None:
        return None
    query = (
        select(Conversation)
        .where(Conversation.id == parsed, Conversation.user_id == user_id)
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
    if doc.hearing is not None:
        add_hearing(session, document, doc)
    return document


def add_hearing(session: Session, document: Document, doc: Any) -> Event | None:
    """문서에서 읽은 기일을 '미확정' 일정으로 올린다. 날짜를 읽지 못했으면 올리지 않는다(검증 경고로 남는다)."""
    hearing = doc.hearing
    try:
        day = date.fromisoformat(hearing.date)
    except ValueError:
        return None
    at = None
    if hearing.time:
        try:
            at = time.fromisoformat(hearing.time)
        except ValueError:
            at = None
    kind = hearing.kind or "기일"
    event = Event(
        kind="hearing",
        title=kind,
        day=day,
        at=at,
        location=hearing.place,
        status="tentative",
        visibility="firm",
        case_id=document.case_id,
        document_id=document.id,
        created_by=actor(session),
    )
    session.add(event)
    session.flush()
    audit(session, "event.create", "event", event.id, {"kind": "hearing", "day": day.isoformat(), "document_id": str(document.id)})
    return event


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
        confirmed_by=actor(session),
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


def deadline_key(rule_id: str | None, document_type: str) -> str:
    """같은 사건에 하나만 진행할 수 있는 기한의 종류. 법정 기간은 규칙, 문서가 정한 기간은 문서 종류로 가른다."""
    return rule_id or f"designated:{document_type}"


def key_of(d: Deadline) -> str:
    return deadline_key(d.rule_id, d.document.document_type)


def open_conflict(
    session: Session, *, key: str, case_id: uuid.UUID | None, document_id: uuid.UUID, exclude_id: uuid.UUID | None = None
) -> Deadline | None:
    """같은 사건(사건이 없으면 같은 문서)에 같은 종류로 진행 중인 기한."""
    scope = Deadline.case_id == case_id if case_id is not None else Deadline.document_id == document_id
    query = _deadline_query().where(Deadline.status == "confirmed", scope)
    if exclude_id is not None:
        query = query.where(Deadline.id != exclude_id)
    return next((d for d in session.scalars(query) if key_of(d) == key), None)


def list_deadlines(
    session: Session,
    statuses: list[str] | None = None,
    document_id: str | None = None,
    *,
    date_from: date | None = None,
    date_to: date | None = None,
    case_number: str | None = None,
) -> list[Deadline]:
    query = _deadline_query().where(Deadline.status != "deleted").order_by(Deadline.deadline, Deadline.created_at)
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
        created_by=actor(session),
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


def get_draft(session: Session, draft_id: str) -> Draft | None:
    parsed = _parse_id(draft_id)
    return session.get(Draft, parsed) if parsed else None


def review_draft(session: Session, draft: Draft) -> Draft:
    """변호사가 초안 검토를 마쳤다고 표시한다. 이미 표시했으면 그대로 둔다."""
    if draft.reviewed_by is None:
        draft.reviewed_by = actor(session)
        draft.reviewed_at = _now()
        audit(session, "draft.review", "draft", draft.id, {"form_id": draft.form_id})
        session.flush()
    return draft


# 캘린더


def _event_query():
    return select(Event).options(
        selectinload(Event.case), selectinload(Event.document).selectinload(Document.file)
    )


def list_events(
    session: Session, start: date, end: date, username: str, *, statuses: list[str] | None = None
) -> list[Event]:
    """start~end의 일정 중 이 사용자가 볼 수 있는 것(법인 공개 + 본인 비공개)."""
    query = (
        _event_query()
        .where(Event.day >= start, Event.day <= end)
        .where((Event.visibility == "firm") | (Event.created_by == username))
        .order_by(Event.day, Event.at.nulls_first(), Event.created_at)
    )
    if statuses:
        query = query.where(Event.status.in_(statuses))
    return list(session.scalars(query))


def get_event(session: Session, event_id: str, username: str) -> Event | None:
    parsed = _parse_id(event_id)
    event = session.scalars(_event_query().where(Event.id == parsed)).first() if parsed else None
    if event is None or (event.visibility == "private" and event.created_by != username):
        return None
    return event


def create_event(
    session: Session,
    *,
    title: str,
    day: date,
    at: time | None,
    location: str | None,
    memo: str,
    visibility: str,
    case: Case | None,
) -> Event:
    event = Event(
        kind="manual",
        title=title.strip(),
        day=day,
        at=at,
        location=(location or "").strip() or None,
        memo=memo.strip(),
        status="confirmed",
        visibility=visibility,
        case_id=case.id if case else None,
        created_by=actor(session),
        confirmed_by=actor(session),
    )
    session.add(event)
    session.flush()
    audit(session, "event.create", "event", event.id, {"kind": "manual", "day": day.isoformat()})
    return event


def update_event(session: Session, event: Event, changes: dict[str, Any]) -> Event:
    """바뀐 항목만 고치고 감사 기록에 남긴다. status가 confirmed가 되면 확정한 사람을 적는다."""
    before = {}
    for key, value in changes.items():
        old = getattr(event, key)
        if old != value:
            before[key] = str(old) if old is not None else None
            setattr(event, key, value)
    if changes.get("status") == "confirmed" and "status" in before:
        event.confirmed_by = actor(session)
    if before:
        audit(session, "event.update", "event", event.id, {"changed": before})
    session.flush()
    return event


def pending_documents(session: Session, limit: int = 50) -> list[Document]:
    """기한을 하나도 확정하지 않았고, 목록에서 빼지도 않은 문서(최근 것부터)."""
    has_deadline = select(Deadline.id).where(Deadline.document_id == Document.id).exists()
    query = (
        select(Document)
        .where(~has_deadline, Document.pending_dismissed_at.is_(None))
        .options(selectinload(Document.case), selectinload(Document.file))
        .order_by(Document.created_at.desc())
        .limit(limit)
    )
    return list(session.scalars(query))


def dismiss_pending(session: Session, document: Document) -> None:
    document.pending_dismissed_at = _now()
    audit(session, "document.dismiss_pending", "document", document.id, {})
    session.flush()


def get_document(session: Session, document_id: str) -> Document | None:
    parsed = _parse_id(document_id)
    return session.get(Document, parsed) if parsed else None


def issue_calendar_token(session: Session, user: User) -> str:
    """캘린더 구독 주소용 토큰을 새로 만든다. 예전 주소는 더 이상 쓸 수 없다."""
    token = new_token()
    user.calendar_token_hash = token_hash(token)
    audit(session, "user.calendar_token", "user", user.id, {})
    session.flush()
    return token


def user_for_calendar_token(session: Session, token: str) -> User | None:
    query = select(User).where(User.calendar_token_hash == token_hash(token), User.deleted_at.is_(None))
    return session.scalars(query).first()


def update_deadline_terms(
    session: Session,
    deadline: Deadline,
    *,
    label: str,
    event_date: date,
    service_kind: str,
    period_amount: int,
    period_unit: str,
    result: DeadlineResult,
) -> Deadline:
    """진행 중인 기한의 송달일·기간을 고치고 서버가 다시 계산한 값으로 바꾼다. 고치기 전 값은 감사 기록에 남긴다."""
    before = {
        "label": deadline.label,
        "event_date": deadline.event_date.isoformat(),
        "service_kind": deadline.service_kind,
        "period": f"{deadline.period_amount}{deadline.period_unit}",
        "deadline": deadline.deadline.isoformat(),
    }
    deadline.label = label
    deadline.event_date = event_date
    deadline.service_kind = service_kind
    deadline.period_amount = period_amount
    deadline.period_unit = period_unit
    deadline.count_start = result.count_start
    deadline.nominal_end = result.nominal_end
    deadline.deadline = result.deadline
    deadline.extended_over = [{"day": d.isoformat(), "reason": why} for d, why in result.extended_over]
    deadline.basis = list(result.basis)
    deadline.warnings = list(result.warnings)
    audit(session, "deadline.update", "deadline", deadline.id, {"before": before, "deadline": result.deadline.isoformat()})
    session.flush()
    return deadline


def delete_deadline(session: Session, deadline: Deadline) -> None:
    """취소한 기한을 지운다. 기록을 남기려고 행은 두고 deleted로 바꿔 어디에도 보이지 않게 한다."""
    audit(session, "deadline.deleted", "deadline", deadline.id, {"from": deadline.status})
    deadline.status = "deleted"
    deadline.status_changed_at = _now()
    session.flush()


def list_tentative_hearings(session: Session, username: str) -> list[Event]:
    query = (
        _event_query()
        .where(Event.kind == "hearing", Event.status == "tentative")
        .where((Event.visibility == "firm") | (Event.created_by == username))
        .order_by(Event.day, Event.at.nulls_first())
    )
    return list(session.scalars(query))


def document_detail_query():  # noqa: ANN201
    return select(Document).options(
        selectinload(Document.case).selectinload(Case.parties), selectinload(Document.file)
    )


def get_document_detail(session: Session, document_id: str) -> Document | None:
    parsed = _parse_id(document_id)
    return session.scalars(document_detail_query().where(Document.id == parsed)).first() if parsed else None


def document_deadlines(session: Session, document: Document) -> list[Deadline]:
    query = _deadline_query().where(Deadline.document_id == document.id, Deadline.status != "deleted")
    return list(session.scalars(query.order_by(Deadline.deadline)))


def document_events(session: Session, document: Document) -> list[Event]:
    query = _event_query().where(Event.document_id == document.id, Event.status != "cancelled")
    return list(session.scalars(query))


class CaseConflict(ValueError):
    def __init__(self, deadline: Deadline) -> None:
        super().__init__(deadline.label)
        self.deadline = deadline


def correct_document_case(
    session: Session, document: Document, *, case_number: str, court: str | None, case_name: str | None
) -> Case:
    """문서의 사건 정보를 사람이 고친다. 사건을 찾거나 만들어 문서·기한·기일을 그 사건으로 옮긴다.

    옮길 사건에 같은 종류로 진행 중인 기한이 있으면 CaseConflict를 낸다(같은 사건·같은 종류는 하나만).
    """
    number = re.sub(r"\s+", "", case_number)
    if not CASE_NUMBER.match(number):
        raise ValueError(f"사건번호 형식이 아닙니다: {case_number}")
    case = session.scalars(select(Case).where(Case.case_number == number)).first()
    if case is None:
        case = Case(case_number=number, court=court or None, case_name=case_name or None)
        session.add(case)
        session.flush()
        audit(session, "case.create", "case", case.id, {"case_number": number, "source": "correction"})
    for d in document_deadlines(session, document):
        if d.status == "confirmed":
            conflict = open_conflict(session, key=key_of(d), case_id=case.id, document_id=document.id, exclude_id=d.id)
            if conflict is not None and conflict.document_id != document.id:
                raise CaseConflict(conflict)
    before = {"case_id": str(document.case_id), **document.corrections}
    if court:
        case.court = court
    if case_name:
        case.case_name = case_name
    document.case_id = case.id
    document.corrections = {"case_number": number, "court": court or case.court, "case_name": case_name or case.case_name}
    for d in document_deadlines(session, document):
        d.case_id = case.id
    for e in document_events(session, document):
        e.case_id = case.id
    for item in session.scalars(select(LibraryDoc).where(LibraryDoc.document_id == document.id)):
        item.case_id = case.id  # 자료실의 사건별 검색에서도 고친 사건으로 찾히게
    audit(session, "document.correct", "document", document.id, {"before": before, "after": document.corrections})
    session.flush()
    return case


def set_final(session: Session, draft: Draft, file: File) -> None:
    before = str(draft.final_file_id) if draft.final_file_id else None
    draft.final_file_id = file.id
    draft.final_uploaded_by = actor(session)
    draft.final_uploaded_at = _now()
    audit(session, "draft.final", "draft", draft.id, {"file_id": str(file.id), "replaced": before})
    session.flush()


# 상대방 서면 요약과 증거 목록


def set_summary(session: Session, document: Document, summary: dict[str, Any]) -> None:
    document.summary = summary
    audit(session, "document.summary", "document", document.id, {"claims": len(summary.get("claims", []))})
    session.flush()


def list_evidence(session: Session, case: Case) -> list[EvidenceItem]:
    from lawca.agent.brief import number_key

    rows = list(session.scalars(select(EvidenceItem).where(EvidenceItem.case_id == case.id)))
    return sorted(rows, key=lambda e: (e.side, number_key(e.number)))


def next_evidence_number(session: Session, case: Case, side: str) -> str:
    from lawca.agent.brief import number_key

    numbers = [e.number for e in list_evidence(session, case) if e.side == side]
    top = max((number_key(n)[0] for n in numbers), default=0)
    return str(top + 1)


def get_evidence(session: Session, evidence_id: str) -> EvidenceItem | None:
    parsed = _parse_id(evidence_id)
    return session.get(EvidenceItem, parsed) if parsed else None


def find_evidence(session: Session, case: Case, side: str, number: str) -> EvidenceItem | None:
    query = select(EvidenceItem).where(EvidenceItem.case_id == case.id, EvidenceItem.side == side, EvidenceItem.number == number)
    return session.scalars(query).first()


def add_evidence(
    session: Session,
    case: Case,
    *,
    side: str,
    number: str,
    title: str,
    note: str = "",
    source_document_id: uuid.UUID | None = None,
) -> EvidenceItem:
    item = EvidenceItem(
        case_id=case.id, side=side, number=number, title=title.strip(), note=note.strip(),
        source_document_id=source_document_id, created_by=actor(session),
    )
    session.add(item)
    session.flush()
    audit(session, "evidence.add", "evidence", item.id, {"case": case.case_number, "label": f"{side}{number}", "title": item.title})
    return item


def update_evidence(session: Session, item: EvidenceItem, changes: dict[str, Any]) -> EvidenceItem:
    before = {k: str(getattr(item, k)) for k, v in changes.items() if getattr(item, k) != v}
    for key, value in changes.items():
        setattr(item, key, value)
    if before:
        audit(session, "evidence.update", "evidence", item.id, {"before": before})
    session.flush()
    return item


def delete_evidence(session: Session, item: EvidenceItem) -> None:
    audit(session, "evidence.delete", "evidence", item.id, {"label": f"{item.side}{item.number}", "title": item.title})
    session.delete(item)
    session.flush()
