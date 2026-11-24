"""The Agency map's per-agent activity (`kernel/agent_activity.py`,
`GET /api/agency/activity`).

Proved here:
  * the endpoint needs a session and serves every roster agent;
  * the module is read-only (walked in the AST, as the work board's is);
  * "yesterday" is the previous New York day, DST included;
  * now / yesterday / issues come from the reads, each with its read time;
  * a read that fails is None + `unread`, never 0 (invariant 7);
  * stalled and failed jobs, failing / UNKNOWN / stale checks, failed runs
    and expired cards are issues; a healthy check is not.
"""
from __future__ import annotations

import ast
import json
import pathlib
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from atlas.api import auth
from atlas.kernel import agent_activity
from atlas.kernel import agents as agent_module

SECRET = "s" * 40
PASSWORD = "a-long-enough-password"
NOW = datetime(2026, 10, 28, 16, 0, tzinfo=UTC)          # 12:00 in New York
ROSTER = [("sites", "Felix", "Websites"), ("qa", "Quinn", "QA")]
MODULES = {"sites": {"agency"}, "qa": {"qa"}}


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
    with TestClient(app, client=("127.0.0.1", 51012)) as test_client:
        yield test_client


def test_the_panel_needs_a_session(client):
    assert client.get("/api/agency/activity").status_code == 401


def test_a_signed_in_reader_gets_every_roster_agent(client, monkeypatch):
    agent_module.build_roster()

    async def fails() -> list:
        raise RuntimeError("database is down")

    async def fails_between(_a, _b) -> list:
        raise RuntimeError("database is down")

    async def no_board(_days, _now):
        raise RuntimeError("database is down")

    monkeypatch.setattr(agent_activity, "_read_last_runs", fails)
    monkeypatch.setattr(agent_activity, "_read_checks", fails)
    monkeypatch.setattr(agent_activity, "_read_runs_between", fails_between)
    monkeypatch.setattr(agent_activity, "_read_events_between", fails_between)
    monkeypatch.setattr("atlas.kernel.workboard.board", no_board)
    assert client.post("/api/login", json={"password": PASSWORD}).status_code == 200
    body = client.get("/api/agency/activity").json()
    assert [a["name"] for a in body["agents"]] == list(agent_module.ROSTER)
    assert len(body["agents"]) == 9 and "served_at" in body
    for a in body["agents"]:
        # Nothing could be read: every figure is unknown, never a zero.
        assert a["now"]["waiting"] is None and a["now"]["in_flight"] is None
        assert a["yesterday"]["runs"] is None and a["yesterday"]["actions_ok"] is None
        assert a["issues"]["unread"] and a["now"]["unread"] and a["yesterday"]["unread"]
        for section in ("now", "yesterday", "issues"):
            assert a[section]["read_at"]
    assert "checks" in body["errors"]


def test_the_module_is_read_only():
    root = pathlib.Path(__file__).parent.parent / "atlas" / "kernel"
    tree = ast.parse((root / "agent_activity.py").read_text())
    names = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
    names |= {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)}
    imported = {a.name for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)
                for a in n.names}
    for forbidden in ("execute", "invoke", "think", "run_agent", "warmup",
                      "execute_sql", "run_tier1", "propose", "decide", "fetch_one_write"):
        assert forbidden not in names | imported, forbidden
    source = (root / "agent_activity.py").read_text().upper()
    for verb in ("INSERT ", "UPDATE ", "DELETE ", "TRUNCATE ", "DROP "):
        assert verb not in source, verb


def test_yesterday_is_the_previous_new_york_day_dst_included():
    start, end = agent_activity.yesterday_bounds(NOW)
    assert start == datetime(2026, 10, 27, 4, 0, tzinfo=UTC)
    assert end == datetime(2026, 10, 28, 4, 0, tzinfo=UTC)
    # 2026-11-01 is the fall-back day: 25 hours long.
    start, end = agent_activity.yesterday_bounds(datetime(2026, 11, 2, 15, tzinfo=UTC))
    assert end - start == timedelta(hours=25)


def _board(seats: list[dict]) -> dict:
    return {"agents": seats}


def _seat(name: str, *, waiting=(), in_flight=0, jobs=(), headline="idle") -> dict:
    return {"name": name, "waiting": list(waiting), "counts": {"in_flight": in_flight,
                                                               "waiting": len(waiting)},
            "jobs": list(jobs), "headline": headline}


def _assemble(**over):
    at = NOW - timedelta(seconds=1)
    y_start, y_end = agent_activity.yesterday_bounds(NOW)
    kwargs = {
        "now": NOW, "roster": ROSTER, "modules_of": MODULES,
        "board": _board([_seat("sites"), _seat("qa")]), "board_at": at,
        "last_runs": [], "last_runs_at": at, "y_runs": [], "y_events": [], "y_at": at,
        "recent_runs": [], "recent_events": [], "checks": [], "issues_at": at,
        "y_start": y_start, "y_end": y_end, "errors": {}}
    kwargs.update(over)
    return {a["name"]: a for a in agent_activity.assemble(**kwargs)["agents"]}


def test_empty_reads_are_read_zeros_with_their_times():
    out = _assemble()
    s = out["sites"]
    assert s["now"]["waiting"] == 0 and s["now"]["in_flight"] == 0
    assert s["yesterday"]["runs"] == 0 and s["yesterday"]["actions_ok"] == 0
    assert s["yesterday"]["day"] == "2026-10-27"
    assert s["issues"]["count"] == 0 and s["issues"]["unread"] == []
    assert s["label"] == "Felix"
    assert s["now"]["read_at"] == (NOW - timedelta(seconds=1)).isoformat()


def test_now_shows_the_agents_cards_and_last_run():
    card = {"id": 7, "action": "agency.send_preview", "tier": 2, "reason": "x",
            "created_at": None, "expires_at": "2026-10-29T00:00:00+00:00"}
    out = _assemble(
        board=_board([_seat("sites", waiting=[card], in_flight=2,
                            headline="1 card(s) waiting on Miro"), _seat("qa")]),
        last_runs=[{"agent": "sites", "at": NOW - timedelta(hours=1), "failed": False}])
    now = out["sites"]["now"]
    assert now["waiting"] == 1 and now["in_flight"] == 2
    assert now["cards"] == [{"id": 7, "action": "agency.send_preview", "tier": 2,
                             "expires_at": "2026-10-29T00:00:00+00:00"}]
    assert "reason" not in now["cards"][0]           # no table text on the page
    assert now["last_run"] == {"at": (NOW - timedelta(hours=1)).isoformat(),
                               "failed": False}
    assert out["qa"]["now"]["last_run"] is None and out["qa"]["now"]["last_run_known"]


def test_yesterday_counts_runs_and_the_agents_own_actions():
    y = NOW - timedelta(hours=20)
    ok = json.dumps({"data": {"ok": True}})
    bad = json.dumps({"data": {"ok": False}})
    events = [
        {"event": "execute.finish", "subject": "agency.deploy_site", "payload": ok, "at": y},
        {"event": "execute.finish", "subject": "agency.deploy_site", "payload": bad,
         "at": y - timedelta(hours=1)},
        {"event": "execute.finish", "subject": "qa.audit", "payload": ok, "at": y},
        {"event": "proposal.expired", "subject": "agency.send_preview", "payload": "{}",
         "at": y},
    ]
    out = _assemble(y_runs=[{"agent": "sites", "runs": 5, "failed": 1, "last_at": y,
                             "last_failed_at": y}], y_events=events)
    s = out["sites"]["yesterday"]
    assert (s["runs"], s["failed_runs"], s["actions_ok"], s["actions_failed"]) == (5, 1, 1, 1)
    assert s["cards_expired"] == 1
    assert [a["ok"] for a in s["last_actions"]] == [True, False]
    assert out["qa"]["yesterday"]["actions_ok"] == 1 and out["qa"]["yesterday"]["runs"] == 0


def test_a_failed_read_is_unknown_never_zero():
    out = _assemble(board=None, y_runs=None, y_events=None, checks=None,
                    recent_runs=None, recent_events=None, last_runs=None)
    s = out["sites"]
    assert s["now"]["waiting"] is None and s["now"]["in_flight"] is None
    assert not s["now"]["last_run_known"]
    assert s["yesterday"]["runs"] is None and s["yesterday"]["cards_expired"] is None
    assert set(s["issues"]["unread"]) >= {"agent_runs", "checks", "work board",
                                          "actions_log"}
    assert s["issues"]["count"] == 0          # unknown is said in `unread`, not as OK


def test_issues_name_failed_runs_bad_checks_stalled_jobs_and_expired_cards(monkeypatch):
    from atlas.kernel.occurrence import STALE_AFTER
    from atlas.kernel.registry import REGISTRY
    # The modules are not loaded here, so every row is a check of theirs.
    monkeypatch.setattr(REGISTRY, "modules", {})
    stuck = (NOW - STALE_AFTER - timedelta(minutes=5)).isoformat()
    fresh = (NOW - timedelta(minutes=2)).isoformat()
    jobs = [
        {"name": "agency_sweep", "last_run": {"status": "running", "at": stuck},
         "last_run_known": True, "recorded": True},
        {"name": "agency_digest", "last_run": {"status": "running", "at": fresh},
         "last_run_known": True, "recorded": True},
        {"name": "agency_mail", "last_run": {"status": "failed", "at": fresh},
         "last_run_known": True, "recorded": True},
    ]
    checks = [
        {"property": "agency", "check_name": "leads", "interval_s": 600,
         "health": "failing", "observed_at": NOW - timedelta(minutes=1)},
        {"property": "agency", "check_name": "hosting", "interval_s": 600,
         "health": "ok", "observed_at": NOW - timedelta(minutes=1)},
        {"property": "agency", "check_name": "old", "interval_s": 600,
         "health": "ok", "observed_at": NOW - timedelta(days=2)},
        {"property": "agency", "check_name": "never", "interval_s": 600,
         "health": None, "observed_at": None},
        {"property": "qa", "check_name": "audit", "interval_s": 600,
         "health": "failing", "observed_at": NOW},
    ]
    expired_at = NOW - timedelta(hours=3)
    out = _assemble(
        board=_board([_seat("sites", jobs=jobs), _seat("qa")]),
        recent_runs=[{"agent": "sites", "runs": 4, "failed": 2, "last_at": NOW,
                      "last_failed_at": NOW - timedelta(hours=2)}],
        recent_events=[{"event": "proposal.expired", "subject": "agency.send_preview",
                        "payload": "{}", "at": expired_at}],
        checks=checks)
    kinds = [(i["kind"], i.get("check") or i.get("job") or i.get("action"))
             for i in out["sites"]["issues"]["items"]]
    assert ("runs_failed", None) in kinds
    assert ("check_failing", "leads") in kinds
    assert ("check_unknown", "old") in kinds and ("check_unknown", "never") in kinds
    assert ("job_stalled", "agency_sweep") in kinds
    assert ("job_failed", "agency_mail") in kinds
    assert ("cards_expired", "agency.send_preview") in kinds
    assert ("check_ok", "hosting") not in kinds and ("job_stalled", "agency_digest") not in kinds
    stale = next(i for i in out["sites"]["issues"]["items"] if i.get("check") == "old")
    assert stale["stale"] is True
    # Each agent sees only its own modules' checks.
    assert [i["check"] for i in out["qa"]["issues"]["items"]] == ["audit"]
