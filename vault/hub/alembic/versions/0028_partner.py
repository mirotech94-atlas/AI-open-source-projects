"""An MSP partner sits above its customers: a brand they inherit, and staff who reach them all."""
from alembic import op
import sqlalchemy as sa

revision = "0028_partner"
down_revision = "0027_device_owner"
branch_labels = depends_on = None


def upgrade():
    op.create_table(
        "partner",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("slug", sa.String(64), nullable=False, unique=True),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("brand", sa.JSON(), nullable=False),
        sa.Column("settings", sa.JSON(), nullable=False),
        sa.Column("created", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "partner_membership",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("user_id", sa.String(32), sa.ForeignKey("user.id", ondelete="CASCADE"), nullable=False),
        sa.Column("partner_id", sa.String(32), sa.ForeignKey("partner.id", ondelete="CASCADE"), nullable=False),
        sa.Column("role", sa.String(16), nullable=False),
        sa.UniqueConstraint("user_id", "partner_id"),
    )
    # batch_alter_table: SQLite cannot ADD a FOREIGN KEY column in place,
    # and SQLite is the deployed engine. Batch mode rebuilds the table, and
    # the rebuild refuses an anonymous constraint ("Constraint must have a
    # name"), so the FK is named here and dropped by that name below.
    with op.batch_alter_table("tenant") as b:
        b.add_column(sa.Column("partner_id", sa.String(32), nullable=True))
        b.create_foreign_key("fk_tenant_partner", "partner", ["partner_id"], ["id"], ondelete="SET NULL")
        b.create_index("ix_tenant_partner_id", ["partner_id"])


def downgrade():
    with op.batch_alter_table("tenant") as b:
        b.drop_index("ix_tenant_partner_id")
        b.drop_constraint("fk_tenant_partner", type_="foreignkey")
        b.drop_column("partner_id")
    op.drop_table("partner_membership")
    op.drop_table("partner")
