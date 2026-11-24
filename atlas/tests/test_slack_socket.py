"""T2: Slack Socket Mode, against an in-process fake wss server.

The fake stands in for Slack's end of the socket: it says hello, sends
envelopes, and records the acks. `apps.connections.open` is replaced by an
opener that returns the fake's ws:// URL, so nothing leaves the process.
"""
from __future__ import annotations

import asyncio
import json
import time

import pytest
from fastapi.testclient import TestClient
from websockets.asyncio.server import serve

from atlas.channels import slack_socket
from atlas.kernel.contracts import Health


@pytest.fixture(autouse=True)
def _fresh_state(monkeypatch):
    slack_socket.STATE.reset()

    async def no_sleep(_s: float) -> None:
        await asyncio.sleep(0)

    monkeypatch.setattr(slack_socket, "_sleep", no_sleep)
    monkeypatch.setenv("SLACK_APPROVER_IDS", "UMIRO")
    monkeypatch.delenv("ATLAS_DURABLE_FLOWS", raising=False)
    yield
    slack_socket.STATE.reset()


class FakeSlack:
    """One script per connection: the frames to send after hello."""

    def __init__(self, scripts: list[list[dict]]):
        self.scripts = scripts
        self.acks: list[dict] = []
        self.connections = 0
        self.got_acks = asyncio.Event()
        self.expect_acks = sum(1 for s in scripts for m in s if "envelope_id" in m)

    async def handler(self, ws) -> None:
        n = self.connections
        self.connections += 1
        script = self.scripts[n] if n < len(self.scripts) else []
        await ws.send(json.dumps({"type": "hello", "num_connections": 1}))
        for frame in script:
            await ws.send(json.dumps(frame))
        try:
            async for raw in ws:
                self.acks.append(json.loads(raw))
                if len(self.acks) >= self.expect_acks:
                    self.got_acks.set()
        except Exception:  # noqa: BLE001, S110
            pass


async def _running(fake: FakeSlack):
    server = await serve(fake.handler, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    opened: list[str] = []

    async def opener(tok: str) -> str:
        opened.append(tok)
        return f"ws://127.0.0.1:{port}"

    task = asyncio.create_task(slack_socket.run("xapp-test", opener=opener))
    return server, task, opened


async def _stop(server, task) -> None:
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)
    server.close()
    await server.wait_closed()


async def _until(cond, timeout: float = 3.0) -> None:
    end = time.monotonic() + timeout
    while not cond():
        if time.monotonic() > end:
            raise AssertionError("condition not met in time")
        await asyncio.sleep(0.01)


async def test_acks_first_and_dispatches_each_kind(monkeypatch):
    from atlas.api import main, slack_events

    interactions: list[dict] = []
    events: list[dict] = []
    never = asyncio.Event()

    async def interaction(payload: dict) -> None:
        interactions.append(payload)
        await never.wait()          # a handler that never finishes

    async def event(payload: dict) -> dict:
        events.append(payload)
        await never.wait()
        return {"ok": True}

    answer = {"response_type": "ephemeral", "text": "On it — restart x"}

    async def command(form: dict) -> dict:
        assert form["text"] == "restart x"
        return answer

    monkeypatch.setattr(main, "handle_interaction", interaction)
    monkeypatch.setattr(slack_events, "handle_event", event)
    monkeypatch.setattr(main, "handle_command", command)

    fake = FakeSlack([[
        {"envelope_id": "e1", "type": "interactive",
         "payload": {"user": {"id": "UMIRO"}, "actions": [{"value": "approve:5"}]}},
        {"envelope_id": "e2", "type": "events_api",
         "payload": {"event_id": "Ev1", "event": {"type": "message"}}},
        {"envelope_id": "e3", "type": "slash_commands",
         "payload": {"user_id": "UMIRO", "text": "restart x"}},
    ]])
    server, task, opened = await _running(fake)
    try:
        await asyncio.wait_for(fake.got_acks.wait(), 3)
        await _until(lambda: interactions and events)
    finally:
        await _stop(server, task)

    by_id = {a["envelope_id"]: a for a in fake.acks}
    # Acked although both handlers are still blocked: the ack never waits.
    assert by_id["e1"] == {"envelope_id": "e1"}
    assert by_id["e2"] == {"envelope_id": "e2"}
    assert by_id["e3"] == {"envelope_id": "e3", "payload": answer}
    assert interactions[0]["actions"][0]["value"] == "approve:5"
    assert events[0]["event_id"] == "Ev1"
    assert opened == ["xapp-test"]


async def test_hello_makes_the_check_ok(monkeypatch):
    monkeypatch.setenv("SLACK_APP_TOKEN", "xapp-test")
    fake = FakeSlack([[]])
    server, task, _ = await _running(fake)
    try:
        await _until(lambda: slack_socket.STATE.connected)
        obs = await slack_socket.check_socket()
    finally:
        await _stop(server, task)
    assert obs.health is Health.OK
    assert obs.check == "slack.socket"


async def test_disconnect_reconnects(monkeypatch):
    fake = FakeSlack([[{"type": "disconnect", "reason": "refresh_requested"}], []])
    server, task, opened = await _running(fake)
    try:
        await _until(lambda: fake.connections >= 2 and slack_socket.STATE.connected)
    finally:
        await _stop(server, task)
    assert len(opened) >= 2


async def test_a_failed_open_retries_and_never_logs_the_token(monkeypatch, caplog):
    calls: list[int] = []

    async def opener(tok: str) -> str:
        calls.append(1)
        raise slack_socket.SocketOpenError("invalid_auth")

    task = asyncio.create_task(slack_socket.run("xapp-SECRETVALUE", opener=opener))
    try:
        await _until(lambda: len(calls) >= 3)
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
    assert slack_socket.STATE.last_error == "invalid_auth"
    assert "SECRETVALUE" not in caplog.text


async def test_non_approver_ignored_and_duplicate_dropped(monkeypatch, memory_backend):
    from atlas.api import slack_events

    real = slack_events.handle_event
    results: list[dict] = []
    looked: list[str] = []
    seen: set[str] = set()

    async def recorded(payload: dict) -> dict:
        r = await real(payload)
        results.append(r)
        return r

    async def seen_before(event_id: str) -> bool:
        looked.append(event_id)
        if event_id in seen:
            return True
        seen.add(event_id)
        return False

    async def request(*_a, **_k) -> None:
        return None

    monkeypatch.setattr(slack_events, "handle_event", recorded)
    monkeypatch.setattr(slack_events, "_seen_before", seen_before)
    monkeypatch.setattr(slack_events, "_request", request)

    def msg(user: str, eid: str) -> dict:
        return {"event_id": eid, "event": {
            "type": "message", "user": user, "text": "atlas hello there",
            "channel": "C1", "ts": "1.2"}}

    fake = FakeSlack([[
        {"envelope_id": "a", "type": "events_api", "payload": msg("UOTHER", "Ev7")},
        {"envelope_id": "b", "type": "events_api", "payload": msg("UMIRO", "Ev8")},
        {"envelope_id": "c", "type": "events_api", "payload": msg("UMIRO", "Ev8")},
    ]])
    server, task, _ = await _running(fake)
    try:
        await asyncio.wait_for(fake.got_acks.wait(), 3)
        await _until(lambda: len(results) == 3)
    finally:
        await _stop(server, task)

    # The non-approver never reaches the duplicate lookup, nor anything else.
    assert looked == ["Ev8", "Ev8"]
    assert results.count({"ok": True, "ignored": "duplicate"}) == 1


def test_no_token_no_task(monkeypatch):
    monkeypatch.delenv("SLACK_APP_TOKEN", raising=False)
    assert slack_socket.start() is None


async def test_missing_token_reads_unknown_with_the_reason(monkeypatch):
    monkeypatch.delenv("SLACK_APP_TOKEN", raising=False)
    obs = await slack_socket.check_socket()
    assert obs.health is Health.UNKNOWN
    assert "SLACK_APP_TOKEN" in obs.detail


async def test_down_past_grace_is_failing(monkeypatch):
    monkeypatch.setenv("SLACK_APP_TOKEN", "xapp-test")
    st = slack_socket.STATE
    st.running, st.connected = True, False
    st.down_since = time.monotonic() - slack_socket.GRACE_S - 5
    assert (await slack_socket.check_socket()).health is Health.FAILING
    st.down_since = time.monotonic() - 5
    within = await slack_socket.check_socket()
    assert within.health is Health.UNKNOWN and "grace" in within.detail


async def test_not_running_is_unknown_never_ok(monkeypatch):
    monkeypatch.setenv("SLACK_APP_TOKEN", "xapp-test")
    assert (await slack_socket.check_socket()).health is Health.UNKNOWN


def test_lifespan_starts_and_cancels_the_task(monkeypatch, memory_backend):
    monkeypatch.setenv("SLACK_APP_TOKEN", "xapp-test")
    monkeypatch.setenv("MESH_ALLOWED_CIDRS", "0.0.0.0/0")
    state = {"started": False, "cancelled": False}

    async def fake_run(tok: str, **_k) -> None:
        state["started"] = True
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            state["cancelled"] = True
            raise

    async def no_boot() -> None:
        return None

    monkeypatch.setattr(slack_socket, "run", fake_run)
    from atlas.api.main import app
    from atlas.kernel.registry import REGISTRY
    monkeypatch.setattr("atlas.api.main.boot", no_boot)
    monkeypatch.setattr("atlas.api.main.scheduler.loop", no_boot)
    monkeypatch.delitem(REGISTRY.checks, "slack.socket", raising=False)
    with TestClient(app, client=("127.0.0.1", 51000)) as c:
        c.get("/healthz")
        assert "slack.socket" in REGISTRY.checks
    assert state == {"started": True, "cancelled": True}
    # Process-global registry: left as found, or later tests see a check
    # they never registered.
    assert "slack.socket" not in REGISTRY.checks
