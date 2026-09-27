"""평가용 합성 법원 문서(PDF)와 정답(JSON)을 만든다. 모든 이름·사건·회사는 지어낸 것이다.

- 텍스트 PDF 10건: 보정명령 4, 판결 2, 결정 1, 지급명령 1, 화해권고결정 1, 기일통지서 1
- 스캔본 6건: 일부 문서를 이미지로 바꿔 기울이고, 잡음과 도장을 얹은 PDF(텍스트 층 없음)
- 함정: 변론종결일과 선고일이 함께 있는 판결, 작성일과 기일이 함께 있는 기일통지서, 두 쪽 문서, 피고 두 명

정답의 skip은 채점하지 않는 항목이다. 문서가 정한 기간(designated_period)은 보정명령에서만 채점한다.

사용법: uv run python eval/make_documents.py   (Windows의 맑은 고딕 글꼴이 필요하다)
"""

from __future__ import annotations

import io
import json
import random
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pypdfium2 as pdfium
from PIL import Image, ImageDraw, ImageFilter, ImageFont
from reportlab.lib.pagesizes import A4
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas

ROOT = Path(__file__).resolve().parent
DOCS = ROOT / "documents"
TRUTH = ROOT / "truth"
FONT_PATH = Path("C:/Windows/Fonts/malgun.ttf")

Line = tuple[str, int, str]  # (글, 크기, 정렬 left|center|right)


@dataclass
class Spec:
    id: str
    pages: list[list[Line]]
    truth: dict[str, Any]
    scan: bool = False
    stamp_text: str = "판사"


def header(court: str, title: str) -> list[Line]:
    return [(" ".join(court), 16, "center"), (" ".join(title), 20, "center"), ("", 12, "left")]


def case_lines(number: str, name: str, parties: list[tuple[str, str]]) -> list[Line]:
    lines: list[Line] = [(f"사      건    {number}  {name}", 12, "left")]
    for role, person in parties:
        spaced = "   ".join(role) if len(role) == 2 else role
        lines.append((f"{spaced}    {person}", 12, "left"))
    return lines + [("", 12, "left")]


def korean(date: str) -> str:
    y, m, d = (int(x) for x in date.split("-"))
    return f"{y}. {m}. {d}."


def correction(id_: str, court: str, number: str, name: str, parties, date: str, amount: int, unit_phrase: str,
               items: list[str], judge: str, scan: bool = False) -> Spec:
    body = header(court, "보정명령") + case_lines(number, name, parties)
    body.append((f"원고는 이 명령을 송달받은 날부터 {amount}{unit_phrase} 이내에 다음 사항을 보정하시기 바랍니다.", 11, "left"))
    body.append(("", 12, "left"))
    body.append(("보정할 사항", 12, "left"))
    body += [(f"{i}. {text}", 11, "left") for i, text in enumerate(items, 1)]
    body += [("", 12, "left"), (korean(date), 12, "center"), (f"판사  {judge}", 14, "right")]
    unit = "일" if unit_phrase == "일" else "주"
    return Spec(id_, [body], {
        "document_type": "보정명령", "court": court, "case_number": number, "case_name": name,
        "parties": [list(p) for p in parties], "issued_date": date, "designated_period": [amount, unit],
    }, scan=scan)


def specs() -> list[Spec]:
    out: list[Spec] = []
    out.append(correction(
        "c01_주소보정", "서울중앙지방법원", "2026가단51234", "대여금",
        [("원고", "홍길동"), ("피고", "김철수")], "2026-09-15", 7, "일",
        ["피고 김철수에 대한 소장 부본이 폐문부재로 송달되지 않았으므로,",
         "   피고의 주민등록초본 등을 발급받아 정확한 주소를 보정하십시오."], "이영희", scan=True))
    out.append(correction(
        "c02_인지보정", "수원지방법원", "2026가소30217", "손해배상(기)",
        [("원고", "이영희"), ("피고", "주식회사 한빛물산")], "2026-08-28", 7, "일",
        ["소장에 붙인 인지액이 부족합니다. 부족한 인지액 45,000원을 납부하고",
         "   그 영수증을 제출하십시오."], "정도윤", scan=True))
    out.append(correction(
        "c03_청구취지보정", "부산지방법원 동부지원", "2026가합1052", "공사대금",
        [("원고", "주식회사 대성토건"), ("피고", "박민수")], "2026-09-02", 14, "일",
        ["청구취지 제1항의 지연손해금 기산일을 특정하십시오.",
         "2. 공사도급계약서 사본을 증거로 제출하십시오."], "한서연"))
    two_defendants = [("원고", "주식회사 다온보증"), ("피고", "1. 문성진"), ("", "2. 백은서")]
    c04 = correction(
        "c04_피고2명", "대구지방법원 서부지원", "2026가단3345", "구상금",
        two_defendants, "2026-09-18", 10, "일",
        ["피고 문성진, 백은서에 대한 소장 부본이 이사불명으로 송달되지 않았으므로",
         "   각 피고의 주소를 보정하십시오."], "오지민")
    c04.truth["parties"] = [["원고", "주식회사 다온보증"], ["피고", "문성진"], ["피고", "백은서"]]
    out.append(c04)

    # 판결: 변론종결일과 선고일이 함께 있다. 발령일(issued_date)은 선고일이다.
    j01 = header("서울중앙지방법원", "판결") + [
        ("사      건    2025가단287716  임대차보증금", 12, "left"),
        ("원      고    정수진", 12, "left"),
        ("피      고    최동욱", 12, "left"),
        ("변 론 종 결    2026. 8. 20.", 12, "left"),
        ("판 결 선 고    2026. 9. 10.", 12, "left"),
        ("", 12, "left"),
        ("주      문", 13, "center"),
        ("1. 피고는 원고에게 50,000,000원 및 이에 대하여 2025. 12. 1.부터 다 갚는 날까지", 11, "left"),
        ("   연 12%의 비율로 계산한 돈을 지급하라.", 11, "left"),
        ("2. 소송비용은 피고가 부담한다.", 11, "left"),
        ("3. 제1항은 가집행할 수 있다.", 11, "left"),
        ("", 12, "left"),
        ("판사  김태윤", 14, "right"),
    ]
    out.append(Spec("j01_판결", [j01], {
        "document_type": "판결", "court": "서울중앙지방법원", "case_number": "2025가단287716",
        "case_name": "임대차보증금", "parties": [["원고", "정수진"], ["피고", "최동욱"]],
        "issued_date": "2026-09-10", "designated_period": None,
    }, scan=True))

    j02_p1 = header("광주지방법원", "판결") + [
        ("사      건    2026가합20418  손해배상(기)", 12, "left"),
        ("원      고    주식회사 새벽유통", 12, "left"),
        ("피      고    유재민", 12, "left"),
        ("변 론 종 결    2026. 8. 27.", 12, "left"),
        ("판 결 선 고    2026. 9. 17.", 12, "left"),
        ("", 12, "left"),
        ("주      문", 13, "center"),
        ("1. 원고의 청구를 기각한다.", 11, "left"),
        ("2. 소송비용은 원고가 부담한다.", 11, "left"),
        ("", 12, "left"),
        ("청 구 취 지", 13, "center"),
        ("피고는 원고에게 120,000,000원 및 이에 대한 지연손해금을 지급하라.", 11, "left"),
    ]
    j02_p2 = [
        ("이      유", 13, "center"),
        ("1. 기초사실", 12, "left"),
        ("원고는 2025. 3. 2. 피고와 물품공급계약을 체결하였다.", 11, "left"),
        ("2. 판단", 12, "left"),
        ("원고가 제출한 증거만으로는 피고의 채무불이행을 인정하기 부족하다.", 11, "left"),
        ("3. 결론", 12, "left"),
        ("원고의 청구는 이유 없으므로 기각한다.", 11, "left"),
        ("", 12, "left"),
        ("재판장  판사  강민재", 13, "right"),
        ("판사  윤하은", 13, "right"),
        ("판사  조현우", 13, "right"),
    ]
    out.append(Spec("j02_판결_2쪽", [j02_p1, j02_p2], {
        "document_type": "판결", "court": "광주지방법원", "case_number": "2026가합20418",
        "case_name": "손해배상(기)", "parties": [["원고", "주식회사 새벽유통"], ["피고", "유재민"]],
        "issued_date": "2026-09-17", "designated_period": None,
    }))

    d01 = header("서울동부지방법원", "결정") + [
        ("사      건    2026카확512  소송비용액확정", 12, "left"),
        ("신  청  인    배서준", 12, "left"),
        ("피 신 청 인    임다현", 12, "left"),
        ("", 12, "left"),
        ("주      문", 13, "center"),
        ("피신청인이 신청인에게 상환하여야 할 소송비용액은 3,245,000원임을 확정한다.", 11, "left"),
        ("", 12, "left"),
        ("이      유", 13, "center"),
        ("신청인의 소송비용액확정 신청을 심사하여 주문과 같이 결정한다.", 11, "left"),
        ("", 12, "left"),
        ("2026. 9. 4.", 12, "center"),
        ("사법보좌관  남궁현", 14, "right"),
    ]
    out.append(Spec("d01_결정", [d01], {
        "document_type": "결정", "court": "서울동부지방법원", "case_number": "2026카확512",
        "case_name": "소송비용액확정", "parties": [["신청인", "배서준"], ["피신청인", "임다현"]],
        "issued_date": "2026-09-04", "skip": ["designated_period"],
    }, scan=True, stamp_text="사법보좌관"))

    p01 = header("서울중앙지방법원", "지급명령") + [
        ("사      건    2026차전115203  양수금", 12, "left"),
        ("채  권  자    주식회사 가온캐피탈", 12, "left"),
        ("채  무  자    신유나", 12, "left"),
        ("", 12, "left"),
        ("채무자는 채권자에게 아래 청구취지 기재의 금액을 지급하라.", 11, "left"),
        ("채무자는 이 명령이 송달된 날부터 2주 이내에 이의신청을 할 수 있습니다.", 11, "left"),
        ("", 12, "left"),
        ("청 구 취 지", 13, "center"),
        ("채무자는 채권자에게 8,730,000원 및 이에 대한 지연손해금을 지급하라.", 11, "left"),
        ("", 12, "left"),
        ("2026. 9. 8.", 12, "center"),
        ("사법보좌관  권도현", 14, "right"),
    ]
    out.append(Spec("p01_지급명령", [p01], {
        "document_type": "지급명령", "court": "서울중앙지방법원", "case_number": "2026차전115203",
        "case_name": "양수금", "parties": [["채권자", "주식회사 가온캐피탈"], ["채무자", "신유나"]],
        "issued_date": "2026-09-08", "skip": ["designated_period"],
    }, scan=True, stamp_text="사법보좌관"))

    s01 = header("인천지방법원", "화해권고결정") + [
        ("사      건    2026가소7781  손해배상(자)", 12, "left"),
        ("원      고    한지훈", 12, "left"),
        ("피      고    송미라", 12, "left"),
        ("", 12, "left"),
        ("위 사건의 공평한 해결을 위하여 당사자의 이익, 그 밖의 모든 사정을 참작하여", 11, "left"),
        ("다음과 같이 결정한다.", 11, "left"),
        ("", 12, "left"),
        ("결정사항", 13, "center"),
        ("1. 피고는 원고에게 2,400,000원을 2026. 10. 31.까지 지급한다.", 11, "left"),
        ("2. 원고는 나머지 청구를 포기한다.", 11, "left"),
        ("", 12, "left"),
        ("이 결정서 정본을 송달받은 날부터 2주 이내에 이의를 신청하지 아니하면", 11, "left"),
        ("이 결정은 재판상 화해와 같은 효력을 가집니다.", 11, "left"),
        ("", 12, "left"),
        ("2026. 9. 11.", 12, "center"),
        ("판사  서지안", 14, "right"),
    ]
    out.append(Spec("s01_화해권고결정", [s01], {
        "document_type": "화해권고결정", "court": "인천지방법원", "case_number": "2026가소7781",
        "case_name": "손해배상(자)", "parties": [["원고", "한지훈"], ["피고", "송미라"]],
        "issued_date": "2026-09-11", "skip": ["designated_period"],
    }))

    # 기일통지서: 작성일과 변론기일 날짜가 함께 있다. 발령일은 작성일이다.
    h01 = header("서울남부지방법원", "기일통지서") + [
        ("사      건    2026가단9921  부당이득금", 12, "left"),
        ("원      고    오세영", 12, "left"),
        ("피      고    윤태호", 12, "left"),
        ("", 12, "left"),
        ("위 사건의 변론기일이 다음과 같이 지정되었으니 출석하시기 바랍니다.", 11, "left"),
        ("", 12, "left"),
        ("일      시    2026. 10. 15. 14:30", 12, "left"),
        ("장      소    제303호 법정", 12, "left"),
        ("", 12, "left"),
        ("2026. 9. 1.", 12, "center"),
        ("법원주사  고은비", 14, "right"),
    ]
    out.append(Spec("h01_기일통지서", [h01], {
        "document_type": "기일통지서", "court": "서울남부지방법원", "case_number": "2026가단9921",
        "case_name": "부당이득금", "parties": [["원고", "오세영"], ["피고", "윤태호"]],
        "issued_date": "2026-09-01", "skip": ["designated_period"],
    }, scan=True, stamp_text="법원주사"))
    return out


def write_pdf(pages: list[list[Line]]) -> bytes:
    buffer = io.BytesIO()
    c = canvas.Canvas(buffer, pagesize=A4)
    width, height = A4
    for page in pages:
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
        c.drawString(72, 40, "※ 평가용으로 만든 가상 문서입니다.")
        c.showPage()
    c.save()
    return buffer.getvalue()


def scan(pdf: bytes, seed: int, stamp_text: str) -> bytes:
    """스캔본처럼 만든다: 150dpi 이미지, 약간 기울임, 잡음, 흐림, 빨간 도장. 텍스트 층은 없다."""
    rng = random.Random(seed)
    font = ImageFont.truetype(str(FONT_PATH), 22)
    images = []
    for page in pdfium.PdfDocument(pdf):
        image = page.render(scale=150 / 72).to_pil().convert("L")
        draw = ImageDraw.Draw(image)
        # 서명 줄 근처에 도장(빨간 원)을 찍는다. 흑백 스캔이므로 회색으로 남는다.
        x, y = image.width - 250 + rng.randint(-20, 20), int(image.height * 0.55) + rng.randint(-40, 40)
        draw.ellipse((x, y, x + 110, y + 110), outline=110, width=5)
        draw.text((x + 18, y + 40), stamp_text[:4], fill=110, font=font)
        image = image.rotate(rng.uniform(-1.2, 1.2), expand=False, fillcolor=255, resample=Image.BICUBIC)
        noise = Image.effect_noise(image.size, 18).convert("L")
        image = Image.blend(image, noise, 0.08).filter(ImageFilter.GaussianBlur(0.6))
        images.append(image.convert("RGB"))
    out = io.BytesIO()
    images[0].save(out, format="PDF", save_all=True, append_images=images[1:], resolution=150)
    return out.getvalue()


def main() -> None:
    pdfmetrics.registerFont(TTFont("Malgun", str(FONT_PATH)))
    DOCS.mkdir(exist_ok=True)
    TRUTH.mkdir(exist_ok=True)
    count = 0
    for index, spec in enumerate(specs()):
        pdf = write_pdf(spec.pages)
        variants = [("text", pdf)] + ([("scan", scan(pdf, index, spec.stamp_text))] if spec.scan else [])
        for variant, data in variants:
            name = f"{spec.id}_{variant}"
            (DOCS / f"{name}.pdf").write_bytes(data)
            truth = {"source": "synthetic", "variant": variant, **spec.truth}
            (TRUTH / f"{name}.json").write_text(json.dumps(truth, ensure_ascii=False, indent=2), encoding="utf-8")
            count += 1
    print(f"{count}건 → {DOCS}")


if __name__ == "__main__":
    main()
