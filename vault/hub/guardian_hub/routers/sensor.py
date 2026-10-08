import datetime as dt

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy.orm import Session as DbSession

from ..db import get_db
from .. import models as m, security, ratelimit, billing, recorders as recorders_mod, router_store
from ..ingest import store_scan
from ..live import bus
from .. import adminnotify

router = APIRouter(prefix="/api/v1/sensor", tags=["sensor"])
_now = lambda: dt.datetime.now(dt.timezone.utc)

# A sensor enrolment code is short-lived and single-use, but it's still
# brute-forceable at the HTTP layer -- cap attempts per client IP.
ENROL_PER_IP = (10, 600)   # 10 requests / 10 min for one client


class EnrolIn(BaseModel):
    code: str
    name: str = "sensor"
    platform: str = ""
    version: str = ""


def sensor_auth(request: Request, db: DbSession = Depends(get_db)):
    h = request.headers.get("authorization") or ""
    if not h.lower().startswith("bearer "):
        raise HTTPException(401, "sensor token required")
    tok = h[7:].strip()
    s = db.query(m.Sensor).filter_by(token_hash=security.hash_token(tok), status="active").first()
    if not s:
        raise HTTPException(401, "bad sensor token")
    tenant = db.get(m.Tenant, s.tenant_id)
    return s, tenant


@router.post("/enrol")
def enrol(body: EnrolIn, request: Request, db: DbSession = Depends(get_db)):
    ip = request.client.host if request.client else ""
    if not ratelimit.allow(f"sensor_enrol:ip:{ip}", *ENROL_PER_IP):
        raise HTTPException(429, "too many requests, try again later")
    s = db.query(m.Sensor).filter_by(enrol_code=body.code.strip().upper(), status="pending").first()
    if not s or (s.enrol_expires and s.enrol_expires < _now()):
        raise HTTPException(404, "code invalid or expired")
    tok = security.new_token()
    s.token_hash = security.hash_token(tok)
    s.enrol_code = None
    s.status = "active"
    s.name = body.name
    s.platform = body.platform
    s.version = body.version
    s.last_seen = _now()
    t = db.get(m.Tenant, s.tenant_id)
    if s.network_id:
        # Round 2 of Task 51: the enrol code was minted for a specific
        # network -- bind this sensor onto it now that it is active. A
        # network deleted between mint and redemption just leaves nothing to
        # bind onto; the sensor still enrols.
        net = db.query(m.Network).filter_by(tenant_id=s.tenant_id, id=s.network_id).first()
        if net:
            net.sensor_id = s.id
            if net.status != "active":
                net.status = "active"
    db.commit()
    adminnotify.notify("sensor", f"Sensor enrolled: {s.name or s.id[:8]}", f"first push expected within a few minutes", tenant=t.slug)
    return {"token": tok, "tenant": {"slug": t.slug, "name": t.name}, "sensor_id": s.id}


@router.post("/ingest")
def ingest(payload: dict, auth=Depends(sensor_auth), db: DbSession = Depends(get_db)):
    s, t = auth
    # The ONE place a subscription can stop the product doing something.
    # `billing.ingest_allowed` is False only for an account whose free trial
    # ran out (`paused`) or whose paid plan was cancelled -- never for a
    # failed payment, which stays fully monitored (see `billing`'s docstring:
    # a watchdog that stops watching because a card expired is worse than
    # useless). Everything already collected stays readable; only new data is
    # refused, and 402 is a status the sensor can log and back off on rather
    # than retry as if the hub were broken.
    if not billing.ingest_allowed(billing.get(db, t.id)):
        raise HTTPException(402, "subscription inactive -- new sensor data is not being "
                                 "stored. Existing data is unaffected; choose a plan to resume.")
    if "version" in payload:
        s.version = str(payload["version"])
    return {"ok": True, "stored": store_scan(db, t, s, payload)}


@router.get("/config")
def config(auth=Depends(sensor_auth)):
    s, t = auth
    return t.config or {}


@router.get("/recorders")
def recorders(auth=Depends(sensor_auth), db: DbSession = Depends(get_db)):
    """The camera recorders this site has registered, WITH their credentials.

    The one route that puts a recorder password on the wire, and it puts it
    only here: the recorder is on a private address the hub has no route to,
    it speaks no token or capability scheme, so the sensor that can reach it
    has to hold the real login. The channel is the sensor's own authenticated,
    TLS-terminated one -- the same one that already carries every scan.

    Deliberately NOT delivered as an `/api/config` edit: the edit log is
    plaintext, retained, and replayed into the sensor's world-readable
    `config.json`. See `guardian_sensor.sync.recorders_file`.
    """
    s, t = auth
    return {"recorders": recorders_mod.sensor_payload(db, t.id)}


@router.get("/router")
def router_credential(auth=Depends(sensor_auth), db: DbSession = Depends(get_db)):
    """The site gateway router this tenant registered, WITH its admin password,
    or `{"router": null}` when none is configured/enabled. Same reasoning as
    `/recorders`: the gateway is on a private LAN address the hub cannot reach,
    so the sensor that can reach it holds the real login, delivered only here
    over the sensor's own authenticated channel -- never in an `/api/config`
    edit (that log is plaintext and replayed into a world-readable file)."""
    s, t = auth
    return {"router": router_store.sensor_payload(db, t.id)}


@router.get("/edits")
def edits(after: int = 0, auth=Depends(sensor_auth), db: DbSession = Depends(get_db)):
    s, t = auth
    rows = db.query(m.Edit).filter(m.Edit.tenant_id == t.id, m.Edit.id > after).order_by(m.Edit.id).limit(500).all()
    return {
        "edits": [{"id": e.id, "ts": e.ts.isoformat(), "who": e.who, "path": e.path, "body": e.body} for e in rows],
        "last_id": rows[-1].id if rows else after,
    }


# A "running" row the sensor never posted a /result for -- it crashed,
# was OOM-killed, or the host rebooted mid-run -- would otherwise sit
# "running" forever (Task: sensor-queue-latency found exactly one of these
# stuck on the live hub). `full_upgrade`/`security_updates` are the longest
# budgeted action (1500s in fix.py's FIX_ACTIONS) and, while genuinely
# running, post a `/progress` heartbeat at least every ~3s -- so a row with
# no sign of life for this long is not "still running," it is stuck.
STALE_RUNNING_TIMEOUT_S = 2700   # 45 min: ~1.8x the longest fix's own timeout


def _reap_stale_running(db: DbSession, tenant_id: str):
    """Fail any "running" row for this tenant that has shown no sign of life
    (a `/progress` heartbeat, or -- for an action that never sends one --
    the original enqueue time) for STALE_RUNNING_TIMEOUT_S. Called from
    `GET /queue` rather than a separate scheduled job: the smaller fix, and
    it means a stuck row reaps itself the moment that tenant's sensor next
    polls -- which, with the sensor's own ~10s queue-poll cadence, is soon
    after the sensor itself comes back, even after an outage."""
    cutoff = _now() - dt.timedelta(seconds=STALE_RUNNING_TIMEOUT_S)
    rows = db.query(m.Queue).filter_by(tenant_id=tenant_id, status="running").all()
    reaped = []
    for q in rows:
        last_alive = q.heartbeat_at or q.started_at or q.ts
        at = ((q.result or {}).get("progress") or {}).get("at")
        if at:
            try:
                last_alive = dt.datetime.fromisoformat(at)
            except ValueError:
                pass
        if last_alive.tzinfo is None:
            last_alive = last_alive.replace(tzinfo=dt.timezone.utc)
        if last_alive < cutoff:
            result = {**(q.result or {}), "out": "timed out -- the sensor never reported a result (it may have crashed or lost connectivity mid-run)"}
            changed = db.query(m.Queue).filter_by(id=q.id, status="running").filter(m.Queue.heartbeat_at == q.heartbeat_at).update(
                {"status": "failed", "finished_at": _now(), "result": result}, synchronize_session=False)
            if not changed:
                continue
            db.refresh(q)
            # A fresh attempt has its own id: a late response to the abandoned
            # attempt cannot overwrite the replacement's outcome.
            if q.path in ("/api/run", "/api/speedtest", "/api/vuln-scan", "/api/deep-dive") and not result.get("retry_of"):
                retry = m.Queue(tenant_id=tenant_id, who=q.who, path=q.path, body=q.body,
                                expires_at=_now() + dt.timedelta(hours=2), result={"retry_of": q.id},
                                wait_reason="Read-only retry after an abandoned attempt; waiting for a suitable sensor")
                db.add(retry)
                db.flush()
                q.result = {**result, "retry_id": retry.id}
            reaped.append(q)
    if reaped:
        db.commit()
        for q in reaped:
            bus.publish(tenant_id, "queue", {"id": q.id, "path": q.path, "status": q.status})
    return reaped


@router.get("/queue")
def queue(auth=Depends(sensor_auth), db: DbSession = Depends(get_db)):
    s, t = auth
    _reap_stale_running(db, t.id)
    from ..jobdispatch import claim
    claimed = claim(db, t, s, _now())
    for job in claimed:
        bus.publish(t.id, "queue", {"id": job["id"], "status": "running"})
    return {"queued": claimed}


class QResult(BaseModel):
    ok: bool
    out: str = ""
    result: dict | None = None


@router.post("/queue/{qid}/result")
def qresult(qid: str, body: QResult, auth=Depends(sensor_auth), db: DbSession = Depends(get_db)):
    s, t = auth
    q = db.get(m.Queue, qid)
    if not q or q.tenant_id != t.id or (q.sensor_id and q.sensor_id != s.id):
        raise HTTPException(404, "not found")
    if q.status != "running":
        raise HTTPException(409, "This attempt is no longer running; its outcome must be reconciled")
    q.status = "done" if body.ok else "failed"
    # Real completion time (Task 29 fix round) -- `Queue.ts` is the enqueue
    # time, not this, so `UpgradeProgress.finished` must come from here, not
    # from `ts`.
    q.finished_at = _now()
    # Keep whatever "/progress" already wrote (Task 29) -- the final result
    # otherwise wipes it, leaving a *finished* upgrade reporting
    # done=0/total=0 in /updates because nothing survived to the last frame.
    prior_progress = (q.result or {}).get("progress")
    q.result = {**{k: v for k, v in (q.result or {}).items() if k in ("retry_of", "retry_id")}, "out": body.out[:4000], **(body.result or {})}
    if prior_progress and "progress" not in q.result:
        q.result["progress"] = prior_progress
    db.commit()
    bus.publish(t.id, "queue", {"id": q.id, "path": q.path, "status": q.status})
    qbody = q.body or {}
    if q.path == "/api/fix":
        _report_fix_result(db, t, q)
    if q.path == "/api/router/test":
        _record_router_test(db, t, q)
    if q.path == "/api/router/disable-remote-admin":
        _record_router_disable(db, t, q)
    if q.path in ("/api/fix", "/api/reboot-plan") and q.status == "done":
        q.result = {**q.result, "verification": "pending", "verification_note": "Command completed; waiting for a fresh target check"}
        db.commit()
    if q.path in ("/api/vuln-scan", "/api/deep-dive"):
        # A finished manual scan goes wherever alerts go (Telegram, Slack,
        # ...), as a report: the target, the score, the counts, and the top
        # findings. Severity follows the worst finding so a critical result
        # rides the CRIT route and a clean one the INFO route.
        _deliver_scan_report(t, q)
    if qbody.get("action") in ("security_updates", "full_upgrade"):
        # The `upgrade` frame the web's UpgradeProgressPanel patches its
        # cards from (Task 29 fix round finding 1) -- publish the final
        # status/finished here too, not just from `/progress`, or a card
        # never settles out of "running" once the last progress line has
        # already been delivered.
        prog = (q.result or {}).get("progress") or {}
        bus.publish(t.id, "upgrade", {
            "queue_id": q.id, "host": qbody.get("host") or "", "action": qbody.get("action") or "",
            "status": q.status, "done": int(prog.get("done") or 0), "total": int(prog.get("total") or 0),
            "log_tail": (q.result or {}).get("out") or prog.get("line") or "",
            "finished": q.finished_at.isoformat(timespec="seconds"),
        })
    return {"ok": True}


def _record_router_test(db, t, q) -> None:
    """Write a finished router credential-Test back onto the tenant's `Router`
    row so the browser card can show pass/fail. The sensor's result carries a
    plain message ONLY -- never the credential -- so this is safe to persist and
    show. Nothing on the router was changed: a Test is login + logout."""
    row = router_store.get(db, t.id)
    if row is None:
        return
    res = q.result or {}
    ok = q.status == "done"
    msg = (res.get("out") or "")[:300]
    row.last_test_ok = ok
    row.last_test_msg = msg
    row.last_test = q.finished_at or _now()
    row.status = "ok" if ok else "error"
    db.commit()
    bus.publish(t.id, "router", {"action": "tested", "ok": ok})


def _record_router_disable(db, t, q) -> None:
    """Remember that Vault turned remote administration off on this gateway.

    Without this the owner pressed the button, the sensor did the work, the
    toast said it worked -- and the card offered the very same action again on
    the next render, because nothing persisted the outcome. That is the bug
    this closes.

    What gets written is the ACT, not the state: `apply_abstract.cgi` is a
    write-only diff model with no getter, so the hub can never read the live
    value back. So a success stamps the time (the card then says "Vault
    turned this off on <date>" and keeps a quieter way to run it again, for
    the case where somebody re-enables it from the router's own page), and a
    FAILURE records only the message. A failed attempt is evidence about the
    attempt; it says nothing about the router, and must never clear a stamp an
    earlier successful run left behind."""
    row = router_store.get(db, t.id)
    if row is None:
        return
    res = q.result or {}
    ok = q.status == "done"
    row.remote_admin_msg = (res.get("out") or res.get("error") or "")[:300]
    if ok:
        row.remote_admin_off_at = q.finished_at or _now()
    db.commit()
    bus.publish(t.id, "router", {"action": "remote-admin-disabled", "ok": ok})


def _settle_fixed_alerts(db, t, q) -> int:
    """A fix the sensor just reported done closes the open alerts it was the
    remedy for -- right now, on the live bus, instead of minutes later when
    the sensor's next sweep notices. "Install pending security updates" ran
    and finished, yet the inbox still showed the CVE row it had been pressed
    on; that is what this closes. The sensor raises it again if the repair
    did not actually take. Returns the alerts it resolved (key/sev/msg).

    Closing here is not the same as verifying: the command finishing and the
    recovery being observed are two different facts, and the caller marks the
    row `verification: pending` until a later target check confirms it."""
    from .. import remedy as remedy_mod
    from .write import apply_alert_op

    body = q.body or {}
    action, host = body.get("action") or "", body.get("host") or ""
    if not action or not host:
        return 0
    resolved = []
    # Which alerts this repair answers is `remedy.alerts_for_fix`'s question,
    # not this function's: `routers/write.queue_fix` asks the same one when it
    # records the decision, and two copies of the reverse mapping would drift
    # (see that helper's docstring).
    for a in remedy_mod.alerts_for_fix(db, t, action, host, body.get("arg")):
        if apply_alert_op(db, t, a.key, "resolve", q.who or "sensor", "", ip="fix-done") is not None:
            resolved.append({"key": a.key, "sev": a.sev, "msg": a.msg})
    if resolved:
        db.commit()
    return resolved


def _report_fix_result(db, t, q) -> None:
    """Report a finished fix back to the channels the owner watches -- the
    "report when done" half of texting Vault "fix it". Every `/api/fix` is
    human-requested (autonomous repairs run on the sensor, never through this
    queue path), so a completion notice here is always something the owner
    asked for and is waiting on. On success it also closes the alert the fix
    remedied, right now. Delivered through the normal channel routing, so it
    lands wherever alerts do (Slack), at the severity of what it fixed."""
    from .. import remedy as remedy_mod, errfmt
    from ..integrations import registry
    from ..config import settings as _settings
    body = q.body or {}
    action, host = body.get("action") or "", body.get("host") or ""
    if not action:
        return
    label = remedy_mod.FIX_LABELS.get(action, action.replace("_", " "))
    out = ((q.result or {}).get("out") or "").strip()
    on_host = f" on {host}" if host else ""
    if q.status == "done":
        resolved = _settle_fixed_alerts(db, t, q)
        sev = "CRIT" if any(r["sev"] == "CRIT" for r in resolved) else "WARN"
        line = f"✓ Done: ran {label}{on_host}."
        if out:
            line += f" {out[:160]}"
        if resolved:
            line += " Cleared: " + "; ".join(r["msg"] for r in resolved)[:220] + "."
        kind = "resolve"
        dedup = resolved[0]["key"] if resolved else ""
    else:
        sev, kind, dedup = "WARN", "alert", ""
        why = errfmt.humanize(out)
        line = f"✗ {label}{on_host} failed" + (f" — {why}" if why else "") + "."
    # Mirrored to the operator's own channel too -- he asked to see the fixes
    # that run on customer networks, not only the alerts that prompted them.
    adminnotify.mirror_alert(
        "fix", sev,
        "Fix done" if q.status == "done" else "Fix failed",
        line, tenant=t.slug or t.name or t.id[:8])
    registry.deliver_background(t.id, registry.Message(
        title="Vault: fix done" if q.status == "done" else "Vault: fix failed",
        body=line, severity=sev,
        url=f"{_settings.base_url.rstrip('/')}/inbox",
        actions=[], kind=kind, dedup_key=dedup))


def _deliver_scan_report(t, q) -> None:
    from ..integrations import registry
    res = q.result if isinstance(q.result, dict) else {}
    detail = res.get("detail") if isinstance(res.get("detail"), dict) else res
    findings = [f for f in (detail.get("findings") or []) if isinstance(f, dict)]
    counts = detail.get("summary") or {}
    target = detail.get("target") or (q.body or {}).get("target") or "?"
    if q.status != "done":
        title = f"Vulnerability scan of {target} failed"
        body = (res.get("out") or "")[:300]
        sev = "WARN"
    else:
        sev = "CRIT" if counts.get("critical") else "WARN" if counts.get("high") else "INFO"
        title = f"Vulnerability scan: {target} — score {detail.get('score', '?')}"
        head = ", ".join(f"{counts.get(k, 0)} {k}" for k in ("critical", "high", "medium", "low") if counts.get(k))
        lines = [head or "nothing to report"]
        for f in findings[:6]:
            if f.get("severity") == "info":
                continue
            port = f" (port {f['port']})" if f.get("port") else ""
            lines.append(f"[{f.get('severity')}] {f.get('title')}{port}")
        if len(findings) > 6:
            lines.append(f"…and {len(findings) - 6} more")
        body = "\n".join(lines)
    adminnotify.mirror_alert("report", sev, title, body,
                             tenant=t.slug or t.name or t.id[:8])
    msg = registry.Message(title=title, body=body, severity=sev, kind="report",
                           dedup_key=f"vulnscan:{q.id}")
    registry.deliver_background(t.id, msg)


class QProgress(BaseModel):
    done: int
    total: int
    line: str = ""


@router.post("/queue/{qid}/progress")
def qprogress(qid: str, body: QProgress, auth=Depends(sensor_auth), db: DbSession = Depends(get_db)):
    """One in-flight upgrade progress line (Task 29). Merges into
    `Queue.result["progress"]` without disturbing `status` -- the item is
    still "running" until the sensor posts a real `/result` -- and publishes
    an `upgrade` live frame so a connected dashboard can show a progress bar
    without polling."""
    s, t = auth
    q = db.get(m.Queue, qid)
    if not q or q.tenant_id != t.id or (q.sensor_id and q.sensor_id != s.id):
        raise HTTPException(404, "not found")
    if q.status != "running":
        raise HTTPException(409, "This attempt is no longer running; its outcome must be reconciled")
    q.heartbeat_at = _now()
    result = dict(q.result or {})
    # "at": the liveness heartbeat `_reap_stale_running` checks -- without it,
    # a stale check would have only the enqueue time (`Queue.ts`) to go on,
    # which would misjudge a legitimately still-running 25-minute upgrade as
    # stuck.
    result["progress"] = {"done": body.done, "total": body.total, "line": body.line[:300], "at": _now().isoformat()}
    q.result = result
    db.commit()
    qbody = q.body or {}
    # Publish the `UpgradeProgress` shape the web's UpgradeProgressPanel
    # actually reads (`status`, `log_tail`, `finished`) -- Task 29 fix round
    # finding 1: this used to send `line`/`done`/`total` only, which the
    # panel's `mergeFrame` silently ignores (it looks for `log_tail`), so a
    # dashboard connected mid-upgrade never saw the bar move.
    bus.publish(t.id, "upgrade", {
        "queue_id": q.id, "host": qbody.get("host") or "", "action": qbody.get("action") or "",
        "status": q.status, "done": body.done, "total": body.total, "log_tail": body.line[:300],
        "finished": None,
    })
    return {"ok": True}
