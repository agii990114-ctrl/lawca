"""DB 테이블 정의.

지금 저장하는 것: 사용자·로그인 세션, 대화·메시지, 일정(기일·직접 입력), 자료실(문서·조각·임베딩), 첨부 파일, 사건·당사자, 문서(추출 결과), 확정한 기한, 서식 초안,
작업(Job, 되묻기로 멈춘 LangGraph 실행), 감사 기록.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime, time
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    Computed,
    Date,
    DateTime,
    ForeignKey,
    Identity,
    Index,
    Integer,
    LargeBinary,
    String,
    Text,
    Time,
    UniqueConstraint,
    cast,
    func,
)
from sqlalchemy.types import UserDefinedType
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Vector(UserDefinedType):
    """pgvector의 vector(n). 파이썬 쪽은 float 목록이다. 값은 '[1,2,3]' 글로 보내고 vector로 바꾼다."""

    cache_ok = True

    def __init__(self, dim: int) -> None:
        self.dim = dim

    def get_col_spec(self, **kw: Any) -> str:
        return f"vector({self.dim})"

    def bind_expression(self, bindvalue: Any) -> Any:
        return cast(bindvalue, self)

    def bind_processor(self, dialect: Any):  # noqa: ANN201
        def process(value: list[float] | None) -> str | None:
            return None if value is None else "[" + ",".join(f"{x:.7g}" for x in value) + "]"

        return process

    def result_processor(self, dialect: Any, coltype: Any):  # noqa: ANN201
        def process(value: Any) -> list[float] | None:
            if value is None or isinstance(value, list):
                return value
            return [float(x) for x in str(value).strip("[]").split(",")]

        return process


class Base(DeclarativeBase):
    type_annotation_map = {dict[str, Any]: JSONB, list[Any]: JSONB, uuid.UUID: UUID(as_uuid=True)}


def _uuid() -> uuid.UUID:
    return uuid.uuid4()


class Timestamped:
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class User(Timestamped, Base):
    """사용자. 관리자가 만든다. 삭제하면 deleted_at만 채워 감사 기록과 대화의 주인을 남긴다."""

    __tablename__ = "users"
    __table_args__ = (
        # 삭제한 사용자의 아이디는 다시 쓸 수 있다
        Index("uq_users_username_active", "username", unique=True, postgresql_where="deleted_at IS NULL"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=_uuid)
    username: Mapped[str] = mapped_column(String(50))
    name: Mapped[str] = mapped_column(String(50))
    role: Mapped[str] = mapped_column(String(16))
    """clerk(사무원) | lawyer(변호사) | admin(관리자)"""
    password_hash: Mapped[str] = mapped_column(String(255))
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    calendar_token_hash: Mapped[str | None] = mapped_column(String(64), unique=True)
    """캘린더 구독 주소 토큰의 해시. 주소는 발급할 때 한 번만 보여 준다."""


class UserSession(Timestamped, Base):
    """로그인 세션. 쿠키에는 토큰을, DB에는 토큰의 해시만 둔다."""

    __tablename__ = "user_sessions"

    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    user: Mapped[User] = relationship()


class Conversation(Timestamped, Base):
    __tablename__ = "conversations"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=_uuid)
    user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"), index=True)
    """대화의 주인. 대화는 본인만 본다(사건·기한은 법인 전체가 함께 본다)."""
    title: Mapped[str] = mapped_column(String(200), default="새 대화")
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    messages: Mapped[list[Message]] = relationship(
        back_populates="conversation", order_by="Message.seq", cascade="all, delete-orphan"
    )


class Message(Timestamped, Base):
    __tablename__ = "messages"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=_uuid)
    seq: Mapped[int] = mapped_column(BigInteger, Identity(), unique=True)
    conversation_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("conversations.id", ondelete="CASCADE"), index=True)
    role: Mapped[str] = mapped_column(String(16))
    text: Mapped[str] = mapped_column(Text, default="")
    steps: Mapped[list[Any]] = mapped_column(default=list)
    cards: Mapped[list[Any]] = mapped_column(default=list)
    state: Mapped[str] = mapped_column(String(16), default="done")

    conversation: Mapped[Conversation] = relationship(back_populates="messages")
    attachments: Mapped[list[MessageFile]] = relationship(order_by="MessageFile.position", cascade="all, delete-orphan")


class File(Timestamped, Base):
    """첨부 파일. 원본은 DB(bytea)에 둔다. 파일이 많아지면 객체 저장소로 옮긴다."""

    __tablename__ = "files"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=_uuid)
    name: Mapped[str] = mapped_column(String(255))
    mime: Mapped[str] = mapped_column(String(100))
    size: Mapped[int] = mapped_column(Integer)
    pages: Mapped[int | None] = mapped_column(Integer)
    sha256: Mapped[str] = mapped_column(String(64), index=True)
    data: Mapped[bytes] = mapped_column(LargeBinary, deferred=True)


class MessageFile(Base):
    __tablename__ = "message_files"

    message_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("messages.id", ondelete="CASCADE"), primary_key=True)
    file_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("files.id"), primary_key=True)
    position: Mapped[int] = mapped_column(Integer, default=0)

    file: Mapped[File] = relationship()


class Case(Timestamped, Base):
    __tablename__ = "cases"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=_uuid)
    case_number: Mapped[str] = mapped_column(String(40), unique=True)
    court: Mapped[str | None] = mapped_column(String(100))
    case_name: Mapped[str | None] = mapped_column(String(200))
    facts: Mapped[dict[str, Any]] = mapped_column(default=dict, server_default="{}")
    """서식을 만들며 입력받은 값(판결 확정일 등). 다음 서식에서 다시 묻지 않는다."""

    parties: Mapped[list[Party]] = relationship(back_populates="case", cascade="all, delete-orphan")
    documents: Mapped[list[Document]] = relationship(back_populates="case")


class Party(Timestamped, Base):
    __tablename__ = "parties"
    __table_args__ = (UniqueConstraint("case_id", "role", "name"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=_uuid)
    case_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("cases.id", ondelete="CASCADE"), index=True)
    role: Mapped[str] = mapped_column(String(40))
    name: Mapped[str] = mapped_column(String(200))

    case: Mapped[Case] = relationship(back_populates="parties")


class Document(Timestamped, Base):
    """법원 문서 처리 결과. 추출값은 사람이 고치기 전의 원래 값 그대로 남긴다."""

    __tablename__ = "documents"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=_uuid)
    file_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("files.id"), index=True)
    case_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("cases.id"), index=True)
    document_type: Mapped[str] = mapped_column(String(40))
    issued_date: Mapped[date | None] = mapped_column(Date)
    model: Mapped[str] = mapped_column(String(100))
    text_available: Mapped[bool] = mapped_column(Boolean)
    extraction: Mapped[dict[str, Any]] = mapped_column()
    issues: Mapped[list[Any]] = mapped_column(default=list)
    corrections: Mapped[dict[str, Any]] = mapped_column(default=dict)
    """사람이 고친 사건 정보(court, case_number, case_name). 추출값(extraction)은 그대로 둔다."""
    pending_dismissed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    """'송달일 입력 대기' 목록에서 뺀 시각(기한을 잡지 않기로 한 문서)."""

    case: Mapped[Case | None] = relationship(back_populates="documents")
    file: Mapped[File] = relationship()


class Deadline(Timestamped, Base):
    """사람이 확정한 기한. 서버가 다시 계산한 값을 저장한다(화면이 보낸 날짜를 믿지 않는다)."""

    __tablename__ = "deadlines"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=_uuid)
    document_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("documents.id"), index=True)
    case_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("cases.id"), index=True)
    label: Mapped[str] = mapped_column(String(100))
    kind: Mapped[str] = mapped_column(String(16))
    """'statutory'(법정 기간) 또는 'designated'(문서가 정한 기간)."""
    rule_id: Mapped[str | None] = mapped_column(String(60))
    period_amount: Mapped[int] = mapped_column(Integer)
    period_unit: Mapped[str] = mapped_column(String(4))
    event_date: Mapped[date] = mapped_column(Date)
    """송달일(사람이 입력)."""
    service_kind: Mapped[str] = mapped_column(String(30))
    count_start: Mapped[date] = mapped_column(Date)
    nominal_end: Mapped[date] = mapped_column(Date)
    deadline: Mapped[date] = mapped_column(Date, index=True)
    extended_over: Mapped[list[Any]] = mapped_column(default=list)
    basis: Mapped[list[Any]] = mapped_column(default=list)
    warnings: Mapped[list[Any]] = mapped_column(default=list)
    status: Mapped[str] = mapped_column(String(16), default="confirmed", index=True)
    """confirmed(진행 중) | done(완료) | cancelled(취소) | deleted(삭제, 어디에도 보이지 않음)."""
    confirmed_by: Mapped[str] = mapped_column(String(100))
    status_changed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    document: Mapped[Document] = relationship()
    case: Mapped[Case | None] = relationship()


class Event(Timestamped, Base):
    """캘린더 일정. 기일통지서에서 읽은 기일(hearing)과 사람이 넣은 일정(manual). 기한은 deadlines 표에 있다."""

    __tablename__ = "events"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=_uuid)
    kind: Mapped[str] = mapped_column(String(16))
    """hearing(기일) | manual(직접 입력)"""
    title: Mapped[str] = mapped_column(String(200))
    day: Mapped[date] = mapped_column(Date, index=True)
    at: Mapped[time | None] = mapped_column(Time)
    location: Mapped[str | None] = mapped_column(String(200))
    memo: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(16), default="confirmed", index=True)
    """tentative(미확정, 문서에서 읽은 값) | confirmed(확정) | cancelled(취소)"""
    visibility: Mapped[str] = mapped_column(String(16), default="firm")
    """firm(법인 전체) | private(본인만)"""
    case_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("cases.id"), index=True)
    document_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("documents.id"), index=True)
    created_by: Mapped[str] = mapped_column(String(50))
    confirmed_by: Mapped[str | None] = mapped_column(String(50))

    case: Mapped[Case | None] = relationship()
    document: Mapped[Document | None] = relationship()


class Job(Timestamped, Base):
    """채팅 요청 하나를 처리하는 LangGraph 실행. id가 체크포인터의 thread_id다.

    상태의 기준은 이 테이블이다. 체크포인트는 멈춘 곳에서 이어가는 데만 쓴다.
    """

    __tablename__ = "jobs"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=_uuid)
    conversation_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("conversations.id", ondelete="CASCADE"), index=True)
    status: Mapped[str] = mapped_column(String(16), default="running", index=True)
    """running(진행 중) | waiting(되묻기 답을 기다림) | done(완료) | error(오류) | stopped(중지)."""
    question: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    """waiting일 때 사용자에게 물은 내용."""
    graph_version: Mapped[str] = mapped_column(String(20))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class Draft(Timestamped, Base):
    """서식 초안. 사람이 검토하고 제출한다."""

    __tablename__ = "drafts"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=_uuid)
    case_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("cases.id"), index=True)
    form_id: Mapped[str] = mapped_column(String(60))
    file_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("files.id"))
    values: Mapped[dict[str, Any]] = mapped_column()
    blanks: Mapped[list[Any]] = mapped_column(default=list)
    """'나중에 입력'으로 빈칸으로 둔 항목 이름."""
    job_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("jobs.id"))
    created_by: Mapped[str | None] = mapped_column(String(50))
    reviewed_by: Mapped[str | None] = mapped_column(String(50))
    """검토를 마친 변호사의 아이디. 비어 있으면 검토 전이다."""
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    final_file_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("files.id"))
    """사람이 고쳐 실제로 낸 최종본. 있으면 자료실에는 초안 대신 이것이 들어간다."""
    final_uploaded_by: Mapped[str | None] = mapped_column(String(50))
    final_uploaded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    file: Mapped[File] = relationship(foreign_keys=[file_id])
    final_file: Mapped[File | None] = relationship(foreign_keys=[final_file_id])
    case: Mapped[Case | None] = relationship()


class AuditLog(Base):
    """누가 언제 무엇을 바꿨는지. actor는 로그인한 사용자의 아이디, 사람이 아닌 작업은 'system'이다."""

    __tablename__ = "audit_log"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)
    actor: Mapped[str] = mapped_column(String(100))
    action: Mapped[str] = mapped_column(String(60))
    target_type: Mapped[str] = mapped_column(String(40))
    target_id: Mapped[str] = mapped_column(String(64))
    detail: Mapped[dict[str, Any]] = mapped_column(default=dict)


class LibraryDoc(Timestamped, Base):
    """자료실 문서. 사람이 올린 과거 서면·서식, 채팅에서 처리한 법원 문서, lawca가 만든 초안."""

    __tablename__ = "library_docs"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=_uuid)
    title: Mapped[str] = mapped_column(String(300))
    kind: Mapped[str] = mapped_column(String(20), index=True)
    """filing(서면) | form(서식) | court(법원 문서) | draft(lawca 초안) | other"""
    file_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("files.id"))
    case_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("cases.id"), index=True)
    document_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("documents.id", ondelete="CASCADE"), unique=True)
    draft_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("drafts.id", ondelete="CASCADE"), unique=True)
    created_by: Mapped[str] = mapped_column(String(50))
    chunk_count: Mapped[int] = mapped_column(Integer, default=0)
    embedded: Mapped[bool] = mapped_column(Boolean, default=False)
    """모든 조각에 임베딩이 있는지. false면 키워드로만 찾힌다."""
    embedding_model: Mapped[str | None] = mapped_column(String(100))

    file: Mapped[File] = relationship()
    case: Mapped[Case | None] = relationship()
    draft: Mapped[Draft | None] = relationship()
    chunks: Mapped[list[LibraryChunk]] = relationship(
        back_populates="doc", order_by="LibraryChunk.seq", cascade="all, delete-orphan"
    )


class LibraryChunk(Base):
    """자료실 문서의 조각. 키워드(트라이그램)와 의미(벡터)로 찾는다."""

    __tablename__ = "library_chunks"
    __table_args__ = (
        Index(
            "ix_library_chunks_compact_trgm", "compact", postgresql_using="gin", postgresql_ops={"compact": "gin_trgm_ops"}
        ),
        Index(
            "ix_library_chunks_embedding",
            "embedding",
            postgresql_using="hnsw",
            postgresql_ops={"embedding": "vector_cosine_ops"},
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    doc_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("library_docs.id", ondelete="CASCADE"), index=True)
    seq: Mapped[int] = mapped_column(Integer)
    page: Mapped[int | None] = mapped_column(Integer)
    text: Mapped[str] = mapped_column(Text)
    compact: Mapped[str] = mapped_column(Text, Computed(r"regexp_replace(text, '\s+', '', 'g')", persisted=True))
    """공백을 뺀 글. "사 실 조 회"처럼 띄어 쓴 제목도 "사실조회"로 찾히게 키워드 검색은 이것으로 한다."""
    embedding: Mapped[list[float] | None] = mapped_column(Vector(1024))

    doc: Mapped[LibraryDoc] = relationship(back_populates="chunks")
