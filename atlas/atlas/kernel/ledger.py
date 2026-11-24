"""The mistake ledger: the same failure, recognised across nights.

`lessons` already refuses to repeat an action with the SAME parameters that
failed twice. What it cannot see is one fault wearing different parameters:
a provider that rejects every post, a tool that times out for every site.
Each is a fresh pair of (action, params) to `lessons`, so nothing ever
reaches two. the owner's operations brief (2026-10-04) asks for a ledger that
knows a mistake when it comes back.

Nightly, after the review, `nightly()` reads the week's `failed_action`
lessons, strips what varies (numbers, ids, quoted values, addresses) from
each error, and groups them by action and the shape that is left. A group
is one mistake, with how often it happened, across how many different
parameters, and since when. Each mistake is in one state:

  new        seen, not yet a pattern
  recurring  three times, or under two different sets of parameters
  closed     not seen for CLOSE_DAYS
  reopened   closed, and it came back

He is told once when a mistake becomes recurring and once each time it
reopens, in one digest message with the evidence. That is all this does.
It never changes a tier, a rule, a parameter or a line of code, and it
never hands ATLAS's own code to a coding agent: `solve` already forbids
that, and learning here is a report to the person who can change things.
The archive's "compile a validator and load it" is self-modification and
is not built.

Everything is derived from `lessons`, at night, off the action path: the
executor and `lessons.record` are untouched.
"""
from __future__ import annotations

import hashlib
import logging
import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

log = logging.getLogger("atlas.ledger")

WINDOW_DAYS = 7
CLOSE_DAYS = 14
RECURRING_TIMES = 3
SAMPLE_MAX = 200

_PREFIX = re.compile(r"^\S+ failed:\s*", re.IGNORECASE)
_URL = re.compile(r"https?://\S+")
_QUOTED = re.compile(r"""(['"`])[^'"`]{1,200}\1""")
_HEX = re.compile(r"\b[0-9a-f]{8,}\b", re.IGNORECASE)
_NUM = re.compile(r"\d+(?:[.,:]\d+)*")


def normalise(lesson: str) -> str:
    """The error with everything that varies taken out, so the same fault
    under different parameters reads the same."""
    text = _PREFIX.sub("", " ".join((lesson or "").split()))
    text = _URL.sub("<url>", text)
    text = _QUOTED.sub("<value>", text)
    text = _HEX.sub("<id>", text)
    text = _NUM.sub("<n>", text)
    return text.lower()[:300]


def fingerprint(action: str, lesson: str) -> str:
    return hashlib.sha256(f"{action}\n{normalise(lesson)}".encode()).hexdigest()[:16]


@dataclass(frozen=True, slots=True)
class Mistake:
    fingerprint: str
    action: str
    sample: str
    occurrences: int
    variants: int               # different parameter sets it failed under
    first_seen: datetime
    last_seen: datetime


def group(rows: list[dict]) -> list[Mistake]:
    """`lessons` rows (action, lesson, times, at, last_at) -> one Mistake
    per fingerprint."""
    seen: dict[str, dict] = {}
    for r in rows:
        action = str(r.get("action") or "")
        lesson = str(r.get("lesson") or "")
        if not action or not lesson:
            continue
        fp = fingerprint(action, lesson)
        g = seen.setdefault(fp, {"action": action, "sample": lesson[:SAMPLE_MAX],
                                 "n": 0, "v": 0, "first": r["at"], "last": r["last_at"]})
        g["n"] += max(1, int(r.get("times") or 1))
        g["v"] += 1
        g["first"] = min(g["first"], r["at"])
        g["last"] = max(g["last"], r["last_at"])
    return [Mistake(fp, g["action"], g["sample"], g["n"], g["v"], g["first"], g["last"])
            for fp, g in seen.items()]


def is_recurring(m: Mistake) -> bool:
    return m.occurrences >= RECURRING_TIMES or m.variants >= 2


@dataclass(frozen=True, slots=True)
class Step:
    mistake: Mistake
    state: str                  # new | recurring | reopened
    tell: bool                  # say it tonight
    reopened: bool = False


def plan(mistakes: list[Mistake], known: dict[str, dict], now: datetime
         ) -> tuple[list[Step], list[str]]:
    """What tonight's pass writes: a Step per mistake seen this week, and
    the fingerprints to close (known, open, and silent for CLOSE_DAYS).

    `known` maps fingerprint -> {state, told_at, closed_at, last_seen}."""
    steps: list[Step] = []
    live = {m.fingerprint for m in mistakes}
    for m in mistakes:
        prior = known.get(m.fingerprint)
        if prior is not None and prior.get("state") == "closed":
            # It was gone, and it is back: the earlier fix did not hold.
            came_back = prior.get("closed_at") is None or m.last_seen > prior["closed_at"]
            if came_back:
                steps.append(Step(m, "reopened", True, reopened=True))
                continue
        if is_recurring(m):
            already = prior is not None and prior.get("told_at") is not None \
                and prior.get("state") in ("recurring", "reopened")
            steps.append(Step(m, prior["state"] if already else "recurring", not already))
        else:
            steps.append(Step(m, "new", False))
    cutoff = now - timedelta(days=CLOSE_DAYS)
    close = [fp for fp, k in known.items()
             if fp not in live and k.get("state") != "closed"
             and k.get("last_seen") is not None and k["last_seen"] < cutoff]
    return steps, close


def summary_line(steps: list[Step], closed: int) -> str:
    n = {s: sum(1 for x in steps if x.state == s) for s in ("new", "recurring", "reopened")}
    return (f"Ledger: {n['new']} new, {n['recurring']} recurring, "
            f"{n['reopened']} reopened, {closed} closed")


def digest(steps: list[Step]) -> str:
    """The evidence for what he is told tonight, one block per mistake."""
    blocks = []
    for s in steps:
        if not s.tell:
            continue
        m = s.mistake
        lead = ("Came back after it had stopped" if s.reopened else "Keeps failing")
        blocks.append(
            f"{lead}: `{m.action}`\n"
            f"{m.occurrences} time(s) under {m.variants} different set(s) of parameters, "
            f"first {m.first_seen:%d %b %H:%M} UTC, last {m.last_seen:%d %b %H:%M} UTC.\n"
            f"Example: {m.sample}")
    return "\n\n".join(blocks)


async def nightly(now: datetime | None = None) -> str | None:
    """Read the week's failures, update the ledger, tell him what is new to
    tell. Returns the summary line, or None when nothing could be read."""
    now = now or datetime.now(UTC)
    from .state import execute_sql, fetch_all
    try:
        rows = await fetch_all(
            """SELECT action, lesson, times, at, last_at FROM lessons
                WHERE kind = 'failed_action' AND action IS NOT NULL
                  AND last_at > $1""", now - timedelta(days=WINDOW_DAYS))
        known_rows = await fetch_all(
            "SELECT fingerprint, state, told_at, closed_at, last_seen FROM ledger_mistakes")
    except Exception as exc:                            # noqa: BLE001
        log.warning("ledger: could not read (%s); nothing changed", type(exc).__name__)
        return None
    known = {str(r["fingerprint"]): dict(r) for r in known_rows or []}
    steps, close = plan(group([dict(r) for r in rows or []]), known, now)
    for s in steps:
        m = s.mistake
        await execute_sql(
            """INSERT INTO ledger_mistakes
                   (fingerprint, action, sample, occurrences, variants, first_seen,
                    last_seen, state, told_at, recurrences_after_close)
               VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, 0)
               ON CONFLICT (fingerprint) DO UPDATE
                   SET occurrences = $4, variants = $5, last_seen = $7, state = $8,
                       sample = $3,
                       told_at = COALESCE($9, ledger_mistakes.told_at),
                       closed_at = CASE WHEN $10 THEN NULL ELSE ledger_mistakes.closed_at END,
                       recurrences_after_close = ledger_mistakes.recurrences_after_close
                                                 + CASE WHEN $10 THEN 1 ELSE 0 END""",
            m.fingerprint, m.action, m.sample, m.occurrences, m.variants, m.first_seen,
            m.last_seen, s.state, now if s.tell else None, s.reopened)
    for fp in close:
        await execute_sql(
            "UPDATE ledger_mistakes SET state = 'closed', closed_at = $2 WHERE fingerprint = $1",
            fp, now)
    line = summary_line(steps, len(close))
    body = digest(steps)
    if body:
        try:
            from .comms import Outbound, notify
            from .contracts import Severity
            await notify(Outbound(title=line, body=body, severity=Severity.NORMAL,
                                  topic="brief", kind="digest"))
        except Exception as exc:                        # noqa: BLE001
            log.warning("ledger: digest not posted (%s)", type(exc).__name__)
    log.info(line)
    return line
