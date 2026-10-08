"""A bounded, guided DNS demo: the same example domain and client recur.
Temporary exceptions change subsequent simulated queries to allowed; expiry
restores the simulated block. No unrelated ports or successful repairs are
invented between refreshes. Historical fixtures remain labeled example data.
"""

import datetime as dt
import random
import threading

from sqlalchemy.orm import Session as DbSession

from . import models as m
from .scans import latest_scan, parse_ts
from .seed import DEMO_DNS_LISTS, DEMO_DNS_NAMES, DEMO_EVENT_PORTS, DEMO_FIX_ACTIONS, DEMO_PEERS

#: See the cadence note above.
TRICKLE_INTERVAL_S = 15
#: See the retention note above.
TRICKLE_KEEP_S = 3600.0

#: Once the demo's newest scan is this old, a tick writes a fresh one -- a
#: copy of it, restamped. See `_add_heartbeat_scan`.
HEARTBEAT_AFTER_S = 300

_lock = threading.Lock()
#: tenant_id -> [(model, row_id, inserted_at_epoch_s), ...], insertion order.
_inserted: dict[str, list[tuple[type, int, float]]] = {}
#: tenant_id -> index into `_KINDS`, for round-robin variety across ticks.
_next_kind: dict[str, int] = {}

#: Round-robin order. "port" needs an existing device and "fix" needs an
#: existing host; `trickle_demo` falls back to "dns" (needs neither, beyond
#: an optional device to attribute the client to) when either is briefly
#: unavailable, so a tick is never wasted on a tenant that has, say, no
#: Host rows yet.
_KINDS = ("dns",)

#: The two demo peers a tick's flow rows repeat, pulled out of `seed.
#: DEMO_PEERS` (the seeded 30 days' superset) by name rather than
#: re-listed here -- so there is exactly one place that names them and the
#: trickle can never drift onto a peer the historical seed doesn't also
#: know about. CLAUDE.md's bound on this module ("it does not invent
#: unrelated ports or successful repairs between refreshes") applies here
#: to peers: `dentrix.example` is the practice's own PMS and `www.google.com`
#: is the one large, boring destination every device on the network reaches
#: constantly -- exactly the traffic a peer of a dental practice's LAN
#: should see between refreshes, never a name invented for the demo.
_TRICKLE_FLOW_PEERS = tuple(p for p in DEMO_PEERS if p[0] in ("www.google.com", "dentrix.example"))


def _now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def _tenant_offset(db: DbSession, tenant_id: str) -> dt.timedelta:
    """The UTC offset the demo tenant's OWN rows are already stamped with.

    `Event.ts`/`Fix.ts`/`DnsQuery.ts` are plain strings, and every row
    builder in `routers/public_feed.py` takes its "most recent" slice with a
    SQL `order_by(ts.desc()).limit(n)` -- a lexicographic comparison, not a
    parsed one (see `scans.py`'s module docstring for exactly this
    distinction, which is why `latest_scan` below doesn't just trust
    `order_by`). `seed_demo` deliberately keeps the fixture's own offset for
    every row it writes (its docstring: "The fixture's own UTC offset is
    kept, not swapped for the caller's"), so every historical row in these
    tables already shares one consistent offset. A trickled row stamped in
    a DIFFERENT offset (naively, UTC) would still be chronologically
    correct once parsed, but could sort into the wrong position -- or fall
    outside the row builders' over-read window entirely -- under their
    plain string `ORDER BY`. Matching the tenant's own offset here keeps
    every row in one consistent family, so the string order and the actual
    chronology agree exactly the way `seed_demo`'s own rows already do.

    Falls back to UTC (offset zero) when there is no scan yet to read it
    from -- the trickled row is still correct, just not guaranteed to
    out-sort older rows in a different offset, which cannot happen anyway
    on a tenant with no scan (the row builders have nothing to compare it
    against until one exists).
    """
    scan = latest_scan(db, tenant_id)
    if not scan or not scan.ts:
        return dt.timedelta(0)
    try:
        parsed = dt.datetime.fromisoformat(scan.ts.replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return dt.timedelta(0)
    return parsed.utcoffset() or dt.timedelta(0)


def reset() -> None:
    """Test-only, mirroring `public_feed.reset_cache()`/`ratelimit.reset()`:
    this module's bookkeeping is process-global and keyed by tenant id, so
    without this a test suite that drops and recreates the database between
    tests (every test gets a fresh `demo` tenant, and ids are not reused)
    would just accumulate orphaned entries for tenants that no longer
    exist -- harmless, but unbounded over a long test run."""
    with _lock:
        _inserted.clear()
        _next_kind.clear()


def forget_tenant(tenant_id: str) -> None:
    """Drop this module's bookkeeping for `tenant_id`. Called by
    `scheduler.demo_refresh_job` right after a successful `refresh_demo`, so
    a ten-minute-old id is never retried against a row `reset_tenant` has
    already deleted."""
    with _lock:
        _inserted.pop(tenant_id, None)
        _next_kind.pop(tenant_id, None)


def _prune(db: DbSession, tenant_id: str, moment: float) -> int:
    """Delete every row THIS MODULE inserted for `tenant_id` more than
    `TRICKLE_KEEP_S` ago -- never a table scan by timestamp, which would
    just as happily delete a legitimate historical row that happens to be
    within the window (a freshly-seeded tenant's newest DNS row lands
    within `~30s` of the anchor, per `seed._gen_dns`)."""
    with _lock:
        rows = list(_inserted.get(tenant_id) or [])
    cutoff = moment - TRICKLE_KEEP_S
    keep = [r for r in rows if r[2] >= cutoff]
    drop = [r for r in rows if r[2] < cutoff]
    if drop:
        by_model: dict[type, list[int]] = {}
        for model, row_id, _when in drop:
            by_model.setdefault(model, []).append(row_id)
        for model, ids in by_model.items():
            db.query(model).filter(model.id.in_(ids)).delete(synchronize_session=False)
        db.commit()
    with _lock:
        _inserted[tenant_id] = keep
    return len(drop)


def _pick_device(db: DbSession, tenant_id: str, rng: random.Random) -> "m.Device | None":
    devices = (
        db.query(m.Device)
        .filter(m.Device.tenant_id == tenant_id, m.Device.mac != "", m.Device.ip != "")
        .all()
    )
    return sorted(devices, key=lambda d: (d.network_id or "", d.mac))[0] if devices else None


def _pick_devices(db: DbSession, tenant_id: str, n: int) -> "list[m.Device]":
    """The first `n` real demo devices, same deterministic sort as
    `_pick_device` (so this always widens from the same one it would have
    picked alone) -- never a random sample, or which devices get flow rows
    on a given tick would depend on dict/query ordering rather than the rng
    seed."""
    devices = (
        db.query(m.Device)
        .filter(m.Device.tenant_id == tenant_id, m.Device.mac != "", m.Device.ip != "")
        .all()
    )
    return sorted(devices, key=lambda d: (d.network_id or "", d.mac))[:n]


def _add_dns(db: DbSession, tenant: m.Tenant, device: "m.Device | None",
             ts: str, rng: random.Random) -> tuple[type, int]:
    """A blocked lookup -- the only DNS shape the public panel shows
    (`public_feed._dns_rows` filters `blocked=True`), so that is the only
    shape trickled in."""
    stamp = parse_ts(ts)
    allowed = any(e.get("domain") == "blocked-demo.example" and (parse_ts(e.get("expires")) or stamp) > stamp
                  for e in ((tenant.config or {}).get("dns") or {}).get("exceptions") or [])
    row = m.DnsQuery(
        tenant_id=tenant.id, network_id=device.network_id if device else None,
        ts=ts, client=device.ip if device else "203.0.113.10",
        name="blocked-demo.example", qtype="A", blocked=not allowed,
        list="simulated temporary exception" if allowed else "simulated rule", ms=rng.randint(5, 60),
        mac=device.mac if device else None,
    )
    db.add(row)
    db.flush()
    return m.DnsQuery, row.id


def _add_port(db: DbSession, tenant: m.Tenant, device: "m.Device | None",
              ts: str, rng: random.Random) -> "tuple[type, int] | None":
    """Toggles one port on a real device, keeping `Device.ports` (what the
    Devices tab reads) and the `Event` row (what the public panel reads) in
    agreement -- an open port closes, a closed one opens, never a port that
    was never seen open in the first place."""
    if device is None:
        return None
    port = rng.choice(DEMO_EVENT_PORTS)
    ports = dict(device.ports or {})
    if str(port) in ports:
        ports.pop(str(port), None)
        kind = "port_closed"
    else:
        ports[str(port)] = ts
        kind = "port_open"
    device.ports = ports
    device.last_seen = ts
    row = m.Event(tenant_id=tenant.id, network_id=device.network_id, ts=ts,
                  type=kind, entity=device.mac, payload={"port": port})
    db.add(row)
    db.flush()
    return m.Event, row.id


def _add_fix(db: DbSession, tenant: m.Tenant, ts: str, rng: random.Random) -> "tuple[type, int] | None":
    """A completed auto-fix against a host the demo tenant already reports
    on (`Host.name`) -- never a fabricated hostname, so the Updates tab's
    per-host history stays consistent with what the panel claims."""
    hosts = [h.name for h in db.query(m.Host).filter_by(tenant_id=tenant.id).all() if h.name]
    if not hosts:
        return None
    row = m.Fix(
        tenant_id=tenant.id, ts=ts, host=rng.choice(hosts),
        action=rng.choice(DEMO_FIX_ACTIONS), label="Simulated repair example", ok=True, by="auto",
    )
    db.add(row)
    db.flush()
    return m.Fix, row.id


def _add_flows(db: DbSession, tenant: m.Tenant, ts_min: str, rng: random.Random) -> "list[tuple[type, int]]":
    """Four `Flow` rows at the trickled minute: the two `_TRICKLE_FLOW_PEERS`
    times up to two real demo devices, so the traffic tab has live motion the
    same way DNS does. Fewer than two devices existing yet (a tenant mid-seed)
    just yields fewer rows -- never a fabricated device.

    `Flow`'s own unique key is `(tenant, ts, mac, peer_ip, peer_port, proto,
    source)` -- and since ts here is floored to the minute while
    `TRICKLE_INTERVAL_S` is 15s, several ticks land in the same minute for
    the same (device, peer) pair. `ingest.store_scan` handles this by
    de-duping before insert rather than trusting the constraint to raise;
    this does the same, so a same-minute tick is a harmless no-op for that
    pair instead of an `IntegrityError` that would take the whole tick down.
    """
    devices = _pick_devices(db, tenant.id, 2)
    existing = {
        (mac, peer_ip, peer_port, proto, source)
        for mac, peer_ip, peer_port, proto, source in db.query(
            m.Flow.mac, m.Flow.peer_ip, m.Flow.peer_port, m.Flow.proto, m.Flow.source
        ).filter(m.Flow.tenant_id == tenant.id, m.Flow.ts == ts_min).all()
    }
    added: list[tuple[type, int]] = []
    for device in devices:
        for peer_name, peer_ip, peer_port, proto in _TRICKLE_FLOW_PEERS:
            key = (device.mac, peer_ip, peer_port, proto, "mirror")
            if key in existing:
                continue
            existing.add(key)
            row = m.Flow(
                tenant_id=tenant.id, network_id=device.network_id, ts=ts_min,
                mac=device.mac, ip=device.ip, peer_ip=peer_ip, peer_port=peer_port, proto=proto,
                bytes_in=rng.randint(200, 20_000), bytes_out=rng.randint(200, 5_000),
                pkts_in=rng.randint(1, 50), pkts_out=rng.randint(1, 50),
                peer_name=peer_name, hints={}, source="mirror",
            )
            db.add(row)
            db.flush()
            added.append((m.Flow, row.id))
    return added


def _stream_flows(db: DbSession, tenant_id: str, ids: list[int]) -> None:
    """Send the tick's new flows to anyone watching Live connections, shaped
    by the same `traffic.stream_row` the real ingest path uses -- so the demo
    tab moves the way a customer's does, and never shows a field a real row
    would not have."""
    from .live import bus
    from .payloads import traffic
    if not ids or not bus.has_subscribers(tenant_id):
        return
    rows = db.query(m.Flow).filter(m.Flow.id.in_(ids)).order_by(m.Flow.id).all()
    names = traffic.device_names(db, tenant_id, {r.mac for r in rows})
    for r in rows:
        row = traffic.stream_row(r, names.get(r.mac, ""))
        bus.publish(tenant_id, "flow", row.model_dump())


def _add_heartbeat_scan(db: DbSession, tenant: m.Tenant, ts: str,
                        moment_dt: dt.datetime) -> "tuple[type, str] | None":
    """Restamp the demo's newest scan as a new row, when it has gone stale.

    **Why this exists.** The demo dashboard reads "online" only while its
    newest scan is under `overview.SENSOR_LIVE_S` (15 minutes) old, and the
    only thing that wrote one was `seed.refresh_demo` -- a full wipe and
    re-seed of the tenant. So it ran every ten minutes. On the production hub
    (one vCPU) each run took ~30 s of the only CPU with SQLite's write lock
    held, and every request that landed in that window waited for it: the
    11.5 s Overview a person measured, and the public site's live panel
    sitting on grey placeholders.

    A scan is what makes the demo read as alive, so this writes one: the
    newest scan's own payload, unchanged except for its timestamp (and the
    `ts` inside it, which readers of `raw` use). It says nothing new -- the
    same devices, the same findings -- which is exactly the honesty rule the
    refresh holds itself to: move *when*, never *what*. The full refresh now
    runs hourly, and it deletes these along with everything else; between
    refreshes `_prune` removes them after `TRICKLE_KEEP_S` like every other
    trickled row.
    """
    newest = latest_scan(db, tenant.id)
    seen = parse_ts(newest.ts) if newest else None
    if newest is None or seen is None:
        return None
    if (moment_dt - seen).total_seconds() < HEARTBEAT_AFTER_S:
        return None
    raw = dict(newest.raw or {})
    raw["ts"] = ts
    row = m.Scan(tenant_id=tenant.id, ts=ts, sensor_id=newest.sensor_id, score=newest.score,
                 health=dict(newest.health or {}), raw=raw)
    db.add(row)
    # The devices this scan reports present are restamped with it. A device
    # reads ONLINE only while `last_seen` is under 15 minutes old
    # (`effective_presence`), so a fresh scan with stale devices put every
    # demo device at UNKNOWN for most of each hour once the full re-seed
    # stopped running every ten minutes. Only rows already ONLINE move: the
    # copy claims nothing the scan it copies did not.
    (db.query(m.Device)
       .filter(m.Device.tenant_id == tenant.id, m.Device.presence == "ONLINE")
       .update({m.Device.last_seen: ts}, synchronize_session=False))
    db.query(m.Sensor).filter(m.Sensor.tenant_id == tenant.id).update(
        {m.Sensor.last_seen: moment_dt}, synchronize_session=False)
    db.flush()
    return m.Scan, row.id


def trickle_demo(db: DbSession, slug: str = "demo", now: "dt.datetime | None" = None) -> "dict | None":
    """Add one new row for the `demo` tenant and prune whatever this module
    inserted more than `TRICKLE_KEEP_S` ago. Returns `None` -- a no-op --
    when there is no tenant at `slug`.

    `now`, when given, is both the row's timestamp and the instant pruning
    measures against -- a seam so a test can simulate many ticks (and the
    passage of an hour) without a real sleep.
    """
    tenant = db.query(m.Tenant).filter_by(slug=slug).first()
    if tenant is None:
        return None

    moment_dt = now or _now()
    offset = _tenant_offset(db, tenant.id)
    # Stamped in the tenant's own existing offset, not forced to UTC -- see
    # `_tenant_offset`'s docstring for why a mismatched offset here would be
    # a correctness bug, not just a style choice.
    local_dt = moment_dt.astimezone(dt.timezone(offset))
    ts = local_dt.isoformat(timespec="seconds")
    # Flow rows are a per-minute aggregate (`seed._gen_flows`'s own note),
    # so a tick's flow rows are stamped at the trickled MINUTE, not the
    # second -- matching the shape a real sensor mirror would report.
    ts_min = local_dt.replace(second=0, microsecond=0).isoformat(timespec="seconds")
    moment = moment_dt.timestamp()
    # Stable timing makes the demonstration reproducible.
    rng = random.Random(int(moment // TRICKLE_INTERVAL_S))

    with _lock:
        idx = _next_kind.get(tenant.id, 0)
        _next_kind[tenant.id] = (idx + 1) % len(_KINDS)
    kind = _KINDS[idx]

    device = _pick_device(db, tenant.id, rng)
    added = None
    if kind == "port":
        added = _add_port(db, tenant, device, ts, rng)
    elif kind == "fix":
        added = _add_fix(db, tenant, ts, rng)
    if added is None:
        # Either this was already "dns"'s turn, or "port"/"fix" had nothing
        # to attribute to yet (no device / no host) -- dns needs neither.
        kind = "dns"
        added = _add_dns(db, tenant, device, ts, rng)

    flow_added = _add_flows(db, tenant, ts_min, rng)
    heartbeat = _add_heartbeat_scan(db, tenant, ts, moment_dt)
    db.commit()
    _stream_flows(db, tenant.id, [row_id for _, row_id in flow_added])

    model, row_id = added
    with _lock:
        bucket = _inserted.setdefault(tenant.id, [])
        bucket.append((model, row_id, moment))
        for flow_model, flow_id in flow_added:
            bucket.append((flow_model, flow_id, moment))
        if heartbeat is not None:
            bucket.append((heartbeat[0], heartbeat[1], moment))
    pruned = _prune(db, tenant.id, moment)
    return {"tenant_id": tenant.id, "kind": kind, "pruned": pruned}
