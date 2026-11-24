"""Slack over Socket Mode: Slack without a public URL.

On the Mac nothing is published to the internet, so Slack cannot POST to
/webhooks/slack*. Socket Mode turns the direction round: ATLAS opens an
outbound websocket to Slack (`apps.connections.open`, then the wss URL it
returns) and Slack sends every button click, slash command and channel
message down it as an envelope.

Authority. Socket Mode skips `_verify_slack`: there is no signed HTTP request
to verify. What proves the envelope came from Slack is the `xapp-` app-level
token (SLACK_APP_TOKEN, scope `connections:write`) that opened the socket --
only Slack holds the other end. What proves it came from the owner is unchanged:
the same pure handlers (`handle_interaction`, `handle_command`,
`handle_event`) check the user id against SLACK_APPROVER_IDS, drop duplicate
event ids durably (`_seen_before`) and reach the one write path
(`approvals.decide` -> `executor.execute`) exactly as the HTTP routes do. This
module only carries envelopes; it never acts.

Each envelope is acknowledged first (Slack wants the ack within 3 s and
redelivers otherwise), then handled in the background. A slash command's ack
carries the command's immediate answer as its `payload` -- the one Slack
surface whose response body is shown.

The socket reconnects on Slack's `disconnect` (a routine refresh), on a
close, and after the Mac wakes (the keepalive ping times out and the
connection closes), with a capped backoff. The check `slack.socket` reports
it: OK while connected after Slack's hello; FAILING when the token is set and
the socket has been down past the grace period; UNKNOWN, with the reason,
when there is no token or the client is not running in this process.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx

from ..kernel.background import spawn
from ..kernel.contracts import Check, Health, Observation, Severity

log = logging.getLogger("atlas.slack_socket")

OPEN_URL = "https://slack.com/api/apps.connections.open"
CHECK_KEY = "slack.socket"
# Down this long with a token set is a failure, not a reconnect in progress.
GRACE_S = 180.0
BACKOFF_START_S = 1.0
BACKOFF_CAP_S = 60.0
# A slash command's answer must ride on the ack, inside Slack's 3 s.
COMMAND_ACK_BUDGET_S = 2.5
PING_INTERVAL_S = 20.0
PING_TIMEOUT_S = 20.0


def token() -> str:
    return os.getenv("SLACK_APP_TOKEN", "").strip()


@dataclass
class SocketState:
    running: bool = False
    connected: bool = False
    hello_at: datetime | None = None          # UTC, Slack's hello on this socket
    down_since: float | None = None           # monotonic
    opens: int = 0
    last_error: str = ""

    def reset(self) -> None:
        self.running = self.connected = False
        self.hello_at = None
        self.down_since = None
        self.opens = 0
        self.last_error = ""


STATE = SocketState()

Opener = Callable[[str], Awaitable[str]]
_sleep = asyncio.sleep


class SocketOpenError(RuntimeError):
    """apps.connections.open said no; the message is Slack's error code."""


async def open_url(app_token: str) -> str:
    """The wss URL for one connection. The URL carries a one-time ticket, so
    it is never logged."""
    async with httpx.AsyncClient(timeout=10) as client:
        r = await client.post(OPEN_URL,
                              headers={"Authorization": f"Bearer {app_token}"})
    try:
        data = r.json()
    except ValueError:
        raise SocketOpenError(f"http {r.status_code}") from None
    if not isinstance(data, dict) or not data.get("ok") or not data.get("url"):
        err = data.get("error") if isinstance(data, dict) else None
        raise SocketOpenError(str(err or f"http {r.status_code}"))
    return str(data["url"])


def _connect(url: str) -> Any:
    from websockets.asyncio.client import connect
    return connect(url, ping_interval=PING_INTERVAL_S, ping_timeout=PING_TIMEOUT_S,
                   open_timeout=15, max_size=4 * 1024 * 1024)


async def _command_ack(payload: dict) -> dict | None:
    """The slash command's immediate answer, for the ack. If it is not ready
    inside the budget, the ack goes bare and the answer follows on
    response_url -- the command itself is never cut short."""
    from ..api import main

    task = asyncio.ensure_future(main.handle_command(payload))
    try:
        return await asyncio.wait_for(asyncio.shield(task), COMMAND_ACK_BUDGET_S)
    except asyncio.CancelledError:
        raise
    except TimeoutError:
        reply_to = str(payload.get("response_url") or "")

        async def later() -> None:
            said = await task
            if said and said.get("text"):
                await main._slack_reply(
                    reply_to, str(said["text"]),
                    ephemeral=said.get("response_type") != "in_channel")

        spawn(later(), "slack socket command reply")
        return None
    except Exception as exc:
        log.exception("slack socket: slash command failed")
        return {"response_type": "ephemeral",
                "text": f"That failed before it started: {type(exc).__name__}. "
                        "Nothing was done."}


async def _event(payload: dict) -> None:
    from ..api import slack_events
    await slack_events.handle_event(payload)


async def _interaction(payload: dict) -> None:
    from ..api import main
    await main.handle_interaction(payload)


async def handle_envelope(ws: Any, msg: dict) -> None:
    """Ack first, then hand the payload to its pure handler."""
    envelope_id = str(msg.get("envelope_id") or "")
    kind = str(msg.get("type") or "")
    payload = msg.get("payload")
    payload = payload if isinstance(payload, dict) else {}
    if kind == "slash_commands":
        answer = await _command_ack(payload)
        ack: dict[str, Any] = {"envelope_id": envelope_id}
        if answer is not None:
            ack["payload"] = answer
        await ws.send(json.dumps(ack))
        return
    await ws.send(json.dumps({"envelope_id": envelope_id}))
    if kind == "interactive":
        spawn(_interaction(payload), "slack socket interaction")
    elif kind == "events_api":
        spawn(_event(payload), "slack socket event")
    else:
        log.info("slack socket: ignoring an envelope of type %r", kind)


async def _serve(ws: Any) -> tuple[bool, bool]:
    """Read one connection until it ends: (Slack asked for the reconnect with
    `disconnect`, so the next open is immediate; Slack said hello on it)."""
    greeted = False
    async for raw in ws:
        try:
            msg = json.loads(raw)
        except ValueError:
            log.warning("slack socket: a frame that is not JSON, ignored")
            continue
        if not isinstance(msg, dict):
            continue
        kind = msg.get("type")
        if kind == "hello":
            STATE.connected = True
            STATE.hello_at = datetime.now(UTC)
            STATE.down_since = None
            STATE.last_error = ""
            greeted = True
            continue
        if kind == "disconnect":
            log.info("slack socket: disconnect (%s); reconnecting",
                     msg.get("reason") or "no reason")
            return True, greeted
        if msg.get("envelope_id"):
            await handle_envelope(ws, msg)
    return False, greeted


async def run(app_token: str, *, opener: Opener | None = None,
              connect: Callable[[str], Any] | None = None) -> None:
    """Hold a Socket Mode connection open until cancelled."""
    opener = opener or open_url
    connect = connect or _connect
    STATE.running = True
    STATE.down_since = time.monotonic()
    backoff = BACKOFF_START_S
    try:
        while True:
            asked = greeted = False
            try:
                url = await opener(app_token)
                STATE.opens += 1
                async with connect(url) as ws:
                    asked, greeted = await _serve(ws)
            except asyncio.CancelledError:
                raise
            except Exception as exc:                    # noqa: BLE001
                # The type and Slack's error code only: the wss URL holds a
                # ticket and the token must never reach a log.
                STATE.last_error = (str(exc) if isinstance(exc, SocketOpenError)
                                    else type(exc).__name__)
                log.warning("slack socket: connection failed (%s)", STATE.last_error)
            if greeted or STATE.connected:
                backoff = BACKOFF_START_S
            if STATE.connected or STATE.down_since is None:
                STATE.down_since = time.monotonic()
            STATE.connected = False
            if asked and greeted:
                continue
            await _sleep(backoff)
            backoff = min(backoff * 2, BACKOFF_CAP_S)
    finally:
        STATE.running = False
        STATE.connected = False


def start() -> asyncio.Task | None:
    """The client task, or None when SLACK_APP_TOKEN is unset."""
    app_token = token()
    if not app_token:
        log.info("SLACK_APP_TOKEN is unset; Slack Socket Mode is off")
        return None
    return asyncio.create_task(run(app_token), name="atlas.slack socket")


async def check_socket() -> Observation:
    if not token():
        return Observation(
            check=CHECK_KEY, health=Health.UNKNOWN,
            detail="SLACK_APP_TOKEN is not set, so Socket Mode is off and "
                   "the socket cannot be measured")
    if not STATE.running:
        return Observation(
            check=CHECK_KEY, health=Health.UNKNOWN,
            detail="the Socket Mode client is not running in this process")
    if STATE.connected and STATE.hello_at is not None:
        return Observation(
            check=CHECK_KEY, health=Health.OK,
            detail=f"connected, Slack's hello at {STATE.hello_at:%Y-%m-%d %H:%M:%S} UTC",
            evidence={"hello_at": STATE.hello_at.isoformat()})
    down = (time.monotonic() - STATE.down_since) if STATE.down_since is not None else 0.0
    why = f"; last error {STATE.last_error}" if STATE.last_error else ""
    if down >= GRACE_S:
        return Observation(
            check=CHECK_KEY, health=Health.FAILING, value=round(down), unit="s",
            detail=f"down for {down:.0f}s, past the {GRACE_S:.0f}s grace{why}")
    return Observation(
        check=CHECK_KEY, health=Health.UNKNOWN, value=round(down), unit="s",
        detail=f"connecting, down for {down:.0f}s (grace {GRACE_S:.0f}s){why}")


CHECK = Check(
    name="socket", run=check_socket, interval=timedelta(minutes=1),
    severity_on_fail=Severity.HIGH, baseline_sigma=None,
    timeout=timedelta(seconds=5),
    description="Slack Socket Mode connection (approvals and commands from Slack)")


def register_check() -> None:
    """Put `slack.socket` beside the modules' checks, so the scheduler runs it
    and /api/overview shows it."""
    from ..kernel.registry import REGISTRY
    REGISTRY.checks[CHECK_KEY] = CHECK


def unregister_check() -> None:
    """Undo `register_check` at shutdown: the registry is process-global."""
    from ..kernel.registry import REGISTRY
    if REGISTRY.checks.get(CHECK_KEY) is CHECK:
        del REGISTRY.checks[CHECK_KEY]
