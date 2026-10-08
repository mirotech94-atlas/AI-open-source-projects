"""Shared Scan helpers.

`Scan.ts` is stored as an ISO-8601 string (whatever the sensor sent). Scans
from sensors in different UTC offsets (e.g. "-04:00" vs "+00:00") don't sort
correctly by plain string order -- a lexicographically larger string can be
a chronologically EARLIER instant. `latest_scan` fixes that cheaply: it pulls
the most-recent-by-string-order rows (a generous window) and picks the true
max by parsed timestamp, rather than trusting string order on its own.
"""

import datetime as dt

from sqlalchemy.orm import Session as DbSession, defer

from . import models as m

# Cheap-but-correct window: even a wildly-skewed offset (+/-14h) can't push
# a scan more than a handful of positions out of true chronological order
# among the most-recent-by-string rows, so 50 is generous headroom.
_CANDIDATE_WINDOW = 50


def parse_ts(s: str | None) -> dt.datetime | None:
    """Parse an ISO-8601 timestamp string into an aware UTC datetime.

    Handles a trailing "Z", an explicit offset ("-04:00", "+00:00"), or no
    offset at all (assumed already UTC). Returns None for anything that
    doesn't parse instead of raising, so callers can treat "unparseable"
    as its own condition.
    """
    if not s:
        return None
    s = s.replace("Z", "+00:00")
    # `-0400` -> `-04:00`. The sensor's DNS filter stamps queries with
    # `time.strftime("%Y-%m-%dT%H:%M:%S%z")`, and `%z` has no colon.
    # `fromisoformat` accepts that form only from Python 3.11: production runs
    # 3.13 and parses it, while the hub's own test venv runs 3.10 and returned
    # None for every such row. So the same `dns_query` table read as ~230k
    # queries on the server and as zero in any test or local check -- a
    # divergence nothing flagged, because "zero" is a perfectly plausible
    # answer for a DNS page. Normalising here makes the answer independent of
    # the interpreter.
    if len(s) >= 5 and s[-5] in "+-" and s[-4:].isdigit() and s[-3] != ":":
        s = f"{s[:-2]}:{s[-2:]}"
    try:
        parsed = dt.datetime.fromisoformat(s)
    except (ValueError, TypeError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt.timezone.utc)
    return parsed.astimezone(dt.timezone.utc)


_MIN_TS = dt.datetime.min.replace(tzinfo=dt.timezone.utc)


def latest_scan(db: DbSession, tenant_id: str) -> "m.Scan | None":
    """Return the chronologically most recent Scan for a tenant.

    Fetches the most-recent-by-string-order candidates and picks the max by
    parsed timestamp, so a scan stored with an earlier-looking UTC offset
    string doesn't shadow a chronologically later one (or vice versa). A
    scan whose `ts` fails to parse sorts as the oldest possible instant, so
    it never wins over a scan with a valid timestamp.

    **Why `raw` is deferred.** Choosing the winner needs only `ts`, but
    `Scan.raw` is the whole sensor payload -- ~20 KB of JSON on a real tenant
    -- and loading a row deserialises it. This used to load all
    `_CANDIDATE_WINDOW` candidates whole: about a megabyte of JSON parsed per
    call to read fifty strings. On the production hub (one 2013-era vCPU)
    that was 0.2-0.7 s a call, there are 38 call sites, and the Updates page
    reached it six times per request. Deferred, the candidates cost one
    cheap statement and the winner's `raw` loads only if a caller reads it --
    many (`confidence.assess`, for one) read only `ts`.

    One statement per call, deliberately, rather than a `(id, ts)` query and
    then a separate load of the winner: `assess` calls this per alert, and
    `tests/test_confidence_read_cost.py` counts statements per alert exactly.
    The two-query version moved that slope from 12 to 14.
    """
    return _latest(
        db.query(m.Scan).options(defer(m.Scan.raw))
        .filter_by(tenant_id=tenant_id)
        .order_by(m.Scan.ts.desc())
        .limit(_CANDIDATE_WINDOW)
        .all()
    )


def _latest(candidates) -> "m.Scan | None":
    return max(candidates, key=lambda row: _ts_key(row.ts)) if candidates else None


def _ts_key(ts: str | None) -> dt.datetime:
    """Sort key for a scan timestamp: parsed, with an unparseable one sorting
    as the oldest possible instant so it never wins a `max`."""
    parsed = parse_ts(ts)
    return parsed if parsed is not None else _MIN_TS


def latest_scan_for_sensor(db: DbSession, tenant_id: str, sensor_id: str) -> "m.Scan | None":
    """The chronologically most recent Scan pushed by ONE sensor.

    Same string-vs-chronological-order care as `latest_scan` (see its
    docstring), and the same deferred `raw`: the candidate window is by string
    order, the winner is picked by parsed timestamp. Used where a figure
    belongs to a specific sensor's own host or egress rather than to the
    tenant as a whole -- the Devices tiles' WAN address, for instance, which
    differs per sensor on a tenant whose networks each have their own.
    """
    return _latest(
        db.query(m.Scan).options(defer(m.Scan.raw))
        .filter_by(tenant_id=tenant_id, sensor_id=sensor_id)
        .order_by(m.Scan.ts.desc())
        .limit(_CANDIDATE_WINDOW)
        .all()
    )


def latest_scan_per_sensor(db: DbSession, tenant_id: str, sensor_ids) -> dict[str, "m.Scan"]:
    """The most recent Scan for EACH of `sensor_ids`, in ONE query.

    Calling `latest_scan_for_sensor` in a loop would pull `_CANDIDATE_WINDOW`
    candidates per sensor, which is real weight on a read path the Devices
    page already invalidates on every live `device` frame. This asks once,
    with the window scaled by the number of sensors, `raw` deferred as in
    `latest_scan`, and picks the winner per sensor in Python with the same
    parsed-timestamp key.
    """
    ids = [s for s in sensor_ids if s]
    if not ids:
        return {}
    rows = (
        db.query(m.Scan).options(defer(m.Scan.raw))
        .filter(m.Scan.tenant_id == tenant_id, m.Scan.sensor_id.in_(ids))
        .order_by(m.Scan.ts.desc())
        .limit(_CANDIDATE_WINDOW * len(ids))
        .all()
    )
    best: dict[str, "m.Scan"] = {}
    for row in rows:
        held = best.get(row.sensor_id)
        if held is None or _ts_key(row.ts) > _ts_key(held.ts):
            best[row.sensor_id] = row
    return best