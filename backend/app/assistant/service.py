import hashlib
import json

from sqlalchemy import func, select, update

from backend.app.analytics.models import Metric, Store
from backend.app.assistant.models import Conversation, Message, QueryRun
from backend.app.infrastructure.database import new_id, utcnow
from backend.app.infrastructure.errors import AppError
from backend.app.jobs.models import Job, RunEvent
from backend.app.sources.service import ensure_ready, get_source, require_source_access


ACTIVE = {"queued", "running", "cancel_requested"}
TERMINAL = {"cancelled", "succeeded", "failed", "needs_input", "rejected"}


def conversation_payload(conversation):
    return {
        key: getattr(conversation, key)
        for key in ("id", "title", "version", "created_at", "updated_at")
    }


def run_payload(run):
    return {
        key: getattr(run, key)
        for key in (
            "id",
            "conversation_id",
            "question",
            "source_id",
            "store_ids",
            "status",
            "stage",
            "sql",
            "result",
            "error",
            "clarification",
            "created_at",
            "finished_at",
            "conversation_version",
            "context",
        )
    }


async def get_conversation(db, conversation_id, access):
    access.require("assistant:use")
    conversation = await db.scalar(
        select(Conversation).where(
            Conversation.id == conversation_id,
            Conversation.workspace_id == access.workspace_id,
            Conversation.user_id == access.user_id,
        )
    )
    if conversation is None:
        raise AppError("not_found", "Диалог не найден", 404)
    return conversation


async def ensure_run_scope(db, run, access):
    if run.workspace_id != access.workspace_id:
        raise AppError("not_found", "Результат не найден", 404)
    access.require_scope(run.store_ids)
    source = await get_source(db, run.source_id, access.workspace_id)
    require_source_access(source, access)
    if not source.enabled or source.policy_revision != run.context.get("policy_revision"):
        raise AppError(
            "result_access_changed", "Доступ к источнику изменился. Выполните новый запрос", 403
        )


async def get_run(db, run_id, access, *, shared=False):
    run = await db.get(QueryRun, run_id)
    if run is None or run.workspace_id != access.workspace_id:
        raise AppError("not_found", "Запрос не найден", 404)
    if not shared and run.user_id != access.user_id:
        raise AppError("not_found", "Запрос не найден", 404)
    await ensure_run_scope(db, run, access)
    return run


async def get_report_run(db, run_id, access, report_id=None):
    from backend.app.analytics.models import Report

    run = await db.get(QueryRun, run_id)
    if run is None or run.workspace_id != access.workspace_id:
        raise AppError("not_found", "Результат не найден", 404)
    statement = select(Report).where(Report.workspace_id == access.workspace_id)
    if report_id:
        statement = statement.where(Report.id == report_id)
    linked = Report.run_id == run.id
    if run.kind == "refresh" and run.context.get("report_id"):
        linked = linked | (Report.id == run.context["report_id"])
    report = await db.scalar(statement.where(linked).limit(1))
    if report is None:
        raise AppError("not_found", "Сохранённый отчёт не найден", 404)
    return await get_run(db, run_id, access, shared=True)


async def get_readable_run(db, run_id, access):
    run = await db.get(QueryRun, run_id)
    if run is not None and run.user_id == access.user_id:
        return await get_run(db, run_id, access)
    return await get_report_run(db, run_id, access)


async def resolve_scope(db, access, requested):
    statement = select(Store).where(
        Store.workspace_id == access.workspace_id, Store.active.is_(True)
    )
    if not access.all_stores:
        statement = statement.where(Store.id.in_(access.store_ids))
    stores = (await db.scalars(statement.order_by(Store.name))).all()
    by_id = {store.id: store for store in stores}
    scope = sorted(set(requested) if requested else by_id)
    access.require_scope(scope)
    if not set(scope).issubset(by_id):
        raise AppError("invalid_scope", "Выбрана недоступная или отключённая точка", 403)
    return scope


async def append_event(db, run, message):
    sequence = (
        await db.scalar(select(func.max(RunEvent.sequence)).where(RunEvent.run_id == run.id)) or 0
    ) + 1
    event = RunEvent(
        run_id=run.id,
        sequence=sequence,
        payload={
            "sequence": sequence,
            "status": run.status,
            "stage": run.stage,
            "message": message,
        },
    )
    db.add(event)
    return event


async def create_run(db, access, conversation_id, body, settings, *, saved_sql=None):
    access.require("assistant:use")
    from backend.app.access.models import Setup

    await db.execute(update(Setup).where(Setup.id == 1).values(id=Setup.id))
    conversation = await get_conversation(db, conversation_id, access)
    request_data = {
        "conversation_id": conversation_id,
        **body.model_dump(mode="json", exclude={"version", "idempotency_key"}),
        "saved_sql": saved_sql,
    }
    fingerprint = hashlib.sha256(
        json.dumps(request_data, sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()
    existing = await db.scalar(
        select(QueryRun).where(
            QueryRun.workspace_id == access.workspace_id,
            QueryRun.user_id == access.user_id,
            QueryRun.idempotency_key == body.idempotency_key,
        )
    )
    if existing:
        if existing.request_hash != fingerprint:
            raise AppError(
                "idempotency_conflict",
                "Этот ключ отправки уже использован для другого запроса",
                409,
            )
        await ensure_run_scope(db, existing, access)
        return existing
    from backend.app.jobs.models import WorkerHeartbeat
    from datetime import timedelta

    worker = await db.scalar(
        select(WorkerHeartbeat.id)
        .where(
            WorkerHeartbeat.updated_at > utcnow() - timedelta(seconds=30),
            WorkerHeartbeat.status.in_({"ready", "busy"}),
        )
        .limit(1)
    )
    if worker is None:
        raise AppError(
            "worker_unavailable",
            "Исполнитель запросов ещё не готов. История и обсуждения доступны",
            503,
        )
    source = await get_source(db, body.source_id, access.workspace_id)
    require_source_access(source, access)
    ensure_ready(source)
    scope = await resolve_scope(db, access, body.store_ids)
    base = None
    if body.base_run_id:
        base = await get_run(db, body.base_run_id, access)
        if (
            base.conversation_id != conversation_id
            or base.source_id != source.id
            or base.status not in {"succeeded", "needs_input"}
        ):
            raise AppError(
                "invalid_base", "Выберите завершённый результат этого диалога и источника", 409
            )
    active = await db.scalar(
        select(QueryRun)
        .where(QueryRun.conversation_id == conversation_id, QueryRun.status.in_(ACTIVE))
        .limit(1)
    )
    if active:
        if active.idempotency_key == body.idempotency_key and active.request_hash == fingerprint:
            await ensure_run_scope(db, active, access)
            return active
        raise AppError("conversation_busy", "В этом диалоге уже выполняется запрос", 409)
    queued = await db.scalar(select(func.count()).select_from(Job).where(Job.status.in_(ACTIVE)))
    if queued >= settings.max_queued_jobs:
        raise AppError("queue_full", "Очередь заполнена. Повторите отправку позже", 429)
    workspace_queued = await db.scalar(
        select(func.count())
        .select_from(QueryRun)
        .where(QueryRun.workspace_id == access.workspace_id, QueryRun.status.in_(ACTIVE))
    )
    user_queued = await db.scalar(
        select(func.count())
        .select_from(QueryRun)
        .where(QueryRun.user_id == access.user_id, QueryRun.status.in_(ACTIVE))
    )
    if workspace_queued >= settings.max_queued_jobs_per_workspace:
        raise AppError(
            "workspace_queue_full",
            "Пространство достигло лимита активных вопросов. Дождитесь результата или отмените лишний запрос",
            429,
        )
    if user_queued >= settings.max_queued_jobs_per_user:
        raise AppError(
            "user_queue_full",
            "У вас уже выполняется несколько вопросов. Дождитесь результата или отмените один из них",
            429,
        )
    context = {
        key: value
        for key, value in (base.context.items() if base else [])
        if key in {"date_from", "date_to", "metric_id", "metric_version"}
    }
    for field in ("date_from", "date_to", "metric_id"):
        if field in body.model_fields_set or not base:
            value = getattr(body, field)
            context[field] = value.isoformat() if field != "metric_id" and value else value
    context.update(
        {"catalog_version": source.catalog_version, "policy_revision": source.policy_revision}
    )
    if context.get("metric_id"):
        metric = await db.get(Metric, context["metric_id"])
        if (
            not metric
            or metric.workspace_id != access.workspace_id
            or metric.source_id != source.id
        ):
            raise AppError("invalid_metric", "Показатель не принадлежит выбранному источнику")
        context["metric_version"] = metric.version
    else:
        context.pop("metric_version", None)
    changed = await db.execute(
        update(Conversation)
        .where(Conversation.id == conversation.id, Conversation.version == body.version)
        .values(
            version=Conversation.version + 1,
            updated_at=utcnow(),
            title=body.question[:120] if conversation.version == 0 else conversation.title,
        )
    )
    if changed.rowcount != 1:
        await db.rollback()
        existing = await db.scalar(
            select(QueryRun).where(
                QueryRun.workspace_id == access.workspace_id,
                QueryRun.user_id == access.user_id,
                QueryRun.idempotency_key == body.idempotency_key,
            )
        )
        if existing and existing.request_hash == fingerprint:
            await ensure_run_scope(db, existing, access)
            return existing
        raise AppError(
            "version_conflict",
            "Диалог изменился в другой вкладке. Обновите историю и повторите отправку",
            409,
        )
    run = QueryRun(
        id=new_id(),
        workspace_id=access.workspace_id,
        user_id=access.user_id,
        conversation_id=conversation_id,
        source_id=source.id,
        question=body.question,
        store_ids=scope,
        context=context,
        base_run_id=body.base_run_id,
        idempotency_key=body.idempotency_key,
        request_hash=fingerprint,
        membership_revision=access.revision,
        conversation_version=body.version + 1,
        kind="refresh" if saved_sql else "question",
        sql=saved_sql,
    )
    db.add(run)
    await db.flush()
    db.add_all(
        [
            Message(
                conversation_id=conversation_id, role="user", content=body.question, run_id=run.id
            ),
            Job(run_id=run.id),
        ]
    )
    await append_event(db, run, "Запрос поставлен в очередь")
    await db.flush()
    return run
