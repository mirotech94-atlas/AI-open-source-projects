"""`POST /api/voice/ask` reads `typed` as a bool that defaults to false: only
the dashboard's own text box sends `typed: true`, and the spoken path is the
one it always was (it never reaches `assistant.converse`)."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from atlas.api import auth, voice

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
    with TestClient(app, client=("127.0.0.1", 51015)) as c:
        assert c.post("/api/login", json={"password": PASSWORD}).status_code == 200
        yield c


@pytest.fixture
def seen(monkeypatch):
    got: list[dict] = []

    async def fake(transcript, **kw):
        got.append(kw)
        return {"answer": "ok", "kind": "question", "proposal": None}

    monkeypatch.setattr(voice, "_ask", fake)
    return got


def test_typed_defaults_to_false(client, seen):
    assert client.post("/api/voice/ask", json={"transcript": "hello"}).status_code == 200
    assert seen == [{}]


@pytest.mark.parametrize("value", ["true", 1, "yes", None, [], {}])
def test_only_a_real_true_counts_as_typed(client, seen, value):
    client.post("/api/voice/ask", json={"transcript": "hello", "typed": value})
    assert seen == [{}]


def test_a_typed_true_reaches_the_handler(client, seen):
    client.post("/api/voice/ask", json={"transcript": "hello", "typed": True})
    assert seen == [{"typed": True}]


async def test_the_typed_door_falls_back_when_the_assistant_fails(monkeypatch):
    from atlas.modules import assistant

    async def boom(*a, **k):
        raise RuntimeError("down")
    monkeypatch.setattr(assistant, "converse", boom)
    assert await voice._typed_assistant("hello", False, 0.0) is None


async def test_the_typed_door_records_an_assistant_turn(monkeypatch):
    from atlas.modules import assistant

    async def converse(text, *, channel, who="", private_ok=False):
        assert private_ok is True            # the dashboard is his own
        assert channel == "dashboard"
        return "Fine."
    monkeypatch.setattr(assistant, "converse", converse)
    recorded = {}

    async def record(transcript, answer, wants_action, started, **kw):
        recorded.update(answer=answer, **kw)
        return {"answer": answer, "kind": kw.get("kind")}
    monkeypatch.setattr(voice, "_record", record)

    got = await voice._typed_assistant("how are you", False, 0.0)

    assert got == {"answer": "Fine.", "kind": "assistant"}


def test_the_dashboard_door_is_off_by_default(monkeypatch):
    monkeypatch.delenv("ASSISTANT_ENABLED", raising=False)
    monkeypatch.delenv("ASSISTANT_DOORS", raising=False)
    assert voice._dashboard_door() is False


def test_an_order_with_no_card_never_claims_it_was_queued():
    said = voice._order_answer("Queued: draft email to your accountant.", [],
                               voice.NO_ACTION)
    assert said == voice.NO_ACTION and "Queued" not in said


def test_an_order_with_a_card_keeps_the_models_line_and_the_card_line():
    said = voice._order_answer("I'll queue the restart.", [7], voice.ASK)
    assert said == f"I'll queue the restart.\n\n{voice.ASK}"
