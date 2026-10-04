from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, JSON, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.infrastructure.database import Base, Identity


class Source(Identity, Base):
    __tablename__ = "sources"
    __table_args__ = (UniqueConstraint("id", "workspace_id", name="uq_source_workspace"),)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"), index=True)
    name: Mapped[str] = mapped_column(String(160))
    host: Mapped[str] = mapped_column(String(253))
    port: Mapped[int] = mapped_column(Integer, default=5432)
    database: Mapped[str] = mapped_column(String(128))
    username: Mapped[str] = mapped_column(String(128))
    encrypted_password: Mapped[str] = mapped_column(Text)
    schemas: Mapped[list] = mapped_column(JSON)
    ssl_mode: Mapped[str] = mapped_column(String(16), default="require")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    reader_ids: Mapped[list | None] = mapped_column(JSON, nullable=True)
    catalog_version: Mapped[int] = mapped_column(Integer, default=0)
    policy_revision: Mapped[int] = mapped_column(Integer, default=1)
    catalog: Mapped[list] = mapped_column(JSON, default=list)
    policies: Mapped[list] = mapped_column(JSON, default=list)
    status: Mapped[str] = mapped_column(String(24), default="unchecked")
    last_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    error: Mapped[str | None] = mapped_column(String(400), nullable=True)
