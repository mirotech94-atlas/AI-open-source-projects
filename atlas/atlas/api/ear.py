"""What is SAID of an answer, as opposed to what is typed.

Miro, 2026-11-10: every spoken answer is a short summary in the NotebookLM
host's way -- the answer first, one to three short sentences, plain warm
words, no list read aloud, no id spelled out, no module-name pile, no
"please note the source". The screen keeps all the detail.

This only ever REMOVES or collapses. It never invents a word of its own about
a fact, so a "that source is unreachable" or an "unknown" in the typed answer
is still in the spoken one (invariants 6 and 7): a sentence that carries one
is the last thing dropped.

Pure text in, text out -- no I/O, nothing here can reach the executor.
"""
from __future__ import annotations

import re

MAX_SENTENCES = 3
MAX_WORDS = 45

# The "More:" section a knowledge answer carries for the screen, and a
# parenthesis that names a switch or a file -- "(no GITLAB_TOKEN)".
_MORE = re.compile(r"\n\s*More:.*\Z", re.DOTALL)
_CONFIG_TOKEN = (r"(?:\b[A-Z][A-Z0-9]+_[A-Z0-9_]*"
                 r"|[\w./-]+\.(?:py|sh|json|ya?ml|env|md|ts|tsx)\b)")
_CONFIG_PAREN = re.compile(r"\s*\([^()]*" + _CONFIG_TOKEN + r"[^()]*\)")
_READ_PAREN = re.compile(r"\s*\((?:read|as of)\s+([^()]*)\)", re.IGNORECASE)

_SENTENCE_END = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9“\"'])")
_BULLET = re.compile(r"^\s*(?:[-*•–]|\d{1,2}[.)])\s+")
_FENCE = re.compile(r"```.*?```", re.DOTALL)
_MD_LINK = re.compile(r"\[([^\]]+)\]\([^)]*\)")
_URL = re.compile(r"https?://\S+|\bwww\.\S+")
_MD_MARK = re.compile(r"[*_]{2,}|`+|^#{1,6}\s*", re.MULTILINE)
_HEX_ID = re.compile(r"\b(?:[0-9a-fA-F]{8}-(?:[0-9a-fA-F]{4}-){3}[0-9a-fA-F]{12}"
                     r"|[0-9a-fA-F]{12,})\b")
_LONG_TOKEN = re.compile(r"\b[\w./:+=-]{25,}\b")
_PATH = re.compile(r"(?<!\w)(?:~|\.{0,2})/[\w.-]+(?:/[\w.-]+)+")
# guardian.cert -> guardian; "example.com" and "e.g." are left alone.
_CHECK_KEY = re.compile(r"\b([a-z][a-z0-9_]{2,})\.([a-z][a-z0-9_]{2,})\b")
_NOT_A_KEY = frozenset({"com", "org", "net", "io", "dev", "app", "ai", "co", "uk",
                        "html", "txt", "pdf", "png", "jpg", "csv"})
_SNAKE = r"[a-z][a-z0-9]*(?:_[a-z0-9]+)+"

# Sentences that are filler, not facts -- unless they carry an honesty word.
_BOILERPLATE = re.compile(
    r"^\s*(?:please note|note that|note:|note,|source:|sources:|as an ai|"
    r"i hope this helps|hope this helps|let me know if|feel free to|"
    r"for more (?:detail|information))\b", re.IGNORECASE)
# What must survive a cut: that something is unknown, unreachable or refused.
_HONEST = re.compile(
    r"\b(?:unknown|unreachable|can'?t|cannot|couldn'?t|could not|unable|don'?t know|"
    r"not sure|not measured|never read|offline|failing|failed|refus\w*|never do)\b",
    re.IGNORECASE)


def _is_honest(sentence: str) -> bool:
    return _HONEST.search(sentence) is not None


def _name_run(names: frozenset[str]) -> re.Pattern[str]:
    """A run of three or more names -- module names (known, or snake_case) -- in
    a row, joined by commas and 'and'."""
    known = "|".join(sorted((re.escape(n) for n in names), key=len, reverse=True))
    one = rf"(?:{_SNAKE}" + (rf"|\b(?:{known})\b" if known else "") + r")"
    return re.compile(rf"{one}(?:\s*(?:,|;|&|\band\b|,\s*and\b)\s*{one}){{2,}}",
                      re.IGNORECASE)


def _clean(text: str, names: frozenset[str]) -> str:
    text = _MORE.sub("", text)
    text = _FENCE.sub(" ", text)
    text = _MD_LINK.sub(r"\1", text)
    text = _URL.sub("", text)
    text = _PATH.sub("", text)
    text = _CONFIG_PAREN.sub("", text)
    text = _READ_PAREN.sub(r" as of \1", text)   # the read time stays
    text = _MD_MARK.sub("", text)
    text = _HEX_ID.sub("that ID", text)
    text = _LONG_TOKEN.sub("that ID", text)
    text = _CHECK_KEY.sub(
        lambda m: m.group(0) if m.group(2) in _NOT_A_KEY else m.group(1), text)
    text = _name_run(names).sub("several modules", text)
    return text


def _split_lines(text: str) -> tuple[str, int]:
    """Prose lines joined, and how many list items were dropped."""
    prose: list[str] = []
    items = 0
    for line in text.splitlines():
        if not line.strip():
            continue
        if _BULLET.match(line):
            items += 1
        else:
            prose.append(line.strip())
    return " ".join(prose), items


def _cut(sentence: str, words: int) -> str:
    parts = sentence.split()
    if len(parts) <= words:
        return sentence
    head = " ".join(parts[:words])
    comma = head.rfind(",")
    if comma > len(head) // 2:
        head = head[:comma]
    return head.rstrip(" ,;:") + "."


def for_ear(text: str | None, *, max_sentences: int = MAX_SENTENCES,
            max_words: int = MAX_WORDS,
            names: frozenset[str] = frozenset()) -> str | None:
    """The short, spoken version of `text`, or None when there is nothing to
    say. Short plain text comes back unchanged (bar its spacing)."""
    if not text or not text.strip():
        return None
    # Bullets are found before the markdown marks are stripped, so "* item"
    # is still a list item.
    prose, items = _split_lines(_FENCE.sub(" ", _MORE.sub("", text)))
    prose = _clean(prose, names)
    prose = re.sub(r"\s+([.,;:!?])", r"\1", re.sub(r"\s+", " ", prose)).strip()
    if not prose:
        if items:
            return f"There are {items} items; they're on your screen."
        return None

    sentences = [s.strip() for s in _SENTENCE_END.split(prose) if s.strip()]
    kept = [s for s in sentences
            if not (_BOILERPLATE.match(s) and not _is_honest(s))]
    if not kept:
        return None

    chosen: list[str] = []
    used = 0
    for sentence in kept:
        n = len(sentence.split())
        if chosen and (len(chosen) >= max_sentences or used + n > max_words):
            break
        chosen.append(sentence)
        used += n
    # Honesty outranks brevity: an "unknown" / "unreachable" in a sentence that
    # was cut is not lost -- it takes the last place, whole, and the rest
    # makes room for it (never the other way round).
    dropped = kept[len(chosen):]
    why: str | None = None
    if not any(_is_honest(s) for s in chosen):
        why = next((s for s in dropped if _is_honest(s)), None)
    if why is not None:
        budget = max_words - len(why.split())
        while chosen and (len(chosen) >= max_sentences
                          or len(" ".join(chosen).split()) > budget):
            if len(chosen) == 1:
                chosen = [_cut(chosen[0], budget)] if budget > 0 else []
                break
            chosen.pop()
        said = " ".join([*chosen, why])
    else:
        said = " ".join(chosen)
        if len(said.split()) > max_words:
            cut = _cut(said, max_words)
            # A lone honest sentence is said whole rather than lose its "why".
            said = cut if _is_honest(cut) or not _is_honest(said) else said
    return _keep_read_time(said, prose) or None


# "as of 7:52", "read 7:52", "read at 7:52 pm" -- when a figure was read.
_READ_TIME = re.compile(
    r"\b(?:as of|read(?: at)?)\s+(\d{1,2}:\d{2}(?:\s?[ap]\.?m\.?)?)", re.IGNORECASE)


def _keep_read_time(said: str, source: str) -> str:
    """Invariant 6: a figure is never said without the time it was read. When
    the cut kept a number but dropped its "as of 7:52", the source's read time
    is said after it -- copied, never invented. No read time in the source,
    nothing is added (the typed answer had none to give)."""
    if not said or _READ_TIME.search(said):
        return said
    if not re.search(r"\d", _READ_TIME.sub("", said)):
        return said
    stamp = _READ_TIME.search(source)
    if stamp is None:
        return said
    return f"{said.rstrip()} As of {stamp.group(1)}."
