"""The SEO panel beside the Agency map (`GET /api/agency/seo`), read only.

From the seo checks' last observations (search_console, core_web_vitals)
and the stored reports: per site, Search Console clicks, impressions, CTR,
position and how many striking-distance queries and losing pages there are;
the mobile and desktop speed scores and regressions; the issues, all built
by our own code. Every figure carries its read time; a reading that does
not exist is null and named in `unread`, never 0. Query strings and page
text are not shipped: counts, scores, names and times only."""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from .._http import explain
from . import store, vitals

CHECKS = ("search_console", vitals.CHECK)
WHY_CHARS = 300


def _iso(value: Any) -> Any:
    return value.isoformat() if isinstance(value, datetime) else value


def _check(obs: dict[str, Any] | None) -> dict[str, Any] | None:
    if obs is None:
        return None
    health = str(obs.get("health") or "unknown").lower()
    return {"health": health, "at": _iso(obs.get("at")),
            "why": None if health == "ok" else str(obs.get("detail") or "")[:WHY_CHARS]}


def _sites(obs: dict[str, Any] | None) -> list[dict[str, Any]]:
    ev = (obs or {}).get("evidence")
    # A retired host's old reading (the old shop host) is never shown again.
    return [s for s in (ev or {}).get("sites") or []
            if isinstance(s, dict) and not store.retired(s.get("slug"), s.get("domain"))] \
        if isinstance(ev, dict) else []


def _n(value: Any) -> int | None:
    return len(value) if isinstance(value, list) else None


def summarize(gsc: dict[str, Any] | None, cwv: dict[str, Any] | None,
              reports: list[dict[str, Any]]) -> dict[str, Any]:
    by: dict[str, dict[str, Any]] = {}

    def site(s: dict[str, Any]) -> dict[str, Any]:
        slug = str(s.get("slug"))
        return by.setdefault(slug, {"slug": slug, "domain": str(s.get("domain") or ""),
                                    "search": None, "vitals": None, "issues": [],
                                    "unread": []})

    for s in _sites(gsc):
        out = site(s)
        t = s.get("totals")
        if isinstance(t, dict):
            out["search"] = {"clicks": t.get("clicks"), "impressions": t.get("impressions"),
                             "ctr": t.get("ctr"), "position": t.get("position"),
                             "striking": _n(s.get("striking")),
                             "losing": _n(s.get("losing")),
                             "read_at": s.get("read_at"), "range": s.get("range")}
        out["unread"] += [f"Search Console {u}"[:WHY_CHARS] for u in s.get("unread") or []]
        out["issues"] += [str(i)[:WHY_CHARS] for i in s.get("issues") or []]
    for s in _sites(cwv):
        out = site(s)
        m, d = s.get("mobile") or {}, s.get("desktop") or {}
        if m or d:
            out["vitals"] = {"mobile": m.get("score"), "desktop": d.get("score"),
                             "mobile_lcp_ms": (m.get("lab") or {}).get("lcp_ms"),
                             "regressions": _n(s.get("regressions")),
                             "read_at": s.get("read_at")}
        out["unread"] += [f"PageSpeed {u}"[:WHY_CHARS] for u in s.get("unread") or []]
        out["issues"] += [f"speed regression: {r}"[:WHY_CHARS]
                          for r in s.get("regressions") or []]
        out["issues"] += [str(i)[:WHY_CHARS] for i in s.get("issues") or []]
    return {"checks": {"search_console": _check(gsc), vitals.CHECK: _check(cwv)},
            "sites": list(by.values()),
            "reports": [{"slug": str(r.get("slug")), "kind": str(r.get("kind")),
                         "week": str(r.get("week")), "at": _iso(r.get("at"))}
                        for r in reports]}


async def build() -> dict[str, Any]:
    errors: dict[str, str] = {}
    got: dict[str, dict[str, Any] | None] = {}
    for name in CHECKS:
        try:
            got[name] = await store.latest_observation(name)
        except Exception as exc:  # noqa: BLE001
            got[name] = None
            errors[name] = explain(exc)
    try:
        reports = await store.recent_reports(10)
    except Exception as exc:  # noqa: BLE001
        reports = []
        errors["reports"] = explain(exc)
    out = summarize(got["search_console"], got[vitals.CHECK], reports)
    return {"read_at": datetime.now(UTC).isoformat(), **out, "errors": errors}
