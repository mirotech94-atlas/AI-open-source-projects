"""agency.audit — the qualifying signal (AGENCY.md 2.2).

A business with no website, or a visibly bad one, is a lead with an obvious,
checkable pitch: "your site takes 11 seconds on a phone and Google flags it
as insecure." All eight checks run against public data, fetched politely —
one GET per site, `follow_redirects=True`, a 15s timeout and an honest
User-Agent, never `_web.fetch_text` because the raw HTML, elapsed time, size
and scheme are all needed and that helper throws the first three away.

`audit_html` is the pure half: given a page already fetched, it derives
everything except the two checks that need a further network call
(`dead_links`, and `slow` when `GOOGLE_PAGESPEED_API_KEY` is set) — kept
separate so the scoring logic is testable without a socket.

Audits are cached 30 days per lead (`agency_audits`), so re-running
`agency.audit` on a lead already checked this month reads the cache instead
of hitting the business's site — and Google's PageSpeed quota — again.

For Task 3 (outreach): a `Finding.detail` and `Audit.measured` are not
trusted text. `dead_links` carries `<a href>` values read straight off the
audited business's own page, and nothing here sanitises them beyond the
same-origin filter in `_dead_links` — they are exactly the "ingested byte"
invariant 4 means. Anything from an `Audit` that reaches an outreach draft's
prompt (via `features()`'s evidence or otherwise) goes through
`wrap_untrusted()` first, the same as email or a buyer message; it is never
treated as an instruction.

Reputation (Task 42): `reputation(lead)` asks Perplexity's search what public
reviews and local news say about the business — one call per lead, cached
30 days in `agency_leads.meta["reputation"]` with the time it was read — and
`agency.audit` carries it in its evidence. It is untrusted third-party text
(wrapped as `perplexity`) with its Sources, background for Miro, never a
figure ATLAS measured. The question carries the lead's public name and
address only; its phone number is never put in it, and the scrub would strip
one anyway. The name rule is off for this one caller: the subject is a
third party by construction, and a plumber called "Guardian" is not his
Guardian.
"""
from __future__ import annotations

import logging
import os
import re
import time
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import urljoin, urlparse

import httpx

from ...kernel.contracts import ActionResult
from .. import _perplexity
from .._http import explain, get_json
from . import _store

log = logging.getLogger("atlas.agency")

USER_AGENT = "ATLAS-audit/1.0"
FETCH_TIMEOUT = 15.0
_MAX_DEAD_LINK_PROBES = 10
_STALE_YEARS = 3

_VIEWPORT = re.compile(r'<meta[^>]+name=["\']viewport["\']', re.IGNORECASE)
_COPYRIGHT_YEAR = re.compile(r"(?:©|copyright)\D{0,20}(\d{4})", re.IGNORECASE)
_HREF = re.compile(r'<a\b[^>]*\bhref\s*=\s*["\']([^"\']+)["\']', re.IGNORECASE)


@dataclass(frozen=True)
class Finding:
    check: str
    severity: int
    detail: str


@dataclass(frozen=True)
class Audit:
    url: str | None
    findings: list[Finding]
    measured: dict[str, Any]
    severity: int


def _is_slow(elapsed_s: float, size_bytes: int) -> bool:
    return elapsed_s > 5 or size_bytes > 3 * 1024 * 1024


def _stale_copyright_year(html: str) -> int | None:
    years = [int(y) for y in _COPYRIGHT_YEAR.findall(html)]
    if not years:
        return None
    newest = max(years)
    cutoff = datetime.now(UTC).year - _STALE_YEARS
    return newest if newest <= cutoff else None


def _ancient_markup(html: str) -> bool:
    low = html.lower()
    first = low[:2000]
    if any(tag in low for tag in ("<marquee", "<font ", "<frameset")):
        return True
    if ".swf" in low:
        return True
    # A table used for page layout, not tabular data, is the 2009 tell; the
    # combination in the first slice of the document is the cheap proxy for
    # "the whole page is one big <table>".
    return "<table" in first and 'width="100%"' in first


def audit_html(html: str, url: str, *, elapsed_s: float, size_bytes: int,
               https: bool) -> list[Finding]:
    """Pure: no network, no clock beyond what the caller measured. Checks
    2, 3, 4 (heuristic), 5, 7, 8 from AGENCY.md's table. 1 (no website) and
    6 (dead links) need information this function is never given."""
    findings: list[Finding] = []
    low = html.lower()

    if not https:
        findings.append(Finding(
            "no_https", 2, f"{url} is served over plain http, not https — "
            "Chrome shows their customers \"Not secure\""))

    if not _VIEWPORT.search(html):
        findings.append(Finding(
            "not_mobile", 2,
            'no <meta name="viewport"> tag — the page is not responsive, '
            "and most of their traffic is phones"))

    if _is_slow(elapsed_s, size_bytes):
        findings.append(Finding(
            "slow", 2,
            f"page took {elapsed_s:.1f}s to load and weighs "
            f"{size_bytes / 1_000_000:.1f}MB — slow enough to lose bookings"))

    if "application/ld+json" not in low and "itemtype=" not in low:
        findings.append(Finding(
            "no_schema", 1,
            "no schema.org structured data (JSON-LD or itemtype) — "
            "invisible in local search results"))

    year = _stale_copyright_year(html)
    if year is not None:
        findings.append(Finding(
            "stale_copyright", 1,
            f"copyright year reads {year}, "
            f"{datetime.now(UTC).year - year} years stale"))

    if _ancient_markup(html):
        findings.append(Finding(
            "ancient_markup", 2,
            "uses markup that stopped being current around 2009 "
            "(marquee, font tags, frameset, a layout table, or Flash)"))

    return findings


async def _fetch(url: str) -> tuple[str | None, float, int, bool, str | None]:
    """One polite GET. Returns (html, elapsed_s, size_bytes, https, reason);
    never raises. `_http.explain` keeps a redirect-loop or a timeout
    secret-free, though nothing here carries a credential in the first
    place — the convention is followed anyway, because a future header
    might."""
    https = url.lower().startswith("https://")
    start = time.monotonic()
    from .._netguard import get_public
    try:
        # Public addresses only, every redirect hop included: a prospect's
        # "website" is someone else's input (audit 2026-09-28).
        async with httpx.AsyncClient(follow_redirects=False,
                                     timeout=FETCH_TIMEOUT) as client:
            response, why = await get_public(client, url,
                                             headers={"User-Agent": USER_AGENT})
            if response is None:
                return None, time.monotonic() - start, 0, https, f"not fetched: {why}"
            response.raise_for_status()
    except Exception as exc:  # noqa: BLE001
        return None, time.monotonic() - start, 0, https, explain(exc)
    elapsed = time.monotonic() - start
    return response.text, elapsed, len(response.content), https, None


async def _dead_links(html: str, base_url: str) -> list[str]:
    """Up to 10 internal links, HEAD-probed. A link that times out or errors
    is skipped, not counted — that is a network hiccup, not evidence the
    page is dead. Only an explicit 404 is."""
    base_netloc = urlparse(base_url).netloc
    candidates: list[str] = []
    for href in _HREF.findall(html):
        href = href.strip()
        if not href or href.startswith(("#", "mailto:", "tel:", "javascript:")):
            continue
        absolute = urljoin(base_url, href)
        if urlparse(absolute).netloc != base_netloc:
            continue                       # external link, not this site's rot
        if absolute not in candidates:
            candidates.append(absolute)
        if len(candidates) >= _MAX_DEAD_LINK_PROBES:
            break

    dead: list[str] = []
    async with httpx.AsyncClient(follow_redirects=True, timeout=10) as client:
        for link in candidates:
            try:
                response = await client.head(link, headers={"User-Agent": USER_AGENT})
            except Exception as exc:  # noqa: BLE001
                # A timeout or connection error is a network hiccup, not
                # evidence of rot; log it at debug and move on rather than
                # counting it as the 404 this check is actually looking for.
                log.debug("dead-link probe failed for %s: %s", link, explain(exc))
                continue
            if response.status_code == 404:
                dead.append(link)
    return dead


async def _pagespeed(url: str, key: str) -> tuple[float | None, str | None]:
    data, reason = await get_json(
        "https://www.googleapis.com/pagespeedonline/v5/runPagespeed",
        params={"url": url, "strategy": "mobile", "key": key}, timeout=30)
    if reason is not None:
        return None, reason
    try:
        score = data["lighthouseResult"]["categories"]["performance"]["score"]
    except (KeyError, TypeError):
        return None, ("pagespeed response is missing "
                      "lighthouseResult.categories.performance.score — the "
                      "assumed shape is wrong")
    if isinstance(score, bool) or not isinstance(score, (int, float)):
        return None, f"pagespeed score was {type(score).__name__}, not numeric"
    return float(score) * 100, None


async def audit_site(url: str | None) -> Audit:
    """Fetch (once, politely) and run all eight checks. Not cached itself —
    `audit_lead` is the cached entry point that wraps this."""
    if not url:
        return Audit(None, [Finding(
            "no_website", 3, "no website is listed for this business — "
            "the easiest conversation you'll have")], {}, 3)

    html, elapsed_s, size_bytes, https, reason = await _fetch(url)

    measured: dict[str, Any] = {}
    if reason is not None and url.lower().startswith("https://"):
        fallback_url = "http://" + url.split("://", 1)[1]
        html2, elapsed2, size2, _https2, reason2 = await _fetch(fallback_url)
        if reason2 is None:
            html, elapsed_s, size_bytes, https, reason = html2, elapsed2, size2, False, None
            measured["fetched_via"] = fallback_url

    if reason is not None or html is None:
        measured["fetch_error"] = reason or "empty response"
        finding = Finding("unreachable", 3,
                          f"could not load {url}: {measured['fetch_error']}")
        return Audit(url, [finding], measured, finding.severity)

    measured["elapsed_s"] = round(elapsed_s, 3)
    measured["size_bytes"] = size_bytes
    findings = audit_html(html, url, elapsed_s=elapsed_s, size_bytes=size_bytes,
                          https=https)

    dead = await _dead_links(html, url)
    if dead:
        findings.append(Finding(
            "dead_links", 1,
            f"{len(dead)} internal link(s) return 404: {', '.join(dead[:5])}"))
        measured["dead_links"] = dead

    psi_key = os.getenv("GOOGLE_PAGESPEED_API_KEY")
    if psi_key:
        score, psi_reason = await _pagespeed(url, psi_key)
        findings = [f for f in findings if f.check != "slow"]
        if psi_reason is None:
            measured["slow_source"] = "pagespeed"
            measured["pagespeed_score"] = score
            if score is not None and score < 50:
                findings.append(Finding(
                    "slow", 2,
                    f"PageSpeed mobile performance score {score:.0f}/100 — "
                    "slow enough to lose bookings"))
        else:
            measured["slow_source"] = "heuristic (pagespeed unavailable)"
            measured["pagespeed_error"] = psi_reason
            if _is_slow(elapsed_s, size_bytes):
                findings.append(Finding(
                    "slow", 2,
                    f"page took {elapsed_s:.1f}s to load and weighs "
                    f"{size_bytes / 1_000_000:.1f}MB — slow enough to lose "
                    "bookings"))
    else:
        measured["slow_source"] = "heuristic"

    severity = sum(f.severity for f in findings)
    return Audit(url, findings, measured, severity)


async def _cached_or_fresh(
    lead_id: int, url: str | None,
) -> tuple[Audit, dict[str, Any] | None, int | None]:
    """The cache lookup, plus enough to build a rollback token that never
    points at a row this call did not write.

    Returns `(audit, cached_row, fresh_id)` — exactly one of `cached_row` /
    `fresh_id` is not None. A rollback that withdraws is undoing *this
    execution's write*; on a cache hit there isn't one, and handing back the
    cached row's id let `agency.audit.rollback` withdraw an audit some
    earlier, unrelated run had written. `audit_lead`'s signature is fixed by
    the brief and has no room for either value, so this stays private.
    """
    cached = await _store.recent_audit(lead_id)
    if cached is not None:
        findings = [Finding(**f) for f in _store.parse_json(cached.get("findings"), [])]
        measured = _store.parse_json(cached.get("measured"), {})
        severity = int(cached.get("severity") or 0)
        return Audit(cached.get("url"), findings, measured, severity), cached, None

    result = await audit_site(url)
    fresh_id = await _store.insert_audit(
        lead_id, result.url, [asdict(f) for f in result.findings],
        result.severity, result.measured)
    return result, None, fresh_id


async def audit_lead(lead_id: int, url: str | None) -> Audit:
    """The cached entry point: reads `agency_audits` for this lead within
    30 days before spending a fetch (and a PageSpeed call) on it again."""
    result, _cached, _fresh_id = await _cached_or_fresh(lead_id, url)
    return result


# ------------------------------------------------------------ reputation

REPUTATION_DAYS = 30


def reputation_question(lead: dict[str, Any]) -> str:
    name = str(lead.get("name") or "").strip()
    where = str(lead.get("address") or "").strip()
    return (f"What do public reviews and local news say about the business "
            f"{name}" + (f" at {where}" if where else "") + "?")


def _fresh(entry: Any) -> bool:
    if not isinstance(entry, dict) or not entry.get("read_at"):
        return False
    try:
        read_at = datetime.fromisoformat(str(entry["read_at"]))
    except ValueError:
        return False
    if read_at.tzinfo is None:
        read_at = read_at.replace(tzinfo=UTC)
    return datetime.now(UTC) - read_at < timedelta(days=REPUTATION_DAYS)


async def reputation(lead: dict[str, Any]) -> tuple[dict | None, str | None]:
    """The lead's public reputation from Perplexity, served from the 30-day
    cache when it is there. `(entry, None)` or `(None, reason)`."""
    meta = _store.parse_json(lead.get("meta"), {})
    cached = meta.get("reputation") if isinstance(meta, dict) else None
    if _fresh(cached):
        return cached, None
    if not lead.get("name"):
        return None, "the lead has no name to look up"
    found, reason = await _perplexity.call(
        "perplexity_search", reputation_question(lead), caller="agency",
        guard_names=False)
    if found is None:
        return None, reason
    entry = {"answer": found.text, "sources": list(found.citations),
             "read_at": found.read_at.isoformat(), "via": found.via}
    await _store.set_lead_meta(int(lead["id"]), "reputation", entry)
    return entry, None


# --------------------------------------------------------------- the action

async def audit_action(lead_id: int) -> ActionResult:
    lead = await _store.get_lead(lead_id)
    if lead is None:
        return ActionResult(False, f"no such lead: {lead_id}")

    result, cached, fresh_id = await _cached_or_fresh(lead_id, lead.get("website"))
    await _store.set_lead_status(lead_id, "audited")
    evidence = {"findings": [asdict(f) for f in result.findings],
               "severity": result.severity, "url": result.url,
               # The row the read-back reads: this run's own write, or the
               # cached row it served (the rollback token stays empty then).
               "audit_id": cached.get("id") if cached is not None else fresh_id}
    rep, why = await reputation(lead)
    if rep is not None:
        evidence["reputation"] = rep
    elif why and _perplexity.configured():
        evidence["reputation_unread"] = why

    if cached is not None:
        # Served from the 30-day cache: this call wrote nothing, so there is
        # nothing for a rollback to undo. An empty token, not the cached
        # row's id — see `_cached_or_fresh`.
        return ActionResult(
            True,
            f"audit of lead {lead_id}: served from cache (audit "
            f"#{cached.get('id')} from {cached.get('at')}); nothing to undo. "
            f"{len(result.findings)} finding(s), severity {result.severity}",
            evidence=evidence,
            rollback_token="",
        )

    return ActionResult(
        True,
        f"audit of lead {lead_id}: {len(result.findings)} finding(s), "
        f"severity {result.severity}",
        evidence=evidence,
        rollback_token=str(fresh_id) if fresh_id is not None else "",
    )


async def audit_dry(lead_id: int) -> ActionResult:
    lead = await _store.get_lead(lead_id)
    if lead is None:
        return ActionResult(False, f"no such lead: {lead_id}")
    url = lead.get("website")
    if url:
        return ActionResult(True, f"would fetch and audit {url} for lead {lead_id}")
    return ActionResult(True, f"would record 'no website' for lead {lead_id}")


async def audit_rollback(token: str) -> ActionResult:
    if not token:
        return ActionResult(True, "nothing was written for this run; nothing to roll back")
    await _store.withdraw_audit(int(token))
    return ActionResult(True, f"withdrew audit {token}")
