from __future__ import annotations

from tests.test_brief import answer_chat  # noqa: F401
from tests.test_brief_flow import CASE, lawyer, make_brief_draft  # noqa: F401
from tests.test_api import client  # noqa: F401
from tests.test_library import docx_bytes


def check(client, draft_id):
    response = client.get(f"/api/drafts/{draft_id}/check")
    assert response.status_code == 200
    return response.json()


def texts(result, level):
    return [i["text"] for i in result["items"] if i["level"] == level]


def test_fresh_brief_draft_reports_placeholders_review_and_evidence(client, lawyer):  # noqa: F811
    card = make_brief_draft(client)
    result = check(client, card["draft_id"])
    assert result["checked"] == "초안" and result["ready"] is False
    assert any("[인용 확인 필요]" in t for t in texts(result, "error"))  # 판례 자리가 남아 있다
    assert any("검토 전" in t for t in texts(result, "warn"))
    assert not any("증거 목록에 없는" in t for t in texts(result, "error"))  # 갑 제1호증은 목록에 있다


def test_review_and_final_upload_change_the_result(client, lawyer):  # noqa: F811
    card = make_brief_draft(client)
    client.post(f"/api/drafts/{card['draft_id']}/review")
    assert any("검토를 마쳤습니다" in i["text"] for i in check(client, card["draft_id"])["items"] if i["level"] == "ok")

    final = docx_bytes("준비서면\n사건 2026가단51234 서울중앙지방법원 원고 홍길동 피고 김철수\n본문입니다. 갑 제9호증에 따르면\n입증방법\n갑 제1호증")
    upload = client.post(
        f"/api/drafts/{card['draft_id']}/final",
        files={"file": ("최종.docx", final, "application/vnd.openxmlformats-officedocument.wordprocessingml.document")},
    )
    assert upload.status_code == 200
    result = check(client, card["draft_id"])
    assert result["checked"] == "올린 최종본"
    assert not any("[인용 확인 필요]" in t for t in texts(result, "error"))  # 최종본에서는 해결됨
    assert any("갑 제9호증" in t for t in texts(result, "error"))  # 증거 목록에 없는 증거를 인용
    assert any("입증방법에 없는" in t and "갑 제9호증" in t for t in texts(result, "warn"))
