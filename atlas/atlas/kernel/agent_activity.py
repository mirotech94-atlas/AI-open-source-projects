"""What each roster agent is doing, did yesterday, and what is wrong.

the owner, 2026-10-28: beside the Agency map, a live panel per agent -- what it
is doing now, what it did yesterday, and its issues -- from real data only,
each item stamped with when it was read. `GET /api/agency/activity` serves
`activity()`; the map polls it every 30 s.

Where the figures come from (nothing here is a second bookkeeping):
  * now -- the work board (`workboard.board`): in-flight work, the pending
    cards on the agent's modules, its scheduled jobs; plus the agent's
    newest `agent_runs` row;
  * yesterday -- the previous New York day, [00:00, 24:00): `agent_runs`
    per agent, and `actions_log` (`execute.finish`, `proposal.expired`) on
    the agent's modules;
  * issues -- the agent's failed runs in the last 24 h, its modules' checks
    that read failing or UNKNOWN (stale counts as UNKNOWN, `freshness`),
    its jobs whose last run failed or has been `running` past the
    occurrence lease (stalled), and its cards that expired in the last 24 h.

Rules kept here, from CLAUDE.md:
  * every section carries `read_at`, the time its reads finished
    (invariant 6);
  * a read that fails makes the figures it fed `None` and names itself in
    `unread`, never 0 -- the page draws that UNKNOWN (invariant 7);
  * no table text is shown: an item is a count, an action name, a check
    name or a time, never a mission's request or a card's reason.

Read-only. Nothing here calls the executor, a tool or a model.
"""
from __future__ import annotations

import logging
from collections import Counter, defaultdict
from collections.abc import Awaitable
from datetime import UTC, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from ..modules._http import explain

log = logging.getLogger("atlas.agent_activity")

NY = ZoneInfo("America/New_York")
ISSUE_WINDOW = timedelta(hours=24)
MAX_ITEMS = 6                     # per list, per agent
STATE_ORDER = ("failing", "degraded", "unknown")


def _iso(value: Any) -> str | None:
    return value.isoformat() if isinstance(value, datetime) else None


def _module_of(action: str) -> str:
    return action.split(".", 1)[0] if action else ""


def day_start(now: datetime) -> datetime:
    """Midnight of `now`'s New York calendar day, in UTC. Every "today"
    figure on the pages counts from here, not from UTC midnight (which is
    20:00 or 19:00 in New York). Built from the local date, never by
    subtracting hours, so a 23 h or 25 h DST day is right."""
    today = now.astimezone(NY).date()
    return datetime(today.year, today.month, today.day, tzinfo=NY).astimezone(UTC)


def yesterday_bounds(now: datetime) -> tuple[datetime, datetime]:
    """The previous New York calendar day as [start, end) in UTC."""
    today = now.astimezone(NY).date()
    end = datetime(today.year, today.month, today.day, tzinfo=NY)
    start = end - timedelta(days=1)
    # A DST day is 23 or 25 h: build the start from its own date, not -24 h.
    y = start.date()
    start = datetime(y.year, y.month, y.day, tzinfo=NY)
    return start.astimezone(UTC), end.astimezone(UTC)


# ------------------------------------------------------------------ reads

async def _read_last_runs() -> list[Any]:
    from .agents import WARMUP_PROMPT
    from .meeting import FAILED_SQL
    from .state import fetch_all
    return await fetch_all(
        f"""SELECT DISTINCT ON (agent) agent, at, ({FAILED_SQL}) AS failed
              FROM agent_runs WHERE task <> $1 ORDER BY agent, at DESC""",
        WARMUP_PROMPT)


async def _read_runs_between(start: datetime, end: datetime) -> list[Any]:
    from .agents import WARMUP_PROMPT
    from .meeting import FAILED_SQL
    from .state import fetch_all
    return await fetch_all(
        f"""SELECT agent,
                   count(*) AS runs,
                   count(*) FILTER (WHERE {FAILED_SQL}) AS failed,
                   max(at) AS last_at,
                   max(at) FILTER (WHERE {FAILED_SQL}) AS last_failed_at
              FROM agent_runs
             WHERE at >= $1 AND at < $2 AND task <> $3
             GROUP BY agent""", start, end, WARMUP_PROMPT)


async def _read_events_between(start: datetime, end: datetime) -> list[Any]:
    from .state import fetch_all
    return await fetch_all(
        """SELECT event, subject, payload, at FROM actions_log
            WHERE at >= $1 AND at < $2
              AND event IN ('execute.finish', 'proposal.expired')
            ORDER BY at DESC""", start, end)


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
        log.warning("agent activity could not read %s: %s", name, why)
        errors[name] = why
        rows = None
    return rows, datetime.now(UTC)


# --------------------------------------------------------------- assembly

def _check_issues(rows: list[Any], modules: set[str], now: datetime
                  ) -> list[dict[str, Any]]:
    from .freshness import effective_health
    from .registry import REGISTRY
    out: list[dict[str, Any]] = []
    for r in rows:
        module = str(r["property"])
        if module not in modules:
            continue
        # A check its live module no longer registers is a retired check:
        # the same rule `/api/overview` applies.
        if module in REGISTRY.modules and f"{module}.{r['check_name']}" not in REGISTRY.checks:
            continue
        interval = timedelta(seconds=int(r["interval_s"])) if r["interval_s"] else None
        health, stale = effective_health(r["health"], r["observed_at"], interval, now)
        if health not in STATE_ORDER:
            continue
        out.append({"kind": f"check_{health}", "module": module,
                    "check": str(r["check_name"]), "stale": stale,
                    "at": _iso(r["observed_at"])})
    out.sort(key=lambda i: (STATE_ORDER.index(i["kind"].removeprefix("check_")),
                            i["module"], i["check"]))
    return out


def _ok_of(payload: Any) -> bool | None:
    from .workboard import _ok_of as ok_of
    return ok_of(payload)


def assemble(*, now: datetime, roster: list[tuple[str, str, str]],
             modules_of: dict[str, set[str]],
             board: dict[str, Any] | None, board_at: datetime,
             last_runs: list[Any] | None, last_runs_at: datetime,
             y_runs: list[Any] | None, y_events: list[Any] | None, y_at: datetime,
             recent_runs: list[Any] | None, recent_events: list[Any] | None,
             checks: list[Any] | None, issues_at: datetime,
             y_start: datetime, y_end: datetime,
             errors: dict[str, str]) -> dict[str, Any]:
    """The panel from its reads. Pure, so every UNKNOWN path is testable.

    `roster` is (name, persona name, title) in roster order; `modules_of`
    maps each agent to the modules its work lives on (the work board's
    seats)."""
    from .occurrence import STALE_AFTER

    seats = {a["name"]: a for a in (board or {}).get("agents", [])}
    last_by = {str(r["agent"]): r for r in last_runs or []}
    y_by = {str(r["agent"]): r for r in y_runs or []}
    recent_by = {str(r["agent"]): r for r in recent_runs or []}

    def by_module(events: list[Any] | None) -> dict[str, list[Any]]:
        out: dict[str, list[Any]] = defaultdict(list)
        for e in events or []:
            out[_module_of(str(e["subject"]))].append(e)
        return out

    y_ev = by_module(y_events)
    recent_ev = by_module(recent_events)

    agents: list[dict[str, Any]] = []
    for name, label, title in roster:
        mine = modules_of.get(name, set())
        seat = seats.get(name)

        # ---- now
        now_unread: list[str] = []
        if board is None or seat is None:
            now_unread.append("work board")
        if last_runs is None:
            now_unread.append("agent_runs")
        cards = (seat or {}).get("waiting")
        in_flight = ((seat or {}).get("counts") or {}).get("in_flight")
        running_jobs = [j["name"] for j in (seat or {}).get("jobs", [])
                        if (j.get("last_run") or {}).get("status") == "running"]
        last = last_by.get(name)
        now_section = {
            "read_at": max(board_at, last_runs_at).isoformat(),
            "headline": (seat or {}).get("headline"),
            "in_flight": in_flight if seat is not None else None,
            "waiting": len(cards) if cards is not None else None,
            "cards": [{"id": c["id"], "action": c["action"], "tier": c["tier"],
                       "expires_at": c["expires_at"]} for c in (cards or [])[:MAX_ITEMS]],
            "running_jobs": running_jobs,
            "last_run": ({"at": _iso(last["at"]), "failed": bool(last["failed"])}
                         if last is not None else None),
            "last_run_known": last_runs is not None,
            "unread": now_unread,
        }

        # ---- yesterday
        y_unread: list[str] = []
        if y_runs is None:
            y_unread.append("agent_runs")
        if y_events is None:
            y_unread.append("actions_log")
        yr = y_by.get(name)
        finishes = [e for m in mine for e in y_ev.get(m, [])
                    if e["event"] == "execute.finish"]
        expired_y = [e for m in mine for e in y_ev.get(m, [])
                     if e["event"] == "proposal.expired"]
        oks = [_ok_of(e["payload"]) for e in finishes]
        finishes.sort(key=lambda e: e["at"], reverse=True)
        yesterday = {
            "read_at": y_at.isoformat(),
            "day": y_start.astimezone(NY).date().isoformat(),
            "from": y_start.isoformat(), "to": y_end.isoformat(),
            "runs": (int(yr["runs"]) if yr else 0) if y_runs is not None else None,
            "failed_runs": ((int(yr["failed"]) if yr else 0)
                            if y_runs is not None else None),
            "last_run_at": _iso(yr["last_at"]) if yr else None,
            "actions_ok": sum(1 for o in oks if o is True) if y_events is not None else None,
            "actions_failed": (sum(1 for o in oks if o is False)
                               if y_events is not None else None),
            "last_actions": [{"action": str(e["subject"]), "at": _iso(e["at"]),
                              "ok": _ok_of(e["payload"])}
                             for e in finishes[:3]] if y_events is not None else [],
            "cards_expired": len(expired_y) if y_events is not None else None,
            "unread": y_unread,
        }

        # ---- issues
        items: list[dict[str, Any]] = []
        i_unread: list[str] = []
        if recent_runs is None:
            i_unread.append("agent_runs")
        else:
            rr = recent_by.get(name)
            if rr and int(rr["failed"]):
                items.append({"kind": "runs_failed", "count": int(rr["failed"]),
                              "at": _iso(rr["last_failed_at"])})
        if checks is None:
            i_unread.append("checks")
        else:
            items += _check_issues(checks, mine, now)
        if seat is None:
            i_unread.append("work board")
        else:
            for j in seat.get("jobs", []):
                lr = j.get("last_run") or {}
                at = lr.get("at")
                if lr.get("status") == "failed":
                    items.append({"kind": "job_failed", "job": j["name"], "at": at})
                elif lr.get("status") == "running" and at and (
                        now - datetime.fromisoformat(at) > STALE_AFTER):
                    items.append({"kind": "job_stalled", "job": j["name"], "at": at})
                elif not j.get("last_run_known", True) and j.get("recorded"):
                    i_unread.append(f"job {j['name']}")
        if recent_events is None:
            i_unread.append("actions_log")
        else:
            expired = Counter(str(e["subject"]) for m in mine
                              for e in recent_ev.get(m, [])
                              if e["event"] == "proposal.expired")
            for action, n in sorted(expired.items()):
                newest = max(e["at"] for e in recent_ev[_module_of(action)]
                             if e["event"] == "proposal.expired"
                             and str(e["subject"]) == action)
                items.append({"kind": "cards_expired", "action": action, "count": n,
                              "at": _iso(newest)})
        issues = {
            "read_at": issues_at.isoformat(),
            "count": len(items),
            "items": items[:MAX_ITEMS * 2],
            "unread": i_unread,
        }

        agents.append({"name": name, "label": label, "title": title,
                       "modules": sorted(mine),
                       "now": now_section, "yesterday": yesterday, "issues": issues})

    return {"read_at": now.isoformat(), "agents": agents, "errors": errors}


async def activity(now: datetime | None = None) -> dict[str, Any]:
    """Every roster agent's now / yesterday / issues, from the tables."""
    from . import workboard
    from .agents import ROSTER
    from .personas import persona_for

    now = now or datetime.now(UTC)
    errors: dict[str, str] = {}
    y_start, y_end = yesterday_bounds(now)
    since = now - ISSUE_WINDOW

    board, board_at = await _section("work board", errors, workboard.board(1, now))
    last_runs, last_runs_at = await _section("agent_runs (latest)", errors,
                                             _read_last_runs())
    y_runs, _ = await _section("agent_runs (yesterday)", errors,
                               _read_runs_between(y_start, y_end))
    y_events, y_at = await _section("actions_log (yesterday)", errors,
                                    _read_events_between(y_start, y_end))
    recent_runs, _ = await _section("agent_runs (24 h)", errors,
                                    _read_runs_between(since, now))
    recent_events, _ = await _section("actions_log (24 h)", errors,
                                      _read_events_between(since, now))
    checks, issues_at = await _section("checks", errors, _read_checks())

    roster = [(name, persona_for(name, a.title, a.brief).name, a.title)
              for name, a in ROSTER.items()]
    modules_of = {s["name"]: set(s["modules"]) for s in workboard.seats()}
    return assemble(now=now, roster=roster, modules_of=modules_of,
                    board=board, board_at=board_at,
                    last_runs=last_runs, last_runs_at=last_runs_at,
                    y_runs=y_runs, y_events=y_events, y_at=y_at,
                    recent_runs=recent_runs, recent_events=recent_events,
                    checks=checks, issues_at=issues_at,
                    y_start=y_start, y_end=y_end, errors=errors)
