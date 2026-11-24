"""Drive `scheduler.loop()` for a counted number of passes, not a stretch of
wall-clock time.

A test that ran the loop "for 0.4 s" and then counted what happened was
measuring the machine: on a loaded runner, or with one real step left
unstubbed, the same code gave 5 passes or 1. Here every step the loop names
is a no-op unless the test replaces it, and the test waits for the passes it
needs -- with a ceiling, so a loop that really is stuck fails instead of
hanging the job.
"""
from __future__ import annotations

import asyncio
import inspect

import pytest

from atlas.kernel import scheduler

# Called by loop() but not steps: they run the steps.
_RUNNERS = {"_step", "_leased_step"}
# loop() reads this once at the top of every pass, before any step.
_PASS_SEAM = "_durable_enabled"


class QuietLoop:
    """`scheduler.loop` with every step a no-op and its passes countable."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self.monkeypatch = monkeypatch
        self.started = 0
        # LAST_CYCLE_AT as each pass began: entry k is what pass k stamped.
        self.stamps: list[float | None] = []
        self._want = 0
        self._reached = asyncio.Event()

        names = set(scheduler.loop.__code__.co_names)
        assert _PASS_SEAM in names, (
            f"scheduler.loop no longer calls {_PASS_SEAM}(); passes cannot be "
            "counted -- move the seam in tests/scheduler_support.py")

        async def noop() -> None:
            return None

        # Derived from loop() itself, so a step added later is quiet here too
        # instead of quietly doing real work inside a timing test.
        for name in names - _RUNNERS:
            if inspect.iscoroutinefunction(getattr(scheduler, name, None)):
                monkeypatch.setattr(scheduler, name, noop)
        # The approvals board is a method on a module, not a name in loop().
        monkeypatch.setattr(scheduler.board, "step", noop)
        monkeypatch.setattr(scheduler, _PASS_SEAM, self._pass_begins)
        monkeypatch.setattr(scheduler, "LAST_CYCLE_AT", None)

    @property
    def passes(self) -> int:
        """Passes finished: each one ends before the next begins."""
        return max(self.started - 1, 0)

    def _pass_begins(self) -> bool:
        self.started += 1
        self.stamps.append(scheduler.LAST_CYCLE_AT)
        if self.passes >= self._want:
            self._reached.set()
        return False                    # durable core off: no lease, no outbox

    async def run(self, passes: int, *, interval: float = 0.01,
                  ceiling: float = 10.0) -> None:
        """Run the loop until `passes` passes have finished, then stop it.

        AssertionError if they have not finished within `ceiling` seconds --
        a bound on a failure, never the condition for passing."""
        self._want = passes
        task = asyncio.create_task(scheduler.loop(interval=interval))
        try:
            await asyncio.wait_for(self._reached.wait(), timeout=ceiling)
        except TimeoutError:
            raise AssertionError(
                f"the loop finished {self.passes} of {passes} passes in "
                f"{ceiling:.0f}s -- it is blocked") from None
        finally:
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task

