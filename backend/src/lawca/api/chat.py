"""채팅 응답 생성.

지금은 규칙으로만 요청을 나눈다. PDF가 첨부되면 문서 처리, 글만 있으면 안내한다.
LangGraph 라우터와 에이전트는 다음 단계에서 이 자리에 붙는다.

응답은 이벤트 목록으로 흘려보낸다(SSE의 data 한 줄이 이벤트 하나).
- status: 진행 단계. {"type": "status", "id", "label", "state": "running" | "done" | "error"}
- text: 응답 글의 조각. {"type": "text", "delta"}
- card: 결과 카드. {"type": "card", "card": {"kind": "document", ...}}
- done: 응답 끝
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from dataclasses import asdict
from datetime import date
from typing import Any

from fastapi import HTTPException

from lawca.api.schemas import ChatRequest, DocumentOut, IssueOut, PeriodOut, SuggestionOut
from lawca.db.models import File
from lawca.extraction.gemini import ExtractionError, Extractor, ModelUnavailableError
from lawca.extraction.validate import has_text, pdf_text_pages, validate
from lawca.workflow import checklist, suggest_deadlines

Event = dict[str, Any]

TEXT_ONLY_REPLY = (
    "지금은 **법원 문서 PDF**를 첨부하면 내용을 읽고, 송달일을 받아 기한을 계산하고, 할 일을 정리해 드립니다.\n\n"
    "서식 작성, 기한 조회, 선례 검색 같은 글로 하는 요청은 준비 중입니다. "
    "보정명령, 판결문, 결정문, 지급명령 같은 문서를 끌어다 놓거나 📎 버튼으로 첨부해 주세요."
)


def analyze(stored: File, extractor: Extractor, today: date) -> DocumentOut:
    doc = extractor.extract(stored.data)
    pages = pdf_text_pages(stored.data)
    return DocumentOut(
        file_id=str(stored.id),
        filename=stored.name,
        model=extractor.model,
        text_available=has_text(pages),
        extraction=doc,
        issues=[IssueOut(**asdict(i)) for i in validate(doc, pages, today)],
        suggestions=[
            SuggestionOut(
                kind=s.kind,
                label=s.label,
                rule_id=s.rule_id,
                period=PeriodOut.of(s.period) if s.period else None,
                note=s.note,
            )
            for s in suggest_deadlines(doc)
        ],
        checklist=checklist(doc),
    )


def summarize(result: DocumentOut) -> str:
    doc = result.extraction
    lines = [f"**{result.filename}**: **{doc.document_type.value}**(으)로 읽었습니다."]
    where = " ".join(v.value for v in (doc.court, doc.case_number, doc.case_name) if v is not None)
    if where:
        lines.append(f"사건: {where}")
    errors = sum(1 for i in result.issues if i.level == "error")
    if result.issues:
        lines.append(f"확인이 필요한 항목이 {len(result.issues)}건 있습니다(오류 {errors}건 포함).")
    if result.suggestions:
        lines.append("송달일은 문서에 적혀 있지 않습니다. 아래에 송달일을 입력하면 기한을 계산합니다.")
    return "\n\n".join(lines)


def chat_events(
    req: ChatRequest,
    get_file: Callable[[str], File | None],
    make_extractor: Callable[[], Extractor],
    today: date,
    on_document: Callable[[DocumentOut], str | None] = lambda _: None,
) -> Iterator[Event]:
    """on_document는 문서 처리 결과가 나올 때마다 불리고, 저장한 문서의 id를 돌려준다."""
    attachments = [get_file(file_id) for file_id in req.file_ids]
    if any(a is None for a in attachments):
        yield {"type": "text", "delta": "첨부 파일을 찾지 못했습니다. 서버가 다시 시작되었다면 파일을 다시 올려 주세요."}
        yield {"type": "done"}
        return

    if not attachments:
        yield {"type": "text", "delta": TEXT_ONLY_REPLY}
        yield {"type": "done"}
        return

    try:
        extractor = make_extractor()
    except HTTPException as exc:
        yield {"type": "text", "delta": f"문서를 읽을 수 없습니다. {exc.detail}"}
        yield {"type": "done"}
        return

    for index, stored in enumerate(a for a in attachments if a is not None):
        step = f"read-{stored.id}"
        yield {"type": "status", "id": step, "label": f"{stored.name} 읽는 중", "state": "running"}
        prefix = "\n\n" if index else ""
        try:
            result = analyze(stored, extractor, today)
        except (ModelUnavailableError, ExtractionError) as exc:
            yield {"type": "status", "id": step, "label": f"{stored.name} 읽기 실패", "state": "error"}
            yield {"type": "text", "delta": f"{prefix}**{stored.name}**을(를) 읽지 못했습니다. {exc}"}
            continue
        document_id = on_document(result)
        if document_id:
            result = result.model_copy(update={"document_id": document_id})
        yield {"type": "status", "id": step, "label": f"{stored.name} 읽음 · {result.model}", "state": "done"}
        yield {"type": "text", "delta": prefix + summarize(result)}
        yield {"type": "card", "card": {"kind": "document", **result.model_dump(mode="json")}}
    yield {"type": "done"}
