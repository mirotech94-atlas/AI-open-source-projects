"""agency.sites_view -- the client-site registry as read now.

One read for both readers: `/api/agency/sites` (the Client Sites tab) and
the agency line of the daily brief. Each row: kind, hostname, the release
live on this box, and the newest `site_<slug>` reading with the time it was
read -- or UNKNOWN with the reason (invariant 7). Nothing here writes.
Each row also lists its client-site documents (`sitedocs`) with the Claude
Docs link the coding agent reported, or `docs: None` and the reason when
they could not be read -- never an empty list in its place.

`Module.brief()` is synchronous, so the brief reads `_CACHE`, which
`refresh()` fills from `AgencyModule.signals()` each cycle. Every brief
line carries its own read time; before the first refresh it says so.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime
from typing import Any

from .._http import explain
from . import _store, demo_host, hosting, sitedocs
from .deploy import published_domain

log = logging.getLogger("atlas.agency.sites_view")

CHECK_EVERY_S = 1800          # `_make_site_check`: every 30 min

_CACHE: dict[str, Any] = {"state": "unread"}


def reset() -> None:
    """For tests."""
    _CACHE.clear()
    _CACHE["state"] = "unread"


def _iso(value: Any) -> str | None:
    if isinstance(value, datetime):
        return value.isoformat()
    return None if value is None else str(value)


def _hostname(row: dict) -> str | None:
    kind = row.get("kind") or "built"
    if kind == "demo":
        return demo_host.hostname_of(row)
    if kind == "app":
        return row.get("domain") or None
    return published_domain(row)


def _live(row: dict) -> tuple[str | None, str | None]:
    """Blocking. (live release stamp, why it could not be read)."""
    kind = row.get("kind") or "built"
    slug = str(row.get("slug"))
    if kind == "app" or hosting.slug_problem(slug):
        return None, None
    root = demo_host.PUBLISH_ROOT if kind == "demo" else hosting.PUBLISH_ROOT
    try:
        return hosting.live_release(root, slug), None
    except (ValueError, OSError) as exc:
        return None, str(exc)[:200]


def _doc(d: dict) -> dict[str, Any]:
    url = d.get("artifact_url")
    return {"id": d.get("id"), "kind": d.get("kind"), "title": d.get("title"),
            "status": d.get("status"), "handoff_status": d.get("handoff_status"),
            "artifact_url": url if url and sitedocs.artifact_url_ok(url) else None,
            "reported_at": _iso(d.get("reported_at")),
            "created_at": _iso(d.get("created_at"))}


async def _docs(slugs: list[str]) -> tuple[dict[str, list] | None, str | None]:
    try:
        found = await sitedocs.docs_by_site(slugs)
    except Exception as exc:  # noqa: BLE001
        return None, f"the documents could not be read: {explain(exc)}"[:300]
    return {s: [_doc(d) for d in found.get(s, [])] for s in slugs}, None


async def _one(row: dict) -> dict[str, Any]:
    slug = str(row.get("slug"))
    live, live_problem = await asyncio.to_thread(_live, row)
    health, detail, read_at = "unknown", "", None
    try:
        reading = await _store.site_health(slug)
    except Exception as exc:  # noqa: BLE001
        detail = f"the health reading could not be read: {explain(exc)}"
    else:
        if reading is None:
            detail = ("no reading yet: the site check starts after the next api "
                      "restart")
        else:
            health = str(reading.get("health") or "unknown").lower()
            detail = str(reading.get("detail") or "")
            read_at = _iso(reading.get("at"))
    return {
        "slug": slug, "kind": row.get("kind") or "built",
        "hostname": _hostname(row), "source": row.get("source"),
        "managed": bool(row.get("managed", True)),
        "deployed_at": _iso(row.get("deployed_at")),
        "live_release": live, "live_problem": live_problem,
        "health": health, "health_detail": detail[:300], "health_read_at": read_at,
    }


async def overview() -> dict[str, Any]:
    """Raises when the registry cannot be read: the caller says so."""
    rows = await _store.list_sites()
    sites = [await _one(dict(r)) for r in rows]
    docs, problem = await _docs([s["slug"] for s in sites])
    for s in sites:
        s["docs"] = None if docs is None else docs.get(s["slug"], [])
        s["docs_problem"] = problem
    return {"sites": sites, "read_at": datetime.now(UTC).isoformat(),
            "check_every_s": CHECK_EVERY_S}


async def refresh() -> None:
    """Fill the brief's cache. Never raises."""
    try:
        view = await overview()
    except Exception as exc:  # noqa: BLE001
        log.warning("client sites: registry unreadable: %s", explain(exc))
        _CACHE.clear()
        _CACHE.update(state="unreadable", at=datetime.now(UTC))
        return
    _CACHE.clear()
    _CACHE.update(state="read", view=view)


def _clock(iso: str | None) -> str:
    if not iso:
        return "never read"
    try:
        at = datetime.fromisoformat(iso).astimezone(UTC)
    except ValueError:
        return "read at an unknown time"
    return f"read {at:%Y-%m-%d %H:%M} UTC"


def brief_lines() -> list[str]:
    state = _CACHE.get("state")
    if state == "unread":
        return ["client sites: not read yet"]
    if state == "unreadable":
        return [f"client sites: registry unreadable ({_clock(_iso(_CACHE.get('at')))})"]
    view = _CACHE.get("view") or {}
    sites = [s for s in view.get("sites", []) if s.get("managed")]
    if not sites:
        return [f"client sites: none registered ({_clock(view.get('read_at'))})"]
    out = []
    for s in sites:
        name = s.get("hostname") or s.get("slug")
        health = "UNKNOWN" if s["health"] == "unknown" else s["health"]
        live = s.get("live_release") or "nothing live"
        out.append(f"client site {name} ({s['kind']}): {health}, "
                   f"{_clock(s.get('health_read_at'))}; release {live}")
    return out
