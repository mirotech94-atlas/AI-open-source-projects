"""lead decision -- status, decided_at, decided_by, tenant_id, site_name, terms_accepted_at on `lead`

The public "Request access" form used to end at a row and a Slack ping. Now
the owner approves or rejects each lead from the app and approval provisions
a tenant, so the row records the decision and the tenant it produced.

Backfill: every existing row is `pending` (the column default), which is the
correct reading -- nothing before this had been decided.

Idempotent in the style of 0003/0018: each add is guarded on the inspector.

Revision ID: 0019_lead_decision
Revises: 0018_router
Create Date: 2026-09-16

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = '0019_lead_decision'
down_revision: Union[str, Sequence[str], None] = '0018_router'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _columns(table: str) -> set[str]:
    return {c["name"] for c in sa.inspect(op.get_bind()).get_columns(table)}


def _indexes(table: str) -> set[str]:
    return {i["name"] for i in sa.inspect(op.get_bind()).get_indexes(table)}


_ADD = [
    sa.Column("site_name", sa.String(120), nullable=False, server_default=""),
    sa.Column("status", sa.String(12), nullable=False, server_default="pending"),
    sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
    sa.Column("decided_by", sa.String(64), nullable=False, server_default=""),
    sa.Column("tenant_id", sa.String(32), nullable=True),
    sa.Column("terms_accepted_at", sa.DateTime(timezone=True), nullable=True),
]

# The name SQLAlchemy's default naming convention gives `Lead.status`'s
# `index=True` (confirmed against `Base.metadata.create_all`'s own output).
_STATUS_INDEX = "ix_lead_status"


def upgrade() -> None:
    have = _columns("lead")
    for col in _ADD:
        if col.name not in have:
            op.add_column("lead", col)
    # SQLite cannot add a FK to an existing table without a rebuild; the ORM
    # relationship is enforced by the hub (`ondelete="SET NULL"` in models.py)
    # and the column is nullable, so no constraint is added here.
    if _STATUS_INDEX not in _indexes("lead"):
        op.create_index(_STATUS_INDEX, "lead", ["status"])


def downgrade() -> None:
    if _STATUS_INDEX in _indexes("lead"):
        op.drop_index(_STATUS_INDEX, table_name="lead")
    have = _columns("lead")
    for col in reversed(_ADD):
        if col.name in have:
            op.drop_column("lead", col.name)
