import argparse
import asyncio
import logging
import signal
from datetime import timedelta
from threading import Event

from sqlalchemy import select, text

from backend.app.access.policy import get_access
from backend.app.analytics.models import Metric, Store
from backend.app.assistant.models import QueryRun
from backend.app.assistant.service import append_event, ensure_run_scope, get_run
from backend.app.infrastructure.config import load_settings
from backend.app.infrastructure.database import Database, new_id, utcnow
from backend.app.infrastructure.errors import AppError
from backend.app.jobs.models import WorkerHeartbeat
from backend.app.jobs.service import claim_job, finish_job, heartbeat, renew_lease
from backend.app.sources.service import ensure_ready, get_source


logger = logging.getLogger("razbor.worker")


class Worker:
    def __init__(self, database, settings, engine, *, worker_id=None):
        self.database = database
        self.settings = settings
        self.engine = engine
        self.worker_id = worker_id or new_id()
        self.stopping = asyncio.Event()

    async def access_check(self, run):
        async with self.database.sessions() as db:
            access = await get_access(db, run.user_id, run.workspace_id)
            access.require("assistant:use")
            if access.revision != run.membership_revision:
                raise AppError(
                    "access_changed",
                    "Права изменились во время выполнения. Отправьте вопрос заново",
                    403,
                )
            await ensure_run_scope(db, run, access)
            source = await get_source(db, run.source_id, run.workspace_id)
            if source.catalog_version != run.context.get("catalog_version"):
                raise AppError(
                    "catalog_changed",
                    "Структура источника изменилась во время выполнения. Отправьте вопрос заново",
                    409,
                )
            active = set(
                (
                    await db.scalars(
                        select(Store.id).where(
                            Store.id.in_(run.store_ids),
                            Store.workspace_id == run.workspace_id,
                            Store.active.is_(True),
                        )
                    )
                ).all()
            )
            if active != set(run.store_ids):
                raise AppError("scope_changed", "Область точек изменилась", 403)

    async def run_once(self):
        async with self.database.sessions.begin() as db:
            await heartbeat(db, self.worker_id, "ready", getattr(self.engine, "model", ""))
            claim = await claim_job(db, self.settings.job_lease_seconds)
        if claim is None:
            return False
        job_id, token, run_id = claim
        await self.process(job_id, token, run_id)
        return True

    async def process(self, job_id, token, run_id):
        from sql_agent.contracts import QueryContext, QueryError

        cancelled = Event()
        pending = asyncio.Queue(maxsize=128)
        loop = asyncio.get_running_loop()

        def enqueue(event):
            payload = {
                "stage": str(event.content.get("stage", "running"))[:80],
                "message": str(event.content.get("message", "Выполняем запрос"))[:400],
            }

            def put():
                if not pending.full():
                    pending.put_nowait(payload)

            loop.call_soon_threadsafe(put)

        context = QueryContext(request_id=run_id, cancelled=cancelled, on_event=enqueue)
        completed = asyncio.Event()
        monitor = asyncio.create_task(
            self.monitor(job_id, token, run_id, cancelled, pending, completed)
        )
        outcome = {
            "status": "failed",
            "error": {"code": "internal_error", "message": "Не удалось выполнить запрос"},
        }
        try:
            async with self.database.sessions() as db:
                run = await db.get(QueryRun, run_id)
                await self.access_check(run)
                source = await get_source(db, run.source_id, run.workspace_id)
                ensure_ready(source)
                stores = (await db.scalars(select(Store).where(Store.id.in_(run.store_ids)))).all()
                metrics = (
                    await db.scalars(
                        select(Metric).where(
                            Metric.workspace_id == run.workspace_id, Metric.source_id == source.id
                        )
                    )
                ).all()
                if run.context.get("metric_id"):
                    metrics = [
                        metric for metric in metrics if metric.id == run.context["metric_id"]
                    ]
                else:
                    from backend.app.analytics.service import latest

                    metrics = latest(metrics, lambda metric: metric.key)
                access = await get_access(db, run.user_id, run.workspace_id)
                base = await get_run(db, run.base_run_id, access) if run.base_run_id else None
            result = await self.engine.run(
                run, source, stores, metrics, base, context, lambda: self.access_check(run)
            )
            await self.access_check(run)
            if result.status == "success" and result.result:
                table = result.result
                outcome = {
                    "status": "succeeded",
                    "sql": table.sql,
                    "result": {
                        "columns": [
                            {
                                "name": name,
                                "type": table.column_types[index]
                                if index < len(table.column_types)
                                else "unknown",
                            }
                            for index, name in enumerate(table.columns)
                        ],
                        "rows": table.rows,
                        "truncated": table.truncated,
                        "row_count": len(table.rows),
                        "execution": getattr(result, "execution", None),
                    },
                }
            elif result.status == "clarification":
                outcome = {
                    "status": "needs_input",
                    "clarification": {"code": result.error_code, "message": result.clarification},
                }
            elif result.status == "cancelled":
                outcome = {"status": "cancelled"}
            else:
                outcome = {
                    "status": "failed",
                    "sql": result.sql,
                    "error": {
                        "code": result.error_code or "query_failed",
                        "message": result.error or "Запрос не выполнен",
                    },
                }
        except AppError as error:
            outcome = {
                "status": "rejected",
                "error": {"code": error.code, "message": error.message},
            }
        except QueryError as error:
            outcome = {
                "status": "cancelled" if error.code == "cancelled" else "failed",
                "error": {"code": error.code, "message": str(error)},
            }
        except asyncio.CancelledError:
            cancelled.set()
            outcome = {"status": "cancelled"}
            raise
        except Exception:
            logger.exception("Query execution failed: %s", run_id)
        finally:
            completed.set()
            await monitor
            async with self.database.sessions.begin() as db:
                await finish_job(db, job_id, token, outcome)
                await heartbeat(db, self.worker_id, "ready", getattr(self.engine, "model", ""))

    async def monitor(self, job_id, token, run_id, cancelled, pending, completed):
        while not completed.is_set():
            try:
                async with self.database.sessions.begin() as db:
                    state = await renew_lease(db, job_id, token, self.settings.job_lease_seconds)
                    if state is None or state == "cancel_requested" or self.stopping.is_set():
                        cancelled.set()
                    if state is None:
                        return
                    await heartbeat(db, self.worker_id, "busy", getattr(self.engine, "model", ""))
                    run = await db.get(QueryRun, run_id)
                    while not pending.empty():
                        payload = pending.get_nowait()
                        if state == "running":
                            run.stage = payload["stage"]
                            await append_event(db, run, payload["message"])
                            await db.flush()
            except Exception:
                cancelled.set()
                logger.exception("Worker lease renewal failed")
                return
            try:
                await asyncio.wait_for(
                    completed.wait(), timeout=min(2, self.settings.job_lease_seconds / 3)
                )
            except TimeoutError:
                pass

    async def serve(self):
        while not self.stopping.is_set():
            if not await self.run_once():
                try:
                    await asyncio.wait_for(self.stopping.wait(), timeout=1)
                except TimeoutError:
                    pass


async def healthy(database):
    async with database.sessions() as db:
        record = await db.scalar(
            select(WorkerHeartbeat.id)
            .where(
                WorkerHeartbeat.updated_at > utcnow() - timedelta(seconds=30),
                WorkerHeartbeat.status.in_({"ready", "busy"}),
            )
            .limit(1)
        )
        return record is not None


async def main_async(healthcheck=False):
    settings = load_settings()
    settings.validate()
    database = Database(settings.database_url)
    lock_connection = None
    engine = None
    worker = None
    try:
        if healthcheck:
            return 0 if await healthy(database) else 1
        if database.engine.dialect.name == "postgresql":
            lock_connection = await database.engine.connect()
            acquired = await lock_connection.scalar(text("SELECT pg_try_advisory_lock(7272626)"))
            if not acquired:
                raise RuntimeError("Another model worker already owns this application database")
        from backend.app.assistant.engine import LocalEngine

        engine = await asyncio.to_thread(LocalEngine, settings)
        worker = Worker(database, settings, engine)
        loop = asyncio.get_running_loop()
        for name in ("SIGTERM", "SIGINT"):
            signal_number = getattr(signal, name)
            try:
                loop.add_signal_handler(signal_number, worker.stopping.set)
            except NotImplementedError:
                signal.signal(
                    signal_number, lambda *_: loop.call_soon_threadsafe(worker.stopping.set)
                )
        await worker.serve()
        return 0
    finally:
        if worker:
            async with database.sessions.begin() as db:
                await heartbeat(db, worker.worker_id, "stopped", getattr(engine, "model", ""))
        if engine:
            await engine.close()
        if lock_connection:
            await lock_connection.close()
        await database.close()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--healthcheck", action="store_true")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO)
    try:
        raise SystemExit(asyncio.run(main_async(args.healthcheck)))
    except (ValueError, RuntimeError) as error:
        logger.error("%s", error)
        raise SystemExit(1)


if __name__ == "__main__":
    main()
