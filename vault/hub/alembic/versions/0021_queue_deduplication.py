"""Prevent concurrent submissions of the same active request."""
from alembic import op
import sqlalchemy as sa
revision = "0021_queue_deduplication"
down_revision = "0020_operational_state"
branch_labels = depends_on = None

def upgrade():
    op.add_column("queue", sa.Column("fingerprint", sa.String(64), nullable=True))
    op.create_index("uq_queue_active_request", "queue", ["tenant_id", "fingerprint"], unique=True,
        sqlite_where=sa.text("status IN ('queued','scheduled','running')"),
        postgresql_where=sa.text("status IN ('queued','scheduled','running')"))

def downgrade():
    op.drop_index("uq_queue_active_request", table_name="queue")
    op.drop_column("queue", "fingerprint")
