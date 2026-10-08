"""Record job ownership, scheduling, and execution time without inventing old start times."""
from alembic import op
import sqlalchemy as sa
revision = "0018_queue_lifecycle"
down_revision = "0017_tenant_foreign_keys"
branch_labels = depends_on = None
COLUMNS = {"sensor_id": sa.String(32), "started_at": sa.DateTime(),
           "heartbeat_at": sa.DateTime(), "not_before": sa.DateTime(),
           "expires_at": sa.DateTime(), "wait_reason": sa.String(240)}
def upgrade():
    existing = {c["name"] for c in sa.inspect(op.get_bind()).get_columns("queue")}
    for name, kind in COLUMNS.items():
        if name not in existing:
            op.add_column("queue", sa.Column(name, kind, nullable=True))
def downgrade():
    for name in reversed(COLUMNS):
        op.drop_column("queue", name)
