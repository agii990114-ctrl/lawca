"""추출 평가 채점 테스트(순수 함수)와 합성 정답 파일 형식 확인."""

import json
from pathlib import Path

from lawca.evaluation import FIELDS, score_document, summarize
from lawca.extraction.schema import Party, TextField
from tests.test_extraction import correction_order, ev

TRUTH = {
    "document_type": "보정명령", "court": "서울중앙지방법원", "case_number": "2026가단51234",
    "case_name": "대여금", "parties": [["원고", "홍길동"]], "issued_date": "2026-09-15",
    "designated_period": [7, "일"],
}


def test_exact_match():
    score = score_document("a", TRUTH, correction_order())
    assert score.exact and set(score.results) == set(FIELDS)


def test_whitespace_and_date_format_are_ignored():
    doc = correction_order(
        court=TextField(value="서울 중앙 지방법원", evidence=ev("서울중앙지방법원")),
        issued_date=TextField(value="2026. 9. 15.", evidence=ev("2026. 9. 15.")),
        parties=[Party(role="원 고", name="홍 길동", evidence=ev("원 고 홍길동"))],
    )
    assert score_document("a", TRUTH, doc).exact


def test_mismatches_are_reported():
    doc = correction_order(
        issued_date=TextField(value="2026-09-10", evidence=ev("2026. 9. 10.")),
        designated_period=None,
        parties=[Party(role="원고", name="홍길동", evidence=ev("원 고 홍길동")),
                 Party(role="피고", name="김철수", evidence=ev("피 고 김철수"))],
    )
    score = score_document("a", TRUTH, doc)
    assert not score.exact
    assert set(score.mismatches) == {"issued_date", "designated_period", "parties"}
    assert score.mismatches["issued_date"] == ("2026-09-15", "2026-09-10")


def test_skip_and_summary():
    truth = TRUTH | {"skip": ["designated_period"]}
    good = score_document("a", truth, correction_order(designated_period=None))
    bad = score_document("b", TRUTH, correction_order(case_name=None))
    assert "designated_period" not in good.results and good.exact
    summary = summarize([good, bad])
    assert summary["documents"] == 2 and summary["exact_match"] == 1
    assert summary["per_field"]["case_name"] == {"correct": 1, "total": 2}
    assert summary["per_field"]["designated_period"] == {"correct": 1, "total": 1}
    assert summary["field_accuracy"] == 12 / 13  # 6/6 + 6/7


def test_truth_files_are_well_formed():
    truth_dir = Path(__file__).resolve().parents[1] / "eval" / "truth"
    files = sorted(truth_dir.glob("*.json"))
    assert len(files) >= 10
    for path in files:
        truth = json.loads(path.read_text(encoding="utf-8"))
        assert truth["variant"] in ("text", "scan")
        assert (truth_dir.parent / "documents" / f"{path.stem}.pdf").exists()
        for key in FIELDS:
            assert key in truth or key in truth.get("skip", []), (path.name, key)
