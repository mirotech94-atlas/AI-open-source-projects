import datetime as dt
import hashlib
import hmac
import json

from tests.test_seed_helpers import seeded_demo
from guardian_hub.db import SessionLocal
from guardian_hub import models as m
from guardian_hub.integrations import registry

# Fix round 1, finding 5: PagerDuty and Telegram inbound now verify a
# provider-side signature as well as the URL token, and both secrets are
# required fields -- so every `configure` here supplies one and every
# inbound POST presents it.
PD_SIGNING_SECRET = "pd-signing-" + "s" * 20
TG_WEBHOOK_SECRET = "tg-webhook-" + "t" * 20


def configure(client, kind, fields, routes=None):
    r = client.post("/api/v1/integrations", json={"kind": kind, "enabled": True,
        "routes": routes or {"CRIT": True, "WARN": True, "INFO": False}, "fields": fields})
    assert r.status_code == 200, r.text
    return SessionLocal().query(m.Integration).filter_by(kind=kind).one()


def pd_inbound(client, row, payload, secret=PD_SIGNING_SECRET):
    """POST a PagerDuty webhook with a real `X-PagerDuty-Signature` over the
    exact bytes sent (HMAC-SHA256, `v1=<hex>`)."""
    raw = json.dumps(payload)
    sig = hmac.new(secret.encode(), raw.encode(), hashlib.sha256).hexdigest()
    return client.post(f"/inbound/pagerduty/{row.inbound_token}", content=raw,
                        headers={"Content-Type": "application/json",
                                 "X-PagerDuty-Signature": f"v1={sig}"})


def tg_inbound(client, row, payload, secret=TG_WEBHOOK_SECRET):
    return client.post(f"/inbound/telegram/{row.inbound_token}", json=payload,
                        headers={"X-Telegram-Bot-Api-Secret-Token": secret})


def sync_of(kind):
    """The provider's `config["_sync"]` bookkeeping (ruling b)."""
    row = SessionLocal().query(m.Integration).filter_by(kind=kind).one()
    return (row.config or {}).get("_sync") or {}


# --------------------------------------------------------------------------
# Brief's required tests (task-41-brief.md Step 1), verbatim.
# --------------------------------------------------------------------------

def test_all_five_are_registered_with_a_duty(client):
    for kind in ("discord", "pagerduty", "telegram", "jira", "homeassistant"):
        i = registry.REGISTRY[kind]
        assert i.title and i.help and i.fields and i.direction
        assert i.__class__.watchdog is not registry.Integration.watchdog, f"{kind} has no watchdog duty"


def test_pagerduty_dedups_on_the_alert_key_and_acks_inbound(client, monkeypatch):
    seeded_demo(client)
    row = configure(client, "pagerduty", {"routing_key": "R" * 32, "signing_secret": PD_SIGNING_SECRET})
    posts = []
    monkeypatch.setattr("guardian_hub.integrations.pagerduty._post", lambda url, body: posts.append(body))
    key = client.get("/api/v1/inbox?view=alerts").json()["rows"][0]["id"]
    db = SessionLocal(); t = db.query(m.Tenant).filter_by(slug="demo").one()
    registry.deliver(db, t, registry.Message(title="x", body="y", severity="CRIT", url="",
                                             actions=[], kind="alert", dedup_key=key))
    assert posts[0]["dedup_key"] == key and posts[0]["event_action"] == "trigger"
    r = pd_inbound(client, row, {"event": {"event_type": "incident.acknowledged",
                                           "data": {"dedup_key": key}}})
    assert r.status_code == 200
    assert db.query(m.Alert).filter_by(tenant_id=t.id, key=key).one().status == "acked"


def test_tickets_open_one_per_incident_and_stamp_the_alerts(client, monkeypatch):
    seeded_demo(client)
    configure(client, "jira", {"flavour": "jira", "base_url": "https://x.atlassian.net",
                               "email": "a@b.c", "token": "t", "project": "GRD"})
    created = []
    monkeypatch.setattr("guardian_hub.integrations.tickets._create",
                        lambda ctx, summary, body: created.append(summary) or f"GRD-{len(created)}")
    registry.watchdog_job()
    db = SessionLocal(); t = db.query(m.Tenant).filter_by(slug="demo").one()
    incidents = db.query(m.Incident).filter_by(tenant_id=t.id).all()
    assert len(created) == len([i for i in incidents if not i.closed])
    stamped = db.query(m.Alert).filter(m.Alert.tenant_id == t.id, m.Alert.ticket_key != "").count()
    assert stamped >= len(created)
    registry.watchdog_job()
    assert len(created) == len([i for i in incidents if not i.closed])   # no duplicates


def test_inbound_token_is_required_and_rate_limited(client):
    seeded_demo(client)
    configure(client, "telegram", {"bot_token": "123:abc", "webhook_secret": TG_WEBHOOK_SECRET})
    assert client.post("/inbound/telegram/wrong-token", json={}).status_code == 404


# --------------------------------------------------------------------------
# Additional coverage beyond the brief's required tests.
# --------------------------------------------------------------------------

def test_tickets_open_one_per_incident_real_incident_and_reconciles_fix_and_close(client, monkeypatch):
    """The brief's own required test exercises the reconciliation loop
    against `seed_demo`'s data, which carries zero `Incident` rows (nothing
    in `seed.py` synthesizes one) -- so that test's assertions hold
    trivially at zero. This test inserts a real open incident (with a
    matching alert) plus a Fix and a closed incident, and checks the create
    -> stamp -> comment -> close pipeline actually fires."""
    seeded_demo(client)
    configure(client, "jira", {"flavour": "jira", "base_url": "https://x.atlassian.net",
                               "email": "a@b.c", "token": "t", "project": "GRD"})
    created, commented, closed = [], [], []
    counter = {"n": 0}

    def _create(ctx, summary, body):
        counter["n"] += 1
        created.append(summary)
        return f"GRD-{counter['n']}"

    monkeypatch.setattr("guardian_hub.integrations.tickets._create", _create)
    monkeypatch.setattr("guardian_hub.integrations.tickets._comment",
                        lambda ctx, key, body: commented.append((key, body)))
    monkeypatch.setattr("guardian_hub.integrations.tickets._close",
                        lambda ctx, key: closed.append(key))

    db = SessionLocal(); t = db.query(m.Tenant).filter_by(slug="demo").one()
    # seed_demo already carries 3 real open Incidents (state.json ids
    # 101/102/103, entity+alerts populated in last.json's fixture) -- this
    # test adds one more, with its own Fix and a second, already-closed
    # incident, to check the create -> stamp -> comment -> close pipeline
    # end to end without having to hardcode the seeded incidents' own count.
    ts = "2026-09-10T09:00:00"
    db.add(m.Alert(tenant_id=t.id, key="sec:attacker:demo:1.2.3.4", sev="CRIT", msg="attacker",
                    first_seen=ts, last_seen=ts, status="open"))
    db.add(m.Incident(tenant_id=t.id, iid="inc-open-1", entity="router1", sev="CRIT", story="Attacker seen",
                       actions=["block_ip"], alert_keys=["sec:attacker:demo:1.2.3.4"], opened=ts, closed=None))
    db.add(m.Incident(tenant_id=t.id, iid="inc-closed-1", entity="router1", sev="WARN", story="Old issue",
                       actions=[], alert_keys=[], opened=ts, closed="2026-09-10T10:00:00"))
    db.add(m.Fix(tenant_id=t.id, ts="2026-09-10T09:30:00", host="router1", action="block_ip",
                  label="Blocked attacker IP", by="auto", ok=True))
    db.commit()

    open_incidents_before = db.query(m.Incident).filter_by(tenant_id=t.id, closed=None).count()
    registry.watchdog_job()

    assert "[CRIT] router1" in created
    assert len(created) == open_incidents_before   # one ticket per open incident, no more
    a = db.query(m.Alert).filter_by(tenant_id=t.id, key="sec:attacker:demo:1.2.3.4").one()
    row = db.query(m.Integration).filter_by(tenant_id=t.id, kind="jira").one()
    my_key = ((row.config or {}).get("_sync") or {}).get("ticket_by_incident", {}).get("inc-open-1")
    assert my_key and a.ticket_key == my_key
    assert any(c[0] == my_key for c in commented)

    # The already-closed incident (inc-closed-1) has no alert_keys, so it
    # never got a ticket and is never asked to close.
    assert sync_of("jira").get("ticket_by_incident", {}).get("inc-closed-1") is None
    assert closed == []
    assert sync_of("jira").get("commented_fix_ids")

    # Second tick: no duplicate ticket for inc-open-1, no duplicate comment
    # for the same Fix, but the now-closed inc-open-1's ticket does get
    # closed exactly once.
    created.clear(); commented.clear()
    inc = db.query(m.Incident).filter_by(tenant_id=t.id, iid="inc-open-1").one()
    inc.closed = "2026-09-10T11:00:00"
    db.commit()
    registry.watchdog_job()
    assert "[CRIT] router1" not in created
    assert commented == []
    assert closed == [my_key]
    registry.watchdog_job()
    assert closed == [my_key]   # still no duplicate close


def test_pagerduty_owns_escalation_suppresses_ntfy(client, monkeypatch):
    seeded_demo(client)
    configure(client, "pagerduty", {"routing_key": "R" * 32, "signing_secret": PD_SIGNING_SECRET})
    db = SessionLocal(); t = db.query(m.Tenant).filter_by(slug="demo").one()
    ts = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=1)).isoformat(timespec="seconds")
    db.add(m.Alert(tenant_id=t.id, key="test:still-open", sev="CRIT", msg="still open",
                    first_seen=ts, last_seen=ts, status="open"))
    db.commit()

    posted = []
    monkeypatch.setattr("guardian_hub.integrations.ntfy._post",
                        lambda topic, msg, priority: posted.append(priority) or registry.SendResult(True, ""))
    ctx = registry.SendContext(tenant=t, secret=t.ntfy_topic, config={}, db=db, base_url="http://x")
    registry.REGISTRY["ntfy"].watchdog(ctx)
    assert posted == []   # PagerDuty owns escalation for this tenant now

    db.query(m.Integration).filter_by(tenant_id=t.id, kind="pagerduty").delete()
    db.commit()
    registry.REGISTRY["ntfy"].watchdog(ctx)
    assert posted == ["urgent"]


def test_telegram_pairing_binds_chat_id_and_gates_other_commands(client, monkeypatch):
    seeded_demo(client)
    row0 = configure(client, "telegram", {"bot_token": "123:abc", "webhook_secret": TG_WEBHOOK_SECRET})
    sent = []
    monkeypatch.setattr("guardian_hub.integrations.telegram._post",
                        lambda url, body: sent.append(body) or registry.SendResult(True, ""))

    code = client.post("/api/v1/integrations/telegram/pair").json()["code"]
    row = SessionLocal().query(m.Integration).filter_by(kind="telegram").one()

    # A stranger chat can pair with the right code...
    r = tg_inbound(client, row, {"message": {"text": f"/start {code}", "chat": {"id": 555}}})
    assert r.status_code == 200
    assert sync_of("telegram").get("chat_id") == 555

    # ...but a second, different chat sending /status gets no reply routed
    # to it (only the paired chat is honoured).
    sent.clear()
    tg_inbound(client, row, {"message": {"text": "/status", "chat": {"id": 999}}})
    assert sent == []

    tg_inbound(client, row, {"message": {"text": "/status", "chat": {"id": 555}}})
    assert len(sent) == 1 and sent[0]["chat_id"] == 555


def test_telegram_send_renders_actions_and_drops_fix_now(client, monkeypatch):
    seeded_demo(client)
    configure(client, "telegram", {"bot_token": "123:abc", "webhook_secret": TG_WEBHOOK_SECRET})
    pair_db = SessionLocal()
    pair_row = pair_db.query(m.Integration).filter_by(kind="telegram").one()
    pair_row.config = {**pair_row.config, "_sync": {"chat_id": 555}}
    pair_db.commit()
    sent = []
    monkeypatch.setattr("guardian_hub.integrations.telegram._post",
                        lambda url, body: sent.append(body) or registry.SendResult(True, ""))
    db = SessionLocal(); t = db.query(m.Tenant).filter_by(slug="demo").one()
    row = db.query(m.Integration).filter_by(kind="telegram").one()
    integ = registry.REGISTRY["telegram"]
    from guardian_hub import crypto
    ctx = registry.SendContext(tenant=t, secret=crypto.decrypt(row.secret), config=row.config, db=db, base_url="http://x")
    integ.send(ctx, registry.Message(title="CRIT", body="attacker", severity="CRIT", kind="alert",
                                      actions=[("Trust", "http://x/act/tok/trust"), ("Fix now", "http://x/act/tok/fix"),
                                               ("Ack", "http://x/act/tok/ack")]))
    assert sent
    labels = [b[0]["text"] for b in sent[0]["reply_markup"]["inline_keyboard"]]
    assert labels == ["Trust", "Ack"]


def test_discord_status_command_requires_a_valid_signature(client):
    seeded_demo(client)
    row = configure(client, "discord", {"webhook_url": "https://discord.com/api/webhooks/1/abc",
                                        "public_key": "deadbeef" * 8})
    r = client.post(f"/inbound/discord/{row.inbound_token}", json={"type": 2})
    assert r.status_code == 401


def test_discord_ping_with_a_real_keypair_gets_a_pong(client):
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    seeded_demo(client)
    priv = Ed25519PrivateKey.generate()
    pub_hex = priv.public_key().public_bytes_raw().hex() if hasattr(priv.public_key(), "public_bytes_raw") else None
    if pub_hex is None:
        from cryptography.hazmat.primitives import serialization
        pub_hex = priv.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw).hex()
    row = configure(client, "discord", {"webhook_url": "https://discord.com/api/webhooks/1/abc",
                                        "public_key": pub_hex})
    body = '{"type": 1}'
    # Finding 10: the signature check now also requires a fresh timestamp.
    timestamp = str(int(dt.datetime.now(dt.timezone.utc).timestamp()))
    sig = priv.sign((timestamp + body).encode()).hex()
    r = client.post(f"/inbound/discord/{row.inbound_token}", content=body,
                    headers={"Content-Type": "application/json",
                             "X-Signature-Ed25519": sig, "X-Signature-Timestamp": timestamp})
    assert r.status_code == 200
    assert r.json() == {"type": 1}


def test_homeassistant_watchdog_publishes_discovery_and_device_binary_sensors(client, monkeypatch):
    seeded_demo(client)
    configure(client, "homeassistant", {"mqtt_host": "broker.local", "mqtt_password": "secret"})
    published = []
    monkeypatch.setattr("guardian_hub.integrations.homeassistant._mqtt_publish_all",
                        lambda host, port, user, password, client_id, messages, ca_pem=None: published.extend(messages) or registry.SendResult(True, ""))
    registry.watchdog_job()
    topics = [t for t, _, _ in published]
    assert any("security_score" in t for t in topics)
    assert any("alerts_crit" in t for t in topics)
    assert any(t.startswith("homeassistant/binary_sensor/") and "device_" in t for t in topics)


def test_homeassistant_inbound_quarantines_device_and_acks_alert(client):
    seeded_demo(client)
    row = configure(client, "homeassistant", {"mqtt_host": "broker.local", "mqtt_password": "secret"})
    db = SessionLocal(); t = db.query(m.Tenant).filter_by(slug="demo").one()
    ts = "2026-09-10T09:00:00"
    db.add(m.Device(tenant_id=t.id, mac="aa:bb:cc:dd:ee:ff", ip="10.0.0.5", presence="ONLINE"))
    db.add(m.Alert(tenant_id=t.id, key="ha:test:key", sev="WARN", msg="test", first_seen=ts, last_seen=ts, status="open"))
    db.commit()

    r = client.post(f"/inbound/homeassistant/{row.inbound_token}",
                    json={"action": "quarantine_device", "mac": "aa:bb:cc:dd:ee:ff"})
    assert r.status_code == 200
    db2 = SessionLocal()
    assert db2.query(m.Device).filter_by(tenant_id=t.id, mac="aa:bb:cc:dd:ee:ff").one().quarantined is True
    assert db2.query(m.Audit).filter_by(action="device.update").filter(m.Audit.user == f"ha:{t.slug}").count() == 1

    r2 = client.post(f"/inbound/homeassistant/{row.inbound_token}",
                     json={"action": "ack_alert", "key": "ha:test:key"})
    assert r2.status_code == 200
    assert SessionLocal().query(m.Alert).filter_by(tenant_id=t.id, key="ha:test:key").one().status == "acked"


def test_homeassistant_presence_read_feeds_device_extra_and_is_preferred(client, monkeypatch):
    seeded_demo(client)
    configure(client, "homeassistant", {"mqtt_host": "broker.local", "mqtt_password": "secret",
                                        "ha_base_url": "http://ha.local:8123", "ha_token": "tok"})
    db = SessionLocal(); t = db.query(m.Tenant).filter_by(slug="demo").one()
    db.add(m.Device(tenant_id=t.id, mac="11:22:33:44:55:66", ip="10.0.0.9", presence="ONLINE"))
    db.commit()

    monkeypatch.setattr("guardian_hub.integrations.homeassistant._fetch_ha_states",
                        lambda base, token: [{"entity_id": "device_tracker.phone",
                                              "state": "home", "attributes": {"mac": "11:22:33:44:55:66"}}])
    monkeypatch.setattr("guardian_hub.integrations.homeassistant._mqtt_publish_all",
                        lambda *a, **k: registry.SendResult(True, ""))

    registry.watchdog_job()
    d = SessionLocal().query(m.Device).filter_by(tenant_id=t.id, mac="11:22:33:44:55:66").one()
    assert d.extra.get("ha_presence")
    assert d.extra["ha_presence"][-1]["home"] is True

    from guardian_hub.payloads import presence as presence_payloads
    prof = presence_payloads.profile(SessionLocal(), t.id, "11:22:33:44:55:66", days=7)
    now_hour = dt.datetime.now(dt.timezone.utc).hour
    assert prof.hours[now_hour] == 1.0
