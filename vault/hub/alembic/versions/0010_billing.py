"""subscription + billing_event -- Guardian's own billing state

Task 68. Two tables, both new, nothing altered:

- `subscription`: one row per tenant (unique `tenant_id`) holding the plan
  id, the billing interval, Guardian's own status vocabulary, and the Stripe
  customer/subscription ids. No money column: prices live in
  `guardian_hub.plans` and nowhere else.
- `billing_event`: the Stripe webhook replay guard, keyed by Stripe's own
  event id so a retried delivery collides instead of being applied twice.

Idempotent in the same style as the migrations before it: every create is
guarded on the inspector, so running this against a database whose tables
`Base.metadata.create_all` already made (a dev box, or a fresh install that
booted before migrating) is a no-op rather than a crash.

No backfill. A tenant with no `subscription` row is treated as "no
subscription yet" by `guardian_hub.billing`, which is the correct reading for
every tenant that predates this migration -- the owner set those up by hand
and will place them on a plan (or start their trial) deliberately.

Revision ID: 0010_billing
Revises: 0009_leads
Create Date: 2026-09-10

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = '0010_billing'
down_revision: Union[str, Sequence[str], None] = '0009_leads'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _tables() -> set[str]:
    return set(sa.inspect(op.get_bind()).get_table_names())


def upgrade() -> None:
    have = _tables()

    if "subscription" not in have:
        op.create_table(
            "subscription",
            sa.Column("id", sa.String(32), primary_key=True),
            sa.Column("tenant_id", sa.String(32),
                      sa.ForeignKey("tenant.id", ondelete="CASCADE"), nullable=False),
            sa.Column("plan", sa.String(32), nullable=False, server_default="trial"),
            sa.Column("interval", sa.String(8), nullable=False, server_default="none"),
            sa.Column("status", sa.String(16), nullable=False, server_default="trialing"),
            sa.Column("trial_end", sa.DateTime(timezone=True), nullable=True),
            sa.Column("current_period_end", sa.DateTime(timezone=True), nullable=True),
            sa.Column("cancel_at_period_end", sa.Boolean(), nullable=False,
                      server_default=sa.false()),
            sa.Column("stripe_customer_id", sa.String(64), nullable=False, server_default=""),
            sa.Column("stripe_subscription_id", sa.String(64), nullable=False, server_default=""),
            sa.Column("last_event_created", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("trial_warned", sa.DateTime(timezone=True), nullable=True),
            sa.Column("created", sa.DateTime(timezone=True), nullable=True),
            sa.Column("updated", sa.DateTime(timezone=True), nullable=True),
        )
        # Unique, not merely indexed: "one subscription per tenant" is the
        # invariant the whole module rests on, and the database is the only
        # place that can actually hold it against two concurrent requests.
        op.create_index("ix_subscription_tenant_id", "subscription", ["tenant_id"], unique=True)
        op.create_index("ix_subscription_status", "subscription", ["status"])
        op.create_index("ix_subscription_stripe_subscription_id", "subscription",
                        ["stripe_subscription_id"])

    if "billing_event" not in have:
        op.create_table(
            "billing_event",
            sa.Column("id", sa.String(80), primary_key=True),
            sa.Column("type", sa.String(80), nullable=False, server_default=""),
            sa.Column("created", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("tenant_id", sa.String(32), nullable=True),
            sa.Column("received", sa.DateTime(timezone=True), nullable=True),
        )
        op.create_index("ix_billing_event_tenant_id", "billing_event", ["tenant_id"])
        op.create_index("ix_billing_event_received", "billing_event", ["received"])


def downgrade() -> None:
    have = _tables()
    if "billing_event" in have:
        op.drop_table("billing_event")
    if "subscription" in have:
        op.drop_table("subscription")
