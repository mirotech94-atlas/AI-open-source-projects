"""Site health/security collector + admin-panel exposure + external-port probes.

Ported from the Nexus collector (~/.claude/nexus-agent/checks.py) — logic
copied faithfully, never imported from that directory.
"""
import datetime as dt
import json
import re
import socket
import ssl
import subprocess
import time
import urllib.error
import urllib.request
import warnings

from . import util

# ---------------- sites ----------------
def check_site(s, th):
    out = {"name": s["name"], "url": s["url"], "ok": False}
    t0 = time.time()
    try:
        req = urllib.request.Request(s["url"], headers={"User-Agent": "guardian-agent/1.0"})
        with urllib.request.urlopen(req, timeout=15, context=util.SSL_CTX) as r:
            body = r.read(4000).decode("utf-8", "ignore")
            out["code"] = r.status; hdrs = {k.lower(): v for k, v in r.headers.items()}
    except urllib.error.HTTPError as e:
        out["code"] = e.code; body = ""; hdrs = {k.lower(): v for k, v in e.headers.items()}
    except Exception as e:
        out["code"] = None; out["error"] = str(e)[:120]; body = ""; hdrs = {}
    out["latency_s"] = round(time.time() - t0, 2)
    out["ok"] = out["code"] in s.get("expect", [200]) and (not s.get("expect_body") or s["expect_body"] in body)
    # DNS + cert
    host = s.get("host") or re.sub(r"^https?://([^/]+).*", r"\1", s["url"])
    try:
        out["dns_a"] = sorted({ai[4][0] for ai in socket.getaddrinfo(host, 443, socket.AF_INET)})
    except Exception as e:
        out["dns_a"] = []; out["dns_error"] = str(e)[:80]
    try:
        with socket.create_connection((host, 443), timeout=10) as sock:
            with util.SSL_CTX.wrap_socket(sock, server_hostname=host) as ss:
                cert = ss.getpeercert()
        exp = dt.datetime.strptime(cert["notAfter"], "%b %d %H:%M:%S %Y %Z").replace(tzinfo=dt.timezone.utc)
        out["cert_expires"] = exp.date().isoformat()
        out["cert_days_left"] = (exp - dt.datetime.now(dt.timezone.utc)).days
        out["cert_issuer"] = dict(x[0] for x in cert.get("issuer", [])).get("organizationName", "?")
        # The leaf is not always the first to lapse. A site stops working
        # when ANY certificate in its served chain expires, and an
        # intermediate going first is invisible above -- see `chain_expiry`.
        chain = chain_expiry(host)
        if chain:
            out["cert_chain_days_left"] = chain["days_left"]
            out["cert_chain_weakest"] = chain["weakest"]
            out["cert_chain_len"] = chain["certs"]
    except Exception as e:
        out["cert_error"] = str(e)[:100]
    if s["url"].startswith("https://") and hdrs:
        out["sec"] = site_security(host, hdrs)
    return out


#: Certificates to read out of a served chain. A real chain is leaf plus one
#: or two intermediates; anything longer is a misconfiguration and reading
#: more of it would only slow the sweep.
CHAIN_MAX = 4


def _split_pems(text: str) -> list[str]:
    """Every PEM block in `openssl s_client` output, in the order served."""
    out, buf = [], None
    for line in (text or "").splitlines():
        if line.startswith("-----BEGIN CERTIFICATE-----"):
            buf = [line]
        elif buf is not None:
            buf.append(line)
            if line.startswith("-----END CERTIFICATE-----"):
                out.append("\n".join(buf))
                buf = None
    return out[:CHAIN_MAX]


def _chain_pems(host: str, port: int = 443) -> list[str]:
    """The served chain, via openssl. Empty when it cannot be read."""
    r = subprocess.run(
        ["openssl", "s_client", "-connect", f"{host}:{port}", "-servername", host,
         "-showcerts"],
        input="", capture_output=True, text=True, timeout=12,
    )
    return _split_pems(r.stdout)


def _cert_enddate(pem: str) -> str | None:
    """One certificate's `notAfter`, or None when openssl cannot read it."""
    r = subprocess.run(["openssl", "x509", "-noout", "-enddate"],
                       input=pem, capture_output=True, text=True, timeout=8)
    line = (r.stdout or "").strip()
    return line.split("=", 1)[1].strip() if "=" in line else None


def chain_expiry(host: str, port: int = 443) -> dict | None:
    """When the SERVED CHAIN expires, which is not always when the leaf does.

    A site stops working when any certificate in its chain lapses, and an
    intermediate expiring next week behind a leaf good for seventy days is
    invisible to a leaf-only check -- it reports "74 days left", a confident
    number pointing at the wrong certificate. That is the first of the trust
    plan's four certificate bugs (§2.5I).

    Reads the chain with `openssl s_client` rather than adding a dependency.
    `ssl.SSLSocket.get_verified_chain()` is Python 3.13 and this sensor
    targets 3.10, which appeared to leave "add `cryptography` to an agent
    people install as root" as the only route -- a supply-chain decision
    rather than a bug fix. The sensor already shells out to openssl for the
    host-side certificate inventory, so this costs a subprocess instead.

    Returns None when the chain could not be read AT ALL -- no openssl, a
    timeout, a refused connection. Unmeasured is not healthy, and a caller
    that got None still has the leaf's own expiry to report; a caller that
    got a number can trust it covers everything served.
    """
    try:
        pems = _chain_pems(host, port)
    except Exception:
        return None
    if not pems:
        return None

    now = dt.datetime.now(dt.timezone.utc)
    seen = []
    for i, pem in enumerate(pems):
        try:
            ends = _cert_enddate(pem)
        except Exception:
            ends = None
        if not ends:
            # One unreadable certificate must not become the weakest link,
            # nor discard the ones that did parse.
            continue
        try:
            when = dt.datetime.strptime(ends, "%b %d %H:%M:%S %Y %Z").replace(
                tzinfo=dt.timezone.utc)
        except ValueError:
            continue
        seen.append((when, "leaf" if i == 0 else f"intermediate {i}"))
    if not seen:
        return None

    when, which = min(seen)
    return {"days_left": (when - now).days, "expires": when.date().isoformat(),
            "weakest": which, "certs": len(seen)}


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k): return None


def site_security(host, hdrs):
    """Cheap web-hardening checks: security headers, http->https redirect, obsolete TLS accepted."""
    sec = {"hsts": "strict-transport-security" in hdrs, "nosniff": hdrs.get("x-content-type-options", "").lower() == "nosniff",
           "frame": "x-frame-options" in hdrs or "frame-ancestors" in hdrs.get("content-security-policy", ""),
           "csp": "content-security-policy" in hdrs, "server": hdrs.get("server", "")[:40], "https_redirect": None, "old_tls": None}
    try:
        op = urllib.request.build_opener(_NoRedirect)
        r = op.open(urllib.request.Request(f"http://{host}/", method="HEAD", headers={"User-Agent": "guardian-agent/1.0"}), timeout=8)
        sec["https_redirect"] = False
    except urllib.error.HTTPError as e:
        sec["https_redirect"] = e.code in (301, 302, 307, 308) and str(e.headers.get("Location", "")).startswith("https://")
    except Exception:
        sec["https_redirect"] = None  # port 80 closed = fine
    try:
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT); ctx.check_hostname = False; ctx.verify_mode = ssl.CERT_NONE
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", DeprecationWarning)
            ctx.minimum_version = ssl.TLSVersion.TLSv1; ctx.maximum_version = ssl.TLSVersion.TLSv1_1
        ctx.set_ciphers("ALL:@SECLEVEL=0")
        with socket.create_connection((host, 443), timeout=6) as sk:
            with ctx.wrap_socket(sk, server_hostname=host) as ss: sec["old_tls"] = ss.version()
    except ssl.SSLError:
        sec["old_tls"] = False
    except Exception:
        sec["old_tls"] = None
    return sec


# path -> (severity, signature regex that must appear in the body; None = any non-catch-all 200)
ADMIN_PATHS = {"/.env": ("CRIT", r"(?m)^[A-Z_]{3,}=\S"), "/.git/config": ("CRIT", r"\[core\]"), "/phpmyadmin/": ("WARN", r"phpMyAdmin"), "/server-status": ("WARN", r"Apache Server Status|Server Version"),
               "/actuator/env": ("WARN", r"propertySources|activeProfiles"), "/wp-login.php": ("INFO", r"wp-login|wordpress"), "/admin": ("INFO", None), "/console": ("INFO", None)}


#: A page that asks for credentials is a protected panel, not an exposed
#: one -- the whole point of the probe is panels that render WITHOUT one.
_LOGIN_FORM = re.compile(r'type=["\']?password["\']?|name=["\']?(?:password|passwd|pwd)["\']?', re.I)


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Report a 3xx as the answer instead of following it: an admin path that
    bounces to a login host (307 -> https://admin.example/) is protected,
    and following it would grade the login page it lands on instead."""
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


_PROBE_OPENER = urllib.request.build_opener(_NoRedirect, urllib.request.HTTPSHandler(context=util.SSL_CTX))


def _fetch(u):
    """`(status, content_type, first 4000 bytes)` for one probe URL; a 3xx
    comes back as its own status, never followed. Module-level so tests can
    stand in a fake without touching the network."""
    try:
        req = urllib.request.Request(u, headers={"User-Agent": "guardian-agent/1.0"})
        with _PROBE_OPENER.open(req, timeout=8) as r:
            return r.status, (r.headers.get("Content-Type") or ""), r.read(4000).decode("utf-8", "ignore")
    except urllib.error.HTTPError as e:
        return e.code, "", ""
    except Exception:
        return None, "", ""


def admin_probe(sites, state):
    """Exposed admin panels / leaked files on each site, once a day. Single-page apps answer 200 for every path, so a random path is fetched first as the catch-all baseline."""
    out = dict(state.get("admin_probe") or {}); now = dt.datetime.now().astimezone()
    get = _fetch
    for s in sites:
        base = re.sub(r"^(https?://[^/]+).*", r"\1", s["url"]); cur = out.get(s["name"])
        if cur and cur.get("ts") and (now - dt.datetime.fromisoformat(cur["ts"])).total_seconds() < 86400: continue
        st0, _, body0 = get(base + "/guardian-probe-" + str(int(time.time()))); catch_all = st0 == 200
        hits = {}
        for path, (sev, sig) in ADMIN_PATHS.items():
            st, ctype, body = get(base + path)
            if st != 200: continue
            if sig:
                if re.search(sig, body, re.I) and not (path == "/.env" and "text/html" in ctype): hits[path] = sev
            elif _LOGIN_FORM.search(body):
                # A panel that asks for a password is protected, not exposed.
                continue
            elif not catch_all or body[:1500] != body0[:1500]: hits[path] = sev
        out[s["name"]] = {"hits": hits, "catch_all": catch_all, "ts": util.now_iso()}
    return out


def external_sites(cfg, sites, state):
    """From the VPS: which ports do the site hosts expose to the internet (daily)."""
    out = dict(state.get("external_sites") or {}); now = dt.datetime.now().astimezone()
    name = (cfg.get("home") or {}).get("external_probe_host", ""); h = util.cfg_host(cfg, name)
    if not h: return out
    todo = {}
    for s in sites:
        host = s.get("host") or re.sub(r"^https?://([^/]+).*", r"\1", s["url"]); ip = (s.get("dns_a") or [None])[0]
        cur = out.get(host)
        if ip and not (cur and cur.get("ts") and (now - dt.datetime.fromisoformat(cur["ts"])).total_seconds() < 86400): todo[host] = ip
    if not todo: return out
    ports = [21, 22, 23, 25, 53, 80, 110, 143, 443, 445, 1433, 3000, 3306, 3389, 5432, 5900, 6379, 8000, 8080, 8443, 9200, 27017]
    # A site that lives ON the probe host cannot be probed from there: the
    # connection to its own public address never leaves the box, so the host
    # firewall is bypassed and every listening port reads as "open to the
    # internet" (the Vault hub's own dashboard port was reported that way).
    # Those targets are probed from here, the sensor, which really is outside.
    own = probe_host_addresses(h)
    local = {host: ip for host, ip in todo.items() if ip in own}
    for host, ip in local.items():
        todo.pop(host)
        out[host] = {"ip": ip, "open_ports": probe_ports_from_here(ip, ports), "ts": util.now_iso(), "probe_host": "sensor"}
    if not todo: return out
    script = ("import socket,sys,concurrent.futures as c,json\nT=json.loads(sys.argv[1]);P=%s\n"
              "def t(a):\n s=socket.socket();s.settimeout(2.0)\n try:\n  s.connect(a);return a\n except Exception:return None\n finally:s.close()\n"
              "r={}\nfor h,ip in T.items():\n r[h]=sorted(a[1] for a in c.ThreadPoolExecutor(20).map(t,[(ip,p) for p in P]) if a)\nprint(json.dumps(r))") % ports
    import base64
    b64 = base64.b64encode(script.encode()).decode(); arg = json.dumps(todo).replace("'", "")
    rc, o = util.ssh_run(h, f"echo {b64} | base64 -d | python3 - '{arg}'", timeout=120)
    try:
        r = json.loads(o.strip().splitlines()[-1])
        for host, pl in r.items(): out[host] = {"ip": todo[host], "open_ports": pl, "ts": util.now_iso(), "probe_host": name}
    except Exception as e:
        for host in todo: out[host] = {"ip": todo[host], "error": (o or str(e))[-120:], "ts": util.now_iso()}
    return out

def probe_host_addresses(h):
    """Every address the probe host answers to (its SSH target resolved)."""
    import socket
    target = (h or {}).get("target") or ""
    name = target.split("@", 1)[-1].split(":", 1)[0]
    if not name:
        return set()
    try:
        return {ai[4][0] for ai in socket.getaddrinfo(name, None)}
    except Exception:
        return set()


def probe_ports_from_here(ip, ports, timeout=2.0):
    """The same connect() sweep the probe host runs, run from the sensor."""
    import socket, concurrent.futures as cf

    def t(p):
        s = socket.socket(); s.settimeout(timeout)
        try:
            s.connect((ip, p)); return p
        except Exception:
            return None
        finally:
            s.close()
    with cf.ThreadPoolExecutor(20) as ex:
        return sorted(p for p in ex.map(t, ports) if p)

