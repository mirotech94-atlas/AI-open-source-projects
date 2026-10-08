import asyncio
from contextlib import asynccontextmanager

from apscheduler.schedulers.background import BackgroundScheduler
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from .config import settings
from . import live as live_bus
from . import scheduler as scheduler_jobs
from .integrations import registry as integrations_registry
from .routers import (
    auth, tenants, sensor, sensors, read, write, live, reports,
    settings as settings_router, public, chat,
    security, topology, updates, voice, dns, networks,
    integrations, inbound, slackbot, leads, public_feed, billing, recorders,
    api_v1, apitokens, vulnscan, router as router_admin, platform, tickets, partners,
)


@asynccontextmanager
async def _lifespan(app: FastAPI):
    # `live.bus.publish` is called from sync (threadpooled) request
    # handlers, so it needs the loop that is actually running -- captured
    # here rather than at import time, so tests that build a fresh app
    # per-test (via TestClient's `with` block) always get the right one.
    live_bus.bus.set_loop(asyncio.get_running_loop())

    # Hourly background jobs (reports_job: weekly/monthly report builds).
    # Started here (rather than at import time) so every `TestClient(...)`
    # in the test suite gets its own scheduler instance, and stopped in the
    # `finally` below so no test leaves a background thread ticking against
    # a DB engine/tmpdir that the next test has already torn down.
    sched = BackgroundScheduler()
    sched.add_job(scheduler_jobs.reports_job, "interval", hours=1, id="reports_job")
    sched.add_job(integrations_registry.watchdog_job, "interval", hours=1, id="integrations_watchdog")
    sched.add_job(scheduler_jobs.retention_job, scheduler_jobs.RETENTION_CRON, id="retention")
    sched.add_job(scheduler_jobs.demo_refresh_job, scheduler_jobs.DEMO_REFRESH_CRON, id="demo_refresh")
    sched.add_job(scheduler_jobs.demo_trickle_job, scheduler_jobs.DEMO_TRICKLE_CRON, id="demo_trickle")
    sched.add_job(scheduler_jobs.sensor_watch_job, scheduler_jobs.SENSOR_WATCH_CRON, id="sensor_watch")
    sched.add_job(scheduler_jobs.trial_expiry_job, scheduler_jobs.TRIAL_EXPIRY_CRON, id="trial_expiry")
    sched.start()
    try:
        yield
    finally:
        sched.shutdown(wait=False)
        live_bus.bus.clear_loop()

def _is_api_path(path: str) -> bool:
    """Paths the SPA catch-all must never claim.

    `api/*` is the dashboard's own surface. `v1/*` is the public REST API on
    api.guarder.io: without it here, a mistyped public-API path would fall
    through to `index.html` and answer a script with 15KB of HTML and a 200 --
    on the one hostname whose callers are machines, and where nginx proxies
    nothing but `/v1/`.
    """
    return path in ("api", "v1") or path.startswith(("api/", "v1/"))

# index.html names the content-hashed bundle to load, so a browser holding a
# cached copy of it keeps running yesterday's app -- silently, and for as long
# as its cache lasts, because FileResponse sends an ETag and no Cache-Control
# at all.
#
# This was `no-cache`, which means revalidate-before-use and is enough for an
# ordinary reload. It is NOT enough for back/forward navigation or a restored
# tab, which browsers serve from the back-forward cache without revalidating
# anything -- so the redesign of the console could be deployed and a page
# reached by the back button would still be the old one. `no-store` closes
# that. The cost is one full fetch of a 1KB file per navigation, which is
# nothing; the bundle it names is content-hashed and stays immutable below.
#
# The same header covers `/build.json`, which the running page polls to learn
# that a newer bundle exists (see `web/src/lib/buildwatch.tsx`); a cached
# answer to that question would make the whole mechanism inert.
_NO_CACHE = {"Cache-Control": "no-store"}


class _ImmutableStatic(StaticFiles):
    """/static, whose file names carry a content hash.

    A hashed name can never change meaning, so it is safe to cache for a year
    and pointless to revalidate. This is the other half of `_NO_CACHE` above:
    the document that names the bundles is always fresh, and the bundles it
    names are never fetched twice."""

    def file_response(self, *args, **kwargs):
        resp = super().file_response(*args, **kwargs)
        resp.headers["Cache-Control"] = "public, max-age=31536000, immutable"
        return resp


def _mount_web(app: FastAPI) -> None:
    """Serve the built web/dist SPA, when present, alongside the API.

    - `/static/*` is served directly from `web_dist/static` (Vite's
      content-hashed JS/CSS bundles — `vite.config.ts` sets `build.assetsDir`
      to "static" rather than Vite's default "assets", because the app's
      own `/assets` SPA route (Websites/Servers/Devices/Screen-time) would
      otherwise collide with a StaticFiles mount at that same prefix: a
      request for `/assets/devices` would resolve inside the mount instead
      of falling through to the catch-all below, 404ing the whole tab).
    - Any other non-API path serves the matching real file under
      `web_dist` when it exists (e.g. `favicon.svg`), else falls back to
      `index.html` so client-side routes (`/login`, `/assets/devices`,
      etc.) resolve.
    - `/api/*` paths that no router claimed still 404 as JSON — this route
      is registered last, so it only ever sees requests every API router
      already declined.
    """
    web_dist = settings.web_dist
    if not web_dist or not web_dist.is_dir():
        return

    assets_dir = web_dist / "static"
    if assets_dir.is_dir():
        app.mount("/static", _ImmutableStatic(directory=assets_dir), name="web-assets")

    index_file = web_dist / "index.html"
    web_dist_resolved = web_dist.resolve()

    # HEAD as well as GET. FastAPI's own APIRoute does not add HEAD for a GET
    # the way Starlette's plain Route does, so without this every HEAD request
    # to the app answers 405 -- and HEAD is what most uptime monitors send, so
    # an external check would report Vault down while it was serving fine.
    @app.api_route("/{path:path}", methods=["GET", "HEAD"], include_in_schema=False)
    def spa(path: str):
        if _is_api_path(path):
            raise HTTPException(404, "not found")
        if path:
            candidate = (web_dist / path).resolve()
            try:
                candidate.relative_to(web_dist_resolved)
            except ValueError:
                candidate = None  # path escapes web_dist; ignore it
            if candidate is not None and candidate.is_file():
                return FileResponse(candidate, headers=_NO_CACHE)
        if index_file.is_file():
            return FileResponse(index_file, headers=_NO_CACHE)
        raise HTTPException(404, "not found")

#: Values `secret_key` must never hold on a hub reachable over https.
_PLACEHOLDER_SECRETS = frozenset({"", "change-me"})


def _refuse_insecure_config() -> None:
    """Fail closed rather than serve the internet with the shipped secret.

    `secret_key` defaults to "change-me" so a laptop checkout runs with no
    setup, and nothing stopped that default from reaching production: a hub
    whose `.env` lost the line (a hand-edited file, a restore from an old
    backup) came up and served my.guarder.io keyed on a string that is in
    this public repository. An https base_url is the signal that this is a
    real deployment; there, the placeholder is a refusal to start, not a
    warning in a log nobody reads. Local and test hubs (http) are unaffected.
    """
    if (settings.base_url or "").strip().lower().startswith("https://") and \
            (settings.secret_key or "").strip() in _PLACEHOLDER_SECRETS:
        raise RuntimeError(
            "GUARDIAN_SECRET_KEY is unset or still 'change-me' while GUARDIAN_BASE_URL is https. "
            "Set a long random secret in hub/.env before starting a public hub.")


def create_app() -> FastAPI:
    _refuse_insecure_config()
    app = FastAPI(title="Vault", version="0.1.0", docs_url="/api/docs", openapi_url="/api/openapi.json",
                  lifespan=_lifespan)

    # GET in the document, HEAD alongside it but out of the document: a
    # HEAD-based uptime monitor pointed at this endpoint must not read 405,
    # and declaring both methods on one route would emit two operations with
    # the same generated operation id.
    @app.get("/api/v1/health")
    @app.head("/api/v1/health", include_in_schema=False)
    def health():
        return {"ok": True, "product": "Vault", "version": app.version}

    app.include_router(auth.router)
    app.include_router(tenants.router)
    app.include_router(partners.router)
    app.include_router(sensor.router)
    app.include_router(sensors.router)
    app.include_router(read.router)
    app.include_router(write.router)
    app.include_router(vulnscan.router)
    app.include_router(live.router)
    app.include_router(reports.router)
    app.include_router(settings_router.router)
    app.include_router(chat.router)
    app.include_router(voice.router)
    app.include_router(security.router)
    app.include_router(topology.router)
    app.include_router(updates.router)
    app.include_router(dns.router)
    app.include_router(networks.router)
    app.include_router(recorders.router)
    app.include_router(router_admin.router)
    app.include_router(integrations.router)
    app.include_router(inbound.router)
    app.include_router(slackbot.router)
    app.include_router(leads.router)
    app.include_router(platform.router)
    app.include_router(tickets.router)
    # Billing: the admin-only plan/checkout/portal routes, plus the
    # unauthenticated Stripe webhook (signature-verified in the route).
    app.include_router(billing.router)
    # The marketing site's live panel: unauthenticated, demo-tenant-only,
    # cached and rate-limited in the router itself.
    app.include_router(public_feed.router)
    # The app's own API-token admin and the API guide's content -- session-authed
    # dashboard routes, so they answer on the app hostname only.
    app.include_router(apitokens.router)
    # The public REST API on api.guarder.io. Registered here, before
    # `_mount_web`, because the SPA catch-all below claims every path it is
    # given and would otherwise swallow `/v1/*` GETs into index.html.
    app.include_router(api_v1.router)
    # Public/ops routes (/act, /status/<slug>, /hb, /r/<token>, /metrics) --
    # registered before the SPA catch-all so they never fall through to
    # index.html the way an unclaimed /api/* path would.
    app.include_router(public.router)

    # Registered after every API router so `/api/*` 404s stay JSON — this
    # catch-all only ever runs for a path no router above it claimed.
    _mount_web(app)

    return app

app = create_app()
