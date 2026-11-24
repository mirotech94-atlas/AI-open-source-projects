"""The kernel door a surface with no person behind it knocks on.

Nothing under `atlas/api` may call `execute` — `tests/test_api.py::
test_no_route_anywhere_can_reach_the_executor` is an AST ban over that whole
tree, and it is what stops "a path from a browser to the executor" from
reappearing by accident. Every other surface already reaches the executor
through the kernel: a Slack button and a keypad through `approvals.decide`,
a spoken request through `intent.act`, an agent through `tools.run_tool`.
The screening webhook needs the same door and there was none, so this is it.

It is deliberately the narrowest of the four:

  * **Named actions only.** `ALLOWED` is the whole list. A door any Tier 1
    action could walk through is a general-purpose way to act with nobody
    attached, which is not what this is for; adding to the list is a
    deliberate edit that shows up in a diff, and `tests/test_api.py` refuses
    a caller outside `atlas/api/screening.py`, `atlas/api/clips.py` (the
    Mac agent's Whop record, 2026-09-29), `atlas/api/webild.py` (its
    Webild site record, the same day) and `atlas/api/lead_call.py` (a
    meeting hold a lead picks on a call, 2026-09-30).
  * **Tier 1 only.** A Tier 2 action is refused here rather than escalated.
    Nothing about this path could carry an approval, so it must not be able
    to stand in for one — that is how a door for unattended work becomes a
    way around the gate.
  * **No `approved_by`.** Nobody approved anything. A stranger on a phone
    line is not an approver, and recording one would make the audit trail
    say something untrue.
  * **Nothing it returns is fit to repeat.** The executor's refusals name
    the action and, in watch-only, the environment variable that would give
    ATLAS hands. Callers on a public surface speak their own fixed lines.

It adds no permission: `execute` still re-checks the tier, still runs the
dry run first, still writes the audit entries. This only refuses earlier,
and refuses more.
"""
from __future__ import annotations

from typing import Any

from .audit import audit
from .contracts import ActionResult, Tier
from .registry import REGISTRY

# Every action a surface with no person behind it may run. One line per
# action, on purpose.
ALLOWED = frozenset({
    # The agent on Miro's Mac recording a clip it submitted on Whop (Miro,
    # 2026-09-29): a status on ATLAS's own row, mesh-only and behind the
    # HANDOFF_TOKEN bearer (atlas/api/clips.py).
    "clips.record_whop_submission",
    # The same agent recording that it uploaded a clip's DRAFT on Whop, and
    # what the brand decided about it (Irubyana's campaign asks for a draft
    # before posting, 2026-10-01): statuses on ATLAS's own row, the same
    # door and bearer (atlas/api/clips.py).
    "clips.record_draft_submitted",
    "clips.record_draft_decision",
    # The agent on Miro's Mac recording the Webild site it published for a
    # lead (Miro, 2026-09-29): a status on ATLAS's own row and a read-only
    # launch check, mesh-only and behind the HANDOFF_TOKEN bearer
    # (atlas/api/webild.py).
    "agency.record_webild_site",
    # A lead on ATLAS's own consented call picking one of the free slots
    # just read from Miro's calendar (Miro, 2026-09-30): a hold on his own
    # calendar with no attendee, re-checked free by its dry run, rolled back
    # by deleting it (atlas/api/lead_call.py).
    "agency.hold_meeting",
    # agency.send_invite and agency.send_agreement left this list on
    # 2026-10-27 (six agents): every send to a client is a Tier 2 card, and
    # a Tier 2 action never goes through this door.
    # The agent on Miro's Mac asking for an approved draft to post now
    # (2026-10-06: Whop takes a live link only within 30 minutes of the
    # post), through the clip's own Tier 1 auto action with every gate it
    # already has -- the same post the posting pass would make on its own
    # timing (atlas/api/clips.py, /post_now).
    "clips.publish_clip",
    "clips.publish_clip_tiktok",
    "clips.publish_clip_youtube_postiz",
})


async def run_tier1(action: str, params: dict[str, Any],
                    reason: str) -> ActionResult:
    """Run one Tier 1 action on behalf of a surface, unattended."""
    if action not in ALLOWED:
        await audit("unattended.refused", action,
                    {"why": "not on the unattended list"})
        return ActionResult(
            False, "refused: that action may not run with nobody attached")

    decision = REGISTRY.decisions.get(action)
    if decision is None or not decision.allowed:
        why = decision.reason if decision else "not in the catalog"
        await audit("unattended.refused", action, {"why": why})
        return ActionResult(False, f"refused: {why}")

    if decision.tier is not Tier.AUTO:
        await audit("unattended.refused", action,
                    {"why": f"tier {int(decision.tier)} reached the "
                            f"unattended path"})
        return ActionResult(
            False, "refused: only a Tier 1 action may run unattended")

    from .executor import ExecutionRequest, execute
    return await execute(ExecutionRequest(
        action=action, params=params, reason=reason))
