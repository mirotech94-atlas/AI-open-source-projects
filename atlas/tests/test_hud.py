"""The portal HUD (`kernel/hud.py`, `GET /api/hud`).

Proved here:
  * the endpoint needs a session, serves `read_at` + `watch_only`, and has no
    route that changes anything;
  * the read model and its router are read-only (walked in the AST);
  * a read that fails is None + `unread`, never 0, and every section keeps its
    `read_at` (invariants 6 and 7);
  * a surface is "working" only while an `execute.start` has no finish; a
    watch-only run is shown as watch-only, not ok; a surface nothing records is
    UNKNOWN "not instrumented", never OK; stale or absent checks are UNKNOWN;
  * a pending Tier 2 card makes its task "waiting for your approval"; an
    expired one is "expired, not run", never done; a Slack Tier 2 run is
    "approved by you in Slack";
  * no table text and no secret reaches the response, and no request text.
"""
from __future__ import annotations

import ast
import json
import pathlib
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from atlas.api import auth
from atlas.kernel import hud, intent

SECRET = "s" * 40
PASSWORD = "a-long-enough-password"
NOW = datetime(2026, 11, 10, 16, 0, tzinfo=UTC)
FAKE = "tok_" + "x" * 36
ROOT = pathlib.Path(__file__).parent.parent / "atlas"


@pytest.fixture
def client(monkeypatch, memory_backend):
    monkeypatch.setenv("SESSION_SECRET", SECRET)
    monkeypatch.setenv("DASHBOARD_PASSWORD_HASH", auth.hash_password(PASSWORD))
    monkeypatch.setenv("MESH_ALLOWED_CIDRS", "127.0.0.1/32")
    auth._attempts.clear()
    from atlas.api.main import app

    async def no_boot() -> None:
        return None

    monkeypatch.setattr("atlas.api.main.boot", no_boot)
    monkeypatch.setattr("atlas.api.main.scheduler.loop", no_boot)
    with TestClient(app, client=("127.0.0.1", 51013)) as test_client:
        yield test_client


def _kwargs(**over):
    at = NOW - timedelta(seconds=1)
    kw = {
        "now": NOW, "watch_only": False, "instrumented": {"gitlab", "mac", "qa"},
        "tasks": [], "tasks_at": at, "missions": [], "missions_at": at,
        "pending": [], "pending_at": at, "events": [], "events_at": at,
        "schedule": [], "schedule_at": at, "mac_jobs": [], "mac_jobs_at": at,
        "mac_seen": None, "mac_seen_at": at, "checks": [], "checks_at": at,
        "running": [], "tools": [], "errors": {}}
    kw.update(over)
    return kw


def _tile(snap: dict, key: str) -> dict:
    return next(t for t in snap["surfaces"]["items"] if t["key"] == key)


def _ev(event: str, subject: str, minutes_ago: float, **extra) -> dict:
    return {"event": event, "subject": subject, "at": NOW - timedelta(minutes=minutes_ago),
            "tier": None, "channel": None, **extra}


# ------------------------------------------------------------------ endpoint

def test_the_hud_needs_a_session(client):
    assert client.get("/api/hud").status_code == 401


def test_a_signed_in_reader_gets_read_at_and_watch_only(client, monkeypatch):
    monkeypatch.setenv("ATLAS_WATCH_ONLY", "true")
    assert client.post("/api/login", json={"password": PASSWORD}).status_code == 200
    body = client.get("/api/hud").json()
    assert body["read_at"] and body["served_at"]
    assert body["watch_only"] is True


def test_every_read_failing_gives_none_never_zero(client, monkeypatch):
    async def fails(*_a, **_kw):
        raise RuntimeError("database is down")

    for name in ("_read_tasks", "_read_missions", "_read_pending", "_read_events",
                 "_read_schedule", "_read_mac_jobs", "_read_mac_seen", "_read_checks"):
        monkeypatch.setattr(hud, name, fails)
    assert client.post("/api/login", json={"password": PASSWORD}).status_code == 200
    body = client.get("/api/hud").json()
    for section in ("tasks", "missions", "waiting", "surfaces", "jobs"):
        assert body[section]["read_at"], section
        assert body[section]["unread"], section
    assert body["tasks"]["items"] is None
    assert body["waiting"]["count"] is None
    assert body["missions"]["items"] is None
    assert body["jobs"]["running"] is None
    for tile in body["surfaces"]["items"]:
        assert tile["state"] == "unknown" and tile["health"] == "unknown"
        assert tile["pending_cards"] is None
    assert {"tasks", "actions_log", "checks"} <= set(body["errors"])


def test_the_router_has_only_get_routes():
    from atlas.api.hud import router
    methods = {m for r in router.routes for m in getattr(r, "methods", set())}
    assert methods <= {"GET", "HEAD"} and methods


def test_the_modules_are_read_only():
    for rel in ("kernel/hud.py", "api/hud.py"):
        source = (ROOT / rel).read_text()
        tree = ast.parse(source)
        names = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
        names |= {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)}
        imported = {a.name for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)
                    for a in n.names}
        modules = {n.module or "" for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)}
        for forbidden in ("execute", "invoke", "propose", "decide", "think",
                          "run_agent", "execute_sql", "run_tier1"):
            assert forbidden not in names | imported, (rel, forbidden)
        for mod in ("executor", "intent", "approvals", "tools", "harness", "llm"):
            assert not any(m.split(".")[-1] == mod for m in modules), (rel, mod)
            assert mod not in imported, (rel, mod)
        for verb in ("INSERT ", "UPDATE ", "DELETE ", "TRUNCATE ", "DROP "):
            assert verb not in source.upper(), (rel, verb)


@pytest.mark.parametrize("value", [None, "true", "1", "0", "false", "no", "off", " OFF "])
def test_watch_only_reads_like_the_executor(monkeypatch, value):
    from atlas.kernel import executor
    if value is None:
        monkeypatch.delenv("ATLAS_WATCH_ONLY", raising=False)
    else:
        monkeypatch.setenv("ATLAS_WATCH_ONLY", value)
    assert hud.watch_only() == executor.watch_only()


def test_the_approval_allow_list_is_unchanged():
    assert intent.CAN_APPROVE == frozenset({"slack", "call_out"})


# ------------------------------------------------------------------ surfaces

def test_start_without_finish_is_working():
    snap = hud.assemble(**_kwargs(events=[_ev("execute.start", "gitlab.open_issue", 1)]))
    tile = _tile(snap, "gitlab")
    assert tile["state"] == "working"
    assert tile["last_action"] == "gitlab.open_issue"


def test_start_then_finish_is_idle_with_the_finish_time():
    finish = _ev("execute.finish", "gitlab.open_issue", 2)
    start = _ev("execute.start", "gitlab.open_issue", 3)
    snap = hud.assemble(**_kwargs(events=[finish, start]))     # newest first
    tile = _tile(snap, "gitlab")
    assert tile["state"] == "idle"
    assert tile["last_at"] == finish["at"].isoformat()


def test_a_watch_only_run_is_shown_as_watch_only_not_ok():
    snap = hud.assemble(**_kwargs(
        watch_only=True, events=[_ev("execute.watch_only", "mac.open_app", 1)]))
    assert snap["watch_only"] is True
    assert _tile(snap, "mac")["state"] == "watch_only"


@pytest.mark.parametrize("event,state", [
    ("execute.refused", "refused"), ("execute.paused", "paused"),
    ("execute.rate_limited", "rate_limited"), ("execute.qa_held", "qa_held")])
def test_each_hold_is_its_own_state(event, state):
    snap = hud.assemble(**_kwargs(events=[_ev(event, "qa.run_suite", 1)]))
    assert _tile(snap, "qa")["state"] == state


def test_a_surface_nothing_records_is_unknown_not_instrumented():
    snap = hud.assemble(**_kwargs(instrumented=set()))
    tile = _tile(snap, "gitlab")
    assert tile["state"] == "unknown" and tile["why"] == "not instrumented"


def test_a_recent_tool_name_lights_an_uninstrumented_surface_as_seen():
    seen = {"agent": "sites", "tool": "gitlab_list_issues", "at": NOW - timedelta(minutes=2)}
    snap = hud.assemble(**_kwargs(instrumented=set(), tools=[seen]))
    tile = _tile(snap, "gitlab")
    assert tile["tool_seen"]["tool"] == "gitlab_list_issues"
    assert tile["state"] == "unknown"           # a tool name is not a state


def test_a_stale_or_absent_check_makes_the_surface_unknown():
    old = {"property": "gitlab", "check_name": "up", "interval_s": 60,
           "health": "ok", "observed_at": NOW - timedelta(hours=5)}
    snap = hud.assemble(**_kwargs(checks=[old]))
    assert _tile(snap, "gitlab")["health"] == "unknown"
    assert _tile(snap, "mac")["health"] == "unknown"           # no check at all
    fresh = dict(old, observed_at=NOW - timedelta(seconds=10))
    assert _tile(hud.assemble(**_kwargs(checks=[fresh])), "gitlab")["health"] == "ok"


def test_a_failed_events_read_makes_every_state_unknown():
    snap = hud.assemble(**_kwargs(events=None, errors={"actions_log": "down"}))
    assert all(t["state"] == "unknown" for t in snap["surfaces"]["items"])
    assert "actions_log" in snap["surfaces"]["unread"]


# --------------------------------------------------------------------- tasks

def _task(**over) -> dict:
    row = {"id": 7, "at": NOW - timedelta(minutes=5), "source": "dashboard",
           "route": "cards", "status": "held", "cards": [11], "done_at": None}
    row.update(over)
    return row


def _card(pid=11, minutes=30, **over) -> dict:
    row = {"id": pid, "action": "gitlab.open_issue", "tier": 2,
           "created_at": NOW - timedelta(minutes=5),
           "expires_at": NOW + timedelta(minutes=minutes), "status": "pending"}
    row.update(over)
    return row


def test_a_pending_card_makes_its_task_wait_for_approval():
    snap = hud.assemble(**_kwargs(tasks=[_task()], pending=[_card()]))
    [t] = snap["tasks"]["items"]
    assert t["state"] == "waiting_for_you"
    assert snap["waiting"]["count"] == 1
    assert snap["waiting"]["cards"][0]["id"] == 11


def test_an_expired_card_is_expired_not_run_never_done():
    expired = _card(minutes=-5)
    snap = hud.assemble(**_kwargs(tasks=[_task()], pending=[expired]))
    [t] = snap["tasks"]["items"]
    assert t["state"] == "expired" and t["state"] != "done"
    assert snap["waiting"]["cards"][0]["expired"] is True
    assert snap["waiting"]["count"] == 0       # expired cards are not waiting


def test_a_slack_tier_two_run_is_approved_by_you_in_slack():
    ev = _ev("intent.accepted", "gitlab.open_issue", 1, tier=2, channel="slack")
    snap = hud.assemble(**_kwargs(events=[ev]))
    assert snap["approved_in_slack"][0]["action"] == "gitlab.open_issue"
    assert snap["approved_in_slack"][0]["label"] == "approved by you in Slack"
    tier1 = _ev("intent.accepted", "gitlab.read", 1, tier=1, channel="slack")
    assert hud.assemble(**_kwargs(events=[tier1]))["approved_in_slack"] == []


def test_a_task_shows_source_route_status_time_and_never_its_words():
    row = _task(text=f"deploy with {FAKE}", outcome=f"used {FAKE}")
    snap = hud.assemble(**_kwargs(tasks=[row]))
    [t] = snap["tasks"]["items"]
    assert set(t) >= {"id", "at", "source", "route", "status", "state"}
    assert "text" not in t and "outcome" not in t
    assert FAKE not in json.dumps(snap, default=str)


def test_a_failed_tasks_read_is_none():
    snap = hud.assemble(**_kwargs(tasks=None, errors={"tasks": "down"}))
    assert snap["tasks"]["items"] is None and snap["tasks"]["unread"] == ["tasks"]


# ------------------------------------------------------------ jobs, missions

def test_missions_show_phase_and_a_step_count_only():
    m = {"id": 3, "agent": "sites", "recipe": "agency.sites", "status": "running",
         "phase": "building", "updated_at": NOW, "deadline": None,
         "steps": [{"k": 1}, {"k": 2}], "request": FAKE, "slots": {"k": FAKE}}
    snap = hud.assemble(**_kwargs(missions=[m]))
    [row] = snap["missions"]["items"]
    assert row["phase"] == "building" and row["steps_done"] == 2
    assert FAKE not in json.dumps(snap, default=str)


def test_a_job_running_past_the_lease_is_stalled():
    from atlas.kernel.occurrence import STALE_AFTER
    rows = [{"job": "a", "claimed_at": NOW - STALE_AFTER - timedelta(minutes=1)},
            {"job": "b", "claimed_at": NOW - timedelta(minutes=1)}]
    snap = hud.assemble(**_kwargs(schedule=rows, running=["bg-1"],
                                  mac_seen=NOW - timedelta(seconds=30)))
    jobs = {j["name"]: j for j in snap["jobs"]["running"]}
    assert jobs["a"]["stalled"] is True and jobs["b"]["stalled"] is False
    assert snap["jobs"]["background"] == ["bg-1"]
    assert snap["jobs"]["mac_last_seen"] == (NOW - timedelta(seconds=30)).isoformat()


def test_mac_jobs_show_op_status_and_time_never_params():
    rows = [{"op": "open_app", "status": "queued", "created_at": NOW,
             "params": {"k": FAKE}, "detail": FAKE}]
    snap = hud.assemble(**_kwargs(mac_jobs=rows))
    assert snap["jobs"]["mac"] == [{"op": "open_app", "status": "queued",
                                    "created_at": NOW.isoformat()}]
    assert FAKE not in json.dumps(snap, default=str)


def test_every_section_carries_read_at():
    snap = hud.assemble(**_kwargs())
    for section in ("tasks", "missions", "waiting", "surfaces", "jobs"):
        assert snap[section]["read_at"], section
    assert snap["read_at"]


# ------------------------------------------------------------------ secrets

async def test_a_seeded_secret_never_reaches_the_response(monkeypatch):
    monkeypatch.setenv("FAKE_TOKEN", FAKE)

    async def tasks(*_a, **_k):
        return [_task(text=FAKE, outcome=FAKE)]

    async def pending(*_a, **_k):
        return [_card(reason=FAKE, params={"t": FAKE}, evidence={"t": FAKE})]

    async def mac_jobs(*_a, **_k):
        return [{"op": "x", "status": "queued", "created_at": NOW, "params": {"t": FAKE}}]

    async def events(*_a, **_k):
        return [_ev("execute.start", "mac.x", 1, payload=FAKE)]

    async def empty(*_a, **_k):
        return []

    async def seen(*_a, **_k):
        return None

    monkeypatch.setattr(hud, "_read_tasks", tasks)
    monkeypatch.setattr(hud, "_read_pending", pending)
    monkeypatch.setattr(hud, "_read_mac_jobs", mac_jobs)
    monkeypatch.setattr(hud, "_read_events", events)
    monkeypatch.setattr(hud, "_read_missions", empty)
    monkeypatch.setattr(hud, "_read_schedule", empty)
    monkeypatch.setattr(hud, "_read_checks", empty)
    monkeypatch.setattr(hud, "_read_mac_seen", seen)
    snap = await hud.snapshot(NOW)
    assert FAKE not in json.dumps(snap, default=str)


def test_a_running_job_without_claimed_at_is_unknown_not_a_crash():
    snap = hud.assemble(**_kwargs(schedule=[{"job": "nightly", "claimed_at": None}]))
    row = snap["jobs"]["running"][0]
    assert row["name"] == "nightly"
    assert row["claimed_at"] is None
    assert row["stalled"] is None


def test_unreadable_jobs_are_none_and_named():
    snap = hud.assemble(**_kwargs(mac_jobs=None, schedule=None,
                                  errors={"mac_jobs": "down"}))
    assert snap["jobs"]["mac"] is None
    assert snap["jobs"]["running"] is None
    assert {"mac_jobs", "schedule_runs"} <= set(snap["jobs"]["unread"])
    assert "background" in snap["jobs"] and "mac_last_seen" in snap["jobs"]


def test_a_tool_note_keeps_names_only_and_is_bounded():
    hud._TOOLS_SEEN.clear()
    for i in range(80):
        hud.note_tool("sites", f"gitlab_t{i}", "start")
    assert len(hud._TOOLS_SEEN) <= hud.MAX_TOOLS_SEEN
    last = hud.recent_tools(datetime.now(UTC))[-1]
    assert set(last) == {"agent", "tool", "at"}
    hud._TOOLS_SEEN.clear()
