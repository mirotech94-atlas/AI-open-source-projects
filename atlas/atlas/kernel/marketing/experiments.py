"""One hypothesis at a time, measured against what is normal.

The loop, in four steps:

1. `propose(business, platform)` reads the playbook, the last eight weeks of
   that platform's measured posts, the trailing 4-week baseline for the
   platform's metric, and the judgment layer's read of what the owner approves for
   `youtube.set_visibility` — so the agent can tell the difference between
   "this idea is bad" and "he keeps declining this kind of post". It asks the
   model for exactly one hypothesis with a variant it can draft, and returns
   it. It writes nothing: `marketing.propose_experiment` does that.
2. When he approves publishing — Tier 2, through the existing gate, every
   time — `attach()` records `posted_ref`, `posted_at`, and a `measure_at`
   48 hours later.
3. `measure_due()` runs daily. For each experiment whose window has closed it
   reads the platform's own number and compares it against the trailing
   4-week median for that platform and metric. Then it calls
   `marketing.record_outcome`, which closes the row and writes the lesson.
4. The lesson goes into memory, `playbook.read()` reads it back, and step 1
   is different next week. That is the entire sense in which this
   self-improves.

**What this file will not do.** If the platform read fails, or there is too
little history to form a baseline, the outcome is `unmeasured` **with the
reason** — never `flat`, which would be a comparison nobody made, and never a
number this file filled in. That rule is the same one invariant 7 applies to
checks: a measurement that could not be taken is not a measurement of zero
difference. Nothing here imports the executor, classifies an action, or names
a tier; the catalog in `atlas/modules/marketing` owns all three.
"""
from __future__ import annotations

import json
import logging
import statistics
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol

from ..contracts import Severity
from ..judgment import policy
from ..llm import Message, Need, think
from ..permissions import wrap_untrusted
from ..state import execute_sql, fetch_all
from . import playbook

log = logging.getLogger("atlas.marketing")

# The window between publishing and reading the result. Short enough that a
# week's plan can learn from the week before it, long enough that a platform's
# own counters have settled.
MEASURE_AFTER = timedelta(hours=48)

# What "better than usual" means. One standard deviation when the baseline has
# one; twenty percent when it does not. Both are deliberately blunt: a finer
# threshold on a handful of posts is false precision.
SIGMA_BAND = 1.0
PERCENT_BAND = 0.20

BASELINE_WINDOW = timedelta(days=28)
BASELINE_LABEL = "4-week median"
# Five, like `state.baseline`: fewer comparable readings than that and a
# median is an anecdote.
BASELINE_MIN_POSTS = 5
HISTORY_WINDOW = timedelta(days=56)      # the eight weeks `propose` reads

# Every platform an experiment can be *measured* on. Which of these the
# weekly plan may *file* on is `cycle.PLATFORMS_WITH_HOOKS` — a narrower set,
# because an experiment only closes if the publish action can carry its id.
PLATFORMS: tuple[str, ...] = ("youtube", "instagram", "tiktok")

# One metric per platform, so an experiment on one platform is never scored
# against another's units.
PLATFORM_METRIC: dict[str, str] = {
    "youtube": "views_48h",
    "instagram": "reach_48h",
    "tiktok": "views_48h",
}

# Where a non-YouTube baseline comes from: one named check per platform, and
# what that check's value actually is. The observations table holds every
# check on the property — token checks, comment counts — and a median across
# them is a number about nothing, so the join is always filtered to this one.
# Neither is a per-post 48-hour count: Instagram's `engagement` is account
# reach per day, TikTok's `video_stats` is views summed over the last five
# videos. The Baseline says so in its `metric`, and `measure_due` refuses to
# score a per-post reading against it.
PLATFORM_BASELINE_CHECK: dict[str, tuple[str, str]] = {
    "instagram": ("engagement", "instagram.engagement (account reach/day)"),
    "tiktok": ("video_stats", "tiktok.video_stats (views, last 5 videos)"),
}

# the owner's course (atlas/modules/course) is measured differently: not per post
# on a platform, but by what the store saw of the post's UTM campaign -- the
# experiment id. Its reading comes from `modules.course.measure`, its
# baseline from earlier course experiments' own readings, and its window is
# seven days (a sale lags a post). A course experiment on any platform uses
# this one metric, so posts on different platforms are comparable.
COURSE_BUSINESS = "course"
COURSE_METRIC = "store_visits"

# The publish decision whose approval history tells the agent whether the owner is
# declining a whole kind of post. Read only — `policy.predict` refuses Tier 3
# before it computes anything, and nothing here writes to the judgment layer.
PUBLISH_DECISION = "youtube.set_visibility"


@dataclass(frozen=True, slots=True)
class Baseline:
    """What is normal, and how it was worked out. `sigma` is None when there
    is not enough spread to compute one — the caller then falls back to the
    percentage band rather than inventing a deviation."""

    value: float
    sigma: float | None
    n: int
    metric: str
    observed_at: datetime
    window: str = BASELINE_LABEL

    def describe(self) -> str:
        spread = f", sigma {self.sigma:,.0f}" if self.sigma else ""
        return (f"{self.value:,.0f} {self.metric} ({self.window} over "
                f"{self.n} posts{spread}, read "
                f"{self.observed_at:%H:%M} UTC)")


@dataclass(frozen=True, slots=True)
class Reading:
    """One number, from one platform, with the time it was read."""

    value: float
    metric: str
    observed_at: datetime
    source: str


# ------------------------------------------------------------ what happened

async def recent_posts(business: str, platform: str, *,
                       weeks: int = 8) -> list[dict]:
    """The measured posts behind this platform in the trailing `weeks`.

    Only YouTube keeps a per-post table (`youtube_videos`, written by Task
    29's collector). Instagram and TikTok are measured in aggregate by their
    own checks, so there is no per-post history to read and the honest answer
    is an empty list — `propose` says so in the prompt rather than implying
    the platform was quiet.
    """
    if platform != "youtube":
        return []
    since = datetime.now(UTC) - timedelta(weeks=weeks)
    try:
        rows = await fetch_all(
            """SELECT title, views, likes, comments, published_at, measured_at
               FROM youtube_videos
               WHERE channel = $1 AND published_at >= $2 AND views IS NOT NULL
               ORDER BY published_at DESC LIMIT 40""",
            business, since)
    except Exception as exc:                            # noqa: BLE001
        log.warning("could not read recent posts for %s: %s", business, exc)
        return []
    return [dict(r) for r in rows]


async def baseline_for(platform: str, metric: str, *, business: str = "",
                       now: datetime | None = None,
                       exclude_ref: str | None = None
                       ) -> tuple[Baseline | None, str | None]:
    """The trailing 4-week median of the same measurement, or a reason.

    The detector's discipline, not a guess: the median of what was actually
    measured, and a refusal below five comparable readings — `state.baseline`
    uses the same floor. The reason reaches the lesson text instead of being
    swallowed.

    YouTube: the median of `youtube_videos.views_48h` — the same
    `analytics(days=2)` count the experiment is scored on, recorded for each
    video in its 48-72 hour window. Not lifetime `views`, which is a counter
    refreshed every six hours and a different measurement. `exclude_ref` keeps
    the experiment's own video out of the median it is judged against.

    What `views_48h` is, precisely: YouTube Analytics reports whole calendar
    days, so `analytics(days=2)` is the trailing two calendar days at the time
    of the read, not exactly the first 48 hours after publishing. The baseline
    and the observation are both taken with that same call, so they compare
    like with like; neither is a to-the-hour "first 48 hours".

    Elsewhere: one named check's observations, never a mix of checks. The
    returned `metric` names what was actually computed, which is not the
    per-post number `metric` asked for — `measure_due` sees the mismatch.
    """
    now = now or datetime.now(UTC)
    since = now - BASELINE_WINDOW
    if business == COURSE_BUSINESS:
        # Earlier course experiments' own store readings, on any platform:
        # every course post is scored on the same store, in the same unit.
        computed = COURSE_METRIC
        sql = ("""SELECT observed AS value FROM marketing_experiments
                  WHERE business = $1 AND metric = $2 AND observed IS NOT NULL
                    AND measured_at >= $3""")
        args = (business, COURSE_METRIC, since)
        if exclude_ref:
            sql += " AND posted_ref <> $4"
            args = (*args, exclude_ref)
    elif platform == "youtube":
        computed = "views_48h"
        sql = ("""SELECT views_48h AS value FROM youtube_videos
                  WHERE channel = $1 AND published_at >= $2
                    AND views_48h IS NOT NULL""")
        args: tuple = (business, since)
        if exclude_ref:
            sql += " AND video_id <> $3"
            args = (*args, exclude_ref)
    elif platform in PLATFORM_BASELINE_CHECK:
        check, computed = PLATFORM_BASELINE_CHECK[platform]
        sql = ("""SELECT o.value AS value FROM observations o
                  JOIN checks c ON c.id = o.check_id
                  JOIN properties p ON p.id = c.property_id
                  WHERE p.name = $1 AND c.name = $3
                    AND o.value IS NOT NULL AND o.at >= $2""")
        args = (platform, since, check)
    else:
        return None, (f"{platform}: no check measures {metric}, so there is "
                      f"no baseline to compare against")
    try:
        rows = await fetch_all(sql, *args)
    except Exception as exc:                            # noqa: BLE001
        return None, f"{platform}: could not read the baseline history: {exc}"

    values = [float(r["value"]) for r in rows if r["value"] is not None]
    if len(values) < BASELINE_MIN_POSTS:
        return None, (f"baseline has {len(values)} of {BASELINE_MIN_POSTS} "
                      f"comparable readings ({computed}, trailing 4 weeks, "
                      f"{platform})")

    sigma = statistics.pstdev(values) if len(values) > 1 else 0.0
    return Baseline(value=float(statistics.median(values)),
                    sigma=float(sigma) if sigma > 0 else None,
                    n=len(values), metric=computed, observed_at=now), None


# ------------------------------------------------------------ the proposal

SYSTEM = """
You plan one marketing experiment for the owner, for a single business on a single
platform. You are given the playbook — what previous experiments actually
measured — the trailing baseline for the platform's metric, the recent posts,
and what the approval record says about this kind of publishing.

Rules:
- Exactly ONE hypothesis. It must be falsifiable by the metric named below at
  48 hours, and it must not repeat a variant the playbook records as lost.
- The variant is a concrete post kind, hook or format that can be drafted from
  material ATLAS already holds. Not a campaign, not a schedule, not a budget.
- Nothing you propose may cost money. No ads, no paid tools, no promotion.
- Use only numbers that appear below. Never invent a figure, a reach estimate
  or a revenue estimate. If the baseline says it is unmeasured, say so in the
  hypothesis rather than assuming one.

Answer with JSON only, no prose around it:
{"hypothesis": "...", "variant": "...", "draft_kind": "...",
 "draft_source": "..."}

draft_kind must be one of: linkedin_post, ebay_listing, short_script. draft_source must be one of: weekly, voice:<n>,
slack:<thread_ts>. Never note:<text> -- only the owner writes notes.
""".strip()


def _judgment_line(pred) -> str:
    """What the approval record says, in words, with no probability when the
    layer abstained. An abstaining prediction carries no number, so none
    reaches the prompt — it says why it abstained instead."""
    if pred.abstained or pred.p is None:
        return (f"Approval record: the judgment layer abstained "
                f"({pred.why or 'no reason given'}). Treat publishing as an "
                f"open question and propose on the merits.")
    return (f"Approval record for {PUBLISH_DECISION}: he has said yes "
            f"{pred.percent()} of the last {pred.seen} times"
            + (f" ({', '.join(pred.reasons[:3])})." if pred.reasons else "."))


def _parse(text: str) -> tuple[dict | None, str | None]:
    body = text.strip()
    if body.startswith("```"):
        body = body.strip("`")
        body = body.split("\n", 1)[1] if "\n" in body else body
    start, end = body.find("{"), body.rfind("}")
    if start < 0 or end <= start:
        return None, "the model did not return JSON"
    try:
        data = json.loads(body[start:end + 1])
    except ValueError as exc:
        return None, f"the model's JSON did not parse: {exc}"
    if not isinstance(data, dict):
        return None, "the model returned JSON that is not an object"
    missing = [k for k in ("hypothesis", "variant", "draft_kind", "draft_source")
               if not str(data.get(k, "")).strip()]
    if missing:
        return None, f"the model left {', '.join(missing)} empty"
    return data, None


async def propose(business: str, platform: str
                  ) -> tuple[dict | None, str | None]:
    """One hypothesis for one platform, or a reason there is none.

    Writes nothing. The caller hands the result to
    `marketing.propose_experiment`, which is the Tier AUTO action that
    records it and drafts the content.
    """
    if platform not in PLATFORMS:
        return None, (f"unknown platform {platform!r}; must be one of "
                      f"{', '.join(PLATFORMS)}")
    metric = PLATFORM_METRIC[platform]

    lessons = await playbook.read(business)
    posts = await recent_posts(business, platform)
    base, base_reason = await baseline_for(platform, metric, business=business)
    pred = await policy.predict(PUBLISH_DECISION, int(Severity.NORMAL))

    baseline_line = (f"Baseline: {base.describe()}" if base is not None
                     else f"Baseline: not measured — {base_reason}")
    posts_line = (
        "\n".join(f"- {p.get('title', 'untitled')}: "
                  f"{int(p['views']):,} views"
                  for p in posts[:12] if p.get("views") is not None)
        or "(no per-post history is measured for this platform)")

    material = (
        f"Business: {business}\n"
        f"Platform: {platform}\n"
        f"Metric to move: {metric}\n"
        f"{baseline_line}\n\n"
        f"Playbook:\n{playbook.format_for_prompt(lessons)}\n\n"
        f"Measured posts, last 8 weeks:\n{posts_line}\n\n"
        f"{_judgment_line(pred)}\n"
    )
    # Every figure above came out of Postgres or a module read in this call.
    # It is still wrapped: it contains post titles, which are text ATLAS did
    # not write (invariant 4).
    completion = await think(
        Need.REASON, SYSTEM,
        [Message("user", wrap_untrusted(material, "marketing.material"))],
        max_tokens=700)

    data, reason = _parse(completion.text)
    if data is None:
        return None, reason

    # A `note:` source is the owner dictating a note; a model filling it in is a
    # note nobody wrote. On 2026-09-27 that produced seven short scripts
    # about "the the owner board" (the whiteboard app) from invented notes.
    if str(data["draft_source"]).strip().lower().startswith("note:"):
        return None, ("the model proposed a note: source, which only the owner can "
                      "write; drafts come from weekly, voice or slack sources")
    losing = playbook.losing_variants(lessons)
    if str(data["variant"]).strip() in losing:
        return None, (f"the model proposed {data['variant']!r}, which the "
                      f"playbook already records as lost")

    return {
        "business": business,
        "platform": platform,
        "metric": metric,
        "hypothesis": str(data["hypothesis"]).strip(),
        "variant": str(data["variant"]).strip(),
        "draft_kind": str(data["draft_kind"]).strip(),
        "draft_source": str(data["draft_source"]).strip(),
        "baseline": base.value if base is not None else None,
        "baseline_note": base.describe() if base is not None else base_reason,
    }, None


# ----------------------------------------------------- publishing, measured

async def attach(experiment_id: int, posted_ref: str, *,
                 posted_at: datetime | None = None,
                 after: timedelta = MEASURE_AFTER) -> datetime:
    """Record that the experiment's post went out, and when to read it.

    Called once the owner has approved publishing through the platform's own Tier
    ASK gate. This function does not publish and cannot: it writes three
    columns and returns the measurement deadline.
    """
    posted_at = posted_at or datetime.now(UTC)
    measure_at = posted_at + after
    await execute_sql(
        """UPDATE marketing_experiments
           SET posted_ref = $2, posted_at = $3, measure_at = $4
           WHERE id = $1""",
        experiment_id, posted_ref, posted_at, measure_at)
    return measure_at


async def read_metric(platform: str, business: str, posted_ref: str, *,
                      experiment_id: int | None = None
                      ) -> tuple[Reading | None, str | None]:
    """The platform's own number for one post, or a plain reason there is none.

    Each platform module has a different contract — YouTube's api returns
    `(value, reason)`, Instagram's and TikTok's helpers raise — so each is
    adapted here. A failure is a reason, never a zero.

    A course experiment is read from the store's side instead: what the
    store saw of its UTM campaign (`modules.course.measure`), or UNKNOWN with
    the reason -- sales are always unknown there, and the reading says so.
    """
    if business == COURSE_BUSINESS:
        from ...modules.course import measure as course_measure
        found, note = await course_measure.read(experiment_id or 0)
        if found is None:
            return None, note
        return Reading(float(found["value"]), found["metric"],
                       found["observed_at"],
                       f"{found['source']}; {note}"), None
    metric = PLATFORM_METRIC.get(platform, "unknown")
    if platform == "youtube":
        from ...modules.youtube import api as youtube_api
        data, reason = await youtube_api.analytics(business, posted_ref, days=2)
        if reason is not None:
            return None, reason
        views = (data or {}).get("views")
        if views is None:
            return None, (f"youtube: analytics returned no views for "
                          f"{posted_ref!r}")
        return Reading(float(views), metric, data["read_at"],
                       "youtube.analytics"), None

    if platform == "instagram":
        try:
            from ...modules import instagram
            payload = await instagram._get(f"{posted_ref}/insights",
                                           metric="reach")
            entries = payload.get("data") or []
            reach = next((e["values"][0]["value"] for e in entries
                          if e.get("name") == "reach"), None)
        except Exception as exc:                        # noqa: BLE001
            return None, f"instagram: insights read failed: {exc}"
        if reach is None:
            return None, (f"instagram: insights returned no reach for "
                          f"{posted_ref!r}")
        return Reading(float(reach), metric, datetime.now(UTC),
                       "instagram.insights"), None

    if platform == "tiktok":
        try:
            from ...modules import tiktok
            obs = await tiktok.check_video_stats()
            videos = (obs.evidence or {}).get("videos") or []
        except Exception as exc:                        # noqa: BLE001
            return None, f"tiktok: video list read failed: {exc}"
        match = next((v for v in videos
                      if str(v.get("id")) == str(posted_ref)), None)
        if match is None:
            return None, (f"tiktok: {posted_ref!r} is not in the last 5 "
                          f"videos the module reads; no per-post number")
        views = match.get("view_count")
        if views is None:
            return None, f"tiktok: no view_count reported for {posted_ref!r}"
        return Reading(float(views), metric, datetime.now(UTC),
                       "tiktok.video_list"), None

    return None, f"no metric reader for platform {platform!r}"


def verdict(observed: float, base: Baseline) -> str:
    """won / lost / flat, against a baseline that exists.

    Never called without one: an experiment with no baseline is `unmeasured`,
    decided in `measure_due`. One sigma when the baseline has spread, twenty
    percent when it does not. A zero median is `flat` whatever was observed:
    "better than nothing" is not a finding, and a percentage of zero is not a
    number — this comes before the sigma path so a 0,0,0,0,10 series cannot
    score a win through it either.
    """
    if base.value == 0:
        return "flat"
    if base.sigma:
        z = (observed - base.value) / base.sigma
        if z >= SIGMA_BAND:
            return "won"
        if z <= -SIGMA_BAND:
            return "lost"
        return "flat"
    change = (observed - base.value) / base.value
    if change >= PERCENT_BAND:
        return "won"
    if change <= -PERCENT_BAND:
        return "lost"
    return "flat"


def _delta_text(observed: float, base: Baseline) -> str:
    if base.value == 0:
        return f"{observed:,.0f} {base.metric} against a zero {base.window}"
    change = (observed - base.value) / base.value * 100
    return (f"{change:+.0f}% {base.metric} vs {base.window}")


def lesson_text(row: dict, outcome: str, *, reading: Reading | None,
                base: Baseline | None, reason: str = "") -> str:
    """The line that goes into the playbook.

    Shaped so the next prompt can read it without parsing: business, platform,
    variant, outcome, and — when there was one — the measured delta and the
    time it was read. Invariant 6: no figure without its timestamp.
    """
    head = (f"{row['business']} · {row['platform']} · {row['variant']}: "
            f"{outcome}")
    if outcome == "unmeasured" and reading is not None:
        # Read, but with nothing to compare it against: the reading is kept
        # in the lesson with its time, so the playbook knows what was seen.
        return (f"{head} ({reason or 'no reason recorded'}; read "
                f"{reading.value:,.0f} {reading.metric} at "
                f"{reading.observed_at:%H:%M} UTC from {reading.source})")
    if outcome == "unmeasured" or reading is None or base is None:
        return f"{head} ({reason or 'no reason recorded'})"
    return (f"{head} ({_delta_text(reading.value, base)}, measured "
            f"{reading.observed_at:%H:%M})")


async def _due(now: datetime) -> list[dict]:
    rows = await fetch_all(
        """SELECT id, business, platform, hypothesis, variant, draft_id,
                  posted_ref, posted_at, measure_at, metric
           FROM marketing_experiments
           WHERE outcome IS NULL AND posted_ref IS NOT NULL
             AND measure_at IS NOT NULL AND measure_at <= $1
           ORDER BY measure_at LIMIT 25""",
        now)
    return [dict(r) for r in rows]


class Recorder(Protocol):
    """Closes one experiment: `marketing.record_outcome`, THROUGH THE
    EXECUTOR. The scheduler hands `measure_due` one that builds an
    ExecutionRequest; nothing in this package may call the action's `run`
    itself, or the audit line, ATLAS_WATCH_ONLY, the dry run and the hourly
    ceiling would not apply to it (invariant 1). This package cannot import
    the executor either -- the planners hand requests up, they never run
    them -- so the recorder is passed in."""

    async def __call__(self, experiment_id: int, outcome: str, lesson: str, *,
                       observed: float | None = None,
                       baseline: float | None = None,
                       metric: str | None = None) -> Any: ...


async def _record_48h(platform: str, posted_ref: str, reading: Reading) -> None:
    """Keep the experiment's own 48-hour reading for the next baseline.

    Every measured YouTube experiment is one more comparable reading, so the
    4-week median fills from the loop itself as well as from the collector.
    A failure here costs a future baseline one sample; it is logged and does
    not change this experiment's outcome.
    """
    if platform != "youtube":
        return
    try:
        await execute_sql(
            """UPDATE youtube_videos SET views_48h = $2, views_48h_at = $3
               WHERE video_id = $1 AND views_48h IS NULL""",
            posted_ref, reading.value, reading.observed_at)
    except Exception as exc:                            # noqa: BLE001
        log.warning("could not keep the 48h reading for %s: %s",
                    posted_ref, exc)


async def measure_due(now: datetime | None = None, *,
                      record: Recorder) -> list[dict]:
    """Read every experiment whose 48 hours are up, and close it with
    `record` (the scheduler's, which goes through the executor). An
    experiment `record` does not close -- watch-only, the hourly ceiling --
    stays due and is measured again tomorrow.

    Three outcomes reach `record_outcome`: a measured verdict, `unmeasured`
    because the platform could not be read, and `unmeasured` because there is
    too little history for a baseline. The last two are the reason this
    function exists in this shape — a system that quietly called them `flat`
    would be reporting a comparison it never made.
    """
    now = now or datetime.now(UTC)
    try:
        due = await _due(now)
    except Exception as exc:                            # noqa: BLE001
        log.warning("could not read due experiments: %s", exc)
        return []

    results: list[dict] = []
    for row in due:
        metric = row.get("metric") or PLATFORM_METRIC.get(row["platform"], "")
        reading, reason = await read_metric(
            row["platform"], row["business"], row["posted_ref"],
            experiment_id=row["id"])

        base: Baseline | None = None
        if reading is None:
            outcome = "unmeasured"
        else:
            base, base_reason = await baseline_for(
                row["platform"], metric, business=row["business"], now=now,
                exclude_ref=row["posted_ref"])
            await _record_48h(row["platform"], row["posted_ref"], reading)
            if base is None:
                outcome, reason = "unmeasured", base_reason or "no baseline"
            elif base.metric != reading.metric:
                # A per-post reading against an account-level median is a
                # comparison of two different things; refuse it by name.
                outcome = "unmeasured"
                reason = (f"the baseline is {base.metric} and the reading is "
                          f"{reading.metric}; not comparable")
                base = None
            else:
                outcome = verdict(reading.value, base)

        lesson = lesson_text(row, outcome, reading=reading, base=base,
                             reason=reason or "")
        try:
            closed = await record(
                row["id"], outcome, lesson,
                observed=reading.value if reading is not None else None,
                baseline=base.value if base is not None else None,
                metric=metric)
            if closed is not None and not getattr(closed, "ok", True):
                log.warning("experiment #%s not recorded: %s", row["id"],
                            getattr(closed, "detail", ""))
        except Exception as exc:                        # noqa: BLE001
            log.warning("could not record experiment #%s: %s", row["id"], exc)
        results.append({"experiment_id": row["id"], "outcome": outcome,
                        "lesson": lesson, "business": row["business"],
                        "platform": row["platform"],
                        "variant": row["variant"]})
    return results
