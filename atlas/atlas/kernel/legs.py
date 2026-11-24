"""Legs: a registered action's effect performed inside another action's run
(reliability plan, RA-C2).

`content.publish_reel` / `content.autopublish_reel` post one reel to three
platforms, and the Instagram leg calls `instagram.publish`'s function
directly. That call is inside an executor run -- the parent's tier, dry
run, pause, rate limits and audit all applied -- but `instagram.publish`'s
OWN gates did not: its `max_per_hour=2`, its module's pause, and whether it
is registered and allowed at all. So a reel could be the third Instagram
post of the hour.

This does not add a path around the executor; it makes the leg honour the
same gates the executor would apply to the leg's own action, sharing the
executor's limiter rather than keeping a second one:

  * **Tier.** Which parents may perform which leg is fixed here, in code
    (`PARENTS`), like every tier (invariant 3). `content.publish_reel` is
    Tier 2 -- the owner's approval of the card covers every leg on it.
    `content.autopublish_reel` is Tier 1 by the owner's settled decision
    (2026-09-27/28: reels that pass every check post themselves), and that
    decision is what covers its Instagram leg. Any other caller is refused.
  * **Registered and allowed.** The leg's action must be in the registry
    with an allowed decision: a switched-off `instagram` module means no
    Instagram leg.
  * **Pause.** `/atlas pause instagram` stops the leg, as it would stop
    `instagram.publish`. Unreadable pause state is a stop.
  * **Rate limit.** The executor's own windows, read and spent under the
    leg's action name and module (`executor._over_limit` / `_recent`), so
    Instagram posts count once whichever path made them. The global window
    is not spent again: the parent's run already spent it.
  * **Audit.** `leg.start` before the call and `leg.finish` after it, naming
    the parent, in the same hash chain; `leg.refused` when a gate says no.
"""
from __future__ import annotations

import time
from typing import Any

from . import executor
from .audit import audit
from .registry import REGISTRY

PARENTS: dict[str, frozenset[str]] = {
    "instagram.publish": frozenset({"content.publish_reel",
                                    "content.autopublish_reel",
                                    # the missing Instagram leg of a reel that
                                    # already posted elsewhere (Tier 1, the
                                    # same settled decision; content.legsync)
                                    "content.post_missing_leg",
                                    # the Academy store's promo reel on the
                                    # clips account (the owner, 2026-10-05)
                                    "clips.publish_promo"}),
}


def _module(action: str) -> str:
    return action.split(".", 1)[0]


# A reel's TikTok and Facebook legs have no action of their own, so no
# executor window: each gets this one, spent by every reel post to that
# platform (`content.crosspost`), read by the leg sync before a catch-up.
PLATFORM_MAX_PER_HOUR = 2


def platform_over_limit(platform: str) -> str | None:
    """Read-only: why a reel may not post to `platform` again this hour."""
    if executor._over_limit(f"reel_leg:{platform}", PLATFORM_MAX_PER_HOUR):
        return (f"{platform} has taken {PLATFORM_MAX_PER_HOUR} reel post(s) this "
                f"hour (its hourly allowance is spent)")
    return None


def spend_platform(platform: str) -> None:
    executor._recent[f"reel_leg:{platform}"].append(time.monotonic())


def over_limit(leg: str) -> str | None:
    """Read-only: why `leg` may not run this hour, or None. Spends nothing,
    so a check that holds a reel back does not use up the allowance."""
    spec = REGISTRY.actions.get(leg)
    if spec is not None and executor._over_limit(leg, spec.max_per_hour):
        return (f"{leg} has already run {spec.max_per_hour} time(s) this hour "
                f"(its own max_per_hour)")
    module = _module(leg)
    if executor._over_limit(f"module:{module}", executor.MODULE_MAX_PER_HOUR):
        return (f"the {module} module has already run "
                f"{executor.MODULE_MAX_PER_HOUR} actions this hour")
    return None


async def gate(leg: str, parent: str, *, account: str | None = None,
               **context: Any) -> str | None:
    """None when `leg` may run inside `parent` now; otherwise why not, and
    the refusal is audited. `account`: the account the leg acts as, so a
    `module:account` pause (a refused login, kernel.pause) stops it too."""
    from . import pause
    why: str | None = None
    decision = REGISTRY.decisions.get(leg)
    if parent not in PARENTS.get(leg, frozenset()):
        why = f"{parent} is not a parent {leg} may run under"
    elif REGISTRY.actions.get(leg) is None or decision is None:
        why = f"{leg} is not registered (its module is off)"
    elif not decision.allowed:
        why = f"{leg} is refused: {decision.reason}"
    else:
        try:
            stopped = await pause.active_for(_module(leg), account)
        except pause.PauseUnreadable as exc:
            why = f"the pause state could not be read ({exc})"
        else:
            if stopped is not None:
                why = stopped.says()
            else:
                why = over_limit(leg)
    if why is not None:
        await audit("leg.refused", leg, {"parent": parent, "why": why,
                                         "account": account, **context})
    return why


async def start(leg: str, parent: str, **context: Any) -> None:
    """Audit the attempt, then spend the leg's windows -- in that order and
    before the call, exactly as `executor.execute` does for an action."""
    await audit("leg.start", leg, {"parent": parent, **context})
    now = time.monotonic()
    for key in (leg, f"module:{_module(leg)}"):
        executor._recent[key].append(now)


async def finish(leg: str, parent: str, ok: bool, detail: str,
                 **context: Any) -> None:
    await audit("leg.finish", leg, {"parent": parent, "ok": ok,
                                    "detail": detail[:300], **context})
