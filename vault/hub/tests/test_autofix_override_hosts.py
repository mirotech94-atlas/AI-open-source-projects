"""The exception control must offer the machines Vault actually knows.

It offered `config["ssh_hosts"]` alone — which no tenant on the live hub
has ever set — so the dropdown was permanently empty and the control
unusable.
"""
from guardian_hub.db import SessionLocal
from guardian_hub import models as m
from tests.test_auth import login, seed_user


def _setup(client, *, ssh_hosts=None, seen=()):
    _uid, tid = seed_user()
    login(client)
    db = SessionLocal()
    t = db.query(m.Tenant).filter_by(id=tid).one()
    if ssh_hosts is not None:
        t.config = {**(t.config or {}), "ssh_hosts": ssh_hosts}
    for name in seen:
        db.add(m.Host(tenant_id=tid, name=name, address=f"{name}.local", last={}))
    db.commit(); db.close()
    return tid


def test_a_host_guardian_has_seen_is_offered_even_with_no_ssh_config(client):
    _setup(client, seen=["vps1", "vps2"])
    body = client.get("/api/v1/settings/autofix/overrides").json()
    assert body["hosts"] == ["vps1", "vps2"]


def test_configured_and_observed_hosts_are_merged_without_duplicates(client):
    _setup(client, ssh_hosts=[{"name": "vps1"}, {"name": "edge"}], seen=["vps1", "vps2"])
    assert client.get("/api/v1/settings/autofix/overrides").json()["hosts"] == ["edge", "vps1", "vps2"]


def test_a_host_that_was_offered_can_actually_be_saved(client):
    # The write used to validate against a narrower list than the read
    # offered, so a person could pick a machine and be told it is not one.
    _setup(client, seen=["vps1"])
    actions = client.get("/api/v1/settings/autofix/overrides").json()["actions"]
    action = sorted(actions)[0]
    r = client.post("/api/v1/settings/autofix/overrides",
                    json={"host": "vps1", "action": action, "mode": "manual"})
    assert r.status_code == 200, r.text
    again = client.get("/api/v1/settings/autofix/overrides").json()
    assert again["overrides"]["vps1"][action] == "manual"


def test_a_machine_guardian_has_never_seen_is_still_refused(client):
    _setup(client, seen=["vps1"])
    actions = client.get("/api/v1/settings/autofix/overrides").json()["actions"]
    r = client.post("/api/v1/settings/autofix/overrides",
                    json={"host": "not-a-host", "action": sorted(actions)[0], "mode": "manual"})
    assert r.status_code == 422
