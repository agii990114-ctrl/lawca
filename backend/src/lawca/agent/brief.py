"""상대방 서면(답변서·준비서면) 요약. 사무원이 변호사에게 보고하고 증거 목록을 정리하는 데 쓴다.

- 서면에 적힌 주장과 증거만 옮긴다. 주장의 옳고 그름을 판단하거나 반박을 쓰지 않는다(법률 판단은 변호사).
- 주장마다 원문 문구와 쪽을 붙이고, 코드가 원문에서 문구를 찾아 확인한다(못 찾으면 확인 필요로 표시).
- 증거 표시(을 제1호증, 을제2호증의1 등)는 코드가 읽어 측(갑·을)과 번호로 정리한다.
"""

from __future__ import annotations

import re
from typing import Any

from pydantic import BaseModel, Field

from lawca.agent.llm import ChatModel, Turn, with_fallback

SYSTEM = """\
당신은 한국 법무법인 사무원을 돕습니다. 첨부한 글은 상대방 당사자가 법원에 낸 답변서나 준비서면입니다.
사무원이 담당 변호사에게 보고할 수 있게 서면의 내용을 정리합니다.

- 서면에 적힌 내용만 옮깁니다. 주장이 맞는지 판단하거나, 반박하거나, 법률 의견을 쓰지 않습니다.
- claims: 서면의 주장을 순서대로 5~10개 정도로 나눕니다. point는 한 줄 요지, detail은 두세 문장 설명입니다.
  quote에는 그 주장이 나온 원문 문구를 고치지 말고 그대로 옮기고(60자 이내), page는 [n쪽] 표시의 쪽 번호입니다.
- evidence: 서면이 낸다고 적은 증거(을 제1호증, 갑 제3호증 등)를 표시 그대로와 증거 이름으로 모읍니다.
- request_summary: 청구취지에 대한 답변이나 서면이 구하는 결론을 한두 문장으로 적습니다. 없으면 null.
- submitter: 서면을 낸 쪽(원고·피고 등). 알 수 없으면 null.
"""

MAX_CHARS = 30000
EVIDENCE_LABEL = re.compile(r"(갑|을|병)\s*(?:제)?\s*(\d+)\s*호\s*증(?:\s*의\s*(\d+))?")
WHITESPACE = re.compile(r"\s+")


class Claim(BaseModel):
    point: str = Field(description="주장 요지 한 줄")
    detail: str = Field(description="두세 문장 설명")
    quote: str = Field(description="원문 문구 그대로, 60자 이내")
    page: int = Field(description="문구가 있는 쪽 번호. 1부터")


class CitedEvidence(BaseModel):
    label: str = Field(description="서면에 적힌 표시 그대로. 예: 을 제1호증")
    title: str = Field(description="증거 이름. 예: 차용증, 문자메시지 캡처")


class BriefSummary(BaseModel):
    submitter: str | None
    request_summary: str | None
    claims: list[Claim]
    evidence: list[CitedEvidence]


def parse_label(label: str) -> tuple[str, str] | None:
    """'을 제2호증의1' → ('을', '2-1'). 읽지 못하면 None."""
    match = EVIDENCE_LABEL.search(label)
    if not match:
        return None
    number = match[2] + (f"-{match[3]}" if match[3] else "")
    return match[1], number


def evidence_label(side: str, number: str) -> str:
    main, _, sub = number.partition("-")
    return f"{side} 제{main}호증" + (f"의{sub}" if sub else "")


def number_key(number: str) -> tuple[int, ...]:
    """'2-1' → (2, 1). 번호 순으로 늘어놓을 때 쓴다."""
    return tuple(int(part) if part.isdigit() else 0 for part in number.split("-"))


def _compact(text: str) -> str:
    return WHITESPACE.sub("", text)


def summarize_brief(models: list[ChatModel], pages: list[str]) -> BriefSummary:
    text = "\n\n".join(f"[{i}쪽]\n{page}" for i, page in enumerate(pages, start=1))[:MAX_CHARS]
    return with_fallback(models, lambda m: m.structured(SYSTEM, [Turn("user", text)], BriefSummary))


def summary_out(summary: BriefSummary, pages: list[str]) -> dict[str, Any]:
    """화면·저장용. 주장 문구를 원문에서 확인하고, 증거 표시를 측·번호로 정리한다."""
    compact_pages = [_compact(p) for p in pages]
    claims = []
    for claim in summary.claims:
        quote = _compact(claim.quote)
        in_page = 1 <= claim.page <= len(compact_pages) and quote and quote in compact_pages[claim.page - 1]
        found = next((i + 1 for i, p in enumerate(compact_pages) if quote and quote in p), None)
        claims.append(
            {
                **claim.model_dump(),
                "page": claim.page if in_page else (found or claim.page),
                "verified": bool(in_page or found),
            }
        )
    evidence = []
    seen: set[tuple[str, str]] = set()
    for item in summary.evidence:
        parsed = parse_label(item.label)
        if parsed is None or parsed in seen:
            continue
        seen.add(parsed)
        evidence.append({"side": parsed[0], "number": parsed[1], "label": evidence_label(*parsed), "title": item.title})
    evidence.sort(key=lambda e: (e["side"], number_key(e["number"])))
    return {
        "submitter": summary.submitter,
        "request_summary": summary.request_summary,
        "claims": claims,
        "evidence": evidence,
    }
