"""The Sunday plan: three experiments, three ideas, and anything he must decide.

`weekly(business)` composes and writes nothing. It asks `experiments.propose`
for one hypothesis per platform in `PLATFORMS_WITH_HOOKS` — the platforms an
experiment can actually close on, which today is YouTube alone — filters that list against what the closed
experiments already measured, asks `monetize.ideas` for the money side, and
collects any standing-rule text the judgment layer has drafted.

Two filters, both from the brief and both tested:

* **Never repeat a losing variant.** A variant a closed experiment recorded as
  `lost` is dropped from the plan, whatever the model proposes. Running it
  again is how a system that looks like it is learning stops learning.
* **At most one repeat of a winner per platform.** Re-running a winner once is
  replication; re-running it everywhere is stopping the experiment.

**Standing rules are drafted here and enacted nowhere.** At p >= 0.95 the
judgment layer produces the text of a rule in Miro's own shape. This file
carries that text to him with `enacted: False` and a note saying only he can
put it in place. CLAUDE.md is explicit that there is no runtime override and
none may be added; nothing in this package reads a standing rule back to skip
a gate, and nothing here can change a tier.

Nothing in this file writes, and nothing in it runs. `as_requests()` turns a
plan into `(action, params, reason)` triples and hands them back to the
scheduler, which puts each through `executor.execute`. That is deliberate:
the executor is the single path (invariant 1), and it is also where the audit
line, the mandatory dry run, the hourly ceiling and the `ATLAS_WATCH_ONLY`
refusal live. A planner that filed its own plan by calling the action
functions would be the one part of ATLAS with hands in week one, which is
exactly the failure CLAUDE.md's phase discipline names.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime

from ..contracts import Severity
from ..judgment import policy
from ..judgment.policy import Advice
from ..state import fetch_all
from . import experiments, monetize

log = logging.getLogger("atlas.marketing")

EXPERIMENTS_PER_WEEK = 3
IDEAS_PER_WEEK = 3

# The platforms the weekly plan files experiments on: only those whose publish
# action carries `experiment_id`, because that is what starts the 48-hour
# clock. Today that is `youtube.set_visibility` alone. An instagram or tiktok
# experiment filed now could never be attached, would never come due, and
# would sit in the ledger as a permanent `unmeasured` row behind a DEGRADED
# check nobody can clear. Instagram and TikTok join this tuple when
# `instagram.publish` and `tiktok.upload_draft` gain `experiment_id`.
# `experiments.PLATFORMS` stays wider: it is what measurement understands.
PLATFORMS_WITH_HOOKS: tuple[str, ...] = ("youtube",)

# The publish decisions whose approval record can produce a standing-rule
# candidate. Read only: `policy.predict` refuses Tier 3 before it computes
# anything, and a candidate is text, not a change.
RULE_CANDIDATES: tuple[str, ...] = (
    "youtube.set_visibility",
    "instagram.publish",
    "tiktok.upload_draft",
)

ONLY_HE_CAN = ("Drafted, not enacted: only you can put a standing rule in "
               "place, and the gate stays until you do.")


async def closed_experiments(business: str, *, weeks: int = 12) -> list[dict]:
    """Every measured experiment for this business in the trailing window."""
    try:
        rows = await fetch_all(
            """SELECT business, platform, variant, outcome, measured_at
               FROM marketing_experiments
               WHERE business = $1 AND outcome IS NOT NULL
                 AND at > now() - ($2 || ' weeks')::interval
               ORDER BY measured_at DESC NULLS LAST LIMIT 100""",
            business, str(weeks))
    except Exception as exc:  # noqa: BLE001
        log.warning("could not read closed experiments for %s: %s",
                    business, exc)
        return []
    return [dict(r) for r in rows]


async def standing_rule_candidates() -> list[dict]:
    """Rule text the judgment layer drafted, for Miro to confirm once.

    A candidate appears only at `Advice.STANDING_RULE` — p >= 0.95 over at
    least twenty decisions, and only for a reversible action. The layer
    decides that; this file only carries the words.
    """
    out: list[dict] = []
    for action in RULE_CANDIDATES:
        try:
            pred = await policy.predict(action, int(Severity.NORMAL))
        except Exception as exc:  # noqa: BLE001
            log.warning("no judgment read for %s: %s", action, exc)
            continue
        if pred.advice is not Advice.STANDING_RULE:
            continue
        out.append({
            "action": action,
            "text": f"{policy.standing_rule_text(action, pred)} [{action}]",
            "enacted": False,
            "note": ONLY_HE_CAN,
        })
    return out


def _admissible(proposal: dict, lost: set[str], won: set[str],
                repeats: dict[str, int]) -> tuple[bool, str]:
    variant = proposal.get("variant", "")
    if variant in lost:
        return False, f"{variant!r} already lost on a closed experiment"
    if variant in won:
        platform = proposal.get("platform", "")
        if repeats.get(platform, 0) >= 1:
            return False, (f"{variant!r} is a winner already repeated once on "
                           f"{platform}")
        repeats[platform] = repeats.get(platform, 0) + 1
    return True, ""


async def weekly(business: str) -> dict:
    """Next week's plan for one business. Writes nothing."""
    history = await closed_experiments(business)
    lost = {r["variant"] for r in history
            if r.get("outcome") == "lost" and r.get("variant")}
    won = {r["variant"] for r in history
           if r.get("outcome") == "won" and r.get("variant")}

    chosen: list[dict] = []
    skipped: list[str] = []
    repeats: dict[str, int] = {}
    for platform in PLATFORMS_WITH_HOOKS:
        if len(chosen) >= EXPERIMENTS_PER_WEEK:
            break
        proposal, reason = await experiments.propose(business, platform)
        if proposal is None:
            skipped.append(f"{platform}: {reason}")
            continue
        ok, why = _admissible(proposal, lost, won, repeats)
        if not ok:
            skipped.append(f"{platform}: {why}")
            continue
        chosen.append(proposal)

    money = await monetize.ideas(business, limit=IDEAS_PER_WEEK)
    rules = await standing_rule_candidates()
    return {
        "business": business,
        "experiments": chosen,
        "skipped": skipped,
        "ideas": money.get("ideas", []),
        "ideas_note": money.get("note", ""),
        "standing_rules": rules,
    }


def as_requests(plan: dict) -> list[tuple[str, dict, str]]:
    """A plan, turned into `(action, params, reason)` triples.

    This package plans; it does not run. Nothing here may import the executor,
    so the triples go back to the scheduler, which puts every one of them
    through `executor.execute` — the single path, with its audit line, its
    dry run, its rate limit and its watch-only refusal. A plan that filed
    itself by calling the action functions directly would be the one corner of
    ATLAS with hands that `ATLAS_WATCH_ONLY` does not reach.
    """
    out: list[tuple[str, dict, str]] = []
    for proposal in plan.get("experiments", []):
        params = {
            "business": proposal["business"],
            "platform": proposal["platform"],
            "hypothesis": proposal["hypothesis"],
            "variant": proposal["variant"],
            "draft_kind": proposal["draft_kind"],
            "draft_source": proposal["draft_source"],
            "metric": proposal.get("metric"),
            "baseline": proposal.get("baseline"),
        }
        out.append(("marketing.propose_experiment", params,
                    f"weekly marketing plan for {proposal['business']}"))
    for idea in plan.get("ideas", []):
        params = {
            "business": idea["business"],
            "idea": idea["idea"],
            "mechanism": idea["mechanism"],
            "evidence": idea["evidence"],
            "requires": idea["requires"],
            "est_note": idea["est_note"],
            "score": idea["score"],
        }
        if idea.get("background"):
            # Perplexity's reading (sources, or why there are none), stored
            # beside the evidence — never counted as evidence.
            params["background"] = idea["background"]
        out.append(("marketing.propose_idea", params,
                    f"weekly monetization review for {idea['business']}"))
    return out


@dataclass(frozen=True, slots=True)
class Filing:
    """How the last weekly filing went: what the executor accepted, what it
    refused, and why. Refusals are silent by construction — the executor
    returns them to the scheduler and nobody else — so they are counted here
    for the brief to say out loud."""

    filed: int
    refused: int
    reasons: tuple[str, ...]
    at: datetime | None


_last_filing = Filing(0, 0, (), None)


def record_filing(*, filed: int, refused: int, reasons: list[str],
                  at: datetime | None) -> None:
    """Called by the scheduler after it has put the plan through the
    executor. Process memory only: a restart forgets it, and the brief then
    says nothing rather than something stale."""
    global _last_filing
    _last_filing = Filing(filed, refused, tuple(dict.fromkeys(reasons)), at)


def last_filing() -> Filing:
    return _last_filing


async def plan_all(businesses: tuple[str, ...] = monetize.BUSINESSES
                   ) -> list[dict]:
    """Every business's plan for the week. Still writes nothing."""
    plans: list[dict] = []
    for business in businesses:
        try:
            plans.append(await weekly(business))
        except Exception as exc:  # noqa: BLE001
            log.warning("weekly plan for %s failed: %s", business, exc)
    return plans
