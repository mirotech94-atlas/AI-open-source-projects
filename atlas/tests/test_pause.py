"""The kill switch (reliability plan, Phase C): durable, global or per
module, enforced in executor.execute, fail-closed, lifted only explicitly."""
from __future__ import annotations

from datetime import UTC, datetime

import pytest

from atlas.kernel import executor, pause
from atlas.kernel.contracts import Action, ActionResult, Tier
from atlas.kernel.executor import ExecutionRequest
from atlas.kernel.permissions import classify
from atlas.kernel.registry import REGISTRY


@pytest.fixture
def armed(monkeypatch, memory_backend):
    monkeypatch.setenv("ATLAS_WATCH_ONLY", "false")
    ran = []

    async def run(**kw):
        ran.append(kw)
        return ActionResult(True, "done", rollback_token="t")

    async def undo(token):
        ran.append(("undo", token))
        return ActionResult(True, "undone")

    async def dry(**kw):
        return ActionResult(True, "would post")

    a = Action(name="reels.post", tier=Tier.AUTO, summary="post", run=run,
               dry_run=dry, rollback=undo)
    monkeypatch.setitem(REGISTRY.actions, a.name, a)
    monkeypatch.setitem(REGISTRY.decisions, a.name, classify(a))
    monkeypatch.setitem(REGISTRY.modules, "reels", object())
    executor._recent.clear()
    return ran, memory_backend


def _paused(scope):
    return {"scope": scope, "paused_at": datetime(2026, 9, 28, 5, 0, tzinfo=UTC),
            "paused_by": "slack:U1", "reason": "checking"}


@pytest.mark.parametrize("scope", ["all", "reels"])
async def test_a_pause_refuses_dispatch_and_says_why(armed, scope):
    ran, mem = armed
    mem.responses["FROM pauses WHERE resumed_at IS NULL AND scope"] = _paused(scope)
    out = await executor.execute(ExecutionRequest("reels.post", {}, "tick"))
    assert not out.ok and "paused" in out.detail and ran == []
    assert any(r.event == "execute.paused" for r in mem.audit_rows)


async def test_an_unreadable_pause_state_refuses(armed, monkeypatch):
    ran, _mem = armed

    async def broken(module, account=None):
        raise pause.PauseUnreadable("ConnectionError")

    monkeypatch.setattr(pause, "active_for", broken)
    out = await executor.execute(ExecutionRequest("reels.post", {}, "tick"))
    assert not out.ok and "could not be read" in out.detail and ran == []


async def test_no_pause_runs(armed):
    ran, _mem = armed
    out = await executor.execute(ExecutionRequest("reels.post", {}, "tick"))
    assert out.ok and len(ran) == 1


async def test_a_rollback_is_not_blocked_by_a_pause(armed):
    ran, mem = armed
    mem.responses["FROM pauses WHERE resumed_at IS NULL AND scope"] = _paused("all")
    out = await executor.rollback("reels.post", "t")
    assert out.ok and ran == [("undo", "t")]


@pytest.mark.parametrize("text,want", [
    ("pause reels", ("pause", "reels", "")),
    ("Pause everything because the render queue is looping",
     ("pause", "everything", "the render queue is looping")),
    ("resume reels", ("resume", "reels", "")),
    ("unpause all", ("resume", "all", "")),
    ("don't pause reels yet", None),
    ("what happens if I pause reels?", None),
    ("please pause reels", None),
])
def test_only_an_exact_command_is_a_command(text, want):
    assert pause.parse(text) == want


async def test_an_unknown_module_is_not_paused(armed):
    said = await pause.command("pause nosuchthing", "cli")
    assert "no module called" in said


async def test_pause_then_resume_records_both(armed):
    _ran, mem = armed
    mem.responses["RETURNING scope"] = {"scope": "reels"}
    assert "paused" in (await pause.command("pause reels because testing", "cli"))
    assert (await pause.command("resume reels", "cli")).startswith("Resumed")
    events = [r.event for r in mem.audit_rows]
    assert "pause.set" in events and "pause.lifted" in events
