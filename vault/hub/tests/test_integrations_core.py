import time

from tests.test_seed_helpers import seeded_demo
from guardian_hub.db import SessionLocal
from guardian_hub import models as m, crypto
from guardian_hub.integrations import registry


def enrol(client, tid):
    """Mint a sensor token for `tid` through the tenant-admin route (the
    client here is already signed in as the demo tenant's admin, not a
    superadmin, so `routers.sensors.enrol_code` -- not
    `routers.tenants.enrol_code` -- is the one available to it)."""
    code = client.post("/api/v1/sensors/enrol-code").json()["code"]
    r = client.post("/api/v1/sensor/enrol", json={"code": code, "name": "t", "platform": "linux", "version": "1"})
    assert r.status_code == 200, r.text
    return r.json()["token"]


def ingest_payload_with_new_crit(key):
    """A minimal, valid `store_scan` payload carrying exactly one new,
    open CRIT alert under `key` (real shape: `last.alerts` + `state.
    alerts_db`, per ingest.py's module docstring)."""
    ts = "2026-09-10T12:00:00"
    return {
        "last": {"ts": ts, "alerts": [{"key": key, "sev": "CRIT", "msg": "attacker seen", "count": 1}]},
        "state": {"alerts_db": {key: {
            "key": key, "sev": "CRIT", "msg": "attacker seen", "count": 1, "state": "open",
            "first_seen": ts, "last_seen": ts,
        }}},
        "events": [], "history": [], "fixes": [], "dns_queries": [],
    }


def test_secret_is_encrypted_at_rest_and_masked_in_the_payload(client):
    seeded_demo(client)
    url = "https://hooks.slack.com/services/T000/B000/examplewebhooktoken"
    assert client.post("/api/v1/integrations", json={"kind": "slack", "enabled": True,
        "routes": {"CRIT": True, "WARN": True, "INFO": False}, "fields": {"url": url}}).status_code == 200
    row = SessionLocal().query(m.Integration).filter_by(kind="slack").one()
    assert row.secret != url and crypto.decrypt(row.secret) == url
    card = next(c for c in client.get("/api/v1/integrations").json()["integrations"] if c["kind"] == "slack")
    assert card["configured"] and url not in str(card) and "…" in card["fields"][0]["value"]


def test_routes_decide_delivery_and_there_is_no_implicit_default(client, monkeypatch):
    seeded_demo(client)
    sent = []
    for kind in ("ntfy", "slack", "teams", "webhook"):
        monkeypatch.setattr(registry.REGISTRY[kind], "send",
                            lambda ctx, msg, k=kind: sent.append((k, msg.severity)) or registry.SendResult(True, ""))
    db = SessionLocal(); t = db.query(m.Tenant).filter_by(slug="demo").one()
    registry.deliver(db, t, registry.Message(title="t", body="b", severity="INFO", url="", actions=[], kind="alert"))
    # ntfy is retired as the implicit default (push moves to the mobile app):
    # the slack row routes INFO off and no other row exists, so nothing goes.
    assert sent == []
    sent.clear()
    registry.deliver(db, t, registry.Message(title="t", body="b", severity="CRIT", url="", actions=[], kind="alert"))
    assert set(k for k, _ in sent) == {"slack"}


def _wait_for(predicate, timeout=2.0, interval=0.02):
    """Fix round 1, finding 2: delivery now runs off the ingest request on
    a background dispatch pool (`registry.deliver_background`), so a
    monkeypatched `registry.deliver` fires slightly after `client.post`
    returns rather than synchronously inside it -- poll instead of
    asserting immediately."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return predicate()


def test_ingest_delivers_a_new_crit_exactly_once(client, monkeypatch):
    tid = seeded_demo(client)
    calls = []
    monkeypatch.setattr(registry, "deliver", lambda db, t, msg: calls.append((msg.kind, msg.severity, msg.dedup_key)))
    payload = ingest_payload_with_new_crit("sec:attacker:demo:203.0.113.9")   # helper in this module
    H = {"Authorization": f"Bearer {enrol(client, tid)}"}
    client.post("/api/v1/sensor/ingest", json=payload, headers=H)
    assert _wait_for(lambda: len(calls) == 1)
    assert calls == [("alert", "CRIT", "sec:attacker:demo:203.0.113.9")]
    client.post("/api/v1/sensor/ingest", json=payload, headers=H)   # same alert, no new fire
    time.sleep(0.1)   # give a (correctly absent) second dispatch a chance to land
    assert len(calls) == 1


def test_webhook_rejects_plain_http_and_test_records_the_error(client, monkeypatch):
    seeded_demo(client)
    assert client.post("/api/v1/integrations", json={"kind": "webhook", "enabled": True,
        "routes": {}, "fields": {"url": "http://insecure.example"}}).status_code == 422
    monkeypatch.setattr(registry.REGISTRY["ntfy"], "test",
                        lambda ctx: registry.SendResult(False, "ntfy 503"))
    body = client.post("/api/v1/integrations/ntfy/test").json()
    assert body["ok"] is False and body["error"] == "ntfy 503"
    card = next(c for c in client.get("/api/v1/integrations").json()["integrations"] if c["kind"] == "ntfy")
    assert card["last_error"] == "ntfy 503" and card["last_test"]


# --------------------------------------------------------------------------
# Additional coverage beyond the brief's required tests.
# --------------------------------------------------------------------------

def test_get_integrations_lists_every_registered_kind_even_unconfigured(client):
    # Extended by Task 41: discord/pagerduty/telegram/jira/servicenow/
    # homeassistant register into the same REGISTRY as the phase-1 four.
    seeded_demo(client)
    kinds = {c["kind"] for c in client.get("/api/v1/integrations").json()["integrations"]}
    # ntfy is registered but no longer offered to a tenant that has not set
    # it up (push moves to the mobile app) -- see test_integrations_tab.
    assert kinds == {"slack", "teams", "webhook",
                      "discord", "pagerduty", "telegram", "jira", "servicenow", "homeassistant",
                      "halopsa", "autotask", "connectwise",
                      # Item 25: findings by email as well as by chat. Every
                      # other channel was here; email -- the one every
                      # customer already has, and the only one that still
                      # works when a chat tool is down -- was not.
                      "email"}


def test_delete_integration_removes_the_row_and_is_audited(client):
    seeded_demo(client)
    assert client.delete("/api/v1/integrations/slack").status_code == 200
    assert client.delete("/api/v1/integrations/slack").status_code == 404
    db = SessionLocal()
    assert db.query(m.Integration).filter_by(kind="slack").first() is None
    assert db.query(m.Audit).filter_by(action="integration.delete").count() == 1


def test_secret_never_appears_in_the_audit_log(client):
    seeded_demo(client)
    url = "https://hooks.slack.com/services/T000/B000/examplewebhooktoken"
    client.post("/api/v1/integrations", json={"kind": "teams", "enabled": True,
        "routes": {"CRIT": True}, "fields": {"url": url}})
    db = SessionLocal()
    rows = db.query(m.Audit).filter_by(action="integration.update").all()
    assert rows and all(url not in str(r.body) for r in rows)


def test_first_integration_flips_sensor_notify_to_local_only(client):
    tid = seeded_demo(client)
    # seed_demo creates its demo Slack row directly (not through this
    # route), so start from zero Integration rows to exercise the
    # "first row created" flip this route itself is responsible for.
    client.delete("/api/v1/integrations/slack")
    db = SessionLocal()
    assert db.query(m.Integration).filter_by(tenant_id=tid).count() == 0

    assert client.post("/api/v1/integrations", json={"kind": "ntfy", "enabled": True,
        "routes": {"CRIT": True}, "fields": {"topic": "myron-alerts"}}).status_code == 200
    tenant = SessionLocal().query(m.Tenant).filter_by(id=tid).one()
    assert (tenant.config or {}).get("notify", {}).get("local_only") is True


def test_first_integration_flip_writes_an_edit_row_for_the_sensor_to_sync(client):
    """Fix round 1, finding 1: without this Edit row an enrolled sensor's
    ordinary /api/v1/sensor/edits poll never learns delivery moved to the
    hub, and keeps pushing over the network itself (double-send). The
    sensor-side half of this (that its own `sync.apply_edit` merges the
    body into its on-disk config, ending up with `notify.local_only=true`)
    is covered by `sensor/tests/test_sync.py::
    test_apply_edit_config_notify_local_only` -- this half only has to
    prove the hub actually writes the row, with the right shape."""
    tid = seeded_demo(client)
    client.delete("/api/v1/integrations/slack")
    db = SessionLocal()
    assert db.query(m.Edit).filter_by(tenant_id=tid, path="/api/config").count() == 0

    assert client.post("/api/v1/integrations", json={"kind": "ntfy", "enabled": True,
        "routes": {"CRIT": True}, "fields": {"topic": "myron-alerts"}}).status_code == 200

    edits = SessionLocal().query(m.Edit).filter_by(tenant_id=tid, path="/api/config").all()
    assert len(edits) == 1
    assert edits[0].body["config"]["notify"]["local_only"] is True

    # A second integration save (no longer the "first" row) must not spam
    # another /api/config edit -- the flip only ever needs to happen once.
    assert client.post("/api/v1/integrations", json={"kind": "teams", "enabled": True,
        "routes": {"CRIT": True}, "fields": {"url": "https://outlook.office.com/webhook/x"}}).status_code == 200
    assert SessionLocal().query(m.Edit).filter_by(tenant_id=tid, path="/api/config").count() == 1


def test_inbound_hook_rejects_an_unknown_token(client):
    seeded_demo(client)
    assert client.post("/api/v1/hooks/not-a-real-token", json={}).status_code == 404
