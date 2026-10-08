"""api_token -- credentials for the public REST API on api.guarder.io

Task 72. One new table, nothing altered.

`api_token` holds one row per customer-created API credential: the tenant it
is bound to, the scope (read|write) and the role it carries, and the sha256 of
the raw secret. The secret itself is never stored -- it is shown once at
creation -- so there is no column here that a database dump could turn back
into a working credential.

Two indexes matter for behaviour, not just speed:

- `ix_api_token_token_hash` is UNIQUE. Authentication is a single indexed
  lookup by hash, so it can never degrade into scanning the table and
  comparing digests, and two rows can never claim the same secret.
- `ix_api_token_tenant_id` is what the app's token list reads; a tenant's
  tokens are always fetched by tenant, never globally.

Idempotent in the same style as the migrations before it: the create is
guarded on the inspector, so running this against a database whose tables
`Base.metadata.create_all` already made (a dev box, or a fresh install that
booted before migrating) is a no-op rather than a crash.

No backfill. A deployment that predates this migration simply has no API
tokens, which is the correct reading -- every token must be created
deliberately by an admin.

Revision ID: 0011_api_tokens
Revises: 0010_billing
Create Date: 2026-09-10

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = '0014_api_tokens'
down_revision: Union[str, Sequence[str], None] = '0013_recorders'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _tables() -> set[str]:
    return set(sa.inspect(op.get_bind()).get_table_names())


def upgrade() -> None:
    if "api_token" in _tables():
        return

    op.create_table(
        "api_token",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("tenant_id", sa.String(32),
                  sa.ForeignKey("tenant.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.String(64), nullable=False, server_default=""),
        sa.Column("prefix", sa.String(24), nullable=False, server_default=""),
        sa.Column("token_hash", sa.String(64), nullable=False),
        sa.Column("scope", sa.String(8), nullable=False, server_default="read"),
        sa.Column("role", sa.String(16), nullable=False, server_default="viewer"),
        sa.Column("created_by", sa.String(64), nullable=False, server_default=""),
        sa.Column("created", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expires", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_used", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_used_ip", sa.String(45), nullable=False, server_default=""),
        sa.Column("revoked", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_api_token_token_hash", "api_token", ["token_hash"], unique=True)
    op.create_index("ix_api_token_tenant_id", "api_token", ["tenant_id"])
    op.create_index("ix_api_token_prefix", "api_token", ["prefix"])


def downgrade() -> None:
    if "api_token" in _tables():
        op.drop_table("api_token")
