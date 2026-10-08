"""router -- backfill the disable Guardian already ran, from the queue

0020 added `remote_admin_off_at` and left it NULL, which is correct for a
column with no history. But the history existed: the disable had already run
on the live hub twice, and the record of it was sitting in `queue` -- a row
with path `/api/router/disable-remote-admin`, status `done`, and the sensor's
own message in `result.out`.

Without this the owner deploys the fix that stops the card nagging and the
card nags anyway, because the row it now reads is empty. The code was right
and the data was missing.

So this moves the evidence onto the row that now owns it: the newest COMPLETED
disable per tenant becomes that tenant's stamp. Only `done` rows count -- a
failed attempt says nothing about the router -- and only NULL stamps are
filled, so this never overwrites a later real run and is safe to re-run.

A separate revision rather than an edit to 0020 on purpose: 0020 has already
run in production, so an edit to it would never execute there, and a fresh
install replaying 0020 -> 0022 must end up in the same state as the hub that
has been running all along.

Revision ID: 0022_router_remote_admin_backfill
Revises: 0021_tickets
Create Date: 2026-09-18

"""
import json
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = '0022_router_remote_admin_backfill'
down_revision: Union[str, Sequence[str], None] = '0021_tickets'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_PATH = "/api/router/disable-remote-admin"


def _tables(bind) -> set[str]:
    return set(sa.inspect(bind).get_table_names())


def upgrade() -> None:
    bind = op.get_bind()
    have = _tables(bind)
    if "router" not in have or "queue" not in have:
        return

    rows = bind.execute(sa.text(
        "SELECT tenant_id, finished_at, result FROM queue "
        "WHERE path = :p AND status = 'done' AND finished_at IS NOT NULL "
        "ORDER BY finished_at ASC"
    ), {"p": _PATH}).fetchall()

    # Last one wins: the ASC ordering means the newest completed run for a
    # tenant is the one left in the dict.
    latest: dict[str, tuple[object, str]] = {}
    for tenant_id, finished_at, result in rows:
        out = ""
        if result:
            try:
                parsed = json.loads(result) if isinstance(result, str) else result
                out = str((parsed or {}).get("out") or "")[:300]
            except (TypeError, ValueError):
                out = ""
        latest[tenant_id] = (finished_at, out)

    for tenant_id, (finished_at, out) in latest.items():
        bind.execute(sa.text(
            "UPDATE router SET remote_admin_off_at = :at, "
            "remote_admin_msg = CASE WHEN remote_admin_msg = '' THEN :msg ELSE remote_admin_msg END "
            "WHERE tenant_id = :tid AND remote_admin_off_at IS NULL"
        ), {"at": finished_at, "msg": out, "tid": tenant_id})


def downgrade() -> None:
    # Nothing: this only ever fills a NULL from a record that still exists in
    # `queue`, so re-running the upgrade restores it. Clearing every stamp on
    # the way down would throw away a real run that happened after the
    # backfill.
    pass
