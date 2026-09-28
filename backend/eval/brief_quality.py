"""준비서면 본문 작성 보조(2단계)의 품질 시험. 가상 사건 3건으로 모델이 쓴 본문을 코드로 점검하고 보고서를 쓴다.

- 프롬프트·출력 형식·점검은 제품 코드(lawca.agent.brief_writer)를 그대로 쓴다.
- 코드 점검: 쓰이지 않은 메모 줄, 판례·법조문 인용, 목록에 없는 증거 표시, 입력에 없는 금액·날짜, 근거 표시 누락.
- API 사용량을 쓰므로 --allow-api가 있어야 돌고, 예비 모델로 넘어가지 않는다(호출 수를 정확히 지키려고).

    uv run python eval/brief_quality.py --allow-api                          # 기본 모델로 3건
    uv run python eval/brief_quality.py --allow-api --model gemini-3.7-flash --cases 1
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from datetime import datetime
from pathlib import Path

from lawca.agent.brief_writer import BriefDraft, BriefInputs, check, write_brief
from lawca.agent.llm import gemini_models
from lawca.config import get_settings

ROOT = Path(__file__).resolve().parent

CASES = [
    {
        "id": "1_대여금",
        "record": "사건 2026가단51234 대여금 / 서울중앙지방법원 / 원고 홍길동 / 피고 김철수 / 우리는 원고 측",
        "opponent": [
            "피고가 원고로부터 3,000만 원을 받은 사실은 인정한다.",
            "위 돈은 원고가 피고의 사업을 돕기 위해 증여한 것이지 빌려준 것이 아니다. 차용증도 없고 이자·변제기도 정하지 않았다.",
            "원고가 낸 문자메시지는 도의적으로 고마움을 표시한 것에 불과하고 갚겠다는 약속이 아니다.",
            "설령 대여금이라 하더라도 소멸시효가 완성되었다.",
        ],
        "evidence": ["갑 제1호증 계좌이체 내역(2021. 3. 2. 3,000만 원, 받는 통장 표시 '대여금')",
                     "갑 제2호증 문자메시지(2023. 5. 10. 피고: '다음 달까지 꼭 갚을게')",
                     "갑 제3호증 내용증명(2025. 1. 15. 발송, 변제 최고)"],
        "notes": """- 증여 아님: 이체할 때 받는 통장 표시를 '대여금'으로 적음(갑1). 증여라면 이렇게 적을 이유 없음.
- 문자(갑2)는 '갚을게'라고 명시 → 변제 약속, 채무 승인에 해당.
- 소멸시효: 2023. 5. 10. 채무 승인으로 시효 중단, 2025. 1. 15. 최고(갑3) 후 6개월 안에 소 제기 → 완성 안 됨. 법리 인용은 내가 넣을 것.
- 결론: 원고 청구 인용되어야 함.""",
    },
    {
        "id": "2_임대차보증금",
        "record": "사건 2026가소30217 임대차보증금 / 수원지방법원 / 원고 이영희(임차인) / 피고 박민수(임대인) / 우리는 원고 측",
        "opponent": [
            "원고가 퇴거하면서 벽지와 장판을 훼손하여 원상복구비 180만 원이 들었으므로 보증금에서 공제해야 한다.",
            "원고는 2025년 11월분과 12월분 월세 합계 140만 원을 내지 않았으므로 이것도 공제해야 한다.",
            "따라서 돌려줄 보증금은 5,000만 원에서 320만 원을 뺀 4,680만 원이다.",
        ],
        "evidence": ["갑 제1호증 임대차계약서(보증금 5,000만 원, 월세 70만 원)",
                     "갑 제2호증 월세 입금 내역(2025. 11. 25. 70만 원, 2025. 12. 26. 70만 원)",
                     "갑 제3호증 입주 당시와 퇴거 당시 사진"],
        "notes": """- 연체 없음: 11월·12월분 모두 입금(갑2). 피고 주장 사실과 다름.
- 원상복구: 사진(갑3) 보면 벽지 변색·장판 눌림 정도 → 4년 거주에 따른 통상 손모. 임차인 부담 아님. 법리 인용 필요.
- 180만 원 견적 자료도 피고가 안 냄. 입증 책임 피고.
- 결론: 보증금 5,000만 원 전액 반환.""",
    },
    {
        "id": "3_공사대금",
        "record": "사건 2026가합1052 공사대금 / 부산지방법원 동부지원 / 원고 주식회사 대성토건(시공사) / 피고 박민수(건축주) / 우리는 원고 측",
        "opponent": [
            "원고가 시공한 건물 외벽에 균열 하자가 있으므로 공사대금을 2,000만 원 감액해야 한다.",
            "원고가 약정 준공일(2025. 6. 30.)보다 45일 늦게 준공했으므로 지체상금 1,350만 원을 공제해야 한다.",
        ],
        "evidence": ["갑 제1호증 공사도급계약서(공사대금 3억 원, 준공일 2025. 6. 30.)",
                     "갑 제2호증 사용승인서(2025. 8. 14.)",
                     "갑 제3호증 피고의 설계변경 요청 공문(2025. 5. 20.)",
                     "갑 제4호증 하자 점검 결과서(2025. 9. 1., 외벽 미세 균열은 건조 수축에 따른 것, 구조 안전 문제 없음)"],
        "notes": """- 하자: 점검 결과(갑4) 미세 균열은 건조 수축, 구조 문제 아님 → 감액 사유 아님. 보수 요청도 없었음.
- 지체: 피고가 5. 20. 설계변경 요청(갑3) → 공기 연장 사유, 원고 책임 없음. 지체상금 조항 해석은 내가 정리할 것.
- 사용승인(갑2) 받음 → 완성된 일.
- 결론: 잔금 청구 인용.""",
    },
]

def inputs_of(case: dict) -> BriefInputs:
    return BriefInputs(record=case["record"], opponent=case["opponent"], evidence=case["evidence"], notes=case["notes"])


def render(case: dict, draft: BriefDraft) -> list[str]:
    lines = []
    for section in draft.sections:
        lines.append(f"**{section.heading}**\n")
        for p in section.paragraphs:
            lines.append(f"{p.text}  \n<sub>근거: {', '.join(p.sources) or '(없음)'}</sub>\n")
    if draft.open_points:
        lines.append("**변호사 확인 사항**\n")
        lines += [f"- {o}" for o in draft.open_points]
    return lines


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--allow-api", action="store_true")
    parser.add_argument("--model")
    parser.add_argument("--cases", nargs="+", help="사건 번호(1 2 3)")
    args = parser.parse_args()
    if not args.allow_api:
        sys.exit("Gemini API 사용량을 씁니다. --allow-api를 함께 주세요.")
    settings = get_settings()
    model_name = args.model or settings.gemini_model
    [model] = gemini_models(settings.gemini_api_key, [model_name])
    chosen = [c for c in CASES if not args.cases or c["id"].split("_")[0] in args.cases]

    results, report = [], [f"# 준비서면 본문 작성 품질 시험 — {model_name}", "", f"- 실행: {datetime.now():%Y-%m-%d %H:%M}", "- 가상 사건", ""]
    for case in chosen:
        started = time.perf_counter()
        try:
            draft = write_brief([model], inputs_of(case))
        except Exception as exc:  # noqa: BLE001 - 한도·혼잡이면 멈추고 알린다(재시도하지 않는다)
            print(f"{case['id']}: 실패 {exc}")
            break
        seconds = time.perf_counter() - started
        result = check(inputs_of(case), draft)
        results.append({"case": case["id"], "model": model_name, "seconds": round(seconds, 1), "checks": result,
                        "draft": draft.model_dump()})
        print(case["id"], f"{seconds:.0f}s", json.dumps(result, ensure_ascii=False))
        report += [f"## {case['id']} ({seconds:.0f}초)", "", "```json", json.dumps(result, ensure_ascii=False, indent=1), "```", "",
                   *render(case, draft), ""]

    out = ROOT / "results"
    out.mkdir(exist_ok=True)
    stem = f"brief_{datetime.now():%Y%m%d-%H%M}_{model_name}"
    (out / f"{stem}.json").write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    (out / f"{stem}.md").write_text("\n".join(report), encoding="utf-8")
    print("→", out / f"{stem}.md")


if __name__ == "__main__":
    main()
