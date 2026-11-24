"""The extended read tools and the private gate (plan 3.2).

atlas_pending_approvals, atlas_recent_actions, atlas_brain read ATLAS's own
tables; atlas_owner_notes and atlas_private_brief are PRIVATE-run only. The
gate is `private_data.PRIVATE_RUN`: it fails closed, so a call from anywhere
but a private assistant run reads nothing private.
"""
from __future__ import annotations

from datetime import UTC, datetime

import pytest

from atlas.kernel import private_data
from atlas.kernel.contracts import InboxItem, Severity
from atlas.kernel.registry import REGISTRY
from atlas.kernel.tools import invoke
from atlas.modules.assistant import tools as T

NOW = datetime(2026, 11, 19, 9, 0, tzinfo=UTC)
ALL = set(T.ALL_TOOLS)


@pytest.fixture(autouse=True)
def registered(memory_backend):
    T.register_tools()


async def call(name, params=None, *, agent="assistant"):
    return await invoke(name, params or {}, agent=agent, allowed=ALL)


@pytest.fixture
def private_run():
    token = private_data.PRIVATE_RUN.set(True)
    yield
    private_data.PRIVATE_RUN.reset(token)


# ------------------------------------------------------------- the gate

def test_gate_fails_closed_by_default():
    assert private_data.PRIVATE_RUN.get() is False
    assert private_data.agent_is_private("assistant") is False
    assert private_data.refusal("mail") is not None


def test_gate_opens_only_for_the_assistant_inside_a_private_run(private_run):
    assert private_data.agent_is_private("assistant") is True
    assert private_data.agent_is_private("ops") is False      # another agent, still closed
    assert private_data.agent_is_private(None) is False


def test_private_run_flag_is_not_sticky():
    token = private_data.PRIVATE_RUN.set(True)
    private_data.PRIVATE_RUN.reset(token)
    assert private_data.agent_is_private("assistant") is False




# ------------------------------------------------- pending approvals

class Rows:
    def __init__(self, rows):
        self.rows = rows
        self.sql = []

    async def __call__(self, sql, *args):
        self.sql.append(sql)
        return self.rows


def _proposal(i, action, tier=2):
    return {"id": i, "action": action, "tier": tier, "reason": "because " * 40,
            "created_at": NOW, "expires_at": NOW}


async def test_pending_approvals_lists_and_hides_private_rows(monkeypatch):
    monkeypatch.setattr("atlas.kernel.state.fetch_all", Rows([
        _proposal(3, "cloudhost.restart_instance"), _proposal(2, "mail.archive_old"),
        _proposal(1, "flycheap.regenerate_links", tier=1)]))
    res = await call("atlas_pending_approvals")
    assert res.ok
    c = res.content
    assert [p["id"] for p in c["pending"]] == [3, 1]
    assert c["private_not_shown"] == 1
    assert c["pending"][0]["tier"] == "needs Miro's approval"
    assert len(c["pending"][0]["reason"]) <= 160
    assert "read_at" in c and "mail.archive_old" not in str(c)


async def test_pending_approvals_shows_private_rows_in_a_private_run(monkeypatch,
                                                                     private_run):
    monkeypatch.setattr("atlas.kernel.state.fetch_all", Rows([
        _proposal(2, "mail.archive_old")]))
    c = (await call("atlas_pending_approvals")).content
    assert [p["action"] for p in c["pending"]] == ["mail.archive_old"]
    assert c["private_not_shown"] == 0


async def test_pending_approvals_unreadable_table_says_so(monkeypatch):
    async def boom(sql, *args):
        raise RuntimeError("db down")
    monkeypatch.setattr("atlas.kernel.state.fetch_all", boom)
    c = (await call("atlas_pending_approvals")).content
    assert "could not be read" in c["error"] and "pending" not in c


# --------------------------------------------------------- recent actions

async def test_recent_actions_skips_noise_hides_private_and_never_shows_payload(
        monkeypatch):
    rows = Rows([
        {"at": NOW, "event": "slack.event", "subject": "Ev123"},
        {"at": NOW, "event": "mail.sweep", "subject": "mail"},
        {"at": NOW, "event": "executor.executed", "subject": "cloudhost.restart_instance"},
        {"at": NOW, "event": "assistant.ask", "subject": "answer"}])
    monkeypatch.setattr("atlas.kernel.state.fetch_all", rows)
    c = (await call("atlas_recent_actions", {"limit": 5})).content
    assert [a["event"] for a in c["actions"]] == ["executor.executed", "assistant.ask"]
    assert c["private_not_shown"] == 1
    assert "payload" not in rows.sql[0] and "read_at" in c


async def test_recent_actions_limit_is_clamped(monkeypatch):
    many = Rows([{"at": NOW, "event": f"e{i}", "subject": "s"} for i in range(60)])
    monkeypatch.setattr("atlas.kernel.state.fetch_all", many)
    assert len((await call("atlas_recent_actions", {"limit": 500})).content["actions"]) == 20


# ------------------------------------------------------------------ brain

async def test_brain_returns_only_the_whole(monkeypatch):
    async def build(now=None):
        return {"read_at": "2026-11-19T09:00:00+00:00", "since": "x", "window_days": 7,
                "watch_only": True, "whole": {"waiting_on_you": 2, "paused": []},
                "departments": [{"big": "x" * 5000}], "sops": [{"big": "y" * 5000}],
                "errors": {"verify": "unreadable"}}
    monkeypatch.setattr("atlas.kernel.brain.build", build)
    c = (await call("atlas_brain")).content
    assert c["whole"]["waiting_on_you"] == 2 and c["errors"] == {"verify": "unreadable"}
    assert "departments" not in c and "sops" not in c
    assert c["read_at"] == "2026-11-19T09:00:00+00:00"


async def test_brain_failure_is_an_error_not_an_empty_answer(monkeypatch):
    async def build(now=None):
        raise RuntimeError("down")
    monkeypatch.setattr("atlas.kernel.brain.build", build)
    assert "error" in (await call("atlas_brain")).content


# ------------------------------------------------- private-only tools

class Mail:
    def brief(self):
        return "mail: 3 unread"

    async def inbox(self, limit=10):
        return (InboxItem(at=NOW, sender="Dr Aziz", subject="your results",
                          urgency=Severity.HIGH),)


async def test_private_brief_is_refused_outside_a_private_run(monkeypatch):
    monkeypatch.setitem(REGISTRY.modules, "mail", Mail())
    c = (await call("atlas_private_brief", {"module": "mail"})).content
    assert c["refused"] == "private_module" and "Aziz" not in str(c)


async def test_private_brief_reads_in_a_private_run(monkeypatch, private_run):
    monkeypatch.setitem(REGISTRY.modules, "mail", Mail())
    c = (await call("atlas_private_brief", {"module": "mail"})).content
    assert c["brief"] == "mail: 3 unread"
    assert c["inbox"][0]["sender"] == "Dr Aziz"


async def test_private_brief_refuses_a_module_that_is_not_private(private_run):
    c = (await call("atlas_private_brief", {"module": "flycheap"})).content
    assert "not a private module" in c["error"]


async def test_private_brief_stays_closed_for_another_agent(monkeypatch, private_run):
    monkeypatch.setitem(REGISTRY.modules, "mail", Mail())
    c = (await call("atlas_private_brief", {"module": "mail"}, agent="ops")).content
    assert c["refused"] == "private_module"


async def test_owner_notes_refused_outside_a_private_run(monkeypatch):
    async def recall(*a, **k):
        raise AssertionError("memory must not be touched")
    monkeypatch.setattr("atlas.kernel.memory.recall", recall)
    c = (await call("atlas_owner_notes", {"query": "my dentist"})).content
    assert c["refused"] == "private_module"


async def test_owner_notes_reads_owner_namespaces_in_a_private_run(monkeypatch,
                                                                   private_run):
    from atlas.kernel.memory import Memory, Recalled
    seen = {}

    async def recall(query, namespaces, **kw):
        seen.update(query=query, namespaces=list(namespaces), kw=kw)
        return Recalled([Memory(1, "fact", "owner", "dentist is on Tuesday " * 30,
                                at=NOW)], degraded="ATLAS_EMBED_URL unset")
    monkeypatch.setattr("atlas.kernel.memory.recall", recall)
    c = (await call("atlas_owner_notes", {"query": "my dentist"})).content
    assert seen["namespaces"][0] == "owner" and seen["kw"]["agent"] == "assistant"
    assert len(c["notes"][0]["content"]) <= 300
    assert c["degraded"] == "ATLAS_EMBED_URL unset" and "not a live reading" in c["note"]


# ----------------------------------------------------------- sensor events





def _feed(health, detail="NVR answered"):
    async def feed():
        return health, detail
    return feed
















# ----------------------------------------------------------- sensor status



















