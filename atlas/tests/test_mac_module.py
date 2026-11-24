"""The Mac bridge: instant, but only for the owner asking directly; asleep is an
instant refusal, never a job that runs when the lid opens."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from atlas.kernel import executor
from atlas.kernel.contracts import Tier
from atlas.kernel.executor import ExecutionRequest
from atlas.kernel.permissions import classify
from atlas.kernel.registry import REGISTRY
from atlas.modules import mac


@pytest.fixture
def registered(monkeypatch, memory_backend):
    monkeypatch.setenv("ATLAS_WATCH_ONLY", "false")
    for a in mac.MODULE.actions():
        monkeypatch.setitem(REGISTRY.actions, a.name, a)
        monkeypatch.setitem(REGISTRY.decisions, a.name, classify(a))
    executor._recent.clear()
    return memory_backend


def _awake(mem, seconds_ago=5):
    mem.responses["SELECT max(last_seen) AS at FROM mac_agent"] = {
        "at": datetime.now(UTC) - timedelta(seconds=seconds_ago)}


def test_every_mac_action_is_principal_only_with_a_dry_run():
    """Tier by tier in tests/test_mac_control.py. `open_url` and `quit_app`
    were left out of the first version; the owner asked for them on 2026-09-28 as
    Tier 2 cards, and on 2026-10-06 they became Tier 1 (his 2026-09-30
    ruling: not urgent, no card). The message and the Shortcut stay cards."""
    for a in mac.MODULE.actions():
        assert a.principal_only and a.dry_run is not None
        if a.tier is Tier.AUTO:
            assert a.rollback is not None and not a.irreversible
    tiers = {a.name: a.tier for a in mac.MODULE.actions()}
    assert tiers["mac.open_url"] is Tier.AUTO and tiers["mac.quit_app"] is Tier.AUTO
    assert tiers["mac.send_imessage"] is Tier.ASK
    assert tiers["mac.run_shortcut"] is Tier.ASK


@pytest.mark.parametrize("who", [None, "sms:+1555", "phone:+1555", "agent:ops"])
async def test_nothing_but_owner_asking_directly_can_drive_the_mac(registered, who):
    _awake(registered)
    out = await executor.execute(ExecutionRequest(
        "mac.open_app", {"app": "Notes"}, "because", approved_by=who))
    assert not out.ok and "only the owner" in out.detail


async def test_an_asleep_mac_is_refused_at_once_and_nothing_is_queued(registered):
    _awake(registered, seconds_ago=3600)
    out = await executor.execute(ExecutionRequest(
        "mac.open_app", {"app": "Notes"}, "asked", approved_by="slack:UOWNER"))
    assert not out.ok and "asleep" in out.detail
    assert not any("INSERT INTO mac_jobs" in q for q, _ in registered.sql_log)


@pytest.mark.parametrize("app", ["Terminal", "Keychain Access", "System Settings",
                                 "/bin/sh", "../x", "Notes; rm",
                                 "Photo Booth", "photo booth", "FaceTime"])
async def test_a_denied_or_bad_app_is_refused(app):
    assert mac._app_problem(app) is not None


def test_the_reasoner_never_sees_a_mac_action(monkeypatch):
    for a in mac.MODULE.actions():
        monkeypatch.setitem(REGISTRY.actions, a.name, a)
        monkeypatch.setitem(REGISTRY.decisions, a.name, classify(a))
    names = {c["name"] for c in REGISTRY.catalog_for_prompt()}
    assert not any(n.startswith("mac.") for n in names)


async def test_a_claimed_job_with_no_answer_is_outcome_unknown(registered, monkeypatch):
    _awake(registered)
    monkeypatch.setattr(mac, "WAIT_S", 0.5)
    registered.responses["INSERT INTO mac_jobs"] = {"id": 9}
    registered.responses["SELECT status, ok, detail, token FROM mac_jobs"] = {
        "status": "claimed", "ok": None, "detail": None, "token": None}
    registered.responses["SET status = 'expired'"] = None
    registered.responses["SET status = 'unknown'"] = {"id": 9}
    out = await mac.open_app("Notes")
    assert not out.ok and "unknown" in out.detail


async def test_the_undo_only_quits_an_app_this_opened(registered):
    out = await mac.undo_open_app('{"app": "Notes", "opened_by_us": false}')
    assert out.ok and "already open" in out.detail


@pytest.mark.parametrize("given,want", [
    ("2026-09-29T17:00:00+00:00", "2026-09-29 17:00"),
    ("2026-09-29T17:00Z", "2026-09-29 17:00"),
    ("2026-09-29 09:30", "2026-09-29 09:30"),
    ("2026-09-29", "2026-09-29"),
])
def test_a_reminder_keeps_the_clock_time_he_said(given, want):
    assert mac.local_due(given) == want


async def test_said_on_the_signed_in_dashboard_a_mac_action_runs_now(monkeypatch):
    """the owner: 'yes, for Mac actions only'. A phone turn still gets a card."""
    from atlas.api import voice
    from atlas.kernel import intent, spoken
    from atlas.kernel.contracts import ActionResult
    ran, proposed = [], []

    async def choose(text, principal=False):
        return "mac.play_music", {"query": "lofi"}

    async def run(name, params, transcript):
        ran.append(name)
        return ActionResult(True, "YouTube Music is showing results for 'lofi'")

    async def propose(*a, **k):
        proposed.append(a)
        return 1

    for a in mac.MODULE.actions():
        monkeypatch.setitem(REGISTRY.actions, a.name, a)
        monkeypatch.setitem(REGISTRY.decisions, a.name, classify(a))
    monkeypatch.setattr(intent, "choose", choose)
    monkeypatch.setattr(spoken, "run_mac_action", run)
    monkeypatch.setattr("atlas.kernel.approvals.propose", propose)
    pids, said = await voice._queue_steps("play some lofi", source="voice", principal=True)
    assert pids == [] and proposed == [] and ran == ["mac.play_music"] and "lofi" in said
    ran.clear()
    await voice._queue_steps("play some lofi", source="phone", principal=True)
    assert ran == [] and proposed


async def test_the_spoken_door_refuses_anything_but_a_mac_action(memory_backend):
    from atlas.kernel import spoken
    out = await spoken.run_mac_action("gitlab.open_issue", {}, "open an issue")
    assert not out.ok

