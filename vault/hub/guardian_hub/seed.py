"""`guardian seed-demo`: builds (or refreshes) the `demo` tenant so every
dashboard tab has something real to render, and wires the fixed 6-digit
demo sign-in code (`Tenant.settings["demo_code"]`, checked in
`routers.auth.verify`) that lets a demo user in without an emailed OTP.

Everything synthesized here is deterministic (seeded RNG) so re-running
`seed_demo` against the same fixtures produces byte-identical timestamps,
which lets `ingest.store_scan`'s own (tenant, ts, ...) dedup keys make the
whole operation idempotent: a second run adds zero rows. That matters
because the live `demo` tenant on the VPS was imported from Nexus and this
must be safe to run against it repeatedly without piling up duplicate scans,
history, events or DNS rows -- and without touching the users already on
that tenant.

The fixtures this reads are a real capture of the owner's own network, and
the `demo` tenant's sign-in is public -- so everything is passed through
`demoscrub.scrub` first, which rewrites it into the fictional Northwind
Dental network. See that module for the mapping rules and why the scrub is
applied here (at load) rather than baked into the fixture files.
"""

import datetime as dt
import json
import pathlib
import random

from sqlalchemy.orm import Session as DbSession

from . import demoscrub, models as m, networks
from . import recorders as recorders_mod
from .accounts import upsert_membership, upsert_user
from .ingest import store_scan
from .scans import parse_ts

DEMO_CODE = "424242"

# hub/guardian_hub/seed.py -> hub/tests/fixtures
_FIXTURES = pathlib.Path(__file__).resolve().parent.parent / "tests" / "fixtures"

# Tables that carry a tenant_id and are safe to wipe for --reset: ingest
# output plus Device/Sensor/Site/Host. Deliberately excludes User and
# Membership -- reset must keep whoever already has an account on the
# tenant, per the "safe to run against the live demo tenant" requirement.
#: Everything `reset_tenant` deletes. **A model that carries `network_id`
#: must be on this list if `Network` is**, because `Network` is recreated with
#: new ids on every re-seed and anything left behind points at nothing.
#: `Flow` and `FlowRollup` were missing when the traffic feature arrived, so
#: the ten-minute demo refresh orphaned each run's flows and seeded new ones
#: beside them -- 405,479 dead rows against 430 live ones on production,
#: invisible per network and scanned on every all-networks Traffic load.
#: `tests/test_demo_refresh.py` now asserts no flow outlives its network.
_RESET_MODELS = (
    m.PresenceSample, m.DnsQuery, m.Metric, m.Event, m.Fix,
    m.Alert, m.Incident, m.Recommendation, m.Scan, m.Device,
    m.Site, m.Host, m.Sensor, m.Queue, m.Report, m.Integration,
    m.ActToken, m.DnsRule, m.Edit, m.Flow, m.FlowRollup, m.Network,
)


def reset_tenant(db: DbSession, tenant_id: str, keep_queue_ids: "set[str] | None" = None) -> None:
    """Delete every ingest-derived row for `tenant_id` (but never the
    Tenant, User or Membership rows) so `seed_demo` can rebuild it clean.

    `keep_queue_ids` spares specific `Queue` rows. The demo tenant's own
    "Scan now" IS a refresh, so the job representing that scan was deleted
    halfway through running it: for about ten seconds the Activity page showed
    no job at all, and then the recreated row carried a brand-new timestamp and
    reported that the scan had taken no time. A job cannot honestly report on
    itself if running it destroys it.
    """
    keep = keep_queue_ids or set()
    for model in _RESET_MODELS:
        q = db.query(model).filter_by(tenant_id=tenant_id)
        if model is m.Queue and keep:
            q = q.filter(~m.Queue.id.in_(keep))
        q.delete(synchronize_session=False)
    db.commit()


def _load_fixture(fixtures: pathlib.Path, name: str) -> dict:
    p = fixtures / name
    return json.loads(p.read_text()) if p.exists() else {}


def _classify(name: str) -> str:
    """Best-effort device duty-cycle group from whatever human-readable name
    the sensor has for it (`last.lan.names[ip]`). Anything not obviously a
    phone or a TV is treated as always-on -- the safe default for routers,
    extenders, cameras, appliances and the like."""
    n = (name or "").lower()
    if "tv" in n:
        return "tv"
    if "iphone" in n or "ipad" in n or "phone" in n:
        return "phone"
    return "always_on"


def _duty_ok(kind: str, hour: int) -> bool:
    if kind == "phone":
        return 7 <= hour < 23
    if kind == "tv":
        return 18 <= hour < 23
    return True


def synth_history(last: dict, days: int = 30, step_minutes: int = 30) -> list[dict]:
    """Pure, deterministic: one history line every `step_minutes` for `days`
    days, ending at `last["ts"]`. Derives sites/devices from `last` alone
    (no db access) so it is trivially unit-testable.

    - site latency (`lat`) is the fixture's `latency_s` jittered +/-25%
    - `score` random-walks between 78 and 92
    - one site flips to `ok=False` for two consecutive lines on day 3 and
      day 11 (0-indexed days 2 and 10)
    - `live_macs` follows a per-device duty cycle: always-on devices are
      always present, phones 07:00-23:00, TVs 18:00-23:00
    - `speed` is populated every 12h; every line carries `dns_ms`
    """
    anchor = dt.datetime.fromisoformat(last["ts"])
    step = dt.timedelta(minutes=step_minutes)
    n = (days * 24 * 60) // step_minutes
    start = anchor - step * (n - 1)

    sites = last.get("sites") or []
    ssh = last.get("ssh") or []
    lan = last.get("lan") or {}
    dev_names = lan.get("names") or {}
    ip2mac = lan.get("devices") or {}

    macs_by_kind: dict[str, list[str]] = {"phone": [], "tv": [], "always_on": []}
    for ip, mac in ip2mac.items():
        macs_by_kind[_classify(dev_names.get(ip, ""))].append(mac)

    base_score = int((last.get("score") or {}).get("total") or 85)
    base_score = min(92, max(78, base_score))

    dns = last.get("dns") or {}
    base_dns_ms = float((dns.get("system") or {}).get("ms") or 40)

    flip_site = (sites[0].get("name") or sites[0].get("url") or "") if sites else None
    flip_days = {2, 10}   # "day 3" / "day 11" are 1-indexed in the brief

    lines_per_day = (24 * 60) // step_minutes
    speed_step = max(1, (12 * 60) // step_minutes)

    rng = random.Random(20260909)
    rows = []
    score = base_score
    for i in range(n):
        ts = start + step * i
        day_idx = i // lines_per_day
        idx_in_day = i % lines_per_day
        hour = ts.hour

        score = min(92, max(78, score + rng.randint(-2, 2)))

        site_rows = {}
        for s in sites:
            name = s.get("name") or s.get("url") or ""
            lat = s.get("latency_s")
            ok = s.get("ok", True)
            if lat is not None:
                lat = lat * (1 + rng.uniform(-0.25, 0.25))
            if name == flip_site and day_idx in flip_days and idx_in_day in (0, 1):
                ok = False
            site_rows[name] = {"ok": ok, "lat": lat, "cert": s.get("cert_days_left")}

        host_rows = {}
        for h in ssh:
            name = h.get("name") or ""
            host_rows[name] = {
                "disk": h.get("disk_pct"), "mem": h.get("mem_pct"),
                "load": h.get("load1"), "failed": h.get("failed_ssh_24h"),
            }

        live: list[str] = list(macs_by_kind["always_on"])
        if _duty_ok("phone", hour):
            live.extend(macs_by_kind["phone"])
        if _duty_ok("tv", hour):
            live.extend(macs_by_kind["tv"])

        speed = None
        if i % speed_step == 0:
            speed = {"down": round(200 + rng.uniform(-20, 20), 1), "up": round(20 + rng.uniform(-5, 5), 1)}

        rows.append({
            "ts": ts.isoformat(timespec="seconds"),
            "sites": site_rows,
            "hosts": host_rows,
            "lan": len(live),
            "live_macs": live,
            "dns_ms": {"system": round(base_dns_ms + rng.uniform(-10, 10), 1)},
            "speed": speed,
            "score": score,
        })
    return rows


# The demo's two networks (Task 51): the fixture's own 192.168.1.0/24 as the
# primary Wi-Fi, plus a Lab on 10.10.0.0/24.
DEMO_NETWORKS = [
    {"name": "Office Wi-Fi", "kind": "wifi", "subnet_prefix": "192.168.1",
     "expected_gateway": "192.168.1.1", "ssid": demoscrub.SSID},
    {"name": "Lab", "kind": "lab", "subnet_prefix": "10.10.0",
     "expected_gateway": "10.10.0.1"},
]

LAB_PREFIX = "10.10.0"
LAB_GATEWAY_MAC = "02:10:0a:00:00:01"

#: The fixture MACs that move to the Lab: the computers, the printer and the
#: two appliances. `74:04:f1:4f:5a:ca` (the Windows PC) deliberately STAYS on
#: the Wi-Fi -- it is the MAC the "same mac on two networks" test adds to the
#: Lab by hand, and it cannot already be there.
#:
#: These are the *fixture's* addresses, so they are put through the same
#: `demoscrub.map_mac` rule the scrub applies before `split_networks` runs --
#: otherwise none of them would match any more and the Lab network would end
#: up holding nothing but its own synthetic gateway (silently shrinking the
#: demo). `LAB_MACS` below is the scrubbed form, which is what everything
#: downstream -- and `tests/test_networks.py` -- actually sees.
FIXTURE_LAB_MACS = (
    "7e:b3:23:ef:d0:1f",   # the practice MacBook (laptop)
    "8a:06:a1:00:82:2c",   # a MacBook (laptop)
    "54:27:1e:d3:5c:fb",   # Canon printer
    "54:88:0e:dc:75:2c",   # Samsung device (appliance)
    "34:55:e5:e9:7b:b8",   # Samsung range (appliance)
)

LAB_MACS = tuple(demoscrub.map_mac(mac) for mac in FIXTURE_LAB_MACS)


def _to_lab_ip(ip: str) -> str:
    """`192.168.1.202` -> `10.10.0.202` -- same host octet, so client-ip ->
    network attribution, the per-network gateway node and the DNS client join
    all render real data instead of orphan rows."""
    parts = (ip or "").split(".")
    return f"{LAB_PREFIX}.{parts[3]}" if len(parts) == 4 else ip


def split_networks(last: dict, state: dict) -> dict:
    """Move `LAB_MACS` onto the Lab subnet, in place, across every place the
    fixtures carry an ip: `state.lan.known_macs[mac].ip`, `last.lan.devices`
    (ip -> mac), `last.lan.names` (ip -> name) and the ip field of the
    port/lan event lists. Adds the Lab's own gateway device.

    Returns the `{old_ip: new_ip}` map so callers can see what moved. Pure
    dict surgery: the synthesised history (`live_macs` from
    `last.lan.devices`) and the 3 000 DNS rows (clients from the same map)
    then split across both prefixes in the same proportion for free.
    """
    lan = last.setdefault("lan", {})
    known = (state.setdefault("lan", {})).setdefault("known_macs", {})
    ip_map: dict[str, str] = {}
    for mac in LAB_MACS:
        d = known.get(mac)
        if not d or not d.get("ip") or d["ip"].startswith(LAB_PREFIX + "."):
            continue
        old_ip = d["ip"]
        ip_map[old_ip] = _to_lab_ip(old_ip)
        d["ip"] = ip_map[old_ip]

    if ip_map:
        lan["devices"] = {ip_map.get(ip, ip): mac for ip, mac in (lan.get("devices") or {}).items()}
        if lan.get("names"):
            lan["names"] = {ip_map.get(ip, ip): n for ip, n in lan["names"].items()}
        for key in ("port_events", "events", "new_devices"):
            for e in lan.get(key) or []:
                if isinstance(e, dict) and e.get("ip") in ip_map:
                    e["ip"] = ip_map[e["ip"]]

    # The Lab's own gateway, so its topology cluster has a real root and its
    # DNS clients resolve to a device.
    if LAB_GATEWAY_MAC not in known:
        known[LAB_GATEWAY_MAC] = {
            "ip": f"{LAB_PREFIX}.1", "name": "lab-switch", "label": "Lab switch",
            "vendor": "Vault demo", "kind": "router", "trusted": True, "always_on": True,
            "presence": "ONLINE", "risk": 0, "ports": {},
            "first_seen": last.get("ts", ""), "last_seen": last.get("ts", ""),
        }
        lan.setdefault("devices", {})[f"{LAB_PREFIX}.1"] = LAB_GATEWAY_MAC
        if lan.get("names") is not None:
            lan["names"][f"{LAB_PREFIX}.1"] = "lab-switch"
    return ip_map


def _build_config(last: dict) -> dict:
    """A Nexus-shaped config.json built from the fixture's own last.json --
    the demo tenant has no real config.json, so this derives one from
    whatever sites/hosts the fixture already describes."""
    return {
        # `public` defaults on for the demo: every fixture site is a real
        # public website, and the demo's own `/status/demo` page is one of
        # the things being demonstrated. An admin un-ticking it later is
        # never overridden -- see `_publish_demo_sites`.
        "sites": [{"name": s.get("name", ""), "url": s.get("url", ""), "public": True}
                  for s in last.get("sites") or []],
        "ssh_hosts": [{"name": h.get("name", ""), "target": h.get("target", "")} for h in last.get("ssh") or []],
        "lan": {"subnet": "192.168.1.0/24", "subnet_prefix": "192.168.1",
                 "expected_gateway": "192.168.1.1"},
        # Task 51: the demo watches two networks. First entry is the primary.
        "networks": [dict(n) for n in DEMO_NETWORKS],
        "thresholds": {"disk_pct": 90, "mem_pct": 90, "load_per_core": 2.0},
    }


#: Shared vocabulary for the historical seed (`_gen_events`/`_gen_fixes`/
#: `_gen_dns` below) AND `demo_trickle.py`'s between-refresh rows -- one
#: place so the two never drift into describing a different demo network.
#: Real action names (sensor/guardian_sensor/fix.py FIX_ACTIONS) -- the
#: Updates tab's patch calendar/CVE table bucket Fix rows by exactly these
#: strings, so a demo-only vocabulary here would leave every calendar cell
#: "none" and every CVE row's fixed_on empty.
DEMO_FIX_ACTIONS: tuple[str, ...] = ("restart_service", "full_upgrade", "reboot", "ban_long")
#: Ports the demo's synthetic port-open/port-closed events cycle through.
DEMO_EVENT_PORTS: tuple[int, ...] = (22, 80, 443, 3389, 445)
DEMO_DNS_NAMES: tuple[str, ...] = (
    "google.com", "netflix.com", "doubleclick.net", "ads.example.com",
    "malicious-tracker.ru", "apple.com", "icloud.com", "amazon.com",
    "phishy-login.top", "urlhaus-host.xyz",
)
DEMO_DNS_LISTS: tuple[str, ...] = ("stevenblack", "urlhaus", "phishing-army", "custom")
#: The demo's whole traffic vocabulary (Task 14) -- shared between `_gen_flows`
#: below and `demo_trickle.py`'s between-refresh rows, the same pairing as
#: `DEMO_DNS_NAMES` above, and for the same reason: one fixed list means the
#: seeded 30 days and the live trickle can never describe a different demo
#: network. `demo_trickle` only ever repeats the `www.google.com` and
#: `dentrix.example` entries (CLAUDE.md: "it does not invent unrelated ports
#: or successful repairs between refreshes" -- the same bound applies here to
#: peers), so this list is the superset the historical seed draws from and
#: the trickle module picks its fixed two out of.
#:
#: Every address is from the RFC 5737 documentation ranges and every name that
#: is not a household one is `.example`. The first version used real ones --
#: Google's 142.250.1.1, Microsoft's 20.1.1.1, pool.ntp.org -- and nothing
#: noticed for a week because `tests/test_demo_scrub.py` sweeps exactly the
#: models `_RESET_MODELS` lists, and `Flow` was not on that list until the
#: orphaned-flows fix put it there. The public demo must not present real
#: third-party infrastructure as something Northwind's devices talked to.
DEMO_PEERS: tuple[tuple[str, str, int, str], ...] = (
    ("www.google.com", "198.51.100.20", 443, "tcp"),
    ("update.microsoft.com", "198.51.100.30", 443, "tcp"),
    ("dentrix.example", "203.0.113.10", 443, "tcp"),
    ("time.example", "192.0.2.123", 123, "udp"),
)


def _gen_events(last: dict, state: dict, rng: random.Random, days: int, n: int = 40) -> list[dict]:
    anchor = dt.datetime.fromisoformat(last["ts"])
    macs = list(((state.get("lan") or {}).get("known_macs") or {}).keys()) or ["aa:bb:cc:dd:ee:ff"]
    kinds = ["join", "leave", "port_open", "port_closed", "fix"]
    step = dt.timedelta(minutes=(days * 24 * 60) / n)
    out = []
    for i in range(n):
        ts = anchor - step * (n - i)
        kind = kinds[i % len(kinds)]
        e = {"ts": ts.isoformat(timespec="seconds"), "type": kind, "mac": rng.choice(macs)}
        if kind in ("port_open", "port_closed"):
            e["port"] = rng.choice(DEMO_EVENT_PORTS)
        out.append(e)
    return out


def _gen_fixes(last: dict, rng: random.Random, n: int = 12) -> list[dict]:
    anchor = dt.datetime.fromisoformat(last["ts"])
    hosts = [h.get("name") for h in (last.get("ssh") or []) if h.get("name")] or ["prod"]
    step = dt.timedelta(hours=1)
    out = []
    for i in range(n):
        ts = anchor - step * (n - i)
        out.append({
            "ts": ts.isoformat(timespec="seconds"),
            "host": rng.choice(hosts),
            "action": DEMO_FIX_ACTIONS[i % len(DEMO_FIX_ACTIONS)],
            "label": f"auto-fix #{i + 1}",
            "ok": True,
            "by": "auto",
        })
    return out


def _gen_dns(last: dict, rng: random.Random, n: int = 3000) -> list[dict]:
    anchor = dt.datetime.fromisoformat(last["ts"])
    lan = last.get("lan") or {}
    ips = list((lan.get("devices") or {}).keys()) or ["192.168.1.100"]
    step = dt.timedelta(seconds=(24 * 3600) / n)
    out = []
    for i in range(n):
        ts = anchor - step * (n - i)
        blocked = rng.random() < 0.35
        out.append({
            "ts": ts.isoformat(timespec="seconds"),
            "client": rng.choice(ips),
            "name": rng.choice(DEMO_DNS_NAMES),
            "type": "A",
            "blocked": blocked,
            "list": rng.choice(DEMO_DNS_LISTS) if blocked else None,
            "ms": rng.randint(5, 60),
        })
    return out


def _gen_flows(last: dict, rng: random.Random, n: int = 600) -> list[dict]:
    """One minute of one device talking to one demo peer, `n` times over the
    trailing 24h -- same anchor-and-step-back shape as `_gen_dns`. `ts` is
    minute-floored because `Flow` rows are naturally a per-minute
    aggregate (that's what a real sensor mirror reports); flooring here
    also means two of these 600 rows landing in the same minute for the
    same device/peer/proto collide on `ingest.store_scan`'s own
    `(tenant, ts, mac, peer_ip, peer_port, proto, source)` dedup key and
    quietly merge into one row rather than raising -- harmless, and it is
    why the stored count can be a bit under `n`.
    """
    anchor = dt.datetime.fromisoformat(last["ts"])
    lan = last.get("lan") or {}
    dev_map = (lan.get("devices") or {}) or {"192.168.1.100": "aa:bb:cc:dd:ee:ff"}
    ips = list(dev_map.keys())
    step = dt.timedelta(seconds=(24 * 3600) / n)
    out = []
    for i in range(n):
        ts = (anchor - step * (n - i)).replace(second=0, microsecond=0)
        ip = rng.choice(ips)
        mac = dev_map.get(ip) or "aa:bb:cc:dd:ee:ff"
        peer_name, peer_ip, peer_port, proto = rng.choice(DEMO_PEERS)
        out.append({
            "ts": ts.isoformat(timespec="seconds"),
            "net": None,
            "mac": mac,
            "ip": ip,
            "peer_ip": peer_ip,
            "peer_port": peer_port,
            "proto": proto,
            "bytes_in": rng.randint(200, 200_000),
            "bytes_out": rng.randint(200, 50_000),
            "pkts_in": rng.randint(1, 200),
            "pkts_out": rng.randint(1, 200),
            "hints": {},
            "peer_name": peer_name,
            "source": "mirror",
        })
    return out


def _publish_demo_sites(db: DbSession, tenant: m.Tenant) -> None:
    """Mirror `config["sites"][].public` onto the freshly-ingested `Site`
    rows, so `/status/<slug>` has something to show.

    `ingest._upsert` only writes a site's url/last -- `Site.public` is a
    hub-side publishing decision (normally made in Settings -> Sites &
    targets, which syncs it the same way). Runs only while no site is
    public yet, so a re-seed never re-publishes a site an admin
    deliberately un-ticked."""
    rows = db.query(m.Site).filter_by(tenant_id=tenant.id).all()
    if not rows or any(r.public for r in rows):
        return

    config = dict(tenant.config or {})
    config_sites = list(config.get("sites") or [])
    # The live demo tenant was imported from Nexus, so its config predates
    # the `public` key entirely -- nobody has decided yet. Decide it here
    # (on for the demo), and write the decision back so Settings -> Sites &
    # targets shows the tick that the status page is acting on. A tenant
    # that already carries the key keeps whatever it says.
    if config_sites and not any("public" in s for s in config_sites):
        for s in config_sites:
            s["public"] = True
        config["sites"] = config_sites
        tenant.config = config

    wanted = {(s.get("name") or "").strip() for s in config_sites if s.get("public")}
    for row in rows:
        if (row.name or "").strip() in wanted:
            row.public = True
    db.commit()


def _seed_attacker_geo(db: DbSession, last: dict, state: dict) -> None:
    """Give the scrubbed attacker addresses a plausible country so the
    Security tab's attacker map still has pins.

    `payloads/security.attackers` fills `Attacker.geo` from `geoip.lookup`,
    which never calls a provider for a documentation-range address (they are
    `is_private` as far as `ipaddress` is concerned) -- so without a cached
    `IpGeo` row the map would come back empty, which would *reduce* what the
    demo shows. `IpGeo` is hub-global and keyed by ip, so this is an upsert
    and `reset_tenant` (tenant-scoped) never removes it.
    """
    for ip in demoscrub.attacker_ips(last, state):
        row = db.get(m.IpGeo, ip)
        if row is None:
            row = m.IpGeo(ip=ip)
            db.add(row)
        elif row.country:
            continue
        for key, value in demoscrub.demo_geo(ip).items():
            setattr(row, key, value)
        row.fetched = dt.datetime.now(dt.timezone.utc)
    db.commit()


#: Task 83, item 4: ten fictional cameras behind one fictional recorder, so
#: the demo tenant's own camera census (Overview -> Cameras) has something
#: real to show instead of the "1/1 cameras" the live demo used to read as a
#: rounding error. Entirely synthetic -- never derived from the owner's own
#: fixtures -- and addressed inside the RFC 5737 documentation range
#: (198.51.100.0/24), the same convention `demoscrub` already uses for every
#: other public/fictional address in this tenant. Named for the demo
#: tenant's own fiction (Northwind Dental), never the owner's real rooms.
_DEMO_RECORDER_HOST = "198.51.100.20"
_DEMO_CAMERA_NAMES = (
    "Waiting room", "Front desk", "Parking lot", "Back door", "Hallway",
    "Exam room 1", "Exam room 2", "Exam room 3", "Supply closet", "Server closet",
)
#: One of the ten is deliberately quiet, and the recorder is deliberately
#: told to expect two more than it has ever listed -- so the census this
#: task adds to Overview has a real, honest gap to show rather than a tidy
#: "all accounted for" that would never demonstrate what the row is for.
_DEMO_QUIET_CAMERA = "Supply closet"
_DEMO_EXPECTED_CHANNELS = len(_DEMO_CAMERA_NAMES) + 2


def _seed_demo_cameras(db: DbSession, tenant: m.Tenant, sensor: m.Sensor, ts: str) -> None:
    """Register (or re-register) the demo's one recorder and push its ten
    channels through `recorders.ingest` -- the exact same code path a real
    recorder's sensor push uses (Task 73), so this camera census is honestly
    "backend behavior with synthetic input", not a hand-rolled shortcut.

    Idempotent: found by `(tenant_id, host)`, the one column
    `Recorder.__table_args__` already uniques on, and every write below is an
    upsert -- calling this twice (or once per `refresh_demo` run) leaves
    exactly one recorder and ten device rows, never more.
    """
    rec = db.query(m.Recorder).filter_by(tenant_id=tenant.id, host=_DEMO_RECORDER_HOST).first()
    if rec is None:
        rec = m.Recorder(tenant_id=tenant.id, host=_DEMO_RECORDER_HOST)
        db.add(rec)
        db.flush()
    rec.name, rec.vendor = "Clinic NVR", "reolink"
    rec.port, rec.scheme, rec.username = 80, "http", "guardian"
    if not rec.secret_enc:
        recorders_mod.set_password(rec, "demo-only")
    rec.expected_channels, rec.enabled = _DEMO_EXPECTED_CHANNELS, True
    db.commit()

    devices, channels = {}, []
    for i, name in enumerate(_DEMO_CAMERA_NAMES):
        online = name != _DEMO_QUIET_CAMERA
        mac = f"02:00:00:00:01:{i:02x}"
        devices[mac] = {
            "ip": f"198.51.100.{40 + i}", "name": f"{name} camera",
            "vendor": "Reolink Innovation Limited", "kind": "camera",
            "presence": "ONLINE" if online else "OFFLINE", "ports": {},
            "first_seen": ts, "last_seen": ts,
        }
        channels.append({"channel": i, "name": f"{name} camera", "online": online, "model": "RLC-810A"})
    recorders_mod.ingest(db, tenant, sensor,
                         [{"id": rec.id, "ok": True, "channels": channels, "devices": devices}], ts)
    db.commit()


def _seed_yesterday_scan(db: DbSession, tenant, sensor, last: dict) -> None:
    """One scan from ~25h before `last`, so the Overview score line has
    something to compare against on a freshly seeded demo.

    `payloads/scorechange` compares against the newest scan at least a day
    old and renders nothing when there is none -- correct for a real tenant
    enrolled this morning, but on the demo it meant the one line that
    explains the score never appeared. Yesterday carried one finding that is
    gone today, so the line reads as an improvement with a named cause.
    Stored directly rather than through `store_scan`: it is history, and must
    not raise alerts, fixes or events of its own.
    """
    import copy
    ts_now = parse_ts(last.get("ts")) if last.get("ts") else None
    if ts_now is None:
        return
    raw = copy.deepcopy(last)
    score = raw.setdefault("score", {})
    deds = list(score.get("deductions") or [])
    deds.append({"category": "Exposure", "points": 4,
                 "why": "RDP (3389) reachable from the internet on booking"})
    score["deductions"] = deds
    total = int(score.get("total") or 0) - 4
    score["total"] = total
    raw["ts"] = (ts_now - dt.timedelta(hours=25)).isoformat()
    db.add(m.Scan(tenant_id=tenant.id, ts=raw["ts"], sensor_id=sensor.id if sensor else None,
                  score=total, raw=raw))
    db.commit()


def seed_demo(
    db: DbSession,
    slug: str = "demo",
    name: str = demoscrub.BRAND,
    fixtures: pathlib.Path | None = None,
    demo_code: str = DEMO_CODE,
    days: int = 30,
    anchor: dt.datetime | None = None,
) -> dict:
    """Create (or refresh) the demo tenant, its two demo users, an active
    demo-sensor, and 30 days of synthesized history/events/fixes/dns -- all
    ingested through `ingest.store_scan` so it lands exactly the way a real
    sensor push would. Idempotent: calling this twice adds no rows.

    `anchor`, when given, replaces the fixture's own (fixed, ever-aging)
    `last["ts"]` as the instant every synthesized row is computed relative
    to -- everything downstream (`synth_history`, `_gen_events`,
    `_gen_fixes`, `_gen_dns`, and `ingest.store_scan`'s own `Device.
    first_seen/last_seen`, `Alert`/`Incident` timestamps, ...) already
    derives its timestamps from `last["ts"]` alone, so overriding just that
    one string is what `refresh_demo` below uses to move the whole demo
    tenant's clock forward without re-deriving any of those relationships
    by hand. The fixture's own UTC offset is kept (not swapped for the
    caller's), since `scheduler._tenant_offset` reads that offset back off
    the newest scan to infer "the tenant's local time" for report
    scheduling -- changing it out from under a live demo tenant would shift
    when its reports fire.
    """
    fixtures = fixtures or _FIXTURES
    last = _load_fixture(fixtures, "last.json")
    state = _load_fixture(fixtures, "state.json")
    # Before anything else: the fixtures are a real capture, this tenant is
    # publicly signed into, so rewrite every address, hostname, MAC and
    # device label into the fictional Northwind Dental network. Runs ahead of
    # `split_networks` (which matches on `LAB_MACS`, itself derived through
    # the same mapping) and ahead of `_build_config`, so nothing downstream
    # ever sees a real value.
    demoscrub.scrub(last, state)
    if anchor is not None:
        try:
            offset = dt.datetime.fromisoformat(last["ts"]).utcoffset() or dt.timedelta(0)
        except (KeyError, ValueError, TypeError):
            offset = dt.timedelta(0)
        last["ts"] = anchor.astimezone(dt.timezone(offset)).isoformat(timespec="seconds")
    split_networks(last, state)

    tenant = db.query(m.Tenant).filter_by(slug=slug).first()
    created = tenant is None
    if created:
        tenant = m.Tenant(slug=slug, name=name)
        db.add(tenant)
        db.flush()

    # The live `demo` tenant on the VPS was imported from Nexus and carries
    # its own real name/config/profile -- re-seeding must set the demo code
    # (and bump retention) without clobbering that data. A brand-new tenant
    # gets the fixture-derived config and profile; an existing one keeps
    # whatever it already has, only filling in fields it doesn't have yet.
    existing_settings = tenant.settings or {}
    tenant.settings = {
        **existing_settings,
        "demo_code": demo_code,
        "retention_days": existing_settings.get("retention_days") or 90,
        # Forced, not preserved -- see the note on `tenant.config` below. An
        # imported profile carries the real owner's name, email and postal
        # address, which is the single worst field on a publicly-signed-into
        # tenant.
        "profile": {"owner": demoscrub.BRAND, "email": "", "address": ""},
    }
    # `tenant.name`/`tenant.config` are FORCED for the demo tenant, not
    # preserved. The live demo tenant was imported from Nexus and carries the
    # real site names and urls; `reset_tenant` never touches `Tenant`, so a
    # preserved config would keep publishing them through Settings while the
    # ingested rows were scrubbed -- and `_publish_demo_sites` matches
    # `Site.name` against `config["sites"][].name`, so a config holding the
    # old real names would match no scrubbed site, publish nothing, and leave
    # `/status/demo` reading "No public services". Rebuilding both from the
    # scrubbed fixture keeps the two halves in step. Nothing of value is lost:
    # this tenant is re-seeded from these same fixtures every hour by
    # `refresh_demo`.
    tenant.name = name
    tenant.config = _build_config(last)
    if not tenant.ntfy_topic:
        tenant.ntfy_topic = "guardian-demo"
    # A tenant seeded before Task 51 (or imported from Nexus) has no
    # `networks` block; `ingest.store_scan` would then give it one unnamed
    # primary rather than the demo's two networks.
    if not (tenant.config or {}).get("networks"):
        cfg = dict(tenant.config or {})
        cfg["networks"] = [dict(n) for n in DEMO_NETWORKS]
        cfg["lan"] = {**(cfg.get("lan") or {}), "subnet_prefix": "192.168.1",
                       "expected_gateway": "192.168.1.1"}
        tenant.config = cfg
    db.commit()

    # A demo Slack integration (Task 40) so the Integrations tab and the
    # severity-routing tests have a real, non-ntfy row to show: CRIT/WARN
    # route through, INFO is deliberately routed off to demonstrate
    # per-severity routing. The webhook URL is a fake but shape-correct
    # Slack incoming-webhook URL -- never a live secret.
    from . import crypto
    slack_row = db.query(m.Integration).filter_by(tenant_id=tenant.id, kind="slack").first()
    if not slack_row:
        db.add(m.Integration(
            tenant_id=tenant.id, kind="slack", name="Slack (demo)",
            secret=crypto.encrypt("https://hooks.slack.com/services/T000/B000/examplewebhooktoken"),
            routes={"CRIT": True, "WARN": True, "INFO": False}, enabled=True,
        ))
        db.commit()

    demo_user = upsert_user(db, "demo", "demo@guardian.invalid", "Demo Viewer")
    upsert_membership(db, demo_user, tenant, "viewer")
    admin_user = upsert_user(db, "demoadmin", "demoadmin@guardian.invalid", "Demo Admin")
    upsert_membership(db, admin_user, tenant, "admin")
    db.commit()

    sensor = db.query(m.Sensor).filter_by(tenant_id=tenant.id, name="demo-sensor").first()
    if not sensor:
        sensor = m.Sensor(tenant_id=tenant.id, name="demo-sensor")
        db.add(sensor)
        db.flush()
    sensor.status = "active"
    db.commit()

    rng = random.Random(20260909)
    history = synth_history(last, days=days, step_minutes=30)
    events = _gen_events(last, state, rng, days=days, n=40)
    fixes = _gen_fixes(last, rng, n=12)
    dns_queries = _gen_dns(last, rng, n=3000)
    flows = _gen_flows(last, rng, n=600)

    _seed_yesterday_scan(db, tenant, sensor, last)
    # Task 14: the demo declares itself a mirror capture with nothing dropped,
    # set on `last` (not on the deepcopy `_seed_yesterday_scan` already took
    # above) so only the LATEST scan -- the one `payloads/traffic.mode_for`
    # reads -- carries it.
    last["capture"] = {"mode": "mirror", "error": "", "interface": "eth1", "flows_dropped": 0}
    store_scan(db, tenant, sensor, {
        "last": last, "state": state,
        "history": history, "events": events, "fixes": fixes, "dns_queries": dns_queries,
        "flows": flows,
    }, deliver=False)

    # The primary network's `traffic_mode` is a person's declaration
    # (`networks.ensure_primary`/`sync_from_config` never touch it), so the
    # demo has to set it explicitly the one time a person would have, via
    # the onboarding wizard. Done after `store_scan` because that call is
    # what creates the primary `Network` row on a brand-new tenant.
    primary_net = db.query(m.Network).filter_by(tenant_id=tenant.id, is_primary=True).first()
    if primary_net is not None:
        primary_net.traffic_mode = "mirror"
        db.commit()

    _publish_demo_sites(db, tenant)
    _seed_attacker_geo(db, last, state)
    _seed_demo_cameras(db, tenant, sensor, last["ts"])

    return {
        "tenant_id": tenant.id,
        "scans": db.query(m.Scan).filter_by(tenant_id=tenant.id).count(),
        "history": len(history),
        "events": db.query(m.Event).filter_by(tenant_id=tenant.id).count(),
        "dns": db.query(m.DnsQuery).filter_by(tenant_id=tenant.id).count(),
        "flows": db.query(m.Flow).filter_by(tenant_id=tenant.id).count(),
        "devices": db.query(m.Device).filter_by(tenant_id=tenant.id).count(),
        "networks": db.query(m.Network).filter_by(tenant_id=tenant.id).count(),
    }


def refresh_demo(
    db: DbSession,
    slug: str = "demo",
    days: int = 30,
    now: dt.datetime | None = None,
    keep_queue_ids: "set[str] | None" = None,
) -> dict | None:
    """Roll the `demo` tenant's window forward so its newest activity stays
    close to "now" -- what `guardian_hub.scheduler.demo_refresh_job` (and
    the `guardian demo-refresh` CLI subcommand) call to keep guarder.io's
    public "Live from the demo network" panel (`routers/public_feed.py`)
    honestly live instead of drifting toward a newest row that is days old.

    **Why re-seed rather than shift existing rows' timestamps.** The demo
    tenant's rows are not independent: a `Scan.raw` payload embeds the same
    instant that a `Device.last_seen`, an `Alert.first_seen/last_seen`, and
    an `Incident.opened/closed` all carry, and `Event`/`Fix`/`DnsQuery` rows
    are spaced relative to that one anchor by `synth_history`/`_gen_events`/
    `_gen_fixes`/`_gen_dns`. Hand-shifting every `ts`-bearing column (and
    every `ts` string buried inside a JSON blob) by the same delta would
    have to reconstruct that whole dependency graph outside of it, one
    schema change away from silently drifting out of sync with it. Re-
    seeding through `seed_demo(..., anchor=now)` instead asks the exact same
    code that built these relationships once to rebuild them again at a new
    anchor -- so `Device.first_seen/last_seen`, `Alert` timestamps, and
    every row's relative spacing stay exactly as consistent as a first-time
    `seed-demo` run already guarantees. The tradeoff is heavier per run
    (`reset_tenant` + a full re-ingest via `store_scan`, same cost as
    `guardian seed-demo --reset`) -- acceptable for one small demo tenant on
    a cron, not for a request path.

    **Honesty.** This changes *when* the demo's story happened, never *what*
    happened: the same seeded RNG (see `seed_demo`), the same fixtures, the
    same 40 events / 12 fixes / 3000 DNS rows / site flips -- only the
    instant they are anchored to moves to `now`. No severity, count, or
    outcome is invented or exaggerated by this function; the public feed
    keeps showing exactly the demo network's real (synthetic-by-design,
    per this module's own docstring) rows, just never stale ones.

    **Scope.** Touches only the tenant at `slug` (default `"demo"`, the
    same default `seed_demo`/`guardian seed-demo` use) -- `reset_tenant`
    filters every delete by that tenant's id, and nothing here ever queries
    another tenant. Returns `None` -- doing nothing at all -- when no
    tenant with that slug exists, so a self-hosted hub that never ran
    `guardian seed-demo` is untouched even if this job is enabled on it.

    **Idempotent.** Every call resets then fully re-ingests, so calling it
    twice in a row (or a thousand times) always ends in the same kind of
    state: one tenant, one active demo-sensor, exactly the counts a fresh
    `seed_demo` call for the same anchor produces. It never touches `User`,
    `Membership`, `Session`, `Otp`, or `Tenant.settings["demo_code"]` --
    `reset_tenant`'s model list excludes the first three outright, and
    `seed_demo` only ever writes `demo_code` back to what it already was.
    """
    tenant = db.query(m.Tenant).filter_by(slug=slug).first()
    if tenant is None:
        return None
    decisions = _capture_decisions(db, tenant.id)
    reset_tenant(db, tenant.id, keep_queue_ids=keep_queue_ids)
    out = seed_demo(db, slug=slug, days=days, anchor=now or dt.datetime.now(dt.timezone.utc))
    _restore_decisions(db, tenant.id, decisions)
    return out


def _capture_decisions(db: DbSession, tenant_id: str) -> dict:
    """What a visitor decided, so the refresh does not erase it.

    The refresh deletes and rebuilds every ingest-derived row, which is the
    right way to keep the demo's story internally consistent -- but it also
    threw away what the person looking at the demo had just done. The second
    verification cycle reproduced it: acknowledge an alert or accept a
    recommendation, and within ten minutes it was silently back to untouched.
    A prospect clicking around concludes the buttons do nothing.

    Only the human decision is carried, never the observation: an alert's
    status and who set it, a recommendation's yes or no. Everything the sensor
    reports is rebuilt from the fixtures as before, so the demo cannot drift
    into claiming something its data does not say. Rows are matched on `key`,
    which is stable across a re-seed by construction.

    `CheckDecision` is not in `_RESET_MODELS`, so "leave it" on a Security
    check survives the reset by itself -- but its `network_id` does not stay
    true: the refresh recreates the networks with new ids. So the networks'
    slugs are captured here and `_restore_decisions` re-points each decision
    at the new network with the same slug. Without it the demo accumulated
    decisions about networks that no longer existed, matching no check.
    """
    alerts = {
        a.key: (a.status, a.resolved_by, a.snoozed_until)
        for a in db.query(m.Alert).filter_by(tenant_id=tenant_id).all()
        if a.status and a.status != "open"
    }
    recs = {
        r.key: (r.decision, r.status, r.decided_by)
        for r in db.query(m.Recommendation).filter_by(tenant_id=tenant_id).all()
        if r.decision
    }
    net_slugs = {n.id: n.slug for n in db.query(m.Network).filter_by(tenant_id=tenant_id).all()}
    return {"alerts": alerts, "recs": recs, "net_slugs": net_slugs}


def _restore_decisions(db: DbSession, tenant_id: str, decisions: dict) -> None:
    """Re-apply captured decisions to the rebuilt rows, where they still exist.

    A decision about something the new scan no longer reports is dropped, not
    resurrected: the condition is gone, so there is nothing to have decided
    about.
    """
    for a in db.query(m.Alert).filter_by(tenant_id=tenant_id).all():
        saved = decisions.get("alerts", {}).get(a.key)
        if saved:
            a.status, a.resolved_by, a.snoozed_until = saved
    for r in db.query(m.Recommendation).filter_by(tenant_id=tenant_id).all():
        saved = decisions.get("recs", {}).get(r.key)
        if saved:
            r.decision, r.status, r.decided_by = saved
    old_slugs = decisions.get("net_slugs", {})
    new_ids = {n.slug: n.id for n in db.query(m.Network).filter_by(tenant_id=tenant_id).all()}
    for d in db.query(m.CheckDecision).filter_by(tenant_id=tenant_id).all():
        if d.network_id and d.network_id not in new_ids.values():
            new_id = new_ids.get(old_slugs.get(d.network_id, ""))
            if new_id:
                d.network_id = new_id
            else:
                # A network that no longer exists at all: the decision is
                # about nothing, like an alert the new scan no longer reports.
                db.delete(d)
    db.commit()
