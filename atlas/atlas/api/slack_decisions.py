"""What a typed Slack message decides, if anything. Deterministic, no model.

Only an approver's message reaches here (`slack_events.events` drops every
other sender), and even then a message is a decision only when its own words
plainly say so. The model never decides an approval: it may talk about one,
never make one.

What went wrong before (2026-09-27, found in the Slack review):

* "what happens if I approve?" and "don't approve 180" are under six words
  and contain "approve", so both approved the proposal they named.
* Any number was a proposal id, so "do it in 5 minutes" targeted #5, and
  "approve 180 181" silently approved 180 alone.
* Quoted text counted: pasting someone else's "> approve 180" approved it.
* "thanks" and "seen" went to the model as conversation, and "ack" had no
  typed form at all.

So a message is classified, in this order, into one of:

    question  -- asks something; answered, never executed
    hold      -- a negated approval ("don't approve 180"); nothing changes
    ack       -- "ack", "seen", "thanks": recorded, never an approval
    approve / decline -- short and explicit
    None      -- not a decision; the request/conversation path takes it

A bare "yes"/"ok"/"no" is WEAK: it decides only the card it replies to (a
thread under an ATLAS card) or a proposal named by id. A strong verb ("approve",
"do it", "decline") may also take the single pending proposal when there is
exactly one. Nothing ever picks the newest of several.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

# A verdict is a short sentence. "block 30 minutes at 12:00 to approve the
# drafts" contains "approve" and is a request, not a verdict.
VERDICT_MAX_WORDS = 6

# Slack delivers message text HTML-escaped: a quoted line starts with "&gt;".
_QUOTED = re.compile(r"(?m)^[ \t]*(?:&gt;|>).*$")
_FENCED = re.compile(r"```.*?```", re.DOTALL)
_INLINE_CODE = re.compile(r"`[^`\n]*`")
_MENTION = re.compile(r"<[@#!][^>]*>")

_QUESTION_START = re.compile(
    r"^\s*(what|how|why|when|where|which|who|should|shall|can|could|would|"
    r"will|is|are|does|do i|did|if|what'?s)\b", re.IGNORECASE)

_NEGATED_APPROVE = re.compile(
    r"\b(don'?t|do not|never|not|no need to|shouldn'?t|won'?t|wouldn'?t)\s+"
    r"(approve|do it|go ahead|run it|post it|send it|publish it|ship it)\b",
    re.IGNORECASE)

_STRONG_APPROVE = re.compile(
    r"\b(approve|approved|do it|go ahead|run it|send it|post it|publish it|"
    r"ship it|solve it|fix it|fix this|fix that|make it happen|handle it|"
    r"let'?s do it|confirm|confirmed)\b", re.IGNORECASE)
# Weak words decide only as the WHOLE message (an id or "please" allowed):
# "no worries", "ok cool" and "yes, but first..." are not decisions.
_WEAK_TAIL = r"[\s.!,]*(?:please[\s.!]*)?(?:#?\d{1,7}[\s.!]*)?$"
_WEAK_APPROVE = re.compile(
    r"^\s*(yes|yep|yeah|yup|ok|okay|sure|sounds good)" + _WEAK_TAIL, re.IGNORECASE)

_STRONG_DECLINE = re.compile(
    r"\b(decline|declined|deny|denied|reject|rejected|don'?t do|do not do|"
    r"leave it|leave that|skip it|skip that|skip this|cancel that|cancel it|"
    r"not now)\b", re.IGNORECASE)
_WEAK_DECLINE = re.compile(r"^\s*(no|nope|nah)" + _WEAK_TAIL, re.IGNORECASE)

_ACK = re.compile(
    r"^\s*(ack|acked|acknowledged?|seen|noted|got it|thanks|thank you|thx|ty|"
    r"cheers|roger|ok,? thanks|okay,? thanks)[\s.!]*$", re.IGNORECASE)

_DEFERRED = re.compile(
    r"\b(in \d+\s*\w+|in an? (hour|minute|bit|while)|later|tomorrow|tonight|"
    r"next week|at \d{1,2}(:\d\d)?\s*(am|pm)?|after )", re.IGNORECASE)

# An id said as an id: "#180", "proposal 180", "no. 180".
_EXPLICIT_ID = re.compile(r"(?:#|\bproposal\s*#?|\bno\.\s*)(\d{1,7})\b",
                          re.IGNORECASE)
# A bare number, unless a unit says it is a quantity ("in 5 minutes").
_BARE_NUMBER = re.compile(
    r"(?<![\w#.])(\d{1,7})\b(?!\s*(?:%|s\b|sec|secs|second|seconds|m\b|min|"
    r"mins|minute|minutes|h\b|hr|hrs|hour|hours|d\b|day|days|week|weeks|am\b|"
    r"pm\b|gb|mb|tb|k\b|x\b|:\d))", re.IGNORECASE)


@dataclass(frozen=True)
class Decision:
    kind: str                     # question | hold | ack | approve | decline
    strong: bool = False          # a verb, not a bare yes/no
    ids: tuple[int, ...] = field(default_factory=tuple)
    # Nothing but the verb ("do it", "approve please"): only then may it
    # take the single pending proposal. "approve the linkedin drafts" names
    # something, and must not approve an unrelated card that happens to be
    # the only one waiting.
    bare: bool = False


def own_words(text: str) -> str:
    """The sender's own words: no quoted lines, no code, no mentions."""
    text = _FENCED.sub(" ", text or "")
    text = _QUOTED.sub(" ", text)
    text = _INLINE_CODE.sub(" ", text)
    text = _MENTION.sub(" ", text)
    return " ".join(text.split())


def without_quotes(text: str) -> str:
    """The message minus the lines he quoted (someone else's words). Code
    stays: a pasted log is part of what he is asking about."""
    return "\n".join(line for line in _QUOTED.sub("", text or "").splitlines()
                     if line.strip()).strip()


def proposal_ids(words: str, *, explicit_only: bool = False) -> tuple[int, ...]:
    """Ids named in the sender's own words. Explicit forms win; a bare
    number counts only when no explicit form is present, and never in a
    question ("what happened with node 7?" is not about proposal #7)."""
    explicit = [int(m) for m in _EXPLICIT_ID.findall(words)]
    if explicit or explicit_only:
        return tuple(dict.fromkeys(explicit))
    bare = [int(m) for m in _BARE_NUMBER.findall(words)]
    return tuple(dict.fromkeys(bare))


# Words that may accompany a verb without naming anything.
_FILLER = re.compile(
    r"\b(please|pls|now|it|this|that|one|ok|okay|yes|yeah|sure|thanks|go|"
    r"ahead|do|then|atlas|#?\d{1,7}|proposal|no\.)\b", re.IGNORECASE)


def _only_the_verb(words: str, verb: re.Pattern[str]) -> bool:
    rest = verb.sub(" ", words)
    rest = _FILLER.sub(" ", rest)
    return not re.sub(r"[\s.,!#]+", "", rest)


def classify(text: str) -> Decision | None:
    words = own_words(text)
    if not words:
        return None
    ids = proposal_ids(words)
    if words.rstrip().endswith("?") or _QUESTION_START.search(words):
        return Decision("question", ids=proposal_ids(words, explicit_only=True))
    if _NEGATED_APPROVE.search(words):
        return Decision("hold", ids=ids)
    if _ACK.match(words):
        return Decision("ack", ids=ids)
    if len(words.split()) > VERDICT_MAX_WORDS or _DEFERRED.search(words):
        # "do it in 5 minutes" is not an approval now; a deferred ask goes
        # to the request path, which can say what it can and cannot do.
        return None
    if _STRONG_DECLINE.search(words):
        return Decision("decline", strong=True, ids=ids,
                        bare=_only_the_verb(words, _STRONG_DECLINE))
    if _STRONG_APPROVE.search(words):
        return Decision("approve", strong=True, ids=ids,
                        bare=_only_the_verb(words, _STRONG_APPROVE))
    if _WEAK_DECLINE.match(words):
        return Decision("decline", strong=False, ids=ids)
    if _WEAK_APPROVE.match(words):
        return Decision("approve", strong=False, ids=ids)
    return None


_CARD_BUTTON = re.compile(r"^(?:approve|always|decline|detail):(\d{1,7})$")


def card_id(msg: dict) -> int | None:
    """The proposal a card is about, from its own buttons ("approve:180").
    ATLAS writes those values; they do not change when the header wording
    does (the header became "[Reels] REVIEW: #180 — …" and a text-prefix
    match stopped finding the card)."""
    for block in (msg or {}).get("blocks") or []:
        for el in block.get("elements") or []:
            m = _CARD_BUTTON.match(str(el.get("value") or ""))
            if m:
                return int(m.group(1))
    return None
