"""준비서면 본문 초안(변호사 기능). 변호사 메모를 상대방 주장과 대응시켜 준비서면 문장으로 풀어 쓴다.

자료실(RAG): 메모마다 찾은 비슷한 과거 문단(참고 문단)의 논리와 표현을 바탕으로 쓰되, 사실·금액·날짜·이름·증거는 이 사건 자료로 바꿔 쓴다.
참고한 문단은 문단의 근거에 "참고N"으로 남기고, 화면에는 참고한 문서 목록을 보여 준다.

지키는 것(eval/brief_quality.py로 시험)
- 사실의 근거는 변호사 메모, 상대방 서면 요약, 사건 기록, 증거 목록뿐. 판례는 인용하지 않고 법조문은 메모에 있는 것만 쓴다.
  법리 인용이 필요한 곳은 "[인용 확인 필요]"로 남긴다.
- 문단마다 근거(메모N, 참고N, 상대방 서면, 사건 기록, 갑 제N호증)를 붙인다.
- 코드가 점검한다: 쓰이지 않은 메모 줄, 판례·메모에 없는 법조문, 목록에 없는 증거, 입력에 없는 금액·날짜,
  참고 문서에서 베껴 온 이름. 점검은 틀린 것을 막는 장치가 아니라 변호사가 먼저 볼 곳을 알려 주는 장치다.
  초안은 반드시 변호사가 검토한다.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Any

from pydantic import BaseModel, Field

from lawca.agent.llm import ChatModel, Turn, with_fallback

SYSTEM = """\
당신은 한국 법무법인의 담당 변호사가 준비서면 본문을 쓰도록 돕습니다. 결과는 변호사가 검토하고 고칠 초안입니다.

지켜야 할 것
- 사실의 근거는 아래에 준 것뿐입니다: 변호사 메모, 상대방 서면 요약, 사건 기록, 증거 목록. 여기에 없는 사실·금액·날짜를 만들지 않습니다.
- 판례를 인용하지 않습니다. 법조문도 변호사 메모에 적힌 것만 씁니다. 법리 인용이 필요해 보이면 그 자리에 "[인용 확인 필요]"라고 적습니다.
- 변호사 메모의 주장 방향을 바꾸거나 새 주장을 더하지 않습니다. 메모에 없는 수식어(예: "적법한", "명백히")도 덧붙이지 않습니다.
- 메모의 모든 줄을 빠짐없이 반영합니다. 메모를 준비서면 문장으로 풀어 쓰고, 상대방 주장과 대응시켜 정리합니다.
- 메모 줄이 주장이나 근거를 담고 있지 않아 뜻을 알 수 없으면(예: 의미 없는 말) 그 줄로 본문을 쓰지 않습니다.
  대신 open_points에 "메모N은 주장으로 읽히지 않아 반영하지 않았습니다"라고 적습니다. 반박 내용을 지어내서 채우지 않습니다.
- 증거는 목록에 있는 표시(예: 갑 제2호증)로만 가리킵니다.
- 준비서면 문체(~합니다, ~입니다)로 씁니다. 과장하거나 상대방을 비난하는 표현을 쓰지 않습니다.

형식
- sections: "1. 피고 주장의 요지", "2. ○○ 주장에 대한 반박"처럼 번호 붙은 제목과 문단들. 마지막은 "결론" 절.
- 문단마다 sources에 근거를 적습니다: 쓴 메모 줄 번호("메모1", "메모2"), 참고한 문단 번호("참고1"), "상대방 서면", "사건 기록", 또는 증거 표시("갑 제1호증").
- open_points: 변호사가 확인하거나 보충해야 할 점(근거가 부족한 곳, [인용 확인 필요] 자리 등).
- citation_needs: 본문에 넣은 "[인용 확인 필요]" 자리마다 순서대로 하나씩 적습니다.
  issue는 그 자리에 필요한 법적 쟁점을 일반적인 법률 용어로 한 문장(예: "채무 승인에 의한 소멸시효 중단"),
  keywords는 판례 검색어 2~4개(예: "소멸시효", "채무승인", "중단")입니다.
  issue와 keywords에는 사람·회사 이름, 금액, 날짜, 사건번호를 넣지 않습니다(외부 판례 검색에 그대로 쓰입니다).
- [참고 문단]은 자료실의 과거 서면에서 찾은 비슷한 문단입니다. 그 논리 전개와 표현을 바탕으로 이 사건의 메모를 풀어 씁니다.
  참고 문단에 적힌 사실·금액·날짜·이름·증거 번호·당사자는 절대 가져오지 않고, 이 사건의 자료(메모·상대방 요약·증거 목록·사건 기록)로 바꿔 씁니다.
  이 사건 자료에 없는 내용은 참고 문단에 있어도 쓰지 않습니다. 참고한 문단은 그 문단의 sources에 "참고N"으로 적습니다.
"""

PLACEHOLDER = "[인용 확인 필요]"


class Paragraph(BaseModel):
    text: str
    sources: list[str] = Field(description="근거: 메모N | 상대방 서면 | 사건 기록 | 갑 제N호증")


class Section(BaseModel):
    heading: str
    paragraphs: list[Paragraph]


class CitationNeed(BaseModel):
    issue: str = Field(description="필요한 법적 쟁점. 일반적인 법률 용어로, 이름·금액·날짜 없이")
    keywords: list[str] = Field(description="판례 검색어 2~4개")


class BriefDraft(BaseModel):
    sections: list[Section]
    open_points: list[str]
    citation_needs: list[CitationNeed] = []


@dataclass(frozen=True)
class Reference:
    """자료실에서 찾은 참고 문단. 어느 메모(note_no)에 대응해 찾았는지 함께 둔다."""

    number: int
    doc_id: str
    title: str
    text: str
    note_nos: tuple[int, ...] = ()


@dataclass(frozen=True)
class BriefInputs:
    record: str
    """사건 기록 한 줄(사건번호·법원·당사자·우리 측)."""
    opponent: list[str]
    """상대방 서면 요약의 주장(요지와 설명)."""
    evidence: list[str]
    """'갑 제1호증 계좌이체 내역(메모)' 형식."""
    notes: str
    references: tuple[Reference, ...] = ()
    """자료실의 참고 문단. 점검의 '입력'에는 넣지 않는다(베껴 온 사실을 잡으려고)."""


CASE_LAW = re.compile(r"(대법원|고등법원|지방법원)\s*\d{4}\s*\.\s*\d{1,2}\s*\.\s*\d{1,2}\s*\.?\s*(선고|자)|\d{2,4}\s*[가-힣]{1,3}\s*\d{2,}\s*(판결|결정)")
STATUTE = re.compile(r"(민법|민사소송법|민사집행법|주택임대차보호법|상가건물\s*임대차보호법|상법|건설산업기본법|[가-힣]+법)?\s*제\s*\d+\s*조(?:의\s*\d+)?")
EVIDENCE = re.compile(r"(갑|을|병)\s*제?\s*\d+\s*호\s*증(?:\s*의\s*\d+)?")
AMOUNT = re.compile(r"\d[\d,]*\s*(?:만\s*|억\s*)?원")
DATE = re.compile(r"(\d{4})\s*(?:\.|년)\s*(\d{1,2})\s*(?:\.|월)\s*(\d{1,2})\s*(?:\.|일)?")
NOTE_REF = re.compile(r"메모\s*(\d+)")
REF_REF = re.compile(r"참고\s*(\d+)")
PARTY_NAME = re.compile(
    r"(?:원고|피고|신청인|피신청인|채권자|채무자|소송대리인)\s+"
    r"(주식회사\s*[가-힣A-Za-z]{2,10}|[가-힣]{2,3})(?:은|는|이|가|의|에게|에|를|을|와|과|께|도|만)?(?![가-힣])"
)
SUBHEADING = re.compile(r"^(?:[가-하]|\d{1,2}|\(\d{1,2}\))[.)]\s*\S.{0,38}$")


def note_lines(notes: str) -> list[str]:
    """메모를 줄(항목)로 나눈다. 앞의 '-', '1.', '•' 같은 표시는 뗀다."""
    lines = []
    for raw in notes.splitlines():
        line = re.sub(r"^\s*(?:[-•*·]|\d+[.)])\s*", "", raw).strip()
        if line:
            lines.append(line)
    return lines


def prompt(inputs: BriefInputs, with_references: bool = True) -> str:
    notes = note_lines(inputs.notes)
    references = [
        line
        for ref in (inputs.references if with_references else ())
        for line in (
            f"[참고 문단 {ref.number}" + (f" — {'·'.join(f'메모{n}' for n in ref.note_nos)}에 대응" if ref.note_nos else "")
            + " — 논리·표현만 참고. 사실·금액·날짜·이름·증거는 이 사건 자료로 바꿀 것]",
            ref.text,
            "",
        )
    ]
    return "\n".join(
        [
            *references,
            "[사건 기록]", inputs.record, "",
            "[상대방 서면 요약]", *([f"- {c}" for c in inputs.opponent] or ["(없음)"]), "",
            "[증거 목록]", *([f"- {e}" for e in inputs.evidence] or ["(없음)"]), "",
            "[변호사 메모]", *[f"메모{i}: {line}" for i, line in enumerate(notes, start=1)], "",
            "위 자료로 준비서면 본문을 작성하세요. 메모의 모든 줄을 반영하세요.",
        ]
    )


NOT_NAMES = {"본인", "측", "등", "및", "또는", "에게", "에서", "으로", "이며", "이고", "은", "는", "가"}
COPY_MIN_CHARS = 15


def _copies(reference: str, body: str) -> bool:
    """본문이 참고 문단의 문장을 그대로 살렸는지(공백을 뺀 글에서 COPY_MIN_CHARS자 이상 이어서 같다).

    모델이 문단 근거에 "참고N"을 적지 않아도 실제로 참고했는지 코드가 알아낸다.
    """
    a, b = _compact(reference), _compact(body)
    if len(a) < COPY_MIN_CHARS or len(b) < COPY_MIN_CHARS:
        return False
    return SequenceMatcher(None, a, b, autojunk=False).find_longest_match(0, len(a), 0, len(b)).size >= COPY_MIN_CHARS


def _bare(name: str) -> str:
    """'주식회사 가온캐피탈이' → '주식회사 가온캐피탈'. 회사 이름은 길어 조사가 이름에 붙어 잡힌다."""
    name = name.strip()
    if name.startswith("주식회사") and len(name) > 8 and name[-1] in "이가은는을를의에와과도만":
        name = name[:-1]
    return name


def _compact(text: str) -> str:
    return re.sub(r"\s+", "", text)


def check(inputs: BriefInputs, draft: BriefDraft) -> dict[str, Any]:
    """변호사가 먼저 볼 곳. 값이 비어 있으면 문제를 찾지 못했다는 뜻이다(맞다는 보장은 아니다)."""
    source_text = _compact(prompt(inputs, with_references=False))
    notes_compact = _compact(inputs.notes)
    labels = {_compact(m.group(0)) for e in inputs.evidence for m in [EVIDENCE.search(e)] if m}
    paragraphs = [p for s in draft.sections for p in s.paragraphs]
    body = "\n".join(p.text for p in paragraphs)
    used = {int(n) for p in paragraphs for s in p.sources for n in NOTE_REF.findall(s)}
    used_refs = sorted({int(n) for p in paragraphs for s in p.sources for n in REF_REF.findall(s)})
    # 참고 문서에 나온 당사자 이름이 이 사건 자료에 없는데 본문에 있으면 다른 사건에서 온 것이다
    foreign = sorted(
        {
            name
            for ref in inputs.references
            for m in PARTY_NAME.finditer(ref.text)
            for name in [_bare(m.group(1))]
            if name not in NOT_NAMES and _compact(name) not in source_text and _compact(name) in _compact(body)
        }
    )
    notes = note_lines(inputs.notes)
    # 모델이 "메모"라고만 적고 번호를 달지 않았으면 반영 여부를 판단할 수 없다(전부 빠졌다고 하지 않는다)
    tracked = bool(used)
    return {
        "notes_tracked": tracked,
        "unused_notes": [{"number": i, "text": line} for i, line in enumerate(notes, start=1) if tracked and i not in used],
        "case_law": sorted({m.group(0).strip() for m in CASE_LAW.finditer(body)}),
        "statutes_not_in_notes": sorted({m.group(0).strip() for m in STATUTE.finditer(body) if _compact(m.group(0)) not in notes_compact}),
        "unknown_evidence": sorted({m.group(0) for m in EVIDENCE.finditer(body) if _compact(m.group(0)) not in labels}),
        "amounts_not_in_inputs": sorted({m.group(0) for m in AMOUNT.finditer(body) if _compact(m.group(0)) not in source_text}),
        "dates_not_in_inputs": sorted(
            {m.group(0) for m in DATE.finditer(body) if f"{m[1]}.{int(m[2])}.{int(m[3])}" not in source_text}
        ),
        # "가. 소멸시효 주장에 대한 반박" 같은 짧은 소제목 줄은 근거가 없어도 된다
        "paragraphs_without_source": sum(1 for p in paragraphs if not p.sources and not SUBHEADING.match(p.text.strip())),
        "placeholders": body.count(PLACEHOLDER),
        # 모델이 적은 "참고N"과, 본문이 참고 문단의 문장을 실제로 살린 것(코드가 확인)을 합친다
        "references_used": sorted(
            {r.number for r in inputs.references if r.number in used_refs or _copies(r.text, body)}
        ),
        "foreign_names": foreign,
    }


def body_text(draft: BriefDraft) -> str:
    """준비서면 틀의 본문 칸에 넣을 글. 제목과 문단을 빈 줄로 나눈다(근거 표시는 넣지 않는다)."""
    blocks = []
    for section in draft.sections:
        paragraphs = "\n".join(p.text.strip() for p in section.paragraphs if p.text.strip())
        blocks.append(f"{section.heading.strip()}\n{paragraphs}" if paragraphs else section.heading.strip())
    return "\n\n".join(blocks)


def write_brief(models: list[ChatModel], inputs: BriefInputs) -> BriefDraft:
    return with_fallback(models, lambda m: m.structured(SYSTEM, [Turn("user", prompt(inputs))], BriefDraft))
