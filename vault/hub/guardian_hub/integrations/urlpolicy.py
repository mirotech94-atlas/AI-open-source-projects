"""One place the tenant-supplied URL/host policy is decided (fix round 1,
finding 4 / ruling e).

Before this, only `webhook.py` enforced `https://`; Discord's webhook URL,
Jira/ServiceNow's `base_url`, Home Assistant's `ha_base_url` and the MQTT
`host` accepted anything a signed-in admin typed -- including
`http://169.254.169.254/latest/meta-data/` and `http://127.0.0.1:8080/`,
which the Jira and Home Assistant calls then hand an `Authorization:`
header to. Admin-only is not a reason to keep an SSRF-shaped hole.

The ruling, implemented literally:

- **https required** for the Discord webhook URL and the Jira / ServiceNow
  base URL. These are hosted SaaS endpoints; there is no legitimate
  plaintext form of them.
- **plain http / tcp allowed only for a private host** for the Home
  Assistant base URL and the MQTT host: a home install really is
  `http://homeassistant.local:8123` or `tcp://192.168.1.10:1883`, and
  demanding TLS there would just stop the integration from being usable.
  "Private host" means an RFC1918 / RFC4193 literal address, or a
  `.local` / bare single-label hostname.
- **loopback and link-local are always rejected**, in every field, plain or
  TLS: `127.0.0.0/8`, `::1`, `169.254.0.0/16`, `fe80::/10`, and the names
  `localhost` / `*.localhost`. A hub reaching its own loopback (or the
  cloud metadata service) on a tenant's instruction is the SSRF this guard
  exists for.

No DNS resolution happens here: validation is a save-time check on the
string the admin typed, and resolving it would both slow the request down
and be trivially defeated by a name that resolves differently later. The
policy is deliberately about the literal host.
"""

from __future__ import annotations

import ipaddress
from urllib.parse import urlsplit

_LOOPBACK_NAMES = ("localhost",)


def _is_loopback_or_linklocal_name(host: str) -> bool:
    h = host.lower().rstrip(".")
    return h in _LOOPBACK_NAMES or h.endswith(".localhost")


def _has_leading_zero_octet(h: str) -> bool:
    """A dotted quad where some octet has a leading zero (`0177.0.0.1`,
    `127.0.0.01`) is ambiguous: some parsers read the octet as octal, some
    as decimal, and they disagree with each other. Rather than pick one and
    risk being wrong, block the literal outright."""
    parts = h.split(".")
    if len(parts) != 4:
        return False
    return any(len(p) > 1 and p[0] == "0" and p.isdigit() for p in parts)


def _parsed_ip(host: str) -> "ipaddress._BaseAddress | None":
    h = host.strip("[]")
    try:
        return ipaddress.ip_address(h)
    except ValueError:
        pass
    # Integer / hex literal host forms: `http://2130706433/` and
    # `http://0x7f000001/` both resolve to 127.0.0.1 in every browser and
    # in the stdlib socket layer, so a literal-string "is it an IP"
    # comparison that only handles dotted/colon notation misses them.
    # Dotted octal-looking forms are handled by `_has_leading_zero_octet`
    # instead of here, since they must be rejected outright rather than
    # guessed at.
    if h and "." not in h and ":" not in h:
        try:
            if h.isdigit() or h.lower().startswith("0x"):
                return ipaddress.ip_address(int(h, 0))
        except (ValueError, OverflowError):
            return None
    return None


def _effective_ip(ip: "ipaddress._BaseAddress") -> "ipaddress._BaseAddress":
    """Unwrap IPv4-in-IPv6 forms (`::ffff:127.0.0.1`, 6to4 `2002::/16`) down
    to the address they actually carry. Without this, `::ffff:127.0.0.1` is
    a technically-valid, technically-not-`is_loopback` IPv6 literal that a
    dual-stack socket still connects to 127.0.0.1 over."""
    mapped = getattr(ip, "ipv4_mapped", None)
    if mapped is not None:
        return mapped
    sixtofour = getattr(ip, "sixtofour", None)
    if sixtofour is not None:
        return sixtofour
    return ip


def host_is_blocked(host: str) -> bool:
    """Loopback / link-local / unspecified, by literal address or by name.
    Rejected in every field regardless of scheme."""
    if not host:
        return True
    if _is_loopback_or_linklocal_name(host):
        return True
    if _has_leading_zero_octet(host.strip("[]")):
        return True
    ip = _parsed_ip(host)
    if ip is None:
        return False
    ip = _effective_ip(ip)
    return bool(ip.is_loopback or ip.is_link_local or ip.is_unspecified or ip.is_multicast
                or ip.is_reserved)


def host_is_private(host: str) -> bool:
    """True for a host a plain-http/tcp integration may legitimately live
    on: an RFC1918 / RFC4193 literal, a `.local` mDNS name, or a bare
    single-label hostname (`homeassistant`, `nas`). Never true for anything
    `host_is_blocked` rejects, and never true for a literal that parses to a
    non-private IP by the same integer/hex/IPv4-mapped rules `host_is_blocked`
    uses."""
    if host_is_blocked(host):
        return False
    ip = _parsed_ip(host)
    if ip is not None:
        ip = _effective_ip(ip)
        # 100.64.0.0/10 is carrier-grade NAT space, which is also what Tailscale
        # hands out. Python does not count it as private; for this policy it is
        # exactly what "private" means -- a host only a VPN peer can reach.
        if ip.version == 4 and ip in ipaddress.ip_network("100.64.0.0/10"):
            return True
        return bool(ip.is_private)
    h = host.lower().rstrip(".")
    if h.endswith(".local"):
        return True
    return "." not in h


def check_url(url: str, label: str, *, allow_plain_for_private: bool = False) -> str:
    """Validate a tenant-supplied URL. Returns an error string for
    `Integration.validate()` to hand back as a 422, or "" when it passes.
    An empty `url` passes -- "required" is the field's own business (and on
    an edit, blank means "keep what's stored")."""
    if not url:
        return ""
    parts = urlsplit(url)
    scheme = (parts.scheme or "").lower()
    if scheme not in ("http", "https"):
        return f"{label} must be an http(s) URL"
    host = parts.hostname or ""
    if not host:
        return f"{label} must include a hostname"
    if host_is_blocked(host):
        return f"{label} must not point at a loopback or link-local address"
    if scheme != "https":
        if not allow_plain_for_private:
            return f"{label} must be https"
        if not host_is_private(host):
            return f"{label} may only be plain http on a private (RFC1918 or .local) host"
    return ""


def check_host(host: str, label: str) -> str:
    """The MQTT variant: a bare host, no scheme, and this client always
    speaks plain TCP (`homeassistant.py`'s stdlib CONNECT/PUBLISH -- no
    TLS). The ruling allows plain tcp only for a private host, and since
    there is no `mqtts` option here at all, that makes the host itself the
    whole check: RFC1918 / `.local` / bare-single-label passes, a public
    broker (which would mean shipping the MQTT password across the internet
    in the clear) and any loopback/link-local address do not."""
    if not host:
        return ""
    bare = host.split("/")[0]
    if bare.startswith("["):
        # Bracketed IPv6 literal, possibly with a trailing `:port` outside
        # the brackets (`[::ffff:127.0.0.1]:8123`) -- keep the bracketed
        # literal and drop the port, rather than naively splitting on the
        # first/last colon and mangling the address.
        end = bare.find("]")
        if end != -1:
            bare = bare[: end + 1]
    elif ":" in bare:
        bare = bare.rsplit(":", 1)[0]
    if host_is_blocked(bare):
        return f"{label} must not be a loopback or link-local address"
    if not host_is_private(bare):
        return (f"{label} must be a private (RFC1918 or .local) address -- this MQTT client "
                 "speaks plain TCP, so a public broker would send the password in the clear")
    return ""


def check_public_host(host: str, label: str) -> str:
    """The TLS variant of `check_host`: with the password protected by TLS a
    public broker is fine; only loopback / link-local / a blocked host is
    refused. `homeassistant.py` picks this when the port is 8883 or a CA is
    supplied."""
    if not host:
        return ""
    bare = host.split("/")[0]
    if bare.startswith("["):
        bare = bare[1:bare.index("]")] if "]" in bare else bare[1:]
    elif ":" in bare and bare.count(":") == 1:
        bare = bare.rsplit(":", 1)[0]
    if host_is_blocked(bare):
        return f"{label} may not be a loopback or link-local address"
    return ""
