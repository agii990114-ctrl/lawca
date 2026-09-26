"""API 요청·응답 형식."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any, Literal

from pydantic import BaseModel

from lawca.deadlines import DeadlineResult, Period
from lawca.extraction.schema import CourtDocument


class PeriodOut(BaseModel):
    amount: int
    unit: Literal["일", "주", "월", "년"]
    label: str

    @classmethod
    def of(cls, period: Period) -> PeriodOut:
        return cls(amount=period.amount, unit=period.unit.value, label=str(period))


class IssueOut(BaseModel):
    field: str
    level: Literal["error", "warning"]
    message: str


class SuggestionOut(BaseModel):
    kind: Literal["statutory", "designated"]
    label: str
    rule_id: str | None
    period: PeriodOut | None
    note: str | None


class FileOut(BaseModel):
    id: str
    name: str
    size: int
    mime: str
    pages: int | None


class DocumentOut(BaseModel):
    """문서 처리 결과. 채팅에서는 'document' 카드로 보낸다."""

    file_id: str
    filename: str
    model: str
    text_available: bool
    extraction: CourtDocument
    issues: list[IssueOut]
    suggestions: list[SuggestionOut]
    checklist: list[str]


class DeadlineRequest(BaseModel):
    event_date: date
    rule_id: str | None = None
    period: PeriodOut | None = None
    deemed_electronic_service: bool = False


class ExtendedDay(BaseModel):
    day: date
    reason: str


class DeadlineOut(BaseModel):
    event_date: date
    period: PeriodOut
    count_start: date
    nominal_end: date
    deadline: date
    extended_over: list[ExtendedDay]
    basis: list[str]
    warnings: list[str]

    @classmethod
    def of(cls, r: DeadlineResult) -> DeadlineOut:
        return cls(
            event_date=r.event_date,
            period=PeriodOut.of(r.period),
            count_start=r.count_start,
            nominal_end=r.nominal_end,
            deadline=r.deadline,
            extended_over=[ExtendedDay(day=d, reason=why) for d, why in r.extended_over],
            basis=list(r.basis),
            warnings=list(r.warnings),
        )


class ChatRequest(BaseModel):
    conversation_id: str
    message: str = ""
    file_ids: list[str] = []


class ConversationSummary(BaseModel):
    id: str
    title: str
    updated_at: datetime


class MessageOut(BaseModel):
    id: str
    role: Literal["user", "assistant"]
    text: str
    attachments: list[FileOut]
    steps: list[dict[str, Any]]
    cards: list[dict[str, Any]]
    state: str
    created_at: datetime


class ConversationDetail(ConversationSummary):
    messages: list[MessageOut]
