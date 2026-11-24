"""Audits, keywords and ranks (six agents phase 10). Tier 1, our own rows.

`audit_site` fetches the live site the way `livecheck` does (public web
only, the site's own host) and runs the launch-check items over it. A home
page that cannot be read is an audit with status `unknown`: nothing was
measured, so nothing is OK. `keyword_research` picks phrases from the
site's own text in code; search volume has no free source, so it is "not
measured". `rank_snapshot` asks the free search provider (`_scrape.search`:
Brave or Google CSE, never Perplexity); without one each row is "not
measured" with the reason, which is not UNKNOWN and never a rank."""
from __future__ import annotations

import asyncio
import os
import re
from collections import Counter
from datetime import UTC, datetime
from itertools import pairwise
from urllib.parse import urlsplit

from ...kernel.contracts import ActionResult, FailureClass, Health
from .. import _scrape
from . import store

MAX_KEYWORDS = 5
_WORD = re.compile(r"[a-z][a-z'-]{2,}")
_STOP = frozenset({
    "the", "and", "for", "you", "your", "our", "with", "are", "from", "that", "this",
    "all", "can", "will", "we're", "have", "has", "not", "but", "more", "about",
    "contact", "home", "menu", "privacy", "policy", "terms", "call", "today", "now",
    "open", "page", "site", "website", "click", "here", "read", "us", "get"})


def _refused(why: str) -> ActionResult:
    return ActionResult(False, why, failure_class=FailureClass.VALIDATION)


def health_of(status: str) -> Health:
    return {"ok": Health.OK, "issues": Health.DEGRADED}.get(status, Health.UNKNOWN)


def _host_ok(domain: str):  # type: ignore[no-untyped-def]
    domain = domain.lower().removeprefix("www.")
    return lambda host: (host or "").lower().removeprefix("www.") == domain


def url_of(site: dict) -> str:
    return f"https://{site['domain']}/"


# ------------------------------------------------------------------- audit

async def audit_site_dry(slug: str) -> ActionResult:
    site = await store.site(str(slug))
    if site is None or not site.get("domain"):
        return _refused(f"no site with a domain: {slug}")
    return ActionResult(True, f"would read {url_of(site)} (up to 10 pages, GET only) "
                              "and store the audit")


async def audit_site(slug: str) -> ActionResult:
    from ..agency import launchcheck, livecheck
    from ..agency.sitekit import INDEX
    site = await store.site(str(slug))
    if site is None or not site.get("domain"):
        return _refused(f"no site with a domain: {slug}")
    url = url_of(site)
    live = await livecheck.fetch_site(url, allowed_host=_host_ok(str(site["domain"])))
    now = datetime.now(UTC)
    if INDEX not in live.pages:
        why = "; ".join(live.errors) or "the home page could not be read"
        summary = (f"UNKNOWN: {url} could not be read ({why}); nothing was measured "
                   f"(tried {now:%Y-%m-%d %H:%M} UTC)")
        aid = await store.insert_audit(str(slug), url, "unknown", summary, {})
        return ActionResult(True, summary, evidence={"audit_id": aid, "status": "unknown"},
                            rollback_token=str(aid))
    report = await asyncio.to_thread(launchcheck.live_audit, live, None)
    status = "ok" if report.ready else "issues"
    summary = f"{url}: {report.summary()}"
    aid = await store.insert_audit(str(slug), url, status, summary, report.as_dict())
    return ActionResult(True, summary, evidence={"audit_id": aid, "status": status,
                                                 "read_at": report.read_at.isoformat()},
                        rollback_token=str(aid))


async def withdraw_audit(token: str) -> ActionResult:
    if not str(token).isdigit():
        return _refused("not an audit id")
    await store.withdraw_audit(int(token))
    return ActionResult(True, f"audit {token} withdrawn")


# ---------------------------------------------------------------- keywords

def phrases(text: str, limit: int = MAX_KEYWORDS) -> list[str]:
    """Two-word phrases the page repeats, most frequent first (code only)."""
    words = [w for w in _WORD.findall((text or "").lower()) if w not in _STOP]
    pairs = Counter(f"{a} {b}" for a, b in pairwise(words) if a != b)
    return [p for p, n in pairs.most_common(limit * 3) if n >= 2][:limit]


async def keyword_research_dry(slug: str) -> ActionResult:
    site = await store.site(str(slug))
    if site is None or not site.get("domain"):
        return _refused(f"no site with a domain: {slug}")
    return ActionResult(True, f"would read {url_of(site)} and keep up to "
                              f"{MAX_KEYWORDS} phrases (volume: not measured)")


async def keyword_research(slug: str) -> ActionResult:
    site = await store.site(str(slug))
    if site is None or not site.get("domain"):
        return _refused(f"no site with a domain: {slug}")
    page = await _scrape.fetch(url_of(site), agent="seo", client=str(slug))
    if not page.ok:
        return _refused(f"{url_of(site)} could not be read: "
                        f"{page.blocked or f'HTTP {page.status}'}")
    found = phrases(page.text)
    ids = await store.add_keywords(str(slug), found)
    return ActionResult(True, f"{len(ids)} new keyword(s) for {slug}: "
                              f"{', '.join(found) or 'none'}; search volume: not "
                              f"measured (no free source) (read "
                              f"{page.fetched_at:%Y-%m-%d %H:%M} UTC)",
                        evidence={"keywords": found, "ids": ids},
                        rollback_token=",".join(map(str, ids)) or "none")


async def withdraw_keywords(token: str) -> ActionResult:
    if token == "none":
        return ActionResult(True, "no keyword was added")
    ids = [int(i) for i in str(token).split(",") if i.isdigit()]
    if not ids:
        return _refused("not a list of keyword ids")
    await store.withdraw_keywords(ids)
    return ActionResult(True, f"{len(ids)} keyword(s) withdrawn")


# ------------------------------------------------------------------- ranks

def provider() -> str:
    if os.getenv("BRAVE_API_KEY"):
        return "brave"
    if os.getenv("GOOGLE_CSE_KEY") and os.getenv("GOOGLE_CSE_CX"):
        return "google_cse"
    return "none"


async def rank_snapshot_dry(slug: str) -> ActionResult:
    kws = await store.keywords(str(slug), MAX_KEYWORDS)
    if not kws:
        return _refused(f"no keywords for {slug} yet; seo.keyword_research first")
    return ActionResult(True, f"would search {len(kws)} keyword(s) with "
                              f"{provider()} (free providers only)")


async def rank_snapshot(slug: str) -> ActionResult:
    site = await store.site(str(slug))
    if site is None or not site.get("domain"):
        return _refused(f"no site with a domain: {slug}")
    kws = await store.keywords(str(slug), MAX_KEYWORDS)
    if not kws:
        return _refused(f"no keywords for {slug} yet; seo.keyword_research first")
    own = _host_ok(str(site["domain"]))
    ids: list[int] = []
    lines: list[str] = []
    for kw in kws:
        hits, why = await _scrape.search(kw, agent="seo", limit=10)
        now = datetime.now(UTC)
        if why is not None:
            reason = f"not measured: {why}"
            ids.append(await store.insert_rank(str(slug), kw, "none", None, None,
                                               False, reason))
            lines.append(f"{kw}: {reason}")
            continue
        pos = next((i for i, h in enumerate(hits, 1)
                    if own(urlsplit(str(h.get("url") or "")).hostname or "")), None)
        url = str(hits[pos - 1].get("url")) if pos else None
        ids.append(await store.insert_rank(str(slug), kw, provider(), pos, url, True,
                                           None))
        lines.append(f"{kw}: " + (f"#{pos}" if pos else f"not in the top {len(hits)}")
                     + f" (read {now:%Y-%m-%d %H:%M} UTC)")
    return ActionResult(True, f"{slug}: " + "; ".join(lines),
                        evidence={"ids": ids}, rollback_token=",".join(map(str, ids)))


async def withdraw_ranks(token: str) -> ActionResult:
    ids = [int(i) for i in str(token).split(",") if i.isdigit()]
    if not ids:
        return _refused("not a list of rank ids")
    await store.withdraw_ranks(ids)
    return ActionResult(True, f"{len(ids)} rank row(s) withdrawn")
