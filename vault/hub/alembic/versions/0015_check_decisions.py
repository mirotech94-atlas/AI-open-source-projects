"""check_decision -- "resolve or leave it" on a Security-page check

The Wi-Fi guard drill-down (owner request: clicking a failing check like
"evil twin" should open detail and let the owner resolve it or leave it,
same for Security posture). One new table, nothing altered.

A `SecurityCheck`/`Scorecard` deduction is computed fresh from `Scan.raw` on
every `/api/v1/security` request -- there is no `Alert` row behind a Wi-Fi
guard check (see `guardian_hub/remedy.py`'s module docstring) and no `id` at
all. `check_decision` is therefore keyed on the check's own natural key
(`check_key` + `network_id` + `target`) rather than a foreign key to
anything, and it records only the "leave it" decision: an autofix-backed
check resolves itself the moment the next scan shows it passing, so there is
no "resolved" status to persist here, only "open" (the default / after
"undo") and "left" (a deliberate choice to leave a failing check as-is).

Idempotent in the same style as 0014: the create is guarded on the
inspector.

Revision ID: 0015_check_decisions
Revises: 0014_api_tokens
Create Date: 2026-09-11

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = '0015_check_decisions'
down_revision: Union[str, Sequence[str], None] = '0014_api_tokens'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _tables() -> set[str]:
    return set(sa.inspect(op.get_bind()).get_table_names())


def upgrade() -> None:
    if "check_decision" in _tables():
        return

    op.create_table(
        "check_decision",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("tenant_id", sa.String(32),
                  sa.ForeignKey("tenant.id", ondelete="CASCADE"), nullable=False),
        sa.Column("check_key", sa.String(120), nullable=False),
        sa.Column("network_id", sa.String(32), nullable=False, server_default=""),
        sa.Column("target", sa.String(200), nullable=False, server_default=""),
        sa.Column("status", sa.String(12), nullable=False, server_default="open"),
        sa.Column("reason", sa.Text(), nullable=False, server_default=""),
        sa.Column("decided_by", sa.String(64), nullable=False, server_default=""),
        sa.Column("decided_role", sa.String(16), nullable=False, server_default=""),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        # Declared inline rather than added afterwards with
        # `op.create_unique_constraint`. SQLite has no ALTER for constraints,
        # so that call raises NotImplementedError and the whole migration
        # fails -- which is exactly what it did on the first deploy. A
        # constraint that is part of CREATE TABLE needs no ALTER at all.
        sa.UniqueConstraint("tenant_id", "check_key", "network_id", "target",
                            name="uq_check_decision_natural_key"),
    )
    op.create_index("ix_check_decision_tenant_id", "check_decision", ["tenant_id"])


def downgrade() -> None:
    if "check_decision" in _tables():
        op.drop_table("check_decision")
