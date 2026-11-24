"""T8: on the Mac every inbound webhook is off, because no secret is set.

No code change backs this: each route already refuses when its secret (or
ATLAS_HOST, for Twilio's signed URL) is missing. These tests pin that, so a
later edit cannot quietly turn an unsigned webhook into an open door.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

_SECRETS = ("MAPFRONT_STRIPE_WEBHOOK_SECRET", "ACADEMY_STRIPE_WEBHOOK_SECRET",
            "ATLAS_HOST", "TRADINGVIEW_WEBHOOK_SECRET", "TRADINGVIEW_ALLOWED_IPS",
            "SLACK_SIGNING_SECRET", "ATLAS_ALLOWED_HOSTS")


@pytest.fixture
def client(monkeypatch, memory_backend):
    for name in _SECRETS:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("MESH_ALLOWED_CIDRS", "0.0.0.0/0")
    monkeypatch.setenv("SLACK_APPROVER_IDS", "UMIRO")
    monkeypatch.delenv("SLACK_APP_TOKEN", raising=False)

    from atlas.api.main import app

    async def no_boot() -> None:
        return None

    monkeypatch.setattr("atlas.api.main.boot", no_boot)
    monkeypatch.setattr("atlas.api.main.scheduler.loop", no_boot)
    with TestClient(app, client=("127.0.0.1", 51000)) as c:
        yield c


@pytest.mark.parametrize("path", ["/webhooks/stripe-mapfront",
                                  "/webhooks/stripe-academy"])
def test_stripe_webhooks_answer_503_without_a_secret(client, path):
    r = client.post(path, content=b"{}",
                    headers={"Stripe-Signature": "t=1,v1=00"})
    assert r.status_code == 503


@pytest.mark.parametrize("path", ["/webhooks/twilio/sms",
                                  "/webhooks/twilio/voice"])
def test_twilio_refuses_with_atlas_host_unset(client, path):
    r = client.post(path, data={"From": "+15550000000", "Body": "status"},
                    headers={"X-Twilio-Signature": "x"})
    assert r.status_code == 503


def test_tradingview_refuses_without_its_secret(client):
    r = client.post("/hooks/tradingview", json={"secret": "", "x": 1})
    assert r.status_code in (403, 503)


@pytest.mark.parametrize("path", ["/webhooks/slack", "/webhooks/slack/command",
                                  "/webhooks/slack/events"])
def test_slack_http_refuses_without_a_signing_secret(client, path):
    r = client.post(path, content=b"{}",
                    headers={"X-Slack-Request-Timestamp": "1",
                             "X-Slack-Signature": "v0=00"})
    assert r.status_code == 401
