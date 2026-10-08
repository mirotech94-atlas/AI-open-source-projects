"""Outages the ISP can be billed for, kept as evidence."""
from alembic import op
import sqlalchemy as sa

revision = "0026_wan_outage"
down_revision = "0025_report_content_hash"
branch_labels = depends_on = None


def upgrade():
    op.create_table(
        "wan_outage",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("tenant_id", sa.String(32), nullable=False, index=True),
        sa.Column("started_at", sa.String(32), nullable=False),
        sa.Column("ended_at", sa.String(32), nullable=True),
        sa.Column("cause", sa.String(12), nullable=False, server_default="unknown"),
        sa.Column("evidence", sa.JSON(), nullable=True),
        sa.UniqueConstraint("tenant_id", "started_at", name="uq_wan_outage_start"),
    )


def downgrade():
    op.drop_table("wan_outage")
