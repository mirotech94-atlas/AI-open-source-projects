"""Voice: transcription and answering. Read-only, like the rest of the mirror.

Two things this file is responsible for.

**There is no voice path to the executor.** A wake word is a phrase anyone in
the room can say. Asking ATLAS to *do* something returns what he would do and
raises a card — nothing here calls `executor.execute`, and there is no code
here that could. Two kernel doors are reached from the signed-in dashboard
only: `kernel.spoken` for Mac actions (said on /ask, or from the menu-bar
hotkey on /command), and `kernel.missions`, which records a
job for the agent that owns it (Miro, 2026-09-29) and whose Tier 1 steps the
kernel then runs through the executor; its Tier 2 steps are cards. The phone
line recognises a mission but never starts one.

**Every stage says when it cannot run.** No transcriber configured, no model,
no voice — each is reported as itself rather than as silence or a spinner. That
is the same rule the collectors follow: a thing that could not run says so.
"""
from __future__ import annotations

import logging
import os
import re
import time
from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, File, HTTPException, Request, Response, UploadFile

from ..kernel import quota
from ..kernel.permissions import wrap_untrusted
from ..kernel.state import execute_sql, fetch_all
from ..modules._fish import FISH_TTS_URL  # noqa: F401 -- re-exported
from ..modules._fish import fish_request as _fish_request
from . import auth, ui_paths

log = logging.getLogger("atlas.voice")

router = APIRouter(prefix="/api/voice")
SignedIn = Annotated[auth.Session, Depends(auth.require)]
Audio = Annotated[UploadFile, File()]

MAX_AUDIO_BYTES = 8 * 1024 * 1024        # ~2 minutes of opus
MAX_TRANSCRIPT = 1200

# Phrasing that asks ATLAS to change something rather than describe it.
# Deliberately broad: a false positive costs one explanatory sentence, a false
# negative would have him answer as though he had done it.
_ACTION_VERBS = (
    r"restart|reboot|deploy|delete|remove|fix|repair|run|execute|apply|"
    r"regenerate|rotate|approve|reject|cancel|send|post|publish|upload|buy|"
    r"pay|refund|shut\s*down|turn\s+(?:on|off)|enable|disable|kill|stop|start")

# The verbs of the reels he asked for ("build them a website and invoice
# them", "label my inbox and draft replies"). Without them those sentences
# were answered as questions and never reached a card. They are nouns as
# often as verbs -- "the guardian build report", "a shipping label", "how
# does an invoice work" -- so they count only as an order: said first
# (after his name, "please", "can you"), or first after a step break.
#
# The fixing verbs join them on the same terms (2026-09-28: "can you
# optimize my cpu for attendace bot server" was answered as small talk and
# asked him for his own `top` output). As an order they reach intent.act,
# and a catalog miss goes to kernel/solve.py; as a question ("how do I
# optimize postgres") they are still a question.
_IMPERATIVE_VERBS = (
    r"build|invoice|label|draft|"
    r"optimi[sz]e|speed\s+up|clean\s+up|tune|troubleshoot|debug|diagnose|"
    r"investigate|look\s+into|resolve|solve|sort\s+out|upgrade|install|"
    r"configure|set\s+up|patch|migrate|improve|reduce|lower|free\s+up|scale|"
    # The laptop (2026-09-28): "open Notes", "remind me to ...", "run my
    # Focus shortcut" -- a catalog miss becomes a card for the Mac runner.
    # "open" alone stays the dashboard's own verb ("open the build report");
    # it is an app request only when it says "app" or names the Mac.
    r"launch|quit|remind\s+me|add\s+a\s+reminder|run\s+(?:my\s+|the\s+)?"
    r"[\w\s]{0,30}shortcut|"
    r"open\s+(?:up\s+)?(?:the\s+)?[\w.]+(?:\s+[\w.]+)?\s+app\b|"
    r"open\s+[\w.\s]{1,40}\bon\s+my\s+(?:mac|laptop|computer)\b|"
    # Music plays on YouTube Music (2026-09-28).
    r"play\s+(?:some\s+|my\s+)?[\w\s'&-]{0,40}?"
    r"(?:music|songs?|playlist|album|radio|mix|on\s+youtube(?:\s+music)?)\b|"
    r"(?:pause|stop|skip)\s+(?:the\s+)?(?:music|song|track)|"
    # Everyday Mac control (2026-09-28): "text Mum I'm late", "set the
    # volume to 30", "open flycheap.live", "notify me ...". A message and a
    # Shortcut become cards (Tier 2); volume, a notification, a URL and
    # quitting an app run (Tier 1 since 2026-10-06).
    r"text|message|i-?message|"
    r"(?:set|turn)\s+(?:the\s+|my\s+)?volume|"
    r"turn\s+(?:it|the\s+(?:volume|sound|music))\s+(?:up|down)|(?:un)?mute|"
    r"notify\s+me|"
    r"(?:open|go\s+to)\s+(?:up\s+)?(?:https?://\S+|[\w-]+(?:\.[\w-]+)*\.[a-z]{2,})")


def _addressed_names() -> str:
    """Every roster persona's first name ("Hazel", "Nora", "Ivan"), for "have
    Nora post the course" -- read from the persona table, never typed here,
    so a renamed agent is still addressable."""
    from ..kernel.personas import PERSONAS
    return "|".join(sorted({re.escape(p.name.lower()) for p in PERSONAS.values()},
                           key=len, reverse=True))


# Orders that hand a job to an agent (Miro, 2026-09-29: "search up the 50
# local businesses that have a very poor website or no website at all and
# make them one" was answered as small talk -- "I can't pull a live list" --
# while the agency module does exactly that). Same terms as the verbs above:
# an order only when said first, so "how do I search for a plumber" and
# "what did you make of it" stay questions.
#
# The lookup verbs are also how a question is asked ("look up TSLA's price",
# "find me a pasta recipe"), so `is_order` counts them only when the request
# names a domain an agent owns (kernel/missions.py); otherwise it is answered.
_LOOKUP_VERBS = r"search(?:\s+up)?|find|look\s+up|get\s+me"
_ADDRESSED = (rf"(?:have|get|ask|tell)\s+(?:{_addressed_names()})\b"
              r"|(?:have|get|ask|tell)\s+the\s+\w+\s+(?:agent|team)\b")
_ORDER_VERBS = rf"make(?!\s+sure)|create|{_ADDRESSED}"
_IMPERATIVE_AT = (
    r"(?:^|[,;]|\band\b|\bthen\b)[\s,]*"
    r"(?:(?:hey\s+|ok\s+|okay\s+)?(?:atlas|jarvis)\b[\s,]*)?"
    r"(?:(?:please|can you|could you|would you|will you|just|go and)\b[\s,]*)*")
ACTION_INTENT = re.compile(
    rf"\b(?:{_ACTION_VERBS})\b|{_IMPERATIVE_AT}\b(?:{_IMPERATIVE_VERBS}"
    rf"|{_ORDER_VERBS}|{_LOOKUP_VERBS})\b",
    re.IGNORECASE)
# The same, without the lookup verbs: what is an order whatever it names.
_ORDER_NOT_LOOKUP = re.compile(
    rf"\b(?:{_ACTION_VERBS})\b|{_IMPERATIVE_AT}\b(?:{_IMPERATIVE_VERBS}"
    rf"|{_ORDER_VERBS})\b",
    re.IGNORECASE)


def is_order(transcript: str) -> bool:
    """Whether this asks ATLAS to DO something -- the gate every surface uses.

    `ACTION_INTENT` is the recogniser; this is it with one exception. An
    utterance that is an order only through a lookup verb ("search", "find",
    "look up", "get me") is an order when it names work an agent owns
    ("find me 20 plumbers in Astoria with no website" is the agency's), and a
    question otherwise ("look up the capital of Peru" is answered, never
    turned into a card or a handoff)."""
    if _ORDER_NOT_LOOKUP.search(transcript):
        return True
    if not ACTION_INTENT.search(transcript):
        return False
    from ..kernel import missions
    return missions.owner_for(transcript) is not None

# ------------------------------------------------ self questions (Task 33)
# "How are you", asked of ATLAS itself. Answered from the self-model --
# uptime, errors, checks, where he is, the newest lesson -- and never routed
# to a model, which would happily improvise a mood. Checked only when
# ACTION_INTENT did not match: "how are you going to restart it" is a
# request to act and is answered as one. Never when he says "open" or
# "show": "open the status report" is asking for a document, not after
# ATLAS's health.
SELF = re.compile(
    r"^(?!.*\b(?:open|show)\b).*?"
    r"\b(how are you|are you (?:ok|okay|alright|awake)|what['’]?s your status|"
    r"how['’]?s it going|status report|what did you get wrong)\b",
    re.IGNORECASE | re.DOTALL,
)
# ------------------------------------------------ end self questions


# ------------------------------------------------ system check (Task 20, 15.3)
# "System check", "you up?": the estate's agents and checks, read fresh and
# said with their read time -- `api/system_check.py` builds it. Not the
# self-model: "how are you" is ATLAS's own uptime and lessons and stays SELF.
# The brief's pattern, verbatim.
SYSTEM_CHECK = re.compile(r"\b(system check|status check|are you up|you up)\b",
                          re.IGNORECASE)

# "Run a system check" is how people say it, and ACTION_INTENT matches `run`.
# The check reads only, so the verb that introduces it is removed before the
# action test -- and only that verb: "system check and restart flycheap" is
# still a request to act and is answered as one.
_SYSTEM_CHECK_VERB = re.compile(
    r"\b(?:run|do|start|perform|give me)\s+(?:a|an|the|me a)?\s*"
    r"(?:quick\s+|full\s+)?(?:system|status) check\b",
    re.IGNORECASE)


# The brief's "you up" also matches "what are you up to" / "you up for a
# walk" -- a question about his evening, not the estate.
_NOT_SYSTEM_CHECK = re.compile(r"\byou up (?:to|for)\b", re.IGNORECASE)


def is_system_check(transcript: str) -> bool:
    """A system check, and nothing else asked alongside it."""
    if not SYSTEM_CHECK.search(transcript) or _NOT_SYSTEM_CHECK.search(transcript):
        return False
    return not ACTION_INTENT.search(_SYSTEM_CHECK_VERB.sub(" ", transcript))
# ------------------------------------------------ end system check


# ------------------------------------------------ goodnight (Task 20, 15.4)
# The brief's pattern, verbatim. `sleep` on its own is broad -- "what is
# sleep apnea" -- so `is_goodnight` refuses anything shaped like a question
# or a request to be told something, the same guard circumstances use.
GOODNIGHT = re.compile(r"\b(good ?night|going to bed|i'?m off to bed|sleep)\b",
                       re.IGNORECASE)


# "I can't sleep", "the laptop won't sleep": `sleep` negated is a statement
# about the night, not a farewell -- reciting the schedule and closing the
# loop on it would be the wrong answer to both.
_NOT_GOODNIGHT = re.compile(
    r"\b(?:can'?t|cannot|can not|won'?t|couldn'?t|didn'?t|don'?t|doesn'?t|"
    r"not|never|no)\b(?:\s+\w+){0,2}\s+sleep\b",
    re.IGNORECASE)


def is_goodnight(transcript: str) -> bool:
    text = transcript.strip()
    if text.endswith("?") or _QUESTION_START.search(text) or KNOWLEDGE_CUES.search(text):
        return False
    if _NOT_GOODNIGHT.search(text):
        return False
    return bool(GOODNIGHT.search(text))
# ------------------------------------------------ end goodnight


# Phrasing that explains WHY, rather than asking for something to be done --
# an exam, a deadline, travel, being sick. Checked only when ACTION_INTENT
# did not already match: a request to act is handled as a request to act,
# never reinterpreted as context to file away.
#
# A first-person statement, not a bare keyword: an earlier version matched
# `until`, `travel` and `sick` on their own, which caught ordinary questions
# too -- "what's on my calendar until Friday" is not a circumstance. This
# requires Miro to be talking about himself, close to one of these words,
# and `is_circumstance()` below additionally refuses anything that reads as
# a question at all.
CIRCUMSTANCE = re.compile(
    r"\b(i'?m|i am|i'?ll be|i will be|i have|i've got)\b.{0,40}\b"
    r"(sick|ill|travel(?:l)?ing|away|out of town|on holiday|"
    r"off (?:this|next) week|working (?:late|early)|up (?:late|early)|"
    r"(?:an? )?(?:exam|midterm|final|deadline)s?)\b"
    r"|\b(?:exam|finals|midterm|deadline) week\b"
    r"|\bfrom now until\b",
    re.IGNORECASE,
)

# A question, by shape: ends with "?", or opens with a question word. Checked
# before CIRCUMSTANCE is trusted at all -- "am I sick" or "how sick is the
# server" must never be filed away as something Miro told ATLAS about his
# own schedule.
_QUESTION_START = re.compile(
    r"^\s*(what|when|where|how|can|could|would|is|are|do|does)\b",
    re.IGNORECASE,
)


def is_circumstance(transcript: str) -> bool:
    """A first-person statement about Miro's own schedule -- never a
    question, whatever words it contains."""
    text = transcript.strip()
    if text.endswith("?") or _QUESTION_START.search(text):
        return False
    return bool(CIRCUMSTANCE.search(text))


# ----------------------------------------------------------------- knowledge

# Phrasing that asks to be TOLD something, rather than asked to look
# something up in his own systems. Deliberately generic -- Miro wants ATLAS
# to have general knowledge of everything, medicine to technology to
# anatomy, and this is the shape of a question about the world rather than
# about him.
KNOWLEDGE_CUES = re.compile(
    r"\b(what is|how does|why do|explain|difference between|symptoms|"
    r"anatomy|history of|how to)\b",
    re.IGNORECASE,
)


def _module_names() -> frozenset[str]:
    """Every module ATLAS could load, whether or not it currently has.

    `REGISTRY.modules` only holds what actually loaded -- a module missing
    its `requires_env` goes into `REGISTRY.disabled` instead (invariant 7:
    unconfigured is not the same as absent). Knowledge mode must not treat a
    disabled module's name as ordinary vocabulary just because it has no
    token yet, so this reads the package directory itself, the same way
    `Registry.load_all` discovers modules in the first place -- a name here
    does not depend on load succeeding."""
    import importlib
    import pkgutil

    pkg = importlib.import_module("atlas.modules")
    return frozenset(
        info.name for info in pkgutil.iter_modules(pkg.__path__)
        if not info.name.startswith("_")
        # A source ATLAS reads, and the question-answerer: neither is part of
        # his estate, and "assistant" is ordinary English ("hire a virtual
        # assistant" is a question about the world).
        and info.name not in ("perplexity", "assistant")
    )


def _system_names() -> frozenset[str]:
    """Module, business and agent names -- these count as naming ATLAS's own
    systems however they appear in the transcript, unqualified. Built fresh
    on each call: the roster is only as current as whatever last called
    `build_roster()`, and the module list is read live from disk."""
    from ..kernel.agents import ROSTER

    return frozenset(
        name.lower() for name in (_module_names() | set(ROSTER)) if name)


def _check_pairs() -> frozenset[tuple[str, str]]:
    """(dotted key, bare name) for every check currently registered, e.g.
    ("flycheap.database", "database"). Read from the live registry: a check
    that never loaded also never measured anything, so there is nothing
    named after it to guess about the way a disabled module's name still
    is."""
    from ..kernel.registry import REGISTRY

    return frozenset(
        (key.lower(), check.name.lower())
        for key, check in REGISTRY.checks.items()
        # A source ATLAS reads, not part of his estate: "my budget" is his.
        if check.name and not key.startswith("perplexity."))


# A bare check name doubling as a possessive/first-person frame: "is MY
# database ok", "how is ATLAS's database doing". Module and business names
# already count unqualified (`_system_names`), so this only has to cover the
# case where a check's name shows up on its own.
_POSSESSIVE_FRAME = re.compile(r"\b(my|our|atlas'?s?)\b", re.IGNORECASE)


def _check_name_qualifies(transcript: str, key: str, bare: str,
                          system_names: frozenset[str]) -> bool:
    """A check's bare name -- `database`, `public`, `api`, `session`,
    `jobs`, `index` -- is ordinary English on its own. It only counts as
    naming one of ATLAS's own checks when the transcript ties it to ATLAS:
    the qualified `module.check` form; the bare name within four words of a
    module or business name ("is the flycheap database ok"); or a
    possessive/first-person frame ("is my database ok"). "how does public
    key cryptography work" matches none of these, so `public` stays
    ordinary vocabulary."""
    text = transcript.lower()
    if key in text:
        return True
    words = re.findall(r"[a-z0-9']+", text)
    if bare not in words:
        return False
    idx = words.index(bare)
    window = words[max(0, idx - 4):idx] + words[idx + 1:idx + 5]
    if any(word in system_names for word in window):
        return True
    return bool(_POSSESSIVE_FRAME.search(text))


def _mentions_a_name(transcript: str) -> bool:
    system_names = _system_names()
    text = transcript.lower()
    if any(re.search(rf"\b{re.escape(name)}\b", text) for name in system_names):
        return True
    return any(_check_name_qualifies(transcript, key, bare, system_names)
              for key, bare in _check_pairs())


# A question about HIS estate rather than the world's knowledge, even though
# it names no specific module, business, agent or check. Caught two ways,
# neither of them a bare vocabulary word (an earlier version fired on
# "waiting", "balance", "today" and swallowed ordinary questions like "how
# does a waiting list work" and "what is a proposal in parliamentary
# procedure"):
#   - a first-person/possessive frame: "my", "our", "me", "atlas", "the
#     estate" -- "what is MY revenue this month".
#   - a short set of actual status-question phrasings: "what is/what's
#     broken/down/failing/wrong", "is anything/something broken/down/
#     failing/wrong", "what is/what's waiting/pending/due/overdue on/for
#     me".
_ESTATE_FRAME = re.compile(
    r"\b(my|our|me|atlas)\b|\bthe estate\b", re.IGNORECASE)
_STATUS_VERB = re.compile(
    r"\bwhat(?:'s| is)\s+(?:broken|down|failing|wrong)\b"
    r"|\b(?:is|are)\s+(?:anything|something)\s+(?:broken|down|failing|wrong)\b"
    r"|\bwhat(?:'s| is)\s+(?:waiting|pending|due|overdue)\s+(?:on|for)\s+me\b",
    re.IGNORECASE,
)


def _is_estate_framed(transcript: str) -> bool:
    return bool(_ESTATE_FRAME.search(transcript) or _STATUS_VERB.search(transcript))


def is_knowledge(transcript: str) -> bool:
    """A general-knowledge question -- medicine, technology, anatomy,
    history, anything not about Miro's own estate. Checked only when the
    transcript is neither an action request nor a circumstance.

    Two ways a question can turn out to be about his own systems instead:
    it names one of ATLAS's own modules, businesses, agents or (qualified)
    checks (`_mentions_a_name`) -- "explain how a guardian is appointed for
    an estate" names the guardian business -- or it is framed as a question
    about his own state without naming anything specific (`_is_estate_
    framed`) -- "what is broken" or "what is waiting on me". Either way it
    is left for a specialist to answer, never guessed at by a tool-less
    model."""
    if not KNOWLEDGE_CUES.search(transcript):
        return False
    if _mentions_a_name(transcript):
        return False
    return not _is_estate_framed(transcript)


# ----------------------------------------------------------------- views
#
# "Show me your face", "open the fleet", "open the guardian audit report":
# asking the dashboard to SHOW something. Navigation only -- a view reads,
# it never changes anything -- so it is answered here without a model call,
# and never when the same sentence also asks for something to be done.
# The brief's pattern, split across lines only for length; the string is
# unchanged.
VIEW = re.compile(
    r"\b(show me your face"
    r"|open (the )?(fleet|mission control|memory|brain|knowledge|marketing)"
    r"|show (me )?(the )?(fleet|brain|memory|marketing)"
    r"|open (the )?(report|document|mockup) (.+))\b",
    re.IGNORECASE)

# The natural word order VIEW does not cover: the kind comes LAST, as in
# "open the guardian audit report" or the reel's own "open the ESP32
# schematic". Tried only when VIEW did not match. The kind is what he calls
# the thing, not where it is stored: a schematic is a vault document.
VIEW_TITLED = re.compile(
    r"\bopen (the )?(?P<title>.+?) "
    r"(?P<kind>report|document|mockup|schematic|file|pdf|diagram|drawing|sheet)\b",
    re.IGNORECASE)

SCREENS = {
    "fleet": "/fleet",
    "mission control": "/",
    "memory": "/memory",
    # The firing brain is drawn on /core; /memory is the list behind it.
    "brain": "/core",
    "knowledge": "/knowledge",
    "marketing": "/marketing",
}

# Air Glass, the hand-tracking HUD (ui/src/pages/AirGlass.tsx): "open Air
# Glass", "show me airglass". Its own pattern so VIEW stays the brief's
# verbatim. From the menu bar it opens ATLAS Menu's own Air Glass window
# instead (`mac_command`).
_AIRGLASS = re.compile(r"(?:open|show)\s+(?:me\s+)?(?:the\s+)?air\s?glass\b", re.IGNORECASE)

#: What `/command` tells ATLAS Menu to open itself, by view screen. Opening
#: one of its own windows is the menu bar's UI, not an action: nothing runs
#: on the server and nothing goes through the executor.
MENUBAR_WINDOWS = {"/airglass": "airglass"}

_FILLER = frozenset({"the", "a", "an", "on", "about", "for", "of", "my",
                     "called", "named", "titled", "from", "and", "to", "in"})


# A view command is an imperative, said first: "show me…", "open…", "pull
# up…", optionally after his name or a please. "What does the schematic
# show" and "can you summarise the guardian audit report" are questions, not
# page changes. And the command must be the whole utterance, give or take a
# trailing please: "show me the memory usage" asks about memory usage.
_VIEW_LEAD = re.compile(r"^\s*(?:(?:hey\s+)?atlas[\s,]+)?(?:please\s+)?",
                        re.IGNORECASE)
_VIEW_TAIL = re.compile(r"^[\s.,!?]*(?:(?:please|for me|now)[\s.,!?]*)?$",
                        re.IGNORECASE)
_PULL_UP = re.compile(r"^pull up\b", re.IGNORECASE)


def view_target(transcript: str) -> dict | None:
    """What a view command asks to see, or None when it is not one.

    `{"screen": path}` for a named screen, or `{"screen": "/knowledge",
    "query": words, "kind": report|document|mockup}` for a named document.
    """
    said = transcript[_VIEW_LEAD.match(transcript).end():]
    said = _PULL_UP.sub("open", said)
    if (a := _AIRGLASS.match(said)) is not None and _VIEW_TAIL.match(said[a.end():]):
        return {"screen": "/airglass"}
    m = VIEW.match(said)
    if m and not _VIEW_TAIL.match(said[m.end():]):
        m = None
    if m:
        if m.group(1).lower() == "show me your face":
            return {"screen": "/core"}
        screen = m.group(3) or m.group(6)
        if screen:
            return {"screen": SCREENS[screen.lower()]}
        kind, query = m.group(8).lower(), m.group(9)
    else:
        t = VIEW_TITLED.match(said)
        if t is None or not _VIEW_TAIL.match(said[t.end():]):
            return None
        kind, query = t.group("kind").lower(), t.group("title")
    words = re.findall(r"[\w'-]+", query.lower())
    while words and words[0] in _FILLER:
        words.pop(0)
    return {"screen": "/knowledge", "query": " ".join(words).strip(".,?!"),
            "kind": kind}


# The last resort: "open (the) <title>", the whole utterance, with no kind
# word. Only a view when a document by that title actually exists
# (`resolve_view`); otherwise it falls through and is answered like anything
# else, so "open the pod bay doors" never opens a wrong document.
_OPEN_ANY = re.compile(
    r"open\s+(?:the\s+|my\s+)?(?P<title>[\w'.-]+(?:\s+[\w'.-]+){0,7}?)"
    r"(?=[\s.,!?]*(?:(?:please|for me|now)[\s.,!?]*)?$)",
    re.IGNORECASE)


def loose_title(transcript: str) -> str | None:
    """The title in a bare "open (the) <title>", or None."""
    said = transcript[_VIEW_LEAD.match(transcript).end():]
    said = _PULL_UP.sub("open", said)
    m = _OPEN_ANY.match(said)
    if m is None:
        return None
    words = re.findall(r"[\w'-]+", m.group("title").lower())
    while words and words[0] in _FILLER:
        words.pop(0)
    return " ".join(words) or None


async def resolve_view(transcript: str) -> dict | None:
    """`view_target`, then the last resort: a bare "open <title>" is a view
    only when `find_document(title, None)` clears the match threshold."""
    target = view_target(transcript)
    if target is not None:
        return target
    title = loose_title(transcript)
    if title is None:
        return None
    found = await find_document(title, None)
    if found is None:
        return None
    return {"screen": "/knowledge", "query": title, "kind": None,
            "found": found}


def _tokens(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9]+", text.lower())
            if w not in _FILLER}


def best_match(query: str, candidates: list[dict],
               kind: str | None) -> dict | None:
    """The candidate whose title best covers the words he said, or None.

    At least half of his (non-filler) words must appear in the title -- a
    single shared word such as "weekly" is not the report he asked for, and
    opening the wrong document is worse than saying none was found. Ties go
    to the named kind, then to the earliest candidate (callers pass newest
    first).
    """
    wanted = _tokens(query)
    if not wanted:
        return None
    best: tuple[float, int] | None = None
    chosen: dict | None = None
    for i, candidate in enumerate(candidates):
        have = _tokens(str(candidate.get("title") or ""))
        score = len(wanted & have) / len(wanted)
        if score < 0.5:
            continue
        rank = (score + (0.25 if kind and candidate.get("kind") == kind else 0), -i)
        if best is None or rank > best:
            best, chosen = rank, candidate
    return chosen


async def find_document(query: str, kind: str | None) -> dict | None:
    """Look a spoken title up across reports, vault documents and agency
    mockups, read fresh. Each source failing is skipped, not fatal: a
    missing table must not hide a match in the other two."""
    sources = (
        ("report", "SELECT id, title FROM reports ORDER BY at DESC LIMIT 300"),
        ("document", ("SELECT id, title FROM vault_documents "
                      "ORDER BY indexed_at DESC LIMIT 300")),
        ("mockup", ("SELECT m.id, l.name AS title FROM agency_mockups m "
                    "JOIN agency_leads l ON l.id = m.lead_id "
                    "ORDER BY m.at DESC LIMIT 100")),
    )
    candidates: list[dict] = []
    for source, sql in sources:
        try:
            rows = await fetch_all(sql)
        except Exception as exc:                        # noqa: BLE001
            log.info("could not read %s titles: %s", source, exc)
            continue
        candidates.extend({"kind": source, "id": r["id"], "title": r["title"]}
                          for r in rows or [])
    return best_match(query, candidates, kind)

# ----------------------------------------------------------------- focus
#
# A spoken status brings the matching screen into focus while it speaks
# (15.3). Module name -> the screen that module's numbers live on. Every
# business is here (the per-business agents answer "how is X doing"), plus
# the distinctively named modules whose own screen is not the status board.
# Ordinary words that are also module names -- content, messages, research,
# marketing -- are left out: an answer that says "research" in passing is not
# a question about the research module, and a card nobody asked for is noise.
BUSINESS_FOCUS: dict[str, str] = {
    "flycheap": "/status",
    "guardian": "/status",
    "inroots": "/status",
    "vpsie": "/status",
    "mirotech": "/status",
    "fiverr": "/status",
    "agency": "/status",
    "school": "/status",
    "gitlab": "/status",
    "netbird": "/status",
    "stripe_billing": "/status",
    "calendar": "/calendar",
    "nightshift": "/tasks",
    "youtube": "/marketing",
    "tiktok": "/marketing",
    "instagram": "/marketing",
    "linkedin": "/marketing",
}

# How a transcriber tends to write the names that are one word on disk.
_FOCUS_ALIASES: dict[str, str] = {
    "fly cheap": "flycheap",
    "miro tech": "mirotech",
    "stripe": "stripe_billing",
    "stripe billing": "stripe_billing",
    "night shift": "nightshift",
    "net bird": "netbird",
    "git lab": "gitlab",
    "you tube": "youtube",
    "tik tok": "tiktok",
    "linked in": "linkedin",
}

def _names_pattern(names: set[str]) -> re.Pattern[str]:
    return re.compile(
        r"\b(" + "|".join(re.escape(n) for n in sorted(names, key=len, reverse=True))
        + r")\b", re.IGNORECASE)


_FOCUS_NAMES = _names_pattern(set(BUSINESS_FOCUS) | set(_FOCUS_ALIASES))

# Names that are also ordinary words. He may SAY them ("how's school going")
# and mean the module, but an answer that says "your calendar is clear" or
# "school starts Monday" in passing is not pointing at a tile.
_ORDINARY = frozenset({"school", "agency", "calendar", "stripe"})


def _answer_names() -> re.Pattern[str] | None:
    """What an ANSWER may focus on: distinctive names of modules the
    registry actually knows (loaded or disabled), never ordinary words."""
    from ..kernel.registry import REGISTRY

    known = set(REGISTRY.modules) | set(REGISTRY.disabled)
    names = {n for n in BUSINESS_FOCUS if n in known and n not in _ORDINARY}
    names |= {alias for alias, n in _FOCUS_ALIASES.items()
              if n in names and alias not in _ORDINARY}
    return _names_pattern(names) if names else None


def resolve_focus(transcript: str, answer: str) -> dict | None:
    """Where the dashboard should look after this answer. Reads nothing but
    the module registry; makes no call.

    `{"screen": "/fleet"}` for a system check; `{"screen": ..., "module":
    name}` for the first module named in what he said, or failing that in
    the answer; None otherwise. The transcript wins: he asked about the
    thing he named, whatever else the answer mentions. The answer is only
    searched for distinctive names of modules the registry knows -- an
    ordinary word said in passing is not a focus (`_answer_names`)."""
    if is_system_check(transcript):
        return {"screen": "/fleet"}
    for text, pattern in ((transcript, _FOCUS_NAMES), (answer or "", _answer_names())):
        hit = pattern.search(text) if pattern else None
        if hit:
            said = hit.group(1).lower()
            name = _FOCUS_ALIASES.get(said, said)
            return {"screen": BUSINESS_FOCUS[name], "module": name}
    return None


# The on-device wake model (whisper-tiny.en, ONNX), fetched by
# ui/scripts/fetch-whisper-model.sh into ui/public/wake/whisper/ and copied
# into the build by Vite. These are the files the browser's worker cannot run
# without -- the 8-bit set it falls back to on wasm. A directory that exists
# but lacks them is a half-finished download, and reporting it as ready would
# be a detector that loads and never fires.
#
# Every wake model is checked under `ui_paths.wake_dir()` -- the directory
# main.py's `/wake` mount actually serves -- not under the source tree: a
# file fetched but never built is not something the browser can load.
WHISPER_FILES = (
    "config.json",
    "generation_config.json",
    "preprocessor_config.json",
    "tokenizer.json",
    "tokenizer_config.json",
    "onnx/encoder_model_quantized.onnx",
    "onnx/decoder_model_merged_quantized.onnx",
)


def _whisper_ready() -> bool:
    whisper = ui_paths.wake_dir() / "whisper"
    return all((whisper / name).is_file() for name in WHISPER_FILES)


def now() -> str:
    return datetime.now(UTC).isoformat()


def envelope(**payload: Any) -> dict[str, Any]:
    return {"as_of": now(), **payload}


@router.get("/capabilities")
async def capabilities(_s: SignedIn) -> dict:
    """What actually works right now.

    The browser asks this before offering voice, so an unavailable stage is a
    sentence on screen rather than a dead button.
    """
    from ..kernel.llm import ROUTES

    if not _WARMED and os.getenv("FISH_AUDIO_API_KEY"):
        # ATLAS's fixed lines in its Fish voice, rendered once in the
        # background so they are kept before they are needed.
        from ..kernel.background import spawn
        spawn(warm_clips(), "voice.warm_clips")

    picovoice =os.getenv("PICOVOICE_ACCESS_KEY", "").strip()
    wake = ui_paths.wake_dir()    # what /wake serves -- see WHISPER_FILES
    keyword = wake / "atlas.ppn"
    # Both halves are needed. A key with no keyword file, or a keyword file
    # with no key, produces a detector that loads and never fires -- which
    # reads on screen as "the wake word is on" and is the worst of the three
    # possible states.
    wake_ready = bool(picovoice) and keyword.exists()

    # Pinch-to-wake: on-device hand tracking, opted into explicitly because it
    # asks for the camera. The model file is fetched separately (it is large
    # and git-ignored), so an opt-in with no file would be the same silent
    # trap as a Picovoice key with no keyword: a detector that loads and
    # never fires. Both halves are required, same as above.
    pinch_flag = os.getenv("VOICE_PINCH_WAKE", "").lower() in ("1", "true", "yes")
    hand_model = wake / "hand_landmarker.task"
    pinch_wake = pinch_flag and hand_model.exists()

    # The wider gesture set -- cursor, drag, swipe, dismiss -- reuses the
    # exact same on-device model as pinch-wake and the same opt-in shape:
    # a flag with no model file is the same silent trap as the other two.
    gestures_flag = os.getenv("VOICE_GESTURES", "").lower() in ("1", "true", "yes")
    gestures = gestures_flag and hand_model.exists()

    # The on-device spoken wake word: whisper-tiny.en in a browser worker.
    # Opted into, and the model files present -- same two halves as above.
    whisper_ready = _whisper_ready()
    local_flag = os.getenv("VOICE_LOCAL_WAKE", "").lower() in ("1", "true", "yes")

    # A double clap, heard on this device, riding beside whichever of
    # porcupine, local-whisper or pinch is actually live (Task 47 / G4).
    # Unlike those three, it asks for no model file and no account -- the
    # rule lives entirely in `ui/src/voice/clap.ts` -- so the flag alone
    # decides it. The browser still only offers it beside a detector that
    # is actually running (see `createDetector` in `detector.ts`).
    clap_wake = os.getenv("VOICE_CLAP_WAKE", "").lower() in ("1", "true", "yes")

    speech = bool(os.getenv("FISH_AUDIO_API_KEY"))

    return envelope(
        transcription=bool(_transcribers()),
        answering=bool(ROUTES),
        # Speaking is Fish Audio only; hearing is Groq or OpenAI Whisper.
        speech=speech,
        wake_word=wake_ready,
        # Picovoice access keys are designed to be used from a browser, so
        # handing it to a signed-in session is what it is for. It is not a
        # server secret and is useless without the keyword file beside it.
        wake_key=picovoice if wake_ready else None,
        # The browser's own recogniser, as a fallback wake word. Off unless
        # explicitly enabled: in Chrome it streams audio to Google, which is
        # the opposite of what the on-device detector exists to guarantee.
        speech_api_wake=os.getenv("VOICE_BROWSER_WAKE", "").lower()
        in ("1", "true", "yes"),
        # On-device hand tracking, the fallback when there is no wake word at
        # all: nothing leaves the machine, but it does need the camera.
        pinch_wake=pinch_wake,
        # Palm-as-cursor, fist-to-grab, swipe, dismiss -- gated the same way.
        gestures=gestures,
        # "atlas" heard on this machine, with no account and no upload.
        local_wake=local_flag and whisper_ready,
        # A double clap, heard on this machine, no account and no upload --
        # and no model file to be missing, so it never appears below.
        clap_wake=clap_wake,
        missing=[name for name, present in (
            ("GROQ_API_KEY or OPENAI_API_KEY (transcription)",
             bool(_transcribers())),
            ("an LLM provider (answers)", bool(ROUTES)),
            ("FISH_AUDIO_API_KEY (speech)", speech),
            ("PICOVOICE_ACCESS_KEY (wake word)", bool(picovoice)),
            ("wake/atlas.ppn in the built UI (the trained keyword)",
             keyword.exists()),
            (("wake/hand_landmarker.task in the built UI (pinch wake, gestures "
              "-- run ui/scripts/fetch-hand-model.sh, then build)"),
             hand_model.exists()),
            (("wake/whisper/ in the built UI (local wake -- run "
              "ui/scripts/fetch-whisper-model.sh, then build)"), whisper_ready),
        ) if not present],
    )


# ---------------------------------------------------------------- intent

# Voice may READ, and it may PROPOSE. It may never execute.
#
# Miro asked for voice to send email, book things and send messages. The
# standing rule is that there is no path from talking to ATLAS to changing
# anything, and both are satisfied the same way: a spoken request becomes a
# Tier 2 proposal, and he approves it through the channel he already uses.
# The spoken reply is "I have put that to you", never "done".
#
# The enforcement is not in this file. `approvals.propose()` refuses any name
# that is not in the registry's closed catalog and carries each action's own
# tier, and `executor.execute` remains the single path to the outside world.
# What happens here is only the choosing of a name.
# Lead with what was done, not with what cannot be. "I can't do that" is
# both discouraging and inaccurate: the request was queued, which is the
# thing being asked for. What voice cannot do is *approve* it — a wake word
# is something anyone in the room can say, and a caller ID is something
# anyone can forge, so the approval has to happen somewhere that knows who
# is asking.
#
# Short, because it is heard (Miro, 2026-09-29: "not too chatty"). Why voice
# cannot approve -- anyone in earshot could say the same words -- is the
# design, not news he needs every time.
ASK = "Queued — approve it in Slack."
# The same, for an order that is one agent's work ("post a reel").
ASK_OWNED = "{name}'s on it — queued; approve it in Slack."

# On a call he can just say "do it", so telling him to go and find a button
# would be absurd. The dashboard microphone keeps the line above: a wake word
# is something anyone in the room can say, and there is no call in progress
# to have established what "it" refers to.
ASK_PHONE = "Say 'do it' and I'll run it."

NO_ACTION = "There's no action for that, so nothing's queued."
# Tier 3, said plainly, on the phone and the dashboard alike.
NEVER_DO = ("That's one I never do, on a call or anywhere: moving money, DNS, "
            "deleting, revoking keys or changing my own code. I can write it up "
            "for you to do by hand.")
# The never-repeat ledger's refusal, in a sentence he can answer.
FAILED_TWICE = ("That one failed twice recently, so I won't ask again until you "
                "tell me why it's different this time.")


def _refusal(action: str, exc: Exception) -> str | None:
    """Why `propose()` said no, in words for the ear; None when unknown."""
    try:
        from ..kernel.contracts import Tier
        from ..kernel.registry import REGISTRY
        decision = REGISTRY.decisions.get(action)
        if decision is not None and decision.tier is Tier.NEVER:
            return NEVER_DO
    except Exception as err:                            # noqa: BLE001
        log.info("could not read the tier of %s: %s", action, type(err).__name__)
    if "Say why it is different" in str(exc):
        return FAILED_TWICE
    return None

# How every spoken answer is shaped, on the dashboard and the phone alike.
# Brevity never drops honesty: "I don't know", "that source is unreachable"
# and a refusal's reason stay -- said in a few words, not left out.
VOICE_STYLE = (
    "This is heard, not read: answer in one or two short sentences, 35 words "
    "at most. Lead with the answer; never restate the question. Talk like a "
    "friend catching Miro up, not a butler reporting to a master: short "
    "everyday words, contractions, no jargon, no check names, no config or "
    "environment-variable names, no file paths. Say Vault, never Guardian. "
    "If you don't know, or can't, say so in a few words and why. "
)
FIGURES_LINE = (
    "Numbers about Miro's own businesses and systems come only from the live "
    "status above, with the time they were read said once, compactly (\"as "
    "of 7:52\"); if one is not there, say that source is unreachable. "
)

# The improve requests `kernel/improve.py` does not catch by pattern still
# must not come back as "I can't access that". Said in every voice prompt.
IMPROVE_LINE = (
    "If he asks you to improve something -- you, a feature, a module -- never "
    "say you have no access: offer to write it up as a development task for "
    "his supervised coding session (FlyCheap and Vault can go to the night "
    "shift, queued for his approval). "
)

# An order: the card line follows it ("Queued — approve it in Slack."), so
# the model says only what it will queue, in one breath.
ACTION_STYLE = (
    "Miro asked you to DO something. You cannot act from voice, but you CAN "
    "queue it for him to approve, and you are about to. Say in ONE short "
    "sentence (under 20 words) what you'll queue — then stop. Don't restate "
    "his words, don't refuse, don't apologise for lacking buttons, and don't "
    "claim to have done it. No config names or file paths. "
)


async def _pick_action(transcript: str, *,
                       principal: bool = False) -> tuple[str, dict] | None:
    """Choose a catalog action AND its parameters for one step of what was
    said, or nothing.

    `kernel.intent.choose` -- the same picker Slack and the call ATLAS
    placed use, which lists every action with its parameters and fills them
    from the words. The old picker here asked for a bare name and returned
    it with empty params, so every spoken card needed an id it never had
    and failed on approval. The model still picks a NAME from a closed list
    (Tier 3 is not on it) and a name it invents is discarded before
    `propose()`, which refuses it again and now also refuses a card missing
    a parameter its action needs.
    """
    from ..kernel import intent

    return await intent.choose(transcript, principal=principal)


# ------------------------------------------------ compound requests
#
# "Check the email, build them a website and invoice them 3.5 grand" is
# three requests. One card for the lot is wrong twice: he approves things he
# did not hear read back, or the other two vanish. At most three cards per
# sentence, one per step it could match, and the answer says which steps
# were queued and which had nothing behind them.
MAX_STEPS = 3

# A new step starts only where a new verb does. "Send a reply saying thanks
# and see you Monday" is one request; "restart the api and the worker" is
# one request about two things. Checked on the text AFTER a comma or an
# "and"/"then", so an ordinary conjunction inside a step never splits it.
_STEP_VERB = re.compile(
    rf"(?:{_ACTION_VERBS}|{_IMPERATIVE_VERBS}|check|file|book|email|archive|"
    rf"snapshot|create|order)\b",
    re.IGNORECASE)
_STEP_BREAK = re.compile(
    r"\s*[,;]\s*(?:and\s+)?(?:then\s+)?|\s+and\s+(?:then\s+)?|\s+then\s+",
    re.IGNORECASE)
_VOCATIVE = re.compile(
    r"^\s*(?:(?:hey|ok|okay)\s+)?(?:atlas|jarvis)\b[\s,]*", re.IGNORECASE)


def steps(transcript: str) -> list[str]:
    """The separate requests in one sentence, in the order said.

    A break is taken only where the text after it starts with a verb. A
    leading clause that does not ("I'm sick, cancel my meetings") is context
    for the step it precedes, not a step of its own.
    """
    text = _VOCATIVE.sub("", transcript or "").strip().rstrip(".!?")
    cuts = [m for m in _STEP_BREAK.finditer(text)
            if _STEP_VERB.match(text, m.end())]
    if cuts and not _STEP_VERB.match(text):
        # Context, not a request: it stays with the step it introduced.
        cuts = cuts[1:]
    out: list[str] = []
    start = 0
    for cut in cuts:
        out.append(text[start:cut.start()].strip())
        start = cut.end()
    out.append(text[start:].strip())
    return [part for part in out if part]


async def _queue_steps(transcript: str, *, source: str,
                       principal: bool) -> tuple[list[int], str]:
    """Raise one card per recognised step. Returns `(card ids, what to say
    after the model's description)`.

    A single step keeps the old replies (ASK / ASK_PHONE / NO_ACTION), or the
    refusal's own reason when the step named an action but not what it needs
    -- "I need to know which lead" rather than a card that cannot run.
    """
    import asyncio

    from ..kernel import missions
    from ..kernel.approvals import Unrunnable, propose

    # "Have Nora post the course": named, so it is hers -- her own catalog,
    # or her answer (kernel/missions.py). The dashboard only: on the phone
    # an agent's answer would not fit inside the line's deadline.
    if source == "voice" and missions.addressed(transcript) is not None:
        handed = await missions.delegate(transcript, channel="voice",
                                         who_="dashboard", principal=principal)
        if handed is not None:
            return ([handed.proposal_id] if handed.proposal_id is not None
                    else []), handed.spoken

    said = steps(transcript) or [transcript]
    now_, later = said[:MAX_STEPS], said[MAX_STEPS:]
    picks = await asyncio.gather(
        *(_pick_action(step, principal=principal) for step in now_),
        return_exceptions=True)

    results: list[tuple[str, int | None, str | None]] = []
    for step, picked in zip(now_, picks, strict=True):
        if isinstance(picked, BaseException):
            log.info("could not choose an action for %r: %s", step, picked)
            picked = None
        if picked is None:
            results.append((step, None, None))
            continue
        name, params = picked
        # Jarvis on the Mac (Miro, 2026-09-28: "yes, for Mac actions only").
        # On the signed-in dashboard it IS him, so a Mac action he says runs
        # now through kernel.spoken; a phone turn, or anything else, is a card.
        from ..kernel import spoken
        if source == "voice" and principal and spoken.speakable(name):
            done = await spoken.run_mac_action(name, params, transcript)
            results.append((step, None, done.detail))
            continue
        try:
            pid = await propose(
                name, params,
                reason=f"Asked out loud: “{transcript}”",
                evidence={"source": source, "transcript": transcript,
                          "step": step},
            )
        except Unrunnable as exc:
            # Missing what it needs, or given what it does not take.
            results.append((step, None, exc.spoken))
        except ValueError as exc:
            # Tier 3, not in the catalog after all, or never-repeat. He is
            # told which, when it is known: "there's no action for that" was
            # untrue for a thing ATLAS has and will not do.
            log.info("voice proposal refused: %s", exc)
            results.append((step, None, _refusal(name, exc)))
        except Exception:
            log.exception("voice proposal failed")
            results.append((step, None, None))
        else:
            results.append((step, pid, None))

    pids = [pid for _step, pid, _why in results if pid is not None]
    ask_line = ASK_PHONE if source == "phone" else ASK
    if len(said) == 1:
        _step, pid, why = results[0]
        if pid is None and why is None and source == "voice":
            # Nothing in the whole catalog: the agent that owns the domain
            # takes it before "I don't have an action for that".
            handed = await missions.delegate(transcript, channel="voice",
                                             who_="dashboard", principal=principal)
            if handed is not None:
                return ([handed.proposal_id] if handed.proposal_id is not None
                        else []), handed.spoken
        picked_one = picks[0]
        if (pid is not None and source == "voice"
                and isinstance(picked_one, tuple) and picked_one):
            # "Post a reel" is Isla's: the card line says who has it -- only
            # when the card raised is from her own modules, never a guess.
            owner = missions.voice_agent(transcript)
            module = str(picked_one[0]).split(".", 1)[0]
            if owner is not None and module in missions.agent_modules(owner):
                return pids, ASK_OWNED.format(name=missions.first_name(owner))
        return pids, (ask_line if pid is not None else why or NO_ACTION)

    lines = []
    for step, pid, why in results:
        if pid is not None:
            lines.append(f"“{step}” is queued as #{pid}.")
        else:
            lines.append(f"“{step}”: {why or 'no action for that.'}")
    lines += [f"“{step}”: not queued — {MAX_STEPS} at a time, ask me again."
              for step in later]
    lines.append(ask_line if pids else "Nothing to approve.")
    return pids, " ".join(lines)


# How he says the names out loud, and how the transcriber hears them. The
# dashboard mic turned "how is VPSie looking" into "Vipsy", the answer came
# back "nothing named Vipsy", and the question was wasted.
NAMES = (
    "Names he uses: VPSie (heard as Vipsy, VPS-E, VP see, vipsie), FlyCheap "
    "(fly cheap), Vault (its new name; the checks still say guardian; heard as guarder), Roots / InRoots (in roots, the "
    "attendance bot), NetBird, Fiverr (fiver), Mirotech, GitLab, Stripe, "
    "Matrix / k9."
)


async def live_context(modules: tuple[str, ...] | None = None) -> str:
    """What the board says right now, one line per check.

    Measured on atlas.example.com, "how is VPSie looking?":
      route() — agent + tools, fetching the same rows   125 s
      this query                                          0.06 s
      one DRAFT call with this as context                ~6 s
    The agent loop was spending two minutes to read what is already in the
    database, and the dashboard showed nothing while it did. Handing the rows
    to a single call gets the same answer (it was correct: 9 instances up,
    billing is the problem) twenty times faster.
    """
    try:
        # Driven from `checks`, not from `observations`: a check that has
        # never been measured is a row too, and it reads unknown.
        rows = await fetch_all(
            """SELECT p.name AS module, c.name AS check, c.interval_s,
                      o.health, o.detail, o.at
                 FROM checks c
                 JOIN properties p ON p.id = c.property_id
                 LEFT JOIN LATERAL (
                     SELECT health, detail, at FROM observations
                      WHERE check_id = c.id ORDER BY at DESC LIMIT 1
                 ) o ON TRUE
                WHERE c.enabled
                ORDER BY p.name, c.name""")
        waiting = await fetch_all(
            "SELECT action FROM proposals WHERE status = 'pending'")
    except Exception as exc:                            # noqa: BLE001
        log.warning("live context unavailable: %s", exc)
        return ("LIVE STATUS: unreadable right now. Say the source is "
                "unreachable rather than guessing.\n" + NAMES)
    read_at = datetime.now(UTC)
    if modules is not None:
        # One agent's own checks, for an answer in its name.
        rows = [r for r in rows if r["module"] in modules]
    lines = [_live_line(r, read_at) for r in rows]
    if modules is not None and not lines:
        lines = ["(no checks measure this)"]
    # A check's detail is whatever the far end said -- an HTTP body, a bot
    # wall's page, a subject line -- so the rows are quoted, never
    # instructions (invariant 4). The header, the names and the pending
    # action names are ATLAS's own and stay outside.
    return (f"LIVE STATUS, read at {_clock(read_at)} from the checks — answer "
            "from this, never from memory. Out loud never read check keys like "
            "guardian.cert or '(read 03:12)'; when you give a figure, say its "
            "read time once, compactly, the way a person would ('as of 7:52'). "
            "A check marked unknown is not known right now: say so, never call "
            "it fine:\n"
            + wrap_untrusted("\n".join(lines), "checks")
            + f"\n{len(waiting)} decision(s) waiting on Miro"
            + (": " + ", ".join(r["action"] for r in waiting[:6]) if waiting else "")
            + "\n" + NAMES)


# ------------------------------------------------- a voice turn, kept small
#
# Measured 2026-10-06 with the 142 checks registered in this repo (prod has
# more modules enabled, so more): the fast answer's prompt was ~16,300
# characters, every check one line (~4,100 tokens at four characters a
# token -- an estimate, no tokenizer here), and when it failed the agent
# fallback spent ~11,300 more on its first two calls (a classifier over the
# roster, then the concierge with fifteen tool schemas). On a free tier of
# 200,000 tokens a day, that was a few dozen questions. A voice turn now
# carries only what it can use: the rows it names, the rows that are not
# fine, one count line, the style and the names. tests/test_voice_budget.py
# pins the size.

VOICE_NEED_LINES = 8           # rows beyond the count line, at most
VOICE_DETAIL_CHARS = 70
VOICE_MAX_TOKENS = 500         # room for gpt-oss's low-effort thinking too
VOICE_WORKBOARD_CHARS = 1200   # the phone line's work board, when it has one

# How a module is said, beside its own name (NAMES says the same to the model).
_SAID_AS: dict[str, tuple[str, ...]] = {
    "vpsie": ("vipsy", "vps-e", "vp see", "vipsie", "vps"),
    "flycheap": ("fly cheap", "flight"),
    "guardian": ("vault", "guarder"),
    "inroots": ("roots", "in roots", "attendance"),
    "fiverr": ("fiver",),
    "netbird": ("net bird", "mesh"),
    "matrix_rooms": ("matrix", "k9"),
    "stripe_billing": ("stripe", "billing"),
    "markets": ("trading", "alinea", "basket"),
}


def _named_modules(transcript: str, modules: set[str]) -> set[str]:
    words = transcript.lower()
    named = {m for m in modules if m.replace("_", " ") in words or m in words}
    named |= {m for m, said in _SAID_AS.items()
              if m in modules and any(s in words for s in said)}
    return named


async def voice_context(transcript: str) -> str:
    """The live board for one spoken question: one count line, every row
    of a module he named, and the rows that are not fine -- not all of
    them. Same rules as `live_context`: an unmeasured or stale row is
    unknown, details are quoted (invariant 4), and each row says when it
    was read (invariant 6)."""
    try:
        rows = await fetch_all(
            """SELECT p.name AS module, c.name AS check, c.interval_s,
                      o.health, o.detail, o.at
                 FROM checks c
                 JOIN properties p ON p.id = c.property_id
                 LEFT JOIN LATERAL (
                     SELECT health, detail, at FROM observations
                      WHERE check_id = c.id ORDER BY at DESC LIMIT 1
                 ) o ON TRUE
                WHERE c.enabled
                ORDER BY p.name, c.name""")
        waiting = await fetch_all(
            "SELECT action FROM proposals WHERE status = 'pending'")
    except Exception as exc:                            # noqa: BLE001
        log.warning("live context unavailable: %s", exc)
        return ("LIVE STATUS: unreadable right now. Say the source is "
                "unreachable rather than guessing.\n" + NAMES)
    from ..kernel.freshness import row_health
    read_at = datetime.now(UTC)
    named = _named_modules(transcript, {r["module"] for r in rows})
    health = [str(row_health(r.get("health"), r.get("at"), r.get("interval_s"),
                             read_at)).lower() for r in rows]
    fine, unknown = health.count("ok"), health.count("unknown")
    # The rows he named first, then whatever is not fine, to the cap.
    order = ([i for i, r in enumerate(rows) if r["module"] in named]
             + [i for i, r in enumerate(rows)
                if r["module"] not in named and health[i] != "ok"])
    shown = order[:VOICE_NEED_LINES]
    picked = [_short(_live_line(rows[i], read_at)) for i in shown]
    rest_not_fine = sum(1 for i in order[VOICE_NEED_LINES:] if health[i] != "ok")
    count = (f"{len(rows)} checks: {fine} fine, {unknown} unknown, "
             f"{len(rows) - fine - unknown} not fine")
    body = "\n".join([count, *picked]
                     + ([f"(+{rest_not_fine} more not fine; on his screen)"]
                        if rest_not_fine else []))
    return (f"LIVE STATUS read at {_clock(read_at)}. Answer only from it; say a "
            "figure's read time once ('as of 7:52'); an unknown check is not "
            "known, never call it fine:\n"
            + wrap_untrusted(body, "checks")
            + f"\n{len(waiting)} decision(s) waiting on Miro\n" + NAMES)


def _short(line: str) -> str:
    key, sep, rest = line.partition(": ")
    if not sep or " — " not in rest:
        return line
    head, _dash, tail = rest.partition(" — ")
    read = ""
    if tail.endswith(")") and "(read " in tail:
        tail, _p, read = tail.rpartition(" (read ")
        read = f" (read {read}"
    return f"{key}: {head} — {tail[:VOICE_DETAIL_CHARS]}{read}"


def voice_style(transcript: str, *, wants_action: bool) -> str:
    """The instruction half of a voice prompt: the style, and the improve
    line only when he is asking to improve something."""
    if wants_action:
        style = ACTION_STYLE
    else:
        style = (VOICE_STYLE + "General questions (facts, advice, explanations) "
                 "you answer from your own knowledge. " + FIGURES_LINE)
    if re.search(r"\b(improv|better|feature|upgrade)", transcript, re.IGNORECASE):
        style += IMPROVE_LINE
    return style


async def voice_system(transcript: str, *, wants_action: bool = False) -> str:
    """The whole system prompt of a fast voice answer."""
    return (await voice_context(transcript) + "\n\n"
            + voice_style(transcript, wants_action=wants_action))


# Said, in ATLAS's own voice, when no model answered a turn. Two sentences,
# no config names (tests/test_voice_brevity.py), and never an invented time:
# the reset is the one a provider reported (kernel/quota.py), else "later
# today". Mac commands still run with no model (kernel/spoken.py).
NO_MODEL_SPENT = ("My free AI models have used today's allowance, so I can't "
                  "think right now; {when}. I can still open apps, search and "
                  "take notes.")
NO_MODEL_DOWN = ("I can't reach my AI models right now, so I can't think that "
                 "through. I can still open apps, search and take notes.")


def _rate_limited(exc: BaseException | None) -> bool:
    seen = 0
    while exc is not None and seen < 6:
        status = getattr(getattr(exc, "response", None), "status_code", None)
        if status == 429:
            return True
        exc, seen = exc.__cause__ or exc.__context__, seen + 1
    return False


def _voice_lane() -> list[Any]:
    from ..kernel.llm import ROUTES, Need
    return list(ROUTES.get(Need.TRIVIAL, []))


def out_of_allowance(exc: BaseException | None) -> bool:
    """Whether the models failed for want of allowance (a 429, or a pool its
    provider said is spent) rather than for an outage."""
    return (_rate_limited(exc) or isinstance(exc, quota.ReservedForMiro)
            or quota.spent_out(_voice_lane()))


def no_model_line(exc: BaseException | None = None) -> str:
    """What ATLAS says when no model answered: the allowance and when the
    first one comes back, as the provider said it, or that the models
    could not be reached."""
    if not out_of_allowance(exc):
        return NO_MODEL_DOWN
    at = quota.next_reset(_voice_lane())
    when = (f"the first one comes back at {at.strftime('%H:%M')}" if at
            else "they reset later today")
    return NO_MODEL_SPENT.format(when=when)


AGENT_FIGURES = (
    "Numbers come only from your checks and your recent work above, with the "
    "time they were read said once, compactly (\"as of 7:52\"); if a number "
    "is not there, say you can't see it right now -- never estimate. "
)
CONSULT_TIMEOUT_S = 25


async def consult(agent: str, transcript: str) -> str | None:
    """A spoken question answered by the agent that owns it -- "Here's Ivan:
    ..." -- or None, and the caller answers it as ATLAS.

    One model call, like the fast path (the agent's own tool loop is the
    two-minute path the fast path replaced): the agent's persona and brief,
    its own checks from the live board, and its own recent runs and
    missions. Reads only; a question never becomes a card here."""
    import asyncio

    from ..kernel import missions
    from ..kernel.agents import ROSTER
    from ..kernel.llm import Message, Need, think
    member = ROSTER.get(agent)
    if member is None:
        return None
    name = missions.first_name(agent)
    try:
        board = await live_context(missions.agent_modules(agent))
        work = await missions.agent_activity(agent)
        # The work board (kernel/workboard): this seat's missions, outbox,
        # cards and its modules' own progress tables -- "12/50 sites built"
        # lives there, not in the checks (Miro, 2026-09-30: "how is Hazel
        # doing on the 50 websites?" had no source to answer from).
        from ..kernel import workboard
        # Kept small like every voice turn (voice_context): the seat's board
        # at 2,000 characters, on the TRIVIAL lane's small free models.
        seat = await workboard.context_text(f"{name}: {transcript}",
                                            max_chars=2 * VOICE_WORKBOARD_CHARS)
        system = (f"{board}\n\n{work}\n\n{seat}\n\nYou are {name}, ATLAS's {member.title} "
                  f"agent ({member.brief}). Miro asked you this directly, out loud. "
                  + VOICE_STYLE + AGENT_FIGURES + IMPROVE_LINE)
        completion = await asyncio.wait_for(think(
            Need.TRIVIAL, system,
            [Message("user", wrap_untrusted(transcript, "voice", principal=True))],
            max_tokens=VOICE_MAX_TOKENS), timeout=CONSULT_TIMEOUT_S)
    except Exception as exc:                            # noqa: BLE001
        log.warning("%s could not answer (%s); ATLAS answers instead",
                    agent, type(exc).__name__)
        return None
    text = (getattr(completion, "text", "") or "").strip()
    return f"Here's {name}: {text}" if text else None


def _clock(at: datetime) -> str:
    """HH:MM in the box's local time, the clock the brief speaks."""
    return at.astimezone().strftime("%H:%M")


def _live_line(row: Any, now: datetime) -> str:
    """One check as the fast path hands it to the model.

    The same rule the spoken system check and /api/overview apply
    (`system_check.effective_health`): no observation is unknown, and an OK
    older than three of the check's own intervals is unknown too -- what a
    dead scheduler looks like, not a current OK. A stale reading's detail
    is left out, so its number cannot be read back as today's."""
    from datetime import timedelta

    from .system_check import effective_health

    key = f"{row['module']}.{row['check']}"
    at = row.get("at")
    interval_s = row.get("interval_s")
    interval = timedelta(seconds=int(interval_s)) if interval_s else None
    health, stale = effective_health(row.get("health"), at, interval, now)
    if at is None:
        return f"{key}: unknown — never measured"
    if stale:
        return (f"{key}: unknown — the last reading is stale "
                f"({at.astimezone().strftime('%a %H:%M')}), so this is not "
                "known now")
    return (f"{key}: {health} — {(row.get('detail') or '')[:90]} "
            f"(read {_clock(at)})")


async def spoken_answer(transcript: str, *, source: str,
                        preamble: str = "",
                        fast: bool | None = None) -> tuple[str, int | None]:
    """`spoken_turn`, for a caller that wants one card: the first raised."""
    text, pids = await spoken_turn(transcript, source=source,
                                   preamble=preamble, fast=fast)
    return text, (pids[0] if pids else None)


@quota.for_miro
async def spoken_turn(transcript: str, *, source: str,
                      preamble: str = "",
                      fast: bool | None = None) -> tuple[str, list[int]]:
    """Answer something said out loud. Returns `(what to say, card ids)`.

    An order is on the record (kernel/tasks.py) so he can check on it from
    any surface; a question is not a task and is not recorded.
    """
    from ..kernel import tasks
    task_id = None
    if is_order(transcript):
        task_id = await tasks.open(transcript, source=source, who=source)
    try:
        said, pids = await _spoken_turn(transcript, source=source, preamble=preamble,
                                        fast=fast)
    except Exception:
        await tasks.close(task_id, route="answer", ok=False, outcome="failed")
        raise
    if task_id is not None:
        await tasks.close(task_id, route="cards" if pids else "answer",
                          ok=None, outcome=said, cards=pids)
    return said, pids


async def _spoken_turn(transcript: str, *, source: str,
                       preamble: str = "",
                       fast: bool | None = None) -> tuple[str, list[int]]:
    """Answer something said out loud. Returns `(what to say, card ids)`.

    The one place that decides what a spoken request may reach, shared by the
    dashboard microphone and the phone line. Both surfaces have the same
    problem — anyone within earshot, or anyone who can forge a caller ID, can
    say words at ATLAS — so both get the same answer: describe it, raise it
    as a proposal, and let an authenticated surface approve it.

    `propose()` refuses any name outside the registry's closed catalog and
    stamps each action's own tier, so nothing here can widen what voice
    reaches. It also notifies on its own; a caller must not send a second
    message on the success branch or one request becomes two alerts.
    """
    # The phone is the surface with a hard deadline on it, so it takes the
    # single-round-trip path by default rather than having to ask for it.
    if fast is None:
        fast = source == "phone"
    # "Improve X" is a development handoff, the same on every surface: a note
    # for ATLAS itself, a nightshift.run card for FlyCheap/Vault. Never run.
    from ..kernel import improve
    handled = await improve.handle(transcript, source=source,
                                   principal=source == "phone")
    if handled is not None:
        return handled.spoken, ([handled.proposal_id]
                                if handled.proposal_id is not None else [])
    wants_action = is_order(transcript)
    if wants_action:
        # A mission is never started from here: this is the phone line,
        # where caller ID proves nothing (kernel/missions.py, NOT_HERE).
        from ..kernel import missions
        taken = await missions.take(transcript, channel=source, who_=source,
                                    principal=False)
        if taken is not None:
            return taken.spoken, []
    from ..kernel.agents import route

    if wants_action:
        question = ACTION_STYLE + IMPROVE_LINE + "\n\n"
    else:
        question = (
            VOICE_STYLE
            + "General questions (facts, advice, explanations) you answer from "
            "your own knowledge. " + FIGURES_LINE
            + IMPROVE_LINE + "\n\n"
        )
    prompt = preamble + question
    if fast and not preamble:
        # The checks say what is up; the work board says what every agent is
        # doing (kernel/workboard), so a question about anyone's work has a
        # source. It is self-guarding and returns one line when unreadable.
        # Both kept small: a phone turn is a voice turn (voice_context).
        from ..kernel import tasks, workboard
        prompt = (await voice_context(transcript) + "\n\n"
                  + await workboard.context_text(
                      transcript, max_chars=VOICE_WORKBOARD_CHARS)
                  + "\n\n" + await tasks.context_text(limit=5)
                  + "\n\n" + question)
    try:
        if fast:
            # One round trip, deliberately. `route()` classifies to an agent
            # and then lets that agent think, with tools -- two calls and
            # often more, measured at thirty seconds and worse. That is fine
            # at a keyboard and fatal on a phone: Twilio abandons a webhook
            # at fifteen seconds and the caller hears an error instead of an
            # answer.
            import asyncio

            from ..kernel.llm import Message, Need, think
            # Capped: with the head model rationed, the chain can slide
            # through slower providers for two minutes -- 124 s on prod.
            try:
                completion = await asyncio.wait_for(think(
                    Need.TRIVIAL, prompt,
                    [Message("user", wrap_untrusted(
                        transcript, source, principal=source == "phone"))],
                    max_tokens=VOICE_MAX_TOKENS,
                ), timeout=12)
            except TimeoutError:
                slow = ("My models are slow to answer right now. Ask me again "
                        "in a moment.")
                return slow, []
            answer = (getattr(completion, "text", "") or "").strip() or None
        else:
            answer = await route(prompt + wrap_untrusted(
                transcript, source, principal=source == "phone"))
    except RuntimeError as exc:
        # Every model failed: said honestly, never a dead line on a call.
        log.info("spoken turn unanswered: %s", exc)
        return no_model_line(exc), []
    if answer is None:
        return no_model_line(None), []
    if not wants_action:
        return answer, []

    pids, tail = await _queue_steps(transcript, source=source,
                                    principal=source == "phone")
    return f"{answer}\n\n{tail}", pids


@router.post("/transcribe")
async def transcribe(_s: SignedIn, audio: Audio) -> dict:
    """Audio in, words out. The audio is never written to disk or to Postgres.

    This is a POST that changes nothing in the world. It is the one exception to
    "the dashboard has no POST", and it is here rather than in dashboard.py so
    that the route table of the read-only API stays obviously empty of writes.
    """
    blob = await audio.read()
    if not blob:
        raise HTTPException(400, "no audio")
    if len(blob) > MAX_AUDIO_BYTES:
        raise HTTPException(413, "that was too long — keep it under two minutes")

    if not _transcribers():
        # Honest, not silent.
        raise HTTPException(503, NO_TRANSCRIBER)

    text, via = await transcribe_audio(blob, audio.content_type or "audio/webm")
    if text is None:
        raise HTTPException(502, via)
    return envelope(transcript=text, via=via)


NO_TRANSCRIBER = ("No transcriber is configured, so I can't hear you. "
                  "Set GROQ_API_KEY or OPENAI_API_KEY to enable it.")


async def transcribe_audio(blob: bytes, content_type: str) -> tuple[str | None, str]:
    """`(transcript, provider)` or `(None, why)`. Never raises.

    Kept apart from the microphone route so any recorded clip is heard by
    exactly the providers, and in exactly the order, that the dashboard
    microphone is.

    Each provider is tried in turn. The failure that prompted this was not
    a bug: the OpenAI account ran out of credit and every turn came back 429
    credit_balance_exhausted. One provider means one billing account is a
    single point of failure for "can ATLAS hear me at all".
    """
    providers = _transcribers()
    if not providers:
        return None, NO_TRANSCRIBER
    failures: list[str] = []
    for name, call in providers:
        try:
            text = await call(blob, content_type)
            if failures:
                log.info("transcribed with %s after %s failed", name,
                         ", ".join(failures))
            return text[:MAX_TRANSCRIPT], name
        except Exception as exc:                        # noqa: BLE001
            from ..modules._http import explain
            reason = explain(exc)
            log.warning("transcription via %s failed: %s", name, reason)
            failures.append(f"{name}: {reason}")
    return None, "Transcription failed — " + "; ".join(failures)


def _transcribers() -> list[tuple[str, Any]]:
    """Every configured speech-to-text provider, preferred first.

    Groq's Whisper first when both exist: it is the free bench, and the key
    is already on the box for the reel captions. OpenAI's Whisper is the
    paid second. ElevenLabs is gone (2026-09-25: the account is cancelled;
    Fish Audio speaks, these two hear).
    """
    out: list[tuple[str, Any]] = []
    if os.getenv("GROQ_API_KEY"):
        out.append(("groq", _transcribe_groq))
    if os.getenv("OPENAI_API_KEY"):
        out.append(("whisper", _transcribe_whisper))
    return out


def _upload_name(content_type: str) -> str:
    """A filename whose extension matches the audio actually sent. Both
    providers sniff the extension as well as the content type, and an
    OGG/Opus clip is not the browser's WebM."""
    ct = (content_type or "").lower()
    for marker, name in (("ogg", "speech.ogg"), ("opus", "speech.ogg"),
                         ("webm", "speech.webm"), ("mp4", "speech.m4a"),
                         ("m4a", "speech.m4a"), ("mpeg", "speech.mp3"),
                         ("mp3", "speech.mp3"), ("wav", "speech.wav")):
        if marker in ct:
            return name
    return "speech.webm"


async def _transcribe_whisper(blob: bytes, content_type: str) -> str:
    import httpx

    async with httpx.AsyncClient(timeout=60) as client:
        response = await client.post(
            "https://api.openai.com/v1/audio/transcriptions",
            headers={"Authorization": f"Bearer {os.environ['OPENAI_API_KEY']}"},
            files={"file": (_upload_name(content_type), blob, content_type)},
            data={"model": "whisper-1"},
        )
        response.raise_for_status()
        return str(response.json().get("text", "")).strip()


async def _transcribe_groq(blob: bytes, content_type: str) -> str:
    """The same Groq Whisper the reel captions use (`content.captions`),
    asked for plain text rather than word timings."""
    import httpx

    from ..modules.content.captions import GROQ_MODEL, GROQ_URL

    async with httpx.AsyncClient(timeout=60) as client:
        response = await client.post(
            GROQ_URL,
            headers={"Authorization": f"Bearer {os.environ['GROQ_API_KEY']}"},
            files={"file": (_upload_name(content_type), blob, content_type)},
            data={"model": GROQ_MODEL, "response_format": "json"},
        )
        response.raise_for_status()
        return str(response.json().get("text", "")).strip()



# ---------------------------------------------------------------- speech

# The request builder (`_fish_request`, `FISH_TTS_URL`) lives in
# `atlas.modules._fish`, shared with the reel renderer, and is imported at
# the top of this file under its old names for the routes and their tests.
MAX_SPEAK_CHARS = 1500


# The Agency room's two extra voices (Fish `reference_id`s), by the closed
# names `/speak` accepts. Read from env at request time; never a voice id
# sent by the browser. An unset one falls back to ATLAS's own voice rather
# than failing -- the meeting still plays, and the `X-Voice` header says
# which voice actually spoke.
SPEAK_VOICE_ENV = {"female": "FISH_VOICE_FEMALE", "male": "FISH_VOICE_MALE"}


def _speak_voice(raw: Any) -> tuple[str, str | None]:
    """`(voice that will speak, reference_id or None for ATLAS's own)`.
    A 400 for anything outside `personas.VOICES` -- the catalog is closed."""
    from ..kernel.personas import ATLAS_VOICE, VOICES
    if raw is None or raw == "":
        return ATLAS_VOICE, None
    if not isinstance(raw, str) or raw not in VOICES:
        raise HTTPException(
            400, f"voice must be one of: {', '.join(sorted(VOICES))}")
    env = SPEAK_VOICE_ENV.get(raw)
    ref = os.getenv(env, "").strip() if env else ""
    return (raw, ref) if ref else (ATLAS_VOICE, None)


async def _fish_tts(text: str, voice_id: str | None = None) -> bytes:
    import httpx

    url, headers, body = _fish_request(text, voice_id)
    async with httpx.AsyncClient(timeout=45) as client:
        response = await client.post(url, headers=headers, json=body)
        response.raise_for_status()
        return response.content


@router.post("/speak")
async def speak(request: Request, _s: SignedIn) -> Response:
    """Text in, spoken audio out -- Fish Audio, mp3.

    Like /transcribe, a POST that changes nothing in the world: it renders
    words ATLAS has already typed on screen into sound. Nothing is stored.
    The key is read here and goes only into the request header; every
    failure reason passes through `explain()`, which redacts it, before it
    reaches a log line or the browser.

    An optional `voice` -- "atlas" (the default), "female" or "male" -- picks
    one of three fixed voices (`SPEAK_VOICE_ENV`); anything else is a 400.
    The `X-Voice` response header names the voice that actually spoke.
    """
    try:
        body = await request.json()
    except ValueError:
        raise HTTPException(400, "expected a JSON object with a text field") from None
    if not isinstance(body, dict):
        raise HTTPException(400, "expected a JSON object with a text field")
    text = str(body.get("text") or "").strip()
    if not text:
        raise HTTPException(400, "nothing to say")
    if len(text) > MAX_SPEAK_CHARS:
        raise HTTPException(413, f"too long to speak — keep it under "
                                 f"{MAX_SPEAK_CHARS} characters")
    spoke_as, voice_id = _speak_voice(body.get("voice"))
    from . import voice_cache
    own_voice = voice_id is None
    # A fixed line in ATLAS's own voice comes from the Fish clip already
    # rendered (voice_cache): instant, and no Fish call at all.
    if own_voice and voice_cache.is_fixed(text) and (
            clip := voice_cache.cached(text)) is not None:
        return _mp3(clip, spoke_as, "cache")
    if not os.getenv("FISH_AUDIO_API_KEY"):
        raise HTTPException(
            503, "No voice is configured, so I can only type. "
                 "Set FISH_AUDIO_API_KEY to enable speech.")
    try:
        # ATLAS's own voice keeps the original one-argument call.
        fish_at = time.monotonic()
        audio = await (_fish_tts(text, voice_id) if voice_id else _fish_tts(text))
        if not audio:
            raise RuntimeError("Fish Audio returned no audio")
        log.info("voice timing: Fish Audio %d ms for %d chars",
                 (time.monotonic() - fish_at) * 1000, len(text))
    except Exception as exc:                            # noqa: BLE001
        from ..modules._http import explain
        reason = explain(exc)
        # Never another voice: the same line, already rendered by Fish, or
        # nothing and the words stay on screen.
        if own_voice and (clip := voice_cache.fallback_for(text)) is not None:
            log.warning("speech via fish audio failed (%s); said a kept Fish "
                        "clip of the line instead", reason)
            return _mp3(clip, spoke_as, "fallback")
        log.warning("speech via fish audio failed: %s", reason)
        raise HTTPException(502, f"Speech failed — {reason}") from None
    if own_voice:
        voice_cache.store(text, audio)
    return _mp3(audio, spoke_as, "fish")


def _mp3(audio: bytes, spoke_as: str, source: str) -> Response:
    """`X-Voice` names the voice; `X-Voice-Source` says whether Fish Audio
    rendered it now ("fish") or it is a Fish clip kept earlier ("cache",
    "fallback") -- it is Fish Audio either way."""
    return Response(content=audio, media_type="audio/mpeg",
                    headers={"Cache-Control": "no-store", "X-Voice": spoke_as,
                             "X-Voice-Source": source})


@router.get("/clip/{name}")
async def clip(name: str, _s: SignedIn, cached_only: bool = False) -> Response:
    """One of ATLAS's fixed lines ("listening", "no_voice", ...) in its Fish
    voice, for the dashboard and ATLAS Menu to play without a system voice.
    Rendered once and kept (voice_cache). `cached_only` never calls Fish:
    what a client asks for when Fish has just failed."""
    from . import voice_cache
    text = voice_cache.fixed_lines().get(name)
    if text is None:
        raise HTTPException(404, "no such line")
    if (audio := voice_cache.cached(text)) is not None:
        return _mp3(audio, "atlas", "cache")
    if cached_only or not os.getenv("FISH_AUDIO_API_KEY"):
        raise HTTPException(404, "that line has not been rendered yet")
    try:
        audio = await _fish_tts(text)
        if not audio:
            raise RuntimeError("Fish Audio returned no audio")
    except Exception as exc:                            # noqa: BLE001
        from ..modules._http import explain
        log.warning("could not render the %s line: %s", name, explain(exc))
        raise HTTPException(502, "Speech failed") from None
    voice_cache.store(text, audio)
    return _mp3(audio, "atlas", "fish")


_WARMED: list[bool] = []


async def warm_clips() -> int:
    """Render every fixed line not yet kept, once per process. Started in
    the background when a voice surface first loads (`capabilities`), so
    the lines are there before Fish Audio is ever needed for them -- and
    before a day it is down. Returns how many were rendered."""
    from . import voice_cache
    if _WARMED or not os.getenv("FISH_AUDIO_API_KEY"):
        return 0
    _WARMED.append(True)
    made = 0
    for name, text in voice_cache.fixed_lines().items():
        if voice_cache.cached(text) is not None:
            continue
        try:
            audio = await _fish_tts(text)
        except Exception as exc:                        # noqa: BLE001
            from ..modules._http import explain
            log.warning("could not render the %s line: %s", name, explain(exc))
            continue
        if audio:
            voice_cache.store(text, audio)
            made += 1
    if made:
        log.info("rendered %s fixed voice line(s) in Fish Audio", made)
    return made


async def _mac_without_a_model(transcript: str) -> str | None:
    """A Mac command read in code when no model answered, or None when the
    words are not one (or reading them needed a model too)."""
    from ..kernel import spoken
    try:
        heard = await spoken.run_spoken_commands(transcript, origin=spoken.DASHBOARD)
    except Exception as exc:                            # noqa: BLE001
        log.info("no model, and the Mac command could not be read (%s)",
                 type(exc).__name__)
        return None
    return heard.line if heard.handled else None


@router.post("/ask")
@quota.for_miro
async def ask(request: Request, _s: SignedIn) -> dict:
    """Answer a spoken question. Reads only.

    The transcript is wrapped as untrusted before it reaches a prompt for the
    same reason email is: it is whatever was said in the room, by anyone.

    Miro is waiting on it, so every model call beneath it may spend the
    free allowance kept for him (kernel/quota.py); and it never ends in
    silence -- when no model answers, `no_model_line` is what is said.
    """
    body = await request.json()
    transcript = str(body.get("transcript", "")).strip()[:MAX_TRANSCRIPT]
    if not transcript:
        raise HTTPException(400, "nothing was said")

    started = time.monotonic()
    wants_action = is_order(transcript)

    # ---- improve X (kernel/improve.py): a development handoff, never "I
    # can't access that". First, because every branch below would answer it
    # as small talk or a catalog miss. For ATLAS itself it queues nothing
    # and writes a note; for FlyCheap/Vault it raises a nightshift.run card
    # that waits for his yes. Nothing here, or there, can execute.
    from ..kernel import improve
    handled = await improve.handle(transcript, source="voice", principal=True)
    if handled is not None:
        return await _record(transcript, handled.spoken, True, started,
                             proposal_id=handled.proposal_id, kind="improve")
    # ---- end improve

    # ---- missions (kernel/missions.py): a job bigger than one action goes
    # to the agent that owns it -- "find 50 local businesses with a poor
    # website and make them one" is the agency's, run in the background,
    # Tier 1 through the executor and Tier 2 as cards. Ahead of every branch
    # below: split into steps, or answered as small talk, it was refused.
    # A short reply to "Which area should I search?" is its answer.
    from ..kernel import missions
    mission = None
    if (not wants_action and view_target(transcript) is None
            and not is_system_check(transcript)):
        # Only something that is nothing else can be the area he was asked for.
        mission = await missions.answer(transcript, channel="voice")
    if mission is None and wants_action:
        mission = await missions.take(transcript, channel="voice",
                                      who_="dashboard", principal=True)
    if mission is not None:
        return await _record(transcript, mission.spoken, True, started,
                             proposal_id=mission.proposal_id, kind="mission")
    # ---- end missions

    # ---- VIEW: "show me your face", "open the guardian audit report".
    # Navigation, answered without a model. Its own block, ahead of every
    # other kind, and only when the sentence is not also a request to act:
    # "open the fleet and restart it" is answered as the action request it
    # is, never quietly reduced to a page change.
    target = None if wants_action else await resolve_view(transcript)
    if target is not None:
        focus, answer = await _view(target)
        return await _record(transcript, answer, wants_action, started,
                             kind="view", focus=focus)

    # ---- system check (Task 20): the estate's agents and checks, read
    # fresh, spoken with their read time, and the fleet brought up behind the
    # checklist. Before SELF -- "you up?" is about the estate, "how are you"
    # is about ATLAS -- and never when something else is asked alongside it:
    # `is_system_check` refuses "system check and restart flycheap". The
    # check reads only, so "run a system check" is not an action request.
    if is_system_check(transcript):
        from . import system_check
        try:
            check = await system_check.build()
        except Exception as exc:
            log.exception("the system check could not be built")
            from ..modules._http import explain
            why = " ".join(explain(exc).split())[:160]
            return await _record(transcript, None, False, started,
                                 error=f"I could not run the system check: {why}",
                                 kind="system_check")
        return await _record(transcript, check["line"], False, started,
                             kind="system_check",
                             focus=resolve_focus(transcript, check["line"]),
                             check=check)
    # ---- end system check

    # ---- self questions (Task 33): before circumstances, never an action.
    if not wants_action and SELF.search(transcript):
        from ..kernel import selfmodel
        try:
            answer = await selfmodel.spoken()
        except Exception as exc:
            log.exception("the self-model could not answer")
            from ..modules._http import explain
            why = " ".join(explain(exc).split())[:160]
            return await _record(transcript, None, wants_action, started,
                                 error=f"I could not read my own state: {why}",
                                 kind="self")
        # Said: the first two sentences ("I'm running. Up 3 hours, ..."); the
        # peers, the ledger and the rest stay on screen.
        return await _record(transcript, answer, wants_action, started,
                             kind="self", spoken=first_sentences(answer, 2))
    # ---- end self questions

    # ---- goodnight (Task 20): what is actually scheduled, from tables and
    # module agendas only (`kernel/tonight.py`). No model, no route(), and
    # nothing written but this turn's own row. After SELF, before
    # circumstances: "I'm off to bed" is a farewell, not context to file.
    if not wants_action and is_goodnight(transcript):
        from ..kernel import tonight
        try:
            answer = await tonight.build()
        except Exception as exc:
            log.exception("tonight's plan could not be built")
            from ..modules._http import explain
            why = " ".join(explain(exc).split())[:160]
            return await _record(transcript, None, wants_action, started,
                                 error=f"I couldn't put tonight together: {why}",
                                 kind="goodnight")
        return await _record(transcript, answer, wants_action, started,
                             kind="goodnight")
    # ---- end goodnight

    # Circumstances are noted, not routed to a specialist: "exam week" is not
    # a question ATLAS answers, it is context he is handing over. Checked
    # before route() is ever reached, and only when this is not also a
    # request to act -- an action request is answered as one, never
    # reinterpreted as something to file away instead.
    if not wants_action and is_circumstance(transcript):
        from ..kernel.rhythm import note, parse_until
        until = parse_until(transcript)
        try:
            await note(kind="circumstance", detail=transcript, until=until)
        except Exception:
            log.exception("could not record circumstance note")
        answer = ("Noted — I'll keep that in mind until "
                  f"{until.strftime('%A %d %B')}."
                  if until else "Noted — I'll keep that in mind.")
        return await _record(transcript, answer, wants_action, started)

    # ------------------------------------------------------------ knowledge
    #
    # General knowledge -- medicine, technology, anatomy, history, whatever
    # Miro asks -- answered directly, never routed to a specialist. Checked
    # only once action and circumstance are both ruled out, and only when no
    # module, business, agent or check name appears in what was said: a
    # question that names one of ATLAS's own is left for a specialist to
    # judge, never guessed at here as ordinary trivia.
    if not wants_action and is_knowledge(transcript):
        from ..kernel import knowledge

        try:
            answer = await knowledge.answer(transcript, quick=True)
        except RuntimeError as exc:
            log.info("knowledge question unanswered: %s", exc)
            line = no_model_line(exc)
            return await _record(transcript, None, wants_action, started,
                                 error=line, spoken=line, kind="no_model")
        except Exception as exc:
            log.exception("knowledge answer failed")
            return await _record(transcript, None, wants_action, started,
                                 error=str(exc))
        return await _record(transcript, answer, wants_action, started,
                             intent="knowledge", spoken=knowledge.spoken_part(answer))

    # ---- the agent that owns it (Miro, 2026-09-29: "ATLAS should coordinate
    # and redirect to the appropriate agent"). "How's my trading" is Ivan's,
    # "what did Nora do today" is Nora's, "check FlyCheap" is Sofia's: that
    # agent answers, introduced by name, rather than a generic concierge
    # line. Questions only -- an order goes on to the cards below, and a
    # Tier 3 request is never handed to anyone (`missions.voice_agent`).
    if not wants_action:
        owner = missions.voice_agent(transcript)
        if owner is not None:
            said = await consult(owner, transcript)
            if said is not None:
                return await _record(transcript, said, wants_action, started,
                                     agent=owner,
                                     focus=resolve_focus(transcript, said))
    # ---- end the agent that owns it

    from ..kernel.agents import route

    failure: BaseException | None = None
    try:
        if wants_action:
            # Answer the question "what would you do", never "do it".
            question = (
                ACTION_STYLE
                + IMPROVE_LINE + "\n\n"
                + wrap_untrusted(transcript, "voice", principal=True)
            )
        else:
            question = (
                VOICE_STYLE
                + "General questions (facts, advice, explanations) you answer "
                "from your own knowledge. " + FIGURES_LINE
                + IMPROVE_LINE + "\n\n"
                + wrap_untrusted(transcript, "voice", principal=True)
            )
        # One call over the live rows first: ~6 s instead of the ~2 minutes
        # route() spends fetching them through an agent's tools, which is
        # what made the microphone look dead. Small on purpose (see
        # voice_context) and on the TRIVIAL lane, which leads with the small
        # free models. The agent path (other chains) stays as the fallback,
        # except when the models are out of allowance: asking the same
        # rationed pools again through an agent spends thousands of tokens
        # for the same 429.
        answer = None
        try:
            import asyncio

            from ..kernel.llm import Message, Need, think
            board_at = time.monotonic()
            system = await voice_system(transcript, wants_action=wants_action)
            asked_at = time.monotonic()
            completion = await asyncio.wait_for(think(
                Need.TRIVIAL, system,
                [Message("user", wrap_untrusted(transcript, "voice", principal=True))],
                max_tokens=VOICE_MAX_TOKENS), timeout=25)
            answer = (getattr(completion, "text", "") or "").strip() or None
            # Per stage, for "he takes long to answer" (2026-10-06): routing
            # to here, reading the board, the model's whole answer.
            log.info("voice timing: routed %d ms, board %d ms, model %d ms (%s:%s)",
                     (board_at - started) * 1000, (asked_at - board_at) * 1000,
                     (time.monotonic() - asked_at) * 1000,
                     getattr(completion, "provider", "?"), getattr(completion, "model", "?"))
        except Exception as exc:                        # noqa: BLE001
            failure = exc
            log.warning("fast voice answer failed (%s)", type(exc).__name__)
        if answer is None and not out_of_allowance(failure):
            log.info("asking the agent instead")
            answer = await route(question)
    except RuntimeError as exc:
        answer, failure = None, exc
        log.info("voice question unanswered: %s", exc)
    except Exception as exc:
        log.exception("voice answer failed")
        answer = None
        return await _record(transcript, None, wants_action, started, error=str(exc))

    if answer is None:
        # No model, but a Mac command needs none: "open Notes", "volume to
        # 30" are read in code (kernel/spoken.py) and run as from the menu
        # bar -- Tier 1 Mac actions only, through the executor. Tried on
        # every such turn, not only on what `is_order` calls an order: the
        # parser itself says when words are not a Mac command.
        heard = await _mac_without_a_model(transcript)
        if heard is not None:
            return await _record(transcript, heard, True, started,
                                 kind="mac_command")
        # Never silent: the honest line, said in ATLAS's voice.
        line = no_model_line(failure)
        return await _record(transcript, None, wants_action, started,
                             error=line, spoken=line, kind="no_model")

    proposal_id: int | None = None
    if wants_action:
        # Try to turn it into something he can approve. `propose()` refuses
        # any name outside the registry's closed catalog and stamps each
        # action's own tier, so nothing here can widen what voice may reach.
        # One card per step, at most three. A signed-in session on the mesh
        # IS him, so his words reach the picker as his request
        # (principal=True); only what arrives from outside is data.
        pids, tail = await _queue_steps(transcript, source="voice",
                                        principal=True)
        proposal_id = pids[0] if pids else None
        answer = f"{answer}\n\n{tail}"
        if proposal_id is None:
            # No proposal to approve, so tell him in prose instead. propose()
            # already notifies on its own when it succeeds -- doing both would
            # be two messages for one request.
            try:
                from ..kernel.comms import Outbound, notify
                from ..kernel.contracts import Severity

                await notify(Outbound(
                    title="You asked me to do something out loud",
                    body=f"You said: {transcript}\n\n{answer}",
                    severity=Severity.NORMAL,
                ))
            except Exception:                           # noqa: BLE001
                log.warning("could not forward the voice request to a channel")

    # A status that names a business brings its tile up beside the answer
    # (15.3); the screen follows once the conversation is dismissed.
    return await _record(transcript, answer, wants_action, started,
                         proposal_id=proposal_id,
                         focus=resolve_focus(transcript, answer))


@router.post("/command")
async def mac_command(request: Request, _s: SignedIn) -> dict:
    """Something said to the Mac from the menu-bar hotkey (2026-10-06).

    Signed in and mesh-only like every /api route. The words are read in
    code (modules/mac/commands) and, when they are Mac commands, run
    through `kernel.spoken.run_spoken_commands` -- Tier 1 Mac actions only,
    each through the executor. `handled: false` means they were not a Mac
    command at all: the menu bar asks /ask instead, and nothing ran here.
    """
    try:
        body = await request.json()
    except ValueError:
        raise HTTPException(400, "not JSON") from None
    if not isinstance(body, dict):
        raise HTTPException(400, "not a JSON object")
    transcript = " ".join(str(body.get("transcript", "")).split())[:MAX_TRANSCRIPT]
    if not transcript:
        raise HTTPException(400, "nothing was said")
    started = time.monotonic()
    # "Open Air Glass": the menu bar opens its own window. Answered here, in
    # code, before the Mac command parser (which would read it as an app
    # called "Air Glass"); nothing runs on the Mac or the server.
    target = view_target(transcript)
    window = MENUBAR_WINDOWS.get(target.get("screen", "")) if target else None
    if window is not None:
        line = "Opening Air Glass."
        turn = await _record(transcript, line, False, started, kind="view")
        return {**turn, "handled": True, "ask": None, "ran": [], "window": window}
    from ..kernel import spoken
    heard = await spoken.run_spoken_commands(transcript)
    if not heard.handled:
        return envelope(handled=False, transcript=transcript)
    turn = await _record(transcript, heard.line, True, started, kind="mac_command")
    return {**turn, "handled": True, "ask": heard.ask,
            "ran": [{"action": r.action, "said": r.said, "ok": r.ok,
                     "detail": r.detail} for r in heard.ran]}


async def _view(target: dict) -> tuple[dict, str]:
    """`(focus, what to say)` for a view command. Reads only."""
    screen = target["screen"]
    if "query" not in target:
        if screen == "/core":
            return {"screen": screen}, "Here I am."
        label = next((name for name, path in SCREENS.items() if path == screen),
                     screen)
        return {"screen": screen}, f"Opening {label}."
    query, kind = target["query"], target.get("kind")
    found = target.get("found") or (
        await find_document(query, kind) if query else None)
    if found is None:
        said = (f"I couldn't find a {kind or 'document'} called “{query}”. "
                "The knowledge base is open so you can look.")
        return {"screen": screen, "open": None}, said
    return ({"screen": screen, "open": {"kind": found["kind"], "id": found["id"],
                                        "title": found["title"]}},
            f"Opening {found['title']}.")


# What is left out when an answer is SAID rather than read: the "More:"
# section a knowledge answer carries for the screen, and a parenthesis that
# names a switch or a file -- "(no GITLAB_TOKEN)", "(bootstrap/x.py)". The
# sentence around it, and so the fact that something is off, is kept.
_MORE = re.compile(r"\n\s*More:.*\Z", re.DOTALL)
_CONFIG_TOKEN = (r"(?:\b[A-Z][A-Z0-9]+_[A-Z0-9_]*"
                 r"|[\w./-]+\.(?:py|sh|json|ya?ml|env|md|ts|tsx)\b)")
_CONFIG_PAREN = re.compile(r"\s*\([^()]*" + _CONFIG_TOKEN + r"[^()]*\)")


_SENTENCE_END = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9“\"])")


def first_sentences(text: str | None, n: int) -> str | None:
    """The first `n` sentences of ATLAS's OWN composed line (never a model's
    answer: cutting one could drop its "I don't know")."""
    if not text:
        return None
    parts = _SENTENCE_END.split(text.strip())
    return " ".join(parts[:n])


def for_speech(text: str | None) -> str | None:
    """The part of a typed answer that is said out loud, or None when that is
    all of it. Only removes; never rewrites, so an "unknown" or a refusal
    reason in the sentence is still said."""
    if not text:
        return None
    said = _MORE.sub("", text)
    said = _CONFIG_PAREN.sub("", said)
    said = re.sub(r"\s+([.,;:!?])", r"\1", said).strip()
    return said if said and said != text.strip() else None


async def _record(transcript: str, answer: str | None, wants_action: bool,
                  started: float, error: str | None = None,
                  proposal_id: int | None = None,
                  intent: str | None = None, *,
                  kind: str | None = None, focus: dict | None = None,
                  check: dict | None = None, spoken: str | None = None,
                  agent: str = "concierge") -> dict:
    """Keep the words, never the audio. Ages out with browsing history.

    `kind` (alias `intent`) says which branch answered: question,
    action_request, view, self, knowledge, ... `focus` is where the
    dashboard should look, when anywhere -- the browser navigates on it; the
    answer text stands on its own without it. `check` is the system check
    the answer was spoken from, so the checklist drawn is the one said.
    `spoken` is what to SAY when it is shorter than what is typed (the
    browser says `answer` when it is null); `agent` is who answered.
    """
    kind = kind or intent or ("action_request" if wants_action else "question")
    ms = int((time.monotonic() - started) * 1000)
    if intent is None:
        intent = "action_request" if wants_action else "question"
    try:
        await execute_sql(
            """INSERT INTO voice_turns (transcript, answer, intent, agent, ms)
               VALUES ($1,$2,$3,$4,$5)""",
            transcript, answer, kind, agent, ms)
    except Exception:                                   # noqa: BLE001
        log.warning("could not record the voice turn")
    said = for_speech(spoken or answer)
    return envelope(transcript=transcript, answer=answer, error=error,
                    action_request=wants_action, proposal=proposal_id, ms=ms,
                    kind=kind, focus=focus, check=check,
                    spoken=said or spoken, agent=agent)


@router.get("/history")
async def history(_s: SignedIn, limit: int = 50) -> dict:
    rows = await fetch_all(
        """SELECT id, at, transcript, answer, intent, ms
           FROM voice_turns ORDER BY at DESC LIMIT $1""",
        max(1, min(200, limit)))
    return envelope(turns=[{
        "id": r["id"], "at": r["at"].isoformat(), "transcript": r["transcript"],
        "answer": r["answer"], "intent": r["intent"], "ms": r["ms"],
    } for r in rows])
