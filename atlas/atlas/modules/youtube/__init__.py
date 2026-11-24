"""youtube — the `clips` channel (the clips lane's Shorts channel) and
`amsm`, read everything, publish behind a gate.

The `clips` channel (`YOUTUBE_CLIPS_*`; unset reads "not configured",
UNKNOWN, never OK) is the clips lane's alone: `youtube.upload` refuses it,
and `youtube.set_visibility` will not make one of its videos public --
only `clips.publish_clip_youtube`, one Tier 2 card per lane-minted clip,
puts a Short up there.

What it watches, per channel: the OAuth refresh token still working, daily
subscriber/view/video counts (`channels.list`), and the last 25 videos'
stats refreshed into `youtube_videos` every six hours (`search.list` +
`videos.list`). For a video in its 48-72 hour window the same refresh also
records `views_48h` — `api.analytics(days=2)`, the exact read a marketing
experiment is scored on — so the experiment's 4-week baseline compares like
with like rather than against lifetime views. The daily stats check has a baseline on views
(`baseline_sigma=3.0` — the detector decides what "unusual" means); every
other check disables statistical detection (`baseline_sigma=None`) because
a token either works or it does not, and a video count is not a trend.

What it may do:
  * `youtube.upload` (Tier ASK) — uploads a video. It always lands
    `private`; `privacy="public"` (or anything but `"private"`) is refused
    in `api.upload`, not merely overridden, so a caller cannot argue its
    way past the gate. Nothing is public on upload. `irreversible=False`:
    its rollback does not delete (deletion is Tier 3, permanently) — it
    sets the video private again, which is what it already is.
  * `youtube.set_visibility` (Tier ASK) — changes a video's privacy.
    Going public is Miro's word, every time. The rollback restores whatever
    privacy the video actually had immediately before the change (read
    live from YouTube, not from a possibly-stale local row); if that read
    fails, the action refuses rather than changing a video's visibility
    with no way back. An optional `experiment_id` threads a marketing
    experiment through: when — and only when — the approval actually makes
    the video `public`, it starts that experiment's 48-hour measurement
    window (`kernel.marketing.experiments.attach`). This changes nothing
    about the gate; it records when the clock started.
  * `youtube.draft_metadata` (Tier AUTO) — drafts a title, description and
    tags into a `content_drafts` row (`kind="youtube_video"`). Nothing here
    reaches YouTube: it is the same shape of action as `content.draft`,
    reversible by marking the row dropped.

Endpoints this assumes, so Miro can correct them: YouTube Data API v3
(`googleapis.com/youtube/v3`), a resumable upload at
`googleapis.com/upload/youtube/v3/videos`, YouTube Analytics API v2
(`youtubeanalytics.googleapis.com/v2/reports`), OAuth refresh at
`oauth2.googleapis.com/token`. See `api.py` for the exact calls.
"""
from __future__ import annotations

import hashlib
import json
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
from ...kernel.state import execute_sql, fetch_one
from . import api
from .metadata import draft_metadata as _draft_metadata

CHANNELS = api.CHANNELS
CLIPS_CHANNEL = api.CLIPS_CHANNEL
AMSM_CHANNEL = api.AMSM_CHANNEL
TUTORCO_CHANNEL = "tutorco"
MONITOR_ONLY = "the tutorco channel is watched only; ATLAS does not post to it"
LANE_ONLY = ("the clips channel posts only through clips.publish_clip_youtube, "
             "one campaign clip per card")
COMMUNITY_ONLY = ("the amsm channel takes uploads only through "
               "community.schedule_youtube_shorts, each video reviewed by the owner")

# YouTube's own published Partner Program thresholds. Not estimated, not
# adjusted here — only ever compared against a number this module actually
# measured. Source: https://support.google.com/youtube/answer/72851, read
# 2026-10-16: 1,000 subscribers AND either 4,000 qualified public watch hours
# in the last 12 months (Shorts-feed watch time does not count) OR 10M
# qualified Shorts-feed views in the last 90 days.
PARTNER_SUBSCRIBERS = 1_000
PARTNER_WATCH_HOURS_12MO = 4_000
PARTNER_SHORTS_VIEWS_90D = 10_000_000

# What each channel's daily stats check last measured, filled in by
# check_stats and read only by brief() — never awaited, never queried, so
# the morning-brief line can never carry a number without the time it was
# read (invariant 6) and never blocks on a network call it does not own.
_LAST: dict[str, dict] = {}


def _make_check(channel: str, fn):
    async def _wrapped() -> Observation:
        return await fn(channel)
    return _wrapped


async def check_token(channel: str) -> Observation:
    _tok, reason = await api.token(channel)
    if reason is not None:
        return Observation(f"{channel}_token", Health.UNKNOWN, detail=reason)
    return Observation(f"{channel}_token", Health.OK,
                       detail=f"{channel}: refresh token still valid")


async def check_stats(channel: str) -> Observation:
    data, reason = await api.channel_stats(channel)
    if reason is not None:
        return Observation(f"{channel}_stats", Health.UNKNOWN, detail=reason)

    _LAST[channel] = data
    subs = data.get("subscribers")
    views = data.get("views")
    read_at = data["read_at"]
    subs_text = f"{int(subs):,}" if subs is not None else "unreported"
    views_text = f"{int(views):,}" if views is not None else "unreported"
    return Observation(
        f"{channel}_stats", Health.OK, value=views, unit="views",
        detail=(f"{channel}: {subs_text} subscribers, {views_text} views "
               f"(read {read_at:%H:%M})"),
        evidence={"channel_stats": {**data, "read_at": read_at.isoformat()}},
        at=read_at,
    )


# The window in which a video's 48-hour reading is taken. Six-hourly refreshes
# land in it four times; the first successful read wins.
WINDOW_48H = (timedelta(hours=48), timedelta(hours=72))


def _published(value) -> datetime | None:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=UTC)
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


async def _record_48h(channel: str, videos: list[dict], now: datetime
                      ) -> tuple[int, list[str]]:
    """`views_48h` for every video now between 48 and 72 hours old.

    The same call `experiments.read_metric` makes, so a baseline built from
    these readings is a median of the measurement it is compared against. A
    failed read is a missing sample, named in the detail — never a zero.

    Public videos only. Every ATLAS upload lands private, and a private video
    read 48-72h after upload would store ~0 views that the `IS NULL` guard
    then keeps after it goes public. `publishedAt` moves to the moment a
    private video is made public, so the window starts from the right clock.
    """
    kept, missed = 0, []
    earliest, latest = now - WINDOW_48H[1], now - WINDOW_48H[0]
    for v in videos:
        if v.get("privacy") != "public":
            continue
        published = _published(v.get("published_at"))
        if published is None or not earliest <= published <= latest:
            continue
        data, reason = await api.analytics(channel, v["video_id"], days=2)
        views = (data or {}).get("views")
        if reason is not None or views is None:
            missed.append(f"{v['video_id']}: {reason or 'no views reported'}")
            continue
        await execute_sql(
            """UPDATE youtube_videos SET views_48h = $2, views_48h_at = $3
               WHERE video_id = $1 AND views_48h IS NULL""",
            v["video_id"], float(views), data.get("read_at") or now)
        kept += 1
    return kept, missed


async def check_latest_videos(channel: str) -> Observation:
    videos, reason = await api.videos(channel, limit=25)
    if reason is not None:
        return Observation(f"{channel}_latest_videos", Health.UNKNOWN, detail=reason)

    now = datetime.now(UTC)
    try:
        for v in videos:
            await execute_sql(
                """INSERT INTO youtube_videos
                     (channel, video_id, title, published_at, privacy,
                      views, likes, comments, measured_at)
                   VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9)
                   ON CONFLICT (video_id) DO UPDATE SET
                     title = EXCLUDED.title,
                     privacy = EXCLUDED.privacy,
                     views = EXCLUDED.views,
                     likes = EXCLUDED.likes,
                     comments = EXCLUDED.comments,
                     measured_at = EXCLUDED.measured_at""",
                # The API's publishedAt is an ISO string ending in Z; asyncpg
                # wants a datetime (the first live refresh, 2026-10-11, failed
                # on it). _published reads both, and a value it cannot read
                # is stored as NULL rather than refusing the whole refresh.
                channel, v["video_id"], v["title"], _published(v.get("published_at")),
                v.get("privacy"), v.get("views"), v.get("likes"),
                v.get("comments"), now,
            )
    except Exception as exc:  # noqa: BLE001
        return Observation(f"{channel}_latest_videos", Health.UNKNOWN,
                           detail=f"{channel}: could not record videos: {exc}")

    detail = f"{channel}: {len(videos)} video(s) refreshed"
    try:
        kept, missed = await _record_48h(channel, videos, now)
    except Exception as exc:  # noqa: BLE001
        kept, missed = 0, [f"could not record 48h readings: {exc}"]
    if kept:
        detail += f"; {kept} 48h reading(s) kept"
    if missed:
        # The refresh itself worked, so the check stays OK; the gap in the
        # baseline is named rather than hidden.
        detail += f"; 48h reading not taken for {'; '.join(missed[:3])}"

    return Observation(
        f"{channel}_latest_videos", Health.OK, value=float(len(videos)),
        unit="videos", detail=detail,
        evidence={"video_ids": [v["video_id"] for v in videos]}, at=now,
    )


async def partner_eligibility(channel: str) -> dict:
    """A plain comparison of measured numbers, never an estimate.

    YouTube's Partner Program needs 1,000 subscribers AND 4,000 public
    watch hours in the trailing 12 months, OR 10M Shorts views in the
    trailing 90 days. This compares the subscriber count from
    `channel_stats` only. The watch-hour and Shorts-view paths are measured
    channel-wide by `academy.ypp.ypp_progress` (Analytics `api.channel_report`)
    for the Monday digest, not here, so this never claims eligibility on
    them. YouTube Studio → Earn is the authority on all three.
    """
    data, reason = await api.channel_stats(channel)
    if reason is not None:
        return {"channel": channel, "eligible_on_subscribers": None,
                "detail": f"{channel}: cannot read subscriber count: {reason}"}

    subs = data.get("subscribers")
    read_at = data["read_at"]
    if subs is None:
        return {"channel": channel, "eligible_on_subscribers": None,
                "subscribers": None, "read_at": read_at,
                "detail": (f"{channel}: subscriber count not reported "
                          f"(hidden), read {read_at:%H:%M}")}

    eligible = subs >= PARTNER_SUBSCRIBERS
    detail = (
        f"{'eligible' if eligible else 'not eligible'}: {int(subs):,} of "
        f"{PARTNER_SUBSCRIBERS:,} subscribers, read {read_at:%H:%M} "
        f"(watch-hours and Shorts-views paths not measured by this module)")
    return {
        "channel": channel,
        "subscribers": subs,
        "read_at": read_at,
        "eligible_on_subscribers": eligible,
        "detail": detail,
    }


# ------------------------------------------------------------------ actions

def academy_path(path: str) -> bool:
    """A file the Academy may upload to the clips channel: staged Academy video
    or a finished tutorial render. Reads the disk (the executor vet is not hot)."""
    if not path:
        return False
    if api.academy_video_path(path):
        return True
    from ..academy.tutorials import publishable_render
    return publishable_render(path) is None


def vet_upload(params: dict) -> str | None:
    """No card for the generic upload to the clips channel: it takes only
    what the clips lane cut and checked, through its own card. Pure."""
    if str(params.get("channel") or "") == CLIPS_CHANNEL:
        # Miro 2026-11-14: the Academy's own renders may use the generic card too
        # (still Tier 2, still private on upload). api.upload checks the file.
        if not academy_path(str(params.get("path") or "")):
            return LANE_ONLY
        return None
    if str(params.get("channel") or "") == AMSM_CHANNEL:
        return COMMUNITY_ONLY
    if str(params.get("channel") or "") == TUTORCO_CHANNEL:
        return MONITOR_ONLY
    return None


def vet_visibility(params: dict) -> str | None:
    """A clips-channel video goes public only through the clips lane's own
    card; taking one private is always allowed. Pure."""
    if (str(params.get("channel") or "") == CLIPS_CHANNEL
            and str(params.get("privacy") or "") != "private"):
        # unlisted or public: only for a video this module uploaded for the
        # Academy; the record is checked in set_visibility (async), not here.
        return None if params.get("academy_checked") else LANE_ONLY
    if (str(params.get("channel") or "") == TUTORCO_CHANNEL
            and str(params.get("privacy") or "") != "private"):
        return MONITOR_ONLY
    return None


def vet_visibility_card(params: dict) -> str | None:
    """The card-time vet: a public clips video is judged when the action runs
    (it must be an Academy upload, `_is_academy`); the card itself may be raised."""
    return vet_visibility({**params, "academy_checked": True}
                          if str(params.get("channel") or "") == CLIPS_CHANNEL
                          else params)


async def upload_video(
    channel: str, path: str, title: str, description: str, tags: list[str]
) -> ActionResult:
    if (why := vet_upload({"channel": channel, "path": path})) is not None:
        return ActionResult(False, f"refused: {why}")
    if channel == CLIPS_CHANNEL and path and api.academy_video_path(path):
        from ..academy import lesson_youtube
        if await lesson_youtube.uploaded_today() >= lesson_youtube.per_day():
            return ActionResult(False, "refused: today's Academy upload cap "
                                       f"({lesson_youtube.per_day()}) is used")
    result, reason = await api.upload(channel, path, title, description, tags,
                                      privacy="private")
    if reason is not None:
        return ActionResult(False, reason)
    if channel == CLIPS_CHANNEL:
        await _mark_academy(result["video_id"])
    return ActionResult(
        True, f"{channel}: uploaded as private ({result['video_id']})",
        evidence={"channel": channel, "video_id": result["video_id"]},
        rollback_token=f"{channel}:{result['video_id']}")


async def _mark_academy(video_id: str) -> None:
    await execute_sql(
        "CREATE TABLE IF NOT EXISTS academy_youtube_uploads "
        "(video_id text PRIMARY KEY, at timestamptz NOT NULL DEFAULT now())")
    await execute_sql(
        "INSERT INTO academy_youtube_uploads (video_id) VALUES ($1) "
        "ON CONFLICT DO NOTHING", video_id)


async def _is_academy(video_id: str) -> bool:
    try:
        row = await fetch_one(
            "SELECT 1 AS x FROM academy_youtube_uploads WHERE video_id = $1", video_id)
    except Exception:  # noqa: BLE001
        return False
    return row is not None


async def upload_dry(
    channel: str, path: str, title: str, description: str, tags: list[str]
) -> ActionResult:
    if channel not in CHANNELS:
        return ActionResult(False, f"unknown channel {channel!r}")
    if (why := vet_upload({"channel": channel, "path": path})) is not None:
        return ActionResult(False, f"refused: {why}")
    if len(title) > 100:
        return ActionResult(False, "title exceeds YouTube's 100 characters")
    return ActionResult(True, f"would upload {path!r} to {channel} as private")


async def upload_rollback(token: str) -> ActionResult:
    """Nothing public happened to undo. This confirms the video is private —
    the state it landed in — rather than deleting anything: deletion is
    permanently Tier 3, whatever calls this."""
    channel, _, video_id = token.partition(":")
    _result, reason = await api.set_privacy(channel, video_id, "private")
    if reason is not None:
        return ActionResult(False, reason)
    return ActionResult(True, f"{channel}: {video_id} confirmed private")


async def set_visibility(channel: str, video_id: str, privacy: str,
                         experiment_id: int | None = None) -> ActionResult:
    if privacy not in api.PRIVACY_VALUES:
        return ActionResult(
            False, f"invalid privacy {privacy!r}; must be one of "
                  f"{', '.join(api.PRIVACY_VALUES)}")
    if (why := vet_visibility({
            "channel": channel, "privacy": privacy,
            "academy_checked": channel == CLIPS_CHANNEL and await _is_academy(video_id)})):
        return ActionResult(False, f"refused: {why}")

    previous, reason = await api.current_privacy(channel, video_id)
    if reason is not None:
        return ActionResult(
            False, f"cannot read current privacy, refusing to change it "
                  f"blind: {reason}")

    _result, reason = await api.set_privacy(channel, video_id, privacy)
    if reason is not None:
        return ActionResult(False, reason)

    await execute_sql(
        "UPDATE youtube_videos SET privacy = $3 WHERE channel = $1 AND video_id = $2",
        channel, video_id, privacy)

    # This is where a marketing experiment starts its clock: the moment Miro's
    # approval actually made the video public. Not at upload (it lands
    # private) and not when the experiment was proposed — 48 hours of views on
    # a private video is not a result. Only `public` starts it; `unlisted` is
    # not published. The import is local so the youtube module does not pull
    # the marketing package (and through it the judgment layer) into every
    # import of itself.
    measure_at = None
    if experiment_id is not None and privacy == "public":
        from ...kernel.marketing.experiments import attach
        try:
            measure_at = await attach(int(experiment_id), video_id,
                                      posted_at=datetime.now(UTC))
        except Exception as exc:  # noqa: BLE001
            # The video is public either way; that is the part that mattered
            # and it already happened. A bookkeeping failure is reported, not
            # rolled back into a visibility change.
            return ActionResult(
                True, f"{channel}: {video_id} is now {privacy} (was "
                      f"{previous}) — but experiment #{experiment_id} was not "
                      f"attached: {exc}",
                evidence={"previous": previous, "channel": channel,
                          "video_id": video_id},
                rollback_token=f"{channel}:{video_id}:{previous}")

    detail = f"{channel}: {video_id} is now {privacy} (was {previous})"
    if measure_at is not None:
        detail += (f"; experiment #{experiment_id} will be measured "
                   f"{measure_at:%d %b %H:%M} UTC")
    return ActionResult(
        True, detail,
        evidence={"previous": previous, "channel": channel,
                  "video_id": video_id,
                  "experiment_id": experiment_id,
                  "measure_at": measure_at},
        rollback_token=f"{channel}:{video_id}:{previous}")


async def set_visibility_dry(channel: str, video_id: str, privacy: str,
                             experiment_id: int | None = None) -> ActionResult:
    if privacy not in api.PRIVACY_VALUES:
        return ActionResult(
            False, f"invalid privacy {privacy!r}; must be one of "
                  f"{', '.join(api.PRIVACY_VALUES)}")
    if (why := vet_visibility({
            "channel": channel, "privacy": privacy,
            "academy_checked": channel == CLIPS_CHANNEL and await _is_academy(video_id)})):
        return ActionResult(False, f"refused: {why}")
    tail = (f" and start experiment #{experiment_id}'s 48-hour window"
            if experiment_id is not None and privacy == "public" else "")
    return ActionResult(True,
                        f"would set {channel}:{video_id} to {privacy}{tail}")


async def set_visibility_rollback(token: str) -> ActionResult:
    channel, video_id, previous = token.split(":", 2)
    if not previous or previous == "None":
        return ActionResult(False, "no previous privacy was recorded to restore")
    _result, reason = await api.set_privacy(channel, video_id, previous)
    if reason is not None:
        return ActionResult(False, reason)
    await execute_sql(
        "UPDATE youtube_videos SET privacy = $3 WHERE channel = $1 AND video_id = $2",
        channel, video_id, previous)
    return ActionResult(True, f"{channel}: {video_id} restored to {previous}")


async def draft_metadata(business: str, source: str) -> ActionResult:
    result = await _draft_metadata(business, source)
    if "error" in result:
        return ActionResult(False, result["error"])
    body = json.dumps(result, default=str)
    row = await fetch_one(
        """INSERT INTO content_drafts (kind, source, title, body, status)
           VALUES ('youtube_video', $1, $2, $3, 'draft') RETURNING id""",
        source, result["title"], body)
    draft_id = row["id"]
    return ActionResult(
        True, f"drafted YouTube metadata for {business} from {source!r}: "
             f"{result['title']!r}",
        evidence={"id": draft_id, "title": result["title"],
                  "sha256": hashlib.sha256(body.encode()).hexdigest()[:12]},
        rollback_token=str(draft_id))


async def draft_metadata_dry(business: str, source: str) -> ActionResult:
    result = await _draft_metadata(business, source)
    if "error" in result:
        return ActionResult(False, result["error"])
    return ActionResult(
        True, f"would draft YouTube metadata for {business} from {source!r}")


async def draft_metadata_rollback(token: str) -> ActionResult:
    await execute_sql(
        "UPDATE content_drafts SET status = 'dropped' WHERE id = $1", int(token))
    return ActionResult(True, f"draft {token} marked dropped")


class YouTubeModule:
    name = "youtube"
    title = "YouTube"
    requires_env = ()
    optional_env = tuple(
        f"{prefix}{suffix}"
        for prefix in CHANNELS.values()
        for suffix in ("CLIENT_ID", "CLIENT_SECRET", "REFRESH_TOKEN", "CHANNEL_ID")
    )
    setup_note = ("Set YOUTUBE_CLIPS_* and "
                 "YOUTUBE_AMSM_* and YOUTUBE_TUTORCO_* (CLIENT_ID, CLIENT_SECRET, REFRESH_TOKEN, "
                 "CHANNEL_ID) to enable each channel.")

    async def setup(self) -> None: ...
    async def teardown(self) -> None: ...

    def checks(self):
        out = []
        for channel in CHANNELS:
            out += [
                Check(f"{channel}_token", _make_check(channel, check_token),
                      timedelta(hours=6), Severity.HIGH,
                      f"{channel}: refresh token still valid",
                      baseline_sigma=None),
                Check(f"{channel}_stats", _make_check(channel, check_stats),
                      timedelta(days=1), Severity.INFO,
                      f"{channel}: subscribers and views", baseline_sigma=3.0),
                Check(f"{channel}_latest_videos",
                      _make_check(channel, check_latest_videos),
                      timedelta(hours=6), Severity.NORMAL,
                      f"{channel}: recent video stats refreshed",
                      baseline_sigma=None),
            ]
        return tuple(out)

    def actions(self):
        return (
            Action(
                name="youtube.upload",
                tier=Tier.ASK,
                summary="Upload a video to a YouTube channel; it always "
                       "lands private",
                run=upload_video, dry_run=upload_dry, rollback=upload_rollback,
                irreversible=False,
                vet=vet_upload,
                params={
                    "channel": "a channel in youtube.api.CHANNELS (clips posts "
                               "only through clips.publish_clip_youtube)",
                    "path": "local path to the video file",
                    "title": "video title, at most 100 characters",
                    "description": "video description",
                    "tags": "list of tags, at most 15",
                },
                max_per_hour=6,
            ),
            Action(
                name="youtube.set_visibility",
                tier=Tier.ASK,
                summary="Change a video's privacy — going public is Miro's "
                       "word, every time",
                run=set_visibility, dry_run=set_visibility_dry,
                rollback=set_visibility_rollback, irreversible=False,
                vet=vet_visibility_card,
                params={
                    "channel": "a channel in youtube.api.CHANNELS (clips: private "
                               "only; it goes public through its own card)",
                    "video_id": "YouTube video id",
                    "privacy": "private, unlisted or public",
                    "experiment_id": "marketing_experiments.id, when this "
                                     "publish is one; going public starts its "
                                     "48-hour measurement window",
                },
                max_per_hour=10,
            ),
            Action(
                name="youtube.draft_metadata",
                tier=Tier.AUTO,
                summary="Draft a title, description and tags for a YouTube "
                       "upload; writes a content_drafts row, nothing reaches "
                       "YouTube",
                run=draft_metadata, dry_run=draft_metadata_dry,
                rollback=draft_metadata_rollback,
                params={
                    "business": "a business in youtube.metadata.BUSINESS_LINKS, "
                                "or any name (no link appended)",
                    "source": "voice:<n> | weekly | slack:<thread_ts> | "
                              "note:<text>",
                },
                max_per_hour=20,
            ),
        )

    def brief(self) -> str:
        parts = []
        for channel in CHANNELS:
            cached = _LAST.get(channel)
            if not cached or cached.get("subscribers") is None:
                parts.append(f"{channel} not read yet")
            else:
                parts.append(
                    f"{channel} {int(cached['subscribers']):,} subs "
                    f"(read {cached['read_at']:%H:%M})")
        return "YouTube: " + ", ".join(parts)


MODULE = YouTubeModule()
