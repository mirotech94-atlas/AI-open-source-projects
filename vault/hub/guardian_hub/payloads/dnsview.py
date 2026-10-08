"""DNS tab payload: filter status, top/blocked domains, per-client rows
joined to `Device`, block/allow lists, beaconing/suspicious signals and the
setup wizard check (spec S8 / Task 30).

Reads `Scan.raw["dns_filter"]` (already computed sensor-side by
`dnsstats.dns_analyse` -- top_domains/top_blocked/beaconing/suspicious are
trusted as-is, not recomputed here) plus `DnsQuery`, `DnsRule`, `Device` and
`Tenant.config["lan"]`.

`device_profile` is the device-drawer DNS profile (used by
`payloads.assets.device_detail`) -- Task 19 originally computed this inline;
this module is now the single implementation.
"""

import datetime as dt
from collections import Counter

from sqlalchemy import func
from sqlalchemy.orm import Session as DbSession

from .. import models as m, networks
from .. import retention
from ..scans import latest_scan, parse_ts
from ..schemas_dash import (
    DeviceDnsProfile, DnsClient, DnsList, DnsPayload, DnsRules, DnsStatus, DnsWizard, NameCount,
)
from ._util import _lax_cutoff, _now, exact_window

DNS_TOP_N = 10
# The merged (rollup) path covers weeks/months rather than the last 24h, so
# it earns a wider top-N than the live sensor snapshot -- a hard cap of 10
# would let the raw window's handful of high-volume domains crowd out
# everything the rollup contributes for the rest of a long window.
DNS_TOP_N_LONG = 50


#: The columns every section of the DNS page groups by. `build` counts the
#: window ONCE over all of them and hands that to `clients`, `wizard` and
#: `lists`, which each project out the dimensions they need -- one index scan
#: of the window instead of four. On the real tenant that is ~19k groups for
#: ~290k rows, so the shared result is small and the saving is three scans.
_ALL_COLS = (m.DnsQuery.name, m.DnsQuery.blocked, m.DnsQuery.client,
             m.DnsQuery.network_id, m.DnsQuery.mac, m.DnsQuery.list)
_COL_INDEX = {"name": 0, "blocked": 1, "client": 2, "network_id": 3, "mac": 4, "list": 5}


def _project(full: Counter, *names: str, blocked_only: bool = False) -> Counter:
    """Collapse a `_window_counts(..., _ALL_COLS)` result onto `names`."""
    idx = [_COL_INDEX[n] for n in names]
    out: Counter = Counter()
    for key, n in full.items():
        if blocked_only and not key[1]:
            continue
        out[tuple(key[i] for i in idx)] += n
    return out


def _window_counts(db: DbSession, tenant_id: str, cutoff: dt.datetime, cols, *,
                   net=None, blocked_only: bool = False) -> Counter:
    """`{(col values...): n}` over the `DnsQuery` rows whose PARSED `ts` is at
    or after `cutoff` -- exactly the rows the per-row loops this replaced
    counted, counted in SQL instead of in Python.

    The DNS page used to fetch every row in `_lax_cutoff`'s widened window and
    `parse_ts` it one at a time: ~290,000 rows, four times per load, 5-6 s on
    a laptop and several times that on the production hub's single vCPU,
    where it stalled every other tab as well. `_util.exact_window` explains
    why the SQL predicate is exact; the handful of rows it cannot judge come
    back through `odd` and are checked here exactly as before. Verified
    against the old implementation on a snapshot of production: every
    payload for both tenants, every network, 1/6/24/72-hour windows, byte
    for byte.
    """
    where, odd = exact_window(db, m.DnsQuery.ts, m.DnsQuery.tenant_id, tenant_id, cutoff)
    extra = []
    if net is not None:
        extra.append(m.DnsQuery.network_id == net.id)
    if blocked_only:
        extra.append(m.DnsQuery.blocked.is_(True))

    out: Counter = Counter()
    for *vals, n in db.query(*cols, func.count()).filter(where, *extra).group_by(*cols).all():
        out[tuple(vals)] += n
    for ts, *vals in db.query(m.DnsQuery.ts, *cols).filter(odd, *extra).all():
        when = parse_ts(ts)
        if when is not None and when >= cutoff:
            out[tuple(vals)] += 1
    return out


def _pairs(pairs) -> list[NameCount]:
    """`top_domains`/`top_blocked` are `[[name, count], ...]` pairs, already
    sorted and capped sensor-side -- just reshape them."""
    out = []
    for item in pairs or []:
        if isinstance(item, (list, tuple)) and len(item) >= 2:
            out.append(NameCount(name=str(item[0]), count=int(item[1] or 0)))
    return out


def status(raw: dict) -> DnsStatus:
    dns_filter = raw.get("dns_filter") or {}
    st = dns_filter.get("status") or {}
    return DnsStatus(
        enabled=bool(dns_filter.get("enabled")), running=bool(dns_filter.get("running")),
        queries_24h=int(dns_filter.get("queries_24h") or 0), blocked_24h=int(dns_filter.get("blocked_24h") or 0),
        block_pct=float(dns_filter.get("block_pct") or 0.0), cache=int(st.get("cache") or 0),
        upstream_ms_avg=st.get("upstream_ms_avg"), total_blocked_domains=int(st.get("total_blocked_domains") or 0),
        family_filter=bool(st.get("family_filter")), uptime_s=int(st.get("uptime_s") or 0), ts=st.get("ts"),
    )


def top(raw: dict) -> list[NameCount]:
    return _pairs((raw.get("dns_filter") or {}).get("top_domains"))


def blocked_top(raw: dict) -> list[NameCount]:
    return _pairs((raw.get("dns_filter") or {}).get("top_blocked"))


def beaconing(raw: dict) -> list[NameCount]:
    """Nexus's `dns_analyse` shapes a beacon as
    `{client, ip, domain, count, every_s}`; `NameCount` only carries
    name/count, so the who+what collapse into one label."""
    out = []
    for b in (raw.get("dns_filter") or {}).get("beaconing") or []:
        who = b.get("client") or b.get("ip") or ""
        domain = b.get("domain") or ""
        name = f"{who} -> {domain}" if who else domain
        out.append(NameCount(name=name, count=int(b.get("count") or 0)))
    return out


def suspicious(raw: dict) -> list[NameCount]:
    return [NameCount(name=s.get("domain") or "", count=int(s.get("count") or 0))
            for s in (raw.get("dns_filter") or {}).get("suspicious") or []]


def clients(db: DbSession, tenant_id: str, hours: int = 24, net=None,
            full: Counter | None = None) -> list[DnsClient]:
    """Per-client rows. `full` is `build`'s shared window count; without it
    (a caller outside `build`) the window is counted here."""
    if full is None:
        full = _window_counts(db, tenant_id, _now() - dt.timedelta(hours=hours), _ALL_COLS, net=net)
    counts = _project(full, "client", "network_id", "mac", "blocked")
    netrefs = networks.refs_by_id(db, tenant_id)
    # Keyed on (client ip, network), not on the ip alone: private ranges overlap,
    # so the same 192.168.1.x can be two different devices on two networks --
    # aggregating them into one row would report one arbitrary `network` ref and
    # add both networks' query counts together.
    #
    # The MAC shown for a client is the one most of its queries carried. The
    # row-by-row version kept whichever non-empty MAC it happened to meet
    # first, in an unordered query; the two agree whenever a client has one
    # MAC, which is every client on a normal network, and this one is at least
    # stable from one refresh to the next when it has two.
    agg: dict[tuple[str, str | None], dict] = {}
    mac_votes: dict[tuple[str, str | None], Counter] = {}
    for (client_ip, nid, mac, is_blocked), n in counts.items():
        entry = agg.setdefault((client_ip, nid), {"mac": None, "queries": 0, "blocked": 0, "net": nid})
        entry["queries"] += n
        if is_blocked:
            entry["blocked"] += n
        if mac:
            mac_votes.setdefault((client_ip, nid), Counter())[mac] += n
    for key, votes in mac_votes.items():
        agg[key]["mac"] = min(votes.items(), key=lambda kv: (-kv[1], kv[0]))[0]

    macs = {e["mac"] for e in agg.values() if e["mac"]}
    devices: dict[str, m.Device] = {}
    if macs:
        devices = {d.mac: d for d in db.query(m.Device).filter(
            m.Device.tenant_id == tenant_id, m.Device.mac.in_(macs)).all()}

    out = []
    for (ip, _nid), e in sorted(agg.items(),
                                 key=lambda kv: (-kv[1]["queries"], kv[0][0], kv[0][1] or "")):
        dev = devices.get(e["mac"]) if e["mac"] else None
        name = (dev.label or dev.name) if dev else ""
        out.append(DnsClient(mac=e["mac"], ip=ip, name=name, queries=e["queries"],
                              blocked=e["blocked"], network=netrefs.get(e["net"])))
    return out


def lists(db: DbSession, tenant_id: str, raw: dict, hours: int = 24, net=None,
          full: Counter | None = None) -> list[DnsList]:
    status_lists = ((raw.get("dns_filter") or {}).get("status") or {}).get("lists") or {}
    if full is None:
        full = _window_counts(db, tenant_id, _now() - dt.timedelta(hours=hours), _ALL_COLS, net=net)
    counts = {name: n for (name,), n in _project(full, "list", blocked_only=True).items() if name}

    return [
        DnsList(name=name, category=info.get("category") or "", entries=int(info.get("domains") or 0),
                blocked_24h=counts.get(name, 0), enabled=info.get("status") != "disabled")
        for name, info in status_lists.items()
    ]


def rules(db: DbSession, tenant_id: str) -> DnsRules:
    rows = db.query(m.DnsRule).filter_by(tenant_id=tenant_id).all()
    return DnsRules(allow=sorted(r.domain for r in rows if r.kind == "allow"),
                     block=sorted(r.domain for r in rows if r.kind == "block"))


def wizard(db: DbSession, tenant, raw: dict, hours: int = 24, net=None,
           full: Counter | None = None) -> DnsWizard:
    home = raw.get("home") or {}
    dns_filter = raw.get("dns_filter") or {}
    lan_cfg = (tenant.config or {}).get("lan") or {}
    gateway = home.get("gateway") or ""

    # `resolver_ip` is the sensor's own address (Task 30 fix round finding
    # 6) -- it must never be the LAN gateway, which is what this fell back
    # to before the sensor started reporting its own IP. `router_hint`
    # shows the gateway separately (for "does your router's DNS setting
    # match?"), preferring the tenant's configured expectation over the
    # sensor's live reading of it.
    resolver_ip = dns_filter.get("resolver_ip") or ""
    # With a `?network=` filter the router hint is that network's own expected
    # gateway, not the tenant-wide legacy `lan` block's.
    router_hint = (net.expected_gateway if net is not None and net.expected_gateway
                    else lan_cfg.get("expected_gateway") or gateway)
    working = bool(dns_filter.get("running")) and int(dns_filter.get("queries_24h") or 0) > 0
    # Bounded to the same window as the rest of the payload (finding 7) --
    # an unbounded distinct() over every DnsQuery this tenant has ever
    # ingested is both the wrong question ("using it now?") and, on a
    # long-lived tenant, an unbounded table scan.
    real_cutoff = _now() - dt.timedelta(hours=hours)
    if full is None:
        full = _window_counts(db, tenant.id, real_cutoff, _ALL_COLS, net=net)
    clients_using = len(_project(full, "client"))

    return DnsWizard(resolver_ip=resolver_ip, router_hint=router_hint, working=working,
                      clients_using=clients_using, checked_at=(dns_filter.get("status") or {}).get("ts"))


def device_profile(db: DbSession, tenant_id: str, mac: str, hours: int = 24, net=None) -> DeviceDnsProfile:
    cutoff = _now() - dt.timedelta(hours=hours)
    q = (
        db.query(m.DnsQuery.ts, m.DnsQuery.name, m.DnsQuery.blocked)
        .filter(m.DnsQuery.tenant_id == tenant_id, m.DnsQuery.mac == mac,
                m.DnsQuery.ts >= _lax_cutoff(cutoff))
    )
    if net is not None:
        q = q.filter(m.DnsQuery.network_id == net.id)
    rows = q.all()
    total = blocked = 0
    top_counts: dict[str, int] = {}
    blocked_counts: dict[str, int] = {}
    for ts, name, is_blocked in rows:
        when = parse_ts(ts)
        if when is None or when < cutoff:
            continue
        total += 1
        top_counts[name] = top_counts.get(name, 0) + 1
        if is_blocked:
            blocked += 1
            blocked_counts[name] = blocked_counts.get(name, 0) + 1

    def _top(counts: dict[str, int]) -> list[NameCount]:
        return [NameCount(name=n, count=c)
                for n, c in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))[:DNS_TOP_N]]

    return DeviceDnsProfile(queries_24h=total, blocked_24h=blocked,
                            top=_top(top_counts), blocked_top=_top(blocked_counts))


def _merged_top_blocked_clients(db: DbSession, tenant, hours: int,
                                net=None) -> tuple[list[NameCount], list[NameCount], list[DnsClient]]:
    """`top`/`blocked_top`/`clients` for a window longer than the tenant's
    raw retention (`retention.policy(tenant)["dns_raw_days"]`): the raw
    `dns_query` rows still on record (inside the raw window) plus the
    `DnsRollup` rows covering the rest of `hours`, summed by domain/client.
    The "(other)" rollup bucket is excluded from the top lists (it isn't a
    real domain) but its queries/blocked still count in each client's/
    domain's totals, since dropping it silently would undercount.

    `net` scopes BOTH legs -- the raw rows by `DnsQuery.network_id` (as
    `clients()` does) and the rollups by `DnsRollup.network_id`, which
    `retention.rollup_dns` groups by. Filtering only one leg would leave a
    long window silently reporting the whole tenant's domains for a single
    network, which is the same wrong answer the short window cannot give.
    """
    start = _now() - dt.timedelta(hours=hours)
    start_day = start.date().isoformat()
    netrefs = networks.refs_by_id(db, tenant.id)

    name_q: dict[str, int] = {}
    name_b: dict[str, int] = {}
    # Keyed on (mac-or-ip, network) for the same reason `clients()` is: private
    # ranges overlap, so one 192.168.1.x can be two devices on two networks.
    client_agg: dict[tuple[str, str | None], dict] = {}

    raw_q = (
        db.query(m.DnsQuery.name, m.DnsQuery.blocked, m.DnsQuery.mac, m.DnsQuery.client,
                 m.DnsQuery.ts, m.DnsQuery.network_id)
        .filter(m.DnsQuery.tenant_id == tenant.id, m.DnsQuery.ts >= _lax_cutoff(start))
    )
    if net is not None:
        raw_q = raw_q.filter(m.DnsQuery.network_id == net.id)
    for name, is_blocked, mac, client_ip, ts, nid in raw_q.all():
        when = parse_ts(ts)
        if when is None or when < start:
            continue
        name_q[name] = name_q.get(name, 0) + 1
        if is_blocked:
            name_b[name] = name_b.get(name, 0) + 1
        key = (mac or f"ip:{client_ip}", nid)
        entry = client_agg.setdefault(key, {"mac": mac, "ip": "" if mac else client_ip,
                                            "queries": 0, "blocked": 0, "net": nid})
        entry["queries"] += 1
        if is_blocked:
            entry["blocked"] += 1

    roll_q = (
        db.query(m.DnsRollup)
        .filter(m.DnsRollup.tenant_id == tenant.id, m.DnsRollup.day >= start_day)
    )
    if net is not None:
        roll_q = roll_q.filter(m.DnsRollup.network_id == net.id)
    for r in roll_q.all():
        if r.name != retention.DNS_ROLLUP_OTHER:
            name_q[r.name] = name_q.get(r.name, 0) + r.queries
            if r.blocked:
                name_b[r.name] = name_b.get(r.name, 0) + r.blocked
        key = (r.mac or "mac:unknown", r.network_id)
        entry = client_agg.setdefault(key, {"mac": r.mac, "ip": "", "queries": 0,
                                            "blocked": 0, "net": r.network_id})
        entry["queries"] += r.queries
        entry["blocked"] += r.blocked

    top_list = [NameCount(name=n, count=c)
                for n, c in sorted(name_q.items(), key=lambda kv: (-kv[1], kv[0]))[:DNS_TOP_N_LONG]]
    blocked_list = [NameCount(name=n, count=c)
                    for n, c in sorted(((n, c) for n, c in name_b.items() if c), key=lambda kv: (-kv[1], kv[0]))[:DNS_TOP_N_LONG]]

    macs = {e["mac"] for e in client_agg.values() if e["mac"]}
    devices = ({d.mac: d for d in db.query(m.Device).filter(
        m.Device.tenant_id == tenant.id, m.Device.mac.in_(macs)).all()} if macs else {})
    client_rows = []
    for (_k, _nid), e in sorted(client_agg.items(),
                                key=lambda kv: (-kv[1]["queries"], kv[0][0], kv[0][1] or "")):
        dev = devices.get(e["mac"]) if e["mac"] else None
        dev_name = (dev.label or dev.name) if dev else ""
        client_rows.append(DnsClient(mac=e["mac"], ip=e["ip"], name=dev_name,
                                     queries=e["queries"], blocked=e["blocked"],
                                     network=netrefs.get(e["net"])))

    return top_list, blocked_list, client_rows


def build(db: DbSession, tenant, hours: int = 24, net=None) -> DnsPayload:
    scan = latest_scan(db, tenant.id)
    raw = scan.raw if scan else {}
    raw_days = retention.policy(tenant)["dns_raw_days"]
    if hours <= raw_days * 24:
        cutoff = _now() - dt.timedelta(hours=hours)
        full = _window_counts(db, tenant.id, cutoff, _ALL_COLS, net=net)
        names: dict = {}
        blocked: dict = {}
        for (name, is_blocked), n in _project(full, "name", "blocked").items():
            names[name] = names.get(name, 0) + n
            if is_blocked:
                blocked[name] = blocked.get(name, 0) + n
        top_rows = [NameCount(name=n, count=c) for n,c in sorted(names.items(), key=lambda p: (-p[1],p[0]))[:DNS_TOP_N]]
        blocked_rows = [NameCount(name=n, count=c) for n,c in sorted(blocked.items(), key=lambda p: (-p[1],p[0]))[:DNS_TOP_N]]
        client_rows = clients(db, tenant.id, hours=hours, net=net, full=full)
    else:
        # The long-window path merges rollups and has its own reads; `lists`
        # and `wizard` below still cover only their own window, so they count
        # it themselves.
        full = None
        top_rows, blocked_rows, client_rows = _merged_top_blocked_clients(db, tenant, hours, net=net)
    current = status(raw)
    current.queries_24h = sum(c.queries for c in client_rows)
    current.blocked_24h = sum(c.blocked for c in client_rows)
    current.block_pct = round(100 * current.blocked_24h / current.queries_24h, 1) if current.queries_24h else 0
    # Coverage travels with every figure above: they describe the devices
    # that asked Vault's resolver, which on most networks is a minority.
    # `client_rows` is already keyed per (ip, network), so its length is the
    # count of distinct resolver users in the window.
    dev_q = db.query(m.Device).filter(m.Device.tenant_id == tenant.id)
    if net is not None:
        dev_q = dev_q.filter(m.Device.network_id == net.id)
    current.devices_total = dev_q.count()
    current.clients_using = len(client_rows)
    current.coverage_pct = (
        min(100, int(100 * current.clients_using / current.devices_total))
        if current.devices_total else 0
    )
    return DnsPayload(
        status=current, top=top_rows, blocked_top=blocked_rows,
        clients=client_rows, rules=rules(db, tenant.id),
        lists=lists(db, tenant.id, raw, hours=hours, net=net, full=full), beaconing=beaconing(raw),
        suspicious=suspicious(raw), wizard=wizard(db, tenant, raw, hours=hours, net=net, full=full),
    )
