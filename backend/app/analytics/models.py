from datetime import date
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    Date,
    ForeignKey,
    ForeignKeyConstraint,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.infrastructure.database import Base, Identity


class Store(Identity, Base):
    __tablename__ = "stores"
    __table_args__ = (
        UniqueConstraint("workspace_id", "code"),
        UniqueConstraint("id", "workspace_id", name="uq_store_workspace"),
    )
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"), index=True)
    name: Mapped[str] = mapped_column(String(160))
    code: Mapped[str] = mapped_column(String(120))
    city: Mapped[str] = mapped_column(String(160), default="")
    owner_name: Mapped[str] = mapped_column(String(160), default="")
    active: Mapped[bool] = mapped_column(Boolean, default=True)


class Metric(Identity, Base):
    __tablename__ = "metrics"
    __table_args__ = (
        UniqueConstraint("workspace_id", "key", "version"),
        UniqueConstraint("id", "workspace_id", name="uq_metric_workspace"),
        ForeignKeyConstraint(
            ["source_id", "workspace_id"],
            ["sources.id", "sources.workspace_id"],
            name="fk_metric_source_workspace",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "created_by"],
            ["memberships.workspace_id", "memberships.user_id"],
            name="fk_metric_creator_member",
        ),
    )
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"), index=True)
    key: Mapped[str] = mapped_column(String(80))
    name: Mapped[str] = mapped_column(String(160))
    description: Mapped[str] = mapped_column(Text)
    unit: Mapped[str] = mapped_column(String(24), default="RUB")
    source_id: Mapped[str] = mapped_column(ForeignKey("sources.id"))
    table_schema: Mapped[str] = mapped_column(String(128))
    table_name: Mapped[str] = mapped_column(String(128))
    value_column: Mapped[str | None] = mapped_column(String(128), nullable=True)
    date_column: Mapped[str] = mapped_column(String(128))
    store_column: Mapped[str] = mapped_column(String(128))
    aggregation: Mapped[str] = mapped_column(String(12))
    version: Mapped[int] = mapped_column(Integer, default=1)
    created_by: Mapped[str] = mapped_column(ForeignKey("app_users.id"))


class Plan(Identity, Base):
    __tablename__ = "plans"
    __table_args__ = (
        UniqueConstraint("workspace_id", "store_id", "metric_id", "period", "version"),
        ForeignKeyConstraint(
            ["store_id", "workspace_id"],
            ["stores.id", "stores.workspace_id"],
            name="fk_plan_store_workspace",
        ),
        ForeignKeyConstraint(
            ["metric_id", "workspace_id"],
            ["metrics.id", "metrics.workspace_id"],
            name="fk_plan_metric_workspace",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "created_by"],
            ["memberships.workspace_id", "memberships.user_id"],
            name="fk_plan_creator_member",
        ),
    )
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"), index=True)
    store_id: Mapped[str] = mapped_column(ForeignKey("stores.id"))
    metric_id: Mapped[str] = mapped_column(ForeignKey("metrics.id"))
    period: Mapped[date] = mapped_column(Date)
    amount: Mapped[Decimal] = mapped_column(Numeric(20, 4))
    version: Mapped[int] = mapped_column(Integer, default=1)
    created_by: Mapped[str] = mapped_column(ForeignKey("app_users.id"))


class Report(Identity, Base):
    __tablename__ = "reports"
    __table_args__ = (
        ForeignKeyConstraint(
            ["run_id", "workspace_id"],
            ["query_runs.id", "query_runs.workspace_id"],
            name="fk_report_run_workspace",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "created_by"],
            ["memberships.workspace_id", "memberships.user_id"],
            name="fk_report_creator_member",
        ),
    )
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"), index=True)
    title: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(Text, default="")
    run_id: Mapped[str] = mapped_column(ForeignKey("query_runs.id"))
    created_by: Mapped[str] = mapped_column(ForeignKey("app_users.id"))
