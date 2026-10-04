from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, JSON, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.infrastructure.database import Base, Identity, utcnow


class Job(Identity, Base):
    __tablename__ = "jobs"
    run_id: Mapped[str] = mapped_column(ForeignKey("query_runs.id"), unique=True)
    status: Mapped[str] = mapped_column(String(24), default="queued", index=True)
    lease_token: Mapped[str | None] = mapped_column(String(36), nullable=True)
    lease_until: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )
    attempts: Mapped[int] = mapped_column(Integer, default=0)


class RunEvent(Identity, Base):
    __tablename__ = "run_events"
    __table_args__ = (UniqueConstraint("run_id", "sequence"),)
    run_id: Mapped[str] = mapped_column(ForeignKey("query_runs.id"), index=True)
    sequence: Mapped[int] = mapped_column(Integer)
    payload: Mapped[dict] = mapped_column(JSON)


class WorkerHeartbeat(Base):
    __tablename__ = "worker_heartbeats"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    status: Mapped[str] = mapped_column(String(24))
    model: Mapped[str] = mapped_column(String(120), default="")
