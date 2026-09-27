"""조회 에이전트의 도구. 모두 읽기 전용이다(기한 계산도 저장하지 않는다).

도구마다 두 가지를 돌려준다.
- result: LLM에게 줄 요약 데이터
- card: 화면에 보여 줄 카드. 날짜는 카드가 기준이고, LLM은 날짜를 새로 계산하지 않는다.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any

from sqlalchemy.orm import Session

from lawca.agent.llm import ToolSpec
from lawca.agent.ranges import RANGES, resolve_range
from lawca.api.records import record_out
from lawca.api.schemas import DeadlineOut
from lawca.db import repo
from lawca.db.models import Case, Deadline
from lawca.library import store as library_store
from lawca.deadlines import (
    CalendarCoverageError,
    Period,
    Unit,
    compute_designated_deadline,
    compute_statutory_deadline,
    load_rules,
)

WEEKDAYS = "월화수목금토일"
STATUS_LABELS = {"confirmed": "확정", "done": "완료", "cancelled": "취소"}


@dataclass(frozen=True)
class ToolOutcome:
    label: str
    """진행 단계에 보일 설명."""
    result: dict[str, Any]
    card: dict[str, Any] | None = None


def _specs() -> list[ToolSpec]:
    rules = load_rules()
    rule_help = ", ".join(f"{r.id}({r.name} {r.period})" for r in rules.values())
    return [
        ToolSpec(
            name="list_deadlines",
            description=(
                "확정한 기한 목록을 만료일 순으로 조회한다. 기간은 range로 고르고 날짜를 직접 계산하지 않는다. "
                "range=custom일 때만 date_from·date_to(YYYY-MM-DD)를 쓴다."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "range": {
                        "type": "string",
                        "enum": list(RANGES),
                        "description": ", ".join(f"{k}: {v}" for k, v in RANGES.items()),
                    },
                    "date_from": {"type": "string", "description": "range=custom일 때 시작일 YYYY-MM-DD"},
                    "date_to": {"type": "string", "description": "range=custom일 때 종료일 YYYY-MM-DD"},
                    "status": {
                        "type": "string",
                        "enum": ["confirmed", "done", "cancelled", "any"],
                        "description": "confirmed(진행 중, 기본), done(완료), cancelled(취소), any(전체)",
                    },
                    "case_number": {"type": "string", "description": "특정 사건만 볼 때 사건번호. 예: 2026가단12345"},
                },
                "required": ["range"],
            },
        ),
        ToolSpec(
            name="search_cases",
            description="사건번호, 사건명, 법원, 당사자 이름으로 사건을 찾는다.",
            parameters={
                "type": "object",
                "properties": {"query": {"type": "string", "description": "찾을 말. 예: 홍길동, 대여금, 2026가단"}},
                "required": ["query"],
            },
        ),
        ToolSpec(
            name="get_case",
            description="사건번호로 사건의 당사자, 받은 문서, 기한을 조회한다.",
            parameters={
                "type": "object",
                "properties": {"case_number": {"type": "string", "description": "예: 2026가단12345"}},
                "required": ["case_number"],
            },
        ),
        ToolSpec(
            name="search_library",
            description=(
                "자료실(법인이 올린 과거 서면·서식, 처리한 법원 문서, lawca가 만든 초안)에서 내용을 찾는다. "
                "키워드와 의미로 함께 찾고, 문서마다 가장 맞는 부분을 돌려준다."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "찾을 내용. 예: 공시송달 신청 사유, 주소보정서"},
                    "kind": {
                        "type": "string",
                        "enum": ["filing", "form", "court", "draft", "other"],
                        "description": "filing(서면), form(서식), court(법원 문서), draft(lawca 초안). 모르면 비운다",
                    },
                    "case_number": {"type": "string", "description": "특정 사건 자료만 찾을 때"},
                },
                "required": ["query"],
            },
        ),
        ToolSpec(
            name="compute_deadline",
            description=(
                "송달일(또는 고지일)과 기간으로 만료일을 계산한다. 저장하지 않는다. "
                f"법정 기간은 rule_id로 고른다: {rule_help}. 문서가 정한 기간은 amount와 unit으로 준다."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "event_date": {"type": "string", "description": "송달일 또는 고지일 YYYY-MM-DD"},
                    "rule_id": {"type": "string", "enum": list(rules)},
                    "amount": {"type": "integer", "description": "문서가 정한 기간의 숫자"},
                    "unit": {"type": "string", "enum": ["일", "주", "월", "년"]},
                    "deemed_electronic_service": {
                        "type": "boolean",
                        "description": "전자소송 간주 송달이면 true",
                    },
                },
                "required": ["event_date"],
            },
        ),
    ]


TOOLS = _specs()


def _deadline_summary(d: Deadline, today: date) -> dict[str, Any]:
    return {
        "deadline": d.deadline.isoformat(),
        "weekday": WEEKDAYS[d.deadline.weekday()],
        "days_left": (d.deadline - today).days,
        "label": d.label,
        "status": STATUS_LABELS[d.status],
        "case_number": d.case.case_number if d.case else None,
        "case_name": d.case.case_name if d.case else None,
        "court": d.case.court if d.case else None,
        "document_type": d.document.document_type,
        "served_on": d.event_date.isoformat(),
    }


def _case_summary(c: Case, open_deadlines: int) -> dict[str, Any]:
    return {
        "case_number": c.case_number,
        "court": c.court,
        "case_name": c.case_name,
        "parties": [{"role": p.role, "name": p.name} for p in c.parties],
        "documents": [
            {
                "document_type": d.document_type,
                "issued_date": d.issued_date.isoformat() if d.issued_date else None,
                "filename": d.file.name,
                "file_id": str(d.file_id),
            }
            for d in sorted(c.documents, key=lambda d: d.created_at)
        ],
        "open_deadlines": open_deadlines,
    }


def list_deadlines(session: Session, today: date, args: dict[str, Any]) -> ToolOutcome:
    start, end, description = resolve_range(args.get("range", "upcoming"), today, args.get("date_from"), args.get("date_to"))
    status = args.get("status") or "confirmed"
    statuses = None if status == "any" else [status]
    rows = repo.list_deadlines(
        session, statuses, date_from=start, date_to=end, case_number=args.get("case_number") or None
    )
    title = f"기한 · {description}" + (f" · {args['case_number']}" if args.get("case_number") else "")
    return ToolOutcome(
        label=f"기한 조회: {description}",
        result={"range": description, "count": len(rows), "items": [_deadline_summary(d, today) for d in rows[:50]]},
        card={"kind": "deadlines", "title": title, "items": [record_out(d).model_dump(mode="json") for d in rows[:50]]},
    )


def _open_counts(session: Session, cases: list[Case]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for c in cases:
        counts[c.case_number] = len(repo.list_deadlines(session, ["confirmed"], case_number=c.case_number))
    return counts


def search_cases(session: Session, today: date, args: dict[str, Any]) -> ToolOutcome:
    query = str(args.get("query", "")).strip()
    cases = repo.search_cases(session, query) if query else []
    counts = _open_counts(session, cases)
    items = [_case_summary(c, counts[c.case_number]) for c in cases]
    return ToolOutcome(
        label=f"사건 검색: {query}",
        result={"query": query, "count": len(items), "items": items},
        card={"kind": "cases", "title": f"사건 검색 · {query}", "items": items},
    )


def get_case(session: Session, today: date, args: dict[str, Any]) -> ToolOutcome:
    number = str(args.get("case_number", ""))
    case = repo.get_case(session, number)
    if case is None:
        return ToolOutcome(label=f"사건 조회: {number}", result={"found": False, "case_number": number})
    deadlines = repo.list_deadlines(session, ["confirmed", "done"], case_number=case.case_number)
    detail = _case_summary(case, sum(1 for d in deadlines if d.status == "confirmed"))
    detail["deadlines"] = [_deadline_summary(d, today) for d in deadlines]
    return ToolOutcome(
        label=f"사건 조회: {case.case_number}",
        result={"found": True, **detail},
        card={"kind": "cases", "title": f"사건 · {case.case_number}", "items": [detail]},
    )


def compute_deadline(session: Session, today: date, args: dict[str, Any]) -> ToolOutcome:
    deemed = bool(args.get("deemed_electronic_service"))
    try:
        event = date.fromisoformat(str(args["event_date"]))
        if args.get("rule_id"):
            rule = load_rules()[str(args["rule_id"])]
            result = compute_statutory_deadline(rule.id, event, deemed_electronic_service=deemed)
            label = rule.name
        else:
            units = {u.value: u for u in Unit}
            period = Period(int(args["amount"]), units[str(args["unit"])])
            result = compute_designated_deadline(event, period, deemed_electronic_service=deemed)
            label = f"정한 기간 {period}"
    except (KeyError, ValueError, CalendarCoverageError) as exc:
        return ToolOutcome(label="기한 계산", result={"error": f"계산할 수 없습니다: {exc}"})
    out = DeadlineOut.of(result)
    return ToolOutcome(
        label=f"기한 계산: {label}",
        result={
            "deadline": out.deadline.isoformat(),
            "weekday": WEEKDAYS[out.deadline.weekday()],
            "count_start": out.count_start.isoformat(),
            "extended_over": [f"{d.day.isoformat()} {d.reason}" for d in out.extended_over],
            "basis": out.basis,
            "warnings": out.warnings,
            "saved": False,
        },
        card={"kind": "deadline_calc", "label": label, **out.model_dump(mode="json")},
    )


HANDLERS = {
    "list_deadlines": list_deadlines,
    "search_cases": search_cases,
    "get_case": get_case,
    "compute_deadline": compute_deadline,
}


def search_library(session: Session, args: dict[str, Any], embedder: Any) -> ToolOutcome:
    query = str(args.get("query") or "").strip()
    if not query:
        raise ValueError("찾을 내용이 비어 있습니다.")
    hits = library_store.search(
        session, query, embedder, kind=args.get("kind") or None, case_number=args.get("case_number") or None, limit=6
    )
    terms = library_store.terms_of(query)
    items = [
        {
            "doc_id": str(h.doc.id),
            "title": h.doc.title,
            "kind": h.doc.kind,
            "kind_label": library_store.kind_label(h.doc.kind),
            "snippet": library_store.snippet(h.chunk.text, terms),
            "page": h.chunk.page,
            "file_id": str(h.doc.file_id),
            "filename": h.doc.file.name,
            "case_number": h.doc.case.case_number if h.doc.case else None,
            "created_at": h.doc.created_at.date().isoformat(),
            "matched": sorted(h.matched),
        }
        for h in hits
    ]
    result = {
        "count": len(items),
        "semantic": embedder is not None,
        "results": [{**i, "text": h.chunk.text[:800]} for i, h in zip(items, hits)],
    }
    card = {"kind": "search", "query": query, "items": items}
    return ToolOutcome(label=f"자료실 검색: {query}", result=result, card=card)


def run_tool(name: str, args: dict[str, Any], session: Session, today: date, embedder: Any = None) -> ToolOutcome:
    if name == "search_library":
        try:
            return search_library(session, args, embedder)
        except ValueError as exc:
            return ToolOutcome(label=name, result={"error": str(exc)})
    handler = HANDLERS.get(name)
    if handler is None:
        return ToolOutcome(label=name, result={"error": f"알 수 없는 도구입니다: {name}"})
    try:
        return handler(session, today, args)
    except ValueError as exc:
        return ToolOutcome(label=name, result={"error": str(exc)})

