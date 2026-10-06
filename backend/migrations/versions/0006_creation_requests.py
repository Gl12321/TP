from alembic import op
import sqlalchemy as sa


revision = "0006_creation_requests"
down_revision = "0005_source_issues"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "creation_requests",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("workspace_id", sa.String(36), nullable=False),
        sa.Column("created_by", sa.String(36), nullable=False),
        sa.Column("key", sa.String(100), nullable=False),
        sa.Column("operation", sa.String(24), nullable=False),
        sa.Column("fingerprint", sa.String(64), nullable=False),
        sa.Column("target_id", sa.String(36), nullable=False),
        sa.UniqueConstraint("workspace_id", "created_by", "key", name="uq_creation_request"),
        sa.ForeignKeyConstraint(
            ["workspace_id", "created_by"],
            ["memberships.workspace_id", "memberships.user_id"],
            name="fk_creation_request_member",
        ),
        sa.CheckConstraint(
            "operation IN ('report', 'case', 'source_issue')", name="ck_creation_operation"
        ),
    )


def downgrade():
    op.drop_table("creation_requests")
