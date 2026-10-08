"""Per-deployment circuit breakers (kernel/breaker.py) and how `think()`
uses them: a dead provider is skipped rather than waited on, a named
Retry-After is honoured exactly, a spent balance opens at once, and a
breaker can never leave the router with nothing to ask."""
from __future__ import annotations

import pytest

from atlas.kernel import breaker, llm
from atlas.kernel.llm import Completion, Need

A, B = ("anthropic", "sonnet"), ("groq", "fast")


class _Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


class _Http(Exception):
    def __init__(self, status, headers=None):
        super().__init__(f"HTTP {status}")
        self.response = type("R", (), {"status_code": status, "headers": headers or {},
                                       "text": ""})()


@pytest.fixture
def clock():
    c = _Clock()
    breaker.reset(c)
    yield c
    breaker.reset()


def test_three_failures_open_for_a_minute_then_one_probe_is_let_through(clock):
    assert breaker.failure(A, RuntimeError("x")) == 0
    assert breaker.failure(A, RuntimeError("x")) == 0
    assert breaker.allow(A)
    assert breaker.failure(A, RuntimeError("x")) == 60.0
    assert not breaker.allow(A) and breaker.is_open(A)
    assert breaker.allow(B), "another deployment is unaffected"
    clock.t += 61
    assert breaker.allow(A), "half-open: one probe"
    assert not breaker.allow(A), "and only one"


def test_a_failed_probe_doubles_the_cooldown_up_to_ten_minutes(clock):
    for _ in range(3):
        breaker.failure(A, RuntimeError("x"))
    waits = []
    for _ in range(6):
        clock.t += 10_000
        assert breaker.allow(A)
        waits.append(breaker.failure(A, RuntimeError("x")))
    assert waits == [120.0, 240.0, 480.0, 600.0, 600.0, 600.0]


def test_success_closes_it_and_resets_the_backoff(clock):
    for _ in range(3):
        breaker.failure(A, RuntimeError("x"))
    clock.t += 61
    assert breaker.allow(A)
    breaker.success(A)
    assert breaker.allow(A) and not breaker.is_open(A)
    assert breaker.failure(A, RuntimeError("x")) == 0, "counts from zero again"


def test_retry_after_is_honoured_exactly_and_capped(clock):
    assert breaker.failure(A, _Http(429, {"retry-after": "17"})) == 17.0
    clock.t += 16.9
    assert not breaker.allow(A)
    clock.t += 0.2
    assert breaker.allow(A)
    assert breaker.failure(B, _Http(429, {"retry-after": "86400"})) == breaker.MAX_COOLDOWN_S


@pytest.mark.parametrize("status", [401, 402, 403])
def test_an_auth_or_billing_answer_opens_at_once(clock, status):
    assert breaker.failure(A, _Http(status)) == breaker.MAX_COOLDOWN_S
    assert not breaker.allow(A)
    assert "anthropic:sonnet" in breaker.snapshot()


def test_a_single_5xx_or_a_429_without_a_wait_does_not_open(clock):
    assert breaker.failure(A, _Http(503)) == 0
    assert breaker.failure(B, _Http(429)) == 0
    assert breaker.allow(A) and breaker.allow(B)


# ------------------------------------------------------------------ think()

class _Provider:
    def __init__(self, name, model, *, fail=None):
        self.name, self.model, self.fail, self.calls = name, model, fail, 0

    async def complete(self, system, messages, tools, max_tokens):
        self.calls += 1
        if self.fail is not None:
            raise self.fail
        return Completion(text=f"from {self.name}", provider=self.name, model=self.model)


@pytest.fixture
def routed(monkeypatch, clock):
    dead = _Provider("anthropic", "sonnet", fail=_Http(402))
    live = _Provider("groq", "fast")
    monkeypatch.setitem(llm.ROUTES, Need.DRAFT, [dead, live])
    return dead, live


async def test_think_skips_an_open_deployment_on_the_next_call(routed):
    dead, live = routed
    assert (await llm.think(Need.DRAFT, "s", [])).text == "from groq"
    assert dead.calls == 1
    assert (await llm.think(Need.DRAFT, "s", [])).text == "from groq"
    assert dead.calls == 1, "not asked again while its breaker is open"
    assert live.calls == 2


async def test_think_still_asks_when_every_breaker_is_open(monkeypatch, clock):
    a = _Provider("anthropic", "sonnet", fail=_Http(402))
    b = _Provider("groq", "fast", fail=_Http(401))
    monkeypatch.setitem(llm.ROUTES, Need.DRAFT, [a, b])
    with pytest.raises(RuntimeError, match="all providers failed"):
        await llm.think(Need.DRAFT, "s", [])
    assert (a.calls, b.calls) == (1, 1)
    with pytest.raises(RuntimeError, match="all providers failed"):
        await llm.think(Need.DRAFT, "s", [])
    assert (a.calls, b.calls) == (2, 2), "all open: the chain is tried, not refused"


async def test_a_recovered_provider_is_used_again_after_its_cooldown(routed, clock):
    dead, _live = routed
    await llm.think(Need.DRAFT, "s", [])
    dead.fail = None
    clock.t += breaker.MAX_COOLDOWN_S + 1
    assert (await llm.think(Need.DRAFT, "s", [])).text == "from anthropic"
    assert not breaker.is_open(("anthropic", "sonnet"))
