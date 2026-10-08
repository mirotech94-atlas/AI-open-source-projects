"""Who talked to whom, shaped for the page and the bot. Spec §4.

Answers "now and recent" only. A trend over time belongs in
`payloads/history.build_charts` -- the one place a trend is computed -- and
nowhere here. Every response carries `mode`, and `mode` is read from a
person's declaration (`Network.traffic_mode`) crossed with what the sensor
actually reported (`raw["capture"]`), never inferred from the rows: rows
from a router look like rows from a mirror, and only the mode says which
questions they can answer.
"""
from __future__ import annotations

import datetime as dt

from sqlalchemy.orm import Session as DbSession

from .. import models as m, networks
from ..scans import latest_scan, parse_ts
from ..schemas_dash import (ConnStreamRow, ConnectionDest, ConnectionDevice, ConnectionsPayload,
                            DeviceTraffic, TrafficOverview, TrafficPeer, TrafficTalker)
from ._util import _lax_cutoff, exact_window
from ..retention import policy

TOP_N = 10


#: Why there is no traffic to show, in the customer's words. Item 20: an
#: unwatched network and a quiet one used to render identically as
#: "Nothing unusual", which is the silent-absence failure this codebase
#: keeps finding -- the answer was not a wrong number, it was a question
#: nobody had been asked.
_WHY_NONE = {
    "not_asked": ("Vault is not watching traffic on this network. Nothing here is "
                  "quiet — nobody has turned it on."),
    "no_interface": ("Traffic is switched on for this network, but no capture interface "
                     "is set on the sensor, so there is nothing for it to read."),
    "no_tool": ("Traffic is switched on, but the sensor has neither tcpdump nor "
                "tshark installed."),
    "silent": "",
}


#: Which network's declaration speaks for the whole tenant when no network is
#: picked: the one that can see the most. `host` ranks below `router` -- a
#: router's counters cover every device, a host capture covers one.
_DECLARED_RANK = {"mirror": 3, "router": 2, "host": 1}
_WATCHING = ("mirror", "router", "host")


def why_no_traffic(db: DbSession, tenant, net: "m.Network | None") -> str:
    """The sentence a traffic panel shows instead of pretending it is quiet.

    Empty when Vault IS watching: then "nothing notable" is a true and
    useful answer, and the panel should say it.
    """
    mode, _ = mode_for(db, tenant, net)
    if mode in _WATCHING:
        return ""
    declared = net.traffic_mode if net is not None else max(
        (n.traffic_mode for n in db.query(m.Network).filter_by(tenant_id=tenant.id).all()),
        key=lambda s: _DECLARED_RANK.get(s, 0), default="none")
    if declared not in _WATCHING:
        return _WHY_NONE["not_asked"]
    scan = latest_scan(db, tenant.id)
    cap = ((scan.raw or {}).get("capture") or {}) if scan else {}
    if cap.get("error"):
        return _WHY_NONE["no_tool"] if "install" in cap["error"] else cap["error"]
    if (cap.get("mode") or "") == "off":
        return _WHY_NONE["no_interface"]
    return _WHY_NONE["not_asked"]


def mode_for(db: DbSession, tenant, net: "m.Network | None") -> tuple[str, bool]:
    scan = latest_scan(db, tenant.id)
    cap = ((scan.raw or {}).get("capture") or {}) if scan else {}
    declared = net.traffic_mode if net is not None else max(
        (n.traffic_mode for n in db.query(m.Network).filter_by(tenant_id=tenant.id).all()),
        key=lambda s: _DECLARED_RANK.get(s, 0), default="none")
    reported = cap.get("mode") or "none"
    # The sensor's reported mode wins whenever the network DECLARES mirror or
    # router -- a declared "none" stays "none" regardless of what the sensor
    # says. This repo has no router integration that can supply per-device
    # counts yet, so a network declared "router" with no rows must read as
    # "none", or the bot would claim "I can see bytes per device" about
    # nothing. A declared "mirror" with a silent port also honestly falls
    # through to whatever the sensor reports (typically "none").
    # The sensor now says "off" for "nobody configured an interface", which
    # is not a traffic mode -- it is the reason there is none. It collapses
    # to "none" here so every existing reader keeps its three-word
    # vocabulary, and `why_no_traffic` carries the distinction.
    if reported == "off":
        reported = "none"
    if declared == "host":
        # A capturing sensor reports `mirror` -- it cannot tell whether its
        # port is mirrored. The person has said it is not, so what it
        # captures is its own traffic and nothing more.
        mode = "host" if reported == "mirror" else "none"
    else:
        mode = reported if declared in ("mirror", "router") else "none"
    return mode, bool(cap.get("flows_dropped"))


def _window(hours: int) -> tuple[dt.datetime, str]:
    cutoff = dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=hours)
    return cutoff, _lax_cutoff(cutoff)


def _rows(db, tenant, cutoff, lax, net, mac=None):
    # `lax` is unused now and kept so the call sites and their tests stay as
    # they were: `_util.exact_window` derives both bounds itself, and the SQL
    # it returns is exact, so only its `odd` rows still need `parse_ts`.
    where, odd = exact_window(db, m.Flow.ts, m.Flow.tenant_id, tenant.id, cutoff)
    extra = []
    if net is not None:
        extra.append(m.Flow.network_id == net.id)
    if mac:
        extra.append(m.Flow.mac == mac.lower())
    rows = db.query(m.Flow).filter(where, *extra).all()
    rows += [r for r in db.query(m.Flow).filter(odd, *extra).all()
             if (parse_ts(r.ts) or cutoff) >= cutoff]
    # The old single query had no ORDER BY and came back in the order of the
    # `(tenant_id, ts)` index it ran on: by `ts` text, then rowid. Ties in the
    # top-talker and top-peer lists are broken by that insertion order (the
    # sorts below are stable), so it is reproduced here explicitly rather
    # than left to whatever plan SQLite picks for the OR'd predicate.
    rows.sort(key=lambda r: (r.ts or "", r.id))
    return rows


def _peer_label(r) -> str:
    return r.peer_name or r.peer_ip


def _seen_before(db, tenant, net, prev_cut: dt.datetime, cutoff: dt.datetime,
                 labels: "set[str]") -> set[str]:
    """Which of `labels` (the peers in the window on screen) were seen
    before `cutoff`.

    Asks `FlowPeer`, never the raw `flow` table. This used to be a DISTINCT
    over every raw flow from `prev_cut` to `cutoff` -- on the production home
    network 3.9 million rows and 70-76 seconds per Live connections or
    Topology load, warm or cold (2026-10-06). `prev_cut` (now minus raw
    retention) is kept in the signature for the callers' sake: `FlowPeer`
    already forgets a peer silent for longer than that (`retention`), which
    is the lower bound the scan used to apply.
    """
    from .. import flowpeers
    return flowpeers.seen_before(db, tenant.id, labels, cutoff, network_id=net.id if net is not None else None)


def overview(db: DbSession, tenant, hours: int = 1, net=None) -> TrafficOverview:
    mode, truncated = mode_for(db, tenant, net)
    cutoff, lax = _window(hours)
    rows = _rows(db, tenant, cutoff, lax, net)
    out = TrafficOverview(mode=mode, truncated=truncated, window_hours=hours,
                          why_none=why_no_traffic(db, tenant, net))
    if not rows:
        return out
    by_mac: dict[str, dict] = {}
    by_peer: dict[str, dict] = {}
    ext_bytes = total = 0
    for r in rows:
        d = by_mac.setdefault(r.mac, {"bi": 0, "bo": 0, "ip": r.ip, "peers": {}})
        d["bi"] += r.bytes_in
        d["bo"] += r.bytes_out
        d["peers"][_peer_label(r)] = d["peers"].get(_peer_label(r), 0) + r.bytes_in + r.bytes_out
        p = by_peer.setdefault(_peer_label(r), {"ip": r.peer_ip, "port": r.peer_port, "proto": r.proto,
                                                 "bi": 0, "bo": 0, "first": r.ts, "last": r.ts})
        p["bi"] += r.bytes_in
        p["bo"] += r.bytes_out
        p["first"] = min(p["first"], r.ts)
        p["last"] = max(p["last"], r.ts)
        total += r.bytes_in + r.bytes_out
        if r.peer_ip and not networks.is_private_ip(r.peer_ip):
            ext_bytes += r.bytes_in + r.bytes_out
    # One query for every device name at once, never one per talker.
    devs = {d.mac: d for d in db.query(m.Device).filter(m.Device.tenant_id == tenant.id, m.Device.mac.in_(list(by_mac))).all()}
    out.top_talkers = [
        TrafficTalker(
            mac=mac, ip=d["ip"], bytes_in=d["bi"], bytes_out=d["bo"],
            name=(lambda dv: (dv.label or dv.name or dv.vendor or mac) if dv else mac)(devs.get(mac)),
            top_peer=max(d["peers"], key=d["peers"].get) if d["peers"] else "",
        )
        for mac, d in sorted(by_mac.items(), key=lambda kv: -(kv[1]["bi"] + kv[1]["bo"]))[:TOP_N]
    ]
    out.top_peers = [
        TrafficPeer(peer=k, peer_ip=p["ip"], peer_port=p["port"], proto=p["proto"],
                    bytes_in=p["bi"], bytes_out=p["bo"], first_seen=p["first"], last_seen=p["last"])
        for k, p in sorted(by_peer.items(), key=lambda kv: -(kv[1]["bi"] + kv[1]["bo"]))[:TOP_N]
    ]
    out.external_share = round(ext_bytes / total, 3) if total else 0.0
    # New peers: unseen in the previous flow_raw_days before this window --
    # the raw table's own retention floor, not an arbitrary lookback, so a
    # tenant whose raw window is shorter (or longer, see retention.policy)
    # gets the identical floor other flow features use.
    #
    # This must scan ALL of `by_peer`, not just `out.top_peers`: the whole
    # point of a "new peer" callout is to surface a peer that is NOT among
    # the biggest talkers -- a single new outbound connection to somewhere
    # unfamiliar, buried under a device's ordinary CDN traffic. Restricting
    # to the top-10-by-bytes would hide exactly the row a security product
    # exists to show.
    raw_days = policy(tenant)["flow_raw_days"]
    prev_cut = cutoff - dt.timedelta(days=raw_days)
    seen_before = _seen_before(db, tenant, net, prev_cut, cutoff, set(by_peer))
    new = [(k, p) for k, p in by_peer.items() if k not in seen_before]
    new.sort(key=lambda kv: -(kv[1]["bi"] + kv[1]["bo"]))
    out.new_peers = [
        TrafficPeer(peer=k, peer_ip=p["ip"], peer_port=p["port"], proto=p["proto"],
                    bytes_in=p["bi"], bytes_out=p["bo"], first_seen=p["first"], last_seen=p["last"])
        for k, p in new[:TOP_N]
    ]
    return out


def device(db: DbSession, tenant, mac: str, hours: int = 24, net=None) -> DeviceTraffic:
    mode, _ = mode_for(db, tenant, net)
    cutoff, lax = _window(hours)
    rows = _rows(db, tenant, cutoff, lax, net, mac=mac)
    out = DeviceTraffic(mode=mode, mac=mac.lower(), window_hours=hours, hourly=[0] * hours)
    peers: dict[tuple, dict] = {}
    for r in rows:
        out.bytes_in += r.bytes_in
        out.bytes_out += r.bytes_out
        k = (_peer_label(r), r.peer_port, r.proto)
        p = peers.setdefault(k, {"ip": r.peer_ip, "bi": 0, "bo": 0, "first": r.ts, "last": r.ts})
        p["bi"] += r.bytes_in
        p["bo"] += r.bytes_out
        p["first"] = min(p["first"], r.ts)
        p["last"] = max(p["last"], r.ts)
        when = parse_ts(r.ts) or cutoff
        idx = min(hours - 1, max(0, int((when - cutoff).total_seconds() // 3600)))
        out.hourly[idx] += r.bytes_in + r.bytes_out
    out.peers = [
        TrafficPeer(peer=k[0], peer_ip=p["ip"], peer_port=k[1], proto=k[2],
                    bytes_in=p["bi"], bytes_out=p["bo"], first_seen=p["first"], last_seen=p["last"])
        for k, p in sorted(peers.items(), key=lambda kv: -(kv[1]["bi"] + kv[1]["bo"]))[:50]
    ]
    return out


def server(db: DbSession, tenant, host: str) -> DeviceTraffic | None:
    """A configured server by its `ssh_hosts` name: the customer says "prod",
    never a MAC. Resolved to the device row whose IP the entry targets."""
    target = next((h.get("target", "") for h in (tenant.config or {}).get("ssh_hosts") or [] if h.get("name") == host), "")
    ip = target.split("@")[-1] if target else ""
    dev = db.query(m.Device).filter_by(tenant_id=tenant.id, ip=ip).first() if ip else None
    return device(db, tenant, dev.mac) if dev else None


# --------------------------------------------------------------------------
# Live connections -- the `flow` live frame
# --------------------------------------------------------------------------

#: Rows sent the moment a socket subscribes, so the tab is not empty until the
#: sensor's next one-minute capture file lands.
STREAM_REPLAY_LIMIT = 40


def stream_row(f: "m.Flow", device: str = "") -> ConnStreamRow:
    """One stored `Flow` as a Live connections row. The single place that
    shape is built -- ingest, the demo trickle and the subscribe replay all
    call it, so the three can never disagree about what a row shows."""
    sni = ((f.hints or {}).get("sni") or "") if isinstance(f.hints, dict) else ""
    return ConnStreamRow(
        ts=f.ts or "", mac=f.mac or "", ip=f.ip or "", device=device,
        peer=sni or f.peer_name or f.peer_ip or "", peer_ip=f.peer_ip or "",
        peer_port=int(f.peer_port or 0), proto=f.proto or "",
        bytes_in=int(f.bytes_in or 0), bytes_out=int(f.bytes_out or 0),
    )


def device_names(db: DbSession, tenant_id: str, macs) -> dict[str, str]:
    """`{mac: label or name}` for the devices in `macs`, in one query."""
    macs = {x for x in macs if x}
    if not macs:
        return {}
    return {d.mac: (d.label or d.name or "") for d in db.query(m.Device).filter(
        m.Device.tenant_id == tenant_id, m.Device.mac.in_(macs)).all()}


def recent_stream_rows(db: DbSession, tenant_id: str, limit: int = STREAM_REPLAY_LIMIT) -> list[ConnStreamRow]:
    """The tenant's most recently stored flows, oldest first. By insert order
    (`id`), not `ts`: the question is "what just arrived", and a sensor
    catching up after an outage delivers older minutes last."""
    rows = (db.query(m.Flow).filter(m.Flow.tenant_id == tenant_id)
            .order_by(m.Flow.id.desc()).limit(limit).all())
    rows.reverse()
    names = device_names(db, tenant_id, {r.mac for r in rows})
    return [stream_row(r, names.get(r.mac, "")) for r in rows]


def connections(db: DbSession, tenant, minutes: int = 15, net=None) -> ConnectionsPayload:
    """The Live connections page: who talked to what in the last `minutes`,
    one row per device, its destinations named in words.

    The page used to be a raw stream -- `api.anthropic.com  tcp/443  3.8 MB`,
    newest first -- which answered "what is happening" only for someone who
    already reads flow logs. Grouped by device, a person can see at once that
    the Mac talked to Anthropic and Slack and nothing strange, and a
    destination nobody on the network had contacted before carries `is_new`.
    The raw stream is still on the page, behind a disclosure; this is the
    reading of it.

    Naming is `services.identify`: sure only when a hostname says whose it is,
    and never a guessed company for a bare address.
    """
    from . import services

    mode, _ = mode_for(db, tenant, net)
    out = ConnectionsPayload(mode=mode, window_minutes=minutes,
                             why_none=why_no_traffic(db, tenant, net))
    cutoff = dt.datetime.now(dt.timezone.utc) - dt.timedelta(minutes=minutes)
    rows = _rows(db, tenant, cutoff, _lax_cutoff(cutoff), net)
    if not rows:
        return out
    seen_before = _seen_before(db, tenant, net, cutoff - dt.timedelta(days=policy(tenant)["flow_raw_days"]), cutoff,
                               {_peer_label(r) for r in rows})
    names = device_names(db, tenant.id, {r.mac for r in rows})
    by_ip = {d.ip: (d.label or d.name or d.ip) for d in db.query(m.Device).filter(
        m.Device.tenant_id == tenant.id, m.Device.ip.in_({r.peer_ip for r in rows if r.peer_ip})).all()}

    devices: dict[str, ConnectionDevice] = {}
    dests: dict[tuple, ConnectionDest] = {}
    for r in rows:
        dev = devices.setdefault(r.mac, ConnectionDevice(mac=r.mac, name=names.get(r.mac) or r.ip or r.mac, ip=r.ip or ""))
        dev.bytes_in += r.bytes_in or 0
        dev.bytes_out += r.bytes_out or 0
        dev.last_seen = max(dev.last_seen, r.ts or "")
        sni = ((r.hints or {}).get("sni") or "") if isinstance(r.hints, dict) else ""
        host = sni or r.peer_name or ""
        key = (r.mac, host or r.peer_ip, r.peer_port, r.proto)
        d = dests.get(key)
        if d is None:
            name, what, sure = services.identify(host, r.peer_ip)
            local = services.is_local(r.peer_ip)
            if local and not host and r.peer_ip in by_ip:
                name, what = by_ip[r.peer_ip], "a device on your network"
            d = ConnectionDest(host=host, peer_ip=r.peer_ip or "", port=int(r.peer_port or 0), proto=r.proto or "",
                               name=name, what=what, sure=sure, local=local,
                               port_meaning=services.port_meaning(r.proto or "", int(r.peer_port or 0)),
                               first_seen=r.ts or "", last_seen=r.ts or "",
                               is_new=(r.peer_name or r.peer_ip) not in seen_before and not local)
            dests[key] = d
            dev.destinations.append(d)
        d.bytes_in += r.bytes_in or 0
        d.bytes_out += r.bytes_out or 0
        d.first_seen = min(d.first_seen, r.ts or d.first_seen)
        d.last_seen = max(d.last_seen, r.ts or "")

    for dev in devices.values():
        dev.destinations.sort(key=lambda x: -(x.bytes_in + x.bytes_out))
    out.devices = sorted(devices.values(), key=lambda x: -(x.bytes_in + x.bytes_out))
    out.destinations_total = len({(k[1], k[2], k[3]) for k in dests})
    out.new_total = len({(k[1], k[2], k[3]) for k, v in dests.items() if v.is_new})
    return out
