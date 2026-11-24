"""Lessons — the never-repeat ledger, with one way to overrule it.

What it watches: nothing. It has no checks, because there is no outside
service here to measure; the ledger is ATLAS's own table.

What it may do: clear a never-repeat block, and only when the owner says why the
situation is different. `approvals.propose()` refuses an action that has
already failed twice with the same parameters and hands him the sentence
"Say why it is different to clear it"; "it's different because the API
changed" comes back through the voice/intent path as this action. Clearing a
block does not run anything and does not change a tier — the action is still
Tier 2 afterwards if it was Tier 2 before. All it does is let ATLAS ask again.

Tier ASK: only he lifts a block. Reached through his own word on a
principal channel (`intent.act`) or his yes on a card -- never by an
unattended pick, never by an agent on its own.

Endpoints assumed: none. It reads and writes the local `lessons` table
through the kernel.
"""
from __future__ import annotations

import json

from ...kernel import lessons
from ...kernel.contracts import Action, ActionResult, Tier


def _params(params_json: str) -> dict:
    """Parameters as the reasoner wrote them. Anything that is not an object
    is treated as no parameters at all, never guessed at."""
    try:
        value = json.loads(params_json or "{}")
    except (TypeError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


async def clear_block(action: str, why: str, params_json: str = "{}") -> ActionResult:
    """Unblock one action+parameters pair, on his stated reason."""
    reason = " ".join((why or "").split())
    if not reason:
        return ActionResult(
            False, "a block is cleared by saying why it is different, and "
                   "nothing was said")
    params = _params(params_json)
    standing = await lessons.blocked(action, params)
    if standing is None:
        return ActionResult(
            True, f"nothing was blocking {action} with those parameters; "
                  "there was nothing to clear")
    await lessons.clear(action, params, reason)
    return ActionResult(
        True,
        f"{action} can be proposed again — you said: {reason}. It is still "
        "gated exactly as it was; this only stops me refusing to ask.",
        evidence={"was": standing},
        rollback_token=json.dumps({"action": action, "params": params}))


async def clear_dry(action: str, why: str, params_json: str = "{}") -> ActionResult:
    """Say what would change. Clears nothing."""
    params = _params(params_json)
    standing = await lessons.blocked(action, params)
    if standing is None:
        return ActionResult(
            True, f"nothing is blocking {action} with those parameters")
    return ActionResult(
        True, f"would clear the block on {action} — currently: {standing}")


async def clear_rollback(token: str) -> ActionResult:
    """Put the guard back."""
    try:
        undo = json.loads(token or "{}")
    except (TypeError, ValueError):
        return ActionResult(False, "unreadable rollback token")
    action = undo.get("action")
    if not action:
        return ActionResult(False, "rollback token names no action")
    await lessons.reblock(action, undo.get("params") or {})
    return ActionResult(True, f"{action} is blocked again")


class LessonsModule:
    name = "lessons"
    title = "Lessons"
    requires_env: tuple[str, ...] = ()

    async def setup(self) -> None: ...
    async def teardown(self) -> None: ...

    def checks(self):
        return ()

    def actions(self):
        return (
            Action(
                name="lessons.clear",
                # ASK, although it is reversible and invisible outside: the
                # block is the owner's to lift, by saying why it is different.
                # As AUTO it sat in the catalog `diagnose` picks from with
                # nobody attached, and the model could clear its own guard,
                # writing its own "why". The rollback stays: reversibility is
                # why it has one, not why it may run unattended.
                tier=Tier.ASK,
                summary="Clear a never-repeat block after he says why it is "
                        "different now",
                run=clear_block, dry_run=clear_dry, rollback=clear_rollback,
                params={
                    "action": "the action that is blocked, e.g. "
                              "'fiverr.draft_buyer_reply'",
                    "why": "his words: what is different now",
                    "params_json": "the parameters it was blocked with, as a "
                                   "JSON object; default {}",
                },
                max_per_hour=10,
            ),
        )

    def brief(self) -> str:
        return ""


MODULE = LessonsModule()
