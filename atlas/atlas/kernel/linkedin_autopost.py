"""The Vault LinkedIn page's daily post, published on its own, through the
one path that acts.

the owner, 2026-10-11: post the Vault page's approved posts automatically "the
way reels already do" (memory approval-policy: the tier is set in code,
never by approving cards at runtime). This drives it the way
`kernel.reels.post_or_card` drives a reel:

  the page's gate (`modules.linkedin.publish.autopublish_refusal`) ->
  `linkedin.autopublish_post` through the executor -> a Slack receipt in
  the page's channel, or the Tier 2 `linkedin.publish_post` card with
  "Held for you: <reason>" first.

Nothing to post today (nothing approved, not a posting day, a post already
out today) is silence, as it always was. Outside the bank's posting hours,
while the LinkedIn browser is busy, or on a hold (watch-only, a pause, an
hourly ceiling) the post waits for a later tick -- no card. Any other
refusal is the card, under the same title, and so the same dedup key, as
the daily signal used to raise, so one card a day at most. A post that was
clicked but not seen on the page (`posted_unverified`) is an alert, never a
second card: the entry is out of the gate and a person has to look.

Since 2026-10-12 (the owner: "whatever you think should be done should be
automatically approved"), the same tick reshares that post to the owner's
profile through `linkedin.autoreshare_post` (`reshare_tick`), the same way:
a hold waits, any other refusal is the Tier 2 `linkedin.reshare_post` card.
"""
from __future__ import annotations

import logging
import os
from datetime import timedelta

log = logging.getLogger("atlas.linkedin_autopost")


def interval() -> timedelta:
    try:
        return timedelta(minutes=max(
            1, int(os.getenv("LINKEDIN_AUTOPUBLISH_TICK_MIN", "30"))))
    except ValueError:
        return timedelta(minutes=30)


async def _notify(title: str, body: str, topic: str, kind: str) -> None:
    from .comms import Outbound, notify
    await notify(Outbound(title=title, body=body, topic=topic, kind=kind))


async def tick() -> int:
    """One pass over the pages that post themselves. Returns cards raised."""
    from ..modules import linkedin
    from ..modules.linkedin import publish
    from ..modules.linkedin.browser import lock
    from ..modules.linkedin.store import load, today
    from .executor import ExecutionRequest, execute
    from .lead_followup import _card, _held
    raised = 0
    for page in publish.AUTOPUBLISH_PAGES:
        if not publish.autopublish_owns(page):
            continue                    # off or inactive: the daily signal's card
        try:
            state = load()
        except Exception as exc:                        # noqa: BLE001
            log.info("linkedin autopost: no state (%s)", type(exc).__name__)
            return raised
        gated, _why = publish.gate(state, page)
        if gated is None:
            continue                    # nothing to post today
        entry, why, kind = publish.autopublish_refusal(state, page)
        if kind in ("none", "wait"):
            log.info("linkedin autopost %s: %s", page, why)
            continue
        if lock().locked():
            log.info("linkedin autopost: the browser is busy; tried later")
            continue
        entry_id = str((entry or gated).get("id"))
        name = linkedin.PAGES[page]["name"]
        reason = f"the next approved post for the {name} page ({entry_id})"
        made = await execute(ExecutionRequest(
            "linkedin.autopublish_post", {"page": page, "entry_id": entry_id},
            reason=reason))
        if _held(made):
            log.info("linkedin autopost held: %s", made.detail)
            continue
        if made.ok:
            await _notify(
                f"Posted {entry_id} to the {name} LinkedIn page on its own",
                f"{made.detail}.\n\nIt passed every automatic check (your rule, "
                "2026-10-11). LINKEDIN_AUTOPUBLISH=off goes back to a card a day; "
                "the action's rollback cannot delete a live post (Tier 3) -- it "
                "pauses automatic posting for the page.", page, "receipt")
            continue
        if (made.evidence or {}).get("posted_unverified"):
            await _notify(f"{name} LinkedIn post {entry_id}: check it by hand",
                          made.detail, page, "alert")
            continue
        title, body = linkedin.publish_card(page, gated, today())
        raised += await _card(
            "linkedin.publish_post", {"page": page, "entry_id": entry_id},
            made.detail, body, {"page": page, "entry": entry_id},
            f"linkedin.publish_post@{title}")
    return raised + await reshare_tick()


async def reshare_tick() -> int:
    """Reshare to the owner's profile the Vault post ATLAS published today
    (the owner, 2026-10-12; memory approval-policy), through
    `linkedin.autoreshare_post`. Outside the posting hours, while the browser
    is busy, or on a hold, it waits for a later tick; any other refusal is
    the Tier 2 `linkedin.reshare_post` card under the title the signal uses,
    so one card at most. A reshare clicked but not seen on his activity is
    an alert, never a second card. Returns cards raised."""
    from ..modules.linkedin import PAGES, publish
    from ..modules.linkedin.browser import lock
    from ..modules.linkedin.store import load
    from .executor import ExecutionRequest, execute
    from .lead_followup import _card, _held
    raised = 0
    for page in publish.AUTOPUBLISH_PAGES:
        if not publish.autoreshare_owns(page):
            continue
        try:
            state = load()
        except Exception as exc:                        # noqa: BLE001
            log.info("linkedin autoreshare: no state (%s)", type(exc).__name__)
            return raised
        entry = publish.autoreshare_candidate(state, page)
        if entry is None:
            continue
        entry_id = str(entry.get("id"))
        _e, why, kind = publish.autoreshare_refusal(state, page, entry_id)
        if kind == "wait":
            log.info("linkedin autoreshare %s: %s", page, why)
            continue
        if lock().locked():
            log.info("linkedin autoreshare: the browser is busy; tried later")
            continue
        name = PAGES[page]["name"]
        made = await execute(ExecutionRequest(
            "linkedin.autoreshare_post", {"page": page, "entry_id": entry_id},
            reason=f"the {name} post ATLAS published today ({entry_id}), to "
                   f"the owner's profile"))
        if _held(made):
            log.info("linkedin autoreshare held: %s", made.detail)
            continue
        if made.ok:
            await _notify(
                f"Reshared {entry_id} to your LinkedIn profile on its own",
                f"{made.detail}.\n\nIt passed every automatic check (your rule, "
                "2026-10-12). LINKEDIN_AUTORESHARE=off goes back to a card; the "
                "rollback cannot delete a reshare (Tier 3) -- it pauses automatic "
                "resharing.", page, "receipt")
            continue
        if (made.evidence or {}).get("reshare_unverified"):
            await _notify(f"{name} reshare of {entry_id}: check your profile by hand",
                          made.detail, page, "alert")
            continue
        title = f"Reshare {entry_id} to your profile"
        raised += await _card(
            "linkedin.reshare_post", {"page": page, "entry_id": entry_id},
            made.detail,
            f"{entry_id} posted itself on the {name} page; it did not reshare "
            f"itself.\n{entry.get('activity_url')}",
            {"page": page, "entry": entry_id}, f"linkedin.reshare_post@{title}")
    return raised
