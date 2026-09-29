"""제출 전 점검. 초안(또는 올린 최종본)을 코드로 살펴 제출 전에 고칠 것을 알려 준다. AI를 쓰지 않는다.

점검하는 것
    - 빈칸이 남았는가: 밑줄 빈칸, 날짜 빈칸, "[본문: 담당 변호사 작성]", "[인용 확인 필요]"
    - 사건 정보가 문서에 있는가: 사건번호, 법원, 당사자(사건 기록과 대조)
    - 변호사 검토를 받았는가
    - 이 사건의 진행 중 기한이 지났거나 임박했는가
    - 준비서면: 본문에서 인용한 증거가 증거 목록에 있는지, 입증방법에 빠지지 않았는지
"""

from __future__ import annotations

import re
from datetime import date
from typing import Any

from sqlalchemy.orm import Session

from lawca.agent.brief import evidence_label, parse_label
from lawca.agent.brief_writer import EVIDENCE, PLACEHOLDER
from lawca.db import repo
from lawca.db.models import Draft
from lawca.forms import BRIEF_BODY_PLACEHOLDER
from lawca.library import extract_pages

SOON_DAYS = 7
BLANK_LINE = re.compile(r"_{4,}")
BLANK_DATE = re.compile(r"20\s{2,}\.\s{2,}\.\s{2,}\.")


def _item(level: str, text: str) -> dict[str, str]:
    """level: error(제출 전에 반드시 고칠 것) | warn(확인할 것) | ok | info"""
    return {"level": level, "text": text}


def _compact(text: str) -> str:
    return re.sub(r"\s+", "", text)


def _text_of(draft: Draft) -> tuple[str | None, str]:
    """점검할 문서의 글. 올린 최종본이 있으면 그것을 본다. (글, 무엇을 봤는지)"""
    file = draft.final_file or draft.file
    which = "올린 최종본" if draft.final_file else "초안"
    try:
        data = repo.get_file(_session_of(draft), str(file.id)).data
        return "\n".join(extract_pages(data, file.mime)), which
    except Exception:  # noqa: BLE001 - 읽지 못하면(형식·손상) 글 점검만 건너뛴다
        return None, which


def _session_of(draft: Draft) -> Session:
    from sqlalchemy.orm import object_session

    session = object_session(draft)
    assert session is not None
    return session


def _cited(text: str) -> set[tuple[str, str]]:
    return {key for m in EVIDENCE.finditer(text) if (key := parse_label(m.group(0)))}


def check_draft(session: Session, draft: Draft, today: date) -> dict[str, Any]:
    """{'items': [{level, text}], 'ready': 고칠 것이 없는가, 'checked': 무엇을 봤는지}"""
    items: list[dict[str, str]] = []
    values = draft.values or {}
    case = draft.case
    text, which = _text_of(draft)

    if text is None:
        items.append(_item("warn", f"{which} 파일을 읽지 못해 글 내용 점검은 건너뛰었습니다."))
    else:
        # 1. 빈칸
        blanks = len(BLANK_LINE.findall(text)) + len(BLANK_DATE.findall(text))
        if blanks:
            items.append(_item("error", f"채우지 않은 빈칸이 {blanks}곳 있습니다."))
        if BRIEF_BODY_PLACEHOLDER in text:
            items.append(_item("error", "본문이 비어 있습니다(\"[본문: 담당 변호사 작성]\" 자리)."))
        holes = text.count(PLACEHOLDER)
        if holes:
            items.append(_item("error", f"\"{PLACEHOLDER}\"가 {holes}곳 남아 있습니다. 판례·조문을 넣거나 문장을 고치세요."))

        # 2. 사건 정보
        compact = _compact(text)
        wanted = [
            ("사건번호", values.get("case_number")),
            ("법원", values.get("court")),
            ("원고", values.get("plaintiffs")),
            ("피고", values.get("defendants")),
        ]
        missing = [f"{label}({value})" for label, value in wanted if value and _compact(str(value)) not in compact]
        if missing:
            items.append(_item("warn", f"사건 기록의 {', '.join(missing)}이(가) 문서에서 보이지 않습니다. 잘못 고쳤거나 빠졌는지 확인하세요."))

    # 3. 준비서면 증거
    if draft.form_id == "brief" and case is not None and text is not None:
        body, _, listed_part = text.partition("입증방법")
        body_cited = _cited(body)
        known = {(e.side, e.number) for e in repo.list_evidence(session, case)}
        unknown = sorted(body_cited - known)
        if unknown:
            items.append(_item("error", "증거 목록에 없는 증거를 인용했습니다: " + ", ".join(evidence_label(*k) for k in unknown)))
        left_out = sorted(body_cited - _cited(listed_part)) if listed_part else sorted(body_cited)
        if left_out:
            items.append(_item("warn", "본문에서 인용했지만 입증방법에 없는 증거: " + ", ".join(evidence_label(*k) for k in left_out)))
        if not body_cited:
            items.append(_item("info", "본문에서 인용한 증거가 없습니다."))

    # 4. 검토
    if draft.final_file_id:
        items.append(_item("ok", "최종본이 올라와 있습니다."))
    elif draft.reviewed_by:
        items.append(_item("ok", f"{draft.reviewed_by} 변호사가 검토를 마쳤습니다."))
    else:
        items.append(_item("warn", "아직 변호사 검토 전입니다."))

    # 5. 기한
    if case is not None:
        for d in repo.list_deadlines(session, ["confirmed"], case_number=case.case_number):
            left = (d.deadline - today).days
            when = f"{d.label} {d.deadline.isoformat()}"
            if left < 0:
                items.append(_item("error", f"진행 중인 기한이 {-left}일 지났습니다: {when}"))
            elif left <= SOON_DAYS:
                items.append(_item("warn", f"기한이 {left}일 남았습니다: {when}"))
            else:
                items.append(_item("info", f"기한: {when} (D-{left})"))

    ready = not any(i["level"] == "error" for i in items)
    return {"items": items, "ready": ready, "checked": which}
