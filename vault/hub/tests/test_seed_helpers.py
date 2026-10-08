"""Shared seeding helper for every payload test.

Created by Task 18 and reused by Tasks 19-21, 28-31 and 37-44: it builds the
30-day demo tenant with `seed.seed_demo` (so the payload builders are
exercised against realistic data, not a hand-rolled two-row fixture) and
signs the client in as that tenant's admin using the fixed demo code.
"""

from guardian_hub.db import SessionLocal
from guardian_hub.seed import seed_demo

from tests import scanfixture


def sign_in_as_demoadmin(client):
    """Sign in as the demo tenant's admin with an emailed code (never the
    public demo code, whose sessions are read-only)."""
    from guardian_hub import mailer
    assert client.post("/api/v1/auth/start", json={"username": "demoadmin"}).json() == {"sent": True}
    code = mailer.LAST_CODES["demoadmin@guardian.invalid"]
    r = client.post("/api/v1/auth/verify", json={"username": "demoadmin", "code": code})
    assert r.status_code == 200, r.text


def seeded_demo(client):
    """Seed the demo tenant and sign in as its admin. Returns the tenant id.

    Seeded from `scanfixture.rebased_dir()` rather than the on-disk fixtures:
    `seed_demo` derives every synthesized row from `last["ts"]`, so feeding it
    the rebased capture puts the whole 30-day demo window up against *now*.
    Without that, every payload assertion about a 24-hour window (DNS
    clients, site uptime, presence) went red as soon as the captured instant
    aged out of the window -- a suite failing because time passed, not
    because anything broke. `rebased_dir()` is built once per process, so
    `seed_demo`'s documented idempotency across repeated calls is preserved.
    """
    db = SessionLocal()
    try:
        out = seed_demo(db, fixtures=scanfixture.rebased_dir())
    finally:
        db.close()
    # The ordinary emailed-code sign-in, not the public demo code: a demo-code
    # session is read-only (audit 2026-09-28), and these tests write.
    sign_in_as_demoadmin(client)
    return out["tenant_id"]


def enrol_as_tenant_admin(client) -> str:
    """Self-serve sensor enrolment for the signed-in tenant admin (e.g.
    demoadmin, right after `seeded_demo`) -- `POST /api/v1/sensors/enrol-code`
    (commit 58d7fd3), unlike `tests.test_sensor_api.enrol`'s tid-scoped
    route, does not require a superadmin. Preferred by Tasks 29-31 over that
    older helper because it never has to swap the session cookie out from
    under any sockets already open on the same client."""
    code = client.post("/api/v1/sensors/enrol-code").json()["code"]
    r = client.post("/api/v1/sensor/enrol", json={"code": code, "name": "sensor", "platform": "linux", "version": "0.1.0"})
    assert r.status_code == 200, r.text
    return r.json()["token"]


def confirmed_fix(client, host: str, action: str, arg: str | None = None):
    """POST a disruptive (`chat.tiers.TIER3_FIXES`) fix the way any caller now
    has to: mint the one-shot confirmation, then spend it on the same exact
    (host, action, arg). Returns the `/fix` response."""
    body = {"host": host, "action": action, "arg": arg}
    minted = client.post("/api/v1/fix/confirm", json=body)
    assert minted.status_code == 200, minted.text
    return client.post("/api/v1/fix", json={**body, "confirm_token": minted.json()["token"]})
