"""`guardian selftest`: a live health check of a running Vault deployment.

Prints one `ok  <name>` / `FAIL <name>: <why>` / `skip <name>: <why>` line per
check and returns 0 when every check is ok or skipped, 1 if any check fails.

Never prints secrets: sensor tokens, ntfy topic names, and OTP codes are
never read or echoed by any check here.
"""

import datetime as dt
import os
import pathlib

import httpx
from sqlalchemy import inspect

from . import models as m
from .config import settings
from .db import SessionLocal, engine
from .scans import latest_scan, parse_ts

SCAN_MAX_AGE_MINUTES = 25


def _ok(name: str) -> None:
    print(f"ok  {name}")


def _fail(name: str, why: str) -> None:
    print(f"FAIL {name}: {why}")


def _skip(name: str, why: str) -> None:
    print(f"skip {name}: {why}")


def _check_health(get) -> bool:
    name = "health"
    try:
        r = get("/api/v1/health")
    except Exception as e:
        _fail(name, str(e)); return False
    if r.status_code != 200:
        _fail(name, f"status {r.status_code}"); return False
    try:
        body = r.json()
    except Exception:
        body = {}
    if body.get("product") != "Vault":
        _fail(name, f"unexpected body {body!r}"); return False
    _ok(name)
    return True


def _check_login_page(get) -> bool:
    name = "login_page"
    if not settings.web_dist:
        _skip(name, "no web_dist configured (GUARDIAN_WEB_DIST unset, no web/dist build found)")
        return True
    try:
        r = get("/")
    except Exception as e:
        _fail(name, str(e)); return False
    if r.status_code != 200:
        _fail(name, f"status {r.status_code}"); return False
    _ok(name)
    return True


def _check_me(get) -> bool:
    name = "me"
    try:
        r = get("/api/v1/me")
    except Exception as e:
        _fail(name, str(e)); return False
    if r.status_code != 401:
        _fail(name, f"expected 401 without a session cookie, got {r.status_code}"); return False
    _ok(name)
    return True


def _check_db_migrated() -> bool:
    name = "db_migrated"
    try:
        with engine.connect() as conn:
            insp = inspect(conn)
            tables = insp.get_table_names()
            if "alembic_version" not in tables:
                if tables:
                    _skip(name, "no alembic_version table (schema created via create_all, not migrations)")
                    return True
                _fail(name, "database has no tables")
                return False

            from alembic.config import Config
            from alembic.runtime.migration import MigrationContext
            from alembic.script import ScriptDirectory

            cfg = Config(str(pathlib.Path(__file__).resolve().parent.parent / "alembic.ini"))
            script = ScriptDirectory.from_config(cfg)
            head = script.get_current_head()
            current = MigrationContext.configure(conn).get_current_revision()
            if current != head:
                _fail(name, f"db at {current!r}, head is {head!r}")
                return False
    except Exception as e:
        _fail(name, str(e)); return False
    _ok(name)
    return True


def _check_tenants(db) -> bool:
    name = "tenants"
    count = db.query(m.Tenant).count()
    if count < 1:
        _fail(name, "no tenants configured")
        return False
    _ok(name)
    return True


def _check_scan_freshness(db) -> bool:
    """Skips any tenant carrying a `demo_code` (`guardian seed-demo`'s
    marker, `routers.auth.verify`'s own check for it): that tenant's
    history is deliberately frozen at seed time -- `seed_demo`'s docstring
    calls out re-seeding the same fixtures as producing byte-identical
    timestamps, on purpose, so idempotency holds -- and only ever drifts
    further from "now" the longer it's been since the last `seed-demo` run.
    A demo network reporting no *live* sensor is not an outage to page
    on."""
    name = "scan_freshness"
    tenants_with_active_sensor = [
        t for t in db.query(m.Tenant).all()
        if db.query(m.Sensor).filter_by(tenant_id=t.id, status="active").first()
    ]
    if not tenants_with_active_sensor:
        _skip(name, "no active sensors")
        return True

    ok = True
    now = dt.datetime.now(dt.timezone.utc)
    for t in tenants_with_active_sensor:
        if (t.settings or {}).get("demo_code"):
            _skip(f"{name}:{t.slug}", "demo tenant: history is frozen at seed time, not live")
            continue
        latest = latest_scan(db, t.id)
        if not latest:
            _fail(f"{name}:{t.slug}", "active sensor but no scan on record")
            ok = False
            continue
        parsed = parse_ts(latest.ts)
        if parsed is None:
            _fail(f"{name}:{t.slug}", f"unparseable scan ts {latest.ts!r}")
            ok = False
            continue
        age = now - parsed
        if age > dt.timedelta(minutes=SCAN_MAX_AGE_MINUTES):
            _fail(f"{name}:{t.slug}", f"latest scan is {age} old")
            ok = False
        else:
            _ok(f"{name}:{t.slug}")
    return ok


def _check_dashboard(db) -> bool:
    """"dashboard payloads": the Overview tab's payload actually has
    something to draw, for the first tenant with an active sensor.

    Built directly from `payloads.overview.build(db, tenant)` -- an
    internal DB-session call, not a network request through
    `routers.read.overview` -- because that route requires a signed-in
    session cookie, and selftest has no OTP flow (or demo code) to obtain
    one non-interactively. Calling the same builder the route calls
    exercises the real payload logic without needing a fake login.
    """
    name = "dashboard"
    tenant = next(
        (t for t in db.query(m.Tenant).all()
         if db.query(m.Sensor).filter_by(tenant_id=t.id, status="active").first()),
        None,
    )
    if tenant is None:
        _skip(name, "no active sensors")
        return True

    from .payloads import overview as overview_payload

    payload = overview_payload.build(db, tenant)
    empty = [
        field for field, value in (
            ("strip", payload.strip),
            ("timeline", payload.timeline),
            ("score_trend", payload.score_trend.points),
        )
        if not value
    ]
    if empty:
        _fail(f"{name}:{tenant.slug}", f"empty {', '.join(empty)}")
        return False
    _ok(f"{name}:{tenant.slug}")
    return True


def _check_dns(db) -> bool:
    """The tenant's newest scan has a `dns_filter` block reporting the
    resolver as running, with a sane (non-negative) 24h block count --
    Phase 3's DNS tab reads this straight off `Scan.raw["dns_filter"]`
    (`payloads/dnsview.py`). Skipped, like `_check_scan_freshness` and
    `_check_dashboard`, when the tenant has no active sensor at all."""
    name = "dns"
    tenant = next(
        (t for t in db.query(m.Tenant).all()
         if db.query(m.Sensor).filter_by(tenant_id=t.id, status="active").first()),
        None,
    )
    if tenant is None:
        _skip(name, "no active sensors")
        return True

    latest = latest_scan(db, tenant.id)
    if not latest:
        _fail(f"{name}:{tenant.slug}", "active sensor but no scan on record")
        return False

    dns_filter = (latest.raw or {}).get("dns_filter") or {}
    if not dns_filter:
        _fail(f"{name}:{tenant.slug}", "scan has no dns_filter block")
        return False
    if not dns_filter.get("running"):
        _fail(f"{name}:{tenant.slug}", "dns_filter.running is false")
        return False
    blocked_24h = dns_filter.get("blocked_24h")
    if blocked_24h is None:
        # An absent counter is not the same as a broken one, and this check
        # used to treat it that way -- so it failed every night.
        #
        # The sensor's query log is per-day (`queries-YYYY-MM-DD.jsonl`) and is
        # created by the first lookup of the day. A sensor older than the fix
        # that seeds the 24h counters returns early when that file is missing,
        # sending no totals at all. Between midnight and the day's first DNS
        # query that is every sensor, every day. Sensors update on their own
        # schedule, so the hub has to keep reading the old shape correctly.
        #
        # The status block is what tells the two apart: it carries the
        # resolver's own count since it started. Zero there means nobody has
        # asked it anything, which is an idle resolver and fine. A non-zero
        # count with no 24h totals is a real inconsistency -- the DNS tab is
        # showing a confident zero for traffic that happened -- and stays a
        # failure.
        served = (dns_filter.get("status") or {}).get("queries") or 0
        if served:
            _fail(f"{name}:{tenant.slug}",
                  f"resolver answered {served} queries but sent no 24h totals")
            return False
        _ok(f"{name}:{tenant.slug}")
        return True
    if not isinstance(blocked_24h, (int, float)) or blocked_24h < 0:
        _fail(f"{name}:{tenant.slug}", f"bad blocked_24h {blocked_24h!r}")
        return False
    _ok(f"{name}:{tenant.slug}")
    return True


def _check_security(db) -> bool:
    """`payloads.security.build` (the Security tab's payload) returns at
    least one scorecard for a tenant with an active sensor -- same
    in-process-builder pattern as `_check_dashboard` above, so this doesn't
    need a signed-in session cookie."""
    name = "security"
    tenant = next(
        (t for t in db.query(m.Tenant).all()
         if db.query(m.Sensor).filter_by(tenant_id=t.id, status="active").first()),
        None,
    )
    if tenant is None:
        _skip(name, "no active sensors")
        return True

    from .payloads import security as security_payload

    payload = security_payload.build(db, tenant)
    if not payload.scorecards:
        _fail(f"{name}:{tenant.slug}", "no scorecards")
        return False
    _ok(f"{name}:{tenant.slug}")
    return True


def _check_retention(db) -> bool:
    """The retention job (Task 55, `retention.prune_tenant`) has run at
    least once: no tenant should have a `Metric` row older than its
    configured `retention_days` + 1 day. Skipped entirely while the hub is
    younger than a day old (proxied by the oldest `Tenant.created`) --
    a fresh install has nothing to have pruned yet, and the nightly job
    (03:30 hub-local) may not have fired even once."""
    name = "retention"
    tenants = db.query(m.Tenant).all()
    if not tenants:
        _skip(name, "no tenants")
        return True

    now = dt.datetime.now(dt.timezone.utc)
    oldest_created = min((t.created for t in tenants if t.created), default=None)
    if oldest_created is None or (now - oldest_created) < dt.timedelta(days=1):
        _skip(name, "hub has been up for less than a day")
        return True

    ok = True
    for t in tenants:
        retention_days = int((t.settings or {}).get("retention_days") or 90)
        cutoff = (now - dt.timedelta(days=retention_days + 1)).isoformat(timespec="seconds")
        stale = db.query(m.Metric).filter(m.Metric.tenant_id == t.id, m.Metric.ts < cutoff).first()
        if stale is not None:
            _fail(f"{name}:{t.slug}", f"metric row older than retention_days+1 ({retention_days + 1}d) still present")
            ok = False
        else:
            _ok(f"{name}:{t.slug}")
    return ok


def _check_reports_writable() -> bool:
    name = "reports_writable"
    reports_dir = settings.data_dir / "reports"
    try:
        reports_dir.mkdir(parents=True, exist_ok=True)
        probe = reports_dir / ".selftest_write_probe"
        probe.write_text("ok")
        probe.unlink()
    except Exception as e:
        _fail(name, str(e))
        return False
    _ok(name)
    return True


def _check_ntfy() -> bool:
    name = "ntfy"
    topic = os.environ.get("GUARDIAN_NTFY_TEST_TOPIC")
    if not topic:
        _skip(name, "GUARDIAN_NTFY_TEST_TOPIC not set")
        return True
    try:
        with httpx.Client(timeout=10) as c:
            r = c.post(f"https://ntfy.sh/{topic}", data=b"Guardian selftest ping")
        if r.status_code >= 300:
            _fail(name, f"push returned status {r.status_code}")
            return False
    except Exception as e:
        _fail(name, str(e))
        return False
    _ok(name)
    return True


def _guard(name: str, fn, *args) -> bool:
    """Run one check, converting any exception it doesn't already catch
    into a FAIL line instead of letting it propagate.

    Every individual `_check_*` above already guards the operations that
    are expected to fail at runtime (a dead HTTP server, a locked-down
    reports dir, ntfy being unreachable). This is the outer safety net for
    everything else -- e.g. a DB query against a table that doesn't exist
    yet -- so `run()` never raises and always finishes all checks. Never
    includes a traceback or a secret: only the exception class and message.
    """
    try:
        return bool(fn(*args))
    except Exception as e:
        _fail(name, f"{type(e).__name__}: {e}")
        return False


def run(base_url: str | None = None, client=None) -> int:
    """Run every selftest check and return 0 if none failed, else 1.

    Pass `client` (a `fastapi.testclient.TestClient` or `httpx.Client` bound
    to the app) to check in-process rather than over the network; otherwise
    an `httpx.Client(base_url=...)` is opened against `base_url` (defaults
    to `settings.base_url`). Never raises: every check is guarded, and an
    unmigrated/empty database short-circuits the checks that depend on it
    (printed as `skip`, not run at all) instead of blowing up on a missing
    table.
    """
    base_url = base_url or settings.base_url
    ok = True

    if client is not None:
        def get(path, **kw):
            return client.get(path, **kw)
    else:
        _http = httpx.Client(base_url=base_url, timeout=10)

        def get(path, **kw):
            return _http.get(path, **kw)

    try:
        ok &= _guard("health", _check_health, get)
        ok &= _guard("login_page", _check_login_page, get)
        ok &= _guard("me", _check_me, get)
    finally:
        if client is None:
            _http.close()

    db_migrated = _guard("db_migrated", _check_db_migrated)
    ok &= db_migrated

    db = SessionLocal()
    try:
        if db_migrated:
            ok &= _guard("tenants", _check_tenants, db)
            ok &= _guard("scan_freshness", _check_scan_freshness, db)
            ok &= _guard("dashboard", _check_dashboard, db)
            ok &= _guard("dns", _check_dns, db)
            ok &= _guard("security", _check_security, db)
            ok &= _guard("retention", _check_retention, db)
        else:
            _skip("tenants", "db not migrated")
            _skip("scan_freshness", "db not migrated")
            _skip("dashboard", "db not migrated")
            _skip("dns", "db not migrated")
            _skip("security", "db not migrated")
            _skip("retention", "db not migrated")
    finally:
        db.close()

    ok &= _guard("reports_writable", _check_reports_writable)
    ok &= _guard("ntfy", _check_ntfy)

    return 0 if ok else 1
