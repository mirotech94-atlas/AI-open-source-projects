"""RA-D3: a detached scheduler step is cancelled at its deadline, so a hung
one can no longer skip every later cycle under its own name."""
from __future__ import annotations

import asyncio

from atlas.kernel import scheduler


async def test_a_hung_detached_step_is_cancelled_at_its_deadline(monkeypatch):
    monkeypatch.setitem(scheduler.DETACHED_DEADLINE_S, "signals", 0.05)
    scheduler._tasks.pop("signals", None)
    cancelled = asyncio.Event()

    async def hang():
        try:
            await asyncio.sleep(3600)
        except asyncio.CancelledError:
            cancelled.set()
            raise

    assert scheduler._detach("signals", hang())
    await asyncio.wait_for(cancelled.wait(), timeout=2)
    await asyncio.sleep(0)
    assert not scheduler._running("signals")        # the next cycle may run it


def test_every_detached_step_has_a_deadline():
    assert set(scheduler._DETACHED) <= set(scheduler.DETACHED_DEADLINE_S)
