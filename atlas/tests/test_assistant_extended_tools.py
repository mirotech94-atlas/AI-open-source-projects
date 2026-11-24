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
from atlas.kernel.tools import TOOLS, invoke
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


def test_extended_tools_are_read_only_and_closed():
    assert T.EXTRA_TOOLS == {"atlas_pending_approvals", "atlas_recent_actions",
                             "atlas_brain", "atlas_owner_notes",
                             "atlas_private_brief", "atlas_camera_events",
                             "camera_status"}
    assert T.EXTRA_TOOLS.isdisjoint(T.ASSISTANT_TOOLS)         # base set unchanged
    for name in T.EXTRA_TOOLS:
        assert TOOLS[name].read is not None and TOOLS[name].action is None
        assert len(TOOLS[name].description) <= 110
    assert T.PRIVATE_ONLY_TOOLS <= T.EXTRA_TOOLS
    assert not (T.PRIVATE_ONLY_TOOLS & T.LOCAL_TOOLS)


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
        _proposal(3, "cloudco.restart_instance"), _proposal(2, "mail.archive_old"),
        _proposal(1, "flycheap.regenerate_links", tier=1)]))
    res = await call("atlas_pending_approvals")
    assert res.ok
    c = res.content
    assert [p["id"] for p in c["pending"]] == [3, 1]
    assert c["private_not_shown"] == 1
    assert c["pending"][0]["tier"] == "needs the owner's approval"
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
        {"at": NOW, "event": "executor.executed", "subject": "cloudco.restart_instance"},
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


# ----------------------------------------------------------- camera events

async def test_camera_events_is_private_only_and_read_only():
    assert "atlas_camera_events" in T.EXTRA_TOOLS
    assert "atlas_camera_events" in T.PRIVATE_ONLY_TOOLS
    assert TOOLS["atlas_camera_events"].action is None


async def test_camera_events_is_refused_outside_a_private_run(monkeypatch):
    monkeypatch.setattr("atlas.kernel.state.fetch_all", Rows([]))
    c = (await call("atlas_camera_events", {"minutes": 60})).content
    assert c.get("refused") == "private_module"


def _feed(health, detail="NVR answered"):
    async def feed():
        return health, detail
    return feed


async def test_camera_events_counts_by_kind_with_the_read_time(monkeypatch,
                                                                private_run):
    rows = Rows([{"kind": "person", "n": 2}, {"kind": "vehicle", "n": 1}])
    monkeypatch.setattr("atlas.kernel.state.fetch_all", rows)
    monkeypatch.setattr(T, "_camera_feed", _feed("ok"))
    c = (await call("atlas_camera_events", {"minutes": 60})).content
    assert c["total"] == 3 and c["by_kind"] == {"person": 2, "vehicle": 1}
    assert c["window_minutes"] == 60 and "read_at" in c
    assert c["nvr_health"] == "ok" and "note" not in c
    assert "snapshot" not in str(c)
    assert "make_interval(mins => $1::int)" in rows.sql[0]


async def test_camera_events_zero_with_a_live_nvr_is_a_zero(monkeypatch, private_run):
    monkeypatch.setattr("atlas.kernel.state.fetch_all", Rows([]))
    monkeypatch.setattr(T, "_camera_feed", _feed("ok"))
    c = (await call("atlas_camera_events", {"minutes": 30})).content
    assert c["total"] == 0 and c["by_kind"] == {} and "error" not in c


@pytest.mark.parametrize("health", ["unknown", "failing", "degraded"])
async def test_camera_events_zero_with_the_nvr_not_ok_is_never_a_zero(
        monkeypatch, private_run, health):
    monkeypatch.setattr("atlas.kernel.state.fetch_all", Rows([]))
    monkeypatch.setattr(T, "_camera_feed", _feed(health, "the NVR could not be read"))
    c = (await call("atlas_camera_events", {"minutes": 60})).content
    assert "total" not in c and "by_kind" not in c
    assert "not a count of zero" in c["error"] and c["nvr_health"] == health


async def test_camera_events_counts_with_the_nvr_not_ok_carry_a_caveat(
        monkeypatch, private_run):
    monkeypatch.setattr("atlas.kernel.state.fetch_all",
                        Rows([{"kind": "motion", "n": 4}]))
    monkeypatch.setattr(T, "_camera_feed", _feed("unknown"))
    c = (await call("atlas_camera_events", {"minutes": 60})).content
    assert c["total"] == 4 and "there may have been more" in c["note"]


async def test_camera_feed_reads_the_nvr_check_and_fails_closed(monkeypatch,
                                                                private_run):
    from atlas.kernel.tools import CURRENT_AGENT
    token = CURRENT_AGENT.set("assistant")        # as inside `tools.invoke`
    try:
        await _camera_feed_cases(monkeypatch)
    finally:
        CURRENT_AGENT.reset(token)


async def _camera_feed_cases(monkeypatch):
    fresh = datetime.now(UTC)
    nvr = {"check": "nvr", "health": "ok", "value": 1.0, "unit": "reachable",
           "detail": "NVR answered", "observed_at": fresh, "interval_s": 300}
    monkeypatch.setattr("atlas.kernel.state.fetch_all", Rows([nvr]))
    assert await T._camera_feed() == ("ok", "NVR answered")
    stale = {**nvr, "observed_at": datetime(2020, 1, 1, tzinfo=UTC)}
    monkeypatch.setattr("atlas.kernel.state.fetch_all", Rows([stale]))
    assert (await T._camera_feed())[0] == "unknown"           # a stale OK is not OK
    other = {**nvr, "check": "channels"}
    monkeypatch.setattr("atlas.kernel.state.fetch_all", Rows([other]))
    assert (await T._camera_feed())[0] == "unknown"           # no NVR reading

    async def boom(sql, *args):
        raise RuntimeError("db down")
    monkeypatch.setattr("atlas.kernel.state.fetch_all", boom)
    assert (await T._camera_feed())[0] == "unknown"


async def test_camera_events_unreadable_says_so_never_zero(monkeypatch, private_run):
    async def boom(sql, *args):
        raise RuntimeError("db down")
    monkeypatch.setattr("atlas.kernel.state.fetch_all", boom)
    c = (await call("atlas_camera_events", {"minutes": 60})).content
    assert "could not be read" in c["error"] and "total" not in c


# ----------------------------------------------------------- camera status

def _nvr(online_by_channel, *, value_ok=True, reason="NVR unreachable"):
    """What `_camera_channels` returns: (the NVR's per-channel list or None,
    the reason when None, the time it was read)."""
    async def read():
        stamp = "2026-11-19T09:00:00+00:00"
        if not value_ok:
            return None, reason, stamp
        return ([{"channel": c, "online": o} for c, o in online_by_channel.items()],
                None, stamp)
    return read


def test_camera_status_is_a_private_only_read_only_tool():
    # cameras is a private domain: never offered to a cloud (non-private) run.
    assert "camera_status" in T.EXTRA_TOOLS
    assert "camera_status" in T.PRIVATE_ONLY_TOOLS
    assert TOOLS["camera_status"].action is None


async def test_camera_status_counts_online_and_names_the_offline(monkeypatch, private_run):
    from atlas.modules.cameras import channels
    states = {c: 1 for c in range(channels.COUNT)}
    states[0], states[11] = 0, 0
    monkeypatch.setattr(T, "_camera_channels", _nvr(states))
    c = (await call("camera_status")).content
    assert c["total"] == channels.COUNT and c["online"] == channels.COUNT - 2
    assert c["offline"] == [channels.NAMES[0], channels.NAMES[11]]
    assert c["unknown"] == [] and c["read_at"]


async def test_camera_status_an_unlisted_or_unstated_camera_is_unknown_never_online(
        monkeypatch, private_run):
    from atlas.modules.cameras import channels
    states = {c: 1 for c in range(channels.COUNT)}
    del states[3]                          # the NVR did not list it
    states[4] = None                       # listed with no online state
    states[5] = "yes"                      # not the NVR's 1
    monkeypatch.setattr(T, "_camera_channels", _nvr(states))
    c = (await call("camera_status")).content
    assert c["online"] == channels.COUNT - 3
    assert c["offline"] == []
    assert c["unknown"] == [channels.NAMES[3], channels.NAMES[4], channels.NAMES[5]]


async def test_camera_status_with_the_nvr_down_reports_no_count(monkeypatch, private_run):
    monkeypatch.setattr(T, "_camera_channels", _nvr({}, value_ok=False))
    c = (await call("camera_status")).content
    assert "NVR unreachable" in c["error"]
    assert c["online"] is None and c["offline"] is None      # never a guessed zero
    assert c["total"] and c["read_at"]


async def test_camera_status_a_reader_that_raises_is_an_error_not_a_count(monkeypatch, private_run):
    async def boom():
        raise RuntimeError("boom")
    monkeypatch.setattr(T, "_camera_channels", boom)
    c = (await call("camera_status")).content
    assert "error" in c and c["online"] is None


async def test_camera_channels_reads_the_cameras_service_and_fails_closed(monkeypatch):
    from datetime import datetime as dt

    from atlas.modules.cameras import nvr, service
    now = dt(2026, 11, 19, 9, 0, tzinfo=UTC)

    class Svc:
        def __init__(self, read):
            self.read = read

        async def channel_status(self, max_age=0.0):
            return self.read

    monkeypatch.setattr(nvr, "missing_env", list)
    good = nvr.Read({"status": [{"channel": 0, "online": 1}]}, None, now)
    monkeypatch.setattr(service, "SERVICE", Svc(good))
    rows, reason, at = await T._camera_channels()
    assert rows == [{"channel": 0, "online": 1}] and reason is None and at == now.isoformat()
    monkeypatch.setattr(service, "SERVICE", Svc(nvr.Read(None, "timed out", now)))
    rows, reason, _ = await T._camera_channels()
    assert rows is None and "timed out" in reason
    monkeypatch.setattr(nvr, "missing_env", lambda: ["NVR_HOST"])
    rows, reason, _ = await T._camera_channels()
    assert rows is None and "NVR_HOST" in reason


async def test_camera_status_refuses_outside_a_private_run(monkeypatch):
    monkeypatch.setattr(T, "_camera_channels", _nvr({0: 1}))
    c = (await call("camera_status")).content
    assert c["refused"] == "private_module" and "online" not in c


async def test_camera_status_answers_the_owner_on_a_private_run(monkeypatch, private_run):
    from atlas.modules.cameras import channels
    monkeypatch.setattr(T, "_camera_channels", _nvr({c: 1 for c in range(channels.COUNT)}))
    c = (await call("camera_status")).content
    assert c["online"] == channels.COUNT


def test_an_online_question_routes_to_a_private_run():
    from atlas.modules.assistant import privacy
    for q in ("How many cameras are online right now?", "are any cameras offline",
              "which cameras are down", "how many cameras do I have"):
        assert privacy.private_reason(q), q
    assert not privacy.private_reason("tell me something about octopuses")
