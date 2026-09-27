"""상대방 서면 요약, 증거 목록, 준비서면 틀, 사건 API 테스트."""

import io
from pathlib import Path

import pytest
from docx import Document

from lawca.agent.brief import BriefSummary, CitedEvidence, Claim, evidence_label, number_key, parse_label, summary_out
from lawca.api.app import app, get_extractor_factory
from lawca.extraction.schema import DocumentType, Party, TextField
from tests.fakes import FakeChatModel
from tests.test_agent import use_models
from tests.test_api import FakeExtractor, chat, client, new_conversation  # noqa: F401 (픽스처 재사용)
from tests.test_extraction import correction_order, ev

ANSWER_PDF = Path(__file__).parent / "fixtures" / "synthetic_answer.pdf"
PARTIES = [Party(role="원고", name="홍길동", evidence=ev("원 고 홍길동")), Party(role="피고", name="김철수", evidence=ev("피 고 김철수"))]

SUMMARY = BriefSummary(
    submitter="피고",
    request_summary="원고의 청구를 기각한다는 판결을 구함",
    claims=[
        Claim(point="돈을 받은 사실은 인정", detail="3,000만 원 수령 인정", quote="피고가 원고로부터 3,000만 원을 받은 사실은 인정합니다", page=1),
        Claim(point="대여가 아니라 증여", detail="사업을 돕기 위한 증여", quote="빌려준 것이 아닙니다", page=1),
        Claim(point="소멸시효 완성", detail="청구권 소멸", quote="이미 소멸시효가 완성되었습니다", page=1),  # 실제로는 2쪽
        Claim(point="지어낸 주장", detail="원문에 없음", quote="원고는 사기꾼입니다", page=2),
    ],
    evidence=[
        CitedEvidence(label="을 제1호증", title="카카오톡 대화 내역"),
        CitedEvidence(label="을제2호증의1", title="사업자등록증명"),
        CitedEvidence(label="을 제1호증", title="중복"),
        CitedEvidence(label="증인 박가상", title="증인"),
    ],
)


def docx_text(data: bytes) -> str:
    return "\n".join(p.text for p in Document(io.BytesIO(data)).paragraphs)


# 순수 함수


def test_evidence_labels():
    assert parse_label("을 제2호증의1") == ("을", "2-1")
    assert parse_label("갑제10호증") == ("갑", "10")
    assert parse_label("증인 홍길동") is None
    assert evidence_label("을", "2-1") == "을 제2호증의1"
    assert sorted(["10", "2-1", "2", "1"], key=number_key) == ["1", "2", "2-1", "10"]


def test_summary_out_verifies_quotes_and_normalizes_evidence():
    pages = ["피고가 원고로부터 3,000만 원을 받은 사실은 인정합니다. 빌려준 것이 아닙니다.", "청구권은 이미 소멸시효가\n완성되었습니다."]
    out = summary_out(SUMMARY, pages)
    assert [(c["verified"], c["page"]) for c in out["claims"]] == [(True, 1), (True, 1), (True, 2), (False, 2)]
    assert [(e["label"], e["title"]) for e in out["evidence"]] == [("을 제1호증", "카카오톡 대화 내역"), ("을 제2호증의1", "사업자등록증명")]


# 채팅: 상대방 답변서 요약


@pytest.fixture
def answer_chat(client):  # noqa: F811
    doc = correction_order(
        document_type=DocumentType.ANSWER, document_type_evidence=ev("답 변 서"), parties=PARTIES, designated_period=None,
        issued_date=TextField(value="2026-09-20", evidence=ev("2026. 9. 20.")),
    )
    app.dependency_overrides[get_extractor_factory] = lambda: (lambda: FakeExtractor(doc))
    use_models(FakeChatModel(brief=SUMMARY))
    file_id = client.post("/api/files", files={"file": ("답변서.pdf", ANSWER_PDF.read_bytes(), "application/pdf")}).json()["id"]
    return chat(client, new_conversation(client), file_ids=[file_id])


def test_answer_is_summarized_in_chat(client, answer_chat):  # noqa: F811
    card = next(e["card"] for e in answer_chat if e["type"] == "card" and e["card"]["kind"] == "brief_summary")
    assert card["document_type"] == "답변서" and card["case_number"] == "2026가단51234"
    assert [c["verified"] for c in card["claims"]] == [True, True, True, False]
    assert card["claims"][2]["page"] == 2  # 모델이 1쪽이라고 했지만 원문은 2쪽
    text = "".join(e["delta"] for e in answer_chat if e["type"] == "text")
    assert "상대방 주장 4건, 증거 2건" in text and "찾지 못한 주장이 1건" in text
    # 요약은 문서에 저장되고, 답변서에는 기한 후보가 없어 대기 목록에 오르지 않는다
    detail = client.get("/api/cases/2026가단51234").json()
    assert detail["document_list"][0]["summary"]["submitter"] == "피고"
    assert client.get("/api/deadlines/pending").json()["documents"] == []


def test_scanned_answer_is_not_summarized(client):  # noqa: F811
    doc = correction_order(document_type=DocumentType.ANSWER, document_type_evidence=ev("답 변 서"), designated_period=None)
    app.dependency_overrides[get_extractor_factory] = lambda: (lambda: FakeExtractor(doc))
    use_models(FakeChatModel(brief=SUMMARY))
    file_id = client.post("/api/files", files={"file": ("scan.pdf", b"%PDF-1.4 fake", "application/pdf")}).json()["id"]
    events = chat(client, new_conversation(client), file_ids=[file_id])
    assert not any(e["type"] == "card" and e["card"]["kind"] == "brief_summary" for e in events)
    assert "스캔본이라" in "".join(e["delta"] for e in events if e["type"] == "text")


# 증거 목록


def test_evidence_from_summary_and_crud(client, answer_chat):  # noqa: F811
    card = next(e["card"] for e in answer_chat if e["type"] == "card" and e["card"]["kind"] == "brief_summary")
    url = "/api/cases/2026가단51234/evidence"
    body = {"source_document_id": card["document_id"], "items": card["evidence"]}
    assert client.post(f"{url}/bulk", json=body).json() == {"added": ["을 제1호증", "을 제2호증의1"], "skipped": []}
    assert client.post(f"{url}/bulk", json=body).json()["skipped"] == ["을 제1호증", "을 제2호증의1"]

    first = client.post(url, json={"side": "갑", "title": "차용증 사본"}).json()
    second = client.post(url, json={"side": "갑", "title": "계좌이체 내역", "number": "2의1"}).json()
    assert (first["label"], second["label"]) == ("갑 제1호증", "갑 제2호증의1")
    assert client.post(url, json={"side": "갑", "title": "x", "number": "1"}).status_code == 409
    assert client.post(url, json={"side": "갑", "title": "x", "number": "하나"}).status_code == 422
    assert client.post(url, json={"side": "갑", "title": "문자"}).json()["label"] == "갑 제3호증"

    patched = client.patch(f"/api/evidence/{first['id']}", json={"submitted_on": "2026-09-28", "note": "원본 보관"}).json()
    assert patched["submitted_on"] == "2026-09-28" and patched["note"] == "원본 보관"
    assert client.patch(f"/api/evidence/{first['id']}", json={"clear_submitted": True}).json()["submitted_on"] is None
    assert client.delete(f"/api/evidence/{second['id']}").status_code == 204

    detail = client.get("/api/cases/2026가단51234").json()
    assert [e["label"] for e in detail["evidence_list"]] == ["갑 제1호증", "갑 제3호증", "을 제1호증", "을 제2호증의1"]
    assert detail["evidence_list"][2]["from_summary"] is True
    assert client.get("/api/cases", params={"q": "홍길동"}).json()[0]["evidence"] == 4


# 준비서면 틀


def test_brief_frame(client, answer_chat):  # noqa: F811
    url = "/api/cases/2026가단51234"
    a1 = client.post(f"{url}/evidence", json={"side": "갑", "title": "차용증 사본"}).json()
    a2 = client.post(f"{url}/evidence", json={"side": "갑", "title": "계좌이체 내역"}).json()
    b1 = client.post(f"{url}/evidence", json={"side": "을", "title": "대화 내역"}).json()

    assert client.post(f"{url}/brief", json={"side": "원고", "evidence_ids": [b1["id"]]}).status_code == 422
    res = client.post(
        f"{url}/brief",
        json={"side": "원고", "title": "준비서면", "agent": "법무법인 가상 담당변호사 김가상",
              "body": "1. 피고 주장의 요지\n피고는 증여라고 주장합니다.\n\n2. 반박\n차용증과 계좌이체 내역이 있습니다.",
              "evidence_ids": [a2["id"], a1["id"]], "attachments": ["소송위임장 1통"], "filed_on": "2026-09-28"},
    )
    assert res.status_code == 200
    body = res.json()
    assert body["evidence"] == ["갑 제1호증", "갑 제2호증"] and body["body_empty"] is False
    text = docx_text(client.get(f"/api/files/{body['file_id']}/content").content)
    assert "준 비 서 면" in text and "사        건    2026가단51234  대여금" in text
    assert "위 사건에 관하여 원고 소송대리인은 다음과 같이 변론을 준비합니다." in text
    assert "1. 피고 주장의 요지\n피고는 증여라고 주장합니다." in text
    assert "1. 갑 제1호증    차용증 사본" in text and "1. 갑 제2호증    계좌이체 내역" in text
    assert "1. 위 입증방법    각 1통" in text and "1. 소송위임장 1통" in text
    assert "2026. 9. 28." in text and "법무법인 가상 담당변호사 김가상" in text

    # 본문을 비우면 자리표시, 대리인이 없으면 당사자 이름
    empty = client.post(f"{url}/brief", json={"side": "원고"}).json()
    text = docx_text(client.get(f"/api/files/{empty['file_id']}/content").content)
    assert empty["body_empty"] is True and "[본문: 담당 변호사 작성]" in text and "원고   홍길동" in text
    assert "위 사건에 관하여 원고는 다음과 같이" in text
    assert "입  증  방  법" not in text
    # 자료실에는 사건의 가장 최근 준비서면 초안 하나만
    assert len([d for d in client.get("/api/library", params={"kind": "draft"}).json() if d["title"].startswith("준비서면")]) == 1
    assert client.get(f"{url}").json()["facts"]["our_side"] == "원고"
