"""A record of every call ATLAS places, and a receipt for the ones he took.

the owner's voice-partner brief asks that a call leave a trail: what it was
about, what he was asked, what was pressed, and how it ended. Until now a
decision left a receipt, a missed call left a Slack post, and a call he
answered without deciding left nothing at all -- the next morning there was
no way to tell "I heard it and chose to wait" from "it never rang".

`voice_calls` has one row per call: the opening exactly as composed, the
question, the cards, each key press that decided, and the outcome. It holds
ATLAS's own words and card ids, never audio and never a transcript of his
speech. Rows are written best-effort: a database that is down must not stop
a call from being placed or a decision from running.

`answered_note()` words the receipt for a call he took and decided nothing
on, so the open card is named again where he will see it.
"""
from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field

log = logging.getLogger("atlas.callrecord")

KEEP_S = 6 * 3600
MAX_TRACKED = 128


@dataclass(slots=True)
class Record:
    title: str
    opening: str
    ask: str
    pids: list[int]
    attempt: int
    at: float
    decided: list[dict] = field(default_factory=list)
    turn_ms: list[int] = field(default_factory=list)


_CALLS: dict[str, Record] = {}


def reset() -> None:
    _CALLS.clear()


def get(sid: str) -> Record | None:
    return _CALLS.get(sid)


def _remember(sid: str, rec: Record) -> None:
    now = time.monotonic()
    for old in [k for k, v in _CALLS.items() if now - v.at > KEEP_S]:
        _CALLS.pop(old, None)
    while len(_CALLS) >= MAX_TRACKED:
        _CALLS.pop(next(iter(_CALLS)))
    _CALLS[sid] = rec


async def placed(sid: str | None, *, title: str, opening: str, ask: str = "",
                 pids: list[int] | None = None, incident_key: str | None = None,
                 away: bool = False, night: bool = False, attempt: int = 0) -> None:
    if not sid:
        return
    _remember(sid, Record(title, opening, ask, list(pids or []), attempt, time.monotonic()))
    try:
        from .state import execute_sql
        await execute_sql(
            """INSERT INTO voice_calls
                   (call_sid, attempt, title, opening, ask, pids, incident_key, away, night)
               VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
               ON CONFLICT (call_sid) DO NOTHING""",
            sid, attempt, title[:300], opening[:1200], ask[:300], list(pids or []),
            incident_key, away, night)
    except Exception as exc:                            # noqa: BLE001
        log.warning("call %s not recorded: %s", sid[:10], type(exc).__name__)


async def decided(sid: str, pid: int, verdict: str, via: str, result: str = "") -> None:
    """A key press that decided a card on this call."""
    entry = {"pid": int(pid), "verdict": verdict, "via": via.split(":", 1)[0],
             "result": result[:200]}
    rec = _CALLS.get(sid)
    if rec is not None:
        rec.decided.append(entry)
    try:
        from .state import execute_sql
        await execute_sql(
            "UPDATE voice_calls SET decided = decided || $2::jsonb WHERE call_sid = $1",
            sid, json.dumps([entry]))
    except Exception as exc:                            # noqa: BLE001
        log.warning("decision on call %s not recorded: %s", sid[:10], type(exc).__name__)


TURNS_KEPT = 60


def turn(sid: str, ms: float) -> None:
    """How long ATLAS took to answer one turn: from Twilio's request
    arriving to the reply being ready to send. This is ATLAS's share of the
    silence he hears; the phone network adds its own on top, which only a
    recording could measure and none is kept."""
    rec = _CALLS.get(sid)
    if rec is not None and len(rec.turn_ms) < TURNS_KEPT:
        rec.turn_ms.append(max(0, round(ms)))


def timing(sid: str) -> dict[str, int] | None:
    """Turns, median and slowest answer time for a call, in milliseconds."""
    rec = _CALLS.get(sid)
    if rec is None or not rec.turn_ms:
        return None
    ordered = sorted(rec.turn_ms)
    return {"turns": len(ordered), "median_ms": ordered[len(ordered) // 2],
            "slowest_ms": ordered[-1]}


async def ended(sid: str, outcome: str, answered_by: str = "") -> None:
    """How it ended: answered | missed | machine."""
    rec = _CALLS.get(sid)
    try:
        from .state import execute_sql
        await execute_sql(
            "UPDATE voice_calls SET outcome = $2, answered_by = $3, turn_ms = $4, "
            "ended_at = now() WHERE call_sid = $1",
            sid, outcome, answered_by[:40] or None, list(rec.turn_ms) if rec else [])
    except Exception as exc:                            # noqa: BLE001
        log.warning("end of call %s not recorded: %s", sid[:10], type(exc).__name__)


def answered_note(sid: str) -> tuple[str, str] | None:
    """(title, body) of the receipt for a call he took and decided nothing
    on, or None when there is nothing to say: a call with no card, or one
    whose cards were all answered on the keypad (those have their own
    receipt)."""
    rec = _CALLS.get(sid)
    if rec is None or not rec.pids:
        return None
    done = {int(d["pid"]) for d in rec.decided}
    waiting = [pid for pid in rec.pids if pid not in done]
    if not waiting:
        return None
    cards = ", ".join(f"#{pid}" for pid in waiting)
    body = (f"I rang about: {rec.title}\nI asked: {rec.ask or 'no question'}\n"
            f"No key was pressed, so nothing ran and nothing was declined. "
            f"{cards} {'is' if len(waiting) == 1 else 'are'} still waiting here.")
    return "You took my call; nothing was decided", body


def forget(sid: str) -> None:
    _CALLS.pop(sid, None)
