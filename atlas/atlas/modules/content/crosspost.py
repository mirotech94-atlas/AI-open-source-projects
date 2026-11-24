"""One approved AI-stories reel, out to three places on one card.

`content.publish_reel` is Tier 2 (ASK): a post under the AI-stories
account's name is the owner's decision each time. One card covers all three legs,
in this order, each independent of the others:

  1. **Instagram, the stories account** -- through `instagram.publish` with
     `account="stories"`, `kind="reel"`, `content_kind="ai_story"`, so Task
     55's content gate and the AI-label rule apply to this leg exactly as
     they do to a card raised for `instagram.publish` itself.
  2. **The "Atlas Stories" Facebook Page** -- `POST /{page-id}/videos` with
     `file_url` and `description`, a Page access token in the Authorization
     header (`FACEBOOK_STORIES_PAGE_ID`, `FACEBOOK_STORIES_PAGE_TOKEN`: a
     non-expiring Page token with `pages_manage_posts`, verified on
     production). No id is hard-coded.
  3. **TikTok, through Postiz** -- `postiz.post_tiktok`: the rendered file
     uploaded to Postiz, the TikTok integration's settings read and honoured
     before posting, DIRECT_POST (never UPLOAD), public. TikTok often
     returns no id: that leg then reads "posted, id pending", never an
     invented id. `POSTIZ_API_KEY` unset -> "not configured".

Every leg carries the "Made with AI" label: the caption must end with
`AI_LABEL` (refused before the card exists, by `vet`), it is the same
caption on all three, and the video itself has the label burned into every
frame by the renderer.

Each leg reports one of: posted, refused: <reason>, or not configured:
<what is unset> (TikTok may also read "posted, id pending"). The action
succeeds when at least one leg posted; the detail always names all three.
The rollback takes back what posted -- Instagram delete, Facebook delete,
and the Postiz post while it is still queued -- and for a TikTok post that
has already published it records a MANUAL note instead: deleting a
published post is Tier 3.

The public URL is the render's own `/media/{token}` link, read from
`content_media` at run time (never a card parameter, so the token stays out
of the audit chain); an expired token is re-minted for 24 h when he approves
a card the day after it was raised.
"""
from __future__ import annotations

import asyncio
import json
import os
import secrets
from datetime import UTC, datetime

import httpx

from ...kernel.contracts import ActionResult
from ...kernel.state import execute_sql, fetch_one
from .drafts import AI_LABEL

POSTED, REFUSED, NOT_CONFIGURED = "posted", "refused", "not configured"
ID_PENDING = "posted, id pending"
FB_ENV = ("FACEBOOK_STORIES_PAGE_ID", "FACEBOOK_STORIES_PAGE_TOKEN")
NAMES = {"instagram": "Instagram (stories)", "facebook": "Facebook Page",
         "tiktok": "TikTok (via Postiz)"}


def vet_publish_reel(params: dict) -> str | None:
    caption = str(params.get("caption") or "")
    if not caption.rstrip().endswith(AI_LABEL):
        return (f"every leg carries the AI label — the caption must end with "
                f"{AI_LABEL!r}")
    return None


def topic_for_publish_reel(params: dict) -> str:
    return "instagram_stories"


# ----------------------------------------------------------------- the file

async def _media(media_id: int) -> tuple[dict | None, str | None]:
    row = await fetch_one(
        """SELECT id, status, path, media_token, media_token_expires_at
           FROM content_media WHERE id = $1""", media_id)
    if row is None:
        return None, f"no rendered media {media_id}"
    if row["status"] != "done" or not row["path"]:
        return None, f"media {media_id} is {row['status']!r}, not a finished render"
    if not await asyncio.to_thread(os.path.isfile, row["path"]):
        return None, f"media {media_id}'s file is no longer on disk"
    return dict(row), None


def _fresh(row: dict) -> bool:
    exp = row.get("media_token_expires_at")
    return bool(row.get("media_token")) and exp is not None and exp > datetime.now(UTC)


async def _public_url(row: dict) -> tuple[str | None, str | None]:
    """The render's public URL, re-minting a lapsed 24 h token."""
    from . import MEDIA_TOKEN_TTL, _media_url
    if not os.getenv("ATLAS_HOST", "").strip():
        return None, "ATLAS_HOST is unset, so no platform could fetch the video"
    token = row.get("media_token")
    if not _fresh(row):
        token = secrets.token_urlsafe(32)
        await execute_sql(
            """UPDATE content_media SET media_token = $2,
                      media_token_expires_at = $3 WHERE id = $1""",
            row["id"], token, datetime.now(UTC) + MEDIA_TOKEN_TTL)
    return _media_url(token), None


# ----------------------------------------------------------------- legs

def _fb_cfg() -> tuple[str, str] | None:
    page, token = (os.getenv(name, "").strip() for name in FB_ENV)
    return (page, token) if page and token else None


IG_LEG = "instagram.publish"


async def _instagram(caption: str, url: str, *, parent: str,
                     media_id: int) -> tuple[str, str, str | None]:
    """The Instagram leg: `instagram.publish`'s function, under that
    action's own gates (RA-C2, `kernel.legs`): it runs only under a parent
    listed in code, only while `instagram.publish` is registered, allowed
    and not paused, within its max_per_hour, and each attempt is in the
    audit chain as a leg of `parent`."""
    from ...kernel import legs
    from .. import instagram
    try:
        instagram._cfg("stories")
    except instagram.NoAccount as exc:
        return NOT_CONFIGURED, str(exc), None
    if (why := await legs.gate(IG_LEG, parent, account="stories",
                               media_id=media_id)) is not None:
        return REFUSED, why, None
    await legs.start(IG_LEG, parent, media_id=media_id)
    try:
        result = await instagram.publish(caption, url, kind="reel",
                                         account="stories", content_kind="ai_story")
    except Exception as exc:                            # noqa: BLE001
        said = instagram._why(exc)
        await legs.finish(IG_LEG, parent, False, said, media_id=media_id)
        return REFUSED, said, None
    await legs.finish(IG_LEG, parent, result.ok, result.detail, media_id=media_id)
    if not result.ok:
        return REFUSED, result.detail, None
    return POSTED, result.detail, result.rollback_token


async def _facebook(caption: str, url: str) -> tuple[str, str, str | None]:
    cfg = _fb_cfg()
    if cfg is None:
        return NOT_CONFIGURED, f"{' / '.join(FB_ENV)} unset", None
    page, token = cfg
    from ..instagram import GRAPH, GraphError, _raise_for, _unreachable
    from .legsync import spend
    spend("facebook")               # the Page's hourly window, any path
    try:
        async with httpx.AsyncClient(timeout=120) as c:
            r = await c.post(f"{GRAPH}/{page}/videos",
                             data={"file_url": url, "description": caption},
                             headers={"Authorization": f"Bearer {token}"})
        _raise_for(r, token)
        video_id = str(r.json()["id"])
    except GraphError as exc:
        return REFUSED, str(exc), None
    except httpx.HTTPError as exc:
        return REFUSED, str(_unreachable(exc, token)), None
    except (KeyError, ValueError):
        return REFUSED, "Facebook answered without a video id", None
    return POSTED, f"video {video_id} on the Page", video_id


def _read(path: str) -> bytes:
    with open(path, "rb") as fh:
        return fh.read()


TIKTOK_TAGS = ("#fyp", "#aistory", "#storytime")
TIKTOK_MAX = 2200


def platform_caption(caption: str, platform: str, lane: str = "stories") -> str:
    """The caption a platform actually gets, derived in code from the one he
    approved -- shown on the same card, so what posts is what he saw. TikTok
    gets its discovery tags added before the AI label (which stays last):
    `#fyp` for every lane, and the stories tags only on a stories reel (a
    football or course reel is not an #aistory). Never past the platform's
    hashtag ceiling (`hashtags.MAX_PER_PLATFORM`). Instagram and Facebook
    get the approved caption unchanged."""
    from . import hashtags
    if platform != "tiktok":
        return caption
    tags = TIKTOK_TAGS if lane == "stories" else (hashtags.TIKTOK_DISCOVERY,)
    out = hashtags.apply(caption, list(tags), "tiktok")
    return out[:TIKTOK_MAX] if len(out) > TIKTOK_MAX else out


async def _lane(media_id: int) -> str:
    """The reel's lane for its TikTok tags; the stories account's own lane
    when the draft cannot be read."""
    from . import hashtags
    try:
        lane, _, _ = await hashtags.media_lane(media_id)
    except Exception:                                   # noqa: BLE001
        return "stories"
    return lane if lane in ("football", "course", "business") else "stories"


async def _tiktok(caption: str, path: str) -> tuple[str, str, str | None]:
    """Postiz takes the file itself (an upload), not our URL."""
    from . import postiz
    if not postiz.configured():
        return NOT_CONFIGURED, f"{postiz.KEY_ENV} unset", None
    from .legsync import spend
    spend("tiktok")                 # TikTok's hourly window, any path
    try:
        video = await asyncio.to_thread(_read, path)
    except OSError as exc:
        return REFUSED, f"the render could not be read: {exc.strerror}", None
    return await postiz.post_tiktok(video, caption)


def _line(leg: str, status: str, said: str) -> str:
    return f"{NAMES[leg]}: {status}" + (f" — {said}" if said else "")


# ----------------------------------------------------------------- the action

async def legs_ready(caption: str = f"reel\n\n{AI_LABEL}") -> tuple[int, list[str]]:
    """How many of the three platforms could take a reel right now, and one
    line per leg saying why or why not. Reads only; posts nothing."""
    from .. import instagram
    from . import postiz
    lines, ready = [], 0
    ig = await instagram.publish_dry(caption, "https://example.invalid/reel.mp4",
                                     kind="reel", account="stories",
                                     content_kind="ai_story")
    if ig.ok:
        ready += 1
        lines.append(_line("instagram", "ready", ""))
    else:
        lines.append(_line("instagram", NOT_CONFIGURED if "not configured" in
                           ig.detail else REFUSED, ig.detail))
    if _fb_cfg() is not None:
        ready += 1
        lines.append(_line("facebook", "ready", ""))
    else:
        lines.append(_line("facebook", NOT_CONFIGURED, f"{' / '.join(FB_ENV)} unset"))
    if postiz.configured():
        ok, said = await postiz.readiness()
        ready += ok
        lines.append(_line("tiktok", "ready" if ok else REFUSED, said))
    else:
        lines.append(_line("tiktok", NOT_CONFIGURED, f"{postiz.KEY_ENV} unset"))
    return ready, lines


async def publish_reel_dry(media_id: int, caption: str) -> ActionResult:
    """What each leg would do, per platform, without posting anything."""
    if (why := vet_publish_reel({"caption": caption})) is not None:
        return ActionResult(False, f"refused: {why}")
    if len(caption) > 2200:
        return ActionResult(False, "caption exceeds 2200 characters")
    row, why = await _media(media_id)
    if row is None:
        return ActionResult(False, why or "no media")
    if (why := await _already_posted(media_id)) is not None:
        return ActionResult(False, why)
    if not os.getenv("ATLAS_HOST", "").strip():
        return ActionResult(False, "ATLAS_HOST is unset, so no platform could "
                                   "fetch the video")
    ready, lines = await legs_ready(caption)
    link = ("the render's link is live" if _fresh(row) else
            "the render's link has lapsed; approving re-mints it for 24 h")
    detail = f"media {media_id}, {link}. " + " · ".join(lines)
    if not ready:
        return ActionResult(False, f"no platform is configured to take it: {detail}")
    return ActionResult(True, f"would post to {ready} of 3: {detail}")


async def _already_posted(media_id: int) -> str | None:
    """RA-B7: a reel is posted once. A second run for the same render -- a
    retried card, the host import and the calendar both reaching it -- finds
    the first run's `reel_posts` row and posts nothing. Only the Instagram
    and TikTok legs are recorded there (Facebook is not measured), which is
    enough: every posted reel has at least one of them unless Facebook was
    the only leg that posted. Unreadable is a refusal, not a guess."""
    try:
        prior = await fetch_one(
            """SELECT platform, post_ref FROM reel_posts
                WHERE media_id = $1 ORDER BY at LIMIT 1""", media_id)
    except Exception as exc:                            # noqa: BLE001
        from .._http import explain
        return (f"whether reel {media_id} already posted could not be read "
                f"({explain(exc)}), so it was not posted")
    if prior:
        return (f"reel {media_id} {ALREADY_POSTED} ({prior['platform']} "
                f"{prior['post_ref']}); not posted again")
    return None


async def publish_reel(media_id: int, caption: str, *,
                       parent: str = "content.publish_reel") -> ActionResult:
    """`parent` names the action this run is (`kernel.legs.PARENTS`): the
    approved card, or `autopublish_reel` passing its own name. It is not a
    declared parameter, so a card or a model can never set it."""
    if (why := vet_publish_reel({"caption": caption})) is not None:
        return ActionResult(False, f"refused: {why}")
    row, why = await _media(media_id)
    if row is None:
        return ActionResult(False, why or "no media")
    if (why := await _already_posted(media_id)) is not None:
        return ActionResult(False, why)
    url, why = await _public_url(row)
    if url is None:
        return ActionResult(False, why or "no public URL")
    results = {"instagram": await _instagram(caption, url, parent=parent,
                                             media_id=media_id),
               "facebook": await _facebook(caption, url),
               "tiktok": await _tiktok(platform_caption(
                   caption, "tiktok", await _lane(media_id)), row["path"])}
    posted = {leg: token for leg, (status, _, token) in results.items()
              if status in (POSTED, ID_PENDING) and token}
    lines = [_line(leg, status, said) for leg, (status, said, _) in results.items()]
    detail = f"reel {media_id}: " + " · ".join(lines)
    evidence = {leg: {"status": status, "detail": said}
                for leg, (status, said, _) in results.items()}
    if not posted:
        return ActionResult(False, f"posted nowhere — {detail}", evidence=evidence)
    await _record_posts(media_id, posted)
    return ActionResult(True, detail, evidence=evidence,
                        rollback_token=json.dumps(posted, sort_keys=True))


# ----------------------------------------------------------------- automatic
#
# the owner, 2026-09-27: "make sure everything is fully automated so I don't
# have to manually do anything." Until then every reel waited for his tap
# (content.publish_reel, Tier ASK). He asked for that gate to go, for the
# AI-stories account's reels only, so `content.autopublish_reel` is Tier
# AUTO -- a tier assigned here, in code, and reviewed like any other change
# (invariant 3) -- and it posts only what passes every check below. What it
# will not do is decided in code, not by a model:
#
#   * `REELS_AUTOPUBLISH` off (on by default) -> nothing posts itself; the
#     reel goes back to a Tier 2 card, exactly as before.
#   * at most `REELS_AUTOPUBLISH_PER_DAY` (default 3) automatic posts a New
#     York day, counted from `reel_posts`;
#   * the caption ends with the AI label and names no real brand or person
#     (`guard`), and the file is a finished 1080x1920 render with sound,
#     at least 5 s long, read by ffprobe -- a render that cannot be read
#     does not post.
#
# Anything that fails a check is not skipped: it becomes the ordinary card,
# with the reason first. The rollback deletes the posts (`unpublish_reel`).

AUTO_PER_DAY_DEFAULT = 3  # the owner, 2026-09-29: two stories + one soccer reel
MIN_SECONDS = 5.0
# A reel held back only because a platform's hourly allowance is spent: it
# is not a quality failure, so `kernel.reels.post_or_card` waits instead of
# remaking it or raising a card.
RATE_HELD = "hourly allowance is spent"
# A render that already has a post (RA-B7): terminal, never a remake or a card.
ALREADY_POSTED = "already posted"


def autopublish_enabled() -> bool:
    return os.getenv("REELS_AUTOPUBLISH", "on").strip().lower() not in (
        "0", "off", "false", "no")


def autopublish_per_day() -> int:
    try:
        return max(0, int(os.getenv("REELS_AUTOPUBLISH_PER_DAY",
                                    str(AUTO_PER_DAY_DEFAULT))))
    except ValueError:
        return AUTO_PER_DAY_DEFAULT


async def posted_today() -> int:
    """Reels FIRST posted since New York midnight, by any path. A leg caught
    up later (`legsync`, `content.post_missing_leg`) writes a new
    `reel_posts` row for a reel already counted the day it first posted, so
    it never takes a new day's slot."""
    from zoneinfo import ZoneInfo
    zone = ZoneInfo("America/New_York")
    midnight = datetime.now(UTC).astimezone(zone).replace(
        hour=0, minute=0, second=0, microsecond=0)
    row = await fetch_one(
        """SELECT count(DISTINCT media_id) AS n FROM reel_posts WHERE at >= $1
              AND NOT EXISTS (SELECT 1 FROM reel_posts earlier
                               WHERE earlier.media_id = reel_posts.media_id
                                 AND earlier.at < $1)""",
        midnight)
    return int(row["n"]) if row else 0


async def _caption_refusal(media_id: int, caption: str) -> str | None:
    """The stories' caption rule, or the football lane's: a football reel's
    caption names the real players its numbers are about (the owner, 2026-09-29),
    and still no brand, trademark or American football."""
    from . import football, guard
    try:
        stats = await football.is_football_media(media_id)
    except Exception:                                   # noqa: BLE001
        stats = False               # unreadable: the stricter stories rule
    if stats:
        return guard.football_caption_refusal(caption)
    return guard.refusal(caption)


async def autopublish_check(media_id: int, caption: str) -> str | None:
    """None when this reel may post itself now; otherwise why not."""
    if not autopublish_enabled():
        return "automatic posting is off (REELS_AUTOPUBLISH)"
    # First, before any count: a render that already posted is done.
    if (why := await _already_posted(media_id)) is not None:
        return why
    if (why := vet_publish_reel({"caption": caption})) is not None:
        return why
    if (why := await _caption_refusal(media_id, caption)) is not None:
        return why
    # Series order (legsync): part N waits while part N-1 is still missing
    # a leg that will be synced -- never posted to a platform before it.
    from . import legsync
    if (why := await legsync.series_hold(media_id)) is not None:
        return why
    try:
        done = await posted_today()
    except Exception:                                   # noqa: BLE001
        return "today's posts could not be counted, so nothing posts itself"
    if done >= autopublish_per_day():
        return (f"today's {autopublish_per_day()} automatic post(s) are done "
                f"(REELS_AUTOPUBLISH_PER_DAY)")
    # RA-C2: the Instagram leg shares instagram.publish's hourly limit. A
    # reel that would lose its Instagram leg to it waits whole rather than
    # posting to two platforms of three.
    from ...kernel import legs
    if (why := legs.over_limit(IG_LEG)) is not None:
        return f"Instagram's {RATE_HELD}: {why}"
    row, why = await _media(media_id)
    if row is None:
        return why or "no media"
    return await render_refusal(row["path"])


async def render_refusal(path: str, max_seconds: float | None = None) -> str | None:
    """The file's own quality gate, or None: 1080x1920, with sound that is
    not silent, at least `MIN_SECONDS` long (and at most `max_seconds` when
    given -- a YouTube Short's limit). A file that cannot be read fails."""
    from . import ffmpeg
    try:
        info = await ffmpeg.probe(path)
    except ffmpeg.FfmpegError as exc:
        return f"the render could not be checked: {exc}"
    if (info["width"], info["height"]) != (ffmpeg.WIDTH, ffmpeg.HEIGHT):
        return f"the render is {info['width']}x{info['height']}, not 1080x1920"
    if not info["audio"]:
        return "the render has no sound"
    try:
        level = await ffmpeg.mean_volume(path)
    except ffmpeg.FfmpegError as exc:
        return f"the render's sound could not be checked: {exc}"
    if level <= ffmpeg.SILENT_DB:
        return f"the render's sound is silent ({level:.0f} dB)"
    if info["seconds"] < MIN_SECONDS:
        return f"the render is {info['seconds']:.1f} s, under {MIN_SECONDS:.0f} s"
    if max_seconds is not None and info["seconds"] > max_seconds:
        return (f"the render is {info['seconds']:.1f} s, over {max_seconds:.0f} s")
    return None


async def autopublish_reel_dry(media_id: int, caption: str) -> ActionResult:
    if (why := await autopublish_check(media_id, caption)) is not None:
        return ActionResult(False, f"not posted automatically: {why}")
    return await publish_reel_dry(media_id, caption)


async def autopublish_reel(media_id: int, caption: str) -> ActionResult:
    if (why := await autopublish_check(media_id, caption)) is not None:
        return ActionResult(False, f"not posted automatically: {why}")
    return await publish_reel(media_id, caption, parent="content.autopublish_reel")


async def _record_posts(media_id: int, posted: dict[str, str]) -> None:
    """One `reel_posts` row per measured platform, so the daily metrics read
    (`metrics.check_reel_metrics`) knows what to read and which format it
    belongs to. Best effort: the posts are out either way, and a failure
    here only means that reel is not measured -- logged, never raised."""
    import logging
    try:
        row = await fetch_one(
            """SELECT d.source FROM content_media m
                 JOIN content_drafts d ON d.id = m.draft_id
                WHERE m.id = $1""", media_id)
        source = str(row["source"]) if row else ""
        format_id = (int(source.split(":", 1)[1])
                     if source.startswith("reel_format:") else None)
        for platform in ("instagram", "tiktok"):
            ref = posted.get(platform)
            if not ref:
                continue
            if platform == "instagram":
                ref = ref.partition(":")[2] or ref      # "stories:<media id>"
            await execute_sql(
                """INSERT INTO reel_posts (media_id, format_id, platform, post_ref)
                   VALUES ($1, $2, $3, $4)""", media_id, format_id, platform, ref)
    except Exception as exc:                            # noqa: BLE001
        logging.getLogger("atlas.content").warning(
            "reel %s posted but not recorded for measuring: %s", media_id, exc)


async def unpublish_reel(token: str) -> ActionResult:
    """Take back what posted: the Instagram and Facebook posts are deleted
    (the undo of ATLAS's own write), and the TikTok post is deleted in
    Postiz only while it is still queued -- once published it gets a MANUAL
    note instead (deleting a published post is Tier 3). Succeeds only when
    everything that posted is gone."""
    try:
        posted = json.loads(token)
    except ValueError:
        posted = None
    if not isinstance(posted, dict):
        return ActionResult(False, f"malformed rollback token: {token!r}")
    lines, complete = [], True
    if "instagram" in posted:
        from .. import instagram
        try:
            result = await instagram.unpublish(str(posted["instagram"]))
            ok, said = result.ok, result.detail
        except Exception as exc:                        # noqa: BLE001
            ok, said = False, instagram._why(exc)
        complete &= ok
        lines.append(_line("instagram", "deleted" if ok else "not deleted", said))
    if "facebook" in posted:
        ok, said = await _delete_facebook(str(posted["facebook"]))
        complete &= ok
        lines.append(_line("facebook", "deleted" if ok else "not deleted", said))
    if "tiktok" in posted:
        from . import postiz
        ok, said = await postiz.undo(str(posted["tiktok"]))
        complete &= ok
        lines.append(_line("tiktok", "deleted" if ok else "not deleted", said))
    return ActionResult(complete, " · ".join(lines) or "nothing had posted")


async def _delete_facebook(video_id: str) -> tuple[bool, str]:
    cfg = _fb_cfg()
    if cfg is None:
        return False, f"{' / '.join(FB_ENV)} unset, so video {video_id} was not deleted"
    _, token = cfg
    from ..instagram import GRAPH, _unreachable
    try:
        async with httpx.AsyncClient(timeout=20) as c:
            r = await c.delete(f"{GRAPH}/{video_id}",
                               headers={"Authorization": f"Bearer {token}"})
    except httpx.HTTPError as exc:
        return False, str(_unreachable(exc, token))
    if r.status_code != 200:
        return False, f"Graph API HTTP {r.status_code} deleting video {video_id}"
    return True, f"video {video_id}"


async def check_reel_legs():
    """Which of the three legs could take a reel right now, from what is
    configured. A leg that is not configured is UNKNOWN (never OK)."""
    from ...kernel.contracts import Health, Observation
    from .. import instagram
    from . import postiz
    parts, missing = [], []
    try:
        instagram._cfg("stories")
        parts.append("Instagram (stories): configured")
    except instagram.NoAccount as exc:
        missing.append(f"Instagram (stories): {exc}")
    if _fb_cfg() is not None:
        parts.append("Facebook Page: configured")
    else:
        missing.append(f"Facebook Page: {' / '.join(FB_ENV)} unset "
                       f"(needs pages_manage_posts)")
    if postiz.configured():
        parts.append("TikTok (via Postiz): configured, posts public by DIRECT_POST")
    else:
        missing.append(f"TikTok (via Postiz): {postiz.KEY_ENV} unset")
    detail = " · ".join(missing + parts)
    return Observation("reel_legs", Health.UNKNOWN if missing else Health.OK,
                       detail=detail + " (configuration only; each leg's token "
                                       "is proven by its first post)",
                       evidence={"configured": len(parts), "of": 3})
