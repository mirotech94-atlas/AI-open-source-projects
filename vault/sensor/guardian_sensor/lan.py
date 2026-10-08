"""LAN discovery, port sweep, presence and risk-scoring collectors.

Ported from the Nexus collector (~/.claude/nexus-agent/checks.py) — logic
copied faithfully, never imported from that directory.
"""
import concurrent.futures
import datetime as dt
import json
import re
import socket
import subprocess
import time
import urllib.error
import urllib.request

from . import cameras, util


def default_gateway():
    try:
        if util.IS_MAC:
            o = subprocess.run(["route", "-n", "get", "default"], capture_output=True, text=True, timeout=5).stdout; m = re.search(r"gateway: (\S+)", o)
        else:
            o = subprocess.run(["ip", "route", "show", "default"], capture_output=True, text=True, timeout=5).stdout; m = re.search(r"default via (\S+)", o)
        return m.group(1) if m else None
    except Exception: return None


def my_lan_ip():
    try:
        if util.IS_MAC: return subprocess.run(["ipconfig", "getifaddr", "en0"], capture_output=True, text=True, timeout=5).stdout.strip()
        return subprocess.run(["hostname", "-I"], capture_output=True, text=True, timeout=5).stdout.split()[0]
    except Exception: return None


def local_subnets():
    """`{"192.168.1": "en0", "10.10.0": "en8"}` — every IPv4 /24 prefix THIS
    sensor itself holds an address on, and the interface holding it.

    Reachability of a configured network is decided from these addresses, not
    from `default_gateway()`: a multi-homed sensor has one default route but
    can sweep every subnet it is wired into.
    """
    out = {}
    try:
        if util.IS_MAC:
            o = subprocess.run(["ifconfig", "-a"], capture_output=True, text=True, timeout=8).stdout
            iface = None
            for line in o.splitlines():
                m = re.match(r"^(\S+):\s", line)
                if m:
                    iface = m.group(1)
                    continue
                m = re.search(r"^\s+inet (\d+\.\d+\.\d+)\.\d+", line)
                if m and iface and not iface.startswith("lo"):
                    out.setdefault(m.group(1), iface)
        else:
            o = subprocess.run(["ip", "-o", "-4", "addr", "show"], capture_output=True, text=True, timeout=8).stdout
            for line in o.splitlines():
                m = re.search(r"^\d+:\s+(\S+)\s+inet\s+(\d+\.\d+\.\d+)\.\d+", line)
                if m and not m.group(1).startswith("lo"):
                    out.setdefault(m.group(2), m.group(1))
    except Exception:
        return out
    return out


def own_ips():
    """Every IPv4 address this sensor holds, loopback excluded. `flows.rollup`
    uses it to tell the sensor's own port sweep from traffic on the LAN."""
    out = set()
    try:
        if util.IS_MAC:
            o = subprocess.run(["ifconfig", "-a"], capture_output=True, text=True, timeout=8).stdout
            out.update(re.findall(r"^\s+inet (\d+\.\d+\.\d+\.\d+)", o, re.M))
        else:
            o = subprocess.run(["ip", "-o", "-4", "addr", "show"], capture_output=True, text=True, timeout=8).stdout
            out.update(re.findall(r"\sinet (\d+\.\d+\.\d+\.\d+)/", o))
    except Exception:
        pass
    return {ip for ip in out if not ip.startswith("127.")}


def reachable(net, subs):
    """True when this sensor can sweep `net`: it holds an address on that
    subnet, or on the interface the config pins for it."""
    iface = net.get("interface")
    if iface:
        return iface in (subs or {}).values()
    pre = net.get("subnet_prefix")
    return bool(pre) and pre in (subs or {})


def unreachable_reason(net):
    pre = net.get("subnet_prefix") or "?"
    if net.get("interface"):
        return f"no address on interface {net['interface']} for {pre}.0/24"
    return f"no local interface on {pre}.0/24"


# ---------------- LAN ----------------
def lan_sweep(cfg):
    pre = cfg["subnet_prefix"]
    def ping(i):
        ip = f"{pre}.{i}"
        r = subprocess.run(["ping", "-c", "1"] + util.PING_W(400) + [ip], capture_output=True)
        return ip if r.returncode == 0 else None
    live = set()
    for _ in range(2):  # two passes: sleeping phones often miss the first probe
        with concurrent.futures.ThreadPoolExecutor(32) as ex:
            live |= {x for x in ex.map(ping, range(1, 255)) if x}
        time.sleep(1)
    live = sorted(live, key=lambda ip: int(ip.rsplit(".", 1)[1]))
    if util.IS_MAC:
        arp = subprocess.run(["arp", "-a"], capture_output=True, text=True).stdout
        macs = {m.group(1): m.group(2) for m in re.finditer(r"\((\d+\.\d+\.\d+\.\d+)\) at ([0-9a-f:]+)", arp)}
        # the router's DNS usually knows DHCP client names ("iphone", "echo-waiting-room", "staff-phone")
        names = {m.group(2): m.group(1) for m in re.finditer(r"^(\S+) \((\d+\.\d+\.\d+\.\d+)\) at", arp, re.M) if m.group(1) != "?"}
    else:
        arp = subprocess.run(["ip", "neigh", "show"], capture_output=True, text=True).stdout
        macs = {m.group(1): m.group(2) for m in re.finditer(r"^(\d+\.\d+\.\d+\.\d+) .*?lladdr ([0-9a-f:]+)", arp, re.M)}
        names = {}
        def rdns(ip):
            try:
                o = subprocess.run(["dig", "+short", "+time=1", "+tries=1", "-x", ip, "@" + cfg.get("expected_gateway", "")], capture_output=True, text=True, timeout=4).stdout.strip().rstrip(".")
                return ip, (o.split("\n")[0] if o and not o.startswith(";") else None)
            except Exception: return ip, None
        with concurrent.futures.ThreadPoolExecutor(16) as ex:
            for ip, n in ex.map(rdns, live):
                if n: names[ip] = n
    # Bonjour only answers on the segment the sensor itself sits on, and it
    # costs ~12s: a secondary network's sweep skips it (the network entry
    # carries `mdns: False`), which is how the per-network loop stays inside
    # the old single-network time budget.
    if cfg.get("mdns", True):
        names.update(mdns_names())
    def norm(m):  # macOS prints 7e:b3:23:ef:d0:1f without zero padding sometimes
        return ":".join(p.zfill(2) for p in m.split(":")) if m else None
    devices = {ip: norm(macs.get(ip)) for ip in live}
    # also include ARP-table entries that didn't answer ping (phones asleep) but are fresh in the table
    for ip, mac in macs.items():
        if ip.startswith(pre + ".") and ip not in devices:
            devices[ip] = norm(mac)
    # drop broadcast / multicast pseudo-entries the ARP table always contains
    devices = {ip: m for ip, m in devices.items()
               if not ip.endswith(".255") and (m or "").lower() not in ("ff:ff:ff:ff:ff:ff",) and not (m or "").lower().startswith("01:00:5e")}
    return {"live_count": len(live), "devices": devices, "names": {ip: n for ip, n in names.items() if ip in devices}, "gw_mac": norm(macs.get(cfg.get("expected_gateway"))),
            "offsubnet": offsubnet(macs, pre, cfg.get("prefixes"), norm),
            "watch_ips": {ip: (ip in devices) for ip in cfg.get("watch_ips", {})}}


#: Addresses that are on-link but belong to no network at all -- link-local,
#: multicast/broadcast, and the CGNAT range Tailscale and every carrier NAT
#: use (a Tailscale peer is not a device smuggling an uplink onto the LAN).
_NEVER_OFFSUBNET = ("169.254.", "224.", "225.", "226.", "239.", "255.", "100.64.", "127.")


def _private(ip):
    """RFC1918 only. A rogue box on a home LAN gives itself a private address;
    restricting to those keeps VPN peers, CGNAT addresses and anything routed
    out of the report entirely."""
    parts = ip.split(".")
    if len(parts) != 4 or not all(p.isdigit() for p in parts):
        return False
    a, b = int(parts[0]), int(parts[1])
    return a == 10 or (a == 172 and 16 <= b <= 31) or (a == 192 and b == 168)


def offsubnet(macs, prefix, prefixes, norm):
    """`{ip: mac}` for ARP entries that answered on-link but whose address is
    in NO network this sensor knows about.

    This is the honest, collectable form of "a device that is not part of my
    network": it answered ARP on a segment the sensor is wired into, yet it
    carries an address outside every configured subnet and outside every
    subnet this machine itself holds. A device that set its own static address,
    or one bridging in from somewhere else, looks exactly like this.

    Nothing here is probed: the addresses are read out of the ARP table the
    sweep already collected, so no packet is ever sent outside the tenant's own
    networks.
    """
    known = {prefix} | {p for p in (prefixes or []) if p}
    out = {}
    for ip, mac in (macs or {}).items():
        if ip.rsplit(".", 1)[0] in known or ip.startswith(_NEVER_OFFSUBNET) or ip.endswith(".255"):
            continue
        if not _private(ip):
            continue
        m = (mac or "").lower()
        if m in ("ff:ff:ff:ff:ff:ff", "") or m.startswith("01:00:5e"):
            continue
        out[ip] = norm(mac)
    return out


# Camera/NVR ports (`cameras.CAMERA_PORTS`) are part of the ordinary sweep:
# identifying every camera in the house is a first-class job, and a port list
# that stopped at 554 could not tell a Dahua recorder from an unknown box.
LAN_PORTS = sorted({21, 22, 23, 53, 80, 443, 445, 548, 554, 1883, 1900, 3389, 5000, 5900,
                    8080, 8443, 8883, 9100, 62078} | set(cameras.CAMERA_PORTS))
RISKY_PORTS = {23: "telnet", 445: "smb", 3389: "rdp", 5900: "vnc", 1900: "upnp", 21: "ftp"}


def lan_ports(ips):
    """Which services each online device exposes on the LAN (TCP connect, ~3 s for the whole house)."""
    def probe(t):
        ip, port = t
        try:
            with socket.create_connection((ip, port), timeout=0.45): return (ip, port)
        except Exception: return None
    out = {ip: [] for ip in ips}
    with concurrent.futures.ThreadPoolExecutor(64) as ex:
        for r in ex.map(probe, [(ip, p) for ip in ips for p in LAN_PORTS]):
            if r: out[r[0]].append(r[1])
    return {ip: sorted(p) for ip, p in out.items()}


def risk_score(d, unverified):
    """0-100 per device: unknown, risky services, exposure, instability."""
    s = 0
    if unverified: s += 40
    elif not d.get("trusted") and not d.get("label"): s += 10
    ports = d.get("ports") or {}
    risky = [p for p in ports if int(p) in RISKY_PORTS]
    s += 25 if risky else 0
    s += min(20, 5 * max(0, len(ports) - len(risky)))
    if d.get("flapping"): s += 10
    if d.get("quarantined"): s = 100
    return min(100, s)


def presence_state(d, online, now, flapping, base=None):
    fs = d.get("first_seen") or now; ls = d.get("last_seen") or now
    age_h = (dt.datetime.fromisoformat(now) - dt.datetime.fromisoformat(ls)).total_seconds() / 3600
    new = (dt.datetime.fromisoformat(now) - dt.datetime.fromisoformat(fs)).total_seconds() < 86400 and not d.get("trusted") and not d.get("label") and (not base or fs > base)
    if flapping: return "FLAPPING"
    if online: return "NEW" if new else "ONLINE"
    if age_h > 24 * 30: return "ARCHIVED"
    if d.get("always_on"): return "OFFLINE"
    return "SLEEPING" if age_h < 24 else "OFFLINE"


def mdns_names():
    """Bonjour: TVs, Chromecasts, Apple devices, printers, HomeKit gear announce friendly names. ~10s budget."""
    out = {}; t0 = time.time()
    if not util.IS_MAC:  # Linux sensor: avahi-browse gives name, host and address in one pass
        for l in util.run_for(["avahi-browse", "-artp"], 6).splitlines():
            f = l.split(";")
            if len(f) >= 8 and f[0] == "=" and f[2] == "IPv4" and re.match(r"^\d+\.\d+\.\d+\.\d+$", f[7]):
                tag = {"_googlecast._tcp": "cast", "_airplay._tcp": "airplay", "_hap._tcp": "homekit", "_ipp._tcp": "printer"}.get(f[4])
                out.setdefault(f[7], f[3] + (f" ({tag})" if tag else ""))
        return out
    for svc, tag in (("_googlecast._tcp", "cast"), ("_airplay._tcp", "airplay"), ("_hap._tcp", "homekit"), ("_ipp._tcp", "printer"), ("_smb._tcp", "smb"), ("_companion-link._tcp", "apple")):
        if time.time() - t0 > 12: break
        inst = []
        for l in util.run_for(["dns-sd", "-B", svc, "local."], 1.2).splitlines():
            if " Add " in l:
                n = l.split(svc.split(".")[0] + "." + svc.split(".")[1] + ".")[-1].strip()
                if n and n not in inst: inst.append(n)
        for n in inst[:6]:
            m = re.search(r"can be reached at (\S+?)\.?:(\d+)", util.run_for(["dns-sd", "-L", n, svc, "local."], 1.2))
            if not m: continue
            hn = m.group(1)
            for l in util.run_for(["dns-sd", "-G", "v4", hn if hn.endswith(".") else hn + "."], 1.2).splitlines():
                ip = re.search(r"\s(\d+\.\d+\.\d+\.\d+)\s", l)
                if ip and ip.group(1).startswith("192.168.") and ip.group(1) not in out:
                    out[ip.group(1)] = f"{n} ({tag})" if tag not in ("apple", "smb") else n
    return out


_VENDOR_CACHE = {}


def mac_vendor(mac):
    """Best-effort vendor name for a MAC. Locally-administered (randomized) MACs return 'private/randomized'."""
    if not mac:
        return "?"
    first = int(mac.split(":")[0], 16)
    if first & 0x02:
        return "private/randomized MAC (phone/laptop privacy)"
    key = mac[:8]
    if key in _VENDOR_CACHE:
        return _VENDOR_CACHE[key]
    v = None
    try:
        req = urllib.request.Request(f"https://api.macvendors.com/{mac}", headers={"User-Agent": "guardian-agent/1.0"})
        with urllib.request.urlopen(req, timeout=8, context=util.SSL_CTX) as r:
            v = r.read().decode()[:60]
    except urllib.error.HTTPError as e:
        if e.code == 404: v = "unknown vendor"
    except Exception:
        pass
    if v is None:
        try:
            req = urllib.request.Request(f"https://api.maclookup.app/v2/macs/{mac}", headers={"User-Agent": "guardian-agent/1.0"})
            with urllib.request.urlopen(req, timeout=8, context=util.SSL_CTX) as r:
                j = json.loads(r.read().decode()); v = (j.get("company") or ("unknown vendor" if j.get("found") is False else None))
                v = v[:60] if v else None
        except Exception:
            pass
    if v: _VENDOR_CACHE[key] = v
    time.sleep(1.1)  # macvendors free tier: 1 req/s
    return v or "?"


def host_name(ip, names=None):
    if names and names.get(ip): return names[ip]
    if not util.IS_MAC:
        try:
            o = subprocess.run(["getent", "hosts", ip], capture_output=True, text=True, timeout=4).stdout.split()
            return o[1] if len(o) > 1 else None
        except Exception: return None
    try:
        r = subprocess.run(["dscacheutil", "-q", "host", "-a", "ip_address", ip], capture_output=True, text=True, timeout=5)
        m = re.search(r"name:\s*(\S+)", r.stdout)
        if m:
            return m.group(1)
    except Exception:
        pass
    try:
        r = subprocess.run(["dig", "+short", "+time=2", "-x", ip], capture_output=True, text=True, timeout=6)
        return r.stdout.strip().rstrip(".") or None
    except Exception:
        return None
