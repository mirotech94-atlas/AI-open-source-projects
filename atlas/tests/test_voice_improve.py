"""Voice: "improve X". It used to come back as "I don't have access to that".

The honest answer is a development handoff, and which one depends on what is
to be improved (kernel/improve.py):

* ATLAS itself -- its code, voice, reels, prompts, tiers -- is never queued
  to run anything. Self-modification is Tier 3 whoever would carry it out.
  It is written up as a note for Miro's supervised coding session.
* FlyCheap and Vault become a `nightshift.run` proposal: a card that waits
  for his yes, on a repository taken from NIGHTSHIFT_REPOS, never from what
  was said.

Nothing on either path may reach the executor.
"""
from __future__ import annotations

import ast
import inspect

import pytest
from fastapi.testclient import TestClient

from atlas.api import auth, voice
from atlas.kernel import improve

SECRET = "v" * 40
PASSWORD = "a-long-enough-password"
REPOS = "aicode.example.com/root/flycheap-tracker,aicode.example.com/root/guardian"


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

    with TestClient(app, client=("127.0.0.1", 52000)) as c:
        c.post("/api/login", json={"password": PASSWORD})
        yield c


@pytest.fixture
def guarded(monkeypatch):
    """Every way an improve request could go wrong, made loud: the executor
    raises, a model call raises (the answer must not be improvised), and
    every proposal and note is captured."""
    seen: dict[str, list] = {"proposed": [], "notes": []}

    async def no_execute(*_a, **_k):
        raise AssertionError("an improve request reached the executor")

    async def no_model(*_a, **_k):
        raise AssertionError("an improve request was answered by a model")

    async def fake_propose(action, params, reason="", **kw):
        seen["proposed"].append((action, params, reason, kw))
        return 41

    async def fake_notify(msg):
        seen["notes"].append(msg)
        return ["slack"]

    monkeypatch.setattr("atlas.kernel.executor.execute", no_execute)
    monkeypatch.setattr("atlas.kernel.agents.route", no_model)
    monkeypatch.setattr("atlas.kernel.llm.think", no_model)
    monkeypatch.setattr("atlas.kernel.approvals.propose", fake_propose)
    monkeypatch.setattr("atlas.kernel.comms.notify", fake_notify)
    monkeypatch.setenv("NIGHTSHIFT_REPOS", REPOS)
    return seen


def _no_refusal(spoken: str) -> None:
    lowered = spoken.lower()
    for refusal in ("access", "i can't do that", "i cannot do that", "unable to"):
        assert refusal not in lowered, spoken


# ------------------------------------------------------------ classification

@pytest.mark.parametrize("said", [
    "improve yourself",
    "improve ATLAS's voice",
    "improve the reels quality",
    "can you improve your answers",
    "hey atlas, improve yourself",
    "I want you to improve the reels",
    "make your voice sound better",
    "improve your prompts",
])
def test_improving_atlas_itself_is_recognised_as_atlas(said):
    assert improve.target(said) is improve.ATLAS, said


@pytest.mark.parametrize(("said", "key"), [
    ("improve FlyCheap's search", "flycheap"),
    ("atlas, improve flycheap's search", "flycheap"),
    ("make the fly cheap search results better", "flycheap"),
    ("improve vault's alerts", "guardian"),
    ("improve the attendance bot", "roots"),
])
def test_improving_something_else_names_it(said, key):
    place = improve.target(said)
    assert place is not None and place.key == key, said


@pytest.mark.parametrize("said", [
    "how can I improve my sleep",
    "what would improve the reels",
    "improve my voice",
    "how do I improve my GPA",
    "restart the flycheap server",
    "what's broken",
    # Content edits belong to the draft actions they always reached.
    "polish the caption for tomorrow's reel",
    "improve the reply to that buyer",
    "improve the story for tomorrow",
    "improve your reply to the buyer",
    "improve tomorrow's reel",
])
def test_not_every_improve_is_a_development_task(said):
    assert improve.target(said) is None, said


# ------------------------------------------------------------ ATLAS itself

@pytest.mark.parametrize("said", [
    "improve yourself",
    "improve ATLAS's voice",
    "improve the reels quality",
])
async def test_improving_atlas_queues_nothing_and_says_it_wrote_a_dev_task(
        client, memory_backend, guarded, said):
    body = client.post("/api/voice/ask", json={"transcript": said}).json()
    assert body["proposal"] is None
    assert guarded["proposed"] == []
    spoken = body["answer"].lower()
    assert "development task" in spoken
    assert "supervised coding session" in spoken
    _no_refusal(body["answer"])
    # Written down: an audit row and a note to him, carrying the request.
    assert [r for r in memory_backend.audit_rows if "improve.noted" in r.payload]
    note = guarded["notes"][-1]
    assert note.title.startswith("Development task")
    assert said.lower() in note.body.lower()
    # No buttons: a note is not a card, nothing on it can approve anything.
    assert not note.buttons


async def test_improving_atlas_over_the_phone_queues_nothing(memory_backend, guarded):
    spoken, pids = await voice.spoken_turn("improve the reels quality", source="phone")
    assert pids == []
    assert guarded["proposed"] == []
    assert "development task" in spoken.lower()


async def test_tiers_are_noted_with_the_line_said_out_loud(memory_backend, guarded):
    got = await improve.handle("improve your tiers", source="voice")
    assert got is not None and got.proposal_id is None and got.noted
    assert "only ever change in code" in got.spoken
    assert "no runtime override" in guarded["notes"][-1].body


async def test_a_note_that_could_not_be_recorded_does_not_claim_it_was(
        memory_backend, guarded, monkeypatch):
    async def broken(*_a, **_k):
        raise RuntimeError("database down")

    monkeypatch.setattr("atlas.kernel.audit.audit", broken)
    got = await improve.handle("improve yourself", source="voice")
    assert got is not None and not got.noted
    assert "couldn't record" in got.spoken
    assert guarded["notes"] == []


# --------------------------------------------------------- FlyCheap / Vault

async def test_improving_flycheap_is_a_nightshift_proposal_not_an_execution(
        client, memory_backend, guarded):
    body = client.post("/api/voice/ask",
                       json={"transcript": "improve FlyCheap's search"}).json()
    assert body["proposal"] == 41
    assert len(guarded["proposed"]) == 1
    action, params, _reason, kw = guarded["proposed"][0]
    assert action == "nightshift.run"
    # The repository comes from NIGHTSHIFT_REPOS, never from the words.
    assert params["repo"] == "https://aicode.example.com/root/flycheap-tracker"
    # His words reach the sandbox quoted, never as its instructions.
    assert "<request source='voice'>" in params["task"]
    assert "improve FlyCheap's search" in params["task"]
    assert "do not merge or deploy" in params["task"]
    assert kw["dedup_key"].startswith("improve:flycheap:")
    spoken = body["answer"].lower()
    assert "night shift" in spoken and "approval" in spoken and "#41" in spoken
    _no_refusal(body["answer"])
    assert guarded["notes"] == []


async def test_vault_goes_to_its_own_repository(memory_backend, guarded):
    got = await improve.handle("improve vault's alerts", source="voice")
    assert got is not None and got.proposal_id == 41
    assert guarded["proposed"][0][1]["repo"] == "https://aicode.example.com/root/guardian"


async def test_a_repo_not_on_the_list_is_a_note_not_a_card(
        memory_backend, guarded, monkeypatch):
    monkeypatch.setenv("NIGHTSHIFT_REPOS", "aicode.example.com/root/guardian")
    got = await improve.handle("improve FlyCheap's search", source="voice")
    assert got is not None and got.proposal_id is None and got.noted
    assert guarded["proposed"] == []
    assert "isn't on the night shift's list" in got.spoken


async def test_an_atlas_repo_on_the_list_is_never_handed_to_the_night_shift(
        memory_backend, guarded, monkeypatch):
    monkeypatch.setenv("NIGHTSHIFT_REPOS", "aicode.example.com/root/atlas-flycheap")
    assert improve.nightshift_repo(improve.target("improve flycheap")) is None


async def test_a_refused_card_falls_back_to_a_note(memory_backend, guarded, monkeypatch):
    async def refusing(*_a, **_k):
        raise ValueError("cannot propose nightshift.run: module not loaded")

    monkeypatch.setattr("atlas.kernel.approvals.propose", refusing)
    got = await improve.handle("improve FlyCheap's search", source="voice")
    assert got is not None and got.proposal_id is None and got.noted
    assert "couldn't queue it" in got.spoken


async def test_improving_by_deleting_is_still_refused(memory_backend, guarded):
    got = await improve.handle("improve flycheap by deleting the old accounts",
                               source="voice")
    assert got is not None and got.proposal_id is None and not got.noted
    assert "permanently denied" in got.spoken
    assert guarded["proposed"] == [] and guarded["notes"] == []


# ------------------------------------------------------------ the prompts

def test_every_voice_prompt_offers_the_handoff_instead_of_refusing():
    source = inspect.getsource(voice)
    # ask() and spoken_turn() twice each, and consult() in an agent's name.
    assert source.count("+ IMPROVE_LINE") == 5
    assert "never say you have no access" in voice.IMPROVE_LINE.lower() or (
        "never " in voice.IMPROVE_LINE and "no access" in voice.IMPROVE_LINE)


def test_the_agents_are_told_to_offer_the_handoff():
    from atlas.kernel.agents import BASE_RULES
    assert "Asked to improve something" in BASE_RULES
    assert "development task" in BASE_RULES


# ------------------------------------------------------------ structure

def test_improve_cannot_reach_the_executor():
    """Structural, like the voice guard: no import of the executor and no
    call named execute anywhere in the module."""
    tree = ast.parse(inspect.getsource(improve))
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            where = getattr(node, "module", "") or ""
            names = " ".join(a.name for a in node.names)
            assert "executor" not in f"{where} {names}", node.lineno
            assert "intent" not in f"{where} {names}", node.lineno
        if isinstance(node, ast.Call):
            assert getattr(node.func, "attr", "") not in ("execute", "act"), node.lineno


async def test_slack_gets_the_same_handoff(memory_backend, guarded, monkeypatch):
    """Typed in Slack, the same request reaches the same place: never the
    catalog-miss path, never intent.act."""
    from atlas.api import slack_events

    async def no_act(*_a, **_k):
        raise AssertionError("an improve request reached intent.act")

    monkeypatch.setattr("atlas.kernel.intent.act", no_act)
    await slack_events._request("improve yourself", "U1", "C1", "1.0")
    assert guarded["proposed"] == []
    receipt = guarded["notes"][-1]
    assert "development task" in receipt.body.lower()
    assert any(n.title.startswith("Development task") for n in guarded["notes"])


async def test_the_reply_says_where_the_note_went(memory_backend, guarded, monkeypatch):
    got = await improve.handle("improve yourself", source="voice")
    assert got is not None and "sent you the note" in got.spoken

    async def quiet(_msg):
        return ["queued"]

    monkeypatch.setattr("atlas.kernel.comms.notify", quiet)
    got = await improve.handle("improve yourself", source="voice")
    assert got is not None and "morning brief" in got.spoken

    async def down(_msg):
        raise RuntimeError("slack down")

    monkeypatch.setattr("atlas.kernel.comms.notify", down)
    got = await improve.handle("improve yourself", source="voice")
    assert got is not None and got.noted
    assert "couldn't send you the note" in got.spoken
