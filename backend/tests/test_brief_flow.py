"""준비서면 작성 흐름(채팅): 사건 묻기 → 정보 되묻기 → 자료실 참고 문단(RAG)으로 본문 초안 → 저장·참고한 문서 목록."""

import pytest

from lawca.agent.brief_writer import BriefDraft, BriefInputs, CitationNeed, Paragraph, Reference, Section, check
from lawca.extraction.gemini import ModelUnavailableError
from lawca.library.store import paragraph_references
from tests.conftest import login, make_user
from tests.fakes import FakeChatModel
from tests.test_agent import use_models
from tests.test_api import chat, client, events_of  # noqa: F401 (픽스처 재사용)
from tests.test_brief import answer_chat, docx_text  # noqa: F401
from tests.test_library import docx_bytes, upload_doc
from tests.test_api import new_conversation

CASE = "2026가단51234"
PAST = """준 비 서 면
1. 증여 주장에 대한 반박
원고 박가상은 피고 이가나에게 9,999만 원을 이체하면서 받는 통장 표시를 '대여금'으로 적었습니다. 증여라면 그렇게 적을 이유가 없습니다.
2. 소멸시효
피고 이가나는 2021. 5. 3. 문자로 변제를 약속하여 채무를 승인하였으므로 소멸시효가 중단되었습니다. [인용 확인 필요]"""
NOTES = "- 증여 아님: 이체 때 받는 통장 표시를 '대여금'으로 적음(갑1)\n- 문자는 변제 약속 → 채무 승인\n- 소멸시효 중단"

DRAFT = BriefDraft(
    sections=[
        Section(
            heading="1. 피고 주장의 요지",
            paragraphs=[Paragraph(text="피고는 3,000만 원이 증여라고 주장합니다.", sources=["상대방 서면"])],
        ),
        Section(
            heading="2. 증여 주장에 대한 반박",
            paragraphs=[
                Paragraph(text="원고는 이체할 때 통장 표시를 '대여금'으로 적었습니다(갑 제1호증).", sources=["메모1", "참고1", "갑 제1호증"]),
                Paragraph(text="피고 이가나는 9,999만 원을 받았습니다.", sources=["메모2", "참고1"]),  # 참고 문서에서 베껴 온 이름·금액
                Paragraph(text="채무 승인으로 시효가 중단되었습니다. [인용 확인 필요] 이후 최고도 있었습니다. [인용 확인 필요]", sources=["메모3"]),
            ],
        ),
    ],
    open_points=["소멸시효 법리 보충"],
    citation_needs=[
        CitationNeed(issue="채무 승인에 의한 소멸시효 중단", keywords=["소멸시효", "채무승인"]),
        CitationNeed(issue="최고의 효력", keywords=["최고", "시효"]),
    ],
)


class BriefUnavailable(FakeChatModel):
    def structured(self, system, turns, schema):
        if schema.__name__ == "BriefDraft":
            raise ModelUnavailableError("본문 모델 혼잡")
        return super().structured(system, turns, schema)


def resume(client, job_id, answers):  # noqa: F811
    return client.post(f"/api/jobs/{job_id}/resume", json={"answers": answers})


def question_of(events):
    return next(e["card"] for e in events if e["type"] == "card" and e["card"]["kind"] == "question")


def draft_card(events):
    return next(e["card"] for e in events if e["type"] == "card" and e["card"]["kind"] == "draft")


def statuses(events):
    return [e["label"] for e in events if e["type"] == "status"]


def start(client, model, message="준비서면 작성해줘", case=None):  # noqa: F811
    use_models(model)
    return chat(client, new_conversation(client), message)


@pytest.fixture
def lawyer(client, answer_chat, db):  # noqa: F811
    """답변서 요약이 있는 사건(2026가단51234), 자료실의 예전 준비서면, 갑 제1호증을 준비하고 변호사로 로그인한다."""
    upload_doc(client, "예전_준비서면.docx", docx_bytes(PAST), title="예전 원고 준비서면")
    client.post(f"/api/cases/{CASE}/evidence", json={"side": "갑", "title": "계좌이체 내역", "note": "받는 통장 표시 '대여금'"})
    make_user(db, "lawyer1", "lawyer")
    login(client, "lawyer1")


def test_lawyer_asks_case_then_info_then_drafts_with_references(client, lawyer):  # noqa: F811
    model = FakeChatModel(tasks=[("draft", "준비서면 작성해줘")], form_request=("brief", None, {}), draft=DRAFT)
    events = start(client, model)
    q = question_of(events)
    assert q["stage"] == "case"  # 사건을 먼저 묻는다

    q = question_of(events_of(resume(client, q["job_id"], {"case": next(o for o in q["fields"][0]["options"] if o.startswith(CASE))})))
    assert q["stage"] == "fields" and [f["key"] for f in q["fields"]] == ["our_side", "notes"]  # 없는 정보만 되묻는다
    assert [r["title"] for r in q["references"]] == ["예전 원고 준비서면"]  # 묻는 동안 참고할 과거 서면도 보여 준다

    events = events_of(resume(client, q["job_id"], {"our_side": "원고", "notes": NOTES}))
    assert any("참고 문단 1건 찾음" in label for label in statuses(events))
    card = draft_card(events)
    assert card["form_id"] == "brief" and card["filename"] == f"준비서면_{CASE}_초안.docx"

    # 모델에는 메모에 대응한 참고 문단이 갔고, 이 사건 자료도 함께 갔다
    sent = model.seen_turns[-1][-1].text
    assert "[참고 문단 1 — 메모1에 대응" in sent and "받는 통장 표시를 '대여금'" in sent
    assert "우리는 원고 측" in sent and "갑 제1호증 계좌이체 내역" in sent and "대여가 아니라 증여" in sent

    # 채팅 답변: 참고한 문서 목록
    text = "".join(e["delta"] for e in events if e["type"] == "text")
    assert "**참고한 문서** (자료실)" in text and "예전 원고 준비서면" in text and "메모1에 대응" in text and "본문에 반영" in text
    assert "메모 3줄을 자료실의 참고 문단 1건과 함께" in text
    assert "참고 문서에서 온 이름" in text and "자료에 없는 금액" in text  # 베껴 온 이름·금액은 점검에 걸린다
    ref = card["references"][0]
    assert (ref["title"], ref["note_no"], ref["used"], ref["number"]) == ("예전 원고 준비서면", 1, True, 1)

    brief = card["brief"]
    assert brief["checks"]["foreign_names"] == ["이가나"] and brief["checks"]["amounts_not_in_inputs"] == ["9,999만 원"]
    assert brief["checks"]["references_used"] == [1] and [n["number"] for n in brief["checks"]["unused_notes"]] == []
    assert brief["opponent_document"].startswith("답변서") and brief["evidence"] == ["갑 제1호증"]

    # DOCX: 사건 정보·본문·인용한 증거로 채운 입증방법
    docx = docx_text(client.get(f"/api/files/{card['file_id']}/content").content)
    assert "준 비 서 면" in docx and f"사        건    {CASE}  대여금" in docx
    assert "위 사건에 관하여 원고는 다음과 같이 변론을 준비합니다." in docx
    assert "2. 증여 주장에 대한 반박" in docx and "1. 갑 제1호증    계좌이체 내역" in docx and "1. 위 입증방법    각 1통" in docx

    # 초안 탭 API
    [item] = [d for d in client.get("/api/drafts").json() if d["form_id"] == "brief"]
    assert (item["form_name"], item["case_number"], item["status_label"], item["created_by"]) == ("준비서면", CASE, "검토 전", "lawyer1")
    detail = client.get(f"/api/drafts/{item['id']}/detail").json()
    assert detail["brief"]["references"][0]["title"] == "예전 원고 준비서면" and detail["brief"]["citation_needs"][0]["issue"]
    assert client.get("/api/drafts", params={"case_number": CASE, "form_id": "address_correction"}).json() == []
    # 다음 서식에서 다시 묻지 않도록 우리 측을 기억한다
    assert client.get(f"/api/cases/{CASE}").json()["facts"]["our_side"] == "원고"


def test_clerk_gets_frame_without_body(client, answer_chat):  # noqa: F811
    model = FakeChatModel(tasks=[("draft", "준비서면 작성해줘")], form_request=("brief", CASE, {}))  # draft가 없어 본문 호출이 있으면 실패한다
    events = start(client, model)
    q = question_of(events)
    assert [f["key"] for f in q["fields"]] == ["our_side"]  # 사무원에게는 메모를 묻지 않는다
    events = events_of(resume(client, q["job_id"], {"our_side": "피고"}))
    card = draft_card(events)
    assert card["blanks"] == ["본문"] and card["brief"]["checks"] is None and card["references"] == []
    assert "변호사만 받을 수 있어" in "".join(e["delta"] for e in events if e["type"] == "text")
    docx = docx_text(client.get(f"/api/files/{card['file_id']}/content").content)
    assert "[본문: 담당 변호사 작성]" in docx and "위 사건에 관하여 피고는" in docx


def test_body_model_failure_still_makes_frame(client, lawyer):  # noqa: F811
    model = BriefUnavailable(tasks=[("draft", "준비서면")], form_request=("brief", CASE, {"our_side": "원고", "notes": NOTES}))
    events = start(client, model, "2026가단51234 준비서면 작성해줘")
    card = draft_card(events)
    assert card["blanks"] == ["본문"] and "본문 초안을 만들지 못해" in card["brief"]["note"]
    assert "본문 모델 혼잡" in "".join(e["delta"] for e in events if e["type"] == "text")


def test_values_in_request_skip_questions(client, lawyer):  # noqa: F811
    model = FakeChatModel(tasks=[("draft", "x")], form_request=("brief", CASE, {"our_side": "원고", "notes": NOTES}), draft=DRAFT)
    events = start(client, model, "2026가단51234 원고 준비서면, 메모는 ...")
    assert draft_card(events)["form_id"] == "brief"  # 물을 것이 없으면 바로 만든다


# 판례 인용 넣기


def make_brief_draft(client):  # noqa: F811
    model = FakeChatModel(tasks=[("draft", "x")], form_request=("brief", CASE, {"our_side": "원고", "notes": NOTES}), draft=DRAFT)
    return draft_card(start(client, model, "2026가단51234 준비서면"))


def test_insert_citation_into_nth_placeholder(client, lawyer, db):  # noqa: F811
    card = make_brief_draft(client)
    url = f"/api/drafts/{card['draft_id']}/citation"
    first = client.post(url, json={"index": 1, "citation": "(대법원 2020. 1. 9. 선고 2019다1 판결 참조)"})
    assert first.status_code == 200 and first.json()["brief"]["filled"] == {"1": "(대법원 2020. 1. 9. 선고 2019다1 판결 참조)"}
    # 1번 자리(두 번째 마커)에 들어갔고 첫 번째 마커는 그대로다
    text = docx_text(client.get(f"/api/files/{card['file_id']}/content").content)
    assert "시효가 중단되었습니다. [인용 확인 필요] 이후 최고도 있었습니다. (대법원 2020. 1. 9. 선고 2019다1 판결 참조)" in text
    assert client.post(url, json={"index": 1, "citation": "(다시)"}).status_code == 409  # 이미 넣은 자리
    assert client.post(url, json={"index": 5, "citation": "(없는 자리)"}).status_code == 409
    second = client.post(url, json={"index": 0, "citation": "(대법원 2021. 2. 3. 선고 2020다2 판결 참조)"})
    assert second.status_code == 200
    text = docx_text(client.get(f"/api/files/{card['file_id']}/content").content)
    assert "[인용 확인 필요]" not in text and "(대법원 2021. 2. 3. 선고 2020다2 판결 참조) 이후 최고도" in text

    make_user(db, "clerk9", "clerk")
    login(client, "clerk9")
    assert client.post(url, json={"index": 0, "citation": "(사무원)"}).status_code == 403


# 점검·검색 단위


def test_check_flags_foreign_names_and_reports_used_references():
    inputs = BriefInputs(
        record="사건 / 원고 홍길동 / 피고 김철수", opponent=[], evidence=[], notes="- 증여 아님",
        references=(Reference(1, "d1", "예전", "원고 박가상은 피고 이가나에게 빌려주었고 원고 주식회사 가온캐피탈이 청구합니다.", 1),
                    Reference(2, "d2", "다른", "원고 홍길동은", 1)),
    )
    draft = BriefDraft(
        sections=[Section(heading="h", paragraphs=[Paragraph(text="원고 박가상은 피고 김철수에게. 원고 주식회사 가온캐피탈이 홍길동", sources=["메모1", "참고1"])])],
        open_points=[],
    )
    result = check(inputs, draft)
    assert result["foreign_names"] == ["박가상", "주식회사 가온캐피탈"]  # 이 사건 자료에 있는 이름(홍길동)은 아니다
    assert result["references_used"] == [1]


def test_reference_search_skips_court_documents_and_unreviewed_drafts(client, answer_chat, db):  # noqa: F811
    upload_doc(client, "서면.docx", docx_bytes("준비서면\n대여금 반환 청구 증여 아님"), title="직접 올린 서면")
    with db() as session:
        hits = paragraph_references(session, ["대여금 반환 증여", "답변서 소멸시효 완성"], None)
        titles = [h.doc.title for _, h in hits]
    assert titles == ["직접 올린 서면"]  # 이 사건의 답변서(법원 문서)와 검토 전 초안은 참고로 쓰지 않는다


def test_reference_use_is_detected_by_code_and_particles_are_not_names():
    ref = Reference(1, "d1", "예전", "증여라면 이체 당시 그와 같이 표시할 이유가 없습니다. 피고에게 청구합니다.", 1)
    other = Reference(2, "d2", "다른", "임차인은 통상의 손모에 대하여 원상회복 의무를 지지 않습니다.", 1)
    inputs = BriefInputs(record="사건 / 원고 홍길동 / 피고 김철수", opponent=[], evidence=[], notes="- 증여 아님", references=(ref, other))
    draft = BriefDraft(
        sections=[Section(heading="h", paragraphs=[Paragraph(text="만약 증여라면 이체 당시 그와 같이 표시할 이유가 없습니다.", sources=["메모1"])])],
        open_points=[],
    )
    result = check(inputs, draft)
    assert result["references_used"] == [1]  # 모델이 참고1을 적지 않아도 문장을 살린 것을 코드가 찾는다
    assert result["foreign_names"] == []  # "피고에게"의 조사는 이름이 아니다


def test_too_short_memo_is_asked_again(client, lawyer):  # noqa: F811
    model = FakeChatModel(tasks=[("draft", "x")], form_request=("brief", CASE, {"our_side": "원고"}), draft=DRAFT)
    q = question_of(start(client, model))
    assert [f["key"] for f in q["fields"]] == ["notes"]
    again = question_of(events_of(resume(client, q["job_id"], {"notes": "그래서 그렇습니다"})))  # 뜻 없는 짧은 메모
    assert [f["key"] for f in again["fields"]] == ["notes"] and "너무 짧습니다" in again["errors"][0]
    assert again["question_id"] != q["question_id"]
    events = events_of(resume(client, again["job_id"], {"notes": NOTES}))
    assert draft_card(events)["form_id"] == "brief"


def test_reply_says_so_when_no_reference_was_found(client, answer_chat, db):  # noqa: F811
    make_user(db, "lawyer1", "lawyer")
    login(client, "lawyer1")  # 자료실에 참고할 과거 서면이 없다
    model = FakeChatModel(tasks=[("draft", "x")], form_request=("brief", CASE, {"our_side": "원고", "notes": NOTES}), draft=DRAFT)
    events = start(client, model, "2026가단51234 준비서면")
    text = "".join(e["delta"] for e in events if e["type"] == "text")
    assert "**참고 없이**" in text and "참고 문단 0건" not in text and "**참고한 문서**" not in text
    assert draft_card(events)["references"] == []


def test_question_lists_only_references_the_draft_would_use(client, lawyer):  # noqa: F811
    make_brief_draft(client)  # 검토 전 준비서면 초안이 자료실에 들어간다(참고로는 쓰지 않는다)
    model = FakeChatModel(tasks=[("draft", "x")], form_request=("brief", CASE, {"our_side": "원고"}), draft=DRAFT)
    q = question_of(start(client, model))
    assert [r["title"] for r in q["references"]] == ["예전 원고 준비서면"]  # 검토 전 초안은 빠진다
