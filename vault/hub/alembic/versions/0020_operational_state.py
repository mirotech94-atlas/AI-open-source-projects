"""Durable conversations, report delivery receipts, and expiring exceptions."""
from alembic import op
import sqlalchemy as sa
revision = "0020_operational_state"
down_revision = "0019_personal_preferences"
branch_labels = depends_on = None

def upgrade():
    op.add_column("check_decision", sa.Column("expires_at", sa.DateTime(), nullable=True))
    op.create_table("conversation_state", sa.Column("id",sa.String(64),primary_key=True),
        sa.Column("tenant_id",sa.String(32),sa.ForeignKey("tenant.id",ondelete="CASCADE"),nullable=False),
        sa.Column("data",sa.JSON(),nullable=False))
    op.create_table("delivery",sa.Column("id",sa.String(32),primary_key=True),
        sa.Column("tenant_id",sa.String(32),sa.ForeignKey("tenant.id",ondelete="CASCADE"),nullable=False),
        sa.Column("reference",sa.String(64),nullable=False),sa.Column("channel",sa.String(32),nullable=False),
        sa.Column("status",sa.String(16),nullable=False),sa.Column("detail",sa.String(300),nullable=False),
        sa.Column("updated",sa.DateTime(),nullable=False))
    op.create_index("ix_delivery_reference","delivery",["reference"])
    op.create_index("ix_delivery_tenant_id","delivery",["tenant_id"])
    op.create_index("ix_conversation_state_tenant_id","conversation_state",["tenant_id"])

def downgrade():
    op.drop_table("delivery")
    op.drop_table("conversation_state")
    op.drop_column("check_decision","expires_at")
