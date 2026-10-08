"""One message when no model answers (kernel/outage.py): once an hour, with
the reason in plain words, one line when a model answers again, and never a
second failure because Slack is down too."""
from __future__ import annotations

import asyncio

import pytest

from atlas.kernel import breaker, llm, outage
from atlas.kernel.contracts import Severity
from atlas.kernel.llm import Completion, Need


class _Clock:
    def __init__(self):
        self.t = 5000.0

    def __call__(self):
        return self.t


class _Http(Exception):
    def __init__(self, status):
        super().__init__(f"HTTP {status}")
        self.response = type("R", (), {"status_code": status, "headers": {}, "text": ""})()


class _Provider:
    def __init__(self, name, model, fail=None):
        self.name, self.model, self.fail = name, model, fail

    async def complete(self, system, messages, tools, max_tokens):
        if self.fail is not None:
            raise self.fail
        return Completion(text="hello", provider=self.name, model=self.model)


@pytest.fixture
def posted(monkeypatch):
    clock = _Clock()
    breaker.reset(clock)
    outage.reset(clock)
    sent: list = []

    async def notify(msg):
        sent.append(msg)
        return ["slack"]
    monkeypatch.setattr("atlas.kernel.comms.notify", notify)
    return sent, clock


async def _settle():
    for _ in range(3):
        await asyncio.sleep(0)


async def test_an_exhausted_chain_posts_one_high_message_with_the_reason(posted, monkeypatch):
    sent, _clock = posted
    monkeypatch.setitem(llm.ROUTES, Need.DRAFT, [
        _Provider("anthropic", "sonnet", _Http(402)), _Provider("groq", "fast", _Http(401))])
    with pytest.raises(RuntimeError, match="all providers failed"):
        await llm.think(Need.DRAFT, "s", [])
    await _settle()
    assert sent == [], "a blip is not an outage yet"
    _clock.t += outage.PERSIST_S + 1
    with pytest.raises(RuntimeError, match="all providers failed"):
        await llm.think(Need.DRAFT, "s", [])
    await _settle()
    [msg] = sent
    assert msg.title == outage.TITLE and msg.severity is Severity.HIGH
    assert msg.topic == "comms" and msg.incident_key == "llm.routes"
    assert "anthropic:sonnet: out of credit (HTTP 402)" in msg.body
    assert "groq:fast: key refused (HTTP 401)" in msg.body
    assert "topping it up is yours" in msg.body and "DRAFT" in msg.body
    assert outage.active()


async def test_it_is_said_once_an_hour_not_once_per_agent(posted, monkeypatch):
    sent, clock = posted
    monkeypatch.setitem(llm.ROUTES, Need.DRAFT, [_Provider("groq", "fast", _Http(500))])
    for _ in range(6):
        clock.t += outage.PERSIST_S
        with pytest.raises(RuntimeError):
            await llm.think(Need.DRAFT, "s", [])
    await _settle()
    assert len(sent) == 1
    clock.t += outage.GAP_S + 1
    with pytest.raises(RuntimeError):
        await llm.think(Need.DRAFT, "s", [])
    await _settle()
    assert len(sent) == 2


async def test_a_model_answering_again_is_said_once(posted, monkeypatch):
    sent, clock = posted
    p = _Provider("groq", "fast", _Http(500))
    monkeypatch.setitem(llm.ROUTES, Need.DRAFT, [p])
    with pytest.raises(RuntimeError):
        await llm.think(Need.DRAFT, "s", [])
    clock.t += outage.PERSIST_S + 1
    with pytest.raises(RuntimeError):
        await llm.think(Need.DRAFT, "s", [])
    p.fail = None
    clock.t += 700
    assert (await llm.think(Need.DRAFT, "s", [])).text == "hello"
    assert (await llm.think(Need.DRAFT, "s", [])).text == "hello"
    await _settle()
    assert [m.title for m in sent] == [outage.TITLE, outage.CLEARED]
    assert "groq:fast answered" in sent[1].body and not outage.active()


async def test_a_blip_is_neither_announced_nor_cleared(posted, monkeypatch):
    """2026-10-05: the free bench flapped 66 times in a day; each flap was
    two Slack messages. Exhausted and answering within PERSIST_S says nothing."""
    sent, clock = posted
    p = _Provider("groq", "fast", _Http(429))
    monkeypatch.setitem(llm.ROUTES, Need.DRAFT, [p])
    for _ in range(20):
        p.fail = _Http(429)
        with pytest.raises(RuntimeError):
            await llm.think(Need.DRAFT, "s", [])
        clock.t += 60
        p.fail = None
        assert (await llm.think(Need.DRAFT, "s", [])).text == "hello"
        clock.t += 60
    await _settle()
    assert sent == []


async def test_a_healthy_router_posts_nothing(posted, monkeypatch):
    sent, _ = posted
    monkeypatch.setitem(llm.ROUTES, Need.DRAFT, [_Provider("groq", "fast")])
    await llm.think(Need.DRAFT, "s", [])
    await _settle()
    assert sent == []


async def test_slack_being_down_does_not_change_what_think_raises(monkeypatch):
    async def down(msg):
        raise RuntimeError("slack down")
    monkeypatch.setattr("atlas.kernel.comms.notify", down)
    monkeypatch.setitem(llm.ROUTES, Need.DRAFT, [_Provider("groq", "fast", _Http(500))])
    with pytest.raises(RuntimeError, match="all providers failed"):
        await llm.think(Need.DRAFT, "s", [])
    await _settle()


def test_with_no_breaker_open_the_last_error_is_named():
    said = outage.describe({}, ValueError("connection reset"))
    assert "last error: ValueError: connection reset" in said and "402" not in said
