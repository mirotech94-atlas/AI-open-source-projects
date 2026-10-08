"""recorder -- camera recorders (NVR/DVR) whose channels Guardian enumerates

Task 73. One table, nothing altered.

A house with ten or twelve cameras has them behind the recorder's own PoE
network, invisible to any ARP sweep of the LAN. `recorder` holds the boxes to
ask, including the credential (as one Fernet blob in `secret_enc`, never a
plaintext column), and `network_id` points at the `Network` row the cameras
behind it live on -- so each camera is an ordinary `device` row and every
existing surface works unchanged.

No backfill: a deployment that predates this has no recorders, which is the
correct reading. The `Network` rows for camera networks are created by
`guardian_hub.recorders` when a recorder is registered, not here.

Idempotent in the same style as the migrations before it: the create is
guarded on the inspector, so running this against a database whose tables
`Base.metadata.create_all` already made is a no-op rather than a crash.

Revision ID: 0011_recorders
Revises: 0010_billing
Create Date: 2026-09-10

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = '0013_recorders'
down_revision: Union[str, Sequence[str], None] = '0012_sensor_load'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _tables() -> set[str]:
    return set(sa.inspect(op.get_bind()).get_table_names())


def upgrade() -> None:
    if "recorder" in _tables():
        return
    op.create_table(
        "recorder",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("tenant_id", sa.String(32),
                  sa.ForeignKey("tenant.id", ondelete="CASCADE"), nullable=False),
        sa.Column("network_id", sa.String(32), nullable=True),
        sa.Column("lan_network_id", sa.String(32), nullable=True),
        sa.Column("name", sa.String(120), nullable=False, server_default=""),
        sa.Column("vendor", sa.String(24), nullable=False, server_default="reolink"),
        sa.Column("host", sa.String(45), nullable=False, server_default=""),
        sa.Column("port", sa.Integer(), nullable=False, server_default="80"),
        sa.Column("scheme", sa.String(8), nullable=False, server_default="http"),
        sa.Column("username", sa.String(64), nullable=False, server_default=""),
        sa.Column("secret_enc", sa.Text(), nullable=False, server_default=""),
        sa.Column("expected_channels", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("status", sa.String(16), nullable=False, server_default="pending"),
        sa.Column("error_kind", sa.String(24), nullable=False, server_default=""),
        sa.Column("error", sa.Text(), nullable=False, server_default=""),
        sa.Column("model", sa.String(64), nullable=False, server_default=""),
        sa.Column("firmware", sa.String(64), nullable=False, server_default=""),
        sa.Column("channels", sa.JSON(), nullable=True),
        sa.Column("last_ok", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_attempt", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("tenant_id", "host", name="uq_recorder_host"),
    )
    op.create_index("ix_recorder_tenant_id", "recorder", ["tenant_id"])
    op.create_index("ix_recorder_network_id", "recorder", ["network_id"])


def downgrade() -> None:
    if "recorder" in _tables():
        op.drop_table("recorder")
