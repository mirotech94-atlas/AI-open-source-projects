"""Topology payload: the network map -- internet, router, extenders, every
other device, plus a server node per `Host` and a site node per `Site`
(Task 31 / spec S10).

Two divergences from the task-31 brief's prose, both forced by
`schemas_dash.py` (binding: `web/src/types.ts` mirrors it field-for-field,
`tests/test_contracts.py` pins that mirror, and this module must never
add/rename a field -- see `payloads/security.py`'s docstring for the same
reasoning applied there):

- `TopoNode.ring` is `int`, not the brief's `none|good|warn|alert` string.
  The schema's own comment defines it as a radial-layout depth (0 internet,
  1 gateway, 2 infra, 3 leaves) -- genuinely useful for a client-side
  radial layout, and not redundant with `risk`/`state`, which already carry
  the risk signal the brief's prose wanted `ring` to encode.
- `TopoNode.state` is a free `string` in the TS mirror (no literal union),
  so this module uses the brief's own `online|offline|sleeping|flapping|
  archived` vocabulary -- more informative than the schema comment's
  `ok|warn|crit|offline` and nothing pins either one.

`clusters(cluster_by="area")` groups by `Device.extra["area"]`, defaulting
every node (device or not) to `"Home"` when absent -- so a fixture with no
`area` key anywhere collapses to the single fallback cluster the brief
describes, without a separate code path.
"""

from sqlalchemy.orm import Session as DbSession

from .. import models as m, networks
from ..scans import latest_scan, parse_ts
from ..schemas_dash import NetworkHealth, TopoCluster, TopoLink, TopoNode, TopologyPayload
from . import assets as assets_payload
from ._util import effective_presence, device_online, _live_macs, _now

# assets.KIND_GROUPS covers every device kind_of() can return; topology adds
# three node kinds that never come from kind_of (router/extender ARE device
# kinds already in that map -- these are the three non-device node kinds).
_EXTRA_GROUPS = {"site": "Sites", "internet": "Internet"}

ARCHIVED_AFTER_DAYS = 14
SLEEPING_WITHIN_DAYS = 1


def _group_of(kind: str) -> str:
    if kind in _EXTRA_GROUPS:
        return _EXTRA_GROUPS[kind]
    return assets_payload.group_of(kind)


def _device_state(d: m.Device, live: set[str]) -> str:
    if effective_presence(d) == "UNKNOWN":
        return "unknown"
    if (d.extra or {}).get("flapping"):
        return "flapping"
    if device_online(d):
        observed = parse_ts(d.last_seen)
        return "online" if observed and (_now()-observed).total_seconds() <= 900 else "unknown"
    when = parse_ts(d.last_seen)
    if when is None:
        return "archived"
    age_days = (_now() - when).days
    if age_days >= ARCHIVED_AFTER_DAYS:
        return "archived"
    if d.always_on:
        return "offline"
    if age_days <= SLEEPING_WITHIN_DAYS:
        return "sleeping"
    return "offline"


def _alerts_for(n: TopoNode, open_rows) -> list:
    """The open alerts that point at this node. A device's alerts carry its
    MAC in the key (lan:*:<mac>, sec:*:<mac>); a host's or site's carry its
    name as a key segment."""
    if n.kind == "internet":
        return []
    needle = (n.mac or "").lower() if n.mac else None
    name = n.label if n.kind in ("server", "site") else None
    out = []
    for a in open_rows:
        key = (a.key or "")
        hit = bool(needle and needle in key.lower())
        if not hit and name:
            hit = f":{name}:" in key or key.endswith(f":{name}")
        if hit:
            out.append(a)
    return out


def _alert_sev_for(n: TopoNode, open_rows) -> str | None:
    """CRIT beats WARN; None when nothing open points at this node."""
    sevs = {a.sev for a in _alerts_for(n, open_rows)}
    if "CRIT" in sevs:
        return "CRIT"
    return "WARN" if "WARN" in sevs else None


def _detail(label: str, ip: str, vendor: str, risk: int) -> str:
    return f"{label} · {ip} · {vendor} · risk {risk}"


def _device_node(d: m.Device, live: set[str], kind: str, netrefs: dict | None = None) -> TopoNode:
    label = d.label or d.name or d.mac
    return TopoNode(
        network=(netrefs or {}).get(d.network_id),
        id=d.mac, kind=kind, group=_group_of(kind), label=label, ip=d.ip or "", mac=d.mac,
        icon=kind, state=_device_state(d, live), risk=int(d.risk or 0),
        trusted=bool(d.trusted), quarantined=bool(d.quarantined),
        pulse=_device_state(d, live) == "online", detail=_detail(label, d.ip or "", d.vendor or "", int(d.risk or 0)),
    )


def _host_node(h: m.Host) -> TopoNode:
    last = h.last or {}
    reachable = last.get("reachable")
    stamp = parse_ts(h.last_seen)
    fresh = stamp is not None and (_now()-stamp).total_seconds() <= 1200
    state = ("online" if reachable else "offline") if fresh and reachable is not None else "unknown"
    return TopoNode(
        id=f"host:{h.name}", kind="server", group=_group_of("server"), label=h.name,
        ip=h.address or "", mac=None, icon="server", state=state, risk=0,
        trusted=True, quarantined=False, pulse=bool(reachable) and fresh,
        detail=_detail(h.name, h.address or "", "", 0),
    )


def _site_node(s: m.Site) -> TopoNode:
    last = s.last or {}
    ok = last.get("ok")
    stamp = parse_ts(s.last_seen)
    fresh = stamp is not None and (_now()-stamp).total_seconds() <= 1200
    state = ("online" if ok else "offline") if fresh and ok is not None else "unknown"
    return TopoNode(
        id=f"site:{s.name}", kind="site", group=_group_of("site"), label=s.name,
        ip=s.url or "", mac=None, icon="site", state=state, risk=0,
        trusted=bool(s.public), quarantined=False, pulse=bool(ok) and fresh,
        detail=_detail(s.name, s.url or "", "", 0),
    )


def _internet_node() -> TopoNode:
    return TopoNode(id="internet", kind="internet", group=_group_of("internet"), label="Internet",
                     ip="", mac=None, icon="internet", state="online", risk=0,
                     trusted=True, quarantined=False, pulse=True, detail="Internet")


def build(db: DbSession, tenant, cluster_by: str = "kind", net=None) -> TopologyPayload:
    """`net` narrows the map to one network. Non-device nodes (the internet, a
    synthesized gateway, `Host`/`Site` nodes) carry `network=None` -- they are
    not attached to any one LAN -- so a filtered map is "every node that is
    either on this network or network-agnostic"."""
    scan = latest_scan(db, tenant.id)
    raw = scan.raw if scan else {}
    live = _live_macs(raw)
    gw_mac = ((raw.get("lan") or {}).get("gw_mac")) or ""
    netrefs = networks.refs_by_id(db, tenant.id)

    dq = db.query(m.Device).filter_by(tenant_id=tenant.id)
    if net is not None:
        dq = dq.filter(m.Device.network_id == net.id)
    devices = dq.order_by(m.Device.mac).all()
    hosts = db.query(m.Host).filter_by(tenant_id=tenant.id).order_by(m.Host.name).all()
    sites = db.query(m.Site).filter_by(tenant_id=tenant.id).order_by(m.Site.name).all()

    device_by_mac = {d.mac: d for d in devices}
    area_by_id: dict[str, str] = {}

    nodes: list[TopoNode] = [_internet_node()]
    area_by_id["internet"] = "Home"
    ring_by_id: dict[str, int] = {"internet": 0}

    router_mac = gw_mac if gw_mac in device_by_mac else None
    if router_mac is None:
        # No device matches the scan's gw_mac (empty tenant, or a fixture
        # that never named a router) -- synthesize one rather than leaving
        # the graph without a gateway node, so it is always fully connected.
        router_id = "router"
        nodes.append(TopoNode(id=router_id, kind="router", group=_group_of("router"), label="Router",
                               ip="", mac=None, icon="router", state="unknown", risk=0,
                               trusted=False, quarantined=False, pulse=False,
                               detail="Inferred gateway position; no current device observation"))
        area_by_id[router_id] = "Home"
    else:
        router_id = router_mac
        d = device_by_mac[router_mac]
        node = _device_node(d, live, "router", netrefs)
        nodes.append(node)
        area_by_id[router_id] = (d.extra or {}).get("area") or "Home"
    ring_by_id[router_id] = 1

    links: list[TopoLink] = [TopoLink(source=router_id, target="internet", kind="wan",
                                       pulse=next(n.pulse for n in nodes if n.id == router_id))]

    extender_ids: set[str] = set()
    for d in devices:
        if d.mac == router_mac:
            continue
        kind = assets_payload.kind_of(d)
        if kind == "extender":
            node = _device_node(d, live, "extender", netrefs)
            nodes.append(node)
            extender_ids.add(d.mac)
            area_by_id[d.mac] = (d.extra or {}).get("area") or "Home"
            ring_by_id[d.mac] = 2

    for d in devices:
        if d.mac == router_mac or d.mac in extender_ids:
            continue
        kind = assets_payload.kind_of(d)
        node = _device_node(d, live, kind, netrefs)
        nodes.append(node)
        area_by_id[d.mac] = (d.extra or {}).get("area") or "Home"
        ring_by_id[d.mac] = 3

    all_ids = {n.id for n in nodes}
    for d in devices:
        if d.mac == router_mac:
            continue
        via = (d.extra or {}).get("via")
        if d.parent and d.parent in all_ids:
            target = d.parent
        elif via and via in all_ids:
            target = via
        else:
            target = router_id
        source_kind = "extender" if d.mac in extender_ids else assets_payload.kind_of(d)
        link_kind = "uplink" if source_kind == "extender" else "lan"
        pulse = _device_state(d, live) == "online"
        links.append(TopoLink(source=d.mac, target=target, kind=link_kind, pulse=pulse))

    for h in hosts:
        node = _host_node(h)
        nodes.append(node)
        area_by_id[node.id] = "Home"
        ring_by_id[node.id] = 3
        links.append(TopoLink(source=node.id, target="internet", kind="wan", pulse=node.pulse))

    for s in sites:
        node = _site_node(s)
        nodes.append(node)
        area_by_id[node.id] = "Home"
        ring_by_id[node.id] = 3
        links.append(TopoLink(source=node.id, target="internet", kind="wan", pulse=node.pulse))

    # Worst open alert per node -- the signal that colours the link to it.
    # A device's alerts carry its MAC in the key (lan:*:<mac>, sec:*:<mac>);
    # a host's or site's carry its name as a key segment.
    open_rows = db.query(m.Alert).filter(m.Alert.tenant_id == tenant.id,
                                          m.Alert.status == "open",
                                          m.Alert.sev.in_(["WARN", "CRIT"])).all()
    for n in nodes:
        n.ring = ring_by_id.get(n.id, 3)
        n.alert_sev = _alert_sev_for(n, open_rows)
        n.alert_keys = [a.key for a in _alerts_for(n, open_rows)]

    return TopologyPayload(nodes=nodes, links=links,
                           clusters=build_clusters(nodes, area_by_id, cluster_by,
                                                   net.id if net is not None else None),
                           networks=[NetworkHealth(**h) for h in networks.health(db, tenant, raw)])


def build_clusters(nodes: list[TopoNode], area_by_id: dict[str, str], cluster_by: str,
                   network_id: str | None = None) -> list[TopoCluster]:
    """`cluster_by="network"` draws spec S3.1's one-cluster-per-network map;
    `kind`/`area` keep the pre-network behaviour. A node with no network of its
    own (internet, gateway, hosts, sites) lands in a "Shared" cluster so the
    graph stays fully partitioned."""
    groups: dict[str, list[str]] = {}
    if cluster_by == "network":
        ids: dict[str, str | None] = {}
        for n in nodes:
            label = n.network.name if n.network else "Shared"
            groups.setdefault(label, []).append(n.id)
            ids[label] = n.network.id if n.network else None
        return [TopoCluster(id=label, label=label, members=mem, network_id=ids.get(label))
                for label, mem in groups.items()]
    if cluster_by == "area":
        for n in nodes:
            groups.setdefault(area_by_id.get(n.id, "Home"), []).append(n.id)
    else:
        for n in nodes:
            groups.setdefault(_group_of(n.kind), []).append(n.id)
    return [TopoCluster(id=label, label=label, members=ids, network_id=network_id)
            for label, ids in groups.items()]
