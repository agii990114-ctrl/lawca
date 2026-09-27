"""추출 정확도 채점. 순수 함수이며 LLM을 쓰지 않는다.

정답(truth)과 추출 결과(CourtDocument)를 항목별로 비교한다.
- 문자열 항목은 공백을 무시하고 비교한다(원문 표기 "서 울 중 앙"과 "서울중앙"을 같게 본다).
- 당사자는 (지위, 이름) 집합이 같아야 맞다.
- 날짜는 정규화한 뒤(YYYY-MM-DD) 비교한다.
- 정답에 없는 항목(skip)은 채점하지 않는다.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from lawca.extraction.normalize import normalize
from lawca.extraction.schema import CourtDocument

FIELDS = ("document_type", "court", "case_number", "case_name", "parties", "issued_date", "designated_period")
WHITESPACE = re.compile(r"\s+")


def _compact(value: str | None) -> str:
    return WHITESPACE.sub("", value or "")


def extracted_values(doc: CourtDocument) -> dict[str, Any]:
    doc = normalize(doc)
    return {
        "document_type": doc.document_type.value,
        "court": doc.court.value if doc.court else None,
        "case_number": doc.case_number.value if doc.case_number else None,
        "case_name": doc.case_name.value if doc.case_name else None,
        "parties": sorted((_compact(p.role), _compact(p.name)) for p in doc.parties),
        "issued_date": doc.issued_date.value if doc.issued_date else None,
        "designated_period": [doc.designated_period.amount, doc.designated_period.unit] if doc.designated_period else None,
    }


def _truth_value(key: str, value: Any) -> Any:
    if key == "parties":
        return sorted((_compact(role), _compact(name)) for role, name in value)
    return value


def field_matches(key: str, expected: Any, actual: Any) -> bool:
    expected = _truth_value(key, expected)
    if key in ("court", "case_number", "case_name", "document_type"):
        return _compact(expected) == _compact(actual)
    return expected == actual


@dataclass
class DocScore:
    name: str
    results: dict[str, bool] = field(default_factory=dict)
    """항목 → 맞았는지. 채점하지 않은 항목은 없다."""
    mismatches: dict[str, tuple[Any, Any]] = field(default_factory=dict)
    """항목 → (정답, 추출값)."""

    @property
    def exact(self) -> bool:
        return all(self.results.values())


def score_document(name: str, truth: dict[str, Any], doc: CourtDocument) -> DocScore:
    actual = extracted_values(doc)
    skip = set(truth.get("skip", []))
    score = DocScore(name)
    for key in FIELDS:
        if key in skip or key not in truth:
            continue
        ok = field_matches(key, truth[key], actual[key])
        score.results[key] = ok
        if not ok:
            score.mismatches[key] = (_truth_value(key, truth[key]), actual[key])
    return score


def summarize(scores: list[DocScore]) -> dict[str, Any]:
    per_field: dict[str, dict[str, int]] = {}
    for score in scores:
        for key, ok in score.results.items():
            counts = per_field.setdefault(key, {"correct": 0, "total": 0})
            counts["total"] += 1
            counts["correct"] += int(ok)
    total = sum(c["total"] for c in per_field.values())
    correct = sum(c["correct"] for c in per_field.values())
    return {
        "documents": len(scores),
        "exact_match": sum(s.exact for s in scores),
        "field_accuracy": correct / total if total else 0.0,
        "per_field": {k: per_field[k] for k in FIELDS if k in per_field},
    }
