"""Item 21: gateway rules Vault PROPOSES, and never applies on its own.

Miro's ruling is the shape of the whole feature: propose with one-click
apply and a timed auto-rollback, never write to a router silently. The
tests that matter are therefore about restraint -- what Vault does NOT
propose, and what it does when somebody says no.
"""
from guardian_hub.db import SessionLocal
from guardian_hub import models as m
from guardian_hub.payloads import firewall
from guardian_hub.scans import latest_scan
from tests.test_auth import login, seed_user
from tests.test_seed_helpers import seeded_demo


def _expose(tid, ports, wan="203.0.113.9"):
    db = SessionLocal()
    scan = latest_scan(db, tid)
    raw = dict(scan.raw or {})
    home = dict(raw.get("home") or {})
    home["exposure"] = {"open_ports": ports, "wan_ip": wan,
                        "ts": "2026-09-22T10:00:00+00:00", "probe_host": "probe.example"}
    raw["home"] = home
    scan.raw = raw
    db.commit(); db.close()


def _rules(client):
    return client.get("/api/v1/security/firewall").json()


def test_an_exposed_database_port_becomes_a_rule_that_names_itself(client):
    tid = seeded_demo(client)
    _expose(tid, [3306])
    out = _rules(client)
    (rule,) = [r for r in out["rules"] if r["port"] == 3306]
    assert "MySQL" in rule["title"]
    assert "databases do not belong on the internet" in rule["because"]
    assert "203.0.113.9:3306" in rule["evidence"], "a rule with no evidence is a rule nobody can check"
    assert rule["risk"] == "CRIT"


def test_the_ports_people_actually_use_are_left_alone(client):
    """22, 80 and 443 are how somebody administers and serves their own
    things. A rule that blocks the door they are standing in gets applied
    once, rolled back in a panic, and the next real proposal is not read."""
    tid = seeded_demo(client)
    _expose(tid, [22, 80, 443])
    assert [r for r in _rules(client)["rules"] if r["status"] == "proposed"] == []


def test_guardian_says_plainly_that_it_cannot_apply_one(client):
    tid = seeded_demo(client)
    _expose(tid, [23])
    out = _rules(client)
    assert out["can_apply"] is False
    assert "no write access to this gateway" in out["why_not"]


def test_pressing_apply_fails_loudly_rather_than_lying(client):
    """Marking it applied and hoping would be Vault claiming a firewall
    rule exists when it does not -- the worst thing this product could say."""
    tid = seeded_demo(client)
    _expose(tid, [23])
    rule = [r for r in _rules(client)["rules"] if r["port"] == 23][0]
    r = client.post(f"/api/v1/security/firewall/{rule['id']}/apply")
    assert r.status_code == 409
    assert "write access" in r.json()["detail"]
    # And the row still says proposed, not applied.
    assert [x for x in _rules(client)["rules"] if x["id"] == rule["id"]][0]["status"] == "proposed"


def test_a_declined_rule_does_not_come_back_on_the_next_scan(client):
    """Re-proposing something a person has already answered is how a product
    trains them to stop reading it."""
    tid = seeded_demo(client)
    _expose(tid, [23])
    rule = [r for r in _rules(client)["rules"] if r["port"] == 23][0]
    assert client.post(f"/api/v1/security/firewall/{rule['id']}/decline",
                       json={"reason": "it is our own jump host"}).status_code == 200

    _expose(tid, [23])           # the finding is still there
    again = [x for x in _rules(client)["rules"] if x["port"] == 23]
    assert len(again) == 1, "one row per rule, however many scans see it"
    assert again[0]["status"] == "declined"
    assert again[0]["reason"] == "it is our own jump host"


def test_a_finding_that_goes_away_expires_rather_than_vanishing(client):
    """"This was open last week and is not now" is what an auditor asks
    about, and a deleted row cannot answer."""
    tid = seeded_demo(client)
    _expose(tid, [23])
    assert [r for r in _rules(client)["rules"] if r["port"] == 23][0]["status"] == "proposed"
    _expose(tid, [])
    assert [r for r in _rules(client)["rules"] if r["port"] == 23][0]["status"] == "expired"


def test_a_persisting_finding_never_mints_a_second_row(client):
    tid = seeded_demo(client)
    _expose(tid, [23, 3389])
    _rules(client); _rules(client); _rules(client)
    db = SessionLocal()
    n = db.query(m.FirewallProposal).filter_by(tenant_id=tid).count()
    db.close()
    assert n == 2


def test_a_viewer_cannot_decline_a_rule(client):
    tid = seeded_demo(client)
    _expose(tid, [23])
    rule = [r for r in _rules(client)["rules"] if r["port"] == 23][0]
    # `seeded_demo` already owns the `demo` tenant; add the viewer to it
    # rather than seeding a second one with the same slug.
    db = SessionLocal()
    u = m.User(username="lurker", email="l@example.com", display_name="Lurker")
    db.add(u); db.flush()
    db.add(m.Membership(user_id=u.id, tenant_id=tid, role="viewer"))
    db.commit(); db.close()
    login(client, "lurker", "l@example.com")
    assert client.post(f"/api/v1/security/firewall/{rule['id']}/decline",
                       json={"reason": "no"}).status_code in (401, 403)


def test_the_assistant_can_leave_a_rule_but_never_apply_one(client):
    """Ask Vault sits beside the panel, so "leave the telnet one alone"
    has to work. Applying does NOT have a tool, and that is deliberate: the
    day a gateway can be written to, applying a rule must stamp its
    automatic rollback deadline in the same breath."""
    from guardian_hub.chat import registry
    tid = seeded_demo(client)
    _expose(tid, [23])
    rule = [r for r in _rules(client)["rules"] if r["port"] == 23][0]

    assert "leave_firewall_rule" in registry.TOOLS
    assert not [n for n in registry.TOOLS if "firewall" in n and "apply" in n]

    db = SessionLocal()
    tenant = db.query(m.Tenant).filter_by(id=tid).one()
    user = db.query(m.User).filter_by(username="demoadmin").one()
    ctx = registry.ToolContext(db=db, tenant=tenant, user=user, role="admin",
                               request_ip="127.0.0.1")
    res = registry.dispatch(ctx, "leave_firewall_rule",
                            {"rule_id": rule["id"], "reason": "our own jump host"})
    db.close()
    assert res.ok, res.text
    assert [x for x in _rules(client)["rules"] if x["id"] == rule["id"]][0]["status"] == "declined"
