"""`camera_facts` accounts for every camera exactly once.

`payloads/cameras.census` reports a camera as `online`, `offline` or
`unobserved`, and the owner reads those three as a complete answer: a camera
missing from all of them has silently stopped being counted, which on a
camera census is the failure that matters -- "nothing is wrong" and "I cannot
see it" must never render the same.

`camera_facts` supplies the parts. It counts `online` (presence ONLINE) and
`quiet` (OFFLINE or ARCHIVED) explicitly, and a camera in neither bucket --
presence never established, or aged out of freshness -- belongs to neither
count. That remainder is `unknown`, and this test is what keeps the three
buckets summing to `total` as presence states are added or renamed: add a
fourth state to `quiet`'s tuple without thinking about the remainder and the
arithmetic here goes red rather than the census quietly under-reporting.
"""

from guardian_hub.db import SessionLocal
from guardian_hub import models as m
from guardian_hub.payloads.security import camera_facts
from guardian_hub.scans import latest_scan

from tests.test_seed_helpers import seeded_demo


def _facts(tenant_id):
    db = SessionLocal()
    try:
        tenant = db.query(m.Tenant).filter_by(id=tenant_id).one()
        scan = latest_scan(db, tenant_id)
        return camera_facts(db, tenant, (scan.raw if scan else {}) or {})
    finally:
        db.close()


def test_every_camera_is_online_quiet_or_unknown_and_never_two_of_them(client):
    tenant_id = seeded_demo(client)
    facts = _facts(tenant_id)

    assert facts["total"] > 0, "the demo tenant seeds cameras; without them this proves nothing"
    assert facts["online"] + len(facts["quiet"]) + facts["unknown"] == facts["total"], (
        f"cameras unaccounted for: total={facts['total']} online={facts['online']} "
        f"quiet={len(facts['quiet'])} unknown={facts['unknown']}"
    )
    assert facts["unknown"] >= 0


def test_a_tenant_with_no_cameras_has_nothing_unknown(client):
    """The empty case must be zero, not a negative remainder."""
    from tests.test_auth import login, seed_user

    seeded_demo(client)
    client.post("/api/v1/auth/logout")
    seed_user(username="ann", email="a@x.com", tenant_slug="other", role="viewer")
    login(client, "ann", "a@x.com")

    db = SessionLocal()
    try:
        tenant = db.query(m.Tenant).filter_by(slug="other").one()
        facts = camera_facts(db, tenant, {})
    finally:
        db.close()

    assert facts["total"] == 0
    assert facts["online"] == 0
    assert facts["quiet"] == []
    assert facts["unknown"] == 0
