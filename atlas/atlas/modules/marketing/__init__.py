"""marketing — the experiment ledger and the money ledger, and nothing public.

What it watches:
  * `experiments_overdue` — experiments whose 48-hour window has passed with
    no measured outcome: still open, or closed `unmeasured` in the last seven
    days. A learning loop
    that stopped measuring looks exactly like a learning loop that has nothing
    to report, so this is DEGRADED, not quiet.
  * `playbook_size` — how many lessons the playbook holds. INFO: a number for
    the brief, never a finding.

What it may do — three actions, all Tier AUTO, all reversible, none of which
touches an outside party:
  * `marketing.propose_experiment` — writes one `marketing_experiments` row
    and drafts the content for it by calling `content.draft`'s own function.
    Rollback drops both: the draft is marked `dropped` and the experiment row
    is marked `unmeasured` with a note saying it was withdrawn.
  * `marketing.propose_idea` — writes one `monetization_ideas` row. Rollback
    marks it `dropped`.
  * `marketing.record_outcome` — closes an experiment with a measured outcome
    and writes the lesson into the memory namespace `playbook`. Rollback
    removes the memory row it just wrote and reopens the experiment.

**Publishing is deliberately not an action here.** `youtube.upload`,
`youtube.set_visibility`, `instagram.publish` and `tiktok.upload_draft`
already exist, all Tier ASK, because a post under the owner's name is his decision
every time. Nothing in this module can reach a feed, and nothing in
`atlas/kernel/marketing` can either.

**Nothing here spends.** A monetization idea that would cost money to try is
recorded with `requires = "money out — Tier 3, you decide"`. It is a row he
reads, never an action anything can run.

Endpoints this assumes: none. It reads and writes Postgres
(`marketing_experiments`, `monetization_ideas`, `content_drafts`, `memories`)
and calls `content.draft`, which calls an LLM. There is no third-party API
here for the owner to correct.
"""
from __future__ import annotations

import json
import logging
from datetime import UTC, datetime, timedelta

from ...kernel.contracts import (
    Action,
    ActionResult,
    Check,
    Health,
    Observation,
    Severity,
    Tier,
)
from ...kernel.marketing.cycle import PLATFORMS_WITH_HOOKS
from ...kernel.marketing.experiments import PLATFORMS
from ...kernel.marketing.playbook import NAMESPACE as PLAYBOOK
from ...kernel.memory import Kind, remember
from ...kernel.state import execute_sql, fetch_all, fetch_one
from ..content.drafts import KINDS as DRAFT_KINDS
from ..content.drafts import draft as content_draft

log = logging.getLogger("atlas.marketing")

# `course` is the owner's own course (atlas/modules/course): its experiments are
# filed by `course.propose_post`, one per post, and closed by `record_outcome`
# like every other; an idea for it is recorded here like any business's.
BUSINESSES: tuple[str, ...] = ("exampleco", "agency", "course")
OUTCOMES: tuple[str, ...] = ("won", "lost", "flat", "unmeasured")
IMPORTANCE: dict[str, float] = {
    "won": 0.75, "lost": 0.75, "flat": 0.5, "unmeasured": 0.2,
}

# The hourly ceiling on filing. The weekly plan files up to
# len(BUSINESSES) x 3 experiments and as many ideas in one pass; a ceiling
# below that refuses the tail of the plan every Sunday.
FILING_PER_HOUR = 15

# Past this many overdue experiments the loop has plainly stopped measuring.
# One is a platform hiccup; several is a broken collector, and a broken
# collector reading as healthy is the worst bug this system can have.
OVERDUE_DEGRADED_AT = 1

# How long an experiment closed `unmeasured` keeps the check DEGRADED. Past
# this it is history rather than a live degradation, so the check clears once
# the cause (a missing analytics scope, say) is fixed — and a fresh unmeasured
# closure makes it DEGRADED again.
UNMEASURED_LIVE_FOR = timedelta(days=7)


# ------------------------------------------------------------------ checks

def _still_live(row, now: datetime) -> bool:
    """Open past its window, or closed `unmeasured` within the last week."""
    if row["outcome"] is None:
        return True
    at = row["measured_at"]
    return (row["outcome"] == "unmeasured" and at is not None
            and now - at <= UNMEASURED_LIVE_FOR)


def _unmeasured_reason(lesson: str | None) -> str:
    """The reason in `… : unmeasured (reason)`, the shape `lesson_text` writes."""
    text = lesson or "no reason recorded"
    _, sep, tail = text.partition(": unmeasured (")
    return tail[:-1] if sep and tail.endswith(")") else text


async def check_experiments_overdue() -> Observation:
    """Posted experiments whose window has closed without a measurement.

    Two kinds count: rows still open past `measure_at`, and rows closed as
    `unmeasured` in the last seven days. Counting only the first let a
    permanently broken read — a missing analytics scope, a baseline that never
    fills — close every row `unmeasured` and read OK. Counting the second
    forever would never clear. `measure_at IS NOT NULL` keeps experiments that
    were withdrawn or never drafted (they were never posted) out of it.
    """
    now = datetime.now(UTC)
    try:
        rows = await fetch_all(
            """SELECT outcome, lesson, measured_at FROM marketing_experiments
               WHERE measure_at IS NOT NULL AND measure_at < $1
                 AND (outcome IS NULL
                      OR (outcome = 'unmeasured' AND measured_at >= $2))""",
            now, now - UNMEASURED_LIVE_FOR)
    except Exception as exc:                            # noqa: BLE001
        return Observation("experiments_overdue", Health.UNKNOWN,
                           detail=f"could not count experiments: {exc}")
    live = [r for r in rows if _still_live(r, now)]
    n = len(live)
    if n < OVERDUE_DEGRADED_AT:
        return Observation("experiments_overdue", Health.OK, 0.0,
                           "experiments", "every posted experiment is measured")
    unmeasured = [r for r in live if r["outcome"] == "unmeasured"]
    detail = (f"{n} experiment(s) past their 48-hour window without a "
              f"measurement ({n - len(unmeasured)} still open, "
              f"{len(unmeasured)} closed unmeasured in the last 7 days)")
    if unmeasured:
        newest = max(unmeasured, key=lambda r: r["measured_at"])
        detail += (f"; newest unmeasured: {_unmeasured_reason(newest['lesson'])}"
                   f", read {newest['measured_at']:%H:%M}")
    return Observation(
        "experiments_overdue", Health.DEGRADED, float(n), "experiments",
        detail + " — the playbook is not learning from them")


async def check_playbook_size() -> Observation:
    """How much the playbook actually holds. A number, not a verdict."""
    try:
        row = await fetch_one(
            "SELECT count(*) AS n FROM memories WHERE namespace = $1",
            PLAYBOOK)
    except Exception as exc:                            # noqa: BLE001
        return Observation("playbook_size", Health.UNKNOWN,
                           detail=f"could not count playbook lessons: {exc}")
    if row is None:
        return Observation("playbook_size", Health.UNKNOWN,
                           detail="the playbook count query returned nothing")
    n = int(row["n"])
    return Observation("playbook_size", Health.OK, float(n), "lessons",
                       f"{n} lesson(s) in the playbook")


# ----------------------------------------------------------------- actions

def _reject(business: str, platform: str, draft_kind: str) -> str | None:
    if business not in BUSINESSES:
        return (f"unknown business {business!r}; must be one of "
                f"{', '.join(BUSINESSES)}")
    if platform not in PLATFORMS:
        return (f"unknown platform {platform!r}; must be one of "
                f"{', '.join(PLATFORMS)}")
    from ...kernel.registry import REGISTRY
    if platform not in REGISTRY.modules:
        # Switched off (ATLAS_DISABLED_MODULES) or never set up: nothing
        # would ever post it. 2026-09-27, youtube off by the owner's word ("we
        # don't need YouTube"), the marketing agent still filed twelve
        # YouTube experiments overnight, each drafting a "short script"
        # that was the weekly ops report reworded.
        return (f"{platform} is switched off in ATLAS, so an experiment on "
                f"it could never be posted or measured")
    if platform not in PLATFORMS_WITH_HOOKS:
        # It could be measured, but nothing would ever attach a post to it,
        # so it would never come due and never close.
        return (f"{platform} has no experiment hook yet; only "
                f"{', '.join(PLATFORMS_WITH_HOOKS)} can close an experiment")
    if draft_kind not in DRAFT_KINDS:
        return (f"unknown draft kind {draft_kind!r}; must be one of "
                f"{', '.join(DRAFT_KINDS)}")
    return None


async def propose_experiment(business: str, platform: str, hypothesis: str,
                             variant: str, *, draft_kind: str,
                             draft_source: str, metric: str | None = None,
                             baseline: float | None = None) -> ActionResult:
    """One experiment row plus the draft it will publish, if he approves it.

    The draft is written by `content.draft`'s own function rather than a copy
    of it, so there is exactly one place that decides what a draft looks like.
    Nothing here publishes: the draft sits in `content_drafts` until the owner
    answers a Tier ASK card on the platform module.
    """
    bad = _reject(business, platform, draft_kind)
    if bad is not None:
        return ActionResult(False, bad)

    row = await fetch_one(
        """INSERT INTO marketing_experiments
             (business, platform, hypothesis, variant, metric, baseline)
           VALUES ($1,$2,$3,$4,$5,$6) RETURNING id""",
        business, platform, hypothesis, variant, metric, baseline)
    if row is None:
        return ActionResult(False, "the experiment row was not written")
    experiment_id = int(row["id"])

    drafted = await content_draft(draft_kind, draft_source)
    if not drafted.ok:
        # The experiment exists but has nothing to publish. Close it now with
        # the reason rather than leaving a row that will go overdue and read
        # as a broken measurement.
        await execute_sql(
            """UPDATE marketing_experiments
               SET outcome = 'unmeasured', lesson = $2 WHERE id = $1""",
            experiment_id, f"no draft was written: {drafted.detail}")
        return ActionResult(False, f"could not draft the variant: "
                                   f"{drafted.detail}")

    draft_id = int(drafted.evidence.get("id", 0)) or None
    await execute_sql(
        "UPDATE marketing_experiments SET draft_id = $2 WHERE id = $1",
        experiment_id, draft_id)
    return ActionResult(
        True, f"{business} · {platform}: experiment #{experiment_id} — "
              f"{hypothesis!r} via {variant!r}, drafted as #{draft_id}",
        evidence={"experiment_id": experiment_id, "draft_id": draft_id,
                  "variant": variant, "metric": metric},
        rollback_token=f"{experiment_id}:{draft_id}")


async def propose_experiment_dry(business: str, platform: str,
                                 hypothesis: str, variant: str, *,
                                 draft_kind: str, draft_source: str,
                                 metric: str | None = None,
                                 baseline: float | None = None
                                 ) -> ActionResult:
    bad = _reject(business, platform, draft_kind)
    if bad is not None:
        return ActionResult(False, bad)
    return ActionResult(
        True, f"would record a {platform} experiment for {business} "
              f"({variant!r}) and draft a {draft_kind} from {draft_source!r}")


async def propose_experiment_rollback(token: str) -> ActionResult:
    """Both halves, because the action wrote both."""
    experiment_id, _, draft_id = token.partition(":")
    await execute_sql(
        """UPDATE marketing_experiments
           SET outcome = 'unmeasured', lesson = 'withdrawn before publishing'
           WHERE id = $1""", int(experiment_id))
    if draft_id and draft_id != "None":
        await execute_sql(
            "UPDATE content_drafts SET status = 'dropped' WHERE id = $1",
            int(draft_id))
    return ActionResult(
        True, f"experiment {experiment_id} withdrawn and draft {draft_id} "
              f"marked dropped")


# The evidence-JSON key Perplexity's background reading is filed under.
BACKGROUND_KEY = "background_reading"


async def propose_idea(business: str, idea: str, mechanism: str, *,
                       evidence: dict | None = None, requires: str = "",
                       est_note: str = "", score: float | None = None,
                       background: dict | None = None) -> ActionResult:
    """One monetization idea, with the measured facts it rests on.

    An idea with no evidence is refused. That is the whole discipline of this
    row: it exists to carry facts with the time they were read, and an empty
    `evidence` block would make it a guess with a database id.
    """
    if business not in BUSINESSES:
        return ActionResult(
            False, f"unknown business {business!r}; must be one of "
                   f"{', '.join(BUSINESSES)}")
    if not evidence:
        return ActionResult(
            False, "an idea with no measured evidence is a guess; refusing "
                   "to record one")
    undated = [k for k, v in evidence.items()
               if not isinstance(v, dict) or not v.get("observed_at")]
    if undated:
        return ActionResult(
            False, f"these facts carry no observed_at: {', '.join(undated)}")

    stored = dict(evidence)
    if isinstance(background, dict) and background:
        # Perplexity's background reading (Task 42): its sources, or why
        # there are none ("research timed out"). Kept in the evidence JSON
        # under its own key, added after the measured-facts check and with
        # no `observed_at`, so it is never read — or scored — as a
        # measurement.
        stored[BACKGROUND_KEY] = {k: background.get(k) for k in
                                  ("status", "sources", "read_at", "detail")}
    row = await fetch_one(
        """INSERT INTO monetization_ideas
             (business, idea, mechanism, evidence, requires, est_note, score)
           VALUES ($1,$2,$3,$4,$5,$6,$7) RETURNING id""",
        business, idea, mechanism, json.dumps(stored, default=str),
        requires, est_note, score)
    if row is None:
        return ActionResult(False, "the monetization row was not written")
    idea_id = int(row["id"])
    return ActionResult(
        True, f"{business}: {idea} ({mechanism}) — requires: "
              f"{requires or 'nothing stated'}",
        evidence={"id": idea_id, "mechanism": mechanism},
        rollback_token=str(idea_id))


async def propose_idea_dry(business: str, idea: str, mechanism: str, *,
                           evidence: dict | None = None, requires: str = "",
                           est_note: str = "", score: float | None = None,
                           background: dict | None = None) -> ActionResult:
    if business not in BUSINESSES:
        return ActionResult(
            False, f"unknown business {business!r}; must be one of "
                   f"{', '.join(BUSINESSES)}")
    if not evidence:
        return ActionResult(False, "an idea with no measured evidence is a "
                                   "guess; refusing to record one")
    return ActionResult(True, f"would record {idea!r} for {business}")


async def propose_idea_rollback(token: str) -> ActionResult:
    """Dropped, not deleted — the row stays for the record, out of the way."""
    await execute_sql(
        "UPDATE monetization_ideas SET status = 'dropped' WHERE id = $1",
        int(token))
    return ActionResult(True, f"idea {token} marked dropped")


async def record_outcome(experiment_id: int, outcome: str, lesson: str, *,
                         observed: float | None = None,
                         baseline: float | None = None,
                         metric: str | None = None) -> ActionResult:
    """Close an experiment and write what it taught, into memory.

    This is the whole of "self-improving": one semantic memory in the
    `playbook` namespace, which the next proposal reads back. It changes no
    tier, no prompt and no gate — the only thing it changes is what the model
    is shown next week.
    """
    if outcome not in OUTCOMES:
        return ActionResult(
            False, f"unknown outcome {outcome!r}; must be one of "
                   f"{', '.join(OUTCOMES)}")
    if not lesson.strip():
        return ActionResult(False, "an outcome with no lesson teaches nothing")

    # The playbook filters and ranks on business, platform and variant, so
    # the lesson carries them in `meta`. They come from the row itself rather
    # than from the caller, so the reader and the ledger cannot disagree.
    experiment = await fetch_one(
        """SELECT business, platform, variant FROM marketing_experiments
           WHERE id = $1""", int(experiment_id))
    if experiment is None:
        return ActionResult(False, f"no experiment #{experiment_id} to close")

    await execute_sql(
        """UPDATE marketing_experiments
           SET outcome = $2, lesson = $3, observed = $4, baseline = $5,
               metric = COALESCE($6, metric), measured_at = now()
           WHERE id = $1""",
        int(experiment_id), outcome, lesson, observed, baseline, metric)

    # `unmeasured` goes into the playbook too, at low weight. A run of them is
    # the signal that the loop stopped working, and keeping them out hid that
    # from every prompt that followed. `playbook.rank` already weights an
    # unmeasured lesson at 0.15 of a win.
    memory_id = await remember(
        Kind.SEMANTIC, PLAYBOOK, lesson,
        meta={"experiment_id": int(experiment_id), "outcome": outcome,
              "business": experiment["business"],
              "platform": experiment["platform"],
              "variant": experiment["variant"],
              "observed": observed, "baseline": baseline, "metric": metric},
        importance=IMPORTANCE[outcome])

    return ActionResult(
        True, f"experiment #{experiment_id}: {outcome} — {lesson}",
        evidence={"experiment_id": int(experiment_id), "outcome": outcome,
                  "memory_id": memory_id},
        rollback_token=f"{experiment_id}:{memory_id}")


async def record_outcome_dry(experiment_id: int, outcome: str, lesson: str, *,
                             observed: float | None = None,
                             baseline: float | None = None,
                             metric: str | None = None) -> ActionResult:
    if outcome not in OUTCOMES:
        return ActionResult(
            False, f"unknown outcome {outcome!r}; must be one of "
                   f"{', '.join(OUTCOMES)}")
    return ActionResult(
        True, f"would close experiment #{experiment_id} as {outcome} and "
              f"write one lesson to the playbook")


async def record_outcome_rollback(token: str) -> ActionResult:
    """Undo both writes this action made, in the order that leaves no orphan.

    The memory row goes first: it is the row this action created seconds ago,
    and leaving it while reopening the experiment would teach the next
    proposal from a measurement that has been withdrawn. This is a rollback of
    ATLAS's own write, not a deletion capability — nothing else in the system
    can reach it, and it is reachable only through the rollback token this
    action returned.
    """
    experiment_id, _, memory_id = token.partition(":")
    if memory_id and memory_id != "None":
        await execute_sql("DELETE FROM memories WHERE id = $1", int(memory_id))
    await execute_sql(
        """UPDATE marketing_experiments
           SET outcome = NULL, lesson = NULL, observed = NULL,
               measured_at = NULL
           WHERE id = $1""", int(experiment_id))
    return ActionResult(
        True, f"experiment {experiment_id} reopened and its playbook lesson "
              f"withdrawn")


class MarketingModule:
    name = "marketing"
    title = "Marketing"
    requires_env = ()
    optional_env = ()
    setup_note = ""

    async def setup(self) -> None: ...
    async def teardown(self) -> None: ...

    def checks(self):
        return (
            Check("experiments_overdue", check_experiments_overdue,
                  timedelta(hours=6), Severity.NORMAL,
                  "experiments past their measurement window",
                  baseline_sigma=None),
            Check("playbook_size", check_playbook_size, timedelta(days=1),
                  Severity.INFO, "lessons held in the playbook",
                  baseline_sigma=None),
        )

    def actions(self):
        return (
            Action(
                name="marketing.propose_experiment",
                tier=Tier.AUTO,     # a row and a draft; nothing leaves ATLAS
                summary="Record one marketing experiment and draft the post "
                        "it would publish",
                run=propose_experiment, dry_run=propose_experiment_dry,
                rollback=propose_experiment_rollback,
                params={
                    "business": f"one of: {', '.join(BUSINESSES)}",
                    "platform": f"one of: {', '.join(PLATFORMS_WITH_HOOKS)}",
                    "hypothesis": "what this experiment would show, "
                                  "falsifiable at 48 hours",
                    "variant": "the concrete post kind, hook or format",
                    "draft_kind": f"one of: {', '.join(DRAFT_KINDS)}",
                    "draft_source": "voice:<n> | weekly | slack:<thread_ts> | "
                                    "note:<text>",
                    "metric": "the metric it moves, e.g. views_48h",
                    "baseline": "the measured baseline, or omitted when none "
                                "was measured",
                },
                max_per_hour=FILING_PER_HOUR,
            ),
            Action(
                name="marketing.propose_idea",
                tier=Tier.AUTO,     # a row he reads; it spends nothing
                summary="Record one monetization idea and the measured facts "
                        "it rests on",
                run=propose_idea, dry_run=propose_idea_dry,
                rollback=propose_idea_rollback,
                params={
                    "business": f"one of: {', '.join(BUSINESSES)}",
                    "idea": "what the idea is, in one line",
                    "mechanism": "affiliate | sponsorship | ads | product | "
                                 "service | partner_program | paid_acquisition",
                    "evidence": "measured facts, each with observed_at",
                    "requires": "what only the owner can do — or 'money out — "
                                "Tier 3, you decide'",
                    "est_note": "prose only; never an invented revenue figure",
                    "score": "evidence strength times how little it needs "
                             "from him",
                    "background": "optional: Perplexity background reading "
                                  "{status, sources, read_at} — filed beside "
                                  "the evidence, never counted as it",
                },
                max_per_hour=FILING_PER_HOUR,
            ),
            Action(
                name="marketing.record_outcome",
                tier=Tier.AUTO,     # closes a row, writes one memory
                summary="Close an experiment with a measured outcome and "
                        "write the lesson to the playbook",
                run=record_outcome, dry_run=record_outcome_dry,
                rollback=record_outcome_rollback,
                params={
                    "experiment_id": "marketing_experiments.id",
                    "outcome": f"one of: {', '.join(OUTCOMES)}",
                    "lesson": "the line that goes into the playbook, with the "
                              "measured delta and the time it was read",
                    "observed": "the number read from the platform",
                    "baseline": "the number it was compared against",
                    "metric": "which metric was read",
                },
                max_per_hour=30,
            ),
        )

    def brief(self) -> str:
        return ("Marketing: experiments measured against the 4-week baseline, "
                "and the playbook they write")


MODULE = MarketingModule()
