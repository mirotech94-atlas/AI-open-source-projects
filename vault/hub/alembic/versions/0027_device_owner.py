"""A device can be tagged to the person who has it."""
from alembic import op
import sqlalchemy as sa

revision = "0027_device_owner"
down_revision = "0026_wan_outage"
branch_labels = depends_on = None


def upgrade():
    op.add_column("device", sa.Column("owner", sa.String(120),
                                      nullable=False, server_default=""))


def downgrade():
    op.drop_column("device", "owner")
