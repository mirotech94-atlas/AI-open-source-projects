"""Vault DNS filter — a small, dependency-free filtering resolver
(our own AdGuard/Pi-hole-style filter for the household).

- Listens on UDP+TCP :53. Point the router's DHCP DNS at this machine and every device is filtered.
- Blocks domains from public malware / phishing / ad lists (+ optional family filter) and your own block list.
- Forwards everything else over encrypted DNS (DoH: Cloudflare, then Quad9) so the ISP cannot see lookups; plain UDP fallback.
- Local names (no dot, .local, .lan, .home, the router's own suffix) go to the router, so printers and the router UI keep working.
- Caches answers by TTL. Logs every query (client, name, type, blocked?, list, ms) to dns/queries-YYYY-MM-DD.jsonl for the
  Vault DNS tab, beaconing detection and known-bad alerts. Keeps 7 days.
- Reloads config.json -> dns.{allow,block,family_filter,upstreams} every minute; refreshes lists daily. Fails OPEN: if a list
  cannot be fetched the old copy is kept; if DoH fails the query goes plain UDP; if the process dies the service manager
  restarts it and the collector alerts.

Ported from ~/.claude/nexus-agent/dns.py — logic copied faithfully, never
imported from that directory. Differences: the data directory comes from
guardian_sensor.paths, config comes from guardian_sensor.config.load(), and
nothing touches the filesystem at import time (so importing this module in a
test never creates ~/.guardian).
"""
import datetime as dt
import json
import os
import resource
import re
import socket
import socketserver
import struct
import threading
import time
import urllib.request

from . import VERSION, config, paths, util

DDIR = paths.p("dns")
SSL_CTX = util.SSL_CTX
UA = f"guardian-dns/{VERSION}"
LISTS = {  # name -> (url, category)
    "stevenblack": ("https://raw.githubusercontent.com/StevenBlack/hosts/master/hosts", "ads+malware"),
    "urlhaus": ("https://urlhaus.abuse.ch/downloads/hostfile/", "malware"),
    "phishing-army": ("https://phishing.army/download/phishing_army_blocklist.txt", "phishing"),
    "family": ("https://raw.githubusercontent.com/StevenBlack/hosts/master/alternates/porn/hosts", "adult"),
}
#: The dark web, refused by name.
#:
#: `.onion` is the whole of Tor's address space. It is not a real DNS zone:
#: a resolver can never answer one, and RFC 7686 says a resolver SHOULD
#: return NXDOMAIN for it. So blocking it breaks nothing that worked -- but
#: a device ASKING for one is worth knowing about, because it means software
#: on that machine is trying to reach a hidden service through the ordinary
#: resolver.
DARK_WEB_SUFFIXES = (".onion", ".onion.to", ".exit", ".i2p")

#: Clearnet doorways into Tor -- proxies that fetch a hidden service for you
#: over plain HTTPS, so no Tor client is needed on the device. These ARE real
#: domains that resolve, which is exactly why they need naming: blocking
#: `.onion` alone does nothing about them.
DARK_WEB_GATEWAYS = {
    "tor2web.org", "onion.to", "onion.ws", "onion.ly", "onion.pet", "onion.cab",
    "onion.link", "onion.city", "onion.sh", "onion.lu", "onion.direct",
    "tor2web.io", "tor2web.fi", "darknet.to", "onion.moe", "onion.re",
    # Tor's own bootstrap surface: a client that cannot fetch a consensus
    # cannot build a circuit.
    "bridges.torproject.org", "check.torproject.org", "dist.torproject.org",
    "collector.torproject.org", "onionoo.torproject.org", "snowflake.torproject.org",
}

LOCAL_SUFFIXES = (".local", ".lan", ".home", ".internal", ".arpa", ".mynetworksettings.com", ".fios-router.home")
#: Cloudflare runs three resolvers, not one, and which one you ask decides
#: whether anything is filtered upstream at all:
#:
#:   1.1.1.1 / 1.0.0.1  -- plain. Resolves everything, filters nothing.
#:   1.1.1.2 / 1.0.0.2  -- blocks known malware and phishing domains.
#:   1.1.1.3 / 1.0.0.3  -- the above, plus adult content.
#:
#: Vault asked for the plain one, which threw away free protection: its
#: own lists (urlhaus, phishing-army, stevenblack) are good but finite, and
#: Cloudflare sees a rather larger share of the internet's traffic than a
#: handful of text files do. Asking the SECURITY resolver instead is defence
#: in depth at no cost -- Vault blocks what it knows, logs it, and shows
#: it in the query stream where the client's own allow and block rules
#: apply; Cloudflare then catches what Vault's lists had not heard of
#: yet.
#:
#: It also means the filtering survives the client doing the obvious thing:
#: point the router straight at 1.1.1.2 and malware is still blocked, just
#: without Vault's logging and rules.
#:
#: DoH is preferred over the bare IPs throughout: the same resolver, but the
#: query leaves the network encrypted, so the ISP cannot read or sell it.
SECURITY_DOH = "https://security.cloudflare-dns.com/dns-query"
FAMILY_DOH = "https://family.cloudflare-dns.com/dns-query"
QUAD9_DOH = "https://dns.quad9.net/dns-query"   # also malware-filtering

#: Quad9 second because it filters malware too -- a fallback that silently
#: stopped filtering would be worse than the outage it was covering for.
DEFAULT_UPSTREAMS = [SECURITY_DOH, QUAD9_DOH]
FAMILY_UPSTREAMS = [FAMILY_DOH, QUAD9_DOH]

#: Last resort, when DoH itself cannot be reached (a captive portal, a
#: blocked 443). Cloudflare's filtering pair, never the plain one.
FALLBACK_UPSTREAMS = ("1.1.1.2", "1.0.0.2")
FAMILY_FALLBACK_UPSTREAMS = ("1.1.1.3", "1.0.0.3")


def upstreams_for(cfg: dict) -> tuple[list, tuple]:
    """`(doh_urls, plain_ips)` for this config.

    An explicit `dns.upstreams` still wins -- an operator who has named
    their own resolver means it -- but the default now filters.
    """
    family = bool(cfg.get("family_filter"))
    doh = cfg.get("upstreams") or (FAMILY_UPSTREAMS if family else DEFAULT_UPSTREAMS)
    plain = FAMILY_FALLBACK_UPSTREAMS if family else FALLBACK_UPSTREAMS
    return list(doh), plain
STATE = {"blocked": {}, "lists": {}, "cache": {}, "cfg": {}, "gw": "192.168.1.1",
         "started": time.time(), "q": 0, "b": 0, "lock": threading.Lock(), "up_ms": [],
         "allow": set(), "custom": set()}
LOG_LOCK = threading.Lock()

# 2026-09-29: after two days the resolver hit macOS's default 256 open files
# (a background LaunchAgent gets 256) and every lookup on the Mac failed. A
# thread per query with no ceiling, slow DoH lookups each holding a TLS socket,
# and TCP clients that connect and never send all held descriptors. Now: at
# most MAX_INFLIGHT lookups at once (the rest get SERVFAIL at once, so a client
# retries instead of piling up), TCP reads time out, the query log fails open,
# and a watchdog exits for launchd (KeepAlive) to restart us well before the
# limit, instead of limping on half-broken. Measured the week before: one
# device's bursts of several hundred lookups hit the limit 196 times, and each
# time every device on the network got SERVFAIL until the burst drained. The
# ceiling is applied per lookup (`bounded_resolve`), not in the serving loop:
# a loop that blocks for a free slot lets 64 TCP clients that connect and
# never send stall every UDP lookup behind them.
MAX_INFLIGHT = 64
INFLIGHT = threading.BoundedSemaphore(MAX_INFLIGHT)
TCP_TIMEOUT_S = 5.0
FD_TARGET = 4096
FD_EXIT_FRACTION = 0.8


def log(msg):
    print(time.strftime("%H:%M:%S"), msg, flush=True)


def _ddir():
    """The dns data directory, created on first use (never at import)."""
    d = paths.p("dns")
    os.makedirs(d, exist_ok=True)
    return d


def load_cfg():
    try:
        c = config.load()
        d = c.get("dns") or {}
        STATE["cfg"] = d
        STATE["gw"] = (c.get("lan") or {}).get("expected_gateway") or STATE["gw"]
        STATE["allow"] = {x.lower().strip(".") for x in d.get("allow", [])}
        STATE["custom"] = {x.lower().strip(".") for x in d.get("block", [])}
    except Exception as e:
        log(f"config: {e}")
        STATE.setdefault("allow", set())
        STATE.setdefault("custom", set())


def fetch_list(name, url):
    p = os.path.join(_ddir(), f"list-{name}.txt")
    try:
        req = urllib.request.Request(url, headers={"User-Agent": UA})
        with urllib.request.urlopen(req, timeout=40, context=SSL_CTX) as r:
            data = r.read().decode("utf-8", "ignore")
        doms = set()
        for line in data.splitlines():
            line = line.split("#", 1)[0].strip()
            if not line:
                continue
            parts = line.split()
            host = parts[1] if len(parts) >= 2 and parts[0] in ("0.0.0.0", "127.0.0.1", "::1", "::") else parts[0]
            host = host.lower().strip(".")
            if host and "." in host and host not in ("localhost", "localhost.localdomain", "broadcasthost", "0.0.0.0") and re.match(r"^[a-z0-9.-]+$", host):
                doms.add(host)
        if doms:
            open(p, "w").write("\n".join(sorted(doms)))
            return doms, "fresh"
    except Exception as e:
        log(f"list {name}: {e}")
    if os.path.exists(p):
        return set(open(p).read().split()), "cached"
    return set(), "missing"


def refresh_lists():
    fam = bool(STATE["cfg"].get("family_filter"))
    blocked = {}
    info = {}
    for name, (url, cat) in LISTS.items():
        if name == "family" and not fam:
            continue
        doms, how = fetch_list(name, url)
        info[name] = {"domains": len(doms), "status": how, "category": cat, "ts": time.strftime("%Y-%m-%dT%H:%M:%S")}
        for d in doms:
            blocked.setdefault(d, name)
    with STATE["lock"]:
        STATE["blocked"] = blocked
        STATE["lists"] = info
        STATE["lists_ts"] = time.time()
    json.dump({"lists": info, "total": len(blocked), "ts": time.strftime("%Y-%m-%dT%H:%M:%S%z")},
              open(os.path.join(_ddir(), "lists.json"), "w"), indent=2)
    log(f"lists loaded: {len(blocked)} domains " + ", ".join(f"{k}={v['domains']}({v['status']})" for k, v in info.items()))


def is_blocked(name):
    # Exact-domain exceptions expire at request time, including if the hub
    # or config sync is temporarily unavailable.
    for exception in STATE.get("cfg", {}).get("exceptions") or []:
        try:
            from datetime import datetime, timezone
            expiry = datetime.fromisoformat(exception.get("expires", "").replace("Z", "+00:00"))
            if expiry.tzinfo and expiry > datetime.now(timezone.utc) and name.lower().rstrip(".") == exception.get("domain"):
                return None
        except (TypeError, ValueError):
            continue
    n = name.lower().strip(".")
    parts = n.split(".")
    for i in range(len(parts) - 1):
        cand = ".".join(parts[i:])
        if cand in STATE["allow"]:
            return None
    # The dark web is refused before the allow list is even consulted for
    # `.onion` -- see below -- but AFTER it for the gateways, so an operator
    # who genuinely needs one can allow it explicitly rather than editing
    # code. Checked ahead of the downloaded lists because it must hold even
    # when every list failed to fetch: this filter fails OPEN by design, and
    # "we could not reach GitHub" is not a reason to start resolving Tor.
    if STATE["cfg"].get("block_dark_web", True):
        if n.endswith(DARK_WEB_SUFFIXES) or n in DARK_WEB_SUFFIXES:
            return "dark-web"
        for i in range(len(parts) - 1):
            if ".".join(parts[i:]) in DARK_WEB_GATEWAYS:
                return "dark-web"
    for i in range(len(parts) - 1):
        cand = ".".join(parts[i:])
        if cand in STATE["custom"]:
            return "custom"
        src = STATE["blocked"].get(cand)
        if src:
            return src
    return None


# ---------------- wire format ----------------
def parse_question(pkt):
    """Return (qname, qtype, qclass, end_offset) of the first question."""
    if len(pkt) < 12:
        raise ValueError("short")
    qd = struct.unpack("!H", pkt[4:6])[0]
    if qd < 1:
        raise ValueError("no question")
    i = 12
    labels = []
    while True:
        l = pkt[i]
        if l == 0:
            i += 1
            break
        if l & 0xC0:
            raise ValueError("compressed qname")
        labels.append(pkt[i + 1:i + 1 + l].decode("ascii", "ignore"))
        i += 1 + l
    qtype, qclass = struct.unpack("!HH", pkt[i:i + 4])
    return ".".join(labels), qtype, qclass, i + 4


def skip_name(pkt, i):
    while True:
        l = pkt[i]
        if l == 0:
            return i + 1
        if l & 0xC0:
            return i + 2
        i += 1 + l


def min_ttl(pkt):
    """Smallest TTL over answer RRs (for caching); 0 when unparsable."""
    try:
        an = struct.unpack("!H", pkt[6:8])[0]
        i = skip_name(pkt, 12) + 4
        ttls = []
        for _ in range(an):
            i = skip_name(pkt, i)
            _, _, ttl, rdl = struct.unpack("!HHIH", pkt[i:i + 10])
            ttls.append(ttl)
            i += 10 + rdl
        return min(ttls) if ttls else 0
    except Exception:
        return 0


def blocked_response(pkt, qtype, qend):
    hdr = bytearray(pkt[:12])
    hdr[2] = 0x81
    hdr[3] = 0x80  # QR, RD, RA, rcode 0
    q = pkt[12:qend]
    ans = b""
    if qtype == 1:
        ans = b"\xc0\x0c" + struct.pack("!HHIH", 1, 1, 300, 4) + b"\x00\x00\x00\x00"
    elif qtype == 28:
        ans = b"\xc0\x0c" + struct.pack("!HHIH", 28, 1, 300, 16) + b"\x00" * 16
    struct.pack_into("!HHHH", hdr, 4, 1, 1 if ans else 0, 0, 0)
    return bytes(hdr) + q + ans


def upstream_udp(pkt, server, timeout=2.5):
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.settimeout(timeout)
    try:
        s.sendto(pkt, (server, 53))
        return s.recv(4096)
    finally:
        s.close()


def upstream_doh(pkt, url):
    req = urllib.request.Request(url, data=pkt, headers={"Content-Type": "application/dns-message", "Accept": "application/dns-message", "User-Agent": UA})
    with urllib.request.urlopen(req, timeout=4, context=SSL_CTX) as r:
        return r.read()


def resolve(pkt, client):
    t0 = time.time()
    rec = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "client": client, "name": None, "type": None,
           "blocked": False, "list": None, "cached": False, "via": None, "ms": 0}
    try:
        qname, qtype, qclass, qend = parse_question(pkt)
    except Exception:
        return None, rec
    rec["name"] = qname.lower()
    rec["type"] = {1: "A", 28: "AAAA", 5: "CNAME", 15: "MX", 16: "TXT", 12: "PTR", 33: "SRV", 65: "HTTPS", 2: "NS", 6: "SOA"}.get(qtype, str(qtype))
    key = (qname.lower(), qtype)
    tid = pkt[:2]
    src = is_blocked(qname) if qname else None
    if src:
        rec.update(blocked=True, list=src, via="block")
        STATE["b"] += 1
        resp = blocked_response(pkt, qtype, qend)
    else:
        c = STATE["cache"].get(key)
        if c and c[0] > time.time():
            resp = tid + c[1][2:]
            rec.update(cached=True, via="cache")
        else:
            resp = None
            # Bound before either branch that reads them: the plain-IP list
            # is used in a LATER `if resp is None` than the one that used to
            # define it, which only happened to work because both branches
            # are reached together.
            doh_urls, plain_ips = upstreams_for(STATE["cfg"])
            local = ("." not in qname) or qname.lower().endswith(LOCAL_SUFFIXES) or qname.lower().endswith("in-addr.arpa")
            if local:
                try:
                    resp = upstream_udp(pkt, STATE["gw"], 1.5)
                    rec["via"] = "router"
                except Exception:
                    resp = None
            if resp is None:
                for url in doh_urls:
                    try:
                        resp = upstream_doh(pkt, url)
                        rec["via"] = "doh:" + url.split("/")[2]
                        break
                    except Exception:
                        continue
            if resp is None:
                for ip in plain_ips:
                    try:
                        resp = upstream_udp(pkt, ip)
                        rec["via"] = "udp:" + ip
                        break
                    except Exception:
                        continue
            if resp is None:
                hdr = bytearray(pkt[:12])
                hdr[2] = 0x81
                hdr[3] = 0x82
                struct.pack_into("!HHHH", hdr, 4, 1, 0, 0, 0)
                resp = bytes(hdr) + pkt[12:qend]
                rec["via"] = "servfail"
            else:
                ttl = min_ttl(resp)
                if ttl > 0 and (resp[3] & 0x0F) == 0:
                    STATE["cache"][key] = (time.time() + min(ttl, 3600), resp)
                    if len(STATE["cache"]) > 20000:
                        STATE["cache"].clear()
    rec["ms"] = int((time.time() - t0) * 1000)
    STATE["q"] += 1
    if rec["via"] and rec["via"].startswith(("doh", "udp")):
        STATE["up_ms"] = (STATE["up_ms"] + [rec["ms"]])[-200:]
    return resp, rec


def servfail(pkt):
    """A SERVFAIL for this query, built without resolving anything."""
    try:
        *_, qend = parse_question(pkt)
    except Exception:
        return None
    hdr = bytearray(pkt[:12])
    hdr[2] = 0x81
    hdr[3] = 0x82
    struct.pack_into("!HHHH", hdr, 4, 1, 0, 0, 0)
    return bytes(hdr) + pkt[12:qend]


def bounded_resolve(pkt, client):
    """resolve() under the in-flight ceiling. Over it, answer SERVFAIL now:
    a slow upstream must never turn into unbounded threads and sockets."""
    if not INFLIGHT.acquire(timeout=0.5):
        STATE["shed"] = STATE.get("shed", 0) + 1
        return servfail(pkt), {"name": None}
    try:
        return resolve(pkt, client)
    finally:
        INFLIGHT.release()


def raise_fd_limit(target=FD_TARGET):
    """Lift the soft open-files limit toward `target` (never above the hard
    limit). Returns the soft limit in force afterwards."""
    soft, hard = resource.getrlimit(resource.RLIMIT_NOFILE)
    want = target if hard == resource.RLIM_INFINITY else min(target, hard)
    if soft != resource.RLIM_INFINITY and soft < want:
        try:
            resource.setrlimit(resource.RLIMIT_NOFILE, (want, hard))
        except (ValueError, OSError) as e:
            log(f"could not raise open-files limit to {want}: {e}")
    return resource.getrlimit(resource.RLIMIT_NOFILE)[0]


def open_fds():
    """How many descriptors this process holds, or None if unknown."""
    for d in ("/dev/fd", f"/proc/{os.getpid()}/fd"):
        try:
            return len(os.listdir(d))
        except OSError:
            continue
    return None


def fd_watchdog_tripped(limit):
    """True when open descriptors pass FD_EXIT_FRACTION of `limit`."""
    n = open_fds()
    return n is not None and limit not in (None, resource.RLIM_INFINITY) and n > limit * FD_EXIT_FRACTION


def write_log(rec):
    if not rec.get("name"):
        return
    p = os.path.join(_ddir(), "queries-" + time.strftime("%Y-%m-%d") + ".jsonl")
    try:
        with LOG_LOCK:
            with open(p, "a") as f:
                f.write(json.dumps(rec) + "\n")
    except OSError as e:
        # The log is for the dashboard; losing a line must never cost an answer.
        STATE["log_errors"] = STATE.get("log_errors", 0) + 1
        if STATE["log_errors"] % 100 == 1:
            log(f"query log write failed ({STATE['log_errors']} so far): {e}")


class UDP(socketserver.ThreadingMixIn, socketserver.UDPServer):
    allow_reuse_address = True
    daemon_threads = True

    def server_bind(self):
        try:
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
        except Exception:
            pass
        super().server_bind()


class TCP(socketserver.ThreadingMixIn, socketserver.TCPServer):
    allow_reuse_address = True
    daemon_threads = True

    def server_bind(self):
        try:
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
        except Exception:
            pass
        super().server_bind()


class UDPHandler(socketserver.BaseRequestHandler):
    def handle(self):
        data, sock = self.request
        resp, rec = bounded_resolve(data, self.client_address[0])
        if resp:
            sock.sendto(resp[:4096], self.client_address)
        write_log(rec)


class TCPHandler(socketserver.BaseRequestHandler):
    def handle(self):
        try:
            self.request.settimeout(TCP_TIMEOUT_S)
            hdr = self.request.recv(2)
            if len(hdr) < 2:
                return
            n = struct.unpack("!H", hdr)[0]
            data = b""
            while len(data) < n:
                chunk = self.request.recv(n - len(data))
                if not chunk:
                    return
                data += chunk
            resp, rec = bounded_resolve(data, self.client_address[0])
            if resp:
                self.request.sendall(struct.pack("!H", len(resp)) + resp)
            write_log(rec)
        except Exception:
            pass


def housekeeping():
    while True:
        time.sleep(60)
        if fd_watchdog_tripped(STATE.get("fd_limit")):
            log(f"open files {open_fds()} passed {int(FD_EXIT_FRACTION * 100)}% of "
                f"{STATE.get('fd_limit')}: exiting for launchd to restart a clean resolver")
            os._exit(3)
        try:
            load_cfg()
            if time.time() - STATE.get("lists_ts", 0) > 86400:
                refresh_lists()
            up = STATE["up_ms"]
            d = _ddir()
            with open(os.path.join(d, "status.json"), "w") as sf:
                json.dump({"ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "uptime_s": int(time.time() - STATE["started"]),
                           "queries": STATE["q"], "blocked": STATE["b"], "cache": len(STATE["cache"]),
                           "lists": STATE["lists"], "total_blocked_domains": len(STATE["blocked"]),
                           "upstream_ms_avg": (sum(up) // len(up)) if up else None,
                           "family_filter": bool(STATE["cfg"].get("family_filter")),
                           "open_fds": open_fds(), "fd_limit": STATE.get("fd_limit"),
                           "shed": STATE.get("shed", 0), "log_errors": STATE.get("log_errors", 0)},
                          sf, indent=2)
            cutoff = (dt.date.today() - dt.timedelta(days=7)).isoformat()
            for f in os.listdir(d):
                if f.startswith("queries-") and f[8:18] < cutoff:
                    os.remove(os.path.join(d, f))
        except Exception as e:
            log(f"housekeeping: {e}")


def main(args=None):
    """Run the filtering resolver until interrupted."""
    paths.ensure()
    _ddir()
    load_cfg()
    STATE["fd_limit"] = raise_fd_limit()
    threading.Thread(target=refresh_lists, daemon=True).start()
    bind = STATE["cfg"].get("bind", "0.0.0.0")
    port = int(STATE["cfg"].get("port", 53))
    try:
        udp = UDP((bind, port), UDPHandler)
        tcp = TCP((bind, port), TCPHandler)
    except OSError as e:
        print(f"guardian-dns: cannot bind {bind}:{port}: {e}"
              + (" (port 53 needs root)" if port < 1024 else ""))
        return 1
    threading.Thread(target=udp.serve_forever, daemon=True).start()
    threading.Thread(target=tcp.serve_forever, daemon=True).start()
    threading.Thread(target=housekeeping, daemon=True).start()
    log(f"guardian-dns listening on {bind}:{port} (udp+tcp), router {STATE['gw']}")
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        pass
    return 0
