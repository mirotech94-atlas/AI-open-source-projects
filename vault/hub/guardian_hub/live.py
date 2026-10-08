"""In-process async pub/sub for the dashboard's live feed (`WS /api/v1/ws`).

One `Bus` singleton, keyed by `tenant_id`. Every write path (ingest, the
dashboard write routes, the sensor's queue-result callback) calls
`bus.publish(...)` from what is very often a *sync* request handler running
on a worker thread, never the event loop itself -- so `publish` schedules
delivery onto the loop captured at startup via
`asyncio.run_coroutine_threadsafe` and is a complete no-op when no loop has
been captured (a CLI script, a unit test that never opens `/ws`, or any
code path that runs before the app's startup event fires). That keeps every
call site trivially safe to add without special-casing tests.

Each `/ws` connection owns a bounded `asyncio.Queue(maxsize=200)`. A slow
browser must never back-pressure ingest, so a full queue drops its OLDEST
frame to make room for the new one, rather than blocking the publisher or
dropping the new frame.
"""

from __future__ import annotations

import asyncio
import datetime as dt
from collections.abc import AsyncIterator
from dataclasses import dataclass, field

QUEUE_MAXSIZE = 200


def _now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


@dataclass
class LiveEvent:
    type: str
    data: dict = field(default_factory=dict)
    ts: str = field(default_factory=_now_iso)


class Bus:
    def __init__(self) -> None:
        self._loop: asyncio.AbstractEventLoop | None = None
        self._subscribers: dict[str, set[asyncio.Queue]] = {}
        # subscribers dict is mutated only from the captured loop's thread
        # (publish/subscribe/unsubscribe all run there), so no lock needed.

    def set_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop

    def clear_loop(self) -> None:
        self._loop = None

    def has_subscribers(self, tenant_id: str) -> bool:
        """Best-effort: `_subscribers` is only ever mutated from the loop
        thread, so a read from another thread (ingest's rate limiter, see
        `ingest._publish_dns_throttled`) can be a message or two stale --
        acceptable here, same as the no-lock reasoning on `_subscribers`
        itself above."""
        return bool(self._subscribers.get(tenant_id))

    def publish(self, tenant_id: str, type: str, data: dict) -> None:
        """Safe to call from any thread, sync or async. No-op if no loop is
        running (tests that never open a socket, CLI scripts, etc.)."""
        if self._loop is None:
            return
        evt = LiveEvent(type=type, data=data)
        try:
            self._loop.call_soon_threadsafe(self._deliver, tenant_id, evt)
        except RuntimeError:
            pass  # loop is closed/shutting down -- drop the event

    def _deliver(self, tenant_id: str, evt: LiveEvent) -> None:
        for q in list(self._subscribers.get(tenant_id, ())):
            if q.full():
                try:
                    q.get_nowait()   # drop the oldest frame to make room
                except asyncio.QueueEmpty:
                    pass
            try:
                q.put_nowait(evt)
            except asyncio.QueueFull:
                pass   # lost a race with another producer -- drop the new one too

    async def subscribe(self, tenant_id: str) -> AsyncIterator[LiveEvent]:
        q: asyncio.Queue = asyncio.Queue(maxsize=QUEUE_MAXSIZE)
        self._subscribers.setdefault(tenant_id, set()).add(q)
        try:
            while True:
                yield await q.get()
        finally:
            subs = self._subscribers.get(tenant_id)
            if subs is not None:
                subs.discard(q)
                if not subs:
                    self._subscribers.pop(tenant_id, None)


bus = Bus()
