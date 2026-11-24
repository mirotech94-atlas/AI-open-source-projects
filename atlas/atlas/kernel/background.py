"""Every background task ATLAS starts, held and accounted for at shutdown.

A task started with `asyncio.create_task` and not awaited has two problems:
the loop keeps only a weak reference to it, so it can be collected mid-run;
and at shutdown nothing cancels or waits for it, so it dies at loop close
with no line in the log -- a weekly marketing plan half filed and never
recorded, an inbox triage that promised a text and never sent it.

`track` holds a strong reference until the task finishes and logs its
exception if it raised. `cancel_all`, called from the API's lifespan,
cancels whatever is still running, waits for it (bounded), and logs one
line per job it cut short.

Reads and writes nothing; imports nothing that acts.
"""
from __future__ import annotations

import asyncio
import logging
from collections.abc import Coroutine
from typing import Any

log = logging.getLogger("atlas.background")

_TASKS: dict[asyncio.Task, str] = {}

SHUTDOWN_TIMEOUT_S = 10.0


def track(task: asyncio.Task, name: str, *, log_errors: bool = True) -> asyncio.Task:
    """Hold `task` until it finishes; say so if it raised."""
    _TASKS[task] = name

    def _done(t: asyncio.Task) -> None:
        _TASKS.pop(t, None)
        if log_errors and not t.cancelled() and (exc := t.exception()):
            log.error("background job %r failed: %s", name, exc, exc_info=exc)

    task.add_done_callback(_done)
    return task


def spawn(coro: Coroutine[Any, Any, Any], name: str) -> asyncio.Task:
    """`asyncio.create_task`, tracked -- and always background for the free
    models' reserve (kernel/quota.py): a task copies the context it was
    started in, so a job started during one of Miro's voice turns would
    otherwise spend the allowance kept for him. A handler that IS his turn
    (a Slack message) marks itself interactive inside."""
    from .quota import background_context
    return track(asyncio.create_task(coro, name=f"atlas.{name}",
                                     context=background_context()), name)


def running() -> list[str]:
    return [name for task, name in _TASKS.items() if not task.done()]


async def cancel_all(timeout: float = SHUTDOWN_TIMEOUT_S) -> list[str]:
    """Cancel every tracked job still running, wait up to `timeout`, and log
    one line for each that was cut short. Returns their names."""
    pending = [(task, name) for task, name in list(_TASKS.items())
               if not task.done()]
    for task, name in pending:
        log.warning("shutdown: %s was still running and is cut short", name)
        task.cancel()
    if pending:
        _done, still = await asyncio.wait([t for t, _ in pending], timeout=timeout)
        for task, name in pending:
            if task in still:
                log.error("shutdown: %s did not stop within %.0fs", name, timeout)
    return [name for _, name in pending]
