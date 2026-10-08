"""Flows: who talked to whom, per minute, and their daily rollups; networks say how traffic reaches the sensor."""
from alembic import op
import sqlalchemy as sa

revision = "0029_flow"
down_revision = "0028_partner"
branch_labels = depends_on = None


def upgrade():
    op.create_table("flow",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("tenant_id", sa.String(32), nullable=False),
        sa.Column("network_id", sa.String(32), nullable=True),
        sa.Column("ts", sa.String(32), nullable=False),
        sa.Column("mac", sa.String(17), nullable=False),
        sa.Column("ip", sa.String(45), nullable=False, server_default=""),
        sa.Column("peer_ip", sa.String(45), nullable=False, server_default=""),
        sa.Column("peer_port", sa.Integer, nullable=False, server_default="0"),
        sa.Column("proto", sa.String(8), nullable=False, server_default="other"),
        sa.Column("bytes_in", sa.Integer, nullable=False, server_default="0"),
        sa.Column("bytes_out", sa.Integer, nullable=False, server_default="0"),
        sa.Column("pkts_in", sa.Integer, nullable=False, server_default="0"),
        sa.Column("pkts_out", sa.Integer, nullable=False, server_default="0"),
        sa.Column("peer_name", sa.String(255), nullable=False, server_default=""),
        sa.Column("hints", sa.JSON, nullable=False),
        sa.Column("source", sa.String(8), nullable=False, server_default="mirror"),
        sa.UniqueConstraint("tenant_id", "ts", "mac", "peer_ip", "peer_port", "proto", "source", name="uq_flow"),
    )
    op.create_index("ix_flow_ts", "flow", ["tenant_id", "ts"])
    op.create_index("ix_flow_mac", "flow", ["tenant_id", "mac", "ts"])
    op.create_index("ix_flow_network_id", "flow", ["network_id"])
    op.create_table("flow_rollup",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("tenant_id", sa.String(32), nullable=False),
        sa.Column("day", sa.String(10), nullable=False),
        sa.Column("network_id", sa.String(32), nullable=True),
        sa.Column("mac", sa.String(17), nullable=False),
        sa.Column("peer", sa.String(255), nullable=False),
        sa.Column("peer_port", sa.Integer, nullable=False, server_default="0"),
        sa.Column("proto", sa.String(8), nullable=False, server_default="other"),
        sa.Column("bytes_in", sa.Integer, nullable=False, server_default="0"),
        sa.Column("bytes_out", sa.Integer, nullable=False, server_default="0"),
        sa.Column("pkts", sa.Integer, nullable=False, server_default="0"),
        sa.UniqueConstraint("tenant_id", "day", "network_id", "mac", "peer", "peer_port", "proto"),
    )
    op.create_index("ix_flowroll", "flow_rollup", ["tenant_id", "day"])
    op.create_index("ix_flow_rollup_tenant_id", "flow_rollup", ["tenant_id"])
    with op.batch_alter_table("network") as b:
        b.add_column(sa.Column("traffic_mode", sa.String(8), nullable=False, server_default="none"))


def downgrade():
    with op.batch_alter_table("network") as b:
        b.drop_column("traffic_mode")
    op.drop_table("flow_rollup")
    op.drop_table("flow")
