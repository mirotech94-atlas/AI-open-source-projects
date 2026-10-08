"""Twelve columns the models always fill stop accepting NULL, and old rows are filled in.

Found by `tests/test_schema_matches_migrations.py`, which is new and is the
first thing in this repository to compare what the revisions build against
what `models.py` declares. All twelve divergences ran the same way round: the
model says NOT NULL, the revision created the column nullable. The model is
the correct side in every case -- each column has a Python-side default and
every writer sets it -- so this tightens the database rather than the
annotations being relaxed to match.

Ten of them (`api_token.created`, `billing_event.received`,
`check_decision.decided_at`, `recorder.channels`, `recorder.created`,
`subscription.created`, `subscription.updated`, `user_onboarding.created`,
`user_onboarding.updated`, `wan_outage.evidence`) were declared nullable
inside their own `create_table`, so no writer has ever been able to leave one
NULL: this is DDL drift with no data behind it.

The other two have rows behind them. `queue.wait_reason`
(0018_queue_lifecycle) and `user.preferences` (0019_personal_preferences)
were added to EXISTING tables with `nullable=True` and no `server_default`,
and neither revision backfilled, so every queue row and every user row
predating them holds NULL today -- against a `Mapped[str]` and a
`Mapped[dict]`. Nothing fails right now because both read paths already
compensate (`payloads/jobs.py` reads `q.wait_reason or ""`,
`routers/settings.py` reads `user.preferences or {}`), which is the trap
rather than the reassurance: the annotation says the value is always there,
every test database builds it NOT NULL, and the first reader to trust either
of those breaks only on the customer's oldest rows. This makes the database
agree with the annotation so that trust is warranted.

The UPDATE runs for all twelve regardless, not only for the two known to hold
NULLs. "No writer can have left a NULL here" is an inference about every
process that has ever touched a production database, and if one row disagrees
the ALTER fails and the deploy stops. A backfill that matches zero rows costs
nothing; the alternative costs a rollback.

Timestamps are backfilled with `datetime('now')`. When a row's real creation
time is already lost there is no honest value to write, and a fabricated
epoch would paint 1970 into the UI, which reads as a bug rather than as a
missing value. In practice this writes nothing at all: only the two JSON/text
columns above have NULL rows to find.

SQLite cannot ALTER a column's nullability, so each change goes through
`batch_alter_table`, which rebuilds the table from its reflected definition.
That preserves server defaults, indexes, unique constraints and the foreign
keys of child tables pointing at the rebuilt one (`user` is rebuilt here and
is referenced by `membership`, `partner_membership` and `user_onboarding`).
"""
from alembic import op
import sqlalchemy as sa

revision = "0033_not_null_parity"
down_revision = "0032_decision_outcome"
branch_labels = depends_on = None

# (table, column, type, the SQL for the model's own default)
COLUMNS = (
    ("api_token", "created", sa.DateTime(timezone=True), "datetime('now')"),
    ("billing_event", "received", sa.DateTime(timezone=True), "datetime('now')"),
    ("check_decision", "decided_at", sa.DateTime(timezone=True), "datetime('now')"),
    ("queue", "wait_reason", sa.String(240), "''"),
    ("recorder", "channels", sa.JSON(), "'[]'"),
    ("recorder", "created", sa.DateTime(timezone=True), "datetime('now')"),
    ("subscription", "created", sa.DateTime(timezone=True), "datetime('now')"),
    ("subscription", "updated", sa.DateTime(timezone=True), "datetime('now')"),
    ("user", "preferences", sa.JSON(), "'{}'"),
    ("user_onboarding", "created", sa.DateTime(timezone=True), "datetime('now')"),
    ("user_onboarding", "updated", sa.DateTime(timezone=True), "datetime('now')"),
    ("wan_outage", "evidence", sa.JSON(), "'{}'"),
)


def _nullable(insp, table: str, column: str) -> bool | None:
    """True/False as the database has it, None when the column is not there.

    Re-running a migration is not supposed to happen, but several revisions
    here guard for it anyway (`0019_personal_preferences`,
    `0031_firewall_proposal`) because the deployed database has been rebuilt
    and re-stamped by hand more than once.
    """
    if table not in insp.get_table_names():
        return None
    for c in insp.get_columns(table):
        if c["name"] == column:
            return bool(c["nullable"])
    return None


def upgrade():
    bind = op.get_bind()
    insp = sa.inspect(bind)
    for table, column, kind, default_sql in COLUMNS:
        if _nullable(insp, table, column) is not True:
            continue
        bind.exec_driver_sql(
            f'UPDATE "{table}" SET "{column}" = {default_sql} WHERE "{column}" IS NULL')
        with op.batch_alter_table(table) as batch:
            batch.alter_column(column, existing_type=kind, nullable=False)


def downgrade():
    bind = op.get_bind()
    insp = sa.inspect(bind)
    for table, column, kind, _ in reversed(COLUMNS):
        if _nullable(insp, table, column) is not False:
            continue
        with op.batch_alter_table(table) as batch:
            batch.alter_column(column, existing_type=kind, nullable=True)
