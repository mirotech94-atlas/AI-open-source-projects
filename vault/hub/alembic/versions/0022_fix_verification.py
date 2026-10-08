"""Distinguish automatic commands from verified recovery."""
from alembic import op
import sqlalchemy as sa
revision = "0022_fix_verification"
down_revision = "0021_queue_deduplication"
branch_labels = depends_on = None

def upgrade():
    op.add_column("fix", sa.Column("sensor_id",sa.String(32),nullable=True))
    op.add_column("fix", sa.Column("verified_at",sa.DateTime(),nullable=True))

def downgrade():
    op.drop_column("fix","verified_at")
    op.drop_column("fix","sensor_id")
