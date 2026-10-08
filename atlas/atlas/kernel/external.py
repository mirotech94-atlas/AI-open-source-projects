"""The external-agent socket: a read-only advisor, nothing more.

AGENCY.md §1 states the hard rule this module exists to satisfy: an external
agent (Apex or anything like it) is a read-only advisor. It can answer
questions. It can never propose, approve, or execute. It has no path to
`executor.execute`, and its output is wrapped in `wrap_untrusted()` like any
other outside text — because it *is* outside text, written by someone else's
model.

`ADVISORS` starts empty on purpose: nothing is wired in until an external
agent actually ships an API to plug in here.
"""
from __future__ import annotations

from typing import Protocol

from .permissions import wrap_untrusted


class ExternalAgent(Protocol):
    name: str

    async def ask(self, question: str) -> str: ...


ADVISORS: dict[str, ExternalAgent] = {}


def register(agent: ExternalAgent) -> None:
    """Add an advisor to the catalog. Registering it is not trusting it."""
    ADVISORS[agent.name] = agent


async def consult(name: str, question: str) -> str:
    """Ask a registered advisor a question and return its answer, wrapped as
    untrusted data. Raises KeyError if `name` is not registered — there is no
    silent fallback to a different advisor."""
    agent = ADVISORS[name]
    answer = await agent.ask(question)
    return wrap_untrusted(answer, f"external:{name}")
