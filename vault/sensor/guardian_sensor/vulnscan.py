"""On-demand vulnerability scan -- the manual, Nessus-shaped one.

The scheduled sweep already watches the whole estate every few minutes, but
it watches broadly and shallowly. This is the opposite: one target, looked
at hard, when the owner asks. It runs as a queued job (`/api/vuln-scan`) so
the dashboard can start it, watch it, and keep the report.

What it does, in order, for a LAN device or IP:
  1. TCP connect sweep of the common ports (quick: ~60, full: ~330), in a
     thread pool, connect-only -- nothing is sent until a port answers.
  2. On each open port, one gentle probe: a banner read, or for web ports a
     single HEAD /, or for TLS ports a handshake. That is enough to name the
     service and often its version.
  3. Rules over what came back: risky services (telnet, RDP, VNC, databases,
     unauthenticated MQTT, TR-069, UPnP), obsolete server versions in
     banners, TLS problems (expired, self-signed, TLS 1.0/1.1 accepted),
     missing web security headers.
For a configured SSH host it adds what only root on the box can see: the
listening sockets, sshd's password/root-login settings, fixable CVEs
(debsecan), unattended-upgrades, ufw. For a site it reuses the sweep's own
HTTP/TLS checks and probes the admin-panel paths.

What it never does: log in, guess a password, send an exploit, or fuzz.
Everything here is a connect, a read, or a HEAD. A finding says what was
seen and which Vault fix (if any) clears it; deciding is the owner's.

Stdlib only, like the rest of the sensor.
"""
import concurrent.futures
import datetime as dt
import json
import re
import socket
import ssl
import time
import urllib.error
import urllib.request

from . import paths, util

QUICK_PORTS = [21, 22, 23, 25, 53, 80, 81, 110, 111, 135, 139, 143, 161, 389, 443, 445, 465, 515, 548, 554,
               587, 631, 636, 873, 993, 995, 1080, 1433, 1521, 1883, 1900, 2049, 2375, 2376, 3000, 3306,
               3389, 3690, 4443, 5000, 5060, 5222, 5432, 5555, 5900, 5901, 6379, 6443, 7547, 8000, 8008,
               8080, 8081, 8123, 8443, 8883, 8888, 9000, 9090, 9100, 9200, 9443, 10000, 27017, 32400, 49152]
#: Item 24: a middle depth. Quick (66 ports) answers "is the obvious stuff
#: exposed" in about a minute; full (1119) sweeps every privileged port and
#: takes several. Between them sits the question people actually ask of a
#: server -- "is anything an attacker scans for listening" -- which is the
#: quick set plus the management, database, container and remote-access
#: ports that do not live under 1024. Roughly 150 ports, a handful of
#: seconds, and it catches the Docker socket, the exposed Redis and the
#: RDP that a quick scan walks straight past.
STANDARD_EXTRA = [1194, 1701, 1723, 2000, 2222, 2323, 3128, 3260, 4000, 4200, 4444, 5001, 5601,
                  5683, 5800, 6000, 6001, 6666, 6667, 7000, 7001, 7070, 7443, 7777, 8001, 8009,
                  8010, 8086, 8088, 8089, 8090, 8181, 8200, 8291, 8333, 8444, 8500, 8834, 8880,
                  9001, 9002, 9091, 9092, 9101, 9300, 9418, 9999, 11211, 15672, 25565, 27018,
                  28017, 50000, 51820]
STANDARD_PORTS = sorted(set(QUICK_PORTS) | set(STANDARD_EXTRA))

FULL_PORTS = sorted(set(QUICK_PORTS) | set(range(1, 1025)) | {1194, 1701, 1723, 2000, 2222, 2323, 3128, 3260,
             4000, 4200, 4444, 5001, 5353, 5601, 5683, 5800, 6000, 6001, 6666, 6667, 7000, 7001, 7070, 7443,
             7777, 8001, 8009, 8010, 8086, 8088, 8089, 8090, 8181, 8200, 8291, 8333, 8444, 8500, 8834, 8880,
             9001, 9002, 9091, 9092, 9101, 9300, 9418, 9999, 11211, 15672, 25565, 27018, 28017, 50000, 51820})

#: port -> (service name, default risk when open on a home LAN, why)
SERVICES = {
    21: ("ftp", "medium", "FTP sends passwords in clear text"),
    22: ("ssh", "info", "SSH is fine if it is key-only and patched"),
    23: ("telnet", "high", "telnet is unencrypted and usually a default password"),
    25: ("smtp", "low", "a mail relay on a home device is unusual"),
    53: ("dns", "info", "a resolver"),
    80: ("http", "info", "a web interface"), 81: ("http-alt", "info", "a web interface"),
    110: ("pop3", "medium", "clear-text mail"), 111: ("rpcbind", "medium", "RPC portmapper exposes NFS/NIS"),
    135: ("msrpc", "medium", "Windows RPC"), 139: ("netbios", "medium", "NetBIOS session service"),
    143: ("imap", "medium", "clear-text mail"), 161: ("snmp", "medium", "SNMP is often 'public' read"),
    389: ("ldap", "medium", "directory without TLS"), 443: ("https", "info", "a web interface (TLS)"),
    445: ("smb", "medium", "Windows file sharing; ransomware's favourite door"),
    465: ("smtps", "info", ""), 515: ("lpd", "low", "a printer"), 548: ("afp", "low", "Apple file sharing"),
    554: ("rtsp", "low", "a camera stream; check it needs a password"), 587: ("submission", "info", ""),
    631: ("ipp", "low", "a printer"), 636: ("ldaps", "info", ""), 873: ("rsync", "medium", "rsync daemon, often no auth"),
    993: ("imaps", "info", ""), 995: ("pop3s", "info", ""), 1080: ("socks", "high", "an open proxy"),
    1433: ("mssql", "high", "a database"), 1521: ("oracle", "high", "a database"),
    1883: ("mqtt", "medium", "MQTT without TLS; check it requires a login"), 1900: ("upnp-ssdp", "low", "UPnP discovery"),
    2049: ("nfs", "medium", "NFS export"), 2375: ("docker", "critical", "Docker API with no auth = root on the box"),
    2376: ("docker-tls", "medium", "Docker API (TLS)"), 3000: ("http-dev", "low", "a dev server"),
    3306: ("mysql", "high", "a database"), 3389: ("rdp", "high", "Remote Desktop; brute-forced constantly"),
    3690: ("svn", "low", ""), 4443: ("https-alt", "info", ""), 5000: ("http-5000", "low", "a web app / UPnP on some routers"),
    5060: ("sip", "low", "VoIP"), 5222: ("xmpp", "info", ""), 5432: ("postgres", "high", "a database"),
    5555: ("adb", "critical", "Android debug bridge = full control of the device"),
    5900: ("vnc", "high", "VNC; often no or weak password"), 5901: ("vnc-1", "high", "VNC"),
    6379: ("redis", "critical", "Redis with no auth is a remote shell"), 6443: ("k8s-api", "medium", "Kubernetes API"),
    7547: ("tr-069", "medium", "ISP remote management (CWMP)"), 8000: ("http-8000", "low", "a web app"),
    8008: ("http-8008", "low", "Chromecast / a web app"), 8080: ("http-proxy", "low", "a web interface"),
    8081: ("http-8081", "low", "a web interface"), 8123: ("home-assistant", "info", "Home Assistant"),
    8443: ("https-alt", "info", "a web interface (TLS)"), 8883: ("mqtts", "info", ""), 8888: ("http-8888", "low", ""),
    9000: ("http-9000", "low", ""), 9090: ("http-9090", "low", ""), 9100: ("jetdirect", "low", "raw printer port"),
    9200: ("elasticsearch", "critical", "Elasticsearch with no auth leaks everything in it"),
    9443: ("https-9443", "info", ""), 10000: ("webmin", "medium", "Webmin admin panel"),
    27017: ("mongodb", "critical", "MongoDB with no auth"), 32400: ("plex", "info", "Plex"),
    49152: ("upnp", "low", "UPnP control"), 2222: ("ssh-alt", "info", ""), 2323: ("telnet-alt", "high", "telnet"),
    8291: ("winbox", "medium", "MikroTik Winbox"), 11211: ("memcached", "high", "memcached, no auth"),
    15672: ("rabbitmq-ui", "medium", "RabbitMQ admin"), 5601: ("kibana", "medium", "Kibana"), 8834: ("nessus", "info", "Nessus"),
    5353: ("mdns", "info", ""), 51820: ("wireguard", "info", ""), 1194: ("openvpn", "info", ""),
}
TLS_PORTS = {443, 465, 636, 993, 995, 2376, 4443, 6443, 8443, 8883, 9443, 7443, 8444}
HTTP_PORTS = {80, 81, 3000, 5000, 8000, 8008, 8080, 8081, 8088, 8123, 8888, 9000, 9090, 10000, 15672, 5601, 32400, 8834}
SEV_ORDER = {"critical": 0, "high": 1, "medium": 2, "low": 3, "info": 4}

#: banner regex -> (severity, title). Versions that are years past end of
#: life, where the banner alone is enough to say "this needs updating".
OLD_BANNERS = [
    (r"OpenSSH_[1-6]\.", "high", "Very old OpenSSH (pre-7.x) — known remote vulnerabilities"),
    (r"OpenSSH_7\.[0-3]", "medium", "Old OpenSSH (7.0–7.3)"),
    (r"Apache/2\.[0-2]\.", "high", "Apache 2.0/2.2 is end-of-life"),
    (r"nginx/0\.|nginx/1\.[0-9]\.", "medium", "Old nginx (1.9 or earlier)"),
    (r"Dropbear sshd 20(0\d|1[0-6])", "medium", "Old Dropbear SSH"),
    (r"MiniUPnPd/1\.", "low", "Old MiniUPnPd"),
    (r"ProFTPD 1\.[0-2]", "high", "Old ProFTPD"),
    (r"vsFTPd 2\.", "medium", "Old vsftpd"),
    (r"Microsoft-IIS/[5-7]\.", "high", "IIS 5–7 is end-of-life"),
    (r"lighttpd/1\.4\.[0-3]\d\b", "medium", "Old lighttpd"),
]


def _now():
    return dt.datetime.now().astimezone().isoformat(timespec="seconds")


def probe(ip, port, timeout=0.8):
    try:
        with socket.create_connection((ip, port), timeout=timeout):
            return port
    except Exception:
        return None


def sweep(ip, ports, workers=48, timeout=0.8):
    with concurrent.futures.ThreadPoolExecutor(workers) as ex:
        return sorted(p for p in ex.map(lambda p: probe(ip, p, timeout), ports) if p)


def banner(ip, port, timeout=1.5):
    """One read. Most services talk first (SSH, FTP, SMTP); web ports get a
    HEAD so they have something to say."""
    try:
        with socket.create_connection((ip, port), timeout=timeout) as s:
            s.settimeout(timeout)
            if port in HTTP_PORTS:
                s.sendall(b"HEAD / HTTP/1.0\r\nHost: %s\r\nUser-Agent: guardian-scan/1.0\r\n\r\n" % ip.encode())
            data = s.recv(512)
        return data.decode("utf-8", "ignore").strip()[:300]
    except Exception:
        return ""


def tls_facts(ip, port, timeout=4.0):
    """Verified handshake first; if that fails, an unverified one so the
    version still gets read. Then: does it accept TLS 1.0/1.1?"""
    out = {"verified": None, "version": None, "expires": None, "days_left": None, "issuer": None, "old_tls": None, "error": None}
    try:
        with socket.create_connection((ip, port), timeout=timeout) as sk:
            with util.SSL_CTX.wrap_socket(sk, server_hostname=ip) as ss:
                cert = ss.getpeercert(); out["version"] = ss.version(); out["verified"] = True
        exp = dt.datetime.strptime(cert["notAfter"], "%b %d %H:%M:%S %Y %Z").replace(tzinfo=dt.timezone.utc)
        out["expires"] = exp.date().isoformat(); out["days_left"] = (exp - dt.datetime.now(dt.timezone.utc)).days
        out["issuer"] = dict(x[0] for x in cert.get("issuer", [])).get("organizationName")
    except ssl.SSLCertVerificationError as e:
        out["verified"] = False; out["error"] = str(e)[:120]
        try:
            ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT); ctx.check_hostname = False; ctx.verify_mode = ssl.CERT_NONE
            with socket.create_connection((ip, port), timeout=timeout) as sk:
                with ctx.wrap_socket(sk, server_hostname=ip) as ss: out["version"] = ss.version()
        except Exception:
            pass
    except Exception as e:
        out["error"] = str(e)[:120]
        return out
    try:
        import warnings
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT); ctx.check_hostname = False; ctx.verify_mode = ssl.CERT_NONE
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", DeprecationWarning)
            ctx.minimum_version = ssl.TLSVersion.TLSv1; ctx.maximum_version = ssl.TLSVersion.TLSv1_1
        ctx.set_ciphers("ALL:@SECLEVEL=0")
        with socket.create_connection((ip, port), timeout=timeout) as sk:
            with ctx.wrap_socket(sk, server_hostname=ip) as ss: out["old_tls"] = ss.version()
    except ssl.SSLError:
        out["old_tls"] = False
    except Exception:
        out["old_tls"] = None
    return out


def http_headers(ip, port, tls, timeout=6.0):
    scheme = "https" if tls else "http"
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT); ctx.check_hostname = False; ctx.verify_mode = ssl.CERT_NONE
    try:
        req = urllib.request.Request(f"{scheme}://{ip}:{port}/", method="HEAD", headers={"User-Agent": "guardian-scan/1.0"})
        with urllib.request.urlopen(req, timeout=timeout, context=ctx) as r:
            return r.status, {k.lower(): v for k, v in r.headers.items()}
    except urllib.error.HTTPError as e:
        return e.code, {k.lower(): v for k, v in e.headers.items()}
    except Exception:
        return None, {}


def finding(severity, title, evidence="", port=None, fix=None, cve=None):
    return {"severity": severity, "title": title, "evidence": evidence[:300], "port": port, "fix": fix, "cve": cve}


def rules_for_port(port, bnr, tls, hdrs, code):
    """Findings for one open port, from what the probe saw."""
    out = []
    name, risk, why = SERVICES.get(port, (f"tcp/{port}", "low", "an unrecognised service"))
    if risk != "info":
        out.append(finding(risk, f"{name} open on port {port}", why or bnr, port))
    for rx, sev, title in OLD_BANNERS:
        if bnr and re.search(rx, bnr):
            out.append(finding(sev, title, bnr, port))
            break
    if tls:
        if tls.get("verified") is False:
            out.append(finding("low", "Certificate is self-signed or not trusted", tls.get("error") or "", port))
        if tls.get("days_left") is not None and tls["days_left"] < 0:
            out.append(finding("high", "Certificate has expired", f"expired {tls.get('expires')}", port))
        elif tls.get("days_left") is not None and tls["days_left"] < 14:
            out.append(finding("medium", f"Certificate expires in {tls['days_left']} days", tls.get("expires") or "", port))
        if tls.get("old_tls"):
            out.append(finding("medium", f"Accepts obsolete {tls['old_tls']}", "TLS 1.0/1.1 handshake succeeded", port))
    if hdrs is not None and code is not None:
        missing = [h for h, k in (("HSTS", "strict-transport-security"), ("CSP", "content-security-policy"),
                                  ("X-Frame-Options", "x-frame-options"), ("nosniff", "x-content-type-options")) if k not in hdrs]
        if tls and missing:
            out.append(finding("low", "Missing web security headers: " + ", ".join(missing), f"HTTP {code}", port))
        srv = hdrs.get("server", "")
        for rx, sev, title in OLD_BANNERS:
            if srv and re.search(rx, srv):
                out.append(finding(sev, title, srv, port))
                break
        if code in (200,) and port in (80, 8080, 8000, 8081) and not tls:
            out.append(finding("low", "Web interface without TLS", f"HTTP {code} on port {port}", port))
    return out


#: Profile -> the port list it sweeps. Anything unrecognised falls back to
#: quick rather than to the slowest option: an unknown profile is a bug, and
#: a bug should not launch a several-minute scan.
PROFILE_PORTS = {"quick": QUICK_PORTS, "standard": STANDARD_PORTS, "full": FULL_PORTS}


def scan_device(ip, profile="quick"):
    ports = PROFILE_PORTS.get(profile, QUICK_PORTS)
    t0 = time.time()
    open_ports = sweep(ip, ports)
    rows, findings = [], []
    for port in open_ports:
        bnr = banner(ip, port) if port not in TLS_PORTS else ""
        tls = tls_facts(ip, port) if port in TLS_PORTS else None
        code, hdrs = (None, None)
        if port in HTTP_PORTS or port in TLS_PORTS:
            code, hdrs = http_headers(ip, port, port in TLS_PORTS)
        name = SERVICES.get(port, (f"tcp/{port}",))[0]
        rows.append({"port": port, "service": name, "banner": bnr, "tls": tls, "http": code})
        findings.extend(rules_for_port(port, bnr, tls, hdrs, code))
    if not open_ports:
        findings.append(finding("info", "No open TCP ports found", f"{len(ports)} ports checked"))
    findings.extend(_gateway_context(ip, open_ports))
    return {"kind": "device", "target": ip, "profile": profile, "ports": rows, "findings": findings,
            "checked": len(ports), "duration_s": round(time.time() - t0, 1)}


def _gateway_context(ip, open_ports):
    """For the router itself, say what the router's own findings mean.

    A home router's admin page is plain HTTP on the LAN with a self-signed
    certificate on 443 -- that is the vendor default on nearly every model,
    and Vault cannot change it: those are settings inside the router.
    What matters is whether the internet can reach that page. The scheduled
    sweep already knocks on the WAN address from a VPS (`home.external_exposure`),
    so the answer is on disk; it is reported here, beside the findings it
    qualifies, rather than leaving two "low" lines that read as something to
    fix and cannot be.
    """
    try:
        with open(paths.p("last.json")) as f:
            last = json.load(f)
    except Exception:
        return []
    home = last.get("home") or {}
    if ip != home.get("gateway"):
        return []
    exp = home.get("exposure") or {}
    if not exp or exp.get("error"):
        return [finding("info", "This is your router; internet exposure not yet measured", "the WAN probe has not run")]
    wan_open = set(exp.get("open_ports") or [])
    admin = {80, 443, 8080, 8443, 23, 22} & set(open_ports)
    leaked = sorted(admin & wan_open)
    if leaked:
        return [finding("critical", "Router admin page is reachable from the internet",
                        f"ports {leaked} open on WAN {exp.get('wan_ip')} -- turn off remote management in the router", fix=None)]
    return [finding("info", "This is your router; its admin page is LAN-only",
                    f"WAN probe from {exp.get('probe_host')}: none of {sorted(admin) or '[]'} reachable from the internet. "
                    "The self-signed certificate and plain-HTTP admin page are the router's defaults and cannot be changed from here.")]


_HOST_SCRIPT = r'''
echo "== LISTEN"; (ss -tlnpH 2>/dev/null || netstat -tlnp 2>/dev/null) | head -60
echo "== SSHD"; sshd -T 2>/dev/null | grep -E "^(passwordauthentication|permitrootlogin|permitemptypasswords|x11forwarding) "
echo "== UFW"; ufw status 2>/dev/null | head -1
echo "== UNATT"; test -f /etc/apt/apt.conf.d/20auto-upgrades && grep -c Unattended-Upgrade /etc/apt/apt.conf.d/20auto-upgrades || echo 0
echo "== UPG"; apt list --upgradable 2>/dev/null | grep -c "/" 
echo "== SECUPG"; apt list --upgradable 2>/dev/null | grep -ci security
echo "== CVE"; if command -v debsecan >/dev/null 2>&1; then UPGPK=" $(apt list --upgradable 2>/dev/null | awk -F/ "NR>1{print \$1}" | tr "\n" " ") "; debsecan --suite $(. /etc/os-release; echo $VERSION_CODENAME) --only-fixed --format summary 2>/dev/null | grep "^CVE" | awk -v u="$UPGPK" "index(u, \" \" \$2 \" \")" | sort -u | head -40; else echo NODEBSECAN; fi
echo "== CVENOUP"; if command -v debsecan >/dev/null 2>&1; then UPGPK=" $(apt list --upgradable 2>/dev/null | awk -F/ "NR>1{print \$1}" | tr "\n" " ") "; debsecan --suite $(. /etc/os-release; echo $VERSION_CODENAME) --only-fixed --format summary 2>/dev/null | grep "^CVE" | awk -v u="$UPGPK" "!index(u, \" \" \$2 \" \")" | awk "{print \$2}" | sort | uniq -c | sort -rn | head -10; fi
echo "== REBOOT"; test -f /var/run/reboot-required && echo yes || echo no
echo "== SUID"; find / -xdev -perm -4000 -type f 2>/dev/null | grep -vE "^/(usr/)?(bin|sbin|lib|lib64|libexec)/" | head -10
echo "== END"
'''


def scan_host(cfg, name):
    """A configured SSH host: what only root on the box can see."""
    h = util.cfg_host(cfg, name)
    if not h:
        return {"kind": "host", "target": name, "findings": [finding("info", "Unknown host", f"{name!r} is not a configured SSH host")], "error": "unknown host"}
    t0 = time.time()
    rc, out = util.ssh_run(h, _HOST_SCRIPT, timeout=180)
    if rc != 0 and "== END" not in (out or ""):
        return {"kind": "host", "target": name, "findings": [finding("high", "Host unreachable over SSH", (out or "")[-160:])], "error": "ssh failed"}
    sec = {}
    cur = None
    for line in (out or "").splitlines():
        if line.startswith("== "):
            cur = line[3:].strip(); sec[cur] = []
        elif cur:
            sec[cur].append(line)
    findings, facts = [], {}
    listen = sec.get("LISTEN", [])
    facts["listening"] = [l.strip() for l in listen if l.strip()]
    for l in listen:
        m = re.search(r"(\S+):(\d+)\s", l)
        if not m:
            continue
        addr, port = m.group(1), int(m.group(2))
        exposed = addr in ("0.0.0.0", "*", "::", "[::]")
        svc, risk, why = SERVICES.get(port, (f"tcp/{port}", "low", ""))
        if exposed and risk in ("critical", "high", "medium"):
            findings.append(finding(risk, f"{svc} listening on all interfaces (port {port})", why or l.strip(), port,
                                    fix="enable_ufw"))
    sshd = " ".join(sec.get("SSHD", []))
    facts["sshd"] = sshd
    if "passwordauthentication yes" in sshd:
        findings.append(finding("high", "SSH accepts passwords", "sshd: passwordauthentication yes", 22, fix="disable_ssh_password"))
    if "permitrootlogin yes" in sshd:
        findings.append(finding("high", "Root may log in over SSH with a password", "sshd: permitrootlogin yes", 22, fix="disable_ssh_password"))
    ufw = " ".join(sec.get("UFW", []))
    facts["ufw"] = ufw
    if "inactive" in ufw or not ufw.strip():
        findings.append(finding("medium", "No host firewall (ufw inactive)", ufw.strip() or "ufw not installed", fix="enable_ufw"))
    unatt = "".join(sec.get("UNATT", [])).strip()
    facts["unattended_upgrades"] = unatt not in ("", "0")
    if unatt in ("", "0"):
        findings.append(finding("medium", "Unattended security upgrades are off", "no 20auto-upgrades", fix="enable_unattended"))
    try:
        upg = int("".join(sec.get("UPG", [])).strip() or 0); secupg = int("".join(sec.get("SECUPG", [])).strip() or 0)
    except ValueError:
        upg = secupg = 0
    facts["upgradable"] = upg; facts["security_upgradable"] = secupg
    if secupg:
        findings.append(finding("high", f"{secupg} security update(s) waiting", f"{upg} packages upgradable", fix="security_updates"))
    elif upg > 20:
        findings.append(finding("low", f"{upg} packages upgradable", "", fix="full_upgrade"))
    cves = [l.strip() for l in sec.get("CVE", []) if l.startswith("CVE")]
    if sec.get("CVE") and sec["CVE"] and sec["CVE"][0].strip() == "NODEBSECAN":
        findings.append(finding("info", "debsecan not installed — CVE list unavailable", "", fix="install_debsecan"))
    facts["cves"] = cves
    for c in cves[:25]:
        parts = c.split()
        findings.append(finding("medium", f"Fixable vulnerability {parts[0]}", " ".join(parts[1:])[:120], cve=parts[0], fix="security_updates"))
    if cves:
        findings.append(finding("high" if len(cves) >= 10 else "medium", f"{len(cves)} fixable CVE(s) reported by debsecan", "run the security updates", fix="security_updates"))
    noup = [l.strip() for l in sec.get("CVENOUP", []) if l.strip()]
    if noup:
        # debsecan judges by Debian's version table; a vendor-repo package
        # (Caddy from cloudsmith, say) has no apt update to apply, so this is
        # a fact to know, not a fix Vault can run.
        pk = ", ".join(f"{p.split()[1]} ({p.split()[0]})" for p in noup[:5] if len(p.split()) >= 2)
        findings.append(finding("info", "CVEs listed by debsecan for packages with no update available", f"vendor-packaged or not yet fixed in this suite: {pk}"))
    if "".join(sec.get("REBOOT", [])).strip() == "yes":
        findings.append(finding("low", "Reboot required to finish an update", "/var/run/reboot-required", fix="reboot"))
    suid = [l.strip() for l in sec.get("SUID", []) if l.strip()]
    if suid:
        findings.append(finding("low", f"{len(suid)} setuid binaries outside system paths", ", ".join(suid[:5])))
    return {"kind": "host", "target": name, "profile": "host", "facts": facts, "findings": findings, "duration_s": round(time.time() - t0, 1)}


def scan_site(cfg, name_or_url):
    from . import sites
    site = next((s for s in (cfg.get("sites") or []) if s.get("name") == name_or_url or s.get("url") == name_or_url), None)
    if site is None:
        site = {"name": name_or_url, "url": name_or_url if name_or_url.startswith("http") else f"https://{name_or_url}/", "expect": [200]}
    t0 = time.time()
    res = sites.check_site(site, {})
    findings = []
    if not res.get("ok"):
        findings.append(finding("medium", "Site did not answer as expected", f"HTTP {res.get('code')} {res.get('error') or ''}"))
    d = res.get("cert_days_left")
    if d is not None and d < 0:
        findings.append(finding("high", "Certificate has expired", res.get("cert_expires") or ""))
    elif d is not None and d < 14:
        findings.append(finding("medium", f"Certificate expires in {d} days", res.get("cert_expires") or ""))
    if res.get("cert_error"):
        findings.append(finding("low", "Certificate problem", res["cert_error"]))
    sec = res.get("sec") or {}
    if sec:
        missing = [n for n, k in (("HSTS", "hsts"), ("nosniff", "nosniff"), ("X-Frame-Options", "frame"), ("CSP", "csp")) if not sec.get(k)]
        if missing:
            findings.append(finding("low", "Missing web security headers: " + ", ".join(missing), "", fix="security_headers"))
        if sec.get("https_redirect") is False:
            findings.append(finding("medium", "HTTP does not redirect to HTTPS", "port 80 answers without redirecting"))
        if sec.get("old_tls"):
            findings.append(finding("medium", f"Accepts obsolete {sec['old_tls']}", ""))
        for rx, sev, title in OLD_BANNERS:
            if sec.get("server") and re.search(rx, sec["server"]):
                findings.append(finding(sev, title, sec["server"])); break
    host = site.get("host") or re.sub(r"^https?://([^/]+).*", r"\1", site["url"])
    for path, (sev, sig) in sites.ADMIN_PATHS.items():
        try:
            req = urllib.request.Request(f"https://{host}{path}", headers={"User-Agent": "guardian-scan/1.0"})
            with urllib.request.urlopen(req, timeout=8, context=util.SSL_CTX) as r:
                body = r.read(3000).decode("utf-8", "ignore")
                if r.status == 200 and (sig is None or re.search(sig, body)):
                    findings.append(finding({"CRIT": "critical", "WARN": "medium", "INFO": "low"}[sev], f"Sensitive path reachable: {path}", f"HTTP 200 at {path}"))
        except Exception:
            pass
    return {"kind": "site", "target": site["name"], "profile": "site", "site": {k: v for k, v in res.items() if k != "sec"}, "sec": sec,
            "findings": findings, "duration_s": round(time.time() - t0, 1)}


def _lan_ips(limit=64):
    try:
        with open(paths.p("state.json")) as f:
            st = json.load(f)
    except Exception:
        return []
    known = ((st.get("lan") or {}).get("known_macs") or {})
    ips = sorted({d.get("ip") for d in known.values() if d.get("ip") and d.get("presence", "ONLINE") != "OFFLINE"})
    return ips[:limit]


def _summarise(findings):
    counts = {k: 0 for k in SEV_ORDER}
    for f in findings:
        counts[f["severity"]] = counts.get(f["severity"], 0) + 1
    # 100 minus weighted deductions, floored at 0 -- the same shape as the
    # security score, so the two read alike.
    score = max(0, 100 - counts["critical"] * 25 - counts["high"] * 10 - counts["medium"] * 4 - counts["low"] * 1)
    return counts, score


def run(cfg, body):
    """Entry point for the `/api/vuln-scan` queue job (and `/api/deep-dive`,
    which is a quick device scan by another name)."""
    target = str(body.get("target") or "").strip()
    kind = body.get("kind") or ("lan" if target in ("", "lan", "all") else None)
    profile = body.get("profile") or "quick"
    started = _now()
    if kind is None:
        if re.match(r"^\d+\.\d+\.\d+\.\d+$", target):
            kind = "device"
        elif target.startswith("http") or "." in target and not util.cfg_host(cfg, target):
            kind = "site"
        elif util.cfg_host(cfg, target):
            kind = "host"
        else:
            kind = "device"
    if kind == "lan":
        ips = _lan_ips()
        devices = [scan_device(ip, "quick") for ip in ips]
        findings = [dict(f, target=d["target"]) for d in devices for f in d["findings"] if f["severity"] != "info"]
        counts, score = _summarise(findings)
        return {"kind": "lan", "target": "lan", "profile": "quick", "devices": devices, "findings": findings,
                "summary": counts, "score": score, "started": started, "finished": _now()}
    if kind == "host":
        out = scan_host(cfg, target)
    elif kind == "site":
        out = scan_site(cfg, target)
    else:
        out = scan_device(target, profile)
    out["findings"].sort(key=lambda f: SEV_ORDER.get(f["severity"], 9))
    counts, score = _summarise(out["findings"])
    out.update({"summary": counts, "score": score, "started": started, "finished": _now()})
    return out
