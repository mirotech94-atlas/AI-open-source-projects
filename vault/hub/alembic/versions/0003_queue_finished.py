"""queue.finished_at

Adds `queue.finished_at`: the real completion time set once by
`qresult` (POST /sensor/queue/{qid}/result), replacing the fabricated
`UpgradeProgress.finished = Queue.ts` (enqueue time) the Task 29 fix round
found. Nullable -- stays None until the sensor actually posts a result.

SQLite-safe: nullable column, no server default needed.

Revision ID: 0003_queue_finished
Revises: 0002_dashboard
Create Date: 2026-09-10

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '0003_queue_finished'
down_revision: Union[str, Sequence[str], None] = '0002_dashboard'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('queue', sa.Column('finished_at', sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table('queue') as batch:
        batch.drop_column('finished_at')
