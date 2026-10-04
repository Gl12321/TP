import argparse
import asyncio
from pathlib import Path

from alembic import command
from alembic.config import Config

from backend.app.infrastructure.config import load_settings


def migrate():
    directory = Path(__file__).resolve().parents[1]
    config = Config(str(directory / "alembic.ini"))
    config.set_main_option("script_location", str(directory / "migrations"))
    config.set_main_option("sqlalchemy.url", load_settings().database_url.replace("%", "%%"))
    command.upgrade(config, "head")


async def restore_state(database=None):
    from sqlalchemy import delete, select
    from backend.app.access.models import Session
    from backend.app.assistant.models import QueryRun
    from backend.app.assistant.service import ACTIVE, append_event
    from backend.app.infrastructure.database import Database, utcnow
    from backend.app.jobs.models import Job, WorkerHeartbeat

    owned = database is None
    database = database or Database(load_settings().database_url)
    try:
        async with database.sessions.begin() as db:
            sessions = await db.execute(delete(Session))
            await db.execute(delete(WorkerHeartbeat))
            jobs = (
                await db.scalars(select(Job).where(Job.status.in_(ACTIVE)).with_for_update())
            ).all()
            runs = (
                await db.scalars(
                    select(QueryRun).where(QueryRun.status.in_(ACTIVE)).with_for_update()
                )
            ).all()
            for job in jobs:
                job.status = "failed"
                job.lease_token = job.lease_until = None
            for run in runs:
                run.status = run.stage = "failed"
                run.finished_at = utcnow()
                run.result = None
                run.error = {
                    "code": "backup_restored",
                    "message": "Приложение восстановлено из резервной копии. Отправьте вопрос заново",
                }
                await append_event(db, run, run.error["message"])
        return {
            "revoked_sessions": sessions.rowcount,
            "interrupted_jobs": len(jobs),
            "interrupted_runs": len(runs),
        }
    finally:
        if owned:
            await database.close()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["migrate", "restore-state"])
    arguments = parser.parse_args()
    if arguments.command == "migrate":
        migrate()
    else:
        print(asyncio.run(restore_state()))


if __name__ == "__main__":
    main()
