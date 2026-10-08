"""Task 41 fix round 1: one test per review finding.

`test_integrations_fix1.py` is Task 40's fix round; this file is Task 41's.
Findings are numbered as in `task-41-findings.md`.
"""

import datetime as dt
import hashlib
import hmac
import json

import pytest

from tests.test_seed_helpers import seeded_demo
from tests.test_integrations_new import (PD_SIGNING_SECRET, TG_WEBHOOK_SECRET, configure,
                                          pd_inbound, sync_of, tg_inbound)
from guardian_hub.db import SessionLocal
from guardian_hub import crypto, models as m, ratelimit
from guardian_hub.integrations import registry, urlpolicy


def _row(kind):
    return SessionLocal().query(m.Integration).filter_by(kind=kind).one()


def _card(client, kind):
    cards = client.get("/api/v1/integrations").json()["integrations"]
    return next(c for c in cards if c["kind"] == kind)


HA_FIELDS = {"mqtt_host": "broker.local", "mqtt_password": "mqtt-pw",
             "ha_base_url": "http://ha.local:8123", "ha_token": "ha-long-lived-" + "z" * 30}


# --------------------------------------------------------------------------
# Finding 1 / ruling (a): every kind="secret" field is encrypted and masked.
# --------------------------------------------------------------------------

def test_ha_token_is_encrypted_at_rest_and_never_echoed_back(client):
    seeded_demo(client)
    configure(client, "homeassistant", HA_FIELDS)
    row = _row("homeassistant")

    # Not in the plaintext config column, and not anywhere in the row's raw
    # JSON either -- the whole point of finding 1.
    assert "ha_token" not in (row.config or {})
    assert HA_FIELDS["ha_token"] not in json.dumps(row.config or {})
    assert row.secrets_enc and HA_FIELDS["ha_token"] not in row.secrets_enc
    assert crypto.decrypt_json(row.secrets_enc)["ha_token"] == HA_FIELDS["ha_token"]

    # And the card masks it the way it masks fields[0].
    card = _card(client, "homeassistant")
    values = {f["name"]: f["value"] for f in card["fields"]}
    assert HA_FIELDS["ha_token"] not in json.dumps(card)
    assert values["ha_token"] and values["ha_token"] != HA_FIELDS["ha_token"]
    assert values["mqtt_password"] != "mqtt-pw"


def test_every_secret_field_reaches_the_provider_through_ctx_secrets(client):
    seeded_demo(client)
    configure(client, "homeassistant", HA_FIELDS)
    row = _row("homeassistant")
    integ = registry.REGISTRY["homeassistant"]
    secrets = registry.row_secrets(integ, row, crypto.decrypt(row.secret))
    assert secrets["mqtt_password"] == "mqtt-pw"          # fields[0], under its own name
    assert secrets["ha_token"] == HA_FIELDS["ha_token"]   # out of the encrypted blob


def test_a_required_secret_field_cannot_be_saved_empty(client):
    seeded_demo(client)
    r = client.post("/api/v1/integrations", json={"kind": "pagerduty", "enabled": True,
        "routes": {"CRIT": True}, "fields": {"routing_key": "R" * 32}})
    assert r.status_code == 422 and "signing secret" in r.text.lower()


def test_blank_secret_on_edit_keeps_the_stored_one(client):
    seeded_demo(client)
    configure(client, "homeassistant", HA_FIELDS)
    configure(client, "homeassistant", {**HA_FIELDS, "ha_token": "", "mqtt_password": ""})
    row = _row("homeassistant")
    assert crypto.decrypt_json(row.secrets_enc)["ha_token"] == HA_FIELDS["ha_token"]
    assert crypto.decrypt(row.secret) == "mqtt-pw"


def test_ha_presence_read_uses_the_encrypted_token(client, monkeypatch):
    seeded_demo(client)
    configure(client, "homeassistant", HA_FIELDS)
    seen = []
    monkeypatch.setattr("guardian_hub.integrations.homeassistant._fetch_ha_states",
                        lambda base, token: seen.append((base, token)) or [])
    monkeypatch.setattr("guardian_hub.integrations.homeassistant._mqtt_publish_all",
                        lambda *a, **k: registry.SendResult(True, ""))
    registry.watchdog_job()
    assert seen and seen[0][1] == HA_FIELDS["ha_token"]


# --------------------------------------------------------------------------
# Finding 2 / ruling (c): ingest reads the sensor's real incident shape.
# --------------------------------------------------------------------------

def _payload(incidents, include_key=True):
    last = {"ts": "2026-09-11T09:00:00-04:00"}
    if include_key:
        last["incidents"] = incidents
    return {"last": last, "state": {}}


def _ingest(payload):
    from guardian_hub import ingest
    db = SessionLocal()
    t = db.query(m.Tenant).filter_by(slug="demo").one()
    ingest.store_scan(db, t, None, payload, deliver=False)
    db.commit()
    return db, t


# A host of its own, deliberately not one the seeded fixture already has an
# incident for: `commented_fix_ids` is a per-ROW set, so a seeded incident on
# the same entity would claim this test's Fix comment first.
REAL_INCIDENT = {
    "id": 901, "entity": "host:edge1", "title": "Brute-force pressure on edge1",
    "severity": "CRIT", "confidence": 90, "alerts": ["sec:attacker:edge1:9.9.9.9"],
    "started": "2026-09-10T06:00:00-04:00",
    "story": [{"ts": "2026-09-10T06:00:00-04:00", "text": "362 failed SSH logins", "sev": "CRIT"}],
    "actions": [{"key": "sec:attacker:demo", "title": "Block the IP", "priority": "High"}],
    "state": "open",
}


def test_ingest_reads_severity_and_started_from_the_real_emitter_shape(client):
    seeded_demo(client)
    db, t = _ingest(_payload([REAL_INCIDENT]))
    inc = db.query(m.Incident).filter_by(tenant_id=t.id, iid="901").one()
    # `severity`, not `sev`; `started`, not `opened` -- neither key the old
    # reader looked for exists in a real payload.
    assert inc.sev == "CRIT"
    assert inc.opened == REAL_INCIDENT["started"]
    assert inc.closed is None
    # story is a timeline list, actions are recommendation dicts.
    assert "362 failed SSH logins" in inc.story
    assert inc.actions and isinstance(inc.actions[0], dict)


def test_ingest_closes_an_incident_that_leaves_the_live_list(client):
    seeded_demo(client)
    _ingest(_payload([REAL_INCIDENT]))
    db, t = _ingest(_payload([]))          # key present, id gone -> closed
    inc = db.query(m.Incident).filter_by(tenant_id=t.id, iid="901").one()
    assert inc.closed
    # opened is NOT rewritten to the new ingest ts.
    assert inc.opened == REAL_INCIDENT["started"]

    db, t = _ingest(_payload([REAL_INCIDENT]))   # back on the list -> reopened
    assert db.query(m.Incident).filter_by(tenant_id=t.id, iid="901").one().closed is None


def test_a_payload_with_no_incidents_key_closes_nothing(client):
    seeded_demo(client)
    _ingest(_payload([REAL_INCIDENT]))
    db, t = _ingest(_payload([], include_key=False))
    assert db.query(m.Incident).filter_by(tenant_id=t.id, iid="901").one().closed is None


def test_ticket_lifecycle_runs_on_a_real_shaped_incident(client, monkeypatch):
    """The whole point of finding 2: create -> comment -> close has to fire
    on a payload the sensor actually produces, not only on a fixture written
    against ingest's old reader keys."""
    seeded_demo(client)
    configure(client, "jira", {"flavour": "jira", "base_url": "https://x.atlassian.net",
                               "email": "a@b.c", "token": "t", "project": "GRD"})
    created, commented, closed = [], [], []
    monkeypatch.setattr("guardian_hub.integrations.tickets._create",
                        lambda ctx, summary, body: created.append(summary) or f"GRD-{len(created)}")
    monkeypatch.setattr("guardian_hub.integrations.tickets._comment",
                        lambda ctx, key, body: commented.append((key, body)))
    monkeypatch.setattr("guardian_hub.integrations.tickets._close", lambda ctx, key: closed.append(key))

    db, t = _ingest(_payload([REAL_INCIDENT]))
    db.add(m.Alert(tenant_id=t.id, key="sec:attacker:edge1:9.9.9.9", sev="CRIT", msg="attacker",
                    first_seen=REAL_INCIDENT["started"], last_seen=REAL_INCIDENT["started"], status="open"))
    # `Fix.host` is the bare host; the incident entity is "host:edge1".
    db.add(m.Fix(tenant_id=t.id, ts="2026-09-10T07:00:00-04:00", host="edge1", action="block_ip",
                  label="Blocked attacker IP", by="auto", ok=True))
    db.commit()

    registry.watchdog_job()
    key = sync_of("jira")["ticket_by_incident"]["901"]
    assert "[CRIT] host:edge1" in created
    assert SessionLocal().query(m.Alert).filter_by(tenant_id=t.id,
                                                   key="sec:attacker:edge1:9.9.9.9").one().ticket_key == key
    assert any(k == key and "Blocked attacker IP" in body for k, body in commented)

    _ingest(_payload([]))                  # incident disappears -> closed
    registry.watchdog_job()
    assert closed == [key]
    registry.watchdog_job()
    assert closed == [key]                 # closed exactly once


# --------------------------------------------------------------------------
# Finding 3 / ruling (b): a re-save merges, and never touches _sync.
# --------------------------------------------------------------------------

def test_resaving_an_integration_keeps_its_sync_state(client, monkeypatch):
    seeded_demo(client)
    configure(client, "jira", {"flavour": "jira", "base_url": "https://x.atlassian.net",
                               "email": "a@b.c", "token": "t", "project": "GRD"})
    created = []
    monkeypatch.setattr("guardian_hub.integrations.tickets._create",
                        lambda ctx, summary, body: created.append(summary) or f"GRD-{len(created)}")
    _ingest(_payload([REAL_INCIDENT]))
    registry.watchdog_job()
    first = list(created)
    assert first

    # An admin re-saves the card (e.g. to change the project key). Before
    # the fix this wiped ticket_by_incident/ticket_by_rec, so the next tick
    # filed a duplicate ticket for every incident AND every recommendation.
    configure(client, "jira", {"flavour": "jira", "base_url": "https://x.atlassian.net",
                               "email": "a@b.c", "token": "", "project": "OPS"})
    row = _row("jira")
    assert row.config["project"] == "OPS"                 # the edit landed
    assert sync_of("jira")["ticket_by_incident"]["901"]   # ...and the map survived
    created.clear()
    registry.watchdog_job()
    assert created == []


def test_resaving_telegram_does_not_unpair_the_chat(client, monkeypatch):
    seeded_demo(client)
    row = configure(client, "telegram", {"bot_token": "123:abc", "webhook_secret": TG_WEBHOOK_SECRET})
    monkeypatch.setattr("guardian_hub.integrations.telegram._post",
                        lambda url, body: registry.SendResult(True, ""))
    code = client.post("/api/v1/integrations/telegram/pair").json()["code"]
    tg_inbound(client, row, {"message": {"text": f"/start {code}", "chat": {"id": 777}}})
    assert sync_of("telegram")["chat_id"] == 777

    configure(client, "telegram", {"bot_token": "", "webhook_secret": "", "digest_hour": "7"})
    assert sync_of("telegram")["chat_id"] == 777
    assert _row("telegram").config["digest_hour"] == "7"


def test_a_nested_sync_write_survives_the_session_round_trip(client):
    """Guards the bug class the shallow `dict(row.config)` snapshot would
    have reintroduced: `ctx.config["_sync"][k] = v` mutates a dict the
    snapshot shares, so a shallow compare finds them equal and drops it."""
    seeded_demo(client)
    configure(client, "telegram", {"bot_token": "123:abc", "webhook_secret": TG_WEBHOOK_SECRET})
    integ = registry.REGISTRY["telegram"]
    original = integ.__class__.watchdog

    def watchdog(self, ctx):
        registry.sync_state(ctx)["probe"] = "kept"
        return None

    integ.__class__.watchdog = watchdog
    try:
        registry.watchdog_job()
    finally:
        integ.__class__.watchdog = original
    assert sync_of("telegram")["probe"] == "kept"


# --------------------------------------------------------------------------
# Finding 4 / ruling (e): URL policy.
# --------------------------------------------------------------------------

@pytest.mark.parametrize("kind,fields,bad", [
    ("discord", {"webhook_url": "http://discord.com/api/webhooks/1/a"}, "https"),
    ("discord", {"webhook_url": "https://127.0.0.1/hook"}, "loopback"),
    ("jira", {"flavour": "jira", "email": "a@b.c", "token": "t", "project": "P",
              "base_url": "http://169.254.169.254/latest/meta-data/"}, "loopback"),
    ("jira", {"flavour": "jira", "email": "a@b.c", "token": "t", "project": "P",
              "base_url": "http://jira.example.com"}, "https"),
    ("homeassistant", {"mqtt_host": "broker.local", "mqtt_password": "p",
                       "ha_base_url": "http://ha.example.com:8123"}, "private"),
    ("homeassistant", {"mqtt_host": "127.0.0.1", "mqtt_password": "p"}, "loopback"),
    ("homeassistant", {"mqtt_host": "broker.example.com", "mqtt_password": "p"}, "private"),
])
def test_tenant_supplied_urls_are_policed(client, kind, fields, bad):
    seeded_demo(client)
    r = client.post("/api/v1/integrations", json={"kind": kind, "enabled": True,
                                                  "routes": {"CRIT": True}, "fields": fields})
    assert r.status_code == 422, r.text
    assert bad in r.text.lower()


@pytest.mark.parametrize("kind,fields", [
    ("discord", {"webhook_url": "https://discord.com/api/webhooks/1/a"}),
    ("jira", {"flavour": "jira", "email": "a@b.c", "token": "t", "project": "P",
              "base_url": "https://x.atlassian.net"}),
    # A home install really is plain http on a .local / RFC1918 host.
    ("homeassistant", {"mqtt_host": "192.168.1.10", "mqtt_password": "p",
                       "ha_base_url": "http://homeassistant.local:8123", "ha_token": "t"}),
])
def test_legitimate_urls_still_save(client, kind, fields):
    seeded_demo(client)
    r = client.post("/api/v1/integrations", json={"kind": kind, "enabled": True,
                                                  "routes": {"CRIT": True}, "fields": fields})
    assert r.status_code == 200, r.text


def test_url_policy_rejects_loopback_even_over_https():
    assert urlpolicy.check_url("https://localhost:8443/x", "X")
    assert urlpolicy.check_url("https://[::1]/x", "X")
    assert not urlpolicy.check_url("https://example.com/x", "X")


# --------------------------------------------------------------------------
# Finding 5: provider-side signatures on PagerDuty and Telegram inbound.
# --------------------------------------------------------------------------

def _open_alert(t_id, key="sig:test"):
    db = SessionLocal()
    ts = "2026-09-10T09:00:00"
    db.add(m.Alert(tenant_id=t_id, key=key, sev="CRIT", msg="x", first_seen=ts, last_seen=ts, status="open"))
    db.commit()
    return key


def test_pagerduty_inbound_requires_a_valid_signature(client):
    seeded_demo(client)
    row = configure(client, "pagerduty", {"routing_key": "R" * 32, "signing_secret": PD_SIGNING_SECRET})
    t = SessionLocal().query(m.Tenant).filter_by(slug="demo").one()
    key = _open_alert(t.id)
    payload = {"event": {"event_type": "incident.resolved", "data": {"dedup_key": key}}}

    # No signature header at all, and a wrong one, are both 401 -- and the
    # alert is untouched either way.
    assert client.post(f"/inbound/pagerduty/{row.inbound_token}", json=payload).status_code == 401
    assert pd_inbound(client, row, payload, secret="wrong-secret").status_code == 401
    assert SessionLocal().query(m.Alert).filter_by(tenant_id=t.id, key=key).one().status == "open"

    assert pd_inbound(client, row, payload).status_code == 200
    assert SessionLocal().query(m.Alert).filter_by(tenant_id=t.id, key=key).one().status == "resolved"


def test_pagerduty_accepts_a_rotating_multi_value_signature_header(client):
    seeded_demo(client)
    row = configure(client, "pagerduty", {"routing_key": "R" * 32, "signing_secret": PD_SIGNING_SECRET})
    raw = json.dumps({"event": {"event_type": "incident.acknowledged", "data": {"dedup_key": "nope"}}})
    good = hmac.new(PD_SIGNING_SECRET.encode(), raw.encode(), hashlib.sha256).hexdigest()
    r = client.post(f"/inbound/pagerduty/{row.inbound_token}", content=raw,
                     headers={"Content-Type": "application/json",
                              "X-PagerDuty-Signature": f"v1={'0' * 64},v1={good}"})
    assert r.status_code == 200


def test_telegram_inbound_requires_the_secret_token(client, monkeypatch):
    seeded_demo(client)
    row = configure(client, "telegram", {"bot_token": "123:abc", "webhook_secret": TG_WEBHOOK_SECRET})
    monkeypatch.setattr("guardian_hub.integrations.telegram._post",
                        lambda url, body: registry.SendResult(True, ""))
    code = client.post("/api/v1/integrations/telegram/pair").json()["code"]
    msg = {"message": {"text": f"/start {code}", "chat": {"id": 42}}}

    assert client.post(f"/inbound/telegram/{row.inbound_token}", json=msg).status_code == 401
    assert tg_inbound(client, row, msg, secret="nope").status_code == 401
    assert "chat_id" not in sync_of("telegram")
    assert tg_inbound(client, row, msg).status_code == 200
    assert sync_of("telegram")["chat_id"] == 42


# --------------------------------------------------------------------------
# Finding 6: inbound writes are audited with the caller's IP.
# --------------------------------------------------------------------------

def test_inbound_writes_are_audited_with_a_real_ip(client):
    seeded_demo(client)
    row = configure(client, "pagerduty", {"routing_key": "R" * 32, "signing_secret": PD_SIGNING_SECRET})
    t = SessionLocal().query(m.Tenant).filter_by(slug="demo").one()
    key = _open_alert(t.id, "ip:test")
    pd_inbound(client, row, {"event": {"event_type": "incident.acknowledged", "data": {"dedup_key": key}}})
    audit = (SessionLocal().query(m.Audit)
             .filter_by(tenant_id=t.id, action="alert.ack", user="pagerduty").one())
    assert audit.ip                      # was always "" before finding 6

    ha = configure(client, "homeassistant", {"mqtt_host": "broker.local", "mqtt_password": "p"})
    db = SessionLocal()
    db.add(m.Device(tenant_id=t.id, mac="aa:bb:cc:00:11:22", ip="10.0.0.7", presence="ONLINE"))
    db.commit()
    client.post(f"/inbound/homeassistant/{ha.inbound_token}",
                json={"action": "quarantine_device", "mac": "aa:bb:cc:00:11:22"})
    dev_audit = (SessionLocal().query(m.Audit)
                 .filter_by(tenant_id=t.id, action="device.update", user=f"ha:{t.slug}").one())
    assert dev_audit.ip


# --------------------------------------------------------------------------
# Finding 7: a non-ASCII token is a uniform 404, not a 500.
# --------------------------------------------------------------------------

def test_non_ascii_inbound_token_is_a_plain_404(client):
    seeded_demo(client)
    configure(client, "telegram", {"bot_token": "123:abc", "webhook_secret": TG_WEBHOOK_SECRET})
    bad = client.post("/inbound/telegram/tökén-ünïcode", json={})
    wrong = client.post("/inbound/telegram/0123456789abcdef0123456789abcdef", json={})
    assert bad.status_code == 404          # was a TypeError -> 500 from compare_digest
    assert bad.json() == wrong.json()      # indistinguishable from any other miss


# --------------------------------------------------------------------------
# Finding 8: digest_hour is a real, honoured form field.
# --------------------------------------------------------------------------

@pytest.mark.parametrize("kind,fields", [
    ("discord", {"webhook_url": "https://discord.com/api/webhooks/1/a"}),
    ("telegram", {"bot_token": "123:abc", "webhook_secret": TG_WEBHOOK_SECRET}),
])
def test_digest_hour_is_an_exposed_field(client, kind, fields):
    seeded_demo(client)
    configure(client, kind, {**fields, "digest_hour": "17"})
    card = _card(client, kind)
    values = {f["name"]: f["value"] for f in card["fields"]}
    assert values["digest_hour"] == "17"


@pytest.mark.parametrize("mod", ["discord", "telegram"])
@pytest.mark.parametrize("raw,expected", [("17", 17), ("0", 0), ("", 9), ("nine", 9), ("25", 9)])
def test_digest_hour_parses_safely(mod, raw, expected):
    import importlib
    provider = importlib.import_module(f"guardian_hub.integrations.{mod}")
    assert provider.digest_hour({"digest_hour": raw}) == expected


def test_digest_hour_actually_drives_the_digest(client, monkeypatch):
    seeded_demo(client)
    configure(client, "discord", {"webhook_url": "https://discord.com/api/webhooks/1/a",
                                  "digest_hour": str(dt.datetime.now(dt.timezone.utc).hour)},
              routes={"INFO": True})
    posts = []
    monkeypatch.setattr("guardian_hub.integrations.discord._post_json",
                        lambda url, body: posts.append(body) or registry.SendResult(True, ""))
    registry.watchdog_job()
    assert any("digest" in json.dumps(p).lower() for p in posts)


# --------------------------------------------------------------------------
# Finding 9: an MQTT failure records last_error and keeps the tick's config.
# --------------------------------------------------------------------------

def test_mqtt_failure_keeps_the_ticks_config_writes(client, monkeypatch):
    seeded_demo(client)
    configure(client, "homeassistant", HA_FIELDS)
    monkeypatch.setattr("guardian_hub.integrations.homeassistant._mqtt_publish_all",
                        lambda *a, **k: registry.SendResult(False, "broker unreachable"))
    integ = registry.REGISTRY["homeassistant"]
    original = integ.__class__.watchdog

    def watchdog(self, ctx):
        registry.sync_state(ctx)["recorded_before_mqtt"] = True
        return original(self, ctx)

    integ.__class__.watchdog = watchdog
    try:
        registry.watchdog_job()
    finally:
        integ.__class__.watchdog = original

    row = _row("homeassistant")
    assert "broker unreachable" in row.last_error         # reported, not raised
    assert sync_of("homeassistant")["recorded_before_mqtt"] is True


# --------------------------------------------------------------------------
# Finding 10: a captured Discord interaction cannot be replayed forever.
# --------------------------------------------------------------------------

def test_stale_discord_signature_timestamp_is_rejected(client):
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    seeded_demo(client)
    priv = Ed25519PrivateKey.generate()
    from cryptography.hazmat.primitives import serialization
    pub_hex = priv.public_key().public_bytes(serialization.Encoding.Raw,
                                              serialization.PublicFormat.Raw).hex()
    row = configure(client, "discord", {"webhook_url": "https://discord.com/api/webhooks/1/abc",
                                        "public_key": pub_hex})
    body = '{"type": 1}'
    stale = str(int(dt.datetime.now(dt.timezone.utc).timestamp()) - 3600)
    r = client.post(f"/inbound/discord/{row.inbound_token}", content=body,
                     headers={"Content-Type": "application/json",
                              "X-Signature-Ed25519": priv.sign((stale + body).encode()).hex(),
                              "X-Signature-Timestamp": stale})
    assert r.status_code == 401             # a genuine signature, but hours old
    assert "stale" in r.text.lower()


# --------------------------------------------------------------------------
# Finding 12: the rate-limiter's bucket dict is bounded.
# --------------------------------------------------------------------------

def test_ratelimit_buckets_are_capped(client):
    seeded_demo(client)
    ratelimit.reset()
    # Every prefix an unauthenticated prober can invent is its own bucket.
    for i in range(ratelimit._MAX_BUCKETS + 500):
        ratelimit.allow(f"inbound:{i:08x}", 60, 60)
    assert ratelimit.bucket_count() <= ratelimit._MAX_BUCKETS
    # Eviction never blocks a live caller.
    assert ratelimit.allow("inbound:legit001", 60, 60) is True
    ratelimit.reset()


# --------------------------------------------------------------------------
# Finding 1, the OTHER half: the 0006 data move. Every test above goes
# through `upsert_integration`, where the token was never plaintext to begin
# with -- but the reason 0006 carries a data step at all is an ALREADY
# DEPLOYED hub whose `config["ha_token"]` is sitting there in the clear. The
# suite builds its schema with `create_all`, so nothing else here ever runs a
# migration body; this drives the real alembic chain on its own temp file.
# --------------------------------------------------------------------------

def test_migration_0006_moves_a_plaintext_ha_token_into_the_encrypted_blob(tmp_path, monkeypatch):
    import pathlib
    from alembic import command
    from alembic.config import Config
    from sqlalchemy import create_engine, text

    from guardian_hub.config import settings

    db_path = tmp_path / "legacy.db"
    url = f"sqlite:///{db_path}"
    # `alembic/env.py` calls `settings.db_url()`, and `settings` is already
    # imported -- so point the attribute at the temp file rather than the
    # env var, which is only read when Settings() is first constructed.
    monkeypatch.setattr(settings, "database_url", url)
    ini = str(pathlib.Path(crypto.__file__).resolve().parent.parent / "alembic.ini")

    # A hub as it stood BEFORE this fix round: the token in plaintext config.
    command.upgrade(Config(ini), "0005_alert_escalated_at")
    engine = create_engine(url)
    token = "legacy-ha-token-" + "q" * 30
    with engine.begin() as conn:
        # `integration.tenant_id` is a plain String, not an enforced FK, so
        # the row under test needs no tenant of its own.
        conn.execute(text("""INSERT INTO integration (id, tenant_id, kind, secret, routes, enabled, name, config)
                             VALUES ('i1', 't1', 'homeassistant', :sec, '{}', 1, '', :cfg)"""),
                      {"sec": crypto.encrypt("mqtt-pw"),
                       "cfg": json.dumps({"mqtt_host": "broker.local", "ha_token": token})})

    command.upgrade(Config(ini), "0006_integration_secrets")

    with engine.begin() as conn:
        cfg_raw, blob = conn.execute(text("SELECT config, secrets_enc FROM integration WHERE id='i1'")).one()
    cfg = json.loads(cfg_raw) if isinstance(cfg_raw, str) else cfg_raw
    assert "ha_token" not in cfg                      # gone from the clear
    assert cfg["mqtt_host"] == "broker.local"         # ...and nothing else lost
    assert blob and token not in blob
    assert crypto.decrypt_json(blob)["ha_token"] == token


# --------------------------------------------------------------------------
# Fix round 2, finding 2: `_incident_body` rendered `inc.actions` dict
# entries with Python's raw repr (`{'priority': 'Low', 'text': ...}`) in
# every Jira/ServiceNow description instead of the action's text.
# --------------------------------------------------------------------------

def test_incident_body_renders_dict_action_text_not_python_repr():
    from guardian_hub.integrations.tickets import _incident_body

    inc = m.Incident(
        tenant_id="t1", iid="inc-42", entity="host:demo", sev="CRIT",
        story="Repeated auth failures on demo",
        actions=[
            {"priority": "Low", "text": "Block the source IP"},
            {"priority": "High", "key": "restart_service"},
            "Rotate the API key",
        ],
        alert_keys=[], opened="2026-09-10T10:00:00", closed=None,
    )

    body = _incident_body(inc)

    assert "Block the source IP" in body
    assert "restart_service" in body
    assert "Rotate the API key" in body
    assert "{'" not in body
    assert "Low" not in body  # the dict's other keys don't leak into the body
