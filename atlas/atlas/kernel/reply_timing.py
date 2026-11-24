"""How long a reply took, per channel and per stage. A measurement, nothing else.

One `reply_timings` row per answered turn: the channel (`whatsapp` or `portal`),
the route it took, the total in milliseconds and a split into stages. The row
holds NO message text, no question and no answer, only numbers and a model name.

Low risk by construction, because it runs on the live reply path:
  * it only reads the clock (`time.monotonic()`), never changes a reply;
  * every failure is swallowed (the exception class is logged, never a message);
  * the row is written after the reply is built, in a background task, so the
    insert is never on the reply path;
  * with no active timer every call is a no-op.

A write to ATLAS's own table is not an outside-world write (invariant 1), the
same as `memory_reads` and `chat_memory.remember`. Nothing here acts.
"""
from __future__ import annotations

import asyncio
import contextlib
import contextvars
import json
import logging
import time
from collections.abc import Iterator

from .state import execute_sql

log = logging.getLogger("atlas.reply_timing")

CHANNELS = frozenset({"whatsapp", "portal"})
ROUTES = frozenset({"greeting", "command", "chat", "live"})
STAGES = ("recall", "live_read", "model", "other")
WRITE_TIMEOUT_S = 5.0

_CURRENT: contextvars.ContextVar[Timer | None] = contextvars.ContextVar(
    "reply_timer", default=None)
_PENDING: set[asyncio.Task] = set()


class Timer:
    """One turn's clock. Stage totals accumulate; `other` is the remainder."""

    def __init__(self, channel: str) -> None:
        self.channel = channel
        self.started = time.monotonic()
        self.stages: dict[str, float] = {}
        self.route: str | None = None
        self.model: str | None = None
        self.ok = True
        self.recorded = False

    def total_ms(self) -> int:
        return max(0, int((time.monotonic() - self.started) * 1000))


def begin(channel: str) -> Timer:
    """Start the clock for this turn and make it the current one."""
    timer = Timer(channel)
    _CURRENT.set(timer)
    return timer


def current() -> Timer | None:
    return _CURRENT.get()


@contextlib.contextmanager
def stage(name: str) -> Iterator[None]:
    """Add the time spent inside the block to a stage. A no-op with no timer."""
    timer = _CURRENT.get()
    if timer is None:
        yield
        return
    t0 = time.monotonic()
    try:
        yield
    finally:
        key = name if name in STAGES else "other"
        timer.stages[key] = timer.stages.get(key, 0.0) + (time.monotonic() - t0)


def note(route: str | None = None, model: str | None = None,
         ok: bool | None = None) -> None:
    """Say which route answered, which model, and whether it worked."""
    timer = _CURRENT.get()
    if timer is None:
        return
    if route is not None:
        timer.route = route
    if model:
        timer.model = str(model)[:80]
    if ok is not None:
        timer.ok = ok


def _row(timer: Timer) -> tuple:
    total = timer.total_ms()
    parts = {k: int(timer.stages.get(k, 0.0) * 1000) for k in STAGES if k != "other"}
    # `other` is whatever the named stages do not account for.
    parts["other"] = max(0, total - sum(parts.values()))
    route = timer.route if timer.route in ROUTES else "chat"
    return (timer.channel, route, total,
            json.dumps({k: parts[k] for k in STAGES}), timer.model, timer.ok)


async def record(timer: Timer | None) -> None:
    """Write the row. Never raises; at most once per timer."""
    if timer is None or timer.recorded or timer.channel not in CHANNELS:
        return
    timer.recorded = True
    try:
        await _write(_row(timer))
    except Exception as exc:                                # noqa: BLE001
        log.warning("reply timing not saved: %s", type(exc).__name__)


async def _write(row: tuple) -> None:
    try:
        await asyncio.wait_for(execute_sql(
            "INSERT INTO reply_timings (channel, route, total_ms, stages, model, ok) "
            "VALUES ($1,$2,$3,$4::jsonb,$5,$6)", *row), WRITE_TIMEOUT_S)
    except Exception as exc:                                # noqa: BLE001
        log.warning("reply timing not saved: %s", type(exc).__name__)


def schedule(timer: Timer | None) -> None:
    """Write the row in the background. The total is read now, so the insert
    adds nothing to it, and a failure changes nothing for the caller."""
    if timer is None or timer.recorded or timer.channel not in CHANNELS:
        return
    try:
        # Frozen here, not when the task runs: loop delay and any background
        # work started by the turn (it inherits the timer) never count.
        row = _row(timer)
        timer.recorded = True
        task = asyncio.get_running_loop().create_task(_write(row))
    except Exception as exc:                                # noqa: BLE001
        log.warning("reply timing not scheduled: %s", type(exc).__name__)
        return
    _PENDING.add(task)
    task.add_done_callback(_PENDING.discard)


async def drain() -> None:
    """Wait for scheduled writes (tests and shutdown). A task that belongs to
    another event loop is waited for by polling, never awaited from here."""
    loop = asyncio.get_running_loop()
    for _ in range(500):
        mine = [t for t in _PENDING if t.get_loop() is loop]
        if mine:
            await asyncio.gather(*mine, return_exceptions=True)
        elif _PENDING:
            await asyncio.sleep(0.01)
        else:
            return
