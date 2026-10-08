"""Fetch and search for modules. Never raises, never wraps: the caller labels
the source when it wraps, because 'web' is not a source.

`search()` prefers Brave (`BRAVE_API_KEY`); when that is unset and
`PERPLEXITY_API_KEY` is set instead, it falls back to Perplexity, through
`_perplexity.search_hits` -- the MCP `perplexity_search` tool, with its own
cap, privacy scrub and name rule (Task 50: Perplexity's chat-completions
endpoint is dead, `403 chat_completions_not_available`, so there is no
direct HTTP call to it left in this module). Perplexity does not return a
list of links the way Brave does -- it returns one answer plus the pages it
drew on -- so the shape is reconciled at that seam, not left for every
caller to special-case: each citation becomes an ordinary hit (title=url,
empty snippet), and the answer text rides along as the `snippet` on a
synthetic first hit (`url=""`) so a caller that only reads `hits[0]["url"]`
never mistakes it for a real page. The synthetic hit counts against
`limit` like any other -- the total is never more than `limit`, the same
contract Brave's path already keeps. Perplexity is a wrapped source like
any other -- an answer, not an oracle -- so `research.report` wraps that
first hit's snippet with its own label rather than treating it as report
text already written.

`fetch_text()` has one optional fallback (Phase 20): when `FIRECRAWL_API_KEY`
is set and a plain fetch SUCCEEDED but left fewer than
`FIRECRAWL_MIN_CHARS` characters of text -- the signature of a page that
draws itself with JavaScript -- the same URL is asked of Firecrawl's
`/v1/scrape` (markdown). Firecrawl is a fetch backend, not a research
oracle: its markdown is returned as the same plain `str` the plain fetch
returns, and the caller wraps it with its own source label like any other
page. A failed plain fetch is not retried through it, whole sites are
never crawled, and a failed fallback never turns the plain fetch's success
into a failure -- the short text comes back as it would have without the
key, and the redacted reason goes to the log. The request/response shape
(`{"url", "formats": ["markdown"]}` in, `{"success", "data": {"markdown"}}`
out) follows Firecrawl's published v1 API, assumed, not verified against a
live call."""
from __future__ import annotations

import logging
import os
import re

import httpx

from ._http import explain, post_json, redact
from ._netguard import get_public

log = logging.getLogger("atlas.web")

_TAGS = re.compile(r"<script.*?</script>|<style.*?</style>", re.DOTALL | re.IGNORECASE)
_ANY_TAG = re.compile(r"<[^>]+>")
_WS = re.compile(r"\s+")

FIRECRAWL_URL = "https://api.firecrawl.dev/v1/scrape"
FIRECRAWL_MIN_CHARS = 200


async def fetch_text(url: str, *, limit: int = 14000,
                     timeout: float = 25.0) -> tuple[str | None, str | None]:
    try:
        # Redirects by hand, each hop asked `assert_public` first: a page may
        # not steer a fetch into the local API or the mesh (see _netguard).
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=False) as c:
            r, refused = await get_public(c, url, headers={"User-Agent": "ATLAS/1.0"})
            if refused is not None:
                return None, refused
            r.raise_for_status()
    except Exception as exc:                            # noqa: BLE001
        return None, explain(exc)
    text = _flatten(r.text)
    text = await _firecrawl_fallback(url, text)
    return text[:limit], None


def _flatten(markup: str) -> str:
    return _WS.sub(" ", _ANY_TAG.sub(" ", _TAGS.sub(" ", markup or ""))).strip()


async def _firecrawl_fallback(url: str, text: str) -> str:
    """The one point `fetch_text` consults Firecrawl: only for a plain fetch
    that succeeded with too little text, and only with the key set. Returns
    the better of the two texts; never raises, never returns a reason --
    a fallback that failed leaves the plain result exactly as it was."""
    key = os.getenv("FIRECRAWL_API_KEY")
    if not key or len(text) >= FIRECRAWL_MIN_CHARS:
        return text
    if redact(url) != url:
        # The URL itself carries a credential (a token in the query, a
        # secret feed URL): it is never handed to a third party to fetch.
        return text
    scraped, reason = await _firecrawl_scrape(url, key)
    if reason is not None:
        log.warning("firecrawl fallback for a short page failed: %s", redact(reason))
        return text
    return scraped if scraped and len(scraped) > len(text) else text


async def _firecrawl_scrape(url: str, key: str) -> tuple[str | None, str | None]:
    """One page, as markdown. `post_json` (not raw `httpx`) so a failure is
    already run through `explain`/`redact`; every reason is redacted again
    here anyway, because Firecrawl's own error text is third-party text
    that could echo the request back, bearer key and all."""
    data, reason = await post_json(
        FIRECRAWL_URL,
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        json_body={"url": url, "formats": ["markdown"], "onlyMainContent": True},
        timeout=45,
    )
    if reason is not None:
        return None, redact(reason)
    if not isinstance(data, dict) or data.get("success") is False:
        error = data.get("error") if isinstance(data, dict) else None
        return None, redact(f"firecrawl refused the page: {error or 'no detail'}")
    body = data.get("data")
    markdown = body.get("markdown") if isinstance(body, dict) else None
    if not isinstance(markdown, str):
        return None, "firecrawl response had no markdown"
    return _flatten(markdown), None


async def search(query: str, limit: int = 5) -> tuple[list[dict], str | None]:
    key = os.getenv("BRAVE_API_KEY")
    if key:
        return await _search_brave(query, limit, key)
    pplx_key = os.getenv("PERPLEXITY_API_KEY")
    if pplx_key:
        return await _search_perplexity(query, limit, pplx_key)
    return [], "no search provider configured (BRAVE_API_KEY or PERPLEXITY_API_KEY)"


async def _search_brave(query: str, limit: int, key: str) -> tuple[list[dict], str | None]:
    try:
        async with httpx.AsyncClient(timeout=20) as c:
            r = await c.get("https://api.search.brave.com/res/v1/web/search",
                            headers={"X-Subscription-Token": key,
                                     "Accept": "application/json"},
                            params={"q": query, "count": limit})
            r.raise_for_status()
            hits = r.json().get("web", {}).get("results", [])[:limit]
    except Exception as exc:                            # noqa: BLE001
        return [], explain(exc)
    return [{"title": h.get("title", ""), "url": h.get("url", ""),
             "snippet": h.get("description", "")} for h in hits], None


async def _search_perplexity(query: str, limit: int, key: str) -> tuple[list[dict], str | None]:
    """Perplexity as a search backend, through `_perplexity.search_hits`:
    the MCP path with the monthly cap, the privacy scrub and the name rule.
    No fallback (Task 50: the MCP server is the only door). `key` is read
    there from the environment; it is accepted here only so the seam's
    signature stays as Task 36 shaped it."""
    from ._perplexity import search_hits
    return await search_hits(query, limit)
