"""자료실(RAG) 테스트: 글 뽑기·자르기, 올리기·권한, 키워드+의미 검색, 자동 색인, 채팅 검색 도구."""

import io
from pathlib import Path

import pytest
from docx import Document
from fastapi.testclient import TestClient

from lawca.agent.llm import TextDelta, ToolCall
from lawca.api.app import app, get_extractor_factory
from lawca.api.embedding import get_embedder_factory
from lawca.library import UnsupportedLibraryFile, chunk_pages, detect_mime
from tests.conftest import login, make_user
from tests.fakes import FakeChatModel, FakeEmbedder
from tests.test_agent import use_models
from tests.test_api import FakeExtractor, chat, client, new_conversation  # noqa: F401 (픽스처 재사용)
from lawca.extraction.schema import Party
from tests.test_extraction import correction_order, ev

FIXTURE = Path(__file__).parent / "fixtures" / "synthetic_correction_order.pdf"

SERVICE_BY_PUBLICATION = (
    "공시송달신청서\n\n사건 2025가단1234 대여금\n\n"
    "피고의 주소지를 주민등록초본으로 확인하였으나 이미 말소되었고, 휴대전화도 해지되어 연락이 되지 않습니다.\n"
    "통상의 방법으로는 송달할 수 없으므로 민사소송법 제194조에 따라 공시송달을 신청합니다.\n"
)
ADDRESS = "주소보정서\n\n피고의 새 주소는 서울특별시 강남구 테헤란로 1입니다. 주민등록초본을 첨부합니다.\n"


def docx_bytes(text: str) -> bytes:
    doc = Document()
    for line in text.split("\n"):
        doc.add_paragraph(line)
    out = io.BytesIO()
    doc.save(out)
    return out.getvalue()


def upload_doc(client, name, data, kind="filing", **form):  # noqa: F811
    return client.post("/api/library", files={"file": (name, data)}, data={"kind": kind, **form})


def search(client, q, **params):  # noqa: F811
    return client.get("/api/library/search", params={"q": q, **params}).json()


# 순수 함수


def test_detect_mime_and_unsupported():
    assert detect_mime("a.pdf", b"%PDF-1.4") == "application/pdf"
    assert detect_mime("memo.txt", b"hi") == "text/plain"
    with pytest.raises(UnsupportedLibraryFile, match="HWP"):
        detect_mime("서면.hwp", b"\xd0\xcf")


def test_chunks_follow_paragraphs_and_overlap():
    text = "\n".join(f"{i}번째 문단입니다. " + "가나다라마바사 " * 20 for i in range(10))
    pieces = chunk_pages([text], size=400, overlap=60)
    assert len(pieces) > 3 and all(len(p.text) <= 400 + 200 for p in pieces)
    assert pieces[1].text[:30] in pieces[0].text  # 앞 조각 끝이 겹친다
    assert chunk_pages(["", "  "]) == []
    assert [p.page for p in chunk_pages(["첫 쪽 글", "둘째 쪽 글"])] == [1, 2]


# 올리기·목록·삭제


def test_upload_docx_and_txt_then_search(client):  # noqa: F811
    res = upload_doc(client, "공시송달신청서_2025가단1234.docx", docx_bytes(SERVICE_BY_PUBLICATION), title="공시송달 신청(말소)")
    assert res.status_code == 201
    doc = res.json()
    assert (doc["title"], doc["kind_label"], doc["embedded"]) == ("공시송달 신청(말소)", "서면", True) and doc["chunk_count"] >= 1
    assert upload_doc(client, "주소보정서.txt", ADDRESS.encode("cp949"), kind="form").status_code == 201
    assert [d["kind"] for d in client.get("/api/library").json()] == ["form", "filing"]

    body = search(client, "공시송달")
    assert body["semantic"] is True
    assert body["hits"][0]["doc"]["title"] == "공시송달 신청(말소)"
    assert "keyword" in body["hits"][0]["matched"] and "공시송달" in body["hits"][0]["snippet"]
    assert [h["doc"]["kind"] for h in search(client, "주민등록초본", kind="form")["hits"]] == ["form"]
    assert search(client, "   ")["hits"] == []


def test_semantic_match_without_shared_words(client):  # noqa: F811
    upload_doc(client, "공시송달.docx", docx_bytes(SERVICE_BY_PUBLICATION))
    # 띄어쓰기를 없애 낱말이 그대로 맞지는 않지만 글자 조각이 겹치는 검색어 → 가짜 임베딩의 의미 검색에도 걸린다
    hits = search(client, "".join(SERVICE_BY_PUBLICATION.split()))["hits"]
    assert hits and "semantic" in hits[0]["matched"]


def test_upload_rejections(client):  # noqa: F811
    assert upload_doc(client, "서면.hwp", b"\xd0\xcf\x11").status_code == 415
    assert upload_doc(client, "scan.pdf", b"%PDF-1.4 no text").status_code == 422
    assert upload_doc(client, "a.docx", docx_bytes("x"), kind="court").status_code == 422
    assert upload_doc(client, "a.docx", docx_bytes("내용"), case_number="2099가단1").status_code == 404
    assert client.get("/api/library").json() == []


def test_delete_permissions(client, db):  # noqa: F811
    doc_id = upload_doc(client, "a.docx", docx_bytes(ADDRESS)).json()["id"]
    make_user(db, "clerk2", "clerk")
    make_user(db, "lawyer1", "lawyer")
    other = TestClient(app)
    login(other, "clerk2")
    assert other.get("/api/library").json()[0]["can_delete"] is False
    assert other.delete(f"/api/library/{doc_id}").status_code == 403
    login(other, "lawyer1")
    assert other.delete(f"/api/library/{doc_id}").status_code == 204
    assert client.get("/api/library").json() == [] and search(client, "주소")["hits"] == []


def test_keyword_only_when_embedder_is_down_and_reindex(client):  # noqa: F811
    down = FakeEmbedder(unavailable=True)
    app.dependency_overrides[get_embedder_factory] = lambda: (lambda: down)
    doc = upload_doc(client, "a.docx", docx_bytes(SERVICE_BY_PUBLICATION)).json()
    assert doc["embedded"] is False
    assert search(client, "공시송달")["hits"][0]["matched"] == ["keyword"]

    app.dependency_overrides[get_embedder_factory] = lambda: (lambda: FakeEmbedder())
    assert client.post(f"/api/library/{doc['id']}/reindex").json()["embedded"] is True


# 자동 색인


def test_court_document_with_text_is_indexed(client):  # noqa: F811
    app.dependency_overrides[get_extractor_factory] = lambda: (lambda: FakeExtractor(correction_order()))
    file_id = client.post(
        "/api/files", files={"file": ("보정명령.pdf", FIXTURE.read_bytes(), "application/pdf")}
    ).json()["id"]
    chat(client, new_conversation(client), file_ids=[file_id])
    [doc] = client.get("/api/library", params={"kind": "court"}).json()
    assert doc["title"].startswith("보정명령 2026가단51234") and doc["case_number"] == "2026가단51234"
    assert search(client, "보정", case_number="2026가단51234")["hits"]


def test_scanned_court_document_is_not_indexed(client):  # noqa: F811
    app.dependency_overrides[get_extractor_factory] = lambda: (lambda: FakeExtractor(correction_order()))
    file_id = client.post("/api/files", files={"file": ("scan.pdf", b"%PDF-1.4 fake", "application/pdf")}).json()["id"]
    chat(client, new_conversation(client), file_ids=[file_id])
    assert client.get("/api/library").json() == []


# 채팅 검색 도구


def test_chat_search_tool_returns_search_card(client):  # noqa: F811
    upload_doc(client, "공시송달.docx", docx_bytes(SERVICE_BY_PUBLICATION), title="공시송달 신청(말소)")
    use_models(
        FakeChatModel(
            tasks=[("query", "예전에 쓴 공시송달 신청서 찾아줘")],
            script=[
                [ToolCall("search_library", {"query": "공시송달 신청"})],
                [TextDelta("자료실에서 1건을 찾았습니다.")],
            ],
        )
    )
    events = chat(client, new_conversation(client), "예전에 쓴 공시송달 신청서 찾아줘")
    card = next(e["card"] for e in events if e["type"] == "card")
    assert card["kind"] == "search" and card["items"][0]["title"] == "공시송달 신청(말소)"
    assert any(e["type"] == "status" and e["label"].startswith("자료실 검색") for e in events)


# 초안·최종본과 제목 검색


@pytest.fixture
def two_cases(client):  # noqa: F811
    """보정명령을 처리해 2026가단51234 사건을 만든다(서식 초안의 사건)."""
    parties = [Party(role="원고", name="홍길동", evidence=ev("원 고 홍길동")), Party(role="피고", name="김철수", evidence=ev("피 고 김철수"))]
    app.dependency_overrides[get_extractor_factory] = lambda: (lambda: FakeExtractor(correction_order(parties=parties)))
    file_id = client.post("/api/files", files={"file": ("order.pdf", b"%PDF-1.4 fake", "application/pdf")}).json()["id"]
    chat(client, new_conversation(client), file_ids=[file_id])


def make_draft(client, form_id="fact_inquiry"):  # noqa: F811
    values = {"applicant": "원고 홍길동", "institution": "가상은행", "institution_address": "서울",
              "purpose": "피고의 계좌 명의", "inquiry_items": "1. 계좌 명의인"}
    use_models(FakeChatModel(tasks=[("draft", "사실조회")], form_request=(form_id, "2026가단51234", values)))
    events = chat(client, new_conversation(client), "사실조회신청서 만들어 줘")
    cards = [e["card"] for e in events if e["type"] == "card"]
    assert cards and cards[-1]["kind"] == "draft", [(c["kind"], [f["key"] for f in c.get("fields", [])]) for c in cards]
    return cards[-1]


def test_spaced_title_is_found_by_compact_word(client, two_cases):  # noqa: F811
    make_draft(client)
    # 초안 첫 줄은 "사 실 조 회 신 청 서"로 띄어 쓰여 있다
    hits = search(client, "사실조회신청서")["hits"]
    assert hits and hits[0]["doc"]["title"] == "사실조회신청서_2026가단51234_초안"
    assert hits[0]["doc"]["status_label"] == "검토 전 초안"


def test_only_latest_draft_per_case_and_form(client, two_cases):  # noqa: F811
    first = make_draft(client)
    second = make_draft(client)
    drafts = client.get("/api/library", params={"kind": "draft"}).json()
    assert len(drafts) == 1 and first["draft_id"] != second["draft_id"]


def test_final_version_replaces_draft(client, two_cases):  # noqa: F811
    card = make_draft(client)
    final = docx_bytes("사실조회신청서 최종본\n가상은행에 피고 명의 계좌의 개설일과 잔액을 조회합니다.")
    assert client.post(f"/api/drafts/{card['draft_id']}/final", files={"file": ("최종.txt", b"x")}).status_code == 415
    res = client.post(f"/api/drafts/{card['draft_id']}/final", files={"file": ("사실조회_최종.docx", final)})
    assert res.status_code == 200
    body = res.json()
    assert body["final_filename"] == "사실조회_최종.docx" and body["final_uploaded_by"] == "clerk1"
    docs = client.get("/api/library").json()
    assert [(d["kind"], d["title"], d["status_label"]) for d in docs if d["case_number"]] == [
        ("filing", "사실조회신청서_2026가단51234_최종본", "최종본")
    ]
    assert search(client, "개설일과 잔액")["hits"][0]["doc"]["status_label"] == "최종본"
    # 초안을 다시 만들어도 최종본은 남는다
    make_draft(client)
    kinds = sorted(d["kind"] for d in client.get("/api/library").json() if d["case_number"])
    assert kinds == ["draft", "filing"]


def test_unreviewed_draft_ranks_below_uploaded_filing(client, two_cases):  # noqa: F811
    make_draft(client)
    upload_doc(client, "사실조회신청서_예전.docx", docx_bytes("사 실 조 회 신 청 서\n가상은행 계좌 명의 조회"), title="예전 사실조회신청서")
    titles = [h["doc"]["title"] for h in search(client, "사실조회신청서")["hits"]]
    assert titles[0] == "예전 사실조회신청서"


def test_case_correction_moves_library_entry(client):  # noqa: F811
    app.dependency_overrides[get_extractor_factory] = lambda: (lambda: FakeExtractor(correction_order()))
    file_id = client.post("/api/files", files={"file": ("보정명령.pdf", FIXTURE.read_bytes(), "application/pdf")}).json()["id"]
    events = chat(client, new_conversation(client), file_ids=[file_id])
    document_id = next(e["card"] for e in events if e["type"] == "card")["document_id"]
    client.patch(f"/api/documents/{document_id}/case", json={"case_number": "2026가단777"})
    assert search(client, "보정", case_number="2026가단777")["hits"]
    assert search(client, "보정", case_number="2026가단51234")["hits"] == []


# 서식 작성 때 참고할 과거 서면


def test_references_on_question_and_draft_cards(client, two_cases):  # noqa: F811
    upload_doc(client, "사실조회_예전.docx", docx_bytes("사 실 조 회 신 청 서\n가상카드사에 피고 명의 카드 사용 내역을 조회"), title="예전 사실조회신청서")
    upload_doc(client, "무관.docx", docx_bytes("항소장\n원판결을 취소한다"), title="항소장")

    # 항목이 빠진 요청 → 되묻는 질문에 참고 서면이 실린다
    use_models(FakeChatModel(tasks=[("draft", "사실조회")], form_request=("fact_inquiry", "2026가단51234", {})))
    events = chat(client, new_conversation(client), "사실조회신청서 만들어 줘")
    question = next(e["card"] for e in events if e["type"] == "card")
    assert question["kind"] == "question" and question["stage"] == "fields"
    assert [r["title"] for r in question["references"]] == ["예전 사실조회신청서"]

    # 초안 카드에도 실리고, 방금 만든 초안 자신은 빠진다
    card = make_draft(client)
    assert [r["title"] for r in card["references"]] == ["예전 사실조회신청서"]
    second = make_draft(client)
    assert all(r["doc_id"] for r in second["references"])
    assert "사실조회신청서_2026가단51234_초안" not in [r["title"] for r in second["references"]]


def test_references_are_empty_without_library(client, two_cases):  # noqa: F811
    assert make_draft(client)["references"] == []
