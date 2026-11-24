"""Acknowledge from Slack: stop telling him until it changes.

the owner, 2026-09-24: "there should be an acknowledge button in Slack when they
send messages so they don't send the same one again." A known CloudCo balance
paged him daily and re-filed a tracking-issue card every time the last expired.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from atlas.kernel import acks
from atlas.kernel.contracts import Health


def _finding(check, health):
    return SimpleNamespace(check=check,
                           observation=SimpleNamespace(health=health))


@pytest.fixture
def db(monkeypatch):
    """acks table + latest health, in memory."""
    store = {"acks": [], "health": {}, "deleted": []}

    async def fetch_all(sql, *a):
        return list(store["acks"])

    async def latest(key):
        return store["health"].get(key)

    async def execute_sql(sql, *a):
        if sql.strip().startswith("DELETE"):
            store["deleted"].append(a[0])
            store["acks"] = [x for x in store["acks"] if x["key"] != a[0]]
    monkeypatch.setattr(acks, "fetch_all", fetch_all)
    monkeypatch.setattr(acks, "latest_health", latest)
    monkeypatch.setattr(acks, "execute_sql", execute_sql)
    return store


async def test_an_acknowledged_check_is_muted(db):
    db["acks"] = [{"key": "cloudco.balance", "health": "failing",
                   "until": datetime.now(UTC) + timedelta(days=3)}]
    db["health"]["cloudco.balance"] = "failing"
    kept = await acks.filter_acked([_finding("cloudco.balance", Health.FAILING),
                                    _finding("guardian.site", Health.DEGRADED)])
    assert [f.check for f in kept] == ["guardian.site"]


async def test_getting_worse_is_news_and_speaks_again(db):
    db["acks"] = [{"key": "gitlab.pipelines", "health": "degraded",
                   "until": datetime.now(UTC) + timedelta(days=3)}]
    db["health"]["gitlab.pipelines"] = "failing"
    kept = await acks.filter_acked([_finding("gitlab.pipelines", Health.FAILING)])
    assert len(kept) == 1
    assert db["deleted"] == ["gitlab.pipelines"]


async def test_recovery_retires_the_ack(db):
    db["acks"] = [{"key": "cloudco.balance", "health": "failing",
                   "until": datetime.now(UTC) + timedelta(days=3)}]
    db["health"]["cloudco.balance"] = "ok"
    await acks.filter_acked([])
    assert db["deleted"] == ["cloudco.balance"]


async def test_an_expired_ack_speaks_again(db):
    db["acks"] = [{"key": "cloudco.balance", "health": "failing",
                   "until": datetime.now(UTC) - timedelta(minutes=1)}]
    db["health"]["cloudco.balance"] = "failing"
    kept = await acks.filter_acked([_finding("cloudco.balance", Health.FAILING)])
    assert len(kept) == 1


async def test_unreadable_acks_never_swallow_an_alert(monkeypatch):
    async def broken(*a, **k):
        raise ConnectionError("db down")
    monkeypatch.setattr(acks, "fetch_all", broken)
    f = [_finding("cloudco.balance", Health.FAILING)]
    assert await acks.filter_acked(f) == f


def test_the_page_carries_an_acknowledge_button_and_the_cycle_filters():
    import inspect

    from atlas.kernel import scheduler
    assert 'Button("Acknowledge"' in inspect.getsource(scheduler.page)
    assert "filter_acked" in inspect.getsource(scheduler._run_checks)


async def test_a_critical_page_still_rings_and_the_call_never_offers_acknowledge(
        monkeypatch, memory_backend):
    """Every page now carries `ack:<check>`. The phone cannot render a
    button, so the call must still be placed, say the page, and never hand
    the Acknowledge value to anything that decides; Slack gets the button."""
    from atlas.channels import twilio as twilio_channel
    from atlas.kernel import comms, scheduler
    from atlas.kernel.contracts import Severity

    for key, value in (("TWILIO_ACCOUNT_SID", "AC1"), ("TWILIO_AUTH_TOKEN", "t"),
                       ("TWILIO_FROM", "+1"), ("TWILIO_TO", "+2"),
                       ("ATLAS_HOST", "atlas.example.com")):
        monkeypatch.setenv(key, value)
    rung: dict = {}

    class FakeResponse:
        def raise_for_status(self): ...
        def json(self): return {"sid": "CA1"}

    class FakeClient:
        def __init__(self, **kw): ...
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False

        async def post(self, url, **kw):
            rung["url"] = kw["data"]["Url"]
            return FakeResponse()

    async def home():
        return {"home": True, "peers_seen": [], "read_at": "now", "reason": None}

    monkeypatch.setattr(twilio_channel.httpx, "AsyncClient", FakeClient)
    monkeypatch.setattr(comms, "presence", home)

    class Slack:
        name, rich = "slack", True

        def __init__(self):
            self.got: list = []

        async def available(self):
            return True

        async def send(self, msg):
            self.got.append(msg)

    slack = Slack()
    monkeypatch.setattr(comms, "CHANNELS", {
        "voice": twilio_channel.VoiceChannel(), "slack": slack})
    monkeypatch.setitem(comms.ROUTING, Severity.CRITICAL, ("voice", "slack"))

    finding = SimpleNamespace(
        check="matrix_rooms.urgent", title="Urgent room unanswered",
        detail="3 messages waiting 40 minutes", severity=Severity.CRITICAL,
        observation=SimpleNamespace(health=Health.FAILING))
    assert await scheduler.page(finding) is True

    from urllib.parse import unquote
    assert "Urgent room unanswered" in unquote(rung["url"])
    assert "ack" not in rung["url"] and "pid=" not in rung["url"]
    assert "Acknowledge" not in unquote(rung["url"])
    assert [b.value for b in slack.got[0].buttons] == ["ack:matrix_rooms.urgent"]
