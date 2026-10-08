"""System check (15.3). Spoken and drawn from live agent availability and
check health -- and "all clear" is said only when nothing is DEGRADED,
FAILING *or UNKNOWN*. Saying all clear over an unmeasured check is the one
thing this file exists to make impossible."""
from __future__ import annotations

import ast
import pathlib
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from atlas.api import auth, system_check
from atlas.kernel.contracts import Check, Health, Observation
from atlas.kernel.llm import Need
from atlas.kernel.registry import REGISTRY

SECRET = "s" * 40
PASSWORD = "a-long-enough-password"
NOW = datetime(2026, 9, 24, 21, 30, tzinfo=UTC)


async def _probe() -> Observation:                      # never run here
    return Observation("x", Health.UNKNOWN)


def _check(name: str, minutes: int = 15) -> Check:
    return Check(name, _probe, timedelta(minutes=minutes))


class _Module:
    def __init__(self, name: str, title: str) -> None:
        self.name, self.title = name, title


class _Agent:
    def __init__(self, name: str, need: Need | None) -> None:
        self.name, self.title, self.need = name, name.title(), need


@pytest.fixture
def estate(monkeypatch):
    """Two modules, three checks, two agents -- set per test through the
    returned dict of observation rows."""
    monkeypatch.setattr(REGISTRY, "modules", {
        "flycheap": _Module("flycheap", "FlyCheap"),
        "guardian": _Module("guardian", "Guardian"),
    })
    monkeypatch.setattr(REGISTRY, "checks", {
        "flycheap.health": _check("health"),
        "flycheap.signups": _check("signups", 60),
        "guardian.api": _check("api"),
    })
    monkeypatch.setattr(REGISTRY, "disabled", {})
    monkeypatch.setattr("atlas.kernel.agents.ROSTER", {
        "concierge": _Agent("concierge", Need.REASON),
        "flycheap": _Agent("flycheap", Need.REASON),
    })
    monkeypatch.setattr("atlas.kernel.llm.ROUTES", {Need.REASON: [object()]})
    monkeypatch.setattr(system_check, "_now", lambda: NOW)

    rows: list[dict] = []

    async def fake_fetch_all(sql, *args):
        assert "FROM checks" in sql
        return rows

    monkeypatch.setattr(system_check, "fetch_all", fake_fetch_all)
    return rows


def _row(module: str, check: str, health: str | None, minutes_ago: int = 5) -> dict:
    return {"property": module, "check_name": check, "health": health,
            "observed_at": NOW - timedelta(minutes=minutes_ago) if health else None}


def _all_ok(rows: list[dict]) -> None:
    rows[:] = [_row("flycheap", "health", "ok"), _row("flycheap", "signups", "ok"),
               _row("guardian", "api", "ok")]


# ------------------------------------------------------------------ all clear

async def test_all_ok_says_all_clear_and_the_counts_match(estate):
    _all_ok(estate)
    built = await system_check.build()
    assert built["summary"] == {"ok": 3, "degraded": 0, "failing": 0,
                                "unknown": 0, "total": 3, "not_loaded": 0}
    assert built["line"].startswith("All clear. 3 of 3 checks passed.")
    # Invariant 6: the spoken figure carries the time it was read.
    assert f"oldest reading {system_check._clock(NOW - timedelta(minutes=5))}" in (
        built["line"].lower())


async def test_one_unknown_check_never_says_all_clear(estate):
    _all_ok(estate)
    estate[2] = _row("guardian", "api", "unknown")
    built = await system_check.build()
    assert "all clear" not in built["line"].lower()
    assert built["summary"]["unknown"] == 1
    # Everything measured is fine, and it says so -- then names what is not.
    assert built["line"].startswith(
        "Everything I can measure is fine: 2 of 3 checks passed.")
    assert "Not measured: Guardian (check could not run)." in built["line"]


async def test_a_check_never_observed_is_unknown_not_ok(estate):
    _all_ok(estate)
    del estate[1]                                       # signups never ran
    built = await system_check.build()
    assert "all clear" not in built["line"].lower()
    assert built["summary"]["unknown"] == 1
    assert "Not measured: FlyCheap (never read)." in built["line"]


async def test_an_unrecognised_health_word_is_unknown(estate):
    _all_ok(estate)
    estate[0] = _row("flycheap", "health", "GREAT")
    built = await system_check.build()
    assert built["summary"]["unknown"] == 1
    assert "all clear" not in built["line"].lower()


async def test_a_stale_ok_is_not_a_current_ok(estate):
    """An OK read three intervals ago is what a dead scheduler looks like."""
    _all_ok(estate)
    estate[0] = _row("flycheap", "health", "ok", minutes_ago=15 * 3 + 11)
    built = await system_check.build()
    assert built["summary"]["unknown"] == 1
    assert "all clear" not in built["line"].lower()
    last = system_check._clock(NOW - timedelta(minutes=56))
    assert f"FlyCheap (last read {last})" in built["line"]


async def test_degraded_and_failing_need_attention(estate):
    _all_ok(estate)
    estate[0] = _row("flycheap", "health", "failing")
    estate[2] = _row("guardian", "api", "degraded")
    built = await system_check.build()
    assert built["summary"]["failing"] == 1
    assert built["summary"]["degraded"] == 1
    assert "1 of 3 checks passed; 2 need attention" in built["line"]
    assert "Not measured" not in built["line"]
    assert "Everything I can measure is fine" not in built["line"]
    assert "all clear" not in built["line"].lower()


async def test_a_disabled_module_blocks_all_clear(estate, monkeypatch):
    """A module that could not load is an unmeasured module (Gate 15)."""
    _all_ok(estate)
    monkeypatch.setattr(REGISTRY, "disabled", {
        "calendar": "missing env: GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET",
        "gitlab": "missing env: GITLAB_TOKEN",
    })
    built = await system_check.build()
    assert "all clear" not in built["line"].lower()
    s = built["summary"]
    # M counts registered checks only: a module that did not load has none,
    # so it is named, never added to the count.
    assert s["ok"] + s["degraded"] + s["failing"] + s["unknown"] == s["total"] == 3
    assert s["not_loaded"] == 2
    cal = next(m for m in built["modules"] if m["name"] == "calendar")
    assert cal["health"] == "unknown" and cal["enabled"] is False
    assert cal["checks_total"] == 0
    assert built["line"].startswith(
        "Everything I can measure is fine: 3 of 3 checks passed. "
        "Not measured: calendar (no GOOGLE_ key), gitlab (no GITLAB_TOKEN).")


async def test_nothing_measured_is_never_all_clear(estate, monkeypatch):
    monkeypatch.setattr(REGISTRY, "checks", {})
    monkeypatch.setattr(REGISTRY, "modules", {})
    built = await system_check.build()
    assert built["summary"]["total"] == 0
    assert "all clear" not in built["line"].lower()


async def test_only_disabled_modules_is_nothing_measured_and_named(estate, monkeypatch):
    monkeypatch.setattr(REGISTRY, "checks", {})
    monkeypatch.setattr(REGISTRY, "modules", {})
    monkeypatch.setattr(REGISTRY, "disabled", {"gitlab": "missing env: GITLAB_TOKEN"})
    built = await system_check.build()
    assert built["summary"]["total"] == 0 and built["summary"]["not_loaded"] == 1
    assert built["line"].startswith("Nothing is being measured")
    assert "Not measured: gitlab (no GITLAB_TOKEN)." in built["line"]


async def test_an_unreachable_record_store_is_unknown_not_clear(estate, monkeypatch):
    async def down(*_a, **_k):
        raise ConnectionError("postgres refused the connection")

    monkeypatch.setattr(system_check, "fetch_all", down)
    built = await system_check.build()
    assert built["summary"]["unknown"] == built["summary"]["total"] == 3
    assert "all clear" not in built["line"].lower()
    assert built["unreadable"]
    assert built["line"].startswith("Nothing could be measured: 0 of 3 checks passed.")
    # One cause, said once -- not once per module.
    assert built["line"].count("record store") == 1
    assert "The record store is unreachable" in built["line"]
    # Nothing had a reading, so the line carries when the check was made.
    assert f"Checked at {system_check._clock(NOW)}." in built["line"]


async def test_an_offline_agent_is_not_all_clear(estate, monkeypatch):
    _all_ok(estate)
    monkeypatch.setattr("atlas.kernel.agents.ROSTER", {
        "concierge": _Agent("concierge", Need.REASON),
        "school": _Agent("school", Need.PRIVATE),
    })
    built = await system_check.build()
    online = {a["name"]: a["online"] for a in built["agents"]}
    assert online == {"concierge": True, "school": False}
    assert "all clear" not in built["line"].lower()
    # A stated item, in the same shape -- not a silent gate.
    assert built["line"].startswith(
        "Everything I can measure is fine: 3 of 3 checks passed; "
        "the school agent is offline (no local model).")
    school = next(a for a in built["agents"] if a["name"] == "school")
    assert school["reason"] == "no local model"


async def test_modules_carry_their_counts(estate):
    _all_ok(estate)
    estate[1] = _row("flycheap", "signups", "unknown")
    built = await system_check.build()
    fly = next(m for m in built["modules"] if m["name"] == "flycheap")
    assert fly == {**fly, "title": "FlyCheap", "health": "unknown",
                   "checks_ok": 1, "checks_total": 2, "unknown": 1}


# ------------------------------------------------------------------- the route

@pytest.fixture
def client(monkeypatch, memory_backend):
    monkeypatch.setenv("SESSION_SECRET", SECRET)
    monkeypatch.setenv("DASHBOARD_PASSWORD_HASH", auth.hash_password(PASSWORD))
    monkeypatch.setenv("MESH_ALLOWED_CIDRS", "127.0.0.0/8")
    auth._attempts.clear()
    from atlas.api.main import app

    async def no_boot() -> None:
        return None
    monkeypatch.setattr("atlas.api.main.boot", no_boot)
    monkeypatch.setattr("atlas.api.main.scheduler.loop", no_boot)

    with TestClient(app, client=("127.0.0.1", 52001)) as c:
        c.post("/api/login", json={"password": PASSWORD})
        yield c


async def test_the_route_serves_the_check(client, estate):
    _all_ok(estate)
    body = client.get("/api/system/check").json()
    assert body["as_of"]
    assert body["line"].startswith("All clear.")
    assert {a["name"] for a in body["agents"]} == {"concierge", "flycheap"}


async def test_the_route_requires_a_session(memory_backend, monkeypatch):
    monkeypatch.setenv("SESSION_SECRET", SECRET)
    monkeypatch.setenv("MESH_ALLOWED_CIDRS", "127.0.0.0/8")
    from atlas.api.main import app

    async def no_boot() -> None:
        return None
    monkeypatch.setattr("atlas.api.main.boot", no_boot)
    monkeypatch.setattr("atlas.api.main.scheduler.loop", no_boot)
    with TestClient(app, client=("127.0.0.1", 52002)) as c:
        assert c.get("/api/system/check").status_code in (401, 403)


def test_the_system_check_cannot_reach_the_executor():
    path = pathlib.Path(system_check.__file__)
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            where = getattr(node, "module", "") or ""
            names = " ".join(a.name for a in node.names)
            assert "executor" not in f"{where} {names}"


async def test_an_unknown_checks_own_detail_says_why(estate):
    _all_ok(estate)
    estate[2] = {**_row("guardian", "api", "unknown"),
                 "detail": "GUARDIAN_TOKEN not set — read-scope token needed"}
    built = await system_check.build()
    assert "Not measured: Guardian (GUARDIAN_TOKEN not set — read-scope token needed)." \
        in built["line"]


def test_the_disabled_reason_names_what_is_missing_never_a_value():
    reason = system_check._disabled_reason
    assert reason("missing env: GITLAB_TOKEN") == "no GITLAB_TOKEN"
    assert reason("missing env: GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET, "
                  "GOOGLE_REFRESH_TOKEN") == "no GOOGLE_ key"
    assert reason("missing env: A_KEY, B_KEY") == "no A_KEY or B_KEY"
    assert reason("import failed: boom at line 3") == "failed to load"


def test_effective_health_is_the_one_staleness_rule():
    interval = timedelta(minutes=15)
    fresh = NOW - timedelta(minutes=5)
    stale = NOW - timedelta(minutes=56)
    rule = system_check.effective_health
    assert rule("ok", fresh, interval, NOW) == ("ok", False)
    assert rule("OK", fresh, interval, NOW) == ("ok", False)
    assert rule("ok", stale, interval, NOW) == ("unknown", True)
    assert rule("failing", stale, interval, NOW) == ("unknown", True)
    assert rule("ok", None, interval, NOW) == ("unknown", False)
    assert rule("great", fresh, interval, NOW) == ("unknown", False)
    assert rule("ok", stale, None, NOW) == ("ok", False)   # no interval known
