from alembic import op
import sqlalchemy as sa


revision = "0003_measurements"
down_revision = "0002_workspace_references"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "case_measurements",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("case_id", sa.String(36), nullable=False),
        sa.Column("workspace_id", sa.String(36), nullable=False),
        sa.Column("source_id", sa.String(36), nullable=False),
        sa.Column("metric_id", sa.String(36), nullable=False),
        sa.Column("created_by", sa.String(36), nullable=False),
        sa.Column("idempotency_key", sa.String(100), nullable=False),
        sa.Column("date_from", sa.Date(), nullable=False),
        sa.Column("date_to", sa.Date(), nullable=False),
        sa.Column("store_ids", sa.JSON(), nullable=False),
        sa.Column("policy_revision", sa.Integer(), nullable=False),
        sa.Column("catalog_version", sa.Integer(), nullable=False),
        sa.Column("overview", sa.JSON(), nullable=False),
        sa.UniqueConstraint("case_id", "idempotency_key", name="uq_measurement_request"),
        sa.ForeignKeyConstraint(
            ["case_id", "workspace_id"],
            ["cases.id", "cases.workspace_id"],
            name="fk_measurement_case_workspace",
        ),
        sa.ForeignKeyConstraint(
            ["source_id", "workspace_id"],
            ["sources.id", "sources.workspace_id"],
            name="fk_measurement_source_workspace",
        ),
        sa.ForeignKeyConstraint(
            ["metric_id", "workspace_id"],
            ["metrics.id", "metrics.workspace_id"],
            name="fk_measurement_metric_workspace",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "created_by"],
            ["memberships.workspace_id", "memberships.user_id"],
            name="fk_measurement_creator_member",
        ),
    )
    op.create_index("ix_case_measurements_case_id", "case_measurements", ["case_id"])
    op.create_index("ix_case_measurements_workspace_id", "case_measurements", ["workspace_id"])


def downgrade():
    op.drop_index("ix_case_measurements_workspace_id", table_name="case_measurements")
    op.drop_index("ix_case_measurements_case_id", table_name="case_measurements")
    op.drop_table("case_measurements")
