"""Which questions are about Miro's own private life, and so stay on the
local model with no outside tool.

The rule `kernel/private_data.py` already keeps for the agents -- mail,
messages, calendar, school, vault, screening hold his personal content and
are read on the local model only -- restated for a free-text question: one
that asks about HIS mail, messages, calendar, grades, documents, browsing,
health or credentials is PRIVATE (`llm.Need.PRIVATE`). Such a question is
answered on the local model or not at all, and none of the tools that reach
outside ATLAS is offered for it: a web search about "my prescription" is
his medical detail leaving the box.

A general question on the same subject ("what are the symptoms of flu",
"how does IMAP work") is not private: it is about the world, not about him.
The cue has to be first-person. Fails open to "not private" only in the
sense that an unmatched question is ordinary -- an ordinary question still
runs under every other rule here.
"""
from __future__ import annotations

import re

from ...kernel.private_data import PRIVATE_MODULES

# "my <up to two words> <private thing>": my inbox, my work e-mail, my
# school grades, my browser history, my doctor's letter.
_MINE = re.compile(
    r"\b(?:my|our)\b(?:\s+\w+){0,2}\s+"
    r"(?:e-?mails?|inbox|mail|messages?|dms?|texts?|chats?|calendar|"
    r"appointments?|schedule|meetings?|grades?|coursework|assignments?|"
    r"homework|documents?|files?|notes?|vault|library|browser|browsing|"
    r"history|health|medical|doctor|prescriptions?|therapist|diagnosis|"
    r"symptoms|passwords?|credentials?|bank|statements?|contacts?|"
    r"voicemails?|phone\s+calls?|callers?)\b",
    re.IGNORECASE)

# First-person phrasings that name no noun from the list above.
_ABOUT_ME = re.compile(
    r"\b(?:what\s+did\s+i\s+(?:visit|read|search|browse|watch)|"
    r"did\s+(?:anyone|anybody|someone)\s+(?:e-?mail|message|text|call|write\s+to)"
    r"\s+me|who\s+(?:e-?mailed|messaged|texted|called|wrote\s+to)\s+me|"
    r"(?:am|was)\s+i\s+(?:sick|ill|diagnosed)|"
    r"(?:what|which)\s+(?:sites?|pages?)\s+(?:did|have)\s+i\b|"
    r"(?:what|where)\s+(?:am|was|will)\s+i\b.{0,30}\b(?:today|tomorrow|tonight|"
    r"this\s+week|next\s+week|yesterday|on\s+\w+day))\b",
    re.IGNORECASE)

# A private module named with a possessive frame: "my mail module", "how
# is my calendar". The module list is the kernel's, never retyped.
_MODULE = re.compile(
    r"\b(?:my|our)\s+(?:" + "|".join(sorted(map(re.escape, PRIVATE_MODULES))) + r")\b",
    re.IGNORECASE)


# The home sensors, named without a possessive: "how many sensor events".
_SENSORS = re.compile(
    r"\b(?:sensor|cctv|nvr)\s+(?:events?|footage|alerts?|clips?|motion|"
    r"detections?|snapshots?|recordings?)\b|"
    r"\b(?:events?|motion|people|persons?|vehicles?|visitors?)\s+(?:on|at|from|"
    r"detected\s+by)\s+(?:the|my|our)\s+(?:\w+\s+)?sensors?\b|"
    r"\bsensors?\s+(?:\w+\s+){0,2}?(?:online|offline|up|down)\b|"
    r"\b(?:online|offline)\s+sensors?\b|\bhow\s+many\s+(?:\w+\s+)?sensors?\b",
    re.IGNORECASE)


def private_reason(question: str) -> str | None:
    """Why this question is PRIVATE, in a few words, or None when it is an
    ordinary question. Pure."""
    text = " ".join((question or "").split())
    if not text:
        return None
    for pattern, why in ((_MINE, "it asks about your own private content"),
                         (_ABOUT_ME, "it asks about you personally"),
                         (_MODULE, "it names one of your private modules"),
                         (_SENSORS, "it asks about the sensors at your home")):
        if pattern.search(text):
            return why
    return None
