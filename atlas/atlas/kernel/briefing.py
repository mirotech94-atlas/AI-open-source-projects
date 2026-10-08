"""The first ten seconds of a call ATLAS places: who is calling, what is
wrong in plain words, and one yes-or-no question.

Miro, 2026-10-04 (the voice-partner brief): a call that opens by reading a
check's key and its collector output for forty seconds is a robot reading
logs. He is often half asleep when it rings. So the opening is three beats
and nothing else:

  1. grounding      "Hey Miro, it's ATLAS."  (at night: "Sorry to wake you.")
  2. the incident   one or two short sentences, in words a person would say
  3. the question   only when a card is pending: the action, "yes or no?"

Everything here is deterministic and built before the phone rings
(`channels.twilio`), so nothing waits on a model when he answers. It only
re-words what the alert already says: no figure, name or cause is added
(invariant 6), an UNKNOWN stays unknown, and what cannot be said plainly is
left for Slack rather than guessed at. ATLAS says it is ATLAS.

Not here: the decision. The keypad decides on a call ATLAS placed
(`api/phone.py`); this module only words the asking.
"""
from __future__ import annotations

import os
import re
from datetime import datetime, time

INCIDENT_WORDS = 26          # beat two, at most
QUESTION_WORDS = 18          # the action inside the question, at most
GREETING = "Hey Miro, it's ATLAS."
GREETING_NIGHT = "Hey Miro, it's ATLAS. Sorry to wake you."
MORE_IN_SLACK = "The details are in Slack."
KEYPAD_HINT = "Press 1 for yes, 2 for no."
NIGHT_DEFAULT = (time(23, 0), time(7, 0))

# How an estate is said aloud. Vault is the name Miro uses for Guardian.
SPOKEN = {
    "flycheap": "FlyCheap", "guardian": "Vault", "vault": "Vault", "vpsie": "VPSie",
    "stripe_billing": "Stripe billing", "matrix_rooms": "the chat rooms",
    "fiverr": "Fiverr", "comms": "my own messaging", "screening": "screening",
    "academy": "the Academy store", "agency": "Mapfront", "mirotech": "Mirotech",
    "inroots": "Roots", "instagram": "Instagram", "youtube": "YouTube",
    "tiktok": "TikTok", "content": "the reels", "clips": "the clips lane",
    "mail": "mail", "calendar": "the calendar", "backups": "backups",
    "markets": "markets", "netbird": "the mesh", "gitlab": "GitLab",
}
_HEALTH = {"failing": "is failing", "degraded": "is degraded", "unknown": "can't be read"}
_PAGE = re.compile(r"^\s*([a-z0-9_]+)\.([a-z0-9_.]+)\s+is\s+([a-z]+)\s*$", re.IGNORECASE)
_CARD = re.compile(r"^\s*#\d+\s*[—-]\s*")
_SENTENCE = re.compile(r"(?<=[.!?])\s+")
_CODE = re.compile(r"`[^`]*`")
_MACHINE_LINE = re.compile(r"\bTier \d\b|\brollback:", re.IGNORECASE)
_NOISE = re.compile(r"[#*_~|<>\[\]{}\\]")
_HEX = re.compile(r"\b[0-9a-f]{12,}\b", re.IGNORECASE)
_PERCENT = re.compile(r"(\d+(?:\.\d+)?)\s*%")
# Things a collector prints and nobody says: an exception's class, in or out
# of brackets, and a bare address. Dropped, never replaced with a guess.
_EXCEPTION = re.compile(r"\(?\b[\w.]*(?:Error|Exception|Errno)\b[\w.:]*\)?")
_ADDRESS = re.compile(r"(?:\b(?:at|on|to|from)\s+)?\b\d{1,3}(?:\.\d{1,3}){3}(?::\d+)?\b")


def night(now: datetime | None = None) -> bool:
    """Is it his night? `QUIET_HOURS` ("23:00-07:00") when set, since that
    is the window he chose; otherwise 23:00 to 07:00 on the server's clock."""
    start, end = NIGHT_DEFAULT
    window = os.getenv("QUIET_HOURS", "")
    if "-" in window:
        try:
            a, b = window.split("-", 1)
            start, end = time.fromisoformat(a.strip()), time.fromisoformat(b.strip())
        except ValueError:
            start, end = NIGHT_DEFAULT
    t = (now or datetime.now().astimezone()).time()
    return (start <= t or t < end) if start > end else (start <= t < end)


def shape(text: str) -> str:
    """Words for the ear: no code, ids, markup or symbols a voice trips on."""
    text = _CODE.sub(" ", text or "")
    text = _HEX.sub(" ", text)
    text = _EXCEPTION.sub(" ", text)
    text = _ADDRESS.sub(" ", text)
    text = _PERCENT.sub(r"\1 percent", text)
    text = text.replace("&", " and ").replace("/", " ").replace("—", ", ")
    text = _NOISE.sub(" ", text)
    text = " ".join(text.split())
    text = re.sub(r"\s+([.,;:!?])", r"\1", text)      # no space before a stop
    return text.strip(" ,;:-")


def _cap(text: str, words: int) -> str:
    parts = text.split()
    if len(parts) <= words:
        return text
    return " ".join(parts[:words]).rstrip(",;:") + "."


def _first_sentences(text: str, words: int) -> str:
    """Whole sentences up to `words`; a first sentence longer than that is
    cut at the word limit rather than dropped."""
    out: list[str] = []
    used = 0
    for s in _SENTENCE.split(text):
        s = s.strip()
        if not s:
            continue
        n = len(s.split())
        if out and used + n > words:
            break
        out.append(s if s[-1] in ".!?" else s + ".")
        used += n
        if used >= words:
            break
    return _cap(" ".join(out), words)


def _detail(body: str) -> str:
    """What the alert's body says, minus the lines only a card needs."""
    lines = [ln for ln in (body or "").splitlines()
             if ln.strip() and not _MACHINE_LINE.search(ln)]
    return shape(" ".join(lines))


def incident(title: str, body: str = "") -> str:
    """Beat two. A page titled "flycheap.database is failing" becomes
    "FlyCheap's database check is failing."; a card says its reason."""
    title = " ".join((title or "").split())
    detail = _detail(body)
    page = _PAGE.match(title)
    if page:
        module, check, health = page.group(1).lower(), page.group(2), page.group(3).lower()
        name = SPOKEN.get(module, module.replace("_", " "))
        what = check.replace("_", " ").replace(".", " ")
        state = _HEALTH.get(health, f"is {health}")
        head = (f"The {what} check on {name} {state}." if name.startswith(("the ", "my "))
                else f"{name}'s {what} check {state}.")
        rest = _first_sentences(detail, INCIDENT_WORDS - len(head.split())) if detail else ""
        return f"{head} {rest}".strip()
    if _CARD.match(title):
        # A card: the reason is the incident; the action is the question.
        said = _first_sentences(detail, INCIDENT_WORDS) if detail else ""
        return said or MORE_IN_SLACK
    head = shape(title)
    head = head if not head or head[-1] in ".!?" else head + "."
    left = INCIDENT_WORDS - len(head.split())
    rest = _first_sentences(detail, left) if detail and left >= 4 else ""
    return _cap(f"{head} {rest}".strip(), INCIDENT_WORDS) or MORE_IN_SLACK


def action_of(title: str) -> str:
    """The action a card's title names: "#12 — Restart the instance" ->
    "Restart the instance". Empty for anything that is not a card."""
    title = " ".join((title or "").split())
    if not _CARD.match(title):
        return ""
    return shape(_CARD.sub("", title))


def question(action: str) -> str:
    """Beat three: the one decision, as a yes-or-no question. The action is
    the catalog's own summary ("Restart the FlyCheap worker"); nothing is
    added to it."""
    action = _cap(shape(action), QUESTION_WORDS).rstrip(".")
    if not action:
        return "Should I go ahead, yes or no?"
    return f"Want me to {action[0].lower()}{action[1:]}, yes or no?"


def opening(title: str, body: str = "", *, at_night: bool | None = None) -> str:
    """Beats one and two, ready to be spoken. Beat three is `question()`,
    added by the phone line only when a card is pending on the call."""
    asleep = night() if at_night is None else at_night
    return f"{GREETING_NIGHT if asleep else GREETING} {incident(title, body)}".strip()
