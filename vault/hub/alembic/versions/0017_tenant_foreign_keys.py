"""Give every tenant-scoped table the foreign key its model already declares

Twenty tables carry `tenant_id` with `ForeignKey("tenant.id",
ondelete="CASCADE")` in the model and no constraint at all in the deployed
schema: their migrations declared the column as a plain String. The twelve
created by the initial migration have the key; everything added later does
not. `PRAGMA foreign_key_list(alert)` on production came back empty.

That is the whole reason deleting a tenant stranded 70,604 rows (see
`accounts.purge_tenant`), and the reason no test caught it: tests build the
schema with `create_all`, which emits every declared key, so the cascade
that was missing in production worked in the test database. After this the
two schemas agree.

Derived from the live schema at run time rather than hand-listed -- each
table with a `tenant_id` column that has no foreign key to `tenant` gets one
-- so it is idempotent and will also cover a table that appears later with
the same gap. SQLite cannot ALTER a constraint in; `batch_alter_table`
rebuilds the table (0002/0003 use the same route). With foreign keys on, a
row whose tenant no longer exists would fail the copy: `guardian tenant
purge-orphans` is the cleanup that makes this safe to run.

Revision ID: 0017_tenant_foreign_keys
Revises: 0016_asset_last_seen
Create Date: 2026-09-14

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = '0017_tenant_foreign_keys'
down_revision: Union[str, Sequence[str], None] = '0016_asset_last_seen'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _missing() -> list[str]:
    insp = sa.inspect(op.get_bind())
    out = []
    for table in insp.get_table_names():
        if table == "tenant" or "tenant_id" not in {c["name"] for c in insp.get_columns(table)}:
            continue
        if any(fk.get("referred_table") == "tenant" for fk in insp.get_foreign_keys(table)):
            continue
        out.append(table)
    return sorted(out)


def upgrade() -> None:
    for table in _missing():
        with op.batch_alter_table(table) as batch:
            batch.create_foreign_key(f"fk_{table}_tenant", "tenant", ["tenant_id"], ["id"],
                                     ondelete="CASCADE")


def downgrade() -> None:
    insp = sa.inspect(op.get_bind())
    for table in insp.get_table_names():
        for fk in insp.get_foreign_keys(table):
            if fk.get("name") == f"fk_{table}_tenant":
                with op.batch_alter_table(table) as batch:
                    batch.drop_constraint(f"fk_{table}_tenant", type_="foreignkey")
