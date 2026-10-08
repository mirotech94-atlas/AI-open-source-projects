"""Background scheduler jobs.

`reports_job()` runs hourly (wired into an APScheduler `BackgroundScheduler`
in `main.py`'s lifespan): for each tenant whose `Tenant.settings
["report_schedule"]` is on, it builds the weekly (Monday) and/or monthly
(the 1st) report at the configured hour, in the tenant's *local* offset --
taken from the newest `Scan.ts` string's own UTC offset, since Vault has
no per-tenant timezone column. It is idempotent: a report of that `kind`
already created in the current local period is skipped, so an hourly tick
that keeps landing inside the due hour (or a retry) never doubles the
library or the ntfy push.

`_now()` is a seam for tests: `monkeypatch.setattr(scheduler, "_now", ...)`
pins "the current instant" without touching the wall clock.
"""

import datetime as dt
import logging

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger
from sqlalchemy.orm import Session as DbSession

from . import billing, mailer, dailyfacts
from . import models as m
from .payloads import updates as updates_payload
from . import networks
from . import retention
from .config import settings
from .db import SessionLocal
from .reports import builder
from .scans import latest_scan, parse_ts

SHARE_HOURS = 24 * 30  # 30-day share link for the pushed report

#: The alert a silent sensor raises. Not a `lan:` key, so it is never
#: network-namespaced (`networks.lan_alert_key` leaves it alone) -- there is
#: one sensor-liveness verdict per tenant, not one per network.
SENSOR_SILENT_KEY = "sensor:silent"

#: Minutes of silence before `sensor_watch_job` calls a sensor dead, when the
#: tenant's `config.thresholds.sensor_silent_min` says nothing. 25 is the
#: predecessor's `nexus-hub-watch` value: five missed 5-minute pushes.
DEFAULT_SILENT_MIN = 25

#: Floor on the configured threshold. Below one push interval the job would
#: alarm on ordinary jitter, so a tenant cannot configure itself into a
#: permanent alert.
MIN_SILENT_MIN = 10

logger = logging.getLogger(__name__)


def _now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def _tenant_offset(db: DbSession, tenant_id: str) -> dt.timedelta:
    """The UTC offset carried by the tenant's most recent scan timestamp
    string, or zero when there is none / it doesn't parse. This is a proxy
    for "the tenant's local time" -- Vault tenants have no timezone
    field of their own, but every scan is stamped by a sensor sitting in
    the tenant's own location."""
    from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
    tenant = db.get(m.Tenant, tenant_id)
    zone = (tenant.settings or {}).get("timezone") if tenant else None
    if zone:
        try:
            return _now().astimezone(ZoneInfo(zone)).utcoffset() or dt.timedelta(0)
        except ZoneInfoNotFoundError:
            pass
    scan = latest_scan(db, tenant_id)
    if not scan or not scan.ts:
        return dt.timedelta(0)
    try:
        parsed = dt.datetime.fromisoformat(scan.ts.replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return dt.timedelta(0)
    return parsed.utcoffset() or dt.timedelta(0)


def _period_key(local_now: dt.datetime, kind: str) -> str:
    if kind == "weekly":
        monday = local_now.date() - dt.timedelta(days=local_now.weekday())
        return monday.isoformat()
    return f"{local_now.year:04d}-{local_now.month:02d}"


def _already_built(db: DbSession, tenant_id: str, kind: str, offset: dt.timedelta, period_key: str) -> bool:
    rows = db.query(m.Report).filter_by(tenant_id=tenant_id, kind=kind).all()
    for r in rows:
        if not r.created:
            continue
        local_created = r.created + offset
        if _period_key(local_created, kind) == period_key:
            return True
    return False


def _push_ntfy(db: DbSession, tenant: m.Tenant, report: m.Report, share_token: str,
               background: bool = False) -> None:
    """Push the freshly-built report's share link at severity INFO.

    `background=True` hands the send to `deliver_background` instead of
    blocking on it. The scheduler runs on its own thread and wants the
    synchronous path (it has the `db` session open and nothing is waiting on
    it); a request handler must never block on a third-party HTTP POST to
    every enabled integration, which is the whole reason
    `deliver_background` exists (Task 40 fix round 1, finding 2).

    Delivery ownership (registry, providers, per-tenant routing) lands in
    Task 40 as `guardian_hub.integrations.registry`; this calls it lazily so
    wiring this scheduler up now doesn't need to wait on that module landing
    first, and so a test environment where it is absent (or monkeypatched
    away, per the brief -- ntfy pushes never actually fire in tests) simply
    no-ops instead of raising.
    """
    url = f"{settings.base_url}/r/{share_token}"
    try:
        from .integrations import registry  # noqa: PLC0415 (intentionally lazy/optional)
    except ImportError:
        return
    try:
        msg = registry.Message(
            title=f"{report.title or report.kind.capitalize() + ' report'} is ready",
            body=url, severity="INFO", url=url, actions=[], kind="report", dedup_key=report.id,
        )
        if background:
            registry.deliver_background(tenant.id, msg)
        else:
            registry.deliver(db, tenant, msg)
    except Exception:
        # Delivery failures must never break the build/schedule loop -- the
        # report is already persisted and in the library regardless.
        pass


def reports_job() -> None:
    db = SessionLocal()
    try:
        for tenant in db.query(m.Tenant).all():
            sched = (tenant.settings or {}).get("report_schedule") or {}
            if not sched.get("weekly") and not sched.get("monthly"):
                continue

            offset = _tenant_offset(db, tenant.id)
            local_now = _now() + offset
            try:
                hour = int(sched.get("hour", 8))
            except (TypeError, ValueError):
                hour = 8
            # The hour gate must compare against the tenant-LOCAL hour
            # (local_now, same offset-shifted clock used just below for the
            # weekday/day-of-month check) -- not the scheduler's own UTC
            # tick clock. Comparing against UTC here while checking
            # weekday/day-of-month against local_now made the two checks
            # disagree for any tenant off UTC: `hour` is configured in the
            # tenant's own local time (the UI shows a plain HH picker with
            # no timezone), so a tenant several hours off UTC would either
            # never fire (no UTC tick ever equals their local target hour
            # in DST-crossing configs) or fire at the wrong wall-clock
            # moment relative to the weekday/day-of-month boundary it just
            # checked (Task 38 carried fix).
            if local_now.hour != hour:
                continue

            preset = sched.get("preset") or "full"
            due_kinds = []
            if sched.get("weekly") and local_now.weekday() == 0:
                due_kinds.append(("weekly", 7))
            if sched.get("monthly") and local_now.day == 1:
                due_kinds.append(("monthly", 30))

            for kind, days in due_kinds:
                period_key = _period_key(local_now, kind)
                if _already_built(db, tenant.id, kind, offset, period_key):
                    continue
                report, _path = builder.render_to_file(
                    db, tenant, days=days, preset=preset, kind=kind,
                    title=f"{kind.capitalize()} report",
                )
                # Stamp the row with the scheduler's own clock: `_already_built` compares
                # `created` against it, and a row stamped by the DB default (real now)
                # can land in a different period than the run that made it.
                report.created = _now()
                db.commit()
                token = builder.mint_share_token(report, SHARE_HOURS)
                db.commit()
                if sched.get("ntfy", True):
                    _push_ntfy(db, tenant, report, token)
    finally:
        db.close()


class _IntrospectableCronTrigger(CronTrigger):
    """`CronTrigger` plus a `fields_map` alias (name -> field) over its own
    `fields` list, so a test can read e.g. `trigger.fields_map["hour"]
    .expressions[0].first` without needing to scan `fields` itself. Pure
    convenience -- scheduling behavior is unchanged from `CronTrigger`."""

    @property
    def fields_map(self) -> dict:
        return {f.name: f for f in self.fields}


# Daily retention job (Task 55): rolls up + prunes metric/event/dns_query/
# presence_sample/audit/report history per the policy in `retention.py`.
# Registered at 03:30 hub-local time (quiet hours for a home-network
# tenant, same rationale as the hourly reports_job avoiding the top of the
# hour). Uses `_now()` above -- the same seam `reports_job` is tested
# through -- rather than `retention._now()` directly, so a test can freeze
# "now" for the whole scheduler module in one place.
RETENTION_CRON = _IntrospectableCronTrigger(hour=3, minute=30)


#: 03:00, deliberately half an hour BEFORE `RETENTION_CRON`. This job reads
#: the `metric` rows retention is about to delete, and if the order were ever
#: reversed the failure would be silent -- a table of honest-looking zeroes
#: frozen forever. Moving either time means checking this one.
DAILY_FACTS_CRON = _IntrospectableCronTrigger(hour=3, minute=0)


def daily_facts_job() -> None:
    """Freeze yesterday's availability for every tenant, before it is pruned.

    Never raises into the scheduler: one tenant's bad day is logged and
    skipped, the same as `retention_job`. A missed close is recoverable --
    `close_due` backfills up to `BACKFILL_DAYS` on the next run -- whereas
    crashing the scheduler would stop the other jobs too.
    """
    db = SessionLocal()
    try:
        now = _now()
        for tenant in db.query(m.Tenant).all():
            try:
                closed = dailyfacts.close_due(db, tenant,
                                              _tenant_offset(db, tenant.id), now=now)
                if closed:
                    logger.info("daily facts: tenant=%s closed=%s", tenant.slug, closed)
            except Exception:
                logger.exception("daily facts: failed for tenant %s", tenant.slug)
    except Exception:
        logger.exception("daily facts: job failed")
    finally:
        db.close()


def retention_job() -> None:
    """Prune every tenant's history per its retention policy. Never raises
    into the scheduler: a failure for one tenant (or the whole run) is
    logged, not propagated -- a missed prune tonight is not worth crashing
    the background scheduler over."""
    db = SessionLocal()
    try:
        now = _now()
        for tenant in db.query(m.Tenant).all():
            try:
                counts = retention.prune_tenant(db, tenant, now=now)
                logger.info("retention: tenant=%s %s", tenant.slug, counts)
            except Exception:
                logger.exception("retention: prune failed for tenant %s", tenant.slug)
    except Exception:
        logger.exception("retention: job failed")
    finally:
        db.close()


# Demo-feed refresh job (Task 64, cadence fixed for F5): rolls the `demo`
# tenant's window forward so guarder.io's public panel
# (`routers/public_feed.py`) never shows a newest row that is days stale,
# AND so the app dashboard's own freshness read never trips "STALE".
#
# `payloads/overview.SENSOR_LIVE_S` (900s / 15 minutes) is what
# `guardian_health` compares `age_s` against to call the sensor "online" vs
# "silent" -- that figure is the *newest Scan row's* timestamp
# (`payloads/overview.build`), which only moves forward when this job (or a
# real sensor) ingests one. The original :15-past-every-hour cadence left up
# to ~59 minutes between refreshes: the demo dashboard read the same
# "STALE / no scan for Nm" red state for 45 of every 60 minutes even though
# nothing was actually broken. Every 10 minutes keeps the worst-case gap at
# 600s, comfortably under the 900s threshold with margin for the job's own
# run time, so the demo tenant reads "online" continuously instead of
# flapping red on an hourly clock.
#
# 2026-09-27/28: once a day. The ten-minute cadence existed only to keep the
# newest scan (and the devices' `last_seen`) fresh, and a full wipe-and-
# reseed was a very expensive way to do that -- ~30 s of the production hub's
# single CPU with the write lock held, which stalled every request that
# landed in the window, a real sensor's upload included (one timed out at
# 21:07 on the 27th while this ran hourly). `demo_trickle._add_heartbeat_scan`
# now keeps the newest scan AND the online devices fresh on its own, so this
# only rolls the story forward: 04:07, the quietest hour, off the :00 mark.
DEMO_REFRESH_CRON = _IntrospectableCronTrigger(hour="4", minute="7")


def demo_refresh_job() -> None:
    """Refresh the `demo` tenant's rows so their newest timestamps stay
    close to "now" -- see `seed.refresh_demo` for why this re-seeds rather
    than shifting timestamps, why it is safe to run repeatedly, and why it
    never touches any tenant but `demo`.

    Guarded by `settings.demo_refresh_enabled` (default `False`): a
    self-hosted install running this same image must never have a
    background job start wiping and rewriting a `demo` tenant's data (if it
    even has one) unless it explicitly opts in -- only the owner's own
    guarder.io deployment sets `GUARDIAN_DEMO_REFRESH_ENABLED=1`. Also a
    no-op, independent of that setting, whenever this hub has no `demo`
    tenant at all (`seed.refresh_demo` returns `None` rather than creating
    one). Never raises into the scheduler, matching `retention_job`: a
    missed refresh is not worth crashing the background scheduler over."""
    if not settings.demo_refresh_enabled:
        return
    db = SessionLocal()
    try:
        from . import seed  # noqa: PLC0415 (lazy: keeps this module's import surface small)
        try:
            out = seed.refresh_demo(db)
            if out is not None:
                logger.info("demo_refresh: tenant=%s scans=%s dns=%s events=%s",
                            out.get("tenant_id"), out.get("scans"), out.get("dns"), out.get("events"))
                # The reset half of this refresh just deleted every row
                # `demo_trickle` may still be tracking for this tenant --
                # clear its bookkeeping so it never retries a delete against
                # ids that are already gone. See `demo_trickle`'s own
                # docstring ("The ten-minute reset") for the full reasoning.
                from . import demo_trickle  # noqa: PLC0415 (lazy, same reasoning as `seed` above)
                demo_trickle.forget_tenant(out["tenant_id"])
        except Exception:
            logger.exception("demo_refresh: failed")
    finally:
        db.close()


#: Kept equal to `demo_trickle.TRICKLE_INTERVAL_S` (asserted in
#: `tests/test_demo_trickle.py` so the two can never silently drift) -- a
#: plain int here rather than importing `demo_trickle` at module scope,
#: which would pull in `seed.py`'s whole import chain just to read one
#: constant (the same reason `demo_refresh_job` imports `seed` lazily).
DEMO_TRICKLE_INTERVAL_S = 15
DEMO_TRICKLE_CRON = IntervalTrigger(seconds=DEMO_TRICKLE_INTERVAL_S)


def demo_trickle_job() -> None:
    """Add one new, real row to the `demo` tenant between `demo_refresh_job`'s
    ten-minute beats, so guarder.io's public feed panel
    (`routers/public_feed.py`) shows a newest row that is seconds old rather
    than drifting toward the next refresh. See `demo_trickle.trickle_demo`
    for the cadence and retention reasoning.

    Same enable gate and same never-raise-into-the-scheduler contract as
    `demo_refresh_job` -- a self-hosted hub never runs this unless it opts
    in, and a failed tick is logged, not fatal to the background scheduler.
    """
    if not settings.demo_refresh_enabled:
        return
    db = SessionLocal()
    try:
        from . import demo_trickle  # noqa: PLC0415 (lazy, same reasoning as `seed` above)
        try:
            demo_trickle.trickle_demo(db)
        except Exception:
            logger.exception("demo_trickle: failed")
    finally:
        db.close()


# Sensor-liveness watchdog (Task 58, gap G1). The predecessor ran this as a
# systemd timer on the hub host (`nexus-hub-watch`, pushing "hub silent" when
# the newest scan was older than 25 minutes); Vault had no equivalent, so a
# dead sensor showed only as a stale pill on a dashboard nobody was looking at.
# Every 5 minutes, on the same tick cadence the sensor pushes at.
SENSOR_WATCH_CRON = _IntrospectableCronTrigger(minute="*/5")


#: Load per core at which a host is busy enough to be the likeliest reason a
#: scan did not finish in time. Four is deliberately conservative: at 4x, work
#: takes roughly four times as long, so the ~2-minute scan this sensor does
#: becomes ~8 and starts crowding a 25-minute staleness threshold that also
#: has to absorb the gap between scans. Below it the load is not the story,
#: and naming it would point the reader at a healthy number.
OVERSUBSCRIBED_AT = 4.0


def _host_load(scan) -> tuple[float, float] | None:
    """`(load1, cores)` the sensor reported about its OWN host on this scan,
    or None when it did not say.

    Every scan carries `self.load1` and `self.cores` (`runner.py` sets them
    beside the sensor's version). Either can be absent or None -- the
    sensor's own comment says so, for a platform with no load average -- and
    an older sensor has no `self` block at all. All of those mean "no
    reading", never zero: a host reporting no load is not an idle host.
    """
    self_block = ((scan.raw or {}).get("self") or {}) if scan is not None else {}
    load1, cores = self_block.get("load1"), self_block.get("cores")
    try:
        load1, cores = float(load1), float(cores)
    except (TypeError, ValueError):
        return None
    return (load1, cores) if cores > 0 else None


def _silent_why(scan, minutes: int, threshold: int) -> tuple[str, dict]:
    """The explanation and the extra evidence for a silent-sensor alert.

    This used to guess -- "either the sensor process stopped, its host is
    off, or it cannot reach the hub" -- and on 2026-09-21 none of the three
    was true. The process was running, the host was on, and every push had
    logged "hub ok". The host was at load 105 across 10 cores, so a scan that
    normally takes 1.6 minutes was taking 16, and the newest scan the hub
    held was always older than the threshold.

    The sensor had been reporting that load on every scan and the hub had
    been storing it; nothing read it. So the last scan that DID arrive is
    asked why the next one did not, and when it was plainly overloaded that
    answer replaces the guesses rather than joining them -- three wrong
    causes beside the right one is how the right one gets missed.
    """
    base = (f"The newest scan is {minutes} min old and the threshold is "
            f"{threshold} min. ")
    reading = _host_load(scan)
    if reading is None:
        return base + ("Either the sensor process stopped, its host is off, or it "
                       "cannot reach the hub."), {}
    load1, cores = reading
    per_core = round(load1 / cores, 1)
    if per_core < OVERSUBSCRIBED_AT:
        return base + ("Either the sensor process stopped, its host is off, or it "
                       "cannot reach the hub."), {}
    return base + (
        f"Its last scan reported load {load1} across {int(cores)} cores -- "
        f"{per_core}x oversubscribed. A scan that cannot finish inside the "
        f"threshold looks identical to a sensor that stopped, so check the "
        f"host's load before the sensor process."
    ), {"load1": load1, "cores": int(cores), "load_per_core": per_core}


def _silent_threshold_min(tenant: m.Tenant, *, default: int = DEFAULT_SILENT_MIN) -> int:
    """`config.thresholds.sensor_silent_min`, floored at `MIN_SILENT_MIN`.

    **The one accessor for that setting**, and the reason it takes a `default`
    is that it has a second reader whose fallback differs. `confidence.
    _fresh_threshold_min` asks the same question for a different purpose --
    "could a repair be verified afterwards" rather than "should somebody be
    woken up" -- so an unconfigured tenant gets `confidence.SENSOR_FRESH_MIN`
    there and `DEFAULT_SILENT_MIN` here (each constant says why). Everything
    else about the answer is identical, and it used to be written out twice:
    two readers of one key, applying the same floor, with nothing failing if
    one of them drifted. That is the hazard CLAUDE.md names for `remedy`'s fix
    tables against `sensor/guardian_sensor/fix.py` and for `payloads/history.
    py` being the only place a trend is computed, and in both cases the
    resolution was one definition rather than two careful ones. Renaming
    `config.thresholds.sensor_silent_min` is now one edit.

    Keyword-only on purpose: this branch already carries a scar from two
    `fix_args` functions whose first two positional parameters are swapped, so
    a second parameter that could be passed positionally is not worth the
    saving.

    `getattr` rather than `tenant.config`: a real `m.Tenant` row always has the
    attribute, and this costs nothing, but the rubric's caller is inside a
    fail-open scoring path (`remedy._apply_confidence`) that must degrade to
    the default rather than raise on anything tenant-shaped it is handed. That
    tolerance was in the accessor this replaced, and dropping it in the course
    of removing a duplicate would have been a silent behaviour change.
    """
    cfg = getattr(tenant, "config", None) or {}
    raw = (cfg.get("thresholds") or {}).get("sensor_silent_min")
    try:
        return max(MIN_SILENT_MIN, int(raw))
    except (TypeError, ValueError):
        return default


def _scan_age_min(scan: "m.Scan", now: dt.datetime) -> float | None:
    """Minutes since `scan.ts`, or None when the stamp does not parse.
    `scans.parse_ts` already handles "Z", an explicit offset and a naive
    stamp (read as UTC), and returns None rather than raising -- a crash here
    would take the whole watchdog down for every tenant."""
    parsed = parse_ts(scan.ts)
    if parsed is None:
        return None
    return (now - parsed).total_seconds() / 60.0


def sensor_watch_job() -> None:
    """Raise `sensor:silent` for every tenant whose newest scan has aged past
    its threshold, and resolve it when data comes back.

    Deliberately narrow:

    - a tenant with **no** scan at all is skipped. "Never enrolled" is the
      onboarding wizard's problem, not an alert;
    - a demo tenant (`settings.demo_code` set) is skipped: its history is a
      one-shot backfill that is stale by design;
    - the alert goes through the ordinary `Alert` row + `registry.deliver`
      path, so it inherits dedup, severity routing, the maintenance-window
      suppression and the CRIT resend ladder rather than re-implementing any
      of them;
    - it is idempotent. A sensor that is still silent on the next tick updates
      `last_seen`/`count` but does not re-deliver -- repeating is the resend
      ladder's job. A row a human acked or snoozed is left in that state.

    Never raises into the scheduler: one bad tenant is logged and skipped.
    """
    db = SessionLocal()
    try:
        now = _now()
        for tenant in db.query(m.Tenant).all():
            try:
                _watch_one(db, tenant, now)
                from .jobdispatch import maintain_waiting
                maintain_waiting(db, tenant, now)
            except Exception:
                logger.exception("sensor-watch: failed for tenant %s", tenant.slug)
        db.commit()
    except Exception:
        logger.exception("sensor-watch: job failed")
        db.rollback()
    finally:
        db.close()



def _watch_sensor(db: DbSession, tenant: m.Tenant, now: dt.datetime, sensor=None) -> str:
    """One tenant's verdict, as a short word for the log/tests:
    `"skip"`, `"ok"`, `"silent"`, `"still-silent"` or `"recovered"`."""
    if (tenant.settings or {}).get("demo_code"):
        return "skip"
    from .scans import latest_scan_for_sensor
    key = f"sensor:silent:{sensor.id}" if sensor else SENSOR_SILENT_KEY
    scan = latest_scan_for_sensor(db, tenant.id, sensor.id) if sensor else latest_scan(db, tenant.id)
    if not scan:
        return "skip"
    age_min = _scan_age_min(scan, now)
    if age_min is None:
        return "skip"

    threshold = _silent_threshold_min(tenant)
    row = db.query(m.Alert).filter_by(tenant_id=tenant.id, key=key).first()
    stamp = now.isoformat(timespec="seconds")

    if age_min <= threshold:
        if row is not None and row.status != "resolved":
            row.status = "resolved"
            row.resolved_by = "auto"
            row.last_seen = stamp
            db.flush()
            _deliver(tenant, "resolve", row)
            return "recovered"
        return "ok"

    minutes = int(age_min)
    msg = (f"No scan data from {sensor.name if sensor else 'the sensor'} for {minutes} min — "
           f"Vault cannot see this network right now.")
    why, load_evidence = _silent_why(scan, minutes, threshold)
    if row is None:
        net = (db.query(m.Network).filter_by(tenant_id=tenant.id, sensor_id=sensor.id).first()
               if sensor else networks.primary(db, tenant.id))
        row = m.Alert(
            tenant_id=tenant.id, network_id=net.id if net else None,
            key=key, sev="CRIT", msg=msg,
            why=why,
            evidence={"last_scan_ts": scan.ts, "age_min": minutes,
                      "threshold_min": threshold, **load_evidence},
            confidence="high", first_seen=stamp, last_seen=stamp, count=1, status="open",
        )
        db.add(row)
        db.flush()
        _deliver(tenant, "alert", row)
        return "silent"

    # Already known. Refresh the facts; deliver again only if a human (or a
    # recovery) had closed it and the sensor has gone quiet a second time.
    reopened = row.status == "resolved"
    row.sev = "CRIT"
    row.msg = msg
    # `why` is refreshed too, not just the evidence: a sensor can go quiet
    # while its host is idle and still be quiet an hour later because the host
    # is now loaded, and the reading should follow the newest scan the hub has.
    row.why = why
    row.evidence = {"last_scan_ts": scan.ts, "age_min": minutes,
                    "threshold_min": threshold, **load_evidence}
    row.last_seen = stamp
    if reopened:
        row.status = "open"
        row.resolved_by = ""
        row.count = (row.count or 1) + 1
        row.first_seen = stamp
        db.flush()
        _deliver(tenant, "alert", row)
        return "silent"
    db.flush()
    return "still-silent"


def _deliver(tenant: m.Tenant, kind: str, row: m.Alert) -> None:
    """Same `Message` shape `ingest.store_scan` uses for an alert, so a silent
    sensor reads like every other Vault alert on the phone. Delivery
    failures are swallowed: the row is stored either way."""
    try:
        from .integrations import registry  # noqa: PLC0415 (lazy, optional in tests)
    except ImportError:
        return
    base_url = settings.base_url.rstrip("/")
    try:
        registry.deliver_background(tenant.id, registry.Message(
            title="Vault: CRIT alert" if kind == "alert" else "Vault: alert resolved",
            body=row.msg, severity=row.sev,
            url=f"{base_url}/inbox?row={row.key}", actions=[],
            kind="resolve" if kind == "resolve" else "alert", dedup_key=row.key,
        ))
    except Exception:
        logger.exception("sensor-watch: delivery failed for tenant %s", tenant.slug)


# A never-started BackgroundScheduler used purely for introspection: it
# lets `jobs()` describe the job specs (id, trigger) that `main.py`
# registers on the real, running scheduler at app startup, without needing
# a live app/lifespan to ask. No thread ever runs on this instance.
_job_registry = BackgroundScheduler()
#: Item 34: once a morning, and only when there is something to say.
PATCH_DIGEST_CRON = _IntrospectableCronTrigger(hour=8, minute=30)


def patch_digest_job() -> None:
    """Tell the client's own channel what is waiting to be installed.

    Alerts, resolutions and reports already reach Slack (or whichever
    integration a tenant configured); pending updates never did, so the one
    finding with a known fix and a known fixer was the one nobody heard
    about until they opened the tab.

    Sent only when security updates or a pending restart exist -- a daily
    "nothing to install" is how a channel gets muted, and a muted channel
    misses the message that mattered. Never raises into the scheduler: one
    tenant's failure is logged and skipped, like every job beside it.
    """
    # Lazy, like every other delivery in this module: the integrations
    # package is optional, and a module-level import here would have raised
    # NameError inside the job's own `except` -- a silent no-op forever.
    from .integrations import registry  # noqa: PLC0415

    db = SessionLocal()
    try:
        for tenant in db.query(m.Tenant).all():
            try:
                posture = updates_payload.patch_posture(db, tenant)
                if posture.security_pending == 0 and posture.needs_reboot == 0:
                    continue
                bits = []
                if posture.security_pending:
                    bits.append(f"{posture.security_pending} security update"
                                f"{'' if posture.security_pending == 1 else 's'} waiting")
                if posture.needs_reboot:
                    bits.append(f"{posture.needs_reboot} machine"
                                f"{'' if posture.needs_reboot == 1 else 's'} waiting on a restart")
                unseen = ""
                if posture.servers_unchecked:
                    unseen = (f" {posture.servers_unchecked} server"
                              f"{'' if posture.servers_unchecked == 1 else 's'} could not be reached, "
                              f"so nothing here speaks for "
                              f"{'it' if posture.servers_unchecked == 1 else 'them'}.")
                registry.deliver_background(tenant.id, registry.Message(
                    title="Vault: updates are waiting",
                    body=f"{' and '.join(bits)}.{unseen}",
                    # A security update with a known fix is a WARN, not an
                    # emergency; CRIT is for something already happening.
                    severity="WARN",
                    url=f"{settings.base_url.rstrip('/')}/updates",
                    actions=[],
                    kind="alert",
                    # One key, so a channel that already carries today's
                    # notice is not told twice.
                    dedup_key=f"patch-digest:{tenant.id}",
                ))
            except Exception:
                logger.exception("patch digest: failed for tenant %s", tenant.slug)
    except Exception:
        logger.exception("patch digest: job failed")
    finally:
        db.close()


_job_registry.add_job(daily_facts_job, DAILY_FACTS_CRON, id="daily-facts", replace_existing=True)
_job_registry.add_job(retention_job, RETENTION_CRON, id="retention", replace_existing=True)
_job_registry.add_job(demo_refresh_job, DEMO_REFRESH_CRON, id="demo_refresh", replace_existing=True)
_job_registry.add_job(demo_trickle_job, DEMO_TRICKLE_CRON, id="demo_trickle", replace_existing=True)
_job_registry.add_job(sensor_watch_job, SENSOR_WATCH_CRON, id="sensor-watch", replace_existing=True)
# Item 10b: a snooze is a promise to come back, and a promise nobody is
# told about is not one. Hourly at :40, off the tick of everything else.
SNOOZE_WAKE_CRON = _IntrospectableCronTrigger(minute=40)


def snooze_wake_job() -> None:
    """Wake alerts whose snooze has run out, and SAY SO.

    Expiry itself already happened lazily, at the top of every inbox read
    (`payloads/inbox._expire_snoozed`) -- which means a customer who chose
    "remind me in 8 hours" got no reminder at all. The alert simply
    reappeared if they happened to look. The lazy path stays (an inbox read
    must never show a stale snooze), and this job is the half that was
    missing: it expires them on a clock, and tells the client's own channel
    once per sweep.

    One message per tenant per sweep, not one per alert: a night's worth of
    snoozes expiring together is a single thing to come back to, and a
    channel that gets fifteen notices at 02:40 is a channel that gets muted.
    """
    from .integrations import registry  # noqa: PLC0415
    from .payloads import inbox as inbox_payload  # noqa: PLC0415

    now_iso = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
    db = SessionLocal()
    try:
        for tenant in db.query(m.Tenant).all():
            try:
                # The SAME expiry the inbox read runs, with the receipt.
                # Duplicating the query here is what broke it the first
                # time: the read path cleared the stamp and this found
                # nothing, so anyone who opened the tab got no reminder.
                woke = inbox_payload.wake_expired(db, tenant.id, announce=True)
                if not woke:
                    continue
                first = woke[0].msg or woke[0].key
                more = (f" and {len(woke) - 1} other"
                        f"{'' if len(woke) == 2 else 's'}") if len(woke) > 1 else ""
                registry.deliver_background(tenant.id, registry.Message(
                    title="Vault: you asked to be reminded",
                    body=f"{first}{more} — back in your inbox.",
                    severity="WARN",
                    url=f"{settings.base_url.rstrip('/')}/inbox",
                    actions=[],
                    kind="alert",
                    # The hour is in the key: a later sweep with a different
                    # set of alerts is a different reminder, but a retry of
                    # this one is not.
                    dedup_key=f"snooze-wake:{tenant.id}:{now_iso[:13]}",
                ))
            except Exception:
                db.rollback()
                logger.exception("snooze wake: failed for tenant %s", tenant.slug)
    except Exception:
        logger.exception("snooze wake: job failed")
    finally:
        db.close()


_job_registry.add_job(patch_digest_job, PATCH_DIGEST_CRON, id="patch-digest", replace_existing=True)
_job_registry.add_job(snooze_wake_job, SNOOZE_WAKE_CRON, id="snooze-wake", replace_existing=True)


def jobs():
    """Registered job specs (id, trigger, ...) -- for introspection/tests."""
    return _job_registry.get_jobs()


# Trial expiry (task 68): moves an elapsed free trial to `paused`. Hourly at
# :05 -- the trial's end is a date, not an instant anybody is watching, so
# within-the-hour precision is plenty, and :05 keeps it off the same tick as
# `reports_job` (top of the hour) and `demo_refresh_job` (:15).
TRIAL_EXPIRY_CRON = _IntrospectableCronTrigger(minute=5)


#: Tenants that never receive billing mail, whatever their subscription row
#: says. The demo tenant is reseeded by `demo_refresh_job` and its admin is a
#: real address, so a seed that ever creates a subscription must not turn into
#: a letter to a person about a trial they do not have.
NO_BILLING_MAIL_SLUGS = frozenset({"demo"})


def _mail_about_trial(db, sub, send, what: str) -> None:
    """Write to one tenant about its trial. Never raises.

    Each address is its own try/except: a refused mailbox must not cost the
    other recipients their letter, and must never unwind the state change
    that prompted it -- the pause has already been committed by the time we
    get here, which is the right way round.
    """
    tenant = db.query(m.Tenant).filter_by(id=sub.tenant_id).first()
    if tenant is None or tenant.slug in NO_BILLING_MAIL_SLUGS:
        return
    for email in billing.trial_recipients(db, sub.tenant_id):
        try:
            send(email, tenant.name or tenant.slug)
        except Exception:
            logger.exception("trial_expiry: could not send %s to a %s recipient", what, tenant.slug)


def trial_expiry_job() -> None:
    """Warn trials that are about to end, then end the ones that have.

    Deletes nothing and charges nothing. A paused tenant keeps every row it
    has collected, keeps the dashboard, keeps its exports; the only thing
    that stops is fresh sensor ingest (see `billing.ingest_allowed` and the
    gate in `routers/sensor.py`). Never raises into the scheduler: a missed
    pass just means a trial ends up to an hour later than it might have,
    which is the right way round to fail.

    The two letters are latched by state, not by a timestamp this job keeps:
    the warning by `billing.mark_trial_warned` (stamped once), the ending by
    the `trialing -> paused` transition itself (`expire_trials` returns the
    rows it moved, and a second pass moves none). Mail is sent from here
    rather than from `billing`, so a test that exercises the state machine
    can never emit a letter.
    """
    db = SessionLocal()
    try:
        for sub in billing.trials_to_warn(db):
            left = billing.trial_days_left(sub) or 0
            ends_on = sub.trial_end.strftime("%d %B %Y") if sub.trial_end else ""
            if not billing.mark_trial_warned(db, sub):
                continue      # somebody else's pass got there first
            _mail_about_trial(
                db, sub,
                lambda email, name, d=left, e=ends_on: mailer.send_trial_ending(email, name, d, e),
                "trial-ending notice")

        moved = billing.expire_trials(db)
        if moved:
            logger.info("trial_expiry: paused %d expired trial(s)", len(moved))
        for sub in moved:
            _mail_about_trial(db, sub, mailer.send_trial_ended, "trial-ended notice")
    except Exception:
        logger.exception("trial_expiry: job failed")
    finally:
        db.close()


_job_registry.add_job(trial_expiry_job, TRIAL_EXPIRY_CRON, id="trial-expiry", replace_existing=True)


def _watch_one(db: DbSession, tenant: m.Tenant, now: dt.datetime) -> str:
    sensors = db.query(m.Sensor).filter_by(tenant_id=tenant.id, status="active").all()
    if len(sensors) <= 1:
        return _watch_sensor(db, tenant, now)
    outcomes = [_watch_sensor(db, tenant, now, sensor) for sensor in sensors]
    return next((state for state in ("silent", "still-silent", "recovered", "ok") if state in outcomes), "skip")
