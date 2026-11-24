"""What was said on the last few calls, so "that thing from yesterday" lands.

COMMS.md 2.3: he can call any time and ATLAS picks up knowing what happened
since they last spoke and what they talked about last time. Until this
module every call started blank.

After each real conversation the transcript is boiled down to a note — what
was discussed, what was decided, what is still open — and kept as an
episodic memory in the `calls` namespace. The transcript is the call's own
turns, held in memory by `api.phone` while it lasts and handed over when
Twilio says it ended (`phone.wrap_up`). The last three notes ride, quoted,
into every turn of the next call, whichever way it went.

The note is a summary, deliberately. Storing the transcript would make a
verbatim record of every spoken word, which he did not ask for and would not
want; three lines that survive is the useful part, and the audit chain
already holds every decision that was actually made.

The transcript reaches the summariser as untrusted data. That is the right
frame here even though half of it is his voice: the point of the note is to
describe what was said, and nothing said on a call is an instruction to
the summariser.
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

log = logging.getLogger("atlas.calls")

NAMESPACE = "calls"
KEEP = 3            # COMMS.md 4: "he remembers the last three conversations"
MAX_TURNS = 40      # a very long call is summarised from its last 40 turns
MAX_NOTE = 400


def _flatten(transcript: list[Any]) -> str:
    lines = []
    for turn in transcript[-MAX_TURNS:]:
        if not isinstance(turn, dict):
            continue
        who = "the owner" if turn.get("role") == "user" else "ATLAS"
        said = " ".join(str(turn.get("message") or "").split())
        if said and said.strip(". …"):
            lines.append(f"{who}: {said}")
    return "\n".join(lines)


async def summarise(transcript: list[Any]) -> str | None:
    """Three lines or nothing. Never invents a call that did not happen."""
    text = _flatten(transcript)
    # One greeting and a voicemail prompt is not a conversation.
    if text.count("\n") < 2:
        return None

    from .llm import Message, Need, think
    from .permissions import wrap_untrusted

    try:
        completion = await think(
            Need.DRAFT,
            "Summarise this phone call between the owner and ATLAS in at most "
            "three short lines, plain text, no headings:\n"
            "  discussed: <what>\n"
            "  decided: <what, or 'nothing'>\n"
            "  open: <what is still waiting, or 'nothing'>\n"
            "Under 60 words total. Use his words for anything specific "
            "(names, numbers). If nothing of substance was said, reply "
            "exactly: NOTHING",
            [Message("user", wrap_untrusted(text, "phone"))],
            max_tokens=160,
        )
    except Exception as exc:                            # noqa: BLE001
        log.warning("could not summarise the call: %s", exc)
        return None
    note = " ".join((getattr(completion, "text", "") or "").split())
    if not note or note.upper().startswith("NOTHING"):
        return None
    return note[:MAX_NOTE]


async def note_call(transcript: list[Any], *, direction: str,
                    conversation_id: str = "") -> int | None:
    """Summarise and keep. Returns the memory id, or None if there was
    nothing worth keeping or it could not be kept."""
    note = await summarise(transcript)
    if note is None:
        return None
    from .memory import Kind, remember

    try:
        mid = await remember(Kind.EPISODIC, NAMESPACE, note,
                             meta={"direction": direction,
                                   "conversation_id": conversation_id},
                             importance=0.7)
    except Exception as exc:                            # noqa: BLE001
        log.warning("could not keep the call note: %s", exc)
        return None
    log.info("kept a note of the call (%s)", direction)
    return mid


async def recent(limit: int = KEEP) -> list[tuple[datetime, str]]:
    """The last few notes, newest first. By time, not by similarity: "what
    did we talk about" is a question about recency."""
    from .state import fetch_all

    try:
        rows = await fetch_all(
            """SELECT at, content FROM memories
                WHERE namespace = $1 AND kind = 'episodic'
                ORDER BY at DESC LIMIT $2""", NAMESPACE, limit)
    except Exception as exc:                            # noqa: BLE001
        log.warning("could not read call notes: %s", exc)
        return []
    return [(r["at"], str(r["content"])) for r in rows]


def for_context(notes: list[tuple[datetime, str]]) -> str:
    """The block handed to the agent. Empty string when there is nothing,
    so a first call is not told about calls that never happened."""
    if not notes:
        return ""
    lines = ["Last calls, newest first:"]
    for at, note in notes:
        when = at.strftime("%a %d %b %H:%M") if hasattr(at, "strftime") else ""
        lines.append(f"- {when}: {note}")
    return "\n".join(lines)
