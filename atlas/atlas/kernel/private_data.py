"""Which modules hold the owner's personal content, and who may read it.

Commit 7da37bf's rule, extended to the read tools: private work stays on
the local model. `module_brief` returns a module's inbox (mail senders and
subjects; Instagram DM, Slack mention and Matrix messages);
`module_status`, `read_metrics`, `read_history` and the named SQL queries
return observation details, which can carry the same text. A cloud-model
agent -- ops, growth, commerce, sentinel, a business agent through
`businesses.BASE_READS` -- asking for one of these modules gets a stated
refusal, never the content and never a quiet empty list.

The one place the set lives. A module is private here because of what it
READS, not how it is configured; add one by adding its name.
"""
from __future__ import annotations

from contextvars import ContextVar

PRIVATE_MODULES: frozenset[str] = frozenset({
    "mail",          # his mailboxes
    "messages",      # Instagram DMs
    "messaging",     # Slack mentions
    "matrix_rooms",  # his Matrix rooms and DMs, read as him
    "screening",     # messages strangers left for him on the phone line
    "vault",         # his documents and reading log
    "school",        # coursework, grades
    "calendar",      # his calendar
    "cameras",       # the cameras at his home
})


# The free-text assistant is one agent that is sometimes a PRIVATE run (the
# question is about the owner's own content, or the chat window it replays holds a
# private turn). A ROSTER entry would be scheduled and listed everywhere, so the
# run itself carries the fact: set by `assistant.loop` alone, around a run
# routed to the local model, reset in a finally. Default False = fails closed.
PRIVATE_RUN: ContextVar[bool] = ContextVar("atlas_private_run", default=False)
PRIVATE_RUN_AGENTS: frozenset[str] = frozenset({"assistant"})


def calling_agent() -> str | None:
    """The agent a read tool is running for, set by `tools.invoke` alone."""
    from .tools import CURRENT_AGENT
    return CURRENT_AGENT.get()


def agent_is_private(name: str | None) -> bool:
    """True only for a registered agent whose work routes to the local model."""
    if not name:
        return False
    if name in PRIVATE_RUN_AGENTS and PRIVATE_RUN.get():
        return True
    from .agents import ROSTER
    from .llm import Need
    agent = ROSTER.get(name)
    return agent is not None and agent.need is Need.PRIVATE


def refusal(module: str) -> dict | None:
    """The refusal a read tool returns for a private module, or None.

    Fails closed: no calling agent (a call from outside `tools.invoke`) is
    not a private agent.
    """
    if str(module) not in PRIVATE_MODULES:
        return None
    who = calling_agent()
    if agent_is_private(who):
        return None
    return {"error": (f"{module} holds the owner's personal content and stays on the "
                      f"local model; {who or 'this caller'} is not a local-model "
                      f"agent, so it is not read here. Ask the inbox agent."),
            "refused": "private_module"}


def allowed_rows(rows: list[dict], key: str = "property") -> list[dict]:
    """Drop rows about a private module unless the caller is a private agent."""
    if agent_is_private(calling_agent()):
        return rows
    return [r for r in rows if str(r.get(key)) not in PRIVATE_MODULES]
