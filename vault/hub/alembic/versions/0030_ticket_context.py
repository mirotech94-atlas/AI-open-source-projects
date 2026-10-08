"""A ticket says what it is about: a Guardian-shaped category, and the thing it concerns."""
from alembic import op
import sqlalchemy as sa

revision = "0030_ticket_context"
down_revision = "0029_flow"
branch_labels = depends_on = None

# Existing rows were raised before there was anywhere to say what they were
# about, so they get "other" and an empty context rather than a guess. A
# category inferred from a subject line would be a fact Guardian invented.
COLUMNS = (
    ("category", sa.String(16), "other"),
    ("context_kind", sa.String(16), ""),
    ("context_id", sa.String(64), ""),
    ("context_label", sa.String(160), ""),
)


def _has(name: str) -> bool:
    insp = sa.inspect(op.get_bind())
    return name in {c["name"] for c in insp.get_columns("ticket")}


def upgrade():
    for name, type_, default in COLUMNS:
        if not _has(name):
            op.add_column("ticket", sa.Column(name, type_, nullable=False,
                                              server_default=default))


def downgrade():
    for name, _type, _default in reversed(COLUMNS):
        if _has(name):
            op.drop_column("ticket", name)
