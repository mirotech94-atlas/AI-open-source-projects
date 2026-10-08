"""A session minted with a tenant's public demo code is marked, so it can be
kept read-only (audit 2026-09-28, issue 3).

The demo code is published on the site, so anyone holding it signs in as the
demo tenant's admin. Marking the session -- rather than the tenant -- keeps
the demo read-only for the public while the same users signing in the
ordinary way (emailed code) can still maintain it.
"""
from alembic import op
import sqlalchemy as sa

revision = "0034_session_via_demo"
down_revision = "0033_not_null_parity"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("session") as batch:
        batch.add_column(sa.Column("via_demo", sa.Boolean(), nullable=False,
                                   server_default=sa.false()))


def downgrade() -> None:
    with op.batch_alter_table("session") as batch:
        batch.drop_column("via_demo")
