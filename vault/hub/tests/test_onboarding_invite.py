"""Declaring a network gets somebody the means to watch it.

The security constraint is most of this file. The enrol code is a bearer
credential -- whoever holds it can attach a sensor to this tenant -- so the
address it goes to is NOT a parameter. A wizard field that mails a credential
to an address supplied in the request body is a credential-exfiltration feature
with a friendly label on it.
"""
from guardian_hub import mailer, models as m
from guardian_hub.db import SessionLocal
from tests.test_auth import login, seed_user


def _network(tid: str, name: str = "Clinic", slug: str = "clinic") -> str:
    db = SessionLocal()
    n = m.Network(tenant_id=tid, name=name, slug=slug, is_primary=True)
    db.add(n); db.commit(); nid = n.id; db.close()
    return nid


def test_the_invite_goes_to_the_account_address_and_cannot_be_redirected(client):
    _uid, tid = seed_user()
    login(client)
    nid = _network(tid)

    mailer.SENT.clear()
    r = client.post(f"/api/v1/onboarding/networks/{nid}/invite",
                    json={"email": "attacker@example.invalid"})
    assert r.status_code == 200, r.text
    assert len(mailer.SENT) == 1
    to, _subject, _text = mailer.SENT[0]
    assert to == "m@example.com"


def test_the_code_is_bound_to_the_network_it_was_minted_for(client):
    _uid, tid = seed_user()
    login(client)
    nid = _network(tid)

    client.post(f"/api/v1/onboarding/networks/{nid}/invite", json={})
    db = SessionLocal()
    s = db.query(m.Sensor).filter_by(tenant_id=tid).one()
    assert s.network_id == nid
    assert s.status == "pending"
    assert len(s.enrol_code) == 16      # the e-mailed variant, not the screen one
    db.close()


def test_the_reply_shows_the_network_as_pending_straight_away(client):
    """The wizard re-renders from this response, so it has to already reflect
    the code that was just minted -- otherwise the screen tells someone to
    request an invite they have this second been sent."""
    _uid, tid = seed_user()
    login(client)
    nid = _network(tid)
    body = client.post(f"/api/v1/onboarding/networks/{nid}/invite", json={}).json()
    assert [n["sensor"] for n in body["networks"]] == ["pending"]


def test_a_network_belonging_to_another_tenant_is_refused(client):
    _uid, tid = seed_user()
    login(client)
    db = SessionLocal()
    other = m.Tenant(slug="other", name="Other")
    db.add(other); db.flush()
    n = m.Network(tenant_id=other.id, name="Theirs", slug="theirs", is_primary=True)
    db.add(n); db.commit(); nid = n.id; db.close()

    mailer.SENT.clear()
    assert client.post(f"/api/v1/onboarding/networks/{nid}/invite", json={}).status_code == 404
    assert mailer.SENT == []


def test_a_viewer_cannot_mint_a_credential(client):
    """`POST /onboarding` beside this one is viewer-level, because every user
    walks the wizard. Minting a bearer credential is not walking a wizard."""
    seed_user(role="viewer")
    login(client)
    db = SessionLocal()
    tid = db.query(m.Tenant).one().id
    db.close()
    nid = _network(tid)
    assert client.post(f"/api/v1/onboarding/networks/{nid}/invite", json={}).status_code == 403


def test_the_act_is_recorded_without_recording_the_code(client):
    """An audit row says a sensor was invited onto a network. It must not carry
    the code: `Audit.body` is read back by the owner console and reaches
    reports, and a credential in a log is a credential."""
    _uid, tid = seed_user()
    login(client)
    nid = _network(tid)
    client.post(f"/api/v1/onboarding/networks/{nid}/invite", json={})

    db = SessionLocal()
    row = db.query(m.Audit).filter_by(tenant_id=tid, action="sensor.invite").one()
    code = db.query(m.Sensor).filter_by(tenant_id=tid).one().enrol_code
    blob = f"{row.target} {row.body}"
    db.close()
    assert code not in blob


def test_a_refused_relay_is_not_reported_as_sent(client, monkeypatch):
    """Zoho accepted Vault's mail for two days while Gmail discarded it,
    which is why `mailer.send` returns a bool. Telling a customer their
    instructions are on the way when nothing left the building is the failure
    this whole flow is most vulnerable to."""
    _uid, tid = seed_user()
    login(client)
    nid = _network(tid)
    monkeypatch.setattr(mailer, "send_sensor_install", lambda *a, **k: False)
    r = client.post(f"/api/v1/onboarding/networks/{nid}/invite", json={})
    assert r.status_code == 502, r.text
    assert "could not be sent" in r.text.lower() or "not sent" in r.text.lower()
