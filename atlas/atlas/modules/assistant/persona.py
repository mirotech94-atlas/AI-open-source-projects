"""The assistant's voice, and when it should keep its tools out of a chat.

Miro, 2026-11-19: the assistant talks like a calm, close friend. In casual
chat it uses no tool and brings up no status (live bug: an answer about an
octopus added the sensor count from earlier turns). Facts and figures still
come only from a tool (invariant 6); this module changes the tone and which
prompt notes and tools a turn is offered, never what a tool may read or what
tier an action has (invariants 2 and 3).

`is_casual` fails toward the old behaviour: any word about his systems, any
digit, the clock or a conversion, and the turn is not casual, so it keeps the
tools it had. A casual turn that needed a fact says plainly it cannot check.
"""
from __future__ import annotations

import re

# The tone, said once. Facts rule stays in IDENTITY; this only shapes speech.
PERSONA = (
    "Talk like Miro's calm, warm best friend: plain words, short sentences, "
    "contractions. Plain prose only: no bullet lists, no headers, no 'As an "
    "AI', no sign-off. Remember what he told you earlier in this chat and "
    "pick it up. When it fits, end with one gentle question about him, not "
    "about his systems. If you are not sure of a fact, say so plainly; never "
    "state a doubtful fact as true."
)

# Said to the model only on a casual turn: it has no tools, and says nothing
# about the state of his systems.
CASUAL_NOTE = (
    "This is casual talk, not a question about his systems. You have no tools "
    "here and nothing to look up. Answer only his latest message. Do not "
    "bring up sensors, mail, checks, status, counts, searches or older topics "
    "from earlier turns unless he does. If he asks for a figure or a live "
    "fact, say plainly you would need to check it."
)

# Anything about his own systems, figures, the clock or a conversion.
_SYSTEMS = re.compile(
    r"\b(?:atlas|modules?|checks?|status|health\w*|uptime|errors?|alerts?|"
    r"incidents?|sensor|sensors|cctv|nvr|footage|e-?mails?|inbox|mail|calendar|"
    r"meetings?|schedule|board|proposals?|cards?|approv\w*|queue\w*|server|"
    r"servers|down|offline|online|broken|failing|failed|deploy\w*|backups?|"
    r"cloudhost|flycheap|fiverr|linkedin|nexus|tiktok|instagram|stripe|revenue|"
    r"sales|orders?|subscribers?|followers?|views|traffic|balance|spark|slack|"
    r"whatsapp|dashboard|how\s+many|how\s+much|numbers?|figures?|stats|"
    r"time|date|clock|convert|calculate|percent|"
    r"remind\w*|remember\s+to|search|look\s+up|news|weather|price|stock)\b",
    re.IGNORECASE)
_DIGIT = re.compile(r"\d")
# Small talk that the systems list above would otherwise catch.
_SMALL_TALK = re.compile(
    r"\b(?:how\s+(?:are|is)\s+(?:you|it\s+going)(?:\s+doing)?|how(?:'s|\s+is)\s+"
    r"(?:it\s+going|your\s+day)|how\s+have\s+you\s+been|what(?:'s|\s+is)\s+up)\b",
    re.IGNORECASE)


def without_small_talk(text: str) -> str:
    return _SMALL_TALK.sub(" ", " ".join((text or "").split()))


def is_casual(text: str, *, also: re.Pattern[str] | None = None) -> bool:
    """True when the message is plain talk: nothing about his systems, no
    figure, no clock or conversion, and nothing `also` matches (a door's own
    stricter pattern, e.g. voice's live-question words). Pure."""
    body = without_small_talk(text)
    if also is not None and also.search(body):
        return False
    if not body.strip():
        return True
    return not (_SYSTEMS.search(body) or _DIGIT.search(body))
