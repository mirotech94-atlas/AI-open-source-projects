"""Task 59 -- the public site's "Request access" form and the owner's list.

`POST /api/v1/public/lead` is the only unauthenticated *write* on the hub, so
these tests are mostly about what it refuses: oversized fields, a filled
honeypot, and more than a handful of submissions from one address. The list
route on the other side is superadmin-only, and that is tested from all three
angles (anonymous, tenant admin, superadmin) because a leak there hands one
customer the whole sales pipeline.
"""
from guardian_hub import models as m, ratelimit
from guardian_hub.db import SessionLocal
from guardian_hub.routers import leads as leads_router
from tests.test_auth import login, seed_user
from tests.test_tenants import make_super

GOOD = {
    "name": "Dana Okafor",
    "email": "Dana@Example.com",
    "company": "Okafor Dental",
    "network_size": "one-site",
    "note": "Two servers and a shop full of tablets.",
    "accept_terms": True,
}


def _leads():
    db = SessionLocal()
    try:
        return db.query(m.Lead).order_by(m.Lead.created.desc()).all()
    finally:
        db.close()


# --------------------------------------------------------------- the happy path

def test_lead_is_stored_and_nothing_is_echoed_back(client):
    r = client.post("/api/v1/public/lead", json=GOOD)
    assert r.status_code == 200, r.text

    body = r.json()
    assert body["ok"] is True
    # The confirmation is a fixed string: no field of it carries anything the
    # submitter typed, so a submission can never be reflected into the page.
    blob = r.text.lower()
    for typed in ("dana", "example.com", "okafor", "one-site", "tablets"):
        assert typed not in blob

    rows = _leads()
    assert len(rows) == 1
    assert rows[0].name == "Dana Okafor"
    assert rows[0].email == "dana@example.com"      # lower-cased
    assert rows[0].company == "Okafor Dental"
    assert rows[0].network_size == "one-site"
    assert rows[0].ip                                # the source address is kept


def test_optional_fields_may_be_omitted(client):
    r = client.post("/api/v1/public/lead", json={"name": "Sam", "email": "s@x.io", "accept_terms": True})
    assert r.status_code == 200
    row = _leads()[0]
    assert row.company == "" and row.network_size == "" and row.note == ""


def test_name_and_email_are_required(client):
    assert client.post("/api/v1/public/lead", json={"email": "s@x.io"}).status_code == 422
    assert client.post("/api/v1/public/lead", json={"name": "Sam"}).status_code == 422
    assert client.post("/api/v1/public/lead", json={"name": "", "email": "s@x.io"}).status_code == 422
    assert _leads() == []


# -------------------------------------------------------------- the email shape

def test_a_second_address_smuggled_in_after_a_comma_is_refused(client):
    # This becomes `User.username`: a second address riding along after a
    # comma must not reach the DB, or approval could create or notify an
    # address the submitter never agreed to.
    body = dict(GOOD, email="victim@x.com, attacker@evil.com")
    assert client.post("/api/v1/public/lead", json=body).status_code == 422
    assert _leads() == []


def test_an_email_longer_than_the_username_column_is_refused(client):
    # `User.username` is `String(64)` and `AuthStart.username` caps at 64 --
    # a lead's email becomes that username at approval, so anything the
    # column could not hold is refused here instead of failing later.
    local = "a" * 78
    body = dict(GOOD, email=f"{local}@x.com")
    assert len(body["email"]) == 84
    assert client.post("/api/v1/public/lead", json=body).status_code == 422
    assert _leads() == []


def test_a_mixed_case_email_is_accepted_and_stored_lower_cased(client):
    body = dict(GOOD, email="Dana@Example.com")
    assert client.post("/api/v1/public/lead", json=body).status_code == 200
    assert _leads()[0].email == "dana@example.com"


# ------------------------------------------------------------------- the caps

def test_every_field_has_a_length_cap(client):
    caps = {"name": 120, "email": 200, "company": 120, "network_size": 32,
            "note": 2000, "website": 200}
    for field, cap in caps.items():
        body = dict(GOOD)
        body[field] = "x" * (cap + 1)
        r = client.post("/api/v1/public/lead", json=body)
        assert r.status_code == 422, f"{field} accepted {cap + 1} characters"
    assert _leads() == []


def test_a_field_at_exactly_the_cap_is_accepted(client):
    body = dict(GOOD, note="n" * 2000)
    assert client.post("/api/v1/public/lead", json=body).status_code == 200
    assert len(_leads()[0].note) == 2000


# --------------------------------------------------------------- the honeypot

def test_filled_honeypot_is_dropped_silently(client):
    r = client.post("/api/v1/public/lead", json=dict(GOOD, website="http://spam.example"))
    # Same 200 and same body a real submission gets: a bot must not be able to
    # tell that it was caught, or it just tries again without the field.
    assert r.status_code == 200
    assert r.json() == client.post("/api/v1/public/lead", json=GOOD).json()
    # ...and only the real one was stored.
    assert len(_leads()) == 1
    assert _leads()[0].name == "Dana Okafor"


def test_honeypot_of_only_whitespace_is_still_a_real_submission(client):
    # A browser that autofills a space, or a stray newline, must not lose a
    # genuine lead -- the check is on the *stripped* value.
    assert client.post("/api/v1/public/lead", json=dict(GOOD, website="   ")).status_code == 200
    assert len(_leads()) == 1


def test_a_caught_bot_does_not_spend_the_rate_limit(client):
    limit = leads_router.LEAD_PER_IP[0]
    for _ in range(limit + 3):
        assert client.post("/api/v1/public/lead", json=dict(GOOD, website="x")).status_code == 200
    # The honeypot returns before the limiter, so a genuine visitor behind the
    # same address (a shared office IP, say) still gets their full allowance.
    assert client.post("/api/v1/public/lead", json=GOOD).status_code == 200
    assert len(_leads()) == 1


# ------------------------------------------------------------- the rate limit

def test_rate_limit_per_ip(client):
    limit, _window = leads_router.LEAD_PER_IP
    for i in range(limit):
        # A distinct email per post: this test is about the per-IP counter,
        # not about deduplication, so each post must land its own row.
        body = {**GOOD, "email": f"dana{i}@example.com"}
        assert client.post("/api/v1/public/lead", json=body).status_code == 200, i
    r = client.post("/api/v1/public/lead", json=GOOD)
    assert r.status_code == 429
    assert len(_leads()) == limit
    # A refused request stores nothing, and says so without naming anything
    # the submitter sent.
    assert "dana" not in r.text.lower()


def test_rate_limit_is_per_address_not_global_for_ordinary_traffic(client):
    limit, _window = leads_router.LEAD_PER_IP
    # nginx puts the real address in X-Real-IP; the hub trusts it only because
    # TestClient's peer ("testclient") is one of the loopback names.
    for i in range(limit):
        assert client.post("/api/v1/public/lead", json=GOOD,
                           headers={"X-Real-IP": "203.0.113.9"}).status_code == 200, i
    assert client.post("/api/v1/public/lead", json=GOOD,
                       headers={"X-Real-IP": "203.0.113.9"}).status_code == 429
    # A different address still gets through.
    assert client.post("/api/v1/public/lead", json=GOOD,
                       headers={"X-Real-IP": "198.51.100.4"}).status_code == 200


def test_the_stored_ip_is_the_forwarded_one_when_it_is_valid(client):
    client.post("/api/v1/public/lead", json=GOOD, headers={"X-Real-IP": "203.0.113.9"})
    assert _leads()[0].ip == "203.0.113.9"

    ratelimit.reset()
    # A garbage header falls back to the peer rather than becoming a bucket key
    # (or a stored value) of the attacker's choosing.
    # A distinct email: this test is about the ip fallback, not deduplication,
    # so this post must land its own row rather than updating the one above.
    client.post("/api/v1/public/lead", json=dict(GOOD, name="Second", email="second@example.com"),
                headers={"X-Real-IP": "not-an-ip; drop table lead"})
    assert _leads()[0].ip == "testclient"


def test_a_global_ceiling_bounds_a_distributed_flood(client):
    """Per-IP alone is no defence against a botnet, so there is a hub-wide
    ceiling too. Exercised with a small stand-in rather than 60 real posts."""
    original = leads_router.LEAD_GLOBAL
    leads_router.LEAD_GLOBAL = (3, 600)
    try:
        for i in range(3):
            assert client.post("/api/v1/public/lead", json=GOOD,
                               headers={"X-Real-IP": f"203.0.113.{i}"}).status_code == 200, i
        assert client.post("/api/v1/public/lead", json=GOOD,
                           headers={"X-Real-IP": "203.0.113.200"}).status_code == 429
    finally:
        leads_router.LEAD_GLOBAL = original


# ------------------------------------------------------------------- the list

def test_leads_list_is_superadmin_only(client):
    client.post("/api/v1/public/lead", json=GOOD)

    # anonymous
    assert client.get("/api/v1/leads").status_code == 401

    # a tenant admin -- who must never see the owner's pipeline
    seed_user(username="ann", email="ann@x.com", tenant_slug="acme", role="admin")
    login(client, "ann", "ann@x.com")
    r = client.get("/api/v1/leads")
    assert r.status_code == 403
    assert "dana" not in r.text.lower()
    client.post("/api/v1/auth/logout")

    # the superadmin
    make_super(client)
    r = client.get("/api/v1/leads")
    assert r.status_code == 200
    rows = r.json()["leads"]
    assert len(rows) == 1
    assert rows[0]["email"] == "dana@example.com"
    assert set(rows[0]) == {"id", "name", "email", "company", "network_size",
                            "note", "ip", "created", "site_name", "status",
                            "decided_at", "decided_by", "tenant_slug"}


def test_leads_list_is_newest_first(client):
    for name in ("first", "second", "third"):
        # A distinct email per post: this test is about ordering, not
        # deduplication, so each post must land its own row.
        client.post("/api/v1/public/lead", json=dict(GOOD, name=name, email=f"{name}@example.com"))
    make_super(client)
    rows = client.get("/api/v1/leads").json()["leads"]
    assert [r["name"] for r in rows] == ["third", "second", "first"]


# ------------------------------------------------------------ decision columns

def test_a_new_lead_is_pending_and_carries_its_site_name(client):
    r = client.post("/api/v1/public/lead", json={**GOOD, "site_name": "Okafor Dental — Main St"})
    assert r.status_code == 200
    row = _leads()[0]
    assert row.status == "pending"
    assert row.site_name == "Okafor Dental — Main St"
    assert row.decided_at is None and row.decided_by == "" and row.tenant_id is None
    assert row.terms_accepted_at is not None


def test_a_submission_without_accepting_the_terms_is_refused_and_not_stored(client):
    r = client.post("/api/v1/public/lead", json={**GOOD, "accept_terms": False})
    assert r.status_code == 422
    assert _leads() == []


def test_a_submission_that_omits_accept_terms_entirely_is_refused_and_not_stored(client):
    body = {k: v for k, v in GOOD.items() if k != "accept_terms"}
    r = client.post("/api/v1/public/lead", json=body)
    assert r.status_code == 422
    assert _leads() == []


def test_the_list_shows_status_site_name_and_no_tenant_yet(client):
    client.post("/api/v1/public/lead", json={**GOOD, "site_name": "Main St"})
    make_super(client)
    lead = client.get("/api/v1/leads").json()["leads"][0]
    assert lead["status"] == "pending"
    assert lead["site_name"] == "Main St"
    assert lead["decided_at"] == "" and lead["decided_by"] == "" and lead["tenant_slug"] == ""


def test_a_second_pending_submission_from_the_same_email_updates_the_row(client):
    client.post("/api/v1/public/lead", json={**GOOD, "note": "first"})
    client.post("/api/v1/public/lead", json={**GOOD, "note": "second, with a typo fixed", "site_name": "Main St"})
    rows = _leads()
    assert len(rows) == 1
    assert rows[0].note == "second, with a typo fixed"
    assert rows[0].site_name == "Main St"
    assert rows[0].status == "pending"
