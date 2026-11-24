"""`qa.security_scan`: a passive look at a site Mapfront is allowed to look
at. Never an attack: GETs only, a fixed list of paths, at most MAX_REQUESTS
requests, redirects never followed, no form ever submitted.

Allowed hosts: an `agency_sites.domain` Mapfront built (kind 'built') or one
whose owner consented (`security_consent_at`), plus Mapfront's own hosts.
The allowlist and the public-address check both pass before any socket is
opened, TLS included. The host is resolved exactly once
(`_netguard.resolve_public`) and every connection -- the TLS read and each
GET -- dials that pinned address, with the domain kept as SNI, certificate
name and Host header: a DNS answer that changes after the check (rebinding)
is never asked for, so it cannot point a probe at a private address.

What a row keeps: header names present or missing, the TLS version and the
certificate's expiry, and for every probe its status, byte length and
sha256. Never a body: a page read for its `<script src>` list is parsed in
memory and dropped.
"""
from __future__ import annotations

import asyncio
import hashlib
import re
import ssl
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import httpx

from .. import _netguard, store_origin

FIXED_HOSTS: dict[str, str] = {
    "shop": store_origin.host(),
    "agency": store_origin.host(),
}
EXPOSED_PATHS = ("/.env", "/.git/HEAD", "/wp-config.php.bak", "/backup.zip", "/.DS_Store")
HEADERS = ("strict-transport-security", "content-security-policy",
           "x-content-type-options", "referrer-policy")
FRAME_HEADERS = ("x-frame-options",)
MAX_REQUESTS = 20
MAX_BYTES = 2 * 1024 * 1024
TIMEOUT = 15.0
CERT_WARN_DAYS = 14
# The oldest version of each library without a published, widely exploited
# flaw (a code list, reviewed by hand; not fetched).
MIN_SAFE: dict[str, tuple[int, ...]] = {
    "jquery": (3, 5, 0),
    "bootstrap": (3, 4, 1),
    "angular": (1, 8, 0),
    "lodash": (4, 17, 21),
    "moment": (2, 29, 4),
}
_SCRIPT = re.compile(r"<script[^>]+src\s*=\s*[\"']([^\"']+)[\"']", re.IGNORECASE)
_VERSIONED = re.compile(
    r"(jquery|bootstrap|angular|lodash|moment)(?:\.min)?[-@/.]v?(\d+)\.(\d+)(?:\.(\d+))?",
    re.IGNORECASE)


class Refused(ValueError):
    """The host may not be scanned; nothing was requested."""


@dataclass
class Scan:
    host: str
    findings: list[str] = field(default_factory=list)   # fail the scan
    warnings: list[str] = field(default_factory=list)   # reported, not failing
    unknown: list[str] = field(default_factory=list)    # could not be checked
    probes: list[dict[str, Any]] = field(default_factory=list)
    requests: int = 0

    def evidence(self) -> dict[str, Any]:
        return {"host": self.host, "warnings": self.warnings, "unknown": self.unknown,
                "probes": self.probes, "requests": self.requests}


async def allowed_host(slug: str) -> str:
    """The host `slug` names, if it may be scanned; else raise Refused."""
    if slug in FIXED_HOSTS:
        return FIXED_HOSTS[slug]
    from ...kernel.state import fetch_one
    row = await fetch_one(
        """SELECT domain, kind, security_consent_at FROM agency_sites WHERE slug = $1""",
        str(slug))
    if row is None:
        raise Refused(f"no site {slug!r}")
    domain = str(row["domain"] or "").strip().lower().rstrip(".")
    if not domain or "/" in domain or ":" in domain:
        raise Refused(f"{slug} has no domain on record")
    if str(row["kind"] or "") != "built" and row["security_consent_at"] is None:
        raise Refused(f"{domain} was not built by Mapfront and its owner has not "
                      "consented to a scan")
    return domain


def _version(parts: tuple[str | None, ...]) -> tuple[int, ...]:
    return tuple(int(p) for p in parts if p is not None)


def script_findings(html: str) -> list[str]:
    out = []
    for src in _SCRIPT.findall(html or "")[:50]:
        m = _VERSIONED.search(src)
        if not m:
            continue
        lib = m.group(1).lower()
        have = _version((m.group(2), m.group(3), m.group(4)))
        if have < MIN_SAFE[lib]:
            safe = ".".join(map(str, MIN_SAFE[lib]))
            out.append(f"{lib} {'.'.join(map(str, have))} is older than {safe}")
    return out


def _netloc(ip: str) -> str:
    return f"[{ip}]" if ":" in ip else ip


async def _get(client: httpx.AsyncClient, scan: Scan, ip: str, path: str,
               keep_text: bool = False) -> tuple[dict[str, Any], str | None, Any]:
    """One streamed GET to the pinned address: (probe row, the text if asked
    for, headers). Host header and SNI carry the domain; the certificate is
    verified against the domain."""
    if scan.requests >= MAX_REQUESTS:
        raise Refused(f"the scan reached its {MAX_REQUESTS}-request cap")
    scan.requests += 1
    digest, size, chunks = hashlib.sha256(), 0, []
    url = f"https://{_netloc(ip)}{path}"
    async with client.stream("GET", url, headers={"Host": scan.host},
                             extensions={"sni_hostname": scan.host}) as r:
        async for chunk in r.aiter_bytes():
            take = chunk[: max(0, MAX_BYTES - size)]
            digest.update(take)
            size += len(take)
            if keep_text:
                chunks.append(take)
            if size >= MAX_BYTES:
                break
        status, headers = r.status_code, r.headers
    ctype = str(headers.get("content-type", "")).split(";", 1)[0].strip().lower()
    probe = {"path": path, "status": status,
             "type": ctype[:60], "bytes": size, "sha256": digest.hexdigest()}
    text = b"".join(chunks).decode("utf-8", "replace") if keep_text else None
    return probe, text, headers


async def _tls(host: str, ip: str) -> tuple[str | None, datetime | None]:
    """The TLS version and certificate expiry, read from the pinned address;
    the certificate is still verified against `host`."""
    ctx = ssl.create_default_context()
    reader, writer = await asyncio.wait_for(
        asyncio.open_connection(ip, 443, ssl=ctx, server_hostname=host), TIMEOUT)
    try:
        sslobj = writer.get_extra_info("ssl_object")
        version = sslobj.version() if sslobj else None
        cert = sslobj.getpeercert() if sslobj else None
        expires = None
        if cert and cert.get("notAfter"):
            expires = datetime.fromtimestamp(ssl.cert_time_to_seconds(cert["notAfter"]),
                                             UTC)
        return version, expires
    finally:
        writer.close()
        try:
            await writer.wait_closed()
        except (OSError, ssl.SSLError):
            pass
        del reader


async def scan(slug: str, *, client: httpx.AsyncClient | None = None) -> Scan:
    host = await allowed_host(slug)
    ip, why = await _netguard.resolve_public(host)
    if why or ip is None:
        raise Refused(why or f"{host} did not resolve")
    result = Scan(host)
    try:
        version, expires = await _tls(host, ip)
    except (OSError, ssl.SSLError, TimeoutError) as exc:
        result.unknown.append(f"TLS could not be read: {type(exc).__name__}")
    else:
        if version in ("TLSv1", "TLSv1.1", "SSLv3"):
            result.findings.append(f"TLS {version} accepted")
        if expires is not None:
            days = (expires - datetime.now(UTC)).days
            if days < CERT_WARN_DAYS:
                result.findings.append(f"the certificate expires in {days} day(s)")
            result.probes.append({"tls": version, "cert_expires": expires.isoformat()})
    own = client is None
    if client is None:
        client = httpx.AsyncClient(timeout=TIMEOUT, follow_redirects=False,
                                   trust_env=False,
                                   headers={"User-Agent": "MapfrontQA/1.0"})
    try:
        try:
            probe, html, headers = await _get(client, result, ip, "/", keep_text=True)
        except (httpx.HTTPError, OSError) as exc:
            result.unknown.append(f"the home page could not be read: {type(exc).__name__}")
            return result
        result.probes.append(probe)
        home_sha = probe["sha256"]
        present = {k.lower() for k in headers}
        missing = [h for h in HEADERS if h not in present]
        if not any(h in present for h in FRAME_HEADERS) and "content-security-policy" \
                not in present:
            missing.append("x-frame-options")
        if missing:
            result.warnings.append("missing headers: " + ", ".join(missing))
        result.findings += script_findings(html or "")
        del html
        for path in EXPOSED_PATHS:
            try:
                probe, _text, _h = await _get(client, result, ip, path)
            except (httpx.HTTPError, OSError) as exc:
                result.unknown.append(f"{path}: {type(exc).__name__}")
                continue
            result.probes.append(probe)
            # A site that answers every path with its page (a soft 404) is
            # not serving the file: same bytes as the home page, or HTML.
            if (probe["status"] == 200 and probe["bytes"] > 0
                    and probe["sha256"] != home_sha and probe["type"] != "text/html"):
                result.findings.append(f"{path} is served ({probe['bytes']} bytes)")
    finally:
        if own:
            await client.aclose()
    return result
