"""When is a reading still a reading? One rule, for every reader of the board.

An observation says what a check saw when it ran. An OK from three intervals
ago is what a dead scheduler looks like, not a current OK (invariant 7), so
everything that says "fine" -- the screen, the spoken check, the self-model,
the agent's status tool -- applies this before it counts anything:

  - no observation at all is unknown;
  - a health word the enum does not name is unknown;
  - a reading older than STALE_FACTOR of the check's own interval, plus
    STALE_GRACE, is unknown.

It lives in the kernel so the kernel's own readers (`selfmodel`) can use it;
`api/system_check.py` re-exports it. Pure: no I/O, no imports that act.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

# How stale is stale: a check's newest reading older than this many of its
# own intervals (plus the grace) is no longer a reading of now.
STALE_FACTOR = 3
STALE_GRACE = timedelta(minutes=10)

# Worst first. Anything not named here is unknown, never folded into OK.
HEALTH_RANK = {"failing": 3, "degraded": 2, "unknown": 1, "ok": 0}


def known_health(value: Any) -> str:
    """Anything not recognised is an absence of knowledge, never a pass."""
    word = str(value or "").strip().lower()
    return word if word in HEALTH_RANK else "unknown"


def effective_health(health: Any, observed_at: datetime | None,
                     interval: timedelta | None, now: datetime) -> tuple[str, bool]:
    """`(health, stale)` for a check's newest observation, as it stands NOW."""
    if observed_at is None:
        return "unknown", False
    word = known_health(health)
    if interval is not None and now - observed_at > interval * STALE_FACTOR + STALE_GRACE:
        return "unknown", True
    return word, False


def row_health(health: Any, observed_at: datetime | None, interval_s: Any,
               now: datetime | None = None) -> str:
    """`effective_health` for a row that carries `interval_s` (the checks
    table's column)."""
    interval = timedelta(seconds=int(interval_s)) if interval_s else None
    return effective_health(health, observed_at, interval,
                            now or datetime.now(UTC))[0]
