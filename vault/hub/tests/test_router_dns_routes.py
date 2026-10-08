"""Vault points the router's DNS at itself, so nobody opens 192.168.1.1.

Miro's call. The routes are the narrowest version of it, on the same path
`disable-remote-admin` already uses: a credential in the encrypted store, a
Test that has ALREADY passed, then a tier-3 single-use confirmation.

The gate that matters most: the first live login with a credential must
never be a mutating one.
"""
from guardian_hub.db import SessionLocal
from guardian_hub import models as m
from tests.test_seed_helpers import seeded_demo


def _router(client, tid, *, tested=True, enabled=True):
    """Configure a router the way the card does, then record whether its
    Test has passed. The password here is a fixture value: the one that was
    pasted into chat is compromised and is never used anywhere."""
    client.put("/api/v1/router", json={"base_url": "https://192.168.1.1",
                                       "password": "fixture-only", "username": "admin",
                                       "vendor": "fios", "enabled": enabled})
    db = SessionLocal()
    row = db.query(m.Router).filter_by(tenant_id=tid).one()
    row.last_test_ok = tested
    db.commit(); db.close()


def test_a_credential_that_has_never_been_tested_cannot_write(client):
    """The first live login must never be the mutating one."""
    tid = seeded_demo(client)
    _router(client, tid, tested=False)
    r = client.post("/api/v1/router/set-dns/confirm", json={"server": "192.168.1.170"})
    assert r.status_code == 409
    assert "Test connection first" in r.json()["detail"]


def test_setting_dns_needs_a_confirmation_and_refuses_a_bare_post(client):
    tid = seeded_demo(client)
    _router(client, tid)
    r = client.post("/api/v1/router/set-dns", json={"server": "192.168.1.170"})
    assert r.status_code == 409
    assert "confirm" in r.json()["detail"]


def test_the_confirmation_names_the_address_and_the_one_server_rule(client):
    tid = seeded_demo(client)
    _router(client, tid)
    out = client.post("/api/v1/router/set-dns/confirm",
                      json={"server": "192.168.1.170"}).json()
    assert out["tier"] == 3
    assert "192.168.1.170" in out["summary"]
    assert "second server is a way around the filter" in out["summary"]
    # And it warns that nothing changes for devices until their lease renews.
    assert "lease renews" in out["summary"]


def test_a_confirmation_for_a_DIFFERENT_address_is_refused(client):
    """The token is bound to the arguments. Minting one for a safe address
    and spending it on another would make the confirmation meaningless."""
    tid = seeded_demo(client)
    _router(client, tid)
    tok = client.post("/api/v1/router/set-dns/confirm",
                      json={"server": "192.168.1.170"}).json()["token"]
    r = client.post("/api/v1/router/set-dns",
                    json={"server": "8.8.8.8", "confirm_token": tok})
    assert r.status_code == 403


def test_a_confirmation_cannot_be_spent_twice(client):
    tid = seeded_demo(client)
    _router(client, tid)
    tok = client.post("/api/v1/router/set-dns/confirm",
                      json={"server": "192.168.1.170"}).json()["token"]
    body = {"server": "192.168.1.170", "confirm_token": tok}
    assert client.post("/api/v1/router/set-dns", json=body).status_code == 200
    assert client.post("/api/v1/router/set-dns", json=body).status_code == 403


def test_a_confirmed_change_is_queued_for_the_sensor_with_the_address(client):
    tid = seeded_demo(client)
    _router(client, tid)
    tok = client.post("/api/v1/router/set-dns/confirm",
                      json={"server": "192.168.1.170"}).json()["token"]
    out = client.post("/api/v1/router/set-dns",
                      json={"server": "192.168.1.170", "confirm_token": tok}).json()
    db = SessionLocal()
    q = db.query(m.Queue).filter_by(id=out["id"]).one()
    assert q.path == "/api/router/set-dns"
    assert q.body["server"] == "192.168.1.170"
    # The password is NOT in the queue: the sensor fetches it itself.
    assert "password" not in str(q.body)
    db.close()


def test_a_bad_address_never_becomes_a_job(client):
    tid = seeded_demo(client)
    _router(client, tid)
    for bad in ("", "1.1.1.1.1", "not-an-ip", "192.168.1"):
        assert client.post("/api/v1/router/set-dns/confirm",
                           json={"server": bad}).status_code == 422


def test_reading_the_routers_dns_needs_no_confirmation(client):
    """It changes nothing, and it answers the one question that decides
    whether DNS filtering works at all."""
    tid = seeded_demo(client)
    _router(client, tid)
    out = client.post("/api/v1/router/read-dns").json()
    db = SessionLocal()
    assert db.query(m.Queue).filter_by(id=out["id"]).one().path == "/api/router/read-dns"
    db.close()


def test_putting_it_back_is_EASIER_than_changing_it(client):
    """A confirmation exists to stop somebody changing something by
    accident. Making the undo harder than the change is the wrong way
    round, and the moment somebody wants it is the moment their network
    stopped working."""
    tid = seeded_demo(client)
    _router(client, tid)
    out = client.post("/api/v1/router/restore-dns",
                      json={"previous": {"dns_server_1": "192.168.1.1"}})
    assert out.status_code == 200
    db = SessionLocal()
    q = db.query(m.Queue).filter_by(id=out.json()["id"]).one()
    assert q.path == "/api/router/restore-dns"
    assert q.body["previous"]["dns_server_1"] == "192.168.1.1"
    db.close()


def test_a_viewer_cannot_touch_the_router(client):
    tid = seeded_demo(client)
    _router(client, tid)
    from tests.test_auth import login
    db = SessionLocal()
    u = m.User(username="lurker", email="l2@example.com", display_name="L")
    db.add(u); db.flush()
    db.add(m.Membership(user_id=u.id, tenant_id=tid, role="viewer"))
    db.commit(); db.close()
    login(client, "lurker", "l2@example.com")
    assert client.post("/api/v1/router/set-dns/confirm",
                       json={"server": "192.168.1.170"}).status_code in (401, 403)
    assert client.post("/api/v1/router/read-dns").status_code in (401, 403)
