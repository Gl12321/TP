from alembic import op
import sqlalchemy as sa


revision = "0005_source_issues"
down_revision = "0004_source_readers"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "source_issues",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("workspace_id", sa.String(36), nullable=False),
        sa.Column("source_id", sa.String(36), nullable=False),
        sa.Column("created_by", sa.String(36), nullable=False),
        sa.Column("assignee_id", sa.String(36), nullable=True),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("resolution", sa.Text(), nullable=True),
        sa.Column("source_status", sa.String(24), nullable=False),
        sa.Column("source_error", sa.String(400), nullable=True),
        sa.Column("last_checked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("status IN ('open', 'in_progress', 'resolved')", name="ck_issue_status"),
        sa.CheckConstraint(
            "status <> 'resolved' OR resolution IS NOT NULL", name="ck_issue_resolution"
        ),
        sa.UniqueConstraint("id", "workspace_id", name="uq_source_issue_workspace"),
        sa.ForeignKeyConstraint(
            ["source_id", "workspace_id"],
            ["sources.id", "sources.workspace_id"],
            name="fk_issue_source_workspace",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "created_by"],
            ["memberships.workspace_id", "memberships.user_id"],
            name="fk_issue_creator_member",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "assignee_id"],
            ["memberships.workspace_id", "memberships.user_id"],
            name="fk_issue_assignee_member",
        ),
    )
    op.create_index("ix_source_issues_workspace_id", "source_issues", ["workspace_id"])
    op.create_index("ix_source_issues_source_id", "source_issues", ["source_id"])
    op.create_table(
        "source_issue_comments",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("workspace_id", sa.String(36), nullable=False),
        sa.Column("issue_id", sa.String(36), nullable=False),
        sa.Column("author_id", sa.String(36), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(
            ["issue_id", "workspace_id"],
            ["source_issues.id", "source_issues.workspace_id"],
            name="fk_issue_comment_workspace",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "author_id"],
            ["memberships.workspace_id", "memberships.user_id"],
            name="fk_issue_comment_author_member",
        ),
    )
    op.create_index("ix_source_issue_comments_issue_id", "source_issue_comments", ["issue_id"])
    with op.batch_alter_table("notifications") as batch:
        batch.alter_column("case_id", existing_type=sa.String(36), nullable=True)
        batch.add_column(sa.Column("source_issue_id", sa.String(36), nullable=True))
        batch.create_foreign_key(
            "fk_notification_source_issue_workspace",
            "source_issues",
            ["source_issue_id", "workspace_id"],
            ["id", "workspace_id"],
        )
        batch.create_check_constraint(
            "ck_notification_target",
            "(case_id IS NOT NULL AND source_issue_id IS NULL) OR (case_id IS NULL AND source_issue_id IS NOT NULL)",
        )


def downgrade():
    op.execute("DELETE FROM notifications WHERE source_issue_id IS NOT NULL")
    with op.batch_alter_table("notifications") as batch:
        batch.drop_constraint("ck_notification_target", type_="check")
        batch.drop_constraint("fk_notification_source_issue_workspace", type_="foreignkey")
        batch.drop_column("source_issue_id")
        batch.alter_column("case_id", existing_type=sa.String(36), nullable=False)
    op.drop_index("ix_source_issue_comments_issue_id", table_name="source_issue_comments")
    op.drop_table("source_issue_comments")
    op.drop_index("ix_source_issues_source_id", table_name="source_issues")
    op.drop_index("ix_source_issues_workspace_id", table_name="source_issues")
    op.drop_table("source_issues")
