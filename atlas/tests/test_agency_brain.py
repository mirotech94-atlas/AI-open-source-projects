"""`GET /api/agency/brain`: the departments' numbers, from the record.

Proved here:
  * the endpoint needs a session, and nothing it runs can act or think;
  * against a real Postgres (ATLAS_TEST_PG_DSN), seeded through the real
    audit chain writer: % runs without you = Tier 1 executions over Tier 1
    + Tier 2 ones, per department and for the whole business; refusals and
    tierless rows are not executions; an action no department holds is
    `unmapped`, and the parts add up to the whole; no executions at all is
    unknown (null), never 0% or 100%;
  * waiting on you is the badge's own count (`status = 'pending'`), split by
    department with the rest unmapped, and the split adds up;
  * seats that ran come from agent_runs and schedule_runs; an interval job
    has no record and reads unknown, not 0; a pause lands on its
    department.
"""
from __future__ import annotations

import ast
import pathlib
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from atlas.api import auth
from atlas.kernel import agents as agent_module
from atlas.kernel import brain, state, verify
from atlas.kernel.audit import audit
from atlas.kernel.registry import REGISTRY
from tests.acceptance_support import (
    needs_pg,
    pg_fixture,  # noqa: F401
    registries_fixture,  # noqa: F401
)

SECRET = "s" * 40
PASSWORD = "a-long-enough-password"


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
    with TestClient(app, client=("127.0.0.1", 51009)) as test_client:
        yield test_client


def test_the_brain_needs_a_session(client):
    assert client.get("/api/agency/brain").status_code == 401


def test_the_brain_is_read_only_and_never_reaches_the_executor_or_a_model():
    """Walked in the AST of both files, not trusted from a docstring."""
    root = pathlib.Path(__file__).parent.parent / "atlas" / "kernel"
    for name in ("brain.py", "departments.py"):
        tree = ast.parse((root / name).read_text())
        names = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
        names |= {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)}
        imported = {a.name for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)
                    for a in n.names}
        for forbidden in ("execute", "invoke", "think", "run_agent", "warmup",
                          "execute_sql", "run_tier1", "propose", "decide"):
            assert forbidden not in names | imported, (name, forbidden)


# ------------------------------------------------------------ against PG

def _load(*modules: str) -> None:
    for m in modules:
        REGISTRY.load(f"atlas.modules.{m}", m)


async def _truncate() -> None:
    await state.execute_sql("TRUNCATE agent_runs, schedule_runs RESTART IDENTITY")


async def _start(action: str, tier: int | None) -> None:
    data = {"params": {}, "reason": "test", "approved_by": None}
    if tier is not None:
        data["tier"] = tier
    await audit("execute.start", action, data)


async def _card(action: str, status: str = "pending") -> None:
    await state.execute_sql(
        """INSERT INTO proposals (expires_at, action, tier, reason, status)
           VALUES (now() + interval '1 day', $1, 2, 'test', $2)""", action, status)


def _dept(out: dict, key: str) -> dict:
    return next(d for d in out["departments"] if d["key"] == key)


@pytest.fixture
async def world(pg, monkeypatch):
    # Lookup only: the real has_verifier imports each module's readback.py
    # once (verify._LOADED), and a first import inside a test would outlive
    # the registries snapshot's restore -- later read-back tests would find
    # the module "loaded" and its verifiers gone.
    monkeypatch.setattr(verify, "has_verifier", lambda name: name in verify._VERIFIERS)
    _load("markets", "content")
    agent_module.build_roster()
    await _truncate()
    yield


@needs_pg
async def test_no_executions_reads_unknown_everywhere(world):
    out = await brain.build()
    assert out["errors"] == {}
    assert out["whole"]["runs_without_you"]["pct"] is None
    assert out["whole"]["runs_without_you"]["executions"] == 0
    for d in out["departments"]:
        assert d["runs_without_you"]["pct"] is None, d["key"]
        assert d["waiting_on_you"] == 0
    assert out["whole"]["waiting_on_you"] == 0


@needs_pg
async def test_runs_without_you_is_tier_1_over_all_executions(world):
    for _ in range(3):
        await _start("markets.alert", 1)
    await _start("markets.paper_idea", 2)
    await _start("markets.alert", None)                 # no tier: left out
    await audit("execute.refused", "markets.alert", {"why": "watch only"})
    await audit("execute.dry_run", "content.draft_reel", {"ok": True})
    await _start("gone.renamed_action", 1)               # no department
    await _start("content.publish_reel", 2)

    out = await brain.build()
    money = _dept(out, "money")["runs_without_you"]
    assert (money["auto"], money["approved"], money["pct"]) == (3, 1, 75.0)
    content = _dept(out, "content")["runs_without_you"]
    assert (content["auto"], content["approved"], content["pct"]) == (0, 1, 0.0)
    assert _dept(out, "ops")["runs_without_you"]["pct"] is None
    whole = out["whole"]["runs_without_you"]
    assert (whole["auto"], whole["approved"], whole["excluded"]) == (4, 2, 1)
    assert whole["pct"] == round(100 * 4 / 6, 1)
    assert out["unmapped"] == {"executions": 1, "actions": ["gone.renamed_action"],
                               "waiting_on_you": 0}
    placed = sum(d["runs_without_you"]["executions"] for d in out["departments"])
    assert placed + out["unmapped"]["executions"] == whole["executions"]


@needs_pg
async def test_an_old_execution_is_outside_the_window(world):
    await _start("markets.alert", 1)
    later = datetime.now(UTC) + timedelta(days=brain.WINDOW_DAYS, minutes=1)
    out = await brain.build(now=later)
    assert _dept(out, "money")["runs_without_you"]["pct"] is None


@needs_pg
async def test_waiting_on_you_is_the_badges_count_split_by_department(world):
    await _card("markets.paper_idea")
    await _card("markets.paper_rebalance")
    await _card("content.publish_reel")
    await _card("nosuch.action")
    await _card("markets.paper_idea", status="declined")
    await _card("markets.paper_idea", status="expired")
    badge = await state.fetch_one(
        "SELECT count(*) AS n FROM proposals WHERE status = 'pending'")

    out = await brain.build()
    assert out["whole"]["waiting_on_you"] == int(badge["n"]) == 4
    assert _dept(out, "money")["waiting_on_you"] == 2
    assert _dept(out, "content")["waiting_on_you"] == 1
    assert out["unmapped"]["waiting_on_you"] == 1
    split = sum(d["waiting_on_you"] for d in out["departments"])
    assert split + out["unmapped"]["waiting_on_you"] == out["whole"]["waiting_on_you"]


@needs_pg
async def test_seats_that_ran_and_a_pause_on_its_department(world):
    await state.execute_sql(
        "INSERT INTO agent_runs (agent, task) VALUES ('markets', 'why is NVDA up'), "
        "('markets', 'Your shift: today'), ('reels', 'draft')")
    await state.execute_sql(
        """INSERT INTO schedule_runs (job, occurrence, status, finished_at)
           VALUES ('playlist', '2026-W39', 'done', now()),
                  ('ideas', '2026-09-28', 'failed', now())""")
    await state.execute_sql(
        "INSERT INTO pauses (scope, paused_by, reason) VALUES ('markets', 'miro', 'test')")

    out = await brain.build()
    money = _dept(out, "money")
    seats = money["seats"]
    assert (seats["agents"], seats["agents_ran"]) == (1, 1)
    jobs = {j["name"]: j for j in money["jobs"]}
    assert jobs["playlist"]["ran_7d"] == 1
    assert jobs["ideas"]["ran_7d"] == 0                 # failed is not done
    assert jobs["market_alerts"]["ran_7d"] is None      # interval: no record
    assert jobs["market_alerts"]["recorded"] is False
    assert (seats["jobs_recorded"], seats["jobs_ran"]) == (2, 1)
    strategy = {j["name"]: j for j in _dept(out, "strategy")["jobs"]}
    assert strategy["agent_shifts"]["ran_7d"] == 1
    assert [p["scope"] for p in money["paused"]] == ["markets"]
    assert _dept(out, "content")["paused"] == []
    assert out["whole"]["paused"][0]["department"] == "money"


@needs_pg
async def test_every_seat_has_an_sop_and_the_head_is_named(world):
    out = await brain.build()
    for d in out["departments"]:
        for item in d["agents"] + d["jobs"] + d["actions"]:
            assert item["id"] in out["sops"], item["id"]
    assert _dept(out, "money")["head"]["label"] == "Ivan"
    assert _dept(out, "strategy")["head"]["agent"] is None
    idea = out["sops"]["action:markets.paper_idea"]
    assert any(s["kind"] == "signoff" for s in idea)


@needs_pg
async def test_a_failed_read_is_an_error_with_its_figures_unknown(world, monkeypatch):
    async def broken(_since):
        raise RuntimeError("actions_log is unreadable")

    monkeypatch.setattr(brain, "_read_executions", broken)
    out = await brain.build()
    assert "executions" in out["errors"]
    assert out["whole"]["runs_without_you"] is None
    assert all(d["runs_without_you"] is None for d in out["departments"])
    assert out["whole"]["waiting_on_you"] == 0          # its own read still stands
