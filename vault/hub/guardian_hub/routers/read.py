import csv
import datetime as dt
import io
import json

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import Response
from sqlalchemy.orm import Session as DbSession

from ..db import get_db
from .. import audit, models as m, networks, onboarding as onboarding_mod
from ..deps import COOKIE, require_role
from ..payloads import (
    assets as assets_payload, history as history_payload, inbox as inbox_payload,
    incidents as incidents_payload, jobs as jobs_payload,
    overview as overview_payload, presence as presence_payload,
    search as search_payload, traffic as traffic_payload, triage as triage_payload,
)
from ..scans import latest_scan as _scans_latest_scan, parse_ts as _scans_parse_ts
from .. import tabular
from .. import schemas_dash as sd

router = APIRouter(prefix="/api/v1", tags=["dashboard-read"])
_now = lambda: dt.datetime.now(dt.timezone.utc)

VIEWER = Depends(require_role("viewer"))


# --- output helpers (shared with write.py, which returns the mutated row) ---

def device_out(d: m.Device, net: "m.Network | None" = None) -> dict:
    return {
        "network": networks.ref(net),
        "mac": d.mac, "ip": d.ip, "name": d.name, "vendor": d.vendor, "kind": d.kind,
        "label": d.label, "trusted": d.trusted, "always_on": d.always_on,
        "quarantined": d.quarantined, "parent": d.parent, "risk": d.risk,
        "presence": d.presence, "ports": d.ports, "first_seen": d.first_seen,
        "last_seen": d.last_seen, "extra": d.extra,
    }


def alert_out(a: m.Alert) -> dict:
    return {
        "key": a.key, "sev": a.sev, "msg": a.msg, "why": a.why, "evidence": a.evidence,
        "confidence": a.confidence, "first_seen": a.first_seen, "last_seen": a.last_seen,
        "count": a.count, "status": a.status, "resolved_by": a.resolved_by,
        "snoozed_until": a.snoozed_until, "fix": a.fix,
    }


def incident_out(i: m.Incident) -> dict:
    return {
        "iid": i.iid, "entity": i.entity, "sev": i.sev, "story": i.story,
        "actions": i.actions, "alert_keys": i.alert_keys, "opened": i.opened, "closed": i.closed,
    }


def rec_out(r: m.Recommendation) -> dict:
    return {
        "key": r.key, "priority": r.priority, "title": r.title, "why": r.why,
        "action": r.action, "decision": r.decision, "decided_by": r.decided_by,
        "status": r.status, "last_seen": r.last_seen,
    }


# --- overview helpers --------------------------------------------------------

def _latest_scan(db: DbSession, tenant_id: str) -> m.Scan | None:
    return _scans_latest_scan(db, tenant_id)


def _unverified_devices(db: DbSession, tenant_id: str, raw: dict) -> list[m.Device]:
    """Kept as a thin alias: the predicate now lives with the payload builder."""
    devices = db.query(m.Device).filter_by(tenant_id=tenant_id).all()
    return overview_payload.unverified_devices(devices, raw)


@router.get("/overview", response_model=sd.OverviewPayload)
def overview(ctx=VIEWER, db: DbSession = Depends(get_db)):
    user, tenant, role = ctx
    return overview_payload.build(db, tenant)


@router.get("/scans/latest")
def scans_latest(ctx=VIEWER, db: DbSession = Depends(get_db)):
    user, tenant, role = ctx
    scan = _latest_scan(db, tenant.id)
    return scan.raw if scan else {}


def _tenant_scan(db: DbSession, tenant, scan_id: str) -> "m.Scan":
    scan = db.query(m.Scan).filter_by(id=scan_id, tenant_id=tenant.id).first()
    if scan is None:
        raise HTTPException(404, "scan not found")
    return scan


@router.get("/scans/{scan_id}", response_model=sd.ScanSummary)
def scan_summary(scan_id: str, ctx=VIEWER, db: DbSession = Depends(get_db)):
    """One stored scan in counts, for the Activity drawer. Tenant-scoped: a
    scan id from another tenant is a 404, never a leak."""
    user, tenant, role = ctx
    scan = _tenant_scan(db, tenant, scan_id)
    raw = scan.raw or {}
    sites = [s for s in raw.get("sites") or [] if isinstance(s, dict)]
    hosts = [h for h in raw.get("ssh") or [] if isinstance(h, dict)]
    return sd.ScanSummary(
        id=scan.id, ts=scan.ts, score=scan.score,
        devices=len(((raw.get("lan") or {}).get("devices") or {})),
        sites_up=sum(1 for s in sites if s.get("ok")), sites_total=len(sites),
        hosts_reachable=sum(1 for h in hosts if h.get("reachable")), hosts_total=len(hosts),
        alerts=len(raw.get("alerts") or []),
    )


@router.get("/scans/{scan_id}/download")
def scan_download(scan_id: str, ctx=VIEWER, db: DbSession = Depends(get_db)):
    """The whole scan as a JSON file. It is the same `Scan.raw` that
    `/scans/latest` already serves to any viewer -- credentials are stripped
    before the row is ever inserted (`ingest.store_scan`) -- so a download
    discloses nothing a viewer could not already read."""
    user, tenant, role = ctx
    scan = _tenant_scan(db, tenant, scan_id)
    stamp = "".join(ch for ch in (scan.ts or "")[:16] if ch.isdigit()) or scan.id
    return Response(content=json.dumps(scan.raw or {}, indent=2, default=str),
                    media_type="application/json",
                    headers={"Content-Disposition": f'attachment; filename="vault-scan-{stamp}.json"'})


@router.get("/sites", response_model=sd.SitesPayload)
def sites(ctx=VIEWER, db: DbSession = Depends(get_db)):
    user, tenant, role = ctx
    return assets_payload.sites(db, tenant)


@router.get("/hosts", response_model=sd.HostsPayload)
def hosts(ctx=VIEWER, db: DbSession = Depends(get_db)):
    user, tenant, role = ctx
    return assets_payload.hosts(db, tenant)


@router.get("/devices", response_model=sd.DevicesPayload)
def devices(network: str = "", ctx=VIEWER, db: DbSession = Depends(get_db)):
    """`?network=<id|slug>` narrows to one network; absent (or `all`) means
    every network. An unknown ref 404s -- never a silent full-tenant read."""
    user, tenant, role = ctx
    return assets_payload.devices(db, tenant, net=networks.resolve(db, tenant.id, network))


@router.get("/devices/{mac}", response_model=sd.DeviceDetail)
def device_detail(mac: str, network: str = "", ctx=VIEWER, db: DbSession = Depends(get_db)):
    user, tenant, role = ctx
    return assets_payload.device_detail(db, tenant, mac, net=networks.resolve(db, tenant.id, network))


@router.get("/presence", response_model=sd.ScreenTimePayload)
def presence(days: int = 7, network: str = "", ctx=VIEWER, db: DbSession = Depends(get_db)):
    user, tenant, role = ctx
    if not 1 <= days <= 90:
        raise HTTPException(422, "days must be between 1 and 90")
    return presence_payload.screen_time(db, tenant.id, days=days,
                                        net=networks.resolve(db, tenant.id, network))


@router.get("/traffic/connections", response_model=sd.ConnectionsPayload)
def traffic_connections(minutes: int = 15, network: str = "", ctx=VIEWER, db: DbSession = Depends(get_db)):
    """Live connections, grouped by device and named -- `payloads/traffic.connections`."""
    user, tenant, role = ctx
    minutes = max(1, min(int(minutes), 24 * 60))
    net = networks.resolve(db, tenant.id, network) if network else None
    return traffic_payload.connections(db, tenant, minutes=minutes, net=net)


@router.get("/traffic", response_model=sd.TrafficOverview)
def traffic_overview(hours: int = 1, network: str = "", ctx=VIEWER, db: DbSession = Depends(get_db)):
    """Who talked to whom in the last `hours` (1..168). See `payloads/traffic.py`
    for why `mode` -- never the rows -- decides what this response can claim."""
    user, tenant, role = ctx
    if not 1 <= hours <= 168:
        raise HTTPException(422, "hours must be between 1 and 168")
    return traffic_payload.overview(db, tenant, hours=hours, net=networks.resolve(db, tenant.id, network))


@router.get("/devices/{mac}/traffic", response_model=sd.DeviceTraffic)
def device_traffic(mac: str, hours: int = 24, network: str = "", ctx=VIEWER, db: DbSession = Depends(get_db)):
    """One device's peers and hourly bytes over the last `hours` (1..168)."""
    user, tenant, role = ctx
    if not 1 <= hours <= 168:
        raise HTTPException(422, "hours must be between 1 and 168")
    return traffic_payload.device(db, tenant, mac, hours=hours, net=networks.resolve(db, tenant.id, network))


@router.get("/history", response_model=sd.HistoryPayload)
def history(days: int = 7, hours: int = 0, network: str = "", ctx=VIEWER,
            db: DbSession = Depends(get_db)):
    """The trend charts, the scan log and the event log over `days` -- or over
    `hours`, which is the only way to ask for a window shorter than a day.

    `?hours=<n>` (1..MAX_HOURS) wins over `?days=`; the payload echoes back
    which of the two it answered. `?network=<id|slug>` narrows the two sources
    that carry a network (events, and the presence samples behind "devices
    online"); an unknown ref 404s rather than silently widening to the whole
    tenant. See `payloads/history.py` for why the metric-backed charts stay
    tenant-wide.
    """
    user, tenant, role = ctx
    if hours and not 1 <= hours <= history_payload.MAX_HOURS:
        raise HTTPException(422, f"hours must be between 1 and {history_payload.MAX_HOURS}")
    if not hours and not 1 <= days <= history_payload.MAX_DAYS:
        raise HTTPException(422, f"days must be between 1 and {history_payload.MAX_DAYS}")
    return history_payload.build(db, tenant, days=days, hours=hours,
                                 net=networks.resolve(db, tenant.id, network))


@router.get("/search", response_model=sd.SearchPayload)
def search(q: str = "", ctx=VIEWER, db: DbSession = Depends(get_db)):
    """The CMD-K palette: devices, hosts, sites, open alerts. Tenant-scoped,
    case-insensitive substring match, capped at 25 results."""
    user, tenant, role = ctx
    return search_payload.search(db, tenant, q)


@router.get("/onboarding", response_model=sd.OnboardingState)
def onboarding_state(ctx=VIEWER, db: DbSession = Depends(get_db)):
    """Has THIS user been through the first-run wizard on THIS tenant, and
    which walkthrough should they get?

    Viewer-readable, and deliberately about the caller only: there is no way
    to ask this about somebody else, and no admin surface that lists it. The
    browser's shell reads this instead of guessing from the tenant's scan
    count -- see `guardian_hub/onboarding.py` for why that guess was wrong.
    """
    user, tenant, role = ctx
    return onboarding_mod.state(db, user, tenant)


def _prev_last_seen(request: Request, db: DbSession = Depends(get_db)):
    """Snapshot `Session.last_seen` as it stood BEFORE this request.

    Declared before `ctx=VIEWER` in `inbox()`'s signature so FastAPI resolves
    it first: `deps.current_session` (reached via `require_role` -> `ctx`)
    bumps the same row's `last_seen` to "now" and commits, so any read taken
    after that dependency runs would see the bumped value. Reading the
    cookie's session here, first, captures the value the sensor/UI actually
    means by "since you were last here".
    """
    tok = request.cookies.get(COOKIE)
    s = db.get(m.Session, tok) if tok else None
    return s.last_seen if s else None


@router.get("/inbox", response_model=sd.InboxPayload)
def inbox(view: str, status: str = "open", network: str = "", prev_seen=Depends(_prev_last_seen),
          ctx=VIEWER, db: DbSession = Depends(get_db)):
    user, tenant, role = ctx
    if view not in ("alerts", "incidents", "recs", "needs"):
        raise HTTPException(422, "view must be alerts|incidents|recs|needs")
    return inbox_payload.build(db, tenant, view, status, role=role, prev_seen=prev_seen,
                               net=networks.resolve(db, tenant.id, network))


@router.get("/jobs", response_model=sd.JobsPayload)
def jobs(ctx=VIEWER, db: DbSession = Depends(get_db)):
    """The Activity page: every queued/running/finished sensor job for this
    tenant, newest first, in plain English (see `payloads/jobs.py`)."""
    user, tenant, role = ctx
    return jobs_payload.build(db, tenant.id)


@router.get("/inbox/triage", response_model=sd.InboxTriagePayload)
def inbox_triage(ctx=VIEWER, db: DbSession = Depends(get_db)):
    """The prioritised inbox (task 83): one lead item, everything else one
    line, causes rolled up, the three-way outcome counted. No `prev_seen` --
    triage has no unread count of its own, so it does not need the
    session-ordering dance `/inbox` documents on `_prev_last_seen`."""
    user, tenant, role = ctx
    return triage_payload.build(db, tenant, role=role)


@router.get("/incidents/{iid}", response_model=sd.IncidentDetail)
def incident_detail(iid: str, ctx=VIEWER, db: DbSession = Depends(get_db)):
    """One incident, one page (Task 83/A2): the written narrative, a timeline
    of its alerts, the assets it touched, a graph scoped to just those
    assets, and the evidence rows. Viewer-readable like the inbox row it
    replaces the `/inbox?filter=incidents&row=` link for; `actions` stays
    empty for a role that may not run them, same contract as every other
    inbox row."""
    user, tenant, role = ctx
    incident = incidents_payload.find(db, tenant.id, iid)
    if incident is None:
        raise HTTPException(404, "incident not found")
    return incidents_payload.detail(db, tenant, incident, role)


@router.get("/events")
def events(limit: int = 200, network: str = "", ctx=VIEWER, db: DbSession = Depends(get_db)):
    user, tenant, role = ctx
    net = networks.resolve(db, tenant.id, network)
    netrefs = networks.refs_by_id(db, tenant.id)
    q = db.query(m.Event).filter_by(tenant_id=tenant.id)
    if net is not None:
        q = q.filter(m.Event.network_id == net.id)
    rows = q.order_by(m.Event.id.desc()).limit(limit).all()
    return [{"ts": e.ts, "type": e.type, "entity": e.entity, "payload": e.payload,
             "network": netrefs.get(e.network_id)} for e in rows]


@router.get("/metrics")
def metrics(name: str = "", entity: str = "", hours: int = 24, ctx=VIEWER, db: DbSession = Depends(get_db)):
    user, tenant, role = ctx
    cutoff = _now() - dt.timedelta(hours=hours)
    # A day-widened text cut first: `Metric.ts` carries the sensor's own
    # offset, so SQL can only narrow, and the exact test stays in Python
    # below. Without it every metric the tenant ever kept was loaded to
    # answer a 24-hour question -- 7.7 s on the production home network.
    from ..payloads._util import _lax_cutoff
    q = db.query(m.Metric).filter(m.Metric.tenant_id == tenant.id, m.Metric.ts >= _lax_cutoff(cutoff))
    if name:
        q = q.filter_by(name=name)
    if entity:
        q = q.filter_by(entity=entity)
    rows = q.order_by(m.Metric.ts.asc()).all()
    out = []
    for r in rows:
        try:
            p = dt.datetime.fromisoformat(r.ts)
            if p.tzinfo is None:
                p = p.replace(tzinfo=dt.timezone.utc)
            keep = p >= cutoff
        except (TypeError, ValueError):
            keep = True
        if keep:
            out.append({"ts": r.ts, "value": r.value})
    return out


# --------------------------------------------------------------------------
# Export (Task 77 / F6)
# --------------------------------------------------------------------------

EXPORT_MAX_DAYS = 365
EXPORT_COLUMNS = ("key", "severity", "confidence", "status", "first_seen", "last_seen", "count",
                  "network", "alert", "why", "evidence", "resolved_by", "snoozed_until")


def _csv_cell(value) -> str:
    """One CSV cell, safe to open in a spreadsheet.

    `Alert.msg`/`why`/`evidence` are sensor-authored free text. A cell starting
    with `=`, `+`, `-`, `@`, tab or CR is a formula to Excel/Sheets, so it is
    prefixed with an apostrophe -- the standard, lossless neutralisation. The
    value is never altered otherwise; quoting and newlines are `csv`'s job.
    """
    text = "" if value is None else str(value)
    return "'" + text if text[:1] in ("=", "+", "-", "@", "\t", "\r") else text


@router.get("/export/alerts.csv")
def export_alerts(days: int = 30, status: str = "all", network: str = "", request: Request = None,
                  ctx=VIEWER, db: DbSession = Depends(get_db)):
    """The alert log as a CSV file -- the reference console's export button.

    Viewer-readable, like the inbox rows it exports: a higher bar on the same
    data the same user can already read row by row would be incoherent. It is
    still data leaving the hub, so every export is audit-logged with the user,
    role, tenant, IP and the window asked for.
    """
    user, tenant, role = ctx
    if not 1 <= days <= EXPORT_MAX_DAYS:
        raise HTTPException(422, f"days must be between 1 and {EXPORT_MAX_DAYS}")
    if status not in ("all", "open", "acked", "snoozed", "resolved"):
        raise HTTPException(422, "status must be all|open|acked|snoozed|resolved")
    net = networks.resolve(db, tenant.id, network)

    cutoff = _now() - dt.timedelta(days=days)
    q = db.query(m.Alert).filter(m.Alert.tenant_id == tenant.id)
    if status != "all":
        q = q.filter(m.Alert.status == status)
    if net is not None:
        q = q.filter(m.Alert.network_id == net.id)
    refs = networks.refs_by_id(db, tenant.id)
    rows = []
    for a in q.order_by(m.Alert.last_seen.desc()).all():
        when = _scans_parse_ts(a.last_seen)
        if when is not None and when < cutoff:
            continue
        rows.append(a)

    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(EXPORT_COLUMNS)
    for a in rows:
        ref = refs.get(a.network_id) if a.network_id else None
        writer.writerow([_csv_cell(v) for v in (
            a.key, a.sev, a.confidence, a.status, a.first_seen, a.last_seen, a.count,
            (ref or {}).get("name", ""), a.msg, a.why, a.evidence, a.resolved_by, a.snoozed_until,
        )])

    audit.log(db, tenant.id, user.username, role, "export.alerts",
              target=f"{len(rows)} alerts", ip=(request.client.host if request and request.client else ""),
              body={"days": days, "status": status, "network": network})
    db.commit()

    stamp = _now().date().isoformat()
    return Response(
        content=buf.getvalue(), media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="vault-alerts-{stamp}.csv"'},
    )


#: What "Export" can mean, one entry per dataset. A page names the dataset it
#: is showing and picks a format; nothing else about an export varies, which
#: is the whole point of `tabular` -- History cannot end up with a different
#: idea of an export than Inbox has.
#:
#: Each builder returns `(columns, rows, title)` and does its own filtering.
#: All of them are viewer-readable, like the screens they mirror: a higher
#: bar on data the same user already reads row by row would be incoherent.


def _rows_history(db: DbSession, tenant, days: int, **_kw):
    cutoff = _now() - dt.timedelta(days=days)
    out = []
    for e in (db.query(m.Event).filter(m.Event.tenant_id == tenant.id)
              .order_by(m.Event.ts.desc()).limit(20000).all()):
        when = _scans_parse_ts(e.ts)
        if when is not None and when < cutoff:
            continue
        payload = e.payload or {}
        detail = payload.get("detail") or payload.get("msg") or ""
        if not detail and payload:
            detail = ", ".join(f"{k}={v}" for k, v in list(payload.items())[:6])
        out.append([e.ts, e.type, e.entity, detail])
    return ["when", "what", "where", "detail"], out, "History"


def _rows_activity(db: DbSession, tenant, days: int, **_kw):
    cutoff = _now() - dt.timedelta(days=days)
    out = []
    for q in (db.query(m.Queue).filter(m.Queue.tenant_id == tenant.id)
              .order_by(m.Queue.ts.desc()).limit(20000).all()):
        if q.ts is not None and q.ts < cutoff:
            continue
        body = q.body or {}
        result = q.result or {}
        out.append([
            q.ts.isoformat(timespec="seconds") if q.ts else "",
            q.path, body.get("host", ""), body.get("action", ""), q.who, q.status,
            q.finished_at.isoformat(timespec="seconds") if q.finished_at else "",
            result.get("out", "") or result.get("error", ""),
        ])
    return (["queued", "kind", "target", "action", "asked by", "state", "finished", "result"],
            out, "Activity")


def _rows_devices(db: DbSession, tenant, days: int, **_kw):
    out = []
    for d in (db.query(m.Device).filter(m.Device.tenant_id == tenant.id)
              .order_by(m.Device.last_seen.desc()).limit(20000).all()):
        out.append([d.mac, d.name or "", d.ip or "", d.vendor or "", d.kind or "",
                    d.first_seen, d.last_seen, d.verified])
    return (["mac", "name", "address", "vendor", "kind", "first seen", "last seen", "verified"],
            out, "Devices")


def _rows_fixes(db: DbSession, tenant, days: int, **_kw):
    cutoff = _now() - dt.timedelta(days=days)
    out = []
    for f in (db.query(m.Fix).filter(m.Fix.tenant_id == tenant.id)
              .order_by(m.Fix.ts.desc()).limit(20000).all()):
        when = _scans_parse_ts(f.ts)
        if when is not None and when < cutoff:
            continue
        out.append([f.ts, f.host, f.action, f.label or "", f.arg or "", f.by, f.ok,
                    "yes" if f.verified_at else "no", (f.out or "")[:400]])
    return (["when", "host", "action", "what it did", "argument", "asked by", "worked",
             "re-checked", "output"], out, "Repairs")


def _rows_reports(db: DbSession, tenant, days: int, **_kw):
    """The report LIBRARY as a table.

    A report itself is a narrative document, not rows -- it opens as a page
    and prints to PDF through the browser, which is the same mechanism this
    module's own `pdf` format uses. What is tabular is the list: what was
    built, when, over what period. That is the thing an auditor asks for.
    """
    cutoff = _now() - dt.timedelta(days=days)
    out = []
    for r in (db.query(m.Report).filter(m.Report.tenant_id == tenant.id)
              .order_by(m.Report.created.desc()).limit(5000).all()):
        if r.created is not None and r.created < cutoff:
            continue
        out.append([r.created.isoformat(timespec="seconds") if r.created else "",
                    r.kind, r.preset, r.period_days, len(r.sections or [])])
    return ["built", "kind", "preset", "period (days)", "sections"], out, "Report library"


def _rows_tickets(db: DbSession, tenant, days: int, **_kw):
    """The ticket log as a table: what was asked, about what, and how it ended.

    The thread bodies are deliberately NOT here. A ticket conversation is
    prose between two people and a spreadsheet cell destroys it; what an
    auditor or a new IT provider asks for is the ledger -- when it was
    raised, what it concerned, how long it took to close.
    """
    cutoff = _now() - dt.timedelta(days=days)
    out = []
    for t in (db.query(m.Ticket).filter(m.Ticket.tenant_id == tenant.id)
              .order_by(m.Ticket.created.desc()).limit(5000).all()):
        if t.created is not None and t.created < cutoff:
            continue
        # Days open, or days it took -- one column, because "still open for
        # 9 days" and "closed after 9 days" are the same measurement and a
        # desk is judged on it either way.
        end = t.resolved_at or _now()
        age = (end - t.created).days if t.created else ""
        out.append([f"#{t.id[-6:].upper()}",
                    t.created.isoformat(timespec="seconds") if t.created else "",
                    t.subject, t.category, t.context_label or "",
                    t.priority, t.status, t.opened_by, age,
                    t.resolved_at.isoformat(timespec="seconds") if t.resolved_at else ""])
    return (["ref", "raised", "subject", "category", "about", "priority",
             "status", "raised by", "days", "resolved"], out, "Support tickets")


EXPORT_DATASETS = {
    "alerts": None,            # handled by its own route, which predates this
    "history": _rows_history,
    "activity": _rows_activity,
    "devices": _rows_devices,
    "fixes": _rows_fixes,
    "reports": _rows_reports,
    "tickets": _rows_tickets,
}


@router.get("/export/alerts")
def export_alerts_any(fmt: str = "csv", days: int = 30, status: str = "all", network: str = "",
                      request: Request = None, ctx=VIEWER, db: DbSession = Depends(get_db)):
    """The same alert log as `/export/alerts.csv`, in csv, xlsx or pdf.

    `/export/alerts.csv` stays exactly as it was: sensors and any script a
    customer already points at it keep working. This is the route the UI's
    format picker calls, and both read the same rows through the same
    filters and write the same audit row -- an export is data leaving the
    hub whichever button produced it.
    """
    user, tenant, role = ctx
    if fmt not in tabular.FORMAT_SPEC:
        raise HTTPException(422, f"fmt must be one of {', '.join(sorted(tabular.FORMAT_SPEC))}")
    if not 1 <= days <= EXPORT_MAX_DAYS:
        raise HTTPException(422, f"days must be between 1 and {EXPORT_MAX_DAYS}")
    if status not in ("all", "open", "acked", "snoozed", "resolved"):
        raise HTTPException(422, "status must be all|open|acked|snoozed|resolved")
    net = networks.resolve(db, tenant.id, network)

    cutoff = _now() - dt.timedelta(days=days)
    q = db.query(m.Alert).filter(m.Alert.tenant_id == tenant.id)
    if status != "all":
        q = q.filter(m.Alert.status == status)
    if net is not None:
        q = q.filter(m.Alert.network_id == net.id)
    refs = networks.refs_by_id(db, tenant.id)
    rows = []
    for a in q.order_by(m.Alert.last_seen.desc()).all():
        when = _scans_parse_ts(a.last_seen)
        if when is not None and when < cutoff:
            continue
        ref = refs.get(a.network_id) if a.network_id else None
        rows.append([a.key, a.sev, a.confidence, a.status, a.first_seen, a.last_seen, a.count,
                     (ref or {}).get("name", ""), a.msg, a.why, a.evidence, a.resolved_by,
                     a.snoozed_until])

    body, media, filename = tabular.render(
        fmt, columns=list(EXPORT_COLUMNS), rows=rows, title="Alert log",
        subtitle=f"{tenant.name} · last {days} days · {status}", slug="alerts",
    )
    audit.log(db, tenant.id, user.username, role, "export.alerts",
              target=f"{len(rows)} alerts", ip=(request.client.host if request and request.client else ""),
              body={"days": days, "status": status, "network": network, "fmt": fmt})
    db.commit()
    # `inline` for pdf: the browser opens the printable page in a tab where
    # the person presses Print, rather than downloading an .html they then
    # have to find and open by hand.
    disposition = "inline" if fmt == "pdf" else "attachment"
    return Response(
        content=body, media_type=media,
        headers={"Content-Disposition": f'{disposition}; filename="{filename}"'},
    )


@router.get("/export/{dataset}")
def export_dataset(dataset: str, fmt: str = "csv", days: int = 30, request: Request = None,
                   ctx=VIEWER, db: DbSession = Depends(get_db)):
    """Any registered dataset, in csv, xlsx or pdf.

    `/export/alerts` keeps its own handler above -- it has filters this
    generic one does not -- and this serves everything else. Both audit-log,
    because an export is data leaving the hub whichever page produced it.
    """
    user, tenant, role = ctx
    builder = EXPORT_DATASETS.get(dataset)
    if builder is None:
        raise HTTPException(404, f"no such export: {dataset}")
    if fmt not in tabular.FORMAT_SPEC:
        raise HTTPException(422, f"fmt must be one of {', '.join(sorted(tabular.FORMAT_SPEC))}")
    if not 1 <= days <= EXPORT_MAX_DAYS:
        raise HTTPException(422, f"days must be between 1 and {EXPORT_MAX_DAYS}")

    columns, rows, title = builder(db, tenant, days)
    body, media, filename = tabular.render(
        fmt, columns=columns, rows=rows, title=title,
        subtitle=f"{tenant.name} · last {days} days", slug=dataset,
    )
    audit.log(db, tenant.id, user.username, role, f"export.{dataset}",
              target=f"{len(rows)} rows", ip=(request.client.host if request and request.client else ""),
              body={"days": days, "fmt": fmt})
    db.commit()
    disposition = "inline" if fmt == "pdf" else "attachment"
    return Response(content=body, media_type=media,
                    headers={"Content-Disposition": f'{disposition}; filename="{filename}"'})
