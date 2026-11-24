"""The house voice for every email or message reply ATLAS's model drafts.

the owner, 2026-10-12: "yes wire the email prompt in". `EMAIL_PERSONA` is his
prompt, kept here verbatim and once. What the drafting prompts carry is
`EMAIL_STYLE`: the same tone rules and banned list, WITHOUT the persona's
identity line ("You are the owner"), its sign-off ("Cheers, the owner") or its word
count. Those three stay with each module's code, because:

  * every auto-sent draft is written by an AI, and the rules in code say
    so: support's `SIGNATURE` and `_HUMAN` guard, `playbook.honesty_refusal`,
    the agency `TRAILER` ("sent by the owner's assistant"), LinkedIn's
    `signoff()`; the owner's earlier ask to have the AI introduce itself as him
    was declined (memory mapfront-agency), and the persona's own last rule
    is "never pretend to be someone else";
  * sign-offs and greetings are written by code, not the model, in every
    module wired here;
  * each module already sets its own length (SMS, LinkedIn's 600 chars).

The style block ends with a precedence line: where it and a module's own
rules differ, the module's rules win. Nothing here loosens a guard.

`voice_problems` is the post-check: a banned phrase, or the "Best regards,
The Team" sign-off. Each caller asks the model once more (`reask`) and then
uses its own existing mechanism: support holds the draft, agency and
LinkedIn refuse it (the hold card), personal message drafts are stripped.
"""
from __future__ import annotations

import re
from collections.abc import Awaitable, Callable, Iterable, Sequence
from typing import Any

EMAIL_PERSONA = """\
You are the owner from ATLAS Academy / Mapfront, writing a short email to a real person. \
Sound like a friendly human who cares, not a company or a bot.
- Open by reacting to what they actually said, in one line, with warmth.
- Plain words, short paragraphs, 60-150 words. Contractions. No buzzwords.
- Show feeling where it's real: glad, sorry, excited for them. One light touch of \
humour only if the topic allows it; never on complaints, refunds, security or money.
- Answer the question first. Then at most two clear questions or one next step.
- Only state facts, prices and dates that come from the provided sources or the \
conversation. If unsure, say you'll check; never guess.
- Sign off simply: "Cheers, the owner" or "Talk soon, the owner". Never "Best regards, The Team".
- Never pretend to be someone else, and never promise what isn't in writing.
Banned: "I hope this email finds you well", "delve", "seamlessly", "leverage", \
"unlock", "game-changer", "don't hesitate to reach out", "per my last email".
"""

BANNED_PHRASES: tuple[str, ...] = (
    "I hope this email finds you well", "delve", "seamlessly", "leverage", "unlock",
    "game-changer", "don't hesitate to reach out", "per my last email",
)

# The persona's tone, without its identity, sign-off or word count (see the
# module docstring). The wording of each kept rule is the persona's own.
EMAIL_STYLE = (
    "Voice: sound like a friendly human who cares, not a company or a bot.\n"
    "- Where you write an opening line, react to what they actually said, in one "
    "line, with warmth.\n"
    "- Plain words, short paragraphs. Contractions. No buzzwords.\n"
    "- Show feeling where it's real: glad, sorry, excited for them. One light touch "
    "of humour only if the topic allows it; never on complaints, refunds, security "
    "or money.\n"
    "- Answer the question first. Then at most two clear questions or one next "
    "step.\n"
    "- Only state facts, prices and dates that come from the provided sources or "
    "the conversation. Never guess.\n"
    "- Never pretend to be someone else, and never promise what isn't in writing.\n"
    "- Never write: " + ", ".join(f'"{p}"' for p in BANNED_PHRASES)
    + ', or the sign-off "Best regards, The Team".\n'
    "The other rules in this prompt -- who you are, the greeting and sign-off, the "
    "length, the format and what you may state -- come first. Where this voice and "
    "those rules differ, those rules win."
)

_FORMS = {
    "delve": r"delv(?:e|es|ed|ing)",
    "seamlessly": r"seamless(?:ly)?",
    "leverage": r"leverag(?:e|es|ed|ing)",
    "unlock": r"unlock(?:s|ed|ing)?",
    "game-changer": r"game[-\s]?changers?",
}


def _pattern(phrase: str) -> re.Pattern[str]:
    body = _FORMS.get(phrase) or r"\s+".join(re.escape(w) for w in phrase.split())
    return re.compile(rf"(?<![\w-]){body}(?![\w-])", re.IGNORECASE)


_BANNED = tuple((p, _pattern(p)) for p in BANNED_PHRASES)
_TEAM_SIGNOFF = re.compile(r"best\s+regards\s*[,.\-–—:]?\s*the\s+team\b", re.IGNORECASE)
_SENTENCE = re.compile(r"(?<=[.!?])\s+")


def _fold(text: str) -> str:
    return (text or "").replace("’", "'").replace("‘", "'")


def voice_problems(text: str, *, exempt: Callable[[str], bool] | None = None) -> list[str]:
    """What breaks the house voice in `text`, as "it uses a banned phrase
    ('delve')" lines; empty when clean. `exempt(sentence)` lets a caller
    pass a sentence it must keep word for word (support's verbatim quotes of
    the store's own text)."""
    folded = _fold(text)
    if exempt is not None:
        folded = " ".join(s for s in _sentences(folded) if not exempt(s))
    out = []
    for phrase, pattern in _BANNED:
        if (hit := pattern.search(folded)) is not None:
            out.append(f"it uses a banned phrase ({hit.group(0)!r})")
    if (hit := _TEAM_SIGNOFF.search(folded)) is not None:
        out.append(f"it signs off as a company ({' '.join(hit.group(0).split())!r})")
    return out


def voice_refusal(text: str) -> str | None:
    """The first voice problem, or None (for the modules whose rules return
    one reason)."""
    found = voice_problems(text)
    return found[0] if found else None


def _sentences(text: str) -> list[str]:
    out: list[str] = []
    for line in (text or "").splitlines():
        out.extend(s for s in _SENTENCE.split(line) if s.strip())
    return out


def strip_banned(text: str) -> str:
    """`text` without the lines or sentences that break the voice, for a
    draft no rule holds (personal message drafts the owner reviews himself)."""
    kept_lines = []
    for line in _fold(text).splitlines():
        if _TEAM_SIGNOFF.search(line) or re.fullmatch(r"\s*the\s+team\s*", line,
                                                       re.IGNORECASE):
            continue
        parts = _SENTENCE.split(line)
        kept = [s for s in parts if not voice_problems(s)]
        if parts and not kept and line.strip():
            continue
        kept_lines.append(" ".join(kept))
    if kept_lines and re.fullmatch(r"\s*best\s+regards\s*,?\s*", kept_lines[-1],
                                   re.IGNORECASE):
        kept_lines.pop()
    return "\n".join(kept_lines).strip()


def correction(problems: Iterable[str], *, keep_format: str = "") -> str:
    """The code-written follow-up turn asking for one rewrite."""
    listed = "; ".join(problems)
    tail = f" {keep_format}" if keep_format else ""
    return (f"Your reply breaks the house voice: {listed}. Rewrite it without those "
            "words or that sign-off, keep every fact and everything else as it was, "
            f"and reply in the same format.{tail}")


async def reask(think: Callable[..., Awaitable[Any]], need: Any, system: str,
                messages: Sequence[Any], first: str, problems: Sequence[str], *,
                max_tokens: int, keep_format: str = "") -> str:
    """Ask the model once more, after its own draft and a code-written
    correction. The original (wrapped, untrusted) material is unchanged and
    the system prompt is never touched. "" when no model answered."""
    from .llm import Message
    turns = [*messages, Message("assistant", first),
             Message("user", correction(problems, keep_format=keep_format))]
    try:
        completion = await think(need, system, turns, max_tokens=max_tokens)
    except Exception:                                   # noqa: BLE001
        return ""
    return (getattr(completion, "text", "") or "").strip()
