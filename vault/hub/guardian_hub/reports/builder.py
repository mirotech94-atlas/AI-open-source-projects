"""Report engine: `build_report` renders the same 19 sections, in the same
order, with the same KPI cards / tables / heartbeat bars / inline SVG charts
as Nexus `build_report` (`~/.claude/nexus-agent/ui/server.py:360-521`) --
but reading Vault's SQL tables instead of Nexus's flat JSON/jsonl files.

Data-source translation table (Nexus -> Vault), the actual work of this
port:

    Nexus source                       Vault source
    ---------------------------------   --------------------------------------
    load("last")                        `scans.latest_scan(db, tenant.id).raw`
                                         (`last_of`) -- stored exactly
                                         Nexus-shaped by `ingest.store_scan`,
                                         so sites/ssh/home/wan_path/domains/
                                         email_sec/rogue/score are read from
                                         it directly, unchanged.
    load("state")["alerts_db"]          `Alert` rows. Nexus `state` ->
                                         `Alert.status`; Nexus `resolved_at`
                                         -> `Alert.last_seen` when
                                         `status == "resolved"`, else None;
                                         Nexus `resolved_by` -> `Alert.resolved_by`.
    load("state")["lan"]["known_macs"]  `Device` rows (`extra` carries
                                         anything not columnar).
    load("config")                      `Tenant.config`,
                                         `Tenant.settings["profile"]`,
                                         `Tenant.brand`.
    jsonl("history.jsonl", since=cut)   `Metric` rows regrouped by `ts` into
                                         the Nexus history-line shape by
                                         `history_lines`.
    h["live_macs"]                      `PresenceSample` rows, grouped by
                                         `ts` inside `history_lines`.
    jsonl("remediation.jsonl")           `Fix` rows since the cut.
    jsonl("events.jsonl")                `Event` rows since the cut.
    last["incidents"]                    `Incident` rows (open, or opened
                                         within the period).
    last["recommendations"]              `Recommendation` rows.

Device-group sections (`dev_*`, see `sections.DEVICE_SECTIONS`) always
render, even for a group with zero devices in it -- unlike a KPI-derived
section, "you have no cameras" is still meaningful information for a "full"
report, so every key named in `SECTION_KEYS`/`SECTION_TITLES` is guaranteed
to show up (as an empty table) rather than silently disappearing.
"""

import datetime as dt
import hashlib
import pathlib
import secrets
from urllib.parse import urlparse

from sqlalchemy.orm import Session as DbSession

from .. import models as m
from ..config import settings
from ..payloads._util import incidents as count_incidents, _lax_cutoff
from ..payloads.assets import HARDENING_CHECKS, group_of, hardening, kind_of
from ..payloads import dnsview as dnsview_payload
from ..payloads import overview as overview_payload
from ..payloads.overview import unverified_devices
from ..payloads import presence as presence_payload
from .. import controls, dailyfacts
from ..scans import latest_scan, parse_ts
from .. import schemas_dash as sd
from . import compliance as compliance_mod
from . import presets as presets_mod
from . import sections as S
from .css import EXEC_REPORT_CSS, REPORT_CSS

_now = lambda: dt.datetime.now(dt.timezone.utc)

_SITE_METRICS = {"latency_ms": "lat", "up": "ok", "cert_days": "cert"}
_HOST_METRICS = {"disk_pct": "disk", "mem_pct": "mem", "load": "load", "failed_ssh": "failed"}


# --------------------------------------------------------------------------
# Data accessors
# --------------------------------------------------------------------------

def last_of(db: DbSession, tenant) -> dict:
    """The tenant's latest scan payload, Nexus-`last.json`-shaped. Empty
    dict when the tenant has no scan yet."""
    scan = latest_scan(db, tenant.id)
    return scan.raw if scan else {}


def networks_of(db: DbSession, tenant) -> list["m.Network"]:
    """Every network belonging to `tenant`, primary first then by name --
    the single accessor every per-network report section iterates over
    (mirrors `last_of`). A tenant with 0 or 1 networks tells its sections
    to render the legacy, un-prefixed body instead of splitting."""
    rows = db.query(m.Network).filter_by(tenant_id=tenant.id).all()
    rows.sort(key=lambda n: (not n.is_primary, (n.name or "").lower()))
    return rows


def history_lines(db: DbSession, tenant_id: str, since: str) -> list[dict]:
    """Rebuild the Nexus `history.jsonl` line shape from `Metric` +
    `PresenceSample` rows, grouped back together by `ts`:

        {ts, sites: {name: {ok, lat, cert}}, hosts: {name: {disk, mem, load,
         failed}}, lan, score, speed: {down, up} | None, live_macs: [...],
         dns_ms}

    Returned oldest-first (ascending `ts`), matching the jsonl tail's own
    order.
    """
    metric_rows = (
        db.query(m.Metric.ts, m.Metric.name, m.Metric.entity, m.Metric.value)
        .filter(m.Metric.tenant_id == tenant_id, m.Metric.ts >= since)
        .all()
    )
    presence_rows = (
        db.query(m.PresenceSample.ts, m.PresenceSample.mac)
        .filter(m.PresenceSample.tenant_id == tenant_id, m.PresenceSample.ts >= since)
        .all()
    )

    by_ts: dict[str, dict] = {}

    def line(ts: str) -> dict:
        return by_ts.setdefault(ts, {
            "ts": ts, "sites": {}, "hosts": {}, "lan": None, "score": None,
            "speed": None, "live_macs": [], "dns_ms": None,
        })

    for ts, name, entity, value in metric_rows:
        L = line(ts)
        if name in _SITE_METRICS:
            site = L["sites"].setdefault(entity, {"ok": None, "lat": None, "cert": None})
            key = _SITE_METRICS[name]
            if key == "ok":
                site[key] = value not in (None, 0.0)
            elif key == "lat":
                # `Metric.name == "latency_ms"` is stored in milliseconds
                # (`ingest.store_scan`'s `met("latency_ms", name, lat * 1000)`)
                # but the Nexus-shaped history line's `sites[name]["lat"]`
                # (and everything build_report renders from it -- averages,
                # the response-time chart) is in seconds. Convert here, once,
                # so every consumer of `history_lines` gets seconds.
                site[key] = (value / 1000.0) if value is not None else None
            else:
                site[key] = value
        elif name in _HOST_METRICS:
            host = L["hosts"].setdefault(entity, {"disk": None, "mem": None, "load": None, "failed": None})
            host[_HOST_METRICS[name]] = value
        elif name == "live_devices":
            L["lan"] = value
        elif name == "score":
            L["score"] = value
        elif name == "speed_down":
            L["speed"] = L["speed"] or {}
            L["speed"]["down"] = value
        elif name == "speed_up":
            L["speed"] = L["speed"] or {}
            L["speed"]["up"] = value
        elif name == "dns_ms":
            L["dns_ms"] = value

    for ts, mac in presence_rows:
        line(ts)["live_macs"].append(mac)

    return [by_ts[k] for k in sorted(by_ts.keys())]


def network_presence_lines(db: DbSession, tenant_id: str, network_id: str, since: str) -> list[dict]:
    """Like `history_lines`'s `live_macs`, but scoped to one network via
    `presence_sample.network_id` (Task 51) -- the raw material for the
    per-network "Last 60 scans" heartbeat column, since `history_lines`
    itself pools every network's samples into one tenant-wide `live_macs`
    per `ts`."""
    rows = (
        db.query(m.PresenceSample.ts, m.PresenceSample.mac)
        .filter(m.PresenceSample.tenant_id == tenant_id, m.PresenceSample.network_id == network_id,
                m.PresenceSample.ts >= since)
        .all()
    )
    by_ts: dict[str, list[str]] = {}
    for ts, mac in rows:
        by_ts.setdefault(ts, []).append(mac)
    return [{"ts": ts, "live_macs": by_ts[ts]} for ts in sorted(by_ts.keys())]


def _resolved_at(alert: "m.Alert") -> str | None:
    return alert.last_seen if alert.status == "resolved" else None


def _report_identity(db: DbSession, tenant, sites: list) -> tuple[str, str, str, dict]:
    """`(owner, domain, who, brand)` -- the cover/masthead's "who this is
    for", "whose network this is" and "who is presenting it" strings, shared
    by every `build_*_report` so they never drift on the same tenant.

    Two different brands are in play here and it matters which is which.
    `owner` is the CUSTOMER: it falls back to the tenant's own `brand.name`
    and never to the partner's, because "Prepared for Acme MSP" on a dental
    practice's report is the wrong name on the document. `brand` is the
    PRESENTER, resolved through `branding.resolve` -- the customer's own if
    set, else the partner's, else Vault's -- and is what the masthead
    prints. It is returned fully resolved so the HTML freezes the computed
    brand of the day, which is the audit spec's snapshot rule.
    `sites[0]`'s host is only a fallback for `domain`; a configured brand
    "domain" at either level always wins."""
    from .. import branding
    brand = branding.resolve(db, tenant)
    own = tenant.brand or {}
    prof = (tenant.settings or {}).get("profile") or {}
    owner = prof.get("company") or own.get("name") or prof.get("owner") or tenant.name or "Vault"
    if prof.get("owner") and prof.get("company"):
        owner = f"{prof['company']} · {prof['owner']}"
    # `urlparse` strips the scheme/path so the cover shows "safehome.example.com",
    # not "https://safehome.example.com/".
    domain = brand["domain"] or (urlparse(sites[0].get("url") or "").netloc if sites else "") or ""
    who = S.esc(owner) + (f" · {S.esc(prof['email'])}" if prof.get("email") else "") + \
        (f" · {S.esc(prof['address'])}" if prof.get("address") else "")
    return owner, domain, who, brand


# The product's own theme toggle (`web/src/lib/theme.ts`) writes an explicit
# "light"/"dark" choice to `localStorage["guardian.theme"]` and stamps
# `data-theme` on `<html>` before first paint (`web/src/lib/themeBoot.ts`) --
# but only inside the SPA document. A report opened in its own tab
# (`window.open(card.url)`) or a shared `/r/<token>` link is a *different*
# document on the same origin, so it never inherits that attribute unless
# it re-applies the same persisted choice itself. This is that same boot
# script, by hand, for the report document: same storage key, same "light
# or dark only, system means absent" rule, run before the stylesheet is
# even parsed so there is no flash. It has no effect for someone who opens
# a shared link on a device that never visited the app (no matching
# localStorage) -- `prefers-color-scheme` in `EXEC_REPORT_CSS` covers that
# reader instead. Print is intentionally untouched by this: see
# `EXEC_REPORT_CSS`'s own comment for why the printed page always stays
# light regardless of which skin the screen is showing.
_THEME_BOOT_SCRIPT = (
    '<script>try{var t=localStorage.getItem("guardian.theme");'
    'if(t==="light"||t==="dark")document.documentElement.dataset.theme=t;}catch(e){}</script>'
)


# --------------------------------------------------------------------------
# build_report
# --------------------------------------------------------------------------

def build_report(db: DbSession, tenant, days: int = 7, sections: list[str] | None = None,
                  title: str | None = None, preset: str | None = None, *,
                  toolbar: bool = True) -> str:
    """`toolbar=False` drops the "Print / save as PDF" control.

    The builder's live-preview iframe is sandboxed without `allow-scripts`
    (the report body renders tenant-supplied strings, so scripts stay off),
    which makes that button's inline `print()` both dead and a console
    error in every preview refresh. A saved or shared report -- opened as a
    real document -- keeps it."""
    # `exec_report` is not a `SECTION_KEYS` subset -- it is a different
    # document entirely (see `presets.SPECIAL_PRESETS`'s comment), so it is
    # dispatched before any of the section-picking logic below runs, the
    # same way `sections`/`preset=None` never reach `PRESETS[preset]` either.
    if preset == "exec_report":
        return build_executive_report(db, tenant, days=days, title=title, toolbar=toolbar)
    if preset == "vuln_report":
        return build_vuln_report(db, tenant, days=days, title=title, toolbar=toolbar)
    if preset == "threat_report":
        return build_threat_report(db, tenant, days=days, title=title, toolbar=toolbar)
    if preset:
        _, preset_keys = presets_mod.PRESETS[preset]
        wanted = set(preset_keys)
    elif sections:
        wanted = set(sections)
    else:
        wanted = set(S.SECTION_KEYS)
    secs = [k for k in S.SECTION_KEYS if k in wanted]

    now = _now()
    cut_dt = now - dt.timedelta(days=days)
    cut = cut_dt.isoformat(timespec="seconds")
    # The closed days this report covers, for `uptime()`'s frozen-facts read.
    # Yesterday is the last day that can have been closed; today has not
    # finished and must never be frozen.
    first_day = (now - dt.timedelta(days=days)).date().isoformat()
    last_day = (now - dt.timedelta(days=1)).date().isoformat()

    last = last_of(db, tenant)
    sites = last.get("sites") or []
    hosts = last.get("ssh") or []
    home = last.get("home") or {}

    devices = db.query(m.Device).filter_by(tenant_id=tenant.id).all()
    known = {d.mac: d for d in devices}
    unv_devices = unverified_devices(devices, last)
    unv_macs = {d.mac for d in unv_devices}

    nets = networks_of(db, tenant)
    single_network = len(nets) <= 1
    # Screen-time (Task 51 per-network semantics) computed once per network,
    # reused by both the presence section and each dev_* group's Presence
    # column so a device's percentage is consistent everywhere it appears.
    net_screen: dict[str, list] = {}
    if not single_network:
        for n in nets:
            net_screen[n.id] = presence_payload.screen_time(db, tenant.id, days=days, net=n).devices

    per_alerts = (
        db.query(m.Alert)
        .filter(m.Alert.tenant_id == tenant.id)
        .filter((m.Alert.first_seen >= cut) | (m.Alert.status != "resolved"))
        .all()
    )
    fixes = db.query(m.Fix).filter(m.Fix.tenant_id == tenant.id, m.Fix.ts >= cut).all()
    events = db.query(m.Event).filter(m.Event.tenant_id == tenant.id, m.Event.ts >= cut).all()
    incidents = (
        db.query(m.Incident)
        .filter(m.Incident.tenant_id == tenant.id)
        .filter((m.Incident.closed.is_(None)) | (m.Incident.opened >= cut))
        .all()
    )
    recommendations = db.query(m.Recommendation).filter_by(tenant_id=tenant.id).all()

    pts = history_lines(db, tenant.id, cut)
    spts = [h for h in pts if h.get("speed")]

    def tsx(ts: str) -> float:
        when = parse_ts(ts)
        return when.timestamp() if when else 0.0

    def uptime(name: str):
        """Prefer the frozen daily facts; recompute only when they cannot answer.

        A report is the one Vault artefact that gets filed, forwarded and
        quoted back months later, so it is the worst place for a figure that
        drifts. Recomputing from `history_lines` reads rows that retention
        deletes at 90 days, which means a report re-run about last quarter
        does not reproduce the report that was sent -- it degrades toward
        "no data" and says so as a lower number first.

        `daily_fact` rows were written once at day close and are never
        recomputed, so they answer the same way in a year. The fallback stays
        for the windows they genuinely cannot cover: a period that includes
        today, which has not closed yet, and any span that predates the table
        itself.
        """
        frozen = dailyfacts.uptime_between(db, tenant.id, name, first_day, last_day)
        if frozen["days"]:
            return frozen["uptime_pct"], frozen["checks"]
        p = [h for h in pts if name in (h.get("sites") or {})]
        return S.pct(sum(1 for h in p if h["sites"][name].get("ok")), len(p)), len(p)

    def incident_count(name: str) -> int:
        return count_incidents([h["sites"][name].get("ok") for h in pts if name in (h.get("sites") or {})])

    out: list[str] = []
    toc: list[tuple[str, str, int]] = []
    sec_no = 0
    part_shown: str | None = None

    def section(key: str, heading: str, body: str) -> None:
        """One numbered section, under its Part.

        The Parts are the organising device: nineteen sections numbered 01
        to 19 read as a dump, the same nineteen under six headings read as a
        document. A Part's heading is printed once, before the first of its
        sections that made the cut, and only then -- a Part with nothing in
        it prints nothing. The `<section class="sec"><h2><span class="num">`
        shape is unchanged: the print CSS, the tests and the shared PDF
        renderer all key on it."""
        nonlocal sec_no, part_shown
        if key in secs and body:
            sec_no += 1
            part = S.PART_OF.get(key)
            if part and part != part_shown:
                part_shown = part
                pn, pt = S.PARTS[part]
                out.append(f'<div class="part"><span class="part-n">{S.esc(pn)}</span><span class="part-t">{S.esc(pt)}</span></div>')
            toc.append((f"{sec_no:02d}", heading, sec_no))
            # The contents link lands on an anchor placed BEFORE the section:
            # the `<section class="sec"><h2><span class="num">` shape is what
            # the print CSS, the tests and the PDF renderer key on, and an
            # attribute on either tag would change it.
            out.append(f'<span id="s{sec_no}" class="anchor"></span>'
                       f'<section class="sec"><h2><span class="num">{sec_no:02d}</span>{heading}</h2>{body}</section>')

    # ---------------- summary --------------------------------------------
    crit_open = sum(1 for a in per_alerts if a.sev == "CRIT" and a.status != "resolved")
    warn_open = sum(1 for a in per_alerts if a.sev == "WARN" and a.status != "resolved")
    # Imported here, not at module scope: `scheduler` imports this module,
    # so a top-level import closes a cycle that breaks a COLD
    # `import guardian_hub.main` -- which is how uvicorn starts and is not
    # how pytest does, so the suite cannot see it. See
    # `tests/test_cold_import.py`.
    from ..scheduler import _tenant_offset

    _cov = coverage_days(db, tenant, days=days, offset=_tenant_offset(db, tenant.id))
    up_all = [uptime(s.get("name") or s.get("url") or "")[0] for s in sites]
    up_vals = [u for u in up_all if u is not None]
    up_avg = round(sum(up_vals) / len(up_vals), 2) if up_vals else None
    resolved = [a for a in per_alerts if _resolved_at(a)]
    mttr = (round(sum((S.ago_hours(a.first_seen) or 0) - (S.ago_hours(_resolved_at(a)) or 0) for a in resolved)
                  / len(resolved) * 60) if resolved else None)
    verdict = ("Healthy" if crit_open == 0 and (up_avg or 0) >= 99.5 and not unv_devices
               else ("Attention needed" if crit_open == 0 else "Critical issues open"))
    sc = last.get("score") or {}
    scores = [h.get("score") for h in pts if h.get("score") is not None]
    sc_first = scores[0] if scores else None

    summ = S.kpis([
        ("Security score", f"{sc.get('total', '—')}<small style='font-size:12px;color:#6b7280'> / 100 · grade {sc.get('grade', '—')}</small>",
         "ok" if (sc.get("total") or 0) >= 90 else ("warn" if (sc.get("total") or 0) >= 60 else "bad"),
         f"{sc_first} at start of period → {sc.get('total')} now" if sc_first is not None else f"reachable: {sc.get('potential', '—')}"),
        ("Overall", verdict, "ok" if verdict == "Healthy" else ("warn" if crit_open == 0 else "bad"), f"{len(pts)} scans in {days} days"),
        ("Website uptime", f"{up_avg if up_avg is not None else '—'}%", "ok" if (up_avg or 0) >= 99.5 else "warn", f"{len(sites)} sites"),
        ("Open alerts", f"{crit_open + warn_open}", "bad" if crit_open else ("warn" if warn_open else "ok"),
         f"{crit_open} critical · {warn_open} warnings"),
        ("Auto-fixes", f"{sum(1 for f in fixes if f.ok)}", "ok", f"{sum(1 for f in fixes if not f.ok)} failed"),
        ("Devices online across networks", str(sum(1 for d in devices if d.presence == "ONLINE")), "warn" if unv_devices else "ok",
         f"{len(known)} known · {len(unv_devices)} unverified"),
        ("Mean time to resolve", f"{mttr if mttr is not None else '—'} min", "", f"{len(resolved)} resolved"),
        ("Networks configured", str(len(nets)), "", f"{sum(1 for n in nets if n.sensor_id and n.status == 'active')} active; inspect coverage notes below"),
        # Page one, same weight as every other number. A month the sensor
        # was down produces an uptime figure that is arithmetically correct
        # and completely misleading, and nothing inside the uptime
        # calculation can say so -- see `coverage_days`.
        ("Coverage", coverage_sentence(_cov), "ok" if not _cov["gaps"] else "warn",
         "days Vault was watching"),
    ])
    notes = []
    if crit_open:
        notes.append(f"{crit_open} critical alert(s) are still open and need a decision.")
    for n in nets:
        if n.status == "unreachable":
            notes.append(f"Vault could not reach the {n.name} network during this period.")
        elif n.status == "no-sensor" and len(nets) > 1:
            notes.append(f"{n.name} needs a sensor on this network.")
    if unv_devices:
        names = ", ".join((d.label or d.name or d.vendor or d.mac) for d in unv_devices[:4])
        notes.append(f"{len(unv_devices)} device(s) on the Wi-Fi have not been verified: {names}.")
    for s in sites:
        name = s.get("name") or s.get("url") or ""
        u, _n = uptime(name)
        if u is not None and u < 99.5:
            notes.append(f"{name.split(' (')[0]} uptime was {u}% ({incident_count(name)} incident(s)).")
    if not notes:
        notes.append("No critical issues, all monitored websites stayed up, and every Wi-Fi device is accounted for.")
    fx_l: dict[str, int] = {}
    for f in fixes:
        label = f.label or f.action
        fx_l[label] = fx_l.get(label, 0) + 1
    if fx_l:
        top = ", ".join(f"{k} ×{v}" for k, v in sorted(fx_l.items(), key=lambda x: -x[1])[:5])
        notes.append(f"Vault fixed things on its own: {top}.")
    ded = "".join(
        f"<tr><td>{S.esc(x.get('category'))}</td><td class='muted'>{S.esc(x.get('why'))}</td><td><b>−{x.get('points')}</b></td></tr>"
        for x in sc.get("deductions") or []
    )
    # The page-one read, in the order an executive wants it: one sentence of
    # verdict, then what is waiting on a decision, then what was handled
    # without one. The figures come after; a reader who stops at the first
    # panel has the answer.
    open_now = sorted([a for a in per_alerts if a.status != "resolved" and a.sev in ("CRIT", "WARN")],
                      key=lambda a: (0 if a.sev == "CRIT" else 1, a.first_seen or ""))
    if verdict == "Healthy":
        lede = (f"Over the last {days} days nothing critical was open, every monitored website stayed up, "
                f"and every device on the network is accounted for.")
    elif crit_open:
        lede = (f"{crit_open} critical {'issue is' if crit_open == 1 else 'issues are'} open and "
                f"{'needs' if crit_open == 1 else 'need'} a decision; the rest of the estate is steady.")
    else:
        lede = (f"Nothing critical is open. {warn_open} {'item needs' if warn_open == 1 else 'items need'} attention"
                f"{', and ' + str(len(unv_devices)) + ' device(s) on the Wi-Fi are unverified' if unv_devices else ''}.")
    decide_html = ("".join(
        f"<li><span class='pill {'bad' if a.sev == 'CRIT' else 'warn'}'>{S.esc(a.sev)}</span> {S.esc(a.msg)}"
        f"<span class='muted'> · since {S.esc((a.first_seen or '')[5:16].replace('T', ' '))}</span></li>"
        for a in open_now[:6])
        + (f"<li class='muted'>and {len(open_now) - 6} more, listed under Alerts &amp; incidents.</li>" if len(open_now) > 6 else ""))
    handled_html = "".join(
        f"<li>{S.esc(k)} <b>×{v}</b></li>" for k, v in sorted(fx_l.items(), key=lambda x: -x[1])[:6])
    panels = (
        f"<p class='lede'>{S.esc(lede)}</p>"
        f"<div class='panels'>"
        f"<div class='panel decide'><h3>Needs a decision <span class='count'>{len(open_now)}</span></h3>"
        + (f"<ul class='plain'>{decide_html}</ul>" if open_now else "<p class='muted'>Nothing is waiting on you.</p>")
        + f"</div><div class='panel handled'><h3>Handled without being asked <span class='count'>{sum(1 for f in fixes if f.ok)}</span></h3>"
        + (f"<ul class='plain'>{handled_html}</ul>" if handled_html else f"<p class='muted'>Nothing needed a repair in the period.</p>")
        + "</div></div>"
    )
    # Only incidents that say something: one with no entity, no alerts and no
    # story prints as "· WARN · 0 alerts · since 09-14", a row of punctuation
    # the reader cannot act on. Sixteen of those on page one is what the
    # owner meant by "too much information all over the place".
    telling = [i for i in incidents if (i.entity or "").strip() or (i.alert_keys or []) or (i.story or "").strip()]
    inc_html = "".join(
        f"<div style='border-left:4px solid {'#e02424' if i.sev=='CRIT' else '#e0b000'};padding:6px 10px;margin:6px 0'>"
        f"<b>{S.esc(i.entity or (i.story or '')[:60] or 'Incident')}</b> <span class='muted'>· {S.esc(i.sev)} · "
        f"{len(i.alert_keys or [])} alert{'s' if len(i.alert_keys or []) != 1 else ''} · "
        f"since {S.esc((i.opened or '')[5:16].replace('T', ' '))}</span>"
        f"{('<div class=muted>' + S.esc((i.story or '')[:200]) + '</div>') if i.entity and i.story else ''}</div>"
        for i in telling[:8]
    ) + (f"<p class='muted'>and {len(telling) - 8} more open incidents, listed under Alerts &amp; incidents.</p>" if len(telling) > 8 else "")
    trend = S.svg_line([{"name": "security score", "pts": [(tsx(h["ts"]), h["score"]) for h in pts if h.get("score") is not None]}],
                        ymax=100) if len(scores) > 1 else ""
    section("summary", S.SECTION_TITLES["summary"],
            panels + summ + "<ul class='notes'>" + "".join(f"<li>{S.esc(n)}</li>" for n in notes) + "</ul>"
            + (f"<h3>Score deductions</h3><table class='small'><tr><th>Area</th><th>Why</th><th>Points</th></tr>{ded}</table>" if ded else "")
            + (f"<h3>Open incidents</h3>{inc_html}" if telling else "")
            + (f"<h3>Security score over the period</h3>{trend}" if trend else ""))

    # ---------------- availability ---------------------------------------
    rows = []
    for s in sites:
        name = s.get("name") or s.get("url") or ""
        u, _n = uptime(name)
        lat = [h["sites"][name].get("lat") for h in pts if name in (h.get("sites") or {}) and h["sites"][name].get("lat") is not None]
        rows.append([
            f"<b>{S.esc(name.split(' (')[0])}</b><div class='muted'>{S.esc(s.get('url'))}</div>",
            S.pill(s.get("ok"), "up" if s.get("ok") else "DOWN"),
            f"{u if u is not None else '—'}%", str(incident_count(name)),
            f"{round(sum(lat)/len(lat),2) if lat else '—'}s / {max(lat) if lat else '—'}s",
            f"{s.get('cert_days_left','—')} d",
            S.heartbeat_row([h["sites"][name].get("ok") for h in pts[-60:] if name in (h.get("sites") or {})]),
        ])
    latser = [{"name": (s.get("name") or "").split(" (")[0],
               "pts": [(tsx(h["ts"]), h["sites"][s.get("name") or ""].get("lat"))
                       for h in pts if (s.get("name") or "") in (h.get("sites") or {})]} for s in sites]
    section("availability", S.SECTION_TITLES["availability"],
            S.table(["Site", "Now", f"Uptime {days}d", "Incidents", "Latency avg / max", "Certificate", "Last 60 checks"], rows)
            + "<h3>Response time</h3>" + S.svg_line(latser, fmt=lambda v: f"{v:.2f}s"))

    # ---------------- servers ----------------------------------------------
    rows = []
    for h in hosts:
        if not h.get("reachable"):
            rows.append([f"<b>{S.esc(h.get('name'))}</b>", S.pill(False, "unreachable"), "", "", "", "", "", ""])
            continue
        s = h.get("sec") or {}
        rows.append([
            f"<b>{S.esc(h.get('name'))}</b><div class='muted'>{S.esc(h.get('hostname'))} · up {S.esc(h.get('uptime'))}</div>",
            S.pill(True, "online"), f"{h.get('disk_pct')}%", f"{h.get('mem_pct')}%", f"{h.get('load_per_core')}",
            S.pill(not s.get("ssh_password"), "key-only" if not s.get("ssh_password") else "password"),
            S.pill(s.get("ufw") == "active", s.get("ufw") or "?"),
            f"{h.get('failed_ssh_24h')} <span class='muted'>({h.get('banned') or 0} banned)</span>",
        ])
    # Hardening checklist reuses the Assets payload's own scorecard
    # (`payloads.assets.HARDENING_CHECKS` / `hardening()`) instead of
    # re-deriving the same seven checks locally, so a report's grade for a
    # host is always identical to what the Servers tab shows for it.
    posture = []
    for h in [x for x in hosts if x.get("reachable")]:
        grade = hardening(h)
        posture.append([f"<b>{S.esc(h.get('name'))}</b>"]
                        + [S.pill(c.passed, "pass" if c.passed else "fail") for c in grade.checks]
                        + [f"<b>{grade.score}</b>", f"<b>{grade.grade}</b>"])
    fails = [{"name": h.get("name"), "pts": [(tsx(p["ts"]), (p["hosts"].get(h.get("name")) or {}).get("failed")) for p in pts if p.get("hosts")]}
             for h in hosts]
    section("servers", S.SECTION_TITLES["servers"],
            S.table(["Host", "Status", "Disk", "Memory", "Load/core", "SSH login", "Firewall", "Failed logins 24h"], rows)
            + "<h3>Hardening scorecard</h3>"
            + S.table(["Host"] + [c for c, _ in HARDENING_CHECKS] + ["Score", "Grade"], posture, "small")
            + "<h3>Failed SSH logins (brute force pressure)</h3>" + S.svg_line(fails))

    # ---------------- updates ------------------------------------------------
    rows = []
    for h in [x for x in hosts if x.get("reachable")]:
        u = h.get("updates") or {}
        s = h.get("sec") or {}
        rows.append([
            f"<b>{S.esc(h.get('name'))}</b><div class='muted'>{S.esc(u.get('os'))}</div>",
            f"{S.esc(u.get('kernel'))}" + (f"<div class='muted'>installed {S.esc(u.get('kernel_latest'))}</div>"
                                            if u.get("kernel_latest") and u.get("kernel_latest") != u.get("kernel") else ""),
            str(u.get("count", h.get("pending_updates"))), str(len(s.get("security_updates") or [])),
            str(h.get("cve_fixable")) if h.get("cve_fixable", -1) >= 0 else "—",
            S.pill(s.get("unattended"), "on" if s.get("unattended") else "off"),
            S.pill(not h.get("reboot_required"), "no" if not h.get("reboot_required") else "NEEDED"),
            S.esc(u.get("last_apt") or "—"),
        ])
    upd_fx = [f for f in fixes if f.action in ("security_updates", "full_upgrade", "enable_unattended", "install_debsecan")]
    section("updates", S.SECTION_TITLES["updates"],
            S.table(["Host", "Kernel", "Pending packages", "Security", "Fixable CVEs", "Auto-updates", "Reboot", "Last apt run"], rows)
            + f"<p>{len(upd_fx)} patch actions ran automatically in the period ({sum(1 for f in upd_fx if f.ok)} succeeded).</p>")

    # ---------------- home ---------------------------------------------------
    w = home.get("wifi") or {}
    ex = home.get("exposure") or {}
    rg = last.get("rogue") or {}
    wp = last.get("wan_path") or {}
    cfg = tenant.config or {}
    hk = S.kpis([
        ("Wi-Fi security", S.esc(w.get("security") or "—"), "ok" if "WPA" in (w.get("security") or "") else "bad", S.esc(w.get("ssid"))),
        ("Exposed to internet", f"{len(ex.get('open_ports') or [])} ports", "bad" if ex.get("open_ports") else "ok",
         f"{len(ex.get('checked') or [])} probed"),
        ("Unverified devices", str(len(unv_devices)), "warn" if unv_devices else "ok", ""),
        ("Router MAC", S.esc((last.get("state") or {}).get("gw_mac") or home.get("gateway") or "—"), "", "changes are alerted"),
        ("DHCP server", S.esc(rg.get("dhcp_server") or "—"),
         "ok" if rg.get("dhcp_server") == (cfg.get("lan") or {}).get("expected_gateway") else "warn", "must be the router"),
        ("Joins / leaves", f"{sum(1 for e in events if e.type=='join')} / {sum(1 for e in events if e.type=='leave')}", "",
         f"{sum(1 for e in events if e.type=='port_open')} new services"),
    ])
    unv_rows = [[S.esc(d.name or d.label or "—"), S.esc(d.ip), S.esc(d.vendor), S.esc(d.mac), S.esc((d.first_seen or "")[:16].replace("T", " "))]
                for d in unv_devices]
    section("home", S.SECTION_TITLES["home"],
            hk + ("<h3>Unverified devices</h3>" + S.table(["Name", "IP", "Vendor", "MAC", "First seen"], unv_rows)
                  if unv_devices else "<p>Every device on the Wi-Fi is labelled or trusted.</p>")
            + "<h3>Internet path health</h3>"
            + S.table(["Anchor", "IP", "Latency", "Jitter", "Loss"],
                      [[S.esc(k), S.esc(v.get("ip")), f"{v.get('avg_ms')} ms", f"{v.get('jitter_ms')} ms", f"{v.get('loss_pct')}%"]
                       for k, v in wp.items()]))

    # ---------------- devices + device-group sections -----------------------
    live = {mac for mac in ((last.get("lan") or {}).get("devices") or {}).values() if mac}
    npts = len(pts) or 1
    device_rows = sorted(known.values(), key=lambda d: -(d.risk or 0))

    def _presence_pct(mac: str) -> float:
        return S.pct(sum(1 for h in pts if mac in (h.get("live_macs") or [])), npts) or 0

    # A tenant with 0 or 1 networks renders the legacy, un-prefixed body
    # (`network=None`) -- byte-for-byte what these two blocks produced
    # before networks existed. With 2+ networks, one block per network
    # (primary first, then by name) is concatenated under the same `<h2>`.
    if single_network:
        section("devices", S.SECTION_TITLES["devices"], S.devices(device_rows, unv_macs, live, network=None))
    else:
        section("devices", S.SECTION_TITLES["devices"], "".join(
            S.devices([d for d in device_rows if d.network_id == n.id], unv_macs, live, network=n)
            for n in nets
        ))

    for skey, stitle, group_spec in S.DEVICE_SECTIONS:
        want = set(group_spec.split("|"))
        grp_rows = [d for d in device_rows if group_of(kind_of(d)) in want]
        # Always render (even with zero devices in the group) so every
        # section named in SECTION_TITLES/SECTION_KEYS shows up in a "full"
        # report -- unlike a KPI-derived section, an empty device group is
        # still meaningful information ("you have no cameras").
        if single_network:
            body = S.dev_group(stitle, grp_rows, unv_macs, live, _presence_pct, network=None)
        else:
            parts = []
            for n in nets:
                rows_n = [d for d in grp_rows if d.network_id == n.id]
                net_pcts = {r.mac: r.pct for r in net_screen[n.id]}
                parts.append(S.dev_group(stitle, rows_n, unv_macs, live,
                                          lambda mac, _p=net_pcts: _p.get(mac, 0), network=n))
            body = "".join(parts)
        section(skey, stitle, body)

    # ---------------- presence ------------------------------------------------
    # "Devices online over time" stays a single tenant-wide chart either way
    # -- only the screen-time table below it splits per network.
    lanser = [{"name": "devices online", "pts": [(tsx(h["ts"]), h.get("lan")) for h in pts if h.get("lan") is not None]}]
    if single_network:
        ppts = [h for h in pts if h.get("live_macs")]
        per: dict[str, int] = {}
        for h in ppts:
            for mac in h["live_macs"]:
                per[mac] = per.get(mac, 0) + 1
        prows = [[
            f"<b>{S.esc((known.get(mac).label or known.get(mac).name or known.get(mac).vendor or mac) if known.get(mac) else mac)}</b>",
            # Legacy (single-network) presence %: a plain history-line
            # fraction -- scans this mac appeared in, over total scan lines.
            S.esc(known.get(mac).ip if known.get(mac) else ""), f"{round(c*5/60,1)} h", f"{S.pct(c, len(ppts) or 1)}%",
            S.heartbeat_row([mac in (h.get("live_macs") or []) for h in ppts[-60:]]),
        ] for mac, c in sorted(per.items(), key=lambda x: -x[1])[:25]]
        pres_body = S.table(["Device", "IP", "Time online", "Presence", "Last 60 scans"], prows)
    else:
        # Split (per-network) presence %: `r.pct` comes from
        # `payloads.presence.screen_time(net=n)` -- samples-for-this-network
        # over distinct-ts-for-this-network -- not the legacy history-line
        # fraction above. The two agree for a single-network tenant but can
        # diverge for a multi-homed device split across networks.
        net_hb = {n.id: [h for h in network_presence_lines(db, tenant.id, n.id, cut) if h.get("live_macs")]
                  for n in nets}
        pres_body = "".join(S.presence(n, net_screen[n.id], net_hb[n.id]) for n in nets)
    section("presence", S.SECTION_TITLES["presence"],
            pres_body + "<h3>Devices online over time</h3>" + S.svg_line(lanser))

    # ---------------- alerts -----------------------------------------------
    byday: dict[str, int] = {}
    for a in per_alerts:
        byday[(a.first_seen or "")[:10]] = byday.get((a.first_seen or "")[:10], 0) + 1
    sev_order = {"CRIT": 0, "WARN": 1, "INFO": 2}
    # The log lists what a reader has to act on or account for -- everything
    # still open, and everything that was critical -- and counts the rest.
    # Forty rows of warnings that opened and cleared themselves within the
    # hour are a scan log, not a report; the count says the same thing in
    # one line, and the dashboard's Inbox has every row.
    listed = [a for a in per_alerts if a.status != "resolved" or a.sev == "CRIT"]
    quiet = [a for a in per_alerts if a not in listed]
    arows = [[
        S.pill(a.sev != "CRIT", a.sev, "bad"), S.esc(a.status), S.esc(a.msg),
        S.esc((a.first_seen or "")[:16].replace("T", " ")), S.esc((_resolved_at(a) or "—")[:16].replace("T", " ")),
        S.esc(a.resolved_by or ""), str(a.count),
    ] for a in sorted(listed, key=lambda a: (sev_order.get(a.sev, 3), a.first_seen or ""))]
    quiet_line = ""
    if quiet:
        qw = sum(1 for a in quiet if a.sev == "WARN"); qi = len(quiet) - qw
        quiet_line = (f"<p class='muted'>{len(quiet)} further alert(s) opened and cleared on their own within the period "
                      f"({qw} warnings, {qi} notices); they are in the dashboard's alert history.</p>")
    ak = S.kpis([
        ("Alerts raised", str(len(per_alerts)), "", ""),
        ("Critical", str(sum(1 for a in per_alerts if a.sev == "CRIT")), "bad" if any(a.sev == "CRIT" for a in per_alerts) else "ok", ""),
        ("Resolved", str(len(resolved)), "ok", f"{sum(1 for a in resolved if (a.resolved_by or '').startswith('fix'))} by Vault"),
        ("Still open", str(sum(1 for a in per_alerts if a.status != "resolved")), "warn" if any(a.status != "resolved" for a in per_alerts) else "ok", ""),
        ("Acknowledged", str(sum(1 for a in per_alerts if a.status in ("acked", "snoozed"))), "", ""),
        ("MTTR", f"{mttr if mttr is not None else '—'} min", "", ""),
    ])
    section("alerts", S.SECTION_TITLES["alerts"],
            ak + "<h3>Alerts per day</h3>" + S.svg_bars(sorted((k[5:], v) for k, v in byday.items() if k))
            + "<h3>Open and critical alerts</h3>" + S.table(["Sev", "State", "Alert", "Opened", "Resolved", "By", "Seen"], arows, "small")
            + quiet_line)

    # ---------------- fixes --------------------------------------------------
    # One row per (host, action): how many times, when last, how many failed.
    # A hundred and five rows of "full apt upgrade · ok" is the same fact as
    # "70 times, all ok, last on the 11th", and the second one can be read.
    grouped: dict[tuple[str, str], dict] = {}
    for f in sorted(fixes, key=lambda f: f.ts, reverse=True):
        g = grouped.setdefault((f.host or "", f.label or f.action or ""), {"n": 0, "bad": 0, "last": f.ts, "by": set()})
        g["n"] += 1
        if not f.ok:
            g["bad"] += 1
        g["by"].add(f.by or "auto")
    frows = [[
        S.esc(host), S.esc(label), f"<b>{g['n']}</b>",
        S.pill(g["bad"] == 0, "all ok" if g["bad"] == 0 else f"{g['bad']} failed"),
        S.esc(g["last"][:16].replace("T", " ")), S.esc(", ".join(sorted(g["by"]))),
    ] for (host, label), g in sorted(grouped.items(), key=lambda kv: -kv[1]["n"])]
    failed_rows = [[
        S.esc(f.ts[:16].replace("T", " ")), S.esc(f.host), S.esc(f.label or f.action), S.esc(f.by or "auto"), S.esc((f.arg or "")[:60]),
    ] for f in sorted((x for x in fixes if not x.ok), key=lambda f: f.ts, reverse=True)]
    section("fixes", S.SECTION_TITLES["fixes"],
            S.kpis([
                ("Actions", str(len(fixes)), "", ""), ("Succeeded", str(sum(1 for f in fixes if f.ok)), "ok", ""),
                ("Failed", str(sum(1 for f in fixes if not f.ok)), "bad" if any(not f.ok for f in fixes) else "ok", ""),
                ("Triggered by you", str(sum(1 for f in fixes if f.by == "dashboard")), "", ""),
            ]) + S.svg_bars(sorted(fx_l.items(), key=lambda x: -x[1])[:8])
            + "<h3>By host and action</h3>" + S.table(["Host", "Action", "Runs", "Result", "Last run", "By"], frows, "small")
            + (("<h3>Runs that failed</h3>" + S.table(["When", "Host", "Action", "By", "Detail"], failed_rows, "small")) if failed_rows else ""))

    # ---------------- internet ------------------------------------------------
    sp = [h["speed"] for h in spts]
    dns_vals = [h["dns_ms"] for h in pts if h.get("dns_ms")]
    ik = S.kpis([
        ("Download avg", f"{round(sum(s['down'] for s in sp)/len(sp),1) if sp else '—'} Mbps", "",
         f"min {min(s['down'] for s in sp) if sp else '—'} · max {max(s['down'] for s in sp) if sp else '—'}"),
        ("Upload avg", f"{round(sum(s['up'] for s in sp)/len(sp),1) if sp else '—'} Mbps", "", ""),
        ("Latency avg", "—", "", ""),
        ("DNS (system) avg", f"{round(sum(dns_vals)/len(dns_vals)) if dns_vals else '—'} ms", "", f"{len(dns_vals)} samples"),
        ("Speed tests", str(len(sp)), "", ""),
        ("WAN IP", S.esc(last.get("wan_ip") or "—"), "", ""),
    ])
    spser = [{"name": "down Mbps", "pts": [(tsx(h["ts"]), h["speed"]["down"]) for h in spts]},
             {"name": "up Mbps", "pts": [(tsx(h["ts"]), h["speed"]["up"]) for h in spts], "color": "#5aa9ff"}]
    section("internet", S.SECTION_TITLES["internet"],
            ik + "<h3>Speed over time</h3>" + S.svg_line(spser) + "<h3>Path health now</h3>"
            + S.table(["Anchor", "Latency", "Jitter", "Loss"],
                      [[S.esc(k), f"{v.get('avg_ms')} ms", f"{v.get('jitter_ms')} ms", f"{v.get('loss_pct')}%"] for k, v in wp.items()]))

    # ---------------- email/domains -------------------------------------------
    es = last.get("email_sec") or {}
    doms = last.get("domains") or {}
    erows = [[
        f"<b>{S.esc(d)}</b>", S.esc((doms.get(d) or {}).get("expires") or "—"), S.esc((doms.get(d) or {}).get("days_left")),
        S.pill(bool(v.get("spf")), "yes" if v.get("spf") else "missing"),
        S.pill(bool(v.get("dkim_selector")), v.get("dkim_selector") or "not found", "muted"),
        S.pill(bool(v.get("dmarc")), "yes" if v.get("dmarc") else "missing"),
        S.esc(v.get("dmarc_policy") or "—") + (" (reported, monitor mode)" if v.get("dmarc_policy") == "none" else ""),
        "yes" if v.get("has_mx") else "no",
    ] for d, v in es.items()]
    exr = [[f"<b>{S.esc(h)}</b>", S.esc(v.get("ip")), ", ".join(map(str, v.get("open_ports") or [])) or "none"]
           for h, v in (last.get("external_sites") or {}).items()]
    section("email", S.SECTION_TITLES["email"],
            S.table(["Domain", "Expires", "Days left", "SPF", "DKIM", "DMARC", "Policy", "Sends mail"], erows)
            + "<h3>Ports visible from the internet per host</h3>" + S.table(["Host", "IP", "Open ports"], exr))

    # ---------------- compliance evidence pack ---------------------------------
    # The mapped view: every control an assessor for each framework will ask
    # about, answered from this scan, WITH the ones Vault cannot answer.
    # Those gaps are the point -- a pack listing only what it can evidence
    # looks complete, and an MSP caught over-claiming to an underwriter never
    # opens the document again. See `guardian_hub/controls.py`.
    pack_rows = controls.pack(last)
    fw_summary = "".join(
        "<tr><td><b>{}</b></td><td>{}</td><td>{}</td><td>{}</td><td class='muted'>{}</td></tr>".format(
            S.esc(name), t[controls.Scope.EVIDENCED], t[controls.Scope.PARTIAL],
            t["unmeasured"], t[controls.Scope.OUTSIDE])
        for fw, name in controls.FRAMEWORKS.items()
        for t in [controls.tally(controls.pack(last, fw))]
    )
    _pill = {controls.Scope.EVIDENCED: ("Evidenced", "ok"),
             controls.Scope.PARTIAL: ("Partial", "muted"),
             controls.Scope.OUTSIDE: ("Outside scope", "muted"),
             "unmeasured": ("Not measured", "muted")}
    pack_detail = "".join(
        "<tr><td><b>{}</b><div class='muted'>{}</div></td><td>{}</td>"
        "<td class='muted'>{}</td><td class='muted'>{}</td></tr>".format(
            S.esc(r["title"]), S.esc(r["question"]),
            S.pill(r["status"] == controls.Scope.EVIDENCED, *_pill[r["status"]]),
            S.esc(r["note"]), S.esc("; ".join(r["references"])))
        for r in pack_rows
    )
    section("evidence_pack", S.SECTION_TITLES["evidence_pack"],
            f"<p>What Vault can evidence against each framework, and what it cannot. "
            f"The &ldquo;outside scope&rdquo; rows are listed deliberately: a pack that omits "
            f"the questions it cannot answer looks complete.</p>"
            f"<table><thead><tr><th>Framework</th><th>Evidenced</th><th>Partial</th>"
            f"<th>Not measured</th><th>Outside scope</th></tr></thead>"
            f"<tbody>{fw_summary}</tbody></table>"
            f"<h3>Every control</h3>"
            f"<table><thead><tr><th>Control</th><th>Status</th><th>What Vault holds</th>"
            f"<th>Referenced as</th></tr></thead><tbody>{pack_detail}</tbody></table>"
            f"<p class='muted' style='font-size:11px'>{S.esc(controls.DISCLAIMER)}</p>")

    # ---------------- compliance -----------------------------------------------
    crows = compliance_mod.rows(last)
    met = sum(1 for r in crows if r["met"])
    part = sum(1 for r in crows if r["met"] is None)
    crows_html = "".join(
        f"<tr><td><b>{S.esc(r['area'])}</b></td><td class='muted'>{S.esc(r['control'])}</td>"
        f"<td>{S.pill(r['met'], 'Met' if r['met'] else ('n/a' if r['met'] is None else 'Gap'), 'muted' if r['met'] is None else 'bad')}</td></tr>"
        for r in crows
    )
    section("compliance", S.SECTION_TITLES["compliance"],
            f"<p>These are the controls a cyber-insurance questionnaire or a client security review usually asks about, "
            f"answered from live evidence rather than a guess. <b>{met} of {len(crows)} met</b>"
            f"{f', {part} not applicable' if part else ''}.</p>"
            f"<table><thead><tr><th>Control area</th><th>What it means</th><th>Status</th></tr></thead>"
            f"<tbody>{crows_html}</tbody></table>"
            f"<p class='muted' style='font-size:11px'>Not a certification. Evidence for an assessor; map to Cyber Essentials, "
            f"SOC 2 CC-series or ISO 27001 Annex A as needed.</p>")

    # ---------------- recommendations ------------------------------------------
    recs = [(r.priority.title() if r.priority else "Low", r.title + (f" — {r.why}" if r.why else "")) for r in recommendations if r.status != "done"]
    if not recs:
        for h in [x for x in hosts if x.get("reachable")]:
            s = h.get("sec") or {}
            if h.get("reboot_required"):
                recs.append(("High", f"Reboot {h['name']} to activate the updated kernel (button on the Updates tab)."))
            if s.get("ssh_password"):
                recs.append(("High", f"{h['name']}: SSH still accepts passwords; Vault will switch it to key-only automatically."))
            if s.get("ufw") != "active":
                recs.append(("High", f"{h['name']}: firewall is off; Vault enables it automatically."))
            if h.get("disk_pct", 0) >= 80:
                recs.append(("Medium", f"{h['name']}: disk {h['disk_pct']}% full."))
    if unv_devices:
        recs.append(("High", f"Verify {len(unv_devices)} unknown Wi-Fi device(s): press 'mine, trust' on the ones you own, "
                              f"block the rest in the router and change the Wi-Fi password."))
    if ex.get("open_ports"):
        recs.append(("Critical", f"Close port forwarding / UPnP on the router: ports {ex['open_ports']} are reachable from the internet."))
    if w.get("security") and "WPA" not in w["security"]:
        recs.append(("Critical", "Switch the router to WPA2/WPA3 Personal; the current Wi-Fi security is weak."))
    for d, v in es.items():
        if v.get("has_mx") and v.get("dmarc_policy") == "none":
            recs.append(("Low", f"{d}: DMARC is in monitor mode (p=none) — kept as a reported item per your choice."))
        if not v.get("has_mx") and not v.get("spf"):
            recs.append(("Low", f"{d} sends no mail: publish 'v=spf1 -all' and a DMARC reject record so nobody can spoof it."))
    for a in per_alerts:
        if a.status == "open" and a.sev == "CRIT":
            recs.append(("Critical", f"Open critical alert: {a.msg}"))
    recs.append(("Low", "Use a guest Wi-Fi for visitors and smart-home gear so they cannot see your laptops."))
    # Standing advice, minus the half Vault has already done. Once the hub
    # holds a record of its own disable-remote-admin run on this tenant's
    # gateway, a report that still asks for it is the app requesting work it
    # carried out itself. The password half stands either way: Vault never
    # changes that.
    _router = db.query(m.Router).filter_by(tenant_id=tenant.id).one_or_none()
    if _router is not None and _router.remote_admin_off_at:
        recs.append(("Low", "Change the router admin password from the default. "
                            "Remote administration is already off, turned off by Vault."))
    else:
        recs.append(("Low", "Change the router admin password from the default and disable remote administration."))
    pri_order = {"Critical": 0, "High": 1, "Medium": 2, "Low": 3}
    # Once each. The same advice arrives from two sources -- a stored
    # recommendation row and the standing heuristics above -- worded almost
    # identically, and a list that says "verify 4 unknown devices" twice in a
    # row reads as unedited. The first occurrence (highest priority) wins.
    seen_recs: set[str] = set()
    unique: list[tuple[str, str]] = []
    for p, a in sorted(recs, key=lambda r: pri_order.get(r[0], 3)):
        key_ = "".join(ch for ch in a.lower() if ch.isalnum())[:48]
        if key_ in seen_recs:
            continue
        seen_recs.add(key_)
        unique.append((p, a))
    section("recommendations", S.SECTION_TITLES["recommendations"],
            S.table(["Priority", "Action"],
                    [[S.pill(p == "Low", p, "bad" if p == "Critical" else ("warn" if p in ("High", "Medium") else "ok")), S.esc(a)]
                     for p, a in unique]))

    # ---------------- cover / chrome --------------------------------------------
    owner, domain, who, brand = _report_identity(db, tenant, sites)
    ttl = title or "Infrastructure & Home Network Report"

    period_from = (now - dt.timedelta(days=days)).strftime("%B %d, %Y")
    period_to = now.strftime("%B %d, %Y")
    contents = "".join(
        f'<li><a href="#s{n}"><span class="toc-n">{num}</span><span class="toc-t">{title}</span></a></li>'
        for num, title, n in toc
    )
    html = (
        f'<!doctype html><html><head><meta charset="utf-8">'
        f'<meta name="viewport" content="width=device-width,initial-scale=1"><title>{S.esc(ttl)}</title>'
        f'<style>{REPORT_CSS}</style></head><body>'
        + (f'<div class="toolbar"><button onclick="print()">Print / save as PDF</button>'
           f'<span class="muted">{len(secs)} sections · {days}-day period</span></div>'
           if toolbar else
           f'<div class="toolbar"><span class="muted">{len(secs)} sections · {days}-day period</span></div>')
        # The cover is a title page: what this is, whose it is, the period,
        # who prepared it, and its classification -- the five facts a
        # document needs before anyone reads a number in it.
        + f'<div class="page"><div class="cover">'
        f'<div class="brand"><b>{S.esc(brand["name"].upper())}</b><span>Network security report</span></div>'
        f'<h1>{S.esc(ttl)}</h1>'
        f'<dl class="cover-meta">'
        f'<div><dt>Prepared for</dt><dd>{who}</dd></div>'
        f'<div><dt>Period</dt><dd>{period_from} – {period_to} <span class="muted">({days} days · {len(pts)} scans)</span></dd></div>'
        f'<div><dt>Network</dt><dd>{S.esc(domain) or S.esc(tenant.name)}</dd></div>'
        f'<div><dt>Prepared by</dt><dd>{S.esc(brand["name"])} · {now.strftime("%B %d, %Y")}</dd></div>'
        f'<div><dt>Classification</dt><dd>Confidential — contains network details</dd></div>'
        f'</dl></div>'
        + (f'<nav class="toc"><h2>Contents</h2><ol>{contents}</ol></nav>' if len(toc) > 3 else "")
        + "".join(out)
        + f'<div class="foot"><span>Generated by {S.esc(brand["name"])} on {now.strftime("%Y-%m-%d %H:%M")}. '
        f'Data: {len(pts)} scans, {len(per_alerts)} alerts, {len(fixes)} actions, {len(known)} devices.</span>'
        f'<span>Confidential — contains network details</span></div></div></body></html>'
    )
    return html


# --------------------------------------------------------------------------
# Executive report (design/4-executive-report.html)
# --------------------------------------------------------------------------

# The sensor's fixed deduction categories (`sensor/guardian_sensor/alerts.py`
# `security_score`'s ten `d(cat, ...)` calls -- that function is the only
# place a `category` string is minted, so this dict is complete as long as
# that enum is). Each `why` string the sensor writes already carries the
# tenant-specific figures (a host name, a count); the two strings below add
# only what is true of the *category itself*, never a fabricated number --
# a short plain-English headline in place of the system term, and one
# sentence naming the *mechanism* that clears it (a category always clears
# the same way, unlike its `why`, which changes every scan). A category the
# scorer starts emitting later and this dict has not caught up with still
# renders correctly: `_deduction_li` falls back to the raw category name and
# drops the "fix" line rather than guessing.
_DEDUCTION_COPY: dict[str, tuple[str, str]] = {
    "Active threats": ("An attacker is active", "Clears once every critical alert is resolved."),
    "Availability": ("Something you rely on is down", "Clears once everything listed here answers again."),
    "Vulnerabilities": ("Unpatched software", "Clears once the pending updates are installed."),
    "Configuration": ("A server's security settings are weak",
                       "Clears once the hardening gaps are closed — Vault applies most of these on its own."),
    "Exposure": ("Something is reachable from the internet",
                 "Clears once nothing configured here is reachable from the internet."),
    "Coverage": ("A device on the Wi-Fi isn't verified", "Clears once every device on the Wi-Fi is verified."),
    "Cameras": ("A camera isn't accounted for", "Clears once every camera is answering and listed."),
    "Behaviour": ("A device is behaving oddly",
                  "Clears once the flagged device stops reaching out to that destination."),
    "Patch status": ("A server is waiting on a restart", "Clears with one scheduled restart of the affected host(s)."),
    "Identity & access": ("Wi-Fi or login security is weak",
                           "Clears once Wi-Fi security is upgraded and any attacking source is blocked."),
}


def _deduction_li(category: str, points: int, why: str) -> str:
    headline, clears = _DEDUCTION_COPY.get(category, (category, ""))
    fix_html = f'<span class="fix">{S.esc(clears)}</span>' if clears else ""
    return (f'<li><span class="ded-amt">&minus;{points}</span>'
            f'<span class="ded-text"><b>{S.esc(headline)}.</b> {S.esc(why)}{fix_html}</span></li>')


def _sparkline(values: list[float], w: int = 86, h: int = 26) -> str:
    """The score card's 7-day trend line -- a hand-scaled inline SVG
    polyline, deliberately tiny (this is a supporting detail next to the
    score, not a chart in its own right; `sections.svg_line` is the report
    engine's real chart primitive, sized for a full-width section)."""
    if len(values) < 2:
        return ""
    lo, hi = min(values), max(values)
    span = (hi - lo) or 1.0
    n = len(values)
    pts = [(round(1 + i / (n - 1) * (w - 2), 1), round(h - 4 - (v - lo) / span * (h - 8), 1))
           for i, v in enumerate(values)]
    path = " ".join(f"{x},{y}" for x, y in pts)
    up = values[-1] >= values[0]
    color = "var(--handled)" if up else "var(--signal)"
    return (f'<svg width="{w}" height="{h}" viewBox="0 0 {w} {h}" role="img" '
            f'aria-label="7-day trend from {values[0]:g} to {values[-1]:g}">'
            f'<polyline points="{path}" fill="none" stroke="{color}" stroke-width="2.2" '
            f'stroke-linecap="round" stroke-linejoin="round"/>'
            f'<circle cx="{pts[0][0]}" cy="{pts[0][1]}" r="2.4" fill="var(--ink-soft)"/>'
            f'<circle cx="{pts[-1][0]}" cy="{pts[-1][1]}" r="2.6" fill="{color}"/></svg>')


def _decision_li(item: "sd.TriageItem") -> str:
    # `<title> — <entity>`, the same suffix convention the Inbox's own
    # triage table uses for its "rest" rows (`InboxTriage.tsx`'s
    # `item.entity && <span> — {item.entity}</span>`) -- not re-derived
    # here, just carried over, so an item reads identically wherever it
    # shows up in the app.
    title_txt = item.title + (f" — {item.entity}" if item.entity else "")
    # `why` is often the same finding `title` already states, once for an
    # incident's roll-up and again in its own words as "what happened"
    # (`payloads.inbox._incident_row`'s `story.what_happened`) -- a report
    # meant to be read start to finish cannot repeat a sentence back to back
    # the way a table row can. Vault's own remedy summary (a different
    # angle: what it can or cannot do about this) is preferred whenever
    # `why` opens with essentially the same words as `title` already does;
    # `why` is kept only when it genuinely adds something.
    why = (item.why or "").strip()
    summary = (item.remedy.summary if item.remedy else "") or ""
    body = why if why and not why[:32].lower() == item.title.strip()[:32].lower() else (summary or why)
    need = item.remedy.need if item.remedy else ""
    # `need` is "exactly what is needed from the owner" (`schemas_dash.
    # AlertRemedy.need`'s own docstring) -- stated as a consequence rather
    # than invented per-item flavour text, since that need is real data and
    # its plain logical complement (it stays undone until met) is not.
    if_html = f'<div class="d-if">If nothing changes: {S.esc(need)}</div>' if need else ""
    return (f'<li><div class="d-title">{S.esc(title_txt)}</div>'
            f'<div class="d-body">{S.esc(body)}</div>{if_html}</li>')


def build_executive_report(db: DbSession, tenant, days: int = 7, title: str | None = None, *,
                            toolbar: bool = True) -> str:
    """The one-page executive report (`design/4-executive-report.html`): an
    opening judgement, a score card with a fixed 7-day trend, the score's
    deductions as the argument, what Vault handled without being asked,
    and what still needs a decision.

    Built on the same payloads the Overview and Inbox tabs already compute
    (`payloads.overview.build`, `payloads.triage.build`) rather than a
    second reading of the same tables, so this report can never disagree
    with what the dashboard says right now -- and rewrites nothing they
    already say correctly, only composes it into a document.

    `days` bounds the report's own period-scoped figures (the masthead's
    "last N days", the fixes counted for "handled"); the score card's trend
    is deliberately always the trailing 7 days regardless of `days` -- see
    the design's own revision note: it is "the one number a reader will ask
    'compared to what?' about," and letting it follow whatever period is
    selected would make the same sparkline mean something different on
    every rebuild.
    """
    # Local import: `payloads.triage` pulls in `remedy` -> `chat.registry` ->
    # `chat.tools` -> `routers.write` -> `payloads.jobs` -> `remedy` again,
    # a cycle that only resolves once every module in it has finished
    # loading. `reports.builder` is imported at process start (by
    # `scheduler.py`, well before `chat`/`routers` exist yet), so importing
    # `payloads.triage` at *this* module's top level would try to close that
    # cycle mid-load and fail with a partially-initialized `remedy`. Deferred
    # to call time, long after start-up has finished, it imports cleanly.
    from ..payloads import triage as triage_payload_mod

    now = _now()
    last = last_of(db, tenant)
    sites = last.get("sites") or []

    ov = overview_payload.build(db, tenant)
    # role="admin": this report is for the account owner, and the point of
    # `AlertRemedy.summary`/`.need` is that their wording already changes by
    # role (`remedy.resolve`) -- "ask me in chat" copy aimed at a lower-role
    # caller has no place in a document addressed to the owner directly.
    triage = triage_payload_mod.build(db, tenant, role="admin")
    dns_status = dnsview_payload.status(last)

    hour_cut = now - dt.timedelta(hours=1)
    scan_ts_rows = (db.query(m.Scan.ts)
                    .filter(m.Scan.tenant_id == tenant.id, m.Scan.ts >= _lax_cutoff(hour_cut))
                    .all())
    scans_last_hour = sum(1 for (ts,) in scan_ts_rows if (when := parse_ts(ts)) is not None and when >= hour_cut)

    # Same filter `triage._handled_count` uses for `outcomes.handled`
    # (by == "auto", ok == True, trailing `HANDLED_WINDOW_DAYS`) -- re-run
    # here, not imported, because the count alone doesn't say *what* Vault
    # did; grouping by label is what turns it into the panel's examples.
    handled_cutoff = now - dt.timedelta(days=triage_payload_mod.HANDLED_WINDOW_DAYS)
    handled_candidates = (
        db.query(m.Fix)
        .filter(m.Fix.tenant_id == tenant.id, m.Fix.by == "auto", m.Fix.ok.is_(True),
                m.Fix.ts >= _lax_cutoff(handled_cutoff))
        .all()
    )
    handled_fixes = [f for f in handled_candidates if (when := parse_ts(f.ts)) is not None and when >= handled_cutoff]
    fix_labels: dict[str, int] = {}
    for f in handled_fixes:
        label = f.label or f.action
        fix_labels[label] = fix_labels.get(label, 0) + 1
    top_fixes = sorted(fix_labels.items(), key=lambda x: -x[1])[:3]

    # ---------------- verdict ----------------------------------------------
    crit_open = ov.alerts.crit
    ded_count = len(ov.deductions)
    if ov.score is None:
        verdict_sentence = "Vault has not finished a first scan for this tenant yet, so there is nothing to score."
    elif crit_open:
        verdict_sentence = (f"{crit_open} critical issue{'s' if crit_open != 1 else ''} open right now need a "
                             f"decision — everything else Vault watches stayed under control this period.")
    elif ded_count:
        verdict_sentence = (f"Everything you rely on stayed under control this period — {ded_count} specific "
                             f"item{'s' if ded_count != 1 else ''} are the only reason the score isn't higher.")
    else:
        verdict_sentence = "Everything you rely on stayed under control this period, and nothing here needs a decision."

    byline_bits = [f"Vault ran {scans_last_hour} scan{'s' if scans_last_hour != 1 else ''} in the last hour"]
    if ov.assets.sites:
        byline_bits[0] += f" across {ov.assets.sites} site{'s' if ov.assets.sites != 1 else ''}"
    if ov.age_s is not None:
        age_min = round(ov.age_s / 60)
        byline_bits.append(f"the most recent scan was {age_min} minute{'s' if age_min != 1 else ''} ago")
    byline = "; ".join(byline_bits) + "."

    # ---------------- score + deductions -----------------------------------
    score_txt = str(ov.score) if ov.score is not None else "—"
    trend_pts = ov.score_trend_7d.points
    if len(trend_pts) >= 2:
        trend_values = [p.value for p in trend_pts]
        trend_svg = _sparkline(trend_values)
        trend_line = f'<span>7-day trend, {trend_values[0]:g} to {trend_values[-1]:g}</span>'
    else:
        trend_svg = ""
        trend_line = '<span class="no-data">not enough history yet for a 7-day trend</span>'
    ded_html = "".join(_deduction_li(d.category, d.points, d.why) for d in ov.deductions)
    ded_body = (f'<ul class="ded-list">{ded_html}</ul>' if ded_html else
                '<p class="no-data">Nothing was deducted this period — the score is exactly what it looks like.</p>')

    # ---------------- handled without asking --------------------------------
    handled_bullets = [
        f"{S.esc(label)} — applied automatically {count} time{'s' if count != 1 else ''} in "
        f"{triage_payload_mod.HANDLED_WINDOW_DAYS} days."
        for label, count in top_fixes
    ]
    if dns_status.blocked_24h:
        handled_bullets.append(
            f"Blocked {dns_status.blocked_24h} outbound request(s) to known-bad destinations in the last "
            f"24 hours before they reached a device."
        )
    coverage = (f"Kept watch across {ov.devices.total} device(s), {ov.assets.servers} server(s) and "
                f"{ov.assets.sites} site(s) throughout the period")
    if scans_last_hour:
        coverage += f" — {scans_last_hour} scan(s) in the last hour"
    if crit_open == 0:
        coverage += ", none requiring escalation"
    handled_bullets.append(coverage + ".")
    handled_list = "".join(f"<li>{b}</li>" for b in handled_bullets)

    # ---------------- needs a decision ---------------------------------------
    decision_items = ([triage.lead] if triage.lead else []) + triage.rest
    decision_items = decision_items[:3]
    decision_html = "".join(_decision_li(i) for i in decision_items)
    remaining = max(0, (triage.outcomes.needs_yes + triage.outcomes.needs_you) - len(decision_items))
    tally_html = (
        f'<div class="decision-tally">The remaining {remaining} pending item(s) follow the same pattern — a fix '
        f'ready to run, or a decision only you can make.</div>' if remaining else ""
    )
    decision_body = (
        (f'<ul class="decision-list">{decision_html}</ul>{tally_html}') if decision_items else
        '<p class="no-data">Nothing here needs a decision right now.</p>'
    )

    # ---------------- footer strip -------------------------------------------
    sensor_chip = ov.sensor.status if ov.sensor else "none"
    strip = (
        f'<span>Assets <b>{ov.assets.total}</b></span>'
        f'<span>Devices online <b>{ov.devices.online}/{ov.devices.total}</b></span>'
        f'<span>Servers <b>{ov.assets.servers}</b></span>'
        f'<span>Sites <b>{ov.assets.sites}</b></span>'
        f'<span>Changes in 24h <b>{ov.changed_24h}</b></span>'
        f'<span>Open incidents <b>{ov.incidents.open}</b></span>'
        f'<span>DNS filter <b>{"on" if dns_status.enabled else "off"}</b></span>'
        f'<span>Sensor <b>{S.esc(sensor_chip)}</b></span>'
    )

    # ---------------- cover / chrome -----------------------------------------
    owner, domain, who, brand = _report_identity(db, tenant, sites)
    ttl = title or "Executive security report"
    period_txt = f"last {days} day{'s' if days != 1 else ''}"

    toolbar_html = (
        f'<div class="toolbar"><button onclick="print()">Print / save as PDF</button>'
        f'<span>{period_txt}</span></div>' if toolbar else
        f'<div class="toolbar"><span>{period_txt}</span></div>'
    )

    html = (
        '<!doctype html><html><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        f'<title>{S.esc(ttl)}</title>{_THEME_BOOT_SCRIPT}<style>{EXEC_REPORT_CSS}</style></head><body>'
        + toolbar_html
        + '<div class="page">'
        + f'<div class="masthead"><div class="brand">{S.esc(brand["name"])}</div>'
        + f'<div class="period">{S.esc(period_txt)}</div></div>'
        + f'<div class="subhead"><h1>{S.esc(ttl)} — {S.esc(owner)}</h1>'
        + f'<div class="meta">Prepared for {who}<br>Produced by Vault'
        + (f' · {S.esc(domain)}' if domain else '') + '</div></div>'
        + f'<div class="verdict"><p>{S.esc(verdict_sentence)}</p><div class="byline">{S.esc(byline)}</div></div>'
        + '<div class="row">'
        + '<div class="card score-card"><span class="label">Security score</span>'
        + f'<div class="score-top"><span class="score-num">{score_txt}</span>'
        + '<span class="score-out">/ 100</span>'
        + f'<span class="score-grade">{S.esc(ov.grade or "—")}</span></div>'
        + f'<div class="trend-line">{trend_svg}{trend_line}</div></div>'
        + '<div class="card ded-card"><span class="label">What kept the score from being higher</span>'
        + ded_body + '</div>'
        + '</div>'
        + '<div class="panel panel-handled"><div class="panel-head">'
        + '<h2>Handled without anyone being asked</h2>'
        + f'<span class="count">{triage.outcomes.handled} item(s), {S.esc(triage.outcomes.handled_period)}</span>'
        + f'</div><ul class="plain-list">{handled_list}</ul></div>'
        + '<div class="panel panel-decision"><div class="panel-head">'
        + '<h2>Needs a decision</h2>'
        + f'<span class="count">{triage.outcomes.needs_yes} need a yes · {triage.outcomes.needs_you} need you '
        + 'directly</span></div>' + decision_body + '</div>'
        + f'<div class="strip">{strip}</div>'
        + f'<div class="footpage"><span>Generated {now.strftime("%Y-%m-%d %H:%M")}'
        + (f' · {S.esc(domain)}' if domain else '') + '</span>'
        + '<span>Confidential — contains network details</span></div>'
        + '</div></body></html>'
    )
    return html


# --------------------------------------------------------------------------
# The vulnerability-scan report (its own document, like the executive one)
# --------------------------------------------------------------------------

#: What a person has to do for a finding Vault cannot clear itself.
_VULN_NEEDS_YOU = {
    "adb": "Turn ADB debugging off on the device (Settings, Developer options).",
    "rdp": "Turn Remote Desktop off on that computer, or accept it if it is managed by an employer.",
    "vnc": "Turn Screen Sharing off on that computer if you do not use it.",
    "smb": "Turn File Sharing off on that computer if you do not use it.",
    "netbios": "Turn File Sharing off on that computer if you do not use it.",
    "msrpc": "Part of Windows file sharing; goes away with it.",
    "telnet": "Disable telnet on the device; use SSH or the web interface.",
    "upnp": "Low risk on the LAN; disable UPnP on the router if you want it gone.",
}
_ACCEPTED_WORDS = ("accepted", "company policy", "expected", "intentional")


def _vuln_scans_in_period(db: DbSession, tenant, since) -> list["m.Queue"]:
    rows = (db.query(m.Queue).filter(m.Queue.tenant_id == tenant.id, m.Queue.path == "/api/vuln-scan",
                                     m.Queue.status == "done", m.Queue.ts >= since)
            .order_by(m.Queue.ts.desc()).all())
    return [q for q in rows if isinstance(q.result, dict) and isinstance(q.result.get("findings"), list)]


def _finding_key(f: dict) -> tuple:
    return (str(f.get("target") or ""), int(f.get("port") or 0), str(f.get("title") or ""))


def _needs_you_text(f: dict) -> str:
    title = (f.get("title") or "").lower()
    for word, text in _VULN_NEEDS_YOU.items():
        if word in title:
            return text
    return "Review this on the device; Vault has no remedy it can run for it."


def vuln_finding_trail(db: DbSession, tenant, q: "m.Queue", f: dict, earlier: list["m.Queue"],
                       devices_by_ip: dict, fix_jobs: list["m.Queue"]) -> dict:
    """The seen -> fixed -> reported story for ONE finding of the latest scan
    of a target. Returns {status, note}: status is one of
    fixed_by_guardian | accepted | needs_you | open."""
    key = _finding_key(f)
    finished = (q.result or {}).get("finished") or ""
    first = finished
    for e in earlier:
        if any(_finding_key(x) == key for x in (e.result or {}).get("findings") or []):
            first = (e.result or {}).get("finished") or first
    seen = f"Seen {first[:16].replace('T', ' ')}" + (f", still there {finished[:16].replace('T', ' ')}" if first != finished else "")
    # A remedy Vault ran for this host after the scan saw it.
    fix = f.get("fix")
    if fix:
        host = str(f.get("target") or q.body.get("target") or "")
        for j in fix_jobs:
            b = j.body or {}
            if b.get("host") == host and b.get("action") == fix and j.finished_at and j.finished_at.isoformat() >= finished:
                return {"status": "fixed_by_guardian",
                        "note": f"{seen}. Fixed by Vault: {fix.replace('_', ' ')} ran {j.finished_at.strftime('%Y-%m-%d %H:%M')} (asked by {j.who})."}
    d = devices_by_ip.get(str(f.get("target") or ""))
    if d is not None:
        label = ((d.label or "") + " " + str((d.extra or {}).get("note") or "")).lower()
        if any(w in label for w in _ACCEPTED_WORDS):
            return {"status": "accepted", "note": f"{seen}. Accepted by you: {d.label}."}
    if fix:
        return {"status": "open", "note": f"{seen}. Vault can fix this ({fix.replace('_', ' ')}) once you say yes."}
    return {"status": "needs_you", "note": f"{seen}. Needs you: {_needs_you_text(f)}"}


def build_threat_report(db: DbSession, tenant, days: int = 7, title: str | None = None, *,
                        toolbar: bool = True) -> str:
    """Threats and protection, on paper: every named attack type, what Vault
    does about it, what it found, and how each finding was settled.

    A separate document rather than a preset of existing sections, for the same
    reason `vuln_report` is: `PRESETS` can only select from `SECTION_KEYS`, and
    there is no threats section to select. The nearest preset would have
    printed "alerts and fixes" under a new name, which is not the same thing.

    It reads `payloads.security.threats` and `threat_detail` -- the same
    builders the Security tab reads -- so the report cannot state a different
    stance from the screen. That matters more here than anywhere else in the
    product: this is the document that goes to an insurer or an auditor, and a
    PDF disagreeing with the dashboard about whether Vault stops
    ransomware is the worst output this codebase could produce.

    Which is also why `limit` is printed rather than trimmed for length. For
    `dos` (effect-only) and `ransomware` (indirect) the honest answer is that
    Vault does NOT stop the attack, and a document listing them under
    "Threats and protection" while omitting that would read as a claim of
    protection nobody made.
    """
    from ..payloads import security as sec_payload

    now = _now()
    last = last_of(db, tenant)
    owner, domain, who, brand = _report_identity(db, tenant, last.get("sites") or [])
    ttl = title or "Threats and protection"
    period_txt = f"last {days} day{'s' if days != 1 else ''}"

    STANCE_WORD = {
        "blocking": "Blocked before it reaches a device",
        "watching": "Watched — Vault detects it and tells you",
        "effect-only": "Damage only — Vault sees the effect, not the attack",
        "indirect": "Odds reduced — no detection, Vault only makes it less likely",
    }

    blocks = []
    total_open = total_settled = 0
    for cls in sec_payload.threats(db, tenant, days=days):
        d = sec_payload.threat_detail(db, tenant, cls.key, days=days)
        if d is None:
            continue
        total_open += d.open_count
        total_settled += d.resolved_count

        rows = []
        for f in d.findings:
            if f.resolved:
                outcome = f"Vault ran {f.fix}" if f.fix else (
                    f"Settled by {f.resolved_by} without a repair" if f.resolved_by
                    else "Settled without a repair")
            else:
                outcome = "Still open"
            rows.append([S.esc(f.severity), S.esc(f.title), S.esc(f.why or ""), S.esc(outcome)])

        body = [f'<p class="stance">{S.esc(STANCE_WORD.get(d.stance, d.stance))}</p>',
                f'<p>{S.esc(d.what)}</p>',
                f'<p>{S.esc(d.does)}</p>']
        if d.limit:
            body.append(f'<p class="limit"><strong>What Vault does not do:</strong> {S.esc(d.limit)}</p>')
        # Two counts, never summed: `blocked` is refused DNS lookups over the
        # window, the findings are alerts. A single total would mean nothing.
        tally = f"{d.open_count} open · {d.resolved_count} settled"
        if d.blocked:
            tally += f" · {d.blocked:,} lookups refused"
        body.append(f'<p class="tally">{S.esc(tally)}</p>')
        body.append(S.table(["Severity", "What Vault found", "Why", "What happened to it"], rows, cls="findings")
                    if rows else '<p class="clear">Nothing found in this period.</p>')
        blocks.append(f'<section class="threat"><h2>{S.esc(d.label)}</h2>' + "".join(body) + "</section>")

    verdict = (f"{total_open} open signal{'s' if total_open != 1 else ''} across all attack types, "
               f"and {total_settled} settled in the {period_txt}.")

    toolbar_html = (
        f'<div class="toolbar"><button onclick="print()">Print / save as PDF</button>'
        f'<span>{period_txt}</span></div>' if toolbar else
        f'<div class="toolbar"><span>{period_txt}</span></div>'
    )
    extra_css = (
        "table.findings{width:100%;border-collapse:collapse;font-size:12px;margin:8px 0}"
        "table.findings th{text-align:left;font-weight:600;padding:6px 8px;border-bottom:1px solid var(--line,#334)}"
        "table.findings td{padding:6px 8px;vertical-align:top;border-bottom:1px solid var(--line,#223)}"
        "section.threat{margin:0 0 18px;padding:0 0 12px;border-bottom:1px solid var(--line,#223)}"
        "section.threat h2{font-size:15px;margin:0 0 6px}"
        ".stance{font-weight:600;margin:0 0 6px}"
        ".limit{margin:6px 0}.tally{margin:6px 0;opacity:.8}.clear{opacity:.7}"
    )
    return (
        '<!doctype html><html><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        f'<title>{S.esc(ttl)}</title>{_THEME_BOOT_SCRIPT}<style>{EXEC_REPORT_CSS}{extra_css}</style></head><body>'
        + toolbar_html
        + '<div class="page">'
        + f'<div class="masthead"><div class="brand">{S.esc(brand["name"])}</div>'
        + f'<div class="period">{S.esc(period_txt)}</div></div>'
        + f'<div class="subhead"><h1>{S.esc(ttl)} — {S.esc(owner)}</h1>'
        + f'<div class="meta">Prepared for {who}<br>Produced by Vault'
        + (f' · {S.esc(domain)}' if domain else '') + '</div></div>'
        + f'<div class="verdict"><p>{S.esc(verdict)}</p><div class="byline">Each attack type states what Vault '
          'does about it, including where it does nothing. Every finding carries what happened to it.</div></div>'
        + "".join(blocks)
        + f'<div class="footpage"><span>Generated {now.strftime("%Y-%m-%d %H:%M")}'
        + (f' · {S.esc(domain)}' if domain else '') + '</span>'
        + '<span>Confidential — contains network details</span></div>'
        + '</div></body></html>'
    )


def build_vuln_report(db: DbSession, tenant, days: int = 7, title: str | None = None, *,
                      toolbar: bool = True) -> str:
    """The vulnerability-scan report: every manual scan in the period, the
    latest result per target, and for each finding what happened to it --
    seen, fixed by Vault, fixed since the previous scan, accepted by the
    owner, or still needing them -- plus where it was reported. A separate
    document from the infrastructure report, so it can go to whoever asked
    for the scan without the rest of the network riding along."""
    now = _now()
    since = now - dt.timedelta(days=days)
    scans = _vuln_scans_in_period(db, tenant, since)
    last = last_of(db, tenant)
    owner, domain, who, brand = _report_identity(db, tenant, last.get("sites") or [])
    ttl = title or "Vulnerability scan report"
    period_txt = f"last {days} day{'s' if days != 1 else ''}"
    devices_by_ip = {d.ip: d for d in db.query(m.Device).filter_by(tenant_id=tenant.id).all() if d.ip}
    fix_jobs = (db.query(m.Queue).filter(m.Queue.tenant_id == tenant.id, m.Queue.path == "/api/fix",
                                         m.Queue.status == "done", m.Queue.ts >= since).all())
    channels = [r.kind for r in db.query(m.Integration).filter_by(tenant_id=tenant.id, enabled=True).all()
                if r.kind != "ntfy"]

    # latest scan per target, and the earlier ones behind it
    latest: dict[tuple, "m.Queue"] = {}
    earlier: dict[tuple, list] = {}
    for q in scans:   # newest first
        k = (str((q.body or {}).get("kind") or ""), str((q.body or {}).get("target") or ""))
        if k not in latest:
            latest[k] = q
        else:
            earlier.setdefault(k, []).append(q)

    counts = {"critical": 0, "high": 0, "medium": 0, "low": 0, "info": 0}
    status_counts = {"fixed_by_guardian": 0, "fixed": 0, "accepted": 0, "needs_you": 0, "open": 0}
    sections = []
    sev_rank = {"critical": 0, "high": 1, "medium": 2, "low": 3, "info": 4}
    for k, q in latest.items():
        res = q.result or {}
        findings = sorted(res.get("findings") or [], key=lambda f: sev_rank.get(str(f.get("severity")), 5))
        prev = earlier.get(k) or []
        rows = []
        for f in findings:
            sev = str(f.get("severity") or "info")
            counts[sev] = counts.get(sev, 0) + 1
            tr = vuln_finding_trail(db, tenant, q, f, prev, devices_by_ip, fix_jobs)
            status_counts[tr["status"]] += 1
            label = {"fixed_by_guardian": "Fixed by Vault", "accepted": "Accepted", "needs_you": "Needs you", "open": "Open"}[tr["status"]]
            rows.append(f'<tr class="sev-{S.esc(sev)}"><td><span class="pill">{S.esc(sev)}</span></td>'
                        f'<td>{S.esc(str(f.get("target") or k[1]))}{(" · port " + str(f.get("port"))) if f.get("port") else ""}</td>'
                        f'<td>{S.esc(str(f.get("title") or ""))}<div class="muted">{S.esc(str(f.get("evidence") or "")[:160])}</div></td>'
                        f'<td><b class="st-{tr["status"]}">{label}</b><div class="muted">{S.esc(tr["note"])}</div></td></tr>')
        # findings that were in the previous scan of this target and are gone now
        gone_rows = []
        if prev:
            now_keys = {_finding_key(f) for f in findings}
            seen_gone = set()
            for e in prev:
                for f in (e.result or {}).get("findings") or []:
                    fk = _finding_key(f)
                    if fk not in now_keys and fk not in seen_gone:
                        seen_gone.add(fk)
                        status_counts["fixed"] += 1
                        gone_rows.append(f'<li><b>{S.esc(str(f.get("title") or ""))}</b> on {S.esc(fk[0])}'
                                         f'{(" port " + str(fk[1])) if fk[1] else ""} — seen '
                                         f'{S.esc(((e.result or {}).get("finished") or "")[:16].replace("T", " "))}, '
                                         f'gone by {S.esc((res.get("finished") or "")[:16].replace("T", " "))}.</li>')
        reported = (f"Reported to {', '.join(channels)} when the scan finished." if channels
                    else "Not sent anywhere: no notification channel is set up.")
        sections.append(
            f'<div class="panel"><div class="panel-head"><h2>{S.esc(k[0])} · {S.esc(k[1])}</h2>'
            f'<span class="count">score {S.esc(str(res.get("score", "—")))} · {S.esc(str(res.get("profile") or (q.body or {}).get("profile") or ""))} scan · '
            f'{S.esc((res.get("finished") or "")[:16].replace("T", " "))} · asked by {S.esc(q.who or "")}</span></div>'
            + (f'<table class="findings"><thead><tr><th>Severity</th><th>Where</th><th>Finding</th><th>What happened</th></tr></thead>'
               f'<tbody>{"".join(rows)}</tbody></table>' if rows else '<p class="muted">Nothing found.</p>')
            + (f'<h3>Fixed since the previous scan</h3><ul class="plain-list">{"".join(gone_rows)}</ul>' if gone_rows else "")
            + f'<p class="muted">{S.esc(reported)}</p></div>')

    n_scans = len(scans)
    verdict = ("No vulnerability scans ran in this period." if not scans else
               f"{n_scans} scan{'s' if n_scans != 1 else ''} on {len(latest)} target{'s' if len(latest) != 1 else ''}: "
               f"{counts['critical']} critical, {counts['high']} high, {counts['medium']} medium, {counts['low']} low finding"
               f"{'s' if (counts['critical'] + counts['high'] + counts['medium'] + counts['low']) != 1 else ''} in the latest results. "
               f"{status_counts['fixed_by_guardian']} fixed by Vault, {status_counts['fixed']} fixed since the previous scan, "
               f"{status_counts['accepted']} accepted by you, {status_counts['needs_you']} need you, {status_counts['open']} waiting for a yes.")
    toolbar_html = (
        f'<div class="toolbar"><button onclick="print()">Print / save as PDF</button>'
        f'<span>{period_txt}</span></div>' if toolbar else
        f'<div class="toolbar"><span>{period_txt}</span></div>'
    )
    extra_css = (
        "table.findings{width:100%;border-collapse:collapse;font-size:12px;margin:8px 0}"
        "table.findings th{text-align:left;font-weight:600;padding:6px 8px;border-bottom:1px solid var(--line,#334)}"
        "table.findings td{padding:6px 8px;vertical-align:top;border-bottom:1px solid var(--line,#223)}"
        ".pill{display:inline-block;padding:1px 7px;border-radius:9px;font-size:11px;text-transform:uppercase;letter-spacing:.03em;background:#3a3f4b;color:#fff}"
        ".sev-critical .pill{background:#b3261e}.sev-high .pill{background:#c2410c}.sev-medium .pill{background:#a16207}.sev-low .pill{background:#475569}"
        ".st-fixed_by_guardian,.st-fixed{color:#15803d}.st-accepted{color:#0369a1}.st-needs_you{color:#b45309}.st-open{color:#7c3aed}"
        "h3{font-size:13px;margin:10px 0 4px}"
    )
    html = (
        '<!doctype html><html><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        f'<title>{S.esc(ttl)}</title>{_THEME_BOOT_SCRIPT}<style>{EXEC_REPORT_CSS}{extra_css}</style></head><body>'
        + toolbar_html
        + '<div class="page">'
        + f'<div class="masthead"><div class="brand">{S.esc(brand["name"])}</div>'
        + f'<div class="period">{S.esc(period_txt)}</div></div>'
        + f'<div class="subhead"><h1>{S.esc(ttl)} — {S.esc(owner)}</h1>'
        + f'<div class="meta">Prepared for {who}<br>Produced by Vault'
        + (f' · {S.esc(domain)}' if domain else '') + '</div></div>'
        + f'<div class="verdict"><p>{S.esc(verdict)}</p><div class="byline">Every finding below carries what happened to it: '
          'seen, fixed by Vault, fixed since the previous scan, accepted by you, or what it still needs from you.</div></div>'
        + "".join(sections)
        + f'<div class="footpage"><span>Generated {now.strftime("%Y-%m-%d %H:%M")}'
        + (f' · {S.esc(domain)}' if domain else '') + '</span>'
        + '<span>Confidential — contains network details</span></div>'
        + '</div></body></html>'
    )
    return html


# --------------------------------------------------------------------------
# render_to_file
# --------------------------------------------------------------------------

def coverage_days(db: DbSession, tenant, days: int, offset: dt.timedelta) -> dict:
    """Which days of the period Vault was actually watching, and the gaps.

    The MSP spec calls presenting 99.9% uptime for a month the sensor was
    down the one incident that would cost you every customer who noticed,
    and it is right that the figure is not wrong by its own arithmetic --
    nothing was observed to be down. That is precisely why coverage has to
    be stated rather than computed around: no amount of care inside the
    uptime calculation can tell the reader that the calculation had nothing
    to look at.

    `Scan` rows are the source, deliberately, and not the newer `daily_fact`
    table. Scans are never pruned, so a report about last March can still
    say honestly whether Vault was watching in March; the facts table
    began this month and would answer a confident, false "0 of 30" for any
    earlier period. The coarser source that can answer about the past beats
    the finer one that cannot.

    Days are the tenant's own local days, from `offset`, for the same reason
    the daily facts are: a day boundary cut at UTC for an account in New
    York mixes two of the owner's days together.

    Consecutive missing days are merged into one gap. Three days off the air
    is one outage, and listing it as three invites the reader to picture
    three unrelated blips.
    """
    today = (_now() + offset).date()
    wanted = [today - dt.timedelta(days=n) for n in range(days, 0, -1)]
    first = wanted[0].isoformat()

    seen: set[str] = set()
    rows = (db.query(m.Scan.ts).filter(m.Scan.tenant_id == tenant.id,
                                        m.Scan.ts >= first)
            .all())
    for (ts,) in rows:
        when = parse_ts(ts)
        if when is not None:
            seen.add((when + offset).date().isoformat())

    gaps: list[tuple[str, str]] = []
    run: list[str] = []
    for day in wanted:
        key = day.isoformat()
        if key in seen:
            if run:
                gaps.append((run[0], run[-1]))
                run = []
        else:
            run.append(key)
    if run:
        gaps.append((run[0], run[-1]))

    return {"covered": sum(1 for d in wanted if d.isoformat() in seen),
            "total": len(wanted), "gaps": gaps}


def coverage_sentence(cov: dict) -> str:
    """"27 of 30 days — no sensor data 10-12 Sept", for page one.

    Rendered even when coverage is complete. A line that appears only on a
    bad month teaches readers that its absence means nothing was checked,
    and it makes the bad month look like a different document rather than
    the same one reporting a worse number.
    """
    base = f"{cov['covered']} of {cov['total']} days"
    if not cov["gaps"]:
        return base
    parts = [a if a == b else f"{a} to {b}" for a, b in cov["gaps"]]
    return f"{base} — no sensor data {', '.join(parts)}"


#: The stand-in the digest is computed against. 64 zeros is not a valid
#: SHA-256 of anything anyone will produce by accident, and it is the same
#: width as the real value so the rendered layout does not shift when it is
#: substituted in.
_HASH_PLACEHOLDER = "0" * 64


def stamp_content_hash(html: str) -> tuple[str, str]:
    """Print a verifiable SHA-256 of the document inside the document.

    Returns `(stamped_html, digest)`.

    THE SELF-REFERENCE PROBLEM is why this needs a stated rule rather than
    an obvious implementation. Printing a hash of a document into that
    document changes it, so the printed value can never be the hash of the
    bytes a reader is holding. Vault resolves it by defining the
    canonical form and publishing it in the footer:

        take the document, replace the 64-hex digest with 64 zeros,
        SHA-256 the result.

    That is checkable by a third party holding nothing but the file -- no
    access to Vault, no API call, no trust in us. Which is the entire
    reason the line is worth printing: an underwriter or an auditor who can
    only verify a checksum by asking the party who issued it has not
    verified anything.

    The instruction is printed alongside the digest rather than kept in
    documentation, because the reader who needs it is precisely the one who
    does not have our docs open.

    Applied here, after rendering, rather than inside the three report
    templates: the hash must cover the whole document including its footer,
    and doing it per-template would be three chances to hash a different
    subset and one silent disagreement.
    """
    line = (
        '<div class="foot foot-hash"><span>Document checksum (SHA-256): '
        f'<code>{_HASH_PLACEHOLDER}</code>. To verify: replace that value with 64 zeros '
        "and hash the file.</span></div>"
    )
    marker = "</body>"
    stamped = (html.replace(marker, line + marker, 1) if marker in html else html + line)
    digest = hashlib.sha256(stamped.encode("utf-8")).hexdigest()
    return stamped.replace(_HASH_PLACEHOLDER, digest), digest


def render_to_file(db: DbSession, tenant, days: int = 7, sections: list[str] | None = None,
                    title: str | None = None, preset: str | None = None,
                    kind: str = "manual") -> tuple["m.Report", pathlib.Path]:
    """Render `build_report` and persist it: writes the HTML file under
    `settings.data_dir / "reports" / tenant.slug / "<kind>-<date>-<short id>.html"`
    and inserts the matching `Report` row."""
    html = build_report(db, tenant, days=days, sections=sections, title=title, preset=preset)
    html, content_hash = stamp_content_hash(html)

    last = last_of(db, tenant)
    score = (last.get("score") or {}).get("total")
    scan_count = db.query(m.Scan).filter_by(tenant_id=tenant.id).count()
    alert_count = db.query(m.Alert).filter_by(tenant_id=tenant.id).count()

    report_id = m.uid()
    dest_dir = settings.data_dir / "reports" / tenant.slug
    dest_dir.mkdir(parents=True, exist_ok=True)
    stamp = _now().strftime("%Y-%m-%d")
    filename = f"{kind}-{stamp}-{report_id[:8]}.html"
    path = dest_dir / filename
    path.write_text(html, encoding="utf-8")

    default_title = ("Executive security report" if preset == "exec_report"
                      else "Vulnerability scan report" if preset == "vuln_report"
                      else "Infrastructure & Home Network Report")
    report = m.Report(
        id=report_id, tenant_id=tenant.id, kind=kind, period_days=days, preset=preset or "",
        sections=sections or (presets_mod.label_and_sections(preset)[1] if preset else list(S.SECTION_KEYS)),
        title=title or default_title, path=str(path),
        meta={"score": score, "scans": scan_count, "alerts": alert_count},
        content_hash=content_hash,
    )
    db.add(report)
    db.commit()
    return report, path


# --------------------------------------------------------------------------
# Sharing
# --------------------------------------------------------------------------

def mint_share_token(report: "m.Report", hours: int) -> str:
    """Mint (or rotate) a public share token for `report`.

    Does not commit -- caller commits alongside its own audit row. Rotating
    an already-shared report invalidates the previous link: the old token
    is simply overwritten, so `/r/<old-token>` (Task 42) no longer resolves
    to any row.
    """
    report.share_token = secrets.token_hex(16)
    report.share_expires = _now() + dt.timedelta(hours=hours)
    return report.share_token
