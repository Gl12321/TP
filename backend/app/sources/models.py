from datetime import datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
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


class SourceIssue(Identity, Base):
    __tablename__ = "source_issues"
    __table_args__ = (
        CheckConstraint("status IN ('open', 'in_progress', 'resolved')", name="ck_issue_status"),
        CheckConstraint(
            "status <> 'resolved' OR resolution IS NOT NULL", name="ck_issue_resolution"
        ),
        UniqueConstraint("id", "workspace_id", name="uq_source_issue_workspace"),
        ForeignKeyConstraint(
            ["source_id", "workspace_id"],
            ["sources.id", "sources.workspace_id"],
            name="fk_issue_source_workspace",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "created_by"],
            ["memberships.workspace_id", "memberships.user_id"],
            name="fk_issue_creator_member",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "assignee_id"],
            ["memberships.workspace_id", "memberships.user_id"],
            name="fk_issue_assignee_member",
        ),
    )
    workspace_id: Mapped[str] = mapped_column(String(36), index=True)
    source_id: Mapped[str] = mapped_column(String(36), index=True)
    created_by: Mapped[str] = mapped_column(String(36))
    assignee_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    title: Mapped[str] = mapped_column(String(200))
    body: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(24), default="open")
    resolution: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_status: Mapped[str] = mapped_column(String(24))
    source_error: Mapped[str | None] = mapped_column(String(400), nullable=True)
    last_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class IssueComment(Identity, Base):
    __tablename__ = "source_issue_comments"
    __table_args__ = (
        ForeignKeyConstraint(
            ["issue_id", "workspace_id"],
            ["source_issues.id", "source_issues.workspace_id"],
            name="fk_issue_comment_workspace",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "author_id"],
            ["memberships.workspace_id", "memberships.user_id"],
            name="fk_issue_comment_author_member",
        ),
    )
    workspace_id: Mapped[str] = mapped_column(String(36))
    issue_id: Mapped[str] = mapped_column(String(36), index=True)
    author_id: Mapped[str] = mapped_column(String(36))
    body: Mapped[str] = mapped_column(Text)
