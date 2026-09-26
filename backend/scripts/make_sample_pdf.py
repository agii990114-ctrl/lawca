"""합성(가상) 보정명령 PDF를 만든다. 추출 동작 확인용이며 모든 내용은 지어낸 것이다.

사용법: uv run python scripts/make_sample_pdf.py
"""

from __future__ import annotations

from pathlib import Path

from reportlab.lib.pagesizes import A4
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas

OUT = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "synthetic_correction_order.pdf"
FONT = Path("C:/Windows/Fonts/malgun.ttf")

LINES = [
    ("서 울 중 앙 지 방 법 원", 18, "center"),
    ("보 정 명 령", 20, "center"),
    ("", 12, "left"),
    ("사      건    2026가단51234  대여금", 12, "left"),
    ("원      고    홍길동", 12, "left"),
    ("피      고    김철수", 12, "left"),
    ("", 12, "left"),
    ("원고는 이 명령을 송달받은 날부터 7일 이내에 다음 사항을 보정하시기 바랍니다.", 12, "left"),
    ("", 12, "left"),
    ("보정할 사항", 12, "left"),
    ("1. 피고 김철수에 대한 소장 부본이 폐문부재로 송달되지 않았으므로,", 12, "left"),
    ("   피고의 주민등록초본 등을 발급받아 정확한 주소를 보정하십시오.", 12, "left"),
    ("", 12, "left"),
    ("2026. 9. 15.", 12, "center"),
    ("판사  이영희", 14, "right"),
    ("", 12, "left"),
    ("※ 이 문서는 개발용으로 만든 가상 문서입니다.", 9, "left"),
]


def main() -> None:
    pdfmetrics.registerFont(TTFont("Malgun", str(FONT)))
    OUT.parent.mkdir(parents=True, exist_ok=True)
    c = canvas.Canvas(str(OUT), pagesize=A4)
    width, height = A4
    y = height - 80
    for text, size, align in LINES:
        c.setFont("Malgun", size)
        if align == "center":
            c.drawCentredString(width / 2, y, text)
        elif align == "right":
            c.drawRightString(width - 72, y, text)
        else:
            c.drawString(72, y, text)
        y -= size + 14
    c.save()
    print(OUT)


if __name__ == "__main__":
    main()
