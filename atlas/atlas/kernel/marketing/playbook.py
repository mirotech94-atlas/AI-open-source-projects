"""The playbook: what publishing actually did, ranked by recency x outcome.

A lesson is a semantic memory in the namespace `playbook`, written by
`marketing.record_outcome` once an experiment has been measured. Its `meta`
carries the business, the platform, the variant and the outcome so this file
can rank and filter without parsing prose.

Ranking is recency times outcome weight, not recency alone. A loss is worth
nearly as much as a win — it is the thing that stops the next proposal
repeating it — while an `unmeasured` lesson is worth almost nothing, because
by construction it records that nobody found out. Recency decays on a
four-week half-life: the same window the baseline uses, so "what worked" and
"what is normal" age at the same rate.

Nothing here writes. `read()` is a query the marketing agent and the weekly
cycle both call; the only writer is the Tier AUTO action in
`atlas/modules/marketing`.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from ..memory import Kind, Memory, recall

NAMESPACE = "playbook"

# Four weeks, matching the baseline window in `experiments.py`.
HALF_LIFE_DAYS = 28.0

# A loss is nearly as instructive as a win: it is what keeps the next
# proposal off a variant that already failed. An `unmeasured` lesson records
# that nobody found out, so it barely counts.
OUTCOME_WEIGHT: dict[str, float] = {
    "won": 1.0,
    "lost": 0.8,
    "flat": 0.5,
    "unmeasured": 0.15,
}
UNKNOWN_OUTCOME_WEIGHT = 0.3

QUERY = "what worked and what did not when publishing"


@dataclass(frozen=True, slots=True)
class Lesson:
    content: str
    business: str
    platform: str
    variant: str
    outcome: str
    at: datetime | None
    score: float

    def line(self) -> str:
        return self.content


def _meta(memory: Memory, key: str) -> str:
    value = memory.meta.get(key) if isinstance(memory.meta, dict) else None
    return str(value) if value is not None else ""


def rank(memory: Memory, *, now: datetime | None = None) -> float:
    """recency x outcome, in [0, 1]. No history means no recency bonus."""
    now = now or datetime.now(UTC)
    weight = OUTCOME_WEIGHT.get(_meta(memory, "outcome"), UNKNOWN_OUTCOME_WEIGHT)
    if memory.at is None:
        return weight * 0.5
    age_days = max((now - memory.at.astimezone(UTC)).total_seconds(), 0.0) / 86400
    recency = 0.5 ** (age_days / HALF_LIFE_DAYS)
    return weight * recency


async def read(business: str, *, limit: int = 12,
               now: datetime | None = None) -> list[Lesson]:
    """The lessons for one business, best first.

    `recall` is asked for more than `limit` because it cannot filter on
    `meta`: the business filter happens here, after the fetch. An empty
    result means nothing has been measured yet for that business — not a
    failure, and not a licence to invent one.
    """
    memories = await recall(QUERY, [NAMESPACE], kinds=(Kind.SEMANTIC,),
                            limit=max(limit * 4, 20))
    now = now or datetime.now(UTC)
    lessons = [
        Lesson(content=m.content, business=_meta(m, "business"),
               platform=_meta(m, "platform"), variant=_meta(m, "variant"),
               outcome=_meta(m, "outcome"), at=m.at, score=rank(m, now=now))
        for m in memories
        if _meta(m, "business") == business
    ]
    lessons.sort(key=lambda lesson: -lesson.score)
    return lessons[:limit]


def format_for_prompt(lessons: list[Lesson]) -> str:
    """What the playbook looks like inside a prompt. Plain lines, no numbers
    added that are not already in the lesson text."""
    if not lessons:
        return "(the playbook is empty for this business — nothing measured yet)"
    return "\n".join(f"- {lesson.content}" for lesson in lessons)


def losing_variants(lessons: list[Lesson]) -> set[str]:
    return {lesson.variant for lesson in lessons
            if lesson.outcome == "lost" and lesson.variant}


def winning_variants(lessons: list[Lesson]) -> set[str]:
    return {lesson.variant for lesson in lessons
            if lesson.outcome == "won" and lesson.variant}
