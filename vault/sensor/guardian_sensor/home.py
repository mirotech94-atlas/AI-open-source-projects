"""Home network + ISP-path + Wi-Fi security collectors.

Ported from the Nexus collector (~/.claude/nexus-agent/checks.py) — logic
copied faithfully, never imported from that directory.
"""
import concurrent.futures
import datetime as dt
import json
import os
import re
import socket
import subprocess
import time
import urllib.request

from . import cameras, lan, util


def dns_check():
    """Resolver health: system resolver + 1.1.1.1 via dig, timed. Returns dict with ms per resolver and failures."""
    out = {}
    for name, extra in (("system", []), ("cloudflare", ["@1.1.1.1"]), ("google", ["@8.8.8.8"])):
        t0 = time.time()
        try:
            r = subprocess.run(["dig", "+short", "+time=2", "+tries=1", "example.com", "A"] + extra, capture_output=True, text=True, timeout=6)
            ok = r.returncode == 0 and bool(re.search(r"\d+\.\d+\.\d+\.\d+", r.stdout))
        except Exception:
            ok = False
        out[name] = {"ok": ok, "ms": int((time.time() - t0) * 1000)}
    return out


def speed_test():
    """macOS networkQuality (Apple's built-in responsiveness test). ~15-25s. Returns Mbps down/up + latency ms."""
    if not util.IS_MAC:
        try:  # Linux sensor: speedtest-cli if the technician installed it, otherwise a 10 MB download timing from Cloudflare
            t0 = time.time(); req = urllib.request.Request("https://speed.cloudflare.com/__down?bytes=10000000", headers={"User-Agent": "guardian-sensor"})
            with urllib.request.urlopen(req, timeout=60, context=util.SSL_CTX) as r:
                n = 0
                while True:
                    ch = r.read(65536)
                    if not ch: break
                    n += len(ch)
            el = time.time() - t0
            return {"down_mbps": round(n * 8 / el / 1e6, 1), "up_mbps": None, "latency_ms": None, "responsiveness_rpm": None, "ts": util.now_iso(), "method": "cloudflare-10MB"}
        except Exception as e:
            return {"error": str(e)[:120], "ts": util.now_iso()}
    try:
        r = subprocess.run(["/usr/bin/networkQuality", "-s", "-c"], capture_output=True, text=True, timeout=90)
        j = json.loads(r.stdout)
        return {"down_mbps": round(j.get("dl_throughput", 0) / 1e6, 1), "up_mbps": round(j.get("ul_throughput", 0) / 1e6, 1),
                "latency_ms": round(j.get("base_rtt", j.get("il_h2_req_resp", [0])[0] if isinstance(j.get("il_h2_req_resp"), list) else 0) or 0),
                "responsiveness_rpm": j.get("responsiveness") or j.get("dl_responsiveness"), "ts": util.now_iso()}
    except Exception as e:
        return {"error": str(e)[:120], "ts": util.now_iso()}


def host_load():
    """The sensor host's own one-minute load average, and how many cores it has
    to spread that over.

    Both halves matter: `0.06` on an 8-core Mac and `0.06` on a 1-core VPS mean
    different things, and a reader needs the core count to judge which. Returns
    `{"load1": None, "cores": None}` where the platform has no load average at
    all (Windows: `os.getloadavg` raises `OSError`) or refuses to say how many
    CPUs it has -- a missing figure is reported as missing rather than as 0.0,
    which would read as an idle machine.
    """
    try:
        load1 = round(os.getloadavg()[0], 2)
    except (OSError, AttributeError):
        load1 = None
    cores = os.cpu_count()
    return {"load1": load1, "cores": int(cores) if cores else None}


def wan_ip():
    for url in ("https://api.ipify.org", "https://ifconfig.me/ip"):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "curl/8"})
            with urllib.request.urlopen(req, timeout=8, context=util.SSL_CTX) as r:
                ip = r.read().decode().strip()
                if re.match(r"^\d+\.\d+\.\d+\.\d+$", ip):
                    return ip
        except Exception:
            continue
    return None


def wan_path(cfg, slow_ms=None):
    """ISP path health: latency / jitter / loss to two anchors and the VPS."""
    targets = {"cloudflare": "1.1.1.1", "google": "8.8.8.8"}
    # The gateway is pinged alongside the external anchors, and it is the
    # only one of them that can tell an ISP outage from a local one. If the
    # gateway answers and nothing beyond it does, the line is down and that
    # is the ISP's problem -- which is the distinction an SLA credit claim
    # turns on. If the gateway is down too, the fault is inside the
    # building and claiming otherwise once would destroy the feature's
    # credibility with the customer's own ISP.
    gw = (cfg.get("lan") or {}).get("expected_gateway")
    if gw:
        targets["gateway"] = gw
    h = util.cfg_host(cfg, (cfg.get("home") or {}).get("external_probe_host", ""))
    if h: targets[h["name"]] = h["target"].split("@")[-1]
    def _sample(ip, count):
        # One ping run. 20 packets, not 5: at 5 a single dropped packet reads
        # as 20% loss and trips the WARN, which is why a transient blip to two
        # anchors both showed exactly "20%". At 20 packets one drop is 5%, well
        # under the threshold, and a genuinely lossy line still shows its real
        # rate.
        r = subprocess.run(["ping", "-c", str(count), "-i", "0.2"] + util.PING_W(1000) + [ip],
                           capture_output=True, text=True, timeout=15).stdout
        loss = re.search(r"(\d+(?:\.\d+)?)% packet loss", r)
        rtt = re.search(r"= ([\d.]+)/([\d.]+)/([\d.]+)/([\d.]+) ms", r)
        return (float(loss.group(1)) if loss else 100.0,
                float(rtt.group(2)) if rtt else None,
                float(rtt.group(4)) if rtt else None)

    def ping(t):
        name, ip = t
        try:
            loss, avg, jitter = _sample(ip, 20)
            # Confirm any loss OR a latency spike with a second run: one slow
            # window must not become a "Latency X ms" WARN on its own.
            if loss > 0 or (slow_ms and avg and avg > slow_ms):
                # Confirm any loss with a second back-to-back run before we
                # believe it: a one-off burst clears on the retry, a real
                # problem does not. Report the LOWER of the two so a transient
                # spike never alerts on its own; a sustained line loses in both.
                loss2, avg2, jitter2 = _sample(ip, 20)
                if loss2 < loss or (loss2 == loss and avg2 is not None and (avg is None or avg2 < avg)):
                    loss, avg, jitter = loss2, avg2, jitter2
            return name, {"ip": ip, "loss_pct": loss, "avg_ms": avg, "jitter_ms": jitter}
        except Exception as e:
            return name, {"ip": ip, "loss_pct": 100.0, "avg_ms": None, "jitter_ms": None, "error": str(e)[:60]}
    with concurrent.futures.ThreadPoolExecutor(4) as ex: return dict(ex.map(ping, targets.items()))


def rogue_checks(cfg, wifi, on_home_lan, state):
    """Rogue DHCP server, wrong default route on the home SSID, evil-twin SSID
    on a new channel — the PRIMARY network, with the legacy signature and the
    flat `state` slice. `net_rogue_checks` is the per-network form."""
    net = {"expected_gateway": (cfg.get("lan") or {}).get("expected_gateway"), "primary": True}
    return net_rogue_checks(net, wifi, on_home_lan, state)


def net_rogue_checks(net, wifi, on_net, netstate):
    """The Wi-Fi guard for ONE network: `expected_gateway` comes from `net`,
    `home_ssid`/`twin_channels` from that network's state slice (`netstate`).

    Only the network this sensor's own Wi-Fi interface sits on can produce
    these facts; for any other network the hub reports "not verified on this
    network" rather than a false clean bill of health.
    """
    state = netstate if netstate is not None else {}
    out = {}
    try:
        if util.IS_MAC:
            pk = subprocess.run(["ipconfig", "getpacket", "en0"], capture_output=True, text=True, timeout=8).stdout
            m = re.search(r"server_identifier \(ip\): (\S+)", pk); out["dhcp_server"] = m.group(1) if m else None
            m = re.search(r"router \(ip_mult\): \{([^}]+)\}", pk); out["dhcp_router"] = m.group(1).strip() if m else None
        else:
            pk = subprocess.run(["nmcli", "-t", "-f", "DHCP4.OPTION", "dev", "show"], capture_output=True, text=True, timeout=8).stdout
            m = re.search(r"dhcp_server_identifier = (\S+)", pk); out["dhcp_server"] = m.group(1) if m else None
            m = re.search(r"routers = (\S+)", pk); out["dhcp_router"] = m.group(1) if m else None
    except Exception: pass
    out["default_gateway"] = lan.default_gateway()
    ssid = (wifi or {}).get("ssid"); home_ssid = state.get("home_ssid")
    if on_net and ssid and not home_ssid: state["home_ssid"] = ssid
    out["on_home_ssid"] = bool(ssid and ssid == state.get("home_ssid"))
    # Carried here so the evil-twin rule can compare a twin's encryption
    # against the network we are actually on, without reaching back into the
    # wifi payload from alerts.py.
    out["security"] = (wifi or {}).get("security") or ""
    try:
        # `twin_security` is what actually separates an attacker from your own
        # kit. Every modern router puts the SAME SSID on 2.4GHz and 5GHz, and a
        # mesh extender repeats it again -- so "the SSID is on another channel"
        # describes a normal house, not an attack, and channels move on their
        # own because routers pick them automatically. What a rogue AP cannot
        # fake for free is the encryption: it has to be open, or a weaker mode,
        # for a client to hand it anything. A twin advertising DIFFERENT
        # security from the one you are on is the signal worth waking someone
        # for; the channel list is only ever supporting detail.
        if not util.IS_MAC:
            o = subprocess.run(["nmcli", "-t", "-f", "ACTIVE,SSID,CHAN,SECURITY", "dev", "wifi"], capture_output=True, text=True, timeout=10).stdout
            fields = [l.split(":") for l in o.splitlines()]
            mine = state.get("home_ssid")
            out["twin_channels"] = sorted({int(f[2]) for f in fields if len(f) >= 3 and f[0] == "no" and f[1] == mine and f[2].isdigit()})
            out["twin_security"] = sorted({f[3].strip() for f in fields if len(f) >= 4 and f[0] == "no" and f[1] == mine and f[3].strip()})
            return out
        o = subprocess.run(["system_profiler", "SPAirPortDataType"], capture_output=True, text=True, timeout=20).stdout
        oth = o.split("Other Local Wi-Fi Networks:", 1)[1] if "Other Local Wi-Fi Networks:" in o else ""
        chans = set(); secs = set(); cur = None
        for line in oth.splitlines():
            mm = re.match(r"^\s{12}(\S.*):\s*$", line)
            if mm: cur = mm.group(1).strip(); continue
            if cur != state.get("home_ssid"): continue
            mc = re.search(r"Channel:\s*(\d+)", line)
            if mc: chans.add(int(mc.group(1)))
            ms = re.search(r"Security:\s*(.+?)\s*$", line)
            if ms: secs.add(ms.group(1).strip())
        out["twin_channels"] = sorted(chans)
        out["twin_security"] = sorted(secs)
    except Exception: pass
    return out


def tailscale_status():
    ts = "/usr/local/bin/tailscale" if os.path.exists("/usr/local/bin/tailscale") else "/Applications/Tailscale.app/Contents/MacOS/Tailscale"
    try:
        r = subprocess.run([ts, "status", "--json"], capture_output=True, text=True, timeout=15)
        if r.returncode != 0:
            return {"running": False, "error": (r.stderr or r.stdout).strip()[:100]}
        j = json.loads(r.stdout)
        peers = {p.get("HostName"): {"online": p.get("Online"), "ip": (p.get("TailscaleIPs") or [None])[0]} for p in j.get("Peer", {}).values()}
        return {"running": j.get("BackendState") == "Running", "state": j.get("BackendState"), "peers": peers}
    except Exception as e:
        return {"running": False, "error": str(e)[:100]}


# ---------------- home network security ----------------
STRONG_WIFI = ("WPA2 Personal", "WPA3 Personal", "WPA2/WPA3 Personal", "WPA2 Enterprise", "WPA3 Enterprise", "WPA2/WPA3 Enterprise")
# Camera ports are in the list the VPS knocks on: an internet-reachable
# camera is the single most common way a home camera gets watched by
# strangers, and until this landed the probe never asked about 554 at all, so
# `home:exposed` could not fire for a port-forwarded camera.
EXPOSURE_PORTS = sorted({21, 22, 23, 25, 53, 80, 443, 445, 3389, 5900, 8080, 8443, 7547,
                         1900, 5000, 8291, 32400, 49152} | set(cameras.CAMERA_PORTS))


def wifi_info():
    """Which Wi-Fi this sensor is on and how it is secured (macOS system_profiler / Linux nmcli). None when wired."""
    if not util.IS_MAC:
        try:
            o = subprocess.run(["nmcli", "-t", "-f", "ACTIVE,SSID,SECURITY,CHAN,SIGNAL", "dev", "wifi"], capture_output=True, text=True, timeout=10).stdout
            for l in o.splitlines():
                f = l.split(":")
                if f and f[0] == "yes" and len(f) >= 5:
                    sec = f[2].replace("WPA2", "WPA2 Personal").replace("WPA3", "WPA3 Personal") if f[2] else "Open"
                    return {"ssid": f[1], "security": sec, "channel": f[3], "phy": "", "signal": f"{f[4]}%"}
        except Exception: pass
        return None
    try:
        o = subprocess.run(["system_profiler", "SPAirPortDataType"], capture_output=True, text=True, timeout=20).stdout
    except Exception:
        return None
    m = re.search(r"Current Network Information:\n\s+(.+?):\n(.*?)(?:\n\s*\n|\Z)", o, re.S)
    if not m: return None
    blk = m.group(2); g = lambda k: (re.search(rf"{k}:\s*(.+)", blk) or [None, None])[1]
    return {"ssid": m.group(1).strip(), "security": (g("Security") or "?").strip(), "channel": (g("Channel") or "").strip(), "phy": (g("PHY Mode") or "").strip(),
            "signal": (g("Signal / Noise") or "").strip()}


def gateway_ports(gw):
    def probe(port):
        try:
            with socket.create_connection((gw, port), timeout=0.6): return port
        except Exception: return None
    with concurrent.futures.ThreadPoolExecutor(12) as ex:
        return sorted(p for p in ex.map(probe, [21, 22, 23, 53, 80, 443, 8080, 8443, 7547, 49152]) if p)


def external_exposure(cfg, wan):
    """Ask a VPS to knock on the home WAN IP: which ports does the internet see open?"""
    name = (cfg.get("home") or {}).get("external_probe_host", "")
    h = util.cfg_host(cfg, name)
    # A probe host that was configured and has since been retired used to
    # return None here, which every consumer reads as "not verified lately"
    # with no way to learn why -- the check had been silently dead for weeks.
    # Name the misconfiguration instead.
    if name and not h:
        return {"probe_host": name,
                "error": f"no ssh host named {name!r} is configured -- "
                         "the internet-exposure check cannot run"}
    if not (h and wan): return None
    script = ("import socket,sys,concurrent.futures as c\nip=sys.argv[1];P=[%s]\n"
              "def t(p):\n s=socket.socket();s.settimeout(2.5)\n try:\n  s.connect((ip,p));return p\n except Exception:return None\n finally:s.close()\n"
              "print(','.join(str(p) for p in c.ThreadPoolExecutor(20).map(t,P) if p))") % ",".join(map(str, EXPOSURE_PORTS))
    import base64
    b64 = base64.b64encode(script.encode()).decode()
    rc, out = util.ssh_run(h, f"echo {b64} | base64 -d | python3 - {wan}", timeout=60)
    if rc != 0: return {"probe_host": name, "error": out[-160:]}
    ports = [int(x) for x in out.strip().splitlines()[-1].split(",") if x.strip().isdigit()] if out.strip() else []
    return {"probe_host": name, "wan_ip": wan, "open_ports": ports, "checked": EXPOSURE_PORTS, "ts": util.now_iso()}


def home_security(cfg, res, state):
    gw = cfg["lan"]["expected_gateway"]
    home = {"wifi": wifi_info(), "gateway": gw, "gateway_ports": gateway_ports(gw)}
    prev = (state.get("home") or {}).get("exposure") or {}
    due = not prev.get("ts") or (dt.datetime.now().astimezone() - dt.datetime.fromisoformat(prev["ts"])).total_seconds() > 3300 or prev.get("wan_ip") != res.get("wan_ip")
    home["exposure"] = external_exposure(cfg, res.get("wan_ip")) if due else prev
    # unverified devices: joined after the baseline, not labelled, not trusted, seen in the last 24h
    known = (state.get("lan") or {}).get("known_macs") or {}
    base = min((d.get("first_seen") or "9" for d in known.values()), default=None)
    cutoff = (dt.datetime.now().astimezone() - dt.timedelta(hours=24)).isoformat(timespec="seconds")
    home["unverified"] = [{"mac": m, "ip": d.get("ip"), "vendor": d.get("vendor"), "name": d.get("name"), "first_seen": d.get("first_seen"), "last_seen": d.get("last_seen")}
                          for m, d in known.items() if not d.get("trusted") and not d.get("label") and base and (d.get("first_seen") or "") > base and (d.get("last_seen") or "") >= cutoff]
    return home
