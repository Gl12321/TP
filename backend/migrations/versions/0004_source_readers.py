from alembic import op
import sqlalchemy as sa


revision = "0004_source_readers"
down_revision = "0003_measurements"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("sources", sa.Column("reader_ids", sa.JSON(), nullable=True))


def downgrade():
    op.drop_column("sources", "reader_ids")
