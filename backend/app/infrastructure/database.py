from collections.abc import AsyncIterator
from datetime import datetime, timezone
from importlib import import_module
from uuid import uuid4

from fastapi import Request
from sqlalchemy import DateTime, String, event
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def new_id() -> str:
    return str(uuid4())


def aware(value: datetime) -> datetime:
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value


class Base(DeclarativeBase):
    pass


class Identity:
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Database:
    def __init__(self, url: str):
        self.engine = create_async_engine(url, pool_pre_ping=True)
        if url.startswith("sqlite"):

            @event.listens_for(self.engine.sync_engine, "connect")
            def configure_sqlite(connection, record):
                cursor = connection.cursor()
                cursor.execute("PRAGMA foreign_keys=ON")
                cursor.execute("PRAGMA busy_timeout=10000")
                cursor.execute("PRAGMA journal_mode=WAL")
                cursor.close()

        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False)

    async def create_schema(self) -> None:
        load_models()
        async with self.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)

    async def close(self) -> None:
        await self.engine.dispose()


def load_models() -> None:
    for name in ("access", "analytics", "assistant", "collaboration", "jobs", "sources"):
        import_module(f"backend.app.{name}.models")


async def get_db(request: Request) -> AsyncIterator[AsyncSession]:
    async with request.app.state.database.sessions() as session:
        try:
            yield session
            if request.method not in {"GET", "HEAD", "OPTIONS"} and getattr(
                request.state, "actor_id", None
            ):
                from backend.app.access.models import AuditEvent

                session.add(
                    AuditEvent(
                        actor_id=request.state.actor_id,
                        workspace_id=request.path_params.get("workspace_id"),
                        action=f"{request.method} {request.url.path}"[:300],
                    )
                )
            await session.commit()
        except BaseException:
            await session.rollback()
            raise
