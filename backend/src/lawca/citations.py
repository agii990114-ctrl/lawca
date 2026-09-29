"""판례 후보 찾기. "[인용 확인 필요]" 자리의 법적 쟁점으로 대법원 판례를 찾아 변호사가 고르게 한다.

1. 검색어를 거른다: 사건 당사자 이름, 숫자, 증거 표시, 한 글자 낱말을 빼고 법률 용어만 남긴다(밖으로 나가는 글).
2. 사건명으로 찾고(정확), 적으면 본문으로 찾는다(넓음). API는 낱말을 모두 담은 판례만 주므로, 모자라면 낱말을 줄여 다시 찾는다. 대법원만.
3. 로컬 임베딩(bge-m3)으로 쟁점과 사건명의 뜻을 비교해 추린 뒤(API는 최신순으로만 준다),
   몇 건의 상세(판시사항·판결요지)를 읽어 쟁점과 다시 비교한다. 뜻이 먼 판례는 보여 주지 않는다.
4. 넣을지는 변호사가 고른다.
"""

from __future__ import annotations

import logging
import math
import re
from itertools import combinations
from dataclasses import dataclass
from typing import Any

from lawca.lawapi import LawApi, LawApiError, Precedent, PrecedentHit, citation_text, precedent_url, statute_url
from lawca.library import Embedder, EmbeddingUnavailable

log = logging.getLogger(__name__)
MAX_KEYWORDS = 3
POOL = 20
DETAIL = 5
SHOW = 3
MIN_SIMILARITY = 0.45
"""쟁점과 판시사항·판결요지의 코사인 유사도가 이보다 낮으면 관련이 적다고 보고 뺀다(bge-m3 기준, 시험으로 맞춘 값)."""


REQUEST_WORDS = re.compile(r"(?:판례|대법원|판결|사례|검색|관련|쟁점|찾아\S*|알려\S*|보여\S*|줘|주세요)[을를은는이가에]?")
"""채팅으로 "판례 찾아줘"라고 요청할 때 딸려 오는 말. 검색어로 쓰지 않는다."""

NOISE = re.compile(r"(갑|을|병)\s*제?\s*\d+\s*호\s*증(?:\s*의\s*\d+)?|\d[\d,.]*\s*(?:만|억|천)?\s*원?|\d+")


def _strip(text: str) -> str:
    """증거 표시·금액·숫자를 통째로 지운다(쪼개진 조각이 검색어로 새지 않게)."""
    return NOISE.sub(" ", text)


def sanitize(keywords: list[str], issue: str, forbidden: list[str]) -> list[str]:
    """밖으로 보낼 검색어. 이름·숫자·증거 표시를 빼고 두 글자 이상 한글 낱말만 남긴다."""
    names = {re.sub(r"\s+", "", n) for n in forbidden if n and len(n.strip()) >= 2}
    words: list[str] = []
    # 검색어가 두 개 이상이면 그것만 쓰고, 모자랄 때만 쟁점 문장의 낱말을 보탠다
    keywords = [_strip(k) for k in keywords]
    issue = _strip(issue)
    given = [w for k in keywords for w in re.findall(r"[가-힣]{2,}", k)]
    for raw in given if len(set(given)) >= 2 else [*keywords, *re.findall(r"[가-힣]{2,}", issue)]:
        for word in re.findall(r"[가-힣]{2,}", raw):
            if any(name in word or word in name for name in names):
                continue
            if re.fullmatch(r"(갑|을|병)(제)?\d*호증?|원고|피고|신청인|피신청인|채권자|채무자", word):
                continue
            if REQUEST_WORDS.fullmatch(word):
                continue
            if word not in words:
                words.append(word)
    return words[:MAX_KEYWORDS]


def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    norm = math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(y * y for y in b))
    return dot / norm if norm else 0.0


def _rank(issue: str, hits: list[PrecedentHit], embedder: Embedder | None, words: list[str]) -> list[PrecedentHit]:
    """쟁점과 사건명의 뜻이 가까운 순. 임베딩을 못 쓰면 검색어가 사건명에 많이 든 순."""
    if embedder is not None and hits:
        try:
            vectors = embedder.embed([issue, *[h.case_name for h in hits]])
            scores = [_cosine(vectors[0], v) for v in vectors[1:]]
            return [h for _, h in sorted(zip(scores, hits), key=lambda t: -t[0])]
        except EmbeddingUnavailable:
            log.warning("임베딩 없이 판례 후보를 늘어놓습니다")
    return sorted(hits, key=lambda h: -sum(w in h.case_name for w in words))


@dataclass
class Candidates:
    query: str
    """실제로 밖에 보낸 검색어."""
    items: list[dict[str, Any]]
    statutes: list[dict[str, Any]]


def _item(p: Precedent) -> dict[str, Any]:
    return {
        "id": p.id,
        "citation": citation_text(p),
        "case_number": p.case_number,
        "case_name": p.case_name,
        "date": p.date,
        "holdings": p.holdings[:600],
        "summary": p.summary[:900],
        "references": p.references,
        "url": precedent_url(p.case_number),
    }


MAX_SEARCHES = 8
"""판례 하나를 찾을 때 API 검색을 부르는 최대 횟수(상세 읽기는 따로 DETAIL건)."""


def _gather(api: LawApi, words: list[str]) -> tuple[str, list[PrecedentHit]]:
    """낱말을 모두 담은 판례부터 찾고, 모자라면 본문 검색과 낱말 빼기(한 개씩)로 넓힌다. 실제로 쓴 검색어도 돌려준다.

    예: "원상회복 통상손모 임차인" → 판결문은 "통상의 손모"라고 써서 안 맞으면 "원상회복 임차인"으로 다시 찾는다.
    """
    hits: dict[str, PrecedentHit] = {}
    tried: list[str] = []
    calls = 0
    for n in range(len(words), 1, -1):
        for combo in combinations(words, n):
            query = " ".join(combo)
            for full_text in (False, True):
                if calls >= MAX_SEARCHES:
                    return " / ".join(tried), list(hits.values())
                calls += 1
                found = api.search_precedents(query, full_text=full_text, limit=POOL)
                if found and query not in tried:
                    tried.append(query)
                for hit in found:
                    hits.setdefault(hit.id, hit)
                if len(hits) >= DETAIL * 2:
                    return " / ".join(tried), list(hits.values())
    return " / ".join(tried) or " ".join(words), list(hits.values())


def _rerank(issue: str, precedents: list[Precedent], embedder: Embedder | None) -> list[Precedent]:
    """쟁점과 판시사항·판결요지를 비교해 가까운 순으로, 너무 먼 것은 뺀다. 임베딩을 못 쓰면 순서를 그대로 둔다."""
    if embedder is None or not precedents:
        return precedents
    try:
        texts = [f"{p.holdings[:400]} {p.summary[:400]}".strip() or p.case_name for p in precedents]
        vectors = embedder.embed([issue, *texts])
    except EmbeddingUnavailable:
        return precedents
    scored = sorted(((_cosine(vectors[0], v), p) for v, p in zip(vectors[1:], precedents)), key=lambda t: -t[0])
    return [p for score, p in scored if score >= MIN_SIMILARITY]


def find(api: LawApi, issue: str, keywords: list[str], forbidden: list[str], embedder: Embedder | None) -> Candidates:
    words = sanitize(keywords, issue, forbidden)
    if not words:
        raise LawApiError("검색할 법률 용어가 없습니다. 쟁점을 법률 용어로 적어 주세요.")
    query, hits = _gather(api, words)
    shortlist = _rank(issue, hits, embedder, words)[:DETAIL]
    precedents = []
    for hit in shortlist:
        try:
            precedents.append(api.precedent(hit.id))
        except LawApiError as exc:
            log.warning("판례 상세를 읽지 못했습니다: %s", exc)
    precedents = _rerank(issue, precedents, embedder)[:SHOW]
    statutes: list[dict[str, Any]] = []
    for p in precedents:
        for ref in p.references:
            # "제3조"처럼 법령 이름이 빠진 조각은 어느 법인지 몰라 뺀다
            if not ref.startswith("제") and ref not in [s["reference"] for s in statutes]:
                statutes.append({"reference": ref, "url": statute_url(ref)})
    return Candidates(query=query, items=[_item(p) for p in precedents], statutes=statutes[:6])
