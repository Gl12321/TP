from datetime import date, datetime

from sqlalchemy import (
    DateTime,
    CheckConstraint,
    Date,
    ForeignKey,
    ForeignKeyConstraint,
    JSON,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.infrastructure.database import Base, Identity, utcnow


class Case(Identity, Base):
    __tablename__ = "cases"
    __table_args__ = (
        UniqueConstraint("id", "workspace_id", name="uq_case_workspace"),
        ForeignKeyConstraint(
            ["run_id", "workspace_id"],
            ["query_runs.id", "query_runs.workspace_id"],
            name="fk_case_run_workspace",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "created_by"],
            ["memberships.workspace_id", "memberships.user_id"],
            name="fk_case_creator_member",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "assignee_id"],
            ["memberships.workspace_id", "memberships.user_id"],
            name="fk_case_assignee_member",
        ),
    )
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"), index=True)
    title: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(24), default="open")
    store_ids: Mapped[list] = mapped_column(JSON)
    run_id: Mapped[str | None] = mapped_column(ForeignKey("query_runs.id"), nullable=True)
    created_by: Mapped[str] = mapped_column(ForeignKey("app_users.id"))
    assignee_id: Mapped[str | None] = mapped_column(ForeignKey("app_users.id"), nullable=True)
    conclusion: Mapped[str | None] = mapped_column(Text, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Measurement(Identity, Base):
    __tablename__ = "case_measurements"
    __table_args__ = (
        UniqueConstraint("case_id", "idempotency_key", name="uq_measurement_request"),
        ForeignKeyConstraint(
            ["case_id", "workspace_id"],
            ["cases.id", "cases.workspace_id"],
            name="fk_measurement_case_workspace",
        ),
        ForeignKeyConstraint(
            ["source_id", "workspace_id"],
            ["sources.id", "sources.workspace_id"],
            name="fk_measurement_source_workspace",
        ),
        ForeignKeyConstraint(
            ["metric_id", "workspace_id"],
            ["metrics.id", "metrics.workspace_id"],
            name="fk_measurement_metric_workspace",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "created_by"],
            ["memberships.workspace_id", "memberships.user_id"],
            name="fk_measurement_creator_member",
        ),
    )
    case_id: Mapped[str] = mapped_column(String(36), index=True)
    workspace_id: Mapped[str] = mapped_column(String(36), index=True)
    source_id: Mapped[str] = mapped_column(String(36))
    metric_id: Mapped[str] = mapped_column(String(36))
    created_by: Mapped[str] = mapped_column(String(36))
    idempotency_key: Mapped[str] = mapped_column(String(100))
    date_from: Mapped[date] = mapped_column(Date)
    date_to: Mapped[date] = mapped_column(Date)
    store_ids: Mapped[list] = mapped_column(JSON)
    policy_revision: Mapped[int] = mapped_column(Integer)
    catalog_version: Mapped[int] = mapped_column(Integer)
    overview: Mapped[dict] = mapped_column(JSON)


class Comment(Identity, Base):
    __tablename__ = "case_comments"
    case_id: Mapped[str] = mapped_column(ForeignKey("cases.id"), index=True)
    author_id: Mapped[str] = mapped_column(ForeignKey("app_users.id"))
    body: Mapped[str] = mapped_column(Text)


class AssignedQuestion(Identity, Base):
    __tablename__ = "assigned_questions"
    case_id: Mapped[str] = mapped_column(ForeignKey("cases.id"), index=True)
    created_by: Mapped[str] = mapped_column(ForeignKey("app_users.id"))
    assignee_id: Mapped[str] = mapped_column(ForeignKey("app_users.id"))
    body: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(24), default="open")
    answer: Mapped[str | None] = mapped_column(Text, nullable=True)


class Notification(Identity, Base):
    __tablename__ = "notifications"
    __table_args__ = (
        CheckConstraint(
            "(case_id IS NOT NULL AND source_issue_id IS NULL) OR (case_id IS NULL AND source_issue_id IS NOT NULL)",
            name="ck_notification_target",
        ),
        ForeignKeyConstraint(
            ["case_id", "workspace_id"],
            ["cases.id", "cases.workspace_id"],
            name="fk_notification_case_workspace",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "user_id"],
            ["memberships.workspace_id", "memberships.user_id"],
            name="fk_notification_member",
        ),
        ForeignKeyConstraint(
            ["source_issue_id", "workspace_id"],
            ["source_issues.id", "source_issues.workspace_id"],
            name="fk_notification_source_issue_workspace",
        ),
    )
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"), index=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("app_users.id"), index=True)
    kind: Mapped[str] = mapped_column(String(32))
    title: Mapped[str] = mapped_column(String(200))
    body: Mapped[str] = mapped_column(Text, default="")
    case_id: Mapped[str | None] = mapped_column(ForeignKey("cases.id"), nullable=True)
    source_issue_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
