"""준비서면 작성 흐름(서식 'brief'). 채팅에서 사건과 필요한 정보를 받은 뒤 초안을 만든다.

    prepare  (코드) 사건 기록·상대방 서면 요약·증거 목록·변호사 메모를 모으고, 메모마다 자료실에서 비슷한 과거 문단을 찾는다(RAG)
    write    (LLM 1회) 메모를 참고 문단의 논리·표현으로 풀어 본문을 쓴다(brief_writer, 변호사만)
    finish   (코드) 점검하고, 본문에서 인용한 증거로 입증방법을 채워 DOCX 틀에 넣고, 초안과 참고한 문서를 저장한다

본문 초안은 변호사만 받는다. 사무원은 사건 정보·서명란만 채운 틀(본문은 빈칸)을 받는다.
모델이 실패해도 틀은 만든다(본문만 빈칸).
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import Any

from sqlalchemy.orm import Session

from lawca.agent.brief import evidence_label, number_key, parse_label
from lawca.agent.brief_writer import (
    EVIDENCE,
    PLACEHOLDER,
    BriefDraft,
    BriefInputs,
    Reference,
    body_text,
    check,
    note_lines,
    write_brief,
)
from lawca.agent.llm import ChatModel
from lawca.db import repo
from lawca.db.models import Case, Document, Draft, EvidenceItem, File
from lawca.forms import LATER, load_forms, render_brief
from lawca.library import DOCX_MIME, Embedder
from lawca.library import store as library_store

REFERENCE_CHARS = 700


@dataclass
class Prepared:
    case: Case | None
    values: dict[str, str]
    inputs: BriefInputs
    opponent: Document | None
    hits: list[tuple[int, Any]]
    """(메모 번호, 자료실 검색 결과)"""


def paragraphs(body: str) -> list[str]:
    """빈 줄로 문단을 나눈다. 한 문단 안의 줄바꿈은 그대로 둔다."""
    return [p.strip() for p in re.split(r"\n\s*\n", body.strip()) if p.strip()]


def _opponent(case: Case | None) -> Document | None:
    if case is None:
        return None
    with_summary = sorted((d for d in case.documents if d.summary), key=lambda d: d.created_at, reverse=True)
    return with_summary[0] if with_summary else None


def prepare(session: Session, case: Case | None, values: dict[str, str], embedder: Embedder | None) -> Prepared:
    side = values.get("our_side", "원고")
    ours = "갑" if side == "원고" else "을"
    notes = note_lines(values.get("notes", ""))
    opponent = _opponent(case)
    claims = [f"{c['point']}: {c['detail']}" for c in (opponent.summary or {}).get("claims", [])] if opponent else []
    evidence = (
        [
            f"{evidence_label(e.side, e.number)} {e.title}" + (f"({e.note})" if e.note else "")
            for e in repo.list_evidence(session, case)
            if e.side == ours
        ]
        if case
        else []
    )
    record = (
        f"사건 {values.get('case_number', '')} {values.get('case_name', '')} / {values.get('court', '')}"
        f" / 원고 {values.get('plaintiffs', '')} / 피고 {values.get('defendants', '')} / 우리는 {side} 측"
    )
    hits = library_store.paragraph_references(session, notes, embedder)
    references = tuple(
        Reference(
            number=i,
            doc_id=str(hit.doc.id),
            title=hit.doc.title,
            text=hit.chunk.text[:REFERENCE_CHARS],
            note_no=note_no,
        )
        for i, (note_no, hit) in enumerate(hits, start=1)
    )
    inputs = BriefInputs(record=record, opponent=claims, evidence=evidence, notes=values.get("notes", ""), references=references)
    return Prepared(case=case, values=values, inputs=inputs, opponent=opponent, hits=hits)


def write(models: list[ChatModel], prepared: Prepared) -> BriefDraft:
    return write_brief(models, prepared.inputs)


def _cited_evidence(session: Session, case: Case | None, side: str, body: str) -> list[EvidenceItem]:
    """본문에서 인용한 우리 측 증거(증거 목록에 있는 것). 입증방법에 넣는다."""
    if case is None:
        return []
    ours = "갑" if side == "원고" else "을"
    listed = {(e.side, e.number): e for e in repo.list_evidence(session, case) if e.side == ours}
    cited: dict[tuple[str, str], EvidenceItem] = {}
    for match in EVIDENCE.finditer(body):
        key = parse_label(match.group(0))
        if key in listed:
            cited[key] = listed[key]
    return sorted(cited.values(), key=lambda e: number_key(e.number))


def render_docx(session: Session, case: Case | None, values: dict[str, str], sections: list[dict[str, Any]]) -> tuple[bytes, list[EvidenceItem]]:
    """준비서면 틀(DOCX)을 만든다. 입증방법은 본문에서 인용한 증거로 채운다."""
    side = values.get("our_side", "원고")
    body = body_text(BriefDraft.model_validate({"sections": sections, "open_points": []}))
    evidence = _cited_evidence(session, case, side, body)
    agent = (values.get("agent") or "").strip()
    party = values.get("plaintiffs" if side == "원고" else "defendants", "")
    data = render_brief(
        {
            "title": values.get("title", "준비서면").strip(),
            "side": side,
            "representative": "소송대리인" if agent else "",
            "signer": agent or party,
            "body": paragraphs(body),
            "evidence": [{"label": evidence_label(e.side, e.number), "title": e.title} for e in evidence],
            "attachments": ["위 입증방법    각 1통"] if evidence else [],
            "court": values.get("court", ""),
            "case_number": values.get("case_number", ""),
            "case_name": values.get("case_name", ""),
            "plaintiffs": values.get("plaintiffs", ""),
            "defendants": values.get("defendants", ""),
            "filed_on": values.get("filed_on"),
        }
    )
    return data, evidence


def _reference_items(prepared: Prepared, used: list[int]) -> list[dict[str, Any]]:
    """참고한 문서 목록(채팅·초안 화면). used는 본문이 실제로 인용한 참고 번호."""
    items = []
    for i, (note_no, hit) in enumerate(prepared.hits, start=1):
        item = library_store.hit_item(hit, library_store.terms_of(prepared.inputs.notes))
        items.append({**item, "number": i, "note_no": note_no, "used": i in used})
    return items


def finish(
    session: Session,
    prepared: Prepared,
    draft: BriefDraft | None,
    *,
    model: str | None,
    note: str | None,
    job_id: Any,
) -> dict[str, Any]:
    """초안을 저장하고 채팅 카드를 돌려준다. draft가 None이면 본문이 빈 틀이다(note가 이유)."""
    values = prepared.values
    case = prepared.case
    sections = [s.model_dump() for s in draft.sections] if draft else []
    checks = check(prepared.inputs, draft) if draft else None
    used = checks["references_used"] if checks else []
    data, evidence = render_docx(session, case, values, sections)

    title = values.get("title", "준비서면").strip()
    filename = f"{title}_{values.get('case_number') or '사건번호없음'}_초안.docx"
    file = repo.save_file(session, filename, data, mime=DOCX_MIME)
    references = _reference_items(prepared, used)
    payload = {
        "sections": sections,
        "open_points": draft.open_points if draft else [],
        "citation_needs": [n.model_dump() for n in draft.citation_needs] if draft else [],
        "checks": checks,
        "references": references,
        "opponent_document": f"{prepared.opponent.document_type} ({prepared.opponent.file.name})" if prepared.opponent else None,
        "model": model,
        "note": note,
        "filled": {},
        "evidence": [evidence_label(e.side, e.number) for e in evidence],
    }
    saved = repo.save_draft(
        session,
        case=case,
        form_id="brief",
        file=file,
        values={**values, "_brief": payload},
        blanks=[] if draft else ["본문"],
        job_id=job_id,
    )
    form = load_forms()["brief"]
    if case is not None:
        repo.remember_facts(
            session, case, {f.key: values[f.key] for f in form.fields if f.remember and values.get(f.key) not in (None, "", LATER)}
        )
    session.commit()
    return {
        "kind": "draft",
        "draft_id": str(saved.id),
        "form_id": "brief",
        "form_name": "준비서면",
        "file_id": str(file.id),
        "filename": filename,
        "size": file.size,
        "case_number": values.get("case_number"),
        "fields": [
            {"label": "우리 측", "value": values.get("our_side", "")},
            {"label": "서면 제목", "value": title},
            *([{"label": "소송대리인", "value": values["agent"]}] if values.get("agent") else []),
            *([{"label": "반박 대상", "value": payload["opponent_document"]}] if payload["opponent_document"] else []),
            *([{"label": "입증방법", "value": ", ".join(payload["evidence"])}] if payload["evidence"] else []),
        ],
        "blanks": saved.blanks,
        "brief": payload,
        "references": references,
    }


def reference_list_text(references: list[dict[str, Any]]) -> str:
    """채팅 답변에 붙이는 '참고한 문서' 목록(마크다운)."""
    if not references:
        return ""
    lines = ["", "", "**참고한 문서** (자료실)", ""]
    for ref in references:
        where = f"메모{ref['note_no']}에 대응" if ref.get("note_no") else "문체 참고"
        used = "본문에 반영" if ref.get("used") else "검색됨(본문에는 인용하지 않음)"
        label = f"[{ref['title']}](/api/files/{ref['file_id']}/content)"
        status = f" · {ref['status_label']}" if ref.get("status_label") else ""
        lines.append(f"{ref['number']}. {label} — {ref['kind_label']}{status}, {where}, {used}")
    return "\n".join(lines)


def apply_citation(session: Session, draft: Draft, index: int, citation: str) -> dict[str, Any]:
    """[인용 확인 필요] n번째 자리에 판례 인용을 넣고 DOCX를 다시 만든다(같은 파일을 갱신)."""
    values = dict(draft.values)
    payload = dict(values["_brief"])
    needs = payload.get("citation_needs", [])
    filled = {int(k): v for k, v in payload.get("filled", {}).items()}
    if not 0 <= index < len(needs):
        raise ValueError("없는 인용 자리입니다.")
    if index in filled:
        raise ValueError("이미 인용을 넣은 자리입니다.")
    # 아직 채우지 않은 자리 중 앞에서 몇 번째인지
    slot = sum(1 for i in range(index) if i not in filled)
    remaining = slot
    done = False
    sections = []
    for section in payload["sections"]:
        paras = []
        for para in section["paragraphs"]:
            text = para["text"]
            while not done and PLACEHOLDER in text:
                if remaining == 0:
                    text = text.replace(PLACEHOLDER, citation, 1)
                    done = True
                    break
                remaining -= 1
                text = text.replace(PLACEHOLDER, "\0", 1)  # 건너뛴 자리를 잠시 가려 다음 자리를 찾는다
            paras.append({**para, "text": text.replace("\0", PLACEHOLDER)})
        sections.append({**section, "paragraphs": paras})
    if not done:
        raise ValueError("본문에 [인용 확인 필요] 자리가 없습니다.")
    case = draft.case
    clean_values = {k: v for k, v in values.items() if k != "_brief"}
    data, evidence = render_docx(session, case, clean_values, sections)
    file = session.get(File, draft.file_id)
    assert file is not None
    file.data, file.size, file.sha256 = data, len(data), hashlib.sha256(data).hexdigest()
    payload["sections"] = sections
    payload["filled"] = {**payload.get("filled", {}), str(index): citation}
    payload["evidence"] = [evidence_label(e.side, e.number) for e in evidence]
    payload["citation_needs"] = needs
    draft.values = {**values, "_brief": payload}
    repo.audit(session, "draft.citation", "draft", draft.id, {"index": index, "citation": citation})
    session.flush()
    return payload


def check_summary(checks: dict[str, Any] | None) -> str:
    """점검에서 걸린 것을 한 줄로. 채팅 답변에 쓴다."""
    if not checks:
        return ""
    parts = []
    if checks["unused_notes"]:
        parts.append(f"반영되지 않은 메모 {len(checks['unused_notes'])}줄")
    if checks["placeholders"]:
        parts.append(f"[인용 확인 필요] {checks['placeholders']}곳")
    for key, label in (
        ("case_law", "판례 인용"),
        ("statutes_not_in_notes", "메모에 없는 법조문"),
        ("unknown_evidence", "증거 목록에 없는 증거"),
        ("amounts_not_in_inputs", "자료에 없는 금액"),
        ("dates_not_in_inputs", "자료에 없는 날짜"),
        ("foreign_names", "참고 문서에서 온 이름"),
    ):
        if checks[key]:
            parts.append(f"{label} {len(checks[key])}건")
    if checks["paragraphs_without_source"]:
        parts.append(f"근거 없는 문단 {checks['paragraphs_without_source']}개")
    return ", ".join(parts)
