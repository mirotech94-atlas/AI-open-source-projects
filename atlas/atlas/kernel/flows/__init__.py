"""The first implementation slice: three end-to-end durable workflows.

The reliability plan (p. 14, "Prioritized first implementation slice")
starts with three workflows rather than a refactor of every module:

  ask      "Ask ATLAS a factual question in any Slack channel": durable
           intake of the event, the reply in the thread it was asked in, a
           fresh answer or an honest unknown, exactly one reply
           (`flows/ask.py`, wired into `api/slack_events.py`).
  ops      "Detect one synthetic operational issue": deduplicate, bounded
           diagnostics, one action or one next step, then recovery verified
           or escalated (`flows/ops.py`, wired into `kernel/fixes.sweep`).
  content  "Process one content job through a stub publisher": the quality
           gate on the actual file, the autopost rule or an exact card,
           concurrent duplicates giving one effect, a partial platform
           failure keeping what succeeded and reconciling the rest, and a
           reconciled receipt (`flows/content.py`, wired into
           `kernel/reels.post_or_card`).

Each is a PRODUCER for the durable core (docs/durable-core.md): it writes a
`workflows` row and walks the state machine in `kernel/durable.py`, and its
effects go out as `outbox` rows through `executor.execute`. Nothing here
sends on its own, and nothing here changes the executor or the approvals
claim.

Two switches, both read per call and before any SQL, both default off:

  ATLAS_DURABLE_CORE=on               the core itself (kernel/durable.py)
  ATLAS_DURABLE_FLOWS=ask,ops,content which slices produce ("all" for all)

The second exists so that turning the core on to watch its leases (rollout
step 3) does not also switch the producers on. With either off, every
wired call site runs exactly the code it ran before. docs/acceptance.md
maps the plan's acceptance IDs to the tests that exercise these slices.
"""
from __future__ import annotations

import logging
import os

from ..durable import State
from ..leases import Fence

log = logging.getLogger("atlas.flows")

FLOWS = ("ask", "ops", "content")


def enabled(flow: str) -> bool:
    """ATLAS_DURABLE_CORE on AND `flow` named in ATLAS_DURABLE_FLOWS."""
    from ..durable import enabled as core_enabled
    if flow not in FLOWS or not core_enabled():
        return False
    raw = os.getenv("ATLAS_DURABLE_FLOWS", "").strip().lower()
    names = {part.strip() for part in raw.split(",") if part.strip()}
    return flow in names or "all" in names


def any_enabled() -> bool:
    return any(enabled(f) for f in FLOWS)


async def walk(workflow_id: int, fence: Fence | None,
               *moves: tuple[State, State], step: str | None = None,
               evidence_refs: list[str] | None = None) -> bool:
    """Make each move in order, fenced. False at the first one refused (a
    stale fence, or the row was not where this worker thought): the rest
    are not attempted."""
    from ..durable import transition
    for src, dst in moves:
        if not await transition(workflow_id, src, dst, fence=fence, step=step,
                                evidence_refs=evidence_refs):
            return False
    return True


async def tick() -> None:
    """The flows' share of the scheduler's outbox step (only in the loop with
    ATLAS_DURABLE_CORE=on): settle what the outbox finished, mirror decided
    cards, and read back whether a fixed check recovered. Each part is
    skipped while its slice is off, and one failing never stops the others."""
    from . import content, ops
    parts = []
    if enabled("content"):
        parts += [("content cards", content.sync_cards),
                  ("content settle", content.settle_all)]
    if enabled("ops"):
        parts += [("ops recovery", ops.verify_recovery)]
    for name, part in parts:
        try:
            await part()
        except Exception:
            log.exception("flows: %s pass failed; retried next cycle", name)
