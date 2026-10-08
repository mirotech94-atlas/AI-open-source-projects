"""sensor.network_id

Task 51 round 2: `POST /api/v1/sensors/enrol-code` can now name the network
the resulting sensor should bind to once it redeems the code. Nullable and
additive -- every enrol code minted before this column existed, and every one
minted without a `network_id`, simply binds nothing, exactly as it always has.

Revision ID: 0008_sensor_network_id
Revises: 0007_networks
Create Date: 2026-09-10

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '0008_sensor_network_id'
down_revision: Union[str, Sequence[str], None] = '0007_networks'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("sensor", sa.Column("network_id", sa.String(32), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("sensor") as b:
        b.drop_column("network_id")
