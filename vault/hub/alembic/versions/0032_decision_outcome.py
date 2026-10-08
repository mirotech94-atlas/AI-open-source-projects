"""Every repair decision and what became of it, so a model can be fitted later."""
from alembic import op
import sqlalchemy as sa

revision = "0032_decision_outcome"
down_revision = "0031_firewall_proposal"
branch_labels = depends_on = None


def upgrade():
    insp = sa.inspect(op.get_bind())
    if "decision_outcome" in insp.get_table_names():
        return
    op.create_table(
        "decision_outcome",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("tenant_id", sa.String(32), nullable=False, index=True),
        sa.Column("alert_key", sa.String(200), nullable=False, server_default=""),
        sa.Column("host", sa.String(120), nullable=False, server_default=""),
        sa.Column("action", sa.String(40), nullable=False, server_default=""),
        # Part of the decision's identity: `restart_service nginx` and
        # `restart_service postgres` on one host are two rows, and
        # `decisions.settle` must not stamp both from one repair's evidence.
        sa.Column("arg", sa.String(200), nullable=False, server_default=""),
        # Frozen at decision time on purpose -- see the model's docstring.
        sa.Column("features", sa.JSON, nullable=False, server_default="{}"),
        sa.Column("score", sa.Integer, nullable=False, server_default="0"),
        sa.Column("verdict", sa.String(12), nullable=False, server_default=""),
        sa.Column("chosen", sa.String(12), nullable=False, server_default=""),
        sa.Column("outcome", sa.String(12), nullable=False, server_default="", index=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("settled_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade():
    op.drop_table("decision_outcome")
