"""lawca HTTP API. 대화·파일·문서는 PostgreSQL에 저장한다."""

from __future__ import annotations

import json
from collections.abc import Callable, Iterator
from dataclasses import asdict
from datetime import date
from typing import Annotated, Any

from fastapi import Depends, FastAPI, File, HTTPException, UploadFile
from fastapi.responses import Response, StreamingResponse
from sqlalchemy.orm import Session, sessionmaker

from lawca.api.chat import chat_events
from lawca.api.schemas import (
    ChatRequest,
    ConversationDetail,
    ConversationSummary,
    DeadlineOut,
    DeadlineRequest,
    FileOut,
    MessageOut,
    PeriodOut,
)
from lawca.config import Settings, get_settings
from lawca.db import repo
from lawca.db.models import Conversation, File as FileRow, Message
from lawca.db.session import get_session_factory
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

SessionFactory = Annotated[sessionmaker[Session], Depends(get_session_factory)]


def get_session(factory: SessionFactory) -> Iterator[Session]:
    with factory() as session:
        yield session


DB = Annotated[Session, Depends(get_session)]


def get_extractor(settings: Annotated[Settings, Depends(get_settings)]) -> Extractor:
    if not settings.gemini_api_key:
        raise HTTPException(503, "Gemini API 키가 설정되지 않았습니다. 레포 루트 .env에 GEMINI_API를 넣으세요.")
    return GeminiExtractor(settings.gemini_api_key, settings.gemini_models)


def get_extractor_factory(settings: Annotated[Settings, Depends(get_settings)]) -> Callable[[], Extractor]:
    """추출기를 만드는 함수를 넘긴다. 글만 보낸 요청은 키가 없어도 응답하도록 필요할 때 만든다."""
    return lambda: get_extractor(settings)


def file_out(row: FileRow) -> FileOut:
    return FileOut(id=str(row.id), name=row.name, size=row.size, mime=row.mime, pages=row.pages)


def summary_out(c: Conversation) -> ConversationSummary:
    return ConversationSummary(id=str(c.id), title=c.title, updated_at=c.updated_at)


def message_out(m: Message) -> MessageOut:
    return MessageOut(
        id=str(m.id),
        role=m.role,  # type: ignore[arg-type]
        text=m.text,
        attachments=[file_out(a.file) for a in m.attachments],
        steps=m.steps,
        cards=m.cards,
        state=m.state,
        created_at=m.created_at,
    )


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


# 파일


@app.post("/api/files")
def upload_file(
    file: Annotated[UploadFile, File()],
    session: DB,
    settings: Annotated[Settings, Depends(get_settings)],
) -> FileOut:
    limit = settings.max_upload_mb * 1024 * 1024
    data = file.file.read(limit + 1)
    if len(data) > limit:
        raise HTTPException(413, f"{settings.max_upload_mb}MB를 넘는 파일은 올릴 수 없습니다.")
    try:
        row = repo.save_file(session, file.filename or "document.pdf", data)
    except repo.UnsupportedFileError as exc:
        raise HTTPException(415, str(exc)) from exc
    session.commit()
    return file_out(row)


@app.get("/api/files/{file_id}/content")
def file_content(file_id: str, session: DB) -> Response:
    row = repo.get_file(session, file_id)
    if row is None:
        raise HTTPException(404, "파일을 찾을 수 없습니다.")
    return Response(row.data, media_type=row.mime)


# 대화


@app.get("/api/conversations")
def conversations(session: DB) -> list[ConversationSummary]:
    return [summary_out(c) for c in repo.list_conversations(session)]


@app.post("/api/conversations")
def create_conversation(session: DB) -> ConversationSummary:
    conversation = repo.create_conversation(session)
    session.commit()
    return summary_out(conversation)


@app.get("/api/conversations/{conversation_id}")
def conversation(conversation_id: str, session: DB) -> ConversationDetail:
    c = repo.get_conversation(session, conversation_id)
    if c is None:
        raise HTTPException(404, "대화를 찾을 수 없습니다.")
    return ConversationDetail(**summary_out(c).model_dump(), messages=[message_out(m) for m in c.messages])


class _Reply:
    """스트리밍한 이벤트를 모아 저장할 답변 메시지를 만든다(프론트엔드의 applyEvent와 같은 규칙)."""

    def __init__(self) -> None:
        self.text = ""
        self.steps: list[dict[str, Any]] = []
        self.cards: list[dict[str, Any]] = []

    def apply(self, event: dict[str, Any]) -> None:
        if event["type"] == "status":
            step = {k: event[k] for k in ("id", "label", "state")}
            self.steps = [step if s["id"] == step["id"] else s for s in self.steps]
            if step not in self.steps:
                self.steps.append(step)
        elif event["type"] == "text":
            self.text += event["delta"]
        elif event["type"] == "card":
            self.cards.append(event["card"])


def _sse(event: dict[str, Any]) -> str:
    return f"data: {json.dumps(event, ensure_ascii=False)}\n\n"


@app.post("/api/chat")
def chat(
    req: ChatRequest,
    factory: SessionFactory,
    make_extractor: Annotated[Callable[[], Extractor], Depends(get_extractor_factory)],
) -> StreamingResponse:
    """사용자 메시지를 저장하고 답변을 SSE로 흘려보낸다. 답변은 끝나거나 중지되면 저장한다."""
    with factory() as session:
        conversation = repo.get_conversation(session, req.conversation_id)
        if conversation is None:
            raise HTTPException(404, "대화를 찾을 수 없습니다.")
        files = [f for f in (repo.get_file(session, fid) for fid in req.file_ids) if f is not None]
        repo.add_message(session, conversation, "user", req.message, files=files)
        session.commit()

    def stream() -> Iterator[str]:
        reply = _Reply()
        state = "done"
        with factory() as session:
            conversation = repo.get_conversation(session, req.conversation_id)
            assert conversation is not None

            def on_document(result: Any) -> None:
                repo.save_document(session, result)
                session.commit()

            try:
                for event in chat_events(
                    req, lambda fid: repo.get_file(session, fid), make_extractor, date.today(), on_document
                ):
                    reply.apply(event)
                    yield _sse(event)
            except GeneratorExit:
                state = "stopped"
                raise
            except Exception:
                state = "error"
                raise
            finally:
                repo.add_message(
                    session, conversation, "assistant", reply.text, steps=reply.steps, cards=reply.cards, state=state
                )
                session.commit()

    return StreamingResponse(
        stream(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}
    )


# 기한


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
