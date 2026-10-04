import asyncio
import json

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse
from sqlalchemy import select, update

from backend.app.access.dependencies import Db, WorkspaceAccess
from backend.app.access.models import Session
from backend.app.access.policy import get_access
from backend.app.assistant.models import Conversation, Message, QueryRun
from backend.app.assistant.schemas import ConversationCreate, QuestionCreate
from backend.app.assistant.service import (
    ACTIVE,
    TERMINAL,
    append_event,
    conversation_payload,
    create_run,
    ensure_run_scope,
    get_conversation,
    get_run,
    get_readable_run,
    run_payload,
)
from backend.app.infrastructure.database import aware, utcnow
from backend.app.infrastructure.errors import AppError
from backend.app.infrastructure.security import token_hash
from backend.app.jobs.models import Job, RunEvent


router = APIRouter(prefix="/workspaces/{workspace_id}")


@router.get("/conversations")
async def conversations(access: WorkspaceAccess, db: Db):
    access.require("assistant:use")
    records = (
        await db.scalars(
            select(Conversation)
            .where(
                Conversation.workspace_id == access.workspace_id,
                Conversation.user_id == access.user_id,
            )
            .order_by(Conversation.updated_at.desc())
            .limit(100)
        )
    ).all()
    visible = []
    for item in records:
        first = await db.scalar(
            select(QueryRun)
            .where(QueryRun.conversation_id == item.id)
            .order_by(QueryRun.created_at)
            .limit(1)
        )
        payload = conversation_payload(item)
        if first:
            try:
                await ensure_run_scope(db, first, access)
            except AppError:
                payload["title"] = "Недоступный диалог"
        visible.append(payload)
    return visible


@router.post("/conversations", status_code=201)
async def create_conversation(body: ConversationCreate, access: WorkspaceAccess, db: Db):
    access.require("assistant:use")
    conversation = Conversation(
        workspace_id=access.workspace_id, user_id=access.user_id, title=body.title
    )
    db.add(conversation)
    await db.flush()
    return conversation_payload(conversation)


@router.patch("/conversations/{conversation_id}")
async def rename_conversation(
    conversation_id: str, body: ConversationCreate, access: WorkspaceAccess, db: Db
):
    record = await get_conversation(db, conversation_id, access)
    changed = await db.execute(
        update(Conversation)
        .where(Conversation.id == record.id, Conversation.version == record.version)
        .values(title=body.title, version=Conversation.version + 1, updated_at=utcnow())
    )
    if changed.rowcount != 1:
        raise AppError("version_conflict", "Диалог изменился. Обновите историю", 409)
    await db.refresh(record)
    return conversation_payload(record)


@router.get("/conversations/{conversation_id}")
async def conversation(conversation_id: str, access: WorkspaceAccess, db: Db):
    record = await get_conversation(db, conversation_id, access)
    runs = (
        await db.scalars(
            select(QueryRun)
            .where(QueryRun.conversation_id == record.id)
            .order_by(QueryRun.created_at)
        )
    ).all()
    visible = []
    for run in runs:
        try:
            await ensure_run_scope(db, run, access)
            visible.append(run)
        except AppError:
            continue
    allowed_ids = {run.id for run in visible}
    messages = (
        await db.scalars(
            select(Message).where(Message.conversation_id == record.id).order_by(Message.created_at)
        )
    ).all()
    payload = conversation_payload(record)
    if runs and runs[0].id not in allowed_ids:
        payload["title"] = "Недоступный диалог"
    return {
        **payload,
        "runs": [run_payload(run) for run in visible],
        "messages": [
            {
                key: getattr(message, key)
                for key in ("id", "role", "content", "run_id", "created_at")
            }
            for message in messages
            if message.run_id in allowed_ids
        ],
    }


@router.post("/conversations/{conversation_id}/messages", status_code=202)
async def question(
    conversation_id: str, body: QuestionCreate, access: WorkspaceAccess, db: Db, request: Request
):
    return run_payload(
        await create_run(db, access, conversation_id, body, request.app.state.settings)
    )


@router.get("/runs/{run_id}")
async def query_run(run_id: str, access: WorkspaceAccess, db: Db):
    return run_payload(await get_readable_run(db, run_id, access))


@router.post("/runs/{run_id}/cancel")
async def cancel(run_id: str, access: WorkspaceAccess, db: Db):
    run = await get_run(db, run_id, access)
    job = await db.scalar(select(Job).where(Job.run_id == run.id).with_for_update())
    await db.refresh(run)
    if run.status not in ACTIVE:
        return run_payload(run)
    if job.status == "queued":
        job.status = run.status = "cancelled"
        run.finished_at = utcnow()
    else:
        job.status = run.status = "cancel_requested"
    run.stage = run.status
    await append_event(
        db, run, "Запрос отменён" if run.status == "cancelled" else "Останавливаем запрос"
    )
    await db.flush()
    return run_payload(run)


@router.get("/runs/{run_id}/events")
async def events(run_id: str, access: WorkspaceAccess, db: Db, request: Request, after: int = 0):
    await get_readable_run(db, run_id, access)
    try:
        start = max(0, after, int(request.headers.get("Last-Event-ID", "0")))
    except ValueError:
        raise AppError("invalid_event_id", "Некорректный идентификатор события")
    session_hash = token_hash(request.cookies.get("razbor_session", ""))
    database = request.app.state.database

    async def stream():
        cursor = start
        while not await request.is_disconnected():
            async with database.sessions() as session:
                try:
                    cookie = await session.scalar(
                        select(Session).where(Session.token_hash == session_hash)
                    )
                    if cookie is None or aware(cookie.expires_at) <= utcnow():
                        raise AppError("unauthenticated", "Сессия завершена", 401)
                    current = await get_access(session, access.user_id, access.workspace_id)
                    run = await get_readable_run(session, run_id, current)
                except AppError:
                    yield "event: access_revoked\ndata: {}\n\n"
                    return
                records = (
                    await session.scalars(
                        select(RunEvent)
                        .where(RunEvent.run_id == run_id, RunEvent.sequence > cursor)
                        .order_by(RunEvent.sequence)
                        .limit(100)
                    )
                ).all()
                for event in records:
                    cursor = event.sequence
                    yield f"id: {cursor}\nevent: update\ndata: {json.dumps(event.payload, ensure_ascii=False)}\n\n"
                if run.status in TERMINAL:
                    return
            yield ": keepalive\n\n"
            await asyncio.sleep(1)

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
