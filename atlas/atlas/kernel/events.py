"""In-process pub/sub for the dashboard's live updates.

Deliberately not Redis. The scheduler and the API run in the same process, the
dashboard is single-user, and an event that is lost on restart costs nothing —
the client refetches on reconnect. Adding a broker here would be infrastructure
serving no requirement.

Nothing here can cause an action. Publishing is fire-and-forget and a slow or
dead subscriber is dropped rather than allowed to block the scheduler.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

log = logging.getLogger("atlas.events")

MAX_QUEUE = 64


@dataclass(frozen=True, slots=True)
class Event:
    kind: str                       # finding | proposal | decision | check | audit
    data: dict[str, Any] = field(default_factory=dict)
    at: datetime = field(default_factory=lambda: datetime.now(UTC))


_subscribers: set[asyncio.Queue[Event]] = set()


def subscribe() -> asyncio.Queue[Event]:
    queue: asyncio.Queue[Event] = asyncio.Queue(maxsize=MAX_QUEUE)
    _subscribers.add(queue)
    return queue


def unsubscribe(queue: asyncio.Queue[Event]) -> None:
    _subscribers.discard(queue)


def publish(kind: str, **data: Any) -> None:
    """Tell every connected dashboard something changed.

    A subscriber whose queue is full is a browser tab that stopped reading.
    Dropping its event is correct: the client shows a stale indicator and
    refetches, which is far better than the scheduler blocking on a dead tab.
    """
    event = Event(kind=kind, data=data)
    for queue in list(_subscribers):
        try:
            queue.put_nowait(event)
        except asyncio.QueueFull:
            log.debug("dropping %s for a subscriber that stopped reading", kind)


def subscriber_count() -> int:
    return len(_subscribers)
