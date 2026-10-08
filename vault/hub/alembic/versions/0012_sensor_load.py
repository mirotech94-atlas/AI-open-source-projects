"""sensor.load1 + sensor.cores -- the sensor host's own load, for the sub-header

Task 70. The dashboard's sub-header renders the sensor host's one-minute load
average, which the sensor now reports in `last["self"]`. It lands on the
`sensor` row rather than being read back out of `Scan.raw` because `GET /live`
is polled once a second and already loads every `Sensor` row for its freshness
figure -- reading the load off that row costs no extra query, while
`scans.latest_scan` would drag fifty full scan payloads onto a polled endpoint.

Both columns are nullable and stay NULL for a sensor that predates the
reporting change, or one on a platform with no load average at all. NULL means
"unknown", which the sub-header renders as the freshness pill it showed before
this existed -- never as 0.0, which would read as an idle machine.

Guarded on the inspector like `0010_billing`, so running against a database
whose `sensor` table `Base.metadata.create_all` already built is a no-op.

Revision ID: 0011_sensor_load
Revises: 0010_billing
Create Date: 2026-09-10

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '0012_sensor_load'
down_revision: Union[str, Sequence[str], None] = '0011_user_onboarding'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _existing() -> set[str]:
    return {c["name"] for c in sa.inspect(op.get_bind()).get_columns("sensor")}


def upgrade() -> None:
    have = _existing()
    if "load1" not in have:
        op.add_column("sensor", sa.Column("load1", sa.Float(), nullable=True))
    if "cores" not in have:
        op.add_column("sensor", sa.Column("cores", sa.Integer(), nullable=True))


def downgrade() -> None:
    have = _existing()
    with op.batch_alter_table("sensor") as b:
        for name in ("cores", "load1"):
            if name in have:
                b.drop_column(name)
