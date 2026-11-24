"""What ATLAS is doing right now, for the portal's HUD (`/hud`).

the owner, 2026-11-10: a live view of ATLAS at work -- the tasks it has, the
surfaces it touches (GitLab, the Mac, the QA browser, NotebookLM...), the
Tier 2 cards waiting on him, and what is running. `GET /api/hud` serves
`snapshot()`; the page re-reads it on a stream nudge and on a slow poll.

Where each figure comes from (nothing here is a second bookkeeping):
  * tasks -- the `tasks` table (kernel/tasks.py): source, route, status, time.
    Never the request's words (the owner: no request text on screen), never the
    outcome;
  * waiting -- `proposals`: pending cards and cards that lapsed;
  * surfaces -- `actions_log` over the last 30 min (event name and subject
    only, never the payload) and the surface's checks. A surface no catalog
    action touches is UNKNOWN "not instrumented"; the names of read tools seen
    in use (`note_tool`, fed by `tools.invoke`) are shown as "seen", never as
    a state;
  * jobs -- `schedule_runs`, the background jobs, the Mac's queue.

Rules kept here, from CLAUDE.md:
  * every section carries `read_at` (invariant 6);
  * a read that fails makes its figures `None` and names itself in `unread`,
    never 0 -- the page draws that UNKNOWN (invariant 7);
  * no table text is shown: an item is a count, an action name, a status or a
    time, never a card's reason, a job's params or a mission's request.

Read-only. Nothing here calls the executor, a tool, an approval or a model;
the HUD has no way to approve or run anything (invariants 1 to 3).
"""
from __future__ import annotations

import json
import logging
import os
from collections import deque
from collections.abc import Awaitable
from datetime import UTC, datetime, timedelta
from typing import Any

from ..modules._http import explain

log = logging.getLogger("atlas.hud")

EVENT_WINDOW = timedelta(minutes=30)
TASK_WINDOW = timedelta(hours=24)
TOOL_WINDOW = timedelta(minutes=10)
MAX_TASKS = 12
MAX_CARDS = 8
MAX_TOOLS_SEEN = 40

HOLD_STATES = {
    "execute.watch_only": "watch_only", "execute.refused": "refused",
    "execute.paused": "paused", "execute.rate_limited": "rate_limited",
    "execute.qa_held": "qa_held"}
SLACK_LABEL = "approved by you in Slack"

# (key, tile label, module prefixes). Static, in code: the owner confirms the list.
SURFACES: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    ("gitlab", "GitLab (localhost:8929)", ("gitlab",)),
    ("mac", "Mac", ("mac",)),
    ("qa", "QA browser", ("qa", "browser")),
    ("notebooklm", "NotebookLM", ("academy",)),
    ("mail", "Email", ("mail",)),
    ("calendar", "Calendar", ("calendar",)),
    ("messaging", "Messaging", ("messaging", "comms", "messages")),
)

# Read tools seen in use, newest last: names only, never params or results.
_TOOLS_SEEN: deque[tuple[str, str, datetime]] = deque(maxlen=MAX_TOOLS_SEEN)


def watch_only() -> bool:
    """The switch `executor.watch_only` reads (same variable, same default),
    repeated here because the HUD may not import the executor. A test pins
    the two together."""
    return os.getenv("ATLAS_WATCH_ONLY", "true").strip().lower() not in (
        "0", "false", "no", "off")


def note_tool(agent: str, tool: str, phase: str) -> None:
    """A read tool started or ended. Names only; the end is not recorded."""
    if phase == "start":
        _TOOLS_SEEN.append((str(agent)[:40], str(tool)[:60], datetime.now(UTC)))


def recent_tools(now: datetime) -> list[dict[str, Any]]:
    return [{"agent": a, "tool": t, "at": at}
            for a, t, at in _TOOLS_SEEN if now - at <= TOOL_WINDOW]


def _iso(value: Any) -> str | None:
    return value.isoformat() if isinstance(value, datetime) else None


def _module_of(subject: str) -> str:
    return subject.split(".", 1)[0] if subject else ""


# ------------------------------------------------------------------ reads

async def _read_tasks(since: datetime) -> list[Any]:
    from .state import fetch_all
    return await fetch_all(
        """SELECT id, at, source, route, status, cards, done_at FROM tasks
            WHERE at >= $1 OR status IN ('open', 'held')
            ORDER BY id DESC LIMIT $2""", since, MAX_TASKS)


async def _read_missions() -> list[Any]:
    from .state import fetch_all
    return await fetch_all(
        """SELECT id, agent, recipe, status, phase, updated_at, deadline,
                  jsonb_array_length(steps) AS steps_done
             FROM missions WHERE status IN ('running', 'waiting', 'needs_input')
            ORDER BY id DESC LIMIT 12""")


async def _read_pending(since: datetime) -> list[Any]:
    from .state import fetch_all
    return await fetch_all(
        """SELECT id, action, tier, created_at, expires_at, status FROM proposals
            WHERE status = 'pending' OR created_at >= $1
            ORDER BY id DESC LIMIT 80""", since)


async def _read_events(since: datetime) -> list[Any]:
    """Event name and subject, plus two tiny captured tokens (the tier digit
    and the channel word) for the "approved in Slack" label. Never the
    payload itself."""
    from .state import fetch_all
    return await fetch_all(
        """SELECT event, subject, at,
                  substring(payload from '"tier": ?([0-9])') AS tier,
                  substring(payload from '"channel": ?"([a-z_]+)"') AS channel
             FROM actions_log
            WHERE at >= $1 AND (event LIKE 'execute.%' OR event = 'intent.accepted')
            ORDER BY id DESC LIMIT 300""", since)


async def _read_schedule() -> list[Any]:
    from .state import fetch_all
    return await fetch_all(
        """SELECT job, claimed_at FROM schedule_runs WHERE status = 'running'
            ORDER BY claimed_at DESC LIMIT 20""")


async def _read_mac_jobs() -> list[Any]:
    from .state import fetch_all
    return await fetch_all(
        """SELECT op, status, created_at FROM mac_jobs
            WHERE status IN ('queued', 'claimed', 'unknown')
            ORDER BY id DESC LIMIT 10""")


async def _read_mac_seen() -> datetime | None:
    from .state import fetch_one
    row = await fetch_one("SELECT max(last_seen) AS seen FROM mac_agent")
    return row["seen"] if row else None


async def _read_checks() -> list[Any]:
    from .state import fetch_all
    return await fetch_all(
        """SELECT p.name AS property, c.name AS check_name, c.interval_s,
                  o.health, o.at AS observed_at
             FROM checks c
             JOIN properties p ON p.id = c.property_id
             LEFT JOIN LATERAL (
                 SELECT health, at FROM observations
                  WHERE check_id = c.id ORDER BY at DESC LIMIT 1
             ) o ON TRUE""")


async def _section(name: str, errors: dict[str, str], read: Awaitable[Any]
                   ) -> tuple[Any, datetime]:
    """One read, and the time it finished. A failure is `None` + `errors`."""
    try:
        rows = await read
    except Exception as exc:                                # noqa: BLE001
        why = explain(exc)
        log.warning("hud could not read %s: %s", name, why)
        errors[name] = why
        rows = None
    return rows, datetime.now(UTC)


# --------------------------------------------------------------- assembly

def _cards_of(row: dict[str, Any]) -> list[int]:
    raw = row.get("cards")
    if isinstance(raw, str):
        try:
            raw = json.loads(raw or "[]")
        except ValueError:
            raw = []
    return [int(c) for c in raw or [] if str(c).lstrip("-").isdigit()]


def _card_state(card: dict[str, Any], now: datetime) -> str:
    status = str(card.get("status"))
    expires = card.get("expires_at")
    if status == "expired" or (
            status == "pending" and isinstance(expires, datetime) and expires <= now):
        return "expired"
    return {"pending": "waiting", "held": "waiting", "approved": "approved",
            "executed": "done", "failed": "failed", "declined": "declined"
            }.get(status, "other")


def _task_state(task: dict[str, Any], cards: dict[int, dict[str, Any]] | None,
                now: datetime) -> str:
    ids = _cards_of(task)
    if ids and cards is None:
        return "unknown"
    states = [_card_state(cards[i], now) for i in ids if cards and i in cards]
    for rank in ("waiting", "expired", "failed", "declined"):
        if rank in states:
            return "waiting_for_you" if rank == "waiting" else rank
    if states and all(s in ("done", "approved") for s in states):
        return "done"
    status = str(task.get("status"))
    return {"open": "running", "done": "done", "failed": "failed",
            "held": "answered"}.get(status, "unknown")


def _surface_health(checks: list[Any] | None, prefixes: tuple[str, ...],
                    now: datetime) -> str:
    from .freshness import effective_health
    if checks is None:
        return "unknown"
    seen: list[str] = []
    for r in checks:
        if str(r["property"]) not in prefixes:
            continue
        interval = timedelta(seconds=int(r["interval_s"])) if r["interval_s"] else None
        health, _stale = effective_health(r["health"], r["observed_at"], interval, now)
        seen.append(health)
    for word in ("failing", "degraded", "unknown"):
        if word in seen:
            return word
    return "ok" if seen else "unknown"


def _surface_state(events: list[dict[str, Any]], prefixes: tuple[str, ...]
                   ) -> tuple[str, str | None, str | None]:
    """`(state, last action, when)` from the surface's newest-first events."""
    mine = [e for e in events if str(e["event"]).startswith("execute.")
            and _module_of(str(e["subject"])) in prefixes]
    if not mine:
        return "idle", None, None
    newest: dict[str, dict[str, Any]] = {}
    for e in mine:                                  # newest first: first seen wins
        newest.setdefault(str(e["subject"]), e)
    for subject, e in newest.items():
        if e["event"] == "execute.start":
            return "working", subject, _iso(e["at"])
    head = mine[0]
    state = HOLD_STATES.get(str(head["event"]),
                            "failed" if head["event"] == "execute.error" else "idle")
    return state, str(head["subject"]), _iso(head["at"])


def assemble(*, now: datetime, watch_only: bool, instrumented: set[str],
             tasks: list[Any] | None, tasks_at: datetime,
             missions: list[Any] | None, missions_at: datetime,
             pending: list[Any] | None, pending_at: datetime,
             events: list[Any] | None, events_at: datetime,
             schedule: list[Any] | None, schedule_at: datetime,
             mac_jobs: list[Any] | None, mac_jobs_at: datetime,
             mac_seen: datetime | None, mac_seen_at: datetime,
             checks: list[Any] | None, checks_at: datetime,
             running: list[str], tools: list[dict[str, Any]],
             errors: dict[str, str], mac_seen_ok: bool = True) -> dict[str, Any]:
    """The HUD from its reads. Pure, so every UNKNOWN path is testable."""
    from .occurrence import STALE_AFTER

    tasks_l = [dict(r) for r in tasks] if tasks is not None else None
    pending_l = [dict(r) for r in pending] if pending is not None else None
    events_l = [dict(r) for r in events] if events is not None else None
    by_id = {int(c["id"]): c for c in pending_l} if pending_l is not None else None

    # ---- tasks
    task_items = None
    if tasks_l is not None:
        task_items = [{
            "id": int(t["id"]), "at": _iso(t["at"]), "source": str(t["source"])[:40],
            "route": str(t.get("route") or "")[:40], "status": str(t["status"])[:20],
            "state": _task_state(t, by_id, now), "cards": _cards_of(t),
            "done_at": _iso(t.get("done_at"))} for t in tasks_l]
    tasks_section = {"read_at": tasks_at.isoformat(), "items": task_items,
                     "unread": ["tasks"] if tasks_l is None else
                     (["proposals"] if pending_l is None and any(
                         i["cards"] for i in task_items or []) else [])}

    # ---- missions
    mission_items = None
    if missions is not None:
        mission_items = []
        for m in (dict(r) for r in missions):
            done = m.get("steps_done")
            if done is None:
                done = len(m.get("steps") or [])
            mission_items.append({
                "id": int(m["id"]), "agent": str(m["agent"])[:40],
                "recipe": str(m["recipe"])[:40], "status": str(m["status"])[:20],
                "phase": str(m.get("phase") or "")[:60], "steps_done": int(done),
                "updated_at": _iso(m.get("updated_at")), "deadline": _iso(m.get("deadline"))})
    missions_section = {"read_at": missions_at.isoformat(), "items": mission_items,
                        "unread": [] if missions is not None else ["missions"]}

    # ---- waiting for him
    waiting_cards = None
    waiting_count = None
    if pending_l is not None:
        shown = []
        for c in pending_l:
            state = _card_state(c, now)
            if state in ("waiting", "expired"):
                shown.append({"id": int(c["id"]), "action": str(c["action"])[:80],
                              "tier": int(c["tier"]), "created_at": _iso(c["created_at"]),
                              "expires_at": _iso(c["expires_at"]),
                              "expired": state == "expired"})
        waiting_count = sum(1 for c in shown if not c["expired"])
        waiting_cards = shown[:MAX_CARDS]
    waiting_section = {"read_at": pending_at.isoformat(), "count": waiting_count,
                       "cards": waiting_cards,
                       "unread": [] if pending_l is not None else ["proposals"]}

    # ---- surfaces
    tiles = []
    for key, label, prefixes in SURFACES:
        unread: list[str] = []
        state: str
        last_action = last_at = why = None
        if events_l is None:
            state, why = "unknown", "actions_log unreadable"
            unread.append("actions_log")
        elif key not in instrumented:
            state, why = "unknown", "not instrumented"
        else:
            state, last_action, last_at = _surface_state(events_l, prefixes)
        seen = next((t for t in reversed(tools)
                     if any(str(t["tool"]).startswith(p) for p in prefixes)), None)
        if pending_l is None:
            cards_n = None
            unread.append("proposals")
        else:
            cards_n = sum(1 for c in pending_l if _card_state(c, now) == "waiting"
                          and _module_of(str(c["action"])) in prefixes)
        if checks is None:
            unread.append("checks")
        tiles.append({
            "key": key, "label": label, "state": state, "why": why,
            "last_action": last_action, "last_at": last_at,
            "pending_cards": cards_n,
            "health": _surface_health(checks, prefixes, now),
            "tool_seen": ({"agent": seen["agent"], "tool": seen["tool"],
                           "at": _iso(seen["at"])} if seen else None),
            "read_at": max(events_at, checks_at).isoformat(), "unread": unread})
    surfaces_section = {
        "read_at": max(events_at, checks_at).isoformat(), "items": tiles,
        "unread": sorted({u for t in tiles for u in t["unread"]})}

    # ---- approved by him in Slack (Tier 2 over a channel that can approve)
    slack = [{"action": str(e["subject"])[:80], "at": _iso(e["at"]),
              "label": SLACK_LABEL}
             for e in (events_l or [])
             if e["event"] == "intent.accepted" and str(e.get("tier")) == "2"
             and e.get("channel") == "slack"][:MAX_CARDS]

    # ---- jobs
    jobs_unread = [n for n, rows in (("schedule_runs", schedule), ("mac_jobs", mac_jobs))
                   if rows is None]
    if not mac_seen_ok:
        jobs_unread.append("mac_agent")
    jobs_section = {
        "read_at": max(schedule_at, mac_jobs_at, mac_seen_at).isoformat(),
        "running": None if schedule is None else [
            {"name": str(dict(r)["job"])[:80], "claimed_at": _iso(dict(r)["claimed_at"]),
             "stalled": (None if dict(r)["claimed_at"] is None
                         else now - dict(r)["claimed_at"] > STALE_AFTER)}
            for r in schedule],
        "background": [str(n)[:80] for n in running],
        "mac": None if mac_jobs is None else [
            {"op": str(dict(r)["op"])[:60], "status": str(dict(r)["status"])[:20],
             "created_at": _iso(dict(r)["created_at"])} for r in mac_jobs],
        "mac_last_seen": _iso(mac_seen) if mac_seen_ok else None,
        "unread": jobs_unread}

    return {"read_at": now.isoformat(), "watch_only": watch_only,
            "tasks": tasks_section, "missions": missions_section,
            "waiting": waiting_section, "surfaces": surfaces_section,
            "approved_in_slack": slack, "jobs": jobs_section, "errors": errors}


def _instrumented() -> set[str]:
    """Surfaces a catalog action touches: the only ones `actions_log` can
    speak for."""
    from .registry import REGISTRY
    modules = {_module_of(a) for a in REGISTRY.actions}
    return {key for key, _label, prefixes in SURFACES if modules & set(prefixes)}


async def snapshot(now: datetime | None = None) -> dict[str, Any]:
    """Tasks, surfaces, cards and jobs, straight from the tables."""
    from . import background

    now = now or datetime.now(UTC)
    errors: dict[str, str] = {}
    tasks, tasks_at = await _section("tasks", errors, _read_tasks(now - TASK_WINDOW))
    missions, missions_at = await _section("missions", errors, _read_missions())
    pending, pending_at = await _section("proposals", errors,
                                         _read_pending(now - TASK_WINDOW))
    events, events_at = await _section("actions_log", errors,
                                       _read_events(now - EVENT_WINDOW))
    schedule, schedule_at = await _section("schedule_runs", errors, _read_schedule())
    mac_jobs, mac_jobs_at = await _section("mac_jobs", errors, _read_mac_jobs())
    mac_seen, mac_seen_at = await _section("mac_agent", errors, _read_mac_seen())
    checks, checks_at = await _section("checks", errors, _read_checks())
    return assemble(
        now=now, watch_only=watch_only(), instrumented=_instrumented(),
        tasks=tasks, tasks_at=tasks_at, missions=missions, missions_at=missions_at,
        pending=pending, pending_at=pending_at, events=events, events_at=events_at,
        schedule=schedule, schedule_at=schedule_at, mac_jobs=mac_jobs,
        mac_jobs_at=mac_jobs_at, mac_seen=mac_seen, mac_seen_at=mac_seen_at,
        checks=checks, checks_at=checks_at, running=background.running(),
        tools=recent_tools(now), errors=errors, mac_seen_ok="mac_agent" not in errors)
