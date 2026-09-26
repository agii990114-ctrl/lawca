"""서식: 정의(data/forms.toml), 필수 항목 확인, 사건에서 값 채우기, DOCX 초안 만들기.

누락 판단과 값 검증은 코드가 한다. LLM은 요청에서 서식과 값을 찾아내는 일만 한다.
"""

from __future__ import annotations

import io
import tomllib
from dataclasses import dataclass, field
from datetime import date
from functools import cache
from importlib import resources
from typing import Any

from docxtpl import DocxTemplate

from lawca.extraction.normalize import normalize_date

LATER = "__later__"
"""사용자가 '나중에 입력'을 고른 항목의 값. 초안에는 빈칸으로 들어간다."""


@dataclass(frozen=True)
class Field:
    key: str
    label: str
    type: str
    required: bool = False
    required_if: dict[str, str] = field(default_factory=dict)
    from_case: str | None = None
    default: str | None = None
    allow_later: bool = False
    remember: bool = False
    options: tuple[str, ...] = ()
    help: str | None = None

    def is_required(self, values: dict[str, str]) -> bool:
        if self.required:
            return True
        return bool(self.required_if) and all(values.get(k) == v for k, v in self.required_if.items())


@dataclass(frozen=True)
class Form:
    id: str
    name: str
    template: str
    aliases: tuple[str, ...]
    fields: tuple[Field, ...]

    def field(self, key: str) -> Field:
        return next(f for f in self.fields if f.key == key)


def _field(raw: dict[str, Any]) -> Field:
    return Field(
        key=raw["key"],
        label=raw["label"],
        type=raw["type"],
        required=raw.get("required", False),
        required_if=raw.get("required_if", {}),
        from_case=raw.get("from_case"),
        default=raw.get("default"),
        allow_later=raw.get("allow_later", False),
        remember=raw.get("remember", False),
        options=tuple(raw.get("options", ())),
        help=raw.get("help"),
    )


@cache
def load_forms() -> dict[str, Form]:
    raw = tomllib.loads(resources.files("lawca.forms").joinpath("data/forms.toml").read_text(encoding="utf-8"))
    common = tuple(_field(f) for f in raw["common"])
    forms = {}
    for item in raw["form"]:
        forms[item["id"]] = Form(
            id=item["id"],
            name=item["name"],
            template=item["template"],
            aliases=tuple(item.get("aliases", ())),
            fields=common + tuple(_field(f) for f in item.get("fields", ())),
        )
    return forms


def find_form(text: str) -> Form | None:
    """이름이나 별칭이 들어간 서식을 찾는다. LLM이 서식을 고르지 못했을 때의 예비 수단."""
    compact = text.replace(" ", "")
    for form in load_forms().values():
        if any(alias.replace(" ", "") in compact for alias in (form.name, *form.aliases)):
            return form
    return None


def initial_values(form: Form, case_values: dict[str, str], remembered: dict[str, str], today: date) -> dict[str, str]:
    """사건에서 가져온 값, 사건에 저장해 둔 값, 기본값 순으로 채운다."""
    values: dict[str, str] = {}
    for f in form.fields:
        if f.from_case and case_values.get(f.from_case):
            values[f.key] = case_values[f.from_case]
        elif remembered.get(f.key):
            values[f.key] = remembered[f.key]
        elif f.default == "today":
            values[f.key] = today.isoformat()
        elif f.default is not None:
            values[f.key] = f.default
    return values


def missing_fields(form: Form, values: dict[str, str]) -> list[Field]:
    """비어 있는 필수 항목. '나중에 입력'을 고른 항목은 빠진 것으로 보지 않는다."""
    return [f for f in form.fields if f.is_required(values) and not str(values.get(f.key, "")).strip()]


def clean_answer(f: Field, value: str) -> tuple[str | None, str | None]:
    """사용자 답을 검증한다. (정리된 값, 오류 메시지) 중 하나를 돌려준다."""
    value = str(value).strip()
    if value == LATER:
        return (LATER, None) if f.allow_later else (None, f"{f.label}은(는) 나중에 입력할 수 없습니다.")
    if not value:
        return None, None
    if f.type == "date":
        normalized = normalize_date(value)
        try:
            date.fromisoformat(normalized)
        except ValueError:
            return None, f"{f.label}: 날짜 형식이 아닙니다(예: 2026-09-20)."
        return normalized, None
    if f.type == "select" and value not in f.options:
        return None, f"{f.label}: 목록에서 골라 주세요."
    if f.type == "number" and not value.isdigit():
        return None, f"{f.label}: 숫자로 입력해 주세요."
    return value, None


def question_fields(form: Form, fields: list[Field], values: dict[str, str]) -> list[dict[str, Any]]:
    """되묻기 입력 폼에 보낼 항목 정의."""
    return [
        {
            "key": f.key,
            "label": f.label,
            "type": f.type,
            "options": list(f.options),
            "default": values.get(f.key) if values.get(f.key) != LATER else "",
            "allow_later": f.allow_later,
            "help": f.help,
        }
        for f in fields
    ]


def _korean_date(value: str) -> str:
    try:
        d = date.fromisoformat(value)
    except ValueError:
        return value
    return f"{d.year}. {d.month}. {d.day}."


def render(form: Form, values: dict[str, str]) -> bytes:
    """DOCX 초안을 만든다. '나중에 입력'한 항목은 빈칸(밑줄)으로 둔다."""
    context: dict[str, str] = {}
    for f in form.fields:
        value = values.get(f.key, "")
        if value == LATER:
            value = "20    .    .    ." if f.type == "date" else "____________"
        elif f.type == "date" and value:
            value = _korean_date(value)
        context[f.key] = value
    template = DocxTemplate(io.BytesIO(resources.files("lawca.forms").joinpath(f"data/{form.template}").read_bytes()))
    template.render(context, autoescape=True)
    out = io.BytesIO()
    template.save(out)
    return out.getvalue()


def later_fields(form: Form, values: dict[str, str]) -> list[str]:
    return [f.label for f in form.fields if values.get(f.key) == LATER]
