"""The schema the migrations build must be the schema the models declare.

Nothing else in this suite checks that, and the reason is structural:
`conftest`'s `client` fixture builds its database with
`Base.metadata.create_all(engine)`, which reads `models.py` and never opens
`alembic/versions/`. Production does the opposite -- `guardian migrate` runs
`alembic upgrade head` and never reads `models.py`. So a `mapped_column`
whose type, nullability, primary key or index differs from what the
corresponding revision creates is invisible to all 2000-odd tests: every one
of them runs against a database built from the side that is not deployed.
The divergence surfaces once, in production, as an IntegrityError or a
`None` where the annotation promised a value.

This test closes that by running the real chain -- the alembic CLI, in a
subprocess, against a throwaway SQLite file -- reflecting what it built, and
comparing it against `Base.metadata` table by table and column by column. It
collects every divergence and asserts once at the end rather than failing on
the first, because the interesting output is the whole list.

What it compares, and why each comparison is expressed as a rule rather
than an exact equality:

- **Type affinity, not the declared type.** SQLite has five storage
  affinities and resolves a column to one of them from the declared type
  string; it keeps `VARCHAR(64)` as text next to `VARCHAR(160)` with no
  difference in behaviour, and nothing in the database remembers the
  length. A test that demanded `String(64) == String(64)` would be
  asserting something the engine does not store. Affinity is what the
  database actually enforces, so affinity is what is compared.
- **JSON counts as text.** SQLite has no JSON storage class: a column
  declared `JSON` and a column declared `TEXT` are the same column, and
  SQLAlchemy's `JSON` type serialises to a string into either. So a
  migration that wrote `sa.Text` where the model writes `JSON` has built
  the column the model asked for, and this must not read as drift.
- **Indexes and unique constraints are compared as column tuples, with
  names ignored.** The two sides reach the same guarantee by different
  routes -- a migration's `op.create_index(..., unique=True)` reflects back
  as a unique *index*, while a model's `UniqueConstraint` in
  `__table_args__` reflects back as a unique *constraint* -- and they
  almost never agree on the name (`uq_flow` against `ix_fwprop_tenant_key`).
  Comparing names would bury the real findings under dozens of cosmetic
  ones. The uniqueness of a tuple of columns is the property that matters;
  a unique index's column ORDER does not, so those are sorted, while a
  plain index's order decides which queries it can serve, so those are not.
  Partiality is carried alongside (`sqlite_where`), because a unique index
  with a WHERE clause is a materially weaker promise than one without --
  see `models.Queue.__table_args__`.
- **Nullability is skipped on primary-key columns**, where SQLite's own
  reporting is inconsistent (a PK column may report `notnull=0` and still
  reject NULL), and the primary-key comparison covers the ground instead.

These tolerances are rules, deliberately, not a list of tables to skip. An
allowlist would grow every time someone hit a false positive and would
quietly stop checking the tables most likely to drift.

Declared limits, so the next reader knows where the edge is: this does NOT
compare foreign keys, server defaults, or string lengths. The first two are
real properties that could drift undetected; they are left out because
nothing in this repository has yet been bitten by them, and a comparison
nobody can interpret is worse than a narrower one. Lengths are unrecoverable
from SQLite, as above.

**What the first run found.** Twelve columns, every one of them the same
shape: the model declares NOT NULL and the revision created the column
nullable. In all twelve the MODEL is the correct side -- each column has a
Python-side default and every writer sets it -- so `0033_not_null_parity`
backfills and tightens them rather than the annotations being relaxed to
match. Two of the twelve were a live bug rather than cosmetic DDL:

- `queue.wait_reason` (added by `0018_queue_lifecycle`) and
  `user.preferences` (added by `0019_personal_preferences`) were added to
  EXISTING tables with `nullable=True` and no `server_default`, so every row
  written before those revisions holds NULL in production to this day --
  against a `Mapped[str]` and a `Mapped[dict]`, neither of which admits one.
  Nothing is broken today only because both read paths already work around
  it: `payloads/jobs.py` writes `q.wait_reason or ""` and
  `routers/settings.py` writes `user.preferences or {}`. That defensiveness
  is the shape of the bug, not the absence of one -- it is there because the
  column really is nullable, and the next reader who believes the annotation
  (`job.wait_reason.strip()`, `user.preferences["theme"]`) gets an
  AttributeError on rows nobody can reproduce locally, since every test
  database builds these columns NOT NULL from `models.py`.
- The other ten (`api_token.created`, `billing_event.received`,
  `check_decision.decided_at`, `recorder.channels`, `recorder.created`,
  `subscription.created`, `subscription.updated`, `user_onboarding.created`,
  `user_onboarding.updated`, `wan_outage.evidence`) were declared nullable
  inside their `create_table`, where no writer can have left a NULL. Those
  were DDL drift with no data behind it.
"""
import os
import pathlib
import sqlite3
import subprocess
import sys

import pytest
import sqlalchemy as sa
from sqlalchemy.dialects import sqlite as sqlite_dialect
from sqlalchemy.schema import UniqueConstraint

from guardian_hub.db import Base
from guardian_hub import models  # noqa: F401  (registers every table on Base.metadata)

HUB = pathlib.Path(__file__).resolve().parents[1]
REPO = HUB.parent
INI = "hub/alembic.ini"

# The dialect the declared types are rendered through. It has to be the same
# one the migrations ran against, or `String(32)` would render as some other
# backend's spelling and every column would read as drift.
DIALECT = sqlite_dialect.dialect()

# Alembic's own bookkeeping table. It exists in every migrated database and in
# no model, by design, so it is the one name excluded from the table
# comparison -- and the only one, since anything else present on one side only
# is exactly the finding this test is for.
BOOKKEEPING = {"alembic_version"}


def _affinity(declared: str) -> str:
    """The SQLite storage affinity a declared type resolves to.

    Transcribed from SQLite's "Determination Of Column Affinity" rules, which
    are applied in order and stop at the first match -- so `VARCHAR(64)` and
    `VARCHAR(160)` both land on TEXT and `BIGINT` lands on INTEGER, which is
    the point: this is what the database enforces once the DDL is forgotten.

    The JSON line is ahead of the rules rather than part of them. SQLite has
    no JSON storage class, so a column declared JSON would fall through to
    NUMERIC while the TEXT column holding the identical serialised value
    lands on TEXT. Collapsing the two says the true thing: a migration that
    wrote `sa.Text` where the model writes `JSON` built the same column.
    """
    t = (declared or "").upper()
    if "JSON" in t:
        return "TEXT"
    if "INT" in t:
        return "INTEGER"
    if "CHAR" in t or "CLOB" in t or "TEXT" in t:
        return "TEXT"
    if "BLOB" in t or not t:
        return "BLOB"
    if "REAL" in t or "FLOA" in t or "DOUB" in t:
        return "REAL"
    return "NUMERIC"


def _model_affinity(column: sa.Column) -> tuple[str, str]:
    """(affinity, declared type) for a model column, as SQLite would see it.

    `.compile(DIALECT)` unwraps `TypeDecorator`s on the way down, which is
    what makes `TZDateTime` comparable at all: it renders as its `impl`,
    `DATETIME`, which is what a migration's `sa.DateTime` renders as too.
    """
    declared = column.type.compile(DIALECT)
    return _affinity(declared), declared


def _index_shapes(indexes, unique_constraints) -> tuple[set, set]:
    """Split declared indexes into (plain, unique) sets of column tuples.

    `indexes` is a sequence of (columns, unique, partial) triples and
    `unique_constraints` a sequence of column sequences; the caller adapts
    each side's shape into those. See the module docstring for why names are
    dropped, why unique tuples are sorted and plain ones are not, and why
    partiality rides along.
    """
    plain, unique = set(), set()
    for columns, is_unique, is_partial in indexes:
        if is_unique:
            unique.add((tuple(sorted(columns)), is_partial))
        else:
            plain.add(tuple(columns))
    for columns in unique_constraints:
        unique.add((tuple(sorted(columns)), False))
    return plain, unique


@pytest.fixture(scope="module")
def migrated(tmp_path_factory) -> pathlib.Path:
    """A SQLite file built by running the whole revision chain.

    The CLI, in a subprocess, for the same three reasons
    `test_migrations_networks.py` gives: `guardian_hub.db` binds
    `GUARDIAN_DATABASE_URL` once at import so this process's engine can never
    be pointed elsewhere; `hub/alembic/` shadows the installed `alembic`
    package for anything whose working directory is `hub/`, so the CLI is
    invoked from the repo root the way `guardian migrate` reaches it; and a
    subprocess is how production runs it, which is the whole subject here.

    `GUARDIAN_DATABASE_URL` is OVERRIDDEN rather than inherited: `conftest`
    has already set it process-wide to the suite's own database, and a child
    that inherited it would migrate the database the rest of the suite is
    using.
    """
    db_path = tmp_path_factory.mktemp("schema-parity") / "head.db"
    # Belt and braces around the one genuinely destructive mistake available
    # here: a chain run against `hub/data/guardian.db` would rewrite the
    # deployed database in place.
    assert not db_path.exists(), db_path
    assert "data" not in db_path.parts, f"refusing to migrate anything but a temp file: {db_path}"

    env = dict(os.environ)
    env["GUARDIAN_DATABASE_URL"] = f"sqlite:///{db_path}"
    env.setdefault("GUARDIAN_SECRET_KEY", "test-secret")
    r = subprocess.run([sys.executable, "-m", "alembic", "-c", INI, "upgrade", "head"],
                       cwd=REPO, env=env, capture_output=True, text=True)
    assert r.returncode == 0, f"alembic upgrade head failed:\n{r.stdout}\n{r.stderr}"
    return db_path


def test_every_model_column_matches_the_column_its_migration_creates(migrated):
    engine = sa.create_engine(f"sqlite:///{migrated}")
    inspector = sa.inspect(engine)
    # `pragma table_info` rather than the inspector for columns: it reports
    # the declared type string verbatim, which is the input `_affinity` wants,
    # and it reports NOT NULL and primary-key membership from the same row.
    raw = sqlite3.connect(migrated)

    problems: list[str] = []
    modelled = Base.metadata.tables
    migrated_tables = set(inspector.get_table_names()) - BOOKKEEPING

    for name in sorted(set(modelled) - migrated_tables):
        problems.append(f"{name}: declared in models.py, no migration creates it")
    for name in sorted(migrated_tables - set(modelled)):
        problems.append(f"{name}: created by a migration, absent from models.py")

    for name in sorted(set(modelled) & migrated_tables):
        table = modelled[name]
        model_columns = {c.name: c for c in table.columns}
        # (cid, name, type, notnull, dflt_value, pk)
        db_columns = {row[1]: row for row in raw.execute(f'pragma table_info("{name}")')}

        for col in sorted(set(model_columns) - set(db_columns)):
            problems.append(f"{name}.{col}: declared in models.py, no migration adds it")
        for col in sorted(set(db_columns) - set(model_columns)):
            problems.append(f"{name}.{col}: added by a migration, absent from models.py")

        for col in sorted(set(model_columns) & set(db_columns)):
            model_col, db_row = model_columns[col], db_columns[col]
            _, _, db_declared, db_notnull, _, db_pk = db_row
            want, want_declared = _model_affinity(model_col)
            got = _affinity(db_declared)
            if want != got:
                problems.append(
                    f"{name}.{col}: type affinity {got} in the migrated schema "
                    f"({db_declared}), {want} in models.py ({want_declared})")
            if not model_col.primary_key and not db_pk:
                db_nullable = db_notnull == 0
                if model_col.nullable != db_nullable:
                    problems.append(
                        f"{name}.{col}: "
                        f"{'nullable' if db_nullable else 'NOT NULL'} in the migrated schema, "
                        f"{'nullable' if model_col.nullable else 'NOT NULL'} in models.py")

        model_pk = sorted(c.name for c in table.primary_key.columns)
        db_pk_cols = sorted(row[1] for row in db_columns.values() if row[5])
        if model_pk != db_pk_cols:
            problems.append(
                f"{name}: primary key is {db_pk_cols} in the migrated schema, "
                f"{model_pk} in models.py")

        model_plain, model_unique = _index_shapes(
            [([c.name for c in i.columns], i.unique,
              i.dialect_options.get("sqlite", {}).get("where") is not None)
             for i in table.indexes],
            [[c.name for c in c_.columns] for c_ in table.constraints
             if isinstance(c_, UniqueConstraint)],
        )
        db_plain, db_unique = _index_shapes(
            [(i["column_names"], bool(i["unique"]),
              "sqlite_where" in (i.get("dialect_options") or {}))
             for i in inspector.get_indexes(name)],
            [u["column_names"] for u in inspector.get_unique_constraints(name)],
        )
        for cols in sorted(model_plain - db_plain):
            problems.append(f"{name}: index on {list(cols)} in models.py, not in the migrated schema")
        for cols in sorted(db_plain - model_plain):
            problems.append(f"{name}: index on {list(cols)} in the migrated schema, not in models.py")
        for cols, partial in sorted(model_unique - db_unique):
            problems.append(f"{name}: unique{' (partial)' if partial else ''} on {list(cols)} "
                            "in models.py, not in the migrated schema")
        for cols, partial in sorted(db_unique - model_unique):
            problems.append(f"{name}: unique{' (partial)' if partial else ''} on {list(cols)} "
                            "in the migrated schema, not in models.py")

    raw.close()
    engine.dispose()
    assert not problems, (
        "the migrated schema and models.py disagree in %d place(s). Only the "
        "MIGRATED side describes what production has; a divergence is fixed "
        "by a NEW revision when the model is right, never by editing a "
        "revision that has already been deployed:\n  %s"
        % (len(problems), "\n  ".join(problems)))
