"""The polite shared scraper for the Sales, SEO, Research and QA agents.

the owner, 2026-10-27: "build our own simple polite web-scraping tool". It reads
public pages only, and only the way a careful person would:

  * an honest User-Agent with a contact URL (`SCRAPE_USER_AGENT`);
  * robots.txt honoured: a disallow, or a robots.txt that answers 5xx or
    cannot be read, fetches nothing; a 4xx robots.txt means "no rules";
  * one request per host every `PER_HOST_SECONDS` (2 s), a daily cap across
    every agent (`SCRAPE_DAILY_CAP`, default 300), a 15 s timeout and a
    2 MB body cap;
  * `_netguard.get_public` on every hop, so a page cannot steer a fetch into
    the local API or the mesh;
  * a 24 h cache (`scrape_pages`), so the same page is not asked twice a day;
  * GET only: no login, no form submit, no POST;
  * never Google Search or Maps HTML (AGENCY.md 2.1), never a social network,
    never a reddit host (Reddit is read through its API, phase 8).

Its output is somebody else's text: `wrapped()` puts it through
`wrap_untrusted` with the source `scrape:<host>` before any prompt sees it.

`search()` is free providers only: Brave's API when `BRAVE_API_KEY` is set,
Google CSE when both `GOOGLE_CSE_KEY` and `GOOGLE_CSE_CX` are; with neither
it says so. It never falls back to Perplexity or Firecrawl (ATLAS_FREE_ONLY):
a paid read is its own Tier 2 card, never a silent fallback.
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import re
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib import robotparser
from urllib.parse import urljoin, urlsplit

import httpx

from ..kernel.permissions import wrap_untrusted
from . import _netguard
from ._http import explain, redact
from ._netguard import get_public

log = logging.getLogger("atlas.scrape")

DEFAULT_UA = "ATLAS-scraper/1.0 (+https://agency.example.com/; polite, robots-aware)"
PER_HOST_SECONDS = 2.0
TIMEOUT = 15.0
MAX_BYTES = 2_000_000
CACHE_FOR = timedelta(hours=24)
PRUNE_AFTER = timedelta(days=30)
DEFAULT_CAP = 300

# Hosts never fetched, whatever the agent: search/maps HTML (their terms),
# social networks (logins, terms), reddit (API only).
HOST_DENY: tuple[str, ...] = (
    "google.*", "*.google.*", "maps.google.*", "reddit.com", "*.reddit.com",
    "redd.it", "*.redd.it", "linkedin.com", "*.linkedin.com", "facebook.com",
    "*.facebook.com", "instagram.com", "*.instagram.com",
)

_TAGS = re.compile(r"<script.*?</script>|<style.*?</style>", re.DOTALL | re.IGNORECASE)
_ANY_TAG = re.compile(r"<[^>]+>")
_WS = re.compile(r"\s+")

# Per-process politeness state. The clock and the sleep are module attributes
# so a test can drive them without waiting.
_clock = time.monotonic
_sleep = asyncio.sleep
_LAST_HIT: dict[str, float] = {}
_ROBOTS: dict[str, tuple[float, Any, str | None]] = {}
ROBOTS_TTL = 3600.0


@dataclass(frozen=True, slots=True)
class Scraped:
    url: str
    final_url: str
    status: int | None
    text: str
    fetched_at: datetime
    blocked: str | None = None
    cached: bool = False
    meta: dict[str, Any] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return self.blocked is None and self.status == 200


def user_agent() -> str:
    return (os.getenv("SCRAPE_USER_AGENT") or "").strip() or DEFAULT_UA


def daily_cap() -> int:
    try:
        return max(0, int(os.getenv("SCRAPE_DAILY_CAP", str(DEFAULT_CAP))))
    except ValueError:
        return DEFAULT_CAP


def _host(url: str) -> str:
    try:
        return (urlsplit(url).hostname or "").lower().rstrip(".")
    except ValueError:
        return ""


def denied(url: str) -> str | None:
    """Why this URL is never fetched, or None."""
    host = _host(url)
    if not host:
        return "not a URL with a host"
    parts = host.split(".")
    for pattern in HOST_DENY:
        bare = pattern.lstrip("*.")
        if bare.endswith(".*"):
            stem = bare[:-2]
            if stem in parts[:-1]:
                return f"{host} is on the scraper's deny list"
        elif host == bare or host.endswith("." + bare):
            return f"{host} is on the scraper's deny list"
    return None


def flatten(markup: str) -> str:
    return _WS.sub(" ", _ANY_TAG.sub(" ", _TAGS.sub(" ", markup or ""))).strip()


def wrapped(s: Scraped) -> str:
    """The page text as quoted data, never instruction."""
    return wrap_untrusted(s.text or "", f"scrape:{_host(s.final_url or s.url)}")


# ------------------------------------------------------------------ store

async def _count_today() -> int:
    from ..kernel.state import fetch_one
    row = await fetch_one(
        "SELECT count(*) AS n FROM scrape_log WHERE at >= date_trunc('day', now())")
    return int(row["n"]) if row is not None else 0


async def _cache_get(url: str) -> dict[str, Any] | None:
    from ..kernel.state import fetch_one
    row = await fetch_one(
        "SELECT url, sha, text, fetched_at FROM scrape_pages WHERE url = $1", url)
    return dict(row) if row is not None else None


async def _cache_put(url: str, text: str, at: datetime) -> None:
    from ..kernel.state import execute_sql
    sha = hashlib.sha256(text.encode()).hexdigest()
    await execute_sql(
        """INSERT INTO scrape_pages (url, sha, text, fetched_at)
           VALUES ($1, $2, $3, $4)
           ON CONFLICT (url) DO UPDATE SET sha = EXCLUDED.sha,
               text = EXCLUDED.text, fetched_at = EXCLUDED.fetched_at""",
        url, sha, text, at)


async def _log(url: str, agent: str, client: str, status: int | None,
               size: int) -> None:
    from ..kernel.state import execute_sql
    await execute_sql(
        """INSERT INTO scrape_log (at, host, url, agent, client, status, bytes)
           VALUES (now(), $1, $2, $3, $4, $5, $6)""",
        _host(url), redact(url)[:500], agent[:40], client[:120], status, size)


async def prune() -> None:
    from ..kernel.state import execute_sql
    await execute_sql("DELETE FROM scrape_log WHERE at < now() - $1::interval",
                      PRUNE_AFTER)
    await execute_sql("DELETE FROM scrape_pages WHERE fetched_at < now() - $1::interval",
                      PRUNE_AFTER)


# ------------------------------------------------------------------ robots

async def robots_for(client: httpx.AsyncClient, site: str, *,
                     agent_ua: str | None = None) -> tuple[Any, str | None]:
    """(parser, None) or (None, why nothing may be fetched). A 4xx robots.txt
    means no rules; 5xx or unreadable means nothing is fetched."""
    parts = urlsplit(site)
    url = f"{parts.scheme}://{parts.netloc}/robots.txt"
    parser = robotparser.RobotFileParser()
    try:
        response, why = await get_public(
            client, url, headers={"User-Agent": agent_ua or user_agent()})
    except Exception as exc:                            # noqa: BLE001
        return None, f"robots.txt unreadable: {explain(exc)}"
    if response is None:
        return None, f"robots.txt not fetched: {why}"
    if 400 <= response.status_code < 500:
        parser.parse([])
        return parser, None
    if response.status_code != 200:
        return None, f"robots.txt answered HTTP {response.status_code}"
    parser.parse(response.text[:200_000].splitlines())
    return parser, None


async def _robots_cached(client: httpx.AsyncClient, url: str) -> tuple[Any, str | None]:
    host = _host(url)
    now = _clock()
    hit = _ROBOTS.get(host)
    if hit is not None and hit[0] > now:
        return hit[1], hit[2]
    await _wait(host)
    parser, why = await robots_for(client, url)
    _ROBOTS[host] = (now + ROBOTS_TTL, parser, why)
    return parser, why


async def _wait(host: str) -> None:
    last = _LAST_HIT.get(host)
    now = _clock()
    if last is not None and now - last < PER_HOST_SECONDS:
        await _sleep(PER_HOST_SECONDS - (now - last))
    _LAST_HIT[host] = _clock()


# ------------------------------------------------------------------ fetch

def _blocked(url: str, why: str) -> Scraped:
    return Scraped(url, url, None, "", datetime.now(UTC), blocked=why)


MAX_HOPS = 5
_REDIRECTS = (301, 302, 303, 307, 308)


async def _hop_refused(url: str, ua: str, c: Any) -> str | None:
    """Every check a single request must pass, run again on every hop."""
    if (why := denied(url)) is not None:
        return why
    if urlsplit(url).scheme not in ("http", "https"):
        return "only http(s) is fetched"
    if (why := await _netguard.assert_public(url)) is not None:
        return why
    parser, why = await _robots_cached(c, url)
    if parser is None:
        return why or "robots.txt unreadable"
    if not parser.can_fetch(ua, url):
        return "robots.txt disallows it"
    return None


async def _get(url: str, ua: str) -> tuple[str, int, dict[str, str], str] | str:
    """(final url, status, headers, body) or the reason nothing was read.
    Redirects are followed by hand: deny list, public address, robots.txt and
    the per-host wait are checked before every hop. The body is streamed and
    never read past MAX_BYTES (counted after decompression)."""
    current = url
    async with httpx.AsyncClient(timeout=TIMEOUT, follow_redirects=False) as c:
        for _hop in range(MAX_HOPS + 1):
            if (why := await _hop_refused(current, ua, c)) is not None:
                return why if current == url else f"redirect to {current}: {why}"
            await _wait(_host(current))
            async with c.stream("GET", current, headers={"User-Agent": ua}) as r:
                location = (r.headers.get("location")
                            if r.status_code in _REDIRECTS else None)
                if location:
                    current = urljoin(current, location)
                    continue
                size = r.headers.get("content-length")
                if size and size.strip().isdigit() and int(size) > MAX_BYTES:
                    return f"page larger than {MAX_BYTES} bytes ({size}); not read"
                buf = bytearray()
                async for chunk in r.aiter_bytes():
                    buf += chunk
                    if len(buf) >= MAX_BYTES:
                        break
                charset = getattr(r, "charset_encoding", None) or "utf-8"
                try:
                    body = bytes(buf[:MAX_BYTES]).decode(charset, errors="replace")
                except LookupError:
                    body = bytes(buf[:MAX_BYTES]).decode("utf-8", errors="replace")
                headers = {k.lower(): v for k, v in dict(r.headers or {}).items()}
                return current, r.status_code, headers, body
    return f"more than {MAX_HOPS} redirects; not fetched"


async def fetch(url: str, *, agent: str, client: str = "") -> Scraped:
    """One public page, politely. Never raises: a refusal is `blocked`."""
    url = (url or "").strip()
    if (why := denied(url)) is not None:
        return _blocked(url, why)
    if urlsplit(url).scheme not in ("http", "https"):
        return _blocked(url, "only http(s) is fetched")
    try:
        cached = await _cache_get(url)
    except Exception as exc:                            # noqa: BLE001
        log.debug("scrape cache unreadable: %s", explain(exc))
        cached = None
    if cached is not None:
        at = cached.get("fetched_at")
        if isinstance(at, datetime) and datetime.now(UTC) - at < CACHE_FOR:
            return Scraped(url, url, 200, cached.get("text") or "", at, cached=True)
    try:
        used = await _count_today()
    except Exception as exc:                            # noqa: BLE001
        return _blocked(url, f"the daily count cannot be read ({explain(exc)}); "
                             "nothing is fetched")
    if used >= daily_cap():
        return _blocked(url, f"daily scrape cap reached ({used}/{daily_cap()})")
    ua = user_agent()
    try:
        got = await _get(url, ua)
    except Exception as exc:                            # noqa: BLE001
        return _blocked(url, explain(exc))
    if isinstance(got, str):
        return _blocked(url, got)
    final, status, headers, body = got
    text = flatten(body)
    now = datetime.now(UTC)
    try:
        await _log(url, agent, client, status, len(body))
        if status == 200:
            await _cache_put(url, text, now)
    except Exception as exc:                            # noqa: BLE001
        log.warning("scrape log not written: %s", explain(exc))
    return Scraped(url, final, status, text, now, meta={"headers": headers})


# ------------------------------------------------------------------ search

async def search(query: str, *, agent: str, limit: int = 10
                 ) -> tuple[list[dict], str | None]:
    """Free providers only. ([], reason) when none is configured."""
    key = os.getenv("BRAVE_API_KEY")
    if key:
        return await _brave(query, limit, key)
    cse_key, cse_cx = os.getenv("GOOGLE_CSE_KEY"), os.getenv("GOOGLE_CSE_CX")
    if cse_key and cse_cx:
        return await _cse(query, limit, cse_key, cse_cx)
    return [], "no free search provider"


async def _brave(query: str, limit: int, key: str) -> tuple[list[dict], str | None]:
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT) as c:
            r = await c.get("https://api.search.brave.com/res/v1/web/search",
                            headers={"X-Subscription-Token": key,
                                     "Accept": "application/json"},
                            params={"q": query, "count": min(20, limit)})
            r.raise_for_status()
            hits = r.json().get("web", {}).get("results", [])[:limit]
    except Exception as exc:                            # noqa: BLE001
        return [], redact(explain(exc))
    return [{"title": h.get("title", ""), "url": h.get("url", ""),
             "snippet": h.get("description", "")} for h in hits], None


async def _cse(query: str, limit: int, key: str, cx: str
               ) -> tuple[list[dict], str | None]:
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT) as c:
            r = await c.get("https://www.googleapis.com/customsearch/v1",
                            params={"key": key, "cx": cx, "q": query,
                                    "num": min(10, limit)})
            r.raise_for_status()
            items = r.json().get("items", [])[:limit]
    except Exception as exc:                            # noqa: BLE001
        return [], redact(explain(exc))
    return [{"title": i.get("title", ""), "url": i.get("link", ""),
             "snippet": i.get("snippet", "")} for i in items], None
