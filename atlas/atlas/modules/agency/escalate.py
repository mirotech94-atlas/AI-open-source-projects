"""agency.escalate -- when a lead conversation rings the owner's phone.

the owner, 2026-09-29: "Phone me ONLY for emergencies." Everything else from a
lead -- a question, "not now", a hot lead, a deal agreed -- goes to Slack
(and, where there is something to approve, a card). An emergency is:

  * a complaint or an angry customer, or a legal threat (`EMERGENCY`);
  * a lead asking for something outside the rules: a price below the floor
    that applies, custom terms, a refund, paying after launch
    (`OUTSIDE_RULES`, plus the price check in `pricing`);
  * a system failure in the middle of a call ATLAS placed.

Detection is regex, on the lead's words, before any model -- a model's
reading of "is this a legal threat" is not a thing to ring a phone on, and
it can be steered by the words it reads.

The ring itself goes through this module, not `comms.notify`: routing HIGH
to the phone for everyone would break his 2026-09-22 rule for every other
module, and CRITICAL skips quiet hours and the call budget. So, in order:

  1. Slack always gets the full story (topic `agency`).
  2. Quiet hours (`QUIET_HOURS`) and `escalation.may_call(HIGH)` -- his
     quiet-until, the ten-minute gap, three unanswered calls in a row and
     the three-a-day budget -- decide whether the phone may ring at all.
  3. At most one agency call per `AGENCY_ESCALATE_GAP_MIN` (default 15):
     anything inside the gap is held and joins the next call, one call for
     the batch. A held line older than two hours is dropped (Slack has it).

What is SPOKEN is built here from ATLAS's own labels -- the business name
from our table and the kind of emergency -- never from the lead's words:
those are in Slack, quoted.
"""
from __future__ import annotations

import logging
import os
import re
from datetime import UTC, datetime, timedelta

from ...kernel.contracts import Severity

log = logging.getLogger("atlas.agency.escalate")

# A threat or anger rings; "is this a scam?" alone does not -- it is an
# ordinary cold-outreach question the playbook answers. Scam/fraud counts
# only beside a threat word.
EMERGENCY = re.compile(
    r"\b(?:lawyer|attorney|lawsuit|sue|suing|legal\s+action|cease\s+and\s+desist|"
    r"small\s+claims|court|harass\w*|report\s+(?:you|this)|"
    r"ftc|attorney\s+general|better\s+business\s+bureau|bbb|police|"
    r"(?<!no )complain\w*|furious|outraged|disgusted|unacceptable|how\s+dare|"
    r"angry|pissed|f+u+c+k+\w*|wtf)\b", re.IGNORECASE)
OUTSIDE_RULES = re.compile(
    r"\b(?:refund\w*|money\s+back|charge\s?back|custom\s+(?:terms|contract)|"
    r"net\s?\d{2}|(?:change|amend|modify|rewrite)\s+the\s+(?:contract|terms|"
    r"agreement)|revenue\s+share|equity|pay\s+(?:you\s+)?(?:later|after|once)|"
    r"for\s+free|free\s+of\s+charge|no\s+charge|on\s+commission)\b", re.IGNORECASE)

HOLD_FOR = timedelta(hours=2)
_PENDING: list[tuple[datetime, str]] = []
_LAST_RING: datetime | None = None


def gap() -> timedelta:
    try:
        return timedelta(minutes=max(1, int(os.getenv("AGENCY_ESCALATE_GAP_MIN", "15"))))
    except ValueError:
        return timedelta(minutes=15)


def emergency_kind(text: str) -> str | None:
    """The kind of emergency in the lead's words, as ATLAS's own label."""
    if EMERGENCY.search(text or ""):
        return "a complaint or a legal threat"
    if OUTSIDE_RULES.search(text or ""):
        return "a request outside the rules"
    return None


def reset() -> None:
    """For tests."""
    global _LAST_RING
    _PENDING.clear()
    _LAST_RING = None


def _now() -> datetime:
    return datetime.now(UTC)


async def _gates(now: datetime) -> str | None:
    """Why the phone may not ring now, or None."""
    from ...kernel import comms, escalation
    if comms._quiet_now():
        return "quiet hours"
    ok, why = escalation.may_call(Severity.HIGH, now=now)
    if not ok:
        return why
    if _LAST_RING is not None and now - _LAST_RING < gap():
        return f"an agency call went out under {int(gap().total_seconds() // 60)} min ago"
    voice = comms.CHANNELS.get("voice")
    if voice is None or not await voice.available():
        return "the voice channel is not configured"
    return None


async def _ring(lines: list[str], now: datetime, title: str | None = None) -> None:
    global _LAST_RING
    from ...kernel import comms, escalation
    from ...kernel.comms import Outbound
    voice = comms.CHANNELS["voice"]
    body = " ".join(lines)
    if title is None:
        title = ("Agency emergency" if len(lines) == 1
                 else f"{len(lines)} agency emergencies")
    await voice.send(Outbound(title=title, body=body, severity=Severity.HIGH,
                              topic="agency", kind="alert"))
    escalation.record(Severity.HIGH, title=title)
    _LAST_RING = now


async def emergency(business: str, kind: str, slack_body: str) -> str:
    """Slack now; the phone when the gates allow, else held for the next
    call. Returns what happened, in words, for the caller's record. Never
    raises: an emergency that cannot ring still reached Slack."""
    from ...kernel.comms import Outbound, notify
    now = _now()
    spoken = f"Agency: {business or 'a lead'}. {kind[:1].upper()}{kind[1:]}. Details are in Slack."
    outcome = "held"
    try:
        why = await _gates(now)
        if why is None:
            lines = [line for _at, line in _PENDING] + [spoken]
            _PENDING.clear()
            await _ring(lines, now)
            outcome = "rang the owner"
        else:
            if why != "quiet hours":
                _PENDING.append((now, spoken))
            outcome = f"not rung: {why}"
    except Exception as exc:                            # noqa: BLE001
        log.warning("agency emergency call failed: %s", type(exc).__name__)
        outcome = f"not rung: the call failed ({type(exc).__name__})"
    try:
        await notify(Outbound(
            title=f"Agency emergency: {business or 'a lead'} ({kind})",
            body=f"{slack_body}\n\nPhone: {outcome}.",
            severity=Severity.HIGH, topic="agency", kind="alert"))
    except Exception as exc:                            # noqa: BLE001
        log.warning("agency emergency not posted: %s", type(exc).__name__)
    return outcome


async def ring_owner(title: str, line: str, *, slack_body: str | None = None) -> str:
    """Ring the owner -- only ever the owner's own voice channel, there is no
    number to pass -- with `line`, under the same gates as an emergency
    (quiet hours, `escalation.may_call(HIGH)`, the agency call gap).
    Slack always gets it. A gated call is not held: it is news, not an
    emergency, and Slack has it. `line` is built by ATLAS from its own
    labels, never from text it read. Never raises."""
    from ...kernel.comms import Outbound, notify
    now = _now()
    outcome = "held"
    try:
        why = await _gates(now)
        if why is None:
            await _ring([line], now, title=title)
            outcome = "rang the owner"
        else:
            outcome = f"not rung: {why}"
    except Exception as exc:                            # noqa: BLE001
        log.warning("agency owner call failed: %s", type(exc).__name__)
        outcome = f"not rung: the call failed ({type(exc).__name__})"
    try:
        await notify(Outbound(
            title=title, body=f"{slack_body or line}\n\nPhone: {outcome}.",
            severity=Severity.HIGH, topic="agency", kind="alert"))
    except Exception as exc:                            # noqa: BLE001
        log.warning("agency owner call not posted: %s", type(exc).__name__)
    return outcome


async def flush() -> str | None:
    """Ring for what was held, once the gates allow. Called each signals
    cycle. Stale lines are dropped: Slack already has them."""
    now = _now()
    _PENDING[:] = [(at, line) for at, line in _PENDING if now - at < HOLD_FOR]
    if not _PENDING:
        return None
    try:
        if await _gates(now) is not None:
            return None
        lines = [line for _at, line in _PENDING]
        _PENDING.clear()
        await _ring(lines, now)
        return "rang the owner"
    except Exception as exc:                            # noqa: BLE001
        log.warning("agency held call failed: %s", type(exc).__name__)
        return None
