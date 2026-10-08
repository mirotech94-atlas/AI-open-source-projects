"""The dashboard's HTTP surface.

Two things are being proved here: the approval endpoints revalidate everything
server-side, and the dashboard is unreachable without a session. The UI
disabling a button is not enforcement.
"""
from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient

from atlas.api import auth
from atlas.kernel import approvals
from atlas.kernel.contracts import ActionResult

SECRET = "s" * 40
PASSWORD = "a-long-enough-password"


@pytest.fixture
def client(monkeypatch, memory_backend):
    monkeypatch.setenv("SESSION_SECRET", SECRET)
    monkeypatch.setenv("DASHBOARD_PASSWORD_HASH", auth.hash_password(PASSWORD))
    monkeypatch.setenv("ATLAS_WATCH_ONLY", "false")
    monkeypatch.setenv("MESH_ALLOWED_CIDRS", "127.0.0.0/8,0.0.0.0/0")
    auth._attempts.clear()

    from atlas.api.main import app

    # The scheduler and module discovery are not what these tests are about.
    async def no_boot() -> None:
        return None

    monkeypatch.setattr("atlas.api.main.boot", no_boot)
    monkeypatch.setattr("atlas.api.main.scheduler.loop", no_boot)

    # TestClient's default client host is the string "testclient", which the
    # mesh guard correctly refuses as an unparseable source address. Give it a
    # real one so these tests exercise the handlers rather than the guard.
    with TestClient(app, client=("127.0.0.1", 51000)) as test_client:
        yield test_client


@pytest.fixture
def signed_in(client):
    response = client.post("/api/login", json={"password": PASSWORD})
    assert response.status_code == 200, response.text
    return client


@pytest.fixture
def executed(monkeypatch):
    calls: list = []

    async def fake_execute(req):
        calls.append(req)
        return ActionResult(True, "did the thing")

    monkeypatch.setattr(approvals, "execute", fake_execute)
    return calls


def claim_once(backend, *, status="pending", expires_hours=1.0):
    """Model the conditional UPDATE: it matches at most once."""
    state = {"claimed": False}

    async def fetchrow(sql, *args):
        flat = " ".join(sql.split())
        if flat.startswith("UPDATE proposals SET status="):
            if state["claimed"] or status != "pending" or expires_hours <= 0:
                return None
            state["claimed"] = True
            return {"action": "flycheap.regenerate_links", "params": "{}",
                    "reason": "links are 404ing", "incident_id": None}
        if flat.startswith("SELECT status, expires_at FROM proposals"):
            return {"status": "approved" if state["claimed"] else status,
                    "expires_at": datetime.now(UTC)
                    + timedelta(hours=expires_hours)}
        raise NotImplementedError(flat)

    backend.fetchrow = fetchrow
    return state


# ------------------------------------------------------------------ auth

def test_the_dashboard_api_requires_a_session(client):
    for path in ("/api/overview", "/api/approvals", "/api/audit",
                 "/api/findings", "/api/brief"):
        assert client.get(path).status_code == 401, path




def test_the_wrong_password_does_not_sign_you_in(client):
    assert client.post("/api/login", json={"password": "nope"}).status_code == 401
    assert client.get("/api/overview").status_code == 401


def test_signing_in_then_out(client):
    assert client.post("/api/login", json={"password": PASSWORD}).status_code == 200
    assert client.get("/api/session").json()["authenticated"] is True
    client.post("/api/logout")
    assert client.get("/api/session").json()["authenticated"] is False


# ------------------------------------------------- the required endpoints

















# ------------------------------------------------------------- envelopes

def test_every_response_carries_the_time_it_was_read(signed_in, memory_backend):
    memory_backend.responses["FROM actions_log"] = []
    body = signed_in.get("/api/audit").json()
    assert "as_of" in body
    datetime.fromisoformat(body["as_of"])          # parses as a real timestamp


def test_verify_chain_is_exposed(signed_in, memory_backend):
    body = signed_in.get("/api/audit/verify").json()
    assert body["ok"] is True
    assert "as_of" in body


# ------------------------------------------------------- routing sanity

def test_the_spa_catch_all_does_not_shadow_the_api(signed_in):
    """The SPA fallback is registered last; /api must never fall through to it."""
    response = signed_in.get("/api/does-not-exist")
    assert response.status_code == 404
    assert "<!doctype html" not in response.text.lower()


def test_the_public_health_endpoint_needs_no_session(client):
    assert client.get("/healthz").status_code == 200


# --------------------------------------------------------- the mesh guard

def test_an_unparseable_source_address_is_refused(monkeypatch, memory_backend):
    """The guard is not decoration: an address it cannot parse is refused."""
    monkeypatch.setenv("SESSION_SECRET", SECRET)
    monkeypatch.setenv("MESH_ALLOWED_CIDRS", "127.0.0.0/8")
    from atlas.api.main import app

    async def no_boot() -> None:
        return None
    monkeypatch.setattr("atlas.api.main.boot", no_boot)
    monkeypatch.setattr("atlas.api.main.scheduler.loop", no_boot)

    with TestClient(app) as anonymous:          # client host is "testclient"
        assert anonymous.get("/api/session").status_code == 404


def test_an_off_mesh_address_is_refused(monkeypatch, memory_backend):
    monkeypatch.setenv("SESSION_SECRET", SECRET)
    monkeypatch.setenv("MESH_ALLOWED_CIDRS", "100.64.0.0/10")
    from atlas.api.main import app

    async def no_boot() -> None:
        return None
    monkeypatch.setattr("atlas.api.main.boot", no_boot)
    monkeypatch.setattr("atlas.api.main.scheduler.loop", no_boot)

    with TestClient(app, client=("203.0.113.9", 4000)) as outsider:
        assert outsider.get("/api/session").status_code == 404
        # The public health endpoint is still reachable — it is in PUBLIC_PATHS.
        assert outsider.get("/healthz").status_code == 200






# ----------------------------------- the forwarded address is only trusted
#                                     when it comes from our own proxy

def _app(monkeypatch, cidrs="100.64.0.0/10,172.16.0.0/12"):
    monkeypatch.setenv("SESSION_SECRET", SECRET)
    monkeypatch.setenv("MESH_ALLOWED_CIDRS", cidrs)
    from atlas.api.main import app

    async def no_boot() -> None:
        return None
    monkeypatch.setattr("atlas.api.main.boot", no_boot)
    monkeypatch.setattr("atlas.api.main.scheduler.loop", no_boot)
    return app


def test_a_mesh_client_proxied_by_caddy_is_allowed(monkeypatch, memory_backend):
    """Caddy sits on the Docker bridge and forwards the real mesh address."""
    with TestClient(_app(monkeypatch), client=("172.18.0.5", 40000)) as c:
        response = c.get("/api/session",
                         headers={"X-Forwarded-For": "100.84.1.7"})
        assert response.status_code == 200


def test_a_public_client_cannot_forge_a_mesh_address(monkeypatch, memory_backend):
    """The header is ignored unless the immediate peer is our own proxy.

    Without that restriction, anyone reaching the API directly could claim to
    be on the mesh by setting one header.
    """
    with TestClient(_app(monkeypatch), client=("203.0.113.9", 40000)) as c:
        response = c.get("/api/session",
                         headers={"X-Forwarded-For": "100.84.1.7"})
        assert response.status_code == 404


def test_a_forwarded_public_address_is_still_refused(monkeypatch, memory_backend):
    """Caddy forwards honestly, so a public client proxied through it is
    refused by the API too — two independent checks, not one."""
    with TestClient(_app(monkeypatch), client=("172.18.0.5", 40000)) as c:
        response = c.get("/api/session",
                         headers={"X-Forwarded-For": "203.0.113.9"})
        assert response.status_code == 404


def test_the_public_health_path_is_unaffected_by_forwarding(
        monkeypatch, memory_backend):
    with TestClient(_app(monkeypatch), client=("203.0.113.9", 40000)) as c:
        assert c.get("/healthz").status_code == 200


def test_a_chain_of_forwarded_addresses_uses_the_originator(
        monkeypatch, memory_backend):
    """X-Forwarded-For is a list; the first entry is the original client."""
    with TestClient(_app(monkeypatch), client=("172.18.0.5", 40000)) as c:
        assert c.get("/api/session",
                     headers={"X-Forwarded-For": "203.0.113.9, 100.84.1.7"}
                     ).status_code == 404


# ------------------------------------------------- the mirror has no writes

def test_the_dashboard_exposes_no_write_endpoint(client):
    """ATLAS asks for decisions by text and they are answered by text.

    If a write endpoint ever reappears here, there is a path from a browser to
    the executor — and this test is what makes adding one deliberate.

    `/api/agents/warm` (Task 59) is the one deliberate exception: it never
    calls `invoke` or the executor, only `agents.warmup`, which runs an agent
    with its tool list forced empty (see `test_agent_warmup.py`) and writes
    `agent_runs`, nothing else. `test_no_route_anywhere_can_reach_the_executor`
    below is what actually enforces that, by walking the AST rather than
    trusting a comment.
    """
    from atlas.api.dashboard import router

    writes = [
        (route.path, sorted(route.methods))
        for route in router.routes
        if getattr(route, "methods", None)
        and {"POST", "PUT", "PATCH", "DELETE"} & set(route.methods)
    ]
    assert sorted(path for path, _ in writes) == [
        "/api/agents/warm", "/api/login", "/api/logout"], writes


def test_no_route_anywhere_can_reach_the_executor():
    """The property that actually matters.

    Voice needs POSTs — audio has to be uploaded somehow — so counting POSTs is
    no longer the test. What must stay true is that nothing reachable from a
    browser calls execute(), at any depth.

    `kernel.unattended.run_tier1` is the one door an API surface may use to
    reach the executor without a person — the screening webhook runs a Tier 1
    booking while a stranger holds the line. It is a door, not a loophole, so
    it is named here too: any other file under atlas/api calling it fails this
    test, the same way calling execute() directly does.
    """
    import ast
    import pathlib

    api_dir = pathlib.Path(__file__).parent.parent / "atlas" / "api"
    offenders: list[str] = []
    for path in api_dir.rglob("*.py"):
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                name = getattr(node.func, "id", None) or getattr(
                    node.func, "attr", None)
                if name in ("execute", "rollback"):
                    # execute_sql is not the executor.
                    offenders.append(f"{path.name}:{node.lineno} {name}()")
                elif name == "run_tier1" and path.name not in (
                        "screening.py", "clips.py", "webild.py", "lead_call.py"):
                    # lead_call.py: the calendar hold a lead picks (2026-09-30).
                    # clips.py: the Mac agent's Whop record (2026-09-29).
                    # webild.py: its Webild site record (2026-09-29).
                    offenders.append(f"{path.name}:{node.lineno} {name}()")
                elif name in ("run_mac_action", "run_spoken_commands",
                              "run_chain") and path.name != "voice.py":
                    # kernel.spoken: Mac actions said on the signed-in
                    # dashboard (Miro, 2026-09-28) and from the menu-bar
                    # hotkey (2026-10-06). Voice only.
                    offenders.append(f"{path.name}:{node.lineno} {name}()")
                elif name == "run_chain":
                    # voice.py goes through run_spoken_commands, which
                    # checks the whole plan first; never the bare runner.
                    offenders.append(f"{path.name}:{node.lineno} {name}()")
    assert offenders == [], f"an API module calls the executor: {offenders}"


def test_the_old_approval_routes_are_gone(signed_in, executed):
    for path in ("/api/approvals/1/approve", "/api/approvals/1/deny",
                 "/api/approvals/1/why", "/api/approvals/1/decline"):
        status = signed_in.post(path).status_code
        assert status in (404, 405), f"{path} -> {status}"
    assert executed == []


# -------------------------------------------- module health rolls up correctly

@pytest.mark.parametrize("healths,expected", [
    (["ok"], "ok"),
    (["ok", "ok"], "ok"),
    (["ok", "degraded"], "degraded"),
    (["ok", "unknown"], "unknown"),
    (["unknown", "failing"], "failing"),
    (["degraded", "failing"], "failing"),
    (["unknown"], "unknown"),
    ([], "unknown"),            # nothing measured
])
def test_a_module_tile_takes_the_worst_state_it_actually_saw(
        signed_in, memory_backend, healths, expected):
    """A module whose every check passes must read OK.

    The rank table started each tile at 'unknown', which outranks 'ok', so a
    perfectly healthy module rendered as unmeasured — and an unmeasured one is
    the thing this interface exists to make visible.
    """
    from datetime import datetime
    now = datetime.now(UTC)
    rows = [{"property": "vpsie", "check_name": f"c{i}", "last_run_at": now,
             "next_run_at": now, "interval_s": 300, "health": h,
             "detail": "", "observed_at": now}
            for i, h in enumerate(healths)]

    async def fetch(sql, *args):
        flat = " ".join(sql.split())
        if "FROM checks c" in flat:
            return rows
        return []

    async def fetchrow(sql, *args):
        return {"n": 0}

    memory_backend.fetch = fetch
    memory_backend.fetchrow = fetchrow

    body = signed_in.get("/api/overview").json()
    tile = next((m for m in body["modules"] if m["module"] == "vpsie"), None)
    if not healths:
        assert tile is None or tile["health"] == "unknown"
    else:
        assert tile is not None
        assert tile["health"] == expected, f"{healths} -> {tile['health']}"


def test_a_stale_ok_is_unknown_on_the_board_as_it_is_spoken(
        signed_in, memory_backend):
    """The same staleness rule the system check speaks by: an OK read three
    intervals (plus ten minutes) ago is unknown on the tile too, so the
    screen and the voice never disagree."""
    from datetime import datetime, timedelta

    from atlas.api import system_check
    now = datetime.now(UTC)
    rows = [
        {"property": "vpsie", "check_name": "fresh", "last_run_at": now,
         "next_run_at": now, "interval_s": 300, "health": "ok", "detail": "",
         "observed_at": now - timedelta(minutes=2)},
        {"property": "vpsie", "check_name": "old", "last_run_at": now,
         "next_run_at": now, "interval_s": 300, "health": "ok", "detail": "",
         "observed_at": now - timedelta(minutes=26)},
    ]

    async def fetch(sql, *args):
        return rows if "FROM checks c" in " ".join(sql.split()) else []

    async def fetchrow(sql, *args):
        return {"n": 0}

    memory_backend.fetch = fetch
    memory_backend.fetchrow = fetchrow
    body = signed_in.get("/api/overview").json()
    tile = next(m for m in body["modules"] if m["module"] == "vpsie")
    checks = {c["name"]: c for c in tile["checks"]}
    assert checks["fresh"]["health"] == "ok" and checks["fresh"]["stale"] is False
    assert checks["old"]["health"] == "unknown" and checks["old"]["stale"] is True
    assert tile["health"] == "unknown"
    # One helper, not two copies of the rule.
    assert system_check.effective_health(
        "ok", rows[1]["observed_at"], timedelta(seconds=300), now) == ("unknown", True)


def test_every_check_carries_its_reading_number_unit_and_read_time(
        signed_in, memory_backend):
    """The overview adds each reading's own value and unit (and its health
    and read time) to every check, without removing anything: a number is
    the check's own, null when it wrote none, never a zero -- and an
    UNKNOWN reading's value is still sent, with the health that says it is
    not a measurement of now."""
    now = datetime.now(UTC)
    rows = [
        {"property": "shop", "check_name": "sales_7d", "last_run_at": now,
         "next_run_at": now, "interval_s": 21600, "health": "ok",
         "detail": "A: 2 sale(s), $38.00 (last 7 days)", "observed_at": now,
         "value": 38.0, "unit": "usd"},
        {"property": "shop", "check_name": "sales_count_7d", "last_run_at": now,
         "next_run_at": now, "interval_s": 21600, "health": "unknown",
         "detail": "Stripe answered 401", "observed_at": now,
         "value": None, "unit": None},
        {"property": "shop", "check_name": "odd", "last_run_at": now,
         "next_run_at": now, "interval_s": 21600, "health": "ok",
         "detail": "", "observed_at": now, "value": float("nan"), "unit": ""},
        # A row from a backend that never selected the new columns.
        {"property": "shop", "check_name": "old", "last_run_at": now,
         "next_run_at": now, "interval_s": 21600, "health": "ok",
         "detail": "", "observed_at": None},
    ]
    seen: list[str] = []

    async def fetch(sql, *args):
        flat = " ".join(sql.split())
        if "FROM checks c" in flat:
            seen.append(flat)
            return rows
        return []

    async def fetchrow(sql, *args):
        return {"n": 0}

    memory_backend.fetch = fetch
    memory_backend.fetchrow = fetchrow
    body = signed_in.get("/api/overview").json()
    assert "o.value, o.unit" in seen[0]
    tile = next(m for m in body["modules"] if m["module"] == "shop")
    checks = {c["name"]: c for c in tile["checks"]}
    sales = checks["sales_7d"]
    assert (sales["value"], sales["unit"], sales["health"]) == (38.0, "usd", "ok")
    assert sales["read_at"] == sales["observed_at"] == now.isoformat()
    # Everything the page already read is still there.
    assert {"name", "health", "state", "stale", "detail", "observed_at",
            "next_run_at", "interval_s"} <= set(sales)
    assert checks["sales_count_7d"]["value"] is None
    assert checks["sales_count_7d"]["health"] == "unknown"
    assert checks["odd"]["value"] is None and checks["odd"]["unit"] is None
    assert checks["old"]["value"] is None and checks["old"]["read_at"] is None


def test_status_counts_approved_cards_as_executing(signed_in, memory_backend):
    """The Command Center's "Executing" is the approved cards the executor
    holds right now, counted on this read; null when it could not be read,
    never a zero."""
    async def fetchrow(sql, *args):
        flat = " ".join(sql.split())
        if "status = 'approved'" in flat:
            return {"n": 2}
        return {"n": 0}

    memory_backend.fetchrow = fetchrow
    body = signed_in.get("/api/status").json()
    assert body["executing"] == 2
    assert body["watch_only"] is False

    async def missing(sql, *args):
        return None if "status = 'approved'" in sql else {"n": 0}

    memory_backend.fetchrow = missing
    assert signed_in.get("/api/status").json()["executing"] is None


def test_check_history_needs_a_session(client):
    assert client.get("/api/checks/shop/sales_7d/history").status_code == 401
    assert client.get("/api/health/history").status_code == 401


@pytest.mark.parametrize("days", ["0", "31", "-1", "week"])
def test_check_history_refuses_a_window_outside_a_day_to_a_month(signed_in, days):
    got = signed_in.get(f"/api/checks/shop/sales_7d/history?days={days}")
    assert got.status_code == 422


@pytest.mark.parametrize("days", ["0", "15"])
def test_health_history_refuses_a_window_outside_a_day_to_two_weeks(signed_in, days):
    assert signed_in.get(f"/api/health/history?days={days}").status_code == 422


def test_an_unknown_check_has_no_history_rather_than_an_empty_one(
        signed_in, memory_backend):
    memory_backend.responses["FROM checks c JOIN properties p"] = None
    got = signed_in.get("/api/checks/shop/nope/history")
    assert got.status_code == 404


def test_check_history_is_the_stored_readings_oldest_first_and_nothing_else(
        signed_in, memory_backend):
    """Each stored observation once, as recorded, oldest first: no point is
    made up between two readings, an UNKNOWN reading stays UNKNOWN with the
    value it carried, and a missing value is null -- never zero. The names
    in the path reach the SQL as parameters, never as text."""
    t0 = datetime(2026, 10, 1, 6, 0, tzinfo=UTC)
    stored = [
        {"at": t0, "value": 38.0, "unit": "usd", "health": "ok"},
        {"at": t0 + timedelta(hours=6), "value": None, "unit": None,
         "health": "unknown"},
        {"at": t0 + timedelta(hours=30), "value": 57.0, "unit": "usd",
         "health": "OK "},
    ]
    memory_backend.responses["FROM checks c JOIN properties p"] = {
        "id": 41, "interval_s": 21600}
    memory_backend.responses["FROM observations WHERE check_id = $1"] = stored

    body = signed_in.get(
        "/api/checks/shop/sales'7d/history?days=3").json()
    assert body["days"] == 3 and body["interval_s"] == 21600
    assert body["stale_after_s"] == 21600 * 3 + 600
    assert body["truncated"] is False
    assert [p["read_at"] for p in body["points"]] == [r["at"].isoformat() for r in stored]
    assert [p["value"] for p in body["points"]] == [38.0, None, 57.0]
    assert [p["health"] for p in body["points"]] == ["ok", "unknown", "ok"]
    assert set(body["points"][0]) == {"read_at", "value", "unit", "health"}
    lookup = next(a for sql, a in memory_backend.sql_log if "FROM checks c" in sql)
    assert lookup == ("shop", "sales'7d")
    series = next((sql, a) for sql, a in memory_backend.sql_log
                  if "FROM observations" in sql)
    assert "ORDER BY at ASC" in series[0] and series[1][:2] == (41, 3)
    assert "sales" not in series[0]


def _health_rows(t, **over):
    """One sample's rows: shop (one OK check), flycheap (a stale OK, so
    unknown), calendar (not set up, so set aside)."""
    return [
        {"t": t, "property": "shop", "check_name": "sales_7d", "interval_s": 3600,
         "health": "ok", "detail": "fine", "observed_at": t - timedelta(minutes=5),
         **over},
        {"t": t, "property": "flycheap", "check_name": "prices", "interval_s": 300,
         "health": "ok", "detail": "", "observed_at": t - timedelta(hours=2)},
        {"t": t, "property": "calendar", "check_name": "conflicts",
         "interval_s": 300, "health": "unknown",
         "detail": "GOOGLE_REFRESH_TOKEN not set", "observed_at": t},
    ]


def test_health_history_replays_the_strips_rule_at_each_hour(
        signed_in, memory_backend, monkeypatch):
    """Counts per hour by the overview's own rule: staleness at THAT hour,
    a not-set-up module set aside, a module with no reading unknown, and a
    module that is no longer registered not counted at all."""
    from atlas.kernel.registry import REGISTRY

    monkeypatch.setattr(REGISTRY, "modules", {
        "shop": object(), "flycheap": object(), "calendar": object()})
    monkeypatch.setattr(REGISTRY, "checks", {
        "shop.sales_7d": object(), "flycheap.prices": object(),
        "calendar.conflicts": object()})
    t1 = datetime(2026, 10, 6, 7, 0, tzinfo=UTC)
    t2 = t1 + timedelta(hours=1)
    rows = (_health_rows(t1)
            + _health_rows(t2, health=None, observed_at=None)
            + [{"t": t2, "property": "gone", "check_name": "x", "interval_s": 60,
                "health": "failing", "detail": "", "observed_at": t2}])
    memory_backend.responses["FROM generate_series"] = rows
    body = signed_in.get("/api/health/history?days=2").json()
    assert body["step_hours"] == 1
    first, second = body["points"]
    assert first["at"] == t1.isoformat()
    assert first["counts"] == {"ok": 1, "degraded": 0, "failing": 0, "unknown": 1}
    assert first["set_aside"] == 1
    # No reading at all at that hour is unknown, never OK; an unregistered
    # module is not counted.
    assert second["counts"] == {"ok": 0, "degraded": 0, "failing": 0, "unknown": 2}
    args = next(a for s, a in memory_backend.sql_log if "generate_series" in s)
    assert args[2] == 1 and args[1] - args[0] == timedelta(days=2)


def test_the_health_fold_is_the_overviews_fold():
    from atlas.api.dashboard import _fold_module
    assert _fold_module([("ok", None), ("degraded", None)]) == ("degraded", False)
    assert _fold_module([("ok", None), ("unknown", {"kind": "paused", "label": "p"})]) \
        == ("ok", False)
    assert _fold_module([("unknown", {"kind": "not_set_up", "label": "n"})]) \
        == ("unknown", True)
    assert _fold_module([("unknown", None), ("ok", None)]) == ("unknown", False)


def test_the_module_detail_badges_the_current_health_not_the_raw_one(
        signed_in, memory_backend, monkeypatch):
    """The focus card reads `current`: a stale OK is Unknown there too."""
    from datetime import datetime, timedelta

    from atlas.kernel.contracts import Check, Health, Observation
    from atlas.kernel.registry import REGISTRY

    async def probe() -> Observation:
        return Observation("x", Health.UNKNOWN)

    monkeypatch.setattr(REGISTRY, "modules", {"vpsie": type("M", (), {
        "title": "VPSie", "brief": lambda self: ""})()})
    monkeypatch.setattr(REGISTRY, "checks", {
        "vpsie.api": Check("api", probe, timedelta(minutes=5)),
        "vpsie.db": Check("db", probe, timedelta(minutes=5)),
    })
    now = datetime.now(UTC)

    async def fetch(sql, *args):
        if args and args[1] == "api":
            return [{"at": now - timedelta(minutes=26), "value": 1.0, "health": "ok"}]
        return [{"at": now - timedelta(minutes=1), "value": 2.0, "health": "ok"}]

    memory_backend.fetch = fetch
    body = signed_in.get("/api/modules/vpsie").json()
    current = {c["name"]: c["current"] for c in body["checks"]}
    assert current["api"]["health"] == "unknown" and current["api"]["stale"] is True
    assert current["db"]["health"] == "ok" and current["db"]["stale"] is False
    assert current["db"]["value"] == 2.0


# --------------------------------------------- the command-centre panels

def test_the_first_cpu_reading_is_null_not_a_number(signed_in, monkeypatch):
    """One /proc/stat read gives the average since boot, which on a
    long-running box is flat and meaningless. Only a delta is honest."""
    from atlas.api import dashboard

    monkeypatch.setattr(dashboard, "_cpu_last", None)
    first = signed_in.get("/api/system").json()
    assert first["cpu_percent"] is None
    second = signed_in.get("/api/system").json()
    assert second["cpu_percent"] is None or 0.0 <= second["cpu_percent"] <= 100.0


def test_unreadable_system_values_are_null_never_guessed(signed_in, monkeypatch):
    from atlas.api import dashboard

    monkeypatch.setattr(dashboard, "_meminfo", dict)
    body = signed_in.get("/api/system").json()
    assert body["memory"]["total"] is None
    assert body["memory"]["percent"] is None


def test_an_agent_reports_whether_it_can_run_not_that_it_is_running(
        signed_in, memory_backend):
    """Agents are invoked per request, not resident. Claiming 'running' would
    be theatre; what matters is whether its model tier resolves."""
    # boot() is stubbed in these tests, so the roster is built explicitly.
    from atlas.kernel import agents as agent_module
    agent_module.build_roster()

    async def fetch(sql, *args):
        return []

    memory_backend.fetch = fetch

    body = signed_in.get("/api/agents").json()
    assert body["agents"], "no agents registered"
    for agent in body["agents"]:
        assert "available" in agent
        assert isinstance(agent["providers"], list)
        assert agent["available"] == bool(agent["providers"])


def test_warming_the_roster_needs_a_session(client):
    assert client.post("/api/agents/warm").status_code == 401


def test_warming_the_roster_starts_a_background_job_and_returns_at_once(
        signed_in, monkeypatch):
    """The request must not block on every agent's model call -- it starts
    the job in the tracked background registry (accounted for at shutdown,
    unlike a bare `create_task`) and returns immediately."""
    from atlas.kernel import agents as agent_module
    agent_module.build_roster()

    started = []

    def fake_spawn(coro, name):
        started.append(name)
        coro.close()          # don't actually run it against a real model

    monkeypatch.setattr("atlas.kernel.background.spawn", fake_spawn)

    response = signed_in.post("/api/agents/warm")
    assert response.status_code == 200, response.text
    assert started == ["warm agents"]
    body = response.json()
    assert body["started"] is True
    assert "concierge" in body["agents"]


def test_warming_the_roster_refuses_a_second_click_while_one_runs(
        signed_in, monkeypatch):
    """A double-click must not start two overlapping sweeps over the same
    roster -- each one running a fresh, uncoordinated LLM call per agent."""
    monkeypatch.setattr(
        "atlas.kernel.background.running", lambda: ["warm agents"])

    response = signed_in.post("/api/agents/warm")
    assert response.status_code == 409


def test_llm_status_flags_a_critical_chain_that_is_not_cross_provider(
        signed_in, monkeypatch):
    """Two models from one vendor is not a second opinion, and this is where
    that becomes visible rather than silently passing."""
    from atlas.kernel import llm

    class Fake:
        def __init__(self, name): self.name = name

    monkeypatch.setitem(llm.ROUTES, llm.Need.CRITICAL,
                        [Fake("anthropic"), Fake("anthropic")])
    body = signed_in.get("/api/llm").json()
    assert body["needs"]["critical"]["cross_provider"] is False

    monkeypatch.setitem(llm.ROUTES, llm.Need.CRITICAL,
                        [Fake("anthropic"), Fake("openai")])
    assert signed_in.get("/api/llm").json()["needs"]["critical"]["cross_provider"] is True


def test_memory_reports_all_four_kinds_even_when_empty(signed_in, memory_backend):
    """A missing kind is shown as zero, so a writer that stopped is visible."""
    memory_backend.responses["FROM memories GROUP BY kind"] = [
        {"kind": "episodic", "n": 12}]
    memory_backend.responses["count(*) AS n FROM voice_turns"] = {"n": 0}
    memory_backend.responses["FROM actions_log WHERE event LIKE"] = {"n": 0}
    body = signed_in.get("/api/memory").json()
    assert set(body["kinds"]) == {"episodic", "semantic", "procedural", "working"}
    assert body["kinds"]["episodic"] == 12
    assert body["kinds"]["semantic"] == 0


def test_an_empty_critical_chain_is_not_reported_as_single_vendor(
        signed_in, monkeypatch):
    """Nothing connected is a different problem from one vendor twice, and
    naming the wrong one sends you looking in the wrong place."""
    from atlas.kernel import llm

    class Fake:
        def __init__(self, name): self.name = name

    monkeypatch.setitem(llm.ROUTES, llm.Need.CRITICAL, [])
    body = signed_in.get("/api/llm").json()
    assert body["needs"]["critical"]["cross_provider"] is None
    assert body["needs"]["critical"]["connected"] is False

    monkeypatch.setitem(llm.ROUTES, llm.Need.CRITICAL, [Fake("anthropic")])
    assert signed_in.get("/api/llm").json()["needs"]["critical"]["cross_provider"] is None


# ------------------------------------------------- the Details button

def _slack_post(client, payload: dict, secret: str = "s3cret"):
    """A correctly signed Slack interaction, as Slack would send it."""
    import hashlib
    import hmac
    import json as _json
    import time as _time
    import urllib.parse

    body = urllib.parse.urlencode({"payload": _json.dumps(payload)})
    ts = str(int(_time.time()))
    base = f"v0:{ts}:{body}".encode()
    sig = "v0=" + hmac.new(secret.encode(), base, hashlib.sha256).hexdigest()
    return client.post(
        "/webhooks/slack", content=body,
        headers={"Content-Type": "application/x-www-form-urlencoded",
                 "X-Slack-Request-Timestamp": ts,
                 "X-Slack-Signature": sig})


async def test_the_details_button_is_a_read_not_a_verdict(
        client, monkeypatch, memory_backend):
    """Details sits on every proposal card. Routing it through decide()
    answered "unknown verdict 'detail'" in the channel -- the same mistake as
    the wildcard {verdict} route that once swallowed /approvals/1/why: a read
    sent down the only write path."""
    monkeypatch.setenv("SLACK_SIGNING_SECRET", "s3cret")
    monkeypatch.setenv("SLACK_APPROVER_IDS", "UMIRO")

    decided = []

    async def fake_decide(*a, **k):
        decided.append(a)
        raise AssertionError("Details must never reach decide()")

    async def fake_fetch_one(_sql, *args):
        return {"action": "flycheap.rerun", "params": '{"page":"x"}', "tier": 2,
                "reason": "the scraper is stale", "evidence": "would fetch 3 routes",
                "rollback_plan": "none needed", "confidence": 0.8,
                "status": "pending", "expires_at": None}

    monkeypatch.setattr("atlas.api.main.decide", fake_decide)
    monkeypatch.setattr("atlas.kernel.state.fetch_one", fake_fetch_one)

    posted = []

    async def fake_reply(url, text, *, ephemeral=False):
        posted.append((url, text, ephemeral))

    monkeypatch.setattr("atlas.api.main._slack_reply", fake_reply)
    response = _slack_post(client, {
        "user": {"id": "UMIRO"}, "response_url": "https://hooks.slack/x",
        "actions": [{"value": "detail:7"}]})
    assert response.status_code == 200
    assert decided == []
    # Ephemeral: reading a proposal must not add a message the whole channel
    # has to scroll past.
    _url, text, ephemeral = posted[0]
    assert ephemeral is True
    assert "the scraper is stale" in text
    assert "would fetch 3 routes" in text


async def test_a_non_approver_click_is_refused(client, monkeypatch, memory_backend):
    """A valid Slack signature proves the request came from Slack, not that
    it came from Miro."""
    monkeypatch.setenv("SLACK_SIGNING_SECRET", "s3cret")
    monkeypatch.setenv("SLACK_APPROVER_IDS", "UMIRO")

    async def fake_decide(*a, **k):
        raise AssertionError("a stranger's click must never reach decide()")

    posted = []

    async def fake_reply(url, text, *, ephemeral=False):
        posted.append(text)

    monkeypatch.setattr("atlas.api.main.decide", fake_decide)
    monkeypatch.setattr("atlas.api.main._slack_reply", fake_reply)
    response = _slack_post(client, {
        "user": {"id": "USOMEONE"}, "response_url": "https://hooks.slack/x",
        "actions": [{"value": "approve:7"}]})
    assert response.status_code == 200
    assert "not an approver" in posted[0]


async def test_a_replayed_slack_request_is_rejected(client, monkeypatch):
    """The timestamp is inside the HMAC and older than the window."""
    import hashlib
    import hmac
    import json as _json
    import time as _time
    import urllib.parse

    monkeypatch.setenv("SLACK_SIGNING_SECRET", "s3cret")
    body = urllib.parse.urlencode(
        {"payload": _json.dumps({"user": {"id": "UMIRO"},
                                 "actions": [{"value": "approve:7"}]})})
    ts = str(int(_time.time()) - 1200)           # twenty minutes ago
    sig = "v0=" + hmac.new(b"s3cret", f"v0:{ts}:{body}".encode(),
                           hashlib.sha256).hexdigest()
    response = client.post(
        "/webhooks/slack", content=body,
        headers={"Content-Type": "application/x-www-form-urlencoded",
                 "X-Slack-Request-Timestamp": ts, "X-Slack-Signature": sig})
    assert response.status_code == 401


# --------------------------------------------------- the inbound phone line

def _twilio_post(client, path: str, params: dict, token: str = "twtok",
                 host: str = "atlas.example.com"):
    import base64
    import hashlib
    import hmac
    import urllib.parse

    signed = f"https://{host}{path}" + "".join(
        f"{k}{params[k]}" for k in sorted(params))
    sig = base64.b64encode(
        hmac.new(token.encode(), signed.encode(), hashlib.sha1).digest()).decode()
    return client.post(path, content=urllib.parse.urlencode(params),
                       headers={"Content-Type": "application/x-www-form-urlencoded",
                                "X-Twilio-Signature": sig})


async def test_an_unrecognised_caller_is_told_nothing(client, monkeypatch):
    """A phone number is the least authenticated surface ATLAS has: no mesh,
    no login, no workspace, and caller ID is spoofable. A stranger must not
    learn a status, a business name, or whether something exists."""
    monkeypatch.setenv("TWILIO_AUTH_TOKEN", "twtok")
    monkeypatch.setenv("ATLAS_HOST", "atlas.example.com")
    monkeypatch.setenv("TWILIO_TO", "+15551112222")

    body = _twilio_post(client, "/webhooks/twilio/voice/inbound",
                        {"From": "+15559998888", "CallSid": "CA1"}).text
    assert "private line" in body
    for leak in ("flycheap", "guardian", "vpsie", "roots", "failing",
                 "degraded", "snapshot", "balance"):
        assert leak not in body.lower(), f"leaked {leak!r} to a stranger"


async def test_a_call_with_no_twilio_signature_is_rejected(client, monkeypatch):
    monkeypatch.setenv("TWILIO_AUTH_TOKEN", "twtok")
    monkeypatch.setenv("ATLAS_HOST", "atlas.example.com")
    response = client.post("/webhooks/twilio/voice/inbound",
                           content="From=%2B15551112222",
                           headers={"Content-Type":
                                    "application/x-www-form-urlencoded"})
    assert response.status_code == 401


async def test_miro_is_greeted_and_told_what_needs_him(
        client, monkeypatch, memory_backend):
    monkeypatch.setenv("TWILIO_AUTH_TOKEN", "twtok")
    monkeypatch.setenv("ATLAS_HOST", "atlas.example.com")
    monkeypatch.setenv("TWILIO_TO", "+15551112222")

    async def fake_rows(_sql, *a):
        # Fresh readings: the staleness rule would make an old one unknown.
        at = datetime.now(UTC)
        return [{"health": "failing", "name": "protection", "prop": "vpsie",
                 "at": at, "interval_s": 300},
                {"health": "degraded", "name": "open_errors", "prop": "inroots",
                 "at": at, "interval_s": 300},
                {"health": "unknown", "name": "peers", "prop": "netbird",
                 "at": at, "interval_s": 300},
                {"health": "ok", "name": "api", "prop": "flycheap",
                 "at": at, "interval_s": 300}]

    monkeypatch.setattr("atlas.kernel.state.fetch_all", fake_rows)
    body = _twilio_post(client, "/webhooks/twilio/voice/inbound",
                        {"From": "+15551112222", "CallSid": "CA1"}).text
    assert "How're you doing" in body
    # The call is held open to listen, not ended after the announcement.
    assert 'input="speech dtmf"' in body and "<Hangup/>" not in body.split("</Gather>")[0]
    assert "2 things need you" in body
    # The loudest is named, and it is the FAILING one rather than the first.
    assert "vpsie protection" in body
    # An unmeasured check is said as unmeasured, never folded into "fine".
    assert "couldn't run" in body


async def test_the_greeting_counts_a_stale_ok_as_unmeasured(
        client, monkeypatch, memory_backend):
    """The staleness rule reaches the phone greeting too."""
    monkeypatch.setenv("TWILIO_AUTH_TOKEN", "twtok")
    monkeypatch.setenv("ATLAS_HOST", "atlas.example.com")
    monkeypatch.setenv("TWILIO_TO", "+15551112222")

    async def stale(_sql, *a):
        # One fresh failure (so the board is offered at all) and one OK read
        # 26 minutes ago on a 5-minute check.
        return [{"health": "failing", "name": "protection", "prop": "vpsie",
                 "at": datetime.now(UTC), "interval_s": 300},
                {"health": "ok", "name": "api", "prop": "flycheap",
                 "at": datetime.now(UTC) - timedelta(minutes=26),
                 "interval_s": 300}]

    monkeypatch.setattr("atlas.kernel.state.fetch_all", stale)
    body = _twilio_post(client, "/webhooks/twilio/voice/inbound",
                        {"From": "+15551112222", "CallSid": "CA1"}).text
    assert "1 thing needs you" in body
    assert "1 check couldn't run" in body


async def test_the_greeting_never_guesses_when_it_cannot_read(
        client, monkeypatch, memory_backend):
    """A wrong number said confidently down a phone is worse than none."""
    monkeypatch.setenv("TWILIO_AUTH_TOKEN", "twtok")
    monkeypatch.setenv("ATLAS_HOST", "atlas.example.com")
    monkeypatch.setenv("TWILIO_TO", "+15551112222")

    async def boom(_sql, *a):
        raise RuntimeError("database is down")

    monkeypatch.setattr("atlas.kernel.state.fetch_all", boom)
    body = _twilio_post(client, "/webhooks/twilio/voice/inbound",
                        {"From": "+15551112222", "CallSid": "CA1"}).text
    assert "won't guess" in body


async def test_an_outcome_is_posted_to_response_url_not_the_body(
        client, monkeypatch, memory_backend):
    """Slack IGNORES the HTTP body of a block_actions interaction. It wants a
    bare 200 within three seconds; anything the human should see must be
    POSTed to the payload's response_url.

    Returning {"response_type": ..., "text": ...} is the right shape for a
    slash command and the wrong one here, so every approval outcome went into
    a void: the button worked, decide() ran, and the channel showed nothing.
    That is exactly what "the approve buttons aren't working" looked like."""
    monkeypatch.setenv("SLACK_SIGNING_SECRET", "s3cret")
    monkeypatch.setenv("SLACK_APPROVER_IDS", "UMIRO")

    posted = []

    async def fake_reply(url, text, *, ephemeral=False):
        posted.append((url, text, ephemeral))

    class FakeVerdict:
        def render(self):
            return "Approved. flycheap.rerun ran and finished."

        def receipt(self):
            return "Approved. flycheap.rerun ran and finished."

    async def fake_decide(pid, verdict, via):
        assert (pid, verdict) == (7, "approve")
        return FakeVerdict()

    monkeypatch.setattr("atlas.api.main.decide", fake_decide)
    monkeypatch.setattr("atlas.api.main._slack_reply", fake_reply)

    response = _slack_post(client, {
        "user": {"id": "UMIRO"},
        "response_url": "https://hooks.slack.com/actions/T1/B2/xyz",
        "actions": [{"value": "approve:7"}]})

    assert response.status_code == 200
    url, text, ephemeral = posted[0]
    assert url == "https://hooks.slack.com/actions/T1/B2/xyz"
    assert "Approved" in text
    # The outcome of a decision belongs in the channel, not whispered to the
    # person who clicked: everyone watching should see what ATLAS did.
    assert ephemeral is False


async def test_a_missing_response_url_does_not_break_the_decision(
        client, monkeypatch, memory_backend):
    """Narrating the outcome is not the outcome. If Slack sends no
    response_url, the decision still stands and the handler still 200s."""
    monkeypatch.setenv("SLACK_SIGNING_SECRET", "s3cret")
    monkeypatch.setenv("SLACK_APPROVER_IDS", "UMIRO")

    ran = []

    class FakeVerdict:
        def render(self):
            return "Approved."

        def receipt(self):
            return "Approved."

    async def fake_decide(pid, verdict, via):
        ran.append(pid)
        return FakeVerdict()

    monkeypatch.setattr("atlas.api.main.decide", fake_decide)
    response = _slack_post(client, {"user": {"id": "UMIRO"},
                                    "actions": [{"value": "approve:7"}]})
    assert response.status_code == 200
    assert ran == [7]


# ------------------------------------------------------ the slash command

def _slash(client, text: str, user: str = "UMIRO", secret: str = "s3cret"):
    import hashlib
    import hmac
    import time as _time
    import urllib.parse

    body = urllib.parse.urlencode({
        "user_id": user, "text": text,
        "response_url": "https://hooks.slack.com/commands/T1/B2/xyz"})
    ts = str(int(_time.time()))
    sig = "v0=" + hmac.new(secret.encode(), f"v0:{ts}:{body}".encode(),
                           hashlib.sha256).hexdigest()
    return client.post("/webhooks/slack/command", content=body,
                       headers={"Content-Type": "application/x-www-form-urlencoded",
                                "X-Slack-Request-Timestamp": ts,
                                "X-Slack-Signature": sig})


async def test_a_command_from_a_non_approver_does_nothing(client, monkeypatch):
    """Being in the workspace is not being Miro."""
    monkeypatch.setenv("SLACK_SIGNING_SECRET", "s3cret")
    monkeypatch.setenv("SLACK_APPROVER_IDS", "UMIRO")

    async def should_not_run(*a, **k):
        raise AssertionError("a stranger's command must not reach intent")

    monkeypatch.setattr("atlas.kernel.intent.act", should_not_run)
    body = _slash(client, "restart everything", user="USTRANGER").json()
    assert "not an approver" in body["text"]
    assert body["response_type"] == "ephemeral"


async def test_a_command_with_a_bad_signature_is_rejected(client, monkeypatch):
    monkeypatch.setenv("SLACK_SIGNING_SECRET", "s3cret")
    assert _slash(client, "hello", secret="wrong").status_code == 401


async def test_an_empty_command_asks_for_one(client, monkeypatch):
    monkeypatch.setenv("SLACK_SIGNING_SECRET", "s3cret")
    monkeypatch.setenv("SLACK_APPROVER_IDS", "UMIRO")
    body = _slash(client, "   ").json()
    assert "Tell me what to do" in body["text"]


async def test_a_command_acknowledges_inside_the_window(client, monkeypatch):
    """Slack gives three seconds. A command that thinks for eight is one
    Slack has already told the user timed out, so the work happens after the
    acknowledgement and the outcome follows on response_url."""
    monkeypatch.setenv("SLACK_SIGNING_SECRET", "s3cret")
    monkeypatch.setenv("SLACK_APPROVER_IDS", "UMIRO")

    started = []

    def fake_spawn(coro, name):
        started.append(coro)
        coro.close()          # don't actually run it in the test loop

    # Scheduled through the background registry, so a shutdown accounts for it.
    monkeypatch.setattr("atlas.kernel.background.spawn", fake_spawn)
    body = _slash(client, "rerun the flycheap scraper").json()
    assert body["text"].startswith("On it")
    assert started, "the work must be scheduled, not skipped"


# ------------------------------------------------------- the route inventory

def test_every_inbound_route_still_exists():
    """A route that disappears is silent in a way a broken one is not.

    The commit that added the brand mark deleted /webhooks/twilio/sms -- a
    scripted edit to main.py dropped it, the route table got shorter, and
    nothing failed. No test covered its existence, so the suite stayed green
    and the approve-by-text path was gone for an hour while being described
    as working.

    Each of these is a way the outside world reaches ATLAS. Removing one is
    a decision; it should cost a deliberate edit here, not happen by
    accident somewhere else."""
    from atlas.api.main import app

    expected = {
        "/webhooks/slack",                    # button clicks
        "/webhooks/slack/command",            # /atlas ...
        "/webhooks/twilio/sms",               # approve by replying to a text
        "/webhooks/twilio/voice",             # the TwiML for a CRITICAL page
        "/webhooks/twilio/voice/digit",       # the keypad decision
        "/webhooks/twilio/voice/inbound",     # he rang the number
        "/healthz",
    }
    live = {getattr(route, "path", "") for route in app.routes}
    missing = expected - live
    assert not missing, f"these routes have vanished: {sorted(missing)}"


def test_no_inbound_route_is_unauthenticated():
    """Every webhook verifies a signature before doing anything. /healthz is
    the deliberate exception and says nothing about the estate."""
    import ast
    import inspect

    from atlas.api import main

    source = inspect.getsource(main)
    tree = ast.parse(source)
    checked = {"_verify_slack", "_verify_twilio"}

    for node in ast.walk(tree):
        if not isinstance(node, ast.AsyncFunctionDef):
            continue
        paths = [
            d.args[0].value for d in node.decorator_list
            if isinstance(d, ast.Call) and d.args
            and isinstance(d.args[0], ast.Constant)
            and isinstance(d.args[0].value, str)
            and d.args[0].value.startswith("/webhooks/")
        ]
        if not paths:
            continue
        # The audio route is token-authenticated rather than signed; it is
        # covered by its own tests in test_phone.py.
        if any(p.startswith("/webhooks/twilio/audio") for p in paths):
            continue
        called = {getattr(n.func, "id", "") for n in ast.walk(node)
                  if isinstance(n, ast.Call)}
        assert called & checked, (
            f"{paths} runs without verifying a signature")


# ------------------------------------------------ fleet and memory graph

def test_agents_lists_every_roster_agent_with_no_fake_heartbeat(
        signed_in, memory_backend):
    """With an empty agent_runs table, every roster agent still appears, and
    a missing run is `null`, never a made-up timestamp."""
    from atlas.kernel import agents as agent_module
    agent_module.build_roster()

    async def fetch(sql, *args):
        return []

    memory_backend.fetch = fetch

    body = signed_in.get("/api/agents").json()
    names = {a["name"] for a in body["agents"]}
    assert names == set(agent_module.ROSTER)
    for agent in body["agents"]:
        assert agent["last_run_at"] is None
        assert agent["last_outcome"] is None
        assert agent["runs_24h"] == 0
        assert agent["model"] is None
        assert isinstance(agent["handoff"], list)
        assert isinstance(agent["memory_scope"], list)
        roster_agent = agent_module.ROSTER[agent["name"]]
        assert set(agent["handoff"]) == roster_agent.handoff
        assert list(agent["memory_scope"]) == list(roster_agent.memory_scope)
        # Its persona, from the fixed table -- presentation only.
        from atlas.kernel.personas import PERSONAS
        assert agent["persona"]["name"] == PERSONAS[agent["name"]].name
        assert agent["persona"]["voice"] in {"female", "male"}


def test_agents_reports_the_latest_run_and_the_24h_count(
        signed_in, memory_backend):
    from atlas.kernel import agents as agent_module
    agent_module.build_roster()

    last_at = datetime(2026, 9, 24, 9, 0, tzinfo=UTC)

    async def fetch(sql, *args):
        flat = " ".join(sql.split())
        if "DISTINCT ON (agent)" in flat:
            return [{"agent": "ops", "at": last_at, "outcome": "fixed it",
                      "model": "claude-sonnet"}]
        if "interval '24 hours'" in flat:
            return [{"agent": "ops", "n": 3}]
        return []

    memory_backend.fetch = fetch

    body = signed_in.get("/api/agents").json()
    by_name = {a["name"]: a for a in body["agents"]}
    assert by_name["ops"]["last_run_at"] == last_at.isoformat()
    assert by_name["ops"]["last_outcome"] == "fixed it"
    assert by_name["ops"]["runs_24h"] == 3
    assert by_name["ops"]["model"] == "claude-sonnet"
    # An agent nothing ran recently for reports zero, not a stale copy.
    assert by_name["sentinel"]["last_run_at"] is None
    assert by_name["sentinel"]["runs_24h"] == 0


def test_agents_reports_the_model_that_answered_not_the_configured_pin(
        signed_in, memory_backend):
    """Task 53: `flycheap` is pinned to the OpenRouter default, but the fallback
    prefix means Anthropic actually answered the last run -- the card must
    say `anthropic:claude-sonnet-4-5`, never `openrouter:...hermes...` as if
    the pin had run (invariant 6)."""
    from atlas.kernel import agents as agent_module

    agent_module.build_roster()
    from atlas.kernel.businesses import DEFAULT_MODEL
    assert agent_module.ROSTER["flycheap"].model == DEFAULT_MODEL
    last_at = datetime(2026, 9, 24, 9, 0, tzinfo=UTC)

    async def fetch(sql, *args):
        flat = " ".join(sql.split())
        if "DISTINCT ON (agent)" in flat:
            return [{"agent": "flycheap", "at": last_at, "outcome": "fine",
                      "model": "claude-sonnet-4-5", "provider": "anthropic"}]
        return []

    memory_backend.fetch = fetch

    body = signed_in.get("/api/agents").json()
    by_name = {a["name"]: a for a in body["agents"]}
    assert by_name["flycheap"]["model"] == "anthropic:claude-sonnet-4-5"
    assert by_name["flycheap"]["model_at"] == last_at.isoformat()
    # No run at all: null, not the configured default dressed up as a run.
    assert by_name["guardian"]["model"] is None
    assert by_name["guardian"]["model_at"] is None


# ---------------------------------------------- readiness, groups, shifts

def test_agents_reports_readiness_and_reasons(signed_in, memory_backend, monkeypatch):
    from atlas.kernel import agents as agent_module

    agent_module.build_roster()

    async def fetch(sql, *args):
        return []

    memory_backend.fetch = fetch

    def fake_readiness(name):
        if name == "fiverr":
            return False, ["browser disabled — missing env: X (browser_open)"]
        return True, []

    monkeypatch.setattr(agent_module, "readiness", fake_readiness)

    body = signed_in.get("/api/agents").json()
    by_name = {a["name"]: a for a in body["agents"]}
    assert by_name["ops"]["ready"] is True
    assert by_name["ops"]["reasons"] == []
    assert by_name["fiverr"]["ready"] is False
    assert by_name["fiverr"]["reasons"] == [
        "browser disabled — missing env: X (browser_open)"]


def test_agents_group_places_concierge_businesses_and_functions_correctly(
        signed_in, memory_backend):
    from atlas.kernel import agents as agent_module
    from atlas.kernel.businesses import BY_NAME as BUSINESSES

    agent_module.build_roster()

    async def fetch(sql, *args):
        return []

    memory_backend.fetch = fetch

    body = signed_in.get("/api/agents").json()
    by_name = {a["name"]: a for a in body["agents"]}
    assert by_name["concierge"]["group"] == "lead"
    for name in BUSINESSES:
        assert by_name[name]["group"] == "businesses", name
    for name in ("ops", "growth", "marketing", "inbox", "scheduler",
                 "sentinel", "commerce", "analyst"):
        assert by_name[name]["group"] == "functions", name


def test_guardian_and_school_carry_a_shortened_display_name(
        signed_in, memory_backend):
    """The map shows "Vault" for guardian and "College" for school -- Miro's
    own words for the redesign -- while `title` (Fleet's own cards, the org
    chart, DeepDive) stays exactly what it always was."""
    from atlas.kernel import agents as agent_module

    agent_module.build_roster()

    async def fetch(sql, *args):
        return []

    memory_backend.fetch = fetch

    body = signed_in.get("/api/agents").json()
    by_name = {a["name"]: a for a in body["agents"]}
    assert by_name["guardian"]["title"] == "Guardian"
    assert by_name["guardian"]["display"] == "Vault"
    assert by_name["school"]["display"] == "College"
    assert by_name["ops"]["display"] == by_name["ops"]["title"]


def test_agents_reports_next_shift_at_and_null_when_not_ready(
        signed_in, memory_backend, monkeypatch):
    from atlas.kernel import agents as agent_module

    agent_module.build_roster()
    next_at = datetime(2026, 9, 28, 7, 0, tzinfo=UTC)

    async def fetch(sql, *args):
        return []

    memory_backend.fetch = fetch
    monkeypatch.setattr(agent_module, "next_shift_at",
                        lambda name, last, now: next_at)

    def fake_readiness(name):
        return (False, ["reason"]) if name == "fiverr" else (True, [])

    monkeypatch.setattr(agent_module, "readiness", fake_readiness)

    body = signed_in.get("/api/agents").json()
    by_name = {a["name"]: a for a in body["agents"]}
    assert by_name["ops"]["next_shift_at"] == next_at.isoformat()
    # Not ready: never a next shift time, whatever next_shift_at would say.
    assert by_name["fiverr"]["next_shift_at"] is None


def test_warming_a_single_agent_by_name(signed_in, monkeypatch):
    from atlas.kernel import agents as agent_module
    agent_module.build_roster()

    started = []

    def fake_spawn(coro, name):
        started.append(name)
        coro.close()          # don't actually run it against a real model

    monkeypatch.setattr("atlas.kernel.background.spawn", fake_spawn)

    response = signed_in.post("/api/agents/warm", json={"name": "ops"})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["agents"] == ["ops"]
    assert started == ["warm agents · ops"]


def test_warming_an_unknown_agent_by_name_is_404(signed_in):
    response = signed_in.post("/api/agents/warm", json={"name": "no-such-agent"})
    assert response.status_code == 404


def test_warming_one_agent_refuses_while_the_full_sweep_runs(
        signed_in, monkeypatch):
    from atlas.kernel import agents as agent_module
    agent_module.build_roster()
    monkeypatch.setattr(
        "atlas.kernel.background.running", lambda: ["warm agents"])

    response = signed_in.post("/api/agents/warm", json={"name": "ops"})
    assert response.status_code == 409


def test_warming_one_agent_refuses_a_second_click_on_the_same_agent(
        signed_in, monkeypatch):
    from atlas.kernel import agents as agent_module
    agent_module.build_roster()
    monkeypatch.setattr(
        "atlas.kernel.background.running", lambda: ["warm agents · ops"])

    response = signed_in.post("/api/agents/warm", json={"name": "ops"})
    assert response.status_code == 409


def test_warming_the_full_roster_still_works_with_no_body(
        signed_in, monkeypatch):
    """The extended endpoint must not break the plain, bodyless POST every
    existing caller (and test_warming_the_roster_starts_a_background_job_
    and_returns_at_once above) already makes."""
    from atlas.kernel import agents as agent_module
    agent_module.build_roster()

    started = []

    def fake_spawn(coro, name):
        started.append(name)
        coro.close()

    monkeypatch.setattr("atlas.kernel.background.spawn", fake_spawn)

    response = signed_in.post("/api/agents/warm")
    assert response.status_code == 200, response.text
    assert started == ["warm agents"]


def test_agents_feed_truncates_the_task_and_finds_a_handoff(
        signed_in, memory_backend):
    at1 = datetime(2026, 9, 24, 9, 0, tzinfo=UTC)
    at2 = datetime(2026, 9, 24, 8, 0, tzinfo=UTC)
    long_task = "x" * 200

    async def fetch(sql, *args):
        flat = " ".join(sql.split())
        assert "FROM agent_runs" in flat
        return [
            {"at": at1, "agent": "concierge", "task": long_task,
             "outcome": "relayed", "turns": 2, "model": "claude-sonnet",
             "transcript": [
                 {"turn": 1, "assistant": "let me ask ops",
                  "tool_calls": [{"name": "ask_specialist",
                                  "input": {"agent": "ops", "task": "check it"}}]},
             ]},
            {"at": at2, "agent": "ops", "task": "restart the box",
             "outcome": "done", "turns": 1, "model": "claude-haiku",
             "transcript": [{"turn": 1, "assistant": "done", "tool_calls": None}]},
        ]

    memory_backend.fetch = fetch

    body = signed_in.get("/api/agents/feed").json()
    assert "as_of" in body
    items = body["items"]
    assert len(items) == 2
    assert items[0]["at"] == at1.isoformat()
    assert items[0]["agent"] == "concierge"
    assert items[0]["task"] == long_task[:140]
    assert len(items[0]["task"]) == 140
    assert items[0]["outcome"] == "relayed"
    assert items[0]["turns"] == 2
    assert items[0]["model"] == "claude-sonnet"
    assert items[0]["to"] == "ops"
    # No ask_specialist call in this transcript: no handoff to report.
    assert items[1]["to"] is None


def test_agents_feed_transcript_stored_as_a_json_string_is_still_read(
        signed_in, memory_backend):
    """The column is JSONB; a backend that hands it back as text should not
    break handoff detection."""
    import json as _json
    at = datetime(2026, 9, 24, 9, 0, tzinfo=UTC)

    async def fetch(sql, *args):
        return [{"at": at, "agent": "concierge", "task": "t",
                  "outcome": "ok", "turns": 1, "model": "claude-haiku",
                  "transcript": _json.dumps([
                      {"tool_calls": [{"name": "ask_specialist",
                                        "input": {"agent": "analyst"}}]}])}]

    memory_backend.fetch = fetch
    body = signed_in.get("/api/agents/feed").json()
    assert body["items"][0]["to"] == "analyst"


def test_agents_feed_survives_a_malformed_handoff_call(signed_in, memory_backend):
    """A tool call whose `input` is not even a dict must not 500 the feed —
    it is a reason to say `null`, not to crash the dashboard."""
    at = datetime(2026, 9, 24, 9, 0, tzinfo=UTC)

    async def fetch(sql, *args):
        return [{"at": at, "agent": "concierge", "task": "t",
                  "outcome": "ok", "turns": 1, "model": "claude-haiku",
                  "transcript": [{"name": "ask_specialist", "input": "ops"}]}]

    memory_backend.fetch = fetch
    response = signed_in.get("/api/agents/feed")
    assert response.status_code == 200
    assert response.json()["items"][0]["to"] is None


def test_memory_graph_has_a_node_per_namespace_and_an_edge_per_agent_scope(
        signed_in, memory_backend):
    from atlas.kernel import agents as agent_module
    agent_module.build_roster()

    async def fetch(sql, *args):
        flat = " ".join(sql.split())
        assert "GROUP BY namespace, kind" in flat
        return [
            {"namespace": "people", "kind": "episodic", "n": 5},
            {"namespace": "people", "kind": "semantic", "n": 2},
            {"namespace": "security", "kind": "episodic", "n": 1},
        ]

    memory_backend.fetch = fetch

    body = signed_in.get("/api/memory/graph").json()
    assert "as_of" in body
    nodes_by_id = {n["id"]: n for n in body["nodes"]}
    assert nodes_by_id["ns:people"]["kind_counts"] == {"episodic": 5, "semantic": 2}
    assert nodes_by_id["ns:people"]["total"] == 7
    assert nodes_by_id["ns:security"]["total"] == 1
    # A namespace with an agent pointing at it but zero memories still shows
    # up, or the edge below would dangle.
    assert "ns:incidents" in nodes_by_id
    assert nodes_by_id["ns:incidents"]["total"] == 0

    edges = {(e["from"], e["to"]) for e in body["edges"]}
    assert ("agent:ops", "ns:incidents") in edges
    assert ("agent:inbox", "ns:people") in edges

    agents_by_id = {a["id"]: a for a in body["agents"]}
    assert agents_by_id["ops"]["title"] == "Operations"
    assert set(agents_by_id) == set(agent_module.ROSTER)


# ------------------------------------------------------- mission control
#
# The seven read-only endpoints behind the Command Center. Every one of them
# is a GET with a session, every one carries `as_of`, and none of them may
# ever grow a way to change something.

def test_agenda_returns_structured_items_split_into_today_and_the_horizon(
        signed_in, monkeypatch):
    """The timeline needs a time it can subtract from, not a rendered line.

    The morning brief's own string formatting stays where it is; this endpoint
    reuses the collection behind it so the two can never disagree about what
    is on today.
    """
    from atlas.kernel import brief as brief_mod
    from atlas.kernel.contracts import AgendaItem

    today_at = datetime.now().astimezone().replace(
        hour=9, minute=30, second=0, microsecond=0)
    later = today_at + timedelta(hours=40)

    async def items(_now, *_a, **_kw):
        return [
            AgendaItem(when=today_at, title="Daily standup", kind="event",
                       detail="the whole crew", minutes=30, source="calendar"),
            AgendaItem(when=later, title="Invoice due", kind="deadline",
                       source="mirotech"),
        ]

    monkeypatch.setattr(brief_mod, "agenda_items", items)

    body = signed_in.get("/api/agenda").json()
    assert "as_of" in body
    assert body["unavailable"] is None
    first, second = body["items"]
    assert first["title"] == "Daily standup"
    assert first["when"] == today_at.isoformat()
    assert first["minutes"] == 30
    assert first["source"] == "calendar"
    assert first["today"] is True
    # A deadline 40 hours out is inside the horizon but is not today's diary.
    assert second["kind"] == "deadline"
    assert second["today"] is False


def test_agenda_says_it_could_not_look_rather_than_reporting_an_empty_day(
        signed_in, monkeypatch):
    from atlas.kernel import brief as brief_mod

    async def explode(_now, *_a, **_kw):
        raise RuntimeError("calendar unreachable")

    monkeypatch.setattr(brief_mod, "agenda_items", explode)

    body = signed_in.get("/api/agenda").json()
    assert body["items"] == []
    assert "calendar unreachable" in body["unavailable"]
    assert body["horizon_hours"] == 72


def test_agenda_reads_72_hours_unless_asked_and_passes_the_reach_down(
        signed_in, monkeypatch):
    """The home page's week asks for 168 hours; everything else keeps the
    brief's 72. Whatever was asked is what reaches the modules, and what the
    payload says it covered."""
    from atlas.kernel import brief as brief_mod

    asked: list[timedelta] = []

    async def items(_now, horizon=brief_mod.DEADLINE_HORIZON, unread=None):
        asked.append(horizon)
        return []

    monkeypatch.setattr(brief_mod, "agenda_items", items)

    body = signed_in.get("/api/agenda").json()
    assert body["horizon_hours"] == 72 and asked[-1] == timedelta(hours=72)
    body = signed_in.get("/api/agenda?hours=168").json()
    assert body["horizon_hours"] == 168 and asked[-1] == timedelta(hours=168)
    body = signed_in.get("/api/agenda?hours=1").json()
    assert body["horizon_hours"] == 1 and asked[-1] == timedelta(hours=1)


@pytest.mark.parametrize("hours", ["0", "169", "-5", "9999", "week"])
def test_agenda_refuses_a_reach_outside_one_hour_to_a_week(signed_in, hours):
    assert signed_in.get(f"/api/agenda?hours={hours}").status_code == 422


def test_agenda_needs_a_session(client):
    assert client.get("/api/agenda?hours=168").status_code == 401


@pytest.mark.asyncio
async def test_a_module_whose_agenda_failed_is_named_not_drawn_as_free(monkeypatch):
    """`agenda_items` still leaves a failed module's items out, but now says
    which module it was, so a screen can tell an unread day from a free one.
    The horizon reaches each module's own `agenda(within)`."""
    from atlas.kernel import brief as brief_mod
    from atlas.kernel.contracts import AgendaItem
    from atlas.kernel.registry import REGISTRY

    now = datetime.now(UTC)
    seen: list[timedelta] = []

    class Fine:
        async def agenda(self, within):
            seen.append(within)
            return (AgendaItem(when=now + timedelta(days=5), title="Late",
                               kind="event"),)

    class Broken:
        async def agenda(self, within):
            raise TimeoutError("calendar slow")

    monkeypatch.setattr(REGISTRY, "modules", {"fine": Fine(), "calendar": Broken()})
    unread: list[str] = []
    got = await brief_mod.agenda_items(now, timedelta(hours=168), unread)
    assert [i.title for i in got] == ["Late"]
    assert seen == [timedelta(hours=168)]
    assert unread == ["calendar (TimeoutError)"]
    # The brief's own 72 hours leaves day five out, as it always has.
    assert await brief_mod.agenda_items(now) == []


def test_tasks_merges_proposals_nightshift_jobs_and_queued_research(
        signed_in, memory_backend):
    at = datetime(2026, 9, 24, 9, 0, tzinfo=UTC)
    expires = datetime(2026, 9, 24, 10, 0, tzinfo=UTC)

    async def fetch(sql, *args):
        flat = " ".join(sql.split())
        if "FROM proposals" in flat:
            # A proposal has no finding column: the link runs the other way.
            # The stub cannot execute SQL, so the join is asserted here or
            # nowhere — and getting it backwards 500s the screen in
            # production while every test still passes.
            assert "f.proposal_id = p.id" in flat, flat
            assert "p.finding_id" not in flat, flat
            return [{"id": 3, "action": "flycheap.regenerate_links",
                     "reason": "links are 404ing", "tier": 2,
                     "created_at": at, "expires_at": expires,
                     "title": "404s on the deal pages"}]
        if "FROM nightshift_jobs" in flat:
            return [{"id": 7, "at": at, "task": "fix the footer",
                     "repo": "flycheap", "branch": "nightshift/footer",
                     "status": "running", "preview_url": None,
                     "finished_at": None}]
        if "FROM research_reports" in flat:
            return [{"id": 1, "at": at, "question": "who resells these?",
                     "status": "queued"}]
        raise AssertionError(flat)

    async def fetchrow(sql, *args):
        assert "count(*)" in " ".join(sql.split())
        return {"n": 1}

    memory_backend.fetch = fetch
    memory_backend.fetchrow = fetchrow

    body = signed_in.get("/api/tasks").json()
    assert "as_of" in body
    assert body["proposals"][0]["id"] == 3
    assert body["proposals"][0]["expires_at"] == expires.isoformat()
    assert body["proposals"][0]["finding"] == "404s on the deal pages"
    assert body["jobs"][0]["status"] == "running"
    assert body["jobs"][0]["finished_at"] is None
    assert body["research"][0]["question"] == "who resells these?"
    assert body["pending"] == 1


def test_a_proposal_with_two_findings_is_still_one_task(
        signed_in, memory_backend):
    """The findings -> proposals join is one-to-many. Two findings pointing at
    one decision must not list it twice, and must not make `pending` say two
    decisions are waiting when one is — that is the kind of inflated number a
    person stops trusting."""
    at = datetime(2026, 9, 24, 9, 0, tzinfo=UTC)
    expires = datetime(2026, 9, 24, 10, 0, tzinfo=UTC)

    async def fetch(sql, *args):
        flat = " ".join(sql.split())
        if "FROM proposals" in flat:
            # DISTINCT ON is what collapses these; the stub cannot apply it,
            # so it hands back both rows exactly as the join would produce
            # them and the assertion below proves the SQL asks for it.
            assert "DISTINCT ON (p.id)" in flat, flat
            row = {"id": 3, "action": "flycheap.regenerate_links",
                   "reason": "links are 404ing", "tier": 2,
                   "created_at": at, "expires_at": expires,
                   "title": "404s on the deal pages"}
            return [row, {**row, "title": "also 404s on the archive"}]
        return []

    async def fetchrow(sql, *args):
        assert "count(*)" in " ".join(sql.split())
        # One proposal is pending, whatever the join produced above.
        return {"n": 1}

    memory_backend.fetch = fetch
    memory_backend.fetchrow = fetchrow

    body = signed_in.get("/api/tasks").json()
    assert body["pending"] == 1, "pending is counted, not measured off the join"
    assert len({p["id"] for p in body["proposals"]}) == 1


def test_conversations_merges_three_sources_newest_first(
        signed_in, memory_backend):
    voice_at = datetime(2026, 9, 24, 9, 0, tzinfo=UTC)
    chat_at = datetime(2026, 9, 24, 11, 0, tzinfo=UTC)
    dm_at = datetime(2026, 9, 24, 10, 0, tzinfo=UTC)

    async def fetch(sql, *args):
        flat = " ".join(sql.split())
        if "UNION ALL" in flat:
            # The badge counts the whole table, not the page above it.
            return [{"n": 4}, {"n": 2}]
        if "FROM voice_turns" in flat:
            return [{"id": 1, "at": voice_at, "transcript": "what is down?",
                     "answer": "nothing", "intent": "question",
                     "agent": "concierge"}]
        if "FROM messaging_inbound" in flat:
            # No chat platform is live (WhatsApp and Telegram were removed),
            # so the filter is empty; the row stands in for a future one.
            assert [] in args
            return [{"id": 2, "at": chat_at, "platform": "chat",
                     "chat_id": "c1", "sender": "Sam", "text": "still on?",
                     "replied_at": None}]
        if "FROM messages_inbound" in flat:
            return [{"id": 3, "at": dm_at, "platform": "instagram",
                     "thread_id": "t1", "sender": "Ali", "text": "thanks",
                     "replied_at": dm_at, "draft": None}]
        raise AssertionError(flat)

    memory_backend.fetch = fetch

    body = signed_in.get("/api/conversations?limit=10").json()
    ats = [c["at"] for c in body["conversations"]]
    assert ats == sorted(ats, reverse=True)
    assert [c["source"] for c in body["conversations"]] == [
        "messaging", "messages", "voice"]
    # Unanswered counts the two messaging tables only, and counts all of
    # them: a voice turn has nobody waiting on a reply, and a badge must not
    # report only what fitted on the page.
    assert body["unanswered"] == 6
    assert body["conversations"][2]["answer"] == "nothing"


def test_knowledge_gathers_the_vault_the_library_and_finished_research(
        signed_in, memory_backend):
    at = datetime(2026, 9, 24, 9, 0, tzinfo=UTC)

    async def fetch(sql, *args):
        flat = " ".join(sql.split())
        if "FROM vault_documents" in flat:
            return [{"id": 1, "path": "/vault/lease.pdf", "title": "Lease",
                     "kind": "lease", "indexed_at": at, "chunks": 12}]
        if "FROM library_entries" in flat:
            return [{"id": 2, "at": at, "kind": "book", "title": "Thinking",
                     "source": None, "notes": "worth rereading"}]
        if "FROM research_reports" in flat:
            return [{"id": 3, "at": at, "question": "best courier?",
                     "status": "done", "report_id": 9, "sources": 4}]
        if "FROM reports" in flat:
            return [{"id": 9, "at": at, "kind": "research",
                     "title": "Courier options", "delivered_via": "sms"}]
        raise AssertionError(flat)

    memory_backend.fetch = fetch

    body = signed_in.get("/api/knowledge").json()
    assert "as_of" in body
    assert body["documents"][0]["title"] == "Lease"
    assert body["documents"][0]["indexed_at"] == at.isoformat()
    assert body["library"][0]["kind"] == "book"
    assert body["research"][0]["status"] == "done"
    assert body["reports"][0]["kind"] == "research"
    assert body["counts"] == {"documents": 1, "library": 1, "research": 1,
                              "reports": 1}


def test_tools_lists_the_catalog_with_denied_actions_marked_denied(
        signed_in, monkeypatch):
    """A refused action is in `decisions` and absent from `actions`. It still
    belongs on the screen — as a denial, with no invented summary."""
    from atlas.kernel import skills as skills_mod
    from atlas.kernel.contracts import Action, ActionResult, Tier
    from atlas.kernel.permissions import Decision
    from atlas.kernel.registry import REGISTRY

    async def run(**_kw):
        return ActionResult(True)

    allowed = Action(name="flycheap.regenerate_links", tier=Tier.AUTO,
                     summary="rebuild the affiliate links",
                     run=run, dry_run=run, rollback=lambda _t: run(),
                     params={"site": "which site"})

    monkeypatch.setattr(REGISTRY, "actions", {allowed.name: allowed})
    monkeypatch.setattr(REGISTRY, "decisions", {
        allowed.name: Decision(True, Tier.AUTO, "reversible"),
        "ebay.refund_buyer": Decision(False, Tier.NEVER, "money out"),
    })

    body = signed_in.get("/api/tools").json()
    assert "as_of" in body
    by_name = {t["name"]: t for t in body["actions"]}
    assert by_name["flycheap.regenerate_links"] == {
        "name": "flycheap.regenerate_links", "module": "flycheap", "tier": 1,
        "allowed": True, "reason": "reversible",
        "summary": "rebuild the affiliate links",
        "params": {"site": "which site"}, "reversible": True,
    }
    denied = by_name["ebay.refund_buyer"]
    assert denied["allowed"] is False
    assert denied["tier"] == 3
    assert denied["reason"] == "money out"
    # Nothing is known about an action that never registered. Say nothing.
    assert denied["summary"] == ""
    assert denied["params"] == {}
    assert body["count"] == 2

    names = {s["name"] for s in body["skills"]}
    assert names == {s.name for s in skills_mod.SKILLS}
    triage = next(s for s in body["skills"] if s["name"] == "Incident triage")
    assert "outage" in triage["triggers"]
    assert "ops" in triage["agents"]


def test_workflows_reads_the_standing_rules_and_the_schedulers_own_hours(
        signed_in, monkeypatch, memory_backend):
    at = datetime(2026, 9, 24, 9, 0, tzinfo=UTC)

    async def fetch(sql, *args):
        assert "FROM standing_rules" in " ".join(sql.split())
        return [{"id": 1, "at": at, "said": "prune at 80% and tell me after",
                 "via": "voice", "briefed_at": None}]

    memory_backend.fetch = fetch
    monkeypatch.setenv("BRIEF_HOUR", "6")
    monkeypatch.setenv("REVIEW_HOUR", "22")
    monkeypatch.setenv("TOURNAMENT_HOUR", "3")
    monkeypatch.setenv("WEEKLY_DAY", "1")
    monkeypatch.setenv("SIGNAL_INTERVAL_S", "600")

    body = signed_in.get("/api/workflows").json()
    assert "as_of" in body
    assert body["rules"][0]["said"] == "prune at 80% and tell me after"
    steps = {s["name"]: s for s in body["schedule"]}
    assert steps["Morning brief"]["hour"] == 6
    assert steps["Daily review"]["hour"] == 22
    assert steps["Judgment tournament"]["hour"] == 3
    assert steps["Weekly summary"]["weekday"] == 1
    assert steps["Weekly summary"]["hour"] == 6
    assert steps["Signals"]["interval_s"] == 600


def test_workflows_clamps_an_hour_the_way_the_scheduler_does(
        signed_in, monkeypatch, memory_backend):
    """Reading the env a second time in the API would show 99 where the
    scheduler actually runs at 23. The scheduler's own helpers are the
    source of truth."""
    async def fetch(sql, *args):
        return []

    memory_backend.fetch = fetch
    monkeypatch.setenv("BRIEF_HOUR", "99")
    monkeypatch.setenv("SIGNAL_INTERVAL_S", "not-a-number")

    body = signed_in.get("/api/workflows").json()
    steps = {s["name"]: s for s in body["schedule"]}
    assert steps["Morning brief"]["hour"] == 23
    assert steps["Signals"]["interval_s"] == 900


def test_workflows_carries_the_graph_catalog_with_real_evidence(
        signed_in, monkeypatch, memory_backend):
    """Task 61: `/api/workflows` also carries every workflow in
    `kernel.workflows.WORKFLOWS`, as a graph — nodes, edges, and a fresh
    read of each node's last run. Nothing here is stubbed, so every read
    either finds nothing (never) or fails against the bare MemoryBackend
    (caught, unknown) — never a 500, and never a guessed OK."""
    from atlas.kernel import workflows as workflows_module

    async def fetch(sql, *args):
        return []

    memory_backend.fetch = fetch

    body = signed_in.get("/api/workflows").json()
    graphs = {g["id"]: g for g in body["graphs"]}
    assert set(graphs) == {w.id for w in workflows_module.WORKFLOWS}

    scheduler = graphs["scheduler"]
    assert scheduler["trigger"] in workflows_module.TRIGGER_KINDS
    assert any(n["kind"] == "trigger" for n in scheduler["nodes"])
    node_ids = {n["id"] for n in scheduler["nodes"]}
    for edge in scheduler["edges"]:
        assert edge["from"] in node_ids and edge["to"] in node_ids
    for node in scheduler["nodes"]:
        assert node["last_outcome"] in workflows_module.OUTCOMES
    assert scheduler["last_run"]["outcome"] in workflows_module.OUTCOMES


def test_workflows_graph_read_failing_never_breaks_the_endpoint(
        signed_in, monkeypatch, memory_backend):
    """A workflow-graph read that raises must not take the rest of the
    endpoint with it — `rules` and `schedule` still answer."""
    from atlas.kernel import workflows as workflows_module

    async def boom(*_a, **_kw):
        raise RuntimeError("connection refused")

    monkeypatch.setattr(workflows_module, "fetch_all", boom)
    monkeypatch.setattr(workflows_module, "fetch_one", boom)

    async def fetch(sql, *args):
        assert "FROM standing_rules" in " ".join(sql.split())
        return []

    memory_backend.fetch = fetch

    resp = signed_in.get("/api/workflows")
    assert resp.status_code == 200
    body = resp.json()
    assert body["rules"] == []
    assert "schedule" in body
    for g in body["graphs"]:
        for node in g["nodes"]:
            if node["kind"] != "trigger":
                assert node["last_outcome"] in ("unknown", "never")


@pytest.fixture
def cold_weather_cache():
    """The weather cache is module-level and survives between tests.

    Without this, the 503 test would be served the previous test's successful
    reading and pass for entirely the wrong reason.
    """
    from atlas.api import dashboard

    dashboard._weather_cache.clear()
    yield dashboard._weather_cache
    dashboard._weather_cache.clear()


def test_ambient_without_coordinates_returns_no_weather_and_says_why(
        signed_in, monkeypatch, cold_weather_cache):
    """Never invent a temperature. Without coordinates there is nothing to
    ask, and the screen must say so rather than show a plausible number."""
    from atlas.modules import _http

    async def forbidden(*_a, **_kw):
        raise AssertionError("no coordinates: nothing should have been fetched")

    monkeypatch.setattr(_http, "get_json", forbidden)
    monkeypatch.delenv("ATLAS_LAT", raising=False)
    monkeypatch.delenv("ATLAS_LON", raising=False)
    monkeypatch.delenv("ATLAS_LOCATION_LABEL", raising=False)

    body = signed_in.get("/api/ambient").json()
    assert "as_of" in body
    assert body["weather"] is None
    assert body["reason"] == "ATLAS_LAT/ATLAS_LON unset"
    assert body["location"] is None
    assert body["clock"]


def test_ambient_reads_open_meteo_and_carries_the_time_it_was_read(
        signed_in, monkeypatch, cold_weather_cache):
    from atlas.modules import _http

    seen: dict = {}

    async def get_json(url, **kwargs):
        seen["url"] = url
        seen["params"] = kwargs.get("params")
        return {"current": {"time": "2026-09-24T11:00",
                            "temperature_2m": 28.4, "weather_code": 3}}, None

    monkeypatch.setattr(_http, "get_json", get_json)
    monkeypatch.setenv("ATLAS_LAT", "32.9")
    monkeypatch.setenv("ATLAS_LON", "73.6")
    monkeypatch.setenv("ATLAS_LOCATION_LABEL", "Bhimber, Pakistan")

    body = signed_in.get("/api/ambient").json()
    assert seen["url"] == "https://api.open-meteo.com/v1/forecast"
    assert seen["params"]["latitude"] == "32.9"
    assert seen["params"]["current"] == "temperature_2m,weather_code"
    assert body["location"] == "Bhimber, Pakistan"
    assert body["reason"] is None
    assert body["weather"]["temperature_c"] == 28.4
    assert body["weather"]["code"] == 3
    assert body["weather"]["description"] == "Overcast"
    assert body["weather"]["observed_at"] == "2026-09-24T11:00"
    assert body["cached"] is False


def test_ambient_reports_the_reason_rather_than_a_plausible_temperature(
        signed_in, monkeypatch, cold_weather_cache):
    from atlas.modules import _http

    async def get_json(_url, **_kwargs):
        return None, "HTTP 503 from api.open-meteo.com"

    monkeypatch.setattr(_http, "get_json", get_json)
    monkeypatch.setenv("ATLAS_LAT", "32.9")
    monkeypatch.setenv("ATLAS_LON", "73.6")

    body = signed_in.get("/api/ambient").json()
    assert body["weather"] is None
    assert body["reason"] == "HTTP 503 from api.open-meteo.com"


def test_an_unmapped_weather_code_reports_the_code_not_a_guessed_word(
        signed_in, monkeypatch, cold_weather_cache):
    from atlas.modules import _http

    async def get_json(_url, **_kwargs):
        return {"current": {"time": "2026-09-24T11:00",
                            "temperature_2m": 9.0, "weather_code": 91}}, None

    monkeypatch.setattr(_http, "get_json", get_json)
    monkeypatch.setenv("ATLAS_LAT", "32.9")
    monkeypatch.setenv("ATLAS_LON", "73.6")

    body = signed_in.get("/api/ambient").json()
    assert body["weather"]["description"] == "WMO code 91"


def test_two_reads_inside_the_ttl_only_ask_open_meteo_once(
        signed_in, monkeypatch, cold_weather_cache):
    """The bottom bar refreshes on every SSE event. The weather does not
    change that fast, and neither should the request rate."""
    from atlas.modules import _http

    calls: list[str] = []

    async def get_json(url, **_kwargs):
        calls.append(url)
        return {"current": {"time": "2026-09-24T11:00",
                            "temperature_2m": 28.4, "weather_code": 3}}, None

    monkeypatch.setattr(_http, "get_json", get_json)
    monkeypatch.setenv("ATLAS_LAT", "32.9")
    monkeypatch.setenv("ATLAS_LON", "73.6")

    first = signed_in.get("/api/ambient").json()
    second = signed_in.get("/api/ambient").json()

    assert len(calls) == 1
    assert first["cached"] is False
    assert second["cached"] is True
    # A cached answer still carries Open-Meteo's own stamp for the reading,
    # not the moment it was served back.
    assert second["weather"]["observed_at"] == "2026-09-24T11:00"
    assert second["weather"]["temperature_c"] == 28.4


def test_moving_the_coordinates_reads_again_rather_than_serving_the_old_place(
        signed_in, monkeypatch, cold_weather_cache):
    from atlas.modules import _http

    calls: list[dict] = []

    async def get_json(_url, **kwargs):
        calls.append(kwargs.get("params") or {})
        return {"current": {"time": "2026-09-24T11:00",
                            "temperature_2m": 28.4, "weather_code": 3}}, None

    monkeypatch.setattr(_http, "get_json", get_json)
    monkeypatch.setenv("ATLAS_LAT", "32.9")
    monkeypatch.setenv("ATLAS_LON", "73.6")
    signed_in.get("/api/ambient")

    monkeypatch.setenv("ATLAS_LAT", "51.5")
    monkeypatch.setenv("ATLAS_LON", "-0.1")
    body = signed_in.get("/api/ambient").json()

    assert len(calls) == 2
    assert calls[1]["latitude"] == "51.5"
    assert body["cached"] is False


def test_a_failed_read_is_not_cached_as_if_it_were_an_answer(
        signed_in, monkeypatch, cold_weather_cache):
    """Holding a failure for ten minutes keeps the bar wrong long after the
    network came back. A stale error is worse than an extra request."""
    from atlas.modules import _http

    calls: list[str] = []

    async def get_json(url, **_kwargs):
        calls.append(url)
        return None, "HTTP 503 from api.open-meteo.com"

    monkeypatch.setattr(_http, "get_json", get_json)
    monkeypatch.setenv("ATLAS_LAT", "32.9")
    monkeypatch.setenv("ATLAS_LON", "73.6")

    signed_in.get("/api/ambient")
    body = signed_in.get("/api/ambient").json()

    assert len(calls) == 2
    assert body["cached"] is False
    assert body["reason"] == "HTTP 503 from api.open-meteo.com"


def test_the_new_mission_endpoints_all_need_a_session(client):
    for path in ("/api/agenda", "/api/tasks", "/api/conversations",
                 "/api/knowledge", "/api/tools", "/api/workflows",
                 "/api/ambient", "/api/leads"):
        assert client.get(path).status_code == 401, path


# --------------------------------------------------------------- leads (G16)
#
# The Lead Pipeline pane: every agency_leads row joined to its latest audit
# and outreach rows, then filtered/sorted/paged in Python (Task 62 -- the
# join is read whole, since the Places budget caps this table at a few
# hundred rows a month). These tests are about that join's shape, the
# outreach-state mapping, and the filter/sort/page/why_empty logic layered
# on top of it -- not about anything being written.

def _lead_row(**over: Any) -> dict[str, Any]:
    base = {
        "lead_id": 1, "name": "Guardian Plumbing", "category": "plumber",
        "address": "12 Elm St", "phone": "+1-555-0100",
        "website": "https://guardianplumbing.example", "score": 0.71,
        "lead_features": {"audit_severity": 2.0, "reviews": 12.0},
        "created_at": datetime(2026, 9, 19, 8, 0, tzinfo=UTC),
        "lead_status": "audited",
        "audit_severity": 2,
        "audit_findings": [
            {"check": "no_https", "severity": 2, "detail": "served over plain http"},
            {"check": "slow", "severity": 1, "detail": "11s on a phone"},
        ],
        "audit_at": datetime(2026, 9, 20, 9, 0, tzinfo=UTC),
        "outreach_id": None, "outreach_status": None, "outreach_kind": None,
        "outreach_at": None, "outreach_to": None,
        "proposal_id": None, "proposal_reason": None, "proposal_expires_at": None,
    }
    base.update(over)
    return base


def _stub_leads(memory_backend, rows: list[dict[str, Any]]) -> None:
    async def fetch(sql, *args):
        flat = " ".join(sql.split())
        if "FROM agency_leads l" in flat:
            return rows
        raise AssertionError(flat)

    memory_backend.fetch = fetch


def test_leads_reports_score_the_worst_finding_and_every_outreach_state(
        signed_in, memory_backend):
    rows = [
        _lead_row(lead_id=1, name="No outreach yet"),
        _lead_row(lead_id=2, name="Drafted, nothing proposed",
                  outreach_id=10, outreach_status="draft", outreach_kind="email",
                  outreach_at=datetime(2026, 9, 21, 9, 0, tzinfo=UTC)),
        _lead_row(lead_id=3, name="Waiting on you",
                  outreach_id=11, outreach_status="draft", outreach_kind="email",
                  outreach_at=datetime(2026, 9, 22, 9, 0, tzinfo=UTC),
                  proposal_id=501, proposal_reason="ready to send"),
        _lead_row(lead_id=4, name="Sent",
                  outreach_id=12, outreach_status="sent", outreach_kind="email",
                  outreach_at=datetime(2026, 9, 23, 9, 0, tzinfo=UTC)),
        _lead_row(lead_id=5, name="Replied",
                  outreach_id=13, outreach_status="replied", outreach_kind="email",
                  outreach_at=datetime(2026, 9, 23, 10, 0, tzinfo=UTC)),
        _lead_row(lead_id=6, name="Bounced", score=None,
                  outreach_id=14, outreach_status="bounced", outreach_kind="email",
                  outreach_at=datetime(2026, 9, 23, 11, 0, tzinfo=UTC)),
    ]
    _stub_leads(memory_backend, rows)

    body = signed_in.get("/api/leads").json()
    assert "as_of" in body
    by_name = {r["business"]: r for r in body["leads"]}

    assert by_name["No outreach yet"]["outreach_state"] is None
    assert by_name["No outreach yet"]["next_step"] is None
    assert by_name["Drafted, nothing proposed"]["outreach_state"] == "drafted"
    assert by_name["Drafted, nothing proposed"]["next_step"] == {
        "kind": "draft", "card_id": 10,
        "label": "review the email draft before it is proposed",
        "expires_at": None,
    }
    assert by_name["Waiting on you"]["outreach_state"] == "waiting on you"
    assert by_name["Waiting on you"]["next_step"] == {
        "kind": "proposal", "card_id": 501, "label": "ready to send",
        "expires_at": None,
    }
    assert by_name["Sent"]["outreach_state"] == "sent"
    assert by_name["Replied"]["outreach_state"] == "replied"
    assert by_name["Bounced"]["outreach_state"] == "bounced"

    # The worst finding, not the first one in the list.
    assert by_name["No outreach yet"]["audit_finding"] == "served over plain http"
    assert by_name["No outreach yet"]["audit_at"] == "2026-09-20T09:00:00+00:00"
    assert by_name["Sent"]["outreach_at"] == "2026-09-23T09:00:00+00:00"

    # A null score stays null -- never guessed, never zero -- and carries no
    # read time, while a real score is stamped with this very read's time
    # (invariant 6).
    assert by_name["Bounced"]["score"] is None
    assert by_name["Bounced"]["score_read_at"] is None
    assert by_name["Sent"]["score"] == 0.71
    assert by_name["Sent"]["score_read_at"] == body["as_of"]

    # Contact info is read straight off the lead row, never invented -- there
    # is no contact-person column, so `name` stays null rather than guessing
    # the business name is a person's name.
    assert by_name["Sent"]["contact"] == {
        "name": None, "email": None, "phone": "+1-555-0100",
        "site": "https://guardianplumbing.example",
    }


def test_leads_with_no_audit_at_all_has_no_finding(signed_in, memory_backend):
    _stub_leads(memory_backend,
                [_lead_row(audit_severity=None, audit_findings=[], audit_at=None)])
    body = signed_in.get("/api/leads").json()
    assert body["leads"][0]["audit_finding"] is None
    assert body["leads"][0]["audit_at"] is None


def test_leads_query_excludes_expired_proposals_from_waiting_on_you(
        signed_in, memory_backend):
    """A pending `agency.send_outreach` proposal that has already expired
    must never read as "waiting on you" -- invariant 5, silence is never
    consent, and an expired proposal never executes. This is enforced by the
    query itself (the LATERAL join only matches a still-open proposal), so
    the guarantee is pinned here at the SQL text rather than at Python
    logic a fake backend cannot exercise against a real clock."""
    async def fetch(sql, *args):
        flat = " ".join(sql.split())
        if "FROM agency_leads l" in flat:
            assert "status = 'pending'" in flat
            assert "expires_at > now()" in flat
            return []
        raise AssertionError(flat)

    memory_backend.fetch = fetch
    assert signed_in.get("/api/leads").json()["leads"] == []


def test_leads_search_matches_business_category_and_contact_fields(
        signed_in, memory_backend):
    rows = [
        _lead_row(lead_id=1, name="Guardian Plumbing", category="plumber",
                  address="12 Elm St", phone="+1-555-0100"),
        _lead_row(lead_id=2, name="Riverside Cafe", category="cafe",
                  address="9 River Rd", phone="+1-555-0200",
                  website="https://riverside.example"),
    ]
    _stub_leads(memory_backend, rows)

    body = signed_in.get("/api/leads", params={"q": "river"}).json()
    names = [r["business"] for r in body["leads"]]
    assert names == ["Riverside Cafe"]
    assert body["total"] == 1

    # Case-insensitive, and matches a phone number too.
    body = signed_in.get("/api/leads", params={"q": "0100"}).json()
    assert [r["business"] for r in body["leads"]] == ["Guardian Plumbing"]


def test_leads_status_filter_matches_lead_status_or_outreach_state(
        signed_in, memory_backend):
    rows = [
        _lead_row(lead_id=1, name="New lead", lead_status="new"),
        _lead_row(lead_id=2, name="Waiting", lead_status="audited",
                  outreach_id=10, outreach_status="draft", outreach_kind="email",
                  outreach_at=datetime(2026, 9, 22, 9, 0, tzinfo=UTC),
                  proposal_id=501),
    ]
    _stub_leads(memory_backend, rows)

    body = signed_in.get("/api/leads", params={"status": "new"}).json()
    assert [r["business"] for r in body["leads"]] == ["New lead"]

    body = signed_in.get("/api/leads", params={"status": "waiting on you"}).json()
    assert [r["business"] for r in body["leads"]] == ["Waiting"]


def test_leads_counts_cover_every_schema_status_and_outreach_state(
        signed_in, memory_backend):
    """The header's chips must never come up short a status the column can
    hold, and must never invent one it cannot (invariant 6)."""
    rows = [
        _lead_row(lead_id=1, name="A", lead_status="new"),
        _lead_row(lead_id=2, name="B", lead_status="new"),
        _lead_row(lead_id=3, name="C", lead_status="dropped"),
    ]
    _stub_leads(memory_backend, rows)

    counts = signed_in.get("/api/leads").json()["counts"]
    assert counts == {
        "new": 2, "audited": 0, "mocked": 0, "contacted": 0, "closed": 0,
        "dropped": 1,
        "drafted": 0, "waiting on you": 0, "sent": 0, "replied": 0, "bounced": 0,
    }


def test_leads_pages_beyond_the_old_hundred_row_cap(signed_in, memory_backend):
    rows = [_lead_row(lead_id=i, name=f"Lead {i}") for i in range(1, 251)]
    _stub_leads(memory_backend, rows)

    body = signed_in.get("/api/leads", params={"limit": 500}).json()
    assert body["total"] == 250
    assert len(body["leads"]) == 250

    page = signed_in.get("/api/leads", params={"limit": 50, "offset": 200}).json()
    assert page["total"] == 250
    assert len(page["leads"]) == 50
    assert page["limit"] == 50 and page["offset"] == 200

    # The limit is still clamped, same as before paging existed.
    huge = signed_in.get("/api/leads", params={"limit": 5000}).json()
    assert huge["limit"] == 500


def test_leads_sort_by_score_puts_unscored_leads_last(signed_in, memory_backend):
    rows = [
        _lead_row(lead_id=1, name="High", score=0.9),
        _lead_row(lead_id=2, name="Unscored", score=None),
        _lead_row(lead_id=3, name="Low", score=0.1),
    ]
    _stub_leads(memory_backend, rows)

    body = signed_in.get(
        "/api/leads", params={"sort": "score", "sort_dir": "desc"}).json()
    assert [r["business"] for r in body["leads"]] == ["High", "Low", "Unscored"]

    body = signed_in.get(
        "/api/leads", params={"sort": "score", "sort_dir": "asc"}).json()
    assert [r["business"] for r in body["leads"]] == ["Unscored", "Low", "High"]


def test_leads_sort_by_business_is_alphabetical(signed_in, memory_backend):
    rows = [
        _lead_row(lead_id=1, name="Zebra Plumbing"),
        _lead_row(lead_id=2, name="Acme Cafe"),
    ]
    _stub_leads(memory_backend, rows)

    body = signed_in.get(
        "/api/leads", params={"sort": "business", "sort_dir": "asc"}).json()
    assert [r["business"] for r in body["leads"]] == ["Acme Cafe", "Zebra Plumbing"]


def test_leads_why_empty_names_the_missing_places_key(signed_in, memory_backend,
                                                        monkeypatch):
    monkeypatch.delenv("GOOGLE_PLACES_API_KEY", raising=False)
    _stub_leads(memory_backend, [])

    body = signed_in.get("/api/leads").json()
    assert body["leads"] == []
    assert body["why_empty"] == (
        "GOOGLE_PLACES_API_KEY is not set, so prospecting has never run.")


def test_leads_why_empty_names_a_disabled_agency_module(signed_in, memory_backend,
                                                          monkeypatch):
    from atlas.kernel.registry import REGISTRY

    monkeypatch.setitem(REGISTRY.disabled, "agency", "missing env: SOMETHING")
    _stub_leads(memory_backend, [])

    body = signed_in.get("/api/leads").json()
    assert body["why_empty"] == "the agency module is disabled: missing env: SOMETHING"


def test_leads_why_empty_is_null_once_the_key_is_set(signed_in, memory_backend,
                                                       monkeypatch):
    monkeypatch.setenv("GOOGLE_PLACES_API_KEY", "test-key")
    _stub_leads(memory_backend, [])

    body = signed_in.get("/api/leads").json()
    assert body["leads"] == []
    assert "agency.prospect has not produced" in body["why_empty"]


def test_leads_why_empty_is_null_when_leads_exist_but_the_filter_matches_none(
        signed_in, memory_backend, monkeypatch):
    """An empty page from a search that matched nothing is a different,
    milder empty state than a genuinely empty pipeline -- `why_empty` must
    not claim the Places key is the problem when it is not."""
    monkeypatch.delenv("GOOGLE_PLACES_API_KEY", raising=False)
    _stub_leads(memory_backend, [_lead_row(lead_id=1, name="Guardian Plumbing")])

    body = signed_in.get("/api/leads", params={"q": "no-such-lead"}).json()
    assert body["leads"] == []
    assert body["why_empty"] is None


def test_lead_detail_returns_audits_outreach_and_a_lead_scoped_timeline(
        signed_in, memory_backend):
    lead_row = _lead_row(lead_id=7, name="Guardian Plumbing",
                          outreach_id=99, outreach_status="draft",
                          outreach_kind="email")

    async def fetchrow(sql, *args):
        flat = " ".join(sql.split())
        if "FROM agency_leads l" in flat:
            assert args == (7,)
            return lead_row
        raise AssertionError(flat)

    async def fetch(sql, *args):
        flat = " ".join(sql.split())
        if "FROM agency_audits" in flat:
            return [{"id": 1, "at": datetime(2026, 9, 20, 9, 0, tzinfo=UTC),
                     "url": "https://guardianplumbing.example", "severity": 2,
                     "findings": [{"check": "no_https", "severity": 2,
                                  "detail": "served over plain http"}],
                     "measured": {"load_ms": 4200}}]
        if "FROM agency_outreach" in flat:
            return [{"id": 99, "at": datetime(2026, 9, 21, 9, 0, tzinfo=UTC),
                     "kind": "email", "to_address": None, "subject": "About it",
                     "body": "Hi there --", "status": "draft", "sent_at": None,
                     "followup_of": None}]
        if "FROM agency_sites" in flat:
            return []
        if "FROM actions_log" in flat:
            return [
                {"id": 501, "at": datetime(2026, 9, 21, 9, 0, tzinfo=UTC),
                 "event": "execute.finish", "subject": "agency.draft_outreach",
                 "payload": json.dumps({"data": {
                     "params": {"lead_id": 7, "kind": "email"},
                     "detail": "drafted email outreach #99 for lead 7"}})},
                {"id": 502, "at": datetime(2026, 9, 19, 9, 0, tzinfo=UTC),
                 "event": "execute.finish", "subject": "agency.audit",
                 "payload": json.dumps({"data": {
                     "params": {"lead_id": 999}, "detail": "unrelated lead"}})},
            ]
        raise AssertionError(flat)

    memory_backend.fetch = fetch
    memory_backend.fetchrow = fetchrow

    body = signed_in.get("/api/leads/7").json()
    assert body["lead"]["business"] == "Guardian Plumbing"
    assert body["audits"][0]["findings"][0]["detail"] == "served over plain http"
    assert body["outreach"][0]["body"] == "Hi there --"
    assert body["score_features"] == {"audit_severity": 2.0, "reviews": 12.0}

    # Only the entry naming this lead's own id, not the other lead's.
    assert [e["id"] for e in body["timeline"]] == [501]
    assert body["timeline"][0]["detail"] == "drafted email outreach #99 for lead 7"


def test_lead_detail_404s_for_an_unknown_lead(signed_in, memory_backend):
    async def fetch(sql, *args):
        return []

    async def fetchrow(sql, *args):
        return None

    memory_backend.fetch = fetch
    memory_backend.fetchrow = fetchrow
    assert signed_in.get("/api/leads/999").status_code == 404


# ------------------------------------------------------------------ brain
#
# `/api/brain` is the memory view behind the humanoid stage: namespaces
# grouped into regions, sized by what is stored and lit by what has actually
# been recalled. Every figure is a fresh count; none of it is remembered
# between requests.

@pytest.fixture
def brain_rows(memory_backend):
    """A dispatcher, because the endpoint asks four different questions and
    MemoryBackend refuses any SQL a test has not stated an answer for."""
    state = {
        "memories": [
            {"namespace": "commitments", "n": 4},
            {"namespace": "people", "n": 11},
            {"namespace": "school", "n": 3},
        ],
        "reads": [
            {"namespace": "people", "last_at": datetime(2026, 9, 24, 10, 0, tzinfo=UTC),
             "reads_1h": 6},
            {"namespace": "commitments",
             "last_at": datetime(2026, 9, 24, 9, 0, tzinfo=UTC), "reads_1h": 3},
        ],
        "recent": [
            {"at": datetime(2026, 9, 24, 10, 0, tzinfo=UTC), "namespace": "people",
             "hits": 2, "agent": "inbox"},
        ],
        "actions": {"n": 7, "last_at": datetime(2026, 9, 24, 8, 0, tzinfo=UTC),
                    "recent": 0},
    }

    async def fetch(sql, *_args):
        flat = " ".join(sql.split())
        if "FROM memories" in flat:
            return state["memories"]
        if "FROM memory_reads" in flat and "ORDER BY at DESC" in flat:
            return state["recent"]
        if "FROM memory_reads" in flat:
            return state["reads"]
        raise AssertionError(f"unexpected query: {flat[:120]}")

    async def fetchrow(sql, *_args):
        flat = " ".join(sql.split())
        if "FROM actions_log" in flat:
            return state["actions"]
        raise AssertionError(f"unexpected query: {flat[:120]}")

    memory_backend.fetch = fetch
    memory_backend.fetchrow = fetchrow
    return state


def regions_by_name(body) -> dict:
    return {r["region"]: r for r in body["regions"]}


def test_brain_groups_namespaces_into_regions_and_counts_what_is_stored(
        signed_in, brain_rows):
    from atlas.api.dashboard import BRAIN_REGIONS

    body = signed_in.get("/api/brain").json()
    assert "as_of" in body
    regions = regions_by_name(body)

    # Every region in the map is present, even one with nothing in it: a
    # brain that quietly dropped its empty regions would look smaller than
    # ATLAS is rather than emptier.
    assert set(regions) == set(BRAIN_REGIONS)

    assert "commitments" in regions["PREFRONTAL"]["namespaces"]
    assert regions["PREFRONTAL"]["memories"] == 4
    assert regions["ASSOCIATION"]["memories"] == 11
    # `school` is in a roster memory_scope and in no region of the map. It
    # lands in OTHER rather than vanishing.
    assert "school" in regions["OTHER"]["namespaces"]
    assert regions["OTHER"]["memories"] == 3


def test_brain_lights_regions_by_recalls_in_the_last_hour(
        signed_in, brain_rows):
    body = signed_in.get("/api/brain").json()
    regions = regions_by_name(body)

    assert regions["ASSOCIATION"]["reads_1h"] == 6
    assert regions["PREFRONTAL"]["reads_1h"] == 3
    # Firing is each region against the busiest one, so the brightest is 1.
    assert regions["ASSOCIATION"]["firing"] == 1.0
    assert regions["PREFRONTAL"]["firing"] == 0.5
    assert regions["ASSOCIATION"]["last_read_at"].startswith("2026-09-24T10:00")


def test_a_region_nothing_has_read_is_quiet_rather_than_unknown(
        signed_in, brain_rows):
    """The one the brief names: no reads is `firing: 0` and no time, not a
    missing key and not a guess."""
    body = signed_in.get("/api/brain").json()
    sensory = regions_by_name(body)["SENSORY"]

    assert sensory["firing"] == 0
    assert sensory["reads_1h"] == 0
    assert sensory["last_read_at"] is None
    assert sensory["memories"] == 0


def test_nothing_read_anywhere_leaves_every_region_dark_rather_than_dividing_by_zero(
        signed_in, brain_rows):
    brain_rows["reads"] = []
    body = signed_in.get("/api/brain").json()

    assert body["regions"]
    assert all(r["firing"] == 0 for r in body["regions"])


def test_motor_is_counted_from_the_action_log_not_from_memories(
        signed_in, brain_rows):
    body = signed_in.get("/api/brain").json()
    motor = regions_by_name(body)["MOTOR"]

    assert motor["memories"] == 7
    assert motor["last_read_at"].startswith("2026-09-24T08:00")
    assert motor["reads_1h"] == 0


def test_brain_carries_the_last_reads_so_the_stage_can_show_what_fired(
        signed_in, brain_rows):
    body = signed_in.get("/api/brain").json()

    assert body["recent"] == [{
        "at": "2026-09-24T10:00:00+00:00",
        "namespace": "people",
        "hits": 2,
        "agent": "inbox",
    }]


def test_brain_needs_a_session(client):
    assert client.get("/api/brain").status_code == 401
# ------------------------------------------------------- guardian feed (18.6)

def test_the_guardian_feed_needs_a_session(client):
    assert client.get("/api/guardian/feed").status_code == 401


def test_the_guardian_feed_is_rows_newest_first_each_with_its_time(
        signed_in, memory_backend):
    """The Incident panel draws only from real rows: the latest
    `guardian.open_incidents` observation, the `guardian.*` proposals (the
    plan steps) and the `guardian.*` audit entries. A step's state is the
    proposal's own status, mapped — and a failed or expired one is never
    drawn as DONE."""
    import json as _json
    obs_at = datetime(2026, 9, 24, 3, 15, tzinfo=UTC)
    t1 = datetime(2026, 9, 24, 3, 20, tzinfo=UTC)
    t2 = datetime(2026, 9, 24, 3, 25, tzinfo=UTC)
    t3 = datetime(2026, 9, 24, 3, 30, tzinfo=UTC)
    plan = {"alert_id": "sec:attacker:web1:203.0.113.9", "operator": "unknown",
            "vectors": [{"kind": "attacker", "target": "203.0.113.9"}],
            "steps": [], "observed_at": obs_at.isoformat()}

    async def fetch(sql, *args):
        flat = " ".join(sql.split())
        if "FROM proposals" in flat:
            assert "LIKE 'guardian.%'" in flat, flat
            assert "ORDER BY p.created_at DESC" in flat, flat
            base = {"action": "guardian.contain", "reason": "block it",
                    "expires_at": t3, "decided_at": None}
            return [
                {**base, "id": 4, "created_at": t3, "status": "expired",
                 "params": {"alert_id": "a", "target": "10.0.0.9",
                            "kind": "isolate_host"}},
                {**base, "id": 3, "created_at": t2, "status": "failed",
                 "params": "{}"},
                {**base, "id": 2, "created_at": t1, "status": "pending",
                 "params": _json.dumps({"alert_id": "a", "target": "203.0.113.9",
                                        "kind": "block_ip"})},
                {**base, "id": 1, "created_at": obs_at, "status": "executed",
                 "params": {}},
                {**base, "id": 0, "created_at": obs_at, "status": "declined",
                 "params": {}},
            ]
        if "FROM actions_log" in flat:
            assert "LIKE 'guardian.%'" in flat, flat
            assert "ORDER BY at DESC" in flat, flat
            return [
                {"id": 9, "at": t3, "event": "execute.watch_only",
                 "subject": "guardian.contain", "payload": "{}"},
                {"id": 8, "at": t2, "event": "execute.finish",
                 "subject": "guardian.contain", "payload": '{"ok": false}'},
                {"id": 7, "at": t1, "event": "proposal.created",
                 "subject": "guardian.contain", "payload": "{}"},
            ]
        raise AssertionError(flat)

    async def fetchrow(sql, *args):
        flat = " ".join(sql.split())
        assert "FROM observations" in flat, flat
        assert args == ("guardian", "open_incidents")
        return {"at": obs_at, "health": "failing", "value": 1.0,
                "detail": "1 open incident", "evidence": _json.dumps(
                    {"incidents": [{"iid": "101"}], "plan": plan,
                     "plan_incident": {"iid": "101", "alert_keys": ["a"]},
                     "total": 1, "incidents_read": 1})}

    memory_backend.fetch = fetch
    memory_backend.fetchrow = fetchrow

    body = signed_in.get("/api/guardian/feed").json()
    assert "as_of" in body
    inc = body["open_incidents"]
    assert inc["value"] == 1.0
    assert inc["at"] == obs_at.isoformat()
    assert inc["health"] == "failing"
    assert inc["plan"]["operator"] == "unknown"
    assert inc["incidents"][0]["iid"] == "101"
    # The panel keys step state on the incident the plan was built for.
    assert inc["plan_incident"] == {"iid": "101", "alert_keys": ["a"]}
    assert inc["total"] == 1 and inc["incidents_read"] == 1

    steps = body["steps"]
    assert [s["id"] for s in steps] == [4, 3, 2, 1, 0]
    assert all(s["at"] for s in steps)
    by_id = {s["id"]: s for s in steps}
    assert by_id[2]["state"] == "PROPOSED"
    assert by_id[2]["params"]["kind"] == "block_ip"
    assert by_id[1]["state"] == "DONE"
    assert by_id[0]["state"] == "REFUSED"
    # Silence is never consent, and a failure is not a success: the raw word.
    assert by_id[4]["state"] is None and by_id[4]["status"] == "expired"
    assert by_id[3]["state"] is None and by_id[3]["status"] == "failed"

    log = body["log"]
    assert [e["id"] for e in log] == [9, 8, 7]
    assert all(e["at"] for e in log)
    states = {e["id"]: e["state"] for e in log}
    assert states == {9: "REFUSED", 8: None, 7: "PROPOSED"}
    # approvals.py audits these with the proposal id as subject, so the
    # guardian.% read cannot see them; no dead mapping pretends otherwise.
    from atlas.api.dashboard import LOG_STATE
    assert "proposal.watch_only" not in LOG_STATE


def test_the_guardian_feed_says_null_when_nothing_was_measured(
        signed_in, memory_backend):
    async def fetch(sql, *args):
        return []

    async def fetchrow(sql, *args):
        return None

    memory_backend.fetch = fetch
    memory_backend.fetchrow = fetchrow
    body = signed_in.get("/api/guardian/feed").json()
    assert body["open_incidents"] is None
    assert body["steps"] == [] and body["log"] == []

def test_conversations_label_a_voice_note_transcript(signed_in, memory_backend):
    """A voice note's text is a machine transcript and may be mis-heard;
    whoever approves a reply against it should see that it is one."""
    at = datetime(2026, 9, 24, 11, 0, tzinfo=UTC)

    async def fetch(sql, *args):
        flat = " ".join(sql.split())
        if "UNION ALL" in flat:
            return [{"n": 2}, {"n": 0}]
        if "FROM messaging_inbound" in flat:
            assert "meta" in flat
            return [
                {"id": 1, "at": at, "platform": "chat", "chat_id": "9",
                 "sender": "Sam", "text": "call me", "replied_at": None,
                 "meta": '{"voice": true, "transcribed": true}'},
                {"id": 2, "at": at, "platform": "chat", "chat_id": "9",
                 "sender": "Sam", "text": "typed", "replied_at": None,
                 "meta": {}},
            ]
        return []

    memory_backend.fetch = fetch
    rows = signed_in.get("/api/conversations").json()["conversations"]
    labels = {r["text"]: r["label"] for r in rows}
    assert labels == {"call me": "voice note (transcribed)", "typed": None}


# ---------------------------------------------------------------- marketing
#
# The marketing board is read from rows, never from a live platform call: the
# screen refetches on every stream event, and a figure on it must be a reading
# with its own time, not a fresh network round trip dressed as one.

MK_NOW = datetime.now(UTC)


def _mk_experiment(eid, *, platform="youtube", outcome=None, posted_ref=None,
                   measure_at=None, baseline=None, observed=None,
                   measured_at=None, lesson=None, draft_id=None):
    return {
        "id": eid, "at": MK_NOW - timedelta(days=3), "business": "flycheap",
        "platform": platform, "hypothesis": f"hypothesis {eid}",
        "variant": f"variant {eid}", "draft_id": draft_id,
        "posted_ref": posted_ref,
        "posted_at": (measure_at - timedelta(hours=48)) if measure_at else None,
        "measure_at": measure_at, "metric": "views_48h", "baseline": baseline,
        "observed": observed, "measured_at": measured_at, "outcome": outcome,
        "lesson": lesson,
    }


def _mk_db(memory_backend, *, experiments=(), proposals=(), memories=(),
           ideas=(), latest=None, base=None, fail=(), social=None, tiktok=()):
    """Answer each of the board's queries from canned rows.

    `latest` / `base` map a check name (`flycheap_stats`) to the observation
    row the endpoint should see. `fail` names sections whose query raises.
    """
    latest = latest or {}
    base = base or {}
    social = social or {}

    async def fetch(sql, *args):
        flat = " ".join(sql.split())
        if "FROM reel_metrics" in flat:
            return list(tiktok)
        if "FROM marketing_experiments" in flat:
            if "experiments" in fail:
                raise RuntimeError("relation marketing_experiments is locked")
            return list(experiments)
        if "FROM proposals" in flat:
            if "plan" in fail:
                raise RuntimeError("proposals unreadable")
            return list(proposals)
        if "FROM memories" in flat:
            assert args[0] == "playbook", args
            return list(memories)
        if "FROM monetization_ideas" in flat:
            # The weekly plan re-files the same idea every Sunday; one card
            # per idea is the database's job, not the screen's.
            assert "DISTINCT ON (business, idea)" in flat, flat
            return list(ideas)
        raise AssertionError(flat)

    async def fetchrow(sql, *args):
        flat = " ".join(sql.split())
        assert "FROM observations o" in flat, flat
        if "channels" in fail:
            raise RuntimeError("observations unreadable")
        if "p.name = $1 AND c.name = $2" in flat:
            return social.get((args[0], args[1]))
        # Never the property's other checks: one named check, one number.
        assert "p.name = 'youtube'" in flat and "c.name = $1" in flat, flat
        if "o.at <= $2" in flat:
            return base.get(args[0])
        return latest.get(args[0])

    memory_backend.fetch = fetch
    memory_backend.fetchrow = fetchrow


def _mk_obs(at, views, subs, *, health="ok", detail="", as_string=True):
    import json as _json
    evidence = {"channel_stats": {"subscribers": subs, "views": views,
                                  "read_at": at.isoformat()}}
    return {"at": at, "health": health, "value": views, "detail": detail,
            "evidence": _json.dumps(evidence) if as_string else evidence}


def test_marketing_sorts_experiments_into_the_four_lanes(signed_in, memory_backend):
    soon = MK_NOW + timedelta(hours=20)
    past = MK_NOW - timedelta(hours=5)
    measured = MK_NOW - timedelta(hours=2)
    _mk_db(
        memory_backend,
        experiments=[
            _mk_experiment(1, draft_id=11),
            _mk_experiment(2, draft_id=12),
            _mk_experiment(3, posted_ref="vid3", measure_at=soon, baseline=100.0),
            _mk_experiment(4, posted_ref="vid4", measure_at=past),
            _mk_experiment(5, posted_ref="vid5", measure_at=past, outcome="won",
                           baseline=100.0, observed=164.0, measured_at=measured,
                           lesson="flycheap · youtube · variant 5: won (+64% …)"),
            _mk_experiment(6, posted_ref="vid6", measure_at=past,
                           outcome="unmeasured", measured_at=measured,
                           lesson="flycheap · youtube · variant 6: unmeasured "
                                  "(analytics 403)"),
            _mk_experiment(7, outcome="unmeasured",
                           lesson="withdrawn before publishing"),
            _mk_experiment(8, platform="instagram", draft_id=18),
        ],
        proposals=[{
            "id": 40, "action": "youtube.set_visibility",
            "params": '{"channel": "flycheap", "video_id": "vid2", '
                      '"privacy": "public", "experiment_id": 2}',
            "reason": "publish the route-deal short", "created_at": MK_NOW,
            "expires_at": MK_NOW + timedelta(hours=3),
        }],
    )

    body = signed_in.get("/api/marketing").json()
    assert "as_of" in body
    for section in ("experiments", "plan"):
        assert body["unavailable"][section] is None, body["unavailable"]

    by_id = {e["id"]: e for e in body["experiments"]}
    assert by_id[1]["stage"] == "draft" and by_id[1]["state"] == "draft"
    assert by_id[2]["stage"] == "awaiting" and by_id[2]["proposal_id"] == 40
    assert by_id[3]["stage"] == "published" and by_id[3]["state"] == "measuring"
    # Past its window with no outcome: the loop has stopped measuring it, and
    # that must not look like an experiment that is merely still running.
    assert by_id[4]["stage"] == "published" and by_id[4]["state"] == "overdue"
    assert by_id[5]["stage"] == "measured" and by_id[5]["state"] == "won"
    assert by_id[5]["delta_pct"] == 64.0
    assert by_id[5]["measured_at"] == measured.isoformat()
    assert by_id[5]["baseline_read_at"] == measured.isoformat()
    # Unmeasured carries its reason and no delta: nobody compared anything.
    assert by_id[6]["state"] == "unmeasured"
    assert by_id[6]["delta_pct"] is None
    assert "analytics 403" in by_id[6]["lesson"]
    assert by_id[7]["state"] == "unmeasured"
    assert by_id[7]["never_published"] is True
    # An open experiment's baseline was read when it was filed.
    assert by_id[3]["baseline_read_at"] == by_id[3]["at"]

    assert body["plan"][0]["experiment_id"] == 2
    assert body["plan"][0]["action"] == "youtube.set_visibility"


def test_marketing_says_an_instagram_experiment_cannot_close(
        signed_in, memory_backend):
    _mk_db(memory_backend, experiments=[
        _mk_experiment(8, platform="instagram", draft_id=18),
        _mk_experiment(9, platform="youtube", draft_id=19),
    ])
    body = signed_in.get("/api/marketing").json()
    assert body["unavailable"]["experiments"] is None
    assert body["hooked_platforms"] == ["youtube"]
    assert set(body["unhooked_platforms"]) == {"instagram", "tiktok"}
    assert "instagram" in body["hook_note"] and "tiktok" in body["hook_note"]
    by_id = {e["id"]: e for e in body["experiments"]}
    assert by_id[8]["can_close"] is False
    assert "no experiment hook" in by_id[8]["cannot_close"]
    assert by_id[9]["can_close"] is True
    assert by_id[9]["cannot_close"] is None


def test_marketing_a_zero_baseline_gives_no_percentage(signed_in, memory_backend):
    at = MK_NOW - timedelta(hours=1)
    _mk_db(memory_backend, experiments=[
        _mk_experiment(5, posted_ref="v", measure_at=at, outcome="flat",
                       baseline=0.0, observed=40.0, measured_at=at,
                       lesson="x: flat"),
    ])
    body = signed_in.get("/api/marketing").json()
    assert body["experiments"][0]["delta_pct"] is None
    assert body["experiments"][0]["observed"] == 40.0


def test_marketing_channel_tiles_carry_the_time_each_figure_was_read(
        signed_in, memory_backend):
    read = MK_NOW - timedelta(hours=3)
    then = read - timedelta(days=28, hours=2)
    _mk_db(
        memory_backend,
        latest={"flycheap_stats": _mk_obs(read, 52_000.0, 1_240),
                "guardian_stats": None},
        base={"flycheap_stats": _mk_obs(then, 40_000.0, 1_100)},
    )
    body = signed_in.get("/api/marketing").json()
    assert body["unavailable"]["channels"] is None
    tiles = {t["channel"]: t for t in body["channels"]}

    fly = tiles["flycheap"]
    assert fly["health"] == "ok"
    assert fly["subscribers"] == 1_240
    assert fly["views_total"] == 52_000.0
    assert fly["read_at"] == read.isoformat()
    # Two readings of a lifetime counter, 28 days apart, both dated.
    assert fly["views_28d"] == 12_000.0
    assert fly["views_28d_since"] == then.isoformat()
    assert fly["views_28d_reason"] is None
    # The real span between the two readings, so the screen never calls a
    # 28-day-and-2-hour subtraction "the last 28 days".
    assert fly["views_28d_span_days"] == 28.1
    assert fly["partner"]["threshold"] == 1_000
    assert fly["partner"]["eligible_on_subscribers"] is True
    assert "not measured" in fly["partner"]["line"]

    # No reading at all is UNKNOWN with a reason — never a zero.
    gua = tiles["guardian"]
    assert gua["health"] == "unknown"
    assert gua["subscribers"] is None and gua["views_28d"] is None
    assert gua["read_at"] is None
    assert gua["reason"]


def test_marketing_a_failed_stats_read_is_unknown_not_the_last_number(
        signed_in, memory_backend):
    failed = MK_NOW - timedelta(minutes=30)
    _mk_db(
        memory_backend,
        latest={"flycheap_stats": _mk_obs(failed, None, None, health="unknown",
                                          detail="flycheap: HTTP 401 from "
                                                 "oauth2.googleapis.com")},
    )
    body = signed_in.get("/api/marketing").json()
    fly = {t["channel"]: t for t in body["channels"]}["flycheap"]
    assert fly["health"] == "unknown"
    assert "401" in fly["reason"]
    assert fly["subscribers"] is None and fly["views_total"] is None
    assert fly["attempted_at"] == failed.isoformat()


def test_marketing_fewer_than_28_days_of_readings_is_said_not_guessed(
        signed_in, memory_backend):
    read = MK_NOW - timedelta(hours=1)
    _mk_db(memory_backend,
           latest={"flycheap_stats": _mk_obs(read, 900.0, 40, as_string=False)},
           base={})
    body = signed_in.get("/api/marketing").json()
    fly = {t["channel"]: t for t in body["channels"]}["flycheap"]
    assert fly["health"] == "ok"
    assert fly["views_28d"] is None
    assert fly["views_28d_span_days"] is None
    assert fly["views_28d_reason"] == "fewer than 28 days of readings"
    assert fly["partner"]["eligible_on_subscribers"] is False


def test_marketing_a_reading_far_past_28_days_is_not_a_28_day_span(
        signed_in, memory_backend):
    """The daily check can miss a day or three; a base reading 40 days back
    would make a 40-day subtraction wearing a 28-day label."""
    read = MK_NOW - timedelta(hours=1)
    then = read - timedelta(days=40)
    _mk_db(memory_backend,
           latest={"flycheap_stats": _mk_obs(read, 900.0, 40)},
           base={"flycheap_stats": _mk_obs(then, 100.0, 10)})
    body = signed_in.get("/api/marketing").json()
    assert body["unavailable"]["channels"] is None
    fly = {t["channel"]: t for t in body["channels"]}["flycheap"]
    assert fly["views_28d"] is None
    assert fly["views_28d_span_days"] is None
    assert fly["views_28d_since"] is None
    assert fly["views_28d_reason"].startswith("no reading near 28 days back")


def test_marketing_playbook_is_newest_first_with_its_delta(
        signed_in, memory_backend):
    newer = MK_NOW - timedelta(hours=1)
    older = MK_NOW - timedelta(days=4)
    _mk_db(memory_backend, memories=[
        {"id": 2, "at": newer, "content": "flycheap · youtube · a: lost (-30%)",
         "meta": '{"outcome": "lost", "business": "flycheap", '
                 '"platform": "youtube", "variant": "a", "observed": 70, '
                 '"baseline": 100, "metric": "views_48h"}'},
        {"id": 1, "at": older, "content": "guardian · youtube · b: unmeasured "
                                          "(baseline has 2 of 5)",
         "meta": {"outcome": "unmeasured", "business": "guardian",
                  "platform": "youtube", "variant": "b", "observed": None,
                  "baseline": None, "metric": "views_48h"}},
    ])
    body = signed_in.get("/api/marketing").json()
    assert body["unavailable"]["playbook"] is None
    first, second = body["playbook"]
    assert first["at"] == newer.isoformat()
    assert first["outcome"] == "lost" and first["delta_pct"] == -30.0
    assert second["outcome"] == "unmeasured" and second["delta_pct"] is None


def test_marketing_ideas_are_ranked_deduped_and_never_undated(
        signed_in, memory_backend):
    at = MK_NOW - timedelta(days=1)
    fact = {"value": 1240, "observed_at": at.isoformat(),
            "detail": "1,240 subscribers, read 09:00"}
    _mk_db(memory_backend, ideas=[
        {"id": 3, "at": at, "business": "flycheap",
         "idea": "Paid placement on a deals newsletter",
         "mechanism": "paid_acquisition", "evidence": {"users": fact},
         "requires": "money out — Tier 3, you decide",
         "est_note": "listed, never proposed", "score": 0.05,
         "status": "proposed"},
        {"id": 4, "at": at, "business": "flycheap",
         "idea": "YouTube Partner Program", "mechanism": "partner_program",
         "evidence": '{"subscribers": {"value": 1240, "observed_at": "'
                     + at.isoformat() + '", "detail": "read 09:00"},'
                     ' "guess": {"value": 99999, "detail": "no time"}}',
         "requires": "enable monetisation in YouTube Studio — only you can",
         "est_note": "prose", "score": 0.33, "status": "proposed"},
        {"id": 5, "at": at, "business": "guardian", "idea": "Dropped one",
         "mechanism": "affiliate", "evidence": {}, "requires": "",
         "est_note": "", "score": 0.9, "status": "dropped"},
    ])
    body = signed_in.get("/api/marketing").json()
    assert body["unavailable"]["ideas"] is None
    ideas = body["ideas"]
    assert [i["id"] for i in ideas] == [4, 3]
    assert ideas[1]["money_out"] is True and ideas[0]["money_out"] is False
    facts = {f["name"]: f for f in ideas[0]["evidence"]}
    assert facts["subscribers"]["value"] == 1240
    assert facts["subscribers"]["observed_at"] == at.isoformat()
    # A fact with no read time is not a measurement, so its number is not
    # put on screen at all.
    assert facts["guess"]["undated"] is True
    assert facts["guess"]["value"] is None


def test_marketing_a_failed_query_is_unavailable_never_empty(
        signed_in, memory_backend):
    _mk_db(memory_backend, fail=("experiments",))
    response = signed_in.get("/api/marketing")
    assert response.status_code == 200
    body = response.json()
    assert body["experiments"] == []
    assert "locked" in body["unavailable"]["experiments"]
    # The other sections still read.
    assert body["unavailable"]["playbook"] is None
    assert body["unavailable"]["ideas"] is None


def test_marketing_says_when_no_weekly_filing_has_been_seen(
        signed_in, memory_backend, monkeypatch):
    from atlas.kernel.marketing import cycle

    monkeypatch.setattr(cycle, "_last_filing", cycle.Filing(0, 0, (), None))
    _mk_db(memory_backend)
    body = signed_in.get("/api/marketing").json()
    assert body["filing"]["at"] is None
    assert "restart" in body["filing"]["note"]

    filed_at = MK_NOW - timedelta(days=2)
    monkeypatch.setattr(cycle, "_last_filing",
                        cycle.Filing(3, 1, ("rate limit",), filed_at))
    body = signed_in.get("/api/marketing").json()
    assert body["filing"] == {"filed": 3, "refused": 1,
                              "reasons": ["rate limit"],
                              "at": filed_at.isoformat(), "note": None}
    assert isinstance(body["watch_only"], bool)


def test_marketing_needs_a_session(client):
    assert client.get("/api/marketing").status_code == 401


def test_marketing_an_expired_proposal_is_not_awaiting_approval(
        signed_in, memory_backend):
    """Silence is never consent: a pending row past its expiry can no longer
    be answered, so the experiment it names is not waiting on anyone."""
    _mk_db(
        memory_backend,
        experiments=[_mk_experiment(2, draft_id=12)],
        proposals=[{
            "id": 41, "action": "youtube.set_visibility",
            "params": {"channel": "flycheap", "video_id": "vid2",
                       "privacy": "public", "experiment_id": 2},
            "reason": "publish it", "created_at": MK_NOW - timedelta(hours=30),
            "expires_at": MK_NOW - timedelta(hours=6),
        }],
    )
    body = signed_in.get("/api/marketing").json()
    assert body["unavailable"]["plan"] is None
    assert body["unavailable"]["experiments"] is None
    card = body["experiments"][0]
    assert card["stage"] == "draft"
    assert card["proposal_id"] is None
    assert body["plan"][0]["expired"] is True


# --------------------------------------------------------------- media route

def test_the_media_route_is_reachable_off_mesh_with_no_session(
        monkeypatch, memory_backend, tmp_path):
    """Instagram's Graph API fetches this from the public internet, not the
    mesh -- so it must work from an address `MESH_ALLOWED_CIDRS` would
    otherwise refuse, and without a dashboard session, unlike everything
    else this app serves."""
    from atlas.api import media as media_module

    monkeypatch.setenv("SESSION_SECRET", SECRET)
    monkeypatch.setenv("MESH_ALLOWED_CIDRS", "100.64.0.0/10")
    monkeypatch.setattr(media_module, "_ROOT", str(tmp_path))
    video = tmp_path / "7.mp4"
    video.write_bytes(b"pretend mp4 bytes")

    async def fake_fetch_one(sql, *args):
        assert args == ("good-token",)
        return {"path": str(video)}

    monkeypatch.setattr(media_module, "fetch_one", fake_fetch_one)

    from atlas.api.main import app

    async def no_boot() -> None:
        return None
    monkeypatch.setattr("atlas.api.main.boot", no_boot)
    monkeypatch.setattr("atlas.api.main.scheduler.loop", no_boot)

    with TestClient(app, client=("203.0.113.9", 4000)) as outsider:
        response = outsider.get("/media/good-token")
        assert response.status_code == 200
        assert response.content == b"pretend mp4 bytes"


def test_the_media_route_404s_for_an_unknown_or_expired_token(
        monkeypatch, memory_backend):
    """An expired token's row never matches the query's own
    `media_token_expires_at > now()` clause, so it comes back exactly like
    an unknown one -- a prober cannot tell the two apart."""
    from atlas.api import media as media_module

    async def fake_fetch_one(sql, *args):
        flat = " ".join(sql.split())
        assert "media_token_expires_at > now()" in flat
        assert "status = 'done'" in flat

    monkeypatch.setattr(media_module, "fetch_one", fake_fetch_one)

    from atlas.api.main import app

    async def no_boot() -> None:
        return None
    monkeypatch.setattr("atlas.api.main.boot", no_boot)
    monkeypatch.setattr("atlas.api.main.scheduler.loop", no_boot)
    monkeypatch.setenv("MESH_ALLOWED_CIDRS", "0.0.0.0/0")

    with TestClient(app, client=("127.0.0.1", 51000)) as c:
        assert c.get("/media/no-such-token").status_code == 404
        assert c.get("/media/an-expired-token").status_code == 404


def test_the_media_route_404s_for_a_rolled_back_render(
        monkeypatch, memory_backend, tmp_path):
    """`render_video_rollback` marks a media row `'failed'` without
    deleting it or clearing its token -- the query's own `status = 'done'`
    clause is what stops a rolled-back render from staying fetchable."""
    from atlas.api import media as media_module

    video = tmp_path / "1.mp4"
    video.write_bytes(b"stale bytes")
    monkeypatch.setattr(media_module, "_ROOT", str(tmp_path))

    # A fake that mirrors the real query's own WHERE clause, so this proves
    # the clause -- not just the handler -- does the work.
    fake_row = {"path": str(video), "status": "failed"}

    async def fake_fetch_one(sql, *args):
        if "status = 'done'" in " ".join(sql.split()) and fake_row["status"] != "done":
            return None
        return {"path": fake_row["path"]}

    monkeypatch.setattr(media_module, "fetch_one", fake_fetch_one)

    from atlas.api.main import app

    async def no_boot() -> None:
        return None
    monkeypatch.setattr("atlas.api.main.boot", no_boot)
    monkeypatch.setattr("atlas.api.main.scheduler.loop", no_boot)
    monkeypatch.setenv("MESH_ALLOWED_CIDRS", "0.0.0.0/0")

    with TestClient(app, client=("127.0.0.1", 51000)) as c:
        assert c.get("/media/rolled-back-token").status_code == 404


def test_the_media_route_confines_the_served_path_to_data_content(
        monkeypatch, memory_backend, tmp_path):
    """A `content_media.path` that somehow points outside DATA_DIR (a
    traversal payload, or a future bug) is refused rather than served --
    the row is the only input, but it is not trusted blindly."""
    from atlas.api import media as media_module

    outside = tmp_path / "outside"
    outside.mkdir()
    secret = outside / "secret.txt"
    secret.write_text("not for Instagram")
    confined = tmp_path / "confined"
    confined.mkdir()
    monkeypatch.setattr(media_module, "_ROOT", str(confined))

    async def fake_fetch_one(sql, *args):
        return {"path": str(secret)}

    monkeypatch.setattr(media_module, "fetch_one", fake_fetch_one)

    from atlas.api.main import app

    async def no_boot() -> None:
        return None
    monkeypatch.setattr("atlas.api.main.boot", no_boot)
    monkeypatch.setattr("atlas.api.main.scheduler.loop", no_boot)
    monkeypatch.setenv("MESH_ALLOWED_CIDRS", "0.0.0.0/0")

    with TestClient(app, client=("127.0.0.1", 51000)) as c:
        response = c.get("/media/escaping-token")
        assert response.status_code == 404


def test_the_media_route_404s_when_the_row_has_no_file_on_disk(
        monkeypatch, memory_backend, tmp_path):
    from atlas.api import media as media_module

    monkeypatch.setattr(media_module, "_ROOT", str(tmp_path))

    async def fake_fetch_one(sql, *args):
        return {"path": str(tmp_path / "never-written.mp4")}

    monkeypatch.setattr(media_module, "fetch_one", fake_fetch_one)

    from atlas.api.main import app

    async def no_boot() -> None:
        return None
    monkeypatch.setattr("atlas.api.main.boot", no_boot)
    monkeypatch.setattr("atlas.api.main.scheduler.loop", no_boot)
    monkeypatch.setenv("MESH_ALLOWED_CIDRS", "0.0.0.0/0")

    with TestClient(app, client=("127.0.0.1", 51000)) as c:
        assert c.get("/media/dangling-token").status_code == 404


def test_the_media_route_404s_a_traversal_attempt_in_the_url_itself(
        monkeypatch, memory_backend):
    """The token is a database lookup key, never a filesystem path -- a
    request that tries to smuggle a traversal INTO the token (a literal
    `/` via `%2F`, so it survives as more than one path segment) matches no
    route at all: `/media/{token}` takes exactly one segment, and `media/`
    is excluded from the SPA catch-all, so this can never fall through to
    index.html either."""
    from atlas.api import media as media_module

    async def boom(sql, *args):
        raise AssertionError("a multi-segment path must never reach the query")

    monkeypatch.setattr(media_module, "fetch_one", boom)

    from atlas.api.main import app

    async def no_boot() -> None:
        return None
    monkeypatch.setattr("atlas.api.main.boot", no_boot)
    monkeypatch.setattr("atlas.api.main.scheduler.loop", no_boot)
    monkeypatch.setenv("MESH_ALLOWED_CIDRS", "0.0.0.0/0")

    with TestClient(app, client=("127.0.0.1", 51000)) as c:
        response = c.get("/media/..%2F..%2Fdb%2Fschema.sql")
        assert response.status_code == 404
        assert "<!doctype html" not in response.text.lower()


async def _raw_asgi_get(app, path: str, client_host: str) -> int:
    """One GET sent straight into the ASGI app with `path` exactly as
    given, bypassing httpx's own client-side URL normalization -- which
    would otherwise collapse a literal `..` before this test ever saw
    whether Starlette or the mesh-only middleware does. This is the same
    thing the reviewer's probe did by hand; pinning it as a test."""
    messages = []

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message):
        messages.append(message)

    scope = {
        "type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1",
        "method": "GET", "scheme": "http", "path": path,
        "raw_path": path.encode(), "query_string": b"", "headers": [],
        "client": (client_host, 4000), "server": ("testserver", 80),
    }
    await app(scope, receive, send)
    return next(m["status"] for m in messages if m["type"] == "http.response.start")


def test_the_media_route_404s_an_off_mesh_dot_segment_escape(
        monkeypatch, memory_backend):
    """`/media/` is a prefix match in PUBLIC_PATHS, so an off-mesh request
    starting with it skips the mesh guard entirely -- a literal `..` in the
    path must not then land on a mesh-only route. Sent as a raw ASGI
    request (see `_raw_asgi_get`) so httpx's own normalization cannot hide
    whether the app's own defenses would have caught it."""
    from atlas.api import media as media_module

    async def boom(sql, *args):
        raise AssertionError("a dot-segment escape must never reach the query")

    monkeypatch.setattr(media_module, "fetch_one", boom)

    from atlas.api.main import app

    async def no_boot() -> None:
        return None
    monkeypatch.setattr("atlas.api.main.boot", no_boot)
    monkeypatch.setattr("atlas.api.main.scheduler.loop", no_boot)
    # Deliberately narrow: an off-mesh client must be refused everywhere
    # except the paths actually in PUBLIC_PATHS.
    monkeypatch.setenv("MESH_ALLOWED_CIDRS", "100.64.0.0/10")

    with TestClient(app, client=("203.0.113.9", 4000)):
        for path in ("/media/../agents", "/media/../api/overview",
                     "/media//api/agents"):
            status = asyncio.run(_raw_asgi_get(app, path, "203.0.113.9"))
            assert status == 404, (path, status)


def test_the_media_route_head_is_answered_not_405(
        monkeypatch, memory_backend, tmp_path):
    """HEAD is looked up the same way GET is -- same 404s, same
    confinement -- rather than left to FastAPI's default 405, which would
    hand a prober "this path exists, only GET works" for a route whose
    whole design is that every miss looks identical."""
    from atlas.api import media as media_module

    monkeypatch.setattr(media_module, "_ROOT", str(tmp_path))
    video = tmp_path / "9.mp4"
    video.write_bytes(b"twelve bytes")

    async def fake_fetch_one(sql, *args):
        return {"path": str(video)}

    monkeypatch.setattr(media_module, "fetch_one", fake_fetch_one)

    from atlas.api.main import app

    async def no_boot() -> None:
        return None
    monkeypatch.setattr("atlas.api.main.boot", no_boot)
    monkeypatch.setattr("atlas.api.main.scheduler.loop", no_boot)
    monkeypatch.setenv("MESH_ALLOWED_CIDRS", "0.0.0.0/0")

    with TestClient(app, client=("127.0.0.1", 51000)) as c:
        response = c.head("/media/good-token")
        assert response.status_code == 200
        assert response.content == b""
        assert response.headers.get("content-length") == "12"


def test_the_media_route_head_404s_for_an_unknown_token(
        monkeypatch, memory_backend):
    from atlas.api import media as media_module

    async def fake_fetch_one(sql, *args):
        return None

    monkeypatch.setattr(media_module, "fetch_one", fake_fetch_one)

    from atlas.api.main import app

    async def no_boot() -> None:
        return None
    monkeypatch.setattr("atlas.api.main.boot", no_boot)
    monkeypatch.setattr("atlas.api.main.scheduler.loop", no_boot)
    monkeypatch.setenv("MESH_ALLOWED_CIDRS", "0.0.0.0/0")

    with TestClient(app, client=("127.0.0.1", 51000)) as c:
        response = c.head("/media/no-such-token")
        assert response.status_code == 404


def test_the_media_route_refuses_a_symlink_even_when_it_resolves_inside(
        monkeypatch, memory_backend, tmp_path):
    """Only `render_video` ever writes under DATA_DIR, and always a plain
    file -- a symlink there at all is the anomaly, so it is refused even
    when it happens to resolve back inside DATA_DIR, not only when it
    escapes."""
    from atlas.api import media as media_module

    monkeypatch.setattr(media_module, "_ROOT", str(tmp_path))
    real_file = tmp_path / "real.mp4"
    real_file.write_bytes(b"actual video")
    link = tmp_path / "linked.mp4"
    link.symlink_to(real_file)

    async def fake_fetch_one(sql, *args):
        return {"path": str(link)}

    monkeypatch.setattr(media_module, "fetch_one", fake_fetch_one)

    from atlas.api.main import app

    async def no_boot() -> None:
        return None
    monkeypatch.setattr("atlas.api.main.boot", no_boot)
    monkeypatch.setattr("atlas.api.main.scheduler.loop", no_boot)
    monkeypatch.setenv("MESH_ALLOWED_CIDRS", "0.0.0.0/0")

    with TestClient(app, client=("127.0.0.1", 51000)) as c:
        assert c.get("/media/symlinked-token").status_code == 404


def test_the_media_route_refuses_a_symlink_that_escapes_data_content(
        monkeypatch, memory_backend, tmp_path):
    """The literal case the confinement check exists for: a symlink under
    DATA_DIR whose target is outside it."""
    from atlas.api import media as media_module

    outside = tmp_path / "outside"
    outside.mkdir()
    secret = outside / "secret.txt"
    secret.write_text("not for Instagram")
    confined = tmp_path / "confined"
    confined.mkdir()
    link = confined / "escape.mp4"
    link.symlink_to(secret)
    monkeypatch.setattr(media_module, "_ROOT", str(confined))

    async def fake_fetch_one(sql, *args):
        return {"path": str(link)}

    monkeypatch.setattr(media_module, "fetch_one", fake_fetch_one)

    from atlas.api.main import app

    async def no_boot() -> None:
        return None
    monkeypatch.setattr("atlas.api.main.boot", no_boot)
    monkeypatch.setattr("atlas.api.main.scheduler.loop", no_boot)
    monkeypatch.setenv("MESH_ALLOWED_CIDRS", "0.0.0.0/0")

    with TestClient(app, client=("127.0.0.1", 51000)) as c:
        assert c.get("/media/escaping-symlink-token").status_code == 404


async def test_the_acknowledge_button_mutes_and_confirms_never_decides(
        client, monkeypatch, memory_backend):
    """Acknowledge on an alert records the ack and says so in the channel.
    It is not a decision about any action and must never reach decide()."""
    monkeypatch.setenv("SLACK_SIGNING_SECRET", "s3cret")
    monkeypatch.setenv("SLACK_APPROVER_IDS", "UMIRO")

    async def fake_decide(*a, **k):
        raise AssertionError("Acknowledge must never reach decide()")
    monkeypatch.setattr("atlas.api.main.decide", fake_decide)

    acked = []

    async def fake_ack(key, via, **k):
        acked.append((key, via))
        return "failing"
    monkeypatch.setattr("atlas.kernel.acks.ack", fake_ack)

    posted = []

    async def fake_reply(url, text, *, ephemeral=False):
        posted.append(text)
    monkeypatch.setattr("atlas.api.main._slack_reply", fake_reply)

    r = _slack_post(client, {
        "user": {"id": "UMIRO"}, "response_url": "https://hooks.slack/x",
        "actions": [{"value": "ack:vpsie.balance"}]})
    assert r.status_code == 200
    assert acked == [("vpsie.balance", "slack:UMIRO")]
    assert "Acknowledged vpsie.balance (failing)" in posted[0]



def test_marketing_channels_include_instagram_and_tiktok(signed_in, memory_backend):
    """Miro, 2026-09-27: "include Instagram channels and TikTok as well"."""
    read = MK_NOW - timedelta(hours=1)
    _mk_db(memory_backend, social={
        ("instagram", "main_token"): {"at": read, "health": "ok", "value": 412.0,
                                      "detail": "@flycheap.live 412 followers",
                                      "evidence": "{}"},
        ("instagram", "main_engagement"): {
            "at": read, "health": "ok", "value": 57.0,
            "detail": "reach 57/day", "evidence": '{"profile_views": 23}'},
        ("instagram", "stories_token"): {"at": read, "health": "unknown",
                                         "value": None, "detail": "token expired",
                                         "evidence": "{}"},
    }, tiktok=[{"post_ref": "pz-3", "at": read, "views": 12500.0, "likes": 900.0},
               {"post_ref": "pz-4", "at": read, "views": None, "likes": None}])
    body = signed_in.get("/api/marketing").json()
    tiles = [t for t in body["channels"] if t.get("platform") != "YouTube"]
    ig_main, ig_stories, tiktok = tiles
    assert ig_main["platform"] == "Instagram" and ig_main["channel"] == "@flycheap.live"
    assert ig_main["health"] == "ok"
    assert {f["label"]: f["value"] for f in ig_main["figures"]} == {
        "followers": 412.0, "reach / day": 57.0, "profile views / day": 23}
    assert ig_stories["health"] == "unknown" and ig_stories["reason"] == "token expired"
    assert tiktok["platform"] == "TikTok" and tiktok["health"] == "ok"
    figs = {f["label"]: f["value"] for f in tiktok["figures"]}
    assert figs["posts measured, 28 days"] == 2 and figs["views, 28 days"] == 12500.0


def test_a_check_that_is_not_set_up_or_paused_is_set_aside_not_ok():
    """2026-09-27: the status board should be clean. A missing setting or a
    deliberate back-off is named, grey, and left out of the module's colour
    -- and it is still UNKNOWN underneath, never OK."""
    from atlas.api.dashboard import _check_state
    assert _check_state("unknown", "INSTAGRAM_TOKEN not set") == {
        "kind": "not_set_up", "label": "not set up"}
    paused = _check_state("unknown", (
        "Fiverr's bot wall (PerimeterX) blocked the last attempt at 22:02 UTC; "
        "not trying again until Fri 22:02 UTC so the account is not flagged."))
    assert paused == {"kind": "paused", "label": "paused until Fri 22:02 UTC (bot wall)"}
    assert _check_state("unknown", "HTTP 400 from /v21.0/1784/conversations") is None
    assert _check_state("ok", "INSTAGRAM_TOKEN not set") is None
    assert _check_state("degraded", "not trying again until Fri") is None


def test_a_retired_check_does_not_paint_a_live_module_unknown(
        signed_in, memory_backend, monkeypatch):
    """vpsie 'snapshots' was replaced by 'protection' but its rows stayed; its
    last reading (six days old) turned a healthy VPSie tile UNKNOWN. A live
    module shows only the checks it still registers."""
    from datetime import datetime, timedelta

    from atlas.kernel.registry import REGISTRY
    now = datetime.now(UTC)
    rows = [
        {"property": "vpsie", "check_name": "protection", "last_run_at": now,
         "next_run_at": now, "interval_s": 300, "health": "ok", "detail": "",
         "observed_at": now},
        {"property": "vpsie", "check_name": "snapshots", "last_run_at": now,
         "next_run_at": now, "interval_s": 300, "health": "degraded",
         "detail": "", "observed_at": now - timedelta(days=6)},
    ]

    async def fetch(sql, *args):
        return rows if "FROM checks c" in " ".join(sql.split()) else []

    async def fetchrow(sql, *args):
        return {"n": 0}

    memory_backend.fetch = fetch
    memory_backend.fetchrow = fetchrow
    monkeypatch.setitem(REGISTRY.modules, "vpsie", object())
    monkeypatch.setitem(REGISTRY.checks, "vpsie.protection", object())
    body = signed_in.get("/api/overview").json()
    tile = next(m for m in body["modules"] if m["module"] == "vpsie")
    assert [c["name"] for c in tile["checks"]] == ["protection"]
    assert tile["health"] == "ok"


# ------------------------------- Miro's own device, off the mesh, by cert

FP = "ab" * 32


def test_a_pinned_device_certificate_passes_off_the_mesh(monkeypatch, memory_backend):
    monkeypatch.setenv("ATLAS_DEVICE_FINGERPRINTS", f"{'cd' * 32}, {FP.upper()}")
    with TestClient(_app(monkeypatch), client=("172.18.0.5", 40000)) as c:
        response = c.get("/api/session", headers={
            "X-Forwarded-For": "203.0.113.9", "X-Atlas-Device": FP})
        assert response.status_code == 200


def test_an_unpinned_or_missing_device_is_still_refused(monkeypatch, memory_backend):
    monkeypatch.setenv("ATLAS_DEVICE_FINGERPRINTS", FP)
    with TestClient(_app(monkeypatch), client=("172.18.0.5", 40000)) as c:
        for headers in ({}, {"X-Atlas-Device": "ef" * 32}, {"X-Atlas-Device": ""}):
            response = c.get("/api/session",
                             headers={"X-Forwarded-For": "203.0.113.9", **headers})
            assert response.status_code == 404


def test_the_device_header_is_ignored_from_anyone_but_our_proxy(monkeypatch, memory_backend):
    """A public client cannot claim a device by sending the header itself."""
    monkeypatch.setenv("ATLAS_DEVICE_FINGERPRINTS", FP)
    with TestClient(_app(monkeypatch), client=("203.0.113.9", 4000)) as c:
        assert c.get("/api/session", headers={"X-Atlas-Device": FP}).status_code == 404


def test_no_pinned_devices_means_mesh_only(monkeypatch, memory_backend):
    monkeypatch.delenv("ATLAS_DEVICE_FINGERPRINTS", raising=False)
    with TestClient(_app(monkeypatch), client=("172.18.0.5", 40000)) as c:
        response = c.get("/api/session", headers={
            "X-Forwarded-For": "203.0.113.9", "X-Atlas-Device": FP})
        assert response.status_code == 404
