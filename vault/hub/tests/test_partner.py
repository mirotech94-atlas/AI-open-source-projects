"""The Partner level: an MSP above its customers.

What these guard, in order of how badly the failure would read:

1. A partner's staff never see another partner's customer -- not a 403
   that confirms the tenant exists, and not a row in the switcher.
2. A customer's brand falls through to the partner's PER KEY, and a
   report freezes the resolved brand of the day.
3. The partner routes are superadmin-only.
"""
import datetime as dt

from guardian_hub import branding, models as m
from guardian_hub.db import SessionLocal
from guardian_hub.deps import COOKIE, memberships
from guardian_hub.reports import builder


def _partner(db, slug, brand=None):
    p = m.Partner(slug=slug, name=slug.title() + " MSP", brand=brand or {})
    db.add(p); db.flush()
    return p


def _tenant(db, slug, partner=None, brand=None):
    t = m.Tenant(slug=slug, name=slug.title(), partner_id=partner.id if partner else None, brand=brand or {})
    db.add(t); db.flush()
    return t


def _user(db, username, superadmin=False):
    u = m.User(username=username, email=f"{username}@example.test", is_superadmin=superadmin)
    db.add(u); db.flush()
    return u


def _sign_in(client, db, user, tenant_id):
    s = m.Session(id=m.uid(), user_id=user.id, tenant_id=tenant_id, expires=m.utcnow() + dt.timedelta(hours=1))
    db.add(s); db.commit()
    client.cookies.set(COOKIE, s.id)
    return s


def _world(db):
    """Two partners, two customers each, one direct customer, one engineer per partner."""
    a = _partner(db, "alpha", {"name": "Alpha IT", "accent": "#112233"})
    b = _partner(db, "beta", {"name": "Beta Networks"})
    a1, a2 = _tenant(db, "a1", a), _tenant(db, "a2", a)
    b1, b2 = _tenant(db, "b1", b), _tenant(db, "b2", b)
    direct = _tenant(db, "direct")
    ea, eb = _user(db, "eng-a"), _user(db, "eng-b")
    db.add(m.PartnerMembership(user_id=ea.id, partner_id=a.id, role="operator"))
    db.add(m.PartnerMembership(user_id=eb.id, partner_id=b.id, role="admin"))
    db.commit()
    return dict(a=a, b=b, a1=a1, a2=a2, b1=b1, b2=b2, direct=direct, ea=ea, eb=eb)


# --------------------------------------------------------------- isolation

def test_partner_staff_reach_every_customer_of_their_partner_and_no_other(client):
    db = SessionLocal(); w = _world(db)
    got = {t.slug: role for t, role in memberships(db, w["ea"])}
    assert got == {"a1": "operator", "a2": "operator"}
    got = {t.slug: role for t, role in memberships(db, w["eb"])}
    assert got == {"b1": "admin", "b2": "admin"}


def test_a_customer_added_tomorrow_is_reachable_tomorrow_and_one_that_leaves_is_gone(client):
    db = SessionLocal(); w = _world(db)
    a3 = _tenant(db, "a3", w["a"]); db.commit()
    assert "a3" in {t.slug for t, _ in memberships(db, w["ea"])}
    w["a1"].partner_id = None; db.commit()
    assert "a1" not in {t.slug for t, _ in memberships(db, w["ea"])}


def test_a_direct_membership_on_one_customer_can_only_raise_the_partner_role(client):
    db = SessionLocal(); w = _world(db)
    db.add(m.Membership(user_id=w["ea"].id, tenant_id=w["a1"].id, role="admin"))
    db.add(m.Membership(user_id=w["eb"].id, tenant_id=w["b1"].id, role="viewer"))
    db.commit()
    assert dict((t.slug, r) for t, r in memberships(db, w["ea"]))["a1"] == "admin"
    assert dict((t.slug, r) for t, r in memberships(db, w["eb"]))["b1"] == "admin"   # partner admin wins over direct viewer


def test_switching_into_another_partners_customer_is_refused_and_the_switcher_never_lists_it(client):
    db = SessionLocal(); w = _world(db)
    _sign_in(client, db, w["ea"], w["a1"].id)
    me = client.get("/api/v1/me").json()
    assert {t["slug"] for t in me["tenants"]} == {"a1", "a2"}
    for foreign in (w["b1"], w["direct"]):
        assert client.post("/api/v1/me/tenant", json={"tenant_id": foreign.id}).status_code == 403
    assert client.post("/api/v1/me/tenant", json={"tenant_id": w["a2"].id}).status_code == 200


def test_a_session_pinned_to_a_foreign_tenant_reads_nothing(client):
    """The switcher is the front door; this is the back one. A session row
    whose tenant is another partner's customer (a stale cookie after a
    customer moved partners) must not read that customer's data."""
    db = SessionLocal(); w = _world(db)
    _sign_in(client, db, w["ea"], w["b1"].id)
    r = client.get("/api/v1/overview")
    assert r.status_code == 403
    assert "no active tenant" in r.text


def test_other_partners_objects_are_404_not_403_through_the_owner_api(client):
    """A partner-facing route that grows on top of this must inherit the
    404 rule, so it is pinned at the superadmin routes too: an unknown id
    and a real one you may not touch read the same."""
    db = SessionLocal(); w = _world(db)
    root = _user(db, "root", superadmin=True); db.commit()
    _sign_in(client, db, root, w["a1"].id)
    assert client.get("/api/v1/partners/nope").status_code == 404
    # detaching a tenant from a partner it does not belong to is a 404 too,
    # not a 409 that says "it belongs to someone else"
    assert client.delete(f"/api/v1/partners/{w['a'].id}/tenants/{w['b1'].id}").status_code == 404


def test_partner_routes_are_superadmin_only(client):
    db = SessionLocal(); w = _world(db)
    _sign_in(client, db, w["eb"], w["b1"].id)   # a partner ADMIN, still not superadmin
    assert client.get("/api/v1/partners").status_code == 403
    assert client.post("/api/v1/partners", json={"slug": "x", "name": "X"}).status_code == 403
    assert client.get("/api/v1/tenants").status_code == 403


# ----------------------------------------------------------------- brand

def test_brand_falls_through_per_key_not_per_blob(client):
    db = SessionLocal(); w = _world(db)
    w["a1"].brand = {"domain": "a1.example"}; db.commit()
    got = branding.resolve(db, w["a1"])
    assert got["name"] == "Alpha IT" and got["source"]["name"] == "partner"
    assert got["accent"] == "#112233" and got["source"]["accent"] == "partner"
    assert got["domain"] == "a1.example" and got["source"]["domain"] == "tenant"
    assert got["partner"] == "Alpha MSP"


def test_a_direct_customer_and_a_partner_without_a_brand_land_on_guardian(client):
    db = SessionLocal(); w = _world(db)
    got = branding.resolve(db, w["direct"])
    assert got["name"] == "Vault" and got["accent"] == branding.GUARDIAN_DEFAULT["accent"]
    assert set(got["source"].values()) == {"guardian"}
    got = branding.resolve(db, w["b1"])
    assert got["name"] == "Beta Networks" and got["source"]["accent"] == "guardian"


def test_a_cleared_customer_name_means_inherit_not_blank(client):
    db = SessionLocal(); w = _world(db)
    w["a1"].brand = {"name": "   "}; db.commit()
    assert branding.resolve(db, w["a1"])["name"] == "Alpha IT"


def test_the_report_masthead_is_the_partner_and_prepared_for_is_the_customer(client):
    """White-label is the whole point: the MSP's name on top, the
    customer's name in "Prepared for". Never the MSP in both."""
    db = SessionLocal(); w = _world(db)
    w["a1"].name = "Northwind Dental"; db.commit()
    html = builder.build_report(db, w["a1"], days=7)
    assert "ALPHA IT" in html
    # The masthead is a definition list since ff73ec2 ("a report reads as a
    # document"): the label and the name are separate cells. Read the cell,
    # not the old one-string markup, so the assertion is about WHO is named
    # and cannot go red again when the cover is restyled.
    import re
    prepared = re.search(r"Prepared for</dt>\s*<dd>([^<]*)</dd>", html)
    assert prepared, "the cover must say who the report was prepared for"
    assert prepared.group(1).strip() == "Northwind Dental"
    assert "Alpha" not in prepared.group(1)
    # and the resolved brand is frozen: editing the partner afterwards
    # changes nothing already rendered
    w["a"].brand = {"name": "Renamed"}; db.commit()
    assert "ALPHA IT" in html and "RENAMED" not in html


def test_the_report_masthead_falls_back_to_guardian_for_a_direct_customer(client):
    db = SessionLocal(); w = _world(db)
    html = builder.build_executive_report(db, w["direct"], days=7)
    assert '<div class="brand">Vault</div>' in html


# --------------------------------------------------------- owner's API

def test_owner_creates_a_partner_attaches_customers_and_grants_staff(client):
    db = SessionLocal()
    root = _user(db, "root", superadmin=True)
    t1, t2 = _tenant(db, "c1"), _tenant(db, "c2"); db.commit()
    _sign_in(client, db, root, t1.id)

    r = client.post("/api/v1/partners", json={"slug": "msp", "name": "The MSP", "brand": {"name": "MSP Co", "junk": "x"}})
    assert r.status_code == 200, r.text
    pid = r.json()["id"]
    assert r.json()["brand"] == {"name": "MSP Co"}          # unknown keys never stored
    assert client.post("/api/v1/partners", json={"slug": "msp", "name": "dup"}).status_code == 409

    assert client.post(f"/api/v1/partners/{pid}/tenants/{t1.id}").status_code == 200
    assert client.post(f"/api/v1/partners/{pid}/tenants/{t2.id}").status_code == 200
    assert client.post(f"/api/v1/partners/{pid}/staff",
                       json={"username": "Tech1", "email": "t@msp.test", "role": "operator"}).status_code == 200
    assert client.post(f"/api/v1/partners/{pid}/staff",
                       json={"username": "tech2", "email": "t2@msp.test", "role": "boss"}).status_code == 422

    got = client.get(f"/api/v1/partners/{pid}").json()
    assert {t["slug"] for t in got["tenants"]} == {"c1", "c2"}
    assert got["staff"] == [{"username": "tech1", "role": "operator"}]

    # the grant is live: tech1 signs in and sees both customers
    db2 = SessionLocal()
    tech = db2.query(m.User).filter_by(username="tech1").one()
    assert {t.slug for t, _ in memberships(db2, tech)} == {"c1", "c2"}

    # a customer belongs to one partner at a time
    r2 = client.post("/api/v1/partners", json={"slug": "other", "name": "Other"}).json()
    assert client.post(f"/api/v1/partners/{r2['id']}/tenants/{t1.id}").status_code == 409

    # detach, then the other may take it
    assert client.delete(f"/api/v1/partners/{pid}/tenants/{t1.id}").status_code == 200
    assert client.post(f"/api/v1/partners/{r2['id']}/tenants/{t1.id}").status_code == 200

    # patching the brand replaces it
    got = client.patch(f"/api/v1/partners/{pid}", json={"brand": {"accent": "#abcdef"}}).json()
    assert got["brand"] == {"accent": "#abcdef"}

    # removing staff
    assert client.delete(f"/api/v1/partners/{pid}/staff/tech1").status_code == 200
    assert client.delete(f"/api/v1/partners/{pid}/staff/tech1").status_code == 404
    assert client.get(f"/api/v1/partners/{pid}").json()["staff"] == []


def test_deleting_a_partner_orphans_its_customers_and_keeps_their_data(client):
    db = SessionLocal()
    root = _user(db, "root", superadmin=True)
    p = _partner(db, "gone")
    t = _tenant(db, "kept", p)
    db.add(m.Device(tenant_id=t.id, mac="aa:bb:cc:dd:ee:ff", ip="10.0.0.9", first_seen="2026-09-01T00:00:00", last_seen="2026-09-01T00:00:00"))
    db.commit()
    _sign_in(client, db, root, t.id)
    assert client.delete(f"/api/v1/partners/{p.id}").status_code == 200
    db2 = SessionLocal()
    kept = db2.query(m.Tenant).filter_by(slug="kept").one()
    assert kept.partner_id is None
    assert db2.query(m.Device).filter_by(tenant_id=kept.id).count() == 1
    assert db2.query(m.Partner).count() == 0
    assert client.delete(f"/api/v1/partners/{p.id}").status_code == 404
