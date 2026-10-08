"""The public REST API — everything served under `/v1` on api.guarder.io.

This is a product surface, not an internal endpoint made public. Three rules
hold across every route in the file, and each is enforced structurally rather
than by remembering to do it:

**Tenant scoping.** The account is `principal.tenant`, read from the token row.
No route takes an account id in its path, its query or a header, so there is
nothing for a caller to substitute. Every lookup filters on
`principal.tenant.id`, which makes another account's identifier a row that does
not exist — answered **404, never 403**. A 403 would confirm the identifier is
real.

**Rate limiting.** Applied in `apideps` before a handler runs: a per-token
ceiling on everything, a tighter one on writes, and a per-address one on calls
with no usable credential. There is no route in this file that reaches the
database without passing one of those.

**Audit.** Every write goes through the same helper the dashboard uses
(`write.apply_alert_op`, `write.apply_device_update`, `write.queue_and_audit`),
passing `principal.who` (`token:gapi_########`), `principal.role`,
`principal.tenant` and `principal.ip`. A token-driven change is therefore
indistinguishable in the audit log and the sensor's edit-replay queue from the
same change made on the dashboard — except that the actor names the token.

Every summary and description below is customer-facing documentation: it is
what the generated reference at `/v1/docs` and the app's API-guide section
print. Keep them plain, and keep internal vocabulary out of them.
"""

import datetime as dt
import io
import csv

import pathlib

from ..config import settings

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session as DbSession

from .. import (
    apideps, apidocs, controls, eol, maintenance, models as m, networks,
    offboarding, remedy, retention, sla,
    schemas_api as sa, schemas_dash as sd, security as security_mod,
)
from ..apideps import ApiPrincipal, envelope, page, require_api
from ..db import get_db
from ..scans import parse_ts
from ..payloads import (
    assets as assets_payload, dnsview as dns_payload, overview as overview_payload,
    presence as presence_payload, security as security_payload, topology as topology_payload,
)
from .read import alert_out, device_out, incident_out, rec_out
from .write import ALERT_OPS, apply_alert_op, apply_device_update, queue_and_audit

#: `/v1`, not `/api/v1`: the hostname already says "api", and keeping the two
#: prefixes distinct is what lets the API host's nginx block allow `/v1/` while
#: denying `/api/` outright — so no dashboard route is reachable from the API
#: hostname even by accident.
router = APIRouter(prefix="/v1", tags=["Vault API v1"])

READ = Depends(require_api("viewer"))
WRITE = Depends(require_api("operator", write=True))

_DEFAULT_LIMIT = 50


def _netrefs(db: DbSession, tenant_id: str) -> dict:
    return networks.refs_by_id(db, tenant_id)


def _resolve_net(db: DbSession, tenant_id: str, ref: str):
    """`?network=<id|slug>`. An unknown reference 404s inside
    `networks.resolve` rather than silently widening to the whole account."""
    return networks.resolve(db, tenant_id, ref)


# --------------------------------------------------------------------------
# Unauthenticated
# --------------------------------------------------------------------------

@router.get("/health", response_model=sa.Health, summary="Liveness check",
            description="Answers without a token, so a monitor can watch the API itself.")
def health(_ip: str = Depends(apideps.anon_guard)):
    return sa.Health()


@router.get("/openapi.json", include_in_schema=False)
def openapi_document(request: Request, _ip: str = Depends(apideps.anon_guard)):
    """The machine-readable description of this API. Unauthenticated on
    purpose -- it is documentation, it names no account and carries no data --
    but rate-limited per address like every other route here.

    `include_in_schema=False` so the document does not describe itself."""
    return JSONResponse(apidocs.document(request.app))


# GET and HEAD: FastAPI does not add HEAD for a GET route, and HEAD is what an
# uptime monitor sends. Without it a monitor watching the public reference
# reads 405 and calls the API down.
@router.api_route("/docs", methods=["GET", "HEAD"], include_in_schema=False,
                  response_class=HTMLResponse)
def reference(request: Request, _ip: str = Depends(apideps.anon_guard)):
    """The human-readable reference, rendered from the document above so the
    two can never disagree. Self-contained: every asset it loads -- both faces
    and the one script -- comes from this origin, so the API host's policy
    stays `default-src 'self'` with nothing third-party anywhere on it."""
    return HTMLResponse(apidocs.reference_html(request.app))


@router.get("/docs.css", include_in_schema=False)
def reference_style(_ip: str = Depends(apideps.anon_guard)):
    """The reference's stylesheet.

    A file for the same reason `/v1/docs.js` is a file. The API host's policy
    is `default-src 'self'` with no `style-src` of its own, so an inline
    `<style>` block on this page is refused and the reference renders as
    unstyled Times -- which is exactly what it did in production until this
    route existed. Serving the CSS from a real URL keeps the policy strict and
    the page styled, with no `'unsafe-inline'` anywhere on the host.

    `no-cache` for the same reason as the console: the file name carries no
    hash, and a stale stylesheet against a newer document is a page whose
    sections have no rules."""
    return Response(apidocs._CSS,
                    media_type="text/css; charset=utf-8",
                    headers={"Cache-Control": "no-cache"})


@router.get("/docs.js", include_in_schema=False)
def reference_script(_ip: str = Depends(apideps.anon_guard)):
    """The reference's live console.

    A file, not an inline block, and that is the entire reason it is a route:
    `deploy/nginx-api.conf.tmpl` relaxes the docs path from `script-src 'none'`
    to `script-src 'self'` and grants no inline allowance at all -- no
    `'unsafe-inline'`, no nonce, no hash. An attacker who found a way to write
    a `<script>` into this page would still be refused.

    Like `/v1/fonts/{name}` this is an asset route, not an API route:
    `include_in_schema=False` keeps it out of the OpenAPI document, and it
    answers no account data. `no-cache` rather than the fonts' week: the file
    name carries no hash, and a stale console against a newer document is a
    console that sends the wrong request."""
    return Response(apidocs.CONSOLE_JS,
                    media_type="application/javascript; charset=utf-8",
                    headers={"Cache-Control": "no-cache"})


# The reference names Manrope and IBM Plex Mono, and the API host's CSP is
# `default-src 'self'` with no `data:` allowance -- so the faces have to be
# reachable on this origin or the page silently falls back to the system
# stack while the app beside it renders the real thing. nginx proxies
# `/v1/` only, which is why this route lives under that prefix rather than
# at a tidier path.
def _font_dir() -> pathlib.Path | None:
    """Where the two woff2 files actually are, which differs by deployment.

    A checkout has them in `web/public/fonts`; a deployed hub only ever
    receives the built output, so there they are in `web/dist/fonts`. The
    configured web root wins when it is set, because that is what the hub is
    really serving the app from."""
    roots: list[pathlib.Path] = []
    if getattr(settings, "web_dist", ""):
        roots.append(pathlib.Path(settings.web_dist) / "fonts")
    here = pathlib.Path(__file__).resolve().parents[3]
    roots += [here / "web" / "dist" / "fonts", here / "web" / "public" / "fonts"]
    return next((r for r in roots if r.is_dir()), None)
_FONTS = {
    "manrope-latin-var.woff2",
    "ibm-plex-mono-latin-400.woff2",
    "ibm-plex-mono-latin-600.woff2",
}


@router.get("/fonts/{name}", include_in_schema=False)
def reference_font(name: str, _ip: str = Depends(apideps.anon_guard)):
    """One of the reference's two self-hosted faces.

    An allow-list rather than a path join: `name` comes off the wire, and a
    directory-traversal attempt must not be able to read anything else on the
    box. Cached hard because the file names carry no hash but never change
    within a release."""
    if name not in _FONTS:
        raise HTTPException(404, "not found")
    base = _font_dir()
    if base is None:
        raise HTTPException(404, "not found")
    path = base / name
    if not path.is_file():
        raise HTTPException(404, "not found")
    return FileResponse(path, media_type="font/woff2",
                        headers={"Cache-Control": "public, max-age=604800, immutable"})


# --------------------------------------------------------------------------
# Identity
# --------------------------------------------------------------------------

@router.get("/whoami", response_model=sa.WhoAmI, summary="Describe this token",
            description="What account the presented token reads, and what it is allowed to do. "
                        "A good first call when wiring up a client.")
def whoami(p: ApiPrincipal = READ):
    return sa.WhoAmI(
        account=p.tenant.name, account_slug=p.tenant.slug, token_name=p.token.name,
        token_prefix=p.token.prefix, scope=p.scope, role=p.role,
        expires=apideps.iso(p.token.expires),
    )


# --------------------------------------------------------------------------
# Composite reads — the same payloads the dashboard renders
# --------------------------------------------------------------------------

@router.get("/overview", response_model=sd.OverviewPayload, summary="Account overview",
            description="The headline picture: score, alert counts, device counts, site and host "
                        "health, and what Vault would do next.")
def overview(p: ApiPrincipal = READ, db: DbSession = Depends(get_db)):
    return overview_payload.build(db, p.tenant)


@router.get("/networks", response_model=sd.NetworksPayload, summary="List networks",
            description="Every monitored network, with its device counts and whether its sensor "
                        "is reporting.")
def networks_list(p: ApiPrincipal = READ, db: DbSession = Depends(get_db)):
    return {"networks": networks.health(db, p.tenant)}


@router.get("/sites", response_model=sd.SitesPayload, summary="List monitored websites",
            description="Reachability, response time and certificate expiry for each website "
                        "Vault watches.")
def sites(p: ApiPrincipal = READ, db: DbSession = Depends(get_db)):
    return assets_payload.sites(db, p.tenant)


@router.get("/hosts", response_model=sd.HostsPayload, summary="List monitored servers",
            description="Load, disk, memory, pending updates and hardening grade for each server.")
def hosts(p: ApiPrincipal = READ, db: DbSession = Depends(get_db)):
    return assets_payload.hosts(db, p.tenant)


@router.get("/security", response_model=sd.SecurityPayload, summary="Security posture",
            description="Scorecards, failed checks, exposure, Wi-Fi guard and recent attackers.")
def security(network: str = "", p: ApiPrincipal = READ, db: DbSession = Depends(get_db)):
    return security_payload.build(db, p.tenant, net=_resolve_net(db, p.tenant.id, network))


@router.get("/topology", response_model=sd.TopologyPayload, summary="Network map",
            description="Nodes and links for the network map, clustered by kind, area or network.")
def topology(cluster_by: str = "kind", network: str = "", p: ApiPrincipal = READ,
             db: DbSession = Depends(get_db)):
    if cluster_by not in ("kind", "area", "network"):
        raise HTTPException(422, "cluster_by must be kind, area or network")
    return topology_payload.build(db, p.tenant, cluster_by=cluster_by,
                                  net=_resolve_net(db, p.tenant.id, network))


@router.get("/presence", response_model=sd.ScreenTimePayload, summary="Screen time",
            description="How long each device was online, per day and per hour, over the last "
                        "`days` days.")
def presence(days: int = 7, network: str = "", p: ApiPrincipal = READ,
             db: DbSession = Depends(get_db)):
    if not 1 <= days <= 90:
        raise HTTPException(422, "days must be between 1 and 90")
    return presence_payload.screen_time(db, p.tenant.id, days=days,
                                        net=_resolve_net(db, p.tenant.id, network))


@router.get("/dns", response_model=sd.DnsPayload, summary="DNS summary",
            description="Totals, top and blocked domains, per-device breakdown and the current "
                        "lists, over the last `hours` hours.")
def dns_summary(hours: int = 24, network: str = "", p: ApiPrincipal = READ,
                db: DbSession = Depends(get_db)):
    # Clamped to the account's own retained window: past that there is nothing
    # left to read, and an unbounded value would be an unbounded scan.
    pol = retention.policy(p.tenant)
    hours = max(1, min(hours, (pol["dns_raw_days"] + pol["dns_rollup_days"]) * 24))
    return dns_payload.build(db, p.tenant, hours=hours,
                             net=_resolve_net(db, p.tenant.id, network))


@router.get("/dns/rules", response_model=sa.DnsRules, summary="Read the DNS allow/block lists")
def dns_rules(p: ApiPrincipal = READ, db: DbSession = Depends(get_db)):
    rows = db.query(m.DnsRule).filter_by(tenant_id=p.tenant.id).all()
    return sa.DnsRules(
        allow=sorted(r.domain for r in rows if r.kind == "allow"),
        block=sorted(r.domain for r in rows if r.kind == "block"),
    )


# --------------------------------------------------------------------------
# List reads — paginated
# --------------------------------------------------------------------------

@router.get("/devices", response_model=sa.Page[sa.Device], summary="List devices",
            description="Devices seen on the monitored networks, newest sighting first. Narrow "
                        "with `network`, `kind`, `trusted` or `q` (a substring of the name, label, "
                        "address or vendor).")
def devices(network: str = "", kind: str = "", trusted: bool | None = None, q: str = "",
            limit: int = _DEFAULT_LIMIT, offset: int = 0,
            p: ApiPrincipal = READ, db: DbSession = Depends(get_db)):
    limit, offset = page(limit, offset)
    net = _resolve_net(db, p.tenant.id, network)
    query = db.query(m.Device).filter_by(tenant_id=p.tenant.id)
    if net is not None:
        query = query.filter(m.Device.network_id == net.id)
    if kind:
        query = query.filter(m.Device.kind == kind)
    if trusted is not None:
        query = query.filter(m.Device.trusted == trusted)
    if q:
        like = f"%{q.lower()}%"
        query = query.filter(
            m.Device.name.ilike(like) | m.Device.label.ilike(like)
            | m.Device.ip.ilike(like) | m.Device.vendor.ilike(like) | m.Device.mac.ilike(like)
        )
    total = query.count()
    rows = query.order_by(m.Device.last_seen.desc()).limit(limit).offset(offset).all()
    refs = _netrefs(db, p.tenant.id)
    return envelope([_device(d, refs) for d in rows], total, limit, offset)


def _device(d: m.Device, refs: dict) -> dict:
    """`read.device_out` minus the fields the public API does not publish:
    `quarantined` and `parent` describe containment and de-duplication, both of
    which are dashboard-only concerns and neither of which v1 can set."""
    out = device_out(d)
    out["network"] = refs.get(d.network_id)
    for internal in ("quarantined", "parent", "extra"):
        out.pop(internal, None)
    return out


@router.get("/devices/{mac}", response_model=sd.DeviceDetail, summary="One device in full",
            description="Everything Vault knows about one device: ports, timeline, DNS "
                        "profile and presence.")
def device_detail(mac: str, network: str = "", p: ApiPrincipal = READ,
                  db: DbSession = Depends(get_db)):
    return assets_payload.device_detail(db, p.tenant, mac,
                                        net=_resolve_net(db, p.tenant.id, network))


@router.get("/alerts", response_model=sa.Page[sa.Alert], summary="List alerts",
            description="Alerts, most recently seen first. Narrow with `status` "
                        "(open/acked/snoozed/resolved), `sev` (CRIT/WARN/INFO) or `network`.")
def alerts(status: str = "", sev: str = "", network: str = "",
           limit: int = _DEFAULT_LIMIT, offset: int = 0,
           p: ApiPrincipal = READ, db: DbSession = Depends(get_db)):
    limit, offset = page(limit, offset)
    if status and status not in ("open", "acked", "snoozed", "resolved"):
        raise HTTPException(422, "status must be open, acked, snoozed or resolved")
    if sev and sev not in ("CRIT", "WARN", "INFO"):
        raise HTTPException(422, "sev must be CRIT, WARN or INFO")
    net = _resolve_net(db, p.tenant.id, network)
    query = db.query(m.Alert).filter_by(tenant_id=p.tenant.id)
    if net is not None:
        query = query.filter(m.Alert.network_id == net.id)
    if status:
        query = query.filter(m.Alert.status == status)
    if sev:
        query = query.filter(m.Alert.sev == sev)
    total = query.count()
    rows = query.order_by(m.Alert.last_seen.desc()).limit(limit).offset(offset).all()
    refs = _netrefs(db, p.tenant.id)
    return envelope([_alert(a, refs) for a in rows], total, limit, offset)


#: Vault's heartbeat setup alert tells you the exact command to add to a cron
#: job, and that command contains the heartbeat's own token
#: (`sensor/guardian_sensor/alerts.py`, the `hb:<name>:setup` message). On the
#: dashboard that is correct and useful: an admin, on the app's own origin,
#: looking at their own configuration.
#:
#: On the public API it is a privilege escalation. A read-scope token is a
#: lower-trust credential than an admin session, and it would be handed a second
#: credential that can WRITE -- anyone holding a heartbeat token can falsely
#: report that a backup job ran, which is precisely the failure Vault exists
#: to catch. So the token is cut out of the prose here.
#:
#: The cure has since landed in `sensor/guardian_sensor/alerts.py`: the alert
#: now names the heartbeat and points at Security → Heartbeats, where an
#: admin-only, audited route renders the command. This stays as the guard for
#: rows written by an older sensor build, and is now the shared
#: `security.redact_prose` so the hub has exactly one copy of the rule (imported
#: as `security_mod`: the `/security` route handler below owns the bare name).


def _public_text(text: str) -> str:
    """Alert prose with the one known embedded credential removed."""
    return security_mod.redact_prose(text)


def _alert(a: m.Alert, refs: dict) -> dict:
    out = alert_out(a)
    out["network"] = refs.get(a.network_id)
    # `evidence` is dropped, not redacted: it is a free-form pass-through of
    # sensor-supplied JSON, so there is no version of it this API can promise to
    # have vetted. `sa.Alert` declares the shape without it, and `why` already
    # carries the readable half.
    out.pop("evidence", None)
    for field in ("msg", "why", "fix"):
        out[field] = _public_text(out.get(field) or "")
    return out


# Registered BEFORE `/alerts/{key:path}` and nowhere else. Starlette matches
# in registration order and a `:path` converter is greedy, so with the two the
# other way round `/alerts/<key>/evidence` is swallowed whole -- `alert_one`
# receives "<key>/evidence" as the key, finds no such alert and answers 404.
# It fails as a plausible "alert not found" rather than a routing error,
# which is why the full suite caught it and the endpoint's own tests did not
# when they were written against the earlier non-greedy route.
@router.get("/alerts/{key:path}/evidence", response_model=sa.EvidenceBundle,
            summary="Everything Vault knows about one finding",
            description="A single self-contained document for one alert: the finding as "
                        "recorded, which sensor saw it and what version it was running, the "
                        "sweep's own timestamp alongside the hub's, the scoring rubric in "
                        "force, what Vault can and cannot repair, and every repair "
                        "attempted — with whether the command succeeded and whether a later "
                        "observation confirmed the condition cleared, reported separately.")
def alert_evidence(key: str, p: ApiPrincipal = READ, db: DbSession = Depends(get_db)):
    """The receipts for one finding.

    A score or an alert asks to be believed; this can be checked. It is what
    an MSP hands a client who asks what actually happened, and what survives
    "prove it" from an auditor — the trust plan argues (§2.5O) that it is
    the single highest-value thing in that section, and nobody at this price
    point ships one.

    Assembled strictly from what was stored at the time. Nothing here is
    generated at export to explain the finding after the fact, because a
    narrative written now is exactly what a bundle exists to make
    unnecessary. Where Vault does not know something the field is absent
    rather than filled in with a plausible value.

    `sensor_ts` and `hub_received_ts` are both reported and never
    reconciled. Clock skew between a sensor and the hub is real (§2.5K) and
    a bundle that quietly picked one of them would hide the discrepancy an
    auditor most wants to see.
    """
    alert = networks.find_alert(db, p.tenant.id, key)
    if not alert:
        raise HTTPException(404, "alert not found")

    scan = db.query(m.Scan).filter_by(tenant_id=p.tenant.id).order_by(m.Scan.ts.desc()).first()
    raw = (scan.raw if scan else {}) or {}
    score = raw.get("score") or {}
    sensor = (
        db.query(m.Sensor).filter_by(tenant_id=p.tenant.id, id=scan.sensor_id).first()
        if scan and scan.sensor_id else None
    )

    # The repairs that touched this finding's target. `cause_key` collapses
    # the per-network and per-device namespacing so a repair logged against
    # the host still matches the alert that asked for it.
    host = (alert.evidence or {}).get("host") or ""
    repairs = []
    if host:
        rows = (db.query(m.Fix).filter_by(tenant_id=p.tenant.id, host=host)
                .order_by(m.Fix.ts.desc()).limit(50).all())
        for f in rows:
            first = (f.out or "").strip().splitlines()[:1]
            repairs.append({"ts": f.ts, "host": f.host, "action": f.action,
                            "label": f.label or "", "arg": f.arg or "", "by": f.by or "auto",
                            "ok": bool(f.ok),
                            "verified_at": f.verified_at.isoformat() if f.verified_at else None,
                            "reboot_needed": bool(f.reboot_needed),
                            "detail": first[0][:200] if first else ""})

    rem = remedy.resolve(db, p.tenant, alert, p.role)
    return {
        "exported_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "alert": {
            "key": alert.key, "severity": alert.sev, "message": alert.msg, "why": alert.why,
            "evidence": alert.evidence or {}, "confidence": alert.confidence,
            "first_seen": alert.first_seen, "last_seen": alert.last_seen,
            "count": alert.count, "status": alert.status,
            "resolved_by": alert.resolved_by, "ticket_key": alert.ticket_key,
        },
        "observed_by": {
            "sensor": sensor.name if sensor else "",
            "platform": sensor.platform if sensor else "",
            "version": sensor.version if sensor else "",
            "last_seen": sensor.last_seen.isoformat() if sensor and sensor.last_seen else None,
        },
        "scan": {
            "sensor_ts": scan.ts if scan else None,
            "hub_received_ts": raw.get("received_ts"),
        },
        "scoring": {
            "score": scan.score if scan else None,
            "model": score.get("model") or "",
            "coverage": score.get("coverage") or {},
        },
        "remedy": {"tier": rem.kind, "summary": rem.summary, "need": rem.need},
        "repairs": repairs,
    }


@router.get("/alerts/{key:path}", response_model=sa.Alert, summary="One alert")
def alert_one(key: str, p: ApiPrincipal = READ, db: DbSession = Depends(get_db)):
    a = networks.find_alert(db, p.tenant.id, key)
    if not a:
        raise HTTPException(404, "alert not found")
    return _alert(a, _netrefs(db, p.tenant.id))


@router.get("/incidents", response_model=sa.Page[sa.Incident], summary="List incidents",
            description="Groups of related alerts told as one story, newest first. `open=true` "
                        "hides the ones already closed.")
def incidents(open: bool | None = None, limit: int = _DEFAULT_LIMIT, offset: int = 0,
              p: ApiPrincipal = READ, db: DbSession = Depends(get_db)):
    limit, offset = page(limit, offset)
    query = db.query(m.Incident).filter_by(tenant_id=p.tenant.id)
    if open is True:
        query = query.filter(m.Incident.closed.is_(None))
    elif open is False:
        query = query.filter(m.Incident.closed.isnot(None))
    total = query.count()
    rows = query.order_by(m.Incident.opened.desc()).limit(limit).offset(offset).all()
    return envelope([_incident(i) for i in rows], total, limit, offset)


def _incident(i: m.Incident) -> dict:
    """`read.incident_out` minus `actions`: those name remediations v1
    cannot run, so publishing them would offer something the API does not
    honour. `alert_keys` stays -- those ARE actionable here."""
    out = incident_out(i)
    out.pop("actions", None)
    return out


@router.get("/recommendations", response_model=sa.Page[sa.Recommendation],
            summary="List recommendations",
            description="Changes Vault suggests, highest priority first. `undecided=true` "
                        "shows only the ones nobody has answered yet.")
def recommendations(undecided: bool | None = None, limit: int = _DEFAULT_LIMIT, offset: int = 0,
                    p: ApiPrincipal = READ, db: DbSession = Depends(get_db)):
    limit, offset = page(limit, offset)
    query = db.query(m.Recommendation).filter_by(tenant_id=p.tenant.id)
    if undecided is True:
        query = query.filter(m.Recommendation.decision == "")
    elif undecided is False:
        query = query.filter(m.Recommendation.decision != "")
    total = query.count()
    rows = query.order_by(m.Recommendation.last_seen.desc()).limit(limit).offset(offset).all()
    return envelope([_rec(r) for r in rows], total, limit, offset)


def _rec(r: m.Recommendation) -> dict:
    """`read.rec_out` minus `action`: it names a repair on a specific host,
    and v1 has no route that runs one. Reading it here would look like an
    offer the API does not keep."""
    out = rec_out(r)
    out.pop("action", None)
    return out


@router.get("/events", response_model=sa.Page[sa.Event], summary="List events",
            description="Devices joining and leaving, ports opening and closing, scans "
                        "finishing — newest first. Narrow with `type` or `network`.")
def events(type: str = "", network: str = "", limit: int = _DEFAULT_LIMIT, offset: int = 0,
           p: ApiPrincipal = READ, db: DbSession = Depends(get_db)):
    limit, offset = page(limit, offset, max_limit=500)
    net = _resolve_net(db, p.tenant.id, network)
    query = db.query(m.Event).filter_by(tenant_id=p.tenant.id)
    if net is not None:
        query = query.filter(m.Event.network_id == net.id)
    if type:
        query = query.filter(m.Event.type == type)
    total = query.count()
    rows = query.order_by(m.Event.id.desc()).limit(limit).offset(offset).all()
    refs = _netrefs(db, p.tenant.id)
    return envelope(
        [{"ts": e.ts, "type": e.type, "entity": e.entity, "payload": e.payload,
          "network": refs.get(e.network_id)} for e in rows],
        total, limit, offset,
    )


@router.get("/dns/activity", response_model=sa.Page[sa.DnsQuery], summary="DNS lookups",
            description="Individual DNS lookups over the last `hours` hours, newest first. "
                        "Narrow with `blocked`, `mac`, `domain` or `network`. Older lookups are "
                        "summarised rather than kept one by one — use `/v1/dns` for those.")
def dns_activity(hours: int = 24, blocked: bool | None = None, mac: str = "", domain: str = "",
                 network: str = "", limit: int = _DEFAULT_LIMIT, offset: int = 0,
                 p: ApiPrincipal = READ, db: DbSession = Depends(get_db)):
    limit, offset = page(limit, offset, max_limit=500)
    pol = retention.policy(p.tenant)
    hours = max(1, min(hours, pol["dns_raw_days"] * 24))
    cutoff = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=hours)).isoformat()
    net = _resolve_net(db, p.tenant.id, network)
    query = db.query(m.DnsQuery).filter(m.DnsQuery.tenant_id == p.tenant.id,
                                        m.DnsQuery.ts >= cutoff)
    if net is not None:
        query = query.filter(m.DnsQuery.network_id == net.id)
    if blocked is not None:
        query = query.filter(m.DnsQuery.blocked == blocked)
    if mac:
        query = query.filter(m.DnsQuery.mac == mac.lower())
    if domain:
        query = query.filter(m.DnsQuery.name.ilike(f"%{domain.lower()}%"))
    total = query.count()
    rows = query.order_by(m.DnsQuery.id.desc()).limit(limit).offset(offset).all()
    refs = _netrefs(db, p.tenant.id)
    return envelope(
        [{"ts": r.ts, "client": r.client, "mac": r.mac, "name": r.name, "qtype": r.qtype,
          "blocked": bool(r.blocked), "list": r.list, "ms": r.ms,
          "network": refs.get(r.network_id)} for r in rows],
        total, limit, offset,
    )


@router.get("/metrics", response_model=sa.Page[sa.MetricSample], summary="Raw measurements",
            description="The individual samples behind the figures on the dashboard, oldest "
                        "first. Narrow with `metric` (up, latency_ms, disk_pct, score…), "
                        "`monitor` (a site or host name), and an `hours` window. For the `up` "
                        "series each sample carries the state Vault's own arithmetic gave "
                        "it, so an availability figure can be recomputed from these and "
                        "checked against the one Vault publishes.")
def metrics(metric: str = "", monitor: str = "", hours: int = 24,
            limit: int = _DEFAULT_LIMIT, offset: int = 0,
            p: ApiPrincipal = READ, db: DbSession = Depends(get_db)):
    """Why this endpoint exists at all.

    Every other route here reports a conclusion. This one reports the
    evidence, which is the only way a customer can disagree with Vault
    using anything better than an opinion. An availability number nobody can
    recompute is a claim; one that ships with its samples is a measurement,
    and publishing the means to falsify your own headline figure is worth
    more than the figure.

    The classification is the part that has to be right. `state` is computed
    with the same maintenance windows `payloads/assets` uses, so a client who
    drops `planned` samples and divides gets the number on the page. If this
    ever disagreed with the dashboard the endpoint would be worse than not
    having one -- it would hand a customer evidence that Vault's own
    figures do not reconcile.

    Oldest first, unlike the newest-first lists elsewhere in this file: these
    are read as a series and plotted left to right, and a caller paging
    through a window wants it to arrive in the order they will draw it.
    """
    limit, offset = page(limit, offset, max_limit=1000)
    pol = retention.policy(p.tenant)
    hours = max(1, min(hours, pol["retention_days"] * 24))
    cutoff = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=hours)).isoformat()
    query = db.query(m.Metric).filter(m.Metric.tenant_id == p.tenant.id, m.Metric.ts >= cutoff)
    if metric:
        query = query.filter(m.Metric.name == metric)
    if monitor:
        query = query.filter(m.Metric.entity == monitor)
    total = query.count()
    rows = query.order_by(m.Metric.ts.asc(), m.Metric.id.asc()).limit(limit).offset(offset).all()
    wins = maintenance.windows(p.tenant)
    return envelope(
        [{"ts": r.ts, "metric": r.name, "monitor": r.entity or "", "value": r.value,
          "state": _sample_state(r, wins)} for r in rows],
        total, limit, offset,
    )


def _sample_state(row, wins) -> str:
    """up | down | planned | unknown, and "" where up/down is meaningless.

    `planned` beats up/down on purpose: a sample inside a maintenance window
    is excluded from availability whichever way it went, so reporting it as
    `up` would let a client reconstruct a different denominator from the one
    the dashboard used.
    """
    if row.name != "up":
        return "unknown" if row.value is None else ""
    when = parse_ts(row.ts)
    if when is not None and maintenance.covers(wins, when):
        return "planned"
    if row.value is None:
        return "unknown"
    return "up" if row.value else "down"


@router.get("/fixes", response_model=sa.Page[sa.FixRecord], summary="Repairs Vault ran",
            description="The fix log: every repair Vault ran, newest first, automatic or "
                        "asked for. Narrow with `host`, `action`, `by`, `ok` and an `hours` "
                        "window. `verified_at` is set only where a later observation confirmed "
                        "the condition had actually cleared.")
def fixes(host: str = "", action: str = "", by: str = "", ok: bool | None = None,
          hours: int = 24 * 30, limit: int = _DEFAULT_LIMIT, offset: int = 0,
          p: ApiPrincipal = READ, db: DbSession = Depends(get_db)):
    """Promoted out of `/v1/security`, where the fix log was one field on a
    large posture payload.

    It is the most trust-critical dataset Vault holds -- the record of
    what it changed on someone's machines -- and burying it inside another
    response made it awkward to audit and impossible to page. An MSP handing
    a client "here is everything we did" should not have to fetch a posture
    report to get it.

    `ok` and `verified_at` are kept apart rather than folded into one status.
    "The command exited zero" and "a later sweep confirmed the condition was
    gone" are different claims, and collapsing them is exactly the
    self-flattery the trust plan objects to in the handled count (§2.5D).
    """
    limit, offset = page(limit, offset, max_limit=500)
    pol = retention.policy(p.tenant)
    hours = max(1, min(hours, pol["retention_days"] * 24))
    cutoff = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=hours)).isoformat()
    query = db.query(m.Fix).filter(m.Fix.tenant_id == p.tenant.id, m.Fix.ts >= cutoff)
    if host:
        query = query.filter(m.Fix.host == host)
    if action:
        query = query.filter(m.Fix.action == action)
    if by:
        query = query.filter(m.Fix.by == by)
    if ok is not None:
        query = query.filter(m.Fix.ok == ok)
    total = query.count()
    rows = query.order_by(m.Fix.ts.desc(), m.Fix.id.desc()).limit(limit).offset(offset).all()
    out = []
    for f in rows:
        first = (f.out or "").strip().splitlines()[:1]
        out.append({"ts": f.ts, "host": f.host, "action": f.action, "label": f.label or "",
                    "arg": f.arg or "", "by": f.by or "auto", "ok": bool(f.ok),
                    "verified_at": f.verified_at.isoformat() if f.verified_at else None,
                    "reboot_needed": bool(f.reboot_needed),
                    "detail": first[0][:200] if first else ""})
    return envelope(out, total, limit, offset)


@router.get("/offboarding", summary="What a leaver still has on the network",
            description="Devices tagged to a person, and which of them are still "
                        "appearing after their recorded leave date. Ownership is only "
                        "ever what someone typed — Vault can see a laptop, not whose "
                        "it is. Omit `owner` to list who has devices tagged to them, "
                        "including how many are untagged.")
def offboarding_sweep(owner: str = "", left_on: str = "",
                      p: ApiPrincipal = READ, db: DbSession = Depends(get_db)):
    """The checklist for someone leaving, and the finding when it was skipped.

    "Your former office manager's laptop has been on this network for six
    weeks" is the demo. It is also a real control: unauthorised device
    detection and access review are on every questionnaire, and a
    signed-off checklist is an evidence artifact.
    """
    if not owner:
        return {"owners": offboarding.owners(db, p.tenant)}
    return offboarding.sweep(db, p.tenant, owner, left_on or None)


@router.get("/sla", summary="ISP outages and the service credit they owe",
            description="Availability of the access circuit measured from inside the "
                        "network, the outages behind it with their cause, and the credit "
                        "owed against a recorded commitment. Only outages attributed to "
                        "the provider are claimed. Add `format=letter` for a claim letter "
                        "that states what it excludes.")
def sla_credit(from_day: str = "", to_day: str = "", format: str = "json",
               p: ApiPrincipal = READ, db: DbSession = Depends(get_db)):
    """The evidence for a service-credit claim, and the letter to send.

    Most SMBs never claim: assembling the evidence costs more than the
    credit. Vault measures from the customer side of the demarcation
    point, which is the vantage the provider cannot dispute.

    Only `isp`-attributed minutes are claimed, and the others are reported
    rather than dropped. A claim that quietly includes the customer's own
    outages is one the provider rejects and remembers.
    """
    today = dt.datetime.now(dt.timezone.utc).date()
    first = from_day or today.replace(day=1).isoformat()
    last_d = to_day or today.isoformat()
    try:
        dt.date.fromisoformat(first), dt.date.fromisoformat(last_d)
    except ValueError:
        raise HTTPException(422, "from_day and to_day must be YYYY-MM-DD")

    pack = sla.credit(db, p.tenant, first, last_d)
    if format == "letter":
        return Response(content=sla.claim_letter(p.tenant, pack), media_type="text/plain")
    return pack


@router.get("/eol", summary="Systems past or nearing end of support",
            description="Every monitored server's operating system against its vendor "
                        "support date, with the counts a budget conversation needs. "
                        "Anything the dataset does not recognise is reported as unknown, "
                        "never as supported, and every answer carries the date the "
                        "dataset was last reviewed.")
def eol_survey(p: ApiPrincipal = READ, db: DbSession = Depends(get_db)):
    """Which servers stop receiving security patches, and roughly when.

    Two readers, which is why it exists: the IT contact cares about the
    patches, and whoever approves spending cares that it implies a
    replacement. The second is the person an MSP needs in front of.

    `unknown` is a first-class answer here for the usual reason. Reference
    data goes stale silently in a way a measurement does not, so a
    distribution the table has not heard of resolves to unknown carrying
    the dataset's own age, never to supported.
    """
    hosts = [h.last or {} for h in db.query(m.Host).filter_by(tenant_id=p.tenant.id).all()]
    for host, row in zip(db.query(m.Host).filter_by(tenant_id=p.tenant.id).all(), hosts):
        row.setdefault("name", host.name)
    return eol.survey(hosts)


@router.get("/compliance", summary="What Vault can evidence, by framework",
            description="Every control an assessor for the named framework asks about, "
                        "answered from the latest sweep — including the ones Vault "
                        "cannot answer, with the reason. Omit `framework` for all of "
                        "them. Add `format=csv` for a spreadsheet to paste from.")
def compliance_pack(framework: str = "", format: str = "json",
                    p: ApiPrincipal = READ, db: DbSession = Depends(get_db)):
    """The evidence pack behind an insurance renewal or a client security review.

    The gaps are returned, not filtered out. A pack listing only the
    questions Vault can answer looks complete, and an MSP caught
    over-claiming to an underwriter never opens the document again — so
    `outside_scope` rows travel with their reason attached.

    CSV because the person filling in the actual form is copying and
    pasting. It is the same rows as the JSON, from the same `controls.pack`,
    so the PDF an MSP sends and the spreadsheet their client works from
    cannot disagree.
    """
    if framework and framework not in controls.FRAMEWORKS:
        raise HTTPException(404, "unknown framework")
    scan = db.query(m.Scan).filter_by(tenant_id=p.tenant.id).order_by(m.Scan.ts.desc()).first()
    last = (scan.raw if scan else {}) or {}
    rows = controls.pack(last, framework or None)

    if format == "csv":
        buf = io.StringIO()
        writer = csv.writer(buf)
        writer.writerow(["control", "question", "status", "what Vault holds",
                         "referenced as", "as of"])
        for r in rows:
            writer.writerow([r["title"], r["question"], r["status"], r["note"],
                             "; ".join(r["references"]), scan.ts if scan else ""])
        writer.writerow([])
        writer.writerow([controls.DISCLAIMER])
        return Response(content=buf.getvalue(), media_type="text/csv",
                        headers={"Content-Disposition":
                                 'attachment; filename="vault-compliance.csv"'})

    return {
        "framework": framework or "all",
        "frameworks": controls.FRAMEWORKS,
        "as_of": scan.ts if scan else None,
        "tally": controls.tally(rows),
        "disclaimer": controls.DISCLAIMER,
        "controls": rows,
    }


@router.get("/reports", response_model=sa.Page[sa.Report], summary="List reports",
            description="Reports Vault has generated, newest first.")
def reports(limit: int = _DEFAULT_LIMIT, offset: int = 0, p: ApiPrincipal = READ,
            db: DbSession = Depends(get_db)):
    limit, offset = page(limit, offset)
    query = db.query(m.Report).filter_by(tenant_id=p.tenant.id)
    total = query.count()
    rows = query.order_by(m.Report.created.desc()).limit(limit).offset(offset).all()
    return envelope(
        [{"id": r.id, "kind": r.kind, "title": r.title, "period_days": r.period_days,
          "preset": r.preset, "sections": list(r.sections or []),
          "created": apideps.iso(r.created) or "",
          "score": (r.meta or {}).get("score")} for r in rows],
        total, limit, offset,
    )


@router.get("/reports/{report_id}", summary="Fetch one report",
            description="The rendered report, as a standalone HTML document. Add "
                        "`format=pdf` for the same document as a PDF, rendered from the "
                        "frozen HTML so the two cannot differ.",
            response_class=Response,
            responses={200: {"content": {"text/html": {}, "application/pdf": {}},
                             "description": "The report."}})
def report_html(report_id: str, format: str = "html", p: ApiPrincipal = READ,
                db: DbSession = Depends(get_db)):
    import pathlib
    from ..reports import pdf as pdf_mod

    row = db.query(m.Report).filter_by(tenant_id=p.tenant.id, id=report_id).first()
    if not row:
        raise HTTPException(404, "report not found")
    path = pathlib.Path(row.path)
    if not path.is_file():
        raise HTTPException(404, "report not found")
    if format == "pdf":
        try:
            out = pdf_mod.ensure_pdf(path)
        except pdf_mod.PdfUnavailable as e:
            raise HTTPException(503, f"PDF rendering is not available on this hub: {e}")
        except RuntimeError as e:
            raise HTTPException(500, f"PDF render failed: {e}")
        return Response(content=out.read_bytes(), media_type="application/pdf",
                        headers={"Content-Disposition": f'attachment; filename="{path.stem}.pdf"'})
    return Response(content=path.read_text(encoding="utf-8"), media_type="text/html")


@router.get("/jobs/{job_id}", response_model=sa.Job, summary="Check queued work",
            description="The state of work an earlier write handed to the sensor.")
def job(job_id: str, p: ApiPrincipal = READ, db: DbSession = Depends(get_db)):
    q = db.query(m.Queue).filter_by(tenant_id=p.tenant.id, id=job_id).first()
    if not q:
        raise HTTPException(404, "job not found")
    result = q.result if isinstance(q.result, dict) else None
    return sa.Job(id=q.id, status=q.status, queued_at=apideps.iso(q.ts),
                  finished_at=apideps.iso(q.finished_at),
                  ok=None if result is None else bool(result.get("ok")))


# --------------------------------------------------------------------------
# Writes
# --------------------------------------------------------------------------
# Every write here already exists as a dashboard action, is reversible, and goes
# through the dashboard's own helper. Nothing that reboots, upgrades, blocks a
# subnet, changes an SSH policy, removes a device, quarantines one, edits
# account configuration or touches billing is reachable from v1 at all — see
# the API guide's "Not in v1" section, which is generated from the same list.

class _Body(BaseModel):
    """Every request body in v1 rejects a field it does not know.

    Silently ignoring an unrecognised key is the worse failure: a caller who
    sends `{"trusted": true, "quarantined": true}` would get a 200 and believe
    Vault had isolated the device. It has not -- isolating a device is not in
    v1 at all -- so the call must fail loudly.
    """

    model_config = ConfigDict(extra="forbid")


class AlertOpBody(_Body):
    hours: int | None = Field(
        None, description="Snooze length in hours, 1 to 168. Ignored by the other actions; "
                          "defaults to 8 when snoozing.",
    )


@router.post("/alerts/{key:path}/{op}", response_model=sa.Alert, summary="Act on an alert",
            description="`op` is one of `ack`, `snooze`, `resolve` or `reopen`. All four are "
                        "reversible, and all four are recorded against this token.")
def alert_op(key: str, op: str, body: AlertOpBody = AlertOpBody(), p: ApiPrincipal = WRITE,
             db: DbSession = Depends(get_db)):
    if op not in ALERT_OPS:
        raise HTTPException(404, "unknown alert action")
    hours = body.hours
    if op == "snooze":
        hours = hours or 8
        if not 1 <= hours <= 168:
            raise HTTPException(422, "hours must be between 1 and 168")
    a = apply_alert_op(db, p.tenant, key, op, p.who, p.role, hours, p.ip)
    if a is None:
        raise HTTPException(404, "alert not found")
    db.commit()
    return _alert(a, _netrefs(db, p.tenant.id))


class DeviceWrite(_Body):
    trusted: bool | None = Field(None, description="Mark this device as one you recognise.")
    label: str | None = Field(None, max_length=200, description="Your own name for it.")
    always_on: bool | None = Field(
        None, description="Whether Vault should alert when this device goes quiet.",
    )
    owner: str | None = Field(
        None, max_length=120,
        description="Who holds this device, for an offboarding sweep later. Typed by a "
                    "person and never inferred: a hostname is a guess, and a guess here "
                    "puts the wrong name on a document somebody signs. Send \"\" to clear.",
    )


@router.post("/devices/{mac}", response_model=sa.Device, summary="Update a device",
            description="Set `trusted`, `label` or `always_on`. These are the only device fields "
                        "v1 can change; removing or isolating a device is not exposed.")
def device_write(mac: str, body: DeviceWrite, network: str = "", p: ApiPrincipal = WRITE,
                 db: DbSession = Depends(get_db)):
    payload = body.model_dump(exclude_unset=True)
    if not payload:
        raise HTTPException(422, "nothing to change")
    net = _resolve_net(db, p.tenant.id, network)
    d, _row_net = apply_device_update(db, p.tenant, mac, payload, who=p.who, role=p.role,
                                      ip=p.ip, net=net)
    if d is None:
        raise HTTPException(404, "device not found")
    return _device(d, _netrefs(db, p.tenant.id))


@router.post("/scans", response_model=sa.Queued, status_code=202, summary="Trigger a scan",
            description="Asks the sensor to scan now rather than waiting for its next cycle. "
                        "Returns a job id — poll `/v1/jobs/{id}`.")
def trigger_scan(p: ApiPrincipal = WRITE, db: DbSession = Depends(get_db)):
    return queue_and_audit(db, p.tenant, p.who, p.role, "/api/run", "run", ip=p.ip)


@router.post("/speedtest", response_model=sa.Queued, status_code=202,
             summary="Trigger a speed test",
             description="Asks the sensor to measure the internet connection now. Returns a job "
                         "id — poll `/v1/jobs/{id}`.")
def trigger_speedtest(p: ApiPrincipal = WRITE, db: DbSession = Depends(get_db)):
    return queue_and_audit(db, p.tenant, p.who, p.role, "/api/speedtest", "speedtest", ip=p.ip)
