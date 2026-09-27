"""테스트용 가상 답변서 PDF를 만든다(tests/fixtures/synthetic_answer.pdf). 모든 이름과 사건은 지어낸 것이다.

사용법: uv run python scripts/make_sample_answer.py   (Windows의 맑은 고딕 글꼴이 필요하다)
"""

from __future__ import annotations

from pathlib import Path

from reportlab.lib.pagesizes import A4
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas

OUT = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "synthetic_answer.pdf"
FONT = Path("C:/Windows/Fonts/malgun.ttf")

PAGES = [
    [
        ("답  변  서", 20, "center"),
        ("", 12, "left"),
        ("사      건    2026가단51234  대여금", 12, "left"),
        ("원      고    홍길동", 12, "left"),
        ("피      고    김철수", 12, "left"),
        ("", 12, "left"),
        ("위 사건에 관하여 피고는 다음과 같이 답변합니다.", 11, "left"),
        ("", 12, "left"),
        ("청구취지에 대한 답변", 13, "center"),
        ("1. 원고의 청구를 기각한다.", 11, "left"),
        ("2. 소송비용은 원고가 부담한다.", 11, "left"),
        ("라는 판결을 구합니다.", 11, "left"),
        ("", 12, "left"),
        ("청구원인에 대한 답변", 13, "center"),
        ("1. 피고가 원고로부터 3,000만 원을 받은 사실은 인정합니다.", 11, "left"),
        ("2. 그러나 위 돈은 원고가 피고의 사업을 돕기 위하여 증여한 것이지 빌려준 것이 아닙니다.", 11, "left"),
        ("   원고와 피고 사이에 차용증을 작성한 사실이 없고 이자나 변제기를 정한 사실도 없습니다.", 11, "left"),
    ],
    [
        ("3. 원고가 제출한 문자메시지는 피고가 도의적으로 고마움을 표시한 것에 불과하고", 11, "left"),
        ("   돈을 갚겠다는 약속이 아닙니다.", 11, "left"),
        ("4. 설령 대여금이라 하더라도 원고의 청구권은 이미 소멸시효가 완성되었습니다.", 11, "left"),
        ("", 12, "left"),
        ("입  증  방  법", 13, "center"),
        ("1. 을 제1호증    카카오톡 대화 내역", 11, "left"),
        ("2. 을 제2호증의1    사업자등록증명", 11, "left"),
        ("", 12, "left"),
        ("2026. 9. 20.", 12, "center"),
        ("피고  김철수  (서명 또는 날인)", 12, "right"),
        ("서울중앙지방법원  귀중", 13, "left"),
    ],
]


def main() -> None:
    pdfmetrics.registerFont(TTFont("Malgun", str(FONT)))
    c = canvas.Canvas(str(OUT), pagesize=A4)
    width, height = A4
    for page in PAGES:
        y = height - 80
        for text, size, align in page:
            c.setFont("Malgun", size)
            if align == "center":
                c.drawCentredString(width / 2, y, text)
            elif align == "right":
                c.drawRightString(width - 72, y, text)
            else:
                c.drawString(72, y, text)
            y -= size + 13
        c.setFont("Malgun", 8)
        c.drawString(72, 40, "※ 이 문서는 개발용으로 만든 가상 문서입니다.")
        c.showPage()
    c.save()
    print(OUT)


if __name__ == "__main__":
    main()
