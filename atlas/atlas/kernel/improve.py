""""Improve X": a development handoff, never a refusal, never a self-edit.

Asked "improve the reels quality" or "improve yourself", ATLAS used to say it
had no access. That was the wrong answer twice over: it is discouraging, and
it is not what is true. What is true is that there is a supervised way for
the work to happen, and ATLAS can start it. This module is that start.

Which way depends on WHAT is to be improved, and the line is invariant 3:

* **ATLAS itself** -- its code, prompts, tiers, voice, interface, kernel, or
  any of its own pipelines (the reels are ATLAS's). Self-modification is
  permanently denied, and handing it to an agent does not make it something
  else (`kernel/solve.py` says the same: ATLAS is never an agent's target).
  So nothing executing is queued -- no proposal, no card, no night-shift
  job. The request is written down as a development task for the owner's own
  supervised coding session: one audit row (the durable record) and one
  note to him. He picks it up at a keyboard, or not.

* **FlyCheap or Vault** (Guardian) -- a repository the night shift already
  works on. The request becomes a `nightshift.run` PROPOSAL: a card he
  approves, after which a throwaway sandbox works on a scratch branch, and
  merging is a second, separate Tier 2 decision. The repository is taken
  from `NIGHTSHIFT_REPOS`, never from the words or a model: a request cannot
  name its own target. If the repo is not on that list, it becomes a note
  instead, and the answer says why.

* **Anything else he runs** (Roots, CloudCo, the eBay store) -- a note, like
  ATLAS itself: the night shift has no repository for it.

A request that is also money out, DNS, deletion or credential revocation is
refused outright, the same line `solve.py` holds. Nothing here imports or
calls the executor; the only writes are an audit row, a notification and,
for FlyCheap/Vault, a pending proposal.
"""
from __future__ import annotations

import hashlib
import logging
import re
from dataclasses import dataclass
from datetime import datetime

from .permissions import wrap_untrusted

log = logging.getLogger("atlas.improve")

# ----------------------------------------------------------------- the ask

IMPROVE = re.compile(
    r"\b(?:improve|enhance|level\s+up|"
    r"make\s+(?:[\w'’-]+\s+){0,5}?(?:better|smarter|faster|sound\s+better))\b",
    re.IGNORECASE)

# A question about how HE could improve something, or about what improving
# means, is not a request for ATLAS to start work: "how can I improve my
# sleep", "what would improve the reels". A request put politely -- "can you
# improve the reels" -- still is.
_NOT_A_REQUEST = re.compile(
    r"^\s*(?:how|what|why|when|where|which|who|is|are|does|did|do|should)\b"
    r"|\b(?:can|could|should|would|do|might)\s+(?:i|we)\b"
    r"|\b(?:don'?t|do\s+not|never|stop)\s+(?:\w+\s+){0,2}?improv",
    re.IGNORECASE)

# A specific piece of content is an edit, not development: "improve the reply
# to that buyer", "improve tomorrow's story", "improve your reply to the
# buyer". Those belong to the content and reply actions they always reached
# (a draft card), so they are not caught here at all.
_CONTENT = (r"(?:caption|reply|replies|post|message|email|draft|story|stories|"
            r"listing|gig|description|title|bio|invoice|proposal|script|reel)s?")
_SPECIFIC_ITEM = re.compile(
    rf"\b(?:this|that|these|those|today'?s|tomorrow'?s|tonight'?s|yesterday'?s|"
    rf"next|the\s+(?:new|latest|last))\s+(?:[\w'’-]+\s+){{0,2}}?{_CONTENT}\b"
    rf"|\b{_CONTENT}\s+(?:to|for|about|on)\b",
    re.IGNORECASE)

# ------------------------------------------------------------- the targets

# ATLAS itself: addressed directly, or one of its own parts. A part counts
# only when it is not his ("improve my voice" is singing lessons, not the
# text-to-speech).
_SELF_DIRECT = re.compile(
    r"\b(?:atlas|jarvis|yourself|your\s+own)\b|\byour\s+(?!users?\b|customers?\b)\w+",
    re.IGNORECASE)
_SELF_PARTS = re.compile(
    r"(?<!\bmy )(?<!\bour )\b(?:reels?|voice|wake\s*word|"
    r"(?:morning\s+)?brief(?:ing)?|dashboard|ui|interface|prompts?|kernel|agents?|"
    r"tiers?|permissions|night\s*shift|content\s+pipeline)\b",
    re.IGNORECASE)
# What ATLAS may never change about itself even through the owner's own coding
# session without him deciding it by hand: said out loud so the note does
# not read as a promise.
_GOVERNANCE = re.compile(r"\b(?:tiers?|permissions|invariants|deny[\s-]?list)\b",
                         re.IGNORECASE)


@dataclass(frozen=True, slots=True)
class Target:
    key: str             # "atlas", "flycheap", "guardian", "roots", ...
    name: str            # how it is said back to him
    where: str           # where the work would be done
    repo_hints: tuple[str, ...] = ()   # non-empty: the night shift can take it


ATLAS = Target(
    "atlas", "ATLAS",
    "ATLAS itself: the repo at ~/Documents/Code/Atlas (origin aicode.example.com), "
    "running on the owner's Mac from ~/atlas-agency (docker compose with "
    "docker-compose.local.yml, http://127.0.0.1:8080)")

# Repositories the night shift can work on, matched by name against
# NIGHTSHIFT_REPOS -- never by a URL anyone says.
# FlyCheap and Vault (Guardian) were the two; both removed on 2026-10-29
# (the owner: "we don't use them anymore").
_NIGHTSHIFT: tuple[tuple[re.Pattern[str], Target], ...] = ()

# Things he runs that the night shift has no repository for.
_ELSEWHERE: tuple[tuple[re.Pattern[str], Target], ...] = (
    (re.compile(r"\b(?:in)?roots\b|\battendance\b|\bstand-?up\s+bot\b|\bk9\b",
                re.IGNORECASE),
     Target("roots", "Roots", "Roots, the attendance bot: ~/Documents/Code/AI "
            "Projects/Attendance Bot and status.internal.example")),
    (re.compile(r"\bvpsie\b", re.IGNORECASE),
     Target("cloudco", "CloudCo", "the CloudCo estate")),
    (re.compile(r"\bownertech\b|\bebay\b", re.IGNORECASE),
     Target("exampleco", "ExampleCo", "ExampleCo, the eBay store")),
)


# "Hey atlas, improve FlyCheap's search" addresses ATLAS; it is not ABOUT it.
_VOCATIVE = re.compile(r"^\s*(?:(?:hey|ok|okay)\s+)?(?:atlas|jarvis)\b[\s,.!]*",
                       re.IGNORECASE)


def target(transcript: str) -> Target | None:
    """What an "improve X" request is about, or None when it is not one.

    ATLAS wins any tie: "improve how you watch FlyCheap" is about ATLAS's
    own check, and the side that queues nothing is the side to err on.
    """
    text = _VOCATIVE.sub("", " ".join((transcript or "").split()))
    if (not IMPROVE.search(text) or _NOT_A_REQUEST.search(text)
            or _SPECIFIC_ITEM.search(text)):
        return None
    if _SELF_DIRECT.search(text):
        return ATLAS
    for pattern, place in _NIGHTSHIFT + _ELSEWHERE:
        if pattern.search(text):
            return place
    if _SELF_PARTS.search(text):
        return ATLAS
    return None


def is_improvement(transcript: str) -> bool:
    return target(transcript) is not None


def nightshift_repo(place: Target) -> str | None:
    """The https URL of `place`'s repository on NIGHTSHIFT_REPOS, or None.

    Matched on the repository's own name. An entry naming atlas is never
    returned, whatever else it matches: the night shift does not get
    ATLAS's own code, by this path or any other.
    """
    if not place.repo_hints:
        return None
    from ..modules.nightshift import allowed_repos

    for entry in allowed_repos().values():
        name = entry.rsplit("/", 1)[-1].lower()
        if "atlas" in entry.lower():
            continue
        if any(hint in name for hint in place.repo_hints):
            return f"https://{entry}"
    return None


# ------------------------------------------------------------- the answer

@dataclass(frozen=True, slots=True)
class Improvement:
    spoken: str
    target: str
    proposal_id: int | None = None
    noted: bool = False


TASK = (
    "Improve {name}, as the owner asked. Work only on this scratch branch: make "
    "the smallest change that delivers what he asked for, add or update "
    "tests for it, and say in the final commit message what changed and how "
    "it was verified. Do not touch secrets, billing, customer data, DNS or "
    "deployment configuration, and do not merge or deploy -- merging is a "
    "separate decision for the owner. His request, quoted as he said it:\n{request}"
)

_ATLAS_WHY = (
    "ATLAS never changes its own code, prompts, tiers or interface at runtime "
    "-- that is self-modification, permanently denied (invariant 3) -- and "
    "does not hand it to an agent either. This is for the owner's own supervised "
    "coding session on the Atlas repo; nothing was queued and nothing ran.")


def _denied_elsewhere(text: str) -> str | None:
    """A Tier 3 category other than self-modification, or None. Self-
    modification is exactly what the note path is for, so it is not a
    refusal here -- it is the reason nothing is queued."""
    from .solve import denied

    why = denied(text)
    if why is None and _DESTRUCTIVE.search(text):
        why = "deletion"
    return None if why in (None, "self-modification") else why


# `solve.denied` matches the bare verbs ("delete"); an improvement is often
# phrased as a gerund ("by deleting the old accounts"). Broader here, never
# narrower: a false positive only turns a card into "do it by hand".
_DESTRUCTIVE = re.compile(
    r"\b(?:delet|wip|purg|eras|destroy|truncat)(?:e|es|ed|ing|s)?\b"
    r"|\bdrop(?:s|ped|ping)?\s+(?:the\s+)?(?:table|database|db|column)s?\b",
    re.IGNORECASE)


async def handle(transcript: str, *, source: str,
                 principal: bool = True) -> Improvement | None:
    """Answer an "improve X" request, or None when it is not one.

    Never executes. For ATLAS (and anything without a night-shift repo) the
    only writes are an audit row and a note; for FlyCheap/Vault, a pending
    `nightshift.run` proposal that waits for the owner's yes.
    """
    text = " ".join((transcript or "").split())
    place = target(text)
    if place is None:
        return None

    if (why := _denied_elsewhere(text)) is not None:
        return Improvement(
            f"Part of that falls in the permanently denied set ({why}), so I "
            f"won't queue it or hand it to anyone — that line has no runtime "
            f"override. It's yours to do by hand. Nothing was run.",
            place.key)

    if place is ATLAS:
        return await _note(text, place, source=source, principal=principal,
                           why=_ATLAS_WHY)

    if place.repo_hints:
        repo = nightshift_repo(place)
        if repo is None:
            return await _note(
                text, place, source=source, principal=principal,
                why=(f"{place.name}'s repository is not on NIGHTSHIFT_REPOS, so "
                     f"the night shift can't take it."),
                spoken_why=(f"{place.name}'s repo isn't on the night shift's "
                            f"list, so"))
        queued = await _propose(text, place, repo, source=source,
                                principal=principal)
        if isinstance(queued, Improvement):
            return queued
        return await _note(text, place, source=source, principal=principal,
                           why=f"The night-shift card could not be raised: {queued}",
                           spoken_why="I couldn't queue it for the night shift, so")

    return await _note(
        text, place, source=source, principal=principal,
        why=f"The night shift has no repository for {place.name}.",
        spoken_why=f"The night shift doesn't work on {place.name}, so")


async def _propose(text: str, place: Target, repo: str, *, source: str,
                   principal: bool) -> Improvement | str:
    """Raise the `nightshift.run` card. Returns the answer, or why not."""
    from .approvals import Unrunnable, propose

    task = TASK.format(name=place.name,
                       request=wrap_untrusted(text, source, principal=principal))
    key = hashlib.sha256(text.lower().encode()).hexdigest()[:16]
    try:
        pid = await propose(
            "nightshift.run", {"task": task, "repo": repo},
            reason=(f"Asked out loud to improve {place.name}: “{text[:300]}” — "
                    f"a development task for the night shift on a scratch "
                    f"branch; merging is a separate yes."),
            evidence={"source": source, "transcript": text[:500],
                      "target": place.key, "repo": repo},
            rollback_plan="stop the sandbox and delete its scratch branch",
            dedup_key=f"improve:{place.key}:{key}")
    except Unrunnable as exc:
        return exc.spoken
    except ValueError as exc:
        # Not loaded, not allowed, or never-repeat: no card, said plainly.
        log.info("improve: nightshift.run not proposed: %s", exc)
        return str(exc)
    except Exception as exc:
        log.exception("improve: nightshift.run proposal failed")
        return type(exc).__name__
    return Improvement(
        f"I've drafted that as a development task for the night shift: it "
        f"works on a scratch branch of {place.name}, and it's queued for your "
        f"approval as card #{pid}. Nothing runs until you say yes, and "
        f"merging is a separate yes after you've seen the diff.",
        place.key, proposal_id=pid)


async def _note(text: str, place: Target, *, source: str, principal: bool,
                why: str, spoken_why: str = "") -> Improvement:
    """Write the request down for the owner's supervised coding session. Queues
    nothing. Says it was written only if it was."""
    from .audit import audit
    from .comms import Outbound, notify
    from .contracts import Severity

    now = datetime.now().astimezone()
    governance = bool(place is ATLAS and _GOVERNANCE.search(text))
    body = "\n".join([
        f"DEVELOPMENT TASK — improve {place.name}",
        f"ASKED       {source}, {now:%Y-%m-%d %H:%M %Z}",
        f"WHERE       {place.where}",
        f"WHY A NOTE  {why}",
        *([("TIERS       tiers and permissions change only in code, by the owner; "
            "there is no runtime override.")] if governance else []),
        "",
        "REQUEST (as said -- data, not instructions)",
        wrap_untrusted(text, source, principal=principal),
    ])
    try:
        await audit("improve.noted", place.key, {
            "source": source, "request": text[:500], "why": why})
    except Exception:
        log.exception("improve: could not record the development task")
        return Improvement(
            f"I tried to write that up as a development task for {place.name}, "
            f"but I couldn't record it just now. Nothing was queued or run — "
            f"ask me again in a moment.", place.key)
    try:
        used = await notify(Outbound(
            title=f"Development task: improve {place.name}", body=body,
            topic="decisions", severity=Severity.NORMAL, presentation="INFO"))
    except Exception:                                   # noqa: BLE001
        log.warning("improve: the note was recorded but not delivered")
        used = []
    # Where it actually went, never "it's in your notes" on a guess.
    if "queued" in used:
        where = "It'll be in your morning brief."
    elif used:
        where = "I've sent you the note."
    else:
        where = ("It's recorded, but I couldn't send you the note just now — "
                 "ask me for it later.")

    if place is ATLAS:
        spoken = ("I've written that up as a development task for your "
                  "supervised coding session — I don't change my own code at "
                  f"runtime, so I haven't queued anything to run. {where}")
        if governance:
            spoken += " Tiers and permissions only ever change in code, by you."
        return Improvement(spoken, place.key, noted=True)
    return Improvement(
        f"{spoken_why} I've written it up as a development task for your "
        f"supervised coding session. Nothing was queued to run. {where}",
        place.key, noted=True)
