"""The operator's admin channel: platform events, one Slack webhook, never
in the request path's way."""
from guardian_hub import adminnotify, models as m
from guardian_hub.config import settings
from guardian_hub.db import SessionLocal
from tests.test_tenants import make_super


def _capture(monkeypatch):
    sent = []
    monkeypatch.setattr(settings, "admin_slack_webhook", "https://hooks.slack.com/services/T/B/x")
    monkeypatch.setattr(adminnotify, "_post", lambda url, payload: sent.append(payload))
    # run synchronously
    import threading
    monkeypatch.setattr(threading, "Thread", lambda target, args, daemon: type("T", (), {"start": lambda self: target(*args)})())
    return sent


def test_off_by_default_and_a_noop_when_unset(monkeypatch):
    monkeypatch.setattr(settings, "admin_slack_webhook", "")
    assert adminnotify.enabled() is False
    assert adminnotify.notify("lead", "x") is False


def test_a_non_slack_url_is_refused(monkeypatch):
    monkeypatch.setattr(settings, "admin_slack_webhook", "https://evil.example/hook")
    assert adminnotify.enabled() is False


def test_an_access_request_lands_in_the_admin_channel(client, monkeypatch):
    sent = _capture(monkeypatch)
    r = client.post("/api/v1/public/lead", json={"name": "Dana", "email": "Dana@Clinic.example", "company": "Clinic", "network_size": "20-50", "note": "call me", "website": "", "accept_terms": True})
    assert r.status_code == 200
    assert len(sent) == 1 and "Access request from Dana" in sent[0]["text"] and "dana@clinic.example" in sent[0]["text"]


def test_a_new_tenant_and_a_first_sign_in_are_announced(client, monkeypatch):
    sent = _capture(monkeypatch)
    make_super(client)
    assert client.post("/api/v1/settings/tenants", json={"name": "Northwind", "slug": "northwind"}).status_code == 200
    assert any("New tenant: Northwind" in p["text"] for p in sent)


def test_a_slack_outage_never_breaks_the_request(client, monkeypatch):
    monkeypatch.setattr(settings, "admin_slack_webhook", "https://hooks.slack.com/services/T/B/x")
    def boom(url, payload): raise RuntimeError("slack down")
    monkeypatch.setattr(adminnotify, "_post", boom)
    r = client.post("/api/v1/public/lead", json={"name": "A", "email": "a@b.example", "company": "", "network_size": "", "note": "", "website": "", "accept_terms": True})
    assert r.status_code == 200
