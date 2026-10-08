"""Verizon Fios CR1000A router client — DERIVED, NOT PROVEN, and WIRED TO
NOTHING.

Reverse-engineered from the router's own SPA bundle (`/js/app.*.js`, chunks
5800/5929) on 2026-09-15. The login handshake below matches that JavaScript
exactly, but has NOT been executed against a live router: treat it as a
best-effort reconstruction until a real credential run confirms it.

Nothing in the sensor imports this module. It exists so the login handshake
and the "disable remote administration" payload can be reviewed and unit
tested before any decision is made about (a) where the router credential is
stored and entered and (b) whether Vault may run the mutation at all.
Building the credential path and wiring a tier-3, LAN-admin-only fix action
onto `disable_remote_admin` is deliberately left for that decision.

The handshake (from the bundle):
  * `ArcMD5(x)  = SHA512hex(MD5hex(x))`   (MD5 hex lowercased, then SHA-512 hex)
  * GET  loginStatus.cgi -> {token, loginToken (=salt), islogin, ...}
  * POST login.cgi (form):
        luci_username  = ArcMD5(username)
        luci_password  = SHA512hex(loginToken + ArcMD5(password))
        luci_view      = "Desktop"
        luci_token     = loginToken
        luci_keep_login= "0"
  * Config is a diff model: POST apply_abstract.cgi (form) with
        token          = formToken (the `token` from loginStatus.cgi)
        <changed key>  = <value> ...
    To disable remote administration the SPA sets these to "0":
        remote_ipv4_enable, remote_ipv6_enable,
        https_ipv4_enable,  https_ipv6_enable

The username is NOT optional on the router side. The SPA's login page has no
username field: `loginForm.username` is hard-coded to "admin" (chunk 5929)
and every login sends `luci_username=ArcMD5("admin")`. A blank username here
therefore means "admin" -- sending `ArcMD5("")` is exactly what made the
first live run 403 with a known-good password (2026-09-15).
"""

from __future__ import annotations

import hashlib
import re
import urllib.parse
import urllib.request

#: What the router's own login page sends when the owner never typed a
#: username, because its form has no such field.
DEFAULT_USERNAME = "admin"


def _md5_hex(s: str) -> str:
    return hashlib.md5(s.encode("utf-8")).hexdigest()


def _sha512_hex(s: str) -> str:
    return hashlib.sha512(s.encode("utf-8")).hexdigest()


def arcmd5(s: str) -> str:
    """The router's `ArcMD5`: SHA-512 hex of the (lowercase) MD5 hex."""
    return _sha512_hex(_md5_hex(s))


def login_password(password: str, login_token: str) -> str:
    """`luci_password`: SHA-512 hex of (salt + ArcMD5(password)).

    The bundle's `login_encode(pw, salt)` returns `ArcMD5(pw)` when the salt
    is empty; a real login always has a non-empty `loginToken`, but mirror
    the branch so the function is faithful to the source."""
    if login_token == "":
        return arcmd5(password)
    return _sha512_hex(login_token + arcmd5(password))


def login_fields(password: str, login_token: str, *, username: str = "",
                 view: str = "Desktop", keep_login: bool = False,
                 omit_empty_username: bool = False) -> dict:
    """The exact `login.cgi` POST body, as a plain dict (form-encode with
    `urllib.parse.urlencode`). A blank `username` becomes the gateway's own
    fixed default (`DEFAULT_USERNAME`); `omit_empty_username` keeps the old
    fallback of sending no username field at all."""
    body = {
        "luci_password": login_password(password, login_token),
        "luci_view": view,
        "luci_token": login_token,
        "luci_keep_login": "1" if keep_login else "0",
    }
    if not (omit_empty_username and username == ""):
        body["luci_username"] = arcmd5(username or DEFAULT_USERNAME)
    return body


#: The config keys the SPA writes to "0" to turn remote administration off.
REMOTE_ADMIN_OFF = {
    "remote_ipv4_enable": "0",
    "remote_ipv6_enable": "0",
    "https_ipv4_enable": "0",
    "https_ipv6_enable": "0",
}


def apply_body(form_token: str, changes: dict) -> dict:
    """The `apply_abstract.cgi` POST body: the form token plus the changed
    config rows (the SPA sends only the diff)."""
    return {"token": form_token, **changes}


def disable_remote_admin_body(form_token: str) -> dict:
    """The full `apply_abstract.cgi` body that turns remote administration
    off. Pure: builds the request, sends nothing."""
    return apply_body(form_token, REMOTE_ADMIN_OFF)


# --------------------------------------------------------------------------
# The network flow. Callable, but invoked by nothing in the sensor. A future
# tier-3, LAN-admin-only fix action would call `test_credential` for the
# card's "Test" and `disable_remote_admin` for the confirmed run.
# --------------------------------------------------------------------------

def _client(base_url: str, timeout: float):
    import ssl
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE   # the gateway serves a self-signed cert
    jar = urllib.request.HTTPCookieProcessor()
    opener = urllib.request.build_opener(jar, urllib.request.HTTPSHandler(context=ctx))
    base = base_url.rstrip("/")

    def get(path: str) -> str:
        with opener.open(base + "/" + path.lstrip("/"), timeout=timeout) as r:
            return r.read().decode("utf-8", "replace")

    def post(path: str, form: dict) -> tuple[int, str]:
        data = urllib.parse.urlencode(form).encode()
        req = urllib.request.Request(base + "/" + path.lstrip("/"), data=data,
                                     headers={"Content-Type": "application/x-www-form-urlencoded"})
        try:
            with opener.open(req, timeout=timeout) as r:
                return r.status, r.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as e:
            return e.code, e.read().decode("utf-8", "replace")

    return get, post


def _login_status(get) -> dict:
    import json
    try:
        return json.loads(get("loginStatus.cgi"))
    except Exception:
        return {}


def test_credential(base_url: str, password: str, *, username: str = "",
                    timeout: float = 10.0) -> dict:
    """Log in and log straight back out — proves the credential without
    changing anything. Returns {ok, error}. NO mutation."""
    try:
        get, post = _client(base_url, timeout)
        st = _login_status(get)
        token = st.get("loginToken") or st.get("token") or ""
        if not token:
            return {"ok": False, "error": "no login token from loginStatus.cgi"}
        code, _ = post("login.cgi", login_fields(password, token, username=username, keep_login=False))
        if code == 403:
            return {"ok": False, "error": "router rejected the credential (403)"}
        if code >= 400:
            return {"ok": False, "error": f"login.cgi returned {code}"}
        try:
            post("logout.cgi", {"token": st.get("token") or token})
        except Exception:
            pass
        return {"ok": True, "error": ""}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": str(e)[:200]}


#: The gateway's read-only tables. They are NOT fetched from `db.cgi` --
#: that was a wrong guess that cost two live round trips and answered 200
#: with an empty body every time. The SPA loads `/cgi/cgi_*.js`, which are
#: JavaScript files full of `addROD("<name>", {...});` calls that populate
#: its `readOnlyCGIs` store. Watching the real UI load its Devices page is
#: what showed this; it is not reconstructable from the static bundle.
#:
#: `cgi_owl.js` alone carries all seven tables, including the two that
#: matter: `dump_toplogy_station_info.stations` (per-station Wi-Fi) and
#: `dump_toplogy_map_info.nodes` (the router and its extenders).
ROD_SOURCES = ("cgi/cgi_owl.js",)

#: Where the CR1000A keeps its LAN/WAN settings, including DNS. Read in
#: addition to `ROD_SOURCES` when the DNS fields are wanted -- the station
#: table and the network config live in different cgi bundles, and fetching
#: both on every presence poll would double a request Vault makes every
#: five minutes for something it needs only when somebody presses a button.
DNS_ROD_SOURCES = ("cgi/cgi_lan.js", "cgi/cgi_wan.js", "cgi/cgi_owl.js")

#: How a DNS server field is recognised in the router's own config, without
#: knowing this firmware's exact spelling in advance.
#:
#: The field names are DISCOVERED rather than hard-coded on purpose. A
#: guessed name posts a change the router silently ignores -- the page then
#: says "done" and the customer's DNS never moved, which is the failure this
#: codebase keeps finding: not a wrong value, a thing that quietly did not
#: happen. If nothing matches, `read_dns` says so and `set_dns` refuses.
_DNS_KEY = re.compile(r"(?:^|_)dns(?:_?(?:server|addr|ip))?_?(?:[12]|pri|sec|primary|secondary)?$",
                      re.I)
_IPV4 = re.compile(r"^\d{1,3}(?:\.\d{1,3}){3}$")


def find_dns_fields(rod: dict) -> dict:
    """{field name: current value} for every DNS server field in the config.

    Walks the whole ROD tree rather than a known path: the same firmware
    ships the fields under `lan`, `wan` or flat at the top depending on
    build, and a path that is right today is a silent no-op after an
    update.
    """
    found: dict[str, str] = {}

    def walk(node):
        if isinstance(node, dict):
            for k, v in node.items():
                if isinstance(v, (dict, list)):
                    walk(v)
                elif _DNS_KEY.search(str(k)) and isinstance(v, str):
                    # Only fields that hold an address, or are empty and
                    # therefore writable. A "dns_mode"/"dns_enable" flag
                    # matches the name pattern and must not be overwritten
                    # with an IP.
                    if v == "" or _IPV4.match(v.strip()):
                        found[str(k)] = v.strip()
        elif isinstance(node, list):
            for v in node:
                walk(v)

    walk(rod)
    return found


def parse_rod(text: str) -> dict:
    """Pull `addROD("name", <json>)` payloads out of a cgi_*.js response.

    A brace-counting scan rather than a regex, because the payloads are
    ~100 KB of nested JSON and contain braces inside strings. String and
    escape state are tracked so a `}` inside a device name cannot end the
    object early.
    """
    import json as _json
    out: dict = {}
    for m in re.finditer(r'addROD\(\s*"([^"]+)"\s*,\s*', text or ""):
        i = m.end()
        depth, in_str, esc = 0, False, False
        while i < len(text):
            c = text[i]
            if in_str:
                if esc:
                    esc = False
                elif c == "\\":
                    esc = True
                elif c == '"':
                    in_str = False
            elif c == '"':
                in_str = True
            elif c in "{[":
                depth += 1
            elif c in "}]":
                depth -= 1
                if depth == 0:
                    i += 1
                    break
            i += 1
        try:
            out[m.group(1)] = _json.loads(text[m.end():i])
        except ValueError:
            continue
    return out


def station_info(base_url: str, password: str, *, username: str = "",
                 timeout: float = 25.0) -> dict:
    """Read the gateway's per-station Wi-Fi table. Login, read, log out.

    READ-ONLY: it fetches `cgi_owl.js` and never posts to
    `apply_abstract.cgi`, so nothing on the router changes. This is the only
    source of signal strength and association uptime -- Vault's own
    presence detection is a ping sweep plus ARP, and a device in power save
    ignores pings and looks offline while still perfectly associated.
    """
    try:
        get, post = _client(base_url, timeout)
        st = _login_status(get)
        login_token = st.get("loginToken") or ""
        if not login_token:
            return {"ok": False, "stations": [], "nodes": [], "error":
                    "no login token from loginStatus.cgi"}
        code, body = post("login.cgi", login_fields(password, login_token, username=username))
        if code >= 400:
            return {"ok": False, "stations": [], "nodes": [],
                    "error": f"login failed ({code})"}
        rod: dict = {}
        for path in ROD_SOURCES:
            try:
                rod.update(parse_rod(get(path)))
            except Exception as e:  # noqa: BLE001
                return {"ok": False, "stations": [], "nodes": [],
                        "error": f"{path}: {str(e)[:120]}"}
        try:
            post("logout.cgi", {"token": (_login_status(get).get("token") or login_token)})
        except Exception:
            pass
        stations = ((rod.get("dump_toplogy_station_info") or {}).get("stations")) or []
        nodes = ((rod.get("dump_toplogy_map_info") or {}).get("nodes")) or []
        if not stations:
            return {"ok": False, "stations": [], "nodes": nodes, "tables": sorted(rod),
                    "error": "no station table in the gateway's response"}
        return {"ok": True, "error": "", "stations": stations, "nodes": nodes,
                "tables": sorted(rod)}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "stations": [], "nodes": [], "error": str(e)[:200]}


def _login(get, post, password: str, username: str):
    """Sign in and hand back the form token. Raises nothing; returns "" on
    failure so callers can report rather than crash."""
    st = _login_status(get)
    login_token = st.get("loginToken") or ""
    if not login_token:
        return "", "no login token from loginStatus.cgi"
    code, body = post("login.cgi", login_fields(password, login_token, username=username))
    if code >= 400:
        return "", f"login failed ({code})"
    # Some builds rotate the token on login; re-read rather than reuse.
    return (_login_status(get).get("token") or login_token), ""


def read_dns(base_url: str, password: str, *, username: str = "",
             timeout: float = 20.0) -> dict:
    """What DNS servers the router is currently handing out. READ-ONLY.

    This is what lets the setup page stop guessing. Vault can only
    filter a query that reaches it, so "is the router pointed at us?" is
    the single fact that decides whether DNS filtering works at all -- and
    until now the page asked the customer to go and look.
    """
    try:
        get, post = _client(base_url, timeout)
        token, err = _login(get, post, password, username)
        if err:
            return {"ok": False, "fields": {}, "servers": [], "error": err}
        rod: dict = {}
        for path in DNS_ROD_SOURCES:
            try:
                rod.update(parse_rod(get(path)))
            except Exception:
                # A bundle this firmware does not ship is not an error --
                # the fields may well be in one of the others.
                continue
        try:
            post("logout.cgi", {"token": token})
        except Exception:
            pass
        fields = find_dns_fields(rod)
        if not fields:
            return {"ok": False, "fields": {}, "servers": [], "tables": sorted(rod),
                    "error": "no DNS fields found in this router's config"}
        servers = [v for v in fields.values() if v]
        return {"ok": True, "error": "", "fields": fields, "servers": servers,
                "tables": sorted(rod)}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "fields": {}, "servers": [], "error": str(e)[:200]}


def set_dns(base_url: str, password: str, server: str, *, username: str = "",
            timeout: float = 25.0, both_boxes: bool = False) -> dict:
    """Point the router's DNS at `server`. MUTATING.

    Miro's call, and narrower than it looks: the ONE field this writes is
    the DNS server. It reads the config first, writes only the field names
    that config actually contained, and returns `previous` so the change
    can be put back exactly as it was.

    **One server, not two.** A second entry is a way around Vault --
    devices take whichever answers first, so half the queries would skip
    the filter and the logs would quietly be half the truth. Secondary
    fields are therefore CLEARED, not filled. `both_boxes` exists only for
    routers that refuse a single entry, and then writes the same address
    twice rather than somebody else's.

    Never called on a schedule and never from a scan: a person presses a
    button, the same rule the firewall proposals follow.
    """
    if not _IPV4.match((server or "").strip()):
        return {"ok": False, "previous": {}, "error": f"not an IPv4 address: {server!r}"}
    server = server.strip()
    try:
        get, post = _client(base_url, timeout)
        token, err = _login(get, post, password, username)
        if err:
            return {"ok": False, "previous": {}, "error": err}
        rod: dict = {}
        for path in DNS_ROD_SOURCES:
            try:
                rod.update(parse_rod(get(path)))
            except Exception:
                continue
        fields = find_dns_fields(rod)
        if not fields:
            # Refuses rather than posting a guessed field name: a change the
            # router silently ignores would leave the page saying "done"
            # while the customer's DNS never moved.
            return {"ok": False, "previous": {}, "tables": sorted(rod),
                    "error": "no DNS fields found in this router's config, so nothing was changed"}
        changes = {}
        for i, name in enumerate(sorted(fields)):
            changes[name] = server if (i == 0 or both_boxes) else ""
        code, body = post("apply_abstract.cgi", apply_body(token, changes))
        if code >= 400:
            return {"ok": False, "previous": fields, "error": f"apply failed ({code})",
                    "out": body[:300]}
        return {"ok": True, "error": "", "previous": fields, "applied": changes,
                "out": f"router DNS set to {server}"}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "previous": {}, "error": str(e)[:200]}


def restore_dns(base_url: str, password: str, previous: dict, *, username: str = "",
                timeout: float = 25.0) -> dict:
    """Put back exactly what `set_dns` found. The undo half of the pair --
    written at the same time as the change, not left for later."""
    if not previous:
        return {"ok": False, "error": "nothing recorded to restore"}
    try:
        get, post = _client(base_url, timeout)
        token, err = _login(get, post, password, username)
        if err:
            return {"ok": False, "error": err}
        code, body = post("apply_abstract.cgi", apply_body(token, dict(previous)))
        if code >= 400:
            return {"ok": False, "error": f"apply failed ({code})", "out": body[:300]}
        return {"ok": True, "error": "", "out": "router DNS restored"}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": str(e)[:200]}


def client_bytes(base_url: str, password: str, **kw) -> list[dict]:
    """Per-client rx/tx byte counters, as `[{"mac", "ip", "rx", "tx"}, ...]`.

    Always returns `[]`. The only gateway page this module knows how to read
    is `cgi/cgi_owl.js` (see `ROD_SOURCES` / `station_info`), and its two
    tables -- `dump_toplogy_station_info` (per-station Wi-Fi: signal,
    association time, link rate) and `dump_toplogy_map_info` (the router and
    its extenders) -- carry no byte counter of any kind, as `read_signal`'s
    field list above shows. Nothing else was ever fetched from this gateway:
    the module's own docstring warns that `db.cgi`, the obvious guess for a
    traffic-stats endpoint, was tried and answered 200 with an empty body
    every time. Claiming router-sourced flow rows out of a table that has no
    bytes in it would be worse than reporting none: `runner.run_once` reads
    this list to decide whether capture mode can honestly become "router",
    and an empty list is exactly the signal that it cannot -- mode stays
    "none" rather than a fabricated one. If a future firmware or model does
    expose a bandwidth table, this is where it gets parsed, the same
    login/read/logout shape as `station_info`.
    """
    return []


def read_signal(stations: list, mac: str) -> dict:
    """One station's Wi-Fi facts, normalised, or `{}` when the gateway has no
    entry -- which is itself the answer: it is not associated at all.

    `assoc_time` is the field that settles "is it really dropping off". A
    device with a healthy `signal_strength` and a long `assoc_time` never
    left the Wi-Fi, whatever Vault's ping sweep concluded.
    """
    want = (mac or "").replace("-", ":").lower()
    for s in stations:
        if not isinstance(s, dict):
            continue
        if str(s.get("station_mac") or "").replace("-", ":").lower() != want:
            continue
        def _int(v):
            try:
                return int(str(v).strip())
            except (TypeError, ValueError):
                return None
        return {
            "mac": want,
            "name": s.get("station_name") or s.get("alias_name") or "",
            "ip": s.get("station_ip") or "",
            "rssi": _int(s.get("signal_strength")),
            "rssi_min": _int(s.get("signal_strength_min")),
            "rssi_max": _int(s.get("signal_strength_max")),
            "snr": _int(s.get("snr")),
            "band": s.get("connect_type") or "",
            "channel": s.get("channel") or "",
            "link_rate": s.get("link_rate") or "",
            "link_rate_max": s.get("link_rate_max") or "",
            # Seconds the station has been continuously associated.
            "assoc_time": _int(s.get("assoc_time")),
            # Which access point it is on -- the router itself or an extender.
            "parent_mac": s.get("parent_mac") or s.get("parent_id") or "",
            "bssid": s.get("connect_bssid") or "",
            "online": s.get("online"),
        }
    return {}


def explain_presence(sig: dict, *, weak_dbm: int = -70) -> str:
    """What the router's numbers mean for a device Vault thinks is
    flapping or offline. Plain sentence, no jargon, and it is allowed to say
    that Vault was wrong -- which is the common case."""
    if not sig:
        return ("The gateway has no Wi-Fi entry for this device, so it is not "
                "associated: it really is off the network.")
    rssi, assoc = sig.get("rssi"), sig.get("assoc_time") or 0
    where = " through an extender" if sig.get("parent_mac") and sig.get("parent_mac") != sig.get("bssid") else ""
    band = f" on {sig['band']}" if sig.get("band") else ""
    if rssi is not None and rssi <= weak_dbm:
        return (f"Weak signal: {rssi} dBm{band}{where}. That is low enough to drop "
                f"packets and re-associate; move it closer to an access point.")
    if assoc >= 3600:
        hrs = assoc // 3600
        return (f"It never left the Wi-Fi: associated for {hrs}h at {rssi} dBm{band}"
                f"{where}, {sig.get('link_rate') or 'unknown rate'}. Vault sees gaps "
                f"because the device sleeps and stops answering pings, not because the "
                f"connection drops.")
    return (f"Signal is {rssi} dBm{band}{where} and it has been associated for "
            f"{assoc}s. Nothing here points at a weak signal.")


def disable_remote_admin(base_url: str, password: str, *, username: str = "",
                         timeout: float = 15.0) -> dict:
    """Log in and turn remote administration off. Returns {ok, out, error}.

    Mutating and outward-facing: a caller must gate it behind a real tier-3
    confirmation and the LAN-admin check, exactly like every other fix. It
    is called by nothing today."""
    try:
        get, post = _client(base_url, timeout)
        st = _login_status(get)
        login_token = st.get("loginToken") or ""
        form_token = st.get("token") or login_token
        if not login_token:
            return {"ok": False, "out": "", "error": "no login token from loginStatus.cgi"}
        code, body = post("login.cgi", login_fields(password, login_token, username=username))
        if code >= 400:
            return {"ok": False, "out": body[:300], "error": f"login failed ({code})"}
        # re-read the token: some builds rotate it on login
        st2 = _login_status(get)
        form_token = st2.get("token") or form_token
        code2, body2 = post("apply_abstract.cgi", disable_remote_admin_body(form_token))
        if code2 >= 400:
            return {"ok": False, "out": body2[:300], "error": f"apply failed ({code2})"}
        return {"ok": True, "out": "remote administration disabled (remote_ipv4/ipv6_enable=0, https_ipv4/ipv6_enable=0)", "error": ""}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "out": "", "error": str(e)[:200]}
