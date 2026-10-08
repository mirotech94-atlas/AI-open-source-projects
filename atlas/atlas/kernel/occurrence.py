"""One run per scheduled window, durably (reliability plan, RA-F1/RA-F2).

The daily jobs guarded themselves with a date in process memory, so every
restart inside the window ran them again: 37 morning briefs on 2026-09-22,
10 nightly reviews on 2026-09-27 (tonight's deploys restarted the API every
few minutes after 23:00). A window is now claimed in Postgres, atomically,
before the job runs:

    if await occurrence.claim("review", "2026-09-27"):
        try:
            await send()
        except Exception:
            await occurrence.release("review", "2026-09-27")   # retried later
            raise
        await occurrence.done("review", "2026-09-27")

A claim that is already 'running' or 'done' is refused. A 'failed' one may
be claimed again (the job retries on a later tick, never lost for the
window). If the claim cannot be made at all (database unreadable), the job
does not run this tick: a missing digest is retried; a duplicate cannot be
taken back. A run stuck 'running' past STALE_AFTER (its process died) may be
claimed again.

RA-F2 adds what happens to the windows a job did NOT run (kernel.missed):
each is recorded here as 'skipped' with the reason, and `history` answers
which windows are already settled. `claim_once` is the at-most-once claim
for jobs whose run has partial outward effects (a weekly plan files many
requests): a row in any state, a stale 'running' included, is never taken
again, so a crash mid-run can never file the same week twice.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any

log = logging.getLogger("atlas.occurrence")

STALE_AFTER = timedelta(minutes=30)


async def claim(job: str, window: str,
                scheduled_for: datetime | None = None) -> bool:
    from .state import fetch_one
    try:
        row = await fetch_one(
            """INSERT INTO schedule_runs
                      (job, occurrence, status, claimed_at, scheduled_for)
                    VALUES ($1, $2, 'running', now(), $4::timestamptz)
               ON CONFLICT (job, occurrence) DO UPDATE
                  SET status = 'running', claimed_at = now(),
                      attempts = schedule_runs.attempts + 1,
                      scheduled_for = COALESCE(schedule_runs.scheduled_for,
                                               EXCLUDED.scheduled_for)
                WHERE schedule_runs.status = 'failed'
                   OR (schedule_runs.status = 'running'
                       AND schedule_runs.claimed_at < now() - $3::interval)
               RETURNING job""",
            job, window, STALE_AFTER, scheduled_for)
    except Exception as exc:                            # noqa: BLE001
        log.warning("%s %s: the window could not be claimed (%s); not running "
                    "it this tick", job, window, type(exc).__name__)
        return False
    return row is not None


async def _mark(job: str, window: str, status: str) -> None:
    from .state import execute_sql
    try:
        await execute_sql(
            """UPDATE schedule_runs SET status = $3, finished_at = now()
                WHERE job = $1 AND occurrence = $2""", job, window, status)
    except Exception as exc:                            # noqa: BLE001
        log.warning("%s %s: could not record %s (%s)", job, window, status,
                    type(exc).__name__)


async def done(job: str, window: str) -> None:
    await _mark(job, window, "done")


async def release(job: str, window: str) -> None:
    """The run failed: the window may be claimed again on a later tick."""
    await _mark(job, window, "failed")


async def claim_once(job: str, window: str,
                     scheduled_for: datetime | None = None) -> bool:
    """At most once: the first claim wins and nothing ever takes the window
    again -- not after a failure, not after a crash left it 'running'. For
    jobs where a second run is worse than a missing one. Unreadable
    database: False (not run this tick), as with `claim`."""
    from .state import fetch_one
    try:
        row = await fetch_one(
            """INSERT INTO schedule_runs
                      (job, occurrence, status, claimed_at, scheduled_for)
                    VALUES ($1, $2, 'running', now(), $3::timestamptz)
               ON CONFLICT (job, occurrence) DO NOTHING
               RETURNING job""",
            job, window, scheduled_for)
    except Exception as exc:                            # noqa: BLE001
        log.warning("%s %s: the window could not be claimed (%s); not running "
                    "it this tick", job, window, type(exc).__name__)
        return False
    return row is not None


async def fail(job: str, window: str, detail: str = "") -> None:
    """A run that failed and is NOT to be retried (a `claim_once` job). The
    row stays settled; `detail` says what happened."""
    from .state import execute_sql
    try:
        await execute_sql(
            """UPDATE schedule_runs SET status = 'failed', finished_at = now(),
                      detail = $3
                WHERE job = $1 AND occurrence = $2""", job, window, detail[:500])
    except Exception as exc:                            # noqa: BLE001
        log.warning("%s %s: could not record failed (%s)", job, window,
                    type(exc).__name__)


async def skip(job: str, window: str, scheduled_for: datetime,
               reason: str) -> bool:
    """Record a window the missed-run policy will not run. A window that
    failed earlier and can no longer be retried becomes 'skipped' too; one
    that ran, or is running, is left alone. False when it could not be
    written (the next pass tries again)."""
    from .state import execute_sql
    try:
        await execute_sql(
            """INSERT INTO schedule_runs
                      (job, occurrence, status, claimed_at, finished_at,
                       scheduled_for, detail, attempts)
                    VALUES ($1, $2, 'skipped', now(), now(), $3::timestamptz,
                            $4, 0)
               ON CONFLICT (job, occurrence) DO UPDATE
                  SET status = 'skipped', finished_at = now(),
                      detail = EXCLUDED.detail,
                      scheduled_for = COALESCE(schedule_runs.scheduled_for,
                                               EXCLUDED.scheduled_for)
                WHERE schedule_runs.status = 'failed'""",
            job, window, scheduled_for, reason[:500])
    except Exception as exc:                            # noqa: BLE001
        log.warning("%s %s: could not record skipped (%s)", job, window,
                    type(exc).__name__)
        return False
    return True


async def history(job: str, windows: list[str]
                  ) -> tuple[dict[str, dict[str, Any]], bool] | None:
    """The recorded state of `windows` for `job`, and whether the job has any
    row at all (a job with none has no history to reason from: runs before
    RA-F2 were never recorded). None when it cannot be read -- the caller
    then runs nothing this tick, never treats "unreadable" as "never ran"."""
    from .state import fetch_all, fetch_one
    try:
        rows = await fetch_all(
            """SELECT occurrence, status, claimed_at FROM schedule_runs
                WHERE job = $1 AND occurrence = ANY($2::text[])""", job, windows)
        any_row = await fetch_one(
            "SELECT 1 AS x FROM schedule_runs WHERE job = $1 LIMIT 1", job)
    except Exception as exc:                            # noqa: BLE001
        log.warning("%s: schedule history unreadable (%s); not running it this "
                    "tick", job, type(exc).__name__)
        return None
    return ({r["occurrence"]: {"status": r["status"], "claimed_at": r["claimed_at"]}
             for r in rows}, any_row is not None)
