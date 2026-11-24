"""`POST /api/voice/ask`: a dashboard order is on the record (kernel/tasks.py)
so the HUD can show it; a question is not a task. The wrapper opens and
closes the task around the existing handler and changes no branch inside it.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from atlas.api import auth, voice

SECRET = "s" * 40
PASSWORD = "a-long-enough-password"
ORDER = "make me a reel about the new menu"
QUESTION = "what is the anatomy of the knee"


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
    with TestClient(app, client=("127.0.0.1", 51014)) as c:
        assert c.post("/api/login", json={"password": PASSWORD}).status_code == 200
        yield c


@pytest.fixture
def record(monkeypatch):
    calls: dict[str, list] = {"open": [], "close": []}

    async def fake_open(text, *, source, who="", thread=""):
        calls["open"].append((text, source, who))
        return 41

    async def fake_close(task_id, **kw):
        calls["close"].append((task_id, kw))

    monkeypatch.setattr("atlas.kernel.tasks.open", fake_open)
    monkeypatch.setattr("atlas.kernel.tasks.close", fake_close)
    return calls


def _inner(monkeypatch, **reply):
    async def fake(transcript):
        return {"answer": "ok", "kind": "action_request", "proposal": None, **reply}

    monkeypatch.setattr(voice, "_ask", fake)


def test_an_order_writes_one_dashboard_task(client, record, monkeypatch):
    assert voice.is_order(ORDER)
    _inner(monkeypatch)
    assert client.post("/api/voice/ask", json={"transcript": ORDER}).status_code == 200
    assert record["open"] == [(ORDER, "dashboard", "dashboard")]
    [(task_id, kw)] = record["close"]
    assert task_id == 41 and kw["ok"] is None and kw["cards"] == []


def test_an_order_that_raises_a_card_links_it(client, record, monkeypatch):
    _inner(monkeypatch, proposal=17, kind="cards")
    client.post("/api/voice/ask", json={"transcript": ORDER})
    [(_, kw)] = record["close"]
    assert kw["cards"] == [17] and kw["route"] == "cards" and kw["ok"] is None


def test_a_question_writes_no_task(client, record, monkeypatch):
    assert not voice.is_order(QUESTION)
    _inner(monkeypatch, kind="question")
    client.post("/api/voice/ask", json={"transcript": QUESTION})
    assert record["open"] == [] and record["close"] == []


def test_a_failing_order_closes_failed_and_still_raises(client, record, monkeypatch):
    async def boom(transcript):
        raise RuntimeError("model down")

    monkeypatch.setattr(voice, "_ask", boom)
    with pytest.raises(RuntimeError):
        client.post("/api/voice/ask", json={"transcript": ORDER})
    [(task_id, kw)] = record["close"]
    assert task_id == 41 and kw["ok"] is False


def test_an_empty_transcript_is_still_a_400(client, record):
    assert client.post("/api/voice/ask", json={"transcript": "  "}).status_code == 400
    assert record["open"] == []
