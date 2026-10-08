"""ip-api.com geolocation cache for public attacker IPs.

`lookup(db, ip)` is the main entrypoint callers need: it reads `IpGeo` first,
calls `_fetch` on a miss (or a stale row past `CACHE_DAYS`), stores the
result, and returns a `schemas_dash.Geo`. A private/reserved/loopback IP
never reaches the network -- it returns `None` immediately.

`is_cached(db, ip)` answers "would `lookup` avoid the network for this IP"
without ever calling it -- callers budgeting a per-request cap on *new*
lookups (e.g. `payloads/security.attackers`) use it to decide whether a
given IP's `lookup` call counts against that cap.

`IpGeo` is hub-global (not tenant-scoped): geo facts about a public IP are
not tenant data (see its docstring in `models.py`), so the cache is shared
across every tenant on this hub.

Tests monkeypatch `_fetch` (not `lookup`), so `lookup` must call it as a
module-level name (`_fetch(ip)`), never inline the HTTP call itself and
never be imported as a bound function elsewhere -- callers always go
through `geoip.lookup`.
"""

import datetime as dt
import ipaddress
import json
import logging
import urllib.request

from sqlalchemy.orm import Session as DbSession

from . import models as m
from .schemas_dash import Geo

log = logging.getLogger(__name__)

CACHE_DAYS = 30
TIMEOUT_S = 4
FIELDS = "status,country,countryCode,city,org,isp,lat,lon,hosting,reverse"

_GEO_KEYS = ("country", "country_code", "city", "org", "isp", "lat", "lon", "hosting", "reverse")


def _is_public(ip: str) -> bool:
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return False
    return not (addr.is_private or addr.is_loopback or addr.is_link_local
                or addr.is_reserved or addr.is_multicast or addr.is_unspecified)


#: RFC 5737 documentation ranges. `ipaddress` classes these as private, so
#: they never reach a provider; `lookup` still serves a cached row for one.
_DOC_NETS = tuple(ipaddress.ip_network(c) for c in ("192.0.2.0/24", "198.51.100.0/24", "203.0.113.0/24"))


def _is_doc(ip: str) -> bool:
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return False
    return any(addr in net for net in _DOC_NETS)


def _fetch(ip: str) -> dict | None:
    """One real ip-api.com call. Never raises -- any failure (timeout, bad
    JSON, rate limit, a non-"success" status) is logged and returns None so
    the caller's payload still renders."""
    url = f"http://ip-api.com/json/{ip}?fields={FIELDS}"
    try:
        with urllib.request.urlopen(url, timeout=TIMEOUT_S) as resp:
            data = json.loads(resp.read().decode())
    except Exception:
        log.warning("geoip lookup failed for %s", ip, exc_info=True)
        return None
    if data.get("status") != "success":
        return None
    return {
        "country": data.get("country") or "", "country_code": data.get("countryCode") or "",
        "city": data.get("city") or "", "org": data.get("org") or "", "isp": data.get("isp") or "",
        "lat": data.get("lat"), "lon": data.get("lon"),
        "hosting": bool(data.get("hosting")), "reverse": data.get("reverse") or "",
    }


def _to_geo(row: m.IpGeo) -> Geo:
    return Geo(country=row.country, country_code=row.country_code, city=row.city,
               org=row.org, isp=row.isp, lat=row.lat, lon=row.lon,
               hosting=row.hosting, reverse=row.reverse)


def _fresh_row(db: DbSession, ip: str) -> m.IpGeo | None:
    """The cached `IpGeo` row for `ip` if it exists and is within
    `CACHE_DAYS`, else None. Never touches the network."""
    row = db.get(m.IpGeo, ip)
    if row is None:
        return None
    fetched = row.fetched or dt.datetime.min.replace(tzinfo=dt.timezone.utc)
    if dt.datetime.now(dt.timezone.utc) - fetched <= dt.timedelta(days=CACHE_DAYS):
        return row
    return None


def is_cached(db: DbSession, ip: str) -> bool:
    """True when `lookup(db, ip)` can be answered from the cache alone --
    no network call, so it never counts against a caller's per-request geo
    lookup budget. Private/reserved/blank IPs count as "cached" too (they
    never call the network either)."""
    if not ip or not _is_public(ip):
        return True
    return _fresh_row(db, ip) is not None


def lookup(db: DbSession, ip: str) -> Geo | None:
    """Cached geo lookup for `ip`. Returns None for private/reserved/blank
    IPs (no network call) or when a fresh lookup can't be made and there is
    no usable cached row.

    One exception to "no geo for a non-public address": an RFC 5737
    documentation address is answered from the cache when a row exists. The
    demo tenant's attacker addresses live in those ranges (see
    `demoscrub`), and `seed._seed_attacker_geo` writes their `IpGeo` rows,
    so the Security tab's attacker map still has pins. A provider is still
    never called for one -- there is nothing out there to ask.
    """
    if not ip:
        return None
    if not _is_public(ip):
        if _is_doc(ip):
            row = db.get(m.IpGeo, ip)
            return _to_geo(row) if row is not None else None
        return None

    row = _fresh_row(db, ip)
    if row is not None:
        return _to_geo(row)
    row = db.get(m.IpGeo, ip)

    data = _fetch(ip)
    if data is None:
        return _to_geo(row) if row is not None else None

    if row is None:
        row = m.IpGeo(ip=ip)
        db.add(row)
    for key in _GEO_KEYS:
        if key in data:
            setattr(row, key, data[key])
    row.fetched = dt.datetime.now(dt.timezone.utc)
    db.commit()
    return _to_geo(row)
