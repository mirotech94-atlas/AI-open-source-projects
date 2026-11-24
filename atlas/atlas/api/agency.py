"""The Agency room: ATLAS meets his agents.

`GET /api/agency/meeting` returns a meeting built from the database
(`kernel/meeting.py`): ATLAS opens with the day's theme, every roster agent
-- by its persona (`kernel/personas.py`) -- answers the day's question from
its own record, ATLAS follows up with anyone who failed or is blocked, and
agents that share a module ask each other; ATLAS closes with how many are
ready, blocked or idle. Every sentence is assembled from `agent_runs`,
`agents.readiness`, `agents.next_shift_at` and the roster's tools, so the
room can never say an agent did something the record does not show
(invariant 6).

Two ways to read it:
  * default -- today's meeting as the daily step stored it
    (`agency_meetings`, with `built_at`); its lines may be a model's
    rewording, each one kept only because it passed the grounding check and
    marked `polished`. No stored meeting yet today: a live build instead.
  * `?live=1` -- rebuilt from the records now. Never polished: the page
    polls this every minute, and a model is not asked to reword a meeting
    sixty times an hour.

Read-only. Nothing here calls `invoke`, the executor or a model; the live
build reads `agent_runs` and the in-memory roster, the stored read reads
`agency_meetings`, nothing else. A read that fails is a 503 that says why,
never a meeting of zeros. Every figure carries the time it was read
(`read_at`) and, for a stored meeting, when it was built (`built_at`).

The page is `/agency` in the SPA (`ui/src/pages/Agency.tsx`); this router
owns only `/api/agency/*`. Unrelated to the `agency` business module
(`atlas/modules/agency`, the Leads page) -- that agent simply sits at the
table like every other.
"""
from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query

from ..kernel import agent_activity
from ..kernel import meeting as meeting_module
from ..kernel.meeting import EXCERPT_CHARS, failure_kind, first_sentence
from ..modules._http import explain
from . import auth

log = logging.getLogger("atlas.agency")

router = APIRouter(prefix="/api/agency")
SignedIn = Annotated[auth.Session, Depends(auth.require)]

__all__ = ["EXCERPT_CHARS", "failure_kind", "first_sentence", "router"]


@router.get("/meeting")
async def meeting(_s: SignedIn,
                  live: Annotated[bool, Query()] = False) -> dict[str, Any]:
    served_at = datetime.now(UTC).isoformat()
    if not live:
        today = datetime.now().astimezone().date()
        try:
            stored = await meeting_module.stored_for(today)
        except Exception as exc:  # noqa: BLE001
            why = explain(exc)
            log.warning("the agency meeting could not read agency_meetings: %s", why)
            raise HTTPException(
                503, f"I could not read today's stored meeting, so there is "
                     f"nothing to report — {why}") from None
        if stored is not None:
            stored["served_at"] = served_at
            return stored

    try:
        built = await meeting_module.build(polish=False)
    except Exception as exc:  # noqa: BLE001
        why = explain(exc)
        log.warning("the agency meeting could not read agent_runs: %s", why)
        raise HTTPException(
            503, f"I could not read the agents' runs, so there is no meeting "
                 f"to report — {why}") from None
    built["served_at"] = served_at
    return built


@router.get("/activity")
async def activity(_s: SignedIn) -> dict[str, Any]:
    """Each roster agent's now / yesterday / issues (`kernel/agent_activity.py`).

    Read-only. A read that fails inside is `errors[...]` and the figures it
    fed are null (UNKNOWN on the page); only a failure of the whole build is
    a 503 that says why, never a panel of zeros."""
    served_at = datetime.now(UTC).isoformat()
    try:
        built = await agent_activity.activity()
    except Exception as exc:  # noqa: BLE001
        why = explain(exc)
        log.warning("the agent activity panel could not be built: %s", why)
        raise HTTPException(
            503, f"I could not read the agents' activity — {why}") from None
    return {"served_at": served_at, **built}


@router.get("/autopilot")
async def autopilot_line(_s: SignedIn) -> dict[str, Any]:
    """The Command Center's one Autopilot line (`modules/qa/autopilot.py`):
    last cycle time, fixed N, waiting on Miro N. Read-only; an unreadable
    figure is null and named in `unread` (UNKNOWN on the page), never 0."""
    from ..modules.qa import autopilot
    return await autopilot.read_summary()


@router.get("/seo")
async def seo_panel(_s: SignedIn) -> dict[str, Any]:
    """The SEO panel beside the Agency map (`modules/seo/panel.py`): per site,
    Search Console and Core Web Vitals figures from the checks' last
    readings, with their read times, and the stored reports. Read-only. A
    read that fails inside is `errors[...]` and its figures null (UNKNOWN on
    the page); only a failure of the whole build is a 503 that says why."""
    from ..modules.seo import panel
    served_at = datetime.now(UTC).isoformat()
    try:
        built = await panel.build()
    except Exception as exc:  # noqa: BLE001
        why = explain(exc)
        log.warning("the SEO panel could not be built: %s", why)
        raise HTTPException(503, f"I could not read the SEO readings — {why}") from None
    return {"served_at": served_at, **built}


@router.get("/brain")
async def brain(_s: SignedIn) -> dict[str, Any]:
    """The Brain (`kernel/brain.py`, `kernel/departments.py`): ATLAS's real
    agents, jobs and actions grouped into departments with a head, each
    department's last 7 days from the record (% of executions that needed
    no card, cards waiting on you, seats that ran), the command strip
    (paused scopes, read-backs) and every seat's SOP generated from code.

    Read-only: it reads `actions_log`, `proposals`, `agent_runs`,
    `schedule_runs` and `pauses`, and the in-memory registries -- nothing
    else. A read that fails is reported in `errors` with its reason and its
    figures are null (unknown), never zero."""
    from ..kernel import brain as brain_module
    try:
        built = await brain_module.build()
    except Exception as exc:  # noqa: BLE001
        why = explain(exc)
        log.warning("the brain could not be built: %s", why)
        raise HTTPException(503, f"I could not build the brain view — {why}") from None
    built["served_at"] = datetime.now(UTC).isoformat()
    return built


@router.get("/missions")
async def missions(_s: SignedIn,
                   limit: Annotated[int, Query(ge=1, le=100)] = 20) -> dict[str, Any]:
    """The jobs handed to agents (`kernel/missions.py`), newest first, each
    with its steps and, once finished, its report. Read-only: it reads the
    `missions` table and nothing else. Unreadable is a 503, never an empty
    list that reads as "nothing was asked"."""
    from ..kernel import missions as missions_module
    from ..kernel.personas import persona_for
    try:
        rows = await missions_module.recent(limit)
    except Exception as exc:  # noqa: BLE001
        why = explain(exc)
        log.warning("the missions could not be read: %s", why)
        raise HTTPException(503, f"I could not read the missions — {why}") from None
    out = []
    for row in rows:
        steps = row.get("steps") or []
        state = row.get("state") or {}
        out.append({
            "id": row["id"], "agent": row["agent"],
            "persona": persona_for(missions_module.current_agent(row["agent"])).name,
            "recipe": row["recipe"], "request": row["request"],
            "status": row["status"], "phase": row.get("phase") or "",
            "slots": row.get("slots") or {},
            "area": state.get("area"), "waiting": state.get("waiting"),
            "steps": len(steps),
            "cards": [s["proposal_id"] for s in steps if s.get("proposal_id")],
            "last_step": steps[-1] if steps else None,
            "report": row.get("report"),
            "created_at": _iso(row.get("created_at")),
            "finished_at": _iso(row.get("finished_at")),
        })
    return {"missions": out, "read_at": datetime.now(UTC).isoformat()}


def _iso(value: Any) -> Any:
    return value.isoformat() if isinstance(value, datetime) else value


# ------------------------------------------------------------ client sites
# The Client Sites tab (`ui/src/pages/Sites.tsx`): the agency module's site
# registry (`agency_sites`), read now. Read-only; nothing here deploys.

@router.get("/sites")
async def sites(_s: SignedIn) -> dict[str, Any]:
    """Every registry row: kind, hostname, the release live on this box and
    the newest `site_<slug>` reading with its read time, or UNKNOWN with
    the reason. An unreadable registry is a 503, never an empty list."""
    from ..modules.agency import sites_view
    try:
        return await sites_view.overview()
    except Exception as exc:  # noqa: BLE001
        why = explain(exc)
        log.warning("the client sites could not be read: %s", why)
        raise HTTPException(503, f"I could not read the client sites — {why}") from None


@router.get("/sites/{slug}/events")
async def site_events(_s: SignedIn, slug: str) -> dict[str, Any]:
    """The last 50 deploy outcomes of one site, newest first."""
    from ..modules.agency import _store, hosting
    if hosting.slug_problem(slug):
        raise HTTPException(422, "not a site slug")
    try:
        rows = await _store.site_events(slug, 50)
    except Exception as exc:  # noqa: BLE001
        why = explain(exc)
        log.warning("the site events could not be read: %s", why)
        raise HTTPException(503, f"I could not read the site's events — {why}") from None
    events = [{"at": _iso(r.get("at")), "event": r.get("event"),
               "release": r.get("release"), "detail": r.get("detail") or ""}
              for r in rows]
    return {"slug": slug, "events": events, "read_at": datetime.now(UTC).isoformat()}
