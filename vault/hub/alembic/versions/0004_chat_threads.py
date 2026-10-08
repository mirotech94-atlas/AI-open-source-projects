"""chat_thread table

Adds `chat_thread` for Guardian chat (Task 44): one row per conversation,
keyed by tenant + user, `turns` a JSON list of {"role", "text"} the
provider ladder replays as message history on every subsequent call. The
web sends only `ChatRequest.thread` (this row's id) back on each turn, not
the whole history.

Numbering note: this landed as "0003_chat_threads" on the phase-4 branch
(where 0001 and 0002 were the only migrations), then was renumbered to
0004 when the phase-3 merge brought in `0003_queue_finished`. The chain is
0001 -> 0002 -> 0003_queue_finished -> 0004_chat_threads. Whichever task
adds the networks migration should chain off this one
(down_revision = '0004_chat_threads') and be numbered 0005.

Revision ID: 0004_chat_threads
Revises: 0003_queue_finished
Create Date: 2026-09-10

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '0004_chat_threads'
down_revision: Union[str, Sequence[str], None] = '0003_queue_finished'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table('chat_thread',
    sa.Column('id', sa.String(length=32), nullable=False),
    sa.Column('tenant_id', sa.String(length=32), nullable=False),
    sa.Column('user_id', sa.String(length=32), nullable=False),
    sa.Column('turns', sa.JSON(), nullable=False),
    sa.Column('created', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated', sa.DateTime(timezone=True), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_chat_thread_tenant_id'), 'chat_thread', ['tenant_id'], unique=False)
    op.create_index(op.f('ix_chat_thread_user_id'), 'chat_thread', ['user_id'], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(op.f('ix_chat_thread_user_id'), table_name='chat_thread')
    op.drop_index(op.f('ix_chat_thread_tenant_id'), table_name='chat_thread')
    op.drop_table('chat_thread')
