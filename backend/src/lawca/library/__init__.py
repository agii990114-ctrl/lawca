"""자료실(RAG): 과거 서면·서식과 lawca가 다룬 문서를 잘라 저장하고, 키워드와 의미로 함께 찾는다.

- 글 뽑기: PDF(텍스트 층), DOCX, HWP(5.0), HWPX, TXT. 스캔본 PDF는 아직 읽지 못한다(글이 없으면 색인하지 않는다).
- 자르기: 문단을 이어 붙여 CHUNK_CHARS 안팎으로 자르고, 앞 조각 끝을 조금 겹친다.
- 임베딩: 로컬 Ollama의 bge-m3(1024차원). 문서가 밖으로 나가지 않는다.
  Ollama가 꺼져 있으면 임베딩 없이 저장하고 키워드 검색만 된다(나중에 다시 색인).
"""

from __future__ import annotations

import io
import re
from dataclasses import dataclass
from typing import Protocol

import httpx

EMBED_DIM = 1024
CHUNK_CHARS = 700
OVERLAP_CHARS = 120

KINDS = {
    "filing": "서면",
    "form": "서식",
    "court": "법원 문서",
    "draft": "lawca 초안",
    "other": "기타",
}

DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
TEXT_MIME = "text/plain"
PDF_MIME = "application/pdf"
HWPX_MIME = "application/vnd.hancom.hwpx"
HWP_MIME = "application/x-hwp"
OLE_MAGIC = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
HWPX_MAX_BYTES = 30 * 1024 * 1024
"""HWPX를 풀었을 때 XML 크기 상한(압축 폭탄 방지)."""


class UnsupportedLibraryFile(ValueError):
    pass


def detect_mime(name: str, data: bytes) -> str:
    lower = name.lower()
    if data.startswith(b"%PDF"):
        return PDF_MIME
    if data.startswith(b"PK") and lower.endswith(".docx"):
        return DOCX_MIME
    if lower.endswith((".txt", ".md")):
        return TEXT_MIME
    if data.startswith(b"PK") and lower.endswith(".hwpx"):
        return HWPX_MIME
    if data.startswith(OLE_MAGIC) and lower.endswith(".hwp"):
        return HWP_MIME
    raise UnsupportedLibraryFile("PDF, DOCX, HWP, HWPX, TXT 파일만 올릴 수 있습니다.")


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _hwpx_paragraphs(data: bytes) -> list[str]:
    """HWPX(압축 XML)의 본문 문단. 구역(section) 순서대로, 문단(p)마다 글자(t)를 이어 붙인다. 표 안의 문단도 각각 문단으로 본다."""
    import zipfile
    from xml.etree import ElementTree

    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as exc:
        raise UnsupportedLibraryFile("HWPX 파일을 열 수 없습니다(손상되었을 수 있습니다).") from exc
    sections = sorted(
        (i for i in archive.infolist() if re.fullmatch(r"Contents/section\d+\.xml", i.filename)),
        key=lambda i: int(re.findall(r"\d+", i.filename)[0]),
    )
    if not sections or sum(i.file_size for i in sections) > HWPX_MAX_BYTES:
        raise UnsupportedLibraryFile("HWPX 본문을 읽을 수 없습니다.")
    lines: list[str] = []
    for info in sections:
        root = ElementTree.fromstring(archive.read(info))
        stack: list[list[str]] = []

        def walk(node: ElementTree.Element) -> None:
            name = _local(node.tag)
            if name == "p":
                stack.append([])
            elif name == "t" and stack:
                stack[-1].append("".join(node.itertext()))
            elif name == "lineBreak" and stack:
                stack[-1].append("\n")
            for child in node:
                walk(child)
            if name == "p":
                text = "".join(stack.pop()).strip()
                if text:
                    lines.append(text)

        walk(root)
    return lines


HWPTAG_PARA_TEXT = 67
"""HWP 5.0 레코드 태그(HWPTAG_BEGIN 16 + 51): 문단의 글."""
_PLAIN_CONTROLS = {0, 10, 13, *range(24, 32)}
"""HWP 문단 글에서 글자 하나(2바이트)만 차지하는 제어 문자. 나머지 1~31은 16바이트(글자 8개) 제어 블록이다."""


def hwp_section_paragraphs(stream: bytes) -> list[str]:
    """HWP 5.0 본문 구역(BodyText/SectionN, 압축을 푼 것)의 문단 글. 레코드는 32비트 머리(태그 10·수준 10·크기 12비트)로 시작한다."""
    import struct

    lines: list[str] = []
    pos = 0
    while pos + 4 <= len(stream):
        (head,) = struct.unpack_from("<I", stream, pos)
        pos += 4
        tag, size = head & 0x3FF, head >> 20
        if size == 0xFFF:
            if pos + 4 > len(stream):
                break
            (size,) = struct.unpack_from("<I", stream, pos)
            pos += 4
        payload = stream[pos : pos + size]
        pos += size
        if tag != HWPTAG_PARA_TEXT:
            continue
        chars: list[str] = []
        i = 0
        while i + 2 <= len(payload):
            (code,) = struct.unpack_from("<H", payload, i)
            if code < 32 and code not in _PLAIN_CONTROLS:
                chars.append(" " if code == 9 else "")
                i += 16
                continue
            i += 2
            if code == 10:
                chars.append("\n")
            elif code in (0, 13) or 24 <= code < 32:
                continue
            else:
                chars.append(chr(code))
        text = "".join(chars).strip()
        if text:
            lines.append(text)
    return lines


def _hwp_paragraphs(data: bytes) -> list[str]:
    """예전 형식 HWP(5.0, OLE 묶음 파일)의 본문 문단. 암호가 걸렸거나 배포용(글을 막은) 문서는 읽지 않는다."""
    import olefile
    import zlib

    try:
        ole = olefile.OleFileIO(io.BytesIO(data))
    except Exception as exc:  # noqa: BLE001 - 손상·다른 형식
        raise UnsupportedLibraryFile("HWP 파일을 열 수 없습니다(손상되었거나 HWP 5.0이 아닙니다).") from exc
    with ole:
        if not ole.exists("FileHeader"):
            raise UnsupportedLibraryFile("HWP 5.0 문서가 아닙니다.")
        flags = int.from_bytes(ole.openstream("FileHeader").read()[36:40], "little")
        if flags & 0b10:
            raise UnsupportedLibraryFile("암호가 걸린 HWP는 읽을 수 없습니다. 암호를 풀고 저장해 올려 주세요.")
        if flags & 0b100:
            raise UnsupportedLibraryFile("배포용 HWP는 글을 읽을 수 없습니다. PDF나 HWPX로 저장해 올려 주세요.")
        sections = sorted(
            (e for e in ole.listdir() if len(e) == 2 and e[0] == "BodyText" and re.fullmatch(r"Section\d+", e[1])),
            key=lambda e: int(e[1][7:]),
        )
        if not sections:
            raise UnsupportedLibraryFile("HWP 본문을 찾을 수 없습니다.")
        lines: list[str] = []
        for entry in sections:
            raw = ole.openstream(entry).read()
            if flags & 0b1:
                try:
                    raw = zlib.decompressobj(-15).decompress(raw, HWPX_MAX_BYTES)
                except zlib.error as exc:
                    raise UnsupportedLibraryFile("HWP 본문이 손상되었습니다.") from exc
            lines.extend(hwp_section_paragraphs(raw))
        return lines


def extract_pages(data: bytes, mime: str) -> list[str]:
    """쪽마다 글을 뽑는다. DOCX·TXT는 한 쪽으로 본다."""
    if mime == PDF_MIME:
        from lawca.extraction.validate import pdf_text_pages

        return pdf_text_pages(data)
    if mime == DOCX_MIME:
        from docx import Document

        doc = Document(io.BytesIO(data))
        lines = [p.text for p in doc.paragraphs]
        for table in doc.tables:
            for row in table.rows:
                lines.append(" | ".join(cell.text.strip() for cell in row.cells))
        return ["\n".join(lines)]
    if mime == HWPX_MIME:
        return ["\n".join(_hwpx_paragraphs(data))]
    if mime == HWP_MIME:
        return ["\n".join(_hwp_paragraphs(data))]
    if mime == TEXT_MIME:
        for encoding in ("utf-8-sig", "cp949"):
            try:
                return [data.decode(encoding)]
            except UnicodeDecodeError:
                continue
        raise UnsupportedLibraryFile("글자 인코딩을 알 수 없습니다(UTF-8 또는 CP949로 저장해 주세요).")
    raise UnsupportedLibraryFile(f"읽을 수 없는 형식입니다: {mime}")


@dataclass(frozen=True)
class Piece:
    page: int | None
    text: str


def _clean(text: str) -> str:
    text = text.replace(" ", " ")
    text = re.sub(r"[ \t]+", " ", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def chunk_pages(pages: list[str], size: int = CHUNK_CHARS, overlap: int = OVERLAP_CHARS) -> list[Piece]:
    """문단 단위로 이어 붙여 size 안팎의 조각으로 자른다. 긴 문단은 문장·글자 단위로 나눈다."""
    pieces: list[Piece] = []
    for number, page in enumerate(pages, start=1):
        paragraphs = [p.strip() for p in re.split(r"\n\s*\n|\n", _clean(page)) if p.strip()]
        current = ""
        for paragraph in paragraphs:
            parts = [paragraph]
            if len(paragraph) > size:
                parts = [paragraph[i : i + size] for i in range(0, len(paragraph), size - overlap)]
            for part in parts:
                if current and len(current) + len(part) + 1 > size:
                    pieces.append(Piece(number if len(pages) > 1 else None, current))
                    current = current[-overlap:] + "\n" + part
                else:
                    current = f"{current}\n{part}" if current else part
        if current.strip():
            pieces.append(Piece(number if len(pages) > 1 else None, current))
    return pieces


class Embedder(Protocol):
    model: str

    def embed(self, texts: list[str]) -> list[list[float]]: ...


class EmbeddingUnavailable(RuntimeError):
    pass


class OllamaEmbedder:
    """로컬 Ollama의 /api/embed. 한 번에 여러 조각을 보낸다."""

    def __init__(self, base_url: str, model: str, num_gpu: int | None = None, batch: int = 16,
                 transport: httpx.BaseTransport | None = None) -> None:
        self.model = model
        self._batch = batch
        self._num_gpu = num_gpu
        self._client = httpx.Client(base_url=base_url, timeout=300, transport=transport)

    def embed(self, texts: list[str]) -> list[list[float]]:
        out: list[list[float]] = []
        for i in range(0, len(texts), self._batch):
            body: dict[str, object] = {"model": self.model, "input": texts[i : i + self._batch]}
            if self._num_gpu is not None:
                body["options"] = {"num_gpu": self._num_gpu}
            try:
                res = self._client.post("/api/embed", json=body)
            except httpx.HTTPError as exc:
                raise EmbeddingUnavailable(f"Ollama에 연결하지 못했습니다: {exc}") from exc
            if res.status_code != 200:
                raise EmbeddingUnavailable(f"임베딩 실패({res.status_code}): {res.text[:200]}")
            vectors = res.json().get("embeddings") or []
            if any(len(v) != EMBED_DIM for v in vectors):
                raise EmbeddingUnavailable(f"{self.model}의 차원이 {EMBED_DIM}이 아닙니다.")
            out += vectors
        return out
