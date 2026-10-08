"""dns_rollup table -- daily DNS domain rollups (Task 55)

`retention.rollup_dns` groups raw `dns_query` rows older than the tenant's
`dns_raw_days` by (day, network_id, mac, name) before those raw rows are
pruned, so the DNS tab still has a real answer for a 30/90/365-day window.
`network_id` is nullable and carries the attribution of the raw rows it
summarises (`dns_query.network_id`, added by `0007_networks`); it stays NULL
for rows rolled up before that column existed. This migration holds no
FK/dependency on the `network` table, so it applies cleanly on its own --
it simply follows `0008_sensor_network_id` in the chain now that the
networks work has landed.

Revision ID: 0008_dns_rollup
Revises: 0008_sensor_network_id
Create Date: 2026-09-10

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '0008_dns_rollup'
down_revision: Union[str, Sequence[str], None] = '0008_sensor_network_id'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        'dns_rollup',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('tenant_id', sa.String(length=32), nullable=False),
        sa.Column('day', sa.String(length=10), nullable=False),
        sa.Column('network_id', sa.String(length=32), nullable=True),
        sa.Column('mac', sa.String(length=17), nullable=True),
        sa.Column('name', sa.String(length=255), nullable=False),
        sa.Column('queries', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('blocked', sa.Integer(), nullable=False, server_default='0'),
        sa.UniqueConstraint('tenant_id', 'day', 'network_id', 'mac', 'name',
                             name='uq_dns_rollup_tenant_day_net_mac_name'),
    )
    op.create_index('ix_dns_rollup_tenant_id', 'dns_rollup', ['tenant_id'])
    op.create_index('ix_dnsroll', 'dns_rollup', ['tenant_id', 'day'])


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index('ix_dnsroll', table_name='dns_rollup')
    op.drop_index('ix_dns_rollup_tenant_id', table_name='dns_rollup')
    op.drop_table('dns_rollup')
