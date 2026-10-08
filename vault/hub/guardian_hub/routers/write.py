import datetime as dt
import logging
import re

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy.orm import Session as DbSession

from ..db import get_db, SessionLocal
from .. import acttokens, models as m, audit, mailer, networks, onboarding as onboarding_mod, sensors as sensors_lib
from ..chat.tiers import TIER3_FIXES, fix_args
from ..config import settings
from ..deps import ROLE_ORDER, require_role
from ..live import bus
from ..payloads import jobs as jobs_payload
from .. import schemas_dash as sd
from .read import device_out, alert_out, rec_out

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1", tags=["dashboard-write"])

VIEWER = Depends(require_role("viewer"))
OPERATOR = Depends(require_role("operator"))
ADMIN = Depends(require_role("admin"))

ALERT_OPS = {"ack": "acked", "snooze": "snoozed", "resolve": "resolved", "reopen": "open"}
_now = lambda: dt.datetime.now(dt.timezone.utc)

DOMAIN_RE = re.compile(
    r"^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?(\.[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?)+$"
)


def _ip(request: Request) -> str:
    return request.client.host if request.client else ""


class DeviceUpdate(BaseModel):
    label: str | None = None
    trusted: bool | None = None
    always_on: bool | None = None
    parent: str | None = None
    quarantined: bool | None = None


def settle_unverified_alert(db: DbSession, tenant, d: "m.Device", *, who: str, role: str = "", ip: str = "") -> bool:
    """Close the network's "devices not verified" alert the moment the last
    unverified device on it is named or trusted.

    The sensor raised that alert and will drop it on its next sweep, but that
    is minutes away: someone who has just said "trust all" and "yes" is
    looking at an inbox that still says "4 devices not verified", and reads
    that as nothing having happened. Resolving here (`resolved_by` = the
    person) publishes on the live bus, so the inbox updates as they watch;
    if the sensor disagrees on its next sweep it simply raises it again.
    Returns True when an alert was resolved."""
    if not d.network_id:
        return False
    # Ask about the devices the SCAN flagged -- the set the alert is about --
    # not every untrusted, unnamed row the hub has ever seen on the network.
    # The old count included long-gone devices the alert never mentioned: on
    # 2026-09-28 the owner trusted the one iPhone the alert named and it
    # stayed open, held by an iPhone that had been offline for days.
    # `unverified_devices` is the predicate the dashboard's own count uses,
    # so "the alert closes" and "the count reads zero" can no longer
    # disagree; the trusted/label re-check covers what the owner changed
    # since that scan.
    from ..payloads.overview import unverified_devices
    from ..scans import latest_scan
    scan = latest_scan(db, tenant.id)
    rows = db.query(m.Device).filter(m.Device.tenant_id == tenant.id,
                                     m.Device.network_id == d.network_id).all()
    still = [x for x in unverified_devices(rows, (scan.raw if scan else None) or {})
             if not x.trusted and not (x.label or "")]
    if still:
        return False
    # The red "Verify N unknown device(s)" card is the sensor's recommendation
    # about this same condition, and only ever cleared on the next sweep. It
    # goes with the alert; the sensor raises it again if it still has cause,
    # which is how ingest treats every recommendation it stops receiving. A
    # declined one ("no") is kept, for the same reason ingest keeps it: its
    # undo lives on the row.
    (db.query(m.Recommendation)
       .filter(m.Recommendation.tenant_id == tenant.id,
               m.Recommendation.key.like("lan:%unverified"),
               m.Recommendation.decision != "no")
       .delete(synchronize_session=False))
    done = False
    for a in (db.query(m.Alert)
              .filter(m.Alert.tenant_id == tenant.id, m.Alert.network_id == d.network_id,
                      m.Alert.key.like("lan:%unverified"), m.Alert.status == "open").all()):
        if apply_alert_op(db, tenant, a.key, "resolve", who, role, ip=ip) is not None:
            done = True
    db.commit()
    return done


def apply_device_update(db: DbSession, tenant, mac: str, payload: dict, *, who: str,
                        role: str = "", ip: str = "", net=None):
    """The one place a device row is edited (label / trusted / always_on /
    parent / quarantined), for every principal that may edit one.

    `POST /api/v1/devices/{mac}` below (a dashboard session, `who`=username)
    and the public API's `POST /v1/devices/{mac}` (`who`=`token:gapi_...`) both
    go through this function, so a token-driven edit is indistinguishable, in
    the Edit log and the live bus, from one made on the dashboard -- and, more
    importantly, cannot drift from the sensor's replay contract, which reads
    these exact `m.Edit` paths and body keys.

    Returns `(device_row, network_row)`, or `(None, None)` when this tenant has
    no such device. Commits.
    """
    d = networks.device_row(db, tenant.id, mac, net)
    if not d:
        return None, None
    row_net = net or (db.query(m.Network).filter_by(tenant_id=tenant.id, id=d.network_id).first()
                       if d.network_id else None)
    net_slug = row_net.slug if row_net else ""

    if not payload:
        return d, row_net

    for k, v in payload.items():
        setattr(d, k, v)

    # Every device Edit body carries `net` (the network slug) so the sensor's
    # `sync.apply_edit` can replay it against the right network's known-devices
    # map -- a bare mac is ambiguous once the same MAC exists twice.
    if payload.keys() & {"label", "trusted", "always_on"}:
        db.add(m.Edit(tenant_id=tenant.id, who=who, path="/api/device",
                       body={"mac": mac, "net": net_slug, "label": d.label,
                             "trusted": d.trusted, "always_on": d.always_on}))
    if "quarantined" in payload:
        db.add(m.Edit(tenant_id=tenant.id, who=who, path="/api/device/quarantine",
                       body={"mac": mac, "net": net_slug, "on": bool(d.quarantined)}))
    if "parent" in payload:
        db.add(m.Edit(tenant_id=tenant.id, who=who, path="/api/device/merge",
                       body={"mac": mac, "net": net_slug, "parent": d.parent}))

    audit.log(db, tenant.id, who, role, "device.update", target=mac, ip=ip, body=payload)
    db.commit()
    if payload.keys() & {"label", "trusted"}:
        settle_unverified_alert(db, tenant, d, who=who, role=role, ip=ip)
    # "update"/"leave" here are hub-side edit outcomes, distinct from the
    # scan-derived join/leave/port_open/port_closed literals ingest.py
    # publishes for m.Event rows.
    bus.publish(tenant.id, "device", {"mac": mac, "event": "update", "ip": d.ip, "label": d.label,
                                      "net": net_slug})
    return d, row_net


@router.post("/devices/{mac}")
def update_device(mac: str, body: DeviceUpdate, request: Request, ctx=OPERATOR,
                  db: DbSession = Depends(get_db), network: str = ""):
    # `network` is last on purpose: `chat/tools.py` calls this function
    # POSITIONALLY (mac, body, request, ctx, db), so inserting a parameter
    # before `ctx` would silently hand the chat context to `network`.
    user, tenant, role = ctx
    net = networks.resolve(db, tenant.id, network)
    payload = body.model_dump(exclude_unset=True)
    # A MAC on two networks with no `?network=` resolves to the newest
    # `last_seen` row, and the response's `network` field says which.
    d, row_net = apply_device_update(db, tenant, mac, payload, who=user.username, role=role,
                                     ip=_ip(request), net=net)
    if d is None:
        raise HTTPException(404, "device not found")
    if not payload:
        return {"ok": True}
    return device_out(d, row_net)


@router.post("/devices/{mac}/forget")
def forget_device(mac: str, request: Request, ctx=ADMIN,
                  db: DbSession = Depends(get_db), network: str = ""):
    # `network` last -- see `update_device`'s note about the positional
    # call from `chat/tools.py`.
    user, tenant, role = ctx
    net = networks.resolve(db, tenant.id, network)
    d = networks.device_row(db, tenant.id, mac, net)
    if not d:
        raise HTTPException(404, "device not found")
    row_net = net or (db.query(m.Network).filter_by(tenant_id=tenant.id, id=d.network_id).first()
                       if d.network_id else None)
    net_slug = row_net.slug if row_net else ""

    edit_body = {"mac": mac, "net": net_slug}
    db.add(m.Edit(tenant_id=tenant.id, who=user.username, path="/api/device/forget", body=edit_body))
    audit.log(db, tenant.id, user.username, role, "device.forget", target=mac, ip=_ip(request),
              body=edit_body)
    db.delete(d)
    db.commit()
    # Kept as "leave" (not "forgotten") -- same reasoning as update_device
    # above: no closed enum on the web side to satisfy either way.
    bus.publish(tenant.id, "device", {"mac": mac, "event": "leave", "ip": "", "label": ""})
    return {"ok": True}


class AlertOp(BaseModel):
    hours: int | None = None


def apply_alert_op(db: DbSession, tenant: "m.Tenant", key: str, op: str, who: str, role: str = "",
                    hours: int | None = None, ip: str = "") -> "m.Alert | None":
    """The one place an alert transitions status (ack/snooze/resolve/reopen).

    `routers/write.py`'s `POST /api/v1/alerts/{key}/{op}` (a real dashboard
    session, `who`=username) and Task 41's inbound integrations (PagerDuty's
    ack webhook, Home Assistant's `ack_alert` REST service — `who` is
    `"pagerduty"` / `"ha:<tenant>"`, no session) both go through this
    function so an integration-driven ack is indistinguishable, in the Edit
    log and the live bus, from one made on the dashboard. Returns None (no
    Edit/audit/publish) if `op` or `key` doesn't resolve, so a caller can
    404 or silently ignore as its own contract requires.
    """
    if op not in ALERT_OPS:
        return None
    # Tolerates either LAN key shape: a caller that only knows a MAC (an ntfy
    # button, an integration webhook) cannot know which network's namespaced
    # key the alert landed under.
    a = networks.find_alert(db, tenant.id, key)
    if not a:
        return None
    key = a.key

    a.status = ALERT_OPS[op]
    applied_hours = None
    if op == "snooze":
        applied_hours = hours or 8
        a.snoozed_until = (_now() + dt.timedelta(hours=applied_hours)).isoformat(timespec="seconds")
    else:
        a.snoozed_until = None

    # Body shape is the sensor's replayable-edit CONTRACT (sync.apply_edit
    # reads b.get("action") / b.get("hours") at path "/api/alert") -- keep
    # the key exactly {"key", "action", "hours"}.
    #
    # The KEY has to be in the sensor's dialect as well as its shape. A LAN
    # alert is stored here under its namespaced key (`lan:primary:unverified`)
    # because `ingest.store_scan` rewrites it on the way in, but the sensor
    # still keeps the legacy one (`lan:unverified`) in its own `alerts_db`.
    # `sync.apply_edit` looks the key up there and silently does nothing when
    # it misses (`if x:`), so resolving any LAN alert on the dashboard used to
    # be undone by the next scan: the sensor never recorded the resolve, went
    # on reporting the alert as open, and `store_scan` wrote that back over
    # the row about six minutes after the click.
    edit_body = {"key": networks.canonical_lan_key(key), "action": op, "hours": applied_hours}
    db.add(m.Edit(tenant_id=tenant.id, who=who, path="/api/alert", body=edit_body))
    audit.log(db, tenant.id, who, role, f"alert.{op}", target=key, ip=ip, body=edit_body)
    bus.publish(tenant.id, "alert", {"key": a.key, "sev": a.sev, "msg": a.msg, "status": a.status})
    return a


def _still_firing(db: DbSession, tenant_id: str, a: "m.Alert") -> bool:
    """Was this alert reported by the most recent scan?

    Resolve means "this is dealt with". When the condition underneath is still
    true, the sensor re-raises the alert on its very next sweep -- correctly,
    because it IS still true -- and from the outside that looks exactly like
    the button not working. The owner reported it as precisely that.

    Ack does not have the problem: the sensor keeps an `acked` list and honours
    it across re-fires. Resolve is a statement about the world, not about the
    notification, so it cannot be honoured the same way.

    So the route says which case this was, and the UI tells the owner up front
    that it will be back rather than letting it reappear on its own. An alert
    whose `last_seen` matches the newest scan was seen on that sweep.
    """
    if not a.last_seen:
        return False
    newest = (db.query(m.Scan.ts).filter(m.Scan.tenant_id == tenant_id)
                .order_by(m.Scan.ts.desc()).limit(1).scalar())
    return bool(newest) and str(a.last_seen) >= str(newest)


@router.post("/alerts/{key:path}/{op}")
def alert_op(key: str, op: str, request: Request, body: AlertOp = AlertOp(), ctx=OPERATOR, db: DbSession = Depends(get_db)):
    user, tenant, role = ctx
    if op not in ALERT_OPS:
        raise HTTPException(404, "unknown alert op")
    a = apply_alert_op(db, tenant, key, op, user.username, role, body.hours, _ip(request))
    if a is None:
        raise HTTPException(404, "alert not found")
    db.commit()
    out = alert_out(a)
    out["still_firing"] = _still_firing(db, tenant.id, a)
    return out


class BulkAlertOp(BaseModel):
    keys: list[str]
    op: str
    hours: int | None = None


@router.post("/alerts/bulk")
def alert_bulk(body: BulkAlertOp, request: Request, ctx=OPERATOR, db: DbSession = Depends(get_db)):
    user, tenant, role = ctx
    if body.op not in ALERT_OPS:
        raise HTTPException(404, "unknown alert op")
    hours = body.hours if body.op == "snooze" else None
    if body.op == "snooze" and hours is None:
        hours = 8

    updated = 0
    touched: list[m.Alert] = []
    for key in body.keys:
        a = networks.find_alert(db, tenant.id, key)
        if not a:
            continue
        key = a.key
        a.status = ALERT_OPS[body.op]
        if body.op == "snooze":
            a.snoozed_until = (_now() + dt.timedelta(hours=hours)).isoformat(timespec="seconds")
        else:
            a.snoozed_until = None
        # One Edit per key -- the sensor replay contract (sync.apply_edit)
        # reads "/api/alert" edits one at a time, same shape as the single
        # per-key alert_op route, and in the same legacy LAN key dialect
        # (see `apply_alert_op`).
        db.add(m.Edit(tenant_id=tenant.id, who=user.username, path="/api/alert",
                       body={"key": networks.canonical_lan_key(key), "action": body.op, "hours": hours}))
        touched.append(a)
        updated += 1

    audit.log(db, tenant.id, user.username, role, "alert.bulk", ip=_ip(request),
               body={"op": body.op, "keys": body.keys})
    db.commit()
    for a in touched:
        bus.publish(tenant.id, "alert", {"key": a.key, "sev": a.sev, "msg": a.msg, "status": a.status})
    return {"updated": updated}


class RecDecision(BaseModel):
    decision: str


@router.post("/recs/{key}")
def rec_decide(key: str, body: RecDecision, request: Request, ctx=OPERATOR, db: DbSession = Depends(get_db)):
    user, tenant, role = ctx
    if body.decision not in ("yes", "no", "clear"):
        raise HTTPException(422, "decision must be yes|no|clear")
    r = db.query(m.Recommendation).filter_by(tenant_id=tenant.id, key=key).first()
    if not r:
        raise HTTPException(404, "recommendation not found")

    r.decision = "" if body.decision == "clear" else body.decision
    r.decided_by = user.username

    queue_id = None
    if body.decision == "yes":
        # A `TIER3_FIXES` action (reboot, disable_ssh_password, ...) is
        # disruptive, and this route has no confirmation step of its own --
        # `POST /api/v1/fix` demands a `confirm_token` minted by
        # `/fix/confirm` for exactly this reason. Queueing it straight from
        # a recommendation's "Yes" would run it on one click, the tier
        # machinery `/fix` enforces notwithstanding, so it falls into the
        # same `needs-you` branch as "no runnable action at all": the
        # dashboard hands off to chat, and chat's own `apply_fix` mints and
        # waits on the ordinary tier-3 proposal -- a second, real "yes"
        # still has to happen before anything runs.
        action = r.action if isinstance(r.action, dict) else {}
        if action.get("host") and action.get("action") and action["action"] not in TIER3_FIXES:
            queued = queue_and_audit(db, tenant, user.username, role, "/api/fix", "rec.queue", body=r.action, target=key, ip=_ip(request))
            queue_id = queued["id"]
            r.status = "queued"
            # The decision ledger's SECOND call site, and the reason it is a
            # second call and not a shared wrapper around `queue_fix`: this
            # route and `queue_fix` agree about the repair and deliberately
            # DISAGREE about the audit row. `queue_fix` audits `"fix"` against
            # `target=host`; this audits `"rec.queue"` against `target=key`,
            # because what the owner did here was accept a recommendation, and
            # an audit trail that recorded it as a bare fix would lose which
            # advice they were answering. That trail is not the ledger's to
            # rewrite, so the repair is recorded where the repair happens and
            # the audit stays where it was.
            #
            # Without this, `decisions.py`'s claim that `queue_fix` is "the one
            # funnel every repair Vault actually attempts passes through"
            # was false, and the cost was worse than a missing row: `settle`
            # matches on `(tenant, host, action, arg)`, so this repair's later
            # evidence would have settled some OTHER decision's open row for
            # the same identity -- a wrong label, not an absent one.
            #
            # Gated on `existing` for the same reason `queue_fix` is: pressing
            # Yes twice must not put two rows in the ledger for one repair,
            # which `settle` would then settle together and weight double.
            if not queued.get("existing"):
                _record_decision(db, tenant, host=action["host"], action=action["action"],
                                 arg=action.get("arg"))
        else:
            r.status = "needs-you"

    db.add(m.Edit(tenant_id=tenant.id, who=user.username, path="/api/rec", body={"key": key, "decision": body.decision}))
    audit.log(db, tenant.id, user.username, role, "rec.decide", target=key, ip=_ip(request), body={"key": key, "decision": body.decision})
    db.commit()
    if queue_id:
        bus.publish(tenant.id, "queue", {"id": queue_id, "path": "/api/fix", "status": "queued"})
    out = rec_out(r)
    out["queue_id"] = queue_id
    return out


def queue_and_audit(db: DbSession, tenant, who: str, role: str, path: str, action: str, *,
                    body: dict | None = None, target: str = "", ip: str = "") -> dict:
    """Create one sensor-queue row, audit it, commit, publish it on the
    live bus, and return the `{"id","status"}` the dashboard routes return.

    Extracted from `_queue` so every hub-side producer of a queue row --
    the dashboard routes below *and* `chat/tools.py`'s queue-only handlers
    -- goes through the same sequence in the same order (add -> audit ->
    commit -> publish). The publish-after-commit ordering is observable by
    connected dashboards, so it must not be reordered.
    """
    import hashlib, json
    from sqlalchemy.exc import IntegrityError
    body = body or {}
    fingerprint = hashlib.sha256((path + json.dumps(body, sort_keys=True, separators=(",", ":"))).encode()).hexdigest()
    existing = db.query(m.Queue).filter(m.Queue.tenant_id == tenant.id, m.Queue.path == path,
                   m.Queue.status.in_(["queued", "scheduled", "running"])).all()
    duplicate = next((q for q in existing if q.body == body), None)
    if duplicate:
        return {"id": duplicate.id, "status": duplicate.status, "existing": True}
    q = m.Queue(tenant_id=tenant.id, who=who, path=path, body=body, fingerprint=fingerprint,
                expires_at=_now() + dt.timedelta(hours=2))
    if path == "/api/reboot-plan":
        from ..scans import parse_ts
        start, end = parse_ts(body.get("window_start")), parse_ts(body.get("window_end"))
        def zoned(value):
            try:
                return dt.datetime.fromisoformat(str(value).replace("Z", "+00:00")).tzinfo is not None
            except ValueError:
                return False
        if not start or not end or not all(zoned(body.get(k)) for k in ("window_start", "window_end")) or end <= start or end <= _now():
            raise HTTPException(422, "Choose a valid future window with its timezone")
        q.not_before, q.expires_at = start, end
        q.status = "scheduled" if start > _now() else "queued"
    db.add(q)
    audit.log(db, tenant.id, who, role, action, target=target, ip=ip, body=body or None)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        existing = db.query(m.Queue).filter(m.Queue.tenant_id == tenant.id, m.Queue.fingerprint == fingerprint,
                    m.Queue.status.in_(["queued", "scheduled", "running"])).first()
        if existing is None:
            raise
        return {"id": existing.id, "status": existing.status, "existing": True}
    out = {"id": q.id, "status": q.status}
    bus.publish(tenant.id, "queue", {"id": out["id"], "path": path, "status": out["status"]})
    return out


#: See `routers.live.DEMO_SLUG` (F4): the one tenant with no real sensor
#: behind it. Duplicated here rather than imported so this module never has
#: to reach into `routers.live` -- both files independently agree the demo
#: tenant is `Tenant.slug == "demo"`, the same identity `seed.py` builds it
#: under and `routers.auth.verify` checks `Tenant.settings["demo_code"]`
#: against.
_DEMO_SLUG = "demo"


def _run_demo_scan(queue_id: str, tenant_id: str) -> None:
    """F5: on a real tenant, `POST /api/run` just queues a row -- the
    sensor's own `GET /api/sensor/queue` poll (`routers/sensor.py`) picks it
    up, flips it to `running`, and eventually posts `/result`, which is what
    turns it into fresh data. The demo tenant has no sensor to do any of
    that, so before this fix its queue row sat at `status="queued"` forever:
    the toast said "Scan queued", the request returned 200, and nothing
    downstream ever moved -- a prospect who clicked "Scan now" got a
    dashboard that looked broken.

    Runs as a `BackgroundTasks` job (after the request has already
    returned "queued") so it never makes the button feel slow: it mimics
    the same queued -> running -> done lifecycle a real sensor drives, using
    `seed.refresh_demo` -- the same re-seed-at-`now()` `scheduler
    .demo_refresh_job` runs on a timer -- as the "scan". `refresh_demo`'s
    own `reset_tenant` deletes every `Queue` row for the tenant (it is one
    of the ingest-derived tables `seed._RESET_MODELS` wipes clean before
    rebuilding), which would otherwise 404 a dashboard still polling
    `GET /queue/{qid}` for this exact row -- so the row is recreated at the
    same id once the refresh lands, rather than left gone.
    """
    db = SessionLocal()
    try:
        q = db.get(m.Queue, queue_id)
        who = q.who if q else "system"
        queued_at = q.ts if q else dt.datetime.now(dt.timezone.utc)
        if q:
            q.status = "running"
            db.commit()
            bus.publish(tenant_id, "queue", {"id": queue_id, "path": "/api/run", "status": "running"})

        from .. import seed  # noqa: PLC0415 (lazy: avoids a routers<->seed import cycle at module load)
        tenant = db.get(m.Tenant, tenant_id)
        slug = tenant.slug if tenant else _DEMO_SLUG
        # Keep the row that represents this very scan. Without this the
        # refresh deletes the job halfway through running it: the Activity page
        # showed nothing at all for about ten seconds, then a recreated row
        # stamped `queued_at` and `finished_at` at the same instant, reporting
        # that a ten-second scan had taken no time.
        out = seed.refresh_demo(db, slug=slug, keep_queue_ids={queue_id})

        finished = dt.datetime.now(dt.timezone.utc)
        # `reset_tenant`'s delete inside `refresh_demo` is a bulk
        # `synchronize_session=False` delete -- it never touches this
        # session's identity map, so without expiring it `db.get` below
        # would just hand back the in-memory `q` from before the delete
        # (looking very much alive) instead of re-querying and finding it
        # gone.
        db.expire_all()
        q = db.get(m.Queue, queue_id)
        if q is None:
            # Belt and braces: the row should have survived via
            # `keep_queue_ids`, but a recreated one must at least carry the
            # real queued time so the duration is not a fiction.
            q = m.Queue(id=queue_id, tenant_id=tenant_id, who=who, path="/api/run", body={})
            q.ts = queued_at
            db.add(q)
        q.status = "done" if out is not None else "failed"
        q.result = {"out": "demo network re-scanned" if out is not None
                    else "no demo tenant to scan"}
        q.finished_at = finished
        db.commit()
        bus.publish(tenant_id, "queue", {"id": queue_id, "path": "/api/run", "status": q.status})
    except Exception:
        logger.exception("demo scan-now: failed for tenant %s queue %s", tenant_id, queue_id)
    finally:
        db.close()


def _queue(path: str, action: str):
    def handler(request: Request, background_tasks: BackgroundTasks,
                ctx=OPERATOR, db: DbSession = Depends(get_db)):
        user, tenant, role = ctx
        out = queue_and_audit(db, tenant, user.username, role, path, action, ip=_ip(request))
        if path == "/api/run" and tenant.slug == _DEMO_SLUG:
            background_tasks.add_task(_run_demo_scan, out["id"], tenant.id)
        return out
    return handler


router.post("/run")(_queue("/api/run", "run"))
router.post("/speedtest")(_queue("/api/speedtest", "speedtest"))


class FixIn(BaseModel):
    host: str
    action: str
    arg: str | None = None
    # Required for a `chat.tiers.TIER3_FIXES` action (see `fix` below) and
    # ignored for anything else -- a tier-2 repair has never asked first.
    confirm_token: str | None = None


#: The tool name every fix confirmation is bound to. Deliberately the chat
#: tool's own name: this route and `chat`'s `apply_fix` are two front doors on
#: one action, so they mint and burn the same kind of token through the same
#: `acttokens.mint_chat`/`consume_chat` primitives rather than growing a
#: second, parallel confirmation scheme that would have to be audited twice.
FIX_TOOL = "apply_fix"


def queue_fix(db: DbSession, tenant, who: str, role: str, host: str, action: str,
              arg: str | None, ip: str) -> dict:
    """Queue one remediation for the sensor. Every caller of this has already
    passed its own role gate and, for a tier-3 action, burnt a confirmation.

    **This is also where a repair decision is recorded** (`decisions.record`),
    and the sentence above is the reason it belongs here rather than in
    `remedy.resolve_action` where the score is computed. Two of them:

    * `resolve_action` is read-only by contract ("it never queues, mints or
      writes anything") and `payloads/incidents.py` calls `remedy.resolve()`
      once per open alert on every dashboard load. Recording there would
      INSERT and COMMIT one row per open alert per page view -- a ledger of
      decisions nobody made, growing with traffic, written from a GET. This
      codebase already draws that line for notifications, which are pushed
      from write paths and never from the read path; a ledger write is the
      same kind of thing.
    * "already passed its own role gate and burnt a confirmation" is exactly
      the population the dataset is about: one row per repair Vault
      actually attempts, not per repair it offered.

    It is not the ONLY place a decision is recorded, and the docstring used to
    imply it was. `rec_decide` above queues a repair through
    `queue_and_audit` directly, because accepting a recommendation writes a
    different audit row on purpose, so it calls `_record_decision` itself --
    see the comment at that call for why the two routes agree about the repair
    and disagree about the trail. Any future route that queues a `/api/fix`
    job without coming through here owes the ledger the same call; missing it
    is not a gap but a mislabel, since `settle` matches on
    `(tenant, host, action, arg)` and the unrecorded repair's evidence lands
    on somebody else's open row.

    `decisions.py`'s docstring records the limitations that follow from this
    placement -- a `human` verdict and an unconfirmed `approval` leave no row
    -- and which questions the table may therefore be fitted to.
    """
    from ..fixargs import arg_problem
    if (why := arg_problem(action, arg)) is not None:
        raise HTTPException(422, why)
    out = queue_and_audit(db, tenant, who, role, "/api/fix", "fix",
                          body=fix_args(action=action, host=host, arg=arg), target=host, ip=ip)
    # `queue_and_audit` short-circuits on an identical pending job and says so.
    # Nothing new was queued, so there is no new attempt to record: pressing
    # Fix twice must not put two rows in the ledger for one repair, which
    # `settle` would then settle together and weight double.
    if not out.get("existing"):
        _record_decision(db, tenant, host=host, action=action, arg=arg)
    return out


def _record_decision(db: DbSession, tenant, *, host: str, action: str, arg: str | None) -> None:
    """Score the repair just queued and write it to the decision ledger.

    Fails open, loudly. A rubric must never cost a repair: the job is already
    queued and committed by the time this runs, so anything thrown in here has
    to leave the queueing behaviour and `queue_fix`'s return value exactly as
    they were -- the same reasoning `remedy._apply_confidence` writes down for
    the read path, where a raise would blank the dashboard instead of the
    score. Swallowing it silently would just move the failure from visible to
    invisible, so it is logged at warning with the tenant, host and action.

    The cleanup in the handler is deliberately narrower than `db.rollback()`,
    because this function does not own the session it was handed and has no
    business unwinding transaction state it did not create. It discards
    exactly what it added -- its own pending rows, which would otherwise ride
    along unscored on whatever commits next -- and leaves everything else as
    it found it.

    That narrowness is not theoretical any more: **one of the two callers does
    arrive holding pending work.** `rec_decide` sets `r.status = "queued"` on a
    live `m.Rec` after `queue_and_audit` has committed and before this runs,
    and commits it at the end of the handler. (`queue_fix` is the caller that
    holds nothing: `queue_and_audit` commits before this runs, and chat's
    confirmation burn commits on its own in `acttokens`.)

    The one state that forces a rollback is a failure in the COMMIT itself --
    `decisions.record` commits, so its own failure is the case -- because
    SQLAlchemy deactivates the session then (`is_active` goes False) and every
    later use raises `PendingRollbackError` until someone rolls back. The
    rollback is therefore conditional on exactly that state and not on merely
    having caught something.

    What that rollback costs is worth writing down rather than discovering:
    from `rec_decide` it discards the pending `r.status = "queued"`, so the
    route returns 200 with a live `queue_id` beside a recommendation whose
    status is still the empty undecided one. That is deliberately left alone. The repair itself is
    committed and will run; `queue_and_audit`'s dedup means pressing Yes again
    re-queues nothing and the second attempt settles the status; and the
    alternative -- re-committing somebody else's unit of work from inside a
    failure handler in a scoring helper -- is a larger risk than a stale badge
    on a row. A commit that fails here is also not an ordinary error: it means
    the database rejected a write that the queue insert just succeeded at.

    Imports are function-local on purpose. `remedy` imports `chat.registry`,
    which imports the chat tools, which import this module (the same cycle
    `fix_confirm` notes above), and `decisions` imports `confidence`. At
    module level this file stops importing at all -- a failure `pytest` may
    not surface, because a cold `python -c "import guardian_hub.main"` is what
    actually exercises the import order the app boots in.
    """
    try:
        # Inside the `try`, not above it: an import that fails is exactly the
        # kind of failure that must not cost the repair either.
        from .. import confidence as conf_mod, decisions, remedy as remedy_mod
        # `TOOLS` from its owner, not as `remedy_mod.TOOLS`. It used to be
        # reachable through `remedy` only because `remedy` imported it at
        # module scope -- an import that closed the cycle described in
        # `remedy.py`'s import block and has been moved into the one function
        # that needs it. Reading it off `remedy` then raised AttributeError,
        # this `try` swallowed it, and every repair stopped being recorded
        # with nothing but a warning in the log. Import names from the module
        # that defines them.
        from ..chat.registry import TOOLS
        # The alert this repair answers, so `confidence.assess` keeps its
        # `held` and `undone` factors -- without a key those two are blind,
        # and they are the two that carry "Vault's own repair did not stop
        # this" and "a person overruled it". `queue_fix` is handed no key (it
        # takes a host and an action), so the reverse mapping recovers it;
        # `""` is a real answer, because a repair may legitimately be queued
        # with no alert behind it (chat, a checklist row, a Wi-Fi guard).
        found = remedy_mod.alerts_for_fix(db, tenant, action, host, arg)
        alert_key = found[0].key if found else ""
        # The EFFECTIVE tier, computed exactly as `remedy.resolve_action`
        # computes it, not `action in TIER3_FIXES`. Set membership is only
        # half of the tier and the missing half is not rare:
        # `chat.tiers.effective_tier` escalates a tier-2 action to 3 when the
        # host has no successful fix of any kind yet, when the platform
        # `auto_heal` pause is on, and when the action would touch the hub's
        # own listening port. In each of those a person was shown a proposal
        # and said yes -- `registry.dispatch` mints one and `consume_chat`
        # burns it before `chat.tools.h_apply_fix` reaches `queue_fix` -- so
        # membership alone would file the owner's explicit approval as
        # `chosen="auto"` and hand `assess` a tier of 2, which then stores
        # `verdict="auto"` for a decision whose real tier was 3. That is
        # under-reporting approvals in precisely the population this dataset
        # exists to describe.
        #
        # `chat.tiers.fix_args` is keyword-only, and that is the whole point
        # of it: there used to be two of these with `action` and `host`
        # transposed, returning the same dict shape, so a swap raised nothing
        # and scored the wrong repair. Passing by name is now the only thing
        # the signature allows.
        tier = remedy_mod.effective_tier(
            db, tenant, TOOLS["apply_fix"],
            fix_args(action=action, host=host, arg=arg or ""))
        conf = conf_mod.assess(db, tenant, alert_key=alert_key, host=host,
                               action=action, tier=tier)
        # `chosen` is the tier the decision was actually taken at, which is
        # the tier the customer was shown and the tier `registry.dispatch`
        # enforces. One honest edge: the `/fix` route's own gate is on
        # `TIER3_FIXES` alone, so a direct API POST of a tier-2 action that
        # `effective_tier` escalated is the one path where an `"approval"`
        # row was not literally confirmed. It is still recorded as the tier
        # it carried, because the alternative mislabels every escalated
        # approval that DID go through chat -- far the commoner case.
        chosen = "approval" if tier >= 3 else "auto"
        decisions.record(db, tenant, alert_key=alert_key, host=host, action=action,
                         arg=arg, conf=conf, chosen=chosen)
    except Exception:
        for row in [o for o in db.new if isinstance(o, m.DecisionOutcome)]:
            db.expunge(row)
        if not db.is_active:
            db.rollback()
        logger.warning("recording the repair decision failed for tenant=%s host=%s action=%s",
                       tenant.id, host, action, exc_info=True)


@router.post("/fix/confirm", response_model=sd.FixConfirmation)
def fix_confirm(body: FixIn, request: Request, ctx=ADMIN, db: DbSession = Depends(get_db)):
    """Mint the one-shot confirmation a disruptive fix needs before it runs.

    Only for a `TIER3_FIXES` action: the reversible tier-2 repairs never ask,
    so minting a token for one would teach the UI a step that does not exist.
    The token is bound to (tenant, tool, exact args, user) and expires; the
    caller shows `summary` and posts the token back to `/fix`.
    """
    user, tenant, role = ctx
    if body.action not in TIER3_FIXES:
        raise HTTPException(422, f"{body.action} is not a disruptive action; POST /fix directly")
    # Late import: `remedy` imports `chat.registry`, which imports the chat
    # tools, which import this module.
    from ..remedy import fix_steps
    # The owner asked for exactly what will happen, not only the action's
    # name, before he says yes -- `fix_steps` names the host, the file or
    # setting it touches, the change, and what happens if a safety check
    # fails (see that function's docstring).
    summary = fix_steps(body.action, body.host, body.arg or "")
    args = fix_args(action=body.action, host=body.host, arg=body.arg)
    token, expires = acttokens.mint_chat(db, tenant, FIX_TOOL, args, user.id,
                                         {"path": "/api/v1/fix", **args})
    audit.log(db, tenant.id, user.username, role, "fix.confirm", target=body.host,
              ip=_ip(request), body={"action": body.action})
    db.commit()
    return sd.FixConfirmation(token=token, expires=expires.isoformat(), tier=3,
                              host=body.host, action=body.action, summary=summary)


@router.post("/fix")
def fix(body: FixIn, request: Request, ctx=ADMIN, db: DbSession = Depends(get_db)):
    """Queue one remediation on one host.

    A `chat.tiers.TIER3_FIXES` action -- `full_upgrade`, `reboot`,
    `disable_ssh_password`, `block_subnet`, `ban_long`, `limit_ssh` -- is
    disruptive, so it may never run on a bare POST: the caller must first
    `POST /fix/confirm` and send the token back here, where it is burnt
    through the same atomic, single-use primitive chat's tier-3 flow uses.
    A token bound to a different host, action, argument, user or tenant, an
    expired one, and a replayed one are all refused with nothing queued.
    """
    user, tenant, role = ctx
    if body.action in TIER3_FIXES:
        if not body.confirm_token:
            raise HTTPException(
                409, f"{body.action} is disruptive: POST /api/v1/fix/confirm first and send "
                     "confirm_token back with this request")
        if not acttokens.consume_chat(db, body.confirm_token, tenant_id=tenant.id,
                                      tool=FIX_TOOL,
                                      args=fix_args(action=body.action, host=body.host,
                                                    arg=body.arg),
                                      user_id=user.id):
            raise HTTPException(403, "that confirmation is not valid any more. It was already "
                                     "used, it expired, or it was for a different action")
    return queue_fix(db, tenant, user.username, role, body.host, body.action, body.arg,
                     _ip(request))


@router.post("/jobs/{qid}/retry")
def job_retry(qid: str, request: Request, ctx=OPERATOR, db: DbSession = Depends(get_db)):
    """Re-queue a failed job with the exact same arguments -- the Activity
    page's retry control. Never for a job that has not failed (nothing to
    retry) and never for a disruptive `TIER3_FIXES` action (jobs.is_retryable
    is False for those): that class of fix has already spent its one-shot
    confirmation on the row being retried, and a retry mints no new one, so
    it must go back through `/fix/confirm` instead of a bare re-queue.
    """
    user, tenant, role = ctx
    q = db.query(m.Queue).filter_by(tenant_id=tenant.id, id=qid).first()
    if not q:
        raise HTTPException(404, "job not found")
    if q.status != "failed":
        raise HTTPException(409, f"only a failed job can be retried (this one is {q.status})")
    # Retry re-queues the body verbatim, so it must demand the same role the
    # job's own route demands. `/api/fix` is admin-only; this route is
    # operator-gated, so without this check an operator could replay any
    # admin-queued remediation -- cleanup_disk, restart_service, kill_miner --
    # simply by pressing Retry on a failed one. `is_retryable` only ever asked
    # whether the action was disruptive, never who was allowed to run it.
    need = jobs_payload.role_required(q.path)
    if ROLE_ORDER[role] < ROLE_ORDER[need]:
        raise HTTPException(403, f"{need} role required to retry this job")
    if not jobs_payload.is_retryable(q.path, q.body or {}):
        raise HTTPException(409, "this job is not safe to retry on its own -- it needs a fresh "
                                 "confirmation (POST /api/v1/fix/confirm), not a plain retry")
    if q.path == "/api/fix" and "timed out" in str((q.result or {}).get("out") or ""):
        from ..scans import latest_scan_per_sensor, parse_ts
        from ..verification import outcome
        scan = latest_scan_per_sensor(db, tenant.id, [q.sensor_id]).get(q.sensor_id) if q.sensor_id else None
        stamp = parse_ts(scan.ts) if scan else None
        host = next((h for h in (scan.raw.get("ssh") or []) if h.get("name") == (q.body or {}).get("host") and h.get("reachable")), None) if scan else None
        if not stamp or not q.finished_at or stamp <= q.finished_at or not host:
            raise HTTPException(409, "The previous repair may have run. Wait for a fresh target check before retrying.")
        verdict = outcome((q.body or {}).get("action"), host, (q.body or {}).get("arg", ""))
        if verdict is True:
            raise HTTPException(409, "A fresh check already shows recovery; another repair is unnecessary.")
        if verdict is None:
            raise HTTPException(409, "The previous repair cannot be verified automatically. Review its evidence and request a new action explicitly.")
    reply = queue_and_audit(db, tenant, user.username, role, q.path, "job.retry",
                           body=q.body or {}, target=(q.body or {}).get("host") or "",
                           ip=_ip(request))
    attempt = db.get(m.Queue, reply["id"])
    if not reply.get("existing"):
        attempt.result = {**(attempt.result or {}), "retry_of": q.id}
        q.result = {**(q.result or {}), "retry_id": attempt.id}
        db.commit()
    return reply


# `/config` and `/profile` moved to `routers/settings.py` (Task 39) --
# that module keeps both paths as aliases so the sensor's edit-replay
# contract (`sync.apply_edit`, which reads these exact paths) is untouched.
# See `settings.update_config` / `settings.update_profile`.

class RebootPlanIn(BaseModel):
    host: str
    window_start: str
    window_end: str


@router.post("/reboot-plan")
def reboot_plan(body: RebootPlanIn, request: Request, ctx=ADMIN, db: DbSession = Depends(get_db)):
    user, tenant, role = ctx
    q_body = {"host": body.host, "window_start": body.window_start, "window_end": body.window_end}
    return queue_and_audit(db, tenant, user.username, role, "/api/reboot-plan", "reboot.plan",
                           body=q_body, target=body.host, ip=_ip(request))


ATTACKER_METHODS = ("auto", "block_ip", "block_subnet", "ban_long", "limit_ssh", "off")


class AttackerMethodIn(BaseModel):
    method: str


@router.post("/attacker")
def attacker_method(body: AttackerMethodIn, request: Request, ctx=ADMIN, db: DbSession = Depends(get_db)):
    user, tenant, role = ctx
    if body.method not in ATTACKER_METHODS:
        raise HTTPException(422, "method must be one of " + ", ".join(ATTACKER_METHODS))

    cfg = dict(tenant.config or {})
    auto_fix = dict(cfg.get("auto_fix") or {})
    auto_fix["attacker_method"] = body.method
    cfg["auto_fix"] = auto_fix
    tenant.config = cfg

    # sync.apply_edit reads b.get("method") at path "/api/attacker" -- keep
    # the Edit body exactly {"method": ...}, same shape the POST body has.
    edit_body = {"method": body.method}
    db.add(m.Edit(tenant_id=tenant.id, who=user.username, path="/api/attacker", body=edit_body))
    audit.log(db, tenant.id, user.username, role, "attacker.method", ip=_ip(request), body=edit_body)
    db.commit()
    return {"attacker_method": body.method}


def _normalise_domain(d: str) -> str:
    d = d.strip().lower()
    if d.endswith("."):
        d = d[:-1]
    return d


class DnsRuleIn(BaseModel):
    kind: str
    domain: str
    op: str


@router.post("/dns/rules")
def dns_rules(body: DnsRuleIn, request: Request, ctx=ADMIN, db: DbSession = Depends(get_db)):
    user, tenant, role = ctx
    if body.kind not in ("allow", "block"):
        raise HTTPException(422, "kind must be allow|block")
    if body.op not in ("add", "remove"):
        raise HTTPException(422, "op must be add|remove")

    domain = _normalise_domain(body.domain)
    if not DOMAIN_RE.match(domain):
        raise HTTPException(422, "invalid domain")

    existing = db.query(m.DnsRule).filter_by(tenant_id=tenant.id, kind=body.kind, domain=domain).first()
    if body.op == "add":
        if not existing:
            db.add(m.DnsRule(tenant_id=tenant.id, kind=body.kind, domain=domain))
    elif existing:
        db.delete(existing)
    db.flush()

    allow = sorted(r.domain for r in db.query(m.DnsRule).filter_by(tenant_id=tenant.id, kind="allow").all())
    block = sorted(r.domain for r in db.query(m.DnsRule).filter_by(tenant_id=tenant.id, kind="block").all())
    cfg = dict(tenant.config or {})
    cfg["dns"] = {**(cfg.get("dns") or {}), "allow": allow, "block": block}
    tenant.config = cfg

    # sync.apply_edit reads b.get("allow") / b.get("block") at path
    # "/api/dns/rules" -- keep the Edit body exactly {"allow": [...], "block": [...]}.
    edit_body = {"allow": allow, "block": block}
    db.add(m.Edit(tenant_id=tenant.id, who=user.username, path="/api/dns/rules", body=edit_body))
    audit.log(db, tenant.id, user.username, role, "dns.rule", target=domain, ip=_ip(request),
              body={"kind": body.kind, "domain": domain, "op": body.op})
    db.commit()
    return {"allow": allow, "block": block}


@router.post("/reset-lan-baseline")
def reset_lan_baseline(request: Request, ctx=ADMIN, db: DbSession = Depends(get_db)):
    user, tenant, role = ctx
    db.add(m.Edit(tenant_id=tenant.id, who=user.username, path="/api/reset-lan-baseline", body={}))
    audit.log(db, tenant.id, user.username, role, "lan.reset_baseline", ip=_ip(request))
    db.commit()
    return {"ok": True}


# --------------------------------------------------------------------------
# First-run wizard state (task 71)
# --------------------------------------------------------------------------

class OnboardingIn(BaseModel):
    """Every field optional, and each one a separate fact: the wizard saves
    `step` as it advances, "Skip for now" saves `skipped=True`, "Done" saves
    `completed=True`, and Settings -> Sensors' "Re-run setup" saves both back
    to False. Omitted means "leave as it is", which is why these are
    `None`-defaulted rather than `False`-defaulted."""
    step: str | None = None
    completed: bool | None = None
    skipped: bool | None = None


@router.post("/onboarding", response_model=sd.OnboardingState)
def save_onboarding(body: OnboardingIn, ctx=VIEWER, db: DbSession = Depends(get_db)):
    """Record where THIS user got to in the first-run wizard on THIS tenant.

    Viewer-level on purpose, and it is the only write in this router that is:
    every user walks the wizard, including a viewer who can change nothing
    else, and the row is about the caller alone -- there is no path here to
    write anybody else's state. Not audited: a step save fires on every
    "Next" click and would bury the audit log in noise, and nothing about a
    person's own wizard progress is a security event.
    """
    user, tenant, role = ctx
    out = onboarding_mod.save(db, user, tenant, step=body.step, completed=body.completed,
                              skipped=body.skipped)
    db.commit()
    return out


@router.post("/jobs/{qid}/cancel")
def cancel_job(qid: str, request: Request, ctx=OPERATOR, db: DbSession = Depends(get_db)):
    user, tenant, role = ctx
    q = db.query(m.Queue).filter_by(id=qid, tenant_id=tenant.id).first()
    if q is None:
        raise HTTPException(404, "Job not found")
    required = jobs_payload.role_required(q.path)
    if ROLE_ORDER[role] < ROLE_ORDER[required]:
        raise HTTPException(403, f"{required} role required")
    count = db.query(m.Queue).filter(m.Queue.id == qid, m.Queue.status.in_(["queued", "scheduled"])).update(
        {"status": "cancelled", "finished_at": _now()}, synchronize_session=False)
    if not count:
        raise HTTPException(409, "Only waiting or scheduled work can be cancelled")
    audit.log(db, tenant.id, user.username, role, "job.cancel", target=qid, ip=_ip(request))
    db.commit()
    bus.publish(tenant.id, "queue", {"id": qid, "status": "cancelled"})
    return {"id": qid, "status": "cancelled"}

@router.get("/jobs/{qid}/comparison")
def scan_comparison(qid: str, ctx=VIEWER, db: DbSession = Depends(get_db)):
    _, tenant, _ = ctx
    current = db.query(m.Queue).filter_by(id=qid, tenant_id=tenant.id).first()
    if not current or current.path not in ("/api/vuln-scan", "/api/deep-dive"):
        raise HTTPException(404, "Scan not found")
    candidates = db.query(m.Queue).filter(m.Queue.tenant_id == tenant.id, m.Queue.path == current.path,
                    m.Queue.ts < current.ts, m.Queue.status == "done").order_by(m.Queue.ts.desc()).all()
    previous = next((q for q in candidates if q.body == current.body), None)
    def findings(q):
        result = q.result or {}
        detail = result.get("detail") if isinstance(result.get("detail"), dict) else result
        rows = detail.get("findings")
        if not isinstance(rows, list):
            return None
        import json
        return {json.dumps([f.get("id") or f.get("key") or f.get("title") or f.get("name"), f.get("host") or f.get("target"), f.get("port"), f.get("protocol")], sort_keys=True): f for f in rows
                if isinstance(f, dict) and (f.get("id") or f.get("key") or f.get("title") or f.get("name"))}
    before, after = findings(previous) if previous else None, findings(current)
    if previous is None or before is None or after is None or current.status != "done":
        return {"comparable": False, "reason": "Two completed scans with the same target and profile are required", "new": [], "unchanged": [], "resolved": [], "unverified": []}
    # An absent finding alone is insufficient to promise a repair. Keep it
    # unverified until the scan protocol explicitly certifies complete coverage.
    detail = (current.result or {}).get("detail") or current.result or {}
    complete = detail.get("coverage_complete") is True
    missing = [before[k] for k in before.keys() - after.keys()]
    return {"comparable": True, "previous_id": previous.id, "reason": "Same target and scan profile",
            "new": [after[k] for k in after.keys() - before.keys()], "unchanged": [after[k] for k in after.keys() & before.keys()],
            "resolved": missing if complete else [], "unverified": [] if complete else missing}

class DnsExceptionIn(BaseModel):
    domain: str
    hours: int = 24
    remove: bool = False

@router.get("/dns/exceptions")
def dns_exceptions(ctx=VIEWER, db: DbSession = Depends(get_db)):
    _, tenant, _ = ctx
    from ..scans import parse_ts
    rows = []
    for item in ((tenant.config or {}).get("dns") or {}).get("exceptions") or []:
        expiry = parse_ts(item.get("expires"))
        if not expiry or expiry <= _now():
            continue
        seen = db.query(m.DnsQuery).filter_by(tenant_id=tenant.id, name=item["domain"]).order_by(m.DnsQuery.ts.desc()).first()
        observed = parse_ts(seen.ts) if seen else None
        created = parse_ts(item.get("created"))
        verified = bool(seen and observed and created and observed > created and not seen.blocked)
        rows.append({**item, "verification": "Allowed query observed" if verified else "Waiting for an allowed query"})
    return {"exceptions": rows}

@router.post("/dns/exceptions")
def dns_exception(body: DnsExceptionIn, request: Request, ctx=ADMIN, db: DbSession = Depends(get_db)):
    from ..scans import parse_ts
    user, tenant, role = ctx
    domain = _normalise_domain(body.domain)
    if not DOMAIN_RE.fullmatch(domain) or not 1 <= body.hours <= 168:
        raise HTTPException(422, "Use a domain and an expiry between 1 and 168 hours")
    cfg = dict(tenant.config or {})
    dns = dict(cfg.get("dns") or {})
    entries = [x for x in dns.get("exceptions") or [] if x.get("domain") != domain and (parse_ts(x.get("expires")) or _now()) > _now()]
    if not body.remove:
        entries.append({"domain":domain,"created":_now().isoformat(),"expires":(_now()+dt.timedelta(hours=body.hours)).isoformat()})
    dns["exceptions"] = entries
    cfg["dns"] = dns
    from .settings import _write_config
    _write_config(db,tenant,user,role,request,cfg,"dns.exception",body.model_dump())
    return {"ok":True}

@router.get("/jobs/{qid}/story")
def job_story(qid: str, ctx=VIEWER, db: DbSession = Depends(get_db)):
    _, tenant, _ = ctx
    job = db.query(m.Queue).filter_by(id=qid,tenant_id=tenant.id).first()
    if not job:
        raise HTTPException(404,"Job not found")
    candidates = db.query(m.Queue).filter_by(tenant_id=tenant.id,path=job.path).order_by(m.Queue.ts.desc()).limit(200).all()
    attempts=[]
    for q in candidates:
        if q.body != job.body:
            continue
        sensor=db.query(m.Sensor).filter_by(id=q.sensor_id,tenant_id=tenant.id).first() if q.sensor_id else None
        result=q.result or {}
        attempts.append({"id":q.id,"status":q.status,"created":q.ts.isoformat(),"sensor":sensor.name if sensor else "Awaiting assignment",
                         "started":q.started_at.isoformat() if q.started_at else None,"heartbeat":q.heartbeat_at.isoformat() if q.heartbeat_at else None,
                         "retry_of":result.get("retry_of"),"verification":result.get("verification_note") or "",
                         "output":str(result.get("out") or "")[:4000]})
    from .. import remedy
    body=job.body or {}
    alerts=[]
    if body.get("action") and body.get("host"):
        for a in db.query(m.Alert).filter_by(tenant_id=tenant.id).all():
            fix=remedy.fix_for_key(networks.canonical_lan_key(a.key))
            if fix and fix[:2] == (body["action"],body["host"]) and (not fix[2] or fix[2]==body.get("arg")):
                alerts.append({"key":a.key,"message":a.msg,"status":a.status})
    proposals=[]
    if job.path == "/api/fix":
        for token in db.query(m.ActToken).filter_by(tenant_id=tenant.id,kind="chat").order_by(m.ActToken.created.desc()).limit(200).all():
            blob = token.actions[0] if token.actions and isinstance(token.actions[0],dict) else {}
            if blob.get("args_canon") != acttokens.canon_args(body):
                continue
            author=db.get(m.User,blob.get("user_id")) if blob.get("user_id") else None
            proposals.append({"created":token.created.isoformat(),"expires":token.expires.isoformat(),
                "status":"used" if token.used else "expired" if token.expires <= _now() else "awaiting confirmation",
                "requested_by":author.username if author else "Unknown requester", "target":body.get("host") or ""})
    return {"attempts":attempts,"alerts":alerts,"proposals":proposals}

class AssetImportanceIn(BaseModel):
    importance: str

@router.get("/devices/{mac}/importance")
def device_importance(mac: str, network: str = "", ctx=VIEWER, db: DbSession = Depends(get_db)):
    _, tenant, _ = ctx
    net = networks.resolve(db,tenant.id,network) if network else None
    device = networks.device_row(db,tenant.id,mac,net)
    if not device:
        raise HTTPException(404,"Device not found")
    key=f"{device.network_id}:{device.mac}"
    return {"importance":((tenant.settings or {}).get("asset_importance") or {}).get(key,"normal")}

@router.post("/devices/{mac}/importance")
def set_device_importance(mac: str, body: AssetImportanceIn, request: Request, network: str = "", ctx=OPERATOR, db: DbSession = Depends(get_db)):
    user,tenant,role=ctx
    if body.importance not in ("normal","important","critical"):
        raise HTTPException(422,"Choose normal, important or critical")
    net=networks.resolve(db,tenant.id,network) if network else None
    device=networks.device_row(db,tenant.id,mac,net)
    if not device:
        raise HTTPException(404,"Device not found")
    values=dict((tenant.settings or {}).get("asset_importance") or {})
    values[f"{device.network_id}:{device.mac}"]=body.importance
    tenant.settings={**(tenant.settings or {}),"asset_importance":values}
    audit.log(db,tenant.id,user.username,role,"device.importance",target=mac,ip=_ip(request),body={"network":device.network_id,**body.model_dump()})
    db.commit()
    return body.model_dump()


class SensorInviteIn(BaseModel):
    """Deliberately empty of anything that could redirect the letter.

    A recipient field would be the natural thing to put here and is exactly
    what must not exist: the enrol code is a bearer credential, and a request
    body is the least trustworthy place to learn who should hold one. The
    address comes from the signed-in account. Clients may still POST `{}`.
    """


def _install_origin(request: Request) -> str:
    """The origin to put in the e-mailed install command.

    `routers/public` renders the installer for whatever origin the request
    arrived on, never `settings.base_url`, so a customer who typed a different
    hostname is not handed a script that downloads from somewhere they never
    asked for. That rule is right there and wrong here in one case: a command
    that goes into an E-MAIL is run on a different machine, at a later time, by
    a different person. A loopback origin -- which is what a developer or an
    SSH tunnel produces -- would send somebody a command naming a host that
    resolves to their own laptop.

    So the request's origin is used, except when it is loopback, where the
    deploy-time base URL is the only answer that can possibly work.
    """
    origin = f"{request.url.scheme}://{request.url.netloc}".rstrip("/")
    host = request.url.hostname or ""
    if host in ("127.0.0.1", "localhost", "::1", "testserver", "testclient"):
        return settings.base_url.rstrip("/")
    return origin


@router.post("/onboarding/networks/{network_id}/invite", response_model=sd.OnboardingState)
def invite_sensor(network_id: str, body: SensorInviteIn, request: Request,
                  ctx=ADMIN, db: DbSession = Depends(get_db)):
    """Mint an enrol code for this network and e-mail the setup instructions.

    Admin-role, unlike `POST /onboarding` beside it, which is viewer-level
    because every user walks the wizard. Minting a bearer credential is not
    walking a wizard.

    The letter goes to the signed-in account's own address and `body` has no
    field that could change that -- see `SensorInviteIn`.

    A relay that refuses the letter fails the request. The wizard renders "we
    have e-mailed whoever looks after your computers" from a 200, and the one
    failure this flow cannot survive is saying that when nothing left the
    building: the customer then waits for instructions that do not exist, and
    Vault looks broken in the first ten minutes of the relationship. The
    code is still minted -- it is bound to the network and perfectly valid --
    so a retry re-sends rather than stranding it.
    """
    user, tenant, role = ctx
    net = db.query(m.Network).filter_by(id=network_id, tenant_id=tenant.id).first()
    if net is None:
        # 404 rather than 403: whether a network id exists on some OTHER tenant
        # is not this caller's business, and the two answers must not differ.
        raise HTTPException(404, "No such network")
    if not user.email:
        raise HTTPException(422, "This account has no e-mail address to send to")

    sensor = sensors_lib.new_enrol_code_for(db, tenant, net.id, emailed=True)
    origin = _install_origin(request)
    sent = mailer.send_sensor_install(
        user.email,
        tenant_name=tenant.name or tenant.slug,
        network_name=net.name or net.slug,
        code=sensor.enrol_code,
        expires_on=sensor.enrol_expires.strftime("%d %B %Y"),
        install_cmd=f"curl -fsSL {origin}/install | sh -s -- --code {sensor.enrol_code}",
    )
    # Recorded WITHOUT the code. `Audit.body` is read back by the owner console
    # and reaches built reports; a credential in a log is a credential.
    audit.log(db, tenant.id, user.username, role, "sensor.invite",
              target=net.slug, ip=_ip(request), body={"network": net.id, "sent": bool(sent)})
    db.commit()
    if not sent:
        raise HTTPException(502, "The setup e-mail could not be sent. Try again in a moment.")
    return onboarding_mod.state(db, user, tenant)
