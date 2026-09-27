"""자료실(RAG): 과거 서면·서식과 lawca가 다룬 문서를 잘라 저장하고, 키워드와 의미로 함께 찾는다.

- 글 뽑기: PDF(텍스트 층), DOCX, TXT. 스캔본 PDF와 HWP는 아직 읽지 못한다(글이 없으면 색인하지 않는다).
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
    if lower.endswith((".hwp", ".hwpx")):
        raise UnsupportedLibraryFile("HWP·HWPX는 아직 읽지 못합니다. PDF나 DOCX로 저장해 올려 주세요.")
    raise UnsupportedLibraryFile("PDF, DOCX, TXT 파일만 올릴 수 있습니다.")


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
