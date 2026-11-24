"""A short written report of what each of the six agents did in the last 24 h,
and whether it needs help (WhatsApp "agents" / "what did the team do").

Built from the same reads as the Agency map's panel (`agent_activity`), over a
rolling 24 h window instead of the previous New York day. Read-only: nothing
here calls the executor, a tool or a model.

Rules kept here (CLAUDE.md):
  * a read that failed is "can't read X", never a zero (invariants 6 and 7);
  * the message carries the time the reads finished, once, in New York time;
  * no table text: counts, action names and check names only (invariant 4).
"""
from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import Any

from .agent_activity import (
    NY,
    _read_checks,
    _read_events_between,
    _read_runs_between,
    _section,
    assemble,
)

log = logging.getLogger("atlas.agent_report")

WINDOW = timedelta(hours=24)

# Roster key -> the name it is said as, in the order the owner listed them.
REPORT_AGENTS: tuple[tuple[str, str], ...] = (
    ("content", "Mark"), ("bizops", "Bea"), ("seo", "Sage"),
    ("sales", "Sam"), ("research", "Rex"), ("sites", "Felix"),
)
# Sage also watches the MMT LinkedIn work; its checks live on the `mmt` module.
EXTRA_MODULES: dict[str, frozenset[str]] = {"seo": frozenset({"mmt", "sagemmt"})}

# Status lights. Priority: red > amber > grey > green. Grey is "I can't read it"
# and is never shown as green (invariant 7).
RED, AMBER, GREY, GREEN = "\U0001F534", "\U0001F7E1", "⚪", "\U0001F7E2"
CLOSING = "Reply with an agent's name for details \U0001F642"
NAME_TO_KEY = {name: key for key, name in REPORT_AGENTS}

# What an unreadable source is called to the owner (never the internal name).
_SOURCES = {"agent_runs": "its run history", "actions_log": "its action log",
            "checks": "its health checks", "work board": "the work board"}


def _clock(at: datetime) -> str:
    local = at.astimezone(NY)
    return f"{local.hour % 12 or 12}:{local:%M} {local:%p}"


def _plural(n: int, word: str) -> str:
    return f"{n} {word}" if n == 1 else f"{n} {word}s"


def _said(source: str) -> str:
    if source.startswith("job "):
        return f"the {source[4:].replace('_', ' ')} job"
    return _SOURCES.get(source, "some of its records")


def _check_said(name: str) -> str:
    return " ".join(str(name).replace(".", " ").replace("_", " ").split())


def facts(a: dict[str, Any] | None) -> dict[str, Any] | None:
    """One agent's numbers, from the fresh read only; None when the agent itself
    could not be read."""
    if a is None:
        return None
    last = a["yesterday"]
    runs, ok = last["runs"], last["actions_ok"]
    failed = (last["failed_runs"] or 0) + (last["actions_failed"] or 0)
    kinds: dict[str, list[str]] = {}
    for item in a["issues"]["items"]:
        if item["kind"] == "runs_failed":
            continue                    # already in `failed`
        kinds.setdefault(item["kind"], []).append(
            item.get("check") or item.get("job") or item.get("action") or "")
    unread = sorted({*a["now"]["unread"], *last["unread"], *a["issues"]["unread"]})
    return {
        "done": ok, "failed": failed,
        "waiting": a["now"]["waiting"] or 0,
        "failing": kinds.get("check_failing", []),
        "degraded": kinds.get("check_degraded", []),
        "jobs": kinds.get("job_failed", []) + kinds.get("job_stalled", []),
        "expired": last["cards_expired"] or 0,
        "unknown_checks": kinds.get("check_unknown", []), "unread": unread,
        "count_unread": runs is None or ok is None,
    }


def light(f: dict[str, Any] | None) -> str:
    """The emoji for one agent: red if a check is failing, else amber if anything
    failed or is degraded, else grey if anything cannot be read, else green. An
    agent that cannot be read at all is grey."""
    if f is None:
        return GREY
    if f["failing"]:
        return RED
    if f["failed"] or f["degraded"] or f["jobs"] or f["expired"]:
        return AMBER
    if f["unknown_checks"] or f["unread"] or f["count_unread"]:
        return GREY
    return GREEN


def _line(name: str, a: dict[str, Any] | None, unknown: bool = False) -> str:
    """`🟢 Bea: all good, 2 things done` / `🟡 Mark: did 3 things, 2 failed - 1 check
    is slow or degraded, 8 I can't read`. An unreadable agent is grey."""
    f = facts(a)
    if f is None:
        return f"{GREY} {name}: I couldn't read {name} today."
    lamp = light(f)
    done = f["done"]
    if lamp == GREEN:
        waiting = f", {f['waiting']} waiting for you" if f["waiting"] else ""
        return (f"{lamp} {name}: all good, "
                + (_plural(done, "thing") + " done" if done else "a quiet day") + waiting)
    head = ("did " + _plural(done, "thing")) if done is not None else "can't tell what's done"
    if f["failed"]:
        head += f", {f['failed']} failed"
    bits: list[str] = []
    if f["failing"]:
        n = len(f["failing"])
        bits.append(f"{n} {'check is' if n == 1 else 'checks are'} failing")
    if f["degraded"]:
        n = len(f["degraded"])
        bits.append(f"{n} {'check is' if n == 1 else 'checks are'} slow or degraded")
    if f["jobs"]:
        bits.append(_plural(len(f["jobs"]), "job") + " stuck or failed")
    if f["expired"]:
        bits.append(_plural(f["expired"], "card") + " expired")
    n = len(f["unknown_checks"]) + len(f["unread"]) or int(f["count_unread"])
    if n:
        bits.append(f"{n} I can't read")
    if f["waiting"]:
        bits.append(f"{f['waiting']} waiting for you")
    return f"{lamp} {name}: {head}" + (" - " + ", ".join(bits) if bits else "")


def detail(name: str, a: dict[str, Any] | None) -> str:
    """Why one agent is not green, in plain words, from the fresh read only. No
    internal source or module names."""
    f = facts(a)
    if f is None:
        return f"I couldn't read {name} just now, so I can't say what's wrong."
    parts: list[str] = []
    if f["failing"]:
        parts.append("failing: " + ", ".join(_check_said(c) for c in f["failing"][:6]))
    if f["degraded"]:
        parts.append("slow or degraded: "
                     + ", ".join(_check_said(c) for c in f["degraded"][:6]))
    if f["jobs"]:
        parts.append("jobs that failed or stalled: "
                     + ", ".join(_check_said(j) for j in f["jobs"][:6]))
    if f["failed"]:
        parts.append(f"{_plural(f['failed'], 'run or action')} failed in the last 24 hours")
    if f["expired"]:
        parts.append(_plural(f["expired"], "approval card") + " ran out unanswered")
    cant = [_check_said(c) for c in f["unknown_checks"][:6]]
    cant += [_said(u) for u in f["unread"]]
    if f["count_unread"] and not cant:
        cant.append("its counts for the day")
    if cant:
        parts.append("I can't read " + ", ".join(cant))
    if f["waiting"]:
        parts.append(_plural(f["waiting"], "card") + " waiting for your decision")
    if not parts:
        return f"{name}: nothing wrong that I can see."
    return f"{name} - " + "; ".join(parts) + "."


def compose(data: dict[str, Any], now: datetime, only: str | None = None,
            unknown: bool = False, details: bool = False) -> str:
    """The report text from `read()`'s result. Pure. `only` is one agent's first
    name; `details` says why instead of counting (every agent that is not green
    when `only` is None). `unknown` is kept for old callers and changes nothing."""
    by = {a["name"]: a for a in data["agents"]}
    head = f"Last 24 hours, as of {_clock(now)} ET"
    shown = [(key, name) for key, name in REPORT_AGENTS if only in (None, name)]
    tail = ("\nSome of my records couldn't be read, so a line may be incomplete."
            if data["errors"] else "")
    if details:
        lines = []
        for key, name in shown:
            f = facts(by.get(key))
            if only is None and light(f) == GREEN:
                continue
            lines.append(f"{light(f)} " + detail(name, by.get(key)))
        return (head + "\n" + "\n".join(lines or [f"{GREEN} Nothing is wrong that I can see."])
                + tail)
    lines = [_line(name, by.get(key)) for key, name in shown]
    lamps = {light(facts(by.get(key))) for key, name in shown}
    if only is not None and lamps != {GREEN}:
        # "how is Mark doing": the line, then why.
        lines.append(detail(only, by.get(NAME_TO_KEY[only])))
        return head + "\n" + "\n".join(lines) + tail
    return head + "\n" + "\n".join(lines) + tail + "\n" + CLOSING


async def read(now: datetime | None = None) -> dict[str, Any]:
    """The roster's activity over the last 24 h. The `yesterday` section of
    each agent is the window here (`assemble` is shared with the map)."""
    from . import workboard
    from .agents import ROSTER
    from .personas import persona_for

    now = now or datetime.now(UTC)
    since = now - WINDOW
    errors: dict[str, str] = {}
    board, board_at = await _section("work board", errors, workboard.board(1, now))
    from .agent_activity import _read_last_runs
    last_runs, last_at = await _section("agent_runs (latest)", errors, _read_last_runs())
    runs, _ = await _section("agent_runs", errors, _read_runs_between(since, now))
    events, ev_at = await _section("actions_log", errors,
                                   _read_events_between(since, now))
    checks, issues_at = await _section("checks", errors, _read_checks())
    roster = [(k, persona_for(k, a.title, a.brief).name, a.title)
              for k, a in ROSTER.items()]
    modules_of = {s["name"]: set(s["modules"]) for s in workboard.seats()}
    for key, extra in EXTRA_MODULES.items():
        modules_of[key] = modules_of.get(key, set()) | set(extra)
    return assemble(now=now, roster=roster, modules_of=modules_of,
                    board=board, board_at=board_at,
                    last_runs=last_runs, last_runs_at=last_at,
                    y_runs=runs, y_events=events, y_at=ev_at,
                    recent_runs=runs, recent_events=events,
                    checks=checks, issues_at=issues_at,
                    y_start=since, y_end=now, errors=errors)


async def report(now: datetime | None = None, only: str | None = None,
                 details: bool = False) -> str:
    now = now or datetime.now(UTC)
    try:
        data = await read(now)
    except Exception as exc:                                # noqa: BLE001
        log.warning("agent report unreadable: %s", type(exc).__name__)
        return (f"I can't read the agents' records right now (as of {_clock(now)} ET), "
                "so I won't guess what they did.")
    return compose(data, now, only, details=details)
