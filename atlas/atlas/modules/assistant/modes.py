"""Ask or Search: how much of the outside world a question may reach.

Two modes, as Miro's Atlas Implementation Plan names them for Phase 1:

  ask     answered from what the model knows, with the calculator, unit
          conversion and the clock only -- no web search, no page, no
          weather, Wikipedia, dictionary or exchange rate.
  search  the full read set: the web, one page at a time, and the four
          outside APIs.

`ask! <question>` and `search <question>` force Search; `ask <question>` and
a plain question let `route()` decide. The router picks Search only when the
question needs a fresh or outside fact: current/latest/today, a price or a
rate, the weather, the news, a URL, a recent year -- and a figure about the
world ("when did", "how many", "how tall"), because a number the model only
remembers is a number it would be guessing (invariant 6). Everything else is
Ask, so "explain photosynthesis simply" never touches the network.

The mode can only NARROW what a question may use: the loop intersects it
with the skill's tools and with the privacy rule, so a forced Search on a
private question still runs on the local model with local tools only.

ATLAS's own reads (atlas_status, atlas_module_status, atlas_capabilities)
are not outside reads -- they read ATLAS's tables -- and a question about
ATLAS itself keeps them in either mode. Without them "is flycheap ok" would
be answered with no fresh read at all.
"""
from __future__ import annotations

import re

ASK = "ask"
SEARCH = "search"
MODES = (ASK, SEARCH)

# What an Ask may use: arithmetic, units and the clock.
ASK_TOOLS: frozenset[str] = frozenset({"calculate", "convert_units", "time_now"})
# ATLAS's own tables. Kept for a question about ATLAS in either mode.
ATLAS_TOOLS: frozenset[str] = frozenset({
    "atlas_status", "atlas_module_status", "atlas_capabilities"})

# Skills whose whole job is reading a source.
SEARCH_SKILLS: frozenset[str] = frozenset({"summarise", "fact_check"})

_FRESH = re.compile(
    r"\b(?:current(?:ly)?|latest|today|tonight|yesterday|right\s+now|"
    r"this\s+(?:week|month|year|morning|evening)|recent(?:ly)?|news|headlines?|"
    r"prices?|how\s+much\s+(?:is|are|does|do)|stock|shares?|exchange\s+rates?|"
    r"currenc(?:y|ies)|weather|forecast|raining|snowing|score|who\s+won|"
    r"winner|election|released?|launch(?:ed|es)?|announced|open\s+now|"
    r"look\s+(?:it\s+)?up|search|sources?|cite)\b",
    re.IGNORECASE)
# A figure about the world: the model would only be remembering it.
_FIGURE = re.compile(
    r"\b(?:when\s+(?:did|was|is|were|will)|what\s+year|which\s+year|"
    r"how\s+(?:many|much|tall|far|long\s+ago|old|big|large|fast|high|deep|heavy)|"
    r"population|distance\s+(?:from|between|to)|height\s+of|date\s+of|"
    r"born|died|founded)\b",
    re.IGNORECASE)
_MONEY = re.compile(
    r"[$€£¥₹]|\b(?:usd|eur|gbp|jpy|cad|aud|chf|cny|inr|mxn|brl|dollars?|euros?|"
    r"pounds\s+sterling|yen)\b", re.IGNORECASE)
_URL = re.compile(r"https?://|www\.|\b[\w-]+\.(?:com|org|net|io|dev|gov|edu)\b",
                  re.IGNORECASE)
_RECENT_YEAR = re.compile(r"\b20(?:2[4-9]|[3-9]\d)\b")
# "How many seconds in 3 days" is arithmetic on figures he gave; "how tall
# is the Eiffel tower" is a figure about the world. A number in the question
# that is not a year is the difference.
_OPERAND = re.compile(r"\d")
_YEAR = re.compile(r"\b(?:1[0-9]|20)\d{2}\b")


def _gives_operands(text: str) -> bool:
    return bool(_OPERAND.search(_YEAR.sub(" ", text)))


def route(question: str, *, skill: str = "") -> tuple[str, str]:
    """(mode, why) for a question no one forced. Pure."""
    text = " ".join((question or "").split())
    if skill in SEARCH_SKILLS:
        return SEARCH, f"the {skill} skill reads a source"
    if _URL.search(text):
        return SEARCH, "it names a web address"
    if _MONEY.search(text):
        return SEARCH, "money is converted only at a rate read now"
    if m := _FRESH.search(text):
        return SEARCH, f"it asks for something current ({m.group(0).lower()!r})"
    if _RECENT_YEAR.search(text):
        return SEARCH, "it names a recent year"
    if (m := _FIGURE.search(text)) and not _gives_operands(text):
        return SEARCH, f"it asks for a figure about the world ({m.group(0).lower()!r})"
    return ASK, "general knowledge, arithmetic or units: no outside read needed"


def tools_for(mode: str, *, skill: str = "") -> frozenset[str] | None:
    """The tools a mode allows, or None for no narrowing (Search). The
    atlas skill keeps ATLAS's own reads in Ask: they read ATLAS's tables,
    not the outside world."""
    if mode == SEARCH:
        return None
    return ASK_TOOLS | ATLAS_TOOLS if skill == "atlas" else ASK_TOOLS
