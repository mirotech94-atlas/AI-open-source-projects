"""router -- remember that Guardian turned remote administration off

`POST /api/v1/router/disable-remote-admin` queued the change, the sensor ran
it, and nothing wrote the outcome down. So the Integrations card went on
offering "Turn off remote administration" forever, to an owner who had already
pressed it and watched it succeed.

These two columns are the memory. They record WHAT GUARDIAN DID, not what the
router currently is: the Fios config API is a write-only diff model with no
getter, so the hub cannot read the live value back. The card therefore says
"Guardian turned this off on <date>" and keeps a quiet way to run it again.

Backfill: NULL everywhere, which is the correct reading -- before this column
existed the hub had no record of any disable run.

Idempotent in the style of 0019: each add is guarded on the inspector.

Revision ID: 0020_router_remote_admin
Revises: 0019_lead_decision
Create Date: 2026-09-17

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = '0020_router_remote_admin'
down_revision: Union[str, Sequence[str], None] = '0019_lead_decision'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _tables() -> set[str]:
    return set(sa.inspect(op.get_bind()).get_table_names())


def _columns(table: str) -> set[str]:
    return {c["name"] for c in sa.inspect(op.get_bind()).get_columns(table)}


_ADD = [
    sa.Column("remote_admin_off_at", sa.DateTime(timezone=True), nullable=True),
    sa.Column("remote_admin_msg", sa.String(300), nullable=False, server_default=""),
]


def upgrade() -> None:
    if "router" not in _tables():
        return
    have = _columns("router")
    for col in _ADD:
        if col.name not in have:
            op.add_column("router", col)


def downgrade() -> None:
    if "router" not in _tables():
        return
    have = _columns("router")
    for col in reversed(_ADD):
        if col.name in have:
            op.drop_column("router", col.name)
