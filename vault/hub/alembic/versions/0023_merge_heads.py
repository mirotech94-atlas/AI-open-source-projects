"""Rejoin the two migration lines that grew on separate branches.

`0017_tenant_foreign_keys` was the last revision both lines agreed on. From
there the history forked and each side numbered its own revisions without
seeing the other's, so the repository ended up with two 0018s, two 0019s,
two 0020s, two 0021s and two 0022s:

    0017 ─┬─ 0018_queue_lifecycle ─ 0019_personal_preferences ─
          │    0020_operational_state ─ 0021_queue_deduplication ─
          │    0022_fix_verification
          └─ 0018_router ─ 0019_lead_decision ─ 0020_router_remote_admin ─
               0021_tickets ─ 0022_router_remote_admin_backfill

Two heads is not a cosmetic problem: `alembic upgrade head` refuses to run
at all with "Multiple head revisions are present", and `guardian migrate`
asks for exactly that target, so the deploy path was broken on this branch
from the moment the two lines were merged into one tree. It fails closed,
which is the good version of this bug -- nothing was half-migrated -- but it
fails for every deployment until the heads are joined.

This revision has no schema of its own. Both parents are additive and touch
different tables, so there is nothing to reconcile; the only job here is to
give Alembic one head to walk to. `tests/test_alembic_single_head.py` now
fails the suite if a third line ever appears, because the next person to
merge two branches will not think to check.
"""
revision = "0023_merge_heads"
down_revision = ("0022_fix_verification", "0022_router_remote_admin_backfill")
branch_labels = depends_on = None


def upgrade():
    """Nothing to do: a merge point, not a change."""


def downgrade():
    """Nothing to undo. Splitting back into two heads would only recreate
    the state that stopped `upgrade head` from running."""
