"""Updates tab payload: the patch calendar, host update summaries, the CVE
table, live upgrade progress and the reboot planner (spec S8 / Task 29).

Reads `Scan.raw["ssh"][*]` (`updates`, `sec`, `cve_list`, `cve_fixable`,
`reboot_required`, `docker`, `docker_images`, `services`) plus `Fix`,
`Alert`, `Queue` and `Tenant.config["auto_fix"]["maintenance_window"]`.

Two divergences from a literal reading of the brief, forced by what data is
actually available:

- `calendar`'s "reboots" and "state" are computed from `Fix` rows (action
  "reboot"), not from an "events" table -- there is no per-day reboot Event
  row anywhere in the ingest pipeline (only a deduped `Alert` row keyed
  "reboot:<host>"/"rebooted:<host>"), whereas every executed reboot already
  lands as a `Fix` row with a real per-day timestamp, which is exactly what
  a calendar cell needs.
- `cves`' `fixed_on` needs "the CVE first appeared", which this hub has no
  dedicated CVE-history table for. It is approximated by scanning every
  `Scan.raw` this tenant has (oldest to newest) for the first one that
  already listed that (host, cve) pair, then taking the earliest successful
  `full_upgrade`/`security_updates` Fix after that scan's ts. With a single
  scan (a fresh tenant) this degenerates to "first fix after the only scan
  we have", which is the best available signal.
"""

import datetime as dt

from sqlalchemy import text
from sqlalchemy.orm import Session as DbSession

from .. import models as m
from ..scans import latest_scan, parse_ts
from ..schemas_dash import (
    CveRow, MacUpdates, PatchDay, PatchPosture, RebootCandidate, RebootPlan, RebootWindow, UpdateHost,
    UpdatesPayload, UpgradeProgress,
)
from ._util import _lax_cutoff, _now

TRACKER = "https://security-tracker.debian.org/tracker/{cve}"
UPGRADE_ACTIONS = ("security_updates", "full_upgrade")

#: Most package names carried per host. A neglected box can have hundreds of
#: held-back packages, and this payload is already the heaviest read on the
#: dashboard -- `UpdateHost.pending` keeps the true count, so a trimmed list is
#: visibly trimmed rather than silently wrong.
MAX_PACKAGES = 120


# --------------------------------------------------------------------------
# Patch calendar
# --------------------------------------------------------------------------

def calendar(db: DbSession, tenant_id: str, days: int = 30) -> list[PatchDay]:
    hosts = [h.name for h in db.query(m.Host.name).filter_by(tenant_id=tenant_id).all()]
    if not hosts:
        return []

    today = _now().date()
    start = today - dt.timedelta(days=days - 1)
    start_cutoff = dt.datetime.combine(start, dt.time.min, tzinfo=dt.timezone.utc)

    fixes = (
        db.query(m.Fix)
        .filter(m.Fix.tenant_id == tenant_id, m.Fix.host.in_(hosts),
                m.Fix.action.in_(UPGRADE_ACTIONS + ("reboot",)),
                m.Fix.ts >= _lax_cutoff(start_cutoff))
        .all()
    )

    # (host, day) -> {patches, patch_fail, reboots, reboot_fail}
    buckets: dict[tuple[str, str], dict] = {}
    for f in fixes:
        when = parse_ts(f.ts)
        if when is None or when.date() < start or when.date() > today:
            continue
        key = (f.host, when.date().isoformat())
        cell = buckets.setdefault(key, {"patches": 0, "patch_fail": False, "reboots": 0, "reboot_fail": False})
        if f.action in UPGRADE_ACTIONS:
            if f.ok:
                cell["patches"] += 1
            else:
                cell["patch_fail"] = True
        elif f.action == "reboot":
            if f.ok:
                cell["reboots"] += 1
            else:
                cell["reboot_fail"] = True

    out = []
    for host in sorted(hosts):
        for i in range(days):
            day = (start + dt.timedelta(days=i)).isoformat()
            cell = buckets.get((host, day), {"patches": 0, "patch_fail": False, "reboots": 0, "reboot_fail": False})
            if cell["patch_fail"] or cell["reboot_fail"]:
                state = "failed"
            elif cell["reboots"]:
                state = "reboot"
            elif cell["patches"]:
                state = "patched"
            else:
                state = "none"
            out.append(PatchDay(day=day, host=host, patches=cell["patches"], reboots=cell["reboots"], state=state))
    return out


# --------------------------------------------------------------------------
# Host summaries
# --------------------------------------------------------------------------

def _names(value) -> list[str]:
    """A sensor-sent package list, sanitised: strings only, order kept,
    duplicates dropped. The raw blob is whatever the sensor wrote."""
    out: list[str] = []
    for item in value or []:
        name = str(item).strip()
        if name and name not in out:
            out.append(name)
    return out


def mac_updates(db: DbSession, tenant) -> MacUpdates:
    """The sensor host's own pending updates (`Scan.raw["mac_updates"]`).

    A linux sensor reports `{"os": "linux sensor (patched by apt...)",
    "available": []}` -- still a reported block, and still worth drawing, since
    "the machine Vault runs on is patched like the servers" is the answer
    the panel exists to give.
    """
    scan = latest_scan(db, tenant.id)
    block = ((scan.raw if scan else {}) or {}).get("mac_updates")
    if not isinstance(block, dict) or not block:
        return MacUpdates()
    return MacUpdates(os=str(block.get("os") or ""), available=_names(block.get("available")),
                      checked=str(block.get("ts") or ""), error=str(block.get("error") or ""),
                      reported=True)


def patch_posture(db: DbSession, tenant) -> PatchPosture:
    """Item 29: the update picture as one summary, for Security to carry.

    Security never said anything about patching, so "am I up to date" lived
    only on Updates and a client reading the security page got no answer to
    one of the oldest questions in the trade.

    What Vault can measure, it counts: servers over SSH, and the machine
    its own sensor runs on. What it CANNOT measure it counts separately
    rather than quietly leaving out -- a phone, a camera or a thermostat on
    the LAN has no package list Vault can read, and a total that silently
    skipped them would imply an all-clear it has not earned. `unmeasured` is
    that number, and the page says so in words.
    """
    rows = hosts(db, tenant)
    checked = [h for h in rows if h.checked]
    mac = mac_updates(db, tenant)
    mac_pending = len(mac.available) if mac.reported else 0

    devices = db.query(m.Device).filter_by(tenant_id=tenant.id).count()
    return PatchPosture(
        pending=sum(h.pending for h in checked) + mac_pending,
        security_pending=sum(h.security_pending for h in checked),
        needs_reboot=sum(1 for h in checked if h.reboot_required),
        servers_checked=len(checked),
        servers_unchecked=len(rows) - len(checked),
        # Every LAN device plus, when it reported, the sensor's own machine
        # is a thing Vault sees but cannot read a package list from --
        # except the Mac, which it can, so it is not counted here.
        unmeasured=devices,
    )


def hosts(db: DbSession, tenant) -> list[UpdateHost]:
    scan = latest_scan(db, tenant.id)
    raw = scan.raw if scan else {}
    out = []
    for h in raw.get("ssh") or []:
        upd = h.get("updates") or {}
        sec = h.get("sec") or {}
        out.append(UpdateHost(
            name=h.get("name") or "", os=upd.get("os") or "",
            kernel=upd.get("kernel") or "", kernel_latest=upd.get("kernel_latest") or "",
            pending=int(upd.get("count") or 0),
            # The sensor writes the security list at `sec.security_updates`
            # (`guardian_sensor/hosts.py`) and has no `updates.security` key at
            # all, so this column has always read 0 in production. The original
            # key is kept first in case an older sensor ever sent it.
            security_pending=len(upd.get("security") or sec.get("security_updates") or []),
            # `cve_fixable` is -1 when debsecan hasn't run yet (sensor
            # sentinel, guardian_sensor/hosts.py) -- clamp it to 0 rather
            # than surface a negative "known vulnerabilities with a fix
            # available" count on the dashboard.
            cve_fixable=max(0, int(h.get("cve_fixable") or 0)), unattended=sec.get("unattended"),
            reboot_required=bool(h.get("reboot_required")), last_apt=upd.get("last_apt") or "",
            # The sensor sends both lists; the security subset is intersected
            # with what is actually pending so a stale `sec` block can never
            # advertise a package apt no longer holds back.
            packages=_names(upd.get("pending"))[:MAX_PACKAGES],
            security_packages=[p for p in _names(sec.get("security_updates"))
                               if p in set(_names(upd.get("pending")))][:MAX_PACKAGES],
        ))

    # Item 30: every server Vault knows, not only the ones the last sweep
    # managed to reach.
    #
    # This read from `raw["ssh"]` alone, so a server the sensor could not
    # probe -- unreachable, credentials changed, or simply dropped out of the
    # sensor's config, which is exactly what a bad `/api/config` replay did
    # here -- was absent from the table entirely. Absent reads as fine. It is
    # not fine: an unpatched machine nobody is looking at is the one that
    # gets used.
    #
    # A row with `checked=False` carries no counts, because Vault has
    # none. It exists to say the machine is unaccounted for.
    seen = {h.name for h in out if h.name}
    for row in db.query(m.Host).filter_by(tenant_id=tenant.id).order_by(m.Host.name).all():
        if row.name and row.name not in seen:
            out.append(UpdateHost(name=row.name, checked=False))
    return out


# --------------------------------------------------------------------------
# CVE table
# --------------------------------------------------------------------------

def _cve_first_seen(db: DbSession, tenant_id: str, cutoff: str) -> dict[tuple[str, str], str]:
    """`{(host, cve): ts of the earliest scan in the window that listed it}`.

    "Earliest" is by the `ts` STRING, as it always was here (the loop below
    walks `ORDER BY ts` and keeps the first it meets) -- `fixed_on` is then a
    string comparison against `Fix.ts` too, so the two stay in one ordering.
    `MIN(ts)` in SQL is that same string order.

    On SQLite the pairs are pulled out with `json_each`, so three short
    strings per CVE cross into Python instead of every scan's whole `raw`.
    Measured on the real tenant's full history that is only ~15% faster --
    SQLite still has to parse each blob, it just does it in C -- so do not
    mistake this for the fix to a slow Updates page. That fix is the early
    return in `cves`, which skips this read entirely when there is nothing to
    annotate. If this ever needs to be fast with CVEs present, the answer is
    to record sightings at ingest, not a cleverer query over `raw`.

    Any other backend keeps the Python walk: `json_each` is SQLite's, and the
    hub still accepts a `database_url` pointing elsewhere. The two branches
    were compared over both tenants' entire scan history and agree exactly.
    """
    if db.get_bind().dialect.name == "sqlite":
        rows = db.execute(text(
            "SELECT coalesce(json_extract(h.value, '$.name'), '') AS host, "
            "       coalesce(json_extract(c.value, '$.cve'), '') AS cve, MIN(s.ts) "
            "FROM scan AS s, json_each(s.raw, '$.ssh') AS h, "
            "     json_each(h.value, '$.cve_list') AS c "
            "WHERE s.tenant_id = :tid AND s.ts >= :cutoff AND json_valid(s.raw) "
            "GROUP BY host, cve"), {"tid": tenant_id, "cutoff": cutoff}).all()
        return {(host, cve): ts for host, cve, ts in rows}

    first_seen: dict[tuple[str, str], str] = {}
    scans = (
        db.query(m.Scan.ts, m.Scan.raw)
        .filter(m.Scan.tenant_id == tenant_id, m.Scan.ts >= cutoff)
        .order_by(m.Scan.ts)
        .all()
    )
    for ts, scan_raw in scans:
        for h in (scan_raw or {}).get("ssh") or []:
            name = h.get("name") or ""
            for c in h.get("cve_list") or []:
                key = (name, c.get("cve") or "")
                if key not in first_seen:
                    first_seen[key] = ts
    return first_seen


def cves(db: DbSession, tenant, days: int = 30) -> list[CveRow]:
    scan = latest_scan(db, tenant.id)
    raw = scan.raw if scan else {}

    # `first_seen`/`fixes_by_host` both need to look BACK through history,
    # which -- unlike every other builder in this module -- means they
    # can't just read the latest scan. Bounded to the same `days` window as
    # the rest of the payload rather than the tenant's entire scan history:
    # production scans every few minutes, so an unbounded query here would
    # pull thousands of full `Scan.raw` blobs (each a whole last.json) into
    # memory on every /updates request. This keeps the "first appeared"
    # approximation already documented above (a scan/fix outside the
    # window is invisible to it) but at a bounded, predictable cost.
    #
    # Both are only ever used to annotate rows of the LATEST scan's CVE list,
    # so when that list is empty there is nothing to annotate and the history
    # is not read at all. That is the common case, not an edge: the real
    # tenant had no CVE rows and still paid ~1.6 s per Updates load (several
    # times that on the production hub) to parse a month of scans.
    if not any(h.get("cve_list") for h in raw.get("ssh") or []):
        return []
    cutoff = _lax_cutoff(_now() - dt.timedelta(days=days))
    first_seen = _cve_first_seen(db, tenant.id, cutoff)

    fixes_by_host: dict[str, list[str]] = {}
    fix_rows = (
        db.query(m.Fix.host, m.Fix.ts)
        .filter(m.Fix.tenant_id == tenant.id, m.Fix.action.in_(UPGRADE_ACTIONS),
                m.Fix.ok == True, m.Fix.ts >= cutoff)  # noqa: E712
        .order_by(m.Fix.ts)
        .all()
    )
    for host, ts in fix_rows:
        fixes_by_host.setdefault(host, []).append(ts)

    out = []
    for h in raw.get("ssh") or []:
        host = h.get("name") or ""
        for c in h.get("cve_list") or []:
            cve, package, urgency = c.get("cve") or "", c.get("package") or "", c.get("urgency") or ""
            fixable = urgency != "(fixed)"
            appeared = first_seen.get((host, cve))
            fixed_on = ""
            if appeared:
                for fts in fixes_by_host.get(host, []):
                    if fts > appeared:
                        fixed_on = fts
                        break
            out.append(CveRow(cve=cve, package=package, urgency=urgency, host=host,
                               fixable=fixable, fixed_on=fixed_on, tracker=TRACKER.format(cve=cve)))
    return out


# --------------------------------------------------------------------------
# Live upgrade progress
# --------------------------------------------------------------------------

def progress(db: DbSession, tenant_id: str) -> list[UpgradeProgress]:
    cutoff = _now() - dt.timedelta(hours=24)
    rows = (
        db.query(m.Queue)
        .filter(m.Queue.tenant_id == tenant_id, m.Queue.path == "/api/fix", m.Queue.ts >= cutoff)
        .order_by(m.Queue.ts.desc())
        .all()
    )
    out = []
    for q in rows:
        body = q.body or {}
        if body.get("action") not in UPGRADE_ACTIONS:
            continue
        result = q.result or {}
        prog = result.get("progress") or {}
        done_or_failed = q.status in ("done", "failed")
        # `q.finished_at` is the real completion time `qresult` sets --
        # never fabricated from `q.ts` (the enqueue time; see "started"
        # below), which is what this used to do before the Task 29 fix
        # round: a job could report "finished" the instant it was queued.
        finished = q.finished_at.isoformat(timespec="seconds") if q.finished_at else None
        # Once the sensor posts the final /result, its "out" is the command's
        # actual final output -- prefer that over the last streamed progress
        # line, which is now stale (qresult keeps "progress" around only so
        # done/total don't reset to 0, not so log_tail keeps quoting it).
        log_tail = result.get("out") if done_or_failed and result.get("out") else prog.get("line")
        out.append(UpgradeProgress(
            queue_id=q.id, host=body.get("host") or "", action=body.get("action") or "",
            status=q.status, done=int(prog.get("done") or 0), total=int(prog.get("total") or 0),
            log_tail=str(log_tail or "")[:2000],
            # "started" = queued_at (Queue.ts is the enqueue time, not a
            # true "processing began" timestamp -- the sensor doesn't
            # report that separately).
            started=q.ts.isoformat(timespec="seconds"), finished=finished,
        ))
    return out


# --------------------------------------------------------------------------
# Reboot planner
# --------------------------------------------------------------------------

def _docker_and_service_names(h: dict) -> list[str]:
    names = []
    for d in h.get("docker") or []:
        name = str(d).split(":", 1)[0].strip()
        if name:
            names.append(name)
    names.extend((h.get("services") or {}).keys())
    return names


def _parse_hm(s: str, default: tuple[int, int]) -> tuple[int, int]:
    try:
        h, mi = str(s).split(":", 1)
        return int(h), int(mi)
    except Exception:
        return default


def _sensor_tz(ts: str | None) -> dt.timezone:
    """The offset the sensor's own `ts` strings carry (e.g. "-04:00") --
    "Tonight"/"weekend"/"maintenance window" are household-local concepts,
    so the hour-of-day math below must run in the sensor's own offset, not
    the hub server's UTC. Falls back to UTC when there's no scan yet."""
    if not ts:
        return dt.timezone.utc
    try:
        parsed = dt.datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
        return parsed.tzinfo or dt.timezone.utc
    except Exception:
        return dt.timezone.utc


def _windows(tenant, scan_ts: str | None = None) -> list[RebootWindow]:
    from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
    zone = _sensor_tz(scan_ts)
    try:
        if (tenant.settings or {}).get("timezone"):
            zone = ZoneInfo(tenant.settings["timezone"])
    except (ZoneInfoNotFoundError, ValueError):
        pass
    now = _now().astimezone(zone)

    tonight_start = now.replace(hour=2, minute=0, second=0, microsecond=0)
    if tonight_start <= now:
        tonight_start += dt.timedelta(days=1)
    tonight_end = tonight_start.replace(hour=5)

    # Saturday = weekday() 5 (Monday=0).
    days_to_sat = (5 - now.weekday()) % 7
    sat_start = (now + dt.timedelta(days=days_to_sat)).replace(hour=2, minute=0, second=0, microsecond=0)
    if sat_start <= now:
        sat_start += dt.timedelta(days=7)
    sat_end = sat_start.replace(hour=5)

    mw = ((tenant.config or {}).get("auto_fix") or {}).get("maintenance_window") or {}
    weekday = int(mw.get("weekday", 6)) % 7   # default Sunday
    sh, sm = _parse_hm(mw.get("start"), (3, 0))
    eh, em = _parse_hm(mw.get("end"), (6, 0))
    days_to_mw = (weekday - now.weekday()) % 7
    mw_start = (now + dt.timedelta(days=days_to_mw)).replace(hour=sh, minute=sm, second=0, microsecond=0)
    if mw_start <= now:
        mw_start += dt.timedelta(days=7)
    mw_end = mw_start.replace(hour=eh, minute=em)
    if mw_end <= mw_start:
        mw_end += dt.timedelta(days=1)

    return [
        RebootWindow(label="Now", start=now.isoformat(timespec="seconds"),
                     end=(now + dt.timedelta(minutes=15)).isoformat(timespec="seconds")),
        RebootWindow(label="Tonight", start=tonight_start.isoformat(timespec="seconds"),
                     end=tonight_end.isoformat(timespec="seconds")),
        RebootWindow(label="This weekend", start=sat_start.isoformat(timespec="seconds"),
                     end=sat_end.isoformat(timespec="seconds")),
        RebootWindow(label="Next maintenance window", start=mw_start.isoformat(timespec="seconds"),
                     end=mw_end.isoformat(timespec="seconds")),
    ]


def reboot_plan(db: DbSession, tenant) -> RebootPlan:
    scan = latest_scan(db, tenant.id)
    raw = scan.raw if scan else {}

    pending_by_host: dict[str, bool] = {}
    for q in db.query(m.Queue).filter_by(tenant_id=tenant.id, path="/api/reboot-plan").all():
        if q.status in ("queued", "scheduled", "running"):
            pending_by_host[(q.body or {}).get("host") or ""] = True

    candidates = []
    for h in raw.get("ssh") or []:
        if not h.get("reboot_required"):
            continue
        name = h.get("name") or ""
        alert = db.query(m.Alert).filter_by(tenant_id=tenant.id, key=f"reboot:{name}").first()
        reason = alert.msg if alert else f"{name} needs a reboot (kernel update)"
        candidates.append(RebootCandidate(
            host=name, reason=reason, services=_docker_and_service_names(h),
            queued=bool(pending_by_host.get(name)),
        ))
    return RebootPlan(hosts=candidates, windows=_windows(tenant, scan.ts if scan else None))


# --------------------------------------------------------------------------
# Build
# --------------------------------------------------------------------------

def build(db: DbSession, tenant, days: int = 30) -> UpdatesPayload:
    return UpdatesPayload(
        calendar=calendar(db, tenant.id, days=days),
        hosts=hosts(db, tenant),
        cves=cves(db, tenant, days=days),
        progress=progress(db, tenant.id),
        reboot_plan=reboot_plan(db, tenant),
        mac=mac_updates(db, tenant),
        posture=patch_posture(db, tenant),
    )
