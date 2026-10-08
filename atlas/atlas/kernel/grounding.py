"""A strict check that a model's rephrasing says nothing its facts do not.

Used where a model may only reword a line ATLAS already assembled from the
record (the Agency meeting's polish, `kernel/meeting.py`). The deterministic
line is the source of truth; the rephrase is accepted only when every
checkable thing in it is already in the facts, and it is thrown away --
never repaired -- when anything is not (invariant 6: never guess a number).

What is checked, and why each rule is this strict:

  * every digit run (``3``, ``81%``, ``07:00``, ``2026-09-29``, ``1.5``) must
    appear in the facts as the same run. No exemption for years, ISO dates or
    durations -- unlike `research.report.uncited_numbers`, which serves
    cited prose, not a restatement of a record;
  * every number *word* ("two", "twice", "half", "several", "dozen") must
    appear in the facts as a word: a model that turns "3 runs" into "a few
    runs" has changed the figure;
  * every date word (weekdays, months, "yesterday", "tomorrow", "tonight")
    must appear in the facts;
  * every capitalised token -- a proper name, an acronym, a product -- must
    appear in the facts (case-insensitively), except a small set of
    function words that can open a sentence;
  * status words that make a claim ("failed", "blocked", "fixed", "sent",
    "paid", ...) may not be introduced, and a status the original states
    may not be dropped;
  * every digit run the original states must still be there -- a figure
    left out is a fact lost;
  * a number may not be paired with a unit the facts pair with a different
    number ("3 runs, 24 hours" must not become "3 hours, 24 runs");
  * a URL, an e-mail address or a currency sign not in the facts is refused;
  * the length stays within a bound of the original's, so a rephrase cannot
    smuggle in a paragraph of new sentences.

"No new claims" in general is not decidable by a regex; the status-word and
length rules are its proxy, and the caller keeps the deterministic line
whenever this says no.
"""
from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass, field

# A number as written: digits, optionally joined by . , : / - (a decimal, a
# thousands group, a clock time, a date) and an optional percent sign.
_DIGITS = re.compile(r"\d+(?:[.,:/-]\d+)*%?")

NUMBER_WORDS = re.compile(
    r"\b(zero|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|"
    r"thirteen|fourteen|fifteen|sixteen|seventeen|eighteen|nineteen|twenty|"
    r"thirty|forty|fifty|sixty|seventy|eighty|ninety|hundreds?|thousands?|"
    r"millions?|billions?|dozens?|once|twice|thrice|double|doubled|triple|"
    r"tripled|half|halved|quarter|first|second|third|fourth|fifth|several|"
    r"few|many|multiple|numerous|couple|handful|lots|most|majority|minority|"
    r"single|dozen)\b", re.IGNORECASE)

DATE_WORDS = re.compile(
    r"\b(today|tomorrow|yesterday|tonight|overnight|weekend|morning|afternoon|"
    r"evening|midnight|noon|monday|tuesday|wednesday|thursday|friday|saturday|"
    r"sunday|january|february|march|april|june|july|august|september|"
    r"october|november|december|jan|feb|mar|apr|jun|jul|aug|sep|sept|oct|nov|"
    r"dec|week|weeks|month|months|year|years|daily|weekly|monthly|hourly)\b",
    re.IGNORECASE)

# Stems of words that state an outcome. Introducing one is a new claim;
# dropping one the original had loses the claim it made.
CLAIM_STEMS: tuple[str, ...] = (
    "fail", "block", "broke", "break", "error", "down", "outage", "crash",
    "succe", "fixed", "fixes", "resolv", "complet", "finish", "shipped",
    "sent", "posted", "publish", "approv", "declin", "paid", "pay", "revenue",
    "sold", "sale", "refund", "deploy", "restart", "delet", "remov", "cancel",
    "expir", "limit", "idle", "ready", "never", "no model",
)

_CLAIM = re.compile(r"\b(" + "|".join(re.escape(s) for s in CLAIM_STEMS) + r")",
                    re.IGNORECASE)

# Capitalised words that may open a sentence without being a name.
SENTENCE_WORDS: frozenset[str] = frozenset({
    "a", "an", "the", "i", "i'm", "i've", "i'll", "i'd", "my", "me", "we",
    "our", "you", "your", "it", "its", "it's", "that", "this", "these",
    "those", "there", "here", "and", "but", "so", "or", "yet", "also", "then",
    "still", "nothing", "no", "not", "none", "yes", "okay", "ok", "well",
    "right", "now", "next", "last", "since", "after", "before", "until",
    "while", "as", "on", "in", "at", "for", "from", "with", "without", "to",
    "of", "by", "what", "where", "when", "why", "how", "who", "which", "can",
    "could", "should", "would", "will", "shall", "may", "might", "must", "is",
    "are", "was", "were", "be", "been", "has", "have", "had", "do", "does",
    "did", "let", "let's", "please", "thanks", "thank", "good", "sure",
    "just", "only", "every", "each", "any", "all", "some", "if", "whenever", "because", "though", "although", "plus", "meanwhile",
    "otherwise", "either", "neither", "both", "hi", "hello", "hey", "over",
    "straight", "quick", "briefly", "honestly", "clear", "agreed",
})

# "I" is capitalised wherever it stands, and is never a name.
PRONOUN_I: frozenset[str] = frozenset({"i", "i'm", "i've", "i'll", "i'd"})

_CAPITALISED = re.compile(r"\b[A-Z][A-Za-z0-9'’_.-]*")
_WORD = re.compile(r"[A-Za-z0-9][A-Za-z0-9'’_.-]*")
_URL = re.compile(r"https?://\S+|www\.\S+|\b\S+@\S+\.\w+", re.IGNORECASE)
_CURRENCY = re.compile(r"[$€£¥]")
_SENTENCE_START = re.compile(r"(?:^|[.!?…:;]\s+|—\s*|\n\s*|\"\s*)$")

# The rephrase may run this much longer than the original, plus a little
# slack for a short line ("Next shift in 3 hours." has little room to breathe).
LENGTH_RATIO = 1.3
LENGTH_SLACK = 30


@dataclass(frozen=True, slots=True)
class Verdict:
    ok: bool
    problems: list[str] = field(default_factory=list)


def _digit_runs(text: str) -> list[str]:
    return [m.group(0).rstrip(".,:/-") for m in _DIGITS.finditer(text or "")]


# A numbered list's markers ("1. ", "2) ") at the start of a line are
# structure, not figures.
_LIST_MARKER = re.compile(r"(?m)^\s*\d{1,2}[.)]\s+")


def _variants(run: str) -> set[str]:
    """The spellings a known figure may honestly take in a rewording: the
    run itself, each piece of a compound run ("14:05" of "14:05:12", "2026"
    of "2026-10-06"), its digits without thousands separators, and -- for
    a decimal -- its roundings to fewer places. A model that writes "18
    degrees" of a reading of 18.3 has rounded, not invented."""
    out = {run}
    pieces = re.split(r"[.,:/-]", run.rstrip("%"))
    out.update(p for p in pieces if p)
    if "," in run and re.fullmatch(r"\d{1,3}(?:,\d{3})+(?:\.\d+)?%?", run):
        out.add(run.replace(",", ""))
    bare = run.rstrip("%")
    if re.fullmatch(r"\d+\.\d+", bare):
        value = float(bare)
        places = len(bare.split(".")[1])
        for n in range(places):
            rounded = f"{value:.{n}f}" if n else str(round(value))
            out.add(rounded)
            out.add(rounded + "%")
    return out


def figures(text: str) -> list[str]:
    """The digit runs in `text` that are figures: numbered-list markers
    left out. What `unverified_numbers` checks, exposed for a caller that
    removes the sentences carrying an unverified one."""
    return _digit_runs(_LIST_MARKER.sub("", text or ""))


def unverified_numbers(text: str, facts: Iterable[str]) -> list[str]:
    """The figures in `text` that none of `facts` states -- the check the
    assistant runs on a model's final answer before it leaves (invariant 6:
    never guess a number). `facts` are the question, the tool results and
    the prompt's own clock, as raw text; a run in `text` is verified when
    it is a known run or one of a known run's honest variants. Numbered-list
    markers are ignored. Order of first appearance, no repeats."""
    known: set[str] = set()
    for fact in facts:
        for run in _digit_runs(fact):
            known |= _variants(run)
    seen: list[str] = []
    for run in figures(text):
        # "2,500" in the answer is the "2500" a tool returned.
        bare = (run.replace(",", "") if re.fullmatch(
            r"\d{1,3}(?:,\d{3})+(?:\.\d+)?%?", run) else run)
        if any(r in known or r.rstrip("%") in known for r in {run, bare}):
            continue
        if run not in seen:
            seen.append(run)
    return seen


def _number_units(text: str) -> list[tuple[str, str]]:
    """Digit runs and their immediately following words, where present."""
    pairs: list[tuple[str, str]] = []
    for match in _DIGITS.finditer(text):
        following = re.match(r"\s+([A-Za-z][A-Za-z'-]*)", text[match.end():])
        if following:
            pairs.append((match.group(0).rstrip(".,:/-"), following.group(1).lower()))
    return pairs


def _norm(token: str) -> str:
    return token.strip("'’.-_").lower()


def _words(text: str) -> set[str]:
    return {_norm(w) for w in _WORD.findall(text or "")} - {""}


def _starts_sentence(text: str, index: int) -> bool:
    return bool(_SENTENCE_START.search(text[:index]))


def check(candidate: str, facts: Iterable[str], *, original: str) -> Verdict:
    """Whether `candidate` (a model's rewording of `original`) says only
    what `facts` and `original` already say. `original` counts as a fact:
    it was assembled from the record, so a name or figure in it is grounded.
    """
    text = " ".join((candidate or "").split())
    source = " ".join([original, *facts])
    problems: list[str] = []
    if not text:
        return Verdict(False, ["the rephrase is empty"])

    limit = int(len(original) * LENGTH_RATIO) + LENGTH_SLACK
    if len(text) > limit:
        problems.append(f"it runs {len(text)} characters, over the {limit} allowed")

    known_runs = set(_digit_runs(source))
    for run in _digit_runs(text):
        if run not in known_runs:
            problems.append(f"the number {run!r} is not in the facts")
    kept = set(_digit_runs(text))
    for run in _digit_runs(original):
        if run not in kept:
            problems.append(f"the number {run!r} from the record was dropped")

    source_units: dict[str, set[str]] = {}
    for run, unit in _number_units(source):
        source_units.setdefault(unit, set()).add(run)
    for run, unit in _number_units(text):
        if unit in source_units and run not in source_units[unit]:
            problems.append(f"the number {run!r} was not paired with {unit!r} in the facts")

    known_words = _words(source)
    for pattern, what in ((NUMBER_WORDS, "number word"), (DATE_WORDS, "date word")):
        for m in pattern.finditer(text):
            if _norm(m.group(0)) not in known_words:
                problems.append(f"the {what} {m.group(0)!r} is not in the facts")

    for m in _CAPITALISED.finditer(text):
        token = _norm(m.group(0))
        if not token or token in known_words or token in PRONOUN_I:
            continue
        # "What's", "That's", "Let's": the word before the apostrophe.
        base = re.split(r"['’]", token, maxsplit=1)[0]
        if _starts_sentence(text, m.start()) and (
                token in SENTENCE_WORDS or base in SENTENCE_WORDS):
            continue
        problems.append(f"the name {m.group(0)!r} is not in the facts")

    source_lower, text_lower = source.lower(), text.lower()
    for stem in {m.group(1).lower() for m in _CLAIM.finditer(text)}:
        if not re.search(r"\b" + re.escape(stem), source_lower):
            problems.append(f"it claims {stem!r}, which the record does not")
    for stem in {m.group(1).lower() for m in _CLAIM.finditer(original)}:
        if not re.search(r"\b" + re.escape(stem), text_lower):
            problems.append(f"it drops {stem!r}, which the record states")

    for m in _URL.finditer(text):
        if m.group(0).lower() not in source_lower:
            problems.append(f"the address {m.group(0)!r} is not in the facts")
    if _CURRENCY.search(text) and not _CURRENCY.search(source):
        problems.append("it names a currency the facts do not")
    return Verdict(not problems, problems)
