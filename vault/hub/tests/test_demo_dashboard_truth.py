"""F4 / F5 (verification-walk findings): the demo tenant must never show a
faulted product to a prospect for a reason that isn't real.

F4 -- `GET /api/v1/live` (what the wallboard reads) was probing the demo
tenant's `*.northwind-dental.example` / `*.northwind-clinics.example` site
targets over the real network. Those TLDs are reserved by RFC 6761 and never
resolve, so the probe was not a measurement, it was a certainty of "down" --
while the seeded overview strip (`Site.last`, same synthetic source as every
other demo surface) said the same sites were up. `routers.live._synthetic_sites`
fixes this by sourcing the demo tenant's `/live` sites from that same
`Site.last` data instead of a real probe.

F5 -- `POST /api/v1/run` ("Scan now") on the demo tenant queued a row no real
sensor was ever going to pick up: it sat at `status="queued"` forever and
never touched the demo's data. `routers.write._run_demo_scan` fixes this by
re-seeding the demo tenant's window at "now" in the background and completing
the same queue row.
"""
import datetime as dt

from guardian_hub import models as m
from guardian_hub.db import SessionLocal
from guardian_hub.seed import reset_tenant, seed_demo

from tests.test_seed_helpers import seeded_demo

LIVE = "/api/v1/live"
RUN = "/api/v1/run"


# --------------------------------------------------------------------------
# F4 -- the wallboard's source (`/live`) must agree with the rest of the
# demo's synthetic story, not fail a real probe of an unresolvable address.
# --------------------------------------------------------------------------

def test_demo_live_sites_are_not_real_probed_against_dot_example(client):
    seeded_demo(client)

    r = client.get(LIVE)
    assert r.status_code == 200, r.text
    sites = r.json()["sites"]

    assert sites, "demo tenant has no configured sites to check"
    # Every seeded demo site is reachable=ok in its synthetic fixture -- a
    # real probe of a `.example` address can only ever answer False, so
    # `ok is True` for all of them is the signal that this route stopped
    # doing that and started agreeing with `Site.last` (and therefore with
    # the overview health strip built from the same table).
    assert all(s["ok"] is True for s in sites), sites
    # `.example` addresses never resolve; a real probe would report no code
    # at all. A non-None code is further evidence this came from the
    # seeded scan result, not a live socket.
    assert all(s["code"] is not None for s in sites), sites


def test_demo_live_sites_match_the_overview_health_strip(client):
    """The two surfaces the F4 brief calls out by name must agree."""
    seeded_demo(client)

    live_sites = {s["name"]: s["ok"] for s in client.get(LIVE).json()["sites"]}
    strip = client.get("/api/v1/overview").json()["strip"]
    site_strip = {row["name"]: row["ok"] for row in strip if row["kind"] == "site"}

    assert live_sites and site_strip
    assert live_sites == site_strip


# --------------------------------------------------------------------------
# F5 -- "Scan now" on the demo tenant must visibly refresh it, the way a
# real sensor's scan does on a real tenant.
# --------------------------------------------------------------------------

def _backdate_demo(hours: int) -> str:
    db = SessionLocal()
    try:
        tenant = db.query(m.Tenant).filter_by(slug="demo").one()
        reset_tenant(db, tenant.id)
        stale_anchor = dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=hours)
        seed_demo(db, anchor=stale_anchor)
        return tenant.id
    finally:
        db.close()


def test_demo_scan_now_completes_and_refreshes_the_data(client):
    seeded_demo(client)
    _backdate_demo(hours=2)

    stale = client.get("/api/v1/overview").json()
    assert stale["age_s"] is not None and stale["age_s"] > 3600

    r = client.post(RUN)
    assert r.status_code == 200, r.text
    qid = r.json()["id"]

    # `BackgroundTasks` run before `TestClient`'s call returns, so the
    # lifecycle (queued -> running -> done) has already finished here --
    # a dashboard polling `GET /queue/{id}` must see it land, not 404 (the
    # demo re-seed wipes and rebuilds the `Queue` table along with
    # everything else it resets).
    qstatus = client.get(f"/api/v1/queue/{qid}")
    assert qstatus.status_code == 200, qstatus.text
    assert qstatus.json()["status"] == "done"

    fresh = client.get("/api/v1/overview").json()
    assert fresh["age_s"] is not None and fresh["age_s"] < 60


def test_demo_scan_now_is_a_noop_result_when_there_is_no_demo_tenant(client):
    """Sanity: the background job must not explode (or create a tenant) if
    it somehow ran against a hub with no `demo` tenant."""
    from guardian_hub.routers.write import _run_demo_scan

    _run_demo_scan("nonexistent-queue-id", "nonexistent-tenant-id")
    db = SessionLocal()
    assert db.query(m.Tenant).count() == 0
