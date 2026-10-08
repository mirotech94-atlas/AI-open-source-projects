"""site.last_seen / host.last_seen -- when the sensor last reported this row

A Site or Host row is upserted from whatever the sensor sends and is never
reconciled against what it STOPPED sending. Drop a site from the sensor's
config and its row keeps its last result forever, presenting a months-old
check as a current finding. That is not hypothetical: `safehome.example.com`
sat on the Security page showing a failing CSP check against a header that
had already been fixed, because the site was no longer in the sensor's
config at all and nothing was re-checking it.

`last_seen` records the scan timestamp that last touched the row, so a row
the sensor has stopped reporting can be told apart from one that is being
checked and simply has not changed. A healthy site returns identical
evidence on every sweep -- unchanged evidence is success, not staleness, so
the freshness signal has to be the timestamp and not the content.

Nullable with no backfill on purpose: existing rows have no honest value to
claim, and `payloads.staleness` treats NULL as "not yet known" rather than
stale, so nothing is flagged until the next scan populates it.

Revision ID: 0016_asset_last_seen
Revises: 0015_check_decisions
Create Date: 2026-09-12

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = '0016_asset_last_seen'
down_revision: Union[str, Sequence[str], None] = '0015_check_decisions'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _columns(table: str) -> set[str]:
    return {c["name"] for c in sa.inspect(op.get_bind()).get_columns(table)}


def upgrade() -> None:
    # Idempotent in the style of 0014/0015: re-running must not fail.
    for table in ("site", "host"):
        if "last_seen" not in _columns(table):
            op.add_column(table, sa.Column("last_seen", sa.String(32), nullable=True))


def downgrade() -> None:
    for table in ("site", "host"):
        if "last_seen" in _columns(table):
            op.drop_column(table, "last_seen")
