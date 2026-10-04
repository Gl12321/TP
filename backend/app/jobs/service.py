from datetime import timedelta

from sqlalchemy import select, update

from backend.app.assistant.models import Message, QueryRun
from backend.app.assistant.service import append_event
from backend.app.infrastructure.database import aware, new_id, utcnow
from backend.app.jobs.models import Job, WorkerHeartbeat


async def heartbeat(db, worker_id, status, model=""):
    record = await db.get(WorkerHeartbeat, worker_id)
    if record is None:
        record = WorkerHeartbeat(id=worker_id)
        db.add(record)
    record.updated_at = utcnow()
    record.status = status
    record.model = model


async def claim_job(db, lease_seconds):
    now = utcnow()
    expired = (
        await db.scalars(
            select(Job)
            .where(Job.status.in_({"running", "cancel_requested"}), Job.lease_until < now)
            .with_for_update(skip_locked=True)
        )
    ).all()
    for job in expired:
        run = await db.get(QueryRun, job.run_id)
        job.status = run.status = "cancelled" if job.status == "cancel_requested" else "failed"
        job.lease_token = None
        run.stage = run.status
        run.finished_at = now
        if run.status == "failed":
            run.error = {
                "code": "worker_lost",
                "message": "Исполнитель был остановлен. Отправьте вопрос заново",
            }
        await append_event(db, run, "Выполнение прервано")
    job = await db.scalar(
        select(Job)
        .where(Job.status == "queued")
        .order_by(Job.created_at)
        .with_for_update(skip_locked=True)
        .limit(1)
    )
    if job is None:
        return None
    token = new_id()
    claimed = await db.execute(
        update(Job)
        .where(Job.id == job.id, Job.status == "queued")
        .values(
            status="running",
            lease_token=token,
            lease_until=now + timedelta(seconds=lease_seconds),
            attempts=Job.attempts + 1,
        )
    )
    if claimed.rowcount != 1:
        return None
    run = await db.get(QueryRun, job.run_id)
    run.status = "running"
    run.stage = "preparing"
    await append_event(db, run, "Подготавливаем разрешённый контекст")
    await db.flush()
    return job.id, token, run.id


async def renew_lease(db, job_id, token, seconds):
    job = await db.scalar(
        select(Job)
        .where(Job.id == job_id)
        .execution_options(populate_existing=True)
        .with_for_update()
    )
    if (
        job is None
        or job.lease_token != token
        or job.status not in {"running", "cancel_requested"}
        or job.lease_until is None
        or aware(job.lease_until) <= utcnow()
    ):
        return None
    job.lease_until = utcnow() + timedelta(seconds=seconds)
    return job.status


async def finish_job(db, job_id, token, outcome):
    job = await db.scalar(select(Job).where(Job.id == job_id).with_for_update())
    if (
        job is None
        or job.lease_token != token
        or job.status not in {"running", "cancel_requested"}
        or job.lease_until is None
        or aware(job.lease_until) <= utcnow()
    ):
        return False
    run = await db.get(QueryRun, job.run_id)
    try:
        await publication_access(db, run)
    except Exception as error:
        from backend.app.infrastructure.errors import AppError

        if not isinstance(error, AppError):
            raise
        outcome = {"status": "rejected", "error": {"code": error.code, "message": error.message}}
    if aware(job.lease_until) <= utcnow():
        return False
    if job.status == "cancel_requested":
        outcome = {
            "status": "cancelled",
            "error": None,
            "result": None,
            "sql": None,
            "clarification": None,
        }
    for key in ("status", "error", "result", "sql", "clarification"):
        setattr(run, key, outcome.get(key))
    run.stage = run.status
    run.finished_at = utcnow()
    job.status = run.status
    job.lease_token = None
    job.lease_until = None
    descriptions = {
        "succeeded": "Таблица готова",
        "failed": "Не удалось выполнить запрос",
        "cancelled": "Запрос отменён",
        "needs_input": "Уточните условия запроса",
        "rejected": "Запрос недоступен",
    }
    message = descriptions[run.status]
    if run.clarification:
        message = run.clarification["message"]
    elif run.error:
        message = run.error["message"]
    db.add(
        Message(
            conversation_id=run.conversation_id, role="assistant", content=message, run_id=run.id
        )
    )
    await append_event(db, run, message)
    return True


async def publication_access(db, run):
    from backend.app.access.models import Membership, User
    from backend.app.access.policy import from_membership
    from backend.app.analytics.models import Store
    from backend.app.infrastructure.errors import AppError
    from backend.app.sources.models import Source

    member = await db.scalar(
        select(Membership)
        .where(Membership.workspace_id == run.workspace_id, Membership.user_id == run.user_id)
        .execution_options(populate_existing=True)
        .with_for_update()
    )
    user = await db.scalar(
        select(User)
        .where(User.id == run.user_id)
        .execution_options(populate_existing=True)
        .with_for_update()
    )
    if (
        not member
        or not member.active
        or not user
        or not user.active
        or member.revision != run.membership_revision
    ):
        raise AppError("access_changed", "Права изменились во время выполнения", 403)
    access = from_membership(member)
    access.require("assistant:use")
    access.require_scope(run.store_ids)
    source = await db.scalar(
        select(Source)
        .where(Source.id == run.source_id)
        .execution_options(populate_existing=True)
        .with_for_update()
    )
    if (
        not source
        or not source.enabled
        or source.policy_revision != run.context.get("policy_revision")
        or source.catalog_version != run.context.get("catalog_version")
    ):
        raise AppError("source_changed", "Источник или права изменились во время выполнения", 403)
    from backend.app.sources.service import require_source_access

    require_source_access(source, access)
    stores = (
        await db.scalars(
            select(Store)
            .where(
                Store.id.in_(run.store_ids),
                Store.workspace_id == run.workspace_id,
                Store.active.is_(True),
            )
            .with_for_update()
        )
    ).all()
    if {store.id for store in stores} != set(run.store_ids):
        raise AppError("scope_changed", "Область точек изменилась", 403)
