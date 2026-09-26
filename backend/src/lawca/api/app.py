"""lawca HTTP API.

파일은 아직 메모리에만 보관한다. 서버를 다시 띄우면 사라진다(DB는 다음 단계).
"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterator
from dataclasses import asdict
from datetime import date
from typing import Annotated

from fastapi import Depends, FastAPI, File, HTTPException, UploadFile
from fastapi.responses import Response, StreamingResponse

from lawca.api.chat import chat_events
from lawca.api.files import FileStore, UnsupportedFileError
from lawca.api.schemas import ChatRequest, DeadlineOut, DeadlineRequest, FileOut, PeriodOut
from lawca.config import Settings, get_settings
from lawca.deadlines import (
    CalendarCoverageError,
    Period,
    Unit,
    compute_designated_deadline,
    compute_statutory_deadline,
    load_rules,
)
from lawca.extraction.gemini import Extractor, GeminiExtractor

app = FastAPI(title="lawca API", version="0.1.0")

file_store = FileStore()


def get_files() -> FileStore:
    return file_store


def get_extractor(settings: Annotated[Settings, Depends(get_settings)]) -> Extractor:
    if not settings.gemini_api_key:
        raise HTTPException(503, "Gemini API 키가 설정되지 않았습니다. 레포 루트 .env에 GEMINI_API를 넣으세요.")
    return GeminiExtractor(settings.gemini_api_key, settings.gemini_models)


def get_extractor_factory(settings: Annotated[Settings, Depends(get_settings)]) -> Callable[[], Extractor]:
    """추출기를 만드는 함수를 넘긴다. 글만 보낸 요청은 키가 없어도 응답하도록 필요할 때 만든다."""
    return lambda: get_extractor(settings)


@app.get("/api/health")
def health(settings: Annotated[Settings, Depends(get_settings)]) -> dict[str, object]:
    return {
        "status": "ok",
        "models": settings.gemini_models,
        "gemini_configured": bool(settings.gemini_api_key),
    }


@app.get("/api/rules")
def rules() -> list[dict[str, object]]:
    return [
        {**asdict(rule), "period": PeriodOut.of(rule.period).model_dump(), "verified_on": rule.verified_on.isoformat()}
        for rule in load_rules().values()
    ]


@app.post("/api/files")
def upload_file(
    file: Annotated[UploadFile, File()],
    files: Annotated[FileStore, Depends(get_files)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> FileOut:
    limit = settings.max_upload_mb * 1024 * 1024
    data = file.file.read(limit + 1)
    if len(data) > limit:
        raise HTTPException(413, f"{settings.max_upload_mb}MB를 넘는 파일은 올릴 수 없습니다.")
    try:
        stored = files.put(file.filename or "document.pdf", data)
    except UnsupportedFileError as exc:
        raise HTTPException(415, str(exc)) from exc
    return FileOut(id=stored.id, name=stored.name, size=len(stored.data), mime=stored.mime, pages=stored.pages)


@app.get("/api/files/{file_id}/content")
def file_content(file_id: str, files: Annotated[FileStore, Depends(get_files)]) -> Response:
    stored = files.get(file_id)
    if stored is None:
        raise HTTPException(404, "파일을 찾을 수 없습니다. 서버를 다시 띄우면 파일이 사라집니다.")
    return Response(stored.data, media_type=stored.mime)


def _sse(events: Iterator[dict[str, object]]) -> Iterator[str]:
    for event in events:
        yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"


@app.post("/api/chat")
def chat(
    req: ChatRequest,
    files: Annotated[FileStore, Depends(get_files)],
    make_extractor: Annotated[Callable[[], Extractor], Depends(get_extractor_factory)],
) -> StreamingResponse:
    events = chat_events(req, files, make_extractor, date.today())
    return StreamingResponse(
        _sse(events), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}
    )


@app.post("/api/deadlines")
def deadline(req: DeadlineRequest) -> DeadlineOut:
    if (req.rule_id is None) == (req.period is None):
        raise HTTPException(422, "rule_id와 period 중 하나만 보내야 합니다.")
    try:
        if req.rule_id is not None:
            result = compute_statutory_deadline(
                req.rule_id, req.event_date, deemed_electronic_service=req.deemed_electronic_service
            )
        else:
            assert req.period is not None
            units = {u.value: u for u in Unit}
            result = compute_designated_deadline(
                req.event_date,
                Period(req.period.amount, units[req.period.unit]),
                deemed_electronic_service=req.deemed_electronic_service,
            )
    except KeyError as exc:
        raise HTTPException(404, str(exc.args[0])) from exc
    except (CalendarCoverageError, ValueError) as exc:
        raise HTTPException(422, str(exc)) from exc
    return DeadlineOut.of(result)
