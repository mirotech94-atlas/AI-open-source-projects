"""A visitor's decisions survive the demo refresh.

The refresh deletes and rebuilds every ingest-derived row every ten minutes,
which is the right way to keep the demo's story internally consistent. It also
erased what the person looking at the demo had just done: acknowledge an alert
or accept a recommendation, and within ten minutes it was silently untouched
again. A prospect clicking around concludes the buttons do nothing.

Only the human decision is carried across. Everything the sensor reports is
still rebuilt from the fixtures, so the demo can never drift into claiming
something its own data does not say.
"""

from guardian_hub import models as m, seed
from guardian_hub.db import SessionLocal
from tests.test_seed_helpers import seeded_demo


def _demo_tenant(db):
    return db.query(m.Tenant).filter_by(slug="demo").first()


def test_an_acknowledged_alert_stays_acknowledged(client):
    seeded_demo(client)
    db = SessionLocal()
    try:
        tenant = _demo_tenant(db)
        alert = db.query(m.Alert).filter_by(tenant_id=tenant.id).first()
        key = alert.key
        alert.status = "acked"
        alert.resolved_by = "demoadmin"
        db.commit()

        seed.refresh_demo(db)

        after = db.query(m.Alert).filter_by(tenant_id=tenant.id, key=key).first()
        assert after is not None, "the refresh dropped the alert entirely"
        assert after.status == "acked", "the refresh silently un-acknowledged it"
        assert after.resolved_by == "demoadmin"
    finally:
        db.close()


def test_a_declined_recommendation_stays_declined(client):
    seeded_demo(client)
    db = SessionLocal()
    try:
        tenant = _demo_tenant(db)
        rec = db.query(m.Recommendation).filter_by(tenant_id=tenant.id).first()
        key = rec.key
        rec.decision = "no"
        rec.decided_by = "demoadmin"
        db.commit()

        seed.refresh_demo(db)

        after = db.query(m.Recommendation).filter_by(tenant_id=tenant.id, key=key).first()
        assert after is not None, "the refresh dropped the recommendation entirely"
        assert after.decision == "no", "the refresh silently undid the decision"
    finally:
        db.close()


def test_the_refresh_still_rebuilds_everything_it_used_to(client):
    """Only the decision is carried. The observation is rebuilt as before.

    The seed gives some alerts a status of its own, so "untouched means open"
    is not the property to assert -- the property is that a refresh changes
    nothing except where a person decided something.
    """
    seeded_demo(client)
    db = SessionLocal()
    try:
        tenant = _demo_tenant(db)
        before = {
            a.key: a.status
            for a in db.query(m.Alert).filter_by(tenant_id=tenant.id).all()
        }
        seed.refresh_demo(db)
        after = {
            a.key: a.status
            for a in db.query(m.Alert).filter_by(tenant_id=tenant.id).all()
        }
        assert after == before, (
            "a refresh with no decisions in between changed the alert set: "
            f"{ {k: (before.get(k), after.get(k)) for k in set(before) ^ set(after)} }"
        )
    finally:
        db.close()


def test_a_left_security_check_follows_its_network_across_a_refresh(client):
    """"Leave it" on a Security check survives the refresh -- `CheckDecision`
    is not reset -- but its `network_id` did not: the refresh recreates the
    demo's networks with new ids, so the decision pointed at a network that
    no longer existed and stopped matching the check it was about. Found in
    production as two orphaned rows."""
    tid = seeded_demo(client)
    db = SessionLocal()
    net = db.query(m.Network).filter_by(tenant_id=tid).first()
    slug = net.slug
    db.add(m.CheckDecision(tenant_id=tid, check_key="wifi:wps", network_id=net.id,
                           target="", status="left", reason="demo visitor", decided_by="demo"))
    db.commit()
    seed.refresh_demo(db)
    db.expire_all()
    dec = db.query(m.CheckDecision).filter_by(tenant_id=tid, check_key="wifi:wps").one()
    live = db.get(m.Network, dec.network_id)
    assert live is not None, "the decision points at a network the refresh deleted"
    assert live.slug == slug and dec.status == "left"
    db.close()
