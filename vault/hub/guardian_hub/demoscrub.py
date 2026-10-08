"""Turn the captured sensor fixtures into a plausible *fictional* network
before `seed.seed_demo` ingests them, so the public `demo` tenant never
shows anyone the owner's real infrastructure.

**Why this exists.** The demo tenant's sign-in is deliberately public
(`demo` / `demoadmin`, fixed code, advertised on guarder.io) and it is
seeded from `hub/tests/fixtures/{last,state}.json` -- a real capture of the
owner's own network. Without this module, anyone who signs in as `demo` and
opens Assets, Topology, Security or DNS is handed production IP addresses,
real project hostnames, real device labels carrying people's first names,
and the addresses of people who actually attacked those servers. Handing a
prospect the owner's production addresses is the opposite of what a
security product should demonstrate.

**Why scrub at load rather than sanitise the fixture.** Two reasons.

1. It is future-proof. `scrub` is a *transform*, so the next time somebody
   re-captures `last.json` off a live sensor (which is how it was produced,
   and how it will be refreshed) the new capture is scrubbed too. A
   one-time sanitise of the file protects only today's values.
2. It keeps the fixture as a fixture. The hub-side tests read the same
   files directly (`tests/test_ingest.py`, `tests/test_report_builder.py`,
   ...) as a realistic sensor payload; rewriting them in place would
   re-baseline a lot of unrelated assertions for no safety gain.

The sensor's parity tests (`sensor/tests/test_parity_alerts.py`,
`test_runner.py`, `test_fix.py`) resolve their fixtures from
`sensor/tests/fixtures/` via `__file__` -- a *separate* copy of these files
that has already diverged from the hub's (the hub copy says "Vault
public", the sensor copy still says "Nexus public"). Nothing in `sensor/`
reads `hub/tests/fixtures`, and nothing in `hub/` reads
`sensor/tests/fixtures`, so this module cannot reach the parity baseline
either way.

**Design: rules first, pins for polish.** Every value class is handled by a
*rule* that cannot let an unrecognised real value through:

- **IPv4** -- loopback/private/link-local/documentation addresses are kept
  (a LAN address is already fictional enough, and keeping it preserves
  subnet membership); a short allow-list of public DNS resolvers is kept
  because they are nobody's private infrastructure; **every other public
  address is hashed into the RFC 5737 documentation ranges**. There is no
  pass-through branch for a public address.
- **Domains** -- a registrable domain in `ALLOWED_DOMAINS` (well-known
  destinations any network's DNS log shows) is kept; everything else
  becomes a Northwind Dental name under a `.example` TLD, which by RFC 6761
  can never resolve for anyone.
- **MACs** -- the OUI (vendor prefix) is kept, the low three octets are
  hashed, so vendor bars and "which vendor is this" still read correctly
  while the address is no longer the owner's hardware.
- **Free-text identifiers** (site display names, device names/labels, ssh
  host names, container images, heartbeat names, login users, the SSID) --
  an allow-list of generic class labels is kept, a small `_PINS` table
  gives the fixture's own values pretty fictional replacements so the shop
  window reads well, and anything else falls through to a derived
  `<class>-<hash>` form.

Everything is a pure function of the input strings (`blake2b`, no clock, no
RNG, no `set` iteration), so `seed.refresh_demo` -- which re-seeds hourly in
production -- produces the same demo network every time.
"""

import hashlib
import ipaddress
import re

# --------------------------------------------------------------------------
# the fictional business
# --------------------------------------------------------------------------

#: The demo tenant's name. `seed.seed_demo` forces this onto the tenant row.
BRAND = "Northwind Dental"

#: Its two domains. `.example` is reserved by RFC 6761 and has no registry,
#: so neither can ever resolve to a real party.
DOMAIN = "northwind-dental.example"
DOMAIN2 = "northwind-clinics.example"

#: What an unpinned, unallowed registrable domain becomes: still a
#: `.example` name, still deterministic, still obviously not real.
_FALLBACK_DOMAIN_FMT = "nw-{h}.example"

# --------------------------------------------------------------------------
# rule inputs
# --------------------------------------------------------------------------

#: RFC 5737 documentation ranges -- the only public-shaped addresses the demo
#: tenant is allowed to contain. `tests/test_demo_scrub.py` asserts this.
DOC_NETS = tuple(ipaddress.ip_network(c) for c in ("192.0.2.0/24", "198.51.100.0/24", "203.0.113.0/24"))

#: Public resolvers kept verbatim: they are global infrastructure, not the
#: owner's, and the WAN-path / DNS panels are unreadable if "cloudflare" is
#: labelled with an address that is not Cloudflare's. The scrub test allows
#: exactly this set, so the two can never drift.
ALLOWED_PUBLIC_IPS = frozenset({
    "1.1.1.1", "1.0.0.1", "8.8.8.8", "8.8.4.4", "9.9.9.9", "149.112.112.112",
})

#: Registrable domains kept verbatim. Two groups: destinations that appear in
#: any network's DNS log (so the DNS tab reads like a real one), and the
#: hub's own reserved/fictional names.
ALLOWED_DOMAINS = frozenset({
    # what a DNS filter actually sees
    "google.com", "googleapis.com", "googlesyndication.com", "doubleclick.net",
    "apple.com", "icloud.com", "amazon.com", "netflix.com", "github.com",
    "cloudflare.com", "microsoft.com", "windowsupdate.com", "0following.com",
    # the synthetic block-list victims seed._gen_dns invents
    "malicious-tracker.ru", "phishy-login.top", "urlhaus-host.xyz",
    # reserved-by-RFC names, plus hosts the hub itself puts in a payload:
    # the demo Slack row's webhook and the CVE tracker `payloads/updates`
    # links every CVE row to. Neither is tenant data.
    "example.com", "example.net", "example.org", "invalid", "example",
    "guardian.invalid", "slack.com", "debian.org", "ubuntu.com",
    # our own
    DOMAIN, DOMAIN2,
})

#: Container images left alone: base images say nothing about whose network
#: this is. Anything else is renamed (the fixture's real ones name the
#: owner's projects).
ALLOWED_IMAGES = frozenset({
    "python", "nginx", "redis", "postgres", "mariadb", "mysql", "node",
    "alpine", "debian", "ubuntu", "traefik", "caddy", "grafana", "prometheus",
    "busybox", "httpd", "mongo", "rabbitmq", "memcached",
})

#: Unix account names left alone.
ALLOWED_LOGIN_USERS = frozenset({
    "root", "admin", "ubuntu", "debian", "deploy", "www-data", "backup", "ops",
})

#: ssh host names generic enough to keep as-is. Anything else is renamed --
#: but only when it is at least `_MIN_TOKEN` long, because a literal
#: replacement of a 3-4 character token would corrupt unrelated prose.
GENERIC_HOST_NAMES = frozenset({
    "prod", "demo", "test", "dev", "stage", "web", "db", "app", "lab",
    "nas", "vpn", "mail", "edge", "ci",
})

#: Device labels that describe a *class* of thing rather than a person, a
#: place in the owner's home, or one of his machines. Kept so `_classify`
#: (duty cycles), `payloads/assets` grouping and `public_feed._device_class`
#: keep working on real vocabulary.
SAFE_DEVICE_LABELS = frozenset({
    "iphone", "ipad", "macbook", "smart tv", "amazon echo", "amazon device",
    "samsung device", "samsung range (oven)", "reolink camera", "canon printer",
    "streaming box (fire tv?)", "fire tv", "wi-fi extender", "lab switch",
    "windows pc", "printer", "camera", "router", "tv", "echo",
})

#: Shortest free-text token this module will register as a literal
#: replacement. Literals are matched with an alphanumeric guard on both
#: sides (see `_Table`), so a short token like `weme` cannot land inside a
#: longer word -- but a one- or two-character token is still too generic to
#: rewrite safely.
_MIN_TOKEN = 3

#: Last labels that mean "this is a filename, not a hostname", so
#: `sec:panel:...:/wp-login.php` is not read as a domain to rewrite.
FILE_SUFFIXES = frozenset({
    "php", "html", "htm", "asp", "aspx", "jsp", "cgi", "json", "xml", "txt",
    "csv", "pdf", "log", "md", "yml", "yaml", "sh", "py", "js", "css", "png",
    "svg", "jpg", "gz", "zip", "whl", "sha256", "db", "sqlite", "env", "conf",
    "ini", "sql", "bak", "tmp", "old", "local", "internal", "lan",
})

# --------------------------------------------------------------------------
# pins: the fixture's own values, given pretty fictional replacements
# --------------------------------------------------------------------------
# These exist only so the shop window reads like a dental practice instead
# of `device-3f21`. Every one of them would be handled by the rules above if
# it were missing -- see `_derive_*`. Never add a pin that *keeps* a real
# value; a pin is a nicer replacement, never an exemption.

#: registrable domain -> replacement
_PIN_DOMAINS = {
    "example.org": DOMAIN,
    "example.com": DOMAIN2,
    "mynetworksettings.com": DOMAIN,
}

#: full hostname -> replacement (wins over `_PIN_DOMAINS`)
_PIN_HOSTS = {
    "status.example.org": f"portal.{DOMAIN}",
    "pulsedemo.example.com": f"booking.{DOMAIN2}",
    "flycheap.example.com": f"pay.{DOMAIN2}",
    "safehome.example.com": f"intranet.{DOMAIN2}",
    "cr1000a.mynetworksettings.com": f"gateway.{DOMAIN}",
}

#: site display name -> replacement. "Nexus public (...)" is the pre-rename
#: form the live demo tenant was imported with; it is pinned so no
#: user-facing copy can carry the old product name.
_PIN_SITE_NAMES = {
    "Roots prod (status.example.org)": f"Patient portal (portal.{DOMAIN})",
    "Demo (pulsedemo.example.com)": f"Online booking (booking.{DOMAIN2})",
    "FlyCheap": "Payments",
    # Three spellings of one site, because these are LOOKUP KEYS matched
    # against a real capture -- `tests/fixtures/last.json` still records it
    # as "Guardian public", and a capture is history that does not get
    # rewritten when the product is renamed. Drop a spelling and the scrub
    # silently falls through to the generic path, which collided with
    # "Online booking" and left two sites of that name on the public demo.
    "Vault public (safehome.example.com)": f"Staff intranet (intranet.{DOMAIN2})",
    "Guardian public (safehome.example.com)": f"Staff intranet (intranet.{DOMAIN2})",
    "Nexus public (safehome.example.com)": f"Staff intranet (intranet.{DOMAIN2})",
}

#: ssh host name / reported hostname -> replacement. `prod`/`demo` are in
#: `GENERIC_HOST_NAMES` and so would survive the rule untouched -- they are
#: pinned anyway, because a dental practice's servers should read like a
#: dental practice's servers and because "demo" as a *host* name inside the
#: demo tenant is needlessly confusing.
_PIN_HOST_NAMES = {
    "prod": "records",
    "demo": "booking",
    "safehome": "clinic-nas",
    "n8n-workflow-test": "records-01",
}

#: device name or label -> replacement, keeping the device's class word so
#: duty cycles (`seed._classify`) and device classification still work
_PIN_DEVICE_NAMES = {
    "ce1000a-703e76289bac": "ap-reception",
    "ce1000a-703e76287af4": "ap-surgery",
    "frontdesk-pc": "frontdesk-pc",
    "echo-waiting-room": "echo-waiting-room",
    "Reception’s MacBook Air (airplay)": "Reception MacBook Air (airplay)",
    "miro (airplay)": "practice-mac (airplay)",
    "Miro Mac": "Practice MacBook",
    "weme": "surgery-cam",
    "new-host-4": "printer-reception",
    "staff-phone": "staff-phone",
    "Phone (staff-phone)": "Phone (staff)",
    "FireTV bbc13b3c2bdcf6b9 (homekit)": "FireTV waiting-room (homekit)",
    "waiting's 6th Fire TV (airplay)": "Waiting room Fire TV (airplay)",
    "Fire TV (Waiting)": "Fire TV (waiting room)",
    "Smart-TV-dc002e36b00d2756946def8bd4390d24 (cast)": "Smart-TV-surgery-2 (cast)",
    "bedroomtv": "breakroom-tv",
    "Bedroom TV": "Break room TV",
    "Router (Fios CR1000A)": "Router (clinic gateway)",
    "Fios extender": "Wi-Fi extender",
    "Windows PC (frontdesk-pc)": "Windows PC (frontdesk-pc)",
    "Canon printer (MF8200C)": "Canon printer (reception)",
}

#: container/image name -> replacement
_PIN_IMAGES = {
    "standup-bot-bot": "pms-app",
    "standup-logic-runner": "pms-worker",
    "guardian-tier2-sandbox": "backup-agent",
    "standup-test": "pms-test",
    "standup-bot": "pms-app",
}

#: heartbeat name -> replacement
_PIN_HEARTBEATS = {"roots-daily-report": "nightly-backup"}

#: login user -> replacement
_PIN_LOGIN_USERS = {"gitlab-runner": "deploy"}

#: The SSID. `seed.DEMO_NETWORKS` carries the same string, so the two must
#: be changed together (there is a test that pins that).
SSID = "Northwind-Staff"
_PIN_SSIDS = {"HomeWiFi": SSID}

# --------------------------------------------------------------------------
# derivation pools (the rule half)
# --------------------------------------------------------------------------

_SUBDOMAIN_POOL = ("portal", "booking", "intranet", "records", "pay", "files",
                   "vpn", "mail", "backup", "imaging", "xray", "billing",
                   "scheduler", "kiosk", "reports", "assets")
_SITE_LABEL_POOL = ("Patient portal", "Online booking", "Staff intranet", "Payments",
                    "Records system", "Imaging viewer", "Referrals", "Billing")
_HOST_NAME_POOL = ("records-01", "booking-01", "clinic-nas", "imaging-01",
                   "backup-01", "frontdesk-01", "surgery-01")
_PLACE_POOL = ("reception", "waiting-room", "surgery-1", "surgery-2",
               "break-room", "lab", "office", "corridor")

#: (keyword to look for, the word to keep) for a derived device name. Ordered
#: like `public_feed._DEVICE_CLASSES`: the specific entries come first.
_DEVICE_KEYWORDS = (
    ("iphone", "iphone"), ("ipad", "ipad"), ("android", "phone"),
    ("macbook", "macbook"), ("laptop", "laptop"), ("printer", "printer"),
    ("camera", "camera"), ("echo", "echo"), ("speaker", "speaker"),
    ("fire tv", "fire-tv"), ("firetv", "fire-tv"), ("smart tv", "tv"),
    ("tv", "tv"), ("router", "router"), ("extender", "extender"),
    ("switch", "switch"), ("console", "console"), ("range", "range"),
    ("oven", "oven"), ("thermostat", "thermostat"), ("watch", "watch"),
    ("phone", "phone"), ("mac", "mac"),
)

#: Plausible attacker origins for the map. Assigned by hash of the *mapped*
#: address, so the same demo attacker keeps the same country across refreshes.
#: (country, code, city, lat, lon)
_GEO_POOL = (
    ("Netherlands", "NL", "Amsterdam", 52.374, 4.8897),
    ("Türkiye", "TR", "Istanbul", 41.0082, 28.9784),
    ("Viet Nam", "VN", "Hanoi", 21.0278, 105.8342),
    ("Brazil", "BR", "São Paulo", -23.5505, -46.6333),
    ("Russia", "RU", "Moscow", 55.7558, 37.6173),
    ("India", "IN", "Mumbai", 19.076, 72.8777),
    ("China", "CN", "Shanghai", 31.2304, 121.4737),
    ("Germany", "DE", "Frankfurt", 50.1109, 8.6821),
)


# --------------------------------------------------------------------------
# value-class patterns
# --------------------------------------------------------------------------

_RE_MAC = r"(?:[0-9a-fA-F]{2}:){5}[0-9a-fA-F]{2}"
_RE_IP = r"(?:\d{1,3}\.){3}\d{1,3}"
_RE_DOMAIN = r"(?:[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?\.)+[A-Za-z]{2,24}"


# --------------------------------------------------------------------------
# deterministic hashing
# --------------------------------------------------------------------------

def _h(*parts: str, n: int = 8) -> int:
    """A stable integer from `parts`. `blake2b`, not `hash()` -- Python's
    string hash is salted per process, which would make the demo network
    different on every `refresh_demo` run."""
    digest = hashlib.blake2b("\x00".join(parts).encode("utf-8"), digest_size=n).digest()
    return int.from_bytes(digest, "big")


def _pick(pool: tuple, *parts: str):
    return pool[_h(*parts) % len(pool)]


# --------------------------------------------------------------------------
# rules: IPv4
# --------------------------------------------------------------------------

def is_doc_ip(text: str) -> bool:
    try:
        addr = ipaddress.ip_address(text)
    except ValueError:
        return False
    return any(addr in net for net in DOC_NETS)


def ip_is_allowed(text: str) -> bool:
    """True when `text` may appear in demo-tenant data as-is: not an address
    at all, a documentation address, one of the well-known resolvers, or an
    address that identifies nobody (loopback, RFC1918, link-local, ...).

    Shared with `tests/test_demo_scrub.py`, which is the whole point: the
    sweep and the scrub agree on one definition of "safe" by construction.
    """
    try:
        addr = ipaddress.ip_address(text)
    except ValueError:
        return True
    if text in ALLOWED_PUBLIC_IPS or any(addr in net for net in DOC_NETS):
        return True
    return bool(addr.is_private or addr.is_loopback or addr.is_link_local
                or addr.is_multicast or addr.is_unspecified or addr.is_reserved)


def map_ip(text: str) -> str:
    """Any public address that is not explicitly allowed becomes a
    documentation address. Deterministic and pure, so the same real address
    always maps to the same demo one -- which is what keeps a device's
    address the same everywhere it appears."""
    if ip_is_allowed(text):
        return text
    value = _h("ip", text)
    net = DOC_NETS[value % len(DOC_NETS)]
    host = 1 + (value >> 8) % 254
    return str(net.network_address + host)


# --------------------------------------------------------------------------
# rules: MAC
# --------------------------------------------------------------------------

def map_mac(text: str) -> str:
    """Keep the OUI, hash the device half. Vendor lookups and the vendor
    bars still read correctly; the address is no longer real hardware."""
    parts = text.lower().split(":")
    if len(parts) != 6:
        return text
    value = _h("mac", text.lower())
    tail = [(value >> (8 * i)) & 0xFF for i in range(3)]
    return ":".join(parts[:3] + [f"{b:02x}" for b in tail])


# --------------------------------------------------------------------------
# rules: domains and hostnames
# --------------------------------------------------------------------------

def _registrable(host: str) -> str:
    labels = host.lower().rstrip(".").split(".")
    return ".".join(labels[-2:]) if len(labels) >= 2 else host.lower()


def domain_is_allowed(host: str) -> bool:
    """True when the registrable domain of `host` may appear as-is. Shared
    with the scrub test, same reason as `ip_is_allowed`."""
    low = host.lower().rstrip(".")
    if low in ALLOWED_DOMAINS or _registrable(low) in ALLOWED_DOMAINS:
        return True
    if low.rsplit(".", 1)[-1] in FILE_SUFFIXES:
        return True
    # any *.example name is reserved and cannot resolve for anybody
    return low.endswith(".example") or low == "example"


def map_host(host: str) -> str:
    """A hostname or bare domain -> a Northwind name. Pins first (prettier),
    then the rule: the registrable half becomes one of our `.example`
    domains, every label to its left becomes a plausible clinic subdomain
    drawn from a fixed pool by hash."""
    low = host.lower().rstrip(".")
    if domain_is_allowed(low):
        return host
    if low in _PIN_HOSTS:
        return _PIN_HOSTS[low]

    reg = _registrable(low)
    new_reg = _PIN_DOMAINS.get(reg) or _FALLBACK_DOMAIN_FMT.format(h=f"{_h('dom', reg) % 0x10000:04x}")
    labels = low.split(".")
    if len(labels) <= 2:
        return new_reg
    prefix = ".".join(labels[:-2])
    return f"{_pick(_SUBDOMAIN_POOL, 'sub', prefix)}.{new_reg}"


# --------------------------------------------------------------------------
# rules: free text
# --------------------------------------------------------------------------

def _derive_device_name(text: str) -> str:
    low = text.lower()
    for needle, keep in _DEVICE_KEYWORDS:
        if needle in low:
            return f"{keep}-{_pick(_PLACE_POOL, 'dev', low)}"
    return f"device-{_h('dev', low) % 0x10000:04x}"


def map_device_name(text: str) -> str:
    if not text:
        return text
    if text in _PIN_DEVICE_NAMES:
        return _PIN_DEVICE_NAMES[text]
    if text.lower() in SAFE_DEVICE_LABELS:
        return text
    # A DHCP/mDNS name is often a fully-qualified hostname (the fixture's
    # router announces itself as `cr1000a.mynetworksettings.com`). Route it
    # through the hostname rule so it stays a hostname on the other side
    # instead of collapsing to `device-<hash>`.
    if re.fullmatch(_RE_DOMAIN, text):
        return map_host(text)
    return _derive_device_name(text)


def map_host_name(text: str) -> str:
    """An ssh host name (`ssh[].name`) or a reported `hostname`."""
    if not text:
        return text
    if text in _PIN_HOST_NAMES:
        return _PIN_HOST_NAMES[text]
    if text.lower() in GENERIC_HOST_NAMES:
        return text
    if "." in text:
        return map_host(text)
    return _pick(_HOST_NAME_POOL, "host", text.lower())


def map_site_name(text: str) -> str:
    """A site's display name. The fixture's shape is
    `"Roots prod (status.example.org)"`; the bracketed half is a hostname, so it
    goes through `map_host` and the label half through the pool."""
    if not text:
        return text
    if text in _PIN_SITE_NAMES:
        return _PIN_SITE_NAMES[text]
    label, sep, rest = text.partition(" (")
    new_label = _pick(_SITE_LABEL_POOL, "site", label.lower())
    if not sep:
        return new_label
    host = rest.rstrip(")")
    return f"{new_label} ({map_host(host)})" if host else new_label


def map_image(text: str) -> str:
    """A container image or running-container name (`nginx:latest`,
    `standup-bot:Up 4 hours`) -- only the name half is rewritten."""
    if not text:
        return text
    if text in _PIN_IMAGES:
        return _PIN_IMAGES[text]
    if text.split("/")[0].split(":")[0] in ALLOWED_IMAGES:
        return text
    return f"clinic-app-{_h('img', text) % 0x100:02x}"


def map_login_user(text: str) -> str:
    if not text or text.lower() in ALLOWED_LOGIN_USERS:
        return text
    return _PIN_LOGIN_USERS.get(text) or f"user{_h('usr', text) % 0x100:02x}"


def map_heartbeat(text: str) -> str:
    if not text:
        return text
    return _PIN_HEARTBEATS.get(text) or f"job-{_h('hb', text) % 0x10000:04x}"


def demo_geo(ip: str) -> dict:
    """Plausible geo for a scrubbed attacker address, so the Security tab's
    attacker map still has pins. Needed because the documentation ranges are
    `is_private` as far as `ipaddress` is concerned, so `geoip.lookup` will
    never call a provider for one -- the hub has to supply the answer."""
    country, code, city, lat, lon = _pick(_GEO_POOL, "geo", ip)
    return {"country": country, "country_code": code, "city": city,
            "org": f"AS{64496 + _h('as', ip) % 512} hosting", "isp": "Example Hosting",
            "lat": lat, "lon": lon, "hosting": True, "reverse": ""}


# --------------------------------------------------------------------------
# collection + the single rewrite pass
# --------------------------------------------------------------------------

def _walk_strings(node, fn):
    """Rewrite every string in `node` (dict keys included) through `fn`, in
    place where possible. Dicts are rebuilt in their original insertion
    order: `seed._gen_events` draws `rng.choice` from `known_macs` key order,
    so re-ordering here would silently change the synthesized demo."""
    if isinstance(node, dict):
        items = [(fn(k) if isinstance(k, str) else k, _walk_strings(v, fn)) for k, v in node.items()]
        node.clear()
        node.update(items)
        return node
    if isinstance(node, list):
        for i, v in enumerate(node):
            node[i] = _walk_strings(v, fn)
        return node
    return fn(node) if isinstance(node, str) else node


def _each_string(node):
    if isinstance(node, dict):
        for k, v in node.items():
            if isinstance(k, str):
                yield k
            yield from _each_string(v)
    elif isinstance(node, list):
        for v in node:
            yield from _each_string(v)
    elif isinstance(node, str):
        yield node


class _Table:
    """The ordered replacement table: literal free-text tokens first (longest
    first, so `"safehome.example.com"` beats `"safehome"`), then the MAC / IPv4 /
    domain classes. One `re.sub` over the alternation rewrites each string
    once, so no replacement can be re-matched by a later rule.

    Literals carry an alphanumeric guard on each side, so replacing a short
    device name (`weme`) can never corrupt a longer word that contains it.
    """

    def __init__(self) -> None:
        self._literals: dict[str, str] = {}
        self._re: re.Pattern | None = None

    def add(self, old: str, new: str) -> None:
        if not old or old == new or len(old) < _MIN_TOKEN:
            return
        self._literals.setdefault(old, new)
        self._re = None

    def _compiled(self) -> re.Pattern:
        if self._re is None:
            keys = sorted(self._literals, key=lambda s: (-len(s), s))
            alt = "|".join(re.escape(k) for k in keys)
            parts = [f"(?<![A-Za-z0-9])(?P<lit>{alt})(?![A-Za-z0-9])"] if keys else []
            parts += [f"(?P<mac>{_RE_MAC})", f"(?P<ip>{_RE_IP})", f"(?P<dom>{_RE_DOMAIN})"]
            self._re = re.compile("|".join(parts))
        return self._re

    def _one(self, match: re.Match) -> str:
        text = match.group(0)
        kind = match.lastgroup
        if kind == "lit":
            return self._literals[text]
        if kind == "mac":
            return map_mac(text)
        if kind == "ip":
            return map_ip(text)
        return map_host(text)

    def apply(self, text: str) -> str:
        return self._compiled().sub(self._one, text)


def build_table(last: dict, state: dict) -> _Table:
    """Collect every free-text identifier the fixtures carry and decide its
    replacement. Only free text needs collecting: addresses, MACs and
    domains are matched by pattern wherever they appear, including in fields
    nobody has thought of yet."""
    table = _Table()

    for site in last.get("sites") or []:
        if isinstance(site, dict):
            table.add(site.get("name") or "", map_site_name(site.get("name") or ""))
    for name in (last.get("admin_probe") or {}):
        table.add(name, map_site_name(name))
    for name in (state.get("sites") or {}):
        table.add(name, map_site_name(name))

    for host in last.get("ssh") or []:
        if not isinstance(host, dict):
            continue
        table.add(host.get("name") or "", map_host_name(host.get("name") or ""))
        table.add(host.get("hostname") or "", map_host_name(host.get("hostname") or ""))
        for image in host.get("docker") or []:
            if isinstance(image, str):
                table.add(image.split(":")[0], map_image(image.split(":")[0]))
        for image in host.get("docker_images") or {}:
            table.add(image.split(":")[0], map_image(image.split(":")[0]))
        for user in (host.get("sec") or {}).get("login_users") or []:
            if isinstance(user, str):
                table.add(user, map_login_user(user))
    for name in (state.get("ssh") or {}):
        table.add(name, map_host_name(name))
    for name in (state.get("sec") or {}):
        table.add(name, map_host_name(name))

    known = ((state.get("lan") or {}).get("known_macs") or {})
    names: list[str] = []
    for device in known.values():
        if isinstance(device, dict):
            names += [device.get("name") or "", device.get("label") or ""]
    names += [n for n in ((last.get("lan") or {}).get("names") or {}).values() if isinstance(n, str)]
    for name in names:
        table.add(name, map_device_name(name))

    for name in (last.get("heartbeats") or {}):
        table.add(name, map_heartbeat(name))
    for name in (state.get("heartbeats") or {}):
        table.add(name, map_heartbeat(name))

    for ssid, new in _PIN_SSIDS.items():
        table.add(ssid, new)
    for ssid in {((last.get("home") or {}).get("wifi") or {}).get("ssid") or "",
                 state.get("home_ssid") or ""}:
        table.add(ssid, _PIN_SSIDS.get(ssid) or SSID)

    return table


def scrub(last: dict, state: dict) -> _Table:
    """Rewrite `last` and `state` in place into the Northwind Dental
    network. Returns the table so callers can scrub other strings that
    carry the same values.

    Deterministic, not idempotent: the same input documents always give the
    same output (which is what `seed.refresh_demo` needs, since it re-seeds
    hourly from these files), but re-scrubbing an already-scrubbed document
    renames the fictional identifiers again. Always scrub a fresh load.
    """
    table = build_table(last, state)
    _walk_strings(last, table.apply)
    _walk_strings(state, table.apply)
    return table


_RE_ATTACKER_KEY = re.compile(r"sec:attacker:[^:\\s\"]+:(" + _RE_IP + r")")


def attacker_ips(last: dict, state: dict | None = None) -> list[str]:
    """The addresses the Security tab will want geo for.

    `payloads/security.attackers` builds its list from two places: the
    `top_attackers` map on each host in the newest scan, and every
    `sec:attacker:<host>:<ip>` Alert row. Those alert rows come from
    `state["alerts_db"]`/`state["alert_history"]` as well as `last["alerts"]`,
    so this sweeps every string in both documents for the key shape rather
    than reading the two lists it happens to know about -- otherwise an
    attacker that only exists in the alert history lands on the map with no
    country ("Location unknown").

    Insertion-ordered and de-duplicated, and filtered to documentation
    addresses so it can only ever be called on scrubbed documents.
    """
    found: dict[str, None] = {}
    for host in last.get("ssh") or []:
        if isinstance(host, dict):
            for ip in ((host.get("sec") or {}).get("top_attackers") or {}):
                found.setdefault(ip, None)
    for doc in (last, state or {}):
        for text in _each_string(doc):
            for ip in _RE_ATTACKER_KEY.findall(text):
                found.setdefault(ip, None)
    return [ip for ip in found if is_doc_ip(ip)]


def find_real_values(strings) -> list[str]:
    """Every value in `strings` that a demo tenant must not contain: a public
    IPv4 outside the allow-list, or a domain outside it. The scrub test's
    engine -- exposed here so the definition of "real" lives next to the
    rules that avoid it rather than drifting in a test file."""
    bad: list[str] = []
    for text in strings:
        for token in re.findall(_RE_IP, text):
            if not ip_is_allowed(token):
                bad.append(token)
        for token in re.findall(_RE_DOMAIN, text):
            if re.fullmatch(_RE_IP, token):
                continue
            if not domain_is_allowed(token):
                bad.append(token)
    return bad
