"""Telling "checked, unchanged" apart from "not being checked at all".

The hub upserts whatever the sensor sends and never reconciles what it
stopped sending. Alerts have a lifecycle (they auto-resolve when they stop
firing) and so do recommendations (`ingest` prunes the ones the sensor no
longer makes). Sites and hosts do not: drop a site from the sensor's config
and its row keeps its last result forever, presenting an old check as a
current finding with nothing to say otherwise.

That is how `safehome.example.com` sat on the Security page showing a failing
CSP check against a header that had already been fixed -- the site was not
in the sensor's config at all, so nothing was re-checking it and the row
simply froze.

WHAT THIS DELIBERATELY DOES NOT DO
----------------------------------
It does not compare evidence between sweeps. A healthy site returns a byte
identical result every single sweep; unchanged evidence is the signal of
something working, not of something unchecked. Flagging "evidence has not
changed in N sweeps" would fire on every stable asset a tenant owns -- the
same shape of false positive as an evil-twin check that fires on the normal
case. The only honest signal is whether the sensor reported the row at all,
which is what `last_seen` records.

It also raises no alert and changes no severity. A frozen row is not a
finding about the network; it is the absence of one. It is surfaced as a
flag on the row it concerns, so what was already on screen stops claiming
to be current, and nothing new arrives in the inbox.
"""
import datetime as dt

from sqlalchemy.orm import Session as DbSession

from .. import models as m
from ..scans import parse_ts

#: How many consecutive sweeps a row must be missing from before it is called
#: stale. One missed sweep is a transient -- a sensor restart, a single failed
#: push, a host that timed out once. Three in a row is a pattern. At the
#: roughly six-minute cadence a sensor reports on, this is about twenty
#: minutes of silence about one asset while other assets keep reporting.
STALE_AFTER_SWEEPS = 3


def stale_cutoff(db: DbSession, tenant_id: str,
                 sweeps: int = STALE_AFTER_SWEEPS) -> dt.datetime | None:
    """The timestamp a row's `last_seen` must reach to count as current.

    Returns the `ts` of the `sweeps`-th most recent scan, or None when the
    tenant has not got that many scans yet -- with fewer sweeps on record
    than the window is wide there is no evidence that anything was skipped,
    only that the hub is new. Returning None means "judge nothing", which is
    the right answer for a tenant that just enrolled.
    """
    rows = (db.query(m.Scan.ts)
            .filter(m.Scan.tenant_id == tenant_id)
            .order_by(m.Scan.ts.desc())
            .limit(sweeps)
            .all())
    if len(rows) < sweeps:
        return None
    return parse_ts(rows[-1][0])


def is_stale(last_seen: str | None, cutoff: dt.datetime | None) -> bool:
    """Has this row been absent from every sweep inside the window?

    NULL `last_seen` is NOT stale. Rows written before migration 0016 have no
    honest value to claim, and calling them stale would flag a tenant's whole
    estate the moment this shipped. They resolve themselves on the next scan,
    which stamps the column.
    """
    if cutoff is None or not last_seen:
        return False
    seen = parse_ts(last_seen)
    return seen is not None and seen < cutoff


def stale_note(last_seen: str | None) -> str:
    """One plain line for the row, naming when it was actually last checked.

    Written to be read by someone who does not know what a sweep is: the
    point is that the numbers beside it are old, not that a counter tripped.
    """
    seen = parse_ts(last_seen)
    if seen is None:
        return "The sensor is no longer reporting this — the figures shown are its last known state."
    when = seen.astimezone(dt.timezone.utc).strftime("%-d %b %H:%M UTC")
    return (f"The sensor has stopped reporting this — last checked {when}. "
            "The figures shown are from then, not now.")
