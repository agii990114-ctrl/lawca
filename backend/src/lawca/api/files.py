"""첨부 파일 보관소. 지금은 메모리에만 둔다(서버를 다시 띄우면 사라짐, DB는 다음 단계)."""

from __future__ import annotations

import io
import uuid
from dataclasses import dataclass

from pypdf import PdfReader
from pypdf.errors import PdfReadError

PDF_MIME = "application/pdf"


class UnsupportedFileError(ValueError):
    pass


@dataclass(frozen=True)
class StoredFile:
    id: str
    name: str
    mime: str
    data: bytes
    pages: int | None


def count_pages(pdf: bytes) -> int | None:
    try:
        return len(PdfReader(io.BytesIO(pdf)).pages)
    except (PdfReadError, ValueError, OSError):
        return None


class FileStore:
    def __init__(self) -> None:
        self._files: dict[str, StoredFile] = {}

    def put(self, name: str, data: bytes) -> StoredFile:
        if not data.startswith(b"%PDF"):
            raise UnsupportedFileError("지금은 PDF 파일만 올릴 수 있습니다.")
        stored = StoredFile(id=uuid.uuid4().hex, name=name, mime=PDF_MIME, data=data, pages=count_pages(data))
        self._files[stored.id] = stored
        return stored

    def get(self, file_id: str) -> StoredFile | None:
        return self._files.get(file_id)
