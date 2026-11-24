"""When nothing in the catalog does what the owner asked: find a way.

Until 2026-09-28 the answer to "can you optimize my cpu for the attendance
bot server" was a chat reply asking him for his own `top` output -- ATLAS had
no action for it, so it had nothing to offer but the offer to file an issue.
The work still has somewhere to go: the specialists can measure, and the
coding agent on his Mac (CLAUDE_CODE_AGENT.md) can change things ATLAS may
not. This module is the path between them:

1. **Denied stays denied.** Money out, DNS, deletion, credential revocation
   and self-modification -- ATLAS itself is never a target -- are refused
   before anything is investigated or drafted, with no card: a Tier 3 thing
   handed to an agent is still a Tier 3 thing done (invariant 3).
2. **Measure first.** The request goes to the specialist roster
   (`agents.route`), which reads live state with its own read-only tools and
   says what it could not measure. Nothing it says is a number unless a
   tool read it (invariant 6).
3. **Write the brief, don't run it.** `handoff.delegate` renders the task --
   the request verbatim, the findings quoted as untrusted, the target, what
   it may and may not touch, and what "done" looks like -- and it is stored
   as a draft. The card carrying it is Tier 2 and `card_only`; approving it
   queues it, and the agent claims it on its next scheduled run.

Nothing here executes anything. The only write is the draft row and the card.
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import re
from dataclasses import dataclass
from datetime import UTC, datetime

from .contracts import Tier
from .handoff import delegate, surface
from .permissions import wrap_untrusted

log = logging.getLogger("atlas.solve")

INVESTIGATE_TIMEOUT_S = 120

# ATLAS's own code and host. Handing a change to itself through an agent is
# self-modification, whoever holds the shell (invariant 3).
# ATLAS runs only on the owner's Mac since 2026-10-28, from ~/atlas-agency
# (docker compose -f docker-compose.yml -f docker-compose.local.yml).
ATLAS_PATHS = ("~/Documents/Code/Atlas and every worktree of it",
               ("~/atlas-agency, the checkout ATLAS runs from, its data/ and its "
                "docker compose stack (docker-compose.yml + docker-compose.local.yml)"),
               ("atlas.example.com (the old server, retired) and any host that serves "
                "ATLAS again"))


@dataclass(frozen=True, slots=True)
class Estate:
    key: str
    pattern: re.Pattern[str]
    topic: str
    target: str
    may_touch: tuple[str, ...]
    may_not_touch: tuple[str, ...] = ()
    # The ATLAS module its readings live under. Told to the investigator:
    # asked about "the attendance bot server", it looked up a module called
    # "attendance-bot", found none, and reported Roots as unmonitored
    # (2026-09-28) -- ATLAS knows it as `inroots`.
    module: str = ""


# The approved card is his yes for what the brief asks -- a live fix
# included, when the request was for one -- and for nothing else. The
# agent runs unattended, so anything past the brief comes back as
# "needs_owner" rather than waiting for a yes nobody is there to give.
_NO_PROD = ("any production change the request did not ask for; a live change it "
            "did ask for only per that project's own deploy rules, reversibly, "
            "and verified afterwards")

ESTATES: tuple[Estate, ...] = (
    Estate("roots", re.compile(r"\b(?:in)?roots\b|\battend\w*|\bstand-?up\b"
                               r"|\bk9\b|\bpto\b", re.IGNORECASE),
           "inroots",
           "Roots, the attendance/standup bot: the repo at ~/Documents/Code/AI "
           "Projects/Attendance Bot and its server behind status.internal.example",
           ("the Attendance Bot repo, on a branch",
            ("the Roots server over SSH from this Mac: read anything; change it only "
             "as the owner's request asks, through the project's own deploy rules")),
           ("the attendance data in its database",
            "any message to the owner's colleagues", _NO_PROD), "inroots"),
    # the owner, 2026-09-28: "atals should be able to access my cloudco account and
    # exectue commands based on waht i tell him". His servers, over SSH from
    # the Mac the runner sits on -- never ATLAS's own stack (ATLAS_PATHS),
    # never anything that bills, and deletion stays Tier 3 (`denied`).
    Estate("cloudco", re.compile(
        r"\bvpsie\b|\bvps\b|\bmy servers?\b|\bthe servers?\b|\bssh\b"
        r"|\bdroplet\b|\bvm\b", re.IGNORECASE),
           "cloudco",
           "the owner's servers in his CloudCo account (for example the Roots VPS at "
           "203.0.113.125), over SSH from this Mac with the keys already on it",
           (("the servers his request names, over SSH: run the commands he asked "
             "for, and read whatever is needed to run them safely"),),
           (("creating, resizing, deleting or snapshotting servers, volumes or IPs, "
             "or anything else that is billed"),
            "the CloudCo account's billing, payment methods and API credentials",
            "firewall or SSH settings that could lock this Mac out", _NO_PROD),
           "cloudco"),
    # the owner, 2026-09-28: "give it access to open apps and things like that".
    # His laptop, through the handoff runner on it -- the same card, the
    # same brief, the same limits. Last, so a named project wins.
    Estate("mac", re.compile(
        r"\bon (?:my|the) (?:mac|laptop|computer|macbook)\b"
        r"|\b(?:open|launch|quit)\s+(?:up\s+)?(?:the\s+)?"
        r"(?:[A-Z][\w.]*(?:\s+[A-Z][\w.]*)?|\w+\s+app)\b"
        r"|\breminders?\b|\bremind me\b|\bshortcuts?\b|\bnotes app\b"
        r"|\bcalendar app\b|\bfinder\b|\bsafari\b|\byoutube music\b"
        # Music is YouTube Music, the Chrome app on his Mac (the owner,
        # 2026-09-28: "connect youtube music instead of spotify").
        r"|\bplay\b.{0,40}\b(?:music|songs?|playlist|album|radio|mix)\b"
        r"|\bmusic app\b|\bmy desktop\b|\bmy downloads\b"),
           "decisions",
           "this Mac (the owner's laptop): its apps, Reminders, Notes, the Calendar "
           "app, Shortcuts, and files he names -- through the handoff runner "
           "on it, as the owner, with macOS asking him before any app it has not "
           "used before",
           ("open, switch to or quit the apps he named",
            "YouTube Music (his music player): search and press play, pause or skip",
            "a reminder, note or calendar entry exactly as he asked",
            "a macOS Shortcut he named, by its exact name",
            "reading files or folders he named"),
           ("passwords, the keychain and anything in System Settings",
            "deleting, moving or overwriting files",
            "sending a message, email or post, or accepting an invite",
            "any purchase or payment",
            "~/Documents/Code/Atlas, ~/atlas-agency and anything of ATLAS's own")),
    Estate("exampleco", re.compile(r"\bownertech\b|\bebay\b", re.IGNORECASE),
           "exampleco", "ExampleCo, the eBay store",
           ("read-only inspection of the store",),
           ("listings, prices and orders", "any message to a buyer"), "exampleco"),
)

UNNAMED = Estate(
    "unnamed", re.compile(r"(?!)"), "decisions",
    "not named in the request: establish which system he means and ask the owner "
    "before touching anything",
    ("read-only inspection until the system is confirmed",))

# The permanently denied set, as it shows up in a request. Deliberately
# about the ACT, not a topic: "why did the refund webhook fail" is a
# question about a refund, not a refund.
_TIER3 = (
    # Every verb form ("deleting", "wiped", "paying", "rotated"): the bare
    # base form missed "can you look into deleting the old backups"
    # (2026-09-28, found by the improve.py review).
    ("money out", re.compile(
        r"\b(?:pay|send|sent|transfer|wire|wiring|withdraw|refund)\w*\b.{0,40}"
        r"(?:\$|€|£|\d|money|invoice|bill|fund|payment|them|him|her)", re.IGNORECASE)),
    ("DNS", re.compile(r"\bdns\b|\bnameservers?\b|\b(?:a|aaaa|cname|mx|txt)\s+records?\b",
                       re.IGNORECASE)),
    ("deletion", re.compile(
        r"\b(?:delet|wip(?:e|ed|es|ing)\b|purg|eras(?:e|ed|es|ing)\b|destroy"
        r"|truncat)\w*"
        r"|\brm\s+-\w*r"
        r"|\bdrop(?:s|ped|ping)?\s+(?:the\s+)?(?:table|database|db|schema)\b"
        r"|\bremov\w*\b.{0,25}\b(?:files?|data|backups?|records?|rows?|users?"
        r"|accounts?|repos?|repositor\w+|branch\w*|buckets?|volumes?|snapshots?"
        r"|instances?|servers?|vms?|posts?|emails?|messages?)\b", re.IGNORECASE)),
    ("credential revocation", re.compile(
        r"\b(?:revok|rotat|reset)\w*\b.{0,30}\b(?:keys?|tokens?|credentials?"
        r"|passwords?|secrets?|certificates?)\b", re.IGNORECASE)),
    ("self-modification", re.compile(
        r"\b(?:atlas|your(?:self| own)?)\b.{0,40}\b(?:code|tiers?|rules|permissions"
        r"|invariants|config|prompt|repo)\b"
        r"|\b(?:chang|edit|modif|rewrit|updat|patch)\w*\s+(?:atlas|yourself)\b",
        re.IGNORECASE)),
)

INVESTIGATE = (
    "the owner asked for the request below, and no action in ATLAS's catalog does "
    "it. You cannot change anything. Investigate with your read-only tools: "
    "take the live readings that bear on it, each with the time it was read; "
    "name the likely cause; say plainly what you could not measure and why. "
    "Then say what an engineer with shell access to that system should do, "
    "step by step. Never state a number a tool did not read. No greeting, no "
    "sign-off. End with exactly one line:\n"
    "DONE WHEN: <one observable state that shows it is done>"
)

_DONE = re.compile(r"^\s*\**DONE WHEN\**\s*:\s*(.+?)\s*$", re.IGNORECASE | re.MULTILINE)
DEFAULT_DONE = ("the change the owner asked for is in place and verified on the live "
                "system, or the report says exactly why it could not be done")


def denied(request: str) -> str | None:
    """The Tier 3 category a request falls in, or None."""
    for why, pattern in _TIER3:
        if pattern.search(request):
            return why
    return None


# "open Safari and go to flycheap.example" is a job for the laptop, not the
# FlyCheap repo: a request that STARTS with an app verb is the Mac's,
# whatever project it then names.
_APP_OBJECT = (r"(?:up\s+)?(?:the\s+)?(?:[A-Z][\w.]*(?:\s+[A-Z][\w.]*)?"
               r"(?!\s+(?:repo|server|scraper|site|page|bot|service|api|db|database)\b)"
               r"|[\w.]+(?:\s+[\w.]+)?\s+app\b)")
_MAC_FIRST = re.compile(
    r"^\s*(?:(?:hey\s+|ok\s+)?(?:atlas|jarvis)\b[\s,]*)?"
    r"(?:(?:please|can you|could you|would you|will you|just)\b[\s,]*)*"
    r"(?:(?:open|launch|quit)\s+" + _APP_OBJECT +
    r"|play\b|pause\b|remind me\b|add a reminder\b"
    r"|run (?:my |the )?\w[\w ]{0,30}shortcut\b)")


def estate(request: str) -> Estate:
    if _MAC_FIRST.search(request):
        return next(e for e in ESTATES if e.key == "mac")
    for e in ESTATES:
        if e.pattern.search(request):
            return e
    return UNNAMED


def done_when(findings: str) -> str:
    found = _DONE.findall(findings or "")
    return found[-1].strip() if found else ""


def _summary(findings: str, limit: int = 900) -> str:
    text = _DONE.sub("", findings or "").strip()
    if len(text) <= limit:
        return text
    cut = text[:limit].rsplit(" ", 1)[0]
    return cut + " …"


async def _investigate(request: str, module: str = "") -> tuple[str, str]:
    """(findings, where they came from). A failure is reported, not hidden:
    the brief says ATLAS could not measure, and the agent measures."""
    from .agents import route

    where = (f"\n\nThis concerns ATLAS's `{module}` module: use exactly that name "
             f"with module_status, module_brief and read_metrics." if module else "")
    try:
        text = await asyncio.wait_for(
            route(INVESTIGATE + where + "\n\n" + wrap_untrusted(request, "slack",
                                                               principal=True)),
            timeout=INVESTIGATE_TIMEOUT_S)
        text = (text or "").strip()
        # A run that ended without an answer, or stopped at its tool limit,
        # is not findings (#204's brief quoted "(calling tools)" as its
        # evidence).
        from .harness import NO_ANSWER, TOOL_TURN
        if text and text not in (NO_ANSWER, TOOL_TURN) and not text.startswith(
                "Stopped after the tool-call limit"):
            return text, "the ATLAS specialists' read-only investigation"
        return ("ATLAS's investigation returned nothing; no reading was taken.",
                "none")
    except Exception as exc:                            # noqa: BLE001
        log.warning("solve: investigation failed (%s)", type(exc).__name__)
        why = (f"ATLAS could not investigate ({type(exc).__name__}); no reading "
               f"was taken, so measure before changing anything.")
        return why, "none"


async def solve(request: str, *, who: str, channel: str = "",
                thread: str = "") -> str:
    """Investigate, then raise a Tier 2 card handing the work to the coding
    agent. Returns the reply for the thread he asked in."""
    from ..modules import delegate as delegate_mod
    from .approvals import propose

    asked = " ".join((request or "").split())
    now = datetime.now(UTC)
    why = denied(asked)
    if why:
        # Still says what it is, for him to do by hand -- a denial that does
        # not say what would fix it is indistinguishable from not looking.
        surface(what_changed=f"the owner asked: {asked}", evidence=asked,
                source=f"slack:{who}", target=estate(asked).target, read_at=now)
        return (f"That falls in the permanently denied set ({why}), so I won't do "
                f"it or hand it to an agent — that line has no runtime override. "
                f"It's yours to do by hand. Nothing was run.")

    if len(re.findall(r"[a-z0-9]{2,}", asked.lower())) < 3:
        # ", file it" became a card with nothing in it (#192): a brief is
        # only worth writing when there is a request to write it from.
        return ("I can't tell what you want done from that. Say what and where, "
                "like \"optimize the CPU on the Roots server\". Nothing was run.")

    place = estate(asked)
    findings, source = await _investigate(asked, place.module)
    brief = delegate(
        what_changed=f"the owner asked: {asked}",
        evidence=findings,
        source=source,
        target=place.target,
        done_looks_like=done_when(findings) or DEFAULT_DONE,
        tier=Tier.ASK,
        read_at=now,
        may_touch=place.may_touch,
        may_not_touch=place.may_not_touch + ATLAS_PATHS,
    ).render()
    hid = await delegate_mod.draft(request=asked, brief=brief, target=place.target,
                                   topic=place.topic, channel=channel, thread=thread)
    key = hashlib.sha256(asked.lower().encode()).hexdigest()[:16]
    pid = await propose(
        "delegate.claude_code", {"handoff_id": str(hid), "topic": place.topic},
        brief,
        rollback_plan="cancel the task if the agent has not claimed it yet",
        dedup_key=f"delegate:{key}")
    return "\n\n".join(filter(None, [
        _summary(findings),
        (f"Nothing I can run changes that, so I've written it up for the coding "
         f"agent on your Mac — card #{pid}. Approve it and the agent picks it up "
         f"on its next run; its report comes back to this thread. If nobody "
         f"claims it within the hour, I'll say so."),
    ]))
