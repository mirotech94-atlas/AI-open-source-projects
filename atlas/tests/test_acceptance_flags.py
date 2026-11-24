"""The first-slice flows are off unless both switches say otherwise, and off
means the wired call sites run exactly the code they ran before
(kernel/flows/__init__.py, docs/acceptance.md). No database here: every
test fails if a flow reaches one."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from atlas.kernel import flows, state
from atlas.kernel.contracts import ActionResult
from atlas.kernel.flows import ask
from atlas.kernel.flows import content as content_flow
from atlas.kernel.flows import ops as ops_flow


@pytest.fixture
def no_database(monkeypatch, memory_backend):
    async def refuse(*_a, **_kw):
        raise AssertionError("a switched-off flow reached the database")

    monkeypatch.setattr(state, "fetch_one", refuse)
    monkeypatch.setattr(state, "fetch_all", refuse)
    return memory_backend


def _boom(*_a, **_kw):
    raise AssertionError("a switched-off flow was entered")


@pytest.mark.parametrize("core,named,expect", [
    (None, None, False),
    ("on", None, False),            # the core alone does not start producers
    (None, "all", False),           # nor the list without the core
    ("on", "ask", True),
    ("on", "ops,content", False),
    ("on", "all", True),
    ("off", "ask", False),
])
def test_each_flow_needs_the_core_and_its_own_name(monkeypatch, core, named, expect):
    for var, value in (("ATLAS_DURABLE_CORE", core), ("ATLAS_DURABLE_FLOWS", named)):
        if value is None:
            monkeypatch.delenv(var, raising=False)
        else:
            monkeypatch.setenv(var, value)
    assert flows.enabled("ask") is expect
    assert flows.enabled("not-a-flow") is False


async def test_ask_off_takes_nothing_and_touches_no_database(monkeypatch, no_database):
    monkeypatch.setenv("ATLAS_DURABLE_CORE", "on")
    monkeypatch.delenv("ATLAS_DURABLE_FLOWS", raising=False)
    got = await ask.intake("Ev1", text="hi", user="U", channel="C", thread="1")
    assert got == (ask.OFF, None)
    ask.note_reply(["slack"])                         # outside a workflow: no-op
    assert await ask.answer(_answer("plain"), "knowledge") == "plain"


def _answer(text):
    async def produce():
        return text
    return produce


async def test_ops_off_the_sweep_never_opens_a_workflow(monkeypatch, memory_backend):
    from atlas.kernel import fixes
    monkeypatch.setenv("ATLAS_DURABLE_CORE", "on")
    monkeypatch.delenv("ATLAS_DURABLE_FLOWS", raising=False)
    now = datetime(2026, 9, 28, 9, 0, tzinfo=UTC)
    memory_backend.responses["DISTINCT ON (check_id)"] = [
        {"module": "guardian", "check": "open_alerts", "health": "degraded",
         "detail": "d", "last_ok": now - timedelta(hours=5),
         "first_seen": now - timedelta(days=3)}]
    memory_backend.responses["FROM acks WHERE until > now()"] = []
    memory_backend.responses["event = 'fixes.attempted'"] = []

    async def route(_prompt):
        return "Restart the worker by hand."

    async def notify(_msg):
        return ["slack"]

    monkeypatch.setattr("atlas.kernel.agents.route", route)
    monkeypatch.setattr("atlas.kernel.comms.notify", notify)
    monkeypatch.setattr(ops_flow, "begin", _boom)
    from atlas.kernel.registry import REGISTRY
    monkeypatch.setattr(REGISTRY, "checks", {})
    done = await fixes.sweep(now)
    assert [k for k, _ in done] == ["guardian.open_alerts"]


async def test_content_off_post_or_card_posts_through_the_executor_as_before(
        monkeypatch, memory_backend):
    from atlas.kernel import reels
    from atlas.modules.content import crosspost, quality
    monkeypatch.setenv("ATLAS_DURABLE_CORE", "on")
    monkeypatch.delenv("ATLAS_DURABLE_FLOWS", raising=False)
    monkeypatch.setenv("REELS_AUTOPUBLISH", "on")
    ran: list = []

    async def execute(req):
        ran.append(req.action)
        return ActionResult(True, "posted on 3 platforms")

    async def gate(*_a, **_kw):
        return None

    async def cta(_media_id, caption):
        return caption

    async def notify(_msg):
        return ["slack"]

    monkeypatch.setattr(reels, "execute", execute)
    monkeypatch.setattr(reels, "notify", notify)
    monkeypatch.setattr(quality, "check_media", gate)
    monkeypatch.setattr("atlas.modules.shop.caption_with_cta", cta)
    for name in ("autopost", "gate_failed", "carded"):
        monkeypatch.setattr(content_flow, name, _boom)
    assert crosspost.autopublish_enabled()
    got = await reels.post_or_card({"media_id": 7, "caption": "x. Made with AI"},
                                   "a reel", dedup="d1", media_id=7,
                                   rollback_plan="delete it", series=False)
    assert got == ("posted", None, "posted on 3 platforms")
    assert ran == ["content.autopublish_reel"]


async def test_core_on_flows_off_the_outbox_step_runs_no_flow_pass(monkeypatch):
    from atlas.kernel import outbox, scheduler
    monkeypatch.setenv("ATLAS_DURABLE_CORE", "on")
    monkeypatch.delenv("ATLAS_DURABLE_FLOWS", raising=False)

    async def nothing(*_a, **_kw):
        return []

    async def none(*_a, **_kw):
        return None

    monkeypatch.setattr(outbox, "dispatch", nothing)
    monkeypatch.setattr(outbox, "reconcile_once", none)
    monkeypatch.setattr(flows, "tick", _boom)
    await scheduler._outbox_pass()
