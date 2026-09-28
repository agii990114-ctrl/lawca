"""lawca HTTP API. 대화·파일·문서·기한은 PostgreSQL에 저장한다."""

from __future__ import annotations

import csv
import io
import json
import uuid
from collections.abc import Callable, Iterator
from dataclasses import asdict
from datetime import date, datetime, timezone
from typing import Annotated, Any
from urllib.parse import quote

from fastapi import Depends, FastAPI, File, HTTPException, Query, UploadFile
from fastapi.responses import Response, StreamingResponse
from sqlalchemy.orm import Session, sessionmaker

from lawca.agent.graph import GRAPH_VERSION, Deps, ModelsNotConfigured, graph_for
from lawca.agent.llm import ChatModel, Turn
from lawca.api.calendar import deadline_details, deadline_title
from lawca.api.calendar import router as calendar_router
from lawca.api.chat import answer_summary, chat_events, resume_events
from lawca.api.deps import DB, CurrentUser, Lawyer, SessionFactory, Worker
from lawca.api.llm_deps import get_extractor, get_extractor_factory, get_models_factory  # noqa: F401 (테스트가 덮어쓴다)
from lawca.api.documents_api import router as documents_router
from lawca.api.embedding import EmbedderFactory
from lawca.api.library_api import router as library_router
from lawca.api.cases_api import router as cases_router
from lawca.library import UnsupportedLibraryFile, detect_mime
from lawca.library import store as library_store
from lawca.api.users import router as users_router
from lawca.api.records import record_out
from lawca.api.schemas import (
    SERVICE_LABELS,
    ChatRequest,
    ConversationDetail,
    ConversationSummary,
    DeadlineConfirmRequest,
    DeadlineOut,
    DeadlineRecordOut,
    DeadlineRequest,
    DeadlineStatusUpdate,
    DeadlineTermsUpdate,
    DraftOut,
    FileOut,
    JobOut,
    MessageOut,
    PeriodOut,
    ResumeRequest,
)
from lawca.config import Settings, get_settings
from lawca.db import repo
from lawca.db.models import Conversation, Deadline, File as FileRow, Job, Message
from lawca.db.session import get_checkpointer
from lawca.deadlines import (
    CalendarCoverageError,
    DeadlineResult,
    Period,
    Unit,
    compute_designated_deadline,
    compute_statutory_deadline,
    load_rules,
)
from lawca.deadlines.ics import IcsEvent, to_ics
from lawca.extraction.gemini import Extractor

app = FastAPI(title="lawca API", version="0.1.0")

app.include_router(users_router)
app.include_router(calendar_router)
app.include_router(documents_router)
app.include_router(library_router)
app.include_router(cases_router)

UNITS = {u.value: u for u in Unit}




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
    ollama = settings.llm_provider == "ollama"
    return {
        "status": "ok",
        "provider": settings.llm_provider,
        "models": [settings.ollama_model] if ollama else settings.gemini_models,
        "extraction_models": [settings.ollama_model] if ollama else settings.gemini_extraction_models,
        "configured": ollama or bool(settings.gemini_api_key),
    }


@app.get("/api/rules")
def rules(_: CurrentUser) -> list[dict[str, object]]:
    return [
        {**asdict(rule), "period": PeriodOut.of(rule.period).model_dump(), "verified_on": rule.verified_on.isoformat()}
        for rule in load_rules().values()
    ]


# 파일


@app.post("/api/files")
def upload_file(
    file: Annotated[UploadFile, File()],
    _: Worker,
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
def file_content(file_id: str, _: Worker, session: DB) -> Response:
    row = repo.get_file(session, file_id)
    if row is None:
        raise HTTPException(404, "파일을 찾을 수 없습니다.")
    headers = {}
    if row.mime != "application/pdf":
        headers["Content-Disposition"] = f"attachment; filename*=UTF-8''{quote(row.name)}"
    return Response(row.data, media_type=row.mime, headers=headers)


# 대화


@app.get("/api/conversations")
def conversations(user: Worker, session: DB) -> list[ConversationSummary]:
    return [summary_out(c) for c in repo.list_conversations(session, user.id)]


@app.post("/api/conversations")
def create_conversation(user: Worker, session: DB) -> ConversationSummary:
    conversation = repo.create_conversation(session, user.id)
    session.commit()
    return summary_out(conversation)


@app.get("/api/conversations/{conversation_id}")
def conversation(conversation_id: str, user: Worker, session: DB) -> ConversationDetail:
    c = repo.get_conversation(session, conversation_id, user.id)
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


HISTORY_TURNS = 10


def _history(conversation: Conversation) -> list[Turn]:
    """이번 요청 앞의 대화 글. 되묻는 말이나 '그 사건' 같은 지시어를 이해하는 데 쓴다."""
    turns = [
        Turn("user" if m.role == "user" else "model", m.text)
        for m in conversation.messages
        if m.text.strip() and m.state == "done"
    ]
    return turns[-HISTORY_TURNS:]


ExtractorFactory = Annotated[Callable[[], Extractor], Depends(get_extractor_factory)]
ModelsFactory = Annotated[Callable[[], list[ChatModel]], Depends(get_models_factory)]
Checkpointer = Annotated[Any, Depends(get_checkpointer)]


def _respond(
    factory: sessionmaker[Session],
    conversation_id: uuid.UUID,
    job_id: uuid.UUID,
    make_events: Callable[[Deps], Iterator[dict[str, Any]]],
    make_extractor: Callable[[], Extractor],
    make_models: Callable[[], list[ChatModel]],
    actor: str,
    make_embedder: Callable[[], Any] = lambda: None,
) -> StreamingResponse:
    """그래프 이벤트를 SSE로 흘려보내고, 끝나면 답변 메시지와 작업(Job) 상태를 저장한다.

    되묻기로 멈추면 질문을 'question' 카드로 바꿔 보내고 작업을 waiting으로 둔다.
    """

    def stream() -> Iterator[str]:
        reply = _Reply()
        state = "done"
        question: dict[str, Any] | None = None
        with factory() as session:
            session.info["actor"] = actor

            def on_document(result: Any) -> str:
                document = repo.save_document(session, result)
                session.commit()
                return str(document.id)

            deps = Deps(
                session=session,
                today=date.today(),
                get_file=lambda fid: repo.get_file(session, fid),
                make_extractor=make_extractor,
                on_document=on_document,
                make_models=make_models,
                job_id=job_id,
                make_embedder=make_embedder,
            )
            try:
                for event in make_events(deps):
                    if event["type"] == "question":
                        question = event["question"]
                        event = {"type": "card", "card": {"kind": "question", "job_id": str(job_id), **question}}
                    reply.apply(event)
                    yield _sse(event)
            except GeneratorExit:
                state = "stopped"
                raise
            except Exception:
                state = "error"
                raise
            finally:
                session.rollback()
                conversation = session.get(Conversation, conversation_id)
                assert conversation is not None
                repo.add_message(
                    session, conversation, "assistant", reply.text, steps=reply.steps, cards=reply.cards, state=state
                )
                job = session.get(Job, job_id)
                assert job is not None
                waiting = question is not None and state == "done"
                repo.finish_job(session, job, "waiting" if waiting else state, question if waiting else None)
                session.commit()

    return StreamingResponse(
        stream(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}
    )


@app.post("/api/chat")
def chat(
    req: ChatRequest,
    user: Worker,
    factory: SessionFactory,
    make_extractor: ExtractorFactory,
    make_models: ModelsFactory,
    checkpointer: Checkpointer,
    make_embedder: EmbedderFactory,
) -> StreamingResponse:
    """사용자 메시지를 저장하고 답변을 SSE로 흘려보낸다. 답변은 끝나거나 중지되면 저장한다."""
    with factory() as session:
        session.info["actor"] = user.username
        conversation = repo.get_conversation(session, req.conversation_id, user.id)
        if conversation is None:
            raise HTTPException(404, "대화를 찾을 수 없습니다.")
        history = _history(conversation)
        files = [f for f in (repo.get_file(session, fid) for fid in req.file_ids) if f is not None]
        repo.add_message(session, conversation, "user", req.message, files=files)
        job = repo.create_job(session, conversation.id, GRAPH_VERSION)
        session.commit()
        conversation_id, job_id = conversation.id, job.id

    graph = graph_for(checkpointer)
    return _respond(
        factory,
        conversation_id,
        job_id,
        lambda deps: chat_events(req, deps, history, graph, str(job_id)),
        make_extractor,
        make_models,
        user.username,
        make_embedder,
    )


def _own_job(session: Session, job_id: str, user: CurrentUser) -> Job:
    """본인 대화의 작업. 남의 작업은 없는 것으로 본다."""
    job = repo.get_job(session, job_id)
    conversation = session.get(Conversation, job.conversation_id) if job else None
    if job is None or conversation is None or conversation.user_id != user.id:
        raise HTTPException(404, "작업을 찾을 수 없습니다.")
    return job


@app.get("/api/jobs/{job_id}")
def job_status(job_id: str, user: Worker, session: DB) -> JobOut:
    job = _own_job(session, job_id, user)
    return JobOut(id=str(job.id), status=job.status, question=job.question)


@app.post("/api/jobs/{job_id}/resume")
def resume_job(
    job_id: str,
    req: ResumeRequest,
    user: Worker,
    factory: SessionFactory,
    make_extractor: ExtractorFactory,
    make_models: ModelsFactory,
    checkpointer: Checkpointer,
    make_embedder: EmbedderFactory,
) -> StreamingResponse:
    """되묻기에 답하고 멈춘 곳에서 이어간다. 답은 사용자 메시지로 대화에 남긴다."""
    with factory() as session:
        session.info["actor"] = user.username
        job = _own_job(session, job_id, user)
        if job.status != "waiting" or job.question is None:
            raise HTTPException(409, "이미 답했거나 끝난 작업입니다.")
        if job.graph_version != GRAPH_VERSION:
            raise HTTPException(409, "프로그램이 바뀌어 이 작업을 이어갈 수 없습니다. 요청을 다시 보내 주세요.")
        conversation = session.get(Conversation, job.conversation_id)
        assert conversation is not None
        repo.add_message(session, conversation, "user", answer_summary(job.question, req.answers))
        repo.finish_job(session, job, "running")
        session.commit()
        conversation_id, parsed_job_id = conversation.id, job.id

    graph = graph_for(checkpointer)
    return _respond(
        factory,
        conversation_id,
        parsed_job_id,
        lambda deps: resume_events(req.answers, deps, graph, str(parsed_job_id)),
        make_extractor,
        make_models,
        user.username,
        make_embedder,
    )


# 기한


def _compute(rule_id: str | None, period: PeriodOut | None, event_date: date, deemed: bool) -> DeadlineResult:
    """법정 기간(rule_id) 또는 문서가 정한 기간(period) 중 하나로 기한을 계산한다. 잘못된 입력은 HTTP 오류로 바꾼다."""
    if (rule_id is None) == (period is None):
        raise HTTPException(422, "rule_id와 period 중 하나만 보내야 합니다.")
    try:
        if rule_id is not None:
            return compute_statutory_deadline(rule_id, event_date, deemed_electronic_service=deemed)
        assert period is not None
        return compute_designated_deadline(
            event_date, Period(period.amount, UNITS[period.unit]), deemed_electronic_service=deemed
        )
    except KeyError as exc:
        raise HTTPException(404, str(exc.args[0])) from exc
    except (CalendarCoverageError, ValueError) as exc:
        raise HTTPException(422, str(exc)) from exc


def _one(session: Session, deadline_id: uuid.UUID) -> DeadlineRecordOut:
    """관계(문서·파일·사건)를 함께 다시 읽어 응답으로 바꾼다."""
    saved = repo.get_deadline(session, deadline_id)
    assert saved is not None
    return record_out(saved)


@app.post("/api/deadlines")
def deadline(req: DeadlineRequest, _: Worker) -> DeadlineOut:
    """기한을 계산만 한다. 저장하지 않는다."""
    return DeadlineOut.of(_compute(req.rule_id, req.period, req.event_date, req.deemed_electronic_service))


@app.post("/api/deadlines/confirm", status_code=201)
def confirm_deadline(req: DeadlineConfirmRequest, _: Worker, session: DB) -> DeadlineRecordOut:
    """기한을 확정해 저장한다. 화면이 계산한 날짜가 아니라 서버가 다시 계산한 값을 저장한다."""
    document = repo.find_document(session, req.document_id, req.file_id)
    if document is None:
        raise HTTPException(404, "문서를 찾을 수 없습니다.")
    _no_conflict(session, repo.deadline_key(req.rule_id, document.document_type), document.case_id, document.id)
    result = _compute(req.rule_id, req.period, req.event_date, req.service_kind == "electronic_deemed")
    saved = repo.create_deadline(
        session,
        document,
        label=req.label,
        kind="statutory" if req.rule_id else "designated",
        rule_id=req.rule_id,
        event_date=req.event_date,
        service_kind=req.service_kind,
        result=result,
    )
    session.commit()
    return _one(session, saved.id)


def _no_conflict(
    session: Session, key: str, case_id: uuid.UUID | None, document_id: uuid.UUID, exclude_id: uuid.UUID | None = None
) -> None:
    """같은 사건·같은 종류로 진행 중인 기한이 있으면 409. 기한은 새로 만들지 말고 기한 목록에서 고친다."""
    existing = repo.open_conflict(session, key=key, case_id=case_id, document_id=document_id, exclude_id=exclude_id)
    if existing is not None:
        where = existing.case.case_number if existing.case else "이 문서"
        raise HTTPException(
            409,
            f"{where}에 이미 진행 중인 {existing.label}(만료 {existing.deadline.isoformat()}, 확정 {existing.confirmed_by})이 "
            "있습니다. 고치려면 기한 목록에서 수정하세요.",
        )


def _deadline(session: Session, deadline_id: str) -> Deadline:
    parsed = repo._parse_id(deadline_id)
    deadline = repo.get_deadline(session, parsed) if parsed else None
    if deadline is None or deadline.status == "deleted":
        raise HTTPException(404, "기한을 찾을 수 없습니다.")
    return deadline


def _statuses(status: str | None) -> list[str] | None:
    return [s for s in status.split(",") if s] if status else None


@app.get("/api/deadlines")
def deadlines(
    _: Worker,
    session: DB,
    status: Annotated[str | None, Query(description="쉼표로 구분. 예: confirmed,done")] = None,
    document_id: str | None = None,
    case_number: str | None = None,
) -> list[DeadlineRecordOut]:
    rows = repo.list_deadlines(session, _statuses(status), document_id, case_number=case_number)
    return [record_out(d) for d in rows]


@app.patch("/api/deadlines/{deadline_id}")
def update_deadline(deadline_id: str, req: DeadlineStatusUpdate, _: Worker, session: DB) -> DeadlineRecordOut:
    """완료·취소·복원. 복원(진행 중으로 되돌리기)은 같은 사건에 같은 종류로 진행 중인 기한이 없을 때만 된다."""
    deadline = _deadline(session, deadline_id)
    if req.status == "confirmed" and deadline.status != "confirmed":
        _no_conflict(session, repo.key_of(deadline), deadline.case_id, deadline.document_id, exclude_id=deadline.id)
    saved = repo.set_deadline_status(session, deadline_id, req.status)
    assert saved is not None
    session.commit()
    return _one(session, saved.id)


@app.patch("/api/deadlines/{deadline_id}/terms")
def update_deadline_terms(deadline_id: str, req: DeadlineTermsUpdate, _: Worker, session: DB) -> DeadlineRecordOut:
    """진행 중인 기한의 송달일·송달 유형·기간을 고친다. 새 기한을 만들지 않고 이 기한을 서버가 다시 계산한다."""
    deadline = _deadline(session, deadline_id)
    if deadline.status != "confirmed":
        raise HTTPException(409, "진행 중인 기한만 고칠 수 있습니다. 먼저 복원하세요.")
    if deadline.rule_id is None:
        period = req.period or PeriodOut(amount=deadline.period_amount, unit=deadline.period_unit, label="")  # type: ignore[arg-type]
        result = _compute(None, period, req.event_date, req.service_kind == "electronic_deemed")
    else:
        if req.period is not None:
            raise HTTPException(422, "법정 기간은 기간을 바꿀 수 없습니다.")
        result = _compute(deadline.rule_id, None, req.event_date, req.service_kind == "electronic_deemed")
    repo.update_deadline_terms(
        session,
        deadline,
        label=(req.label or deadline.label).strip(),
        event_date=req.event_date,
        service_kind=req.service_kind,
        period_amount=result.period.amount,
        period_unit=result.period.unit.value,
        result=result,
    )
    session.commit()
    return _one(session, deadline.id)


@app.delete("/api/deadlines/{deadline_id}", status_code=204)
def delete_deadline(deadline_id: str, _: Lawyer, session: DB) -> None:
    """취소한 기한을 지운다(변호사만). 기록은 남기고 어디에도 보이지 않게 한다."""
    deadline = _deadline(session, deadline_id)
    if deadline.status != "cancelled":
        raise HTTPException(409, "취소한 기한만 삭제할 수 있습니다.")
    repo.delete_deadline(session, deadline)
    session.commit()


@app.get("/api/deadlines/export.ics")
def export_ics(_: Worker, session: DB) -> Response:
    """확정 상태인 기한을 캘린더 파일로 내보낸다. 완료·취소한 기한은 넣지 않는다."""
    events = [
        IcsEvent(uid=f"{d.id}@lawca", day=d.deadline, summary=deadline_title(d), description="\n".join(deadline_details(d)))
        for d in repo.list_deadlines(session, ["confirmed"])
    ]
    return Response(
        to_ics(events, datetime.now(timezone.utc)).encode("utf-8"),
        media_type="text/calendar; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="lawca-deadlines.ics"'},
    )


@app.get("/api/deadlines/export.csv")
def export_csv(_: Worker, session: DB) -> Response:
    """취소하지 않은 기한을 엑셀에서 바로 열 수 있는 CSV(UTF-8 BOM)로 내보낸다."""
    status_labels = {"confirmed": "확정", "done": "완료", "cancelled": "취소"}
    weekdays = "월화수목금토일"
    out = io.StringIO()
    writer = csv.writer(out, lineterminator="\r\n")
    writer.writerow(
        ["만료일", "요일", "기한", "상태", "사건번호", "법원", "사건명", "문서", "송달일", "송달 유형", "기간", "기산일", "근거", "주의"]
    )
    for d in repo.list_deadlines(session, ["confirmed", "done"]):
        writer.writerow(
            [
                d.deadline.isoformat(),
                weekdays[d.deadline.weekday()],
                d.label,
                status_labels[d.status],
                d.case.case_number if d.case else "",
                d.case.court if d.case else "",
                d.case.case_name if d.case else "",
                f"{d.document.document_type} ({d.document.file.name})",
                d.event_date.isoformat(),
                SERVICE_LABELS.get(d.service_kind, d.service_kind),
                f"{d.period_amount}{d.period_unit}",
                d.count_start.isoformat(),
                ", ".join(d.basis),
                " / ".join(d.warnings),
            ]
        )
    return Response(
        ("﻿" + out.getvalue()).encode("utf-8"),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="lawca-deadlines.csv"'},
    )


# 서식 초안


def draft_out(d: Any) -> DraftOut:
    return DraftOut(
        id=str(d.id),
        form_id=d.form_id,
        created_by=d.created_by,
        reviewed_by=d.reviewed_by,
        reviewed_at=d.reviewed_at,
        final_file_id=str(d.final_file_id) if d.final_file_id else None,
        final_filename=d.final_file.name if d.final_file else None,
        final_uploaded_by=d.final_uploaded_by,
        final_uploaded_at=d.final_uploaded_at,
    )


@app.get("/api/drafts/{draft_id}")
def get_draft(draft_id: str, _: Worker, session: DB) -> DraftOut:
    draft = repo.get_draft(session, draft_id)
    if draft is None:
        raise HTTPException(404, "초안을 찾을 수 없습니다.")
    return draft_out(draft)


@app.post("/api/drafts/{draft_id}/review")
def review_draft(draft_id: str, _: Lawyer, session: DB) -> DraftOut:
    """변호사가 초안 검토를 마쳤다고 표시한다."""
    draft = repo.get_draft(session, draft_id)
    if draft is None:
        raise HTTPException(404, "초안을 찾을 수 없습니다.")
    repo.review_draft(session, draft)
    session.commit()
    return draft_out(draft)


@app.post("/api/drafts/{draft_id}/final")
def upload_final(
    draft_id: str,
    file: Annotated[UploadFile, File()],
    _: Worker,
    session: DB,
    make_embedder: EmbedderFactory,
    settings: Annotated[Settings, Depends(get_settings)],
) -> DraftOut:
    """초안을 고쳐 실제로 낸 최종본(DOCX·PDF)을 올린다. 자료실에는 초안 대신 최종본이 들어간다."""
    draft = repo.get_draft(session, draft_id)
    if draft is None:
        raise HTTPException(404, "초안을 찾을 수 없습니다.")
    limit = settings.max_upload_mb * 1024 * 1024
    data = file.file.read(limit + 1)
    if len(data) > limit:
        raise HTTPException(413, f"{settings.max_upload_mb}MB를 넘는 파일은 올릴 수 없습니다.")
    name = file.filename or "최종본"
    try:
        mime = detect_mime(name, data)
    except UnsupportedLibraryFile as exc:
        raise HTTPException(415, str(exc)) from exc
    if mime == "text/plain":
        raise HTTPException(415, "최종본은 DOCX나 PDF로 올려 주세요.")
    stored = repo.save_file(session, name, data, mime=mime)
    library_store.index_final(session, draft, stored, mime, make_embedder())
    repo.set_final(session, draft, stored)
    session.commit()
    saved = repo.get_draft(session, draft_id)
    assert saved is not None
    return draft_out(saved)
