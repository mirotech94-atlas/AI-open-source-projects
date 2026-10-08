"""DNS query-log analysis collector: totals, top domains/clients, beaconing, DGA-style lookups.

Ported from the Nexus collector (~/.claude/nexus-agent/checks.py) — logic
copied faithfully, never imported from that directory.
"""
import datetime as dt
import json
import os
import re

from . import config, lan, paths

DGA_RE = re.compile(r"^[a-z]{2}?[a-z0-9]{10,}\.(com|net|info|xyz|top|biz|ru|cn)$")


def dns_analyse(cfg, state):
    """Read today's guardian-dns query log: totals, top domains and clients, blocked, plus beaconing and suspicious-lookup signals."""
    ddir = paths.p("dns")
    stat = {}
    try: stat = json.load(open(os.path.join(ddir, "status.json")))
    except Exception: pass
    qf = os.path.join(ddir, "queries-" + dt.date.today().isoformat() + ".jsonl")
    dns_cfg = cfg.get("dns") or {}
    out = {
        "enabled": bool(dns_cfg.get("enabled")), "running": bool(stat), "status": stat,
        # The setup wizard needs the actual DNS resolver's own address, not
        # the router's gateway -- Task 30 fix round finding 6. This is the
        # sensor's own LAN IP (guardian-dns listens on it), never the LAN's
        # default gateway.
        "resolver_ip": lan.my_lan_ip() or "", "resolver_port": int(dns_cfg.get("port", 53)),
        # Seeded here, not only in the `out.update` below, so that the 24h
        # counters exist on EVERY path out of this function.
        #
        # The query log is per-day and is created by the first lookup of the
        # day, so from midnight until something asks the resolver a question
        # there is no file and the early return below is taken -- daily, on
        # every sensor. That is a resolver which is up and simply idle, but
        # with the counters absent the hub could not tell it apart from a
        # collector that had fallen over, and `selftest`'s dns check duly
        # failed every night. The read path had the same hole from the other
        # side: `payloads/dnsview` does `get("blocked_24h") or 0`, so it
        # showed a confident 0 whether the number was really zero or simply
        # never sent.
        "queries_24h": 0, "blocked_24h": 0, "block_pct": 0,
    }
    if not os.path.exists(qf): return out
    cutoff = (dt.datetime.now().astimezone() - dt.timedelta(hours=24)).isoformat()
    dom = {}; cli = {}; blocked = {}; per_client_dom = {}; times = {}; total = 0; nblock = 0
    cli_block = {}; cli_dom = {}; known = (state.get("lan") or {}).get("known_macs") or {}
    ip2name = {}; ip2mac = {}
    for m, d in known.items():
        if d.get("ip"):
            ip2name[d["ip"]] = d.get("label") or d.get("name") or d.get("vendor") or d["ip"]
            ip2mac[d["ip"]] = m
    try:
        for line in open(qf):
            try: r = json.loads(line)
            except Exception: continue
            if r.get("ts", "") < cutoff: continue
            total += 1; n = (r.get("name") or "").lower(); c = r.get("client") or "?"
            dom[n] = dom.get(n, 0) + 1; cli[c] = cli.get(c, 0) + 1
            cli_dom.setdefault(c, {})[n] = cli_dom.setdefault(c, {}).get(n, 0) + 1
            if r.get("blocked"):
                nblock += 1; blocked[n] = blocked.get(n, 0) + 1; cli_block[c] = cli_block.get(c, 0) + 1
            else:
                per_client_dom.setdefault(c, {}).setdefault(n, []).append(r.get("ts"))
    except Exception as e:
        out["error"] = str(e)[:100]; return out
    # Per-device 24h totals, keyed by MAC rather than IP -- a DHCP lease
    # rotates, a MAC (mostly) doesn't, and `alerts._lateral_alerts`-style code
    # needs an identity that survives that. A client whose IP never resolved
    # to a known MAC this scan (still on the ARP table's "unverified" side, or
    # off-LAN) is left out rather than guessed at.
    client_totals_by_mac = {}; client_names_by_mac = {}
    for c, count in cli.items():
        mac = ip2mac.get(c)
        if not mac:
            continue
        client_totals_by_mac[mac] = client_totals_by_mac.get(mac, 0) + count
        client_names_by_mac[mac] = ip2name.get(c, mac)
    out.update(queries_24h=total, blocked_24h=nblock, block_pct=round(100 * nblock / total, 1) if total else 0,
               top_domains=sorted(dom.items(), key=lambda x: -x[1])[:15], top_blocked=sorted(blocked.items(), key=lambda x: -x[1])[:15],
               top_clients=sorted(([ip2name.get(c, c), c, n] for c, n in cli.items()), key=lambda x: -x[2])[:12], clients=len(cli),
               client_totals_by_mac=client_totals_by_mac, client_names_by_mac=client_names_by_mac)
    # beaconing: same client -> same domain at a very regular interval, many times
    beacons = []
    for c, doms in per_client_dom.items():
        for n, ts in doms.items():
            if len(ts) < 12 or n.endswith(("in-addr.arpa",)): continue
            try: secs = sorted(dt.datetime.fromisoformat(x).timestamp() for x in ts if x)
            except Exception: continue
            gaps = [b - a for a, b in zip(secs, secs[1:]) if b - a > 0]
            if len(gaps) < 10: continue
            avg = sum(gaps) / len(gaps); var = sum((g - avg) ** 2 for g in gaps) / len(gaps)
            if avg > 20 and (var ** 0.5) / avg < 0.15:  # very regular, not chatty browser traffic
                beacons.append({"client": ip2name.get(c, c), "ip": c, "domain": n, "count": len(ts), "every_s": int(avg)})
    out["beaconing"] = sorted(beacons, key=lambda b: -b["count"])[:8]
    out["by_network"] = _by_network(cfg, cli, cli_block, cli_dom)
    out["suspicious"] = [{"domain": n, "count": v} for n, v in dom.items() if DGA_RE.match(n) and n not in blocked][:8]
    out["encrypted_dns"] = _encrypted_dns(cli_dom, ip2name, ip2mac)
    return out



#: Hostnames a device resolves on its way to NOT resolving through us.
#:
#: Encrypted DNS has to bootstrap over ordinary DNS: Firefox asks
#: `use-application-dns.net` whether the network permits DoH, then resolves
#: its provider; Chrome and the system resolvers do the equivalent. Those
#: lookups arrive at Vault's own resolver, so the last query a device
#: makes before it goes dark is the query that names it.
#:
#: Matched on exact host or dot-boundary suffix, never substring. `dns.google`
#: is an endpoint and `google.com` is not, and a substring match would flag
#: most of the internet -- the false-positive machine this whole plan is
#: trying to switch off.
DOH_ENDPOINTS = (
    "use-application-dns.net",        # Firefox canary: the question, not the answer
    "mozilla.cloudflare-dns.com",
    "chrome.cloudflare-dns.com",
    "cloudflare-dns.com",
    "one.one.one.one",
    "dns.google",
    "dns.quad9.net",
    "dns9.quad9.net",
    "dns.nextdns.io",
    "doh.opendns.com",
    "dns.adguard.com",
    "dns.adguard-dns.com",
    "doh.cleanbrowsing.org",
    "dns.controld.com",
)


def _doh_endpoint(name: str) -> str | None:
    """The endpoint `name` matches, or None. Exact or dot-boundary suffix."""
    for endpoint in DOH_ENDPOINTS:
        if name == endpoint or name.endswith("." + endpoint):
            return endpoint
    return None


def _encrypted_dns(cli_dom, ip2name, ip2mac):
    """Devices that look configured to resolve somewhere Vault cannot see.

    This is evidence of INTENT AND CAPABILITY, not proof of use. A device can
    resolve a DoH endpoint and never send it a query, and a device pointed at
    a provider by IP address bootstraps nothing and stays invisible here. So
    the hub words this as "lookups may not be visible" and never as a count
    of hidden queries: claiming to know the size of a gap you cannot see
    would be the same overreach as scoring an unmeasured host as healthy.

    Blocked lookups count. Blocking the bootstrap is a good outcome and does
    not make the device's configuration go away -- it will bypass Vault
    the moment the block lifts or it falls back to a hard-coded address.

    One row per (device, endpoint) rather than per lookup: a browser polling
    its provider all day is one finding, not hundreds. Same root-cause
    dedupe the plan asks for of the handled count.
    """
    rows = []
    for client, doms in cli_dom.items():
        for name, count in doms.items():
            endpoint = _doh_endpoint(name)
            if endpoint:
                rows.append({"client": client, "name": ip2name.get(client, client),
                             "mac": ip2mac.get(client), "endpoint": endpoint,
                             "count": count})
    return sorted(rows, key=lambda r: -r["count"])[:12]


def net_for_client(ip, nets):
    """Longest-prefix attribution of a client IP to a configured network;
    anything that matches nothing belongs to the primary (first) network."""
    if not nets:
        return None
    best = None
    for n in nets:
        pre = n.get("subnet_prefix") or ""
        if pre and (ip or "").startswith(pre + ".") and (best is None or len(pre) > len(best.get("subnet_prefix") or "")):
            best = n
    return best or nets[0]


def _by_network(cfg, cli, cli_block, cli_dom):
    """`{slug: {queries_24h, blocked_24h, top, clients}}` — the same 24h totals
    as the flat block, attributed per network by client IP."""
    nets = config.networks_of(cfg or {})
    out = {n["slug"]: {"queries_24h": 0, "blocked_24h": 0, "top": [], "clients": 0} for n in nets}
    doms = {n["slug"]: {} for n in nets}
    for c, count in cli.items():
        n = net_for_client(c, nets)
        if not n:
            continue
        slot = out[n["slug"]]
        slot["queries_24h"] += count
        slot["blocked_24h"] += cli_block.get(c, 0)
        slot["clients"] += 1
        for name, k in (cli_dom.get(c) or {}).items():
            doms[n["slug"]][name] = doms[n["slug"]].get(name, 0) + k
    for slug, d in doms.items():
        out[slug]["top"] = sorted(d.items(), key=lambda x: -x[1])[:10]
    return out
