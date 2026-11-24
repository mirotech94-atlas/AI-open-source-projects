"""Words said to the Mac, read in code: no model, nothing run here.

Miro, 2026-10-06, from a reel of a voice assistant that opens apps
mid-sentence: "open Notes, once you're there create a new note, make the
title say hello"; "open the browser and google something"; "open x.com".
`plan()` turns one utterance into an ordered list of catalog actions with
their parameters, or into one question, or into nothing. The kernel
(kernel.spoken.run_spoken_commands) runs the list through the executor.

What it understands, clause by clause:

  open / launch X                  mac.open_app (X an app name)
  open / go to / visit <site>      mac.open_url (x.com, "x dot com", a URL,
                                   or a name in SITE_ALIASES)
  close / quit X                   mac.quit_app
  search / google / look up <q>    mac.web_search ("... on youtube" picks
                                   the engine)
  new note <title>, create a note  mac.new_note
  called / titled / named <title>
  make the title say <t>,          the title of the note created earlier in
  title it / call it <t>           the same utterance -- never an edit of
                                   a note already on the screen
  open the browser                 dropped before a search or a site (the
                                   page opens in his default browser);
                                   alone, it opens google.com

Clauses are cut at commas, "and", "then", "once you're there" and the
like, but only where the words after the cut start a command: "search for
salt and pepper" and "a note called bread and butter" stay one clause.
"No wait", "never mind", "scratch that" cancel the clause before them.

Never a guess. A pronoun ("close it"), a missing object ("search"), a
title with no note to put it on, an app name that is two apps: the plan
is one question and nothing runs. A clause this file cannot read is
returned as such; the kernel may ask the model to pick it from the Mac
catalog, and only from that.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from . import (
    _app_problem,
    _quit_problem,
    note_problem,
    search_problem,
    url_problem,
)

MAX_STEPS = 6

# ------------------------------------------------------------------ results


@dataclass(frozen=True, slots=True)
class Step:
    action: str
    params: dict[str, Any]
    said: str                       # the clause, as he said it


@dataclass(frozen=True, slots=True)
class Unread:
    """A clause the parser cannot read. Only the model may fill it, from the
    Mac catalog, and the kernel re-checks what it picks."""

    said: str


@dataclass(frozen=True, slots=True)
class Plan:
    items: tuple[Step | Unread, ...] = ()
    ask: str | None = None          # one question; when set, nothing runs
    cancelled: tuple[str, ...] = field(default=())

    @property
    def steps(self) -> tuple[Step, ...]:
        return tuple(i for i in self.items if isinstance(i, Step))

    @property
    def unread(self) -> tuple[Unread, ...]:
        return tuple(i for i in self.items if isinstance(i, Unread))

    @property
    def handled(self) -> bool:
        """Whether this is a Mac command at all: something parsed, or a
        question was asked, or what was said was cancelled. Nothing parsed
        and nothing cancelled is a question for /ask, not a Mac command."""
        return bool(self.steps or self.ask or self.cancelled)


# ------------------------------------------------------------------ words

_VOCATIVE = re.compile(
    r"^\s*(?:(?:hey|ok|okay)\s+)?(?:atlas|jarvis)\b[\s,.!]*", re.IGNORECASE)
_POLITE = re.compile(
    r"^(?:(?:please|can\s+you|could\s+you|would\s+you|will\s+you|just|now|also|"
    r"go\s+ahead\s+and|and|then|so|ok(?:ay)?|right)\b[\s,]*)+", re.IGNORECASE)
# "once you're there", "when it's open", "after that": the clause after it
# is the next step. Dropped from the front of a clause.
_ONCE = (r"(?:(?:once|when|after|now\s+that)\s+(?:you(?:'re|\s+are|re)|it(?:'s|\s+is|s)|"
         r"that(?:'s|\s+is)|you've|you\s+have)\s+(?:there|in(?:\s+there)?|open(?:ed)?|"
         r"up|done|loaded|in\s+it|got\s+it\s+open)|after\s+that|and\s+then|then)")
_LEAD = re.compile(rf"^(?:{_ONCE}\b[\s,]*)+", re.IGNORECASE)
_TAIL = re.compile(r"[\s.,!?;:…]+$")

_VERB = (r"(?:open|launch|start|fire\s+up|bring\s+up|close|quit|exit|shut\s+down|"
         r"go\s+to|go\s+on|visit|navigate\s+to|head\s+to|take\s+me\s+to|"
         r"search|google|look\s+up|create|make|add|take|write|new\s+note|"
         r"title\s+it|call\s+it|name\s+it|set\s+the\s+title|change\s+the\s+title|"
         # Not read here, but a new step all the same: the model may pick
         # it from the Mac catalog ("open Notes and play some lofi").
         r"play|pause|skip|set|turn|mute|unmute|remind|notify|text|message|send|"
         r"run)\b")
# No `^`: used with `.match(text, pos)`, where `^` would only ever match at 0.
VERB_START = re.compile(rf"(?:{_ONCE}\b[\s,]*)*{_VERB}", re.IGNORECASE)
_TITLE_PHRASE = (r"(?:make|set|change)\s+(?:the\s+|its\s+)?title\b|"
                 r"(?:title|call|name)\s+it\b")

# Where one clause may end and the next begin. A bare space is a break only
# before "once you're there" and "make the title ...": phrases that are never
# part of an app's name or a search ("search for open source" stays whole).
_BREAK = re.compile(
    rf"\s*(?:[,;…]|\.{{2,}}|\.(?=\s))\s*(?:and\s+)?(?:then\s+)?"
    rf"|\s+and\s+(?:then\s+)?|\s+then\s+|\s+(?={_ONCE}\b)|\s+(?={_TITLE_PHRASE})",
    re.IGNORECASE)

# Said instead of the clause before it.
_CANCEL = re.compile(
    r"[\s,.…]*\b(?:no+\s*,?\s*wait|wait\s*,?\s*no|never\s*mind|nevermind|"
    r"scratch\s+that|cancel\s+that|forget\s+(?:that|it)|hold\s+on,?\s+no)\b[\s,.!…]*",
    re.IGNORECASE)

_PRONOUN = re.compile(r"^(?:it|that|this|them|those|these|one|the\s+app|"
                      r"that\s+app|this\s+app|everything|all(?:\s+of\s+them)?)$",
                      re.IGNORECASE)

APP_ALIASES = {
    "chrome": "Google Chrome", "google chrome": "Google Chrome",
    "vs code": "Visual Studio Code", "vscode": "Visual Studio Code",
    "visual studio code": "Visual Studio Code", "code": "Visual Studio Code",
    "word": "Microsoft Word", "excel": "Microsoft Excel",
    "powerpoint": "Microsoft PowerPoint", "outlook": "Microsoft Outlook",
    "teams": "Microsoft Teams", "youtube music": "YouTube Music",
    "app store": "App Store", "messages": "Messages", "imessage": "Messages",
    "facetime": "FaceTime", "whatsapp": "WhatsApp", "chatgpt": "ChatGPT",
    "notes": "Notes", "safari": "Safari", "mail": "Mail", "music": "Music",
    "photos": "Photos", "calendar": "Calendar", "reminders": "Reminders",
    "slack": "Slack", "spotify": "Spotify", "finder": "Finder",
}
# A site said by name, after a site verb ("go to", "visit") or alone after
# "open" when it is not also an app's name. A fixed table, not a guess.
SITE_ALIASES = {
    "x": "x.com", "twitter": "x.com", "youtube": "www.youtube.com",
    "gmail": "mail.google.com", "google": "www.google.com",
    "github": "github.com", "gitlab": "gitlab.com", "reddit": "www.reddit.com",
    "instagram": "www.instagram.com", "tiktok": "www.tiktok.com",
    "linkedin": "www.linkedin.com", "facebook": "www.facebook.com",
    "netflix": "www.netflix.com", "amazon": "www.amazon.com",
    "atlas": "http://127.0.0.1:8080", "flycheap": "flycheap.example.com",
    "fly cheap": "flycheap.example.com", "hacker news": "news.ycombinator.com",
}
# ATLAS runs on this Mac (atlas.storefront.test was deleted 2026-10-27), so its alias
# carries its own scheme; every other alias is an https host.


def _alias_url(name: str) -> str:
    target = SITE_ALIASES[name]
    return target if "://" in target else "https://" + target


_BROWSER = re.compile(r"^(?:the\s+|my\s+)?(?:web\s+)?(?:browser|internet)$",
                      re.IGNORECASE)
BROWSER_HOME = "https://www.google.com"

_DOMAIN = re.compile(
    r"^(?:https?://)?(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,24}"
    r"(?::\d{1,5})?(?:/\S*)?$", re.IGNORECASE)
_SPOKEN_DOT = re.compile(r"\s+dot\s+", re.IGNORECASE)
_SPOKEN_DOMAIN = re.compile(r"^[\w-]+(?:\s+dot\s+[\w-]+)+(?:/\S*)?$", re.IGNORECASE)


def _clean(text: str) -> str:
    return _TAIL.sub("", " ".join(text.split()))


def _object(text: str) -> str:
    """The thing named after a verb: articles and "app" trimmed."""
    text = _clean(text)
    text = re.sub(r"^up\s+", "", text, flags=re.IGNORECASE)
    text = re.sub(r"^(?:the|my|a)\s+", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\s+(?:app|application)$", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\s+(?:for\s+me|please|now|again)$", "", text, flags=re.IGNORECASE)
    return text.strip()


def _app_name(said: str) -> str:
    name = APP_ALIASES.get(said.lower())
    if name:
        return name
    return said.title() if said.islower() else said


def as_url(said: str) -> str | None:
    """An address from what was said: "x.com", "X dot com", a full URL."""
    text = _clean(said)
    if _SPOKEN_DOMAIN.match(text):
        text = _SPOKEN_DOT.sub(".", text)
    if not _DOMAIN.match(text):
        return None
    if not re.match(r"^https?://", text, re.IGNORECASE):
        text = "https://" + text
    scheme, rest = text.split("://", 1)
    host, slash, path = rest.partition("/")
    return f"{scheme.lower()}://{host.lower()}{slash}{path}"


# ------------------------------------------------------------------ clauses


def clauses(utterance: str) -> tuple[list[str], list[str]]:
    """(the clauses to act on, the clauses cancelled), in the order said."""
    text = _VOCATIVE.sub("", utterance or "").strip()
    pieces = _CANCEL.split(text)
    kept: list[str] = []
    cancelled: list[str] = []
    for n, piece in enumerate(pieces):
        parts = _split(piece)
        if n > 0 and parts and cancelled:
            # "open Safari, no wait, Chrome": the verb carries over.
            verb = VERB_START.match(_strip(cancelled[-1]))
            if verb and not VERB_START.match(_strip(parts[0])):
                parts[0] = f"{verb.group(0)} {parts[0]}"
        if n < len(pieces) - 1:
            # The clause said just before "no wait" is withdrawn; if this
            # piece had none, the one before it is. A bare "never mind" is
            # still a withdrawal: recorded as "", so nothing reads it as a
            # question for the model.
            if parts:
                cancelled.append(parts.pop())
            elif kept:
                cancelled.append(kept.pop())
            else:
                cancelled.append("")
        kept.extend(parts)
    return kept, cancelled


def _strip(clause: str) -> str:
    clause = _POLITE.sub("", clause.strip())
    clause = _LEAD.sub("", clause)
    return _POLITE.sub("", clause).strip()


def _split(text: str) -> list[str]:
    text = text.strip()
    if not text:
        return []
    cuts = [m for m in _BREAK.finditer(text) if VERB_START.match(text, m.end())]
    out: list[str] = []
    start = 0
    for cut in cuts:
        out.append(text[start:cut.start()])
        start = cut.end()
    out.append(text[start:])
    return [c for c in (_clean(_strip(c)) for c in out) if c]


# ------------------------------------------------------------------ one clause

_TITLE_EDIT = re.compile(
    r"^(?:(?:make|set|change|update)\s+(?:the\s+|its\s+)?(?:note'?s?\s+)?title\s+"
    r"(?:say|says|to\s+say|to|be|read|as)\b|"
    r"(?:title|call|name)\s+it|give\s+it\s+the\s+title)\s*:?\s*(?P<t>.*)$",
    re.IGNORECASE)
_NOTE = re.compile(
    r"^(?:(?:create|make|add|start|write|take|open|new)\s+)?(?:a\s+|an\s+)?"
    r"(?:new\s+|quick\s+)?note\b(?:\s+(?:in\s+notes|for\s+me))?"
    r"(?:\s*(?P<how>called|titled|entitled|named|with\s+the\s+title|that\s+says|"
    r"saying|which\s+says|about|reading)\s*:?)?\s*(?P<rest>.*)$",
    re.IGNORECASE)
_NOTE_VERB = re.compile(r"^(?:create|make|add|start|write|take|new|open)\b|^(?:a|an)\s",
                        re.IGNORECASE)
_QUIT = re.compile(r"^(?:close|quit|exit|shut\s+down|kill)\s+(?P<x>.*)$", re.IGNORECASE)
_SITE_VERB = re.compile(
    r"^(?:go\s+to|go\s+on|visit|navigate\s+to|head\s+to|take\s+me\s+to)\b\s*(?P<x>.*)$",
    re.IGNORECASE)
_OPEN = re.compile(r"^(?:open|launch|start|fire\s+up|bring\s+up)\b\s*(?P<x>.*)$",
                   re.IGNORECASE)
_SEARCH = re.compile(
    r"^(?:search|google|look\s+up)(?:[\s-]+search)?\b"
    r"(?:\s+(?:on\s+)?(?P<e1>google|youtube|duckduckgo|bing)\b)?"
    r"(?:\s+(?:the\s+)?(?:web|internet))?(?:\s+for)?\s*(?P<q>.*?)"
    r"(?:\s+(?:on|in|using)\s+(?P<e2>google|youtube|duckduckgo|bing))?$",
    re.IGNORECASE)


@dataclass(frozen=True, slots=True)
class _TitleEdit:
    title: str
    said: str


@dataclass(frozen=True, slots=True)
class _Ask:
    question: str


@dataclass(frozen=True, slots=True)
class _Browser:
    said: str


Parsed = Step | Unread | _TitleEdit | _Ask | _Browser


def parse_clause(clause: str) -> Parsed:
    """One clause to one thing. Pure: no model, no I/O."""
    said = clause
    text = _clean(clause)

    if (m := _TITLE_EDIT.match(text)) is not None:
        title = _clean(m.group("t")).strip("\"'“”")
        if not title:
            return _Ask("What should the title say?")
        return _TitleEdit(title, said)

    if (m := _NOTE.match(text)) is not None and (
            _NOTE_VERB.match(text) or m.group("how") or text.lower().startswith("note")):
        rest = _clean(m.group("rest")).strip("\"'“”")
        how = (m.group("how") or "").lower()
        if how in ("that says", "saying", "which says", "reading"):
            return Step("mac.new_note", {"title": "", "body": rest}, said)
        return Step("mac.new_note", {"title": rest, "body": ""}, said)

    if (m := _QUIT.match(text)) is not None:
        target = _object(m.group("x"))
        if not target or _PRONOUN.match(target):
            return _Ask("Which app should I close?")
        if (two := _two(target, "close")) is not None:
            return two
        return Step("mac.quit_app", {"app": _app_name(target)}, said)

    if (m := _SEARCH.match(text)) is not None:
        query = _clean(m.group("q")).strip("\"'“”")
        engine = (m.group("e2") or m.group("e1") or "google").lower()
        if not query:
            return _Ask("Search for what?")
        return Step("mac.web_search", {"query": query, "engine": engine}, said)

    if (m := _SITE_VERB.match(text)) is not None:
        target = _object(m.group("x"))
        if not target or _PRONOUN.match(target):
            return _Ask("Which site? Say its address, like x.com.")
        url = as_url(target) or (
            _alias_url(target.lower()) if target.lower() in SITE_ALIASES else None)
        if url is None:
            return _Ask(f"Which address is {target}? Say it like {target.lower()}.com.")
        return Step("mac.open_url", {"url": url}, said)

    if (m := _OPEN.match(text)) is not None:
        target = _object(m.group("x"))
        if not target or _PRONOUN.match(target):
            return _Ask("Open what?")
        if _BROWSER.match(target):
            return _Browser(said)
        if (url := as_url(target)) is not None:
            return Step("mac.open_url", {"url": url}, said)
        lowered = target.lower()
        if lowered in SITE_ALIASES and lowered not in APP_ALIASES:
            return Step("mac.open_url", {"url": _alias_url(lowered)}, said)
        if (two := _two(target, "open")) is not None:
            return two
        return Step("mac.open_app", {"app": _app_name(target)}, said)

    return Unread(said)


def _two(target: str, verb: str) -> _Ask | None:
    """"Notes or Safari" is a choice he has not made; "Notes and Safari" may
    be two apps or one with "and" in its name. Either way: ask, once."""
    m = re.match(r"^(?P<a>.+?)\s+(?P<how>or|and|&)\s+(?P<b>.+)$", target, re.IGNORECASE)
    if m is None:
        return None
    a, b = _app_name(m.group("a")), _app_name(m.group("b"))
    if m.group("how").lower() == "or":
        return _Ask(f"{verb.capitalize()} which one: {a} or {b}?")
    return _Ask(f"Two apps? Say \"{verb} {a}, then {verb} {b}\".")


# ------------------------------------------------------------------ the plan

def problem(action: str, params: dict[str, Any]) -> str | None:
    """The same pure checks the actions make, asked before anything runs, so
    a chain is refused whole rather than half run. The executor checks again."""
    if action == "mac.open_app":
        return _app_problem(str(params.get("app", "")))
    if action == "mac.quit_app":
        return _quit_problem(str(params.get("app", "")))
    if action == "mac.open_url":
        return url_problem(params.get("url"))
    if action == "mac.web_search":
        return search_problem(params.get("query"), params.get("engine", "google"))
    if action == "mac.new_note":
        return note_problem(params.get("title", ""), params.get("body", ""))
    return None


def plan(utterance: str) -> Plan:
    """The whole utterance, resolved before anything runs."""
    kept, cancelled = clauses(utterance)
    parsed = [parse_clause(c) for c in kept]

    for p in parsed:
        if isinstance(p, _Ask):
            return Plan(ask=p.question, cancelled=tuple(cancelled))

    items: list[Step | Unread] = []
    for n, p in enumerate(parsed):
        if isinstance(p, _TitleEdit):
            note = next((i for i in range(len(items) - 1, -1, -1)
                         if isinstance(items[i], Step)
                         and items[i].action == "mac.new_note"), None)
            if note is None:
                # Never a note already on the screen: ATLAS does not edit them.
                return Plan(ask=("Which note? Say \"create a note called "
                                 f"{p.title}\" and I'll make it."),
                        cancelled=tuple(cancelled))
            old = items[note]
            assert isinstance(old, Step)
            items[note] = Step(old.action, {**old.params, "title": p.title},
                               f"{old.said}, {p.said}")
            continue
        if isinstance(p, _Browser):
            later = parsed[n + 1:]
            if any(isinstance(q, Step) and q.action in ("mac.open_url", "mac.web_search")
                   for q in later):
                continue            # the page opens in his default browser
            items.append(Step("mac.open_url", {"url": BROWSER_HOME}, p.said))
            continue
        assert isinstance(p, Step | Unread)
        items.append(p)

    for item in items:
        if isinstance(item, Step) and item.action == "mac.new_note" and not (
                item.params.get("title", "").strip() or item.params.get("body", "").strip()):
            return Plan(ask="What should the note be called?",
                        cancelled=tuple(cancelled))
    return Plan(items=tuple(items), cancelled=tuple(cancelled))


def describe(step: Step) -> str:
    """A step in a few words, for the one line that says what will run."""
    p = step.params
    return {
        "mac.open_app": lambda: f"open {p.get('app')}",
        "mac.quit_app": lambda: f"quit {p.get('app')}",
        "mac.open_url": lambda: f"open {p.get('url')}",
        "mac.web_search": lambda: f"search {p.get('engine', 'google')} for "
                                  f"{p.get('query')!r}",
        "mac.new_note": lambda: ("create the note "
                                 f"{(p.get('title') or p.get('body') or '')[:40]!r}"),
    }.get(step.action, lambda: step.action)()
