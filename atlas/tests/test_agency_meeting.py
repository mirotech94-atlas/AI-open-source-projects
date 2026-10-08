"""The Agency room: `GET /api/agency/meeting`.

Proved here: it needs a session; every roster agent takes a turn under its
persona; every line comes from the record (runs, failures, readiness, next
shift) and nothing is invented for an agent with no runs; the summary's
buckets are disjoint and add up; a failed read is a 503, never a room of
zeros; and nothing in the file can reach the executor or a model.

Not the `agency` business module (tests/test_agency*.py cover that).
"""
from __future__ import annotations

import ast
import pathlib
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from atlas.api import auth
from atlas.kernel import agents as agent_module
from atlas.kernel import personas

SECRET = "s" * 40
PASSWORD = "a-long-enough-password"


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
    agent_module.build_roster()
    with TestClient(app, client=("127.0.0.1", 51007)) as test_client:
        yield test_client


@pytest.fixture
def signed_in(client):
    assert client.post("/api/login", json={"password": PASSWORD}).status_code == 200
    return client


def stub_runs(backend, *, counts=(), latest=(), failures=(), shifts=(), stored=None):
    """Answer the endpoint's four reads by what each one selects, and its
    read of today's stored meeting (`agency_meetings`: none by default)."""
    seen: list[str] = []
    backend.responses["FROM agency_meetings"] = stored

    async def fetch(sql, *args):
        flat = " ".join(sql.split())
        seen.append(flat)
        if "count(*) FILTER" in flat:
            return list(counts)
        if "SELECT DISTINCT ON (agent) agent, at, outcome, model, provider" in flat:
            return list(latest)
        if "SELECT DISTINCT ON (agent) agent, at, outcome, model FROM" in flat:
            return list(failures)
        if "max(at) AS at" in flat:
            return list(shifts)
        raise NotImplementedError(flat)

    backend.fetch = fetch
    return seen


def all_ready(monkeypatch, blocked: dict[str, list[str]] | None = None):
    blocked = blocked or {}
    monkeypatch.setattr(
        agent_module, "readiness",
        lambda name: (name not in blocked, list(blocked.get(name, []))))


# ------------------------------------------------------------------ access

def test_the_meeting_needs_a_session(client):
    assert client.get("/api/agency/meeting").status_code == 401


def test_the_meeting_is_read_only_and_never_reaches_the_executor_or_a_model():
    """No write verb on the router, and the file imports no path to act or
    to think -- walked in the AST, not trusted from a comment."""
    from atlas.api.agency import router

    for route in router.routes:
        assert set(getattr(route, "methods", set())) <= {"GET", "HEAD"}, route.path
    src = (pathlib.Path(__file__).parent.parent / "atlas" / "api" / "agency.py")
    tree = ast.parse(src.read_text())
    names = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
    names |= {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)}
    imported = {a.name for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)
                for a in n.names}
    for forbidden in ("execute", "invoke", "think", "run_agent", "warmup",
                      "execute_sql", "run_tier1"):
        assert forbidden not in names | imported, forbidden


# ------------------------------------------------------------------ content

def test_every_roster_agent_takes_a_turn_under_its_persona(
        signed_in, memory_backend, monkeypatch):
    all_ready(monkeypatch)
    stub_runs(memory_backend)
    body = signed_in.get("/api/agency/meeting").json()

    assert body["read_at"] and body["as_of"]
    assert body["opening"]["voice"] == "atlas" and body["opening"]["text"]
    assert body["closing"]["voice"] == "atlas"
    agents = [t["agent"] for t in body["turns"]]
    assert set(agents) == set(agent_module.ROSTER)
    for turn in body["turns"]:
        p = personas.PERSONAS[turn["agent"]]
        assert turn["persona"]["name"] == p.name
        assert turn["persona"]["specialty"] == p.specialty
        assert turn["voice"] == p.voice
        assert turn["read_at"] == body["read_at"]
        assert turn["speech"].startswith(p.name)
        assert len(turn["speech"]) < 1500
        for key in ("did", "issues", "improve", "next"):
            assert turn[key]["text"], (turn["agent"], key)


def test_concierge_speaks_first_and_no_line_stutters(
        signed_in, memory_backend, monkeypatch):
    all_ready(monkeypatch)
    stub_runs(memory_backend, latest=[
        {"agent": "analyst", "at": datetime.now(UTC) - timedelta(days=3),
         "model": "m", "provider": "p", "outcome": "Revenue was flat week on week."}])
    turns = signed_in.get("/api/agency/meeting").json()["turns"]
    assert turns[0]["agent"] == "concierge"
    for t in turns:
        assert ".." not in t["speech"], t["speech"]


def test_an_agent_with_no_runs_is_idle_and_nothing_is_invented(
        signed_in, memory_backend, monkeypatch):
    all_ready(monkeypatch)
    stub_runs(memory_backend)
    body = signed_in.get("/api/agency/meeting").json()
    ops = next(t for t in body["turns"] if t["agent"] == "ops")
    assert ops["status"] == "idle"
    assert ops["did"]["runs_24h"] == 0
    assert ops["did"]["last_at"] is None and ops["did"]["last_outcome"] is None
    assert "never run" in ops["did"]["text"]
    assert ops["issues"]["text"] == "none"
    assert "Nothing on the record" in ops["improve"]["text"]


def test_runs_and_the_last_outcome_come_from_the_record(
        signed_in, memory_backend, monkeypatch):
    all_ready(monkeypatch)
    at = datetime.now(UTC) - timedelta(hours=2)
    stub_runs(
        memory_backend,
        counts=[{"agent": "ops", "runs": 3, "self_tests": 1, "shifts": 1,
                 "failed": 0, "total": 4}],
        latest=[{"agent": "ops", "at": at, "model": "claude", "provider": "anthropic",
                 "outcome": "**All four servers** answered. Disk on vps2 is 81%."}],
    )
    ops = next(t for t in signed_in.get("/api/agency/meeting").json()["turns"]
               if t["agent"] == "ops")
    assert ops["status"] == "worked"
    assert ops["did"]["runs_24h"] == 3
    assert ops["did"]["self_tests_24h"] == 1
    assert ops["did"]["last_at"] == at.isoformat()
    # First sentence only, markdown stripped.
    assert ops["did"]["last_outcome"] == "All four servers answered."
    assert "3 runs in the last 24 hours (1 shift)" in ops["did"]["text"]
    assert "Plus 1 self-test" in ops["did"]["text"]


def test_a_failed_run_is_an_issue_and_drives_what_to_improve(
        signed_in, memory_backend, monkeypatch):
    all_ready(monkeypatch)
    at = datetime.now(UTC) - timedelta(minutes=30)
    stub_runs(
        memory_backend,
        counts=[{"agent": "analyst", "runs": 2, "self_tests": 0, "shifts": 0,
                 "failed": 1, "total": 2}],
        latest=[{"agent": "analyst", "at": at, "model": "", "provider": "",
                 "outcome": "The local model is down. Try later."}],
        failures=[{"agent": "analyst", "at": at, "model": "",
                   "outcome": "The local model is down. Try later."}],
    )
    analyst = next(t for t in signed_in.get("/api/agency/meeting").json()["turns"]
                   if t["agent"] == "analyst")
    assert analyst["issues"]["failed_24h"] == 1
    assert analyst["issues"]["last_failure_at"] == at.isoformat()
    assert "1 of 2 runs in the last 24 hours failed" in analyst["issues"]["text"]
    assert "no model answered" in analyst["issues"]["text"]
    assert "no model answered" in analyst["improve"]["text"]


def test_a_blocked_agent_names_its_reason_and_has_no_shift(
        signed_in, memory_backend, monkeypatch):
    all_ready(monkeypatch, blocked={"fiverr": ["browser disabled — missing env: X"]})
    stub_runs(memory_backend)
    body = signed_in.get("/api/agency/meeting").json()
    fiverr = next(t for t in body["turns"] if t["agent"] == "fiverr")
    assert fiverr["status"] == "blocked" and fiverr["ready"] is False
    assert "browser disabled" in fiverr["issues"]["text"]
    assert fiverr["improve"]["text"].startswith("Clear the blocker: browser disabled")
    assert fiverr["next"]["at"] is None
    assert "until it is ready" in fiverr["next"]["text"]
    assert personas.PERSONAS["fiverr"].name in body["closing"]["text"]


def test_the_next_shift_is_the_scheduler_s_own(signed_in, memory_backend, monkeypatch):
    all_ready(monkeypatch)
    monkeypatch.setenv("AGENT_SHIFT_OPS", "6")
    last = datetime.now(UTC) - timedelta(hours=1)
    stub_runs(memory_backend, shifts=[{"agent": "ops", "at": last}])
    monkeypatch.setenv("AGENT_SHIFT_SENTINEL", "0")
    turns = {t["agent"]: t for t in signed_in.get("/api/agency/meeting").json()["turns"]}
    expected = agent_module.next_shift_at("ops", last, datetime.now(UTC).astimezone())
    assert turns["ops"]["next"]["at"] == expected.isoformat()
    assert turns["ops"]["next"]["text"] == "Next shift in 4 hours."
    assert turns["sentinel"]["next"] == {"at": None, "text": "No shift."}


def test_the_closing_buckets_are_disjoint_and_add_up(
        signed_in, memory_backend, monkeypatch):
    all_ready(monkeypatch, blocked={"school": ["no provider resolves private work"]})
    stub_runs(memory_backend, counts=[
        {"agent": "ops", "runs": 1, "self_tests": 0, "shifts": 0, "failed": 0,
         "total": 1}])
    body = signed_in.get("/api/agency/meeting").json()
    c = body["closing"]["counts"]
    assert c["agents"] == len(agent_module.ROSTER)
    assert c["worked"] == 1 and c["blocked"] == 1
    assert c["worked"] + c["idle"] + c["blocked"] == c["agents"]
    assert c["ready"] == c["worked"] + c["idle"]
    assert f"{c['ready']} of {c['agents']} ready" in body["closing"]["text"]


def test_a_secret_in_an_outcome_never_reaches_the_room(
        signed_in, memory_backend, monkeypatch):
    all_ready(monkeypatch)
    key = "fa-" + "z" * 30
    monkeypatch.setenv("FISH_AUDIO_API_KEY", key)
    at = datetime.now(UTC)
    stub_runs(
        memory_backend,
        counts=[{"agent": "ops", "runs": 1, "self_tests": 0, "shifts": 0,
                 "failed": 0, "total": 1}],
        latest=[{"agent": "ops", "at": at, "model": "m", "provider": "p",
                 "outcome": f"Called fish with {key} and it worked."}])
    response = signed_in.get("/api/agency/meeting")
    assert key not in response.text


def test_a_failed_read_is_a_503_never_a_room_of_zeros(signed_in, memory_backend):
    async def broken(sql, *args):
        raise ConnectionError("postgres is not answering")

    memory_backend.fetch = broken
    response = signed_in.get("/api/agency/meeting")
    assert response.status_code == 503
    assert "could not read" in response.json()["detail"]
    assert "turns" not in response.json()


# ------------------------------------------------------------------ helpers

def test_first_sentence_trims_long_outcomes():
    from atlas.api.agency import first_sentence

    assert first_sentence(None) == ""
    assert first_sentence("One. Two.") == "One."
    long = "word " * 100
    out = first_sentence(long)
    assert len(out) <= 160 and out.endswith("…")
