"""Availability figures that stay the number the customer was shown."""
from alembic import op
import sqlalchemy as sa

revision = "0024_daily_facts"
down_revision = "0023_merge_heads"
branch_labels = depends_on = None


def upgrade():
    op.create_table(
        "daily_fact",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("tenant_id", sa.String(32), nullable=False, index=True),
        sa.Column("day", sa.String(10), nullable=False),
        sa.Column("monitor", sa.String(120), nullable=False),
        sa.Column("checks", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("up", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("planned", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("uptime_pct", sa.Float(), nullable=True),
        sa.UniqueConstraint("tenant_id", "day", "monitor", name="uq_daily_fact_day_monitor"),
    )


def downgrade():
    op.drop_table("daily_fact")
