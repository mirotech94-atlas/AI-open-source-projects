"""Select work by observed reachability, then atomically claim one job at a time."""
import datetime as dt
from sqlalchemy import or_
from . import models as m
from .scans import latest_scan_per_sensor, parse_ts

FRESH_SECONDS = 900


def eligible_sensor(db, tenant, job, now):
    sensors = db.query(m.Sensor).filter_by(tenant_id=tenant.id, status="active").all()
    sensors = [s for s in sensors if s.last_seen and -60 <= (now - s.last_seen).total_seconds() <= FRESH_SECONDS]
    if not sensors:
        return None, "Waiting for a reporting sensor"
    nets = db.query(m.Network).filter_by(tenant_id=tenant.id).all()
    body = job.body or {}
    target = body.get("host") or body.get("target") or body.get("mac") or ""
    slug = body.get("net") or body.get("network") or ""
    net = next((n for n in nets if slug in (n.id, n.slug)), None) if slug else None
    if slug and net is None:
        return None, "The target network no longer exists"
    if net is None and target:
        devices = db.query(m.Device).filter(m.Device.tenant_id == tenant.id,
                    or_(m.Device.mac == target, m.Device.ip == target)).all()
        ids = {d.network_id for d in devices}
        if len(ids) > 1:
            return None, "Choose the network: this device address exists on several networks"
        if ids:
            net = next((n for n in nets if n.id in ids), None)
    if net is None and (not target or target in ("lan", "all")):
        net = next((n for n in nets if n.is_primary), None)
    scans = latest_scan_per_sensor(db, tenant.id, [s.id for s in sensors])
    matches = []
    for sensor in sensors:
        scan = scans.get(sensor.id)
        raw = scan.raw if scan else {}
        stamp = parse_ts(scan.ts) if scan else None
        recent = stamp is not None and -60 <= (now - stamp).total_seconds() <= FRESH_SECONDS
        reported = raw.get("networks") or {}
        if isinstance(reported, list):
            reported = {entry.get("slug"): entry for entry in reported if isinstance(entry, dict)}
        slice_ = reported.get(net.slug, {}) if net else {}
        net_access = bool(net and (net.sensor_id == sensor.id or (not net.sensor_id and sensor.network_id == net.id)
                          or (recent and slice_ and slice_.get("status") not in ("unreachable", "no-sensor"))))
        if recent and slice_.get("status") in ("unreachable", "no-sensor"):
            net_access = False
        seen_host = recent and any(target == h.get("name") and h.get("reachable") for h in raw.get("ssh") or [])
        seen_site = recent and any(target in (s.get("name"), s.get("url")) for s in raw.get("sites") or [])
        if body.get("host") or body.get("kind") == "host":
            qualifies = seen_host and (not net or net_access)
        elif net:
            qualifies = net_access
        else:
            qualifies = seen_site or seen_host
        # Older installations have one sensor and no network binding. Its
        # configured targets still work; never apply this fallback across sensors.
        if not qualifies and not recent and len(sensors) == 1 and not any(n.sensor_id for n in nets):
            qualifies = not slug and not (net and not net.is_primary)
        if qualifies:
            matches.append(sensor)
    if not matches:
        return None, f"Waiting for a sensor that can reach {net.name if net else target or 'the target'}"
    matches.sort(key=lambda s: (-(s.last_seen.timestamp()), s.id))
    return matches[0], "Selected automatically from sensors with access to the target"


def claim(db, tenant, sensor, now):
    jobs = db.query(m.Queue).filter(m.Queue.tenant_id == tenant.id,
             m.Queue.status.in_(["queued", "scheduled"])).order_by(m.Queue.ts).all()
    for job in jobs:
        body = job.body or {}
        # Also honor plans created before the lifecycle migration.
        start = job.not_before or (parse_ts(body.get("window_start")) if job.path == "/api/reboot-plan" else None)
        end = job.expires_at or (parse_ts(body.get("window_end")) if job.path == "/api/reboot-plan" else None)
        if job.path == "/api/reboot-plan" and (start is None or end is None):
            db.query(m.Queue).filter(m.Queue.id == job.id, m.Queue.status.in_(["queued", "scheduled"])).update({"status":"missed","finished_at":now,"wait_reason":"This old schedule has no complete date and timezone; choose a new time"},synchronize_session=False)
            db.commit()
            continue
        if end and now >= end:
            db.query(m.Queue).filter(m.Queue.id == job.id, m.Queue.status.in_(["queued", "scheduled"])).update(
                {"status": "missed", "finished_at": now, "wait_reason": "Window missed; choose a new time" if job.path == "/api/reboot-plan" else "Expired while waiting for a suitable sensor; review and request a new attempt"}, synchronize_session=False)
            db.commit()
            continue
        if start and now < start:
            continue
        selected, reason = eligible_sensor(db, tenant, job, now)
        if selected is None:
            db.query(m.Queue).filter(m.Queue.id == job.id, m.Queue.status.in_(["queued", "scheduled"])).update(
                {"wait_reason": reason}, synchronize_session=False)
            result = job.result or {}
            if (now - job.ts).total_seconds() > 900 and not result.get("wait_notified"):
                job.result = {**result, "wait_notified": True}
                db.commit()
                from .integrations import registry
                registry.deliver_background(tenant.id, registry.Message(title="Vault job is waiting", body=reason,
                    severity="WARN", kind="alert", dedup_key=f"queue-wait:{job.id}", actions=[], url=""))
            else:
                db.commit()
            continue
        if selected.id != sensor.id:
            continue
        changed = db.query(m.Queue).filter(m.Queue.id == job.id, m.Queue.status.in_(["queued", "scheduled"])).update(
            {"status": "running", "sensor_id": sensor.id, "started_at": now,
             "heartbeat_at": now, "wait_reason": reason}, synchronize_session=False)
        db.commit()
        if changed:
            # The sensor executes its returned list serially. Claim one so a
            # second job's execution clock does not run while it waits locally.
            return [{"id": job.id, "path": job.path, "body": body, "who": job.who}]
    return []


def maintain_waiting(db, tenant, now):
    """Deadlines and wait notices also advance during a complete sensor outage."""
    from .live import bus
    from .integrations import registry
    jobs = db.query(m.Queue).filter(m.Queue.tenant_id == tenant.id,
            m.Queue.status.in_(["queued", "scheduled"])).order_by(m.Queue.ts).limit(500).all()
    for job in jobs:
        body = job.body or {}
        start = job.not_before or (parse_ts(body.get("window_start")) if job.path == "/api/reboot-plan" else None)
        end = job.expires_at or (parse_ts(body.get("window_end")) if job.path == "/api/reboot-plan" else job.ts + dt.timedelta(hours=2))
        if job.path == "/api/reboot-plan" and (start is None or end is None):
            end = now
        if end and now >= end:
            reason = "Window missed; choose a new time" if job.path == "/api/reboot-plan" else "Expired while waiting; review and request a new attempt"
            changed = db.query(m.Queue).filter(m.Queue.id==job.id,m.Queue.status.in_(["queued","scheduled"])).update(
                {"status":"missed","finished_at":now,"wait_reason":reason},synchronize_session=False)
            db.commit()
            if changed:
                bus.publish(tenant.id,"queue",{"id":job.id,"status":"missed"})
            continue
        eligible_since = max(job.ts, start) if start else job.ts
        if (now-eligible_since).total_seconds() < 900 or (job.result or {}).get("wait_notified"):
            continue
        selected, reason = eligible_sensor(db,tenant,job,now)
        if selected:
            reason = f"Waiting for {selected.name} to pick up this job"
        job.wait_reason = reason
        job.result = {**(job.result or {}),"wait_notified":True}
        db.commit()
        registry.deliver_background(tenant.id,registry.Message(title="Vault job is waiting",body=reason,
            severity="WARN",kind="alert",dedup_key=f"queue-wait:{job.id}",actions=[],url=""))
