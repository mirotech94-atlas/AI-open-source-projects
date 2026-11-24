"""Ring again when a CRITICAL call went unanswered, a bounded number of times.

`escalation.CRITICAL_RETRIES` and `ladder()` have described this since
COMMS.md 3.3 ("call again in two minutes, then every five") and nothing
ever dialled: one missed call at night and the incident sat in Slack until
morning. Miro's voice-partner brief (2026-10-04) asks for the step-up.

What this does, and what it never does:

  * Only a call `channels.twilio` placed is remembered, with the message it
    was about. Only CRITICAL reaches that channel.
  * When Twilio reports the call unanswered (`/webhooks/twilio/voice/status`),
    the next ring is scheduled: two minutes, then five. `CALL_RERINGS`
    (default 2, at most 4) bounds it, so at the default he gets three calls
    over seven minutes and then it stops. Each miss still posts to Slack.
  * Before dialling again it asks whether there is still anything to decide:
    a card that is still pending, or a check that is still failing and has
    not been acknowledged. Approved in Slack, declined, expired, recovered
    or acknowledged: no call. If that cannot be read, it does not ring
    (a second call about nothing is how a phone gets ignored).
  * `ATLAS_NO_RERING` set to anything turns it off.

Silence still never decides anything: a re-ring only asks again. State is
in memory, like the rest of the call bookkeeping; a restart forgets a
pending re-ring, and the Slack post remains.
"""
from __future__ import annotations

import asyncio
import logging
import os
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from .comms import Outbound
from .escalation import CRITICAL_RETRIES

log = logging.getLogger("atlas.rering")

DEFAULT_RERINGS = 2
KEEP_S = 2 * 3600
MAX_TRACKED = 64


@dataclass(slots=True)
class Placed:
    msg: Outbound
    attempt: int            # 0 for the first call, 1 for the first re-ring
    at: float


_PLACED: dict[str, Placed] = {}
_TASKS: set[asyncio.Task] = set()


def reset() -> None:
    for task in list(_TASKS):
        task.cancel()
    _TASKS.clear()
    _PLACED.clear()


def limit() -> int:
    if os.getenv("ATLAS_NO_RERING", "").strip():
        return 0
    try:
        n = int(os.getenv("CALL_RERINGS", str(DEFAULT_RERINGS)))
    except ValueError:
        n = DEFAULT_RERINGS
    return max(0, min(len(CRITICAL_RETRIES), n))


def placed(sid: str | None, msg: Outbound, attempt: int = 0) -> None:
    """Remember what a call ATLAS just placed was about."""
    if not sid:
        return
    now = time.monotonic()
    for old in [k for k, v in _PLACED.items() if now - v.at > KEEP_S]:
        _PLACED.pop(old, None)
    while len(_PLACED) >= MAX_TRACKED:
        _PLACED.pop(next(iter(_PLACED)))
    _PLACED[sid] = Placed(msg, attempt, now)


def _pids(msg: Outbound) -> list[int]:
    out = []
    for button in msg.buttons:
        verdict, _, pid = str(button.value).partition(":")
        if verdict == "approve" and pid.isdigit():
            out.append(int(pid))
    return out


async def still_open(msg: Outbound) -> bool:
    """Is there still something for him to decide or look at? False when
    it cannot be read: a call that might be about nothing is not placed."""
    try:
        pids = _pids(msg)
        if pids:
            from .state import fetch_one
            row = await fetch_one(
                "SELECT count(*) AS n FROM proposals WHERE id = ANY($1::bigint[]) "
                "AND status = 'pending' AND expires_at > now()", pids)
            return bool(row and int(row["n"]) > 0)
        key = (msg.incident_key or "").strip()
        if key:
            from . import acks
            from .state import fetch_one
            if await acks.latest_health(key) != "failing":
                return False
            row = await fetch_one("SELECT 1 AS x FROM acks WHERE key = $1 AND until > now()", key)
            return row is None
    except Exception as exc:  # noqa: BLE001
        log.warning("could not tell whether to ring again: %s", type(exc).__name__)
    return False


async def _ring_later(entry: Placed, delay: float,
                      send: Callable[[Outbound], Awaitable[str | None]],
                      sleep: Callable[[float], Awaitable[None]]) -> str:
    await sleep(delay)
    if not await still_open(entry.msg):
        log.info("not ringing again: nothing is waiting on him any more")
        return "settled"
    try:
        sid = await send(entry.msg)
    except Exception as exc:  # noqa: BLE001
        log.warning("the re-ring could not be placed: %s", type(exc).__name__)
        return "failed"
    placed(sid, entry.msg, entry.attempt + 1)
    log.info("rang him again (attempt %s)", entry.attempt + 2)
    return "rang"


def missed(sid: str, *,
           send: Callable[[Outbound], Awaitable[str | None]] | None = None,
           sleep: Callable[[float], Awaitable[None]] = asyncio.sleep) -> asyncio.Task | None:
    """A call ATLAS placed ended unanswered: schedule the next ring, or
    nothing. Returns the task (for tests), or None when no ring is due."""
    entry = _PLACED.pop(sid, None)
    if entry is None or entry.attempt >= limit():
        return None
    if send is None:
        from .comms import CHANNELS
        channel = CHANNELS.get("voice")
        if channel is None:
            return None
        send = channel.send
    delay = CRITICAL_RETRIES[entry.attempt].total_seconds()
    task = asyncio.get_running_loop().create_task(_ring_later(entry, delay, send, sleep))
    _TASKS.add(task)
    task.add_done_callback(_TASKS.discard)
    return task


def answered(sid: str) -> None:
    """He took it: nothing more to schedule for that call."""
    _PLACED.pop(sid, None)
