"""Task 40 fix round 1: one test per finding in task-40-findings.md.

Finding 1 (Edit row for notify.local_only) is covered in
test_integrations_core.py, next to the existing "flips to local_only"
test it extends -- kept there instead of duplicated here.
"""

import datetime as dt
import time

from tests.test_integrations_core import enrol, ingest_payload_with_new_crit
from tests.test_seed_helpers import seeded_demo
from guardian_hub.db import SessionLocal
from guardian_hub import models as m, crypto
from guardian_hub.integrations import registry


# --------------------------------------------------------------------------
# Finding 2: delivery must not block the ingest request.
# --------------------------------------------------------------------------

def test_ingest_returns_fast_even_when_delivery_is_slow(client, monkeypatch):
    tid = seeded_demo(client)

    def slow_send(ctx, msg):
        time.sleep(2.0)
        return registry.SendResult(True, "")

    # No explicit ntfy row exists for the demo tenant -- seeded_demo only
    # adds a Slack row -- so a new CRIT still fires through ntfy's default
    # path (Tenant.ntfy_topic), the exact provider this patches.
    monkeypatch.setattr(registry.REGISTRY["ntfy"], "send", slow_send)

    payload = ingest_payload_with_new_crit("sec:attacker:demo:slow-delivery")
    headers = {"Authorization": f"Bearer {enrol(client, tid)}"}
    started = time.monotonic()
    r = client.post("/api/v1/sensor/ingest", json=payload, headers=headers)
    elapsed = time.monotonic() - started

    assert r.status_code == 200
    assert elapsed < 1.0, f"ingest waited on delivery: took {elapsed:.2f}s with a 2s slow send"


# --------------------------------------------------------------------------
# Finding 3: a DISABLED ntfy row must suppress the default ntfy fallback.
# --------------------------------------------------------------------------

def test_disabled_ntfy_row_suppresses_the_default_fallback(client, monkeypatch):
    seeded_demo(client)
    db = SessionLocal()
    t = db.query(m.Tenant).filter_by(slug="demo").one()
    db.add(m.Integration(tenant_id=t.id, kind="ntfy", secret=crypto.encrypt("some-topic"),
                          routes={"CRIT": True}, enabled=False))
    db.commit()

    sent = []
    for kind in ("ntfy", "slack"):
        monkeypatch.setattr(registry.REGISTRY[kind], "send",
                            lambda ctx, msg, k=kind: sent.append((k, msg.severity)) or registry.SendResult(True, ""))

    db2 = SessionLocal()
    t2 = db2.query(m.Tenant).filter_by(slug="demo").one()
    registry.deliver(db2, t2, registry.Message(title="t", body="b", severity="CRIT", url="", actions=[], kind="alert"))
    # ntfy is explicitly disabled -> no send from it, and no default
    # fallback either (only the enabled Slack row routes CRIT through).
    assert sent == [("slack", "CRIT")]


# --------------------------------------------------------------------------
# Finding 4: CRIT resend cadence is 6h, not every hourly tick.
# --------------------------------------------------------------------------

def test_ntfy_watchdog_resends_every_6h_not_every_tick(client, monkeypatch):
    seeded_demo(client)
    db = SessionLocal()
    t = db.query(m.Tenant).filter_by(slug="demo").one()
    ts = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=2)).isoformat(timespec="seconds")
    alert = m.Alert(tenant_id=t.id, key="test:resend-cadence", sev="CRIT", msg="still open",
                     first_seen=ts, last_seen=ts, status="open")
    db.add(alert)
    db.commit()

    posted = []
    monkeypatch.setattr("guardian_hub.integrations.ntfy._post",
                        lambda topic, msg, priority: posted.append(priority) or registry.SendResult(True, ""))
    ctx = registry.SendContext(tenant=t, secret=t.ntfy_topic, config={}, db=db, base_url="http://x")

    registry.REGISTRY["ntfy"].watchdog(ctx)
    assert posted == ["urgent"]   # first escalation, alert is already 2h old (> 30 min threshold)

    posted.clear()
    registry.REGISTRY["ntfy"].watchdog(ctx)
    assert posted == []           # immediately re-ticking must NOT resend within 6h

    posted.clear()
    alert.escalated_at = dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=6, minutes=1)
    db.commit()
    registry.REGISTRY["ntfy"].watchdog(ctx)
    assert posted == ["urgent"]   # 6h+ since the last resend -> resend again


# --------------------------------------------------------------------------
# Finding 5: maintenance-window suppression.
# --------------------------------------------------------------------------

def test_maintenance_window_suppresses_delivery_not_storage(client, monkeypatch):
    seeded_demo(client)
    db = SessionLocal()
    t = db.query(m.Tenant).filter_by(slug="demo").one()

    sent = []
    for kind in ("ntfy", "slack"):
        monkeypatch.setattr(registry.REGISTRY[kind], "send",
                            lambda ctx, msg, k=kind: sent.append(k) or registry.SendResult(True, ""))
    msg = registry.Message(title="t", body="b", severity="CRIT", url="", actions=[], kind="alert")

    # Absent -> not active, delivery proceeds normally (the demo tenant
    # carries both a real ntfy_topic -- the default fallback -- and the
    # seeded Slack row, both routing CRIT).
    assert registry.maintenance_active(t) is False
    registry.deliver(db, t, msg)
    # ntfy no longer fires from `Tenant.ntfy_topic` alone; only the slack row.
    assert set(sent) == {"slack"}

    # Future timestamp -> active, delivery is fully suppressed.
    sent.clear()
    future = (dt.datetime.now(dt.timezone.utc) + dt.timedelta(hours=1)).isoformat(timespec="seconds")
    t.settings = {**(t.settings or {}), "maintenance_until": future}
    db.commit()
    assert registry.maintenance_active(t) is True
    assert registry.deliver(db, t, msg) == []
    assert sent == []

    # Past timestamp -> lapsed, delivery resumes.
    sent.clear()
    past = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=1)).isoformat(timespec="seconds")
    t.settings = {**(t.settings or {}), "maintenance_until": past}
    db.commit()
    assert registry.maintenance_active(t) is False
    registry.deliver(db, t, msg)
    assert set(sent) == {"slack"}


# --------------------------------------------------------------------------
# Finding 6: empty primary secret rejected on create; kept on edit.
# --------------------------------------------------------------------------

def test_empty_primary_secret_rejected_on_create_and_kept_on_edit(client):
    seeded_demo(client)
    r = client.post("/api/v1/integrations", json={"kind": "teams", "enabled": True,
        "routes": {}, "fields": {"url": ""}})
    assert r.status_code == 422

    url = "https://outlook.office.com/webhook/real-one"
    r2 = client.post("/api/v1/integrations", json={"kind": "teams", "enabled": True,
        "routes": {"CRIT": True}, "fields": {"url": url}})
    assert r2.status_code == 200
    row = SessionLocal().query(m.Integration).filter_by(kind="teams").one()
    assert crypto.decrypt(row.secret) == url

    # Editing with the primary field left blank must keep the existing
    # secret, not overwrite it with an empty encrypted blob.
    r3 = client.post("/api/v1/integrations", json={"kind": "teams", "enabled": True,
        "routes": {"CRIT": True, "WARN": True}, "fields": {"url": ""}})
    assert r3.status_code == 200
    row2 = SessionLocal().query(m.Integration).filter_by(kind="teams").one()
    assert crypto.decrypt(row2.secret) == url
    assert row2.routes == {"CRIT": True, "WARN": True}


# --------------------------------------------------------------------------
# Finding 7: bool config fields coerced from form strings to real bools.
# --------------------------------------------------------------------------

def test_bool_config_field_is_coerced_not_stored_as_a_string(client):
    seeded_demo(client)
    r = client.post("/api/v1/integrations", json={"kind": "pagerduty", "enabled": True,
        "routes": {"CRIT": True}, "fields": {"routing_key": "R" * 32, "signing_secret": "S" * 24,
                                            "owns_escalation": "false"}})
    assert r.status_code == 200
    row = SessionLocal().query(m.Integration).filter_by(kind="pagerduty").one()
    assert row.config["owns_escalation"] is False   # a real bool, not the string "false"

    r2 = client.post("/api/v1/integrations", json={"kind": "pagerduty", "enabled": True,
        "routes": {"CRIT": True}, "fields": {"routing_key": "R" * 32, "owns_escalation": "true"}})
    assert r2.status_code == 200
    row2 = SessionLocal().query(m.Integration).filter_by(kind="pagerduty").one()
    assert row2.config["owns_escalation"] is True
