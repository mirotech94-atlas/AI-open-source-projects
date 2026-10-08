"""user_onboarding -- first-run wizard state, per user per tenant

Task 71. One new table, nothing altered.

The wizard used to fire per *tenant* (no scan and no sensor on the overview
payload), so the second person to join an established tenant never saw it --
exactly the person who needs orientation most. Completion is a property of a
person, not of a site, and it cannot live in `localStorage` either: the same
user on a second machine has still seen the wizard.

Keyed `(user_id, tenant_id)`: what the wizard shows is tenant-specific, and a
user invited to a second site is genuinely new to that site.

Idempotent in the same style as the migrations before it -- the create is
guarded on the inspector, so running this against a database whose tables
`Base.metadata.create_all` already made (a dev box, or a fresh install that
booted before migrating) is a no-op rather than a crash.

No backfill. Every existing user reads as "not completed", which is the
correct reading: nobody has completed the *per-user* wizard, because until
now there was no such thing. The browser-local per-tenant "dismissed" flag
that used to stand in for it is reconciled once, client-side, on the next
load (`web/src/shell/Shell.tsx` posts `skipped` and deletes the key), so a
tenant that had already skipped is not dragged back into the wizard.

Revision ID: 0011_user_onboarding
Revises: 0010_billing
Create Date: 2026-09-10

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = '0011_user_onboarding'
down_revision: Union[str, Sequence[str], None] = '0010_billing'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _tables() -> set[str]:
    return set(sa.inspect(op.get_bind()).get_table_names())


def upgrade() -> None:
    if "user_onboarding" in _tables():
        return

    op.create_table(
        "user_onboarding",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("user_id", sa.String(32), sa.ForeignKey("user.id", ondelete="CASCADE"), nullable=False),
        sa.Column("tenant_id", sa.String(32), sa.ForeignKey("tenant.id", ondelete="CASCADE"), nullable=False),
        sa.Column("step", sa.String(32), nullable=False, server_default=""),
        sa.Column("completed", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("skipped", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("user_id", "tenant_id", name="uq_user_onboarding_user_tenant"),
    )
    op.create_index("ix_user_onboarding_user_id", "user_onboarding", ["user_id"])
    op.create_index("ix_user_onboarding_tenant_id", "user_onboarding", ["tenant_id"])


def downgrade() -> None:
    if "user_onboarding" not in _tables():
        return
    op.drop_index("ix_user_onboarding_tenant_id", table_name="user_onboarding")
    op.drop_index("ix_user_onboarding_user_id", table_name="user_onboarding")
    op.drop_table("user_onboarding")
