"""router -- the site gateway router Guardian can administer (ONE per tenant)

Only the enrolled sensor on the customer LAN can reach a home gateway
(192.168.x.x); the hub never can. This table holds the router admin credential
(as one Fernet blob in `secret_enc`, never a plaintext column) so the sensor
can pull it in the clear over its authenticated channel and run a login+logout
credential Test. Nothing here mutates the router yet.

No backfill: a deployment that predates this simply has no router row, which is
the correct reading.

Idempotent: the create is guarded on the inspector, so running this against a
database whose tables `Base.metadata.create_all` already made is a no-op.

Revision ID: 0018_router
Revises: 0017_tenant_foreign_keys
Create Date: 2026-09-15

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = '0018_router'
down_revision: Union[str, Sequence[str], None] = '0017_tenant_foreign_keys'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _tables() -> set[str]:
    return set(sa.inspect(op.get_bind()).get_table_names())


def upgrade() -> None:
    if "router" in _tables():
        return
    op.create_table(
        "router",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("tenant_id", sa.String(32),
                  sa.ForeignKey("tenant.id", ondelete="CASCADE"), nullable=False),
        sa.Column("vendor", sa.String(24), nullable=False, server_default="fios"),
        sa.Column("base_url", sa.String(128), nullable=False, server_default=""),
        sa.Column("username", sa.String(64), nullable=False, server_default=""),
        sa.Column("secret_enc", sa.Text(), nullable=False, server_default=""),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("status", sa.String(16), nullable=False, server_default="pending"),
        sa.Column("last_test_ok", sa.Boolean(), nullable=True),
        sa.Column("last_test_msg", sa.String(300), nullable=False, server_default=""),
        sa.Column("last_test", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("tenant_id", name="uq_router_tenant"),
    )
    op.create_index("ix_router_tenant_id", "router", ["tenant_id"])


def downgrade() -> None:
    if "router" not in _tables():
        return
    op.drop_index("ix_router_tenant_id", table_name="router")
    op.drop_table("router")
