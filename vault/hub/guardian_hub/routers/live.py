"""`GET /api/v1/live` -- the 10-second "is it up right now" probe.

The port of Nexus `/api/live`: probe every configured site concurrently with
a hard per-probe timeout, and report the sensor's age and whether a scan is
running. Three bounds keep a dead site from ever holding the request open:

1. every probe has a 6 s socket timeout and swallows all errors;
2. probes run in a `ThreadPoolExecutor(8)`, and the gather itself has a wall
   clock deadline -- a probe still running past it is reported as down and
   left to finish in the background;
3. results are cached per tenant for 10 s, and the route is rate-limited to
   30 calls/minute/tenant, so a hammering browser cannot fan out probes.

Point 3 is why the dashboard (`Shell.tsx` `LIVE_POLL_MS`) can poll this route
at 4s -- the middle of the product's 3-5s "feels live" band -- without that
turning into 4s-cadence probe fan-out: a cache hit costs two cheap DB
lookups (an open-queue check, a max(sensor.last_seen)) and nothing else, so
polling faster than `_CACHE_TTL` buys freshness on those two fields without
buying any extra outbound probing. `_RATE_LIMIT` was raised from its original
12/min to 30/min alongside that change: one tab at 4s uses 15 calls/min, and
30 leaves a full second tab's worth of headroom on the same tenant -- the
same "one tab's usage, doubled" margin the original 12/min-at-15s pairing
gave a second tab.

Task 21 appends `WS /ws` and `GET /queue/{id}` to this module.
"""

import asyncio
import concurrent.futures
import datetime as dt
import ssl
import threading
import time
import urllib.error
import urllib.request

from fastapi import APIRouter, Depends, HTTPException, WebSocket
from starlette.websockets import WebSocketDisconnect, WebSocketState
from sqlalchemy.orm import Session as DbSession

from .. import models as m, netguard, ratelimit
from ..db import get_db
from ..deps import COOKIE, memberships, require_role
from ..live import bus
from ..payloads import traffic as traffic_payload
from ..schemas_dash import LivePayload, LiveSite, QueueStatus

router = APIRouter(prefix="/api/v1", tags=["dashboard-live"])

VIEWER = Depends(require_role("viewer"))

PING_INTERVAL_S = 25.0
#: How long a client that HAS answered a ping may then go quiet before the
#: socket is treated as dead. Enforced only after the first `pong` -- see the
#: loop in `ws_live` for why.
PONG_TIMEOUT_S = 60.0
TENANT_CHANGED_CODE = 4409   # closes the socket after POST /me/tenant moves the session to a new tenant
PONG_TIMEOUT_CODE = 4408     # closes a socket whose client answered pings once and then stopped
DNS_REPLAY_LIMIT = 25        # rows sent immediately on `{"subscribe": ["dns"]}`
#: Streams a socket gets only after `{"subscribe": [...]}`: both can run to
#: many frames a second, and a tab that is not showing them should not pay.
OPT_IN_STREAMS = frozenset({"dns", "flow"})

_HTTP_TIMEOUT = 6.0             # per-probe socket timeout, seconds
_GATHER_DEADLINE = 8.0          # wall-clock bound on the whole fan-out
_CACHE_TTL = 10.0               # seconds a tenant's probe result is reused
_MAX_WORKERS = 8
_RATE_LIMIT = 30                # calls per minute per tenant -- see module docstring
_RATE_WINDOW = 60

_USER_AGENT = "guardian-live/1.0"

# Probes are a liveness check, not a certificate audit (cert expiry comes from
# the sensor's own scan), so a bad chain must not turn into a false "down".
_SSL_CTX = ssl.create_default_context()
_SSL_CTX.check_hostname = False
_SSL_CTX.verify_mode = ssl.CERT_NONE

_cache_lock = threading.Lock()
_cache: dict[str, tuple[float, int, list[LiveSite]]] = {}
_cache_gen: dict[str, int] = {}  # per-tenant generation counter to prevent stale probes from overwriting fresh results


def _open(url: str, method: str):
    """Issue one request. Split out so tests can simulate a dead network.

    Only ever to a public address, checked here -- before every fetch, and
    again at every redirect hop -- rather than when the site was saved: a
    name that was public at save time can be re-pointed at 169.254.169.254
    or the hub's own port afterwards (see `guardian_hub.netguard`). Raises
    `netguard.NotPublicAddress` for anything else."""
    req = urllib.request.Request(url, method=method, headers={"User-Agent": _USER_AGENT})
    return netguard.open_public(req, timeout=_HTTP_TIMEOUT, context=_SSL_CTX)


def _probe(site: dict) -> tuple[str, dict]:
    """HEAD the site, falling back to GET when HEAD is refused.

    Returns `(name, {"ms", "code", "ok"})` and never raises: an unreachable
    site is `code=None, ok=False`.
    """
    name = site.get("name") or site.get("url") or ""
    expect = site.get("expect") or [200]
    if isinstance(expect, int):
        expect = [expect]
    url = site.get("url") or ""
    t0 = time.monotonic()
    code = None
    if url:
        for method in ("HEAD", "GET"):
            try:
                with _open(url, method) as r:
                    code = r.status
                    if method == "GET":
                        r.read(512)
            except urllib.error.HTTPError as e:
                code = e.code
            except netguard.NotPublicAddress:
                # Not measured, rather than "down": the hub will not look
                # at this address at all, so it has no opinion about it --
                # and UNKNOWN must never be drawn as a verdict either way.
                return name, {"ms": None, "code": None, "ok": None}
            except Exception:
                code = None
            # A 4xx from HEAD usually means "HEAD not allowed here" -- retry
            # once with GET before calling the site down.
            if code is not None and code < 400:
                break
            if method == "GET":
                break
    return name, {"ms": int((time.monotonic() - t0) * 1000), "code": code,
                  "ok": bool(code in expect) if code else False}



# Module-level, long-lived executor. `_probe` is a blocking socket call, and
# `Future.cancel()` is a no-op once a worker thread has actually started it
# -- so a `with ThreadPoolExecutor(...)` block around the fan-out is a trap:
# `__exit__` calls `shutdown(wait=True)`, which blocks the request until
# every started probe finishes, deadline or not. Using one persistent pool
# instead means the request can return at `_GATHER_DEADLINE` while slow
# probes keep running in the background; `_watch_pending` below picks up
# their results and refreshes the per-tenant cache when they land.
_EXECUTOR = concurrent.futures.ThreadPoolExecutor(_MAX_WORKERS)


def _probe_sites(sites: list[dict]) -> tuple[list[LiveSite], list[concurrent.futures.Future]]:
    """Fan out one probe per site on `_EXECUTOR` and wait up to
    `_GATHER_DEADLINE`. Returns `(sites-as-of-now, still-running-futures)` --
    a site whose probe hasn't finished by the deadline comes back
    `ok=None` (timed out, not "down") and its future is handed back so the
    caller can watch it finish."""
    if not sites:
        return [], []
    futures = {_EXECUTOR.submit(_probe, s): s for s in sites}
    results: dict[str, dict] = {}
    try:
        for fut in concurrent.futures.as_completed(futures, timeout=_GATHER_DEADLINE):
            try:
                name, res = fut.result()
            except Exception:
                continue
            results[name] = res
    except concurrent.futures.TimeoutError:
        pass        # whatever finished by the deadline is what we report now
    pending = [fut for fut in futures if not fut.done()]
    out = []
    for s in sites:
        name = s.get("name") or s.get("url") or ""
        res = results.get(name)
        out.append(LiveSite(name=name, url=s.get("url") or "", code=(res or {}).get("code"),
                            ms=(res or {}).get("ms"), ok=(res or {}).get("ok")))
    return out, pending


def _watch_pending(tenant_id: str, sites: list[dict], pending: list[concurrent.futures.Future], cycle_gen: int) -> None:
    """Attach a done-callback to every still-running probe future so that,
    once ALL of them have finished, the per-tenant cache is refreshed with
    the completed results (merged with whatever the immediate response
    already had for the sites that finished in time). Only write to cache
    if this cycle's generation >= the cached generation, preventing stale
    probes from overwriting fresh results."""

    def _refresh(_fut=None):
        if not all(f.done() for f in pending):
            return
        results: dict[str, dict] = {}
        for f in pending:
            try:
                name, res = f.result()
            except Exception:
                continue
            results[name] = res
        with _cache_lock:
            hit = _cache.get(tenant_id)
        # Only merge if this generation >= cached generation (if hit exists)
        cached_gen = hit[1] if hit else -1
        if cached_gen > cycle_gen:
            # A newer cycle is already in the cache, don't overwrite it
            return
        existing = {ls.name: ls for ls in (hit[2] if hit else [])}
        out = []
        for s in sites:
            name = s.get("name") or s.get("url") or ""
            if name in results:
                res = results[name]
                out.append(LiveSite(name=name, url=s.get("url") or "", code=res.get("code"),
                                    ms=res.get("ms"), ok=res.get("ok")))
            else:
                out.append(existing.get(name) or LiveSite(name=name, url=s.get("url") or ""))
        with _cache_lock:
            # Keep the cached entry's timestamp (don't update to now)
            cached_ts = hit[0] if hit else time.monotonic()
            _cache[tenant_id] = (cached_ts, cycle_gen, out)

    for fut in pending:
        fut.add_done_callback(_refresh)


def _cached_probe(tenant_id: str, sites: list[dict]) -> list[LiveSite]:
    now = time.monotonic()
    with _cache_lock:
        hit = _cache.get(tenant_id)
        if hit and now - hit[0] < _CACHE_TTL:
            return hit[2]
        # Increment generation for this probe cycle
        _cache_gen[tenant_id] = _cache_gen.get(tenant_id, 0) + 1
        cycle_gen = _cache_gen[tenant_id]
    probed, pending = _probe_sites(sites)
    with _cache_lock:
        _cache[tenant_id] = (time.monotonic(), cycle_gen, probed)
    if pending:
        _watch_pending(tenant_id, sites, pending, cycle_gen)
    return probed


#: How many trailing `latency_ms` points the header's sparkline draws. Kept
#: well below `payloads/assets.LATENCY_SPARK_POINTS` (48, a whole-week trend
#: for the Sites tab) -- the header pill is small and the mockup's own line
#: is 12 points.
LIVE_SPARK_POINTS = 12


def _latency_sparks(db: DbSession, tenant_id: str, names: list[str]) -> dict[str, list[float]]:
    """The last `LIVE_SPARK_POINTS` `latency_ms` readings per site name,
    oldest first. One query for every named site, bucketed in Python -- the
    same shape `payloads/assets.sites()` uses for its own (longer) spark, so
    the two tabs can never disagree about a site's latency history."""
    if not names:
        return {}
    rows = (
        db.query(m.Metric.entity, m.Metric.ts, m.Metric.value)
        .filter(
            m.Metric.tenant_id == tenant_id,
            m.Metric.name == "latency_ms",
            m.Metric.entity.in_(names),
        )
        .order_by(m.Metric.ts.asc())
        .all()
    )
    by_name: dict[str, list[float]] = {}
    for entity, _ts, value in rows:
        if value is None:
            continue
        by_name.setdefault(entity, []).append(float(value))
    return {name: pts[-LIVE_SPARK_POINTS:] for name, pts in by_name.items()}


def _with_sparks(db: DbSession, tenant_id: str, sites: list[LiveSite]) -> list[LiveSite]:
    """Fresh `LiveSite` copies carrying `spark` -- never mutates the list the
    probe cache holds, since that cache is reused verbatim on every hit
    within `_CACHE_TTL` and must stay probe-only."""
    if not sites:
        return sites
    sparks = _latency_sparks(db, tenant_id, [s.name for s in sites if s.name])
    return [
        LiveSite(name=s.name, url=s.url, code=s.code, ms=s.ms, ok=s.ok,
                 spark=[round(v, 1) for v in sparks.get(s.name, [])])
        for s in sites
    ]


def cache_clear() -> None:
    """Test/ops helper: drop every tenant's cached probe result."""
    with _cache_lock:
        _cache.clear()
        _cache_gen.clear()


#: The one tenant whose `config["sites"]` are never real reachable hosts --
#: `seed.py`/`demoscrub.py` deliberately point them at `*.northwind-dental
#: .example` / `*.northwind-clinics.example`, reserved by RFC 6761 and
#: guaranteed to never resolve, so nobody's real address ever leaks into the
#: public demo. Actually probing those over the network (see `_probe`
#: above) can therefore only ever answer "down" -- not a measurement, a
#: certainty -- which is exactly the wrong half of the F4 mismatch: the
#: seeded overview strip (`payloads/overview.health_strip`, sourced from
#: `Site.last`, written by `ingest.store_scan` from the same synthetic
#: fixture) says every site is up, while this route's real probe says all
#: four are down, and the wallboard (which reads *this* route) is the one a
#: prospect sees flash red. Below, this one tenant's `/live` sites are
#: served from that same `Site.last` synthetic source instead of a real
#: probe -- the fix picked in the F4 brief -- so every surface agrees.
DEMO_SLUG = "demo"


def _synthetic_sites(db: DbSession, tenant_id: str, sites: list[dict]) -> list[LiveSite]:
    """`LiveSite` rows for the demo tenant, sourced from `Site.last` -- the
    same synthetic scan result `seed_demo`/`refresh_demo` ingest for every
    other demo surface (the overview strip, the security tab, topology) --
    rather than a real network probe of an address that cannot resolve.
    `Site.last` is the raw fixture site dict (`{"ok","code","latency_s",...}`,
    see `hub/tests/fixtures/last.json`), scrubbed but otherwise untouched by
    `demoscrub`, so a site missing from the table (no scan has landed yet)
    falls back to `ok=None` -- "no data" -- rather than a fabricated `True`.
    """
    by_name = {s.name: (s.last or {}) for s in db.query(m.Site).filter_by(tenant_id=tenant_id).all()}
    out = []
    for s in sites:
        name = s.get("name") or s.get("url") or ""
        last = by_name.get(name) or {}
        latency_s = last.get("latency_s")
        out.append(LiveSite(
            name=name, url=s.get("url") or "",
            code=last.get("code"),
            ms=(latency_s * 1000) if isinstance(latency_s, (int, float)) else last.get("ms"),
            ok=last.get("ok"),
        ))
    return out


@router.get("/live", response_model=LivePayload)
def live(ctx=VIEWER, db: DbSession = Depends(get_db)):
    user, tenant, role = ctx
    if not ratelimit.allow(f"live:{tenant.id}", _RATE_LIMIT, _RATE_WINDOW):
        raise HTTPException(429, "too many live probes; try again in a minute")

    sites = [s for s in ((tenant.config or {}).get("sites") or []) if isinstance(s, dict)]
    # Two independent changes meet here and both are load-bearing.
    #
    # The demo tenant's site targets are `*.example` names that can never
    # resolve, so probing them really did return `ok:false` for all four while
    # the seeded Overview strip claimed they were healthy -- the wallboard's
    # "SITES UP 0 of 4" was the honest surface and the strip was the lie.
    # The demo therefore reads its site state from the same synthetic source as
    # the rest of its data.
    #
    # The sparkline is per-site latency history, and it is attached AFTER
    # whichever of the two produced the rows, so the demo gets a line too when
    # its refresh has written latency points and an empty one when it has not.
    # An empty spark draws nothing, which is the correct answer to "no history".
    probed = (_synthetic_sites(db, tenant.id, sites) if tenant.slug == DEMO_SLUG
              else _cached_probe(tenant.id, sites))
    probed = _with_sparks(db, tenant.id, probed)

    scanning = (
        db.query(m.Queue)
        .filter_by(tenant_id=tenant.id, path="/api/run", status="running")
        .first() is not None
    )
    now = dt.datetime.now(dt.timezone.utc)
    reporting = [s for s in db.query(m.Sensor).filter_by(tenant_id=tenant.id).all() if s.last_seen]
    # The freshest reporter is the one the freshness figure is already about, so
    # the load figure that sits in the same slot must come from that same
    # sensor -- any other choice makes the number unattributable.
    freshest = max(reporting, key=lambda s: s.last_seen) if reporting else None
    sensor_age_s = int((now - freshest.last_seen).total_seconds()) if freshest else None

    # A lapsed deadline reads as "no maintenance" rather than a past date the
    # UI would have to re-check itself.
    until = (tenant.settings or {}).get("maintenance_until") or None
    if until:
        try:
            if dt.datetime.fromisoformat(until) <= now:
                until = None
        except ValueError:
            until = None

    return LivePayload(ts=now.isoformat(timespec="seconds"), sites=probed,
                       scanning=scanning, sensor_age_s=sensor_age_s,
                       load1=freshest.load1 if freshest else None,
                       cores=freshest.cores if freshest else None,
                       maintenance_until=until)


@router.get("/queue/{qid}", response_model=QueueStatus)
def queue_status(qid: str, ctx=VIEWER, db: DbSession = Depends(get_db)):
    """What the chat and the UI poll for "queued -> running -> done"."""
    user, tenant, role = ctx
    q = db.query(m.Queue).filter_by(tenant_id=tenant.id, id=qid).first()
    if not q:
        raise HTTPException(404, "queue item not found")
    return QueueStatus(id=q.id, path=q.path, status=q.status, body=q.body or {},
                       result=q.result, ts=q.ts.isoformat(timespec="seconds"))


def _ws_session(websocket: WebSocket, db: DbSession) -> tuple["m.Session", "m.Tenant"] | None:
    tok = websocket.cookies.get(COOKIE)
    s = db.get(m.Session, tok) if tok else None
    now = dt.datetime.now(dt.timezone.utc)
    if not s or not s.tenant_id or s.expires < now:
        return None
    tenant = db.get(m.Tenant, s.tenant_id)
    if not tenant:
        return None
    if not _still_member(db, s.user_id, tenant.id):
        return None
    return s, tenant


def _still_member(db: DbSession, user_id: str, tenant_id: str) -> bool:
    """Does this user still hold ANY role on this tenant?

    A session row names a tenant, but it does not grant one: the grant is the
    membership (direct, or through a partner), and `deps.require_role` checks
    it on every REST call. The socket used to check only the session, once,
    at connect -- so a member an admin had removed kept receiving every
    alert, DNS query and flow of that tenant for as long as the tab stayed
    open. `memberships()` is the one definition of "may open this tenant"
    (see its docstring), so this asks it rather than querying `Membership`
    directly and missing the partner and superadmin cases."""
    user = db.get(m.User, user_id)
    if user is None:
        return False
    return any(t.id == tenant_id for t, _role in memberships(db, user))


def _session_tenant_id(db: DbSession, session_id: str) -> str | None:
    """Fresh read of `Session.tenant_id`/`expires` for `session_id`, bypassing
    the ORM identity map (a plain `db.get`/attribute access on an
    already-loaded `Session` object would just return the cached value from
    when the socket connected, never seeing a later `POST /me/tenant`).
    `db.rollback()` first drops any transaction this long-lived connection's
    `db` may be sitting in so the SELECT sees the current committed row, not
    a stale snapshot. Returns `None` when the session is gone or expired --
    signed out from under the socket."""
    db.rollback()
    now = dt.datetime.now(dt.timezone.utc)
    row = (
        db.query(m.Session.tenant_id, m.Session.expires)
        .filter(m.Session.id == session_id)
        .first()
    )
    if not row or not row[0] or row[1] < now:
        return None
    return row[0]


def _dns_replay_frames(db: DbSession, tenant_id: str) -> list[dict]:
    """The tenant's most recent `DNS_REPLAY_LIMIT` `DnsQuery` rows, oldest
    first, shaped as `dns` live-event `data` payloads. Sent immediately once
    a client subscribes (`{"subscribe": ["dns"]}`) so the Live query stream
    on the DNS tab isn't empty until the sensor's next real query -- a
    freshly-seeded demo tenant, or `home` between polls, has no query due for
    a while otherwise. `device` is left out: `DnsQuery` doesn't carry it, and
    the web client's `toStreamRow` already defaults a missing field."""
    rows = (
        db.query(m.DnsQuery)
        .filter(m.DnsQuery.tenant_id == tenant_id)
        .order_by(m.DnsQuery.id.desc())
        .limit(DNS_REPLAY_LIMIT)
        .all()
    )
    rows.reverse()
    return [
        {
            "ts": r.ts, "mac": r.mac, "client": r.client, "name": r.name,
            "qtype": r.qtype, "blocked": r.blocked, "list": r.list, "ms": r.ms,
        }
        for r in rows
    ]


@router.websocket("/ws")
async def ws_live(websocket: WebSocket, db: DbSession = Depends(get_db)):
    """Per-tenant live feed. Authenticates with the same `guardian_session`
    cookie used by the REST API; a missing/expired session closes the
    handshake with code 4401 (rejected before `accept()`).

    First client frame may be `{"subscribe": ["dns"]}` to opt into the DNS
    stream (off by default -- it can be noisy). After that, any client frame
    is read opportunistically as either a `{"subscribe": [...]}` update or a
    `{"type": "pong"}` heartbeat reply; nothing the client sends is required
    for the feed to work.

    The socket is bound to the tenant its session pointed at when it
    connected. If a later `POST /me/tenant` moves that same session to a
    different tenant, this connection is still subscribed to the OLD
    tenant's bus channel -- re-subscribing it in place would need to tear
    down and rebuild the `bus.subscribe` generator mid-loop, so instead we
    just close the socket with `TENANT_CHANGED_CODE` (4409) once the switch
    is observed. The web client's `useLiveFeed` treats any non-shutdown
    close as reconnect-with-backoff (see `web/src/lib/live.ts`), so it opens
    a fresh `/ws` connection that authenticates against the session's new
    `tenant_id` and gets that tenant's events -- no special client-side
    handling of 4409 is required. We check for the switch on every loop
    iteration, which covers both "every ping interval" (the ping ticker
    below fires at least that often) and "on every received client
    message" (a `recv_task` completion is itself a loop iteration).
    """
    auth = _ws_session(websocket, db)
    if not auth:
        await websocket.close(code=4401)
        return
    session, tenant = auth
    bound_tenant_id = tenant.id

    await websocket.accept()
    await websocket.send_json({"type": "ready", "data": {"tenant": tenant.slug}})

    # Frame types a socket receives only after asking (`{"subscribe": [...]}`)
    # -- the noisy streams. Everything else is always sent.
    subscribed: set[str] = set()
    agen = bus.subscribe(tenant.id)
    recv_task = asyncio.ensure_future(websocket.receive_json())
    event_task = asyncio.ensure_future(agen.__anext__())
    # A dedicated ticker task, separate from the message-driven tasks above,
    # so a ping goes out every PING_INTERVAL_S regardless of how much other
    # traffic (events, client frames) is flowing -- a busy socket must not
    # starve the heartbeat.
    ping_task = asyncio.ensure_future(asyncio.sleep(PING_INTERVAL_S))
    # None until the client's first `pong`. The dashboard shipped for weeks
    # without answering pings at all, and a browser keeps running the bundle
    # it loaded until it reloads -- so enforcing the timeout from connect
    # disconnected every one of those tabs every ~77 s (three 25 s pings past
    # a 60 s deadline, plus the client's reconnect backoff), all day, and
    # whatever was published in each gap never reached them. The docstring
    # above has always promised that nothing the client sends is required.
    # A client that has answered once has shown it speaks the heartbeat, so
    # its silence afterwards does mean the connection is dead.
    last_pong: float | None = None
    close_code: int | None = None

    try:
        while True:
            done, _pending = await asyncio.wait(
                {recv_task, event_task, ping_task}, return_when=asyncio.FIRST_COMPLETED,
            )

            if ping_task in done:
                ping_task = asyncio.ensure_future(asyncio.sleep(PING_INTERVAL_S))
                if websocket.client_state != WebSocketState.CONNECTED:
                    break
                if last_pong is not None and time.monotonic() - last_pong > PONG_TIMEOUT_S:
                    # An explicit close, not a bare `break`: returning without
                    # one left the peer to notice on its own, which uvicorn
                    # happens to do and Starlette's test client does not.
                    close_code = PONG_TIMEOUT_CODE
                    break
                await websocket.send_json({"type": "ping", "data": {}})

            if event_task in done:
                evt = event_task.result()
                if evt.type not in OPT_IN_STREAMS or evt.type in subscribed:
                    await websocket.send_json({"type": evt.type, "ts": evt.ts, "data": evt.data})
                event_task = asyncio.ensure_future(agen.__anext__())

            if recv_task in done:
                try:
                    msg = recv_task.result()
                except WebSocketDisconnect:
                    break
                if isinstance(msg, dict):
                    if "subscribe" in msg:
                        wanted = {k for k in (msg.get("subscribe") or []) if k in OPT_IN_STREAMS}
                        # Replay only on the transition into a stream, so
                        # re-sending the same list is not a second burst.
                        if "dns" in wanted and "dns" not in subscribed:
                            for frame in _dns_replay_frames(db, bound_tenant_id):
                                await websocket.send_json({"type": "dns", "ts": frame["ts"], "data": frame})
                        if "flow" in wanted and "flow" not in subscribed:
                            for row in traffic_payload.recent_stream_rows(db, bound_tenant_id):
                                await websocket.send_json({"type": "flow", "ts": row.ts, "data": row.model_dump()})
                        subscribed = wanted
                    if msg.get("type") == "pong":
                        last_pong = time.monotonic()
                recv_task = asyncio.ensure_future(websocket.receive_json())

            # Re-check the session's tenant on every iteration (ping tick or
            # client message alike) rather than only here-and-there.
            current_tenant_id = _session_tenant_id(db, session.id)
            if current_tenant_id is None:
                close_code = 4401
                break
            # And the membership behind it: removing someone from a tenant
            # must end their live view of it now, not when they next reload.
            # `_session_tenant_id` has just rolled back, so this reads the
            # current committed rows.
            if not _still_member(db, session.user_id, bound_tenant_id):
                close_code = 4401
                break
            if current_tenant_id != bound_tenant_id:
                close_code = TENANT_CHANGED_CODE
                break
    except WebSocketDisconnect:
        pass
    finally:
        recv_task.cancel()
        event_task.cancel()
        ping_task.cancel()
        # `agen.aclose()` throws `GeneratorExit` into `agen`'s currently
        # suspended `await` -- which fails with "already running" if
        # `event_task` (holding `agen.__anext__()`) hasn't actually
        # unwound yet. Wait for the cancellations to land first.
        for t in (recv_task, event_task, ping_task):
            try:
                await t
            except (asyncio.CancelledError, Exception):
                pass
        await agen.aclose()
        if close_code is not None and websocket.client_state == WebSocketState.CONNECTED:
            await websocket.close(code=close_code)
