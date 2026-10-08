"""Gateway firewall rules Guardian PROPOSES from what its scans found."""
from alembic import op
import sqlalchemy as sa

revision = "0031_firewall_proposal"
down_revision = "0030_ticket_context"
branch_labels = depends_on = None


def upgrade():
    insp = sa.inspect(op.get_bind())
    if "firewall_proposal" in insp.get_table_names():
        return
    op.create_table(
        "firewall_proposal",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("tenant_id", sa.String(32), nullable=False, index=True),
        sa.Column("network_id", sa.String(32), nullable=True),
        # The rule, in machine terms. `key` is what makes a proposal the
        # SAME proposal across scans, so a finding that persists does not
        # produce a new row every five minutes -- and a decision to decline
        # it keeps sticking.
        sa.Column("key", sa.String(160), nullable=False),
        sa.Column("action", sa.String(16), nullable=False, server_default="block_inbound"),
        sa.Column("proto", sa.String(8), nullable=False, server_default="tcp"),
        sa.Column("port", sa.Integer, nullable=False, server_default="0"),
        sa.Column("target", sa.String(64), nullable=False, server_default=""),
        # The rule and the reason for it, in the customer's words.
        sa.Column("title", sa.String(200), nullable=False, server_default=""),
        sa.Column("because", sa.Text, nullable=False, server_default=""),
        sa.Column("evidence", sa.Text, nullable=False, server_default=""),
        sa.Column("risk", sa.String(8), nullable=False, server_default="WARN"),
        # proposed | declined | applied | rolled_back | expired
        sa.Column("status", sa.String(16), nullable=False, server_default="proposed"),
        sa.Column("decided_by", sa.String(64), nullable=False, server_default=""),
        sa.Column("decided_at", sa.String(32), nullable=False, server_default=""),
        sa.Column("reason", sa.String(300), nullable=False, server_default=""),
        # An applied rule carries its own undo deadline. A firewall change
        # nobody confirmed is a change that locked somebody out at 2am.
        sa.Column("applied_at", sa.String(32), nullable=False, server_default=""),
        sa.Column("rollback_at", sa.String(32), nullable=False, server_default=""),
        sa.Column("first_seen", sa.String(32), nullable=False, server_default=""),
        sa.Column("last_seen", sa.String(32), nullable=False, server_default=""),
    )
    op.create_index("ix_fwprop_tenant_key", "firewall_proposal", ["tenant_id", "key"], unique=True)


def downgrade():
    insp = sa.inspect(op.get_bind())
    if "firewall_proposal" in insp.get_table_names():
        op.drop_index("ix_fwprop_tenant_key", table_name="firewall_proposal")
        op.drop_table("firewall_proposal")
