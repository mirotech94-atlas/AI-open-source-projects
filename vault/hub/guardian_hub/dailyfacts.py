"""Freeze each day's availability once, so last month's report still says it.

Every availability figure Vault draws is recomputed from `metric` rows on
the request that draws it. `retention` deletes those rows after 90 days. So
the uptime in a report from March is not a record of March -- it is a
recomputation whose inputs are disappearing underneath it, and which
eventually resolves to "no checks" and then to nothing at all.

That makes the one question an MSP's client always asks unanswerable: "is
this the same number you showed me last quarter?" The trust plan calls
writing append-only daily facts non-negotiable for exactly that reader
(§2.5M), and it is the narrower half of the problem than the plan assumed --
scores are already immutable, because `Scan` rows are never pruned. It is
availability that moves.

TWO ORDERING RULES, BOTH LOAD-BEARING

A day is closed only once it is OVER in the tenant's own local time. Freezing
a partial day would preserve a figure that was never true for the whole day,
which is worse than not freezing it: it looks authoritative and is wrong.

And this job has to run BEFORE `retention_job`, which prunes the very metrics
it reads. They are scheduled half an hour apart for that reason; if the order
were ever reversed the facts would be written from rows that had just been
deleted, and the failure would be silent -- a table full of honest-looking
zeroes.

The arithmetic is deliberately not reimplemented. It calls the same
`payloads.assets._uptime` and the same `maintenance` windows the live page
uses, so the number frozen here is the number the owner actually saw. A
second implementation would drift, and a frozen figure that never matched
the page is worse than no frozen figure.
"""
import datetime as dt

from sqlalchemy.orm import Session as DbSession

from . import maintenance, models as m
from .payloads.assets import MIN_UPTIME_SAMPLES, _uptime
from .scans import parse_ts

#: How far back to look for days that were never closed. Covers a hub that
#: was down over a long weekend without walking the whole of history every
#: night on a tenant that has been quiet for a year.
BACKFILL_DAYS = 14


def _now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def close_day(db: DbSession, tenant, day: str, offset: dt.timedelta) -> int:
    """Write (or rewrite) every monitor's fact for one tenant-local day.

    Idempotent by the `(tenant, day, monitor)` unique key: re-running after
    a failed night updates the row in place rather than adding a second one,
    so the job is safe to retry -- the same property `ingest`'s dedup keys
    give the write path.

    Rewriting an existing row is deliberate and is NOT a contradiction of
    "written once". A day can only be closed from the metrics that exist
    when it is closed, and re-running the night it was missed produces a
    better answer from the same source. What must never happen is a figure
    changing because its INPUTS aged out, which is the thing this table
    exists to stop, and re-running cannot cause that -- pruning only ever
    removes rows, so a later re-run over pruned data would lower the count,
    which is why `close_due` never revisits a day it has already closed.
    """
    start = dt.datetime.fromisoformat(day + "T00:00:00+00:00") - offset
    end = start + dt.timedelta(days=1)
    wins = maintenance.windows(tenant)

    rows = (
        db.query(m.Metric.entity, m.Metric.ts, m.Metric.value)
        .filter(m.Metric.tenant_id == tenant.id, m.Metric.name == "up",
                m.Metric.ts >= (start - dt.timedelta(hours=26)).isoformat(),
                m.Metric.ts <= (end + dt.timedelta(hours=26)).isoformat())
        .all()
    )
    per: dict[str, dict] = {}
    for entity, ts, value in rows:
        when = parse_ts(ts)
        if when is None or not (start <= when < end):
            continue
        slot = per.setdefault(entity, {"vals": [], "planned": 0})
        if maintenance.covers(wins, when):
            slot["planned"] += 1
        else:
            slot["vals"].append(bool(value))

    written = 0
    for monitor, slot in per.items():
        vals = slot["vals"]
        existing = (db.query(m.DailyFact)
                    .filter_by(tenant_id=tenant.id, day=day, monitor=monitor).one_or_none())
        fields = {"checks": len(vals), "up": sum(vals), "planned": slot["planned"],
                  "uptime_pct": _uptime(vals)}
        if existing is None:
            db.add(m.DailyFact(tenant_id=tenant.id, day=day, monitor=monitor, **fields))
        else:
            for k, v in fields.items():
                setattr(existing, k, v)
        written += 1
    db.commit()
    return written


def close_due(db: DbSession, tenant, offset: dt.timedelta, now: dt.datetime | None = None) -> list[str]:
    """Close every complete day in the recent past that has no facts yet.

    Today is never closed -- it is not over. Days that already have a row
    are skipped rather than recomputed: their metrics may since have been
    pruned, and re-deriving them from a thinner set of rows would produce a
    lower, wronger number and destroy the exact guarantee the table offers.
    """
    today = ((now or _now()) + offset).date()
    closed = []
    for back in range(1, BACKFILL_DAYS + 1):
        day = (today - dt.timedelta(days=back)).isoformat()
        if db.query(m.DailyFact.id).filter_by(tenant_id=tenant.id, day=day).first():
            continue
        if close_day(db, tenant, day, offset):
            closed.append(day)
    return closed


def uptime_between(db: DbSession, tenant_id: str, monitor: str,
                   first_day: str, last_day: str) -> dict:
    """Availability across a named span of closed days, from the frozen rows.

    The windowed sibling of `uptime_for`, for reports, which ask about a
    period rather than "the last N days". Same rule about days with too few
    checks: excluded from the aggregate rather than counted as perfect.

    `days` being 0 is the caller's signal that this window is not covered by
    frozen facts at all -- a report about the last 24 hours, say, before the
    day has closed -- and that it should fall back to recomputing. It is a
    genuine "I have nothing", not a zero.
    """
    rows = (db.query(m.DailyFact)
            .filter(m.DailyFact.tenant_id == tenant_id, m.DailyFact.monitor == monitor,
                    m.DailyFact.day >= first_day, m.DailyFact.day <= last_day)
            .all())
    counted = [r for r in rows if r.uptime_pct is not None]
    checks = sum(r.checks for r in counted)
    up = sum(r.up for r in counted)
    return {
        "days": len(counted),
        "checks": checks,
        "planned": sum(r.planned for r in rows),
        "uptime_pct": _uptime([True] * up + [False] * (checks - up)) if checks else None,
    }


def uptime_for(db: DbSession, tenant_id: str, monitor: str, days: int) -> dict:
    """Availability over the last `days` CLOSED days, from the frozen rows.

    Reads facts and never metrics, which is the point: this answers the same
    way in a year as it does tonight. Days with too few checks to have been
    given a percentage are excluded from the aggregate rather than counted
    as perfect -- the same rule `_uptime` applies within a day.
    """
    rows = (db.query(m.DailyFact)
            .filter_by(tenant_id=tenant_id, monitor=monitor)
            .order_by(m.DailyFact.day.desc()).limit(days).all())
    counted = [r for r in rows if r.uptime_pct is not None]
    checks = sum(r.checks for r in counted)
    up = sum(r.up for r in counted)
    return {
        "days": len(counted),
        "checks": checks,
        "planned": sum(r.planned for r in rows),
        "uptime_pct": _uptime([True] * up + [False] * (checks - up)) if checks else None,
    }
