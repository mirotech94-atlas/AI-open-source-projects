"""Which addresses the HUB itself may fetch.

The hub runs on a VPS next to its own database, admin port and the cloud
metadata service at 169.254.169.254. Anything that makes it fetch a URL a
customer typed -- the `/live` site probe, chat's `site_check`, an outbound
webhook -- is a way to make it fetch *those*, and read back at least whether
they answered (audit 2026-09-28, pass 2, issue 8). This module is the one
answer to "is this URL somewhere on the public internet", so the three
callers cannot drift apart.

It is a leaf on purpose: `routers/live.py`, `integrations/webhook.py` and
`chat/tools.py` all import it, and `chat.tools` imports half the routers,
so the check could not live there without a cycle.

Two strengths, for two moments:

- `literal_problem` -- at SAVE time. No DNS: a save must not depend on the
  resolver being up, and tests must not touch the network. It refuses what
  is non-public on its face (a private/loopback/link-local IP literal, a
  `localhost`/`.local` name) -- or, for a site the sensor watches from
  inside the LAN (`allow_lan`), only loopback, localhost and link-local. A
  public-looking name can still resolve anywhere, which is why --
- `is_public_http` -- before EVERY fetch. Resolves the name and requires
  every address to be global. A name that does not resolve is not public:
  better a refusal than a blind fetch.

Redirects are the other door: a public URL answering `302 Location:
http://169.254.169.254/` turns a checked fetch into an unchecked one.
`open_public` follows a redirect only to another public URL
(`PublicOnlyRedirects`) or not at all (`NoRedirects`, for webhooks, which
have no business being redirected).

Known gap: the name is resolved here and again by urllib when it connects,
so a hostile DNS server answering public-then-private (DNS rebinding) can
still slip between the two. Closing that means connecting to the checked
address itself, which is a larger change than this module.
"""
from __future__ import annotations

import ipaddress
import socket
import urllib.request
from urllib.parse import urlparse

#: Names that are local by definition, whatever a resolver says.
_LOCAL_SUFFIXES = (".localhost", ".local", ".internal", ".home.arpa", ".lan")


class NotPublicAddress(OSError):
    """A fetch refused because its target is not on the public internet.
    An OSError, so every existing `except OSError` around a fetch still
    treats it as "did not answer" rather than crashing."""


def resolve(host: str) -> list[ipaddress.IPv4Address | ipaddress.IPv6Address]:
    """Every address `host` resolves to. Module-level so tests replace it
    and never touch real DNS."""
    return [ipaddress.ip_address(info[4][0]) for info in socket.getaddrinfo(host, None)]


def _is_global(addr) -> bool:
    # `::ffff:127.0.0.1` is loopback wearing an IPv6 coat; judge what it maps to.
    if getattr(addr, "ipv4_mapped", None) is not None:
        addr = addr.ipv4_mapped
    return bool(addr.is_global)


def _literal(host: str):
    try:
        return ipaddress.ip_address(host)
    except ValueError:
        return None


def _never_a_lan_target(addr) -> bool:
    """Addresses no customer's network has a site on: this machine
    (loopback), link-local (169.254.x, where the cloud metadata service
    lives), and the unspecified/multicast/reserved ranges."""
    if getattr(addr, "ipv4_mapped", None) is not None:
        addr = addr.ipv4_mapped
    return (addr.is_loopback or addr.is_link_local or addr.is_unspecified
            or addr.is_multicast or addr.is_reserved)


def literal_problem(url: str, *, allow_lan: bool = False) -> str | None:
    """Why `url` is plainly not public, or None. No DNS lookup.

    `allow_lan` is for SITES. The sensor probes them from inside the
    customer's network, and Settings itself offers the web servers it sees
    there (`routers/settings._suggested_sites`) as `https://192.168.x.y`
    sites to watch -- so a private address is a legitimate site, one only
    the sensor can reach. What stays refused even then is what is never a
    LAN site and is exactly what an SSRF wants: loopback, `localhost`, and
    link-local (169.254.169.254). The hub's own fetch (`is_public_http`,
    before every probe) never goes to a private address either way.
    """
    parts = urlparse(url or "")
    host = (parts.hostname or "").lower().rstrip(".")
    if parts.scheme not in ("http", "https") or not host:
        return "needs an http(s) url"
    addr = _literal(host)
    if addr is not None:
        if _is_global(addr):
            return None
        if allow_lan and not _never_a_lan_target(addr):
            return None
        return f"{host} is not a public address"
    if host == "localhost" or host.endswith(".localhost"):
        return f"{host} is not a public address"
    if not allow_lan and host.endswith(_LOCAL_SUFFIXES):
        return f"{host} is not a public address"
    return None


def is_public_http(url: str) -> bool:
    """http(s), and every address the host resolves to is global."""
    if literal_problem(url):
        return False
    host = (urlparse(url).hostname or "").rstrip(".")
    addr = _literal(host)
    if addr is not None:
        return _is_global(addr)
    try:
        addrs = resolve(host)
    except Exception:
        return False
    return bool(addrs) and all(_is_global(a) for a in addrs)


class PublicOnlyRedirects(urllib.request.HTTPRedirectHandler):
    """Follow a redirect only when it lands somewhere public, re-checked
    (and re-resolved) at every hop."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if not is_public_http(newurl):
            raise NotPublicAddress(f"refused a redirect to a non-public address ({code})")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


class NoRedirects(urllib.request.HTTPRedirectHandler):
    """Never follow: urllib then raises the 3xx as an HTTPError."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def open_public(req: urllib.request.Request, *, timeout: float, context=None,
                follow_redirects: bool = True):
    """`urlopen`, for a URL that must be public -- checked before the fetch
    and at every redirect."""
    if not is_public_http(req.full_url):
        raise NotPublicAddress("refused to fetch a non-public address")
    handlers: list = [PublicOnlyRedirects() if follow_redirects else NoRedirects()]
    if context is not None:
        handlers.append(urllib.request.HTTPSHandler(context=context))
    return urllib.request.build_opener(*handlers).open(req, timeout=timeout)
