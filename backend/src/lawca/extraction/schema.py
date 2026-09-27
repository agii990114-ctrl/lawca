"""법원 문서 추출 결과의 스키마. LLM 응답 스키마와 API 응답에 함께 쓴다."""

from __future__ import annotations

from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, Field


class DocumentType(StrEnum):
    CORRECTION_ORDER = "보정명령"
    JUDGMENT = "판결"
    DECISION = "결정"
    PAYMENT_ORDER = "지급명령"
    SETTLEMENT_RECOMMENDATION = "화해권고결정"
    COMPLAINT_COPY = "소장 부본"
    HEARING_NOTICE = "기일통지서"
    ANSWER = "답변서"
    BRIEF = "준비서면"
    """답변서·준비서면은 법원이 아니라 상대방 당사자가 낸 서면이다(법원이 부본을 송달한다)."""
    OTHER = "기타"


PARTY_FILINGS = (DocumentType.ANSWER, DocumentType.BRIEF)


class Evidence(BaseModel):
    quote: str = Field(description="값을 읽은 원문 문구. 원문 그대로, 60자 이내.")
    page: int = Field(description="문구가 있는 쪽 번호. 1부터 센다.")


class TextField(BaseModel):
    value: str
    evidence: Evidence


class Party(BaseModel):
    role: str = Field(description="문서에 적힌 지위. 예: 원고, 피고, 채권자, 채무자, 신청인")
    name: str
    evidence: Evidence


class DesignatedPeriod(BaseModel):
    amount: int = Field(description="문서에 적힌 기간의 숫자")
    unit: Literal["일", "주", "월", "년"]
    evidence: Evidence


class Hearing(BaseModel):
    date: str = Field(description="출석할 기일의 날짜. YYYY-MM-DD")
    time: str | None = Field(description="시각. HH:MM(24시간). 예: 오후 2시 30분 → 14:30")
    kind: str | None = Field(description="기일 종류. 예: 변론기일, 변론준비기일, 조정기일, 선고기일")
    place: str | None = Field(description="장소. 예: 제303호 법정")
    evidence: Evidence


class CourtDocument(BaseModel):
    document_type: DocumentType
    document_type_evidence: Evidence
    court: TextField | None = Field(description="법원명. 예: 서울중앙지방법원")
    case_number: TextField | None = Field(description="사건번호. 예: 2026가단12345")
    case_name: TextField | None = Field(description="사건명. 예: 대여금")
    parties: list[Party]
    issued_date: TextField | None = Field(description="문서 작성일·발령일·선고일. value는 YYYY-MM-DD")
    order_summary: TextField | None = Field(
        description="명령·주문·결정 내용의 요지. value는 요약, evidence는 핵심 문구"
    )
    designated_period: DesignatedPeriod | None = Field(
        description="문서가 정한 기간. 예: '송달받은 날부터 7일 이내' → 7, 일. 문서에 없으면 null"
    )
    hearing: Hearing | None = Field(
        default=None,
        description="문서가 알리는 기일(출석할 날짜와 시각). 작성일이 아니다. 기일통지서·출석요구서 등에만 있고, 없으면 null",
    )
