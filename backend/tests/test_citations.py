"""판례 후보 찾기(국가법령정보센터)와 준비서면 문체 참고 테스트. 실제 API는 부르지 않는다."""

import httpx
import pytest

from lawca.api.app import app
from lawca.api.llm_deps import get_law_api
from lawca.agent.brief_writer import BriefDraft, BriefInputs, CitationNeed, Paragraph, Section, check
from lawca.citations import find, sanitize
from lawca.lawapi import LawApiClient, Precedent, PrecedentHit, citation_text, statute_url
from tests.conftest import login, make_user
from tests.fakes import FakeChatModel, FakeEmbedder
from tests.test_agent import use_models
from tests.test_api import client  # noqa: F401 (픽스처 재사용)
from tests.test_brief import answer_chat  # noqa: F401

SEARCH = {
    "PrecSearch": {
        "prec": [
            {"판례일련번호": "1", "사건번호": "2025다210470", "사건명": "대여금", "법원명": "대법원", "선고일자": "2025.06.05", "데이터출처명": "대법원"},
            {"판례일련번호": "9", "사건번호": "대법원-2012-두-15340", "사건명": "채무승인", "선고일자": "2013.01.01", "데이터출처명": "국세법령정보시스템"},
        ]
    }
}
DETAIL = {
    "PrecService": {
        "사건번호": "2025다210470", "사건명": "대여금", "법원명": "대법원", "선고일자": "20250605", "판결유형": "판결",
        "판시사항": "<br/>소멸시효 중단사유로서 채무승인의 방법", "판결요지": "<br/>채무자가 채무의 존재를 인식하고 있다는 뜻을 표시하면 된다.",
        "참조조문": " [1] 민법 제168조 제3호, 제177조 / [2] 민사소송법 제415조 <br/>",
    }
}


class FakeLawApi:
    def __init__(self, hits: dict[str, list[PrecedentHit]], details: dict[str, Precedent]):
        self.hits = hits
        self.details = details
        self.queries: list[tuple[str, bool]] = []

    def search_precedents(self, query, *, full_text, limit):
        self.queries.append((query, full_text))
        return self.hits.get(query, [])

    def precedent(self, precedent_id):
        return self.details[precedent_id]


def precedent(pid: str, number: str, holdings: str) -> Precedent:
    return Precedent(id=pid, case_number=number, case_name="대여금", court="대법원", date="20250605", judgment_type="판결",
                     holdings=holdings, summary=holdings, references=["민법 제168조 제3호", "제3조"])


# 클라이언트·표기


def test_client_parses_and_filters_other_sources():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params["OC"] == "secret"
        if request.url.path.endswith("lawSearch.do"):
            assert request.url.params["org"] == "400201"  # 대법원만
            return httpx.Response(200, json=SEARCH)
        return httpx.Response(200, json=DETAIL)

    api = LawApiClient("secret", transport=httpx.MockTransport(handler))
    hits = api.search_precedents("소멸시효 채무승인")
    assert [h.case_number for h in hits] == ["2025다210470"]  # 국세법령정보시스템 판례는 뺀다
    p = api.precedent("1")
    assert p.holdings == "소멸시효 중단사유로서 채무승인의 방법"
    assert p.references == ["민법 제168조 제3호", "민법 제177조", "민사소송법 제415조"]  # 이름 빠진 조문은 앞 법령을 잇는다
    assert citation_text(p) == "대법원 2025. 6. 5. 선고 2025다210470 판결"
    assert statute_url("민법 제168조 제3호").endswith("/%EB%AF%BC%EB%B2%95/%EC%A0%9C168%EC%A1%B0")


def test_sanitize_keeps_only_legal_terms():
    words = sanitize(["소멸시효", "홍길동", "갑제1호증", "3,000만 원", "원고", "채무승인"], "홍길동의 채무 승인", ["홍길동", "김철수"])
    assert words == ["소멸시효", "채무승인"]  # 이름·증거 표시·숫자·당사자 지위 빼고, 두 개 이상이면 쟁점 낱말은 안 보탠다
    assert sanitize(["김철수"], "채무 승인에 의한 소멸시효 중단", ["김철수"]) == ["채무", "승인에", "의한"]


def test_find_broadens_and_reranks():
    hit = PrecedentHit("1", "2025다210470", "대여금", "대법원", "2025.06.05")
    far = PrecedentHit("2", "2020다1", "건물명도", "대법원", "2020.01.01")
    api = FakeLawApi(
        {"원상회복 임차인": [hit, far]},  # 세 낱말로는 없고 두 낱말 조합에서 찾힌다
        {"1": precedent("1", "2025다210470", "임차인의 원상회복 의무의 범위와 통상의 손모"),
         "2": precedent("2", "2020다1", "건물 명도 소송의 관할")},
    )
    found = find(api, "임차인의 원상회복 의무의 범위와 통상의 손모", ["원상회복", "통상손모", "임차인"], [], FakeEmbedder())
    assert found.query == "원상회복 임차인"
    assert [i["case_number"] for i in found.items][0] == "2025다210470"
    assert [s["reference"] for s in found.statutes] == ["민법 제168조 제3호"]  # 법령 이름 없는 '제3조'는 뺀다
    assert all(q != "원상회복 통상손모 임차인" or not ft for q, ft in api.queries[:1])


# API


@pytest.fixture
def fake_law(client):  # noqa: F811
    api = FakeLawApi(
        {"소멸시효 채무승인": [PrecedentHit("1", "2025다210470", "대여금", "대법원", "2025.06.05")]},
        {"1": precedent("1", "2025다210470", "소멸시효 중단사유로서 채무승인의 방법")},
    )
    app.dependency_overrides[get_law_api] = lambda: api
    yield api
    app.dependency_overrides.pop(get_law_api, None)


def test_citations_endpoint_is_for_lawyers_and_strips_names(client, answer_chat, fake_law, db):  # noqa: F811
    url = "/api/cases/2026가단51234/citations"
    body = {"issue": "채무 승인에 의한 소멸시효 중단", "keywords": ["소멸시효", "채무승인", "홍길동"]}
    assert client.post(url, json=body).status_code == 403
    make_user(db, "lawyer1", "lawyer")
    login(client, "lawyer1")
    out = client.post(url, json=body).json()
    assert "홍길동" not in out["query"] and "홍길동" not in " ".join(q for q, _ in fake_law.queries)
    assert out["items"][0]["citation"] == "대법원 2025. 6. 5. 선고 2025다210470 판결"
    assert out["items"][0]["url"].startswith("https://www.law.go.kr/")


# 문체 참고(자료실)


def test_style_examples_are_used_but_copied_facts_are_flagged(client, answer_chat, db):  # noqa: F811
    from tests.test_library import docx_bytes, upload_doc

    upload_doc(client, "예전_준비서면.docx", docx_bytes("준 비 서 면\n1. 피고 주장의 요지\n피고는 증여라고 주장하나 9,999만 원은 대여금입니다."),
               title="예전 원고 준비서면")
    draft = BriefDraft(
        sections=[Section(heading="1. 반박", paragraphs=[Paragraph(text="9,999만 원은 대여금입니다. [인용 확인 필요]", sources=["메모1"])])],
        open_points=[],
        citation_needs=[CitationNeed(issue="대여 사실 증명", keywords=["대여", "증명"])],
    )
    model = FakeChatModel(draft=draft)
    use_models(model)
    make_user(db, "lawyer1", "lawyer")
    login(client, "lawyer1")
    out = client.post("/api/cases/2026가단51234/brief/body", json={"side": "원고", "notes": "- 증여 아님"}).json()
    assert [e["title"] for e in out["examples_used"]] == ["예전 원고 준비서면"]
    assert "참고 서면 1" in model.seen_turns[-1][-1].text
    assert out["checks"]["amounts_not_in_inputs"] == ["9,999만 원"]  # 참고 서면에서 베껴 온 금액은 잡힌다
    assert out["citation_needs"] == [{"issue": "대여 사실 증명", "keywords": ["대여", "증명"]}]


def test_check_ignores_examples_as_inputs():
    inputs = BriefInputs(record="r", opponent=[], evidence=[], notes="- a", examples=("예전 서면: 7,777만 원",))
    draft = BriefDraft(sections=[Section(heading="h", paragraphs=[Paragraph(text="7,777만 원", sources=["메모1"])])], open_points=[])
    assert check(inputs, draft)["amounts_not_in_inputs"] == ["7,777만 원"]
