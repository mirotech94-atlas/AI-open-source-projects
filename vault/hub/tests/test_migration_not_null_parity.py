"""`0033_not_null_parity` on a database that still holds the NULLs it fixes.

The parity check next door (`test_schema_matches_migrations.py`) proves the
COLUMNS end up NOT NULL. It cannot prove the rows survived, because it runs
the chain against an empty database: a backfill that matches nothing and a
backfill that is wrong look identical there, and the one that is wrong fails
on the customer's database at deploy time instead.

So this builds a `0031`-shape database, puts in the two rows that actually
exist in production -- a `queue` row from before `0018_queue_lifecycle` added
`wait_reason`, and a `user` row from before `0019_personal_preferences` added
`preferences`, both holding NULL -- and then migrates.

It also pins the one thing `batch_alter_table` could quietly take away.
Tightening a column on SQLite rebuilds the whole table from its reflected
definition, and `check_decision` carries six `server_default=""` columns
through that rebuild. Nothing else in the suite would notice if they came out
the other side without their defaults: the parity test does not compare
server defaults (it says so), and every insert in the application supplies
every column.
"""
import os
import pathlib
import sqlite3
import subprocess
import sys

import pytest

HUB = pathlib.Path(__file__).resolve().parents[1]
REPO = HUB.parent
INI = "hub/alembic.ini"
BEFORE = "0031_firewall_proposal"


def _alembic(db_path: pathlib.Path, *args: str) -> None:
    # Subprocess, from the repo root, with the URL overridden rather than
    # inherited -- same three reasons as `test_migrations_networks.py`, which
    # this is modelled on.
    env = dict(os.environ)
    env["GUARDIAN_DATABASE_URL"] = f"sqlite:///{db_path}"
    env.setdefault("GUARDIAN_SECRET_KEY", "test-secret")
    r = subprocess.run([sys.executable, "-m", "alembic", "-c", INI, *args],
                       cwd=REPO, env=env, capture_output=True, text=True)
    assert r.returncode == 0, f"alembic {' '.join(args)} failed:\n{r.stdout}\n{r.stderr}"


@pytest.fixture()
def db(tmp_path) -> pathlib.Path:
    path = tmp_path / "parity.db"
    _alembic(path, "upgrade", BEFORE)
    c = sqlite3.connect(path)
    c.execute("INSERT INTO tenant (id, slug, name, brand, ntfy_topic, config, settings, created)"
              " VALUES ('t1','acme','Acme','{}','','{}','{}','2026-01-01 00:00:00')")
    # The pre-0018 job: every lifecycle column NULL, the way `add_column`
    # without a server_default left it.
    c.execute("INSERT INTO queue (id, tenant_id, ts, who, path, body, status)"
              " VALUES ('q1','t1','2026-01-01 00:00:00','owner','/api/fix','{}','queued')")
    # The pre-0019 user: no preferences column existed when the row was written.
    c.execute("INSERT INTO user (id, username, email, display_name, is_superadmin, created)"
              " VALUES ('u1','owner','o@example.com','Owner',0,'2026-01-01 00:00:00')")
    c.commit()
    assert c.execute("SELECT wait_reason FROM queue WHERE id='q1'").fetchone()[0] is None
    assert c.execute("SELECT preferences FROM user WHERE id='u1'").fetchone()[0] is None
    c.close()
    return path


def _ddl(c: sqlite3.Connection, table: str) -> str:
    return c.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name=?",
                     (table,)).fetchone()[0]


def test_rows_that_predate_the_column_get_the_model_default(db):
    before = sqlite3.connect(db)
    check_decision_ddl = _ddl(before, "check_decision")
    before.close()

    _alembic(db, "upgrade", "head")

    c = sqlite3.connect(db)
    assert c.execute("SELECT wait_reason FROM queue WHERE id='q1'").fetchone()[0] == ""
    assert c.execute("SELECT preferences FROM user WHERE id='u1'").fetchone()[0] == "{}"
    # The row is otherwise untouched: a table rebuild that lost or reordered
    # data would still satisfy the two assertions above.
    assert c.execute("SELECT username, email, is_superadmin FROM user WHERE id='u1'").fetchone() \
        == ("owner", "o@example.com", 0)
    assert c.execute("SELECT tenant_id, who, path, status FROM queue WHERE id='q1'").fetchone() \
        == ("t1", "owner", "/api/fix", "queued")

    nullable = {(t, r[1]): r[3] == 0
                for t in ("queue", "user")
                for r in c.execute(f'pragma table_info("{t}")')}
    assert nullable[("queue", "wait_reason")] is False
    assert nullable[("user", "preferences")] is False
    # The rebuild must not have taken the foreign keys of the tables pointing
    # AT `user` with it, which is the failure mode of a rename-based rebuild.
    assert c.execute("pragma foreign_key_check").fetchall() == []
    assert any(fk[2] == "user" for fk in c.execute("pragma foreign_key_list(membership)"))

    rebuilt = _ddl(c, "check_decision")
    assert rebuilt.count("DEFAULT") == check_decision_ddl.count("DEFAULT"), (
        "batch_alter_table dropped a server_default while rebuilding "
        "check_decision:\nbefore: %s\nafter:  %s" % (check_decision_ddl, rebuilt))
    c.close()


def test_the_downgrade_puts_the_columns_back(db):
    """A revision that cannot be rolled back is a revision nobody dares deploy."""
    _alembic(db, "upgrade", "head")
    _alembic(db, "downgrade", BEFORE)
    c = sqlite3.connect(db)
    nullable = {r[1]: r[3] == 0 for r in c.execute('pragma table_info("queue")')}
    assert nullable["wait_reason"] is True
    # The backfilled value stays: a downgrade relaxes the constraint, it does
    # not restore NULLs it cannot tell from real data.
    assert c.execute("SELECT wait_reason FROM queue WHERE id='q1'").fetchone()[0] == ""
    c.close()
