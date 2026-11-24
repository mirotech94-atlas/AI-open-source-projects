"""assistant — "ask ATLAS anything": a general-purpose, read-only assistant.

Off unless `ASSISTANT_ENABLED` is set (1/true/yes/on), read on every call.
Off, nothing here is reachable from Slack or `intent.act` and its check is
not on the board: ATLAS behaves exactly as it did before the module.

What it watches: `assistant.budget`, today's outside reads (web searches
and page/weather/Wikipedia/dictionary/rate fetches) against the two daily
caps in `budget.py`, with the time it was read. UNKNOWN when the audit log
cannot be counted (invariant 7), DEGRADED when a cap is spent.

What it may do: one thing, tell the owner a short code-built note (`notices.py`, the
Tier 1 action `assistant.update_owner`, a Slack DM, 1 an hour, 3 a day). Every tool it hands a model is
a read (`tools.py`), the loop (`loop.py`) can reach no catalog action, and
nothing in this package imports the executor. It answers questions -- about
the world, with sources and the time each was read, and about ATLAS itself
from a fresh read of ATLAS's own tables -- and it never claims to be a
person or to have done anything.

How it is asked (ASSISTANT.md has the full account):

  * in Slack, `ask <question>` (the router picks Ask or Search), `ask!
    <question>` or `search <question>` (Search, forced), and `ask cancel
    <run id>` -- on any channel ATLAS reads, parsed in code;
  * `atlas, <question>` when what follows is shaped like a question and is
    not an order, through `kernel.intent.act`'s module `command` hook (the
    same hook as `/atlas funnel ...`), so only after the channel has proved
    it is the owner;
  * every plain message in the channel `SLACK_ASSISTANT_CHANNEL` names
    (topic `assistant`), and `stop` there; unset, that door is closed;
  * `assistant_ask`, a kernel read tool, for the voice route to call later
    -- registered here, held by no agent, and not wired to voice yet.
"""
from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from ...kernel.contracts import Check, Health, Observation, Severity
from ...kernel.quota import for_owner
from ...kernel.tools import Tool, register
from . import budget, modes, notices, runs
from .doors import door_enabled, propose_enabled
from .loop import Answer, ask
from .runs import cancel
from .skills import listing as skills_listing
from .tools import ASSISTANT_TOOLS, register_tools

log = logging.getLogger("atlas.assistant")

__all__ = ["MODULE", "STOP", "Answer", "Request", "answer", "ask", "cancel",
           "converse", "door_enabled", "enabled", "handle", "parse_command",
           "parse_explicit", "propose", "propose_enabled", "runs"]

TOPIC = "assistant"
VOICE_TOOL = "assistant_ask"
ENABLED_ENV = "ASSISTANT_ENABLED"

USAGE = ("Ask me with `ask <question>`, `search <question>` for a fresh read of "
         "the web, or a question after my name, `atlas, what is ...?`")


def enabled() -> bool:
    """`ASSISTANT_ENABLED`, read fresh. Off unless set to 1, true, yes or on."""
    return os.getenv(ENABLED_ENV, "").strip().lower() in ("1", "true", "yes", "on")


@dataclass(frozen=True, slots=True)
class Request:
    """What a message asks of the assistant: a question (with a forced mode,
    or None for the router to choose), or a cancel of a run by its id."""
    kind: str                       # "ask" | "cancel"
    question: str = ""
    mode: str | None = None
    run_id: str = ""


_LEAD = r"^\s*(?:/?atlas\b[\s,:]*)?"
# `ask cancel <id>` -- before `ask <q>`, or "cancel ab12cd34" is a question.
_CANCEL = re.compile(_LEAD + r"ask\s+cancel\s+([0-9a-f]{8})\s*[.!]?\s*$", re.IGNORECASE)
# `ask! <q>` and `search <q>`: Search, forced.
_FORCED = re.compile(_LEAD + r"(?:ask!|search\b)[\s:,-]*(.+)$",
                     re.IGNORECASE | re.DOTALL)
# `ask <q>` / `ask: <q>`: the router decides.
_ASK = re.compile(_LEAD + r"ask\b(?!!)[\s:,-]*(.+)$", re.IGNORECASE | re.DOTALL)
# `atlas, <question>`: taken only when the rest is shaped like a question.
_ADDRESSED = re.compile(r"^\s*(?:hey\s+|ok\s+|okay\s+)?atlas\s*[,:]\s*(.+)$",
                        re.IGNORECASE | re.DOTALL)
_QUESTION = re.compile(
    r"^\s*(?:what|when|where|who|whom|whose|why|how|which|is|are|was|were|do|"
    r"does|did|can|could|would|should|will|define|explain|compare|summari[sz]e|"
    r"convert|calculate|translate|tell\s+me|give\s+me)\b",
    re.IGNORECASE)
# `stop`, alone, in the assistant channel.
STOP = re.compile(r"^\s*(?:atlas\s*[,:]?\s*)?stop\s*[.!]*\s*$", re.IGNORECASE)


def parse_explicit(text: str) -> Request | None:
    """An explicit assistant form -- `ask cancel <id>`, `ask! <q>`, `search
    <q>`, `ask <q>` -- or None. Pure, no model. Slack takes these before any
    other reading of the message (`api.slack_events._request`).

    `search <q>` is a lookup verb, and a lookup that names work an agent owns
    is an order ("search up 50 local businesses with a poor website and
    make them one" is the agency's): that is left for the missions path, so
    `search` here never steals a job (`api.voice.is_order`)."""
    raw = text or ""
    if m := _CANCEL.match(raw):
        return Request("cancel", run_id=m.group(1).lower())
    if m := _FORCED.match(raw):
        question = m.group(1).strip()
        if not question:
            return None
        if re.match(_LEAD + r"search\b", raw, re.IGNORECASE):
            from ...api.voice import is_order
            if is_order(raw.strip()):
                return None
        return Request("ask", question, modes.SEARCH)
    if m := _ASK.match(raw):
        question = m.group(1).strip()
        return Request("ask", question) if question else None
    return None


def parse_command(text: str) -> Request | None:
    """What `text` asks of the assistant, or None when it is not addressed
    to it. Pure, no model. The explicit forms always; `atlas, <q>` only when
    `q` reads as a question and not as an order (`api.voice.is_order`), so
    "atlas, snapshot everything" still reaches the action picker."""
    raw = text or ""
    if (got := parse_explicit(raw)) is not None:
        return got
    if m := _ADDRESSED.match(raw):
        question = m.group(1).strip()
        if not question:
            return None
        if not (question.endswith("?") or _QUESTION.match(question)):
            return None
        from ...api.voice import is_order
        if is_order(question):
            return None
        return Request("ask", question)
    return None


async def answer(question: str, *, channel: str = "slack", who: str = "",
                 skill: str | None = None, mode: str | None = None) -> str:
    """`ask`, rendered for a chat: the answer, what could not be verified,
    the Sources list with read times, and the run id."""
    return (await ask(question, channel=channel, who=who, skill=skill,
                      mode=mode)).render()


# A go-ahead, parsed in code: nothing a model says can make a message one.
GO_AHEAD = re.compile(
    r"^\s*(?:atlas\s*[,:]?\s*)?(?:yes\s*[,.]?\s*)?(?:please\s+)?"
    r"(?:do\s+it|go\s+ahead|go\s+for\s+it|make\s+it\s+so)(?:\s+please)?\s*[.!]*\s*$",
    re.IGNORECASE)
PRIVATE_NO_PROPOSE = ("That earlier message was private, so I won't turn it into "
                      "an action from here. Say it again as a plain request.")
NOTHING_TO_PROPOSE = "There is no earlier message of yours here to act on."


async def propose(text: str, *, channel: str, who: str,
                  conversation: str | None = None) -> str:
    """`ASSISTANT_PROPOSE`: hand the owner's OWN previous message in this channel
    (never ATLAS's words, never a private turn) to the action picker, which
    raises a card at most -- `intent.queue` runs nothing and refuses Tier 3.
    The reply is written here, from the outcome, not by a model."""
    from ...kernel import chat_memory, intent
    earlier, private = await chat_memory.last_user_turn(conversation or channel)
    if not earlier:
        return NOTHING_TO_PROPOSE
    if private:
        return PRIVATE_NO_PROPOSE
    outcome = await intent.queue(earlier, channel=channel, who=who)
    if outcome.ok and outcome.proposal_id is not None:
        return (f"Queued {outcome.action}: a card is waiting in Slack for your "
                f"approval. Nothing has run.")
    return outcome.spoken


# Tools whose result holds no free text from outside ATLAS (no mail, note,
# camera label, web page, buyer words, a collector's detail or a last subject): only a reply that used these alone may
# be stored as prose (figures masked). Any other tool: the placeholder only, so
# untrusted text never becomes a trusted assistant turn.
PROSE_SAFE_TOOLS: frozenset[str] = frozenset({
    "calculate", "time_now", "convert_units", "atlas_capabilities"})


async def converse(text: str, *, channel: str, who: str = "",
                   history: list | None = None, tainted: bool = False,
                   conversation: str | None = None, private_ok: bool = False) -> str:
    """One chat turn for a door's general-chat step (Slack, WhatsApp, the
    dashboard's typed lines): the loop with the extra ATLAS reads, this
    channel's own memory replayed, and the exchange stored back under the same
    channel. Returns a `chat_memory.Remembered`, so the door does not store it
    again. A window that held private content makes this run PRIVATE (the
    local model, local tools); the spoken voice path never comes here.

    `conversation` keys the memory (default: the channel; Slack passes
    `slack:<channel id>`). `private_ok` is set by a door only when the principal
    is the owner AND the reply goes to a private sink (dashboard, his WhatsApp, his
    Slack DM); without it private reads are never offered and a PRIVATE run is
    refused rather than answered into a shared place."""
    from ...kernel import chat_memory
    key = conversation or channel
    if propose_enabled() and GO_AHEAD.match(text or ""):
        return chat_memory.Remembered(await propose(text, channel=channel, who=who,
                                                       conversation=key))
    if history is None:
        history, from_memory = await chat_memory.recent_with_taint(channel=key)
        tainted = tainted or from_memory
    got = await ask(text, channel=channel, who=who, history=history,
                    force_private=tainted, extended=True, private_ok=private_ok,
                    refuse_private=not private_ok)
    reply = got.render(run_line=False)
    live = bool(got.tools_used)
    if got.status == runs.COMPLETED:
        outside = not set(got.tools_used) <= PROSE_SAFE_TOOLS
        if not live:
            stored = got.text
        elif outside:                       # any tool that can return free text
            stored = chat_memory.LiveReply(reply)
        else:                               # ATLAS/local reads: prose kept, figures masked
            stored = chat_memory.mask_figures(got.text)
        await chat_memory.remember(text, stored, channel=key, private=got.private)
    said = chat_memory.Remembered(reply)
    said.live = live
    said.private = got.private
    return said


@for_owner
async def handle(request: Request, *, channel: str, who: str) -> str:
    """Carry out a parsed request: answer the question, or cancel the run.
    the owner asked it, so it may spend the free allowance kept for him
    (kernel/quota.py)."""
    if request.kind == "cancel":
        return await cancel(request.run_id, by=who)
    return await answer(request.question, channel=channel, who=who,
                        mode=request.mode)


async def check_budget() -> Observation:
    """Today's outside reads against the caps. The count is the audit log;
    an unreadable log is UNKNOWN, never a zero."""
    now = datetime.now(UTC)
    lines = []
    worst = Health.OK
    total = 0
    for tool, label in (("search_web", "searches"), ("read_page", "fetches")):
        cap = budget.cap_for(tool)
        try:
            used = await budget.used_today(tool)
        except Exception as exc:                        # noqa: BLE001
            return Observation("budget", Health.UNKNOWN, detail=(
                f"the assistant's call count could not be read "
                f"({type(exc).__name__}); nothing is counted"))
        total += used
        lines.append(f"{used}/{cap} {label}")
        if cap is not None and used >= cap:
            worst = Health.DEGRADED
    detail = (f"{'; '.join(lines)} in the last 24 hours, read {now:%H:%M} UTC"
              + ("; a cap is spent, so that tool is refused until it rolls off"
                 if worst is Health.DEGRADED else ""))
    return Observation("budget", worst, float(total), "calls", detail, at=now)


async def _ask_tool(question: str) -> dict[str, Any]:
    """`assistant_ask`: the loop as a kernel read tool, for the voice route
    to call later. Returns the answer and its sources as data."""
    if not enabled():
        return {"error": f"the assistant is off ({ENABLED_ENV} is not set)"}
    got = await ask(question, channel="voice")
    return {"answer": got.render(), "text": got.text,
            "sources": [{"url": s.url, "read_at": s.read_at, "kind": s.kind}
                        for s in got.sources],
            "tools_used": list(got.tools_used), "skill": got.skill,
            "mode": got.mode, "run_id": got.run_id, "status": got.status,
            "private": got.private, "unverified": list(got.unverified),
            "stopped": got.stopped, "read_at": got.read_at}


def register_voice_tool() -> None:
    """Registered beside the assistant's own tools but NOT in
    `ASSISTANT_TOOLS`: the loop must never be offered itself to call."""
    register(Tool(VOICE_TOOL,
                  "Ask the general-purpose assistant one question and get its "
                  "answer with Sources and read times. Read-only; it cannot act.",
                  {"type": "object", "properties": {"question": {"type": "string"}},
                   "required": ["question"]},
                  read=_ask_tool))


class AssistantModule:
    name = "assistant"
    title = "Assistant (ask ATLAS anything)"
    requires_env = ()
    optional_env = (ENABLED_ENV, budget.SEARCH_ENV, budget.FETCH_ENV,
                    "ASSISTANT_MAX_TOOL_CALLS",
                    "ASSISTANT_MAX_TOKENS", "SLACK_ASSISTANT_CHANNEL",
                    "ASSISTANT_FX_URL", "ASSISTANT_DOORS", "ASSISTANT_PROPOSE",
                    "ASSISTANT_PROFILE", "ASSISTANT_LOCAL_PROMPT_CHARS")
    setup_note = ("answers `ask <question>` in Slack; set SLACK_ASSISTANT_CHANNEL "
                  "for a channel where every message is a question")

    async def setup(self) -> None:
        register_tools()
        register_voice_tool()

    async def teardown(self) -> None: ...

    def checks(self):
        # Off, the board is as it was: no check for a module nobody can ask.
        # The registry reads this at boot; turning the flag on takes a restart
        # for the check, never for the hooks (those read it per call).
        if not enabled():
            return ()
        return (
            Check("budget", check_budget, timedelta(hours=1), Severity.NORMAL,
                  "the assistant's outside reads today against its daily caps",
                  baseline_sigma=None),
        )

    def actions(self):
        # The one thing the assistant may do: tell the owner a short code-built note
        # (Tier 1, `notices.py`). It reaches no other action.
        return notices.actions()

    def brief(self) -> str:
        return ""

    async def command(self, text: str, *, channel: str, who: str):
        """`kernel.intent.act`'s code-parsed hook: a question for the
        assistant, or None so the request goes on to the action picker."""
        if not enabled():
            return None
        from ...kernel.intent import Outcome
        request = parse_command(text)
        if request is None:
            return None
        return Outcome(True, await handle(request, channel=channel, who=who))

    @staticmethod
    def tools() -> list[str]:
        return sorted(ASSISTANT_TOOLS)

    @staticmethod
    def skills() -> list[dict[str, Any]]:
        return skills_listing()


MODULE = AssistantModule()

# Importing the package is enough for the tools and skills to exist: the
# registry calls setup() at boot, and a test or the Slack route may reach
# `ask` before any boot.
register_tools()
register_voice_tool()
