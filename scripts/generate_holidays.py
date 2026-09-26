"""관공서 공휴일 데이터 파일(kr_holidays.csv)을 만든다.

`holidays` 라이브러리로 초안을 만들고, 결과는 저장소에 커밋해 고정한다.
런타임은 이 라이브러리에 의존하지 않고 CSV만 읽는다.

라이브러리가 모르는 임시공휴일은 생성 후 CSV에 직접 추가하고, 근거(관보 등)를 커밋 메시지에 남긴다.

사용법: uv run python scripts/generate_holidays.py 2024 2028
"""

from __future__ import annotations

import csv
import sys
from datetime import date
from pathlib import Path

import holidays

OUT = Path(__file__).resolve().parents[1] / "src" / "lawca" / "deadlines" / "data" / "kr_holidays.csv"


def main(first: int, last: int) -> None:
    rows: list[tuple[date, str]] = []
    for year in range(first, last + 1):
        kr = holidays.KR(years=year, language="ko")
        rows.extend(kr.items())
    rows.sort()
    with OUT.open("w", encoding="utf-8", newline="") as f:
        f.write(f"# 관공서 공휴일 {first}~{last}년. 생성: holidays {holidays.__version__}, {date.today().isoformat()}\n")
        f.write("# 기준: 관공서의 공휴일에 관한 규정(대통령령 제36290호, 2026. 5. 1. 시행). 일요일은 코드에서 처리한다.\n")
        writer = csv.writer(f, lineterminator="\n")
        writer.writerow(["date", "name"])
        writer.writerows((day.isoformat(), name) for day, name in rows)
    print(f"{len(rows)}건 -> {OUT}")


if __name__ == "__main__":
    main(int(sys.argv[1]), int(sys.argv[2]))
