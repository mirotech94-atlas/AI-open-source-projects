"""Small time helpers shared by the payload builders (overview, assets,
presence, search): `_now`/`_iso` for the payload's own timestamps, and
`_lax_cutoff` for a string bound safe to compare against a sensor-authored
ISO `ts` column in SQL.
"""

import datetime as dt


def _now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def _iso(when: dt.datetime) -> str:
    return when.astimezone(dt.timezone.utc).isoformat(timespec="seconds")


def incidents(ups: list) -> int:
    """Number of separate outages in an ordered up/down series.

    Every transition into "not ok" starts one incident, so a five-sample
    outage counts once. Port of Nexus `build_report`'s inner `incidents(n)`.
    Shared by the Assets `sites` payload (`assets.sites`) and the report
    engine's availability/summary sections (`reports.builder`) so the two
    surfaces never drift apart on the same definition.
    """
    count, prev = 0, True
    for ok in ups:
        if not ok and prev:
            count += 1
        prev = ok
    return count


def _live_macs(raw: dict) -> set[str]:
    """MACs the sensor's most recent scan actually saw -- shared by
    assets/topology/security, all of which need "is this device online
    right now" from the same reading.

    Two sources, because there are two ways a device can be seen. The ARP
    sweep's `raw["lan"]["devices"]` map is one. The other (Task 73) is a
    camera behind an NVR/DVR: it is on the recorder's own PoE network, never
    answers an ARP sweep, and the recorder is the only thing that can say
    whether it is up. Without this, every camera behind a recorder would read
    as permanently offline on Assets, Topology and Security while the recorder
    was reporting it live.
    """
    live = {mac for mac in ((raw.get("lan") or {}).get("devices") or {}).values() if mac}
    entries = raw.get("networks") or []
    entries = entries.values() if isinstance(entries, dict) else entries
    for entry in entries:
        if isinstance(entry, dict) and entry.get("status") not in ("unreachable", "no-sensor"):
            lan = entry.get("lan") if isinstance(entry.get("lan"), dict) else entry
            live |= {mac for mac in (lan.get("devices") or {}).values() if mac}
    for rec in raw.get("recorders") or []:
        if not isinstance(rec, dict) or not rec.get("ok"):
            continue
        live |= {mac for mac, d in (rec.get("devices") or {}).items()
                 if isinstance(d, dict) and d.get("presence") == "ONLINE"}
    return live


def _lax_cutoff(cutoff: dt.datetime) -> str:
    """A string cutoff safe for `<col>.ts >= ...` in SQL.

    A sensor-authored `ts` column is the sensor's own ISO string, so its UTC
    offset varies and lexicographic order is only chronological up to that
    offset. A one-day slack (offsets are at most +/-14 h) makes the SQL
    filter a guaranteed superset; callers re-check precisely with
    `scans.parse_ts` when exact ordering matters.
    """
    return (cutoff - dt.timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%S")


_ISO_SECONDS = "%Y-%m-%dT%H:%M:%S"


def exact_window(db, ts_col, tenant_col, tenant_id: str,
                 lo: dt.datetime, hi: dt.datetime | None = None):
    """`(where, odd)`: SQL criteria that select EXACTLY the rows whose parsed
    `ts` lies in `[lo, hi)`, plus the criterion for the few rows SQL cannot
    judge, which the caller re-checks with `scans.parse_ts`.

    **The problem this solves.** A sensor-authored `ts` is an ISO string in
    the sensor's own UTC offset, so SQL can only compare it as text. The house
    pattern has been `_lax_cutoff` (widen by a day) in SQL and `parse_ts` on
    every row in Python. That is correct and, on a real network, slow: the DNS
    page parsed ~290,000 rows four times per load, and the traffic page's "is
    this peer new?" check parsed the entire eight-day flow table to answer a
    one-hour question. With the production hub on one vCPU, each of those
    stalled every other request in every other tab.

    **Why the SQL here is exact, not approximate.** Two timestamps written in
    the SAME offset compare chronologically as text, as long as both are the
    plain `YYYY-MM-DDTHH:MM:SS<offset>` shape. So this first asks which
    `(offset, length)` shapes occur in the window (one small query, answered
    from the `(tenant_id, ts)` index), renders `lo`/`hi` in each such offset,
    and ORs one text comparison per offset. A row matches its own offset's
    branch and nothing else, so each is judged against the cutoff written in
    its own offset -- the comparison `parse_ts` would have made.

    Any row of another shape -- fractional seconds, an offset `parse_ts`
    cannot read, a truncated value -- falls into `odd` and is left to Python,
    so an odd timestamp gets the answer it always got, not a guess.
    """
    from sqlalchemy import and_, false, func, not_, or_

    from ..scans import parse_ts

    lax_lo = _lax_cutoff(lo)
    lax_hi = (hi + dt.timedelta(days=1)).strftime(_ISO_SECONDS) if hi is not None else None
    scope = [tenant_col == tenant_id, ts_col >= lax_lo]
    if lax_hi is not None:
        scope.append(ts_col < lax_hi)

    offset, width = func.substr(ts_col, 20), func.length(ts_col)
    branches, normal = [], []
    for off, n_chars in db.query(offset, width).filter(*scope).distinct().all():
        off = off or ""
        probe = parse_ts(f"2000-01-01T00:00:00{off}") if n_chars == 19 + len(off) else None
        if probe is None:
            continue
        # How far this offset's wall clock sits from UTC, recovered from the
        # probe rather than by re-parsing the offset text a second way.
        shift = dt.datetime(2000, 1, 1) - probe.replace(tzinfo=None)
        shape = and_(offset == off, width == n_chars)
        normal.append(shape)
        # `ts >= lo_local`: an equal 19-character prefix is the same instant,
        # and the longer string (it carries the offset) sorts after, so the
        # bound is inclusive. `ts < hi_local` is exclusive for the same reason.
        cond = [shape, ts_col >= _in_offset(lo, shift)]
        if hi is not None:
            cond.append(ts_col < _in_offset(hi, shift))
        branches.append(and_(*cond))

    where = and_(*scope, or_(*branches)) if branches else false()
    odd = and_(*scope, not_(or_(*normal))) if normal else and_(*scope)
    return where, odd


def _in_offset(t: dt.datetime, shift: dt.timedelta) -> str:
    """`t` as the wall-clock text a sensor `shift` from UTC would write."""
    return (t.astimezone(dt.timezone.utc).replace(tzinfo=None) + shift).strftime(_ISO_SECONDS)


def device_online(device) -> bool:
    """Network-scoped persisted observation; never borrow another MAC's network."""
    return effective_presence(device) == "ONLINE"


def effective_presence(device) -> str:
    """Keep the last observation, but never present an old online state as current."""
    from ..scans import parse_ts
    state = device.presence or "NEW"
    if state == "ONLINE":
        observed = parse_ts(device.last_seen)
        if observed is None or not -60 <= (_now()-observed).total_seconds() <= 900:
            return "UNKNOWN"
    return state
