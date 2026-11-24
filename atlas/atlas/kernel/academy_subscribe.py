"""Queued signup confirmations for the storefront's free updates.

The public form only writes a pending row (`modules/academy/subscribers.py`);
this pass, every few minutes, drives each queued confirmation through the one
path that acts: `academy.send_signup_confirmation` via `execute`. Under
ATLAS_WATCH_ONLY (the default) the executor refuses, the rows stay queued, and
a pending row nobody confirms is deleted after 7 days: silence is never
consent and nothing is sent on a timeout. Never a newsletter.
"""
from __future__ import annotations

import logging
from datetime import timedelta

log = logging.getLogger("atlas.academy_subscribe")

BATCH = 10


def interval() -> timedelta:
    return timedelta(minutes=5)


async def tick() -> int:
    """One pass. Returns the confirmations sent. The first refusal (a hold, a
    ceiling, watch-only) ends the pass: the rest stay queued for the next."""
    from ..modules.academy import subscribers
    from .executor import ExecutionRequest, execute
    sent = 0
    try:
        await subscribers._store.purge(subscribers._now())
        due = await subscribers._store.due(BATCH)
    except Exception:  # noqa: BLE001
        log.warning("academy subscribers: the queue could not be read")
        return 0
    for sub_id in due:
        made = await execute(ExecutionRequest(
            subscribers.ACTION, {"subscriber_id": sub_id},
            reason="a visitor asked for free storefront updates (double opt-in)"))
        if not made.ok:
            log.info("academy subscribers: stopped: %s", made.detail[:120])
            break
        sent += 1
    return sent
