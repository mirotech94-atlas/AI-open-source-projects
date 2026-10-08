"""alert escalated_at column

Task 40 fix round 1, finding 4: ntfy's CRIT resend ladder needs a real
"last resent at" timestamp per alert to hold a genuine 6h cadence -- the
hourly watchdog tick alone can't tell "just escalated" from "escalated 5
ticks ago" without it.

Numbering note: this landed as "0003_alert_escalated_at" on the phase-4-b
branch (where 0001 and 0002 were the only migrations), then was renumbered
to 0005 when the phase4-hub merge brought in `0003_queue_finished` and
`0004_chat_threads`. The chain is 0001 -> 0002 -> 0003_queue_finished ->
0004_chat_threads -> 0005_alert_escalated_at.

Revision ID: 0005_alert_escalated_at
Revises: 0004_chat_threads
Create Date: 2026-09-10

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '0005_alert_escalated_at'
down_revision: Union[str, Sequence[str], None] = '0004_chat_threads'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('alert', sa.Column('escalated_at', sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('alert') as batch:
        batch.drop_column('escalated_at')
