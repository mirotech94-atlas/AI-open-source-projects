"""The run record and the cancel signal.

Every question is one run, a row in `assistant_runs` (db/schema.sql): a
short id made here, when it started, the SHA-256 of the question -- never
its text --, the mode and skill, its status (queued, running, completed,
failed, cancelled), how many tool calls and tokens it spent, which provider
and model answered, how long it took, and the error when it failed.

The row is bookkeeping. A write that fails is logged and the answer goes
on (the same rule `kernel.knowledge._keep` keeps for the library): losing
the record of an answer must never cost him the answer.

Cancel is a signal, not a kill. `cancel(run_id)` marks the run; the loop
checks the mark before every model turn and before every tool call, and
stops there with what it has. A tool call already under way finishes --
every one of them is a read, so there is nothing half-done to undo. The
signal lives in this process: a run on another worker is marked cancelled
in the table but does not see it until a later release reads the table
between steps (ASSISTANT.md, known limitations).
"""
from __future__ import annotations

import hashlib
import logging
import secrets
from dataclasses import dataclass, field

from ...kernel.state import execute_sql

log = logging.getLogger("atlas.assistant.runs")

QUEUED, RUNNING, COMPLETED, FAILED, CANCELLED = (
    "queued", "running", "completed", "failed", "cancelled")
STATUSES = (QUEUED, RUNNING, COMPLETED, FAILED, CANCELLED)


@dataclass(slots=True)
class Live:
    """A run this process is answering right now."""
    run_id: str
    who: str
    channel: str
    cancelled: bool = False
    by: str = ""
    extra: dict = field(default_factory=dict)


_LIVE: dict[str, Live] = {}


def new_id() -> str:
    return secrets.token_hex(4)


def question_sha(question: str) -> str:
    return hashlib.sha256((question or "").encode("utf-8")).hexdigest()


async def _write(sql: str, *args: object) -> bool:
    try:
        await execute_sql(sql, *args)
    except Exception as exc:  # noqa: BLE001
        log.warning("assistant run record not written (%s)", type(exc).__name__)
        return False
    return True


async def start(question: str, *, mode: str, skill: str, who: str,
                channel: str) -> Live:
    run = Live(new_id(), who, channel)
    _LIVE[run.run_id] = run
    await _write(
        """INSERT INTO assistant_runs (id, question_sha, mode, skill, status)
           VALUES ($1, $2, $3, $4, $5)""",
        run.run_id, question_sha(question), mode, skill, RUNNING)
    return run


async def finish(run: Live, *, status: str, tool_calls: int, tokens: int,
                 provider: str, model: str, latency_ms: int,
                 error: str | None = None) -> None:
    _LIVE.pop(run.run_id, None)
    await _write(
        """UPDATE assistant_runs
              SET status = $2, tool_calls = $3, tokens = $4, provider = $5,
                  model = $6, latency_ms = $7, error = $8
            WHERE id = $1""",
        run.run_id, status, tool_calls, tokens, provider, model, latency_ms,
        (error or None) and str(error)[:300])


def is_cancelled(run: Live | None) -> bool:
    return bool(run and run.cancelled)


async def cancel(run_id: str, *, by: str = "") -> str:
    """Ask a run to stop. Says what happened, never raises."""
    rid = (run_id or "").strip().lower()
    run = _LIVE.get(rid)
    if run is not None:
        run.cancelled, run.by = True, by
        await _write("UPDATE assistant_runs SET status = $2 WHERE id = $1 "
                     "AND status IN ('queued', 'running')", rid, CANCELLED)
        return f"Stopping run {rid}: it ends at its next step with what it has."
    return (f"Run {rid} is not running here -- it has finished, or never "
            f"existed. Nothing was stopped.")


async def cancel_mine(who: str, *, channel: str = "") -> str:
    """`stop`: every run this person started that is still going (in this
    channel, when one is given)."""
    mine = [r for r in _LIVE.values()
            if r.who == who and not r.cancelled and (not channel or r.channel == channel)]
    if not mine:
        return "Nothing of yours is running. Nothing was stopped."
    for run in mine:
        await cancel(run.run_id, by=who)
    ids = ", ".join(r.run_id for r in mine)
    return f"Stopping {ids}: each ends at its next step with what it has."


def live() -> list[Live]:
    return list(_LIVE.values())
