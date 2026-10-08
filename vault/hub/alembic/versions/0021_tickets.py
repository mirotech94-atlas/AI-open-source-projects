"""tickets + platform settings -- the owner console's two write surfaces

Two tables, because a ticket is a thread and a thread is rows: `ticket` holds
what it is about and who it is waiting on, `ticket_message` holds what was
actually said, by which side, and whether a notification went out for it.

`ticket_message.tenant_id` is denormalised on purpose: a tenant-scoped read
then never has to join to `ticket` to prove ownership, the same defence the
`alert` and `device` tables already use.

`platform_setting` is the operator-editable settings table: a key/value row
per setting so that adding one is a line of Python, not a migration. It never
holds a secret -- credentials stay in the environment.

No backfill: before this there were no tickets, and an absent settings row
means "use the environment/code default", which is what every install ran on.

Idempotent in the style of 0018/0019: the creates are guarded on the
inspector, so running this against a database whose tables
`Base.metadata.create_all` already made is a no-op.

Revision ID: 0021_tickets
Revises: 0020_router_remote_admin
Create Date: 2026-09-17

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = '0021_tickets'
down_revision: Union[str, Sequence[str], None] = '0020_router_remote_admin'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _tables() -> set[str]:
    return set(sa.inspect(op.get_bind()).get_table_names())


def _indexes(table: str) -> set[str]:
    return {i["name"] for i in sa.inspect(op.get_bind()).get_indexes(table)}


def upgrade() -> None:
    have = _tables()
    if "ticket" not in have:
        op.create_table(
            "ticket",
            sa.Column("id", sa.String(32), primary_key=True),
            sa.Column("tenant_id", sa.String(32),
                      sa.ForeignKey("tenant.id", ondelete="CASCADE"), nullable=False),
            sa.Column("subject", sa.String(160), nullable=False, server_default=""),
            sa.Column("status", sa.String(12), nullable=False, server_default="open"),
            sa.Column("priority", sa.String(8), nullable=False, server_default="normal"),
            sa.Column("opened_by", sa.String(64), nullable=False, server_default=""),
            sa.Column("created", sa.DateTime(timezone=True), nullable=False),
            sa.Column("last_message_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("last_author", sa.String(12), nullable=False, server_default="customer"),
            sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("resolved_by", sa.String(64), nullable=False, server_default=""),
        )
    idx = _indexes("ticket")
    if "ix_ticket_tenant_id" not in idx:
        op.create_index("ix_ticket_tenant_id", "ticket", ["tenant_id"])
    if "ix_ticket_status" not in idx:
        op.create_index("ix_ticket_status", "ticket", ["status"])

    if "ticket_message" not in _tables():
        op.create_table(
            "ticket_message",
            sa.Column("id", sa.String(32), primary_key=True),
            sa.Column("ticket_id", sa.String(32),
                      sa.ForeignKey("ticket.id", ondelete="CASCADE"), nullable=False),
            sa.Column("tenant_id", sa.String(32),
                      sa.ForeignKey("tenant.id", ondelete="CASCADE"), nullable=False),
            sa.Column("author_side", sa.String(12), nullable=False, server_default="customer"),
            sa.Column("author_name", sa.String(64), nullable=False, server_default=""),
            sa.Column("body", sa.Text(), nullable=False, server_default=""),
            sa.Column("created", sa.DateTime(timezone=True), nullable=False),
            sa.Column("emailed_to", sa.String(160), nullable=False, server_default=""),
        )
    midx = _indexes("ticket_message")
    if "ix_ticket_message_ticket_id" not in midx:
        op.create_index("ix_ticket_message_ticket_id", "ticket_message", ["ticket_id"])
    if "ix_ticket_message_tenant_id" not in midx:
        op.create_index("ix_ticket_message_tenant_id", "ticket_message", ["tenant_id"])
    if "ix_ticket_message_created" not in midx:
        op.create_index("ix_ticket_message_created", "ticket_message", ["created"])


    if "platform_setting" not in _tables():
        op.create_table(
            "platform_setting",
            sa.Column("key", sa.String(64), primary_key=True),
            sa.Column("value", sa.Text(), nullable=False, server_default=""),
            sa.Column("updated", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_by", sa.String(64), nullable=False, server_default=""),
        )


def downgrade() -> None:
    have = _tables()
    if "platform_setting" in have:
        op.drop_table("platform_setting")
    if "ticket_message" in have:
        op.drop_table("ticket_message")
    if "ticket" in have:
        op.drop_table("ticket")
