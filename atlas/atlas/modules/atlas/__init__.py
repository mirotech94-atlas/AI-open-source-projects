"""atlas -- requests to change Atlas himself, recorded for a human.

Atlas cannot edit his own code, config or prompts: that is self-modification,
Tier 3, permanently denied (CLAUDE.md invariant 3), and nothing here changes it.
This module holds the one Tier 2, card-only action that lets the verified owner
ASK for a change from chat:

  * `atlas.dev_request` -- raised only by `kernel/dev_requests.py` from a strict
    parse of the owner's "fix X" / "change Y" / "make him do Z". Approving the
    card records the decision and stores the request text as a task note (a
    `tasks` row, held) for a developer session to pick up through git review. It
    applies nothing, runs no command, and the text is untrusted data throughout.
"""
from __future__ import annotations

import logging
from typing import Any

from ...kernel import dev_requests, idempotency, tasks
from ...kernel.audit import audit
from ...kernel.contracts import Action, ActionResult, FailureClass, Tier
from ...kernel.permissions import wrap_untrusted

log = logging.getLogger("atlas.dev_request")

OUTCOME = ("approved; waiting for a developer session to pick it up through git "
           "review. Nothing was changed.")


def _vet(params: dict[str, Any]) -> str | None:
    why = dev_requests.problem(str(params.get("request") or ""))
    if why:
        return why
    if not str(params.get("request_sha256") or "").strip():
        return "the request has no hash"
    return None


def _card_detail(params: dict[str, Any]) -> str:
    return (wrap_untrusted(str(params.get("request") or ""), "owner-chat-dev-request")
            + "\nApproving records this as a task note for a developer session. "
              "ATLAS applies nothing and changes none of its own code, config or "
              "prompts.")


async def dev_request_dry(request: str, request_sha256: str) -> ActionResult:
    why = _vet({"request": request, "request_sha256": request_sha256})
    if why:
        return ActionResult(False, f"would not record: {why}")
    return ActionResult(True, "would record the request as a task note for a "
                              "developer session; applies nothing")


async def dev_request(request: str, request_sha256: str) -> ActionResult:
    """Record the decision and the note. Nothing else."""
    why = _vet({"request": request, "request_sha256": request_sha256})
    if why:
        return ActionResult(False, f"not recorded: {why}",
                            failure_class=FailureClass.VALIDATION)
    run = idempotency.current_run() or f"sha:{str(request_sha256)[:40]}"
    if await idempotency.claim("atlas.dev_request", run) != idempotency.CLAIMED:
        return ActionResult(True, "already recorded; nothing was applied")
    tail = run.split(":", 1)[1] if run.startswith("proposal:") else ""
    card = int(tail) if tail.isdigit() else None
    note = f"Dev request from the owner (untrusted text): {dev_requests.clean(request)}"
    task_id = await tasks.open(note, source="dev_request", who="owner")
    if task_id is None:
        await idempotency.release("atlas.dev_request", run)
        return ActionResult(False, "not recorded: the task note could not be stored")
    await tasks.close(task_id, route="dev_request", ok=None, outcome=OUTCOME,
                      cards=(card,) if card else ())
    await idempotency.settle("atlas.dev_request", run, f"task {task_id}")
    await audit("atlas.dev_request.recorded", str(task_id),
                {"card": card, "sha256": str(request_sha256)[:16]})
    return ActionResult(True, f"recorded as task #{task_id}; nothing was applied",
                        evidence={"task_id": task_id})


class AtlasModule:
    name = "atlas"
    title = "Requests to change Atlas"
    requires_env = ()
    optional_env = ("DEV_REQUESTS_PER_DAY",)
    setup_note = ("Nothing to set up. A \"fix X\" in the owner chat becomes a card; "
                  "approving it only records a note for a developer session.")

    async def setup(self) -> None: ...
    async def teardown(self) -> None: ...

    def checks(self) -> tuple:
        return ()

    def actions(self) -> tuple[Action, ...]:
        return (
            Action("atlas.dev_request", Tier.ASK,
                   "Record a change the owner asked for in chat as a task note for a "
                   "developer session (approval only records the decision; Atlas "
                   "applies nothing and never edits himself)",
                   run=dev_request, dry_run=dev_request_dry,
                   params={"request": "the owner's words, up to 500 characters, "
                                      "untrusted",
                           "request_sha256": "hash of the text (dedupe)"},
                   max_per_hour=5, card_only=True, verbose_card=True,
                   vet=_vet, card_detail=_card_detail),
        )


MODULE = AtlasModule()
