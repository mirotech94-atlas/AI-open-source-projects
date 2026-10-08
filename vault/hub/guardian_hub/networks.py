"""Multiple networks per tenant (Task 51 / spec S3.1).

One `Network` row per watched network -- the home Wi-Fi, the lab private
network -- each with its own sensor, baseline, presence, DNS attribution and
Wi-Fi guard. Everything network-shaped that the rest of the hub needs lives
here so there is exactly one `slugify`, one attribution order and one health
derivation:

- `slugify` is the single slug implementation the whole hub imports.
- `ensure_primary` / `sync_from_config` keep the rows in step with
  `Tenant.config["networks"]` (falling back to the legacy `config["lan"]`
  block, which is one network).
- `backfill` moves an existing single-network deployment's rows onto its
  primary network on the first ingest after this lands.
- `resolve` turns an API `?network=<id|slug>` into a row (or `None` = all).
- `for_client_ip` is the fallback attribution for a row that carries no
  network of its own (a DNS query knows only its client ip).
- `health` builds the overview's per-network strip.
- `lan_alert_key` / `canonical_lan_key` namespace LAN alert keys per network
  (`lan:new:<mac>` -> `lan:<slug>:new:<mac>`) and undo that for the parsers
  that read a key's segments.

`Network.config` does not exist: a network's mutable settings ARE the
`Tenant.config["networks"]` entry, which is what the sensor replays. Every
hub-side write therefore emits one whole-config `Edit(path="/api/config")`
(see `routers/networks.py`).
"""

from __future__ import annotations

import datetime as dt
import ipaddress
import re

from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.orm import Session as DbSession

from . import models as m

#: Every `Network.kind` the API accepts. `home` exists only for the rows
#: `0007_networks` creates for tenants that predate this table (whose kind is
#: genuinely unknown -- the migration cannot tell Wi-Fi from wired); without
#: it those rows would be un-PATCHable through the CRUD routes below.
KINDS = ("wifi", "wired", "lab", "vlan", "guest", "home")

#: Every `Network.traffic_mode` the API accepts. A person's declaration of
#: how Vault sees this network's traffic -- `payloads/traffic.mode_for`
#: still crosses it against what the sensor actually reports before trusting
#: it, so a stale "mirror" left over from a decommissioned tap reads as "none".
#:
#: `host` means the sensor captures only its OWN traffic -- a sensor on Wi-Fi,
#: or on a wired port nobody mirrored. Its rows look exactly like a mirror's,
#: which is why only a person can say which it is: presented as `mirror`, one
#: machine's connections would read as the whole network's, destinations and
#: "new peers" included. The sensor reports `mirror` whenever it captures; the
#: declaration is what narrows that.
TRAFFIC_MODES = ("mirror", "router", "host", "none")

SLUG_MAX = 24

#: The sub-keys a `lan:` alert key can carry (the sensor's
#: `guardian_sensor.alerts` vocabulary). Used to tell an already-namespaced
#: key (`lan:<slug>:new:<mac>`) from a legacy one (`lan:new:<mac>`) without
#: having to look the slug up -- which also makes every one of them a RESERVED
#: slug (see `slugify`).
LAN_SUBKEYS = ("baseline", "rotated", "new", "missing", "port", "offline",
               "flapping", "unverified", "unusual-hour",
               # Task 69: the camera inventory and the rogue-device findings.
               # Mirrors `guardian_sensor.config.LAN_SUBKEYS` -- a sub-key
               # missing here leaves the slug in for secondary networks and
               # `inbox._entity_from_key`'s positional parsing breaks.
               "camera-new", "camera-gone", "offsubnet", "second-router")

#: The tables that carry a `network_id`, in the order `backfill`/`reassign`
#: touch them. Names, not models, because both operations are one bulk
#: `UPDATE` per table rather than a row-by-row ORM walk.
SCOPED_TABLES = ("device", "event", "alert", "dns_query", "presence_sample")

SUBNET_RE = re.compile(r"^(\d{1,3}\.){2}\d{1,3}$")

#: A sensor counts as reachable-and-reporting for `sensor_ok` within this many
#: seconds of its `last_seen`.
SENSOR_STALE_S = 15 * 60


def _now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


# --------------------------------------------------------------------------
# Slugs
# --------------------------------------------------------------------------

def slugify(name: str) -> str:
    """`"Office Wi-Fi"` -> `"office-wi-fi"`, `"Lab / VLAN 40!"` -> `"lab-vlan-40"`.

    Lowercase, non-alphanumerics collapsed to single hyphens, trimmed to
    `SLUG_MAX` characters with no trailing hyphen. The single implementation
    the whole hub imports -- a second one would eventually disagree with the
    slugs already stored, and it is the only place the reserved-word rule below
    is enforced, so both the admin CRUD route and a sensor-pushed config get it.

    A slug may never BE a LAN sub-key: `lan_alert_key`/`canonical_lan_key` tell
    a namespaced key from a legacy one by testing whether the segment after
    `lan:` is in `LAN_SUBKEYS`, so a network called "New" (slug `new`) would
    make `lan:new:port:<mac>` ambiguous -- unstrippable in one direction and
    double-namespaced in the other. Such a name gets `-net` appended instead.
    """
    s = re.sub(r"[^a-z0-9]+", "-", (name or "").strip().lower()).strip("-")
    s = s[:SLUG_MAX].rstrip("-")
    if s in LAN_SUBKEYS:
        s = f"{s}-net"[:SLUG_MAX].rstrip("-")
    return s or "network"


def unique_slug(db: DbSession, tenant_id: str, name: str, exclude_id: str | None = None) -> str:
    """`slugify(name)`, suffixed `-2`, `-3`… until it is free for this tenant."""
    base = slugify(name)
    candidate, n = base, 1
    while True:
        q = db.query(m.Network).filter_by(tenant_id=tenant_id, slug=candidate)
        if exclude_id:
            q = q.filter(m.Network.id != exclude_id)
        if not q.first():
            return candidate
        n += 1
        suffix = f"-{n}"
        candidate = base[:SLUG_MAX - len(suffix)].rstrip("-") + suffix


# --------------------------------------------------------------------------
# LAN alert keys
# --------------------------------------------------------------------------

def lan_alert_key(key: str, slug: str) -> str:
    """`lan:new:<mac>` + `"lab"` -> `lan:lab:new:<mac>`.

    Non-`lan:` keys and keys that are already namespaced come back unchanged,
    so this is safe to run over every incoming key on every ingest -- which is
    what `ingest.store_scan` does, because the sensor keeps emitting the legacy
    shape until Task 52 ships.
    """
    if not key or not key.startswith("lan:") or not slug:
        return key
    rest = key[4:]
    head = rest.split(":", 1)[0]
    if head not in LAN_SUBKEYS:
        return key          # already namespaced (or a key we don't recognise)
    return f"lan:{slug}:{rest}"


def canonical_lan_key(key: str) -> str:
    """The inverse: strip the network segment so a key's remaining segments
    can be parsed positionally (`payloads/inbox._entity_from_key`, the
    `sec:`/`home:`/`lan:unverified` prefix tests in `payloads/overview.py`).
    Idempotent, and a no-op for anything that isn't a namespaced `lan:` key."""
    if not key or not key.startswith("lan:"):
        return key
    parts = key.split(":")
    if len(parts) >= 3 and parts[1] not in LAN_SUBKEYS and parts[2] in LAN_SUBKEYS:
        return "lan:" + ":".join(parts[2:])
    return key


def find_alert(db: DbSession, tenant_id: str, key: str) -> "m.Alert | None":
    """An alert by key, tolerating either LAN key shape.

    A caller that only knows a MAC (an ntfy action button's `lan:new:<mac>`)
    cannot know which network the alert landed on, so an exact miss falls back
    to matching on the canonical form.
    """
    row = db.query(m.Alert).filter_by(tenant_id=tenant_id, key=key).first()
    if row or not (key or "").startswith("lan:"):
        return row
    canon = canonical_lan_key(key)
    for cand in db.query(m.Alert).filter(m.Alert.tenant_id == tenant_id,
                                         m.Alert.key.like("lan:%")).all():
        if canonical_lan_key(cand.key) == canon:
            return cand
    return None


# --------------------------------------------------------------------------
# Config <-> rows
# --------------------------------------------------------------------------

def _prefix_of(ip: str) -> str:
    parts = (ip or "").split(".")
    return ".".join(parts[:3]) if len(parts) == 4 else ""


def config_entries(cfg: dict) -> list[dict]:
    """`cfg["networks"]` when the sensor sent one, else the legacy single
    `cfg["lan"]` block wrapped as a one-entry list (invariant 1)."""
    nets = (cfg or {}).get("networks")
    if isinstance(nets, list) and nets:
        return [n for n in nets if isinstance(n, dict)]
    lan = (cfg or {}).get("lan")
    return [dict(lan)] if isinstance(lan, dict) and lan else []


def _entry_facts(entry: dict, raw: dict | None = None) -> dict:
    """The `Network` column values one config entry (plus, for the primary,
    the live scan `raw`) implies."""
    raw = raw or {}
    home = raw.get("home") or {}
    wifi = home.get("wifi") or {}
    gateway = entry.get("expected_gateway") or home.get("gateway") or ""
    prefix = entry.get("subnet_prefix") or _prefix_of(entry.get("subnet") or "") or _prefix_of(gateway)
    ssid = entry.get("ssid") or str(wifi.get("ssid") or "")
    kind = entry.get("kind") or ("wifi" if ssid else "wired")
    return {
        "kind": kind if kind in KINDS else "wifi",
        "subnet_prefix": prefix,
        "expected_gateway": gateway,
        "ssid": ssid,
    }


def ensure_primary(db: DbSession, tenant, cfg: dict, raw: dict, sensor) -> m.Network:
    """The tenant's primary network, created on first ingest and refreshed on
    every one after.

    Matched on `is_primary=True` FIRST, never on slug: `0007_networks` gives
    every pre-existing tenant a primary called `primary`, while a config-derived
    slug would be `office-wi-fi` -- looking up by slug would create a second
    network for every migrated tenant on the very next push. An existing
    primary is updated in place (its `name` only when it has none), never
    duplicated.
    """
    entries = config_entries(cfg or {})
    entry = entries[0] if entries else {}
    facts = _entry_facts(entry, raw)
    name = entry.get("name") or "Home network"

    row = db.query(m.Network).filter_by(tenant_id=tenant.id, is_primary=True).first()
    if row is None:
        row = m.Network(tenant_id=tenant.id, name=name,
                        slug=unique_slug(db, tenant.id, name), is_primary=True)
        db.add(row)
    elif not row.name:
        row.name = name
    for k, v in facts.items():
        # Never blank a stored fact with an empty config/scan reading.
        if v or not getattr(row, k, ""):
            setattr(row, k, v)
    row.status = "active"
    if sensor is not None:
        row.sensor_id = sensor.id
    db.flush()
    return row


def sync_from_config(db: DbSession, tenant, cfg: dict, reported: set[str] | None = None) -> list[m.Network]:
    """Upsert one `Network` per `cfg["networks"]` entry, in order (first =
    primary). Never deletes: a network dropped from the config keeps its rows
    and its history until an admin deletes it explicitly through
    `DELETE /api/v1/networks/{id}`.

    `reported` is the set of slugs the pushing sensor actually said something
    about; an entry nobody reported goes to `status="no-sensor"` so the UI can
    say "needs a sensor on this network" rather than showing an empty scan.
    """
    reported = reported if reported is not None else set()
    out: list[m.Network] = []
    for idx, entry in enumerate(config_entries(cfg or {})):
        name = entry.get("name") or ("Home network" if idx == 0 else f"Network {idx + 1}")
        facts = _entry_facts(entry)
        slug = slugify(entry.get("slug") or name)
        row = db.query(m.Network).filter_by(tenant_id=tenant.id, slug=slug).first()
        if row is None and idx == 0:
            row = db.query(m.Network).filter_by(tenant_id=tenant.id, is_primary=True).first()
        if row is None:
            row = m.Network(tenant_id=tenant.id, name=name,
                            slug=unique_slug(db, tenant.id, name), is_primary=(idx == 0))
            db.add(row)
        if not row.name:
            row.name = name
        for k, v in facts.items():
            if v or not getattr(row, k, ""):
                setattr(row, k, v)
        if idx == 0:
            row.is_primary = True
        if row.slug in reported:
            row.status = "active"
        elif idx == 0:
            pass                    # `ensure_primary` already set it
        elif not row.sensor_id and row.status != "unreachable":
            row.status = "no-sensor"
        out.append(row)
    db.flush()
    return out


#: The `Network` columns the hub owns in a `config["networks"]` entry. Every
#: OTHER key in a stored entry belongs to the sensor and is preserved verbatim
#: (see `to_config_entries`) -- which is exactly how Task 69's per-network
#: `expected_wan_ip` pin survives a hub-side network write without needing a
#: column, a migration and a route of its own.
HUB_OWNED_KEYS = ("name", "slug", "kind", "subnet_prefix", "expected_gateway",
                  "ssid", "colour", "sensor_id")


def to_config_entries(db: DbSession, tenant_id: str, cfg: dict | None = None) -> list[dict]:
    """The `networks` block to write back into `Tenant.config` -- primary
    first, then by name, so the sensor's replay of a whole-config edit keeps
    the same "first entry is the primary" invariant the hub reads.

    Each entry STARTS from the stored `cfg["networks"]` entry with the same
    slug and only overlays `HUB_OWNED_KEYS`, so per-entry keys the hub has no
    column for (spec S3.1's `interface?`, and anything the sensor grows later)
    survive a hub-side network write. Without that, `routers/networks._commit_write`
    -- which ships the WHOLE config, and which the sensor replays with
    `cfg.clear()` -- would permanently drop them from every entry on the first
    create/patch/delete.

    The stored list is read raw rather than through `config_entries`: that
    helper falls back to the legacy single `cfg["lan"]` block, whose keys
    (`subnet`, …) have no business being merged into a `networks[]` entry.
    """
    stored = (cfg or {}).get("networks")
    by_slug: dict[str, dict] = {}
    if isinstance(stored, list):
        for entry in stored:
            if not isinstance(entry, dict):
                continue
            # `slugify(slug or name)` -- the same derivation `sync_from_config`
            # matches rows with, because a sensor-written entry usually carries
            # only a `name` (the demo config's entries do).
            key = slugify(entry.get("slug") or entry.get("name") or "")
            by_slug.setdefault(key, entry)

    rows = db.query(m.Network).filter_by(tenant_id=tenant_id).all()
    # Primary first (the hub and the sensor both read entry 0 as the primary),
    # then in creation order so a freshly-added network lands last -- the shape
    # an admin who just added one expects to see in the config they replay.
    rows.sort(key=lambda n: (not n.is_primary, n.created or _now(), (n.name or "").lower()))
    out = []
    for n in rows:
        # A copy: `_commit_write` shallow-copies `tenant.config`, so mutating the
        # stored entry in place would edit the live config out from under it.
        entry = dict(by_slug.get(n.slug) or {})
        entry.update({k: getattr(n, k) for k in HUB_OWNED_KEYS})
        out.append(entry)
    return out


# --------------------------------------------------------------------------
# Backfill / reassign
# --------------------------------------------------------------------------

def needs_backfill(db: DbSession, tenant_id: str) -> bool:
    for table in SCOPED_TABLES:
        n = db.execute(text(f"SELECT 1 FROM {table} WHERE tenant_id = :tid AND network_id IS NULL LIMIT 1"),
                       {"tid": tenant_id}).first()
        if n is not None:
            return True
    return False


def backfill(db: DbSession, tenant, network: m.Network) -> dict:
    """One-shot: every row of this tenant that names no network lands on
    `network`. Runs inside `store_scan` right after `ensure_primary`, so an
    existing single-network deployment lands wholly on its primary on the
    first ingest after this task deploys."""
    out: dict[str, int] = {}
    for table in SCOPED_TABLES:
        res = db.execute(
            text(f"UPDATE {table} SET network_id = :nid WHERE tenant_id = :tid AND network_id IS NULL"),
            {"nid": network.id, "tid": tenant.id},
        )
        out[table] = res.rowcount or 0
    return out


def row_counts(db: DbSession, tenant_id: str, network_id: str) -> dict[str, int]:
    """How many rows each scoped table still holds for one network -- what
    `DELETE /networks/{id}` refuses on (over the union, not on `device` alone:
    an alert or a DNS query left behind would keep a dangling `network_id`
    forever, since there is no FK on that column)."""
    out: dict[str, int] = {}
    for table in SCOPED_TABLES:
        row = db.execute(
            text(f"SELECT COUNT(*) FROM {table} WHERE tenant_id = :tid AND network_id = :nid"),
            {"tid": tenant_id, "nid": network_id},
        ).first()
        out[table] = int(row[0]) if row else 0
    return out


def reassign(db: DbSession, tenant_id: str, from_id: str, to_id: str) -> dict:
    """Move every scoped row from one network to another (the `?reassign=`
    branch of `DELETE /networks/{id}`).

    `alert` needs more than the plain bulk `UPDATE` the other four tables get:
    its `key` carries the SOURCE network's slug (`lan:<src-slug>:<sub>:<rest>`),
    so moving only `network_id` would leave a row keyed under a slug the
    destination's next `ingest.store_scan`/`find_alert` never look for --
    that push opens a fresh duplicate under `lan:<dst-slug>:...` while the
    moved row itself never closes. Each LAN-keyed alert is re-namespaced to
    the destination's slug (`canonical_lan_key` then `lan_alert_key`, the same
    round-trip `0007_networks` does) with that migration's own clash guard:
    if the destination already holds the rewritten key (an open twin of the
    same finding, already on the destination), the destination's row wins and
    the moved duplicate is deleted rather than violating
    `UNIQUE(tenant_id, key)`. A non-`lan:` key is untouched by both helpers, so
    it just gets moved like any other row.

    Returns the per-table moved counts (`out[table]`) plus `out["dropped"]`,
    a same-shaped dict counting rows dropped instead of moved -- today only
    ever `dropped["alert"]`, the clash-deleted duplicates above, so a caller
    (`DELETE /networks/{id}`) can report them rather than have them vanish
    silently from the moved counts.
    """
    out: dict[str, int] = {}
    dropped: dict[str, int] = {}
    dst = db.query(m.Network).filter_by(tenant_id=tenant_id, id=to_id).first()

    if dst is not None:
        rows = (db.query(m.Alert)
                .filter(m.Alert.tenant_id == tenant_id, m.Alert.network_id == from_id)
                .all())
        moved = 0
        clash_dropped = 0
        for row in rows:
            new_key = lan_alert_key(canonical_lan_key(row.key), dst.slug)
            if new_key != row.key:
                clash = (db.query(m.Alert)
                         .filter_by(tenant_id=tenant_id, key=new_key)
                         .filter(m.Alert.id != row.id)
                         .first())
                if clash:
                    db.delete(row)
                    clash_dropped += 1
                    continue
                row.key = new_key
            row.network_id = to_id
            moved += 1
        db.flush()
        out["alert"] = moved
        dropped["alert"] = clash_dropped
    else:
        res = db.execute(
            text("UPDATE alert SET network_id = :to WHERE tenant_id = :tid AND network_id = :frm"),
            {"to": to_id, "tid": tenant_id, "frm": from_id},
        )
        out["alert"] = res.rowcount or 0
        dropped["alert"] = 0

    for table in SCOPED_TABLES:
        if table == "alert":
            continue
        res = db.execute(
            text(f"UPDATE {table} SET network_id = :to WHERE tenant_id = :tid AND network_id = :frm"),
            {"to": to_id, "tid": tenant_id, "frm": from_id},
        )
        out[table] = res.rowcount or 0
    out["dropped"] = dropped
    return out


# --------------------------------------------------------------------------
# Lookup / attribution
# --------------------------------------------------------------------------

def resolve(db: DbSession, tenant_id: str, ref: str | None) -> m.Network | None:
    """A `?network=` query value -> a row. `None`/`""`/`"all"` mean "every
    network" and come back as `None`; an unknown id/slug is a 404, never a
    silent full-tenant read."""
    if not ref or ref == "all":
        return None
    row = (db.query(m.Network)
           .filter(m.Network.tenant_id == tenant_id,
                   (m.Network.id == ref) | (m.Network.slug == ref))
           .first())
    if row is None:
        raise HTTPException(404, "no such network")
    return row


def is_private_ip(ip: str) -> bool:
    """Whether `ip` is RFC1918/loopback/link-local -- used by `payloads.traffic`
    to split a device's bytes into "stayed on the LAN" vs "left it" for
    `external_share`. Peer IPs come from the sensor's own capture and can be
    malformed or empty; that is expected input, not a bug, and it must not
    crash the overview. An address Vault cannot parse is one it cannot
    prove left the LAN, so it is treated as private -- undercounting
    `external_share` on bad input, never overcounting it.
    """
    try:
        return ipaddress.ip_address(ip).is_private
    except ValueError:
        return True


def primary(db: DbSession, tenant_id: str) -> m.Network | None:
    return (db.query(m.Network).filter_by(tenant_id=tenant_id, is_primary=True).first()
            or db.query(m.Network).filter_by(tenant_id=tenant_id).order_by(m.Network.created).first())


def by_slug(db: DbSession, tenant_id: str) -> dict[str, m.Network]:
    return {n.slug: n for n in db.query(m.Network).filter_by(tenant_id=tenant_id).all()}


def for_client_ip(db: DbSession, tenant_id: str, ip: str) -> m.Network | None:
    """Longest `subnet_prefix` match -- the fallback attribution when a pushed
    row carries no `net` of its own but does know a client ip."""
    if not ip:
        return None
    best: m.Network | None = None
    for n in db.query(m.Network).filter_by(tenant_id=tenant_id).all():
        p = n.subnet_prefix or ""
        if not p or not ip.startswith(p + "."):
            continue
        if best is None or len(p) > len(best.subnet_prefix or ""):
            best = n
    return best


def device_row(db: DbSession, tenant_id: str, mac: str, net: m.Network | None):
    """The `Device` row a device route should act on.

    With `?network=` given it is unambiguous. Without, a MAC present on more
    than one network resolves to the row with the newest `last_seen` -- never
    an arbitrary `.first()` -- and the response's `network` field says which.
    """
    q = db.query(m.Device).filter_by(tenant_id=tenant_id, mac=mac)
    if net is not None:
        q = q.filter(m.Device.network_id == net.id)
    rows = q.all()
    if not rows:
        return None
    rows.sort(key=lambda d: (d.last_seen or "", d.id), reverse=True)
    return rows[0]


# --------------------------------------------------------------------------
# Health / output shapes
# --------------------------------------------------------------------------

def ref(net: m.Network | None) -> dict | None:
    """The `NetworkRef` every network-scoped row carries."""
    if net is None:
        return None
    return {"id": net.id, "slug": net.slug, "name": net.name, "colour": net.colour or ""}


def refs_by_id(db: DbSession, tenant_id: str) -> dict[str, dict]:
    return {n.id: ref(n) for n in db.query(m.Network).filter_by(tenant_id=tenant_id).all()}


def _sensor_ok(sensors: dict, sensor_id: str | None) -> bool:
    s = sensors.get(sensor_id) if sensor_id else None
    if s is None or s.last_seen is None:
        return False
    return (_now() - s.last_seen).total_seconds() <= SENSOR_STALE_S


def counts(db: DbSession, tenant_id: str, network_id: str, raw: dict | None = None) -> dict:
    """Device/online/unverified/camera counts for one network.

    `cameras` goes through `assets.kind_of` rather than reading `Device.kind`
    straight, so a row the sensor never classified (an imported or seeded
    tenant) is still counted from its vendor/name/ports -- the count on the
    chip and the cards in the Cameras group must never disagree.
    """
    from .payloads.assets import kind_of                  # local: avoids an import cycle
    from .payloads.overview import unverified_devices     # local: avoids an import cycle

    rows = db.query(m.Device).filter_by(tenant_id=tenant_id, network_id=network_id).all()
    cameras = [d for d in rows if kind_of(d) == "camera"]
    return {
        "devices": len(rows),
        "online": sum(1 for d in rows if d.presence == "ONLINE"),
        "unverified": len(unverified_devices(rows, raw or {})),
        "cameras": len(cameras),
        "cameras_online": sum(1 for d in cameras if d.presence == "ONLINE"),
    }


def health(db: DbSession, tenant, raw: dict | None = None) -> list[dict]:
    """The overview's per-network strip: counts, open CRIT/WARN, whether the
    network's sensor has checked in inside `SENSOR_STALE_S`, and a rolled-up
    `state` (`good|warn|bad`) the UI colours the chip with.

    `raw` is the latest scan's payload; every caller already has it loaded, so
    pass it in rather than making this re-read the scan row (it is only needed
    for the unverified count).
    """
    if raw is None:
        from .scans import latest_scan
        scan = latest_scan(db, tenant.id)
        raw = scan.raw if scan else {}
    sensors = {s.id: s for s in db.query(m.Sensor).filter_by(tenant_id=tenant.id).all()}
    rows = db.query(m.Network).filter_by(tenant_id=tenant.id).all()
    rows.sort(key=lambda n: (not n.is_primary, (n.name or "").lower()))

    out = []
    for n in rows:
        c = counts(db, tenant.id, n.id, raw)
        open_alerts = db.query(m.Alert).filter(
            m.Alert.tenant_id == tenant.id, m.Alert.network_id == n.id,
            m.Alert.status.notin_(m.SETTLED_ALERT_STATUSES)).all()
        crit = sum(1 for a in open_alerts if a.sev == "CRIT")
        warn = sum(1 for a in open_alerts if a.sev == "WARN")
        ok = _sensor_ok(sensors, n.sensor_id)
        if crit or n.status == "unreachable":
            state = "bad"
        elif warn or not ok or n.status != "active":
            state = "warn"
        else:
            state = "good"
        out.append({
            "id": n.id, "slug": n.slug, "name": n.name, "colour": n.colour or "",
            "state": state, "status": n.status, "devices": c["devices"],
            "online": c["online"], "unverified": c["unverified"],
            "cameras": c["cameras"], "cameras_online": c["cameras_online"],
            "crit": crit, "warn": warn, "sensor_ok": ok,
            "is_primary": bool(n.is_primary),
        })
    return out


def out(db: DbSession, net: m.Network, raw: dict | None = None, sensor_names: dict | None = None) -> dict:
    """The full `Network` API shape."""
    c = counts(db, net.tenant_id, net.id, raw)
    names = sensor_names if sensor_names is not None else {
        s.id: s.name for s in db.query(m.Sensor).filter_by(tenant_id=net.tenant_id).all()
    }
    return {
        "id": net.id, "slug": net.slug, "name": net.name, "kind": net.kind,
        "subnet_prefix": net.subnet_prefix, "expected_gateway": net.expected_gateway,
        "ssid": net.ssid, "sensor_id": net.sensor_id,
        "sensor_name": names.get(net.sensor_id, "") if net.sensor_id else "",
        "colour": net.colour or "", "is_primary": bool(net.is_primary), "status": net.status,
        "devices": c["devices"], "online": c["online"], "unverified": c["unverified"],
        "cameras": c["cameras"],
        "created": net.created.isoformat(timespec="seconds") if net.created else "",
        "traffic_mode": net.traffic_mode,
    }
