"""The kernel door for a Mac action Miro says out loud on the dashboard.

Nothing under `atlas/api` may call `execute` (the AST ban in tests/test_api
.py). Miro, 2026-09-28, chose to relax one thing: "talking to ATLAS on the
dashboard should run the Mac action straight away, for Mac actions only".
This is that door, as narrow as `kernel.unattended`:

  * **Tier 1 Mac actions only.** `principal_only` Tier 1 actions in the
    `mac.` namespace: apps (open and quit), YouTube Music, reminders, notes,
    web addresses and searches, volume. A Tier 2 Mac action (an iMessage, a
    Shortcut) said out loud is a card like anything else: the executor's
    Tier 2 gate only asks that someone approved, and this door must never
    be that someone.
  * **Only the signed-in dashboard.** The one caller is api/voice.py's
    signed-in /ask path (tests/test_api.py refuses any other caller). It
    records `approved_by="voice:dashboard"`, and the executor accepts that
    origin only for principal_only actions -- it re-checks the tier, runs
    the dry run and audits as always.
  * **Never a phone turn.** A call's words arrive by caller ID; they get a
    card.

The menu-bar hotkey (2026-10-06, "I don't want to have to open the ATLAS
application to use it") is the second caller, through
`run_spoken_commands`: api/voice.py's signed-in /command path, mesh-only
like every /api route. The words are read in code (modules/mac/commands),
each clause mapped to one Mac action; the whole plan is resolved and
checked before anything runs, then `run_chain` runs it in order, at most
MAX_CHAIN steps, stopping at the first refusal, with
`approved_by="voice:menubar"` -- the same "voice" origin, so the same
executor rules (principal_only, Tier 1 only). The model is asked only for
a clause the parser could not read, only to pick from the speakable Mac
actions, and what it picks is checked like everything else.
"""
from __future__ import annotations

from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from typing import Any

from .audit import audit
from .contracts import ActionResult, Tier
from .registry import REGISTRY

MAX_CHAIN = 6
MENUBAR = "voice:menubar"
# The dashboard's /ask when no model answered (2026-10-06): the same parser
# and the same chain, recorded as said on the dashboard -- the origin
# run_mac_action already uses.
DASHBOARD = "voice:dashboard"
_WHERE = {MENUBAR: "Said to the Mac from the menu bar",
          DASHBOARD: "Asked out loud on the dashboard"}


def speakable(name: str) -> bool:
    action = REGISTRY.actions.get(name)
    decision = REGISTRY.decisions.get(name)
    return bool(name.startswith("mac.") and action is not None
                and getattr(action, "principal_only", False)
                and decision is not None and decision.allowed
                and decision.tier is Tier.AUTO)


async def run_mac_action(name: str, params: dict[str, Any],
                         transcript: str) -> ActionResult:
    if not speakable(name):
        await audit("spoken.refused", name, {"why": "not a Mac action"})
        return ActionResult(False, "that isn't something I run straight from voice")
    from .executor import ExecutionRequest, execute
    return await execute(ExecutionRequest(
        action=name, params=params,
        reason=f"Asked out loud on the dashboard: “{transcript[:300]}”",
        approved_by="voice:dashboard"))


# ------------------------------------------------------------------ chains

@dataclass(frozen=True, slots=True)
class Ran:
    action: str
    said: str
    ok: bool
    detail: str


@dataclass(frozen=True, slots=True)
class Heard:
    """What the menu bar shows: one line, and what ran."""

    handled: bool                   # False: not a Mac command; ask /ask instead
    line: str
    ran: tuple[Ran, ...] = ()
    ask: str | None = None          # a question; nothing ran


def speakable_names() -> frozenset[str]:
    return frozenset(n for n in REGISTRY.actions if speakable(n))


Execute = Callable[[Any], Awaitable[ActionResult]]


async def run_chain(steps: Sequence[Any], transcript: str, *,
                    execute: Execute | None = None,
                    origin: str = MENUBAR) -> tuple[Ran, ...]:
    """Each step through the executor, in order. At most MAX_CHAIN; the first
    refusal or failure stops the rest. `steps` carry .action, .params and
    .said (modules/mac/commands.Step)."""
    from .executor import ExecutionRequest
    if execute is None:
        from .executor import execute
    out: list[Ran] = []
    for step in list(steps)[:MAX_CHAIN]:
        if not speakable(step.action):
            await audit("spoken.refused", step.action, {"why": "not a Mac action"})
            out.append(Ran(step.action, step.said, False,
                           "that isn't something I run straight from voice"))
            break
        result = await execute(ExecutionRequest(
            action=step.action, params=dict(step.params),
            reason=f"{_WHERE.get(origin, _WHERE[MENUBAR])}: “{transcript[:300]}”",
            approved_by=origin if origin in _WHERE else MENUBAR))
        out.append(Ran(step.action, step.said, bool(result.ok), str(result.detail)))
        if not result.ok:
            break
    return tuple(out)


def _line(steps: Sequence[Any], ran: Sequence[Ran]) -> str:
    """One line: what ran, where it stopped and why, what never started."""
    done = [r.detail for r in ran if r.ok]
    parts = ["; ".join(done)] if done else []
    stopped = next((r for r in ran if not r.ok), None)
    if stopped is not None:
        parts.append(f"stopped at “{stopped.said}”: {stopped.detail}")
    left = list(steps)[len(ran):]
    if left:
        why = (f"I run at most {MAX_CHAIN} at a time" if stopped is None
               else "not run")
        parts.append(f"{why}: " + ", ".join(f"“{s.said}”" for s in left))
    line = " — ".join(p for p in parts if p) or "Nothing ran."
    return line[0].upper() + line[1:]


Choose = Callable[..., Awaitable[tuple[str, dict] | None]]


async def run_spoken_commands(transcript: str, *, choose: Choose | None = None,
                              execute: Execute | None = None,
                              origin: str = MENUBAR) -> Heard:
    """Read one utterance, resolve it whole, then run it.

    Nothing runs when any clause is a question (a pronoun, a missing name),
    unreadable even by the model, or refused by the actions' own pure
    checks: a chain is never half run on a guess. Once it starts, a step
    the Mac refuses stops it there, and the line says so.
    """
    from ..modules.mac import commands

    plan = commands.plan(transcript)
    if plan.ask is not None:
        await audit("spoken.ask", "-", {"transcript": transcript[:300],
                                        "ask": plan.ask})
        return Heard(True, plan.ask, ask=plan.ask)
    if not plan.handled:
        return Heard(False, "")
    names = speakable_names()
    steps: list[Any] = []
    for item in plan.items:
        if isinstance(item, commands.Step):
            steps.append(item)
            continue
        # A clause the parser could not read: the model picks, from the
        # speakable Mac actions only, or nothing runs.
        if choose is None:
            from .intent import choose as choose_
            choose = choose_
        picked = await choose(item.said, principal=True, only=names)
        if picked is None or picked[0] not in names or not isinstance(picked[1], dict):
            ask = f"I didn't catch “{item.said}”, so nothing ran. Say it again?"
            return Heard(True, ask, ask=ask)
        steps.append(commands.Step(picked[0], dict(picked[1]), item.said))
    if not steps:
        return Heard(True, "Okay — nothing ran.")
    for step in steps:
        why = commands.problem(step.action, step.params)
        if why is not None:
            await audit("spoken.refused", step.action, {"why": why})
            return Heard(True, f"Nothing ran: “{step.said}” — {why}.")
    ran = await run_chain(steps, transcript, execute=execute, origin=origin)
    return Heard(True, _line(steps, ran), ran=ran)
