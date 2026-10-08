"""Overview tab payload: the five hero cards, the score hero (ring, 7-day
sparkline, named deductions), the open-incidents panel, the Vault-health
panel, the 24 h health strip, the "what changed" timeline, the internet-line
path-health panel, the 30-day score trend and the five questions.

Everything the Overview draws is computed here. The web app formats (units,
relative time, colour class) but never buckets a series or joins two
collections, so:

- the health strip is already 24 blocks per site/host, hub-side bucketed;
- the score trend is already decimated to at most 240 points;
- the timeline is already merged, sorted and carries the in-app `href`;
- the five questions already carry their one-line answer and state;
- every hero-card figure is a hub-side count (`changed_24h`, `assets`,
  `incidents`, `recs`) -- the web app picks a tone and writes the sub-line
  from them, it never counts rows itself.

Timestamps that the UI sorts or compares (`timeline[].ts`) are normalized to
UTC ISO-8601 so plain string order is chronological order -- the raw sensor
strings carry per-sensor UTC offsets, where it is not.
"""

from urllib.parse import quote
import datetime as dt

from sqlalchemy.orm import Session as DbSession

from .. import models as m, networks
from .. import voice as voice_mod
from ..scans import latest_scan, parse_ts
from ..schemas_dash import (
    AlertCounts, AssetCounts, CertExpiry, CoverageRow, Deduction, DeviceCounts, FeedState,
    GuardianHealth,
    HealthStrip, HourBlock, IncidentBrief, IncidentCounts, NetworkHealth,
    FixDay, FixedWithoutYou, NeedsYouItem,
    NextAction, OverviewPayload, PathHealth, QuestionItem, RecCounts, ScoreCoverage, SensorStatus,
    Series, TimelineItem, TrendPoint,
)
from . import account as account_payload
from . import cameras as cameras_payload
from . import staleness
from . import scorechange
from ._util import device_online, _iso, _lax_cutoff, _now

# Chat action tiers (spec §7): 1 = read/safe, 2 = changes the network,
# 3 = admin-only. `next_action` carries the tier the chat would assign for
# the same action so the hero button and the chat agree about what it costs.
# NextAction has no `tier` field (and the contract may not grow one), so the
# tier rides in `body["tier"]`; every write endpoint takes a typed body and
# ignores the extra key.
TIER_ALERT_ACK = 1
TIER_DEVICE_LABEL = 2
TIER_FIX = 2

STRIP_HOURS = 24
#: A sensor quieter than this is "silent" -- the reference's own 15-minute
#: staleness line, which drives the "Are you watching?" card's tone.
SENSOR_LIVE_S = 900
#: How many open incidents the overview panel shows before deferring to the
#: Inbox.
INCIDENT_BRIEFS = 3
_REC_PRIORITY = ("Critical", "High", "Medium", "Low")
TREND_MAX_POINTS = 240
TIMELINE_LIMIT = 40

_TIMELINE_EVENT_TYPES = ("join", "leave", "port_open", "port_closed", "wan", "rotated")
_EVENT_KIND = {
    "join": "join", "leave": "leave", "port_open": "port", "port_closed": "port",
    "wan": "wan", "rotated": "wan",
}


def _hour_floor(when: dt.datetime) -> dt.datetime:
    return when.astimezone(dt.timezone.utc).replace(minute=0, second=0, microsecond=0)


# --------------------------------------------------------------------------
# Health strip
# --------------------------------------------------------------------------

def health_strip(db: DbSession, tenant_id: str, hours: int = STRIP_HOURS) -> list[HealthStrip]:
    """One row per site and per host, each `hours` blocks wide.

    Sites read the `up` metric: `"up"` when every sample in the hour passed,
    `"down"` when every sample failed, `"partial"` when mixed. Hosts have no
    up/down metric, so the *presence* of a `disk_pct` sample is the signal --
    the sensor only records it when the host answered. An hour with no sample
    at all is `"nodata"` for both.

    Block indices are hours-ago-normalized: 0 is the oldest hour drawn,
    `hours - 1` the current one.
    """
    sites = db.query(m.Site).filter_by(tenant_id=tenant_id).order_by(m.Site.name).all()
    hosts = db.query(m.Host).filter_by(tenant_id=tenant_id).order_by(m.Host.name).all()
    if not sites and not hosts:
        return []

    now_hour = _hour_floor(_now())
    oldest = now_hour - dt.timedelta(hours=hours - 1)

    # One query for every metric the strip needs, rather than one per entity.
    rows = (
        db.query(m.Metric.name, m.Metric.entity, m.Metric.ts, m.Metric.value)
        .filter(m.Metric.tenant_id == tenant_id,
                m.Metric.name.in_(("up", "disk_pct")),
                m.Metric.ts >= _lax_cutoff(oldest))
        .all()
    )

    # (name, entity) -> {block index -> [passed?, ...]}
    buckets: dict[tuple[str, str], dict[int, list[bool]]] = {}
    for name, entity, ts, value in rows:
        when = parse_ts(ts)
        if when is None:
            continue
        idx = (hours - 1) - int((now_hour - _hour_floor(when)).total_seconds() // 3600)
        if not 0 <= idx < hours:
            continue
        passed = bool(value) if name == "up" else True   # disk_pct: a sample means "answered"
        buckets.setdefault((name, entity), {}).setdefault(idx, []).append(passed)

    def _blocks(name: str, entity: str) -> list[HourBlock]:
        per_hour = buckets.get((name, entity), {})
        out = []
        for idx in range(hours):
            samples = per_hour.get(idx)
            if not samples:
                state = "nodata"
            elif all(samples):
                state = "up"
            elif not any(samples):
                state = "down"
            else:
                state = "partial"
            out.append(HourBlock(hour=idx, state=state))
        return out

    strip = [
        HealthStrip(kind="site", name=s.name, ok=(s.last or {}).get("ok"), blocks=_blocks("up", s.name))
        for s in sites
    ]
    strip += [
        HealthStrip(kind="host", name=h.name, ok=(h.last or {}).get("reachable"),
                    blocks=_blocks("disk_pct", h.name))
        for h in hosts
    ]
    return strip


# --------------------------------------------------------------------------
# Score trend
# --------------------------------------------------------------------------

def score_trend(db: DbSession, tenant_id: str, days: int = 30) -> Series:
    """The `score` metric over `days`, hourly-meaned and then decimated to at
    most `TREND_MAX_POINTS` points (30 days of hours is 720, so the second
    pass groups consecutive hours and means them)."""
    cutoff = _now() - dt.timedelta(days=days)
    rows = (
        db.query(m.Metric.ts, m.Metric.value)
        .filter(m.Metric.tenant_id == tenant_id, m.Metric.name == "score",
                m.Metric.entity == "", m.Metric.ts >= _lax_cutoff(cutoff))
        .all()
    )
    hourly: dict[dt.datetime, list[float]] = {}
    for ts, value in rows:
        when = parse_ts(ts)
        if when is None or value is None or when < cutoff:
            continue
        hourly.setdefault(_hour_floor(when), []).append(float(value))
    if not hourly:
        return Series(name="score")

    ordered = sorted(hourly.items())
    means = [(when, sum(vals) / len(vals)) for when, vals in ordered]

    step = -(-len(means) // TREND_MAX_POINTS)    # ceil
    points = []
    for i in range(0, len(means), step):
        chunk = means[i:i + step]
        points.append(TrendPoint(ts=_iso(chunk[0][0]),
                                 value=round(sum(v for _, v in chunk) / len(chunk), 1)))
    return Series(name="score", points=points)


# --------------------------------------------------------------------------
# Timeline
# --------------------------------------------------------------------------

def timeline(db: DbSession, tenant_id: str, limit: int = TIMELINE_LIMIT) -> list[TimelineItem]:
    """Events, fixes, alert first-sightings and scans merged into one
    reverse-chronological list, each row carrying the in-app route it opens.

    Each source is capped at `limit` before the merge, so this is four bounded
    queries regardless of how much history the tenant has.
    """
    items: list[tuple[dt.datetime, TimelineItem]] = []

    def add(ts: str | None, item: TimelineItem) -> None:
        when = parse_ts(ts)
        if when is None or when < _now() - dt.timedelta(hours=24) or when > _now():
            return
        item.ts = _iso(when)
        items.append((when, item))

    events = (
        db.query(m.Event)
        .filter(m.Event.tenant_id == tenant_id, m.Event.type.in_(_TIMELINE_EVENT_TYPES))
        .order_by(m.Event.ts.desc()).limit(limit).all()
    )
    for e in events:
        port = (e.payload or {}).get("port")
        label = (e.payload or {}).get("label") or (e.payload or {}).get("ip") or e.entity
        if e.type == "join":
            title, detail = f"{label} joined the network", e.entity
        elif e.type == "leave":
            title, detail = f"{label} left the network", e.entity
        elif e.type.startswith("port_"):
            verb = "opened" if e.type == "port_open" else "closed"
            title, detail = f"{label} {verb} port {port}", e.entity
        else:
            title, detail = f"Internet connection changed ({e.type})", str((e.payload or {}).get("ip") or "")
        add(e.ts, TimelineItem(kind=_EVENT_KIND.get(e.type, "wan"), title=title, detail=detail,
                               entity=e.entity,
                               href=f"/assets/devices/{e.entity}" if e.entity and ":" in e.entity else None))

    for f in (db.query(m.Fix).filter_by(tenant_id=tenant_id)
              .order_by(m.Fix.ts.desc()).limit(limit).all()):
        add(f.ts, TimelineItem(kind="fix", title=f"{f.label or f.action} on {f.host}",
                               detail="succeeded" if f.ok else "failed", entity=f.host,
                               sev=None if f.ok else "WARN", href="/updates"))

    for a in (db.query(m.Alert).filter_by(tenant_id=tenant_id)
              .order_by(m.Alert.first_seen.desc()).limit(limit).all()):
        add(a.first_seen, TimelineItem(kind="alert", title=a.msg, detail=a.why or "", sev=a.sev,
                                       entity=a.key, href=f"/inbox?row={a.key}"))

    for s in (db.query(m.Scan).filter_by(tenant_id=tenant_id)
              .order_by(m.Scan.ts.desc()).limit(limit).all()):
        add(s.ts, TimelineItem(kind="scan", title="Scan completed",
                               detail=f"score {s.score}" if s.score is not None else "", entity=""))

    items.sort(key=lambda pair: pair[0], reverse=True)
    return [item for _, item in items[:limit]]


# --------------------------------------------------------------------------
# The five questions
# --------------------------------------------------------------------------

def _plural(n: int, one: str, many: str | None = None) -> str:
    return one if n == 1 else (many or one + "s")


def questions(db: DbSession, tenant, raw: dict, devices: list[m.Device] | None = None) -> list[QuestionItem]:
    """The five questions from the Nexus overview strip, each answered in one
    line. States are `good` / `warn` / `bad`.

    `devices` lets `build()` pass its own already-fetched `Device` rows
    instead of this function querying them again; when omitted (e.g. a
    standalone caller) it fetches them itself."""
    raw = raw or {}
    if devices is None:
        devices = db.query(m.Device).filter_by(tenant_id=tenant.id).all()
    # Open alerts only, the same set the verdict uses: an acked or snoozed
    # alert has already had a decision, so it must not keep answering "yes".
    alerts = db.query(m.Alert).filter_by(tenant_id=tenant.id, status="open").all()
    crit = [a for a in alerts if a.sev == "CRIT"]
    warn = [a for a in alerts if a.sev == "WARN"]

    hosts = raw.get("ssh") or []
    sites = raw.get("sites") or []
    down_sites = [s for s in sites if s.get("ok") is False]
    unreachable = [h for h in hosts if h.get("reachable") is False and not h.get("optional")]

    # 1. Is anything broken right now?
    if crit or down_sites or unreachable:
        broken = []
        if crit:
            broken.append(f"{len(crit)} critical {_plural(len(crit), 'alert')}")
        if down_sites:
            broken.append(f"{len(down_sites)} {_plural(len(down_sites), 'site')} down")
        if unreachable:
            broken.append(f"{len(unreachable)} {_plural(len(unreachable), 'server')} unreachable")
        q1 = QuestionItem(question="Is anything broken right now?",
                          answer="Yes - " + ", ".join(broken) + ".", state="bad")
    elif warn:
        q1 = QuestionItem(question="Is anything broken right now?",
                          answer=f"Nothing is down, but {len(warn)} {_plural(len(warn), 'warning')} need a look.",
                          state="warn")
    else:
        q1 = QuestionItem(question="Is anything broken right now?",
                          answer="No - every site and server is up.", state="good")

    # 2. Is anyone attacking me? (the Nexus sc/sw split: sec:/home:/lan:unverified keys)
    # LAN keys are namespaced per network as of Task 51
    # (`lan:<slug>:unverified`), so the prefix test runs on the canonical form.
    def _sec(a):
        return networks.canonical_lan_key(a.key).startswith(("sec:", "home:", "lan:unverified"))
    sec_crit = [a for a in crit if _sec(a)]
    sec_warn = [a for a in warn if _sec(a)]
    failed = sum(int(h.get("failed_ssh_24h") or 0) for h in hosts)
    banned = sum(int(h.get("banned") or 0) for h in hosts)
    if sec_crit:
        q2 = QuestionItem(question="Is anyone attacking me?",
                          answer=f"Active attack: {sec_crit[0].msg}", state="bad")
    elif failed or sec_warn:
        q2 = QuestionItem(question="Is anyone attacking me?",
                          answer=f"Background noise only - {failed} failed {_plural(failed, 'login')} in 24 h, "
                                 f"{banned} {_plural(banned, 'source')} banned.", state="warn")
    else:
        q2 = QuestionItem(question="Is anyone attacking me?",
                          answer=f"No sign of it - 0 failed logins in 24 h, {banned} {_plural(banned, 'source')} still banned.",
                          state="good")

    # 3. Is everything patched?  (Nexus `upd`)
    reachable = [h for h in hosts if h.get("reachable")]
    sec_updates = sum(len((h.get("sec") or {}).get("security_updates") or []) for h in reachable)
    reboots = [h for h in reachable if h.get("reboot_required")]
    pending = sum(int(h.get("pending_updates") or 0) for h in reachable)
    if sec_updates:
        q3 = QuestionItem(question="Is everything patched?",
                          answer=f"No - {sec_updates} security {_plural(sec_updates, 'update')} pending.", state="bad")
    elif reboots or pending:
        bits = []
        if pending:
            bits.append(f"{pending} ordinary {_plural(pending, 'update')} pending")
        if reboots:
            bits.append(f"{len(reboots)} {_plural(len(reboots), 'server')} waiting on a reboot")
        q3 = QuestionItem(question="Is everything patched?",
                          answer="Almost - " + " and ".join(bits) + ".", state="warn")
    else:
        q3 = QuestionItem(question="Is everything patched?",
                          answer=f"Yes - no security updates pending on {len(reachable)} {_plural(len(reachable), 'server')}.",
                          state="good")

    # 4. Who is on my Wi-Fi?  (Nexus `nd` / unverified)
    total_devices = len(devices)
    live = sum(1 for d in devices if device_online(d))
    unverified = len(unverified_devices(devices, raw))
    new_devices = len(((raw.get("lan") or {}).get("new_devices") or []))
    if unverified or new_devices:
        n = unverified or new_devices
        q4 = QuestionItem(question="Who is on my Wi-Fi?",
                          answer=f"{live} of {total_devices} known {_plural(total_devices, 'device')} online, "
                                 f"{n} not verified yet.", state="warn")
    else:
        q4 = QuestionItem(question="Who is on my Wi-Fi?",
                          answer=f"{live} of {total_devices} known {_plural(total_devices, 'device')} online, all recognised.",
                          state="good")

    # 5. Is my internet healthy?
    speed = raw.get("speed") or {}
    down_mbps = speed.get("down") or speed.get("down_mbps")
    dns_ms = (raw.get("dns") or {}).get("system") or {}
    dns_val = dns_ms.get("ms") if isinstance(dns_ms, dict) else dns_ms
    loss = max((float((v or {}).get("loss") or 0) for v in (raw.get("wan_path") or {}).values()
                if isinstance(v, dict)), default=0.0)
    bits = []
    if down_mbps:
        bits.append(f"{round(float(down_mbps))} Mbps down")
    if dns_val:
        bits.append(f"DNS {round(float(dns_val))} ms")
    detail = ", ".join(bits) or "no speed sample yet"
    if loss >= 5 or down_sites:
        q5 = QuestionItem(question="Is my internet healthy?",
                          answer=f"Struggling - {round(loss)}% packet loss on the WAN path ({detail}).", state="bad")
    elif loss > 0 or not bits:
        q5 = QuestionItem(question="Is my internet healthy?",
                          answer=f"Mostly - {detail}, {round(loss, 1)}% packet loss.", state="warn")
    else:
        q5 = QuestionItem(question="Is my internet healthy?",
                          answer=f"Yes - {detail}, no packet loss.", state="good")

    return [q1, q2, q3, q4, q5]


# --------------------------------------------------------------------------
# Unverified devices, verdict, next action
# --------------------------------------------------------------------------

def unverified_devices(devices: list[m.Device], raw: dict) -> list[m.Device]:
    """The devices the scan considers unverified, filtered from `devices`
    (the tenant's full `Device` rows -- callers pass one already-fetched
    list rather than each having this function re-query them).

    `Scan.raw["home"]["unverified"]` is authoritative when the sensor sent
    the key (even when it is empty -- an empty list means "nothing
    unverified", not "unknown"). Older scans without it fall back to the
    phase-1 predicate.
    """
    home = (raw or {}).get("home") or {}
    if "unverified" in home:
        macs = set()
        for u in home.get("unverified") or []:
            macs.add(u.get("mac") if isinstance(u, dict) else u)
        return [d for d in devices if d.mac in macs]
    return [d for d in devices if not d.trusted and not d.label and d.presence != "ARCHIVED"]


def verdict_and_next_action(crit, warn, unverified) -> tuple[str, NextAction | None]:
    """The phase-1 rules, with `method` and the chat's tier on the action."""
    if crit:
        first = sorted(crit, key=lambda a: a.first_seen)[0]
        return (f"Something needs you now: {first.msg}",
                NextAction(label="Acknowledge", method="POST",
                           path=f"/api/v1/alerts/{quote(first.key, safe='')}/ack", body={"tier": TIER_ALERT_ACK}))
    if warn:
        return f"Mostly fine. {len(warn)} warnings to review.", None
    if unverified:
        d = unverified[0]
        return (f"{len(unverified)} unknown device(s) on your Wi-Fi.",
                NextAction(label="Label device", method="POST",
                           path=f"/api/v1/devices/{d.mac}", body={"label": "", "tier": TIER_DEVICE_LABEL}))
    return "Everything is fine. Vault is watching.", None


# --------------------------------------------------------------------------
# The five hero cards' counts, and the panels under them (Task 74)
# --------------------------------------------------------------------------

def changed_24h(db: DbSession, tenant_id: str) -> int:
    """Alerts opened, alerts resolved and fixes applied in the last 24 hours.

    The reference counts `first_seen >= cut OR resolved_at >= cut` plus fixes;
    `Alert` here has no `resolved_at`, so a resolved alert is counted by its
    `last_seen` -- which `ingest` stamps when it flips the row to resolved --
    and an alert both opened and resolved inside the window counts once.
    """
    cut = _lax_cutoff(_now() - dt.timedelta(hours=24))
    opened = {
        key for (key,) in db.query(m.Alert.key)
        .filter(m.Alert.tenant_id == tenant_id, m.Alert.first_seen >= cut).all()
    }
    resolved = {
        key for (key,) in db.query(m.Alert.key)
        .filter(m.Alert.tenant_id == tenant_id, m.Alert.status == "resolved",
                m.Alert.last_seen >= cut).all()
    }
    fixes = (db.query(m.Fix)
             .filter(m.Fix.tenant_id == tenant_id, m.Fix.ts >= cut).count())
    return len(opened | resolved) + fixes


def open_incidents(db: DbSession, tenant_id: str) -> list[m.Incident]:
    return (db.query(m.Incident)
            .filter(m.Incident.tenant_id == tenant_id, m.Incident.closed.is_(None))
            .order_by(m.Incident.opened.desc()).all())


def incident_counts(rows: list[m.Incident]) -> IncidentCounts:
    return IncidentCounts(open=len(rows),
                          crit=sum(1 for i in rows if i.sev == "CRIT"),
                          warn=sum(1 for i in rows if i.sev == "WARN"))


def incident_briefs(db: DbSession, tenant_id: str, rows: list[m.Incident],
                    limit: int = INCIDENT_BRIEFS) -> list[IncidentBrief]:
    """The panel rows. Title is the same headline `payloads/incidents.title()`
    builds for the Inbox and the detail page, so all three surfaces never
    name the same story differently. `href` points at the incident's own
    page (Task 83/A2) rather than the inbox filter it used to open."""
    from . import incidents as incidents_payload
    out = []
    for i in rows[:limit]:
        alerts = incidents_payload.children(db, tenant_id, i)
        out.append(IncidentBrief(
            id=i.iid, title=incidents_payload.title(i, alerts),
            sev=i.sev if i.sev in ("CRIT", "WARN", "INFO") else "WARN",
            entity=i.entity, opened=i.opened, alerts=len(i.alert_keys or []),
            href=f"/incidents/{i.iid}",
        ))
    return out


def rec_counts(db: DbSession, tenant_id: str) -> RecCounts:
    """Open recommendations. "Open" is `decision != "no"` -- deliberately the
    same predicate `inbox.counts` uses for `InboxCounts.recs`, so the hero
    card and the sidebar badge can never disagree about how many there are.

    `top` is the highest-priority one's own text (Critical, High, Medium, Low,
    and within a priority the most recently seen), so the hero sub-line names
    the actual next thing to do rather than a category.
    """
    rows = (db.query(m.Recommendation)
            .filter(m.Recommendation.tenant_id == tenant_id, m.Recommendation.decision != "no")
            .order_by(m.Recommendation.last_seen.desc()).all())
    # Stable sort on priority over the already-newest-first list, so ties inside
    # a priority keep that order.
    rows.sort(key=lambda r: (_REC_PRIORITY.index((r.priority or "").capitalize())
                             if (r.priority or "").capitalize() in _REC_PRIORITY
                             else len(_REC_PRIORITY)))
    def n(priority: str) -> int:
        return sum(1 for r in rows if (r.priority or "").capitalize() == priority)
    return RecCounts(open=len(rows), critical=n("Critical"), high=n("High"),
                     top=(rows[0].title if rows else ""))


def paths(raw: dict) -> list[PathHealth]:
    """`Scan.raw["wan_path"]`: five pings to each fixed public anchor per scan.

    `bad` when nothing came back or a fifth of the packets were lost, `warn`
    on any loss at all or a latency above 120 ms -- the reference's own bands.
    """
    out: list[PathHealth] = []
    for name, v in (raw.get("wan_path") or {}).items():
        if not isinstance(v, dict):
            continue
        avg = v.get("avg_ms")
        avg = float(avg) if isinstance(avg, (int, float)) else None
        loss = float(v.get("loss_pct") or 0.0)
        jitter = v.get("jitter_ms")
        if avg is None or loss >= 20:
            state = "bad"
        elif loss > 0 or avg > 120:
            state = "warn"
        else:
            state = "good"
        out.append(PathHealth(name=name, ip=str(v.get("ip") or ""), avg_ms=avg,
                              loss_pct=loss,
                              jitter_ms=float(jitter) if isinstance(jitter, (int, float)) else None,
                              state=state))
    out.sort(key=lambda p: p.name)
    return out


def guardian_health(db: DbSession, tenant, raw: dict, age_s: int | None,
                    newest_event: TimelineItem | None) -> GuardianHealth:
    """The health panel: what the sensor said about its own run, plus what the
    hub knows about sensors, fixes, notifications and the DNS filter."""
    raw = raw or {}
    h = raw.get("health") or {}
    if age_s is None:
        sensor_state = "unknown"
    else:
        sensor_state = "online" if age_s < SENSOR_LIVE_S else "silent"

    sensors = db.query(m.Sensor).filter_by(tenant_id=tenant.id).all()
    cut = _lax_cutoff(_now() - dt.timedelta(hours=24))
    dns = raw.get("dns_filter") or {}
    cfg = tenant.config or {}
    autofix = cfg.get("auto_fix") or {}

    run_seconds = h.get("run_seconds")
    scans = h.get("scans_last_hour")
    return GuardianHealth(
        sensor_state=sensor_state,
        run_seconds=int(run_seconds) if isinstance(run_seconds, (int, float)) else None,
        scans_last_hour=int(scans) if isinstance(scans, (int, float)) else None,
        sensors_active=sum(1 for s in sensors if s.status == "active" and s.last_seen and (_now() - s.last_seen).total_seconds() < SENSOR_LIVE_S),
        sensors_total=len(sensors),
        feeds=[FeedState(name=k, ok=bool(v)) for k, v in sorted((h.get("feeds") or {}).items())],
        last_event_ts=newest_event.ts if newest_event else "",
        last_event_age_s=(int((_now() - parse_ts(newest_event.ts)).total_seconds())
                          if newest_event and parse_ts(newest_event.ts) else None),
        autofix_enabled=bool(autofix.get("enabled")),
        autofix_24h=db.query(m.Fix).filter(m.Fix.tenant_id == tenant.id, m.Fix.ts >= cut).count(),
        notify_channel=", ".join(sorted(i.kind for i in db.query(m.Integration).filter_by(tenant_id=tenant.id, enabled=True).all())),
        dns_filter_state=("on" if dns.get("running") else ("down" if dns.get("enabled") else "off")),
        dns_blocked_24h=sum(1 for (stamp,) in db.query(m.DnsQuery.ts).filter(m.DnsQuery.tenant_id == tenant.id, m.DnsQuery.ts >= cut, m.DnsQuery.blocked.is_(True)).yield_per(1000) if (parsed := parse_ts(stamp)) is not None and _now() - dt.timedelta(hours=24) <= parsed <= _now()),
    )


def asset_counts(db: DbSession, tenant_id: str, raw: dict, devices: int) -> AssetCounts:
    servers = db.query(m.Host).filter_by(tenant_id=tenant_id).count()
    sites = db.query(m.Site).filter_by(tenant_id=tenant_id).count()
    total = devices + servers + sites
    return AssetCounts(total=total, servers=servers, sites=sites)


#: Certificates this close to expiry earn a card on the front page. Ninety
#: days is the reference's own window and it is the right one here: a Let's
#: Encrypt certificate lives ninety days, so anything inside the window is
#: either renewing normally or has stopped renewing, and both are worth a
#: glance.
CERT_WINDOW_DAYS = 90


def _cert_days(last: dict) -> int | None:
    """Days until a site's certificate expires, from its last check.

    The sensor writes `cert_days_left`. Two readers here looked for
    `cert_days` instead, so Coverage reported no certificate read for any
    site and the expiring list was empty while three had been read on every
    scan -- and the card said so to the customer. The old key is still
    honoured for any row written before the rename.
    """
    v = last.get("cert_days_left")
    if v is None:
        v = last.get("cert_days")
    return int(v) if isinstance(v, (int, float)) else None


def coverage_rows(db: DbSession, tenant_id: str, devices: list) -> list[CoverageRow]:
    """What the sensor can currently SEE, per class of asset.

    Not what exists -- what is still being reported. The hub upserts whatever
    the sensor sends and never reconciles what it stopped sending, so a site
    dropped from the sensor's config keeps its last result forever and shows a
    frozen check as a current finding; `payloads/staleness` exists because of
    exactly that. "12 certificates" can be perfectly true while nothing has
    looked at four of them for a month, and this card is the difference.

    Devices are counted by PRESENCE rather than by staleness. A laptop that is
    asleep is not a gap in Vault's sight -- it is a device Vault can see
    is asleep -- and counting it as unseen would report a fault every night.
    """
    cutoff = staleness.stale_cutoff(db, tenant_id)
    rows: list[CoverageRow] = []

    if devices:
        rows.append(CoverageRow(label="Devices",
                                seen=sum(1 for d in devices if device_online(d)),
                                total=len(devices)))

    for label, model in (("Servers", m.Host), ("Websites", m.Site)):
        all_rows = db.query(model).filter_by(tenant_id=tenant_id).all()
        if not all_rows:
            continue
        rows.append(CoverageRow(
            label=label, total=len(all_rows),
            seen=sum(1 for r in all_rows
                     if not staleness.is_stale(getattr(r, "last_seen", None), cutoff)),
        ))

    sites = db.query(m.Site).filter_by(tenant_id=tenant_id).all()
    if sites:
        rows.append(CoverageRow(
            label="Certificates", total=len(sites),
            seen=sum(1 for s in sites if _cert_days(s.last or {}) is not None)))

    nets = db.query(m.Network).filter_by(tenant_id=tenant_id).all()
    if nets:
        # A network is "seen" when a sensor is enrolled against it. An
        # unclaimed network is one nothing is watching, which is precisely the
        # gap this card is for.
        rows.append(CoverageRow(label="Networks", total=len(nets),
                                seen=sum(1 for n in nets if getattr(n, "sensor_id", None))))
    return rows


def certs_expiring(db: DbSession, tenant_id: str) -> list[CertExpiry]:
    """Certificates inside the renewal window, soonest first.

    Read off each site's own last check (`Site.last`), which is where the
    sensor writes `cert_days_left` and `cert_issuer`. Nothing is recomputed here,
    so this card and the Security page cannot disagree about how long a
    certificate has left -- the same reason the owner console's account page
    imports its money from `payloads/platform` rather than deriving it again.
    """
    out: list[CertExpiry] = []
    for s in db.query(m.Site).filter_by(tenant_id=tenant_id).all():
        last = s.last or {}
        days = _cert_days(last)
        if days is None or days > CERT_WINDOW_DAYS:
            continue
        out.append(CertExpiry(host=s.name or s.url,
                              issuer=str(last.get("cert_issuer") or ""),
                              days_left=int(days)))
    out.sort(key=lambda c: c.days_left if c.days_left is not None else 10**6)
    return out[:6]


# --------------------------------------------------------------------------
# build
# --------------------------------------------------------------------------

def sensor_status(db: DbSession, tenant_id: str) -> SensorStatus | None:
    rows = db.query(m.Sensor).filter_by(tenant_id=tenant_id).all()
    if not rows:
        return None
    # The tenant's most recently seen sensor is the one the header reports on.
    rows.sort(key=lambda s: (s.last_seen is not None, s.last_seen or dt.datetime.min.replace(tzinfo=dt.timezone.utc)),
              reverse=True)
    s = rows[0]
    return SensorStatus(
        name=s.name, status=s.status,
        last_seen=_iso(s.last_seen) if s.last_seen else None,
        age_s=int((_now() - s.last_seen).total_seconds()) if s.last_seen else None,
        platform=s.platform, version=s.version,
    )


def chat_example_ip(devices) -> str:
    """The IP the chat composer's placeholder uses as its example. Picked
    deterministically -- online before offline, an ordinary device before the
    network gear (asking "what is 192.168.1.1?" about your own router is the
    least interesting question on the network), then lowest address -- so the
    hint does not flap from one poll to the next while the user is reading it.
    Empty when no device has an IP; the web falls back to a literal address."""
    def sort_key(d):
        try:
            octets = tuple(int(p) for p in d.ip.split("."))
        except ValueError:
            octets = ()
        return (d.presence != "ONLINE", d.kind in ("router", "gateway", "switch", "ap"),
                len(octets) != 4, octets, d.ip)

    candidates = [d for d in devices if d.ip]
    if not candidates:
        return ""
    return min(candidates, key=sort_key).ip


# How many open alerts are worth resolving a remedy for. `remedy.resolve`
# touches the database per call, and an owner facing more than a handful of
# decisions at once has a worse problem than a short list -- so this caps the
# work rather than paginating a queue nobody wants to be long.
NEEDS_YOU_SCAN = 40
NEEDS_YOU_MAX = 6


def needs_you(db: DbSession, tenant, role: str = "admin") -> list[NeedsYouItem]:
    """The repairs Vault can run but will not run unasked.

    Only `kind == "approval"`. An "auto" remedy has already run by the time
    anyone reads this, and a "human" one is an errand Vault cannot perform;
    putting either behind an Approve button would be a lie about what the
    button does.
    """
    # Imported on use, never at module scope. `remedy` reaches the chat tool
    # registry, which reaches `routers.write` -> `payloads.jobs` -> back to
    # `remedy` — a cycle that only resolves once the app has finished loading.
    # `payloads/overview.py` is imported very early, so a module-level import
    # here would make `remedy` the first thing to pull in that whole chain,
    # which is exactly when it does not resolve. `payloads/security.py` deals
    # with the same cycle the same way; a cold `import guardian_hub.payloads.
    # overview` followed by this call is what breaks, and deploy.sh's
    # cold-import preflight will not see it because it imports `main` first.
    from .. import remedy as remedy_mod

    rank = {"CRIT": 0, "WARN": 1, "INFO": 2}
    rows = (db.query(m.Alert)
              .filter(m.Alert.tenant_id == tenant.id,
                      m.Alert.status.notin_(m.SETTLED_ALERT_STATUSES))
              .all())
    rows.sort(key=lambda a: (rank.get(a.sev, 3), a.last_seen or ""))
    out: list[NeedsYouItem] = []
    for alert in rows[:NEEDS_YOU_SCAN]:
        r = remedy_mod.resolve(db, tenant, alert, role)
        if r.kind != "approval":
            continue
        out.append(NeedsYouItem(
            key=alert.key, title=alert.msg, detail=r.summary, cost=r.need,
            severity=alert.sev, action=r.action, host=r.host,
        ))
        if len(out) >= NEEDS_YOU_MAX:
            break
    return out


def fixed_without_you(db: DbSession, tenant_id: str, days: int = 14) -> FixedWithoutYou:
    """Repairs that landed in the window, split by who said yes.

    Who said yes is `account.is_person`'s call, the same one the console's
    self-healing chart makes -- a person's yes is stamped `hub:<user>` /
    `hub:chat:<user>` / `dashboard`, and ANY other stamp is Vault acting
    alone. Testing `by != "auto"` instead looks equivalent and is not: the
    sensor writes stamps of its own, and on the live hub 61 rows stamped
    "test" were being reported to the owner as 61 things they had personally
    approved. Reporting the total alone would equally claim the approved
    ones as Vault's work, so the split stays -- it just has to split on
    the same rule in both places, or two panels on one screen disagree.
    """
    since = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=days)).isoformat(timespec="seconds")
    rows = (db.query(m.Fix.by, m.Fix.ts)
              .filter(m.Fix.tenant_id == tenant_id, m.Fix.ts >= since, m.Fix.ok.is_(True))
              .all())
    automatic = sum(1 for (by, _ts) in rows if not account_payload.is_person(by))

    # Every day in the window gets a bar, including the empty ones: closing
    # up over a quiet day would make the fortnight look busier than it was.
    today = dt.datetime.now(dt.timezone.utc).date()
    buckets = {(today - dt.timedelta(days=n)).isoformat(): [0, 0] for n in range(days)}
    for by, ts in rows:
        key = (ts or "")[:10]
        if key in buckets:
            buckets[key][1 if account_payload.is_person(by) else 0] += 1
    daily = [FixDay(day=d, automatic=v[0], approved=v[1]) for d, v in sorted(buckets.items())]

    return FixedWithoutYou(days=days, automatic=automatic, approved=len(rows) - automatic,
                           daily=daily)


def build(db: DbSession, tenant) -> OverviewPayload:
    scan = latest_scan(db, tenant.id)
    raw = scan.raw if scan else {}
    score = raw.get("score") or {}

    live_alerts = db.query(m.Alert).filter(m.Alert.tenant_id == tenant.id,
                                           m.Alert.status != "resolved").all()
    open_alerts = [a for a in live_alerts if a.status == "open"]
    crit = [a for a in open_alerts if a.sev == "CRIT"]
    warn = [a for a in open_alerts if a.sev == "WARN"]
    info = [a for a in open_alerts if a.sev == "INFO"]

    devices = db.query(m.Device).filter_by(tenant_id=tenant.id).all()
    unverified = unverified_devices(devices, raw)
    verdict, next_action = verdict_and_next_action(crit, warn, unverified)

    scan_ts = parse_ts(scan.ts) if scan else None
    age_s = int((_now() - scan_ts).total_seconds()) if scan_ts else None
    rows = timeline(db, tenant.id)
    inc_rows = open_incidents(db, tenant.id)
    return OverviewPayload(
        ts=_iso(scan_ts) if scan_ts else (scan.ts if scan else None),
        age_s=age_s,
        score=scan.score if scan else None,
        grade=score.get("grade") or "",
        potential=score.get("potential"),
        score_model=score.get("model") or "",
        score_coverage=ScoreCoverage(**cov) if isinstance(cov := score.get("coverage"), dict) else None,
        verdict=verdict,
        next_action=next_action,
        deductions=[Deduction(category=d.get("category", ""), points=int(d.get("points") or 0),
                              why=d.get("why", ""))
                    for d in (score.get("deductions") or [])],
        score_change=scorechange.score_change(db, tenant.id, scan),
        strip=health_strip(db, tenant.id),
        timeline=rows,
        score_trend=score_trend(db, tenant.id),
        score_trend_7d=score_trend(db, tenant.id, days=7),
        questions=questions(db, tenant, raw, devices=devices),
        alerts=AlertCounts(
            crit=len(crit), warn=len(warn), info=len(info), open=len(open_alerts),
            acked=sum(1 for a in live_alerts if a.status == "acked"),
            snoozed=sum(1 for a in live_alerts if a.status == "snoozed"),
        ),
        devices=DeviceCounts(
            total=len(devices),
            online=sum(1 for d in devices if device_online(d)),
            sleeping=sum(1 for d in devices if d.presence == "SLEEPING"),
            unverified=len(unverified),
            quarantined=sum(1 for d in devices if d.quarantined),
        ),
        sensor=sensor_status(db, tenant.id),
        networks=[NetworkHealth(**h) for h in networks.health(db, tenant, raw)],
        chat_example_ip=chat_example_ip(devices),
        voice=voice_mod.ready(),
        assets=asset_counts(db, tenant.id, raw, len(devices)),
        changed_24h=changed_24h(db, tenant.id),
        incidents=incident_counts(inc_rows),
        incident_briefs=incident_briefs(db, tenant.id, inc_rows),
        recs=rec_counts(db, tenant.id),
        health=guardian_health(db, tenant, raw, age_s, rows[0] if rows else None),
        paths=paths(raw),
        cameras=cameras_payload.census(db, tenant, raw),
        needs_you=needs_you(db, tenant),
        fixed_without_you=fixed_without_you(db, tenant.id),
        coverage=coverage_rows(db, tenant.id, devices),
        certs_expiring=certs_expiring(db, tenant.id),
    )
