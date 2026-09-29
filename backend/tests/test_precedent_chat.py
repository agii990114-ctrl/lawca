"""채팅에서 판례 찾기(변호사 전용). 실제 API는 부르지 않는다."""

import pytest

from lawca.api.app import app
from lawca.api.llm_deps import get_law_api_factory
from lawca.lawapi import PrecedentHit
from tests.conftest import login, make_user
from tests.fakes import FakeChatModel
from tests.test_agent import use_models
from tests.test_api import chat, client, events_of, new_conversation  # noqa: F401 (픽스처 재사용)
from tests.test_citations import FakeLawApi, precedent

REQUEST = "소멸시효 중단사유로서 채무승인의 방법 판례 찾아줘"


@pytest.fixture
def fake_law(client):  # noqa: F811
    hit = PrecedentHit("1", "2025다210470", "대여금", "대법원", "2025.06.05")

    class AnyQuery(FakeLawApi):
        def search_precedents(self, query, *, full_text, limit):
            self.queries.append((query, full_text))
            return [hit] if "소멸시효" in query else []

    api = AnyQuery({}, {"1": precedent("1", "2025다210470", "소멸시효 중단사유로서 채무승인의 방법")})
    app.dependency_overrides[get_law_api_factory] = lambda: (lambda: api)
    yield api
    app.dependency_overrides.pop(get_law_api_factory, None)


def ask(client, request=REQUEST):
    use_models(FakeChatModel(tasks=[("precedent", request)]))
    return chat(client, new_conversation(client), request)


def text_of(events):
    return "".join(e["delta"] for e in events if e["type"] == "text")


def test_lawyer_gets_precedent_candidates_with_only_legal_terms_sent(client, fake_law, db):  # noqa: F811
    make_user(db, "lawyer1", "lawyer")
    login(client, "lawyer1")
    events = ask(client)
    query = fake_law.queries[0][0]
    assert "소멸시효" in query and "판례" not in query and "찾아" not in query  # "판례 찾아줘"는 검색어에서 빠진다
    card = next(e["card"] for e in events if e["type"] == "card")
    assert card["kind"] == "precedents" and query in card["query"]
    assert card["items"][0]["case_number"] == "2025다210470" and card["items"][0]["url"]
    assert "원문을 확인한 뒤 인용" in text_of(events)


def test_clerk_cannot_search_precedents(client, fake_law, db):  # noqa: F811
    events = ask(client)  # client는 사무원으로 로그인해 있다
    assert fake_law.queries == []
    assert "변호사 계정에서만" in text_of(events) and not any(e["type"] == "card" for e in events)


def test_no_result_and_missing_key_are_explained(client, fake_law, db):  # noqa: F811
    make_user(db, "lawyer1", "lawyer")
    login(client, "lawyer1")
    events = ask(client, "임차인 원상회복 판례 찾아줘")
    assert "찾지 못했습니다" in text_of(events) and not any(e["type"] == "card" for e in events)

    app.dependency_overrides[get_law_api_factory] = lambda: (lambda: None)
    assert "LAW_API" in text_of(ask(client))
