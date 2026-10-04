from datetime import datetime

from sqlalchemy import (
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Integer,
    JSON,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.infrastructure.database import Base, Identity, utcnow


class Conversation(Identity, Base):
    __tablename__ = "conversations"
    __table_args__ = (
        UniqueConstraint("id", "workspace_id", "user_id", name="uq_conversation_owner"),
        ForeignKeyConstraint(
            ["workspace_id", "user_id"],
            ["memberships.workspace_id", "memberships.user_id"],
            name="fk_conversation_member",
        ),
    )
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"), index=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("app_users.id"), index=True)
    title: Mapped[str] = mapped_column(String(200), default="Новый вопрос")
    version: Mapped[int] = mapped_column(Integer, default=0)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class QueryRun(Identity, Base):
    __tablename__ = "query_runs"
    __table_args__ = (
        UniqueConstraint("workspace_id", "user_id", "idempotency_key"),
        UniqueConstraint("id", "workspace_id", name="uq_run_workspace"),
        UniqueConstraint("id", "conversation_id", name="uq_run_conversation"),
        UniqueConstraint(
            "id", "workspace_id", "conversation_id", "source_id", "user_id", name="uq_run_context"
        ),
        ForeignKeyConstraint(
            ["source_id", "workspace_id"],
            ["sources.id", "sources.workspace_id"],
            name="fk_run_source_workspace",
        ),
        ForeignKeyConstraint(
            ["conversation_id", "workspace_id", "user_id"],
            ["conversations.id", "conversations.workspace_id", "conversations.user_id"],
            name="fk_run_conversation_owner",
        ),
        ForeignKeyConstraint(
            ["base_run_id", "workspace_id", "conversation_id", "source_id", "user_id"],
            [
                "query_runs.id",
                "query_runs.workspace_id",
                "query_runs.conversation_id",
                "query_runs.source_id",
                "query_runs.user_id",
            ],
            name="fk_run_base_context",
        ),
    )
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"), index=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("app_users.id"), index=True)
    conversation_id: Mapped[str] = mapped_column(ForeignKey("conversations.id"), index=True)
    source_id: Mapped[str] = mapped_column(ForeignKey("sources.id"))
    question: Mapped[str] = mapped_column(Text)
    store_ids: Mapped[list] = mapped_column(JSON)
    context: Mapped[dict] = mapped_column(JSON, default=dict)
    base_run_id: Mapped[str | None] = mapped_column(ForeignKey("query_runs.id"), nullable=True)
    idempotency_key: Mapped[str] = mapped_column(String(100))
    request_hash: Mapped[str] = mapped_column(String(64))
    membership_revision: Mapped[int] = mapped_column(Integer)
    conversation_version: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(24), default="queued", index=True)
    stage: Mapped[str] = mapped_column(String(80), default="queued")
    kind: Mapped[str] = mapped_column(String(24), default="question")
    sql: Mapped[str | None] = mapped_column(Text, nullable=True)
    result: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    error: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    clarification: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Message(Identity, Base):
    __tablename__ = "conversation_messages"
    __table_args__ = (
        ForeignKeyConstraint(
            ["run_id", "conversation_id"],
            ["query_runs.id", "query_runs.conversation_id"],
            name="fk_message_run_conversation",
        ),
    )
    conversation_id: Mapped[str] = mapped_column(ForeignKey("conversations.id"), index=True)
    role: Mapped[str] = mapped_column(String(16))
    content: Mapped[str] = mapped_column(Text)
    run_id: Mapped[str] = mapped_column(ForeignKey("query_runs.id"))
