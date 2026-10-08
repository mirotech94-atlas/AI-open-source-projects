"""dashboard tables and columns

Adds the four schema additions phases 2-4 need, in one migration:
- presence_sample: one row per (history line, live mac) -- presence profiles,
  online percentages, the hour heatmap and screen time all read it.
- ip_geo: ip-api.com lookup cache for the attacker map.
- act_token: one-shot tokens behind ntfy / Telegram action buttons.
- columns: dns_query.mac, alert.ticket_key, report.title/meta and the
  integration fields (name, config, inbound_token, last_test, last_error)
  plus a widened integration.kind.

SQLite-safe: every added column is nullable or carries a server default,
`integration.inbound_token`'s uniqueness is a unique *index* (SQLite's ALTER
TABLE ADD COLUMN rejects a UNIQUE constraint), and the kind widening and the
downgrade's drop_columns run through batch_alter_table.

Revision ID: 0002_dashboard
Revises: 0001
Create Date: 2026-09-09

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '0002_dashboard'
down_revision: Union[str, Sequence[str], None] = '0001'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table('presence_sample',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('tenant_id', sa.String(length=32), nullable=False),
    sa.Column('ts', sa.String(length=32), nullable=False),
    sa.Column('mac', sa.String(length=17), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('tenant_id', 'ts', 'mac')
    )
    op.create_index('ix_pres_mac', 'presence_sample', ['tenant_id', 'mac', 'ts'], unique=False)
    op.create_index('ix_pres_t', 'presence_sample', ['tenant_id', 'ts'], unique=False)
    op.create_table('ip_geo',
    sa.Column('ip', sa.String(length=45), nullable=False),
    sa.Column('country', sa.String(length=64), nullable=False),
    sa.Column('country_code', sa.String(length=4), nullable=False),
    sa.Column('city', sa.String(length=80), nullable=False),
    sa.Column('org', sa.String(length=160), nullable=False),
    sa.Column('isp', sa.String(length=160), nullable=False),
    sa.Column('lat', sa.Float(), nullable=True),
    sa.Column('lon', sa.Float(), nullable=True),
    sa.Column('hosting', sa.Boolean(), nullable=False),
    sa.Column('reverse', sa.String(length=255), nullable=False),
    sa.Column('fetched', sa.DateTime(timezone=True), nullable=False),
    sa.PrimaryKeyConstraint('ip')
    )
    op.create_table('act_token',
    sa.Column('token', sa.String(length=64), nullable=False),
    sa.Column('tenant_id', sa.String(length=32), nullable=False),
    sa.Column('kind', sa.String(length=16), nullable=False),
    sa.Column('target', sa.String(length=200), nullable=False),
    sa.Column('actions', sa.JSON(), nullable=False),
    sa.Column('expires', sa.DateTime(timezone=True), nullable=False),
    sa.Column('used', sa.String(length=16), nullable=False),
    sa.Column('created', sa.DateTime(timezone=True), nullable=False),
    sa.PrimaryKeyConstraint('token')
    )
    op.create_index(op.f('ix_act_token_tenant_id'), 'act_token', ['tenant_id'], unique=False)

    op.add_column('dns_query', sa.Column('mac', sa.String(length=17), nullable=True))
    op.add_column('alert', sa.Column('ticket_key', sa.String(length=64), nullable=False, server_default=''))
    op.add_column('report', sa.Column('title', sa.String(length=200), nullable=False, server_default=''))
    op.add_column('report', sa.Column('meta', sa.JSON(), nullable=False, server_default=sa.text("'{}'")))
    op.add_column('integration', sa.Column('name', sa.String(length=60), nullable=False, server_default=''))
    op.add_column('integration', sa.Column('config', sa.JSON(), nullable=False, server_default=sa.text("'{}'")))
    op.add_column('integration', sa.Column('inbound_token', sa.String(length=64), nullable=True))
    op.add_column('integration', sa.Column('last_test', sa.DateTime(timezone=True), nullable=True))
    op.add_column('integration', sa.Column('last_error', sa.Text(), nullable=False, server_default=''))
    op.create_index(op.f('ix_integration_inbound_token'), 'integration', ['inbound_token'], unique=True)
    with op.batch_alter_table('integration') as batch:
        batch.alter_column('kind', existing_type=sa.String(length=16), type_=sa.String(length=24),
                           existing_nullable=False)


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table('integration') as batch:
        batch.alter_column('kind', existing_type=sa.String(length=24), type_=sa.String(length=16),
                           existing_nullable=False)
    op.drop_index(op.f('ix_integration_inbound_token'), table_name='integration')
    with op.batch_alter_table('integration') as batch:
        batch.drop_column('last_error')
        batch.drop_column('last_test')
        batch.drop_column('inbound_token')
        batch.drop_column('config')
        batch.drop_column('name')
    with op.batch_alter_table('report') as batch:
        batch.drop_column('meta')
        batch.drop_column('title')
    with op.batch_alter_table('alert') as batch:
        batch.drop_column('ticket_key')
    with op.batch_alter_table('dns_query') as batch:
        batch.drop_column('mac')

    op.drop_index(op.f('ix_act_token_tenant_id'), table_name='act_token')
    op.drop_table('act_token')
    op.drop_table('ip_geo')
    op.drop_index('ix_pres_t', table_name='presence_sample')
    op.drop_index('ix_pres_mac', table_name='presence_sample')
    op.drop_table('presence_sample')
