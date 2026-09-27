"""추출값 정규화. 모델마다 다르게 쓰는 표기를 코드로 맞춘다(LLM에게 맡기지 않는다).

- 날짜: "2026. 9. 15.", "2026년 9월 15일", "2026.09.15" → "2026-09-15"
- 사건번호: 공백 제거("2026가단 51234" → "2026가단51234")
- 기일 시각: "오후 2시 30분", "14시 30분", "14:30" → "14:30"

근거 문구(evidence)는 원문 대조에 쓰므로 바꾸지 않는다.
"""

from __future__ import annotations

import re
from datetime import date

from lawca.extraction.schema import CourtDocument

DATE_PATTERN = re.compile(r"^\s*(\d{4})\s*(?:[.\-/]|년)\s*(\d{1,2})\s*(?:[.\-/]|월)\s*(\d{1,2})\s*(?:\.|일)?\s*$")


def normalize_date(text: str) -> str:
    """한국식 날짜 표기를 YYYY-MM-DD로 바꾼다. 날짜로 읽을 수 없으면 그대로 둔다(검증 단계가 오류로 잡는다)."""
    match = DATE_PATTERN.match(text)
    if not match:
        return text
    try:
        return date(int(match[1]), int(match[2]), int(match[3])).isoformat()
    except ValueError:
        return text


TIME_PATTERN = re.compile(r"^\s*(오전|오후)?\s*(\d{1,2})\s*(?::|시)\s*(?:(\d{1,2})\s*분?)?\s*$")


def normalize_time(text: str) -> str:
    """한국식 시각 표기를 HH:MM으로 바꾼다. 읽을 수 없으면 그대로 둔다."""
    match = TIME_PATTERN.match(text)
    if not match:
        return text
    hour, minute = int(match[2]), int(match[3] or 0)
    if match[1] == "오후" and hour < 12:
        hour += 12
    elif match[1] == "오전" and hour == 12:
        hour = 0
    if hour > 23 or minute > 59:
        return text
    return f"{hour:02d}:{minute:02d}"


def normalize(doc: CourtDocument) -> CourtDocument:
    updates: dict[str, object] = {}
    if doc.issued_date is not None:
        updates["issued_date"] = doc.issued_date.model_copy(update={"value": normalize_date(doc.issued_date.value)})
    if doc.case_number is not None:
        updates["case_number"] = doc.case_number.model_copy(
            update={"value": re.sub(r"\s+", "", doc.case_number.value)}
        )
    if doc.hearing is not None:
        updates["hearing"] = doc.hearing.model_copy(
            update={
                "date": normalize_date(doc.hearing.date),
                "time": normalize_time(doc.hearing.time) if doc.hearing.time else None,
            }
        )
    return doc.model_copy(update=updates)
