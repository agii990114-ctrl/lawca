"""DB 테이블 정의.

지금 저장하는 것: 대화·메시지, 첨부 파일, 사건·당사자, 문서(추출 결과), 확정한 기한, 감사 기록.
LangGraph 작업(Job)은 해당 기능을 만들 때 추가한다.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Identity,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    type_annotation_map = {dict[str, Any]: JSONB, list[Any]: JSONB, uuid.UUID: UUID(as_uuid=True)}


def _uuid() -> uuid.UUID:
    return uuid.uuid4()


class Timestamped:
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Conversation(Timestamped, Base):
    __tablename__ = "conversations"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=_uuid)
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
    """confirmed(확정) | done(완료) | cancelled(취소)."""
    confirmed_by: Mapped[str] = mapped_column(String(100))
    status_changed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    document: Mapped[Document] = relationship()
    case: Mapped[Case | None] = relationship()


class AuditLog(Base):
    """누가 언제 무엇을 바꿨는지. 사용자 인증 전까지 actor는 'system'이다."""

    __tablename__ = "audit_log"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)
    actor: Mapped[str] = mapped_column(String(100))
    action: Mapped[str] = mapped_column(String(60))
    target_type: Mapped[str] = mapped_column(String(40))
    target_id: Mapped[str] = mapped_column(String(64))
    detail: Mapped[dict[str, Any]] = mapped_column(default=dict)
