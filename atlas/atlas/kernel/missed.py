"""What a recurring job does about the windows it missed (RA-F2).

A deploy, a crash or a dead host leaves scheduled windows unrun. Before this,
each job decided by accident: a process-memory date meant a restart could run
a window twice, and a job keyed on "today" silently dropped yesterday with no
record that it had been due at all. Every recurring job now declares one of
three policies (the table is `scheduler.POLICIES`):

  skip-stale         a window runs only inside its grace (by default until
                     local midnight of the day it was due). Anything older is
                     recorded 'skipped' and never sent. For reminders and
                     reports: yesterday's brief is noise today.
  run-once           however many windows were missed, only the newest runs,
                     once, however late; every older one is recorded
                     'skipped'. For work whose newest run supersedes the rest
                     (the weekly summary, next week's marketing plan, the
                     nightly retrain).
  bounded-catch-up   up to `max_runs` of the newest missed windows run, oldest
                     of them first, one per tick; older ones are skipped. Only
                     for work where each window is distinct and nothing is
                     posted or sent. No job in the loop needs it today; it is
                     here, tested, so a future per-window job need not invent
                     its own.

Nothing here ever replays a missed week of posts or reminders: at most one
window of a skip-stale or run-once job runs after any outage.

Two safety rules:
  * History that cannot be read means nothing runs this tick (fail closed).
    "Unreadable" is never taken as "never ran".
  * A job with no recorded history at all (the first deploy of this code:
    runs before it were never recorded) considers only its newest window, and
    only inside the grace. It never replays from before records began, and it
    records nothing it cannot know was missed.

Interval jobs (checks every cycle, signals every 15 minutes, the reels
calendar every 30) have no windows to miss: their clock is process memory
that starts at zero, so the first tick after an outage runs once and nothing
is replayed. They are declared in the same table so every loop step has a
stated policy, but they write no `schedule_runs` rows.
"""
from __future__ import annotations

import enum
import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta

log = logging.getLogger("atlas.missed")


class Missed(enum.StrEnum):
    SKIP_STALE = "skip-stale"
    RUN_ONCE = "run-once-on-recovery"
    CATCH_UP = "bounded-catch-up"


@dataclass(frozen=True)
class Window:
    key: str                    # schedule_runs.occurrence
    scheduled_for: datetime     # when it was due (aware, local)


@dataclass(frozen=True)
class JobPolicy:
    job: str
    missed: Missed
    why: str
    # Windows due at or before `now`, oldest first, within `horizon`. None for
    # an interval job: no windows, nothing recorded.
    windows: Callable[[datetime, timedelta], list[Window]] | None = None
    # skip-stale: how late a window may still run. None: until local midnight
    # of the day it was due. Also the bootstrap rule for a job with no history.
    grace: timedelta | None = None
    max_runs: int = 1           # bounded-catch-up only
    horizon: timedelta = timedelta(days=14)
    # Whether a failed (or crashed, stale 'running') window may run again.
    # False for claim_once jobs: a second run is worse than a missing one.
    retry: bool = False
    guard: str = "schedule_runs"    # where the run itself is kept once-only
    # Whether the job is switched on. A job that is off writes no
    # schedule_runs row by design, so its windows are not "missed" (QA's
    # run check skips it). None: always on.
    enabled: Callable[[], bool] | None = None

    @property
    def recorded(self) -> bool:
        return self.windows is not None


def daily(hour: Callable[[], int], key: Callable[[date], str] = date.isoformat
          ) -> Callable[[datetime, timedelta], list[Window]]:
    """One window a day at `hour()` local, keyed by the local date."""
    def windows(now: datetime, horizon: timedelta) -> list[Window]:
        h = hour()
        out = []
        for back in range(horizon.days, -1, -1):
            d = now.date() - timedelta(days=back)
            at = datetime.combine(d, time(h), tzinfo=now.tzinfo)
            if at <= now:
                out.append(Window(key(d), at))
        return out
    return windows


def iso_week(d: date) -> str:
    year, week, _ = d.isocalendar()
    return f"{year}-W{week:02d}"


def weekly(day: Callable[[], int], hour: Callable[[], int],
           key: Callable[[date], str] = date.isoformat
           ) -> Callable[[datetime, timedelta], list[Window]]:
    """One window a week, on weekday `day()` (Monday 0) at `hour()` local."""
    def windows(now: datetime, horizon: timedelta) -> list[Window]:
        h, wd = hour(), day()
        latest = now.date() - timedelta(days=(now.weekday() - wd) % 7)
        out = []
        for back in range(horizon.days // 7, -1, -1):
            d = latest - timedelta(weeks=back)
            at = datetime.combine(d, time(h), tzinfo=now.tzinfo)
            if at <= now:
                out.append(Window(key(d), at))
        return out
    return windows


def _in_grace(policy: JobPolicy, w: Window, now: datetime) -> bool:
    if policy.grace is None:
        return w.scheduled_for.astimezone(now.tzinfo).date() == now.date()
    return now - w.scheduled_for <= policy.grace


def _late(w: Window, now: datetime) -> str:
    hours = (now - w.scheduled_for).total_seconds() / 3600
    return f"{hours:.0f}h late" if hours >= 1 else "late"


def plan(policy: JobPolicy, candidates: list[Window], open_keys: set[str],
         now: datetime) -> tuple[list[Window], list[tuple[Window, str]]]:
    """Pure. `candidates` are every window due within the horizon, oldest
    first; `open_keys` the ones not yet settled (never run, or retryable).
    Returns (windows to run, oldest first; windows to skip, with reasons)."""
    if not candidates:
        return [], []
    newest = candidates[-1]
    open_ = [w for w in candidates if w.key in open_keys]
    run: list[Window] = []
    if policy.missed is Missed.SKIP_STALE:
        if newest.key in open_keys and _in_grace(policy, newest, now):
            run = [newest]
    elif policy.missed is Missed.RUN_ONCE:
        if newest.key in open_keys:
            run = [newest]
    else:
        fresh = [w for w in open_ if now - w.scheduled_for <= policy.horizon]
        run = fresh[-max(1, policy.max_runs):]
    skip = []
    for w in open_:
        if w in run:
            continue
        if policy.missed is Missed.SKIP_STALE:
            why = (f"missed: {_late(w, now)}, past its grace (skip-stale)")
        elif policy.missed is Missed.RUN_ONCE:
            why = (f"missed: superseded by {newest.key}; run-once runs only "
                   f"the newest window")
        else:
            why = (f"missed: older than the {policy.max_runs} newest windows "
                   f"bounded-catch-up runs")
        skip.append((w, why))
    return run, skip


# Newest window a job was found settled for, in this process: saves the
# history query on every 30 s tick once today's window is handled.
_quiet: dict[str, str] = {}


def _open(policy: JobPolicy, key: str, rows: dict[str, dict],
          now: datetime) -> bool:
    row = rows.get(key)
    if row is None:
        return True
    if not policy.retry:
        return False
    if row["status"] == "failed":
        return True
    from .occurrence import STALE_AFTER
    claimed = row.get("claimed_at")
    return (row["status"] == "running" and claimed is not None
            and claimed < now - STALE_AFTER)


async def due(policy: JobPolicy, now: datetime) -> Window | None:
    """The one window `policy.job` should run now, or None. Records every
    window the policy gives up on as 'skipped'. The caller still claims the
    window it is given (`occurrence.claim` / `claim_once`): this decides
    WHICH window; the claim keeps it once-only."""
    if policy.windows is None:
        return None
    candidates = policy.windows(now, policy.horizon)
    if not candidates:
        return None
    newest = candidates[-1]
    if _quiet.get(policy.job) == newest.key:
        return None
    from . import occurrence
    read = await occurrence.history(policy.job, [w.key for w in candidates])
    if read is None:
        return None                                   # fail closed
    rows, has_history = read
    if not has_history:
        # Nothing recorded yet: the only window that can run is the newest,
        # inside its grace. Nothing older is replayed or marked skipped --
        # it may well have run before runs were recorded.
        if _in_grace(policy, newest, now) and _open(policy, newest.key, rows, now):
            return newest
        _quiet[policy.job] = newest.key
        return None
    # Only windows from the newest recorded one on: an unrecorded window
    # older than a recorded one predates the records (or the job), and is
    # not the policy's to call missed.
    recorded = [i for i, w in enumerate(candidates) if w.key in rows]
    if recorded:
        candidates = candidates[recorded[-1]:]
    open_keys = {w.key for w in candidates if _open(policy, w.key, rows, now)}
    run, skip = plan(policy, candidates, open_keys, now)
    all_written = True
    for w, why in skip:
        if await occurrence.skip(policy.job, w.key, w.scheduled_for, why):
            log.info("%s %s: %s", policy.job, w.key, why)
        else:
            all_written = False
    if run:
        return run[0]
    if all_written:
        _quiet[policy.job] = newest.key
    return None


def forget() -> None:
    """Tests: drop the in-process memo."""
    _quiet.clear()
