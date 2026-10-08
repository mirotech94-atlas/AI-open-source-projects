"""Turn a sensor's ingest payload (Nexus-shaped last.json/state.json + jsonl
tails) into rows across the hub's tables.

The hub, not the sensor, owns alert delivery as of Task 40: after this
module's own `db.commit()`, `store_scan` calls
`integrations.registry.deliver_background` for every alert that newly
fired, re-fired while open, or resolved in this ingest -- always AFTER the
commit, so a slow or failing integration send can never roll back the scan
it is reporting on. Fix round 1, finding 2: delivery is handed to a
dedicated background thread pool (`registry._DISPATCH_EXECUTOR`, its own DB
session) rather than run on the ingest request itself, so a slow/unreachable
third-party integration never blocks the sensor's HTTP response.
`sensor/guardian_sensor/notify.py` only shows a local macOS banner now.

Real sensor data shapes (as produced by the collector, not the tidier shapes
a first draft might assume):
- last.sites[] = {name, url, ok, code, latency_s, cert_days_left,
  cert_expires, cert_issuer, dns_a, sec}
- last.ssh[] = {name, target, reachable, uptime, load1, load_per_core,
  disk_pct, mem_pct, failed_ssh_24h, banned, fail2ban, pending_updates,
  updates, reboot_required, docker, docker_images, services, ports, sec,
  drift, cve_fixable, cve_list, hostname, optional}
- last.alerts[] = {key, sev, msg, count, fires, first_seen, confidence,
  state} (+ optional fix, why)
- state.alerts_db[key] = {key, sev, msg, count, fires, first_seen,
  last_seen, state, resolved_at, resolved_by} -- the lifecycle field is
  `state`, mapped onto Alert.status.
- last.recommendations[] = {key, priority, text, why, action, decision,
  decided_at, status, done} -- Recommendation.title comes from `text`;
  decision/status are read off the rec itself and then overridden by
  state.rec_decisions[key] = {decision, by, ts, status} when present.
- state.lan.known_macs[mac] = {ip, name, vendor, label, trusted,
  always_on, first_seen, last_seen, presence, risk, flapping, ports}
  (+ optional parent, quarantined, kind, unverified) -- unknown keys land
  in Device.extra.
- state.incidents is a dict keyed by an internal composite string (not the
  incident id) -> {id, opened}; last.incidents[] carries the live incident
  in the sensor's real emitter shape (`alerts.correlate_incidents`):
  {id, entity, title, severity, confidence, alerts, started, story, actions,
  state} -- note `severity`/`started`, NOT `sev`/`opened`, and NO `closed`
  key at all. Both are merged by `id` (Incident.iid = str(id)), not by the
  state dict's own key. An incident is CLOSED when its id stops appearing in
  last.incidents (that list is the live set; state.incidents keeps the
  id/opened bookkeeping for 30 days, resolved or not), which is why the
  close pass below reads only the `last` list -- and only when the payload
  actually carries an `incidents` key, so a partial/older payload closes
  nothing.
- last.score = {total, grade, deductions, potential}; last.health is a
  free-form dict, stored as-is.
- history line (real shape) = {ts, sites: {name -> {ok, lat (seconds),
  cert (days)}}, hosts: {name -> {disk, mem, load, failed}},
  lan: <int live count>, live_macs: [...], dns_ms, speed: null |
  {down, up, ...}, score, alerts, checks, incidents, presence, wan_path}.
- events line = {ts, type, mac, ip, port?, label, trusted, vendor?}.
- remediation (fixes) line = {ts, host, action, label, arg, ok, out, by}.
"""

import datetime as dt, json, logging, re, threading, time

from sqlalchemy.orm import Session as DbSession

from . import adminnotify, models as m, networks, recorders as recorders_mod, router_store, security
from .config import settings
from .integrations import registry
from .live import bus
from .scans import parse_ts
from .schemas_dash import DnsStreamRow

log = logging.getLogger(__name__)

# `dns` live frames are one per ingested DnsQuery row, which can be
# thousands in one push (a demo seed, or a burst of resolver traffic) --
# capped per tenant so a connected browser's socket queue never gets
# flooded. Excess frames are dropped, and the count is folded into the
# next frame that DOES go out (a `dropped` field) rather than silently lost.
#
# `dropped`, when present, is a deliberate divergence from `DnsStreamRow`
# (the pinned schemas_dash.py/types.ts contract, which test_contracts.py
# enforces field-for-field and this module must never touch): the base
# frame is still built through `DnsStreamRow(...).model_dump()` so every
# *typed* field stays enforced, and `dropped` is merged in on top as an
# untyped extra the web client treats as optional/absent-by-default.
DNS_FRAME_RATE = 20   # frames/second/tenant
_dns_buckets: dict[str, dict] = {}
# A bulk ingest (a 3000-row demo seed, a sensor catching up after a network
# blip) can call `_publish_dns_throttled` thousands of times back-to-back;
# nothing here otherwise guarantees it runs on the same thread each time
# (a request thread pool, a future FastAPI background task), so the
# read-modify-write on one tenant's bucket needs a real lock, not just the
# "only ever touched from the loop thread" reasoning `live.Bus` gets away
# with.
_dns_buckets_lock = threading.Lock()


def _publish_dns_throttled(tenant_id: str, row: DnsStreamRow) -> None:
    _publish_throttled(tenant_id, "dns", row)


#: The Live connections stream shares the DNS stream's per-second budget
#: logic, in its own bucket: a mirror port can report thousands of flows in
#: one minute file, and the tab is a glance at motion, not an archive -- the
#: Traffic page and the stored rows are the record. Whatever the budget drops
#: is counted into the next frame's `dropped`, as for DNS.
FLOW_FRAME_RATE = 20   # frames/second/tenant


def _publish_throttled(tenant_id: str, kind: str, row) -> None:
    if not bus.has_subscribers(tenant_id):
        # Nobody's listening (the common case for a bulk/demo/replay
        # ingest) -- a real no-op, so don't spend any of the per-second
        # budget on it. Otherwise a 3000-row seed exhausts the whole
        # window before a real subscriber ever connects, and the next
        # genuinely-live row right after gets silently dropped too.
        return
    now = time.monotonic()
    rate = FLOW_FRAME_RATE if kind == "flow" else DNS_FRAME_RATE
    # DNS keeps its original bucket key (the tenant id alone) so nothing that
    # already reads `_dns_buckets` by tenant changes; other kinds are keyed
    # by (tenant, kind) and never share DNS's budget.
    key = tenant_id if kind == "dns" else f"{tenant_id}:{kind}"
    with _dns_buckets_lock:
        b = _dns_buckets.setdefault(key, {"window_start": now, "count": 0, "dropped": 0})
        if now - b["window_start"] >= 1.0:
            b["window_start"] = now
            b["count"] = 0
        if b["count"] >= rate:
            b["dropped"] += 1
            return
        b["count"] += 1
        dropped = b["dropped"]
        b["dropped"] = 0
    data = row.model_dump()
    if dropped:
        data["dropped"] = dropped
    bus.publish(tenant_id, kind, data)

DEVICE_COLS = {
    "ip", "name", "vendor", "label", "trusted", "always_on", "quarantined",
    "parent", "risk", "presence", "ports", "first_seen", "last_seen",
}

_now = lambda: dt.datetime.now(dt.timezone.utc)

#: Severity order for the "only push a problem" delivery rule. INFO (rank 0)
#: is advisory and never pushed to a channel; WARN and CRIT are. Used to gate
#: alert delivery in `store_scan` -- see the comment there.
_PUSH_RANK = {"INFO": 0, "WARN": 1, "CRIT": 2}


def _ts_bounds(values: list) -> tuple[str, str] | None:
    """A cheap `[lo, hi]` string range (a day of slack on each side, the same
    trick `scans.parse_ts` callers use elsewhere) that safely contains every
    parseable timestamp in `values`. Lets a dedup pre-check run as ONE
    range-bounded query instead of one query per row -- the difference
    between a 30-day demo seed (1000s of history/dns rows) taking 20+ s and
    taking well under a second. Returns None when nothing parses (an empty
    batch, or all-garbage input) so callers can skip the query entirely.
    """
    parsed = [d for d in (parse_ts(v) for v in values) if d is not None]
    if not parsed:
        return None
    lo = min(parsed) - dt.timedelta(days=1)
    hi = max(parsed) + dt.timedelta(days=1)
    fmt = "%Y-%m-%dT%H:%M:%S"
    return lo.strftime(fmt), hi.strftime(fmt)


def _upsert(db: DbSession, model, keys: dict, values: dict):
    row = db.query(model).filter_by(**keys).first()
    if not row:
        row = model(**keys)
        db.add(row)
    for k, v in values.items():
        setattr(row, k, v)
    return row


#: Standing advice the sensor emits from its own findings, paired with the
#: hub-side record that proves the owner has already dealt with it. The sensor
#: has no way to know: it never sees the hub's `Router` row.
def _suppressed_rec_keys(db: DbSession, tenant: m.Tenant) -> set:
    """Recommendation keys this tenant should not be shown, because the hub
    holds evidence the advice has been acted on.

    Only `adv:router-admin` today. It is the generic "change the router admin
    password and disable remote administration" line; once Vault has itself
    run the disable for this gateway, repeating the second half of it is the
    app asking for something it did."""
    row = router_store.get(db, tenant.id)
    if row is not None and row.remote_admin_off_at:
        return {"adv:router-admin"}
    return set()


def _story_text(story) -> str:
    """Incidents carry `story` either as a string (hand-written payloads and
    older imports) or, as the sensor's `alerts.correlate_incidents` actually
    emits it, a list of `{ts, text, sev}` timeline steps."""
    if story is None: return ""
    if isinstance(story, str): return story
    if isinstance(story, list):
        parts = []
        for x in story:
            if isinstance(x, dict): parts.append(" ".join(str(x[k]) for k in ("ts", "sev", "text") if x.get(k)))
            else: parts.append(str(x))
        return "\n".join(parts)
    return json.dumps(story)


def store_scan(db: DbSession, tenant: m.Tenant, sensor: "m.Sensor | None", p: dict, deliver: bool = True) -> dict:
    """`deliver=False` (used by `seed.seed_demo` and `importer.import_nexus_tenant`
    for their one-shot historical backfill) skips the `registry.deliver`
    call below: a 30-day-in-one-go backfill must not replay hundreds of
    real pushes for alerts that "fired" only because this is the first
    time the row exists. A live sensor ingest (`routers/sensor.py`) always
    delivers."""
    last = p.get("last") or {}
    state = p.get("state") or {}
    ts = last.get("ts") or _now().isoformat(timespec="seconds")
    out = {
        "scan": 0, "devices": 0, "alerts": 0, "events": 0, "metrics": 0,
        "fixes": 0, "dns": 0, "recs": 0, "incidents": 0, "presence": 0,
        "skipped": 0, "recorders": 0, "heartbeats": 0,
        "flows": 0, "flows_rejected": 0,
    }
    # state/last.lan.devices is {ip -> mac} for whatever was live at scan
    # time: the only place a DNS query's client ip can be resolved to a
    # device. Built once, used by the DnsQuery writer below.
    ip2mac = {ip: mac for ip, mac in ((last.get("lan") or {}).get("devices") or {}).items() if mac}

    # --- Heartbeat tokens come out of the payload before anything stores it
    #
    # `Scan.raw` is the whole sensor payload, and `GET /api/v1/scans/latest`
    # serves it to any VIEWER. `checks.heartbeats_check` puts each
    # heartbeat's token in there, so leaving it would hand every viewer the
    # write credential that the admin-only setup/rotate routes exist to gate
    # -- the same leak as the alert message, through a different door.
    #
    # Pulled out HERE, above the `m.Scan` insert, so what gets persisted
    # never had it: mutating `last` after `db.add(m.Scan(raw=last))` would
    # depend on no flush having serialised the dict in between. The tokens
    # are kept in a local for the `Heartbeat` upsert further down, which is
    # their one legitimate consumer.
    hb_tokens: dict[str, str] = {}
    for _hb_name, _hb in (last.get("heartbeats") or {}).items():
        if isinstance(_hb, dict) and _hb.get("token"):
            hb_tokens[str(_hb_name)] = str(_hb.pop("token"))

    # Flows come out of the payload here too, and for the same reason as the
    # tokens: `Scan.raw` is served to any viewer, and a minute of flows for
    # forty devices is thousands of rows that belong in their own table, not
    # in a blob every dashboard poll can read. Popped ABOVE the insert so what
    # is persisted never had them.
    flow_rows = last.pop("flows", None) or p.get("flows") or []

    # --- Scan (idempotent per tenant+ts) ---------------------------------
    if not db.query(m.Scan).filter_by(tenant_id=tenant.id, ts=ts).first():
        db.add(m.Scan(
            tenant_id=tenant.id, ts=ts,
            sensor_id=sensor.id if sensor else None,
            score=(last.get("score") or {}).get("total"),
            health=last.get("health") or {},
            raw=last,
        ))
        out["scan"] = 1

    # --- Networks (Task 51) ---------------------------------------------
    # Runs before every writer below, because each of them needs a
    # network_id: the primary is the attribution fallback, `by_slug`
    # resolves a row's own `net`, and `for_client_ip` covers a DNS query
    # that knows only its client ip.
    cfg = tenant.config or {}
    primary = networks.ensure_primary(db, tenant, cfg, last, sensor)
    if networks.needs_backfill(db, tenant.id):
        # An existing single-network deployment: every pre-network row lands
        # wholly on the primary on the first ingest after this deploys.
        log.info("ingest: backfilled pre-network rows onto %s: %s",
                  primary.slug, networks.backfill(db, tenant, primary))
    pushed = p.get("networks") or []
    reported = {e.get("slug") for e in pushed if isinstance(e, dict) and e.get("slug")}
    networks.sync_from_config(db, tenant, cfg, reported=reported)
    nets = networks.by_slug(db, tenant.id)

    def _net_for(slug: str | None, ip: str = "") -> "m.Network":
        """Attribution order for every row below: the row's own `net` slug ->
        a longest-prefix match on its client ip -> the primary network."""
        if slug and slug in nets:
            return nets[slug]
        if ip:
            hit = networks.for_client_ip(db, tenant.id, ip)
            if hit is not None:
                return hit
        return primary

    # A pushed network entry that the sensor could not reach reports its
    # state and touches no device row (invariant: never an empty scan).
    for entry in pushed:
        if not isinstance(entry, dict):
            continue
        row_net = nets.get(entry.get("slug") or "")
        if row_net is not None and entry.get("status"):
            row_net.status = str(entry["status"])

    # --- Sites -------------------------------------------------------------
    # `last_seen` stamps the sweep that reported the row, so a site the sensor
    # has stopped reporting can later be told apart from one that is checked
    # every sweep and simply keeps returning the same answer. Nothing is
    # deleted here: a site dropped from the config is still a site the owner
    # once watched, and removing the row would take its history with it.
    for s in last.get("sites") or []:
        name = s.get("name") or s.get("url") or ""
        _upsert(db, m.Site, {"tenant_id": tenant.id, "name": name},
                {"url": s.get("url", ""), "last": s, "last_seen": ts})

    # --- Hosts ---------------------------------------------------------------
    for h in last.get("ssh") or []:
        name = h.get("name") or ""
        _upsert(db, m.Host, {"tenant_id": tenant.id, "name": name},
                {"address": h.get("target") or "", "last": h, "last_seen": ts})

    # --- Devices (state.lan.known_macs, plus per-network known_macs maps) ---
    # `state["lan"]["networks"][slug]["known_macs"]` is Task 52's push shape;
    # the legacy flat `state["lan"]["known_macs"]` is the primary network's
    # map (invariant 1), so both are walked and each device row is keyed on
    # (tenant, network, mac) rather than (tenant, mac).
    lan_state = state.get("lan") or {}
    # Per-network maps FIRST, in slug order, then the legacy flat map: the
    # `device_nets` attribution below is first-wins, so a multi-homed MAC's
    # events and presence samples land on the network that explicitly named it
    # rather than on whichever map happened to be walked last (and the slug sort
    # keeps that deterministic across dict orderings).
    device_maps: list[tuple[str | None, dict]] = [
        (slug, block["known_macs"])
        for slug, block in sorted((lan_state.get("networks") or {}).items())
        if isinstance(block, dict) and block.get("known_macs")
    ]
    device_maps.append((None, lan_state.get("known_macs") or {}))
    device_nets: dict[str, str] = {}          # mac -> network_id, for events/presence
    flat_devices = [(net_slug, mac, d) for net_slug, known in device_maps for mac, d in known.items()]
    # A sensor that reports per-network blocks ALSO still carries the legacy
    # flat `known_macs` (task 52 aliases the primary slice by reference so a
    # pre-networks hub keeps working), so the same MAC arrives twice and both
    # entries resolve to the same network. Upserting both adds two rows for
    # one `(tenant, network, mac)` and the whole scan dies on that unique
    # constraint at commit -- which is how a live sensor went silent for an
    # hour while it was scanning perfectly well. First write wins, matching
    # `device_nets`' own first-wins rule right below.
    seen_dev: set[tuple[str, str]] = set()
    for net_slug, mac, d in flat_devices:
        net = _net_for(d.get("net") or net_slug, d.get("ip") or "")
        if (net.id, mac) in seen_dev:
            continue
        seen_dev.add((net.id, mac))
        device_nets.setdefault(mac, net.id)
        vals = {k: d.get(k) for k in DEVICE_COLS if k in d}
        vals["extra"] = {k: v for k, v in d.items() if k not in DEVICE_COLS}
        vals["kind"] = d.get("kind") or vals["extra"].get("kind") or "unknown"
        for k in ("trusted", "always_on", "quarantined"):
            vals[k] = bool(vals.get(k))
        vals["risk"] = int(vals.get("risk") or 0)
        for k in ("ip", "name", "vendor", "label", "presence", "first_seen", "last_seen"):
            vals[k] = vals.get(k) or ""
        vals["ports"] = vals.get("ports") or {}
        # The owner's decisions live on the hub row and the sensor only
        # mirrors them, so a push must never undo one: a sensor that has not
        # yet applied "trust this device" (it was asleep, the pull failed, the
        # edit named a network slug it does not know) still reports the old
        # value, and copying that back would flip the owner's click off
        # between one sweep and the next. Same shape as the stale-scan guard
        # on alerts above. The sensor's own "distrust" arrives as a hub edit
        # in the first place, never as a scan, so nothing legitimate is lost.
        existing = db.query(m.Device).filter_by(tenant_id=tenant.id, network_id=net.id, mac=mac).first()
        if existing is not None:
            if existing.trusted and not vals["trusted"]:
                vals["trusted"] = True
            if existing.always_on and not vals["always_on"]:
                vals["always_on"] = True
            if existing.label and not vals["label"]:
                vals["label"] = existing.label
        _upsert(db, m.Device, {"tenant_id": tenant.id, "network_id": net.id, "mac": mac}, vals)
        out["devices"] += 1

    # --- Cameras behind a recorder (Task 73) --------------------------------
    # These never appear in an ARP sweep -- they are on the recorder's own PoE
    # network -- so the sensor ASKS each registered recorder over its
    # documented HTTP API and pushes the answer as `last["recorders"]`. Each
    # channel becomes an ordinary `Device` row on the recorder's camera
    # `Network`, which is what makes Assets, Topology, Security and the
    # per-network counts work with no special case at all.
    if last.get("recorders"):
        got = recorders_mod.ingest(db, tenant, sensor, last["recorders"], ts)
        out["devices"] += got["cameras"]
        out["recorders"] = got["recorders"]
        # `nets` is read below for alert attribution: the camera networks were
        # created/renamed just now, so refresh it or a `nvr:` finding on a
        # brand-new recorder lands on the primary network instead.
        nets = networks.by_slug(db, tenant.id)

    # --- Heartbeats: the dead-man switches the sensor declares --------------
    #
    # `Heartbeat` is the hub's own row, and it has to exist: `/hb/<name>/
    # <token>` validates against it, the Security panel renders from it, and
    # the admin-only setup/rotate routes address it by name. Without this the
    # panel is empty and rotation 404s on a real deployment, while the sensor
    # happily reports the same heartbeats in `Scan.raw`.
    #
    # `token` is written on INSERT ONLY. On insert it copies the sensor's
    # token, so a cron line the owner already pasted keeps working. On every
    # later push it is left alone -- otherwise a rotation done here would be
    # silently reverted by the next scan, which is the whole point of being
    # able to rotate. `expect_minutes` follows the sensor freely: that is
    # configuration, not a credential.
    for hb_name, hb in (last.get("heartbeats") or {}).items():
        if not isinstance(hb, dict) or not hb_name:
            continue
        row = db.query(m.Heartbeat).filter_by(tenant_id=tenant.id, name=str(hb_name)[:64]).first()
        if row is None:
            token = hb_tokens.get(str(hb_name), "") or security.new_token()
            row = m.Heartbeat(tenant_id=tenant.id, name=str(hb_name)[:64], token=token[:64])
            db.add(row)
        try:
            row.expect_minutes = int(hb.get("every_min") or row.expect_minutes or 1500)
        except (TypeError, ValueError):
            pass
        out["heartbeats"] += 1

    # --- Alerts: state.alerts_db is the lifecycle store, last.alerts adds
    #     fix/why/confidence context for the same key. -----------------------
    # `alert_deliveries` collects (delivery_kind, Alert row) for every alert
    # that transitioned to `open` in this ingest (a brand-new fire, or a
    # re-fire while already open -- `count` increased) or that just
    # resolved. Dispatched via `registry.deliver_background` AFTER this
    # function's own `db.commit()` below, so a slow/failing integration can
    # never roll back the scan itself, and never blocks the request either
    # (see ingest.py module docstring / task-40 brief + fix round 1).
    alert_deliveries: list[tuple[str, "m.Alert"]] = []
    fixmap = {a["key"]: a for a in (last.get("alerts") or []) if "key" in a}
    # Keys the owner acked/snoozed/resolved/reopened AFTER this scan captured
    # its data, and which the sensor therefore could not have known about.
    #
    # A scan takes minutes to collect and is ingested on arrival, so `ts` --
    # the moment the sensor sampled the network -- can easily predate a click
    # made while it was still running. Writing that stale snapshot through
    # would undo the click: resolve an alert, watch it come straight back
    # open, which is exactly what the owner reported. The sensor learns of
    # the op from the mirrored `/api/alert` edit and reports it correctly on
    # its next scan; until then the hub's own status is the newer truth.
    scan_taken_at = parse_ts(ts)
    stale_for: set[str] = set()
    if scan_taken_at is not None:
        stale_for = {
            e.body.get("key")
            for e in db.query(m.Edit).filter(m.Edit.tenant_id == tenant.id,
                                              m.Edit.path == "/api/alert",
                                              m.Edit.ts > scan_taken_at).all()
            if isinstance(e.body, dict) and e.body.get("key")
        }
    for raw_key, a in (state.get("alerts_db") or {}).items():
        cur = fixmap.get(raw_key, {})
        # LAN findings are namespaced per network (`lan:new:<mac>` ->
        # `lan:<slug>:new:<mac>`, spec S3.1 / invariant 2) so the same MAC on
        # two networks is two alerts rather than one that keeps flipping. The
        # sensor still emits the legacy shape until Task 52 ships, so the
        # rewrite happens HERE as well as in 0007_networks -- otherwise the
        # next push after the migration re-fires every LAN alert as a new row
        # and leaves the rewritten ones stale-open.
        alert_net = _net_for(a.get("net") or cur.get("net"),
                              (a.get("ip") or cur.get("ip") or ""))
        key = networks.lan_alert_key(raw_key, alert_net.slug)
        prior = db.query(m.Alert).filter_by(tenant_id=tenant.id, key=key).first()
        prior_status, prior_count, prior_sev = (
            (prior.status, prior.count, prior.sev) if prior else (None, 0, None))
        incoming_sev = a.get("sev") or cur.get("sev") or "INFO"
        worsened = bool(prior and prior.status in ("snoozed", "acked") and raw_key not in stale_for and key not in stale_for
                        and {"INFO":0,"WARN":1,"CRIT":2}.get(incoming_sev,0) > {"INFO":0,"WARN":1,"CRIT":2}.get(prior.sev,0))
        row = _upsert(db, m.Alert, {"tenant_id": tenant.id, "key": key}, {
            "network_id": alert_net.id,
            "sev": a.get("sev") or cur.get("sev") or "INFO",
            # Sensor-written prose, and the single choke point for it: every
            # later reader (reports, `/r/<token>` share links, push, Discord,
            # Telegram, PagerDuty, the chat DATA blob, the public API) reads
            # the stored row. A sensor build older than the fix in
            # `sensor/guardian_sensor/alerts.py` still sends a heartbeat ping
            # URL with its token attached, so it is stripped on the way in
            # rather than in each of those places.
            "msg": security.redact_prose(a.get("msg") or cur.get("msg") or key),
            "why": security.redact_prose(cur.get("why") or a.get("why") or ""),
            "evidence": cur.get("evidence") or {},
            "confidence": str(cur.get("confidence") or a.get("confidence") or ""),
            "first_seen": a.get("first_seen") or ts,
            "last_seen": a.get("last_seen") or ts,
            "count": int(a.get("count") or 1),
            "status": (prior_status if prior is not None
                       and (raw_key in stale_for or key in stale_for)
                       else a.get("state") or "open"),
            "resolved_by": a.get("resolved_by") or "",
            "snoozed_until": a.get("snoozed_until"),
            "fix": security.redact_prose(cur.get("fix") or ""),
        })
        if worsened:
            row.status, row.snoozed_until = "open", None
            db.add(m.Edit(tenant_id=tenant.id, who="system", path="/api/alert", body={"key":networks.canonical_lan_key(key),"action":"reopen","hours":0}))
        out["alerts"] += 1
        # What actually reaches a channel (Slack, ...). ONE rule now, not two.
        #
        # Onset, escalation, and clearing only -- never a re-push. A new alert
        # is sent once; one that gets WORSE while open (INFO->WARN,
        # WARN->CRIT) is sent once at the step up; a resolve is sent once. The
        # sensor bumps `count` every scan a condition persists, and that must
        # NOT re-notify.
        #
        # The severity floor that used to sit here is GONE. It hard-coded
        # "INFO is advisory, never push it" on top of each channel's own
        # `routes` toggles -- so a card with INFO ticked sent nothing, the
        # checkbox was decoration, and the owner did not get told his phone
        # was flapping (`lan:flapping:` is raised at INFO) despite having
        # asked for INFO. A per-channel switch that silently does nothing is
        # worse than no switch. `deliver()` still filters on `routes[sev]`,
        # which is where that decision belongs and where the owner can see it.
        newly_open = prior_status != "open"
        escalated = (prior_status == "open"
                     and _PUSH_RANK.get(row.sev, 0) > _PUSH_RANK.get(prior_sev, 0))
        if row.status == "open" and (newly_open or escalated):
            alert_deliveries.append(("alert", row))
        elif row.status == "resolved" and prior_status != "resolved":
            alert_deliveries.append(("resolve", row))

    # --- Incidents: merge state.incidents (dict values, keyed on an
    #     internal composite string) with last.incidents (list) by their
    #     shared `id` field -- NOT by the state dict's own key. -------------
    by_id: dict[str, dict] = {}
    for inc in (state.get("incidents") or {}).values():
        if inc.get("id") is None:
            continue
        by_id[str(inc["id"])] = dict(inc)
    for inc in last.get("incidents") or []:
        if inc.get("id") is None:
            continue
        iid = str(inc["id"])
        by_id[iid] = {**by_id.get(iid, {}), **inc}
    # The live set, per the docstring: only `last.incidents` says what is
    # still open. `"incidents" in last` distinguishes "no incidents right
    # now" (an empty list -> close everything still open) from a partial or
    # older payload that simply doesn't carry the key (-> close nothing).
    live_ids: "set[str] | None" = None
    raw_live = last.get("incidents")
    if isinstance(raw_live, list):
        # `isinstance`, not `in`: a history line's `incidents` is an INT
        # count (see the docstring's history-line shape), and only the real
        # `last.json` list can be read as the live set.
        live_ids = {str(inc["id"]) for inc in raw_live if inc.get("id") is not None}

    sensor_id = sensor.id if sensor else None
    for iid, inc in by_id.items():
        prior_inc = db.query(m.Incident).filter_by(tenant_id=tenant.id, sensor_id=sensor_id, iid=iid).first()
        # `severity` is the emitter's key; `sev` only ever appears in
        # hand-written payloads, and is accepted so an older import keeps
        # working. Same for `started` vs the state dict's `opened`.
        sev = inc.get("severity") or inc.get("sev") or "WARN"
        opened = (inc.get("opened") or inc.get("started")
                   or (prior_inc.opened if prior_inc else None) or ts)
        if live_ids is None:
            closed = inc.get("closed") if "closed" in inc else (prior_inc.closed if prior_inc else None)
        elif iid in live_ids:
            closed = None            # back on the live list -> reopened
        else:
            closed = inc.get("closed") or (prior_inc.closed if prior_inc else None) or ts
        _upsert(db, m.Incident, {"tenant_id": tenant.id, "sensor_id": sensor_id, "iid": iid}, {
            "entity": inc.get("entity") or "",
            "sev": sev,
            "story": _story_text(inc.get("story")),
            "actions": inc.get("actions") or [],
            "alert_keys": inc.get("alerts") or inc.get("keys") or [],
            "opened": opened,
            "closed": closed,
        })
        out["incidents"] += 1

    # Close-on-disappearance runs over the STORED rows, not `by_id`: an
    # incident that has gone quiet is absent from both `last.incidents` and
    # (eventually) `state.incidents`, so the merge above never visits it.
    # The sensor emits no `closed` key at all, so leaving the live list is
    # the only signal there is -- and `live_ids is None` (a payload with no
    # incidents list) closes nothing.
    #
    # Scoped per SENSOR as of 0007_networks (`Incident.sensor_id` + a
    # (tenant_id, sensor_id, iid) unique): each sensor numbers its own
    # incidents from `state.incident_seq` starting at 100, so a tenant with
    # two sensors used to have them overwrite each other on `iid` collision,
    # and one sensor's push would close the other's live incidents. This pass
    # now only ever closes incidents reported by the sensor that is pushing.
    if live_ids is not None:
        for row_inc in (db.query(m.Incident)
                        .filter(m.Incident.tenant_id == tenant.id,
                                m.Incident.sensor_id == sensor_id,
                                m.Incident.closed.is_(None))
                        .all()):
            if row_inc.iid not in live_ids:
                row_inc.closed = ts

    # --- Recommendations: last.recommendations is the source list; merge
    #     state.rec_decisions[key] on top when present. -----------------------
    #
    # The sensor sends `action: null` for a recommendation it has no
    # concrete repair for -- some of those really cannot be automated (a
    # DNS record at a registrar Vault has no login to), but some ARE
    # runnable through a fix the hub already knows about from a different
    # angle: `remedy.fix_for_key` is the same table `remedy.resolve` uses to
    # decide whether an alert's own Fix button appears, ported once from
    # `sensor/guardian_sensor/fix.py` and drift-checked against it
    # (`tests/test_remedy.py`). Reading it here, rather than teaching
    # `alerts.py::recommendations()` a second copy of the same mapping, is
    # the whole point: one table, and a recommendation and an alert that
    # share a key (they always do -- see the module docstring) always agree
    # on what repairs them. Only fills in a gap the sensor left `None`;
    # never overrides an action the sensor already sent.
    from . import remedy as remedy_mod

    def _resolved_action(key: str, action):
        if action:
            return action
        hit = remedy_mod.fix_for_key(key)
        if not hit:
            return None
        act, host, arg = hit
        out = {"host": host, "action": act}
        if arg:
            out["arg"] = arg
        return out

    decisions = state.get("rec_decisions") or {}
    # Generic advice the hub knows has been dealt with. `adv:router-admin`
    # ("change the router admin password and disable remote administration")
    # is standing advice the sensor emits from its own findings; it cannot see
    # that the owner has since pressed Vault's own disable-remote-admin
    # button, because that outcome lives on the hub's `Router` row. Dropping
    # the key HERE, at ingest, is deliberate: resolving the stored row instead
    # would flap, since the sensor re-emits the advice every sweep. It is also
    # subtracted from `live_keys` below so an already-stored copy is cleared by
    # the same sweep rather than lingering.
    suppressed = _suppressed_rec_keys(db, tenant)
    for r in last.get("recommendations") or []:
        key = r.get("key")
        if not key or key in suppressed:
            continue
        d = decisions.get(key) or {}
        decision = d.get("decision") if d.get("decision") is not None else r.get("decision")
        status = d.get("status") if d.get("status") is not None else r.get("status")
        values = {
            "priority": r.get("priority") or "low",
            "title": r.get("text") or r.get("title") or key,
            "why": r.get("why") or "",
            "action": _resolved_action(key, r.get("action")),
            "last_seen": ts,
        }
        # The hub owns a recommendation's answer, not the sensor. A decision
        # is made on the dashboard (`routers/write.py:rec_decide`), which
        # records it and *then* mirrors it down as an `/api/rec` edit; the
        # sensor replays that edit into `state.rec_decisions` and echoes it
        # back here on its next scan. So the sensor's copy is always at best
        # one round-trip stale, and blank in two ordinary cases: before it has
        # pulled the edit at all, and after it has (`sync.py` stores the
        # decision with a hardcoded empty `status`).
        #
        # Writing those blanks straight through wiped the answer roughly six
        # minutes after every click: "Yes" queued a real job and reported
        # "applied", then the next scan reset the row to undecided with no
        # status, so the owner saw a recommendation they had just actioned
        # sitting untouched and concluded nothing had run. Only a value the
        # sensor actually holds may overwrite one the hub already has --
        # which still lets the sensor report the outcomes it alone knows
        # (`fix.py` sets "done"/"failed" once an accepted fix has run).
        for field, incoming in (("decision", decision), ("decided_by", d.get("by")), ("status", status)):
            if incoming:
                values[field] = incoming
        _upsert(db, m.Recommendation, {"tenant_id": tenant.id, "key": key}, values)
        out["recs"] += 1

    # --- Recommendations the sensor has STOPPED suggesting. ------------------
    #
    # A recommendation is a statement about the network right now, derived
    # fresh from the current findings every sweep -- `alerts.recommendations()`
    # only emits one while its underlying alert is still open. An alert that
    # stops firing is auto-resolved (`runner.py` closes every key missing from
    # the current sweep), but recommendations had no equivalent here: this
    # function upserted whatever arrived and never removed anything, so a
    # suggestion outlived the condition that produced it, forever.
    #
    # The owner hit exactly that: "System DNS resolver is failing" kept asking
    # to be dealt with two days after the alert behind it had resolved, and
    # could not be cleared from the screen because answering it only moved it
    # to `needs-you`. The hub was holding 18 recommendations while the sensor
    # was emitting 5.
    #
    # Guarded on a non-empty list on purpose: a payload carrying no
    # recommendations at all is far more likely to be a partial or malformed
    # push than a genuine "nothing to suggest", and wiping the table on one of
    # those would be worse than keeping a stale row.
    # A DECLINED one is kept even when its cause is gone: "No thanks" hides a
    # recommendation rather than deleting it, and `_rec_actions` offers "Bring
    # this back" to undo that. Deleting the row would take the undo with it.
    live_keys = {r.get("key") for r in (last.get("recommendations") or []) if r.get("key")} - suppressed
    if live_keys:
        gone = (db.query(m.Recommendation)
                  .filter(m.Recommendation.tenant_id == tenant.id,
                          m.Recommendation.key.notin_(live_keys),
                          m.Recommendation.decision != "no")
                  .delete(synchronize_session=False))
        if gone:
            out["recs_cleared"] = gone

    # --- Fixes (remediation.jsonl tail); skip a replayed row that already
    #     exists for (tenant_id, ts, host, action, arg). ------------------------
    for f in p.get("fixes") or []:
        f_ts, f_host, f_action, f_arg = f.get("ts") or ts, f.get("host") or "-", f.get("action") or "", f.get("arg")
        if db.query(m.Fix).filter_by(tenant_id=tenant.id, ts=f_ts, host=f_host, action=f_action, arg=f_arg).first():
            continue
        db.add(m.Fix(
            sensor_id=sensor.id if sensor else None,
            tenant_id=tenant.id, ts=f_ts, host=f_host,
            action=f_action, label=f.get("label") or "", arg=f_arg,
            by=f.get("by") or "auto", ok=bool(f.get("ok")),
            out=str(f.get("out") or "")[:4000], reboot_needed=bool(f.get("reboot")),
        ))
        out["fixes"] += 1

    # --- Events (events.jsonl tail); skip a replayed row that already exists
    #     for (tenant_id, ts, type, entity) with the same payload port. --------
    device_events: list[dict] = []
    for e in p.get("events") or []:
        e_ts, e_type = e.get("ts") or ts, e.get("type") or "event"
        e_entity, e_port = e.get("mac") or e.get("host") or e.get("site") or "", e.get("port")
        e_net_id = (device_nets.get(e_entity)
                     or _net_for(e.get("net"), e.get("ip") or "").id)
        existing = db.query(m.Event).filter_by(tenant_id=tenant.id, network_id=e_net_id,
                                               ts=e_ts, type=e_type, entity=e_entity).all()
        if any((row.payload or {}).get("port") == e_port for row in existing):
            continue
        db.add(m.Event(tenant_id=tenant.id, network_id=e_net_id, ts=e_ts, type=e_type,
                        entity=e_entity, payload=e))
        out["events"] += 1
        if e_type in ("join", "leave", "port_open", "port_closed"):
            device_events.append({"mac": e_entity, "event": e_type, "ip": e.get("ip") or "",
                                   "label": e.get("label") or ""})

    # --- Metrics from history lines (dict-keyed sites/hosts); skip a
    #     replayed row that already exists for (tenant_id, ts, name, entity).
    #     A single malformed/unexpected-shaped line is skipped (counted in
    #     out["skipped"]) rather than failing the whole ingest -- real
    #     sensor pushes must never 500 because of one bad history row.
    #
    #     Dedup used to be one `Metric`/`PresenceSample` query per row, which
    #     is fine for a live push (a handful of lines) but pathological for a
    #     bulk replay/seed (1000s of lines): pre-load every existing key in
    #     this batch's time range ONCE, then dedup in Python.
    history_lines = p.get("history") or []
    history_bounds = _ts_bounds([
        (h.get("ts") or ts) if isinstance(h, dict) else None for h in history_lines
    ])
    existing_metric_keys: set[tuple[str, str, str]] = set()
    existing_presence_keys: set[tuple[str, str]] = set()
    if history_bounds:
        lo, hi = history_bounds
        existing_metric_keys = {
            (name, entity, mts) for name, entity, mts in
            db.query(m.Metric.name, m.Metric.entity, m.Metric.ts)
            .filter(m.Metric.tenant_id == tenant.id, m.Metric.ts >= lo, m.Metric.ts <= hi).all()
        }
        existing_presence_keys = {
            (pts, mac, nid) for pts, mac, nid in
            db.query(m.PresenceSample.ts, m.PresenceSample.mac, m.PresenceSample.network_id)
            .filter(m.PresenceSample.tenant_id == tenant.id, m.PresenceSample.ts >= lo, m.PresenceSample.ts <= hi).all()
        }

    presence_seen: set[tuple[str, str, str]] = set()
    for h in history_lines:
        try:
            hts = h.get("ts") or ts

            def met(name, entity, value):
                if value is None:
                    return
                key = (name, entity, hts)
                if key in existing_metric_keys:
                    return
                existing_metric_keys.add(key)
                db.add(m.Metric(tenant_id=tenant.id, ts=hts, name=name, entity=entity, value=float(value)))
                out["metrics"] += 1

            for name, sv in (h.get("sites") or {}).items():
                if not sv:      # a site probe that failed entirely comes back as None
                    continue
                lat = sv.get("lat")
                if lat is not None:
                    met("latency_ms", name, lat * 1000)
                if sv.get("ok") is not None:
                    met("up", name, 1 if sv.get("ok") else 0)
                met("cert_days", name, sv.get("cert"))
            for name, hv in (h.get("hosts") or {}).items():
                if not hv:      # a host probe that failed entirely comes back as None
                    continue
                met("disk_pct", name, hv.get("disk"))
                met("mem_pct", name, hv.get("mem"))
                met("load", name, hv.get("load"))
                met("failed_ssh", name, hv.get("failed"))
            met("live_devices", "", h.get("lan"))

            # dns_ms (real shape): {"system": 24, "cloudflare": 25, "google": 32}
            # -- the resolver may also be None (that probe failed) rather than a
            # number. `dns_ms` itself is the "system" resolver's value (falling
            # back to the bare number when it isn't a dict at all); every
            # resolver additionally gets its own dns_ms_<resolver> metric.
            dns_ms = h.get("dns_ms")
            if isinstance(dns_ms, dict):
                met("dns_ms", "", dns_ms.get("system"))
                for resolver, val in dns_ms.items():
                    met(f"dns_ms_{resolver}", "", val)
            else:
                met("dns_ms", "", dns_ms)

            # history live_macs -> presence samples (one row per live mac per
            # line): the raw material for every presence/screen-time view.
            # Guarded both against an existing row (re-ingest) and against a
            # repeat within this batch -- SessionLocal is autoflush=False, so
            # a pending add is invisible to the query and the
            # (tenant, ts, mac) unique constraint would only blow up at
            # commit.
            # `live_macs` may be a flat list (one network) or, from Task 52's
            # sensor, {slug: [macs]}. Either way each sample carries the
            # network it was seen on: the same laptop live on the Wi-Fi and in
            # the lab at one `ts` is two samples (see
            # `payloads/presence.screen_time`'s docstring for how the
            # per-tenant totals de-duplicate that).
            live_macs = h.get("live_macs") or []
            if isinstance(live_macs, dict):
                pres_pairs = [(slug, mac) for slug, macs in live_macs.items() for mac in (macs or [])]
            else:
                pres_pairs = [(None, mac) for mac in live_macs]
            for pres_slug, mac in pres_pairs:
                nid = (nets[pres_slug].id if pres_slug and pres_slug in nets
                        else device_nets.get(mac) or primary.id)
                if (hts, mac, nid) in presence_seen:
                    continue
                presence_seen.add((hts, mac, nid))
                if (hts, mac, nid) in existing_presence_keys:
                    continue
                existing_presence_keys.add((hts, mac, nid))
                db.add(m.PresenceSample(tenant_id=tenant.id, network_id=nid, ts=hts, mac=mac))
                out["presence"] += 1

            met("score", "", h.get("score"))
            sp = h.get("speed") or {}
            met("speed_down", "", sp.get("down"))
            met("speed_up", "", sp.get("up"))

            # wan_path (real shape): {"cloudflare": {"avg": 10.3, "loss": 0.0},
            # "google": {...}, "safehome": {...}} -- may also be None/absent.
            wan_path = h.get("wan_path") or {}
            if isinstance(wan_path, dict):
                for path_name, wp in wan_path.items():
                    if not isinstance(wp, dict):
                        continue
                    met(f"path_ms_{path_name}", "", wp.get("avg"))
                    met(f"path_loss_{path_name}", "", wp.get("loss"))
        except Exception as e:
            out["skipped"] += 1
            log.warning("ingest: skipped history line %s: %s", ts, e)

    # --- DNS queries; skip a replayed row that already exists for
    #     (tenant_id, ts, client, name, qtype). Same range-preload trick as
    #     the metrics/presence dedup above -- a demo/bulk seed pushes 1000s
    #     of DNS rows in one call. ------------------------------------------
    dns_queries = p.get("dns_queries") or []
    dns_bounds = _ts_bounds([
        (q.get("ts") or ts) if isinstance(q, dict) else None for q in dns_queries
    ])
    existing_dns_keys: set[tuple[str, str, str, str]] = set()
    if dns_bounds:
        lo, hi = dns_bounds
        existing_dns_keys = {
            (qts, client, name, qtype, nid) for qts, client, name, qtype, nid in
            db.query(m.DnsQuery.ts, m.DnsQuery.client, m.DnsQuery.name, m.DnsQuery.qtype,
                     m.DnsQuery.network_id)
            .filter(m.DnsQuery.tenant_id == tenant.id, m.DnsQuery.ts >= lo, m.DnsQuery.ts <= hi).all()
        }
    dns_frames: list[DnsStreamRow] = []
    for q in dns_queries:
        q_ts, q_client = q.get("ts") or ts, q.get("client") or ""
        q_name, q_qtype = q.get("name") or "", str(q.get("type") or "")
        q_net_id = _net_for(q.get("net"), q_client).id
        key = (q_ts, q_client, q_name, q_qtype, q_net_id)
        if key in existing_dns_keys:
            continue
        existing_dns_keys.add(key)
        mac = ip2mac.get(q_client)
        q_blocked, q_list, q_ms = bool(q.get("blocked")), q.get("list"), int(q.get("ms") or 0)
        db.add(m.DnsQuery(
            tenant_id=tenant.id, network_id=q_net_id, ts=q_ts, client=q_client,
            name=q_name, qtype=q_qtype,
            blocked=q_blocked, list=q_list, ms=q_ms,
            mac=mac,
        ))
        out["dns"] += 1
        dns_frames.append(DnsStreamRow(ts=q_ts, mac=mac, client=q_client, name=q_name,
                                       qtype=q_qtype, blocked=q_blocked, list=q_list, ms=q_ms))

    # --- Flows: dedup on the full key, one IN query per payload, per-row
    #     validation so one bad row costs one row and never the scan. -------
    _MAC = re.compile(r"^([0-9a-f]{2}:){5}[0-9a-f]{2}$")
    fl_bounds = _ts_bounds([(f.get("ts") or ts) if isinstance(f, dict) else None for f in flow_rows])
    existing_fl: set[tuple] = set()
    new_flows: list[m.Flow] = []
    if fl_bounds:
        lo, hi = fl_bounds
        existing_fl = set(db.query(m.Flow.ts, m.Flow.mac, m.Flow.peer_ip, m.Flow.peer_port, m.Flow.proto, m.Flow.source)
                          .filter(m.Flow.tenant_id == tenant.id, m.Flow.ts >= lo, m.Flow.ts <= hi).all())
    for f in flow_rows:
        try:
            mac = str(f.get("mac") or "").lower()
            if not _MAC.match(mac): raise ValueError("mac")
            vals = {k: int(f.get(k) or 0) for k in ("bytes_in", "bytes_out", "pkts_in", "pkts_out", "peer_port")}
            if any(v < 0 for v in vals.values()): raise ValueError("negative")
            proto = str(f.get("proto") or "other")[:8]
            source = "router" if f.get("source") == "router" else "mirror"
            f_ts, f_ip, peer_ip = str(f.get("ts") or ts), str(f.get("ip") or "")[:45], str(f.get("peer_ip") or "")[:45]
        except (ValueError, TypeError, AttributeError):
            out["flows_rejected"] += 1
            continue
        key = (f_ts, mac, peer_ip, vals["peer_port"], proto, source)
        if key in existing_fl:
            continue
        existing_fl.add(key)
        hints = f.get("hints") if isinstance(f.get("hints"), dict) else {}
        hints = {k: hints[k] for k in ("sni", "dhcp", "mdns") if k in hints}
        flow = m.Flow(tenant_id=tenant.id, network_id=_net_for(f.get("net"), f_ip).id, ts=f_ts, mac=mac, ip=f_ip,
                      peer_ip=peer_ip, peer_port=vals["peer_port"], proto=proto,
                      bytes_in=vals["bytes_in"], bytes_out=vals["bytes_out"], pkts_in=vals["pkts_in"], pkts_out=vals["pkts_out"],
                      peer_name=str(f.get("peer_name") or "")[:255], hints=hints, source=source)
        db.add(flow)
        new_flows.append(flow)
        out["flows"] += 1

    if sensor:
        sensor.last_seen = _now()
        sensor.status = "active"
        # Task 70: the sensor host's own load, for the sub-header. Written only
        # when the sensor actually reported a figure -- a sensor that predates
        # `home.host_load` (or one on a platform without a load average) sends
        # nothing, and the stored value must stay NULL rather than be reset to
        # a zero the UI would draw as an idle machine.
        selfrep = last.get("self") or {}
        if isinstance(selfrep.get("load1"), (int, float)):
            sensor.load1 = float(selfrep["load1"])
        if isinstance(selfrep.get("cores"), int) and selfrep["cores"] > 0:
            sensor.cores = int(selfrep["cores"])

    db.commit()

    # dns/device frames go out before the "scan" summary frame: a socket
    # that only cares about one ingested row (a test, or a UI watching a
    # single device) must not have to skip past a "scan" frame it read as
    # the very next message to find it.
    if dns_frames:
        macs = {f.mac for f in dns_frames if f.mac}
        names: dict[str, str] = {}
        if macs:
            names = {d.mac: (d.label or d.name) for d in
                     db.query(m.Device).filter(m.Device.tenant_id == tenant.id, m.Device.mac.in_(macs)).all()}
        for f in dns_frames:
            f.device = names.get(f.mac, "") if f.mac else ""
            _publish_dns_throttled(tenant.id, f)

    if new_flows and bus.has_subscribers(tenant.id):
        from .payloads import traffic as traffic_payload
        names = traffic_payload.device_names(db, tenant.id, {f.mac for f in new_flows})
        for f in new_flows:
            _publish_throttled(tenant.id, "flow", traffic_payload.stream_row(f, names.get(f.mac, "")))

    for dev_evt in device_events:
        bus.publish(tenant.id, "device", dev_evt)

    base_url = settings.base_url.rstrip("/")
    unreadable_recorders = {str(r.get("id")) for r in last.get("recorders") or [] if isinstance(r, dict) and not r.get("ok") and r.get("id")}
    for delivery_kind, row in (alert_deliveries if deliver else []):
        # Only explicit recorder/channel dependencies qualify. Keep child
        # findings in the incident evidence; do not send contradictory
        # camera-down/recovered notices while their observer is unavailable.
        parts = row.key.split(":")
        if len(parts) >= 3 and parts[0] == "nvr" and parts[1] in ("cam-offline", "cam-gone", "count") and parts[2] in unreadable_recorders:
            continue
        msg = registry.Message(
            title=f"Vault: {row.sev} alert" if delivery_kind == "alert" else "Vault: alert resolved",
            body=row.msg,
            severity=row.sev,
            url=f"{base_url}/inbox?row={row.key}",
            actions=[],
            kind="resolve" if delivery_kind == "resolve" else "alert",
            dedup_key=row.key,
        )
        registry.deliver_background(tenant.id, msg)
        # ...and a copy into the operator's own channel, so he sees every
        # customer's alerts without joining every customer's Slack. Strictly
        # a mirror: it runs after the real delivery is already dispatched and
        # cannot affect it.
        adminnotify.mirror_alert(
            "resolve" if delivery_kind == "resolve" else "alert",
            row.sev,
            (f"Resolved: {row.msg}" if delivery_kind == "resolve"
             else f"{row.sev}: {row.msg}"),
            f"<{base_url}/inbox?row={row.key}|open in Vault>",
            tenant=tenant.slug or tenant.name or tenant.id[:8],
        )

    score = last.get("score") or {}
    bus.publish(tenant.id, "scan", {
        "ts": ts, "score": score.get("total"), "grade": score.get("grade"),
        "devices": out["devices"], "alerts": out["alerts"],
    })

    from .verification import reconcile
    reconcile(db, tenant, sensor, {**last, "ts": ts})

    # Open, extend or close the WAN outage this sweep implies. Uses the
    # scan's OWN timestamp and never `now()`, so a 30-day backfill lands
    # outages where they actually happened and re-seeding the demo stays
    # idempotent -- `sla.record` dedups on `(tenant, started_at)` for the
    # same reason the rows above do.
    from . import sla
    sla.record(db, tenant, {**last, "ts": ts})
    return out
