"""History tab payload: the trend charts, the scan log and the event log.

Everything here is derived from rows that already exist — `Metric`,
`PresenceSample`, `Scan`, `Event` and `Alert`. History adds no table of its
own: it is a *view* of the record every other tab already writes.

Like every other payload builder this one is rendering-ready:

- each chart's series are hourly-meaned and then decimated to at most
  `MAX_POINTS` points, so the web app scales and draws but never buckets;
- the scan log's counts (sites up / hosts reachable / devices / alerts) are
  counted here, not in the browser;
- each event row carries the in-app `href` it opens.

`?network=` narrows the two sources that carry a network (`Event`, and the
presence samples behind "devices online"). `Metric` has no network column —
a site's latency or a host's disk is not a per-network fact — so those series
stay tenant-wide whatever the filter says, which the payload does not pretend
otherwise about.

Timestamps are normalized to UTC ISO-8601, so plain string order is
chronological order — sensor-authored `ts` strings carry per-sensor offsets,
where it is not.
"""

import datetime as dt

from sqlalchemy.orm import Session as DbSession

from .. import models as m, networks
from ..scans import parse_ts
from ..schemas_dash import (
    HistoryChart, HistoryEventRow, HistoryPayload, HistoryScanRow, Series, TrendPoint,
)
from ._util import _iso, _lax_cutoff, _now

MAX_POINTS = 240
SCAN_LOG_LIMIT = 30
EVENT_LOG_LIMIT = 60
MAX_DAYS = 365

# Sub-day windows (`hours=`, Task 77/F9). The reference console's range picker
# is in hours -- 6h / 24h / 3d / 7d / 30d -- and 6 hours of *hourly* means is
# six points, which is a bar chart pretending to be a trend. So a window at or
# under `FINE_BUCKET_MAX_HOURS` buckets at `FINE_BUCKET_MIN` minutes instead.
# `days=` callers never reach that branch: their bucket stays exactly 60
# minutes, so every existing series is byte-identical to before.
MAX_HOURS = MAX_DAYS * 24
HOUR_BUCKET_MIN = 60
FINE_BUCKET_MIN = 5
FINE_BUCKET_MAX_HOURS = 12

# One entry per metric-backed chart: (chart key, label, unit, y-axis ceiling,
# metric name, value scale). `scale` converts the stored unit to the displayed
# one — latency is stored in ms and shown in ms, so every scale here is 1.0
# today, but the column keeps the conversion explicit rather than implied.
_METRIC_CHARTS: list[tuple[str, str, str, float | None, str, float]] = [
    ("latency", "Latency", "ms", None, "latency_ms", 1.0),
    ("disk", "Disk", "%", 100.0, "disk_pct", 1.0),
    ("mem", "Memory", "%", 100.0, "mem_pct", 1.0),
    ("load", "Load per core", "", None, "load", 1.0),
    ("failed_ssh", "Failed SSH logins", "", None, "failed_ssh", 1.0),
    ("dns", "DNS latency", "ms", None, "dns_ms", 1.0),
    ("score", "Security score", "", 100.0, "score", 1.0),
]


def _hour_floor(when: dt.datetime, bucket_min: int = HOUR_BUCKET_MIN) -> dt.datetime:
    """Floor `when` (in UTC) to the start of its `bucket_min`-minute bucket.

    `bucket_min=60` (every `days=` caller) is the original hour floor.
    """
    when = when.astimezone(dt.timezone.utc).replace(second=0, microsecond=0)
    if bucket_min >= HOUR_BUCKET_MIN:
        return when.replace(minute=0)
    return when.replace(minute=(when.minute // bucket_min) * bucket_min)


def bucket_for(hours: int) -> int:
    """Bucket width in minutes for a sub-day window of `hours`."""
    return FINE_BUCKET_MIN if 0 < hours <= FINE_BUCKET_MAX_HOURS else HOUR_BUCKET_MIN


def _decimate(means: list[tuple[dt.datetime, float]], digits: int = 1) -> list[TrendPoint]:
    """Hourly means -> at most `MAX_POINTS` points, by meaning consecutive
    hours. 365 days of hours is 8760, so the step can be well above 1."""
    if not means:
        return []
    step = -(-len(means) // MAX_POINTS)     # ceil
    points: list[TrendPoint] = []
    for i in range(0, len(means), step):
        chunk = means[i:i + step]
        points.append(TrendPoint(ts=_iso(chunk[0][0]),
                                 value=round(sum(v for _, v in chunk) / len(chunk), digits)))
    return points


def current_entities(db: DbSession, tenant_id: str) -> set[str]:
    """The names a per-entity series may carry: every site and host that
    exists now. A retired site's metric rows stay for the retention window,
    but a legend offering it as a comparison would claim Vault still
    watches it."""
    names = {s.name for s in db.query(m.Site.name).filter_by(tenant_id=tenant_id)}
    names |= {h.name for h in db.query(m.Host.name).filter_by(tenant_id=tenant_id)}
    return {n for n in names if n}


def _metric_series(db: DbSession, tenant_id: str, name: str, cutoff: dt.datetime,
                   scale: float = 1.0, bucket_min: int = HOUR_BUCKET_MIN,
                   keep: set[str] | None = None) -> list[Series]:
    """One `Series` per entity for a metric name, oldest point first.

    A global metric (`entity == ""`) yields a single series named after the
    metric; a per-entity metric yields one per site/host, alphabetically, so
    the legend order is stable between reloads. `keep` (default: the
    tenant's current sites and hosts, see `current_entities`) drops the
    series of entities that no longer exist.
    """
    if keep is None:
        keep = current_entities(db, tenant_id)
    rows = (
        db.query(m.Metric.ts, m.Metric.entity, m.Metric.value)
        .filter(m.Metric.tenant_id == tenant_id, m.Metric.name == name,
                m.Metric.ts >= _lax_cutoff(cutoff))
        .all()
    )
    buckets: dict[str, dict[dt.datetime, list[float]]] = {}
    for ts, entity, value in rows:
        when = parse_ts(ts)
        if when is None or value is None or when < cutoff:
            continue
        if entity and entity not in keep:
            continue
        buckets.setdefault(entity, {}).setdefault(_hour_floor(when, bucket_min), []).append(float(value) * scale)

    out: list[Series] = []
    for entity in sorted(buckets):
        ordered = sorted(buckets[entity].items())
        means = [(when, sum(vals) / len(vals)) for when, vals in ordered]
        out.append(Series(name=entity or name, points=_decimate(means)))
    return out


def _devices_series(db: DbSession, tenant_id: str, cutoff: dt.datetime,
                    net: "m.Network | None", bucket_min: int = HOUR_BUCKET_MIN) -> list[Series]:
    """"Devices online", hourly: distinct MACs seen in each hour.

    Built from `PresenceSample` rather than the `live_devices` metric because
    presence samples carry `network_id` — which is what makes this the one
    chart `?network=` can actually narrow.
    """
    q = (
        db.query(m.PresenceSample.ts, m.PresenceSample.mac)
        .filter(m.PresenceSample.tenant_id == tenant_id, m.PresenceSample.ts >= _lax_cutoff(cutoff))
    )
    if net is not None:
        q = q.filter(m.PresenceSample.network_id == net.id)

    hourly: dict[dt.datetime, set[str]] = {}
    for ts, mac in q.all():
        when = parse_ts(ts)
        if when is None or when < cutoff:
            continue
        hourly.setdefault(_hour_floor(when, bucket_min), set()).add(mac)
    if not hourly:
        return []
    means = [(when, float(len(macs))) for when, macs in sorted(hourly.items())]
    return [Series(name="devices online", points=_decimate(means, digits=0))]


#: The severities a trend counts, in the order a reader cares about them. INFO
#: rows are recorded but are not events anyone charts, and folding them in
#: would bury the two that mean something.
TREND_SEVS = ("CRIT", "WARN")


def alert_first_sightings(
    db: DbSession, cutoff: dt.datetime, tenant_id: str | None = None,
    net: "m.Network | None" = None,
) -> list[tuple[str, dt.datetime, str]]:
    """`(tenant_id, when, sev)` for every alert FIRST SEEN since `cutoff`.

    ONE place, because two screens count these rows at different granularities
    and must never disagree about which rows they are: the customer's History
    tab buckets them per hour and splits CRIT from WARN, and the owner's Ledger
    draws one sparkline per customer bucketed per day. Two queries with
    hand-copied predicates would drift on exactly the details that are easy to
    get wrong here, and all three are subtle:

      * `first_seen`, not `last_seen`. The alert table keeps one row per key and
        updates it in place, so there is no per-hour snapshot of the open set
        to recover -- first-sightings are the honest series these rows support.
      * the severity filter, which is `TREND_SEVS` above and not "everything".
      * the two-stage cutoff. `first_seen` is a STRING carrying whatever UTC
        offset the sensor wrote, so a SQL `>=` on it is lexicographic and a row
        stamped `-04:00` sorts wrongly against one stamped `+00:00`.
        `_lax_cutoff` therefore widens the SQL bound deliberately and the real
        comparison is redone in Python below, on parsed datetimes. A caller
        that skipped the second stage would silently include rows from before
        the window.
    """
    q = db.query(m.Alert.tenant_id, m.Alert.first_seen, m.Alert.sev).filter(
        m.Alert.first_seen >= _lax_cutoff(cutoff)
    )
    if tenant_id is not None:
        q = q.filter(m.Alert.tenant_id == tenant_id)
    if net is not None:
        q = q.filter(m.Alert.network_id == net.id)

    out: list[tuple[str, dt.datetime, str]] = []
    for tid, first_seen, sev in q.all():
        when = parse_ts(first_seen)
        if when is None or when < cutoff or sev not in TREND_SEVS:
            continue
        out.append((tid, when, sev))
    return out


def _alerts_series(db: DbSession, tenant_id: str, cutoff: dt.datetime,
                   net: "m.Network | None", bucket_min: int = HOUR_BUCKET_MIN) -> list[Series]:
    """New alerts per hour, split CRIT / WARN, keyed off `Alert.first_seen`.

    Which rows count is `alert_first_sightings`' business; this only buckets
    them.
    """
    buckets: dict[str, dict[dt.datetime, list[float]]] = {}
    for _tid, when, sev in alert_first_sightings(db, cutoff, tenant_id, net):
        hours = buckets.setdefault(sev, {})
        hour = _hour_floor(when, bucket_min)
        hours[hour] = hours.get(hour, [0.0])
        hours[hour][0] += 1

    out: list[Series] = []
    for sev, label in zip(TREND_SEVS, ("critical", "warning")):
        hours = buckets.get(sev)
        if not hours:
            continue
        means = [(when, vals[0]) for when, vals in sorted(hours.items())]
        out.append(Series(name=label, points=_decimate(means, digits=0)))
    return out


def _speed_series(db: DbSession, tenant_id: str, cutoff: dt.datetime,
                  bucket_min: int = HOUR_BUCKET_MIN) -> list[Series]:
    out: list[Series] = []
    for name, label in (("speed_down", "download"), ("speed_up", "upload")):
        for series in _metric_series(db, tenant_id, name, cutoff, bucket_min=bucket_min):
            out.append(Series(name=label, points=series.points))
    return out


def _scan_rows(db: DbSession, tenant_id: str, cutoff: dt.datetime) -> tuple[list[HistoryScanRow], int]:
    """The scan log, newest first and capped at `SCAN_LOG_LIMIT` rows, plus the
    total number of scans in range (which the header reports and the cap
    hides)."""
    rows = (
        db.query(m.Scan)
        .filter(m.Scan.tenant_id == tenant_id, m.Scan.ts >= _lax_cutoff(cutoff))
        .order_by(m.Scan.ts.desc())
        .limit(SCAN_LOG_LIMIT * 4)      # slack for the lax SQL cutoff
        .all()
    )
    in_range = [s for s in rows if (parse_ts(s.ts) or cutoff) >= cutoff]
    out: list[HistoryScanRow] = []
    for scan in in_range[:SCAN_LOG_LIMIT]:
        raw = scan.raw or {}
        sites = [s for s in (raw.get("sites") or []) if isinstance(s, dict)]
        hosts = [h for h in (raw.get("ssh") or []) if isinstance(h, dict)]
        alerts = [a for a in (raw.get("alerts") or []) if isinstance(a, dict)]
        when = parse_ts(scan.ts)
        out.append(HistoryScanRow(
            ts=_iso(when) if when else scan.ts,
            sites_ok=sum(1 for s in sites if s.get("ok")),
            sites_total=len(sites),
            hosts_ok=sum(1 for h in hosts if h.get("reachable")),
            hosts_total=len(hosts),
            devices=(raw.get("lan") or {}).get("live_count"),
            crit=sum(1 for a in alerts if a.get("sev") == "CRIT"),
            warn=sum(1 for a in alerts if a.get("sev") == "WARN"),
            score=scan.score,
        ))
    return out, len(in_range)


# What each event type is called in the log, and where its row goes. An
# unknown type still renders — under its own raw name, pointing at History
# itself — rather than being dropped on the floor.
_EVENT_LABEL = {
    "join": "device joined", "leave": "device left",
    "port_open": "port opened", "port_closed": "port closed",
    "wan": "WAN address changed", "rotated": "MAC rotated",
    "fix": "fix applied", "scan": "scan completed",
}


def _event_href(kind: str, entity: str) -> str:
    if kind in ("join", "leave", "rotated") and entity:
        return f"/assets/devices/{entity}"
    if kind in ("port_open", "port_closed") and entity:
        return f"/assets/hosts/{entity}"
    if kind == "fix":
        return "/inbox?filter=recs"
    return "/history"


def _event_detail(payload: dict) -> str:
    """A one-line human summary of the event payload, preferring whatever
    field the sensor filled in rather than dumping JSON at the reader."""
    for key in ("msg", "detail", "summary", "name", "ip", "port", "action"):
        value = payload.get(key)
        if value not in (None, "", [], {}):
            return str(value)
    return ""


def _event_rows(db: DbSession, tenant_id: str, cutoff: dt.datetime,
                net: "m.Network | None") -> list[HistoryEventRow]:
    q = (
        db.query(m.Event)
        .filter(m.Event.tenant_id == tenant_id, m.Event.ts >= _lax_cutoff(cutoff))
    )
    if net is not None:
        q = q.filter(m.Event.network_id == net.id)
    rows = q.order_by(m.Event.ts.desc()).limit(EVENT_LOG_LIMIT * 4).all()

    refs = networks.refs_by_id(db, tenant_id)
    out: list[HistoryEventRow] = []
    for e in rows:
        when = parse_ts(e.ts)
        if when is None or when < cutoff:
            continue
        out.append(HistoryEventRow(
            ts=_iso(when),
            kind=_EVENT_LABEL.get(e.type, e.type),
            entity=e.entity or "",
            detail=_event_detail(e.payload or {}),
            href=_event_href(e.type, e.entity or ""),
            network=refs.get(e.network_id) if e.network_id else None,
        ))
        if len(out) >= EVENT_LOG_LIMIT:
            break
    return out


def window(days: int = 7, hours: int = 0) -> tuple[int, int, dt.datetime, int]:
    """Normalise a (days, hours) request to `(days, hours, cutoff, bucket)`.

    Split out of `build` so that anything else asking for the same window --
    the owner console's per-customer trends, for one -- clamps it the same
    way. A second caller that clamped `days` differently would draw a chart
    labelled "30 days" over a different span than the customer's own.
    """
    hours = max(0, min(hours, MAX_HOURS))
    if hours:
        days = 0
        cutoff = _now() - dt.timedelta(hours=hours)
    else:
        days = max(1, min(days, MAX_DAYS))
        cutoff = _now() - dt.timedelta(days=days)
    return days, hours, cutoff, bucket_for(hours)


def build_charts(db: DbSession, tenant_id: str, cutoff: dt.datetime,
                 net: "m.Network | None" = None,
                 bucket: int = HOUR_BUCKET_MIN) -> list[HistoryChart]:
    """Every trend chart this tenant has data for, in stable panel order.

    This is public, and it is the ONLY place a trend series is computed.

    The owner console draws a customer's alerts, devices and score on the
    per-account page, which is a screen an owner opens while sitting WITH that
    customer. Two implementations of "new alerts over the last 30 days" would
    eventually disagree, and the screen where they disagreed would be the one
    where the customer is looking at their own numbers next to the person
    telling them what the numbers mean. So the console does not compute a
    series; it calls this and keeps the keys it wants.

    `dailyfacts` makes the same argument for availability and resolves it the
    same way -- by calling `assets._uptime` rather than reimplementing it.
    """
    charts: list[HistoryChart] = []
    for key, label, unit, ceiling, metric, scale in _METRIC_CHARTS:
        series = _metric_series(db, tenant_id, metric, cutoff, scale=scale, bucket_min=bucket)
        if series:
            charts.append(HistoryChart(key=key, label=label, unit=unit, max=ceiling, series=series))

    devices = _devices_series(db, tenant_id, cutoff, net, bucket_min=bucket)
    if devices:
        charts.append(HistoryChart(key="devices", label="Devices online", series=devices))
    alerts = _alerts_series(db, tenant_id, cutoff, net, bucket_min=bucket)
    if alerts:
        charts.append(HistoryChart(key="alerts", label="New alerts", series=alerts))
    speed = _speed_series(db, tenant_id, cutoff, bucket_min=bucket)
    if speed:
        charts.append(HistoryChart(key="speed", label="Internet speed", unit="Mbps", series=speed))

    # Stable, readable panel order regardless of which metrics a tenant has.
    order = ["latency", "devices", "alerts", "disk", "mem", "load", "failed_ssh",
             "speed", "dns", "score"]
    charts.sort(key=lambda c: order.index(c.key) if c.key in order else len(order))
    return charts


def build(db: DbSession, tenant: "m.Tenant", days: int = 7,
          net: "m.Network | None" = None, hours: int = 0) -> HistoryPayload:
    """`GET /api/v1/history?days=<n>|hours=<n>&network=<id|slug>`.

    `hours` (when > 0) wins over `days` and is the only way to ask for a
    sub-day window; the payload then reports `days=0, hours=<n>` so a caller
    can tell "6 hours" from "1 day" without guessing. A `days=` call keeps its
    exact previous behaviour: `hours=0` on the payload and a 60-minute bucket.
    """
    days, hours, cutoff, bucket = window(days, hours)
    charts = build_charts(db, tenant.id, cutoff, net, bucket)

    scans, scan_count = _scan_rows(db, tenant.id, cutoff)
    return HistoryPayload(
        days=days, hours=hours,
        network=networks.ref(net),
        scan_count=scan_count,
        first_ts=scans[-1].ts if scans else None,
        last_ts=scans[0].ts if scans else None,
        charts=charts,
        scans=scans,
        events=_event_rows(db, tenant.id, cutoff, net),
    )
