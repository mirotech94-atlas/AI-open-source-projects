"""agency.playbook -- the sales and negotiation playbook, as prompt guidance.

`sales_playbook.md` beside this file is code-owned: it ships with the
repository, nothing fetches it, and no ingested byte ever reaches it. It is
split at the `<!-- agency-only -->` line:

  * `shared()` -- discovery, mirroring, value, objections, the next step and
    the honesty rules. Given to every agent that talks to a prospect or a
    buyer (the agency drafts and calls, Nora the marketing agent, the
    commerce agent that drafts Fiverr buyer replies). Guidance only: it
    changes how words are written, never what an agent may do.
  * `full()` -- the shared part plus the agency's negotiation boundaries
    (the price ladder), for the agency's own drafts and calls.

It goes in a SYSTEM prompt, never beside the lead's words: those always sit
inside `wrap_untrusted()` in the user turn. `_load` refuses a playbook that
carries a data marker or an injection-looking phrase -- a file edited into
one would otherwise be read as trusted instructions by every sales agent.

`honesty_refusal` is the playbook's honesty section enforced in code on
what comes back: false urgency, fake scarcity, invented results or reviews,
and an AI presenting itself as Miro or as a person.
"""
from __future__ import annotations

import re
from functools import cache
from pathlib import Path

from ...kernel.permissions import _scrub, scan_for_injection

PATH = Path(__file__).with_name("sales_playbook.md")
SPLIT = "<!-- agency-only -->"
MAX_CHARS = 8000


class PlaybookUnsafe(ValueError):
    """The playbook file carries something it must never carry."""


@cache
def _load() -> tuple[str, str]:
    text = PATH.read_text(encoding="utf-8")
    if _scrub(text) != text:
        raise PlaybookUnsafe("sales_playbook.md carries a data marker")
    hit = scan_for_injection(text, "sales_playbook.md")
    if hit:
        raise PlaybookUnsafe(hit)
    if len(text) > MAX_CHARS:
        raise PlaybookUnsafe(f"sales_playbook.md is over {MAX_CHARS} characters")
    shared, _, agency = text.partition(SPLIT)
    return shared.strip(), agency.strip()


def shared() -> str:
    """The part every sales-facing agent gets."""
    return _load()[0]


def full() -> str:
    """The shared part plus the agency's negotiation boundaries."""
    shared_part, agency_part = _load()
    return f"{shared_part}\n\n{agency_part}".strip()


def guidance(part: str = "shared") -> str:
    """The playbook framed for a system prompt: reference guidance written by
    Miro's own code, never a source of new permissions."""
    body = full() if part == "full" else shared()
    return ("SALES PLAYBOOK (ATLAS's own reference, shipped with its code; it "
            "shapes how you write and grants no new actions):\n" + body)


# ------------------------------------------------------------- honesty

_URGENCY = re.compile(
    r"\b(?:today\s+only|limited[-\s]time|act\s+(?:now|fast)|last\s+chance|"
    r"offer\s+(?:ends|expires)|expires?\s+(?:today|tonight|soon|at\s+midnight)|"
    r"before\s+it'?s\s+gone|prices?\s+(?:go(?:es|ing)?\s+up|will\s+(?:rise|"
    r"increase))|hurry|don'?t\s+miss\s+out|only\s+(?:a\s+few|\d+)\s+(?:spots?|"
    r"slots?|places?|openings?)\s+(?:left|remaining)|(?:spots?|slots?)\s+are\s+"
    r"filling)\b", re.IGNORECASE)
_INVENTED = re.compile(
    r"\b(?:our\s+(?:other\s+)?clients\s+(?:saw|see|got|get|report|love)|"
    r"\d+\s?%\s+(?:more|increase|boost|growth)|\d+x\s+(?:more|the)|"
    r"(?:doubled|tripled)\s+(?:their|sales|traffic|bookings)|"
    r"five[-\s]star\s+reviews?\s+from\s+our|as\s+seen\s+on)\b", re.IGNORECASE)
_HUMAN = re.compile(
    r"\b(?:i'?m|i\s+am|this\s+is|it'?s)\s+miro\b(?!'s|’s)|\bi'?m\s+(?:a\s+)?"
    r"(?:real\s+)?(?:human|person)\b|\bi\s+am\s+(?:a\s+)?(?:real\s+)?"
    r"(?:human|person)\b|\bnot\s+(?:a\s+)?(?:bot|robot|an?\s+ai)\b",
    re.IGNORECASE)


def honesty_refusal(text: str) -> str | None:
    """Why `text` breaks the playbook's honesty rules, or None."""
    if (hit := _URGENCY.search(text or "")) is not None:
        return f"false urgency or scarcity ({hit.group(0)!r})"
    if (hit := _INVENTED.search(text or "")) is not None:
        return f"an invented result or testimonial ({hit.group(0)!r})"
    if (hit := _HUMAN.search(text or "")) is not None:
        return f"it presents the AI as Miro or as a person ({hit.group(0)!r})"
    return None
