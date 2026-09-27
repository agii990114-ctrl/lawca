"""API 요청·응답 형식."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

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

    document_id: str | None = None
    """저장한 문서의 id. 기한을 확정할 때 쓴다."""
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


ServiceKind = Literal["electronic_confirmed", "electronic_deemed", "paper"]

SERVICE_LABELS: dict[str, str] = {
    "electronic_confirmed": "전자소송 확인일",
    "electronic_deemed": "전자소송 간주 송달일",
    "paper": "종이 문서 수령일",
}


class DeadlineConfirmRequest(BaseModel):
    """기한 확정. 서버는 이 입력으로 기한을 다시 계산해 저장한다."""

    document_id: str | None = None
    file_id: str | None = None
    """document_id가 없는 예전 카드는 파일로 가장 최근 문서를 찾는다."""
    label: str
    event_date: date
    rule_id: str | None = None
    period: PeriodOut | None = None
    service_kind: ServiceKind


class DeadlineRecordOut(BaseModel):
    id: str
    status: Literal["confirmed", "done", "cancelled"]
    label: str
    kind: str
    rule_id: str | None
    period: PeriodOut
    event_date: date
    service_kind: str
    service_label: str
    count_start: date
    nominal_end: date
    deadline: date
    extended_over: list[dict[str, Any]]
    basis: list[str]
    warnings: list[str]
    created_at: datetime
    document_id: str
    document_type: str
    file_id: str
    filename: str
    case_number: str | None
    court: str | None
    case_name: str | None


class DeadlineStatusUpdate(BaseModel):
    status: Literal["confirmed", "done", "cancelled"]


class ResumeRequest(BaseModel):
    answers: dict[str, str]


class JobOut(BaseModel):
    id: str
    status: str
    question: dict[str, Any] | None


# 로그인·사용자

Role = Literal["clerk", "lawyer", "admin"]


class LoginRequest(BaseModel):
    username: str
    password: str


class UserOut(BaseModel):
    id: str
    username: str
    name: str
    role: Role
    role_label: str
    created_at: datetime
    last_login_at: datetime | None


class UserCreate(BaseModel):
    username: str = Field(min_length=3, max_length=50, pattern=r"^[A-Za-z0-9._-]+$")
    name: str = Field(min_length=1, max_length=50)
    role: Role
    password: str


class PasswordChange(BaseModel):
    current_password: str
    new_password: str


class PasswordReset(BaseModel):
    password: str


class DraftOut(BaseModel):
    id: str
    form_id: str
    created_by: str | None
    reviewed_by: str | None
    reviewed_at: datetime | None


# 캘린더

Source = Literal["deadline", "hearing", "manual"]


class CalendarItemOut(BaseModel):
    """캘린더에 그리는 한 칸. 기한(deadline)과 일정(hearing·manual)을 같은 모양으로 보낸다."""

    id: str
    source: Source
    day: date
    time: str | None
    """HH:MM(한국 시간). 없으면 종일."""
    title: str
    """캘린더에 보이는 제목(종류·사건번호 포함)."""
    label: str
    """고칠 때 쓰는 원래 제목."""
    status: str
    """deadline: confirmed | done, 일정: tentative | confirmed"""
    case_number: str | None
    court: str | None
    case_name: str | None
    location: str | None
    memo: str
    created_by: str | None
    confirmed_by: str | None
    visibility: Literal["firm", "private"]
    file_id: str | None
    filename: str | None
    document_type: str | None
    details: list[str]
    can_edit: bool
    """이 사용자가 일정을 고치거나 취소할 수 있는지(직접 만든 일정 또는 변호사)."""


class HolidayOut(BaseModel):
    day: date
    name: str


class CalendarOut(BaseModel):
    start: date
    end: date
    items: list[CalendarItemOut]
    holidays: list[HolidayOut]


TIME_PATTERN = r"^([01]\d|2[0-3]):[0-5]\d$"


class EventCreate(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    day: date
    time: str | None = Field(default=None, pattern=TIME_PATTERN)
    location: str | None = Field(default=None, max_length=200)
    memo: str = Field(default="", max_length=2000)
    visibility: Literal["firm", "private"] = "firm"
    case_number: str | None = None


class EventUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=200)
    day: date | None = None
    time: str | None = Field(default=None, pattern=TIME_PATTERN)
    clear_time: bool = False
    """true면 시각을 지워 종일 일정으로 바꾼다."""
    location: str | None = Field(default=None, max_length=200)
    memo: str | None = Field(default=None, max_length=2000)
    status: Literal["confirmed", "cancelled"] | None = None


class PendingDocumentOut(BaseModel):
    """기한을 아직 확정하지 않은 문서(송달일 입력 대기)."""

    document_id: str
    file_id: str
    filename: str
    document_type: str
    case_number: str | None
    court: str | None
    issued_date: date | None
    created_at: datetime
    suggestions: list[SuggestionOut]


class FeedOut(BaseModel):
    path: str
    """구독 주소의 경로. 화면이 자기 주소(origin)를 앞에 붙인다."""
