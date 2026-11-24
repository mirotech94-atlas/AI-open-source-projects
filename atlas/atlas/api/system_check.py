"""System check (15.3): every agent and every check, read fresh, in one read.

"System check" is the estate's health said out loud -- the agents, whether
each one can run, and every registered check with the time it was read. It
is not the self-model ("how are you" stays in `kernel/selfmodel.py`): this
is the board, not ATLAS's own uptime and lessons.

One builder, `build()`, serves both `GET /api/system/check` and the voice
branch in `voice.py`. The spoken line and the drawn checklist therefore come
from the same read; two reads could disagree, and a checklist that shows
something the voice did not say is the kind of mismatch nobody trusts twice.

"All clear" is said only when nothing is DEGRADED, FAILING *or UNKNOWN*, at
least one thing was measured, every module loaded, and every agent can run.
Three things are never OK here, all on the conservative side of invariant 7:

  - a registered check with no observation at all -- never measured;
  - a check whose newest observation is older than `STALE_FACTOR` of its own
    interval (plus `STALE_GRACE`) -- an OK from three intervals ago is what
    a dead scheduler looks like, not a current OK. `effective_health` is
    that rule, and `/api/overview` applies the same helper, so the screen
    and the voice never disagree about a stale check;
  - a module that could not load (`REGISTRY.disabled`) -- it never registers
    checks, so reading only the checks table would make it invisible, and a
    system check over an unmeasured module must never say all clear
    (Gate 15). It has no checks, so it is not added to M (a check that does
    not exist is not a number the source gave); it is counted in
    `summary.not_loaded`, named under "Not measured", and blocks all clear.

Not all clear is not the same as bad news. When everything MEASURED is fine
and something is unmeasured, the line says so and names what, and why:
"Everything I can measure is fine: 38 of 40 checks passed. Not measured:
calendar (no GOOGLE_ key), gitlab (no GITLAB_TOKEN)." An offline agent is a
stated item in the same shape -- "the school agent is offline (no local
model)" -- never a silent gate.

Reads only. Nothing here imports the executor.
"""
from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any

from fastapi import APIRouter, Depends

from ..kernel.freshness import (  # noqa: F401 -- re-exported
    HEALTH_RANK,
    STALE_FACTOR,
    STALE_GRACE,
    effective_health,
    known_health,
)
from ..kernel.registry import REGISTRY
from ..kernel.state import fetch_all
from . import auth

log = logging.getLogger("atlas.system_check")

router = APIRouter(prefix="/api/system")
SignedIn = Annotated[auth.Session, Depends(auth.require)]

_LATEST = """SELECT p.name AS property, c.name AS check_name,
                    o.health, o.detail, o.at AS observed_at
             FROM checks c
             JOIN properties p ON p.id = c.property_id
             LEFT JOIN LATERAL (
                 SELECT health, detail, at FROM observations
                 WHERE check_id = c.id ORDER BY at DESC LIMIT 1
             ) o ON TRUE"""

MAX_NAMED = 4
MAX_REASON = 60


def _now() -> datetime:
    """A seam, so a test can fix "now" and the staleness rule with it."""
    return datetime.now(UTC)


def _clock(at: datetime) -> str:
    """HH:MM in the box's local time -- the same clock the brief speaks."""
    return at.astimezone().strftime("%H:%M")


# The staleness rule is the kernel's (`kernel/freshness.py`), so the
# kernel's own readers -- the self-model -- apply the same one; these names
# stay importable from here for the api's readers and their tests.
_health = known_health


def row_health(health: Any, observed_at: datetime | None, interval_s: Any,
               now: datetime | None = None) -> str:
    """`effective_health` for a row that carries `interval_s` (the checks
    table's column) -- for the other readers of the board, so a stale OK is
    unknown wherever it is read, not only here and on /overview. Uses this
    module's `_now`, the seam the tests fix."""
    interval = timedelta(seconds=int(interval_s)) if interval_s else None
    return effective_health(health, observed_at, interval, now or _now())[0]


def _short(text: str) -> str:
    said = " ".join(str(text).split()).rstrip(".")
    return said if len(said) <= MAX_REASON else said[: MAX_REASON - 1] + "…"


def _disabled_reason(why: str) -> str:
    """"missing env: GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET" -> "no GOOGLE_
    key" -- the name of what is missing, never a value (there is none)."""
    if why.startswith("missing env:"):
        names = [n.strip() for n in why.split(":", 1)[1].split(",") if n.strip()]
        if len(names) == 1:
            return f"no {names[0]}"
        prefix = names[0][: names[0].find("_") + 1] if "_" in names[0] else ""
        if prefix and all(n.startswith(prefix) for n in names):
            return f"no {prefix} key"
        return "no " + " or ".join(names)
    if why.startswith("import failed"):
        return "failed to load"
    return _short(why)


def _agents() -> list[dict[str, Any]]:
    """Every roster agent, and whether its Need resolves to a provider right
    now -- the same rule as `/api/agents` `available`. An offline agent says
    why, so it is a stated item rather than a silent gate."""
    from ..kernel import agents, llm

    out = []
    for name, agent in sorted(agents.ROSTER.items()):
        need = getattr(agent, "need", None)
        chain = llm.ROUTES.get(need, []) if need else []
        if chain:
            reason = None
        elif need == llm.Need.PRIVATE:
            reason = "no local model"
        elif need:
            reason = f"no {need} model"
        else:
            reason = "no model tier"
        out.append({"name": name, "title": getattr(agent, "title", name),
                    "online": bool(chain), "reason": reason})
    return out


def _plural(n: int, one: str, many: str) -> str:
    return one if n == 1 else many


def _listed(items: list[str]) -> str:
    shown = items[:MAX_NAMED]
    rest = len(items) - len(shown)
    return ", ".join(shown) + (f" and {rest} more" if rest else "")


def spoken_line(summary: dict[str, int], modules: list[dict[str, Any]],
                agents: list[dict[str, Any]], oldest: datetime | None,
                unreadable: str | None = None,
                checked: datetime | None = None) -> str:
    """The sentence ATLAS says, plus the read time: the oldest reading behind
    the count, or -- when nothing had a reading at all -- when the check
    itself was made.

    "All clear" only when nothing is degraded, failing, unmeasured, not
    loaded or offline. When everything MEASURED is fine but something is not
    measured, it says so and names what, with why -- still useful, never all
    clear. M counts registered checks only: a module that did not load has
    none, so it is named under "Not measured", never added to the count."""
    ok, total = summary["ok"], summary["total"]
    attention = summary["degraded"] + summary["failing"]
    unknown = summary["unknown"]
    not_loaded = summary.get("not_loaded", 0)
    offline = [a for a in agents if not a["online"]]

    # One cause said once: with the store unreachable every check has the
    # same reason, and listing it per module is four copies of one fact.
    unreachable = "record store unreachable"
    unmeasured = [f"{m['title']} ({m['not_measured']})" for m in modules
                  if (m["unknown"] or not m["enabled"])
                  and not (unreadable and m["not_measured"] == unreachable)]
    tail = ""
    if unreadable:
        tail += " The record store is unreachable, so no reading could be taken."
    if unmeasured:
        tail += f" Not measured: {_listed(unmeasured)}."

    stamp = (f" Oldest reading {_clock(oldest)}." if oldest
             else f" Checked at {_clock(checked)}." if checked else "")
    if total == 0:
        return ("Nothing is being measured, so I can't tell you anything is "
                "clear." + tail + stamp)

    count = f"{ok} of {total} {_plural(total, 'check', 'checks')} passed"
    if attention == 0 and unknown == 0 and not_loaded == 0 and not offline:
        return f"All clear. {count}.{stamp}"

    if attention:
        parts = [count, f"{attention} {_plural(attention, 'needs', 'need')} attention"]
    elif ok:
        parts = [f"Everything I can measure is fine: {count}"]
    else:
        parts = [f"Nothing could be measured: {count}"]
    for agent in offline[:MAX_NAMED]:
        parts.append(f"the {agent['name']} agent is offline ({agent['reason']})")
    if len(offline) > MAX_NAMED:
        parts.append(f"{len(offline) - MAX_NAMED} more agents are offline")
    return "; ".join(parts) + "." + tail + stamp


def spoken_summary(summary: dict[str, int], modules: list[dict[str, Any]],
                   agents: list[dict[str, Any]], oldest: datetime | None,
                   unreadable: str | None = None,
                   checked: datetime | None = None) -> str:
    """What is SAID of the check: the host's short summary. The verdict and
    the count first, then HOW MANY are not measured or need attention (never
    which: the names stay on screen, in `spoken_line`), and the time it was
    read (invariant 6). Same honesty as `spoken_line`: "all clear" only when
    nothing is degraded, failing, unmeasured, not loaded or offline, and an
    unreachable store is said, never read as fine (invariant 7)."""
    ok, total = summary["ok"], summary["total"]
    attention = summary["degraded"] + summary["failing"]
    unmeasured = summary["unknown"] + summary.get("not_loaded", 0)
    offline = [a for a in agents if not a["online"]]
    at = oldest or checked
    when = f"as of {_clock(at)}" if at else "just now"

    if unreadable:
        return ("I can't reach my record store, so I can't read anything right "
                f"now. I tried at {_clock(checked or at)}." if (checked or at)
                else "I can't reach my record store, so I can't read anything "
                     "right now.")
    if total == 0:
        return (f"I'm not measuring anything yet, so I can't say it's clear, {when}.")
    count = f"{ok} of {total} {_plural(total, 'check', 'checks')} passed"
    if attention == 0 and unmeasured == 0 and not offline:
        return f"All clear: {count}, {when}."

    lead = (f"{count}, {when}" if attention
            else f"Mostly fine: {count}, {when}" if ok
            else f"I couldn't measure much: {count}, {when}")
    more: list[str] = []
    if attention:
        more.append(f"{attention} {_plural(attention, 'needs', 'need')} attention")
    if unmeasured:
        more.append(f"{unmeasured} {_plural(unmeasured, 'is', 'are')} not measured")
    said = lead + "."
    if more:
        said += " " + more[0][0].upper() + more[0][1:]
        said += (f", and {more[1]}" if len(more) > 1 else "") + "."
    if offline:
        said += (f" {len(offline)} {_plural(len(offline), 'agent is', 'agents are')}"
                 " offline.")
    return said


async def build() -> dict[str, Any]:
    """Agents, modules and the spoken line, from one fresh read."""
    now = _now()
    unreadable: str | None = None
    latest: dict[tuple[str, str], tuple[Any, str, datetime | None]] = {}
    try:
        for row in await fetch_all(_LATEST):
            latest[(row["property"], row["check_name"])] = (
                row["health"], row.get("detail") or "", row["observed_at"])
    except Exception as exc:  # noqa: BLE001
        # Every check is then unmeasured -- said as such, never as clear.
        from ..modules._http import explain
        unreadable = f"the record store is unreachable ({explain(exc)})"
        log.warning("system check could not read observations: %s", unreadable)

    by_module: dict[str, dict[str, Any]] = {}
    oldest: datetime | None = None
    for key, check in sorted(REGISTRY.checks.items()):
        module, _, name = key.partition(".")
        entry = by_module.setdefault(module, {
            "name": module,
            "title": getattr(REGISTRY.modules.get(module), "title", module),
            "health": "ok", "checks_ok": 0, "checks_total": 0, "unknown": 0,
            "degraded": 0, "failing": 0, "enabled": True, "reason": None,
            "not_measured": None,
        })
        raw, detail, at = latest.get((module, name), (None, "", None))
        health, stale = effective_health(raw, at, check.interval, now)
        if at is not None and health != "unknown":
            oldest = at if oldest is None or at < oldest else oldest
        entry["checks_total"] += 1
        if health == "ok":
            entry["checks_ok"] += 1
        else:
            entry[health] += 1
        if health == "unknown" and entry["not_measured"] is None:
            # Why, for the first unmeasured check of this module.
            entry["not_measured"] = (
                "record store unreachable" if unreadable
                else "never read" if at is None
                else f"last read {_clock(at)}" if stale
                else _short(detail) if detail.strip()
                else "check could not run")
        if HEALTH_RANK[health] > HEALTH_RANK[entry["health"]]:
            entry["health"] = health

    for module, why in sorted(REGISTRY.disabled.items()):
        by_module.setdefault(module, {
            "name": module, "title": module, "health": "unknown",
            "checks_ok": 0, "checks_total": 0, "unknown": 0,
            "degraded": 0, "failing": 0, "enabled": False, "reason": why,
            "not_measured": _disabled_reason(why),
        })

    modules = sorted(by_module.values(), key=lambda m: m["name"])
    summary = {
        "ok": sum(m["checks_ok"] for m in modules),
        "degraded": sum(m["degraded"] for m in modules),
        "failing": sum(m["failing"] for m in modules),
        "unknown": sum(m["unknown"] for m in modules),
        "total": sum(m["checks_total"] for m in modules),
        "not_loaded": sum(1 for m in modules if not m["enabled"]),
    }
    agents = _agents()
    return {
        "as_of": now.isoformat(),
        "agents": agents,
        "modules": modules,
        "summary": summary,
        "oldest_reading": oldest.isoformat() if oldest else None,
        "unreadable": unreadable,
        "line": spoken_line(summary, modules, agents, oldest, unreadable, now),
        "spoken": spoken_summary(summary, modules, agents, oldest, unreadable, now),
    }


@router.get("/check")
async def system_check(_s: SignedIn) -> dict:
    return await build()
