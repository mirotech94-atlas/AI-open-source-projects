"""What the board says about a check that is neither OK nor broken (Phase 4).

Three honest states sit beside OK / DEGRADED / FAILING / UNKNOWN, and none of
them may look like OK or like each other (invariants 3 and 7):

  * **parked** -- the owner (or a committed decision) knows this check is not
    right and has said why and until when. A committed list, `PARKED`, in
    code: name, reason, expiry date. No UI toggle, no runtime write, nothing
    reads a database to decide. It un-parks on its date by itself: on the
    expiry day the check is judged on its own reading again.
  * **retired** -- the check has a row in the database but the code no longer
    registers it (renamed, removed). Its last reading is history, not a
    reading of now, and is never OK.
  * **switched off** -- a whole module off by name (`ATLAS_DISABLED_MODULES`
    or `SWITCHED_OFF` below). Shown by the board as its own label.

Pure: no I/O, no imports that act. The board (`api/dashboard.py`) and the
self-test line call these; nothing here executes anything.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any

from .freshness import STALE_FACTOR, STALE_GRACE


@dataclass(frozen=True)
class Parked:
    check: str          # "module.check", as the registry names it
    reason: str         # why it is not being fixed now, in one plain line
    until: date         # parked while today < until; judged again on this day


# Checks parked on purpose. Add a line here (by commit) to park one; remove
# it, or let the date pass, to un-park. Every line needs a reason and a date.
PARKED: tuple[Parked, ...] = (
    Parked("agency.getlayers_auth", "the owner, 2026-11-17: GetLayers is not used",
           date(2027, 12, 31)),
    Parked("content.reel_legs", "the owner, 2026-11-17: no Instagram or Facebook, "
           "the content focus is YouTube", date(2027, 12, 31)),
    Parked("content.shorts_lane", "the owner, 2026-11-17: Postiz is not set up on "
           "purpose", date(2027, 12, 31)),
    Parked("youtube.ecc_latest_videos", "the owner, 2026-11-17: the ecc channel "
           "is dropped from the board", date(2027, 12, 31)),
    Parked("youtube.ecc_stats", "the owner, 2026-11-17: the ecc channel is "
           "dropped from the board", date(2027, 12, 31)),
    Parked("youtube.ecc_token", "the owner, 2026-11-17: the ecc channel is "
           "dropped from the board", date(2027, 12, 31)),
    Parked("calendar.focus_invites", "the owner, 2026-11-18: not essential, no "
           "focus blocks to guard", date(2027, 12, 31)),
    Parked("academy.runway", "the owner, 2026-11-18: not essential, the Academy "
           "content runway is not tracked on the board", date(2027, 12, 31)),
    Parked("academy.posting_health", "the owner, 2026-11-18: not essential, the Academy "
           "tutorial pipeline (NotebookLM runner) is paused; last post 30 Oct",
           date(2027, 12, 31)),
)

# Modules switched off by a committed decision, merged by the registry with
# `ATLAS_DISABLED_MODULES` (the env list still wins on the reason it prints).
# name -> why. the owner, 2026-11-14: he uses the NotebookLM voice, so no paid
# Fish Audio key; the `voice` module and its `fish_credit` check are off.
SWITCHED_OFF: dict[str, str] = {
    "voice": "the owner uses the NotebookLM voice, so no paid Fish Audio key",
    "reddit": "the owner, 2026-11-17: Reddit is not used",
}


def validate(parked: tuple[Parked, ...] = PARKED) -> list[str]:
    """Problems with the committed list: a missing reason, a bad date, a
    duplicate. Empty when it is sound (a test holds it to that)."""
    problems: list[str] = []
    seen: set[str] = set()
    for entry in parked:
        if not entry.reason.strip():
            problems.append(f"{entry.check}: no reason")
        if not isinstance(entry.until, date) or isinstance(entry.until, datetime):
            problems.append(f"{entry.check}: expiry is not a date")
        if "." not in entry.check:
            problems.append(f"{entry.check}: not a module.check name")
        if entry.check in seen:
            problems.append(f"{entry.check}: listed twice")
        seen.add(entry.check)
    return problems


def parked_entry(key: str, today: date,
                 parked: tuple[Parked, ...] | None = None) -> Parked | None:
    """The live parking of `key` on `today`, or None. A parking whose date has
    come is not live: the check is read on its own again."""
    for entry in (PARKED if parked is None else parked):
        if entry.check == key and today < entry.until:
            return entry
    return None


def retired_state() -> dict[str, str]:
    return {"kind": "retired",
            "label": "retired: the code no longer has this check"}


def parked_state(entry: Parked) -> dict[str, str]:
    return {"kind": "parked", "until": entry.until.isoformat(),
            "reason": entry.reason,
            "label": f"parked until {entry.until.isoformat()}: {entry.reason}"}


def override_state(key: str, *, registered: bool, today: date,
                   registry_loaded: bool = True,
                   parked: tuple[Parked, ...] | None = None) -> dict[str, str] | None:
    """`retired` when the registry (loaded) no longer has the check, else
    `parked` when the committed list says so, else None. Retired wins: a
    parking for a check that does not exist is not a reason to show it."""
    if registry_loaded and not registered:
        return retired_state()
    entry = parked_entry(key, today, parked)
    return parked_state(entry) if entry is not None else None


# ------------------------------------------------------- the self-test line

def self_test(checks: list[dict[str, Any]], read_at: datetime) -> dict[str, Any]:
    """The board's line about itself: how many checks ran, how many are
    UNKNOWN, how many are parked -- each with the time it was counted.

    `checks` are the overview's check dicts (`health`, `state`, `observed_at`,
    `stale`). Retired checks are not part of the total: they no longer exist.
    A check "ran" when its newest reading is fresh (not missing, not stale);
    a parked check that ran still counts as run, but is counted under parked
    and never under UNKNOWN. Counted now, from the rows just read."""
    at = read_at.isoformat()
    live = [c for c in checks if not _is(c, "retired")]
    parked = [c for c in live if _is(c, "parked")]
    rest = [c for c in live if not _is(c, "parked")]
    ran = [c for c in live if c.get("observed_at") and not c.get("stale")]
    unknown = [c for c in rest if c.get("health") == "unknown"]
    retired = len(checks) - len(live)
    oldest = min((c["observed_at"] for c in ran), default=None)
    return {
        "read_at": at,
        "checks_run": {"n": len(ran), "of": len(live), "read_at": at,
                       "oldest_reading_at": oldest},
        "unknown": {"n": len(unknown), "read_at": at},
        "parked": {"n": len(parked), "read_at": at},
        "retired": {"n": retired, "read_at": at},
    }


def _is(check: dict[str, Any], kind: str) -> bool:
    state = check.get("state")
    return bool(state) and state.get("kind") == kind


def reads_unknown_within(interval: timedelta) -> timedelta:
    """How long after its last reading a stopped collector reads UNKNOWN:
    three of its own intervals plus the grace (`freshness`). The test pins
    that a collector that stops is UNKNOWN no later than this."""
    return interval * STALE_FACTOR + STALE_GRACE


def today_utc(now: datetime | None = None) -> date:
    return (now or datetime.now(UTC)).astimezone(UTC).date()
