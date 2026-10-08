"""Verify repairs from explicit, later target evidence, never missing alerts."""
from . import models as m
from .scans import parse_ts


def outcome(action, host, arg=""):
    sec = host.get("sec") or {}
    if action == "enable_ufw" and "ufw" in sec:
        return sec["ufw"] == "active"
    if action == "disable_ssh_password" and "ssh_password" in sec:
        return sec["ssh_password"] is False
    if action == "enable_unattended" and "unattended" in sec:
        return bool(sec["unattended"])
    if action == "security_updates" and "security_updates" in sec and host.get("cve_fixable", -1) >= 0:
        return not sec["security_updates"] and host["cve_fixable"] == 0
    if action == "full_upgrade" and isinstance(host.get("pending_updates"), int):
        return host["pending_updates"] == 0
    if action == "reboot" and "reboot_required" in host:
        return host["reboot_required"] is False
    return None


def reconcile(db, tenant, sensor, last):
    # Function-local, like `_settle_fixed_alerts` right below and for the same
    # reason: `decisions` reaches `confidence`, and this module is imported
    # from the ingest path.
    from .routers.sensor import _settle_fixed_alerts
    from .live import bus
    from . import decisions
    stamp = parse_ts(last.get("ts"))
    if sensor is None or stamp is None:
        return
    hosts = {h.get("name"): h for h in last.get("ssh") or [] if h.get("reachable")}
    jobs = db.query(m.Queue).filter_by(tenant_id=tenant.id, sensor_id=sensor.id,
                                     status="done").filter(m.Queue.path.in_(["/api/fix", "/api/reboot-plan"])).all()
    for job in jobs:
        result, body = job.result or {}, job.body or {}
        host = hosts.get(body.get("host"))
        if result.get("verification") != "pending" or host is None or not job.finished_at or stamp <= job.finished_at:
            continue
        act = "reboot" if job.path == "/api/reboot-plan" else body.get("action")
        verdict = outcome(act, host, body.get("arg", ""))
        if verdict is None:
            continue
        job.result = {**result, "verification": "verified" if verdict else "needs_attention",
                      "verification_note": "Recovery verified by a fresh target check" if verdict else "Fresh target check still needs attention"}
        # This is where the outcome of the decision behind the repair becomes
        # KNOWN, and it is the earliest such moment: a fresh target check, on
        # evidence stamped after the job finished, either shows the recovery
        # (`held`) or still shows the problem the repair was meant to clear
        # (`recurred`). Settled from here rather than from the fix-done report
        # in `routers/sensor`, because a command exiting zero is not an
        # outcome -- `_settle_fixed_alerts`' own docstring makes that
        # distinction, and a ledger that recorded "held" on exit code alone
        # would label every repair a success and be worth nothing to fit a
        # model to.
        #
        # `body["host"]`, not the local `host`: that name was rebound to the
        # scan's host DICT eleven lines up. `act`, not `body["action"]`, so
        # the repair settled is the same one the verdict was computed for.
        # `arg` too, so one service's restart does not settle another's on the
        # same host (see `decisions.settle`).
        if body.get("host") and act:
            decisions.settle(db, tenant, host=body["host"], action=act,
                             arg=body.get("arg"),
                             outcome="held" if verdict else "recurred")
        if verdict:
            _settle_fixed_alerts(db, tenant, job)
        db.commit()
        bus.publish(tenant.id, "queue", {"id": job.id, "status": job.status})

    for fix in db.query(m.Fix).filter_by(tenant_id=tenant.id, sensor_id=sensor.id, ok=True, verified_at=None).all():
        completed = parse_ts(fix.ts)
        host = hosts.get(fix.host)
        if completed and stamp > completed and host and outcome(fix.action, host, fix.arg or "") is True:
            fix.verified_at = stamp
            # The other place a repair is confirmed to have taken, and the
            # only one for a repair the sensor ran by itself (no `m.Queue`
            # row, so the job loop above never sees it). `settle` only touches
            # rows still open, so when both loops speak about the same repair
            # in the same sweep the first outcome stands -- which is the
            # property the ledger is built on.
            #
            # `fix.host`, not the local `host`: rebound to the scan's host
            # dict above, exactly as in the job loop.
            decisions.settle(db, tenant, host=fix.host, action=fix.action,
                             arg=fix.arg, outcome="held")
    db.commit()
