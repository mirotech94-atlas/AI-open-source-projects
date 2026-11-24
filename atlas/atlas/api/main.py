"""HTTP surface.

Public (443, signature-verified):   /webhooks/*  /healthz
Public (443, token-verified):       /media/{token}  (atlas/api/media.py)
Public (443, secret-in-body):       /hooks/tradingview  (atlas/api/tradingview.py)
Public (443, CORS + caps):          /public/site-chat/<slug>  (atlas/api/site_chat.py)
Public (443, CORS + caps):          /public/call-me/<slug>  (atlas/api/call_me.py)
Public (443, Stripe-checked):       /public/academy/access, /public/academy/file/<token>
                                    (atlas/api/academy.py)
Mesh only (NetBird, port 8080):     everything else
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import ipaddress
import json
import logging
import os
import re
import time
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from xml.sax.saxutils import escape as xml_escape

import httpx
from fastapi import FastAPI, Header, HTTPException, Request, WebSocket
from fastapi.responses import FileResponse, PlainTextResponse, Response
from fastapi.staticfiles import StaticFiles

from ..boot import boot
from ..channels import slack_socket
from ..kernel import scheduler
from ..kernel.agents import route
from ..kernel.approvals import decide
from ..kernel.registry import REGISTRY
from ..modules.realestate.api import router as realestate_router
from . import peers, ui_paths
from .academy import router as academy_router
from .academy_support import router as academy_support_router
from .academy_videos import router as academy_videos_router
from .agency import router as agency_router
from .agency_contact import router as agency_contact_router
from .agency_previews import router as agency_previews_router
from .airglass import router as airglass_router
from .call_me import router as call_me_router
from .clips import router as clips_router
from .dashboard import router as dashboard_router
from .deliver import router as deliver_router
from .handoffs import router as handoffs_router
from .hud import router as hud_router
from .ig_funnels import router as ig_funnel_router
from .learn import router as learn_router
from .mac_agent import router as mac_agent_router
from .media import router as media_router
from .projects import router as projects_router
from .public_ping import router as public_ping_router
from .selfmodel import router as selfmodel_router
from .site_chat import router as site_chat_router
from .slack_events import router as slack_events_router
from .stripe_mapfront import router as stripe_mapfront_router
from .system_check import router as system_check_router
from .tradingview import router as tradingview_router
from .vault_export import router as vault_router
from .views import router as views_router
from .voice import router as voice_router
from .voice_offline import router as voice_offline_router
from .webild import router as webild_router
from .whatsapp import router as whatsapp_router
from .workboard import router as workboard_router


@asynccontextmanager
async def lifespan(_app: FastAPI):
    """Boot, run, then give modules a chance to close their clients."""
    await boot()
    # Only here, in the API server: an approval a killed process left at
    # 'approved' is closed as failed with a receipt (kernel.approvals).
    # Retried: 2026-09-28 the API came up while Postgres was still recovering
    # from a full disk, the one reap failed, and the run it should have closed
    # read as in flight and held the next deploy.
    for attempt in range(12):
        try:
            from ..kernel import state as _state
            from ..kernel.approvals import reap_orphans
            live = isinstance(_state.backend(), _state.PostgresBackend)
            if live and (reaped := await reap_orphans()):
                logging.getLogger("atlas.api").warning(
                    "closed approvals cut short by the last restart: %s", reaped)
            from ..kernel.approvals import reap_abandoned_runs
            if live and (gone := await reap_abandoned_runs()):
                logging.getLogger("atlas.api").warning(
                    "runs abandoned by a killed process, outcome unknown: %s", gone)
            break
        except Exception:
            logging.getLogger("atlas.api").exception(
                "could not reap orphaned approvals (attempt %d of 12)", attempt + 1)
            await asyncio.sleep(10)
    # Slack without a public URL: the Socket Mode client, only when
    # SLACK_APP_TOKEN is set. Its check is registered either way, so a
    # missing token reads UNKNOWN with the reason, never absent.
    slack_socket.register_check()
    sock = slack_socket.start()
    task = asyncio.create_task(scheduler.loop())
    try:
        yield
    finally:
        # The loop first, so it starts nothing new; then every job the
        # branch runs in the background -- the weekly marketing pass, the
        # reasoning over findings, a detached scheduler step, a phone inbox
        # triage, a browser popup closing -- cancelled, waited for (bounded)
        # and named in the log if it was cut short.
        task.cancel()
        if sock is not None:
            sock.cancel()
        await asyncio.wait([t for t in (task, sock) if t is not None], timeout=10)
        slack_socket.unregister_check()
        from ..kernel.background import cancel_all
        await cancel_all()
        from ..boot import shutdown
        await shutdown()


def _docs_kwargs() -> dict[str, str | None]:
    """The schema and the interactive docs, off unless asked for.

    `/openapi.json` is a map of every route, parameter and model the API
    has -- a free reconnaissance of the mesh surface for anyone who reaches
    it. Nothing in ATLAS reads it, so production serves none of the three.
    `ATLAS_OPENAPI=on` turns them back on for local development only.
    """
    if os.getenv("ATLAS_OPENAPI", "").strip().lower() in ("1", "on", "true", "yes"):
        return {"openapi_url": "/openapi.json", "docs_url": "/docs",
                "redoc_url": "/redoc"}
    return {"openapi_url": None, "docs_url": None, "redoc_url": None}


app = FastAPI(title="ATLAS", lifespan=lifespan, **_docs_kwargs())
log = logging.getLogger("atlas.api")


class _NoAudioTokens(logging.Filter):
    """A secret in the path IS the gate on /webhooks/twilio/audio/{token},
    /media/{token}, /deliver/{checkout_session} and ATLAS Academy's
    /public/academy/file/{token}, and uvicorn's access log writes every
    request path. So is the checkout session in /public/academy/access's
    `session_id=` query, and the library token in /public/academy/library's
    `t=` query. Redacted before any handler sees it -- a log line must never
    be a way to the audio, the video, the paid file or a buyer's purchases.
    (/public/academy/recover and /public/academy/subscribe are POSTs: the email
    is in the body, which no access log writes. The subscribe-confirm and
    unsubscribe links carry their token in `t=`, redacted below.)"""

    _TOKEN = re.compile(
        r"(?<![\w.-])(/webhooks/twilio/audio/|/media/|/deliver/|/public/academy/file/)"
        r"[^\s?\"']+")
    _QUERY = re.compile(r"(?<=[?&])(session_id=|t=)[^\s&#\"']+")

    def _clean(self, text: str) -> str:
        return self._QUERY.sub(r"\1<token>", self._TOKEN.sub(r"\1<token>", text))

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.args, tuple):
            record.args = tuple(
                self._clean(a) if isinstance(a, str) else a for a in record.args)
        if isinstance(record.msg, str):
            record.msg = self._clean(record.msg)
        return True


logging.getLogger("uvicorn.access").addFilter(_NoAudioTokens())

# Paths reachable from the public internet. Everything else is mesh-only and is
# enforced below by source address, not only by the Caddyfile — a one-line edit
# to that file would otherwise publish the dashboard API.
# `/hooks/tradingview` is one exact route, not a `/hooks/` prefix: TradingView's
# alert intake, gated by the secret in its body (atlas/api/tradingview.py).
PUBLIC_PATHS = ("/webhooks/", "/healthz", "/media/", "/deliver/",
                "/hooks/tradingview")
# Matched whole, never as a prefix: "/hooks/tradingview/../api/overview" and
# "/hooks/tradingviewX" are mesh-only like everything else.
# `/webhooks/stripe-mapfront` sits under the /webhooks/ prefix already; it
# is named here too so the one Stripe route is on record as exact, and gated
# by its own signature (atlas/api/stripe_mapfront.py).
# ATLAS Academy's buyer page is one exact path (atlas/api/academy.py); its
# Stripe webhook, like Mapfront's, is on record here too.
# The store's contact and Help forms and the landing page's preview request
# post to /public/agency/contact (2026-11-14); the mailto: address stays on the
# page as plain text.
PUBLIC_EXACT = frozenset({"/hooks/tradingview", "/webhooks/stripe-mapfront",
                          "/webhooks/stripe-academy", "/public/academy/access",
                          "/public/academy/review",
                          # Self-service recovery (email + Stripe receipt number)
                          # and the one-hour purchase library it signs.
                          "/public/academy/recover", "/public/academy/library",
                          # The store's AI help chat (atlas/api/academy_support.py).
                          "/public/academy/chat",
                          # The store's video gallery: GET only, read-only
                          # (atlas/api/academy_videos.py).
                          "/public/academy/videos",
                          # Free updates signup (atlas/api/academy.py): the form
                          # post, the double opt-in confirm link and the
                          # one-click unsubscribe link. `t=` is redacted in logs.
                          "/public/academy/subscribe",
                          "/public/academy/subscribe/confirm",
                          "/public/academy/unsubscribe",
                          # The website contact form (atlas/api/agency_contact.py):
                          # one POST, stored first, delivered by the executor.
                          "/public/agency/contact",
                          # Public edge liveness: the edge rewrites exactly
                          # GET /healthz here (atlas/api/public_ping.py).
                          "/public/ping"})
_PUBLIC_PREFIXES = tuple(p for p in PUBLIC_PATHS if p not in PUBLIC_EXACT)
# Whole-path patterns, never prefixes: the AI chat on client sites
# (atlas/api/site_chat.py) is `/public/site-chat/` plus exactly one DNS label.
# "/public/site-chat/../api/x", "/public/site-chat/a/b", a trailing slash, an
# upper-case or empty slug, and anything else under /public/ stay mesh-only.
# The "Have Mapfront call me" form on preview sites (atlas/api/call_me.py) is
# the same shape: `/public/call-me/` plus exactly one DNS label.
PUBLIC_PATTERNS = (
    re.compile(r"/public/site-chat/[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?"),
    re.compile(r"/public/call-me/[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?"),
    # An ATLAS Academy download: exactly one signed token of the shape
    # `academy.delivery.mint` makes (file key, expiry, checkout session, HMAC).
    re.compile(r"/public/academy/file/v1\.[a-z0-9][a-z0-9-]{0,62}\.[0-9]{1,12}"
               r"\.cs_(?:live|test)_[A-Za-z0-9]{10,200}\.[0-9a-f]{64}"),
)


def _is_public(path: str) -> bool:
    return (path in PUBLIC_EXACT or path.startswith(_PUBLIC_PREFIXES)
            or any(p.fullmatch(path) for p in PUBLIC_PATTERNS))


# 172.16.0.0/12 is the Docker bridge. Traffic that arrives on the published
# port from the host is SNATed to the compose gateway, so the container sees
# 172.18.0.1 rather than 127.0.0.1 and an otherwise-correct admin call is
# refused. Including it does not widen real exposure: the bridge is reachable
# only from this host and from ATLAS's own containers, and the actual boundary
# is MESH_BIND — the port is published on the mesh address, never 0.0.0.0.
DEFAULT_MESH_CIDRS = "100.64.0.0/10,10.0.0.0/8,172.16.0.0/12,127.0.0.0/8"


def _mesh_networks() -> list[Any]:
    raw = os.getenv("MESH_ALLOWED_CIDRS", DEFAULT_MESH_CIDRS)
    nets = []
    for item in raw.split(","):
        item = item.strip()
        if item:
            try:
                nets.append(ipaddress.ip_network(item, strict=False))
            except ValueError:
                log.error("ignoring malformed CIDR in MESH_ALLOWED_CIDRS: %r", item)
    return nets


# Who is on the other end: the proxy's Docker network, and the forwarded
# address believed only from it, live in peers.py (auth and deliver use them
# too, and they are imported by this module).
_effective_client = peers.effective_client


def _device_certified(request: Request) -> bool:
    """Miro's own device, off the mesh, holding its client certificate.

    Caddy verifies the certificate against ATLAS's private device CA during
    the TLS handshake and forwards its SHA-256 fingerprint in
    X-Atlas-Device; every other Caddy route strips that header. It is
    believed only from our own proxy on the Docker bridge (the same rule as
    X-Forwarded-For), and only for a fingerprint pinned in
    ATLAS_DEVICE_FINGERPRINTS -- so revoking a lost phone is one line of
    .env, and the CA alone admits nobody. The login still applies after
    this; it replaces the mesh, not the password.
    """
    pinned = {f.strip().lower().replace(":", "")
              for f in os.getenv("ATLAS_DEVICE_FINGERPRINTS", "").split(",")
              if f.strip()}
    if not pinned:
        return False
    if not peers.from_proxy(request):
        return False
    given = request.headers.get("x-atlas-device", "").strip().lower().replace(":", "")
    return bool(given) and any(hmac.compare_digest(given, f) for f in pinned)


@app.middleware("http")
async def _mesh_only(request: Request, call_next):
    path = request.url.path
    if not _is_public(path) and not _device_certified(request):
        client = _effective_client(request)
        try:
            addr = ipaddress.ip_address(client)
        except ValueError:
            log.warning("refusing %s from unparseable client %r", path, client)
            return PlainTextResponse("not here", status_code=404)
        if not any(addr in net for net in _mesh_networks()):
            log.warning("refusing mesh-only path %s from %s", path, client)
            return PlainTextResponse("not here", status_code=404)
    return await call_next(request)


# The names the API is really served as on the Mac: the published loopback
# port, the Dockerfile HEALTHCHECK's localhost, and the compose service name
# the shop and proxy containers use.
DEFAULT_ALLOWED_HOSTS = ("127.0.0.1:8080", "localhost:8080", "api:8080")


def _allowed_hosts() -> frozenset[str] | None:
    """The Host allowlist, or None when it is off.

    Switched on by setting ATLAS_ALLOWED_HOSTS (a comma list of extra
    `host:port` names; the defaults are always in). Unset, it is off: the
    server behind Caddy is reached under its public names, and switching it
    on by default would have refused every one of them."""
    raw = os.getenv("ATLAS_ALLOWED_HOSTS", "").strip()
    if not raw:
        return None
    extra = {h.strip().lower() for h in raw.split(",") if h.strip()}
    return frozenset(DEFAULT_ALLOWED_HOSTS) | extra


# Added after `_mesh_only`, so it runs before it: a request is first asked
# which name it came for. A page on evil.example that rebinds its name to
# 127.0.0.1 (DNS rebinding) reaches the loopback API with `Host:
# evil.example`, and the unauthenticated /public/* and /webhooks/* routes
# would otherwise answer it. 421 Misdirected Request: wrong name, not
# wrong caller.
@app.middleware("http")
async def _host_allowlist(request: Request, call_next):
    allowed = _allowed_hosts()
    if allowed is not None:
        host = request.headers.get("host", "").strip().lower()
        if host not in allowed:
            log.warning("refusing %s for Host %r", request.url.path, host[:100])
            return PlainTextResponse("misdirected request", status_code=421)
    return await call_next(request)


# The same policy Caddy sets on 443 (Caddyfile), so the SPA shell carries it
# on the plain :8080 port too, now that the page can answer a card (Miro
# 2026-10-29: GUI decisions). Only the SPA shell: other HTML pages set their
# own, and a response that already carries one keeps it.
# `frame-src 'self'` (2026-10-30): the Previews panel frames the built demo
# sites from /api/agency/preview/, each sandboxed and under its own CSP.
CONTENT_SECURITY_POLICY = (
    "default-src 'none'; script-src 'self' 'wasm-unsafe-eval'; "
    "style-src 'self' 'unsafe-inline'; font-src 'self'; img-src 'self' data:; "
    "media-src 'self' blob:; manifest-src 'self'; connect-src 'self'; "
    "frame-src 'self'; base-uri 'none'; form-action 'self'; frame-ancestors 'none'")


@app.middleware("http")
async def _spa_csp(request: Request, call_next):
    response = await call_next(request)
    endpoint = request.scope.get("endpoint")
    if (getattr(endpoint, "__name__", "") == "spa"
            and response.headers.get("content-type", "").startswith("text/html")
            and "content-security-policy" not in response.headers):
        response.headers["Content-Security-Policy"] = CONTENT_SECURITY_POLICY
    return response


# The newest observation, read at most once per HEARTBEAT_CACHE_S: /healthz is
# public, so a burst of hits must not become a burst of queries. The value
# cached is the row's time, not an age, so the age keeps growing between reads.
HEARTBEAT_CACHE_S = 30.0
# Well inside the container HEALTHCHECK's 5 s: a hung database may cost the
# heartbeat its observation age, never /healthz its answer.
HEARTBEAT_DB_TIMEOUT_S = 1.5
_obs_read_at: float | None = None
_obs_at: datetime | None = None


async def _newest_observation() -> datetime | None:
    """max(observations.at), cached; None when it could not be read (and that
    None is cached too, so an unreachable database is not asked every hit).

    The read time is stamped before the query is awaited: hits that arrive
    while it runs are served the previous value instead of each starting a
    query of their own."""
    global _obs_read_at, _obs_at
    now = time.monotonic()
    if _obs_read_at is not None and now - _obs_read_at < HEARTBEAT_CACHE_S:
        return _obs_at
    _obs_read_at = now
    from ..kernel.state import fetch_one
    try:
        row = await asyncio.wait_for(
            fetch_one("SELECT max(at) AS at FROM observations"),
            HEARTBEAT_DB_TIMEOUT_S)
        at = row["at"] if row else None
        _obs_at = at if isinstance(at, datetime) else None
    except Exception as exc:  # noqa: BLE001
        log.warning("healthz: newest observation unreadable (%s)",
                    type(exc).__name__)
        _obs_at = None
    return _obs_at


async def _heartbeat() -> dict[str, int | None]:
    """Two ages for the off-box watchdog (docs/watchdog.md). None means "could
    not be established", never 0: the watchdog reads a null as UNKNOWN."""
    cycle = scheduler.LAST_CYCLE_AT
    at = await _newest_observation()
    if at is not None and at.tzinfo is None:
        at = at.replace(tzinfo=UTC)
    return {
        "cycle_age_s": None if cycle is None
        else max(0, int(time.monotonic() - cycle)),
        "observation_age_s": None if at is None
        else max(0, int((datetime.now(UTC) - at).total_seconds())),
    }


# A stable name the menu-bar app (bootstrap/mac/menubar/main.swift) requires
# before it grants the camera or microphone: whatever listens on ATLAS's port
# must say it is ATLAS. Not a secret; it only tells ATLAS from a stray process.
SERVICE_NAME = "atlas-agency"


@app.get("/healthz")
async def healthz() -> dict:
    # "ok" means what it always meant -- the process answers -- whatever the
    # heartbeat says. Docker's HEALTHCHECK and the deploy gate read "ok"; the
    # judgement on the ages stays with the watchdog.
    return {"ok": True, "service": SERVICE_NAME, "modules": len(REGISTRY.modules),
            "checks": len(REGISTRY.checks), "heartbeat": await _heartbeat()}


# ------------------------------------------------------------ slack webhook

def _verify_slack(body: bytes, ts: str, sig: str) -> bool:
    secret = os.getenv("SLACK_SIGNING_SECRET", "").encode()
    if not secret:
        return False
    try:
        age = abs(time.time() - int(ts or 0))
    except ValueError:
        return False                       # non-numeric header: reject, not 500
    if age > 300:
        return False
    base = b"v0:" + ts.encode() + b":" + body
    mine = "v0=" + hmac.new(secret, base, hashlib.sha256).hexdigest()
    return hmac.compare_digest(mine, sig or "")




async def _slack_reply(response_url: str, text: str, *,
                       ephemeral: bool = False) -> None:
    """Post the outcome back where the button was clicked.

    Slack IGNORES the HTTP response body of a block_actions interaction. It
    wants a bare 200 within three seconds, and anything you want the human to
    see must be POSTed separately to the payload's `response_url`. Returning
    {"response_type": ..., "text": ...} from the handler — which is the right
    shape for a slash command — sent every approval outcome into a void: the
    button worked, decide() ran, and the channel showed nothing at all.
    """
    if not response_url:
        log.warning("slack interaction carried no response_url; "
                    "the outcome cannot be shown")
        return
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            await client.post(response_url, json={
                "response_type": "ephemeral" if ephemeral else "in_channel",
                "replace_original": False,
                "text": text,
            })
    except Exception as exc:  # noqa: BLE001
        # The decision already happened; failing to narrate it must not
        # unwind it, and must not 500 back at Slack either.
        log.warning("could not post the outcome to Slack: %s", exc)


@app.post("/webhooks/slack")
async def slack_hook(
    request: Request,
    x_slack_request_timestamp: str = Header(""),
    x_slack_signature: str = Header(""),
) -> dict:
    raw = await request.body()
    if not _verify_slack(raw, x_slack_request_timestamp, x_slack_signature):
        raise HTTPException(401, "bad signature")

    form = await request.form()
    payload = json.loads(form.get("payload", "{}"))
    await handle_interaction(payload)
    return {"ok": True}


async def handle_interaction(payload: dict) -> None:
    """A button click, from whichever transport carried it: the signed HTTP
    route above, or Socket Mode (atlas/channels/slack_socket.py), where the
    `xapp-` token that opened the socket stands in for the signature."""
    # A valid Slack signature proves the request came from Slack, not that it
    # came from Miro. Without this check, approval authority is "anyone in the
    # workspace who can see the card".
    clicker = (payload.get("user") or {}).get("id", "")
    allowed = {u for u in os.getenv("SLACK_APPROVER_IDS", "").split(",") if u}
    reply_to = str(payload.get("response_url") or "")
    if not allowed:
        log.error("SLACK_APPROVER_IDS is unset — refusing to act on a Slack click")
        await _slack_reply(reply_to, "Approvals over Slack are not "
                           "configured. Nothing was done.", ephemeral=True)
        return
    if clicker not in allowed:
        log.warning("slack user %r is not an approver", clicker)
        await _slack_reply(reply_to, "You are not an approver for ATLAS. "
                           "Nothing was done.", ephemeral=True)
        return

    action = (payload.get("actions") or [{}])[0].get("value", "")
    if ":" not in action:
        return
    verdict, pid = action.split(":", 1)
    # "Acknowledge" on an alert: stop telling him about this check until it
    # changes. Answered here, before the proposal path — it is not a decision
    # about any action, and it must never reach decide().
    if verdict == "ack":
        from ..kernel.acks import ACK_DAYS, ack
        health = await ack(pid, via=f"slack:{clicker}")
        await _slack_reply(
            reply_to,
            f"Acknowledged {pid} ({health}). I won't raise it again for "
            f"{ACK_DAYS} days, or sooner if it gets worse or recovers and "
            f"breaks again." if health else
            f"I don't know a check called {pid}; nothing was muted.")
        return
    if not pid.isdigit():
        return

    # "Details" is a READ, and it sits on every proposal card. Sending it
    # through decide() answered "unknown verdict 'detail'" in the channel --
    # the same mistake as the wildcard {verdict} route that once swallowed
    # /approvals/1/why: a read routed into the only write path. Answer it
    # here, before anything that could decide anything.
    if verdict == "detail":
        await _slack_reply(reply_to, await _proposal_detail(int(pid)),
                           ephemeral=True)
        return

    verdict_result = await decide(int(pid), verdict, via=f"slack:{clicker}")
    await _slack_reply(reply_to, verdict_result.receipt())


async def _proposal_detail(pid: int) -> str:
    """What the card could not fit: the dry run, the rollback, the deadline.

    Ephemeral, so reading a proposal does not add a message everyone else in
    the channel has to scroll past.
    """
    from ..kernel.state import fetch_one

    row = await fetch_one(
        """SELECT action, params, tier, reason, evidence, rollback_plan,
                  confidence, status, expires_at
             FROM proposals WHERE id = $1""", pid)
    if row is None:
        return f"#{pid} does not exist. It may have been cleaned up."

    lines = [f"*#{pid}* `{row['action']}`  _(Tier {row['tier']}, "  # noqa: ISC004
             f"{row['status']})_"]
    if row["reason"]:
        lines.append(str(row["reason"]))
    if row["params"]:
        lines.append(f"*Parameters:* `{row['params']}`")
    # The dry run is the whole point of the button: it is what ATLAS
    # would do, produced before anyone approved anything.
    evidence = row["evidence"]
    if evidence and str(evidence) not in ("{}", "null"):
        lines.append(f"*Dry run:*\n```{str(evidence)[:2500]}```")
    if row["rollback_plan"]:
        lines.append(f"*Rollback:* {row['rollback_plan']}")
    if row["expires_at"]:
        lines.append(f"*Expires:* {row['expires_at']:%Y-%m-%d %H:%M UTC}")
    lines.append(f"*Confidence:* {float(row['confidence'] or 0):.0%}")
    return "\n\n".join(lines)


@app.post("/webhooks/slack/command")
async def slack_command(
    request: Request,
    x_slack_request_timestamp: str = Header(""),
    x_slack_signature: str = Header(""),
) -> dict:
    """`/atlas <what you want>` — say it, and it happens.

    Slack is the strongest identity ATLAS has: workspace membership, a
    signed request inside a five-minute replay window, and a user id checked
    against SLACK_APPROVER_IDS. Someone would have to be in the workspace
    AND be him. That is a better approval than a button click, which proves
    the same things and no more.

    Unlike a button click, a slash command DOES use the HTTP response body —
    that is the one Slack surface where returning JSON is correct. The work
    itself can outlast the three-second acknowledgement window, so the
    command answers immediately and the outcome follows on response_url.
    """
    raw = await request.body()
    if not _verify_slack(raw, x_slack_request_timestamp, x_slack_signature):
        raise HTTPException(401, "bad signature")

    form = await request.form()
    return await handle_command(dict(form))


async def handle_command(form: dict) -> dict:
    """`/atlas ...` from either transport. The dict is the immediate answer:
    the HTTP response body, or the Socket Mode ack's `payload`
    (atlas/channels/slack_socket.py)."""
    user = str(form.get("user_id") or "")
    text = str(form.get("text") or "").strip()
    reply_to = str(form.get("response_url") or "")

    allowed = {u for u in os.getenv("SLACK_APPROVER_IDS", "").split(",") if u}
    if not allowed:
        log.error("SLACK_APPROVER_IDS is unset — refusing a slash command")
        return {"response_type": "ephemeral",
                "text": "Commands aren't configured. Nothing was done."}
    if user not in allowed:
        log.warning("slack user %r is not an approver", user)
        return {"response_type": "ephemeral",
                "text": "You're not an approver for ATLAS. Nothing was done."}
    if not text:
        return {"response_type": "ephemeral",
                "text": "Tell me what to do — `/atlas restart the flycheap "
                        "scraper`."}

    # The kill switch: "pause reels", "pause everything because ...",
    # "resume reels", "pauses". Parsed in code, never by a model, and only
    # from an approver -- checked above.
    from ..kernel import pause as _pause
    if (said := await _pause.command(text, f"slack:{user}")) is not None:
        return {"response_type": "in_channel", "text": said}

    # Acknowledge inside three seconds, then do the work. A slash command
    # that thinks for eight seconds is a slash command Slack has already
    # told the user timed out.
    from ..kernel.background import spawn
    spawn(_run_command(text, user, reply_to), "slack command")
    return {"response_type": "ephemeral", "text": f"On it — {text}"}


async def _run_command(text: str, user: str, reply_to: str) -> None:
    """The work behind a slash command, after the acknowledgement."""
    from ..kernel.intent import act

    try:
        outcome = await act(text, channel="slack", who=user)
    except Exception as exc:
        log.exception("slash command failed")
        await _slack_reply(reply_to, f"That failed before it started: "
                                     f"{type(exc).__name__}.", ephemeral=True)
        return

    prefix = "Done" if outcome.ran else "Not done"
    detail = f"*{prefix}.* {outcome.spoken}"
    if outcome.action:
        detail += "\n_" + outcome.action + "_"
    # In-channel when something actually ran: an action taken on the estate
    # is everyone's business. Ephemeral when nothing did, so a refusal does
    # not clutter the channel.
    await _slack_reply(reply_to, detail, ephemeral=not outcome.ran)


# ----------------------------------------------------------- twilio webhooks

def _public_url(request: Request) -> str | None:
    """The URL Twilio signed, rebuilt from configuration rather than headers.

    Twilio computes its signature over the public `https://` URL it actually
    requested. This process sits behind Caddy and uvicorn runs without
    `--proxy-headers`, so `request.url` carries the scheme uvicorn saw — plain
    http — and every signature check over it fails. Silently: an SMS reply and
    a keypad approval both 401 and the decision is lost.

    The fix is not `--forwarded-allow-ips=*`. docker-compose publishes this
    port on the mesh address, so trusting `X-Forwarded-Proto` would mean
    trusting a header any mesh peer could set, in the one place where the
    header decides whether a signature verifies.

    `ATLAS_HOST` is the same value `VoiceChannel` builds its callback from, so
    if it is wrong the call never arrives to be verified in the first place.
    Returns None when it is unset, and the caller fails closed.
    """
    host = os.getenv("ATLAS_HOST", "").strip()
    if not host:
        return None
    url = f"https://{host}{request.url.path}"
    # Twilio signs the exact URL it fetched. The voice callbacks carry ?say=
    # and ?pid=, and a trailing "?" with nothing after it is a different
    # string from no query at all — which is a failed HMAC.
    return f"{url}?{request.url.query}" if request.url.query else url


def _verify_twilio(url: str, params: dict, signature: str) -> bool:
    """Twilio's scheme: HMAC-SHA1 over the full URL, with POST params appended
    sorted by key. A GET is signed over the URL alone (query string included),
    so `params` is empty for one."""
    token = os.getenv("TWILIO_AUTH_TOKEN", "").encode()
    if not token or not signature:
        return False
    data = url + "".join(f"{k}{params[k]}" for k in sorted(params))
    mine = hmac.new(token, data.encode(), hashlib.sha1).digest()
    return hmac.compare_digest(base64.b64encode(mine).decode(), signature)


# The inbound SMS webhook was removed with the SMS channel. It could still
# receive a command, but `notify` can no longer answer one: a US 10DLC
# number cannot send without an approved A2P campaign. A command surface
# that acts and cannot confirm what it did is worse than no surface, and
# this one was public.

@app.post("/webhooks/twilio/sms")
async def twilio_sms(request: Request,
                     x_twilio_signature: str = Header("")) -> Response:
    """Questions and status by text; never an approval (see below).

    Deleted by accident in the commit that added the brand mark -- a
    scripted edit to this file dropped it, the route table got shorter, and
    nothing failed: no test covered it, so the suite stayed green and I told
    Miro the path was intact while it was gone. A route disappearing is
    silent in a way a broken route is not.
    """
    form = dict(await request.form())
    url = _public_url(request)
    if url is None:
        log.error("ATLAS_HOST is unset — refusing a Twilio webhook that "
                  "cannot be verified")
        raise HTTPException(503, "not configured")
    if not _verify_twilio(url, form, x_twilio_signature):
        raise HTTPException(401, "bad signature")
    # Only Miro's own number may issue commands. Anyone else -- a local
    # business answering outreach that said "call or text" -- is passed to
    # Miro in Slack, quoted, and gets no reply: no command runs, nothing is
    # promised, and the words never reach a model (invariant 4).
    if form.get("From") != os.getenv("TWILIO_TO"):
        # A lead's reply -- or a STOP from anyone -- is taken in first,
        # whether or not the voice line is on (agency.leads): stored,
        # classified, consent recorded or revoked, alerted in Slack. Twilio
        # answers a STOP itself. A lead's text may then get the business
        # line's own answer in this response's <Message> -- the reply to
        # their text, like the spoken reply to their call (`reception.
        # text_reply`: AGENCY_SMS_ENABLED, per-number limit, never an opt-out).
        from ..modules.agency import leads
        from . import reception
        frm, text = str(form.get("From") or ""), str(form.get("Body") or "")
        opt_out = str(form.get("OptOutType") or "")
        if await leads.intake_sms(frm, text, sid=str(form.get("MessageSid") or ""),
                                  opt_out_type=opt_out,
                                  answered=reception.sms_ai_enabled()):
            answer = None
            if opt_out.strip().upper() != "STOP":
                answer = await reception.text_reply(
                    frm, text, await reception.lead_for_text(frm))
            if answer:
                await reception.relay_answer(frm, answer)
                return Response(
                    content=f"<Response><Message>{xml_escape(answer)}</Message>"
                            "</Response>", media_type="application/xml")
            return Response(content="<Response></Response>",
                            media_type="application/xml")
        if not reception.enabled():
            raise HTTPException(403, "unknown sender")
        await reception.relay_text(str(form.get("From") or ""),
                                   str(form.get("Body") or ""))
        return Response(content="<Response></Response>",
                        media_type="application/xml")

    body = (form.get("Body") or "").strip()
    # A text carries no read-back and no attestation: the sender is trusted
    # on caller ID alone, which is the one basis every other surface
    # refuses (a card is approved from Slack, or by keypad on a call ATLAS
    # placed, or by "do it" on an A-attested call after the read-back). So
    # a text may ask a question or read a status, never decide a card.
    low = body.lower()
    if any(low.startswith(v) for v in ("approve", "always", "decline")):
        reply = ("A text can't approve a card: use the Slack button, or press "
                 "1 on the call. Nothing was changed.")
    else:
        reply = await _handle_command(body, via="sms")
    return Response(
        content=f"<Response><Message>{xml_escape(reply[:1500])}</Message></Response>",
        media_type="application/xml")


@app.api_route("/webhooks/twilio/voice", methods=["GET", "POST"])
async def twilio_voice(request: Request,
                       x_twilio_signature: str = Header("")) -> Response:
    """The opening of a call ATLAS placed (`channels.twilio`): the one
    decision, then the same conversation the line he rings holds.

    This endpoint is on the public 443 surface. It once had no signature
    check and interpolated a query parameter straight into the XML, which
    let anyone inject TwiML -- `<Dial>` to a premium-rate number among other
    things. So: verify first, then escape. `say`, `pid` and `away` are
    ATLAS's own, in the URL `send()` built and Twilio signed.

    Which way the call went is Twilio's own record, on this signed request
    (`phone.placed`): only a call it says ATLAS placed from TWILIO_FROM to
    TWILIO_TO gets its cards bound for "do it". Anything else still hears
    the alert -- a CRITICAL page is never lost -- and approves nothing.
    """
    form = dict(await request.form()) if request.method == "POST" else {}
    url = _public_url(request)
    if url is None:
        log.error("ATLAS_HOST is unset — refusing a Twilio webhook that "
                  "cannot be verified")
        raise HTTPException(503, "not configured")
    if not _verify_twilio(url, form, x_twilio_signature):
        raise HTTPException(401, "bad signature")

    from . import phone

    def param(name: str) -> str:
        # POST carries Twilio's parameters in the form; a GET (an older
        # call still in flight) in the query string, signed all the same.
        return str(form.get(name) or request.query_params.get(name) or "").strip()

    call = param("CallSid")
    say = " ".join((request.query_params.get("say") or "").split())[:900]
    title = request.query_params.get("title") or ""
    pids = [int(p) for p in (request.query_params.get("pid") or "").split(",")
            if p.strip().isdigit()][:5]
    mine = phone.placed(call, param("Direction"), param("From"), param("To"),
                        away=request.query_params.get("away") == "1")
    host = os.getenv("ATLAS_HOST", "").strip()
    if not mine:
        log.warning("an outbound call's opening did not come from a call "
                    "ATLAS placed to TWILIO_TO -- reading the alert, binding "
                    "nothing")

    # Twilio's answering-machine detection (DetectMessageEnd): a machine is
    # left one line, after its beep, that approves nothing -- and hung up on.
    who = param("AnsweredBy")
    if mine:
        phone.answered_by(call, who)
    if phone.is_machine(who):
        line = phone.machine_message(title)
        if call:
            phone.remember(call, "ATLAS", line)
        return Response(media_type="application/xml", content=phone.closing(
            line, await phone.clip(line), host))

    from ..kernel import briefing
    opening = say or briefing.GREETING
    if phone.away(call):
        # "I'll be quick" comes straight after the greeting and before the
        # incident, so he knows how long this is before he hears what it is.
        for hello in (briefing.GREETING_NIGHT, briefing.GREETING):
            if opening.startswith(hello):
                opening = (f"{hello} {phone.AWAY_OPENING} "
                           f"{opening[len(hello):].strip()}").strip()
                break
        else:
            opening = f"{phone.AWAY_OPENING} {opening}"
    # Until a person is confirmed (Twilio said `human`), the keypad alone.
    person = phone.human(call)
    if mine and pids:
        phone.set_pending(call, pids)
        # The third beat: the one decision as a yes-or-no question, then
        # how to answer it. The keypad decides (`phone.respond`).
        ask = (" ".join((request.query_params.get("ask") or "").split())[:300]
               or briefing.question(""))
        phone.set_question(call, ask)
        opening = f"{opening} {ask} {briefing.KEYPAD_HINT}"
    if call:
        phone.remember(call, "ATLAS", opening)
    from . import relay
    if mine and person and call and host and relay.enabled():
        # ConversationRelay (PHONE_RELAY=on): one socket for the call, the
        # opening as its welcome greeting, and he can cut in. Only for a
        # call ATLAS placed that a person answered; the same gate decides.
        return Response(media_type="application/xml",
                        content=relay.twiml(host, call, opening))
    return Response(media_type="application/xml", content=phone.listening(
        opening, await phone.clip(opening), host, turn=1, speech=person))


@app.websocket("/webhooks/twilio/relay")
async def twilio_relay(ws: WebSocket) -> None:
    """The ConversationRelay socket for a call ATLAS placed (api/relay.py):
    a single-use token bound to the CallSid, then Twilio's `setup`."""
    from . import relay
    host = os.getenv("ATLAS_HOST", "").strip()
    query = ws.url.query
    signature = ws.headers.get("x-twilio-signature", "")
    # Twilio signs the handshake over the socket's own URL. Both forms are
    # tried because the scheme it signs is the one in the TwiML (wss), and
    # a proxy in front may present it as https. The token in the URL is
    # checked as well, inside `relay.handle`: the signature says Twilio,
    # the token says which call.
    signed = host and any(
        _verify_twilio(f"{scheme}://{host}{relay.PATH}" + (f"?{query}" if query else ""),
                       {}, signature)
        for scheme in ("wss", "https"))
    if not relay.enabled() or not signed:
        await ws.close(code=1008)
        return
    await relay.handle(ws)


@app.post("/webhooks/twilio/relay/after")
async def twilio_relay_after(request: Request,
                             x_twilio_signature: str = Header("")) -> Response:
    """Where Twilio returns when the relay session ends. ATLAS ending it
    hangs up; anything else (a failed socket, a provider error) puts the
    call back on the Gather line so the decision can still be made."""
    from . import phone, relay
    form = dict(await request.form())
    url = _public_url(request)
    if url is None:
        raise HTTPException(503, "not configured")
    if not _verify_twilio(url, form, x_twilio_signature):
        raise HTTPException(401, "bad signature")
    call = str(form.get("CallSid") or "")
    host = os.getenv("ATLAS_HOST", "").strip()
    if str(form.get("HandoffData") or "") == relay.DONE or not phone.outbound(call):
        return Response(media_type="application/xml", content="<Response><Hangup/></Response>")
    from ..kernel import briefing
    back = "The line dropped for a second. I'm back."
    if phone.pending(call) is not None:
        back = f"{back} {phone.question(call) or briefing.question('')} {briefing.KEYPAD_HINT}"
    phone.remember(call, "ATLAS", back)
    return Response(media_type="application/xml", content=phone.listening(
        back, await phone.clip(back), host, turn=1, speech=phone.human(call)))


@app.api_route("/webhooks/twilio/voice/inbound", methods=["GET", "POST"])
async def twilio_voice_inbound(request: Request,
                               x_twilio_signature: str = Header("")) -> Response:
    """Someone rang the ATLAS number.

    Without this the number has no voice_url and callers hear "not in
    service". The outbound alert route is not a substitute: it speaks a
    `say` parameter and asks for a keypad decision, which is nonsense to
    someone who just dialled.

    A phone number is the least authenticated surface ATLAS has. There is no
    mesh, no login, no Slack workspace — anyone who knows the digits can
    reach it, and caller ID is spoofable. So an unrecognised caller is told
    nothing at all: not a status, not a business name, not whether something
    exists. Recognising the number is enough to say how things are; it is not
    enough to do anything, and there is nothing here that could.
    """
    form = dict(await request.form()) if request.method == "POST" else {}
    url = _public_url(request)
    if url is None:
        log.error("ATLAS_HOST is unset — refusing an unverifiable Twilio call")
        raise HTTPException(503, "not configured")
    if not _verify_twilio(url, form, x_twilio_signature):
        raise HTTPException(401, "bad signature")

    caller = str(form.get("From") or request.query_params.get("From") or "")
    mine = os.getenv("TWILIO_TO", "").strip()

    from . import phone as _phone
    if not mine or _phone._digits(caller) != _phone._digits(mine):
        from . import reception
        sid = str(form.get("CallSid") or request.query_params.get("CallSid") or "")
        if reception.enabled() and sid and caller:
            # The business line (Miro, 2026-09-27): answered as Miro, with
            # no hands -- nothing on it reaches the executor.
            reception.open_call(sid, caller, await reception.lead_for(caller))
            host = os.getenv("ATLAS_HOST", "").strip()
            return Response(media_type="application/xml", content=reception.listen(
                reception.GREETING, await _phone.clip(reception.GREETING), host, 1))
        log.info("inbound call from an unrecognised number")
        return Response(media_type="application/xml", content=(
            "<Response>"
            '<Say voice="Polly.Matthew">This is a private line. '
            'I can\'t help you here. Goodbye.</Say>'
            "<Hangup/>"
            "</Response>"))

    # STIR/SHAKEN arrives on this webhook and no other: remembered per call,
    # it is what lets "do it" on the turns that follow reach `decide`.
    from . import phone
    phone.attest(
        str(form.get("CallSid") or request.query_params.get("CallSid") or ""),
        str(form.get("StirVerstat")
            or request.query_params.get("StirVerstat") or ""))

    return Response(media_type="application/xml",
                    content=await _inbound_greeting())


async def _inbound_greeting() -> str:
    """A greeting and the shortest honest summary of the estate.

    Spoken, so it counts rather than lists: a phone call cannot be skimmed,
    and eleven check names read aloud is noise. If a reading could not be
    taken it is said as unknown rather than folded into "fine" — the same
    rule the tiles follow.
    """
    from ..kernel.contracts import Health
    from ..kernel.state import fetch_all

    line = "Everything I can see is healthy."
    try:
        rows = await fetch_all(
            """SELECT DISTINCT ON (o.check_id) o.health, o.at, c.interval_s,
                      c.name, p.name AS prop
                 FROM observations o
                 JOIN checks c ON c.id = o.check_id
                 JOIN properties p ON p.id = c.property_id
                ORDER BY o.check_id, o.at DESC""")
        # A stale OK is unknown here as on the board and in the system check.
        from .system_check import row_health
        rows = [{**dict(r),
                 "health": row_health(r["health"], r["at"], r["interval_s"])}
                for r in rows]
        health = [str(r["health"]) for r in rows]
        bad = [r for r in rows
               if str(r["health"]) in (Health.FAILING, Health.DEGRADED)]
        unknown = health.count(str(Health.UNKNOWN))

        if bad:
            worst = [r for r in bad if str(r["health"]) == str(Health.FAILING)]
            lead = worst[0] if worst else bad[0]
            line = (f"{len(bad)} thing{'s' if len(bad) != 1 else ''} "
                    f"need{'' if len(bad) != 1 else 's'} you. The loudest is "
                    f"{lead['prop']} {str(lead['name']).replace('_', ' ')}.")
        if unknown:
            line += (f" {unknown} check{'s' if unknown != 1 else ''} "
                     f"couldn't run, so I can't speak for "
                     f"{'those' if unknown != 1 else 'that'}.")
    except Exception:  # noqa: BLE001
        log.warning("inbound greeting could not read the board")
        line = "I can't reach my own records right now, so I won't guess."

    from . import phone

    host = os.getenv("ATLAS_HOST", "").strip()
    # The board is only offered if something on it needs him. Opening a call
    # by reciting the estate is what an announcement does; a person says
    # hello and waits.
    opening = phone.GREETING if line.startswith("Everything") else (
        f"{phone.GREETING} {line}")
    return phone.listening(opening, await phone.clip(opening), host, turn=1)


@app.post("/webhooks/twilio/voice/turn")
async def twilio_voice_turn(request: Request,
                            x_twilio_signature: str = Header("")) -> Response:
    """One turn of a phone conversation: hear him, answer, listen again.

    Admitted on a call ATLAS placed (recorded at its opening) or on a call
    from his number, and nothing else (`phone.admitted`). A request is never
    run on the words that asked for it: `phone.respond` raises it as a card
    and reads it back, and only an explicit "do it" -- or 1 on the keypad --
    for the card just described answers it, through `approvals.decide`;
    on a call he placed, only when the incoming webhook carried STIR/SHAKEN
    A-attestation (`phone.attest`).
    """
    form = dict(await request.form())
    url = _public_url(request)
    if url is None:
        log.error("ATLAS_HOST is unset — refusing an unverifiable Twilio call")
        raise HTTPException(503, "not configured")
    if not _verify_twilio(url, form, x_twilio_signature):
        raise HTTPException(401, "bad signature")
    import time as _time
    started = _time.monotonic()
    reply = await _phone_turn(form, int(request.query_params.get("turn") or 1),
                              int(request.query_params.get("wait") or 0))
    # ATLAS's own answer time for this turn, kept on the call's record.
    from ..kernel import callrecord
    callrecord.turn(str(form.get("CallSid") or ""), (_time.monotonic() - started) * 1000)
    return reply


@app.post("/webhooks/twilio/reception/turn")
async def twilio_reception_turn(request: Request,
                                x_twilio_signature: str = Header("")) -> Response:
    """One turn on the business line: hear the caller, answer, listen again.
    Admitted only on a call the line opened, from the same number; it can
    say things and nothing else (`reception`)."""
    form = dict(await request.form())
    url = _public_url(request)
    if url is None:
        log.error("ATLAS_HOST is unset — refusing an unverifiable Twilio call")
        raise HTTPException(503, "not configured")
    if not _verify_twilio(url, form, x_twilio_signature):
        raise HTTPException(401, "bad signature")
    from . import phone, reception
    sid, frm = str(form.get("CallSid") or ""), str(form.get("From") or "")
    if not reception.admitted(sid, frm):
        raise HTTPException(403, "unknown call")
    turn = int(request.query_params.get("turn") or 1)
    text, done = await reception.reply(sid, str(form.get("SpeechResult") or ""))
    host = os.getenv("ATLAS_HOST", "").strip()
    token = await phone.clip(text)
    if done:
        await reception.finish(sid)
        return Response(media_type="application/xml",
                        content=reception.hang_up(text, token, host))
    return Response(media_type="application/xml",
                    content=reception.listen(text, token, host, turn + 1))


async def _lead_call_of(request: Request, form: dict):
    """The lead call a verified webhook is about (`lead_call.admit`): the
    per-call secret, the contact row, the lead's number and the CallSid
    must all agree. The Twilio signature is checked by each route first."""
    from . import lead_call
    contact = str(request.query_params.get("c") or "")
    token = str(request.query_params.get("t") or "")
    sid = str(form.get("CallSid") or "")
    call = await lead_call.admit(contact, token, sid, str(form.get("To") or ""))
    return call, contact, token, sid


@app.post("/webhooks/twilio/agency/voice")
async def twilio_agency_voice(request: Request,
                              x_twilio_signature: str = Header("")) -> Response:
    """The opening of a call ATLAS placed to a lead who asked for one
    (`agency.call_lead`). The disclosure is the first line. No hands: this
    route and its turns can raise a card and nothing else."""
    form = dict(await request.form())
    url = _public_url(request)
    if url is None or not _verify_twilio(url, form, x_twilio_signature):
        raise HTTPException(401, "bad signature")
    call, _contact, token, sid = await _lead_call_of(request, form)
    if call is None:
        raise HTTPException(403, "unknown call")
    from . import lead_call
    host = os.getenv("ATLAS_HOST", "").strip()
    return Response(media_type="application/xml", content=await lead_call.opening(
        call, sid, str(form.get("AnsweredBy") or ""), host, token))


@app.post("/webhooks/twilio/agency/turn")
async def twilio_agency_turn(request: Request,
                             x_twilio_signature: str = Header("")) -> Response:
    form = dict(await request.form())
    url = _public_url(request)
    if url is None or not _verify_twilio(url, form, x_twilio_signature):
        raise HTTPException(401, "bad signature")
    call, _contact, token, sid = await _lead_call_of(request, form)
    if call is None:
        raise HTTPException(403, "unknown call")
    from . import lead_call, phone
    host = os.getenv("ATLAS_HOST", "").strip()
    turn = int(request.query_params.get("turn") or 1)
    try:
        text, done = await lead_call.reply_in_time(
            call, sid, str(form.get("SpeechResult") or ""))
    except Exception:
        log.exception("lead call turn failed")
        lead_call._spawn_emergency(call, "a system failure on a call")
        text, done = lead_call.FAILED, True
    clip = await phone.clip(text)
    if call.transferring:
        # The lead said yes to the owner: bridge to Miro's phone, with the
        # whisper on its own signed route and the outcome on the dial route.
        return Response(media_type="application/xml",
                        content=lead_call.transfer_twiml(call, clip, host, token))
    if done:
        return Response(media_type="application/xml",
                        content=lead_call.hang_up(text, clip, host))
    return Response(media_type="application/xml",
                    content=lead_call.listen(text, clip, host, call, token, turn + 1))


@app.post("/webhooks/twilio/agency/whisper")
async def twilio_agency_whisper(request: Request,
                                x_twilio_signature: str = Header("")) -> Response:
    """What Miro hears before a transferred lead is bridged to him: the
    child leg of the `<Dial>`, admitted on its ParentCallSid being a lead
    call this process holds, the secret, and `To` being his own number."""
    form = dict(await request.form())
    url = _public_url(request)
    if url is None or not _verify_twilio(url, form, x_twilio_signature):
        raise HTTPException(401, "bad signature")
    from . import lead_call, phone
    call = await lead_call.admit_whisper(
        str(request.query_params.get("c") or ""),
        str(request.query_params.get("t") or ""),
        str(form.get("ParentCallSid") or ""), str(form.get("To") or ""))
    if call is None:
        raise HTTPException(403, "unknown call")
    host = os.getenv("ATLAS_HOST", "").strip()
    line = lead_call.whisper_line(call)
    return Response(media_type="application/xml",
                    content=lead_call.whisper_twiml(call, await phone.clip(line), host))


@app.post("/webhooks/twilio/agency/dial")
async def twilio_agency_dial(request: Request,
                             x_twilio_signature: str = Header("")) -> Response:
    """How the transfer to Miro ended (the `<Dial>` action, on the lead's
    own call): he answered and they talked, or the lead hears that he will
    call back today and Slack is alerted. Either way the call ends here."""
    form = dict(await request.form())
    url = _public_url(request)
    if url is None or not _verify_twilio(url, form, x_twilio_signature):
        raise HTTPException(401, "bad signature")
    call, _contact, _token, sid = await _lead_call_of(request, form)
    if call is None:
        raise HTTPException(403, "unknown call")
    from . import lead_call, phone
    host = os.getenv("ATLAS_HOST", "").strip()
    text, _done = await lead_call.dial_result(
        call, sid, str(form.get("DialCallStatus") or ""),
        str(form.get("DialCallDuration") or ""))
    return Response(media_type="application/xml",
                    content=lead_call.hang_up(text, await phone.clip(text), host))


@app.post("/webhooks/twilio/agency/status")
async def twilio_agency_status(request: Request,
                               x_twilio_signature: str = Header("")) -> dict:
    """How a lead call ended. Never `escalation.record_outcome`: a lead not
    answering is not Miro declining a call."""
    form = dict(await request.form())
    url = _public_url(request)
    if url is None or not _verify_twilio(url, form, x_twilio_signature):
        raise HTTPException(401, "bad signature")
    call, contact, _token, sid = await _lead_call_of(request, form)
    if call is None and not contact.isdigit():
        raise HTTPException(403, "unknown call")
    from . import lead_call
    if call is None:
        # No turn was ever admitted (no answer, busy): the row is still
        # checked against its secret before it is closed.
        from ..modules.agency import _store
        row = await _store.get_contact(int(contact))
        if not row or not hmac.compare_digest(
                str(row.get("token_sha") or ""),
                hashlib.sha256(str(request.query_params.get("t") or "")
                               .encode()).hexdigest()):
            raise HTTPException(403, "unknown call")
    await lead_call.finish(sid, contact_id=int(contact),
                           status=str(form.get("CallStatus") or "").strip().lower(),
                           answered_by=str(form.get("AnsweredBy") or ""))
    return {"ok": True}


# The keypad, as words the same gate already understands.
_KEYPAD = {"1": "do it", "2": "decline"}


async def _phone_turn(form: dict, turn: int, resume: int = 0) -> Response:
    from . import phone

    call = str(form.get("CallSid") or "")
    direction = str(form.get("Direction") or "").strip()
    frm, to = str(form.get("From") or "").strip(), str(form.get("To") or "").strip()
    host = os.getenv("ATLAS_HOST", "").strip()
    if not phone.admitted(call, direction, frm, to):
        if phone.ours(direction, frm, to):
            # A call ATLAS placed that this process no longer knows -- it
            # restarted mid-call. Polite, and nothing is decided.
            return Response(media_type="application/xml", content=phone.closing(
                phone.LOST_THREAD, None, host))
        raise HTTPException(403, "unknown caller")

    digit = str(form.get("Digits") or "").strip()
    if digit in _KEYPAD or digit in (phone.REPEAT_KEY, phone.STOP_KEY):
        phone.confirm_human(call)
    # Speech is heard on a call he placed, and on a call ATLAS placed once
    # a person is confirmed; before that, the keypad alone.
    speech = not phone.outbound(call) or phone.human(call)

    if digit == phone.STOP_KEY:
        # He ended it. Nothing is decided and nothing is declined: the
        # cards stay in Slack exactly as they were.
        await phone.wrap_up(call)
        return Response(media_type="application/xml", content=phone.closing(
            phone.STOPPED, await phone.clip(phone.STOPPED), host))
    if digit == phone.REPEAT_KEY:
        last = next((what for who, what in reversed(phone.history(call))
                     if who == "ATLAS"), "") or "There's nothing to repeat yet."
        return Response(media_type="application/xml", content=phone.listening(
            last, await phone.clip(last), host, turn=turn, speech=speech))

    if resume:
        # The answer that missed the last turn's budget.
        task = phone._LATE.pop(call, None)
        if task is None:
            again = "Where were we?"
            return Response(media_type="application/xml", content=phone.listening(
                again, await phone.clip(again), host, turn=turn, speech=speech))
        return await _deliver(phone, call, task, turn, resume, host)

    if phone.confirming(call):
        # The stop window after a spoken yes was read back: "stop" or 2
        # cancels; anything else, silence included, goes ahead.
        heard = str(form.get("SpeechResult") or "").strip()
        phone.remember(call, "Miro", f"(pressed {digit})" if digit else heard or "(said nothing)")
        task = phone.spawn_turn(phone.finish_confirm(call, digit, heard))
        return await _deliver(phone, call, task, turn + 1, 0, host)

    if phone.awaiting_pin(call):
        # The PIN turn. The digits are compared and dropped: they are not
        # remembered on the call, logged, or shown to a model.
        outcome = phone.check_pin(call, digit) if digit else "wrong-empty"
        if outcome == "ok":
            phone.remember(call, "Miro", "(entered the PIN)")
            line_ = phone.PIN_OK
            phone.remember(call, "ATLAS", line_)
            return Response(media_type="application/xml", content=phone.listening(
                line_, await phone.clip(line_), host, turn=turn + 1, speech=speech))
        if outcome == "locked":
            phone.clear_pending(call)
            line_ = phone.CANNOT_VERIFY
            phone.remember(call, "ATLAS", line_)
            return Response(media_type="application/xml", content=phone.listening(
                line_, await phone.clip(line_), host, turn=turn + 1, speech=speech))
        line_ = phone.PIN_AGAIN if outcome == "wrong" else phone.PIN_ASK
        return Response(media_type="application/xml", content=phone.listening(
            line_, await phone.clip(line_), host, turn=turn + 1,
            digits=phone.pin_length()))

    said = _KEYPAD.get(digit) or str(form.get("SpeechResult") or "").strip()

    if not said:
        # Twilio heard nothing. Counted in a row, per call: once is a pause
        # or a mishearing, twice is worth saying so, three times running is
        # a call that has stopped being one.
        empty = phone.quiet(call)
        if empty >= 3:
            await phone.wrap_up(call)
            return Response(media_type="application/xml",
                            content=phone.closing(phone.LOST,
                                                  await phone.fixed_clip(phone.LOST),
                                                  host))
        again = phone.QUIET_FIRST if empty == 1 else phone.QUIET_SECOND
        if not speech and phone.pending(call) is not None:
            # The keypad alone is listening: say how to answer it.
            again = "Still there? Press 1 to approve, 2 to decline."
        return Response(media_type="application/xml", content=phone.listening(
            again, await phone.fixed_clip(again), host, turn=turn + 1,
            speech=speech))

    phone.heard(call)
    phone.remember(call, "Miro", f"(pressed {digit}) {said}" if digit in _KEYPAD
                   else said)
    # Approve, decline, or answer. Decided inside phone.respond so that
    # "do it" is matched before anything reaches a model, and cannot be
    # reinterpreted on the way through a prompt.
    turn_id = phone.begin_turn(call)
    task = phone.spawn_turn(phone.respond(said, call, turn_id,
                                          keypad=digit in _KEYPAD))
    return await _deliver(phone, call, task, turn + 1, 0, host)


async def _deliver(phone, call: str, task: asyncio.Task, turn: int, resume: int,
                   host: str) -> Response:
    """Speak the turn's answer if it is ready within MODEL_BUDGET_S. If not,
    "One moment" in Twilio's voice (no Fish wait) and pick it up on the
    next turn -- Twilio abandons a webhook at fifteen seconds."""
    done, _ = await asyncio.wait({task}, timeout=phone.MODEL_BUDGET_S)
    if task not in done:
        if resume < phone.MAX_RESUMES:
            phone._LATE[call] = task
            action = xml_escape(
                f"/webhooks/twilio/voice/turn?turn={turn}&wait={resume + 1}")
            moment = await phone.fixed_clip(phone.ONE_MOMENT)
            said_moment = (
                f"<Play>https://{host}/webhooks/twilio/audio/{moment}</Play>"
                if moment else
                f'<Say voice="{phone.VOICE_FALLBACK}">{phone.ONE_MOMENT}</Say>')
            return Response(media_type="application/xml", content=(
                f"<Response>{said_moment}"
                f'<Redirect method="POST">{action}</Redirect></Response>'))
        # Given up: nothing this turn produces binds a card on the call, and
        # it is stopped unless it is already deciding (then it finishes,
        # and its receipt goes to Slack).
        answer = (phone.STILL_RUNNING if phone.abandon(call, task)
                  else phone.GAVE_UP)
    else:
        try:
            answer = task.result()
        except Exception:
            log.exception("phone turn failed")
            answer = ("Something went wrong at my end — I'd rather say so "
                      "than make something up.")
            exc = task.exception()
            if isinstance(exc, RuntimeError) and (
                    "providers failed" in str(exc) or "no LLM provider" in str(exc)):
                # Say what it is: the models, not him and not the line.
                answer = ("None of my models are answering right now, so I can't "
                          "think that through. Anything waiting on you is in Slack, "
                          "and the keypad still works.")
    phone.remember(call, "ATLAS", answer)
    spoken = " ".join(answer.split())[:900]
    if phone.confirming(call):
        # A read-back: a short window to say stop, then it goes ahead.
        return Response(media_type="application/xml", content=phone.listening(
            spoken, await phone.clip(spoken), host, turn=turn,
            window=phone.STOP_WINDOW_S))
    if phone.awaiting_pin(call):
        # `respond` asked for the PIN: this Gather takes digits only.
        return Response(media_type="application/xml", content=phone.listening(
            spoken, await phone.clip(spoken), host, turn=turn,
            digits=phone.pin_length()))
    if phone.machine(call):
        # A recording, not Miro: say where it is and hang up.
        return Response(media_type="application/xml", content=phone.closing(
            spoken, await phone.clip(spoken), host))
    speech = not phone.outbound(call) or phone.human(call)
    return Response(media_type="application/xml", content=phone.listening(
        spoken, await phone.clip(spoken), host, turn=turn, speech=speech))


@app.post("/webhooks/twilio/voice/status")
async def twilio_voice_status(request: Request,
                              x_twilio_signature: str = Header("")) -> dict:
    """How the call ended.

    Twilio posts here when a call completes. Without it ATLAS rings and never
    learns whether anyone picked up, so "three declines in a row and he stops
    calling" was a rule with no input -- written, tested, and never fed.

    A declined or unanswered URGENT call is not the end of the matter: it
    falls to the decisions channel with the buttons, which is COMMS.md 3.3's
    "if declined" column. Silence still never executes; this only changes
    where the asking happens.
    """
    form = dict(await request.form())
    url = _public_url(request)
    if url is None:
        log.error("ATLAS_HOST is unset — refusing an unverifiable Twilio call")
        raise HTTPException(503, "not configured")
    if not _verify_twilio(url, form, x_twilio_signature):
        raise HTTPException(401, "bad signature")

    status = str(form.get("CallStatus") or "").strip().lower()
    # Twilio's vocabulary: completed means it connected and ran its course.
    # no-answer, busy and failed all mean he did not take it, and "canceled"
    # means it was pulled before he could.
    answered = status == "completed"

    # Whatever was said is summarised into a note, and the call is let go.
    # A voicemail greeting that "completed" the call is not him answering.
    from . import phone, reception
    call_sid = str(form.get("CallSid") or "")
    # A business-line call the caller hung up on is filed and posted now.
    await reception.finish(call_sid)
    # Twilio's answering-machine detection rides on this callback too: a
    # machine or an unknown pickup is not him answering (it keeps the Slack
    # fallback, and never settles an incident as taken while he was away).
    was_machine = (phone.machine(call_sid)
                   or phone.is_machine(str(form.get("AnsweredBy") or "")))
    if was_machine:
        answered = False
    await phone.wrap_up(call_sid,
                        str(form.get("Direction") or "").strip().lower())

    # A call he placed (if the number's own status callback points here) is
    # not an answer to anything ATLAS rang about: it never touches the
    # decline count.
    if str(form.get("Direction") or "").strip().lower() == "inbound":
        return {"ok": True}

    from ..kernel import rering
    from ..kernel.escalation import record_outcome
    record_outcome(answered)

    from ..kernel import callrecord
    await callrecord.ended(call_sid,
                           "answered" if answered else "machine" if was_machine else "missed",
                           str(form.get("AnsweredBy") or ""))
    if answered:
        rering.answered(call_sid)
        # He took it and pressed nothing: say so where the card is, so
        # "I heard it and chose to wait" is not the same silence as a miss.
        note = callrecord.answered_note(call_sid)
        if note is not None:
            await phone.receipt(*note)
        timing = callrecord.timing(call_sid)
        if timing is not None:
            log.info("call answered: %(turns)s turns, median %(median_ms)s ms, "
                     "slowest %(slowest_ms)s ms to answer", timing)
    callrecord.forget(call_sid)
    if not answered:
        log.info("call ended %s — falling back to the decisions channel",
                 status or "with no status")
        # The step-up: ring again in a couple of minutes while there is
        # still something to decide, a bounded number of times.
        again = rering.missed(call_sid)
        ringing = ("\n\nI'll ring once more in a couple of minutes if it is "
                   "still waiting on you." if again is not None else "")
        try:
            from ..kernel.comms import Outbound, notify
            from ..kernel.contracts import Severity
            await notify(Outbound(
                title="You didn't take that call",
                body=(f"The call ended `{status or 'unknown'}`, so whatever I "
                      f"rang about is here instead. Anything waiting on you "
                      f"is in this channel with its buttons.{ringing}\n\n"
                      f"Reply `quiet 3h` if you want me to stop ringing for a "
                      f"while — I'll still call for anything critical."),
                severity=Severity.NORMAL, topic="decisions"))
        except Exception:
            log.exception("could not fall back to Slack after a missed call")

    return {"ok": True}


@app.get("/webhooks/twilio/audio/{token}")
async def twilio_audio(token: str) -> Response:
    """Serve one Fish Audio clip to Twilio's `<Play>`, at most three times.

    This sits under /webhooks/ so the mesh-only middleware lets it through --
    Twilio fetches media without signing the request, so it could not pass
    the check the other routes use. The token is the whole defence: three
    fetches (Twilio retries a cut-off one), ten minutes at most, and
    unguessable, because the audio carries
    estate status and this path has no login in front of it. The token is a
    key into `phone._CLIPS`, never a path: nothing from the request touches
    the filesystem, and every miss is the same 404.
    """
    from . import phone

    audio = await phone.take(token)
    if audio is None:
        # The same body as a path that matched nothing (the catch-all's
        # miss on this tree), so a miss here cannot be told from one.
        raise HTTPException(404, "not found")
    return Response(content=audio, media_type="audio/mpeg",
                    headers={"Cache-Control": "no-store"})


@app.post("/webhooks/twilio/voice/digit")
async def twilio_voice_digit(request: Request,
                             x_twilio_signature: str = Header("")) -> Response:
    """The keypad callback of calls placed before the conversation opened
    them. It once decided `?pid=` straight from the query -- and checked
    `From` against his number, which on a call ATLAS placed is ATLAS's own,
    so it could never have worked. Now a digit is a turn like any other:
    the same admission, the same gate, and only the card read back on this
    call. `?pid=` is ignored."""
    form = dict(await request.form())
    url = _public_url(request)
    if url is None:
        log.error("ATLAS_HOST is unset — refusing a Twilio webhook that "
                  "cannot be verified")
        raise HTTPException(503, "not configured")
    if not _verify_twilio(url, form, x_twilio_signature):
        raise HTTPException(401, "bad signature")
    return await _phone_turn(form, 1)


async def _handle_command(text: str, *, via: str) -> str:
    low = text.lower().strip()
    for verdict in ("approve", "always", "decline"):
        if low.startswith(verdict):
            parts = low.split()
            if len(parts) > 1 and parts[1].lstrip("#").isdigit():
                return (await decide(int(parts[1].lstrip("#")),
                                     verdict, via=via)).render()
    if low in ("status", "brief", "?"):
        lines = [m.brief() for m in REGISTRY.modules.values() if m.brief()]
        return "\n".join(lines) or "Nothing to report."
    # Anything else is a question for the agent roster.
    return await route(text)


# ---------------------------------------------------------------- mesh-only

# The old mesh-only /ask and /agents routes are gone (audit 2026-09-28): they
# had no sign-in, nothing called them, and any page Miro visited from the mesh
# could POST a task to the agents. The signed-in dashboard's /api/voice/ask
# and /api/agents are the ways in.


# -------------------------------------------------------------- dashboard
#
# Mesh-only by the middleware above: neither /api nor / is in PUBLIC_PATHS, and
# Caddy proxies only /webhooks/*, /healthz and /media/* on 443. The dashboard
# is never added to the public listener — not behind a login, not behind
# anything. `media_router` is the one exception below: `/media/{token}` IS in
# PUBLIC_PATHS (atlas/api/media.py), because Instagram's Graph API has to
# fetch a rendered video the same way Twilio fetches `/webhooks/twilio/audio`
# — an unguessable, expiring token stands in for a session there too.
app.include_router(dashboard_router)
app.include_router(public_ping_router)
app.include_router(agency_previews_router)
app.include_router(media_router)
app.include_router(voice_router)
app.include_router(voice_offline_router)
app.include_router(airglass_router)
app.include_router(slack_events_router)
app.include_router(handoffs_router)
app.include_router(mac_agent_router)
app.include_router(clips_router)
app.include_router(vault_router)
app.include_router(deliver_router)
app.include_router(academy_router)
app.include_router(academy_support_router)
app.include_router(academy_videos_router)
app.include_router(learn_router)
app.include_router(selfmodel_router)
app.include_router(system_check_router)
app.include_router(views_router)
app.include_router(agency_router)
app.include_router(agency_contact_router)
app.include_router(hud_router)
app.include_router(tradingview_router)
app.include_router(stripe_mapfront_router)
app.include_router(site_chat_router)
app.include_router(call_me_router)
# Miro's WhatsApp questions (Twilio sandbox): read-only, signed, owner-only
# (atlas/api/whatsapp.py). Under /webhooks/, so public by the path rule.
app.include_router(whatsapp_router)
app.include_router(webild_router)
app.include_router(realestate_router)
app.include_router(workboard_router)
# The AMSM channel's page, and the Mac's bearer-token progress sync
# (mesh-only like /handoffs: nothing under /api is public).
app.include_router(ig_funnel_router)
# The local project board and remediation list (read-only; writes are actions).
app.include_router(projects_router)


# ------------------------------------------------------- browser extension

MAX_BROWSER_EVENTS = 500


def _ingest_authorised(header: str) -> bool:
    """Bearer token for the browser extension.

    The mesh-only middleware already restricts this path by source address, but
    an address is not a credential: anything else on the mesh could post
    fabricated history. This is defence in depth, not decoration.
    """
    expected = os.getenv("BROWSER_INGEST_TOKEN", "")
    if not expected or len(expected) < 24:
        return False
    supplied = header[7:] if header.lower().startswith("bearer ") else ""
    return hmac.compare_digest(supplied, expected)


@app.post("/ingest/browser")
async def ingest_browser(request: Request,
                         authorization: str = Header("")) -> dict:
    """Browsing history from the extension. Mesh-only, and authenticated.

    The extension applies its own deny-list before sending; nothing on that
    list ever leaves the browser, so there is deliberately no deny-list here to
    keep in sync. Raw rows are deleted after BROWSER_RETENTION_DAYS by
    kernel.privacy; the per-domain roll-up survives.
    """
    if not _ingest_authorised(authorization):
        raise HTTPException(401, "bad ingest token")

    payload = await request.json()
    events = payload.get("events")
    if not isinstance(events, list):
        raise HTTPException(400, "events must be a list")
    if len(events) > MAX_BROWSER_EVENTS:
        raise HTTPException(413, f"at most {MAX_BROWSER_EVENTS} events per post")

    from ..kernel.state import execute_sql

    stored = 0
    for event in events:
        if not isinstance(event, dict):
            continue
        domain = str(event.get("domain") or "")[:253]
        kind = str(event.get("kind") or "visit")[:16]
        if not domain or kind not in ("visit", "dwell", "tabs"):
            continue
        at_raw = str(event.get("at") or "")
        try:
            at = datetime.fromisoformat(at_raw)
        except ValueError:
            continue
        if at.tzinfo is None:
            at = at.replace(tzinfo=UTC)
        try:
            dwell = max(0, int(event.get("dwell_ms") or 0))
            tabs = event.get("tab_count")
            tabs = int(tabs) if tabs is not None else None
        except (TypeError, ValueError):
            continue
        await execute_sql(
            """INSERT INTO browser_events
                 (at, kind, domain, url, title, dwell_ms, tab_count)
               VALUES ($1,$2,$3,$4,$5,$6,$7)""",
            at, kind, domain,
            (str(event.get("url")) [:2000] if event.get("url") else None),
            (str(event.get("title"))[:300] if event.get("title") else None),
            dwell, tabs)
        stored += 1

    return {"ok": True, "stored": stored,
            "retention_days": int(os.getenv("BROWSER_RETENTION_DAYS", "90"))}


# ----------------------------------------------------------- dashboard SPA
#
# Registered last so it can never shadow a real route. Mesh-only, like
# everything else outside PUBLIC_PATHS — Caddy does not proxy any of this on
# 443, and the source-address guard refuses it regardless.
#
# /brief and /f/{id} are served by views.py above, so a texted link gets the
# server-rendered page with no bundle. Navigating to Brief inside the SPA stays
# client-side; that is what "additionally server-rendered" means here.

STATIC = ui_paths.STATIC


class _WakeFiles(StaticFiles):
    """`/wake` -- the on-device wake models (whisper-tiny.en, the MediaPipe
    hand model and its wasm, a Porcupine keyword), served as files.

    Before this, nothing served `/wake` at all: every model request fell
    through to the SPA catch-all below and came back as index.html with a
    200, so a detector the capabilities called ready loaded the dashboard
    page as its model and never fired. The directory is resolved per request
    from `ui_paths.wake_dir()` -- the same path `voice.capabilities` checks --
    so what is served and what is reported cannot drift, and a model built in
    after startup is served without a restart. A missing file is a 404.
    """

    def __init__(self) -> None:
        super().__init__(directory=None, check_dir=False)

    @property
    def all_directories(self) -> list[str]:          # type: ignore[override]
        return [str(ui_paths.wake_dir())]

    @all_directories.setter
    def all_directories(self, _value: list[str]) -> None:
        """StaticFiles assigns this in __init__; the property is the truth."""


# Mounted whether or not the UI is built, and before the catch-all, so a
# /wake request is always answered by this -- a file or a 404, never HTML.
app.mount("/wake", _WakeFiles(), name="wake")


@app.exception_handler(404)
async def _same_miss(request: Request, exc: Exception) -> Response:
    """Every miss on this tree reads the same -- "not found" -- whether the
    catch-all answered it (the UI is built), the router did (it is not), or
    a route declined a token. A miss on a guarded path must not be
    tellable from a path that matched nothing."""
    from fastapi.responses import JSONResponse
    detail = getattr(exc, "detail", None)
    if detail in (None, "Not Found"):
        detail = "not found"
    return JSONResponse({"detail": detail}, status_code=404)

# The app icons and the manifest (2026-10-29): exact names, each the built
# file with its own type. The SPA catch-all answered them with index.html,
# so an installed ATLAS Agency showed no icon. Registered whether or not the
# UI is built (a missing file is a 404, never the shell), and read from
# `ui_paths.STATIC` at request time.
UI_FILES = {"apple-touch-icon.png": "image/png", "icon-192.png": "image/png",
            "icon-512.png": "image/png",
            "manifest.webmanifest": "application/manifest+json"}


def _ui_file_route(name: str, media_type: str) -> None:
    async def serve() -> FileResponse:
        path = ui_paths.STATIC / name
        if not path.is_file():
            raise HTTPException(404, "not found")
        return FileResponse(path, media_type=media_type)
    app.add_api_route(f"/{name}", serve, methods=["GET"], include_in_schema=False,
                      name=f"ui_file_{name}")


for _name, _type in UI_FILES.items():
    _ui_file_route(_name, _type)


@app.get("/favicon.svg", include_in_schema=False)
async def favicon() -> FileResponse:
    """Explicit, because the SPA catch-all would answer with index.html.

    A browser asking for an icon and receiving an HTML document does not
    error -- it just shows no icon, which looks like the file is missing.
    """
    icon = ui_paths.STATIC / "favicon.svg"
    if not icon.is_file():
        raise HTTPException(404, "not found")
    return FileResponse(icon, media_type="image/svg+xml")


def spa_shell(index: Path) -> FileResponse:
    """The SPA shell, revalidated on every load.

    `index.html` names the hashed bundles of the current build; a cached copy
    keeps an old page on screen after a rebuild (2026-10-29: the short Command
    Center). `no-cache` lets the browser keep it but asks the server first;
    the hashed `/assets` keep their own caching.
    """
    return FileResponse(index, headers={"Cache-Control": "no-cache"})


if STATIC.is_dir():
    app.mount("/assets", StaticFiles(directory=str(STATIC / "assets")),
              name="assets")
    # The typeface is vendored rather than fetched from a CDN: this is a
    # mesh-only tool and it should not need the public internet to render.
    if (STATIC / "fonts").is_dir():
        app.mount("/fonts", StaticFiles(directory=str(STATIC / "fonts")),
                  name="fonts")

    @app.get("/{full_path:path}", include_in_schema=False)
    async def spa(full_path: str) -> FileResponse:
        """Serve the SPA shell for any client-side route.

        An unmatched /api path must 404, not fall through to here: returning
        the HTML shell with a 200 would make every API error look to the client
        like a successful page load.
        """
        if full_path.startswith(
                ("api/", "webhooks/", "hooks/", "ingest/", "fonts/", "wake/",
                 "media/")):
            raise HTTPException(404, "not found")
        index = STATIC / "index.html"
        if not index.is_file():
            raise HTTPException(404, "dashboard is not built")
        return spa_shell(index)
else:
    log.warning("ui/dist is missing — the dashboard will not be served. "
                "Build it with: cd ui && npm ci && npm run build")
