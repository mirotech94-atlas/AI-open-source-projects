"""lead table -- "Request access" submissions from the public site

Task 59 / the guarder.io split: the marketing site's only form posts to
`POST /api/v1/public/lead`, which stores a row here. Hub-global on purpose --
a lead has no tenant (the person has no account yet) and is the product
owner's pipeline, so the table carries no `tenant_id` and no foreign key, and
only `GET /api/v1/leads` (superadmin) reads it. See `models.Lead`.

Every column is capped at the length `schemas.LeadIn` validates.

MERGE HAZARD -- read before merging this branch
-----------------------------------------------
Task 55 (branch `phase5-retention`, since merged towards main) added a
migration ALSO numbered 0008, `0008_dns_rollup`, whose `down_revision` the
ledger records as needing a re-point to `0007_networks`. If both land with
`down_revision = '0007_networks'`, alembic has two heads and
`guardian migrate` fails with "Multiple head revisions are present".

Whichever of the two is merged second must be re-pointed to sit AFTER the
first (change its `down_revision`, and only that) -- the two migrations touch
completely disjoint tables, so either order is correct.

Revision ID: 0008_leads
Revises: 0007_networks
Create Date: 2026-09-10

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '0009_leads'
down_revision: Union[str, Sequence[str], None] = '0008_dns_rollup'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "lead",
        sa.Column("id", sa.String(32), nullable=False),
        sa.Column("name", sa.String(120), nullable=False, server_default=""),
        sa.Column("email", sa.String(200), nullable=False, server_default=""),
        sa.Column("company", sa.String(120), nullable=False, server_default=""),
        sa.Column("network_size", sa.String(32), nullable=False, server_default=""),
        sa.Column("note", sa.String(2000), nullable=False, server_default=""),
        sa.Column("ip", sa.String(45), nullable=False, server_default=""),
        sa.Column("created", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    # The list route's only ordering, and the only query this table ever sees.
    op.create_index(op.f("ix_lead_created"), "lead", ["created"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_lead_created"), table_name="lead")
    op.drop_table("lead")
