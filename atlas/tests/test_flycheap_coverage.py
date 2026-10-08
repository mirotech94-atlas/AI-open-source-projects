"""RA-E3: FlyCheap fare-source coverage, one dimension at a time.

Each source is configured / enabled / reachable / fresh, separately. A source
switched off is a coverage choice -- not a success, not an outage. One source
succeeding says nothing about the others. And a stale source produces one
tracked next step, through the signal note path, never through the executor.
"""
from __future__ import annotations

import json

import pytest

from atlas.kernel.contracts import Health
from atlas.modules import flycheap
from atlas.modules.flycheap import SourceStatus, assess_coverage


def src(name, *, configured=True, enabled=True, reachable=True, fresh=True):
    return SourceStatus(name, configured, enabled, reachable, fresh)


# ------------------------------------------------------------ pure verdict

def test_one_stale_source_among_fresh_ones_is_reported_by_name():
    cov = assess_coverage([src("kiwi"), src("duffel", fresh=False),
                           src("amadeus")])
    assert cov.health is Health.DEGRADED
    assert cov.stale == ("duffel",)
    assert "stale: duffel" in cov.detail
    assert "fresh: kiwi, amadeus" in cov.detail
    assert cov.next_step and "duffel" in cov.next_step


def test_a_disabled_source_is_a_choice_not_a_success_or_an_outage():
    cov = assess_coverage([src("kiwi"), src("serpapi", enabled=False,
                                             reachable=False, fresh=False)])
    assert cov.health is Health.OK
    assert cov.off_by_choice == ("serpapi",)
    assert cov.stale == () and cov.unreachable == ()
    assert "off by choice: serpapi" in cov.detail
    assert "fresh: kiwi" in cov.detail          # serpapi is not counted as fresh
    assert cov.next_step is None


def test_every_usable_source_stale_is_failing():
    cov = assess_coverage([src("kiwi", fresh=False),
                           src("duffel", reachable=False)])
    assert cov.health is Health.FAILING
    assert cov.unreachable == ("duffel",)


def test_one_healthy_source_does_not_vouch_for_one_not_measured():
    cov = assess_coverage([src("kiwi"),
                           SourceStatus("duffel", True, True, None, None,
                                        why={"fresh": "not exposed",
                                             "reachable": "not exposed"})])
    assert cov.health is Health.UNKNOWN
    assert "not known for duffel (not exposed)" in cov.detail


def test_nothing_configured_measures_nothing():
    assert assess_coverage([], not_configured=6).health is Health.UNKNOWN


# ------------------------------------------------- the real /api/health shape

def health(providers="4/6 configured (kiwi, duffel, amadeus, serpapi)",
           last_poll="12 min ago"):
    return {"ok": True, "checks": {
        "database": {"ok": True, "detail": "10 users"},
        "last_poll": {"ok": True, "detail": last_poll},
        "providers": {"ok": True, "detail": providers}}}


def test_the_real_payload_names_what_it_cannot_say():
    """/api/health reports which sources are configured and one global
    last-success age. Enabled, reachable and per-source freshness are not in
    it, so they are not known -- never filled in."""
    got = flycheap.sources_from_health(health())
    assert not isinstance(got, str)
    sources, not_configured = got
    assert [x.source for x in sources] == ["kiwi", "duffel", "amadeus", "serpapi"]
    assert not_configured == 2
    for x in sources:
        assert x.configured is True
        assert x.enabled is None and x.reachable is None and x.fresh is None
        assert "not exposed" in x.why["enabled"]
    assert assess_coverage(sources, not_configured).health is Health.UNKNOWN


def test_a_stale_global_poll_is_every_source_stale():
    """Exact, not inferred: nothing has succeeded for 300 minutes, so no
    single source has either."""
    got = flycheap.sources_from_health(health(last_poll="300 min ago"))
    sources, _ = got
    assert all(x.fresh is False for x in sources)
    cov = assess_coverage(sources, 2)
    assert cov.stale == ("kiwi", "duffel", "amadeus", "serpapi")
    # No usable source is delivering: coverage is gone, whatever the reason.
    assert cov.health is Health.FAILING
    assert all(x.reachable is None for x in sources)   # still not claimed


@pytest.mark.parametrize("detail", ["", "configured", "2/6 configured (kiwi)"])
def test_an_unparseable_providers_line_is_said(detail):
    got = flycheap.sources_from_health(health(providers=detail))
    assert isinstance(got, str)


async def test_coverage_check_reads_the_payload(monkeypatch):
    async def payload():
        return health(last_poll="300 min ago"), None
    monkeypatch.setattr(flycheap, "_health_payload", payload)
    obs = await flycheap.check_coverage()
    assert obs.health is Health.FAILING
    assert obs.evidence["stale"] == ["kiwi", "duffel", "amadeus", "serpapi"]
    assert obs.evidence["not_configured"] == 2
    assert "coverage" in {c.name for c in flycheap.MODULE.checks()}

    async def down():
        return None, "unreachable: ConnectError"
    monkeypatch.setattr(flycheap, "_health_payload", down)
    assert (await flycheap.check_coverage()).health is Health.UNKNOWN


# --------------------------------------- the remediation step, end to end

async def test_a_stale_source_becomes_one_tracked_note_not_an_action(
        monkeypatch, memory_backend):
    """Injected stale source -> one signal row, one notification carrying the
    next step, no proposal and no executor call. A second cycle inside the
    dedup window adds nothing."""
    from atlas.kernel import approvals, comms, executor, scheduler
    from atlas.kernel.registry import REGISTRY

    async def stale():
        return health(last_poll="300 min ago"), None
    monkeypatch.setattr(flycheap, "_health_payload", stale)
    monkeypatch.setattr(REGISTRY, "modules", {"flycheap": flycheap.MODULE})

    told: list = []

    async def notify(msg):
        told.append(msg)
        return ["slack"]
    monkeypatch.setattr(comms, "notify", notify)

    async def no_propose(**kw):
        raise AssertionError("a coverage note must not become a proposal")

    async def no_execute(*a, **kw):
        raise AssertionError("a coverage note must not reach the executor")
    monkeypatch.setattr(approvals, "propose", no_propose)
    monkeypatch.setattr(executor, "execute", no_execute)

    memory_backend.responses["SELECT id FROM signals"] = None
    assert await scheduler.collect_signals() == 1
    inserts = [(sql, args) for sql, args in memory_backend.sql_log
               if sql.startswith("INSERT INTO signals")]
    assert len(inserts) == 1
    _sql, args = inserts[0]
    assert args[0] == "flycheap" and args[2] == flycheap.COVERAGE_SIGNAL
    assert args[4] is None                       # suggests nothing
    assert json.loads(args[5]) == {}
    assert len(told) == 1
    assert told[0].title == flycheap.COVERAGE_SIGNAL
    assert "Next step: check" in told[0].body
    assert "stale: kiwi, duffel, amadeus, serpapi" in told[0].body

    # Same cause next cycle: the title is stable, so the 24 h dedup holds.
    memory_backend.responses["SELECT id FROM signals"] = {"id": 1}
    assert await scheduler.collect_signals() == 0
    assert len(told) == 1


async def test_unconfigured_sources_are_not_called_switched_off(monkeypatch):
    async def fine():
        return health(), None
    monkeypatch.setattr(flycheap, "_health_payload", fine)
    signals = await flycheap.MODULE.signals()
    assert len(signals) == 1
    assert signals[0].title == "FlyCheap: 4/6 fare sources configured"
    assert "not configured" in signals[0].body
    assert "switched off" not in signals[0].body
    assert "coverage choice" in signals[0].body
