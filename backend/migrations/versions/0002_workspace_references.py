from collections import defaultdict

from alembic import op
import sqlalchemy as sa


revision = "0002_workspace_references"
down_revision = "0001_workspace"
branch_labels = None
depends_on = None


UNIQUES = (
    ("sources", "uq_source_workspace", ("id", "workspace_id")),
    ("stores", "uq_store_workspace", ("id", "workspace_id")),
    ("metrics", "uq_metric_workspace", ("id", "workspace_id")),
    ("conversations", "uq_conversation_owner", ("id", "workspace_id", "user_id")),
    ("query_runs", "uq_run_workspace", ("id", "workspace_id")),
    ("query_runs", "uq_run_conversation", ("id", "conversation_id")),
    (
        "query_runs",
        "uq_run_context",
        ("id", "workspace_id", "conversation_id", "source_id", "user_id"),
    ),
    ("cases", "uq_case_workspace", ("id", "workspace_id")),
)
REFERENCES = (
    (
        "metrics",
        "fk_metric_source_workspace",
        ("source_id", "workspace_id"),
        "sources",
        ("id", "workspace_id"),
    ),
    (
        "metrics",
        "fk_metric_creator_member",
        ("workspace_id", "created_by"),
        "memberships",
        ("workspace_id", "user_id"),
    ),
    (
        "conversations",
        "fk_conversation_member",
        ("workspace_id", "user_id"),
        "memberships",
        ("workspace_id", "user_id"),
    ),
    (
        "query_runs",
        "fk_run_source_workspace",
        ("source_id", "workspace_id"),
        "sources",
        ("id", "workspace_id"),
    ),
    (
        "query_runs",
        "fk_run_conversation_owner",
        ("conversation_id", "workspace_id", "user_id"),
        "conversations",
        ("id", "workspace_id", "user_id"),
    ),
    (
        "query_runs",
        "fk_run_base_context",
        ("base_run_id", "workspace_id", "conversation_id", "source_id", "user_id"),
        "query_runs",
        ("id", "workspace_id", "conversation_id", "source_id", "user_id"),
    ),
    (
        "plans",
        "fk_plan_store_workspace",
        ("store_id", "workspace_id"),
        "stores",
        ("id", "workspace_id"),
    ),
    (
        "plans",
        "fk_plan_metric_workspace",
        ("metric_id", "workspace_id"),
        "metrics",
        ("id", "workspace_id"),
    ),
    (
        "plans",
        "fk_plan_creator_member",
        ("workspace_id", "created_by"),
        "memberships",
        ("workspace_id", "user_id"),
    ),
    (
        "reports",
        "fk_report_run_workspace",
        ("run_id", "workspace_id"),
        "query_runs",
        ("id", "workspace_id"),
    ),
    (
        "reports",
        "fk_report_creator_member",
        ("workspace_id", "created_by"),
        "memberships",
        ("workspace_id", "user_id"),
    ),
    (
        "cases",
        "fk_case_run_workspace",
        ("run_id", "workspace_id"),
        "query_runs",
        ("id", "workspace_id"),
    ),
    (
        "cases",
        "fk_case_creator_member",
        ("workspace_id", "created_by"),
        "memberships",
        ("workspace_id", "user_id"),
    ),
    (
        "cases",
        "fk_case_assignee_member",
        ("workspace_id", "assignee_id"),
        "memberships",
        ("workspace_id", "user_id"),
    ),
    (
        "notifications",
        "fk_notification_case_workspace",
        ("case_id", "workspace_id"),
        "cases",
        ("id", "workspace_id"),
    ),
    (
        "notifications",
        "fk_notification_member",
        ("workspace_id", "user_id"),
        "memberships",
        ("workspace_id", "user_id"),
    ),
    (
        "conversation_messages",
        "fk_message_run_conversation",
        ("run_id", "conversation_id"),
        "query_runs",
        ("id", "conversation_id"),
    ),
)


def verify_existing_references():
    if op.get_context().as_sql:
        return
    connection = op.get_bind()
    for table_name, name, local, target_name, remote in REFERENCES:
        source = sa.table(table_name, *(sa.column(column) for column in set(local)))
        target = sa.table(target_name, *(sa.column(column) for column in set(remote))).alias(
            "target"
        )
        match = sa.and_(
            *(source.c[left] == target.c[right] for left, right in zip(local, remote, strict=True))
        )
        populated = sa.and_(*(source.c[column].is_not(None) for column in local))
        missing = (
            sa.select(sa.literal(1))
            .select_from(source)
            .where(populated, ~sa.exists(sa.select(sa.literal(1)).select_from(target).where(match)))
            .limit(1)
        )
        if connection.execute(missing).first() is not None:
            raise RuntimeError(
                f"Cannot enforce {name}: existing rows cross workspace or conversation boundaries"
            )


def check_sqlite_foreign_keys():
    if op.get_context().as_sql:
        return
    connection = op.get_bind()
    if (
        connection.dialect.name == "sqlite"
        and connection.exec_driver_sql("PRAGMA foreign_key_check").first() is not None
    ):
        raise RuntimeError("SQLite foreign-key validation failed after rebuilding tables")


def upgrade():
    verify_existing_references()
    unique_groups = defaultdict(list)
    for table, name, columns in UNIQUES:
        unique_groups[table].append((name, columns))
    for table, constraints in unique_groups.items():
        with op.batch_alter_table(table) as batch:
            for name, columns in constraints:
                batch.create_unique_constraint(name, columns)
    foreign_groups = defaultdict(list)
    for table, name, local, target, remote in REFERENCES:
        foreign_groups[table].append((name, local, target, remote))
    for table, constraints in foreign_groups.items():
        with op.batch_alter_table(table) as batch:
            for name, local, target, remote in constraints:
                batch.create_foreign_key(name, target, local, remote)
    check_sqlite_foreign_keys()


def downgrade():
    foreign_groups = defaultdict(list)
    for table, name, _, _, _ in reversed(REFERENCES):
        foreign_groups[table].append(name)
    for table, names in foreign_groups.items():
        with op.batch_alter_table(table) as batch:
            for name in names:
                batch.drop_constraint(name, type_="foreignkey")
    unique_groups = defaultdict(list)
    for table, name, _ in reversed(UNIQUES):
        unique_groups[table].append(name)
    for table, names in unique_groups.items():
        with op.batch_alter_table(table) as batch:
            for name in names:
                batch.drop_constraint(name, type_="unique")
    check_sqlite_foreign_keys()
