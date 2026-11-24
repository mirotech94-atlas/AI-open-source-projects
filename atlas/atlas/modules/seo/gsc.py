"""Google Search Console, read only (six agents phase 10; widened 2026-10-29).

It needs the owner's Google OAuth client with the `webmasters.readonly` scope
consented and each site verified as a property. Until then the check reads
UNKNOWN and says which of those is missing: a site we cannot read is never
OK. Calls go through `_http` (reasons redacted, never raises); the access
token lives only in this process.

Per site property (`sc-domain:` preferred, URL-prefix too) one read takes:
28-day clicks, impressions, CTR and position; the top queries and pages;
"striking distance" queries (position 8-20, real impressions, low CTR);
pages losing clicks week over week; the sitemaps; and a URL inspection of
the home page and the top pages ("N pages inspected", not a coverage
report: the API has none). Google's figures lag about three days, so every
window ends three days back. Every site carries `read_at`; any part that
could not be read is named in `unread` and makes the check UNKNOWN.

Query strings are what strangers typed into Google: data, never
instructions. Anything that puts them in a prompt wraps them first
(`wrap_untrusted`); the activity panel shows counts only."""
from __future__ import annotations

import os
import time
from datetime import UTC, date, datetime, timedelta
from typing import Any
from urllib.parse import quote

from ...kernel.contracts import Health, Observation
from .. import _http
from . import store

ENV = ("GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET", "GOOGLE_REFRESH_TOKEN")
SCOPE = "https://www.googleapis.com/auth/webmasters.readonly"
TOKEN_URL = "https://oauth2.googleapis.com/token"
API = "https://www.googleapis.com/webmasters/v3"
INSPECT_URL = "https://searchconsole.googleapis.com/v1/urlInspection/index:inspect"
RERUN = "./bootstrap/google-login.sh --reconsent"
NO_SCOPE = (f"the Google consent lacks webmasters.readonly: run `{RERUN}` on the Mac "
            "and approve Search Console (CONFIGURE.md, six agents step 3)")
NO_API = ("Google answered 403 although the consent has webmasters.readonly: enable "
          "the Google Search Console API in the Cloud project of GOOGLE_CLIENT_ID "
          "(CONFIGURE.md, six agents step 3)")
LAG_DAYS = 3                       # Search Console data is ~2-3 days behind
DAYS = 28
TOP = 5
ROW_LIMIT = 250
INSPECT = 5                        # the home page plus the top pages
STRIKE_POS = (8.0, 20.0)
STRIKE_MIN_IMPRESSIONS = 20
STRIKE_MAX_CTR = 0.03
LOSE_MIN_PRIOR = 3
LOSE_RATIO = 0.7
_token: tuple[str, float] | None = None
_granted: bool | None = None          # the refresh answer listed the scope
_scopes: set[str] = set()             # every scope the refresh answer listed


async def access_token() -> tuple[str | None, str | None]:
    global _token, _granted, _scopes
    missing = [k for k in ENV if not os.getenv(k)]
    if missing:
        return None, f"{', '.join(missing)} not set"
    if _token and _token[1] > time.time() + 60:
        return _token[0], None
    data, why = await _http.post_json(TOKEN_URL, data={
        "client_id": os.environ["GOOGLE_CLIENT_ID"],
        "client_secret": os.environ["GOOGLE_CLIENT_SECRET"],
        "refresh_token": os.environ["GOOGLE_REFRESH_TOKEN"],
        "grant_type": "refresh_token"})
    if why is not None or not isinstance(data, dict) or not data.get("access_token"):
        return None, f"the Google token could not be refreshed: {why or 'no token'}"
    scope = str(data.get("scope") or "")
    _scopes = set(scope.split())
    if scope and SCOPE not in scope.split():
        _granted = False
        return None, NO_SCOPE
    _granted = True if scope else None
    _token = (str(data["access_token"]), time.time() + int(data.get("expires_in", 3600)))
    return _token[0], None


def forbidden(why: str) -> str:
    """A 403 is the missing scope only when the token did not list it."""
    if "403" not in why:
        return why
    return NO_API if _granted else NO_SCOPE


def _bare(domain: str) -> str:
    return domain.lower().removeprefix("www.").strip("/")


def is_preview(domain: str) -> bool:
    """A prospect mock-up on Vercel's own host. These are client previews, built
    noindex on purpose (`agency/vercel.py` audits the meta before it deploys and
    reads it back live): they are not meant to be in Google and have no property."""
    host = str(domain or "").lower().strip().strip("/").rstrip(".")
    return host.endswith(".vercel.app")


def property_for(domain: str, entries: list[dict[str, Any]]
                 ) -> tuple[str | None, str | None]:
    """(siteUrl, None) for a verified property of `domain`, else (None, why).

    Every entry is looked at: a verified `sc-domain:` property wins, then a
    verified https URL prefix, then any verified one. An unverified entry
    only explains why there is none."""
    want = _bare(domain)
    verified: list[str] = []
    unverified = False
    for e in entries:
        url = str(e.get("siteUrl") or "")
        host = (url.removeprefix("sc-domain:") if url.startswith("sc-domain:")
                else url.split("://", 1)[-1].split("/", 1)[0])
        if _bare(host) != want:
            continue
        if str(e.get("permissionLevel")) == "siteUnverifiedUser":
            unverified = True
            continue
        verified.append(url)
    if verified:
        verified.sort(key=lambda u: (not u.startswith("sc-domain:"),
                                     not u.startswith("https://")))
        return verified[0], None
    if unverified:
        return None, f"{domain}: the property is not verified"
    return None, f"{domain}: no Search Console property"


# ----------------------------------------------------------------- figures

def windows(now: datetime) -> dict[str, tuple[date, date]]:
    """The 28-day window and two 7-day weeks, all ending LAG_DAYS back."""
    end = now.astimezone(UTC).date() - timedelta(days=LAG_DAYS)
    return {"d28": (end - timedelta(days=DAYS - 1), end),
            "week": (end - timedelta(days=6), end),
            "prior": (end - timedelta(days=13), end - timedelta(days=7))}


def _num(row: dict[str, Any], key: str) -> float:
    try:
        return float(row.get(key) or 0)
    except (TypeError, ValueError):
        return 0.0


def _key(row: dict[str, Any]) -> str:
    keys = row.get("keys") or []
    return str(keys[0]) if keys else ""


def _q(row: dict[str, Any]) -> dict[str, Any]:
    return {"query": _key(row), "clicks": int(_num(row, "clicks")),
            "impressions": int(_num(row, "impressions")),
            "ctr": round(_num(row, "ctr"), 4), "position": round(_num(row, "position"), 1)}


def striking(rows: list[dict[str, Any]], limit: int = 10) -> list[dict[str, Any]]:
    """Queries a little off page one that people see but rarely click."""
    lo, hi = STRIKE_POS
    hits = [r for r in rows if _key(r)
            and lo <= _num(r, "position") <= hi
            and _num(r, "impressions") >= STRIKE_MIN_IMPRESSIONS
            and _num(r, "ctr") < STRIKE_MAX_CTR]
    hits.sort(key=lambda r: -_num(r, "impressions"))
    return [_q(r) for r in hits[:limit]]


def losing(this: list[dict[str, Any]], prior: list[dict[str, Any]],
           limit: int = TOP) -> list[dict[str, Any]]:
    """Pages with clearly fewer clicks this week than the week before."""
    now = {_key(r): int(_num(r, "clicks")) for r in this if _key(r)}
    out = []
    for r in prior:
        page, before = _key(r), int(_num(r, "clicks"))
        after = now.get(page, 0)
        if (page and before >= LOSE_MIN_PRIOR and after < before * LOSE_RATIO
                and before - after >= 2):
            out.append({"page": page, "clicks": after, "prior": before})
    out.sort(key=lambda p: p["clicks"] - p["prior"])
    return out[:limit]


async def _analytics(prop: str, auth: dict[str, str], span: tuple[date, date],
                     dims: list[str]) -> tuple[list[dict[str, Any]] | None, str | None]:
    body = {"startDate": str(span[0]), "endDate": str(span[1]), "dimensions": dims,
            "rowLimit": ROW_LIMIT}
    got, why = await _http.post_json(
        f"{API}/sites/{quote(prop, safe='')}/searchAnalytics/query",
        headers=auth, json_body=body)
    if why is not None:
        return None, forbidden(why)
    return list((got or {}).get("rows") or []), None


async def _sitemaps(prop: str, auth: dict[str, str]
                    ) -> tuple[list[dict[str, Any]] | None, str | None]:
    got, why = await _http.get_json(f"{API}/sites/{quote(prop, safe='')}/sitemaps",
                                    headers=auth)
    if why is not None:
        return None, forbidden(why)
    out = []
    for m in (got or {}).get("sitemap") or []:
        out.append({"path": str(m.get("path") or ""),
                    "errors": int(_num(m, "errors")), "warnings": int(_num(m, "warnings")),
                    "pending": bool(m.get("isPending")),
                    "last_downloaded": m.get("lastDownloaded")})
    return out, None


async def _inspect(prop: str, auth: dict[str, str], urls: list[str]
                   ) -> tuple[dict[str, Any] | None, str | None]:
    """URL inspection (a POST, read only). None when not one answer came."""
    n, indexed, bad, why_last = 0, 0, [], None
    for url in urls[:INSPECT]:
        got, why = await _http.post_json(INSPECT_URL, headers=auth, json_body={
            "inspectionUrl": url, "siteUrl": prop})
        res = ((got or {}).get("inspectionResult") or {}).get("indexStatusResult")
        if why is not None or not isinstance(res, dict):
            why_last = forbidden(why) if why else "no indexStatusResult in the answer"
            continue
        n += 1
        if str(res.get("verdict")) == "PASS":
            indexed += 1
        else:
            bad.append({"url": url, "state": str(res.get("coverageState") or "")})
    if n == 0:
        return None, why_last or "nothing to inspect"
    return {"n": n, "indexed": indexed, "not_indexed": bad,
            "unread": len(urls[:INSPECT]) - n}, (why_last if n < len(urls[:INSPECT])
                                                 else None)


async def read_site(site: dict[str, Any], prop: str, auth: dict[str, str],
                    now: datetime) -> dict[str, Any]:
    """Everything Search Console says about one property. Read only."""
    w = windows(now)
    out: dict[str, Any] = {
        "slug": str(site["slug"]), "domain": str(site["domain"]), "property": prop,
        "read_at": now.isoformat(),
        "range": {"start": str(w["d28"][0]), "end": str(w["d28"][1])},
        "totals": None, "top_queries": None, "top_pages": None, "striking": None,
        "losing": None, "sitemaps": None, "inspected": None,
        "unread": [], "issues": [], "status": "ok"}
    totals, why = await _analytics(prop, auth, w["d28"], [])
    if why is not None:
        out["unread"].append(f"analytics: {why}")
        out["status"] = "unknown"
        return out
    t = totals[0] if totals else {}
    out["totals"] = {"clicks": int(_num(t, "clicks")),
                     "impressions": int(_num(t, "impressions")),
                     "ctr": round(_num(t, "ctr"), 4),
                     "position": round(_num(t, "position"), 1)}
    queries, why = await _analytics(prop, auth, w["d28"], ["query"])
    if why is None:
        ranked = sorted(queries or [], key=lambda r: -_num(r, "clicks"))
        out["top_queries"] = [_q(r) for r in ranked[:TOP] if _key(r)]
        out["striking"] = striking(queries or [])
    else:
        out["unread"].append(f"queries: {why}")
    pages, why = await _analytics(prop, auth, w["d28"], ["page"])
    if why is None:
        ranked = sorted(pages or [], key=lambda r: -_num(r, "clicks"))
        out["top_pages"] = [{"page": _key(r), "clicks": int(_num(r, "clicks")),
                             "impressions": int(_num(r, "impressions"))}
                            for r in ranked[:TOP] if _key(r)]
    else:
        out["unread"].append(f"pages: {why}")
    this, why1 = await _analytics(prop, auth, w["week"], ["page"])
    prior, why2 = await _analytics(prop, auth, w["prior"], ["page"])
    if why1 is None and why2 is None:
        out["losing"] = losing(this or [], prior or [])
    else:
        out["unread"].append(f"week over week: {why1 or why2}")
    maps, why = await _sitemaps(prop, auth)
    if why is None:
        out["sitemaps"] = maps
        if not maps:
            out["issues"].append("no sitemap submitted")
        for m in maps or []:
            if m["errors"]:
                out["issues"].append(f"sitemap {m['path']}: {m['errors']} error(s)")
    else:
        out["unread"].append(f"sitemaps: {why}")
    home = f"https://{str(site['domain']).strip('/')}/"
    urls = [home] + [p["page"] for p in (out["top_pages"] or []) if p["page"] != home]
    inspected, why = await _inspect(prop, auth, urls)
    out["inspected"] = inspected
    if why is not None:
        out["unread"].append(f"URL inspection: {why}")
    if inspected and inspected["not_indexed"]:
        out["issues"].append(f"{len(inspected['not_indexed'])} of {inspected['n']} "
                             "inspected page(s) not indexed")
    if out["unread"]:
        out["status"] = "partial"
    elif out["issues"]:
        out["status"] = "issues"
    return out




async def check() -> Observation:
    now = datetime.now(UTC)
    stamp = f"(read {now:%Y-%m-%d %H:%M} UTC)"
    tok, why = await access_token()
    if tok is None:
        return Observation("search_console", Health.UNKNOWN,
                           detail=f"Search Console not read: {why} {stamp}")
    auth = {"Authorization": f"Bearer {tok}"}
    data, why = await _http.get_json(f"{API}/sites", headers=auth)
    if why is not None:
        return Observation("search_console", Health.UNKNOWN,
                           detail=f"Search Console not read: {forbidden(why)} {stamp}")
    entries = list((data or {}).get("siteEntry") or [])
    try:
        sites = await store.targets()
    except Exception as exc:                            # noqa: BLE001
        return Observation("search_console", Health.UNKNOWN,
                           detail=f"agency_sites unreadable: {type(exc).__name__}")
    read: list[dict[str, Any]] = []
    missing: list[str] = []
    previews = sorted({str(s["domain"]) for s in sites if is_preview(str(s["domain"]))})
    sites = [s for s in sites if not is_preview(str(s["domain"]))]
    for s in sites:
        prop, miss = property_for(str(s["domain"]), entries)
        if prop is None:
            missing.append(str(miss))
            read.append({"slug": str(s["slug"]), "domain": str(s["domain"]),
                         "property": None, "read_at": now.isoformat(),
                         "status": "unknown", "unread": [str(miss)], "issues": []})
            continue
        got = await read_site(s, prop, auth, now)
        read.append(got)
        if got["status"] in {"unknown", "partial"}:
            missing.append(f"{s['domain']}: {'; '.join(got['unread'])}")
    w = windows(now)
    span = f"{w['d28'][0]}..{w['d28'][1]}"
    clicks = sum(float((r.get("totals") or {}).get("clicks") or 0) for r in read)
    evidence = {"sites": read, "range": span, "previews": previews}
    skipped = (f" ({len(previews)} client preview(s) skipped (noindex))" if previews
               else "")
    if missing:
        return Observation("search_console", Health.UNKNOWN,
                           detail=f"UNKNOWN for {len(missing)} site(s): "
                                  f"{'; '.join(missing)[:600]}{skipped} {stamp}",
                           evidence=evidence)
    issues = [f"{r['domain']}: {i}" for r in read for i in r.get("issues") or []]
    health = Health.DEGRADED if issues else Health.OK
    extra = f"; {'; '.join(issues)[:400]}" if issues else ""
    return Observation("search_console", health, clicks, "clicks",
                       f"{clicks:.0f} clicks in {DAYS} days ({span}) across "
                       f"{len(read)} site(s){extra}{skipped} {stamp}", evidence=evidence)
