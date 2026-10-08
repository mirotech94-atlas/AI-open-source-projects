"""PostHog: read-only site traffic for the weekly report. A read that fails
is UNKNOWN; a project that has never received an event says so rather than
reporting a confident zero."""
from __future__ import annotations

from atlas.modules import posthog


def test_it_can_do_nothing_but_read():
    assert posthog.MODULE.actions() == ()


async def test_unreadable_is_unknown(monkeypatch):
    async def fail(sql):
        return None, "PostHog returned 401"

    monkeypatch.setattr(posthog, "hogql", fail)
    obs = await posthog.check_traffic()
    assert obs.health.value == "unknown" and obs.value is None
    assert "401" in obs.detail


async def test_no_events_ever_is_unknown_not_zero(monkeypatch):
    async def q(sql):
        return [[0, 0, 0]], None

    monkeypatch.setattr(posthog, "hogql", q)
    obs = await posthog.check_traffic()
    assert obs.health.value == "unknown"
    assert "no site sends events yet" in obs.detail


async def test_traffic_is_reported_per_site_with_its_read_time(monkeypatch):
    calls = []

    async def q(sql):
        calls.append(sql)
        if "group by" in sql.lower():
            return [["guarder.io", 30, 12], ["flycheap.live", 20, 9]], None
        return [[50, 21, 400]], None

    monkeypatch.setattr(posthog, "hogql", q)
    obs = await posthog.check_traffic()
    assert obs.health.value == "ok" and obs.value == 50
    assert "guarder.io 30" in obs.detail and "flycheap.live 20" in obs.detail
    assert "UTC" in obs.detail
