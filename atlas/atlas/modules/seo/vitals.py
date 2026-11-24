"""Core Web Vitals from PageSpeed Insights, read only (2026-10-29).

Every site with a domain, plus the store (ACADEMY_PUBLIC_ORIGIN), is
read twice a day-ish (the check runs every 24 h): mobile and desktop. Lab
figures come from Lighthouse (score, LCP, CLS, TBT, FCP); field figures from
CrUX (`loadingExperience`: LCP, INP, CLS) when Google has enough real
visits, which small sites often lack -- then the field block is None, "not
measured", never 0. INP only exists as a field figure.

Regressions are measured against the last reading this check recorded: the
check's own observations are the snapshots (so it works while ATLAS is
watch-only and the executor writes nothing). Thresholds are wide on purpose
so lab noise does not flap: a score drop of 10+, LCP up 25% and 500 ms+, CLS
up 0.05+ past 0.1. A site PageSpeed cannot read is UNKNOWN, never OK.

The key (`GOOGLE_PAGESPEED_API_KEY`, or `PAGESPEED_API_KEY`) is a query
parameter to Google only; `_http` redacts it from every reason."""
from __future__ import annotations

import asyncio
import os
from datetime import UTC, datetime
from typing import Any

from ...kernel.contracts import Health, Observation
from .. import _http, store_origin
from . import store

URL = "https://www.googleapis.com/pagespeedonline/v5/runPagespeed"
KEY_ENV = ("GOOGLE_PAGESPEED_API_KEY", "PAGESPEED_API_KEY")
STRATEGIES = ("mobile", "desktop")
TIMEOUT = 75.0
PARALLEL = 3
CHECK = "core_web_vitals"
SCORE_DROP = 10
LCP_RATIO, LCP_MIN_MS = 1.25, 500
CLS_UP, CLS_LINE = 0.05, 0.1
POOR_SCORE, POOR_LCP_MS, POOR_CLS = 50, 4000, 0.25


def key() -> str | None:
    for name in KEY_ENV:
        if value := os.getenv(name, "").strip():
            return value
    return None


def _num(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def parse(data: Any) -> dict[str, Any] | None:
    """Score, lab and field figures, or None when the shape is not PSI's."""
    try:
        lh = data["lighthouseResult"]
        score = _num(lh["categories"]["performance"]["score"])
        audits = lh["audits"]
    except (KeyError, TypeError):
        return None
    if score is None:
        return None

    def lab(name: str) -> float | None:
        return _num((audits.get(name) or {}).get("numericValue"))

    lcp, cls, tbt, fcp = (lab("largest-contentful-paint"),
                          lab("cumulative-layout-shift"), lab("total-blocking-time"),
                          lab("first-contentful-paint"))
    out: dict[str, Any] = {
        "score": round(score * 100),
        "lab": {"lcp_ms": None if lcp is None else round(lcp),
                "cls": None if cls is None else round(cls, 3),
                "tbt_ms": None if tbt is None else round(tbt),
                "fcp_ms": None if fcp is None else round(fcp)},
        "field": None}
    metrics = ((data.get("loadingExperience") or {}).get("metrics")) or {}

    def field(name: str) -> float | None:
        return _num((metrics.get(name) or {}).get("percentile"))

    f_lcp, f_inp, f_cls = (field("LARGEST_CONTENTFUL_PAINT_MS"),
                           field("INTERACTION_TO_NEXT_PAINT"),
                           field("CUMULATIVE_LAYOUT_SHIFT_SCORE"))
    if any(v is not None for v in (f_lcp, f_inp, f_cls)):
        out["field"] = {
            "lcp_ms": None if f_lcp is None else round(f_lcp),
            "inp_ms": None if f_inp is None else round(f_inp),
            "cls": None if f_cls is None else round(f_cls / 100, 3),
            "category": (data.get("loadingExperience") or {}).get("overall_category")}
    return out


async def measure(url: str, strategy: str) -> tuple[dict[str, Any] | None, str | None]:
    params = {"url": url, "strategy": strategy, "category": "performance"}
    if k := key():
        params["key"] = k
    # detail: Google's own reason (2026-10-29: "HTTP 400" alone hid that
    # Lighthouse could not load a host that no longer exists).
    data, why = await _http.get_json(URL, params=params, timeout=TIMEOUT, detail=True)
    if why is not None:
        hint = "" if key() else " (no GOOGLE_PAGESPEED_API_KEY: the keyless quota)"
        return None, f"{why}{hint}"
    got = parse(data)
    if got is None:
        return None, "PageSpeed answered without a performance score"
    return got, None


def regressions(before: dict[str, Any] | None, now: dict[str, Any] | None) -> list[str]:
    """Clear drops from `before` to `now`, in words; [] when either is missing."""
    if not before or not now:
        return []
    out: list[str] = []
    b, n = before.get("score"), now.get("score")
    if isinstance(b, (int, float)) and isinstance(n, (int, float)) and b - n >= SCORE_DROP:
        out.append(f"score {b:.0f} -> {n:.0f}")
    bl, nl = before.get("lab") or {}, now.get("lab") or {}
    b, n = bl.get("lcp_ms"), nl.get("lcp_ms")
    if (isinstance(b, (int, float)) and isinstance(n, (int, float)) and b > 0
            and n >= b * LCP_RATIO and n - b >= LCP_MIN_MS):
        out.append(f"LCP {b / 1000:.1f}s -> {n / 1000:.1f}s")
    b, n = bl.get("cls"), nl.get("cls")
    if (isinstance(b, (int, float)) and isinstance(n, (int, float))
            and n - b >= CLS_UP and n > CLS_LINE):
        out.append(f"CLS {b:.2f} -> {n:.2f}")
    return out


def poor(now: dict[str, Any] | None) -> list[str]:
    if not now:
        return []
    out: list[str] = []
    lab = now.get("lab") or {}
    if isinstance(now.get("score"), (int, float)) and now["score"] < POOR_SCORE:
        out.append(f"score {now['score']:.0f}")
    if isinstance(lab.get("lcp_ms"), (int, float)) and lab["lcp_ms"] > POOR_LCP_MS:
        out.append(f"LCP {lab['lcp_ms'] / 1000:.1f}s")
    if isinstance(lab.get("cls"), (int, float)) and lab["cls"] > POOR_CLS:
        out.append(f"CLS {lab['cls']:.2f}")
    return out


def _prior_for(slug: str, history: list[Any]) -> dict[str, Any] | None:
    """The newest earlier reading of `slug` that measured anything."""
    for ev in history:
        if not isinstance(ev, dict):
            continue
        for s in ev.get("sites") or []:
            if (isinstance(s, dict) and s.get("slug") == slug
                    and any(s.get(k) for k in STRATEGIES)):
                return s
    return None


async def read_site(site: dict[str, Any], history: list[Any],
                    gate: asyncio.Semaphore) -> dict[str, Any]:
    url = f"https://{str(site['domain']).strip('/')}/"
    if str(site["slug"]) == "academy-store":     # the store is under /academy/
        url += f"{store_origin.STORE_PATH}/"
    out: dict[str, Any] = {"slug": str(site["slug"]), "domain": str(site["domain"]),
                           "url": url, "unread": [], "issues": [], "regressions": []}

    async def one(strategy: str) -> tuple[str, dict[str, Any] | None, str | None]:
        async with gate:
            got, why = await measure(url, strategy)
        return strategy, got, why

    for strategy, got, why in await asyncio.gather(*(one(s) for s in STRATEGIES)):
        out[strategy] = got
        if why is not None:
            out["unread"].append(f"{strategy}: {why}")
    out["read_at"] = datetime.now(UTC).isoformat()
    prior = _prior_for(out["slug"], history)
    out["compared_with"] = (prior or {}).get("read_at")
    for strategy in STRATEGIES:
        for r in regressions((prior or {}).get(strategy), out.get(strategy)):
            out["regressions"].append(f"{strategy} {r}")
        if bad := poor(out.get(strategy)):
            out["issues"].append(f"{strategy} poor: {', '.join(bad)}")
    return out


async def check() -> Observation:
    now = datetime.now(UTC)
    stamp = f"(read {now:%Y-%m-%d %H:%M} UTC)"
    try:
        sites = await store.targets()
    except Exception as exc:                            # noqa: BLE001
        return Observation(CHECK, Health.UNKNOWN,
                           detail=f"agency_sites unreadable: {type(exc).__name__}")
    try:
        history = await store.recent_evidence(CHECK)
    except Exception:                                   # noqa: BLE001
        history = []                                    # no comparison, said below
    gate = asyncio.Semaphore(PARALLEL)
    read = list(await asyncio.gather(*(read_site(s, history, gate) for s in sites)))
    evidence = {"sites": read}
    unread = [f"{r['domain']}: {'; '.join(r['unread'])}" for r in read if r["unread"]]
    if unread:
        return Observation(CHECK, Health.UNKNOWN,
                           detail=f"PageSpeed UNKNOWN for {len(unread)} site(s): "
                                  f"{'; '.join(unread)[:600]} {stamp}",
                           evidence=evidence)
    flagged = [f"{r['domain']}: {x}" for r in read for x in r["regressions"] + r["issues"]]
    mobile = [r["mobile"]["score"] for r in read if r.get("mobile")]
    worst = float(min(mobile)) if mobile else None
    compared = sum(1 for r in read if r.get("compared_with"))
    base = (f"mobile score {worst:.0f} at worst across {len(read)} site(s); "
            f"{compared} compared with the last reading")
    if flagged:
        return Observation(CHECK, Health.DEGRADED, worst, "score",
                           f"{base}; {'; '.join(flagged)[:500]} {stamp}",
                           evidence=evidence)
    return Observation(CHECK, Health.OK, worst, "score", f"{base} {stamp}",
                       evidence=evidence)
