"""Unauthenticated public/ops surface: `/act`, `/status/<slug>`, `/hb`,
`/r/<token>`, `/metrics`, and the sensor install channel (`/install`,
`/sensor.whl`, `/sensor.whl.sha256`).

Every route here is reachable with no session cookie -- each authenticates
on its own token/opt-in instead (an ActToken, a Heartbeat's per-name token,
a Report's share_token, `Site.public`, or the metrics gate below) and is
rate-limited via `ratelimit.allow` so a leaked/guessed token can't be
hammered. Registered in `main.py` *before* the SPA catch-all mount, so
these paths never fall through to `index.html`.
"""
import datetime as dt
import hmac
import pathlib
import re

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy.orm import Session as DbSession

from .. import acttokens, audit, installer, models as m, networks, ratelimit
from ..config import settings
from ..db import get_db
from ..live import bus
from ..payloads._util import _lax_cutoff
from ..payloads.overview import sensor_status
from ..reports import sections as S
from ..scans import latest_scan, parse_ts

router = APIRouter(tags=["public"])

_now = lambda: dt.datetime.now(dt.timezone.utc)


# --------------------------------------------------------------------------
# small HTML helper, shared by /act and /r
# --------------------------------------------------------------------------

def _brand_page(msg: str, code: int = 200) -> Response:
    html = (
        "<!doctype html><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width,initial-scale=1'>"
        "<body style='font-family:-apple-system,Inter,Segoe UI,sans-serif;padding:24px;"
        "background:#f3f6fb;color:#0f1a2b'>"
        "<h2 style='color:#1d6fe0;margin:0 0 12px'>Vault</h2>"
        f"<p style='font-size:18px;margin:0'>{S.esc(msg)}</p></body>"
    )
    return Response(content=html, media_type="text/html", status_code=code)


# --------------------------------------------------------------------------
# /act/<token>/<action> -- trust / snooze / ack
# --------------------------------------------------------------------------
# Controller ruling (fix round 1): "fix" is not a push-button action. A
# public, unauthenticated link must never be able to trigger an actual fix
# run -- only the reversible trust/snooze/ack writes. acttokens.mint()
# refuses to mint a "fix" action at all (ALLOWED_ACTIONS), and this route
# no longer has a fix branch, so even a token minted before this change
# (or a hand-crafted URL) 400s here rather than running anything.
#
# Each helper returns the confirmation message on success, or None when the
# target (a device, an alert) no longer exists -- the route turns a missing
# *device* into a 404 page (nothing to trust), but a missing *alert* into a
# 410 (the same "expired or already used" family as a burnt token, per the
# fix-round ruling: no Edit/Audit row is written for a write that didn't
# happen). They mirror the exact writes the dashboard itself performs
# (write.py's update_device / alert_op), down to the Edit row shape the
# sensor's edit-replay contract expects, and publish the same live-bus
# frame the dashboard routes do so connected dashboards update immediately.

def _apply_trust(db: DbSession, tenant_id: str, mac: str, who: str) -> str | None:
    d = db.query(m.Device).filter_by(tenant_id=tenant_id, mac=mac).first()
    if not d:
        return None
    d.trusted = True
    db.add(m.Edit(tenant_id=tenant_id, who=who, path="/api/device",
                   body={"mac": mac, "label": d.label, "trusted": d.trusted, "always_on": d.always_on}))
    audit.log(db, tenant_id, who, "", "device.update", target=mac, body={"trusted": True})
    bus.publish(tenant_id, "device", {"mac": mac, "event": "update", "ip": d.ip, "label": d.label})
    return f"Marked {d.label or d.name or mac} as yours (trusted). No more alerts for it."


def _apply_alert_snooze(db: DbSession, tenant_id: str, key: str, who: str, hours: int = 24) -> str | None:
    a = networks.find_alert(db, tenant_id, key)
    if not a:
        return None
    until = (_now() + dt.timedelta(hours=hours)).isoformat(timespec="seconds")
    a.status = "snoozed"
    a.snoozed_until = until
    db.add(m.Edit(tenant_id=tenant_id, who=who, path="/api/alert",
                   body={"key": key, "action": "snooze", "hours": hours}))
    audit.log(db, tenant_id, who, "", "alert.snooze", target=key, body={"hours": hours})
    bus.publish(tenant_id, "alert", {"key": a.key, "sev": a.sev, "msg": a.msg, "status": a.status})
    return f"Ignored for {hours} hours. It stays in the unverified list."


def _apply_alert_ack(db: DbSession, tenant_id: str, key: str, who: str) -> str | None:
    a = networks.find_alert(db, tenant_id, key)
    if not a:
        return None
    a.status = "acked"
    a.snoozed_until = None
    db.add(m.Edit(tenant_id=tenant_id, who=who, path="/api/alert", body={"key": key, "action": "ack", "hours": None}))
    audit.log(db, tenant_id, who, "", "alert.ack", target=key, body={})
    bus.publish(tenant_id, "alert", {"key": a.key, "sev": a.sev, "msg": a.msg, "status": a.status})
    return "Acknowledged."


#: (kind, action) -> (the apply helper, the entity key it needs, whether a
#: missing target is an alert-family 410 rather than a device 404).
def _act_plan(kind: str, action: str, target: str):
    if kind == "device" and action == "trust":
        return _apply_trust, target, False
    if kind == "device" and action == "snooze":
        return _apply_alert_snooze, f"lan:new:{target}", True
    if kind == "alert" and action == "ack":
        return _apply_alert_ack, target, True
    if kind == "alert" and action == "snooze":
        return _apply_alert_snooze, target, True
    return None, "", False


def _act_target_exists(db: DbSession, tenant_id: str, kind: str, entity: str) -> bool:
    if kind == "device" and not entity.startswith("lan:new:"):
        return db.query(m.Device).filter_by(tenant_id=tenant_id, mac=entity).first() is not None
    # LAN alert keys are namespaced per network (Task 51), and an ntfy button
    # only ever knew the mac, so match on the canonical form too.
    return networks.find_alert(db, tenant_id, entity) is not None


@router.get("/act/{token}/{action}")
def act(token: str, action: str, db: DbSession = Depends(get_db)):
    if not ratelimit.allow(f"act:{token[:8]}", 10, 60):
        return _brand_page("Too many attempts. Try again in a minute.", 429)

    # Read the row (without burning it) to learn what this token points at,
    # and check the target still exists BEFORE consuming: a push button for
    # a device that has since been forgotten should say "nothing to do here
    # any more", not burn a link that was neither expired nor used.
    peek = db.get(m.ActToken, token)
    if (peek is not None and peek.kind in ("device", "alert") and not peek.used
            and action in (peek.actions or [])):
        apply, entity, alert_action = _act_plan(peek.kind, action, peek.target)
        # A plan-less (kind, action) pair falls through to `consume`, which
        # decides between "not permitted for this token" (410) and a token
        # that really does carry it (400 "unknown action") -- the pre-check
        # never invents either verdict.
        if apply is not None and not _act_target_exists(db, peek.tenant_id, peek.kind, entity):
            if alert_action:
                return _brand_page("Nothing to do here any more, that alert is gone.", 410)
            return _brand_page("That's no longer here, nothing to do.", 404)

    try:
        row = acttokens.consume(db, token, action)
    except acttokens.ActTokenError:
        db.rollback()
        return _brand_page("This link expired or was already used.", 410)

    who = f"act:{row.kind}"
    apply, entity, alert_action = _act_plan(row.kind, action, row.target)
    if apply is None:
        db.rollback()
        return _brand_page("Unknown action.", 400)

    msg = apply(db, row.tenant_id, entity, who)
    if msg is None:
        # Raced away between the pre-check and the write.
        db.rollback()
        if alert_action:
            return _brand_page("Nothing to do here any more, that alert is gone.", 410)
        return _brand_page("That's no longer here, nothing to do.", 404)

    db.commit()
    return _brand_page(msg)


# --------------------------------------------------------------------------
# /status/<slug> -- public status page, opted-in sites only
# --------------------------------------------------------------------------

_STATUS_HB_BARS = 90
_GREEN, _RED = "#16a34a", "#e02424"


def _uptime_pct(db: DbSession, tenant_id: str, site_name: str, days: int) -> float | None:
    cutoff = _now() - dt.timedelta(days=days)
    rows = (
        db.query(m.Metric.ts, m.Metric.value)
        .filter(m.Metric.tenant_id == tenant_id, m.Metric.name == "up", m.Metric.entity == site_name,
                m.Metric.ts >= _lax_cutoff(cutoff))
        .all()
    )
    pts = [v for ts, v in rows if (when := parse_ts(ts)) is not None and when >= cutoff]
    if not pts:
        return None
    return round(100 * sum(1 for v in pts if v) / len(pts), 2)


def _hb_bars(db: DbSession, tenant_id: str, site_name: str) -> str:
    rows = (
        db.query(m.Metric.value)
        .filter_by(tenant_id=tenant_id, name="up", entity=site_name)
        .order_by(m.Metric.ts.desc())
        .limit(_STATUS_HB_BARS)
        .all()
    )
    values = [v for (v,) in rows][::-1]
    return "".join(f'<i style="background:{_GREEN if v else _RED}"></i>' for v in values)


@router.get("/status/{slug}")
def status_page(slug: str, db: DbSession = Depends(get_db)):
    if not ratelimit.allow(f"status:{slug}", 60, 60):
        raise HTTPException(429, "too many requests")
    tenant = db.query(m.Tenant).filter_by(slug=slug).first()
    if not tenant or (tenant.settings or {}).get("status_page") is False:
        raise HTTPException(404, "not found")

    sites = db.query(m.Site).filter_by(tenant_id=tenant.id, public=True).order_by(m.Site.name).all()
    scan = latest_scan(db, tenant.id)
    last_check = (scan.ts or "")[:16].replace("T", " ") if scan else ""

    rows = []
    for s in sites:
        ok = (s.last or {}).get("ok")
        label = S.esc(s.name.split(" (")[0])
        up24 = _uptime_pct(db, tenant.id, s.name, 1)
        up7 = _uptime_pct(db, tenant.id, s.name, 7)
        color = _GREEN if ok else _RED
        rows.append(
            '<div class="row"><div class="n"><span class="dot" style="background:%s"></span>%s</div>'
            '<div class="st" style="color:%s">%s</div>'
            '<div class="up">%s%% <small>24h</small> &middot; %s%% <small>7d</small></div>'
            '<div class="hb">%s</div></div>'
            % (color, label, color, "Operational" if ok else "Down",
               up24 if up24 is not None else "&mdash;", up7 if up7 is not None else "&mdash;",
               _hb_bars(db, tenant.id, s.name))
        )

    allok = all((s.last or {}).get("ok") for s in sites) if sites else True
    css = (
        "body{margin:0;background:#f3f6fb;color:#0f1a2b;font-family:-apple-system,Inter,Segoe UI,sans-serif}"
        "main{max-width:760px;margin:0 auto;padding:32px 18px}h1{font-size:22px;margin:0 0 6px}"
        ".all{padding:14px 18px;border-radius:12px;color:#fff;font-weight:600;margin:18px 0;background:%s}"
        ".row{display:grid;grid-template-columns:1.4fr .7fr 1fr 2fr;gap:12px;align-items:center;background:#fff;"
        "border:1px solid #d9e2ef;border-radius:12px;padding:14px 16px;margin:10px 0}"
        ".n{font-weight:600}.dot{display:inline-block;width:9px;height:9px;border-radius:50%%;margin-right:8px}"
        ".up{font-size:13px}.up small{color:#5b6b82}.hb{display:flex;gap:2px;height:22px}.hb i{flex:1;border-radius:2px}"
        ".f{color:#5b6b82;font-size:12px;margin-top:20px}"
        "@media(max-width:600px){.row{grid-template-columns:1fr 1fr}.hb{grid-column:1/3}}"
    ) % (_GREEN if allok else _RED)
    body = "".join(rows) if rows else "<p>No public services.</p>"
    html = (
        "<!doctype html><html><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width,initial-scale=1'>"
        "<title>Service status</title>"
        "<meta http-equiv='refresh' content='120'><style>" + css + "</style></head><body><main>"
        "<h1>Service status</h1>"
        f'<div class="u">Monitored by Vault &middot; last check {S.esc(last_check)}</div>'
        f'<div class="all">{"All systems operational" if allok else "Some services are down"}</div>'
        + body +
        '<div class="f">Green = passed check, red = failed.</div></main></body></html>'
    )
    return Response(content=html, media_type="text/html")


# --------------------------------------------------------------------------
# /hb/<name>/<token> -- heartbeat ping
# --------------------------------------------------------------------------

@router.get("/hb/{name}/{token}")
def heartbeat(name: str, token: str, db: DbSession = Depends(get_db)):
    if not ratelimit.allow(f"hb:{name}", 60, 60):
        raise HTTPException(429, "too many requests")
    # No tenant in the URL (the cron job that curls this only knows the
    # heartbeat's name+token), so the row is found by name and confirmed by
    # a constant-time token compare -- (tenant_id, name) is unique, but the
    # same name could exist under a different tenant, so every same-named
    # row is tried rather than assuming the first one found is right.
    candidates = db.query(m.Heartbeat).filter_by(name=name).all()
    hb = next((h for h in candidates if hmac.compare_digest(h.token, token)), None)
    if not hb:
        raise HTTPException(404, "unknown heartbeat")
    hb.last_ping = _now()
    db.commit()
    return {"ok": True, "name": name}


# --------------------------------------------------------------------------
# /r/<share_token> -- shared report link
# --------------------------------------------------------------------------

@router.get("/r/{share_token}")
def shared_report(share_token: str, db: DbSession = Depends(get_db)):
    if not ratelimit.allow(f"r:{share_token[:8]}", 30, 60):
        raise HTTPException(429, "too many requests")
    report = db.query(m.Report).filter_by(share_token=share_token).first()
    if not report or not report.share_expires or report.share_expires < _now():
        return _brand_page("This report link expired or was rotated.", 410)
    path = pathlib.Path(report.path)
    if not path.is_file():
        return _brand_page("This report link expired or was rotated.", 410)
    return Response(content=path.read_text(encoding="utf-8"), media_type="text/html")


# --------------------------------------------------------------------------
# /metrics -- Prometheus text exposition, every tenant, tenant-labelled
# --------------------------------------------------------------------------

def _mname(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", (s or "").lower()).strip("_") or "unknown"


def _metrics_body(db: DbSession) -> str:
    lines: list[str] = []
    for t in db.query(m.Tenant).order_by(m.Tenant.slug).all():
        slug = t.slug
        scan = latest_scan(db, t.id)
        raw = scan.raw if scan else {}

        for s in raw.get("sites") or []:
            n = _mname(s.get("name") or "")
            lines.append(f'guardian_site_up{{tenant="{slug}",site="{n}"}} {1 if s.get("ok") else 0}')
            lines.append(f'guardian_site_latency_seconds{{tenant="{slug}",site="{n}"}} {s.get("latency_s") or 0}')
            lines.append(f'guardian_site_cert_days{{tenant="{slug}",site="{n}"}} {s.get("cert_days_left") or 0}')

        for h in raw.get("ssh") or []:
            n = _mname(h.get("name") or "")
            lines.append(f'guardian_host_reachable{{tenant="{slug}",host="{n}"}} {1 if h.get("reachable") else 0}')
            if h.get("reachable"):
                lines.append(f'guardian_host_disk_pct{{tenant="{slug}",host="{n}"}} {h.get("disk_pct") or 0}')
                lines.append(f'guardian_host_mem_pct{{tenant="{slug}",host="{n}"}} {h.get("mem_pct") or 0}')
                lines.append(f'guardian_host_load_per_core{{tenant="{slug}",host="{n}"}} {h.get("load_per_core") or 0}')
                lines.append(f'guardian_host_failed_ssh_24h{{tenant="{slug}",host="{n}"}} {h.get("failed_ssh_24h") or 0}')
                sec_updates = len((h.get("sec") or {}).get("security_updates") or [])
                lines.append(f'guardian_host_security_updates{{tenant="{slug}",host="{n}"}} {sec_updates}')

        for sev in ("CRIT", "WARN", "INFO"):
            cnt = db.query(m.Alert).filter_by(tenant_id=t.id, sev=sev, status="open").count()
            lines.append(f'guardian_alerts{{tenant="{slug}",severity="{sev}"}} {cnt}')

        lan = raw.get("lan") or {}
        lines.append(f'guardian_lan_devices_online{{tenant="{slug}"}} {lan.get("live_count") or 0}')
        unverified = len((raw.get("home") or {}).get("unverified") or [])
        lines.append(f'guardian_lan_unverified{{tenant="{slug}"}} {unverified}')

        sp = raw.get("speed") or {}
        lines.append(f'guardian_speed_down_mbps{{tenant="{slug}"}} {sp.get("down_mbps") or sp.get("down") or 0}')
        lines.append(f'guardian_speed_up_mbps{{tenant="{slug}"}} {sp.get("up_mbps") or sp.get("up") or 0}')

        cutoff = _lax_cutoff(_now() - dt.timedelta(hours=24))
        dns_blocked = (
            db.query(m.DnsQuery)
            .filter(m.DnsQuery.tenant_id == t.id, m.DnsQuery.blocked.is_(True), m.DnsQuery.ts >= cutoff)
            .count()
        )
        lines.append(f'guardian_dns_blocked_24h{{tenant="{slug}"}} {dns_blocked}')

        scan_ts = parse_ts(scan.ts) if scan else None
        age = int((_now() - scan_ts).total_seconds()) if scan_ts else 0
        lines.append(f'guardian_data_age_seconds{{tenant="{slug}"}} {age}')

        status = sensor_status(db, t.id)
        sensor_up = 1 if status and status.status == "active" else 0
        lines.append(f'guardian_sensor_up{{tenant="{slug}"}} {sensor_up}')

    return "\n".join(lines) + "\n"


_METRICS_LOCAL_HOSTS = {"127.0.0.1", "::1", "testclient"}  # "testclient" is Starlette TestClient's client.host


@router.get("/metrics")
def metrics(request: Request, token: str | None = None, db: DbSession = Depends(get_db)):
    if settings.metrics_token:
        auth = request.headers.get("authorization", "")
        bearer = auth.split(" ", 1)[1] if auth.lower().startswith("bearer ") else ""
        ok = (token and hmac.compare_digest(token, settings.metrics_token)) or \
             (bearer and hmac.compare_digest(bearer, settings.metrics_token))
        if not ok:
            raise HTTPException(401, "unauthorized")
    else:
        client_host = request.client.host if request.client else ""
        if client_host not in _METRICS_LOCAL_HOSTS:
            raise HTTPException(403, "forbidden -- set GUARDIAN_METRICS_TOKEN to expose /metrics publicly")

    return Response(content=_metrics_body(db), media_type="text/plain; version=0.0.4")


# --------------------------------------------------------------------------
# /install, /sensor.whl, /sensor.whl.sha256 -- the sensor install channel
# --------------------------------------------------------------------------
# These three exist so the dashboard never has to name a host the hub's
# operator does not control. `installer.py`'s module docstring has the full
# reasoning; the routing-critical part is that they live in *this* router,
# which `main.py` includes before `_mount_web`'s SPA catch-all. Put them
# anywhere later and `GET /install` returns `index.html` -- exactly the bug
# that made the marketing site's `curl … | sh` line pipe HTML into a shell.

#: scheme://host[:port] with nothing exotic in it. The origin is echoed into
#: a single-quoted sh string, so a host carrying a quote (or a space, or a
#: newline) must never reach the renderer -- validated rather than escaped,
#: because no legitimate Host header needs any of those characters.
_ORIGIN_RE = re.compile(r"^https?://(\[[0-9A-Fa-f:]+\]|[A-Za-z0-9._-]+)(:\d{1,5})?$")

_INSTALL_LOOPBACK = ("127.0.0.1", "localhost", "::1", "[::1]", "testserver", "testclient")


def _request_origin(request: Request) -> str:
    """The origin this request actually arrived on -- NOT `settings.base_url`.

    `settings.base_url` is a deploy-time constant (`https://my.guarder.io`
    today) and would hand a customer who typed a different hostname a script
    that downloads from somewhere they never asked for. uvicorn runs with
    `--proxy-headers`, so behind nginx `request.url` already carries the
    forwarded scheme and the client's Host."""
    return f"{request.url.scheme}://{request.url.netloc}".rstrip("/")


def _plain(text: str, code: int = 200) -> Response:
    return Response(content=text, media_type="text/plain; charset=utf-8", status_code=code)


def _sensor_wheel():
    built = installer.wheel()
    if built is None:
        raise HTTPException(
            404,
            "this hub does not ship the sensor package -- install guardian-sensor from your own source tree",
        )
    return built


@router.get("/install", include_in_schema=False)
def install_script(request: Request):
    """The `curl … | sh` installer, rendered for whichever origin asked."""
    if not ratelimit.allow("install", 120, 60):
        return _plain("# too many requests -- try again in a minute\n", 429)
    origin = _request_origin(request)
    if not _ORIGIN_RE.match(origin):
        return _plain("# bad Host header\n", 400)
    host = request.url.hostname or ""
    if request.url.scheme != "https" and host not in _INSTALL_LOOPBACK:
        # Served over plain http, a MITM rewrites the script before it reaches
        # the shell that runs it as root. Refuse rather than hand it over.
        return _plain(
            f"# Vault: /install is served over https only. Try https://{request.url.netloc}/install\n",
            403,
        )
    version, _data, sha256 = _sensor_wheel()
    return _plain(installer.script(origin, installer.wheel_filename(version), sha256))


@router.get("/sensor.whl", include_in_schema=False)
def sensor_wheel():
    """The sensor package itself: a py3-none-any wheel, built from the
    `sensor/` tree this hub was deployed with."""
    if not ratelimit.allow("sensor_whl", 120, 60):
        raise HTTPException(429, "too many requests")
    version, data, _sha = _sensor_wheel()
    return Response(
        content=data,
        media_type="application/octet-stream",
        headers={
            # pip parses the version out of the *filename*, so the download
            # has to land under the real wheel name, not "sensor.whl".
            "Content-Disposition": f'attachment; filename="{installer.wheel_filename(version)}"',
            "Cache-Control": "no-cache",
        },
    )


@router.get("/sensor.whl.sha256", include_in_schema=False)
def sensor_wheel_sha256():
    """`sha256sum`-format checksum for /sensor.whl. The installer checks the
    copy embedded in its own script (same TLS channel, one fewer round trip);
    this endpoint is for anyone verifying the download by hand or in CI."""
    if not ratelimit.allow("sensor_whl", 120, 60):
        raise HTTPException(429, "too many requests")
    version, _data, sha256 = _sensor_wheel()
    return _plain(f"{sha256}  {installer.wheel_filename(version)}\n")
