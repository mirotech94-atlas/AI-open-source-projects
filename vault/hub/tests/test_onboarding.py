"""The first-run wizard is per USER per TENANT, not per tenant (task 71).

The old gate lived in the browser: the overview payload reported no scan and
no sensor, so the shell redirected to `/welcome`. These tests pin the three
things that gate got wrong.
"""
from guardian_hub.db import SessionLocal
from guardian_hub import models as m

from tests.test_auth import login, seed_user


def _tenant_id():
    db = SessionLocal()
    tid = db.query(m.Tenant).first().id
    db.close()
    return tid


def _enrol_a_sensor(tid):
    """Make the tenant "ready" the cheapest honest way: one enrolled sensor.
    `onboarding.tenant_ready` treats a sensor OR a scan as "something is
    reporting here", so this is the same signal a first scan would give."""
    db = SessionLocal()
    db.add(m.Sensor(tenant_id=tid, name="s1"))
    db.commit()
    db.close()


def _second_member(tid, username="ann", email="ann@example.com", role="viewer"):
    db = SessionLocal()
    u = m.User(username=username, email=email, display_name=username)
    db.add(u)
    db.flush()
    db.add(m.Membership(user_id=u.id, tenant_id=tid, role=role))
    db.commit()
    db.close()


def test_a_fresh_tenant_gets_the_installing_walkthrough(client):
    seed_user()
    login(client)
    s = client.get("/api/v1/onboarding").json()
    assert s["show"] is True
    assert s["path"] == "setup"
    assert s["tenant_ready"] is False
    assert s["completed"] is False and s["skipped"] is False


def test_a_second_user_on_an_established_tenant_still_gets_the_wizard(client):
    """The whole point: the tenant is fully set up and the first admin has
    finished the wizard, and the newcomer is still shown around -- on the
    orienting path, not the installing one."""
    seed_user()
    tid = _tenant_id()
    _enrol_a_sensor(tid)
    login(client)
    assert client.post("/api/v1/onboarding", json={"completed": True}).json()["show"] is False

    _second_member(tid)
    login(client, username="ann", email="ann@example.com")
    s = client.get("/api/v1/onboarding").json()
    assert s["show"] is True, "a newcomer must be shown around even on a configured site"
    assert s["path"] == "tour", "and orientated, not asked to install a second sensor"
    assert s["tenant_ready"] is True


def test_completion_and_skip_both_suppress_the_wizard_and_survive_a_new_session(client):
    seed_user()
    login(client)
    assert client.post("/api/v1/onboarding", json={"skipped": True}).json()["show"] is False
    # A different browser (a fresh session, no localStorage at all) agrees --
    # which is exactly what the old per-browser flag could not do.
    login(client)
    assert client.get("/api/v1/onboarding").json()["skipped"] is True
    assert client.get("/api/v1/onboarding").json()["show"] is False


def test_step_is_resumable_and_re_run_setup_clears_both_flags(client):
    seed_user()
    login(client)
    client.post("/api/v1/onboarding", json={"step": "networks"})
    assert client.get("/api/v1/onboarding").json()["step"] == "networks"

    client.post("/api/v1/onboarding", json={"completed": True})
    # Settings -> Sensors' "Re-run setup" is exactly this call.
    s = client.post("/api/v1/onboarding", json={"completed": False, "skipped": False, "step": ""}).json()
    assert s["show"] is True and s["step"] == ""


def test_state_is_per_user_not_shared(client):
    seed_user()
    _second_member(_tenant_id())

    login(client)
    client.post("/api/v1/onboarding", json={"completed": True})

    login(client, username="ann", email="ann@example.com")
    assert client.get("/api/v1/onboarding").json()["completed"] is False

    db = SessionLocal()
    assert db.query(m.UserOnboarding).count() == 1, "the newcomer's read must not create a row"
    db.close()


def test_a_viewer_may_record_their_own_wizard_state(client):
    """The only viewer-writable route in `write.py`, on purpose: every user
    walks the wizard, including one who can change nothing else."""
    seed_user(role="viewer")
    login(client)
    assert client.post("/api/v1/onboarding", json={"step": "sensor"}).status_code == 200


def test_onboarding_needs_a_session(client):
    assert client.get("/api/v1/onboarding").status_code == 401
    assert client.post("/api/v1/onboarding", json={"completed": True}).status_code == 401


def test_0011_chains_off_the_current_head_and_creates_the_table(tmp_path):
    """The migration is run by the alembic CLI in a subprocess against its own
    temp SQLite file, for the same two reasons `test_migrations_networks.py`
    does it: `guardian_hub.db` binds `GUARDIAN_DATABASE_URL` once at import,
    and `hub/alembic/` shadows the installed `alembic` package unless the CLI
    is invoked from the repo root."""
    import os
    import pathlib
    import sqlite3
    import subprocess
    import sys

    repo = pathlib.Path(__file__).resolve().parents[2]
    db_path = tmp_path / "m.db"
    env = dict(os.environ)
    env["GUARDIAN_DATABASE_URL"] = f"sqlite:///{db_path}"
    env.setdefault("GUARDIAN_SECRET_KEY", "test-secret")
    r = subprocess.run([sys.executable, "-m", "alembic", "-c", "hub/alembic.ini", "upgrade", "head"],
                       cwd=repo, env=env, capture_output=True, text=True)
    assert r.returncode == 0, f"{r.stdout}\n{r.stderr}"

    c = sqlite3.connect(db_path)
    cols = {row[1] for row in c.execute("PRAGMA table_info(user_onboarding)").fetchall()}
    assert {"user_id", "tenant_id", "step", "completed", "skipped"} <= cols
    rev = c.execute("SELECT version_num FROM alembic_version").fetchone()[0]
    # Not pinned to this migration: it was the head when it was written, and
    # later work has legitimately landed on top. What matters is that
    # `upgrade head` reached a single head and built this table on the way --
    # both already asserted above. Pinning the name here turns every later
    # migration into a spurious failure.
    assert rev, "upgrade head left no revision stamped"
    c.close()

    # Idempotent, like every migration before it: re-running against a DB that
    # already has the table is a no-op rather than a crash.
    r = subprocess.run([sys.executable, "-m", "alembic", "-c", "hub/alembic.ini", "downgrade", "0010_billing"],
                       cwd=repo, env=env, capture_output=True, text=True)
    assert r.returncode == 0, f"{r.stdout}\n{r.stderr}"
    r = subprocess.run([sys.executable, "-m", "alembic", "-c", "hub/alembic.ini", "upgrade", "head"],
                       cwd=repo, env=env, capture_output=True, text=True)
    assert r.returncode == 0, f"{r.stdout}\n{r.stderr}"


def test_two_saves_racing_on_a_first_visit_both_succeed(client, monkeypatch):
    """The wizard saves its step as it opens; "Skip for now" posts at the same
    moment. Both read "no row yet", both inserted, and the loser hit the
    (user, tenant) unique key as a 500 (seen on the local demo, 2026-10-06).
    Reproduced here by making the read miss a row that is already there."""
    from guardian_hub import onboarding
    seed_user()
    login(client)
    assert client.post("/api/v1/onboarding", json={"skipped": True}).status_code == 200
    real = onboarding._row
    calls = {"n": 0}

    def stale_first_read(db, user_id, tenant_id):
        calls["n"] += 1
        return None if calls["n"] == 1 else real(db, user_id, tenant_id)

    monkeypatch.setattr(onboarding, "_row", stale_first_read)
    r = client.post("/api/v1/onboarding", json={"step": "welcome"})
    assert r.status_code == 200, r.text
    monkeypatch.setattr(onboarding, "_row", real)
    body = client.get("/api/v1/onboarding").json()
    assert body["step"] == "welcome" and body["skipped"] is True
