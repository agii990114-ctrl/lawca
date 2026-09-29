"""서식 작성 에이전트.

    draft_parse (LLM 1회: 서식·사건·말한 값 찾기)
      → draft_ask (코드: 서식/사건이 없거나 필수 항목이 비면 interrupt로 묻는다. 답을 받으면 다시 확인)
      → draft_render (코드: DOCX 초안 저장, 입력값을 사건에 기억)

LangGraph는 재개할 때 멈춘 노드를 처음부터 다시 실행한다. 그래서 LLM을 부르는 노드와 묻는 노드를 나눴다.
draft_ask는 interrupt 앞에서 아무것도 흘려보내지 않는다(다시 실행돼도 중복이 없게).
"""

from __future__ import annotations

import re
from datetime import date
from typing import Any

from pydantic import BaseModel, Field as PydanticField
from sqlalchemy.orm import Session

from lawca.agent.llm import ChatModel, Turn, with_fallback
from lawca.db import repo
from lawca.db.models import Case
from lawca.extraction.gemini import ExtractionError, ModelUnavailableError
from lawca.forms import (
    LATER,
    Form,
    clean_answer,
    find_form,
    initial_values,
    later_fields,
    load_forms,
    missing_fields,
    question_fields,
    render,
)

DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
CASE_NUMBER_IN_TEXT = re.compile(r"\d{4}\s*[가-힣]{1,3}\s*\d{1,7}")
CASE_IDENTITY = {"court", "case_number", "case_name", "plaintiffs", "defendants"}
"""사건 자체를 나타내는 항목. LLM이 요청에서 해석하지 않고 사건 기록이나 사용자 입력으로만 채운다."""
NO_CASE = "사건 없이 직접 입력"


class FieldValue(BaseModel):
    key: str
    value: str


class FormRequest(BaseModel):
    form_id: str | None = PydanticField(description="서식 id. 목록에 없으면 null")
    case_number: str | None = PydanticField(description="사건번호. 요청이나 앞선 대화에 없으면 null")
    values: list[FieldValue] = PydanticField(description="요청에 명시된 항목 값만. 날짜는 YYYY-MM-DD")


def _prompt(today: date) -> str:
    lines = [
        "당신은 법무법인 사무원의 서식 작성 요청을 해석합니다. 오늘은 " + today.isoformat() + "입니다.",
        "요청과 앞선 대화에서 서식 id, 사건번호, 사용자가 명시한 항목 값만 찾습니다. 추측하지 않습니다.",
        "",
        "서식 목록",
    ]
    for form in load_forms().values():
        fields = ", ".join(f"{f.key}({f.label})" for f in form.fields if f.key not in CASE_IDENTITY)
        lines.append(f"- {form.id}: {form.name}. 항목: {fields}")
    return "\n".join(lines)


def _case_label(case: Case) -> str:
    parties = " · ".join(f"{p.role} {p.name}" for p in case.parties[:2])
    return f"{case.case_number} {case.case_name or ''} ({case.court or '법원 미상'}{', ' + parties if parties else ''})"


def prepare(draft: dict[str, Any], session: Session, today: date) -> dict[str, Any]:
    """서식과 사건이 정해졌으면 값을 채운다. 우선순위: 사용자가 폼에 입력한 값 > 사건 기록 > 요청에서 해석한 값 > 기억한 값·기본값.

    사건 기록에서 오는 항목(from_case)은 기록에 값이 있으면 요청 해석 값으로 덮지 않는다(DB가 기준).
    """
    forms = load_forms()
    form = forms.get(draft.get("form_id") or "")
    if form is None or not draft.get("case_resolved"):
        return draft
    case = repo.get_case(session, draft["case_number"]) if draft.get("case_number") else None
    values = initial_values(form, repo.case_values(case) if case else {}, case.facts if case else {}, today)
    for key, value in draft.get("parsed", {}).items():
        if not (form.field(key).from_case and values.get(key)):
            values[key] = value
    values.update(draft.get("provided", {}))
    return {**draft, "values": values}


def parse_request(models: list[ChatModel], request: str, history: list[Turn], session: Session, today: date) -> dict[str, Any]:
    forms = load_forms()
    try:
        parsed = with_fallback(models, lambda m: m.structured(_prompt(today), [*history, Turn("user", request)], FormRequest))
    except (ModelUnavailableError, ExtractionError):
        parsed = FormRequest(form_id=None, case_number=None, values=[])

    form = forms.get(parsed.form_id or "") or find_form(request)
    number = parsed.case_number
    if not number:
        found = CASE_NUMBER_IN_TEXT.search(request)
        number = found.group(0) if found else None
    case = repo.get_case(session, number) if number else None

    parsed_values: dict[str, str] = {}
    if form is not None:
        keys = {f.key for f in form.fields} - CASE_IDENTITY
        for item in parsed.values:
            if item.key in keys:
                cleaned, error = clean_answer(form.field(item.key), item.value)
                if cleaned and not error and cleaned != LATER:
                    parsed_values[item.key] = cleaned

    draft = {
        "form_id": form.id if form else None,
        "case_number": case.case_number if case else None,
        "case_resolved": case is not None,
        "parsed": parsed_values,
        "provided": {},
        "values": {},
        "errors": [],
    }
    return prepare(draft, session, today)


def build_question(draft: dict[str, Any], session: Session) -> dict[str, Any] | None:
    """물어볼 것이 없으면 None."""
    forms = load_forms()
    form = forms.get(draft.get("form_id") or "")
    if form is None:
        return {
            "stage": "form",
            "title": "어떤 서식을 만들까요?",
            "fields": [{"key": "form", "label": "서식", "type": "select", "options": [f.name for f in forms.values()],
                        "default": "", "allow_later": False, "help": None}],
            "errors": draft.get("errors", []),
        }
    if not draft.get("case_resolved"):
        cases = repo.recent_cases(session)
        choices = {_case_label(c): c.case_number for c in cases}
        return {
            "stage": "case",
            "title": f"{form.name}: 어느 사건인가요?",
            "fields": [{"key": "case", "label": "사건", "type": "select", "options": [*choices, NO_CASE],
                        "default": "", "allow_later": False,
                        "help": "목록에 없으면 '사건 없이 직접 입력'을 고르고 사건 정보를 직접 적습니다."}],
            "choices": choices,
            "errors": draft.get("errors", []),
        }
    missing = missing_fields(form, draft["values"])
    if form.id == "brief" and draft.get("role") != "lawyer":
        missing = [f for f in missing if f.key != "notes"]  # 본문 초안은 변호사만 받는다
    if not missing and not draft.get("errors"):
        return None
    return {
        "stage": "fields",
        "title": f"{form.name}에 필요한 정보",
        "message": "사건 기록에서 찾지 못한 항목입니다. 입력하면 이어서 초안을 만듭니다.",
        "fields": question_fields(form, missing, draft["values"]),
        "errors": draft.get("errors", []),
    }


def apply_answers(
    draft: dict[str, Any], question: dict[str, Any], answers: dict[str, str], session: Session, today: date
) -> dict[str, Any]:
    forms = load_forms()
    draft = {**draft, "errors": []}
    if question["stage"] == "form":
        form = next((f for f in forms.values() if f.name == answers.get("form")), None)
        if form is None:
            return {**draft, "errors": ["서식을 목록에서 골라 주세요."]}
        return prepare({**draft, "form_id": form.id}, session, today)
    if question["stage"] == "case":
        choice = answers.get("case", "")
        if choice == NO_CASE:
            return prepare({**draft, "case_number": None, "case_resolved": True}, session, today)
        number = question.get("choices", {}).get(choice)
        if not number:
            return {**draft, "errors": ["사건을 목록에서 골라 주세요."]}
        return prepare({**draft, "case_number": number, "case_resolved": True}, session, today)

    form: Form = forms[draft["form_id"]]
    values = dict(draft["values"])
    provided = dict(draft.get("provided", {}))
    errors = []
    for key, raw in answers.items():
        if key not in {f.key for f in form.fields}:
            continue
        cleaned, error = clean_answer(form.field(key), raw)
        if error:
            errors.append(error)
        elif cleaned:
            values[key] = cleaned
            provided[key] = cleaned
    return {**draft, "values": values, "provided": provided, "errors": errors}


def save(draft: dict[str, Any], session: Session, job_id: Any) -> dict[str, Any]:
    """DOCX 초안을 저장하고 화면용 카드를 돌려준다."""
    form = load_forms()[draft["form_id"]]
    values = draft["values"]
    case = repo.get_case(session, draft["case_number"]) if draft.get("case_number") else None
    number = values.get("case_number") or "사건번호없음"
    filename = f"{form.name}_{number}_초안.docx"
    file = repo.save_file(session, filename, render(form, values), mime=DOCX_MIME)
    blanks = later_fields(form, values)
    saved = repo.save_draft(session, case=case, form_id=form.id, file=file, values=values, blanks=blanks, job_id=job_id)
    if case is not None:
        repo.remember_facts(session, case, {f.key: values[f.key] for f in form.fields if f.remember and values.get(f.key) not in (None, "", LATER)})
    session.commit()
    return {
        "kind": "draft",
        "draft_id": str(saved.id),
        "form_id": form.id,
        "form_name": form.name,
        "file_id": str(file.id),
        "filename": filename,
        "size": file.size,
        "case_number": values.get("case_number"),
        "fields": [
            {"label": f.label, "value": "(빈칸)" if values.get(f.key) == LATER else values.get(f.key, "")}
            for f in form.fields
            if values.get(f.key)
        ],
        "blanks": blanks,
    }
