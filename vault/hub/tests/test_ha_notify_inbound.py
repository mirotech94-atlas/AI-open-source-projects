"""Home Assistant -> Vault -> every channel. An HA automation POSTs a
notify event to its inbound URL and Vault fans it out like its own alert
-- the leg the owner's own mobile app will ride on."""
from guardian_hub import models as m
from guardian_hub.db import SessionLocal
from guardian_hub.integrations import registry
from tests.test_seed_helpers import seeded_demo


def _ha_token(client):
    r = client.post("/api/v1/integrations", json={"kind": "homeassistant", "enabled": True,
                                                  "routes": {"CRIT": True, "WARN": True, "INFO": True},
                                                  "fields": {"mqtt_host": "192.168.1.50", "mqtt_password": "x"}})
    assert r.status_code == 200, r.text
    db = SessionLocal(); row = db.query(m.Integration).filter_by(kind="homeassistant").first(); tok = row.inbound_token; db.close()
    assert tok
    return tok


def test_a_notify_event_from_ha_is_fanned_out(client, monkeypatch):
    seeded_demo(client)
    sent = []
    monkeypatch.setattr(registry, "deliver_background", lambda tid, msg: sent.append(msg))
    tok = _ha_token(client)
    r = client.post(f"/inbound/homeassistant/{tok}",
                    json={"action": "notify", "title": "Front door", "message": "Front door opened at 02:14", "severity": "warn"})
    assert r.status_code == 200, r.text
    assert len(sent) == 1 and sent[0].title == "Front door" and sent[0].severity == "WARN" and sent[0].kind == "report"
    db = SessionLocal(); assert db.query(m.Audit).filter_by(action="ha.notify", target="Front door").count() == 1; db.close()


def test_notify_needs_a_message_and_a_valid_token(client, monkeypatch):
    seeded_demo(client)
    monkeypatch.setattr(registry, "deliver_background", lambda tid, msg: None)
    tok = _ha_token(client)
    assert client.post(f"/inbound/homeassistant/{tok}", json={"action": "notify", "title": "x"}).status_code == 422
    assert client.post("/inbound/homeassistant/not-a-token", json={"action": "notify", "message": "x"}).status_code in (401, 403, 404)
