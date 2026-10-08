"""The lessons ledger. A mistake is allowed once; it is not allowed forever.

Miro's rule, verbatim: "learn all the skills and improve based on what it has
learned and made mistakes on; mistakes are okay, permanent mistakes
unacceptable." This file is the mechanism for the second half of that
sentence, and it has exactly two moving parts:

  * a ledger — every declined proposal, every action that raised, every
    `went_wrong` line from the nightly review, written to `lessons` and
    reinforced (`times`) rather than duplicated;
  * a never-repeat guard — `blocked()` reads that ledger back, and
    `approvals.propose()` refuses to raise the same action with the same
    parameters a third time until he says why it is different.

What it reads: the `lessons` and `reviews` tables, and procedural memory.
What it may do: write rows and memories. Nothing else — it never imports the
executor, never proposes, and never changes a tier. A lesson is advice the
harness quotes, never an instruction it obeys (invariant 4), and never a
route around the approval gate (invariant 3): the strongest thing a lesson
can do is stop ATLAS asking, which is always safe.

Every function here swallows its own database errors and returns the empty
answer — "" for `for_task`, `None` for `blocked`, `0` for `record`. That is
deliberate and it is the one place this file fails OPEN: the ledger sits on
the approval path, the harness path and the executor's error path, and a
database blip must not be able to stop ATLAS proposing, thinking or
reporting a failure. Nothing is auto-executed because a lesson was
unreadable; the Tier 2 gate is still the control.
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
from datetime import date
from typing import Any

from .memory import Kind, recall, remember
from .state import execute_sql, fetch_all, fetch_one

log = logging.getLogger("atlas.lessons")

# The kinds that can refuse a proposal. `told` is deliberately not one of
# them: `clear()` writes a `told` row against the same action and parameters,
# and if that counted, clearing a lesson twice would start blocking on the
# clearance itself. `review` is not one either — a review line names a
# problem, not an action and its parameters, so it has nothing to key on.
# `lost_experiment` IS one: an experiment that lost against its baseline
# twice, with the same parameters, inside a month is a permanent mistake in
# the making, and re-running it a third time needs him to say what changed.
# Nothing writes that kind yet; 17.2 will.
BLOCKING_KINDS: tuple[str, ...] = ("failed_action", "declined",
                                   "lost_experiment")
REPEATS_BEFORE_BLOCK = 2        # a mistake is allowed once
WINDOW_DAYS = 30                # older than this and the world has moved on
MAX_FOR_TASK = 5                # a prompt is not a manual
MAX_LESSON_CHARS = 200          # one line each; a prompt is not a manual
NAMESPACE = "lessons"

# The only `went_wrong` lines that may become lessons: the three shapes
# kernel/review.py writes, built from registry names and counts alone --
# "<action> failed, N time(s)", "<action> — expired unanswered", and
# "<property.check> — still failing|degraded". A lesson lands in every
# agent's SYSTEM prompt, so its text must be ATLAS's own (invariant 4). A
# review line used to be the finding's detail, which quotes whatever the
# collector saw -- a room name, a sender, a subject.
_REVIEW_LINE = re.compile(
    r"^[A-Za-z0-9_-]+(?:\.[A-Za-z0-9_-]+)+ "
    r"(?:failed, \d+ times?|— expired unanswered|— still (?:failing|degraded))$")


def atlas_authored(kind: str | None, lesson: str) -> bool:
    """May this lesson's text sit in a system prompt?

    Every kind but `review` is composed here or in the kernel from action
    names, error types and counts ("You declined X.", "X failed: HTTP 403
    from host"). A `review` lesson must match one of review.py's shapes;
    anything else -- a row written before that rule -- is never shown.
    """
    if kind != "review":
        return True
    return bool(_REVIEW_LINE.match(" ".join((lesson or "").split())))


def params_key(params: dict) -> str:
    """A stable fingerprint of the parameters, so "the same action again"
    means the same thing whichever order the reasoner wrote the keys in."""
    canonical = json.dumps(params or {}, sort_keys=True, default=str)
    return hashlib.sha256(canonical.encode()).hexdigest()


async def record(
    kind: str,
    lesson: str,
    *,
    agent: str | None = None,
    action: str | None = None,
    params: dict | None = None,
) -> int:
    """Write a lesson, or reinforce the one already there. Returns its id.

    Reinforcement rather than insertion is the whole point: `times` is what
    `blocked()` counts, and a ledger that inserted a fresh row per failure
    would never reach two of anything.

    The lookup is bounded by the same 30-day window `blocked()` reads, and
    that bound is load-bearing. Without it a row nobody has touched since
    last year is bumped to `times = 2` by one fresh failure, and the next
    proposal is refused on the strength of a mistake the world has moved
    past. A stale row is left where it is and the new one starts its own
    count.

    The procedural memory is written once, when the lesson is new. Writing it
    on every repeat would fill recall with the same sentence and push the
    other lessons out of `for_task`.
    """
    lesson = " ".join((lesson or "").split())
    if not lesson:
        return 0
    key = params_key(params or {}) if action else None

    try:
        if action:
            existing = await fetch_one(
                """SELECT id FROM lessons
                    WHERE kind = $1 AND action = $2 AND params_key = $3
                      AND cleared_at IS NULL
                      AND last_at > now() - make_interval(days => $4)
                    ORDER BY last_at DESC LIMIT 1""",
                kind, action, key, WINDOW_DAYS)
        else:
            # No action to key on (a review line, something he told ATLAS):
            # the text itself is the identity.
            existing = await fetch_one(
                """SELECT id FROM lessons
                    WHERE kind = $1 AND action IS NULL AND lesson = $2
                      AND cleared_at IS NULL
                      AND last_at > now() - make_interval(days => $3)
                    ORDER BY last_at DESC LIMIT 1""",
                kind, lesson, WINDOW_DAYS)
        if existing is not None:
            await execute_sql(
                "UPDATE lessons SET times = times + 1, last_at = now() "
                "WHERE id = $1", int(existing["id"]))
            return int(existing["id"])
    except Exception as exc:                            # noqa: BLE001
        log.warning("lessons: could not read the ledger: %s", exc)
        return 0

    memory_id: int | None = None
    try:
        memory_id = await remember(
            Kind.PROCEDURAL, NAMESPACE, lesson,
            meta={"kind": kind, "agent": agent, "action": action},
            importance=0.7)
    except Exception as exc:                            # noqa: BLE001
        log.warning("lessons: could not remember %r: %s", lesson[:60], exc)

    try:
        row = await fetch_one(
            """INSERT INTO lessons (kind, agent, action, params_key, lesson,
                                    memory_id)
               VALUES ($1,$2,$3,$4,$5,$6) RETURNING id""",
            kind, agent, action, key, lesson, memory_id)
    except Exception as exc:                            # noqa: BLE001
        log.warning("lessons: could not write the ledger: %s", exc)
        return 0
    return int(row["id"]) if row else 0


def _one_line(text: str) -> str:
    """One line, capped. Five lessons of unbounded length would be a bigger
    block than the skills they sit under, and a lesson that needs a paragraph
    is a lesson nobody will act on."""
    flat = " ".join((text or "").split())
    if len(flat) > MAX_LESSON_CHARS:
        flat = flat[:MAX_LESSON_CHARS - 1].rstrip() + "…"
    return flat


def _refusal(times: int, since: Any, lesson: str) -> str:
    """The sentence he hears. It says what happened, when it started, and
    exactly what unlocks it — a refusal with no way out is a dead end."""
    how_often = "twice" if times == 2 else f"{times} times"
    when = since.strftime("%-d %B") if hasattr(since, "strftime") else str(since)
    lesson = lesson.rstrip(".")
    return (f"This failed {how_often} since {when}: {lesson}. "
            "Say why it is different to clear it.")


async def blocked(action: str, params: dict) -> str | None:
    """Has this exact action with these exact parameters already failed twice?

    Returns the sentence to refuse with, or None. Fails open — see the module
    docstring. `cleared_at IS NULL` and the 30-day window are both in the
    query rather than in Python so a cleared or stale lesson can never be
    read back as a live one.
    """
    try:
        row = await fetch_one(
            """SELECT lesson, at, times FROM lessons
                WHERE action = $1 AND params_key = $2 AND kind = ANY($3)
                  AND cleared_at IS NULL
                  AND times >= $4
                  AND last_at > now() - make_interval(days => $5)
                ORDER BY times DESC, last_at DESC LIMIT 1""",
            action, params_key(params or {}), list(BLOCKING_KINDS),
            REPEATS_BEFORE_BLOCK, WINDOW_DAYS)
    except Exception as exc:                            # noqa: BLE001
        log.warning("lessons: could not check the never-repeat ledger: %s", exc)
        return None
    if row is None:
        return None
    return _refusal(int(row["times"]), row["at"], str(row["lesson"]))


async def clear(action: str, params: dict, why: str) -> None:
    """He said it is different now. Unblock it, and record that he said so.

    The clearance is itself a lesson (`kind='told'`), because "he overrode the
    guard on 12 October because the API had changed" is exactly the kind of
    thing that is obvious for a week and unrecoverable after a month.
    """
    key = params_key(params or {})
    try:
        await execute_sql(
            """UPDATE lessons SET cleared_at = now()
                WHERE action = $1 AND params_key = $2 AND cleared_at IS NULL
                  AND kind = ANY($3)""",
            action, key, list(BLOCKING_KINDS))
    except Exception as exc:                            # noqa: BLE001
        log.warning("lessons: could not clear %s: %s", action, exc)
        return
    await record("told", f"{action} is different now: {why}",
                 action=action, params=params)


async def reblock(action: str, params: dict) -> None:
    """Undo a `clear()`: `lessons.clear`'s rollback. The action is Tier 2 --
    the block is his to lift -- and keeps a rollback because un-clearing
    puts the guard back exactly as it was. It deliberately does not remove
    the `told` row — he did say it, and that stays on the record.
    """
    try:
        await execute_sql(
            """UPDATE lessons SET cleared_at = NULL
                WHERE action = $1 AND params_key = $2 AND kind = ANY($3)""",
            action, params_key(params or {}), list(BLOCKING_KINDS))
    except Exception as exc:                            # noqa: BLE001
        log.warning("lessons: could not re-block %s: %s", action, exc)


async def for_task(agent: str, task: str) -> str:
    """Up to five lessons for this agent, as a prompt block, or "".

    Rendered exactly like a skill block and injected in the same place, so
    the model reads it the same way: procedural guidance, not instructions
    from anybody. Recall first (what is relevant to THIS task), then the
    heaviest recent rows, deduplicated, capped.
    """
    lines: list[str] = []
    try:
        for memory in await recall(task, [NAMESPACE], kinds=(Kind.PROCEDURAL,),
                                   limit=MAX_FOR_TASK):
            if atlas_authored((memory.meta or {}).get("kind"), memory.content):
                lines.append(_one_line(memory.content))
    except Exception as exc:                            # noqa: BLE001
        log.warning("lessons: could not recall for %s: %s", agent, exc)

    try:
        # `told` is excluded: a clearance is bookkeeping for `clear()`, not
        # guidance. "He said vpsie.restart is different now" in a prompt
        # reads as permission, which is the one thing a lesson must never be.
        rows = await fetch_all(
            """SELECT lesson, kind FROM lessons
                WHERE cleared_at IS NULL AND (agent = $1 OR agent IS NULL)
                  AND kind <> 'told'
                  AND last_at > now() - make_interval(days => $2)
                ORDER BY times DESC, last_at DESC LIMIT $3""",
            agent, WINDOW_DAYS, MAX_FOR_TASK)
    except Exception as exc:                            # noqa: BLE001
        log.warning("lessons: could not read recent lessons: %s", exc)
        rows = []
    for row in rows:
        if atlas_authored(row.get("kind"), str(row["lesson"])):
            lines.append(_one_line(str(row["lesson"])))

    seen: set[str] = set()
    kept: list[str] = []
    for line in lines:
        if line and line.lower() not in seen:
            seen.add(line.lower())
            kept.append(line)
        if len(kept) == MAX_FOR_TASK:
            break
    if not kept:
        return ""
    return "## Lessons\n" + "\n".join(f"- {line}" for line in kept)


async def from_review(day: date) -> int:
    """Every `went_wrong` line from that night's review becomes a lesson.

    Reads the stored review rather than rebuilding it, which keeps this file
    free of any import of `review.py` (which imports this one) and means a
    line only becomes a lesson once it is on the record. Deduplicated by
    text, so the same problem three nights running is one lesson with
    `times = 3` — which is precisely the signal worth having.
    """
    try:
        row = await fetch_one(
            "SELECT went_wrong FROM reviews WHERE day = $1", day)
    except Exception as exc:                            # noqa: BLE001
        log.warning("lessons: could not read the review for %s: %s", day, exc)
        return 0
    if row is None:
        return 0

    raw = row["went_wrong"]
    try:
        lines = json.loads(raw) if isinstance(raw, str) else list(raw or [])
    except (TypeError, ValueError):
        log.warning("lessons: review for %s has unreadable went_wrong", day)
        return 0

    written = 0
    for line in lines:
        text = " ".join(str(line).split())
        if text and not atlas_authored("review", text):
            # Not a shape review.py writes: it carries text ATLAS did not
            # author, and a lesson reaches every agent's system prompt.
            log.warning("lessons: a review line is not ATLAS's own; not "
                        "recorded as a lesson")
            continue
        if text and await record("review", text):
            written += 1
    return written
