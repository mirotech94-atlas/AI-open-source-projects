"""Networks CRUD (Task 51 / spec S3.1).

Reads are viewer+, writes are admin. Every write does three things in one
transaction:

1. mutates the `Network` row(s);
2. audits `network.create|update|delete` with user/role/tenant/IP;
3. writes exactly ONE `Edit(path="/api/config", body={"config": <the whole
   Tenant.config, with the rebuilt networks[] inside>})`.

(3) must be the *whole* config: the sensor's `sync.apply_edit` handles
`/api/config` by doing `cfg.clear(); cfg.update(body["config"])`, so a partial
body would wipe the sensor's sites, hosts, thresholds and auto-fix policy.
There is deliberately no `/api/networks` edit path -- the networks list is a
block inside the config the sensor already replays.
"""

import re

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy.orm import Session as DbSession

from .. import audit, models as m, networks, schemas_dash as sd
from ..db import get_db
from ..deps import require_role
from ..live import bus
from ..scans import latest_scan

router = APIRouter(prefix="/api/v1", tags=["dashboard-networks"])

VIEWER = Depends(require_role("viewer"))
ADMIN = Depends(require_role("admin"))

_IP_RE = re.compile(r"^(\d{1,3}\.){3}\d{1,3}$")


def _ip(request: Request) -> str:
    return request.client.host if request.client else ""


class NetworkIn(BaseModel):
    name: str = ""
    kind: str = "wifi"
    subnet_prefix: str = ""
    expected_gateway: str = ""
    ssid: str | None = None
    sensor_id: str | None = None
    colour: str | None = None
    # A person's declaration, made in the wizard or Settings -- never guessed
    # from traffic itself. `payloads/traffic.mode_for` crosses this against
    # what the sensor actually reports before ever calling a network "mirror".
    traffic_mode: str = "none"


class NetworkPatch(BaseModel):
    name: str | None = None
    kind: str | None = None
    subnet_prefix: str | None = None
    expected_gateway: str | None = None
    ssid: str | None = None
    sensor_id: str | None = None
    colour: str | None = None
    traffic_mode: str | None = None


def _validate(db: DbSession, tenant_id: str, name: str, kind: str, prefix: str, gateway: str,
              sensor_id: str | None, exclude_id: str | None = None,
              check_slug: bool = True, require_prefix: bool = True) -> None:
    if not (name or "").strip():
        raise HTTPException(422, "name is required")
    if kind not in networks.KINDS:
        raise HTTPException(422, "kind must be one of " + ", ".join(networks.KINDS))
    # A prefix is mandatory on create. On PATCH it is only checked when the
    # patch actually carries one: `0007_networks` leaves the migrated primary of
    # a tenant whose config had no readable subnet with an empty `subnet_prefix`,
    # and requiring one here would 422 every unrelated patch (a rename, a
    # colour) on that row.
    if (prefix or require_prefix) and not networks.SUBNET_RE.match(prefix or ""):
        raise HTTPException(422, "subnet_prefix must be three octets, e.g. 192.168.1")
    if gateway:
        if not _IP_RE.match(gateway):
            raise HTTPException(422, "expected_gateway must be an IPv4 address")
        if prefix and not gateway.startswith(prefix + "."):
            raise HTTPException(422, f"expected_gateway must be inside {prefix}.0/24")
    if check_slug:
        # Only on create: PATCH never re-slugs, so a rename cannot collide.
        slug = networks.slugify(name)
        dup = db.query(m.Network).filter_by(tenant_id=tenant_id, slug=slug)
        if exclude_id:
            dup = dup.filter(m.Network.id != exclude_id)
        if dup.first():
            raise HTTPException(422, f"a network with slug {slug} already exists")
    if sensor_id and not db.query(m.Sensor).filter_by(tenant_id=tenant_id, id=sensor_id).first():
        raise HTTPException(422, "no such sensor")


def _commit_write(db: DbSession, tenant, user, role: str, request: Request, action: str,
                  target: str, body: dict) -> None:
    """Rebuild `Tenant.config["networks"]`, audit, emit the whole-config Edit,
    commit, and tell every connected dashboard."""
    db.flush()
    cfg = dict(tenant.config or {})
    # `cfg` in, so every per-entry key the hub has no column for (S3.1's
    # `interface?`) survives instead of being dropped by the rebuild.
    cfg["networks"] = networks.to_config_entries(db, tenant.id, cfg)
    tenant.config = cfg
    db.add(m.Edit(tenant_id=tenant.id, who=user.username, path="/api/config",
                   body={"config": cfg}))
    audit.log(db, tenant.id, user.username, role, action, target=target, ip=_ip(request), body=body)
    db.commit()
    bus.publish(tenant.id, "network", {"action": action, "target": target})


@router.get("/networks", response_model=sd.NetworksPayload)
def list_networks(ctx=VIEWER, db: DbSession = Depends(get_db)):
    user, tenant, role = ctx
    scan = latest_scan(db, tenant.id)
    raw = scan.raw if scan else {}
    names = {s.id: s.name for s in db.query(m.Sensor).filter_by(tenant_id=tenant.id).all()}
    rows = db.query(m.Network).filter_by(tenant_id=tenant.id).all()
    rows.sort(key=lambda n: (not n.is_primary, (n.name or "").lower()))
    return {"networks": [networks.out(db, n, raw, names) for n in rows]}


@router.post("/networks", response_model=sd.Network)
def create_network(body: NetworkIn, request: Request, ctx=ADMIN, db: DbSession = Depends(get_db)):
    user, tenant, role = ctx
    _validate(db, tenant.id, body.name, body.kind, body.subnet_prefix, body.expected_gateway,
              body.sensor_id)
    if body.traffic_mode not in networks.TRAFFIC_MODES:
        raise HTTPException(422, "traffic_mode must be one of " + ", ".join(networks.TRAFFIC_MODES))
    row = m.Network(
        tenant_id=tenant.id, name=body.name.strip(), slug=networks.slugify(body.name),
        kind=body.kind, subnet_prefix=body.subnet_prefix, expected_gateway=body.expected_gateway,
        ssid=body.ssid or "", sensor_id=body.sensor_id or None, colour=body.colour or "",
        is_primary=False, status="active" if body.sensor_id else "no-sensor",
        traffic_mode=body.traffic_mode,
    )
    db.add(row)
    _commit_write(db, tenant, user, role, request, "network.create", row.slug,
                  body.model_dump(exclude_none=True))
    return networks.out(db, row)


@router.patch("/networks/{net_id}", response_model=sd.Network)
def update_network(net_id: str, body: NetworkPatch, request: Request, ctx=ADMIN,
                   db: DbSession = Depends(get_db)):
    user, tenant, role = ctx
    row = db.query(m.Network).filter_by(tenant_id=tenant.id, id=net_id).first()
    if not row:
        raise HTTPException(404, "no such network")
    patch = body.model_dump(exclude_unset=True)
    # `is_primary` is read-only: the primary is whichever network the tenant's
    # first config entry names, and it is the attribution fallback -- moving it
    # would silently re-home every row that names no network.
    patch.pop("is_primary", None)
    name = patch.get("name") if patch.get("name") is not None else row.name
    kind = patch.get("kind") if patch.get("kind") is not None else row.kind
    prefix = patch.get("subnet_prefix") if patch.get("subnet_prefix") is not None else row.subnet_prefix
    gateway = patch.get("expected_gateway") if patch.get("expected_gateway") is not None else row.expected_gateway
    sensor_id = patch.get("sensor_id") if "sensor_id" in patch else row.sensor_id
    _validate(db, tenant.id, name, kind, prefix, gateway, sensor_id, exclude_id=row.id,
              check_slug=False, require_prefix="subnet_prefix" in patch)
    if "traffic_mode" in patch and patch["traffic_mode"] not in networks.TRAFFIC_MODES:
        raise HTTPException(422, "traffic_mode must be one of " + ", ".join(networks.TRAFFIC_MODES))

    row.name = (name or "").strip()
    # The slug is FROZEN at creation, like `is_primary`. It is a durable handle:
    # `?network=<slug>` links, the sensor's config entry, and -- decisively --
    # the network segment of every LAN alert key (`lan:<slug>:new:<mac>`).
    # Re-slugging on rename would orphan every one of that network's existing
    # alerts: the old key can no longer appear in `state.alerts_db`, so it would
    # never close, while the next ingest opened a duplicate under the new slug.
    row.kind, row.subnet_prefix, row.expected_gateway = kind, prefix, gateway
    if "ssid" in patch:
        row.ssid = patch["ssid"] or ""
    if "colour" in patch:
        row.colour = patch["colour"] or ""
    if "traffic_mode" in patch:
        row.traffic_mode = patch["traffic_mode"]
    if "sensor_id" in patch:
        row.sensor_id = patch["sensor_id"] or None
        if not row.sensor_id and row.status == "active":
            row.status = "no-sensor"
    _commit_write(db, tenant, user, role, request, "network.update", row.slug, patch)
    return networks.out(db, row)


@router.delete("/networks/{net_id}")
def delete_network(net_id: str, request: Request, reassign: str = "", ctx=ADMIN,
                   db: DbSession = Depends(get_db)):
    user, tenant, role = ctx
    row = db.query(m.Network).filter_by(tenant_id=tenant.id, id=net_id).first()
    if not row:
        raise HTTPException(404, "no such network")
    if row.is_primary:
        # 422, not 409: the brief's contract test pins this status, and it is
        # the same "your request is not a valid one" code every other network
        # validation failure returns.
        raise HTTPException(422, "the primary network cannot be deleted -- it is the attribution "
                                 "fallback for every row that names no network. Make another "
                                 "network primary in the config first.")

    # The 422 is over the UNION of every scoped table, not `device` alone: a
    # network with no devices but events/alerts/dns queries/presence samples on
    # it must not be deleted silently, because there is no FK on `network_id` --
    # those rows would keep a dangling id forever, invisible to every
    # `?network=` filter and counted in no network's health.
    held = networks.row_counts(db, tenant.id, row.id)
    total = sum(held.values())
    target = None
    if reassign:
        # `resolve` accepts an id OR a slug (same as every other `?network=`
        # consumer) and is scoped to THIS tenant, so a reassign target that
        # belongs to another tenant is indistinguishable from one that does
        # not exist at all -- both 404. Only a target that genuinely IS this
        # tenant's but is invalid for the operation (the row being deleted
        # itself) is a 422.
        target = networks.resolve(db, tenant.id, reassign)
        if target is None or target.id == row.id:
            raise HTTPException(422, "reassign must name another network of this tenant")
    elif total:
        raise HTTPException(422, f"{total} row(s) are still on this network "
                                 f"({', '.join(f'{k}={v}' for k, v in held.items() if v)}) -- pass "
                                 "?reassign=<other network id> to move them first")
    # Always reassign, even when the counts read zero: the count and the delete
    # are not one statement, and a row landing in between must not be orphaned.
    prim = networks.primary(db, tenant.id)
    dest = target or prim
    moved: dict = {}
    dropped: dict = {}
    if dest is not None and dest.id != row.id:
        moved = networks.reassign(db, tenant.id, row.id, dest.id)
        # `dropped` (clash-deleted duplicates, e.g. a moved alert colliding
        # with one already on the destination) is reported alongside `moved`
        # rather than folded into it, so `moved` keeps meaning "rows that now
        # live on the destination" and callers checking its counts (the exact
        # per-table equality in test_networks.py) don't have to know about it.
        dropped = moved.pop("dropped", {})

    slug = row.slug
    db.delete(row)
    _commit_write(db, tenant, user, role, request, "network.delete", slug,
                  {"slug": slug, "reassign": reassign, "moved": moved, "dropped": dropped})
    return {"ok": True, "moved": moved, "dropped": dropped}
