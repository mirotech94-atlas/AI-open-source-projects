"""When ATLAS is allowed to ring the phone, and what happens when he doesn't
answer.

COMMS.md calls the call budget "the single most important rule in this file",
and it is right for a reason that has nothing to do with engineering: an agent
that calls six times a day gets silenced, and a silenced agent is worth
nothing. Every rule here exists to protect the one thing that makes voice
useful — that when the phone rings, it matters.

  * **Three non-critical calls a day.** A fourth thing waits for the brief or
    joins the next call. CRITICAL is exempt and always rings.
  * **Never twice in ten minutes.** Anything arriving inside that window joins
    the next call rather than starting its own.
  * **Three declines in a row and he stops calling** for the rest of the day.
    Declining three times is information: something is going on, and hammering
    him is not the answer. It moves to Slack and says so in the brief.
  * **Quiet hours hold everything below CRITICAL**, which `comms._quiet_now`
    already enforces for the message; this stops the *call* as well.

The severity mapping, stated because COMMS.md names five levels and
`contracts.Severity` has four:

    INFO      -> hold for the brief
    NORMAL    -> the module's channel (COMMS.md's NOTICE and WARN)
    HIGH      -> call (COMMS.md's URGENT)
    CRITICAL  -> call, and keep trying

State is in memory. A restart forgets today's budget, which is the forgiving
direction to fail: it can only ever allow a call that should have waited, and
never suppress one that mattered. Persisting it would mean a database write in
the notification path, which is the one path that runs when things are already
wrong.
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from .contracts import Severity

log = logging.getLogger("atlas.escalation")

# COMMS.md 2.2. Non-critical only; CRITICAL is never counted or capped.
DAILY_CALL_BUDGET = 3
MIN_GAP = timedelta(minutes=10)
DECLINES_BEFORE_BACKING_OFF = 3

# COMMS.md 3.3, the CRITICAL row: call again in two minutes, then every five,
# three times.
CRITICAL_RETRIES = (timedelta(minutes=2), timedelta(minutes=5),
                    timedelta(minutes=5), timedelta(minutes=5))


@dataclass(frozen=True, slots=True)
class Call:
    at: datetime
    severity: Severity
    answered: bool | None = None       # None: still ringing, or never learned
    # Presence at the moment this call was placed -- `None` when it was never
    # read or came back unreadable. Only meaningful for the away-then-home
    # re-ring rule below (G3); the budget and decline rules never look at it.
    home: bool | None = None
    # The page's own `Outbound.title`, carried through only for the title +
    # window fallback below when no `incident_key` was given. Never compared
    # for the budget or decline rules.
    title: str | None = None
    # `Outbound.incident_key` -- an explicit, stable id for "this specific
    # ongoing thing" (the scheduler's `finding.check`, e.g. "flycheap.database"),
    # when the caller has one. Preferred over `title` for G3's re-ring rule
    # because two distinct incidents can share a human-readable title.
    incident_key: str | None = None


_CALLS: list[Call] = []


def _now() -> datetime:
    return datetime.now(UTC)


def reset() -> None:
    """For tests, and for `quiet` clearing the slate."""
    _CALLS.clear()


def record(severity: Severity, *, at: datetime | None = None,
          home: bool | None = None, title: str | None = None,
          incident_key: str | None = None) -> None:
    _CALLS.append(Call(at or _now(), severity, home=home, title=title,
                       incident_key=incident_key))


def record_outcome(answered: bool, *, at: datetime | None = None) -> None:
    """The platform tells us how a call ended. Attach it to the most recent
    one — or, if there is none, record the call from the outcome alone.

    The second branch exists because the call and its outcome can arrive in
    different processes. A call placed from `atlas.cli`, or from a shell
    against the container, records itself in that process and exits; the
    webhook then lands in the API, which has no memory of it. Dropping the
    outcome there would mean every such call was invisible to the back-off
    rule — the exact case the first test of the decline path hit.
    """
    if _CALLS and _CALLS[-1].answered is None:
        last = _CALLS[-1]
        _CALLS[-1] = Call(last.at, last.severity, answered, last.home,
                          last.title, last.incident_key)
        when = last.at
    else:
        when = at or _now()
        # Severity is unknown from the outcome alone. HIGH is the honest
        # default: it counts against the budget, which is the safe direction
        # to be wrong in, and CRITICAL is never counted anyway.
        _CALLS.append(Call(when, Severity.HIGH, answered))
    if not answered:
        log.info("call at %s was not answered", when.isoformat())


def _today(now: datetime) -> list[Call]:
    start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    return [c for c in _CALLS if c.at >= start]


def spent_today(now: datetime | None = None) -> int:
    now = now or _now()
    return sum(1 for c in _today(now) if c.severity < Severity.CRITICAL)


def declines_in_a_row(now: datetime | None = None) -> int:
    """Consecutive unanswered calls today, most recent first."""
    run = 0
    for call in reversed(_today(now or _now())):
        if call.answered is False:
            run += 1
        elif call.answered is True:
            break
        # answered is None: never learned, so it neither counts nor breaks.
    return run


def quiet_until() -> datetime | None:
    """`quiet 3h` — everything but CRITICAL held until this time."""
    raw = os.getenv("ATLAS_QUIET_UNTIL", "").strip()
    if not raw:
        return None
    try:
        when = datetime.fromisoformat(raw)
    except ValueError:
        log.warning("ATLAS_QUIET_UNTIL is not a timestamp: %r", raw)
        return None
    return when if when.tzinfo else when.replace(tzinfo=UTC)


# G3's incident correlation, independent of two other, unrelated 24h windows
# in this codebase: `_today()` above (a calendar-day boundary, for the daily
# budget) and `kernel.scheduler.page`'s own Postgres-backed dedup on
# `(module, title)` (a rolling 24h, but a different table for a different
# purpose -- whether to page at all, not whether an already-answered page
# should re-ring as a call or a text). This window lives entirely in this
# module's own in-memory state.
INCIDENT_WINDOW = timedelta(hours=24)


def _incident_matches(call: Call, *, incident_key: str | None,
                      title: str | None, now: datetime) -> bool:
    """Does this recorded CRITICAL `call` count as "the same page" as the one
    being asked about right now?

    An explicit `incident_key` -- the scheduler's `finding.check`, e.g.
    "flycheap.database" -- matches exactly and without a time bound of its
    own: the key already names one specific check, so two calls under it are
    the same thing however far apart they land, and a call recorded under a
    *different* key (or no key) never matches even if the title happens to
    coincide.

    Without one, this falls back to `title` alone, but only within a rolling
    24h window from the earlier call: two same-titled pages more than a day
    apart are treated as unrelated rather than "the same still-open
    incident" -- a caller that never supplies an incident key gets a softer
    guarantee than one that does, which is the honest trade for not having a
    real id. A call that *did* carry an incident key is never matched by
    title alone, so an explicitly-keyed page and a title-only one are never
    confused for each other.
    """
    if call.severity < Severity.CRITICAL:
        return False
    if incident_key:
        return call.incident_key == incident_key
    if call.incident_key or not title or call.title != title:
        return False
    return now - call.at <= INCIDENT_WINDOW


def _already_settled_while_away(now: datetime, *, title: str | None,
                                incident_key: str | None) -> bool:
    """True when the most recent matching CRITICAL page was placed while he
    was away and has since been answered.

    G3 of the reel audit: "I won't call again once you've arrived." A page
    that doesn't match (a different incident, or the same title outside the
    24h fallback window) is never affected; the moment this exact incident
    re-pages it is a fresh `Call` with `answered=None`, so this returns False
    again too -- it only softens the specific page he already dealt with.
    """
    matching = [c for c in _CALLS
               if _incident_matches(c, incident_key=incident_key, title=title,
                                    now=now)]
    if not matching:
        return False
    last = matching[-1]
    return bool(last.answered is True and last.home is False)


def may_call(severity: Severity, *, now: datetime | None = None,
            home: bool | None = None, title: str | None = None,
            incident_key: str | None = None) -> tuple[bool, str]:
    """May ATLAS ring the phone right now? Returns (yes, why not).

    The reason matters as much as the verdict: it goes in the Slack post
    that replaces the call, so the message says "I'd have rung but that's
    your third today" rather than arriving with no explanation.

    `home`, `title` and `incident_key` are passed in by
    `comms.candidates`/`notify` only for CRITICAL (nothing else ever rings,
    and reading presence or tracking a page's identity for a call that will
    never happen has no reason to exist). Together they only ever narrow
    CRITICAL further, and only in one direction: if he is *not* known to be
    home, or this exact page wasn't already answered while he was away, this
    changes nothing (invariant 6: never guess).
    """
    now = now or _now()

    # CRITICAL is exempt from every rule here except one: he already
    # answered this exact page while he was out, and now he's home. The
    # promise is "I won't call again once you've arrived" -- not "I will
    # never page you again" -- so this narrows the call to a text rather
    # than dropping the exemption altogether, and only for that one page.
    if severity >= Severity.CRITICAL:
        if home is True and _already_settled_while_away(
                now, title=title, incident_key=incident_key):
            return False, ("you already answered this page while you were "
                          "out, and you're home now -- texting instead of "
                          "calling again")
        return True, ""

    if severity < Severity.HIGH:
        return False, "below the level that warrants a call"

    held = quiet_until()
    if held and now < held:
        return False, f"quiet until {held.strftime('%H:%M')}"

    recent = [c for c in _CALLS if now - c.at < MIN_GAP]
    if recent:
        return False, "called within the last ten minutes; this joins the next one"

    declines = declines_in_a_row(now)
    if declines >= DECLINES_BEFORE_BACKING_OFF:
        return False, (f"{declines} calls in a row went unanswered, so I've "
                       f"stopped ringing for today")

    spent = spent_today(now)
    if spent >= DAILY_CALL_BUDGET:
        return False, (f"that's {spent} calls today already, which is the "
                       f"budget")

    return True, ""


def backed_off(now: datetime | None = None) -> bool:
    """Has he declined enough that ATLAS has gone quiet? For the brief."""
    return declines_in_a_row(now) >= DECLINES_BEFORE_BACKING_OFF


def ladder(severity: Severity) -> list[str]:
    """What to do, in order, for this severity. COMMS.md 3.3.

    Named rather than executed here so the scheduler and the brief can both
    describe it without either owning it.
    """
    if severity <= Severity.INFO:
        return ["hold for the brief"]
    if severity < Severity.HIGH:
        return ["post to the module's channel"]
    if severity < Severity.CRITICAL:
        return ["call", "if declined: decisions channel and the module's channel",
                "if unanswered: voicemail, then the decisions channel"]
    return ["call", "call again in 2 minutes",
            "then every 5 minutes, three times", "decisions channel throughout"]
