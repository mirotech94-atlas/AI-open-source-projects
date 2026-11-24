"""The Sites shift: daily, 08:00 New York (scheduler `sites_shift`).

`read_state()` is the lane in one dict -- pure reads, no executor call:
leads waiting on a demo, built sites not yet published, published sites
not yet pitched, the publish/outreach cards waiting on the owner, and each
deployed site's health with its read time (UNKNOWN when nothing observed
it, never OK -- invariant 7).

`build_missing()` (plan 2026-10-30, B5) runs only under
`AGENCY_AUTO_BUILD=on`: for up to `AUTO_BUILD_PER_DAY` waiting leads it
executes `agency.mockup` (when there is none) and `agency.build_site`, both
Tier 1 through the executor, then RAISES the Tier 2 `agency.publish_vercel`
card for each site it built. It never publishes and never emails: the
owner email card follows only after the owner approves the publish
(`lead_followup.after_deploys`). A hold from the executor ends the run.
The job is claimed once a day, so the per-run cap is the daily cap."""
from __future__ import annotations

import logging
import os
from typing import Any

from ...kernel.state import fetch_all

log = logging.getLogger("atlas.agency.sites_shift")

AUTO_BUILD_PER_DAY = 3
CARD_ACTIONS = ("agency.publish_vercel", "agency.send_outreach")

QUERIES: dict[str, str] = {
    # sales_stage needs_demo, or a mockup made, and no site row yet.
    "needs_demo": """
        SELECT l.id, l.name, l.status, l.sales_stage,
               EXISTS (SELECT 1 FROM agency_mockups m WHERE m.lead_id = l.id)
                   AS has_mockup
        FROM agency_leads l
        WHERE (l.sales_stage = 'needs_demo' OR l.status = 'mocked')
          AND NOT EXISTS (SELECT 1 FROM agency_sites s WHERE s.lead_id = l.id)
        ORDER BY l.id LIMIT 50""",
    "built_unpublished": """
        SELECT slug, lead_id, kind, path FROM agency_sites
        WHERE deployed_at IS NULL AND path IS NOT NULL
        ORDER BY id LIMIT 50""",
    "published_unpitched": """
        SELECT s.slug, s.lead_id, s.domain, s.deployed_at FROM agency_sites s
        WHERE s.deployed_at IS NOT NULL
          AND NOT EXISTS (SELECT 1 FROM agency_outreach o
                           WHERE o.lead_id = s.lead_id AND o.status <> 'draft')
        ORDER BY s.deployed_at DESC LIMIT 50""",
    "cards_waiting": """
        SELECT id, action,
               coalesce(params->>'slug', params->>'lead_id') AS target, created_at
        FROM proposals
        WHERE status = 'pending' AND expires_at > now()
          AND action IN ('agency.publish_vercel', 'agency.send_outreach')
        ORDER BY created_at DESC LIMIT 50""",
    "deployed": """
        SELECT slug FROM agency_sites WHERE deployed_at IS NOT NULL
        ORDER BY deployed_at DESC LIMIT 100""",
    "health": """
        SELECT DISTINCT ON (c.name) c.name AS check, o.health, o.at
        FROM checks c JOIN observations o ON o.check_id = c.id
        WHERE c.name LIKE 'site\\_%'
        ORDER BY c.name, o.at DESC""",
}


def _plain(row: Any) -> dict:
    out = dict(row)
    for key, value in out.items():
        if hasattr(value, "isoformat"):
            out[key] = value.isoformat()
    return out


async def _read(name: str, unreadable: list[str]) -> list[dict] | None:
    try:
        return [dict(r) for r in await fetch_all(QUERIES[name])]
    except Exception as exc:                            # noqa: BLE001
        unreadable.append(f"{name}: {type(exc).__name__}")
        return None


async def read_state() -> dict[str, Any]:
    """The lane, read now. A bucket that cannot be read is None and named in
    `unreadable`, never an empty list."""
    unreadable: list[str] = []
    state: dict[str, Any] = {}
    for name in ("needs_demo", "built_unpublished", "published_unpitched"):
        rows = await _read(name, unreadable)
        state[name] = None if rows is None else [_plain(r) for r in rows]

    cards = await _read("cards_waiting", unreadable)
    if cards is None:
        state["cards_waiting"] = None
    else:
        seen: set[tuple] = set()
        state["cards_waiting"] = []
        for card in cards:
            key = (card.get("action"), str(card.get("target")))
            if key in seen:
                continue
            seen.add(key)
            state["cards_waiting"].append(_plain(card))

    deployed = await _read("deployed", unreadable)
    observed = await _read("health", unreadable)
    health: dict[str, dict] = {}
    for row in deployed or []:
        health[str(row["slug"])] = {"health": "unknown", "at": None}
    for row in observed or []:
        slug = str(row["check"]).removeprefix("site_")
        at = row.get("at")
        health[slug] = {"health": str(row.get("health") or "unknown").lower(),
                        "at": at.isoformat() if hasattr(at, "isoformat") else at}
    if observed is None:
        # The health read failed: every site is UNKNOWN, not OK.
        for slug in health:
            health[slug] = {"health": "unknown", "at": None}
    state["health"] = health
    state["unreadable"] = unreadable
    return state


def auto_build_on() -> bool:
    return os.getenv("AGENCY_AUTO_BUILD", "").strip().lower() == "on"


async def _demo_candidates(limit: int) -> list[dict]:
    rows = await fetch_all(QUERIES["needs_demo"])
    return [dict(r) for r in rows][:limit]


class _Held(Exception):
    pass


async def _run(action: str, params: dict[str, Any], why: str) -> Any:
    from ...kernel import executor, lead_followup
    got = await executor.execute(executor.ExecutionRequest(action, params, reason=why))
    if lead_followup._held(got):
        raise _Held(action)
    return got


async def build_missing() -> dict[str, Any]:
    """Build up to AUTO_BUILD_PER_DAY waiting demos and raise each publish
    card. Off unless AGENCY_AUTO_BUILD=on."""
    out: dict[str, Any] = {"built": 0, "cards": 0, "failed": [], "cards_held": [],
                           "held": None, "skipped": None}
    if not auto_build_on():
        out["skipped"] = "AGENCY_AUTO_BUILD is off"
        return out
    from ...kernel import approvals
    try:
        for lead in await _demo_candidates(AUTO_BUILD_PER_DAY):
            lead_id, name = int(lead["id"]), str(lead.get("name") or lead["id"])
            if not lead.get("has_mockup"):
                made = await _run("agency.mockup", {"lead_id": lead_id},
                                  f"a demo for {name}, which waits on one")
                if not getattr(made, "ok", False):
                    out["failed"].append(f"{lead_id}: mockup")
                    continue
            got = await _run("agency.build_site", {"lead_id": lead_id},
                             f"build {name}'s demo site from its mockup")
            slug = (getattr(got, "evidence", None) or {}).get("slug")
            if not getattr(got, "ok", False) or not slug:
                out["failed"].append(f"{lead_id}: build_site")
                continue
            out["built"] += 1
            try:
                await approvals.propose(
                    "agency.publish_vercel", {"slug": slug},
                    f"{name}'s demo is built; publish it on Vercel so the owner "
                    f"email can follow", dedup_key=f"publish_vercel:{slug}")
                out["cards"] += 1
            except Exception as exc:                    # noqa: BLE001
                # The QA gate or a parameter check refused the card: said,
                # not retried here; the site stays built and listed.
                out["cards_held"].append(f"{slug}: {type(exc).__name__}")
    except _Held as held:
        out["held"] = str(held)
        log.info("sites shift held at %s", held)
    return out


async def run() -> dict[str, Any]:
    """One shift: build what waits (if switched on), then read the lane."""
    built = await build_missing()
    state = await read_state()
    return {"build": built, **{k: (len(v) if isinstance(v, list) else v)
                               for k, v in state.items()
                               if k not in ("health", "unreadable")},
            "health": state["health"], "unreadable": state["unreadable"]}
