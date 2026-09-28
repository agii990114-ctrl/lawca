"""국가법령정보센터 Open API(판례). 준비서면의 "[인용 확인 필요]" 자리에 넣을 판례 후보를 찾는다.

- 키(OC)는 .env의 LAW_API. 키가 든 API 주소는 화면에 보내지 않고, 사람에게는 공개 페이지 주소를 준다.
- 밖으로 보내는 것은 법률 용어 검색어뿐이다. 이름·숫자는 lawca.citations가 먼저 걸러 낸다.
- 같은 검색·판례는 프로세스 안에서 다시 부르지 않는다(간단한 캐시).
"""

from __future__ import annotations

import html
import re
import threading
from dataclasses import dataclass, field
from typing import Any, Protocol
from urllib.parse import quote

import httpx

BASE = "https://www.law.go.kr/DRF"
SUPREME_COURT = "400201"
TAG = re.compile(r"<[^>]+>")


class LawApiError(RuntimeError):
    pass


def clean(text: str | None) -> str:
    """HTML 줄바꿈·태그를 걷어 낸다."""
    if not text:
        return ""
    text = re.sub(r"<br\s*/?>", "\n", text)
    return re.sub(r"[ \t]+", " ", html.unescape(TAG.sub("", text))).strip()


@dataclass(frozen=True)
class PrecedentHit:
    id: str
    case_number: str
    case_name: str
    court: str
    date: str
    """YYYY.MM.DD"""
    source: str = ""
    """데이터 출처. 국세법령정보시스템 등 다른 출처의 판례는 상세를 읽을 수 없어 뺀다."""


@dataclass(frozen=True)
class Precedent:
    id: str
    case_number: str
    case_name: str
    court: str
    date: str
    """YYYYMMDD"""
    judgment_type: str
    holdings: str
    """판시사항"""
    summary: str
    """판결요지"""
    references: list[str] = field(default_factory=list)
    """참조조문(예: 민법 제168조 제3호)"""


class LawApi(Protocol):
    def search_precedents(self, query: str, *, full_text: bool, limit: int) -> list[PrecedentHit]: ...

    def precedent(self, precedent_id: str) -> Precedent: ...


def _refs(text: str) -> list[str]:
    """'[1] 민법 제477조 / [2] 민법 제168조 제3호' → ['민법 제477조', '민법 제168조 제3호']"""
    parts = re.split(r"\s*(?:/|,|\[\d+\])\s*", clean(text))
    out: list[str] = []
    law = ""
    for part in parts:
        part = part.strip()
        if not part or "제" not in part:
            continue
        # "민법 제168조 제3호, 제177조" → 법령 이름이 빠진 조각은 앞의 법령 이름을 이어 붙인다
        head = re.match(r"^(.+?)\s*제\d+조", part)
        if head and not head[1].startswith("제"):
            law = head[1].strip()
        elif part.startswith("제") and law:
            part = f"{law} {part}"
        if part not in out:
            out.append(part)
    return out


class LawApiClient:
    def __init__(self, oc: str, transport: httpx.BaseTransport | None = None) -> None:
        self._oc = oc
        self._client = httpx.Client(base_url=BASE, timeout=20, transport=transport)
        self._cache: dict[tuple[Any, ...], Any] = {}
        self._lock = threading.Lock()

    def _get(self, path: str, params: dict[str, Any]) -> dict[str, Any]:
        key = (path, tuple(sorted(params.items())))
        with self._lock:
            if key in self._cache:
                return self._cache[key]
        try:
            res = self._client.get(path, params={"OC": self._oc, "type": "JSON", **params})
        except httpx.HTTPError as exc:
            raise LawApiError(f"국가법령정보센터에 연결하지 못했습니다: {type(exc).__name__}") from exc
        if res.status_code != 200:
            raise LawApiError(f"국가법령정보센터 응답 오류({res.status_code})")
        try:
            body = res.json()
        except ValueError as exc:
            raise LawApiError("국가법령정보센터 응답을 읽지 못했습니다(키가 올바른지 확인하세요).") from exc
        with self._lock:
            self._cache[key] = body
        return body

    def search_precedents(self, query: str, *, full_text: bool = False, limit: int = 20) -> list[PrecedentHit]:
        body = self._get(
            "/lawSearch.do",
            {"target": "prec", "query": query, "display": limit, "search": 2 if full_text else 1, "org": SUPREME_COURT},
        ).get("PrecSearch", {})
        items = body.get("prec") or []
        items = items if isinstance(items, list) else [items]
        return [
            PrecedentHit(
                id=str(i.get("판례일련번호", "")),
                case_number=i.get("사건번호", ""),
                case_name=clean(i.get("사건명")),
                court=i.get("법원명") or "대법원",
                date=i.get("선고일자", ""),
                source=i.get("데이터출처명", ""),
            )
            for i in items
            # 법원 판례 DB의 것만(다른 출처는 사건번호 형식이 '대법원-2012-두-15340'이고 상세를 읽을 수 없다)
            if i.get("판례일련번호") and "-" not in i.get("사건번호", "") and "국세" not in i.get("데이터출처명", "")
        ]

    def precedent(self, precedent_id: str) -> Precedent:
        body = self._get("/lawService.do", {"target": "prec", "ID": precedent_id}).get("PrecService")
        if not body:
            raise LawApiError(f"판례를 찾지 못했습니다: {precedent_id}")
        return Precedent(
            id=precedent_id,
            case_number=body.get("사건번호", ""),
            case_name=clean(body.get("사건명")),
            court=body.get("법원명") or "대법원",
            date=str(body.get("선고일자", "")),
            judgment_type=body.get("판결유형") or "판결",
            holdings=clean(body.get("판시사항")),
            summary=clean(body.get("판결요지")),
            references=_refs(body.get("참조조문") or ""),
        )


def citation_text(p: Precedent) -> str:
    """'대법원 2026. 2. 26. 선고 2025다215255 판결'"""
    d = re.sub(r"\D", "", p.date)
    when = f"{int(d[:4])}. {int(d[4:6])}. {int(d[6:8])}." if len(d) == 8 else p.date
    verb = "자" if p.judgment_type == "결정" else "선고"
    return f"{p.court} {when} {verb} {p.case_number} {p.judgment_type}"


def precedent_url(case_number: str) -> str:
    return f"https://www.law.go.kr/판례/({quote(case_number)})"


def statute_url(reference: str) -> str | None:
    """'민법 제168조 제3호' → 공개 조문 페이지."""
    match = re.match(r"^\s*(.+?)\s*(제\d+조(?:의\d+)?)", reference)
    if not match:
        return None
    return f"https://www.law.go.kr/법령/{quote(match[1].replace(' ', ''))}/{quote(match[2])}"
