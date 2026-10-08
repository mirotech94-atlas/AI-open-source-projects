"""Is this URL on the public web? Asked before ATLAS fetches anything a page,
a search engine or an agent chose.

ATLAS runs next to its own API, which trusts 127.0.0.0/8 and the mesh as
Miro. A fetch of `http://localtest.me:8080/api/memory` — a public name that
resolves to 127.0.0.1 — would hand his memory to the prompt, and the next
fetch could carry it out in a query string. Reading is the leak, so every
fetch of an outside-chosen URL asks `assert_public` first, and every redirect
hop asks again.

`assert_public(url)` resolves the host (a literal IP, including the integer,
octal, hex and short forms `2130706433`, `0177.0.0.1`, `0x7f.1`, `127.1`, is
checked without DNS) and refuses when ANY address is loopback, private
(RFC 1918), link-local, CGNAT 100.64/10 (which is also the NetBird mesh
range), unspecified, multicast, reserved, IPv6 unique-local or link-local, or
a cloud metadata address. A host that does not resolve is refused too.
Answers are cached for 60 s per host, at most 512 hosts (least recently
used evicted first).

What this does not close, stated so nobody relies on it for more:
  * DNS rebinding — a name that answers public to this lookup and private to
    the fetcher's own lookup a moment later.
  * The one GET a redirect hop sends before the hop is seen (the browser
    detects and refuses such a page afterwards; httpx callers here follow
    redirects by hand and check each hop first).
The lasting fix is at the network layer — an egress rule denying these ranges
to the fetching process, or a filtering forward proxy. That is Miro's call.
"""
from __future__ import annotations

import asyncio
import ipaddress
import re
import socket
import time
from collections import OrderedDict
from typing import Any
from urllib.parse import urljoin, urlsplit

CACHE_SECONDS = 60.0
CACHE_SIZE = 512              # hosts; least recently used goes first
MAX_REDIRECTS = 5

CGNAT = ipaddress.ip_network("100.64.0.0/10")      # also NetBird's default mesh
NETBIRD = CGNAT
METADATA = frozenset({ipaddress.ip_address("169.254.169.254"),
                      ipaddress.ip_address("fd00:ec2::254")})

_CACHE: OrderedDict[str, tuple[float, str | None]] = OrderedDict()
_NUMERIC = re.compile(r"^[0-9a-fx.]+$", re.IGNORECASE)

IP = ipaddress.IPv4Address | ipaddress.IPv6Address


def blocked(ip: IP) -> str | None:
    """Why this address is not public, or None."""
    if isinstance(ip, ipaddress.IPv6Address):
        if ip.ipv4_mapped is not None:
            return blocked(ip.ipv4_mapped)
        if ip.sixtofour is not None:
            return blocked(ip.sixtofour)
    if ip in METADATA:
        return "cloud metadata"
    if ip.is_loopback:
        return "loopback"
    if ip.is_unspecified:
        return "unspecified"
    if ip.is_multicast:
        return "multicast"
    if ip.is_link_local:
        return "link-local"
    if isinstance(ip, ipaddress.IPv4Address) and ip in CGNAT:
        return "CGNAT / mesh"
    if ip.is_private:
        return "private"
    if ip.is_reserved or not ip.is_global:
        return "reserved"
    return None


def literal(host: str) -> IP | None:
    """The address a host names without DNS, or None when it is a name.
    Legacy IPv4 spellings are normalised the way browsers do."""
    try:
        return ipaddress.ip_address(host.split("%", 1)[0])
    except ValueError:
        pass
    if _NUMERIC.match(host):
        try:
            return ipaddress.ip_address(socket.inet_aton(host))
        except OSError:
            return None
    return None


async def _getaddrinfo(host: str) -> list[str]:
    """Every address the host resolves to. Non-blocking."""
    infos = await asyncio.get_running_loop().getaddrinfo(
        host, None, type=socket.SOCK_STREAM)
    return [info[4][0] for info in infos]


async def _check_host(host: str) -> str | None:
    ip = literal(host)
    if ip is not None:
        why = blocked(ip)
        return f"{host} is a {why} address, not the public web" if why else None
    try:
        addresses = await _getaddrinfo(host)
    except (OSError, UnicodeError) as exc:
        return f"{host} does not resolve ({type(exc).__name__}); not fetched"
    if not addresses:
        return f"{host} does not resolve; not fetched"
    for address in addresses:
        try:
            ip = ipaddress.ip_address(address.split("%", 1)[0])
        except ValueError:
            return f"{host} resolved to something that is not an address; not fetched"
        why = blocked(ip)
        if why:
            return f"{host} resolves to a {why} address, not the public web"
    return None


async def assert_public(url: str) -> str | None:
    """None when every address `url`'s host resolves to is public; otherwise
    the reason it is not fetched."""
    try:
        parts = urlsplit((url or "").strip())
        host = (parts.hostname or "").lower().rstrip(".")
    except ValueError:
        return "not a URL; not fetched"
    if parts.scheme.lower() not in ("http", "https"):
        return f"only http(s) is fetched, not {parts.scheme or 'no scheme'!r}"
    if not host:
        return "the URL has no host; not fetched"
    now = time.monotonic()
    cached = _CACHE.get(host)
    if cached is not None:
        if cached[0] > now:
            _CACHE.move_to_end(host)
            return cached[1]
        del _CACHE[host]
    reason = await _check_host(host)
    _CACHE[host] = (now + CACHE_SECONDS, reason)
    while len(_CACHE) > CACHE_SIZE:
        _CACHE.popitem(last=False)
    return reason


async def get_public(client: Any, url: str, *, headers: dict[str, str] | None = None
                     ) -> tuple[Any, str | None]:
    """GET through an httpx client created with follow_redirects=False,
    following up to five redirects by hand and asking `assert_public` before
    every hop. Returns (response, None) or (None, reason)."""
    for _hop in range(MAX_REDIRECTS + 1):
        reason = await assert_public(url)
        if reason is not None:
            return None, reason
        response = await client.get(url, headers=headers)
        location = response.headers.get("location") if response.status_code in (
            301, 302, 303, 307, 308) else None
        if not location:
            return response, None
        url = urljoin(url, location)
    return None, f"more than {MAX_REDIRECTS} redirects; not fetched"
