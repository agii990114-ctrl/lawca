"""추출 평가 실행: eval/documents의 PDF를 추출하고 eval/truth의 정답과 비교해 보고서를 쓴다.

기본은 로컬 Ollama다(API 사용량 없음). Gemini로 돌리려면 --provider gemini와 --allow-api를 함께 줘야 한다.

    uv run python eval/run.py                      # Ollama, 전체
    uv run python eval/run.py --only scan --limit 3
    uv run python eval/run.py --provider gemini --allow-api --limit 4

결과: eval/results/<시각>_<모델>.md 와 .json
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import date, datetime
from pathlib import Path

from lawca.api.app import _ollama
from lawca.config import get_settings
from lawca.evaluation import FIELDS, DocScore, score_document, summarize
from lawca.extraction.gemini import GeminiExtractor
from lawca.extraction.normalize import normalize
from lawca.extraction.validate import has_text, pdf_text_pages, validate
from lawca.ollama import OllamaExtractor

ROOT = Path(__file__).resolve().parent
LABELS = {"document_type": "문서 종류", "court": "법원", "case_number": "사건번호", "case_name": "사건명",
          "parties": "당사자", "issued_date": "발령일", "designated_period": "보정 기간"}


def make_extractor(provider: str):
    settings = get_settings()
    if provider == "ollama":
        return OllamaExtractor(_ollama(settings), settings.ollama_max_image_pages), settings.ollama_model
    if not settings.gemini_api_key:
        sys.exit("Gemini API 키가 없습니다.")
    models = settings.gemini_extraction_models
    return GeminiExtractor(settings.gemini_api_key, models), models[0]


def documents(only: str | None, limit: int | None) -> list[Path]:
    paths = sorted((ROOT / "documents").glob("*.pdf"))
    if only:
        paths = [p for p in paths if p.stem.endswith(f"_{only}")]
    return paths[:limit] if limit else paths


def report(model: str, provider: str, rows: list[dict], summary: dict) -> str:
    lines = [
        f"# 추출 평가 — {model} ({provider})",
        "",
        f"- 실행: {datetime.now():%Y-%m-%d %H:%M}",
        "- 평가 기준: 합성 문서(가상 사건, 텍스트 PDF와 스캔본). 실제 법원 문서가 아니므로 실제 정확도의 상한에 가깝다.",
        f"- 문서 {summary['documents']}건, 완전 일치 {summary['exact_match']}건, "
        f"항목 정확도 {summary['field_accuracy']:.1%}, 실패 {summary['failed']}건",
        "",
        "## 항목별",
        "",
        "| 항목 | 맞음 | 전체 | 정확도 |",
        "|---|---:|---:|---:|",
    ]
    for key, c in summary["per_field"].items():
        lines.append(f"| {LABELS[key]} | {c['correct']} | {c['total']} | {c['correct'] / c['total']:.0%} |")
    for variant in ("text", "scan"):
        part = summary["by_variant"].get(variant)
        if part:
            lines.append(f"\n- {variant}: 문서 {part['documents']}건, 완전 일치 {part['exact_match']}건, "
                         f"항목 정확도 {part['field_accuracy']:.1%}")
    lines += ["", "## 문서별", "", "| 문서 | 결과 | 시간(초) | 틀린 항목 | 검증 경고 |", "|---|---|---:|---|---|"]
    for row in rows:
        if row.get("error"):
            lines.append(f"| {row['name']} | 실패 | {row['seconds']:.0f} | {row['error']} | |")
            continue
        wrong = "; ".join(f"{LABELS[k]}: 정답 {v[0]} / 추출 {v[1]}" for k, v in row["mismatches"].items())
        lines.append(f"| {row['name']} | {'일치' if row['exact'] else '불일치'} | {row['seconds']:.0f} | "
                     f"{wrong} | {len(row['issues'])} |")
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--provider", choices=["ollama", "gemini"], default="ollama")
    parser.add_argument("--allow-api", action="store_true", help="Gemini API 호출을 허용한다")
    parser.add_argument("--only", choices=["text", "scan"])
    parser.add_argument("--limit", type=int)
    args = parser.parse_args()
    if args.provider == "gemini" and not args.allow_api:
        sys.exit("Gemini는 API 사용량을 씁니다. 정말 돌리려면 --allow-api를 함께 주세요.")

    extractor, model = make_extractor(args.provider)
    paths = documents(args.only, args.limit)
    scores: dict[str, list[DocScore]] = {"text": [], "scan": []}
    rows: list[dict] = []
    for index, path in enumerate(paths, 1):
        truth = json.loads((ROOT / "truth" / f"{path.stem}.json").read_text(encoding="utf-8"))
        pdf = path.read_bytes()
        started = time.perf_counter()
        try:
            doc = extractor.extract(pdf)
        except Exception as exc:  # 한 문서 실패로 전체를 멈추지 않는다
            seconds = time.perf_counter() - started
            rows.append({"name": path.stem, "seconds": seconds, "error": f"{type(exc).__name__}: {exc}"[:200]})
            print(f"[{index}/{len(paths)}] {path.stem}: 실패 ({seconds:.0f}s) {exc}", flush=True)
            continue
        seconds = time.perf_counter() - started
        score = score_document(path.stem, truth, doc)
        scores[truth["variant"]].append(score)
        pages = pdf_text_pages(pdf)
        issues = validate(normalize(doc), pages, date.today()) if has_text(pages) else []
        rows.append({
            "name": path.stem, "seconds": seconds, "exact": score.exact, "results": score.results,
            "mismatches": {k: [v[0], v[1]] for k, v in score.mismatches.items()},
            "issues": [f"{i.level}:{i.field}:{i.message}" for i in issues],
        })
        print(f"[{index}/{len(paths)}] {path.stem}: {'일치' if score.exact else '불일치'} ({seconds:.0f}s)"
              + "".join(f"\n    {k}: 정답 {v[0]} / 추출 {v[1]}" for k, v in score.mismatches.items()), flush=True)

    summary = summarize(scores["text"] + scores["scan"])
    summary["failed"] = sum(1 for r in rows if r.get("error"))
    summary["by_variant"] = {v: summarize(s) for v, s in scores.items() if s}
    summary["seconds"] = {"total": sum(r["seconds"] for r in rows),
                          "mean": sum(r["seconds"] for r in rows) / len(rows) if rows else 0}

    out = ROOT / "results"
    out.mkdir(exist_ok=True)
    stem = f"{datetime.now():%Y%m%d-%H%M}_{model.replace(':', '-').replace('/', '-')}"
    (out / f"{stem}.json").write_text(json.dumps(
        {"model": model, "provider": args.provider, "fields": FIELDS, "summary": summary, "documents": rows},
        ensure_ascii=False, indent=2), encoding="utf-8")
    (out / f"{stem}.md").write_text(report(model, args.provider, rows, summary), encoding="utf-8")
    print(f"\n완전 일치 {summary['exact_match']}/{summary['documents']}, 항목 정확도 {summary['field_accuracy']:.1%}"
          f" → {out / stem}.md")


if __name__ == "__main__":
    main()
