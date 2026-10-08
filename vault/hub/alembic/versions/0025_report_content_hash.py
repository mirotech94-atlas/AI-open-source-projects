"""A report carries a checksum a reader can verify."""
from alembic import op
import sqlalchemy as sa

revision = "0025_report_content_hash"
down_revision = "0024_daily_facts"
branch_labels = depends_on = None


def upgrade():
    op.add_column("report", sa.Column("content_hash", sa.String(64),
                                      nullable=False, server_default=""))


def downgrade():
    op.drop_column("report", "content_hash")
