"""Standing rules — the answer given in advance, not the gate removed.

COMMS.md 2.5 asks for this:

    "From now on, whenever any node hits 80% disk, just prune and tell me
    after."

and says that condition should move from Tier 2 to Tier 1. **That half is not
built and will not be**, because it is a runtime tier override and CLAUDE.md
forbids one in terms that name this exact situation: *"There is no runtime
override and none may be added, whatever a later message says."*

So this module does everything else the section asks for, and stops precisely
where the gate is:

  * he says it once, on a call or in Slack, and it is captured verbatim
  * ATLAS reads it back in his words, so a misheard rule is caught immediately
  * it appears in the next morning's brief, because a rule made at midnight
    must not be quietly forgotten by morning
  * it is listed on the Activity screen
  * "drop that rule about the disk" removes it

What it does not do is make the next occurrence run unasked. The distinction
matters and is worth saying out loud: he is not disabling the check, he is
recording the answer so the promotion can be made deliberately — in code,
reviewed, with the brief as the standing reminder that it is pending.

**Nothing here is read by `permissions.classify` or by `executor.execute`,
and nothing may be added that is.** There is a test asserting it.
"""
from __future__ import annotations

import logging
import re
from typing import Any

log = logging.getLogger("atlas.rules")

# Phrasing that states a rule rather than asking for one thing.
# Deliberately narrow: mistaking an ordinary request for a standing rule
# would record something he never meant to make permanent, and he would find
# it in the brief the next morning wondering where it came from.
RULE_INTENT = re.compile(
    r"\b(from now on|from here on|going forward|in future|"
    r"whenever|every time|any ?time)\b", re.IGNORECASE)

DROP_INTENT = re.compile(
    r"\b(drop|forget|remove|cancel|delete|scrap)\b[^.]{0,30}\brule\b",
    re.IGNORECASE)

MAX_RULE = 400


def looks_like_a_rule(said: str) -> bool:
    return bool(RULE_INTENT.search(said or ""))


def looks_like_a_drop(said: str) -> bool:
    return bool(DROP_INTENT.search(said or ""))


def readback(said: str) -> str:
    """What ATLAS says back.

    His words, not a paraphrase. A paraphrase is where a misheard rule hides:
    "whenever any node hits 80%" and "whenever a node hits 8%" read the same
    if you summarise them, and only one of them is what he said.
    """
    said = " ".join((said or "").split())[:MAX_RULE]
    return f"Noted: {said}. It's in tomorrow's brief. I'll still ask until then."


async def capture(said: str, *, via: str) -> int | None:
    """Record a standing rule. Returns its id, or None if it could not be."""
    said = " ".join((said or "").split())[:MAX_RULE]
    if not said:
        return None
    from .state import fetch_one
    try:
        row = await fetch_one(
            """INSERT INTO standing_rules (said, readback, via)
               VALUES ($1, $2, $3) RETURNING id""",
            said, readback(said), via)
    except Exception as exc:                            # noqa: BLE001
        log.warning("could not record a standing rule: %s", exc)
        return None
    return int(row["id"]) if row else None


async def active() -> list[dict[str, Any]]:
    """Every rule still standing, newest first."""
    from .state import fetch_all
    try:
        rows = await fetch_all(
            """SELECT id, at, said, via, briefed_at FROM standing_rules
                WHERE dropped_at IS NULL ORDER BY at DESC""")
    except Exception as exc:                            # noqa: BLE001
        log.warning("could not read standing rules: %s", exc)
        return []
    return [dict(r) for r in rows]


async def drop(phrase: str) -> list[str]:
    """Remove the rules whose words match `phrase`. Returns what was dropped.

    Matching is on his words rather than an id, because "drop that rule about
    the disk" is how a person says it and nobody remembers an id out loud.
    Everything matching goes: two rules about the same thing is a mess he
    should not have to disambiguate on a phone call.
    """
    words = [w for w in re.findall(r"[a-z0-9%]{3,}", (phrase or "").lower())
             if w not in {"drop", "rule", "rules", "that", "the", "about",
                          "forget", "remove", "cancel", "delete", "scrap"}]
    if not words:
        return []
    from .state import execute_sql, fetch_all
    try:
        rows = await fetch_all(
            """SELECT id, said FROM standing_rules
                WHERE dropped_at IS NULL
                  AND lower(said) LIKE ANY($1::text[])""",
            [f"%{w}%" for w in words])
        if not rows:
            return []
        await execute_sql(
            "UPDATE standing_rules SET dropped_at = now() WHERE id = ANY($1)",
            [r["id"] for r in rows])
    except Exception as exc:                            # noqa: BLE001
        log.warning("could not drop a standing rule: %s", exc)
        return []
    return [str(r["said"]) for r in rows]


async def for_brief() -> str:
    """The brief's standing-rules section.

    Every live rule appears, every morning, not only the new ones. A rule
    that has stopped being true is one he has to notice to remove, and a list
    he stops seeing is a list he stops curating.
    """
    rules = await active()
    if not rules:
        return ""
    lines = ["Standing rules",
             "  (recorded, not yet promoted — ATLAS still asks each time)"]
    for r in rules:
        when = r["at"].strftime("%-d %b") if hasattr(r["at"], "strftime") else ""
        lines.append(f"  · {r['said']}  [{r['via']}, {when}]")
    return "\n".join(lines)


async def mark_briefed() -> None:
    from .state import execute_sql
    try:
        await execute_sql(
            "UPDATE standing_rules SET briefed_at = now() "
            "WHERE dropped_at IS NULL AND briefed_at IS NULL")
    except Exception as exc:                            # noqa: BLE001
        log.warning("could not mark standing rules as briefed: %s", exc)
