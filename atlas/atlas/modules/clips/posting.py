"""From a cut clip to a Whop claim: the post, the post URL, and the
submission card.

**Posting** goes through the existing Instagram poster, never a new one:
`instagram.publish` with `account="clips"` (@atlas.clipsdrop,
`INSTAGRAM_CLIPS_ACCOUNT_ID` / `INSTAGRAM_CLIPS_TOKEN`) and
`content_kind="campaign_clip"`, whose account rule is the campaigns' FTC
`#Ad` line rather than the AI label. `clips.publish_clip` is Tier 1 since
2026-09-30 (Miro: clips post themselves, the reels lane's way; the
"automatic" section below), gated in code and run by the intake
(`kernel.clip_intake`); a clip it holds is a Tier 2 card with the reason.
The Graph API fetches the file from a 24 h `GET /media/clip.<token>` link,
minted only when it posts. The caption is re-checked against the
campaign's rules before every post.

**YouTube Shorts** go through the youtube module's upload path to its
`clips` channel (`YOUTUBE_CLIPS_*`), never a new client:
`clips.publish_clip_youtube` (Tier 2, one card per clip) uploads the clip
-- which always lands private, `api.upload` refuses anything else -- then
sets it public and reads its privacy back, recording it posted at
`youtube.com/shorts/<id>` only once YouTube says it is public. Approving
the card IS Miro's word to publish that one Short, and the card says so.
The title is the approved post text whole, then `#Ad`, then the
campaign's YouTube handle; the description the campaign's full caption (`captions.youtube_title`,
`build_caption(..., "youtube")`). The uploaded video's id is kept the
moment it exists, so approving again after a failed publish never uploads
a second copy. Its rollback sets the Short private -- deletion is Tier 3 --
and marks the clip `withdrawn`, never posted again.

**TikTok** goes through Postiz, the reels lane's path, to the clips
lane's own TikTok @atlasclips12 (`POSTIZ_TIKTOK_CLIPS_INTEGRATION`, beside
`POSTIZ_API_KEY`): `clips.publish_clip_tiktok` (Tier 1 since 2026-09-30,
the same gates and holds) hands `postiz.post_tiktok_clip` the clip's PATH
-- which takes nothing but a lane-cut file, and never the AI-stories
integration -- and the exact caption, re-checked against the campaign's
rules. It is recorded posted once Postiz accepts it; its URL only when
Postiz returns one on that account (never built from an id), else Miro
sends it (`clips.record_link`) and the Whop card follows. Its rollback
deletes the Postiz post while it is still queued; a published one is a
MANUAL note (deletion is Tier 3).

**YouTube Shorts through Postiz** (2026-09-29) go to the same clips channel,
AtlasClips (@miroatlas), through the Postiz integration
`POSTIZ_YOUTUBE_CLIPS_INTEGRATION` names: `clips.publish_clip_youtube_postiz`
(Tier 1 since 2026-09-30, the TikTok leg's shape) hands
`postiz.post_youtube_clip` the lane-cut file, the title
(`captions.youtube_title`: the approved text whole, `#Ad`, the campaign's
YouTube handle -- refused past 100 characters, never cut), the full caption
as the description and the campaign hashtags as tags. Only for a campaign
whose rules pay on YouTube (`"youtube" in campaign.platforms`), and only for
a Short: 9:16 and at most `SHORTS_MAX_S` seconds, read from the clip's
window and from ffprobe again before it posts. A window past it is never
posted to YouTube and `signals` says why. It is recorded posted once
Postiz accepts it, at `post_account` `youtube:clips` (assumed the same
channel as the Data API's `clips`, so `max_accounts` counts it once); its
URL only when Postiz returns one. Its rollback deletes the Postiz post
while still queued; a published Short is a MANUAL note (deletion is Tier
3). A row the Data API already uploaded (`post_ref` set, private) stays on
that path, never posted a second time here. With both routes configured,
Postiz is used.

When an account cannot be reached -- its env unset, or for Instagram
`ATLAS_HOST` unset so no link would resolve -- the card is `clips.hand_off`
instead: the same file and caption (for YouTube, title and description),
for Miro to post by hand, and approving it records that he has it.

**One platform per clip** when the campaign's rules forbid the same video
on more than one account (`cross_post_allowed` False; both live ones): the
cut makes one row per clip already, and every post and hand-off here
refuses a clip whose file already went (or is going) to another platform.
For an older clip with a row per platform, only the highest-paying one is
posted or carded (`cut.choose_platform`), and the card says why. **Both
platforms** when the rules allow it: a row, a caption, a post and a Whop
link per platform from the one cut, each leg claimed once (the "automatic"
section below).

**Accounts** (`max_accounts`, per campaign, per platform): each clip row
records the account it went to (`post_account`), and posting or handing
off refuses an account beyond the campaign's limit on that platform. There
is one account per platform today, so this is a guard, not a feature.

**The post URL** is read back from Instagram (`permalink`) when ATLAS
posts; for a hand-posted clip, or a permalink that could not be read, it
comes through `clips.record_link`, which runs only when Miro himself says
it (`principal_only`): a URL a model read somewhere must never become a
payout claim.

**Submitting to Whop** is never done by ATLAS: it touches Miro's payout
account and ATLAS has no verified way in. The agent on his Mac submits in
his own Chrome and records it through `clips.record_whop_submission`
(`whop.py`, Miro 2026-09-29). The Tier 2 card `clips.submit_to_whop` -- the
post URL and the campaign's Whop link (or that it is not on file);
approving records that he submitted it -- comes up at once for a clip the
agent will never see, and for one it is offered only as the fallback, 48 h
after it posted unsubmitted (`whop.card_due`). Every writer of `post_url` here also sets
`post_url_source` (the platform's own response, or `record_link` for
Miro's word): the agent is offered only those.
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from ...kernel.contracts import ActionResult, Severity, Signal
from ...kernel.state import execute_sql, fetch_all, fetch_one
from ..content import ffmpeg
from . import campaigns, captions, sources

ACCOUNT = "@atlas.clipsdrop"
TT_HANDLE = "@atlasclips12"
IG_ACCOUNT = "clips"                 # instagram.ACCOUNTS key
YT_CHANNEL = "clips"                 # youtube.CHANNELS key
TT_ACCOUNT = "clips"                 # postiz's clips integration
YT_CHANNEL_NAME = "AtlasClips (@miroatlas)"   # POSTIZ_YOUTUBE_CLIPS_INTEGRATION
# A YouTube Short: vertical and at most this long. The cut's windows stop
# at 60 s (cut.MAX_WINDOW); an encoded file may run a few frames past its
# window, so ffprobe's reading gets SHORTS_PAD_S of slack, the window none.
SHORTS_MAX_S = 60.0
SHORTS_PAD_S = 0.1
# Where each platform's clips go, as Miro reads it on a card ...
ACCOUNTS = {"instagram": f"{ACCOUNT} (Instagram)",
            "tiktok": f"{TT_HANDLE} (TikTok)",
            "youtube": "the clips YouTube channel (Shorts)"}
# ... and as clip_outputs.post_account records it.
POST_ACCOUNT = {"instagram": f"instagram:{IG_ACCOUNT}",
                "tiktok": f"tiktok:{TT_ACCOUNT}",
                "youtube": f"youtube:{YT_CHANNEL}"}
# A clip's row on another platform in one of these states means that
# video is already up (or on its way) somewhere.
COMMITTED = ("handed_off", "posted", "submitted", "withdrawn")
# A row waiting to post: cut (made), or a draft the brand approved on Whop.
WAITING = ("made", "draft_approved")
# A draft on its way to the brand (whop.drafts): never posted or handed off.
DRAFTING = ("draft_pending", "draft_submitted")
CONTENT_KIND = "campaign_clip"       # instagram.ACCOUNT_KINDS["clips"]
TOKEN_PREFIX = "clip."               # api.media serves these from clip_outputs
TOKEN_TTL = timedelta(hours=24)

POST_URL = {
    "instagram": re.compile(r"^https://(www\.)?instagram\.com/(reel|reels|p)/"
                            r"[A-Za-z0-9_-]+/?(\?[^\s]*)?$"),
    "tiktok": re.compile(r"^https://(www\.)?tiktok\.com/@[A-Za-z0-9._]+/video/"
                         r"\d+/?(\?[^\s]*)?$"),
    "youtube": re.compile(r"^https://(www\.)?youtube\.com/shorts/[A-Za-z0-9_-]+"
                          r"/?(\?[^\s]*)?$"),
}


def youtube_api_gap() -> str | None:
    """Why the youtube module's Data API cannot reach the clips channel, or
    None. Reads configuration only. The Data API leg's own gate."""
    from ..youtube import api as yt
    try:
        yt._require_credentials(YT_CHANNEL)
    except yt.NoCredentials as exc:
        return f"the clips YouTube channel: {exc}"
    return None


def youtube_postiz_gap() -> str | None:
    """Why Postiz cannot reach the clips YouTube channel, or None. Reads
    configuration only. The Postiz Shorts leg's own gate."""
    from ..content import postiz
    return postiz.yt_clips_gap()


def youtube_route(row: dict[str, Any]) -> str | None:
    """Which leg posts this YouTube row: "api" for a Short the Data API
    already uploaded (private; it is published there, never a second copy),
    else "postiz" when configured, else "api" when configured, else None."""
    if row.get("post_ref"):
        return "api" if youtube_api_gap() is None else None
    if youtube_postiz_gap() is None:
        return "postiz"
    return "api" if youtube_api_gap() is None else None


def shorts_refusal(seconds: float, frame: tuple[int, int] | None = None,
                   pad: float = 0.0) -> str | None:
    """Why this is not a YouTube Short -- longer than `SHORTS_MAX_S` (plus
    `pad`), or, when its (width, height) is given, not vertical -- or None.
    Pure."""
    if frame is not None and not frame[1] > frame[0] > 0:
        return (f"it is {frame[0]}x{frame[1]}, not vertical, so it is not a "
                f"YouTube Short")
    if seconds > SHORTS_MAX_S + pad:
        return (f"it runs {seconds:.1f} s; a YouTube Short here is at most "
                f"{SHORTS_MAX_S:.0f} s"
                + (f" (plus {pad:.1f} s of encoder padding)" if pad else "")
                + ", so it does not go to YouTube")
    return None


def window_refusal(row: dict[str, Any]) -> str | None:
    """The Shorts length rule on the clip's cut window, when the row has
    one. Pure."""
    start, end = row.get("start_s"), row.get("end_s")
    if start is None or end is None:
        return None
    return shorts_refusal(float(end) - float(start))


def autopost_gap(platform: str = "instagram") -> str | None:
    """Why ATLAS cannot post a clip on `platform` right now, or None when
    it can. Reads configuration only. YouTube is reachable by either leg:
    Postiz (`POSTIZ_YOUTUBE_CLIPS_INTEGRATION`) or the Data API."""
    if platform == "tiktok":
        from ..content import postiz
        return postiz.clips_gap()
    if platform == "youtube":
        api_gap = youtube_api_gap()
        if api_gap is None or youtube_postiz_gap() is None:
            return None
        return f"{youtube_postiz_gap()}; nor through the Data API: {api_gap}"
    if platform != "instagram":
        return f"the clips lane has no {platform} account"
    from .. import instagram
    try:
        instagram._cfg(IG_ACCOUNT)
    except instagram.NoAccount as exc:
        return f"{ACCOUNT}: {exc}"
    if not os.getenv("ATLAS_HOST", "").strip():
        return "ATLAS_HOST is unset, so Instagram could not fetch the file"
    return None


# ----------------------------------------------------------------- automatic
#
# Miro, 2026-09-30: Whop clips post themselves, "the same way the AI reels
# lane does it" -- no approval card per post. So `clips.publish_clip`,
# `clips.publish_clip_tiktok` and `clips.publish_clip_youtube_postiz` are
# Tier AUTO -- a tier assigned in code and reviewed like any other change
# (invariant 3) -- in the shape of `content.autopublish_reel`
# (content/crosspost.py): each posts only past every check its `_*_check`
# makes, and what it will not do is decided in code, not by a model:
#
#   * the campaign is active, names its permission source and has reviewed
#     rules (`campaigns.permission_refusal`, `rules_pending`);
#   * the caption is in the campaign's layout with its disclosure (`#Ad`)
#     alone on its line (`captions.validate`);
#   * the file is a lane-cut 1080x1920 clip with sound, inside the
#     campaign's length band, read by ffprobe now (`probe_refusal`) -- a
#     file that cannot be read does not post;
#   * the account is configured (`autopost_gap`), within the campaign's
#     account limit and its one-platform rule;
#   * then `auto_hold`: `CLIPS_AUTOPUBLISH` off (on by default) -> nothing
#     posts itself, every clip is a Tier 2 card as before; and at most
#     `CLIPS_DAILY_CAP` (default 6, the intake's own cap) posts per platform
#     per UTC day, counted from `clip_outputs`.
#
# A hold answers to Miro: the same check under an approved card (the run
# `approvals.decide` names, `idempotency.current_run()` = `proposal:<id>`)
# passes it and posts. Nothing that fails is skipped in silence:
# `kernel.clip_intake` raises the ordinary post card with the reason first,
# and approving it runs every check again. The rollback takes the post
# down (Instagram: deleted; Postiz: deleted while queued, else a MANUAL
# note) and marks the clip withdrawn. Every automatic post is the
# executor's audit line and a receipt in the clips channel.
#
# **Two legs from one cut** (Miro, 2026-10-01: clip reels on Instagram AND
# TikTok), the shape of `content.post_missing_leg`: when a campaign's
# reviewed rules allow the same clip on more than one account
# (`cross_post_allowed`) the cut makes one `clip_outputs` row per platform
# for the one file (`cut.platforms_for`), each with its own caption in that
# platform's layout, its own post_ref, post_url and Whop submission; the
# intake posts one leg per platform per tick, each through its own action,
# through the same gates and its platform's own daily cap. A leg is
# **claimed once** in `effect_claims` under `(action, "<clip>:<platform>")`
# (`leg_key`) before anything reaches a platform -- settled when it posts,
# released when the platform certainly refused, kept when the outcome is
# unknown -- so two runs of the same row (the intake and an approved card,
# a retry after a timeout) never post twice. The gates that are the
# clip's (the campaign, the file) refuse both legs alike; those that are a
# platform's (its account, its layout, its cap) refuse one. A campaign
# whose rules forbid it (both live ones: "do not copy the same video across
# multiple accounts") stays on ONE platform per clip: `cut.choose_platform`
# at the cut, `_sibling_refusal` and `carded` here.

AUTO_ACTIONS = {"instagram": "clips.publish_clip",
                "tiktok": "clips.publish_clip_tiktok",
                "youtube": "clips.publish_clip_youtube_postiz"}


def leg_key(clip_id: int | str, platform: str) -> str:
    """The `effect_claims` key of one leg: the row and its platform."""
    return f"{int(clip_id)}:{platform}"
# The head of every hold's detail, so the intake tells a hold (a card with
# the reason) from a refusal it must not repeat.
HELD = "held for you"
# The holds that are the day's count, not the clip (`auto_hold`): the intake
# waits on these for tomorrow instead of carding them (2026-10-12).
COUNT_HOLDS = ("(CLIPS_DAILY_CAP)", "could not be counted")
# ffprobe's reading of an encoded file runs a few frames past its window
# either way; the campaign's band gets this much slack.
PROBE_PAD_S = SHORTS_PAD_S


def autopublish_enabled() -> bool:
    """`CLIPS_AUTOPUBLISH`: on by default; off holds every clip for a card."""
    return os.getenv("CLIPS_AUTOPUBLISH", "on").strip().lower() not in (
        "0", "off", "false", "no")


def daily_cap() -> int:
    """`CLIPS_DAILY_CAP` (default 6): the intake's clips per campaign per UTC
    day, and here the posts per platform per UTC day. Read fresh."""
    from .intake import daily_cap as cap
    return cap()


def answered_by_card() -> bool:
    """Whether this run is an approved card's: `approvals.decide` names the
    run `proposal:<id>` around the executor, so the hold it answers is
    passed. A Tier 1 run from the intake names none."""
    from ...kernel import idempotency
    return (idempotency.current_run() or "").startswith("proposal:")


async def posted_today(platform: str, now: datetime | None = None) -> int:
    """Clips posted on `platform` since midnight UTC, by any path (the
    intake, a card, Miro's own link): the cap is on the account's day, not
    on who pressed the button."""
    from .intake import today_utc
    row = await fetch_one(
        """SELECT count(*) AS n FROM clip_outputs
           WHERE platform = $1 AND posted_at >= $2::timestamptz
             AND status IN ('posted', 'submitted')""", platform, today_utc(now))
    return int(row["n"]) if row is not None else 0


async def auto_hold(platform: str) -> str | None:
    """Why this clip is held for Miro rather than posted on its own, or
    None. Asked last, after every check of the clip itself, and never of an
    approved card: his approval is the answer to a hold."""
    if answered_by_card():
        return None
    if not autopublish_enabled():
        return f"{HELD}: automatic posting is off (CLIPS_AUTOPUBLISH)"
    try:
        done = await posted_today(platform)
    except Exception:  # noqa: BLE001
        return (f"{HELD}: today's posts on {platform} could not be counted, "
                f"so nothing posts itself")
    cap = daily_cap()
    if done >= cap:
        return (f"{HELD}: today's {cap} post(s) on {platform} are done "
                f"(CLIPS_DAILY_CAP)")
    return None


async def probe_facts(path: Path | str) -> tuple[dict[str, Any] | None, str | None]:
    """What the file is, read by ffprobe now, or why it could not be read."""
    try:
        return await ffmpeg.probe(str(path)), None
    except ffmpeg.FfmpegError as exc:
        return None, f"the clip could not be checked: {exc}"


def probe_refusal(campaign: campaigns.Campaign, facts: dict[str, Any]) -> str | None:
    """The file's own gate on ffprobe's reading: 1080x1920 (the cut's frame,
    `ffmpeg.WIDTH` x `HEIGHT`), with sound, inside the campaign's length
    band. Pure."""
    frame = (int(facts["width"]), int(facts["height"]))
    if frame != (ffmpeg.WIDTH, ffmpeg.HEIGHT):
        return f"the clip is {frame[0]}x{frame[1]}, not 1080x1920"
    if not facts["audio"]:
        return "the clip has no sound"
    seconds = float(facts["seconds"])
    floor = float(campaign.min_seconds or 0)
    if seconds + PROBE_PAD_S < floor:
        return (f"the clip runs {seconds:.1f} s, under {campaign.name}'s "
                f"{floor:.0f} s minimum")
    if seconds > float(campaign.max_seconds) + PROBE_PAD_S:
        return (f"the clip runs {seconds:.1f} s, over {campaign.name}'s "
                f"{campaign.max_seconds} s maximum")
    return None


async def _file_check(campaign: campaigns.Campaign, row: dict[str, Any]
                      ) -> tuple[Path | None, dict[str, Any] | None, str | None]:
    """The clip's file, confined to its campaign's out folder and a lane cut,
    cut with the brand assets the campaign requires, with ffprobe's reading
    of it; or why not."""
    clip_id, filename = int(row["id"]), str(row["filename"])
    if (why := brand_refusal(campaign, row)) is not None:
        return None, None, why
    folder = sources.out_dir(campaign.slug)
    path = await asyncio.to_thread(sources.confined, folder / filename, folder)
    if path is None or not await asyncio.to_thread(sources.lane_clip, path):
        return None, None, f"clip {clip_id}'s file is no longer in {folder}"
    facts, why = await probe_facts(path)
    if facts is None:
        return None, None, f"clip {clip_id}: {why}"
    if (why := probe_refusal(campaign, facts)) is not None:
        return None, None, f"clip {clip_id}: {why}"
    return path, facts, None


def not_waiting(clip_id: int | str, status: str) -> str:
    """Why a row in `status` is not one to post, naming a draft's place on
    its way to the brand. Pure."""
    said = {"draft_pending": "waiting for the Mac agent to upload its draft on Whop",
            "draft_submitted": "waiting for the brand's decision on its draft"}
    if status in said:
        return (f"clip {clip_id} is {status}: {said[status]}; nothing posts before "
                f"draft_approved")
    return f"clip {clip_id} is {status}, not waiting to be posted"


def approval_refusal(campaign: campaigns.Campaign, row: dict[str, Any]) -> str | None:
    """Why this clip may not post before Whop's approval of its draft: its
    campaign asks for one (`approval_before_post`) and the row is not
    `draft_approved`. A `made` row of such a campaign was cut before the
    rule; the Mac agent uploads it as a draft like any other. Pure."""
    status = str(row.get("status") or "")
    if not campaign.approval_before_post or status == "draft_approved":
        return None
    from . import whop
    if (why := whop.not_connected(str(row.get("platform") or "instagram"))) is not None:
        return f"clip {row['id']} holds: {why}"
    said = {"made": "cut before the rule; the Mac agent uploads it as a draft",
            "draft_pending": "waiting for the Mac agent to upload the draft",
            "draft_submitted": "waiting for the brand's decision",
            "withdrawn": "the brand rejected its draft"}.get(status, "not approved")
    return (f"clip {row['id']} needs {campaign.name}'s approval of its draft on "
            f"Whop first ({status}: {said})")


def auto_action(row: dict[str, Any]) -> str | None:
    """The Tier 1 action that posts this waiting row on its own, or None:
    Instagram and TikTok when their account is configured, YouTube when
    Postiz reaches the clips channel. Reads configuration only."""
    platform = str(row.get("platform") or "instagram")
    if platform == "youtube":
        return AUTO_ACTIONS["youtube"] if youtube_route(row) == "postiz" else None
    if platform in AUTO_ACTIONS and autopost_gap(platform) is None:
        return AUTO_ACTIONS[platform]
    return None


def account_refusal(campaign: campaigns.Campaign, platform: str, account: str,
                    used: set[str]) -> str | None:
    """Why posting this campaign from `account` would pass its account
    limit on `platform`, or None. Counted per platform: one Instagram and
    one YouTube account are one account each. Pure."""
    if account in used or len(used) < campaign.max_accounts:
        return None
    return (f"{campaign.name} allows {campaign.max_accounts} account(s) on "
            f"{platform} and already posts from {', '.join(sorted(used))}; "
            f"{account} would be one more")


async def _account_check(campaign: campaigns.Campaign, platform: str) -> str | None:
    rows = await fetch_all(
        """SELECT DISTINCT post_account FROM clip_outputs
           WHERE campaign = $1 AND platform = $2 AND post_account IS NOT NULL""",
        campaign.slug, platform)
    used = {str(r["post_account"]) for r in rows}
    return account_refusal(campaign, platform, POST_ACCOUNT[platform], used)


async def _sibling_refusal(campaign: campaigns.Campaign, row: dict[str, Any]
                           ) -> str | None:
    """Why this clip may not go up on its platform because the same video
    already went (or is going) to another one, when the campaign's rules
    forbid the same video on more than one account; None otherwise."""
    if campaign.cross_post_allowed:
        return None
    rows = await fetch_all(
        """SELECT id, platform, status, post_ref FROM clip_outputs
           WHERE campaign = $1 AND filename = $2 AND id <> $3""",
        campaign.slug, str(row["filename"]), int(row["id"]))
    for other in map(dict, rows):
        if str(other["status"]) in COMMITTED or other.get("post_ref"):
            return (f"clip {row['id']}'s video already went to "
                    f"{other['platform']} (clip {other['id']}, "
                    f"{other['status']}); {campaign.name}'s rules forbid the "
                    f"same video on more than one account")
    return None


def _ids(text: str) -> list[int]:
    out = []
    for part in re.split(r"[,\s]+", str(text or "")):
        if part:
            if not part.isdigit():
                raise ValueError(f"{part!r} is not a clip id")
            out.append(int(part))
    if not out:
        raise ValueError("no clip ids")
    return out


async def _clips(ids: list[int]) -> dict[int, dict[str, Any]]:
    rows = await fetch_all(
        """SELECT id, campaign, filename, platform, caption, status, post_url,
                  post_ref, start_s, end_s, brand_assets
           FROM clip_outputs WHERE id = ANY($1::bigint[])""", ids)
    return {int(r["id"]): dict(r) for r in rows}


def brand_refusal(campaign: campaigns.Campaign, row: dict[str, Any]) -> str | None:
    """Why this clip may not post for want of the brand assets the
    campaign's rules require (`brand_assets`): it was cut without one of
    them -- before the rule, or by hand. Only a clip cut with every one
    posts. Pure."""
    wanted = [a for a in campaigns.BRAND_ASSETS if a in campaign.brand_assets]
    if not wanted:
        return None
    if str(row.get("status") or "") == "draft_approved":
        # The brand reviewed this exact file on Whop and approved it
        # (2026-10-06: four Irubyana drafts cut before her logo/banner rule
        # existed). Her approval of the file outranks the rule that stands
        # in for her judgement; nothing else about the gate changes.
        return None
    have = row.get("brand_assets")
    if isinstance(have, str | bytes):
        try:
            have = json.loads(have)
        except ValueError:
            have = []
    missing = [a for a in wanted if a not in (have or [])]
    if missing:
        return (f"clip {row['id']} was cut without {campaign.name}'s "
                f"{' and '.join(missing)} (its rules require them on every "
                f"clip); cut it again with {', '.join(f'{a}.png' for a in missing)} "
                f"in {sources.brand_dir(campaign.slug)}")
    return None


# ---------------------------------------------------------- clips.hand_off

async def _handoff_check(campaign: str, clip_ids: str
                         ) -> tuple[list[dict[str, Any]], str | None]:
    try:
        ids = _ids(clip_ids)
    except ValueError as exc:
        return [], str(exc)
    found = await _clips(ids)
    out = []
    for cid in ids:
        row = found.get(cid)
        if row is None:
            return [], f"no clip {cid}"
        if row["campaign"] != campaign:
            return [], f"clip {cid} belongs to {row['campaign']}, not {campaign}"
        if row["status"] not in WAITING:
            return [], f"clip {cid} is {row['status']}, not waiting to be posted"
        if row.get("post_ref"):
            # Already uploaded (private) by its own card: a hand post would
            # put the same clip up twice.
            return [], (f"clip {cid} is already uploaded as {row['post_ref']}; "
                        f"approve its post card again to publish it")
        if str(row["platform"]) not in POST_ACCOUNT:
            return [], f"the clips lane has no {row['platform']} account"
        if row.get("filename") is None:
            return [], f"clip {cid} has no file"
        folder = sources.out_dir(campaign)
        if await asyncio.to_thread(sources.confined, folder / row["filename"],
                                   folder) is None:
            return [], f"clip {cid}'s file is no longer in {folder}"
        out.append(row)
    found_campaign = await campaigns.load(campaign)
    if found_campaign is None:
        return [], f"no campaign {campaign!r}"
    if not found_campaign.cross_post_allowed:
        files = [str(r["filename"]) for r in out]
        if len(files) != len(set(files)):
            return [], (f"{found_campaign.name}'s rules forbid the same video on "
                        f"more than one account; hand off one platform per clip")
    for row in out:
        if (why := approval_refusal(found_campaign, row)) is not None:
            return [], why
        if (why := await _sibling_refusal(found_campaign, row)) is not None:
            return [], why
    for platform in sorted({str(r["platform"]) for r in out}):
        if (why := await _account_check(found_campaign, platform)) is not None:
            return [], why
    return out, None


def _where(rows: list[dict[str, Any]]) -> str:
    return " and ".join(ACCOUNTS[p] for p in sorted({str(r["platform"])
                                                     for r in rows}))


async def hand_off_dry(campaign: str, clip_ids: str) -> ActionResult:
    rows, why = await _handoff_check(campaign, clip_ids)
    if why:
        return ActionResult(False, why)
    return ActionResult(True, f"would mark {len(rows)} clip(s) handed to Miro to "
                              f"post on {_where(rows)}")


async def hand_off(campaign: str, clip_ids: str) -> ActionResult:
    rows, why = await _handoff_check(campaign, clip_ids)
    if why:
        return ActionResult(False, why)
    for row in rows:
        await execute_sql(
            "UPDATE clip_outputs SET status = 'handed_off', post_account = $2 "
            "WHERE id = $1 AND status IN ('made', 'draft_approved')",
            row["id"], POST_ACCOUNT[str(row["platform"])])
    ids = [r["id"] for r in rows]
    return ActionResult(True, f"clip(s) {', '.join(map(str, ids))} are with Miro "
                              f"to post on {_where(rows)}; send each post URL "
                              f"back to claim it",
                        rollback_token=json.dumps({"ids": ids}))


async def hand_off_rollback(token: str) -> ActionResult:
    ids = [int(i) for i in json.loads(token)["ids"]]
    for cid in ids:
        await execute_sql(
            "UPDATE clip_outputs SET status = 'made', post_account = NULL "
            "WHERE id = $1 AND status = 'handed_off'", cid)
    return ActionResult(True, f"clip(s) {', '.join(map(str, ids))} back to ready")


def _words(campaign: campaigns.Campaign, row: dict[str, Any]) -> list[str]:
    """What Miro posts, word for word: Instagram's or TikTok's caption; for
    YouTube the title and the description."""
    caption = str(row["caption"])
    platform = str(row.get("platform") or "instagram")
    if platform != "youtube":
        beside = ".caption.txt" if platform == "instagram" else f".{platform}.txt"
        return [f"Caption, exactly (also in the {beside} beside it):",
                "```", caption, "```"]
    try:
        title = captions.youtube_title(campaign, caption.split("\n")[0])
    except captions.CaptionError as exc:
        title = f"(refused: {exc})"
    return ["Title, exactly:", "```", title, "```",
            "Description, exactly (both also in the .youtube.txt beside it):",
            "```", caption, "```"]


def _only_here(campaign: campaigns.Campaign, row: dict[str, Any]) -> list[str]:
    """The card's line on the one-platform rule, when it applies."""
    if campaign.cross_post_allowed:
        return []
    from .cut import one_platform_note
    note = one_platform_note(campaign, [p for p in campaign.platforms
                                        if p in POST_ACCOUNT])
    if not note:
        return []
    where = ACCOUNTS.get(str(row.get("platform")), str(row.get("platform")))
    return [f"Only on {where}: {note}."]


def ready_card(campaign: campaigns.Campaign, row: dict[str, Any]) -> Signal:
    """The Tier 2 card for one finished clip: its file and its caption, word
    for word -- what Miro posts is what he read. One clip per card, so the
    caption is never cut short by the card's length limit, and each card
    carries its own file."""
    platform = str(row.get("platform") or "instagram")
    path = sources.out_dir(campaign.slug) / row["filename"]
    link = ("youtube.com/shorts/ URL" if platform == "youtube" else "post URL")
    body = "\n".join([
        (f"Clip {row['id']} for {campaign.name} is cut and checked, ready to "
         f"post on {ACCOUNTS.get(platform, platform)}. ATLAS cannot post it "
         f"itself: {(autopost_gap(platform) or 'not configured')[:200]}."),
        f"File: {path}",
        *_words(campaign, row),
        *_only_here(campaign, row),
        (f"Keep it up 30 days, public, likes visible, no paid or story "
         f"boosting. Approve once you have it, then send ATLAS the {link}."),
    ])
    return Signal(
        title=f"Clip {row['id']} ready to post: {campaign.name}",
        body=body, severity=Severity.NORMAL, suggests="clips.hand_off",
        params={"campaign": campaign.slug, "clip_ids": str(row["id"])},
        attachment=str(path))


# ---------------------------------------------------------- clips.publish_clip

async def _publish_check(clip_id: int) -> tuple[dict[str, Any] | None, str | None]:
    """The clip row, re-checked end to end, or why it may not post."""
    row = (await _clips([int(clip_id)])).get(int(clip_id))
    if row is None:
        return None, f"no clip {clip_id}"
    if row["status"] not in WAITING:
        # handed_off is Miro's to post by hand: posting it here too would
        # put the same clip up twice.
        return None, not_waiting(clip_id, str(row["status"]))
    if row["platform"] != "instagram":
        return None, f"clip {clip_id} is for {row['platform']}, not Instagram"
    campaign = await campaigns.load(str(row["campaign"]))
    if campaign is None:
        return None, f"no campaign {row['campaign']!r}"
    if (why := approval_refusal(campaign, row)) is not None:
        return None, why
    for check in (campaigns.refusal, campaigns.permission_refusal):
        if (why := check(campaign)) is not None:
            return None, f"refused: {why}"
    if campaign.status != "active" or campaign.rules_pending:
        return None, f"{campaign.name} is {campaign.status}; nothing of it posts"
    try:
        captions.validate(campaign, str(row["caption"]), "instagram")
    except captions.CaptionError as exc:
        return None, f"caption refused: {exc}"
    _path, _facts, why = await _file_check(campaign, row)
    if why is not None:
        return None, why
    if (gap := autopost_gap("instagram")) is not None:
        return None, gap
    if (why := await _account_check(campaign, "instagram")) is not None:
        return None, why
    if (why := await _sibling_refusal(campaign, row)) is not None:
        return None, why
    if (why := await auto_hold("instagram")) is not None:
        return None, why
    return row, None


async def publish_clip_dry(clip_id: int) -> ActionResult:
    row, why = await _publish_check(clip_id)
    if row is None:
        return ActionResult(False, why or "refused")
    from .. import instagram
    host = os.getenv("ATLAS_HOST", "").strip()
    dry = await instagram.publish_dry(str(row["caption"]),
                                      f"https://{host}/media/{TOKEN_PREFIX}dry-run",
                                      kind="reel", account=IG_ACCOUNT,
                                      content_kind=CONTENT_KIND)
    if not dry.ok:
        return dry
    return ActionResult(True, f"would post clip {clip_id} to {ACCOUNT}: {dry.detail}")


async def publish_clip(clip_id: int) -> ActionResult:
    """Post one clip through `instagram.publish` to the clips account, then
    read its permalink back for the Whop claim."""
    row, why = await _publish_check(clip_id)
    if row is None:
        return ActionResult(False, why or "refused")
    from ...kernel import idempotency
    from .. import instagram
    from ..content import _media_url
    action, key = AUTO_ACTIONS["instagram"], leg_key(clip_id, "instagram")
    state = await idempotency.claim(action, key)
    if state != idempotency.CLAIMED:
        return ActionResult(False, f"clip {clip_id} on Instagram: "
                                   f"{idempotency.refusal(action, state)}")
    token = TOKEN_PREFIX + secrets.token_urlsafe(32)
    await execute_sql(
        """UPDATE clip_outputs SET media_token = $2, media_token_expires_at = $3
           WHERE id = $1""", int(clip_id), token, datetime.now(UTC) + TOKEN_TTL)
    url = _media_url(token)
    if url is None:
        await idempotency.release(action, key)
        return ActionResult(False, "ATLAS_HOST is unset, so Instagram could not "
                                   "fetch the file")
    try:
        result = await instagram.publish(str(row["caption"]), url, kind="reel",
                                         account=IG_ACCOUNT, content_kind=CONTENT_KIND)
    except Exception as exc:  # noqa: BLE001
        result = ActionResult(False, instagram._why(exc))
    if not result.ok or not result.rollback_token:
        await idempotency.finish(action, key, result.detail or "refused")
        return ActionResult(False, f"clip {clip_id} not posted: {result.detail}")
    await idempotency.settle(action, key, result.detail)
    media_id = result.rollback_token.partition(":")[2]
    permalink = None
    try:
        got = await instagram._get(media_id, account=IG_ACCOUNT, fields="permalink")
        link = str(got.get("permalink") or "")
        permalink = link if POST_URL["instagram"].match(link) else None
    except Exception:  # noqa: BLE001
        permalink = None
    await execute_sql(
        """UPDATE clip_outputs SET status = 'posted', post_ref = $2, post_url = $3,
                  posted_at = now(), media_token_expires_at = now(),
                  post_account = $4, post_url_source = $5
           WHERE id = $1""", int(clip_id), media_id, permalink,
        POST_ACCOUNT["instagram"], "instagram" if permalink else None)
    said = (f"at {permalink}" if permalink else
            "(its link could not be read back: send it with clips.record_link)")
    return ActionResult(
        True, f"clip {clip_id} posted to {ACCOUNT} {said}",
        evidence={"media_id": media_id, "post_url": permalink},
        rollback_token=json.dumps({"id": int(clip_id), "ig": result.rollback_token,
                                   "status": row["status"]}))


async def unpublish_clip(token: str) -> ActionResult:
    """Takes the post down (what `instagram.publish`'s own rollback does) and
    marks the clip withdrawn: a campaign post must stay up 30 days, so this
    is for a post that should never have gone up, and a clip put back to
    waiting would only post itself again (its leg's claim stays settled)."""
    from .. import instagram
    data = json.loads(token)
    down = await instagram.unpublish(str(data["ig"]))
    if not down.ok:
        return down
    await execute_sql(
        """UPDATE clip_outputs SET status = 'withdrawn', post_url = NULL,
                  post_ref = NULL, posted_at = NULL, post_url_source = NULL
           WHERE id = $1 AND status = 'posted'""", int(data["id"]))
    return ActionResult(True, f"{down.detail}; clip {data['id']} withdrawn, never "
                              f"posted again")


def post_card(campaign: campaigns.Campaign, row: dict[str, Any]) -> Signal:
    """The card that posts one clip -- its file and its exact caption --
    for the platform's own leg. The intake raises it, with the reason the
    clip was held, for the three automatic legs; `signals` for the Data
    API Short."""
    path = sources.out_dir(campaign.slug) / row["filename"]
    if str(row.get("platform") or "instagram") == "youtube":
        if youtube_route(row) == "postiz":
            return youtube_postiz_card(campaign, row)
        return youtube_card(campaign, row)
    if str(row.get("platform")) == "tiktok":
        return tiktok_card(campaign, row)
    body = "\n".join([
        (f"Post clip {row['id']} for {campaign.name} to {ACCOUNT} (Instagram "
         f"Reels). Approving posts it now, under your name."),
        f"File: {path}",
        "Caption, exactly:",
        "```", str(row["caption"]), "```",
        *_only_here(campaign, row),
        ("It must stay up 30 days with likes visible; no paid or story "
         "boosting. Its Whop card follows once it is live."),
    ])
    return Signal(
        title=f"Post clip {row['id']} to {ACCOUNT}: {campaign.name}",
        body=body, severity=Severity.NORMAL, suggests="clips.publish_clip",
        params={"clip_id": int(row["id"])}, attachment=str(path))


# ------------------------------------------------ clips.publish_clip_youtube

@dataclass(frozen=True)
class Short:
    row: dict[str, Any]
    campaign: campaigns.Campaign
    path: Path
    title: str
    tags: list[str]


def _tags(caption: str) -> list[str]:
    """The campaign hashtags on the caption's tag line, without the '#'."""
    lines = caption.split("\n")
    return [t.lstrip("#") for t in lines[3].split()] if len(lines) == 5 else []


async def _youtube_check(clip_id: int) -> tuple[Short | None, str | None]:
    """The clip, re-checked end to end for the clips YouTube channel, or why
    it may not post there."""
    row = (await _clips([int(clip_id)])).get(int(clip_id))
    if row is None:
        return None, f"no clip {clip_id}"
    if row["platform"] != "youtube":
        return None, f"clip {clip_id} is for {row['platform']}, not YouTube"
    if row["status"] not in WAITING:
        return None, not_waiting(clip_id, str(row["status"]))
    campaign = await campaigns.load(str(row["campaign"]))
    if campaign is None:
        return None, f"no campaign {row['campaign']!r}"
    if (why := approval_refusal(campaign, row)) is not None:
        return None, why
    for check in (campaigns.refusal, campaigns.permission_refusal):
        if (why := check(campaign)) is not None:
            return None, f"refused: {why}"
    if campaign.status != "active" or campaign.rules_pending:
        return None, f"{campaign.name} is {campaign.status}; nothing of it posts"
    caption = str(row["caption"])
    try:
        captions.validate(campaign, caption, "youtube")
        title = captions.youtube_title(campaign, caption.split("\n")[0])
    except captions.CaptionError as exc:
        return None, f"caption refused: {exc}"
    folder = sources.out_dir(campaign.slug)
    path = await asyncio.to_thread(sources.confined, folder / str(row["filename"]),
                                   folder)
    if path is None:
        return None, f"clip {clip_id}'s file is no longer in {folder}"
    if (gap := youtube_api_gap()) is not None:
        return None, gap
    if (why := await _account_check(campaign, "youtube")) is not None:
        return None, why
    if not row.get("post_ref") and (why := await _sibling_refusal(campaign, row)):
        return None, why
    return Short(row, campaign, path, title, _tags(caption)), None


async def publish_clip_youtube_dry(clip_id: int) -> ActionResult:
    short, why = await _youtube_check(clip_id)
    if short is None:
        return ActionResult(False, why or "refused")
    from ..youtube import api as yt
    if short.row.get("post_ref"):
        return ActionResult(True, f"would make clip {clip_id}'s uploaded Short "
                                  f"{short.row['post_ref']} public (no second upload)")
    if (why := await asyncio.to_thread(yt.clips_path_refusal,
                                       str(short.path))) is not None:
        return ActionResult(False, why)
    return ActionResult(True, f"would upload clip {clip_id} to the clips YouTube "
                              f"channel as a Short titled {short.title!r} and make "
                              f"it public")


async def publish_clip_youtube(clip_id: int) -> ActionResult:
    """Upload one clip to the clips channel (it lands private), make it
    public, and record it posted only once YouTube reads it back public."""
    short, why = await _youtube_check(clip_id)
    if short is None:
        return ActionResult(False, why or "refused")
    from ..youtube import api as yt
    cid = int(clip_id)
    video_id = str(short.row.get("post_ref") or "")
    if not video_id:
        result, reason = await yt.upload(YT_CHANNEL, str(short.path), short.title,
                                         str(short.row["caption"]), short.tags,
                                         privacy="private")
        if reason is not None or not result:
            return ActionResult(False, f"clip {cid} not uploaded: {reason}")
        video_id = str(result["video_id"])
        # Kept at once: a later approval publishes this video, never a copy.
        await execute_sql(
            """UPDATE clip_outputs SET post_ref = $2, post_account = $3
               WHERE id = $1""", cid, video_id, POST_ACCOUNT["youtube"])
    _done, reason = await yt.set_privacy(YT_CHANNEL, video_id, "public")
    if reason is not None:
        return ActionResult(False, f"clip {cid} is uploaded as {video_id} but still "
                                   f"private: {reason}. Approving its card again "
                                   f"publishes it, with no second upload")
    privacy, reason = await yt.current_privacy(YT_CHANNEL, video_id)
    if privacy != "public":
        return ActionResult(False, (
            f"clip {cid} ({video_id}) reads back as {privacy or 'unknown'}"
            f"{f': {reason}' if reason else ''}, not public -- if the Google "
            f"project is unverified, YouTube keeps its uploads private until it "
            f"passes the API audit. Not recorded as posted"))
    url = f"https://www.youtube.com/shorts/{video_id}"
    await execute_sql(
        """UPDATE clip_outputs SET status = 'posted', post_url = $2,
                  posted_at = now(), post_account = $3, post_url_source = 'youtube'
           WHERE id = $1 AND status IN ('made', 'draft_approved')""", cid, url, POST_ACCOUNT["youtube"])
    return ActionResult(
        True, f"clip {cid} is public on the clips YouTube channel at {url}",
        evidence={"video_id": video_id, "post_url": url},
        rollback_token=json.dumps({"id": cid, "video_id": video_id}))


async def unpublish_clip_youtube(token: str) -> ActionResult:
    """Sets the Short private -- deleting it is Tier 3, never done here --
    and marks the clip withdrawn, so it is never uploaded again."""
    from ..youtube import api as yt
    data = json.loads(token)
    cid, video_id = int(data["id"]), str(data["video_id"])
    _done, reason = await yt.set_privacy(YT_CHANNEL, video_id, "private")
    if reason is not None:
        return ActionResult(False, f"{video_id} not set private: {reason}")
    await execute_sql(
        """UPDATE clip_outputs SET status = 'withdrawn'
           WHERE id = $1 AND status IN ('posted', 'submitted')""", cid)
    return ActionResult(True, f"{video_id} set private (not deleted); clip {cid} "
                              f"withdrawn and never uploaded again")


def youtube_card(campaign: campaigns.Campaign, row: dict[str, Any]) -> Signal:
    """The Tier 2 card that publishes one clip as a Short: its file, its
    exact title and description, and that approving makes it public."""
    path = sources.out_dir(campaign.slug) / row["filename"]
    again = (f" It is already uploaded as {row['post_ref']} (private); approving "
             f"publishes that video, with no second upload."
             if row.get("post_ref") else "")
    body = "\n".join([
        (f"Post clip {row['id']} for {campaign.name} to the clips YouTube channel "
         f"as a Short. Approving uploads it and makes it PUBLIC now, under your "
         f"name.{again}"),
        f"File: {path}",
        *_words(campaign, row),
        *_only_here(campaign, row),
        ("It must stay up 30 days, public; no paid boosting. Its Whop card "
         "follows once it is live."),
    ])
    return Signal(
        title=f"Post clip {row['id']} as a YouTube Short: {campaign.name}",
        body=body, severity=Severity.NORMAL, suggests="clips.publish_clip_youtube",
        params={"clip_id": int(row["id"])}, attachment=str(path))


# ------------------------------------------------ clips.publish_clip_tiktok

async def _tiktok_check(clip_id: int
                        ) -> tuple[tuple[dict[str, Any], campaigns.Campaign, Path] | None,
                                   str | None]:
    """The clip, re-checked end to end for @atlasclips12, or why it may not
    post there."""
    row = (await _clips([int(clip_id)])).get(int(clip_id))
    if row is None:
        return None, f"no clip {clip_id}"
    if row["platform"] != "tiktok":
        return None, f"clip {clip_id} is for {row['platform']}, not TikTok"
    if row["status"] not in WAITING or row.get("post_ref"):
        return None, not_waiting(clip_id, str(row["status"]))
    campaign = await campaigns.load(str(row["campaign"]))
    if campaign is None:
        return None, f"no campaign {row['campaign']!r}"
    if (why := approval_refusal(campaign, row)) is not None:
        return None, why
    for check in (campaigns.refusal, campaigns.permission_refusal):
        if (why := check(campaign)) is not None:
            return None, f"refused: {why}"
    if campaign.status != "active" or campaign.rules_pending:
        return None, f"{campaign.name} is {campaign.status}; nothing of it posts"
    try:
        captions.validate(campaign, str(row["caption"]), "tiktok")
    except captions.CaptionError as exc:
        return None, f"caption refused: {exc}"
    path, _facts, why = await _file_check(campaign, row)
    if path is None:
        return None, why
    if (gap := autopost_gap("tiktok")) is not None:
        return None, gap
    if (why := await _account_check(campaign, "tiktok")) is not None:
        return None, why
    if (why := await _sibling_refusal(campaign, row)) is not None:
        return None, why
    if (why := await auto_hold("tiktok")) is not None:
        return None, why
    return (row, campaign, path), None


async def publish_clip_tiktok_dry(clip_id: int) -> ActionResult:
    found, why = await _tiktok_check(clip_id)
    if found is None:
        return ActionResult(False, why or "refused")
    from ..content import postiz
    ok, said = await postiz.readiness(TT_ACCOUNT)
    if not ok:
        return ActionResult(False, f"{TT_HANDLE} (via Postiz): {said}")
    return ActionResult(True, f"would post clip {clip_id} to {TT_HANDLE} on TikTok "
                              f"through Postiz: {said}")


async def publish_clip_tiktok(clip_id: int) -> ActionResult:
    """Post one clip to @atlasclips12 through Postiz. Recorded posted once
    Postiz accepts it; its URL only when Postiz returns one."""
    found, why = await _tiktok_check(clip_id)
    if found is None:
        return ActionResult(False, why or "refused")
    row, _campaign, path = found
    from ...kernel import idempotency
    from ..content import postiz
    cid = int(clip_id)
    action, key = AUTO_ACTIONS["tiktok"], leg_key(cid, "tiktok")
    state = await idempotency.claim(action, key)
    if state != idempotency.CLAIMED:
        return ActionResult(False, f"clip {cid} on TikTok: "
                                   f"{idempotency.refusal(action, state)}")
    status, detail, post_id, url = await postiz.post_tiktok_clip(
        str(path), str(row["caption"]))
    if status not in (postiz.POSTED, postiz.ID_PENDING) or not post_id:
        await idempotency.finish(action, key, detail or "refused")
        return ActionResult(False, f"clip {cid} not posted to {TT_HANDLE}: {detail}")
    await idempotency.settle(action, key, detail)
    url = url if url and POST_URL["tiktok"].match(url) else None
    await execute_sql(
        """UPDATE clip_outputs SET status = 'posted', post_ref = $2, post_url = $3,
                  posted_at = now(), post_account = $4, post_url_source = $5
           WHERE id = $1 AND status IN ('made', 'draft_approved')""",
        cid, post_id, url, POST_ACCOUNT["tiktok"], "tiktok" if url else None)
    said = (f"at {url}" if url else
            "(its TikTok link is not known yet: send it with clips.record_link "
            "for its Whop card)")
    return ActionResult(
        True, f"clip {cid} posted to {TT_HANDLE} {said}; {detail}",
        evidence={"postiz_post": post_id, "post_url": url},
        rollback_token=json.dumps({"id": cid, "postiz": post_id}))


async def unpublish_clip_tiktok(token: str) -> ActionResult:
    """Deletes the Postiz post while it is still queued, and marks the clip
    withdrawn (never posted again; its leg's claim stays settled). A
    published TikTok is never deleted here (Tier 3): the answer is a MANUAL
    note and the clip stays posted."""
    from ..content import postiz
    data = json.loads(token)
    cid, post_id = int(data["id"]), str(data["postiz"])
    ok, said = await postiz.undo(post_id)
    if not ok:
        return ActionResult(False, said)
    await execute_sql(
        """UPDATE clip_outputs SET status = 'withdrawn', post_ref = NULL,
                  post_url = NULL, posted_at = NULL, post_url_source = NULL
           WHERE id = $1 AND status = 'posted'""", cid)
    return ActionResult(True, f"{said}; clip {cid} withdrawn, never posted again")


def tiktok_card(campaign: campaigns.Campaign, row: dict[str, Any]) -> Signal:
    """The card that posts one clip to @atlasclips12: its file and its
    exact caption."""
    path = sources.out_dir(campaign.slug) / row["filename"]
    body = "\n".join([
        (f"Post clip {row['id']} for {campaign.name} to {TT_HANDLE} on TikTok "
         f"(through Postiz). Approving posts it now, PUBLIC, under your name, "
         f"marked as branded content."),
        f"File: {path}",
        *_words(campaign, row),
        *_only_here(campaign, row),
        ("It must stay up 30 days, public, likes visible; no paid or Promote "
         "boosting. Its Whop card follows once its link is known."),
    ])
    return Signal(
        title=f"Post clip {row['id']} to {TT_HANDLE}: {campaign.name}",
        body=body, severity=Severity.NORMAL, suggests="clips.publish_clip_tiktok",
        params={"clip_id": int(row["id"])}, attachment=str(path))


# ------------------------------------------ clips.publish_clip_youtube_postiz

async def _youtube_postiz_check(clip_id: int) -> tuple[Short | None, str | None]:
    """The clip, re-checked end to end for the clips YouTube channel through
    Postiz, or why it may not post there. The TikTok leg's checks, plus the
    campaign's YouTube permission and the Shorts format."""
    row = (await _clips([int(clip_id)])).get(int(clip_id))
    if row is None:
        return None, f"no clip {clip_id}"
    if row["platform"] != "youtube":
        return None, f"clip {clip_id} is for {row['platform']}, not YouTube"
    if row["status"] not in WAITING:
        return None, not_waiting(clip_id, str(row["status"]))
    if row.get("post_ref"):
        return None, (f"clip {clip_id} is already uploaded as {row['post_ref']} "
                      f"through the Data API; it is published there "
                      f"(clips.publish_clip_youtube), never a second copy")
    campaign = await campaigns.load(str(row["campaign"]))
    if campaign is None:
        return None, f"no campaign {row['campaign']!r}"
    if (why := approval_refusal(campaign, row)) is not None:
        return None, why
    for check in (campaigns.refusal, campaigns.permission_refusal):
        if (why := check(campaign)) is not None:
            return None, f"refused: {why}"
    if campaign.status != "active" or campaign.rules_pending:
        return None, f"{campaign.name} is {campaign.status}; nothing of it posts"
    if "youtube" not in campaign.platforms:
        return None, (f"{campaign.name}'s rules do not allow YouTube (it pays on "
                      f"{', '.join(campaign.platforms) or 'nothing'}); nothing "
                      f"of it posts there")
    caption = str(row["caption"])
    try:
        captions.validate(campaign, caption, "youtube")
        title = captions.youtube_title(campaign, caption.split("\n")[0])
    except captions.CaptionError as exc:
        return None, f"caption refused: {exc}"
    if (why := window_refusal(row)) is not None:
        return None, f"clip {clip_id} is not posted as a Short: {why}"
    path, facts, why = await _file_check(campaign, row)
    if path is None or facts is None:
        return None, why
    frame = (int(facts["width"]), int(facts["height"]))
    if (why := shorts_refusal(float(facts["seconds"]), frame,
                              SHORTS_PAD_S)) is not None:
        return None, f"clip {clip_id} is not posted as a Short: {why}"
    if (gap := youtube_postiz_gap()) is not None:
        return None, gap
    if (why := await _account_check(campaign, "youtube")) is not None:
        return None, why
    if (why := await _sibling_refusal(campaign, row)) is not None:
        return None, why
    if (why := await auto_hold("youtube")) is not None:
        return None, why
    return Short(row, campaign, path, title, _tags(caption)), None


async def publish_clip_youtube_postiz_dry(clip_id: int) -> ActionResult:
    short, why = await _youtube_postiz_check(clip_id)
    if short is None:
        return ActionResult(False, why or "refused")
    from ..content import postiz
    ok, said = await postiz.readiness(postiz.YT_ACCOUNT)
    if not ok:
        return ActionResult(False, f"{YT_CHANNEL_NAME} (via Postiz): {said}")
    return ActionResult(True, f"would post clip {clip_id} to {YT_CHANNEL_NAME} as "
                              f"a public Short titled {short.title!r} through "
                              f"Postiz: {said}")


async def publish_clip_youtube_postiz(clip_id: int) -> ActionResult:
    """Post one clip as a Short to the clips YouTube channel through Postiz.
    Recorded posted once Postiz accepts it; its URL only when Postiz
    returns one."""
    short, why = await _youtube_postiz_check(clip_id)
    if short is None:
        return ActionResult(False, why or "refused")
    from ...kernel import idempotency
    from ..content import postiz
    cid = int(clip_id)
    action, key = AUTO_ACTIONS["youtube"], leg_key(cid, "youtube")
    state = await idempotency.claim(action, key)
    if state != idempotency.CLAIMED:
        return ActionResult(False, f"clip {cid} on YouTube: "
                                   f"{idempotency.refusal(action, state)}")
    status, detail, post_id, url = await postiz.post_youtube_clip(
        str(short.path), short.title, str(short.row["caption"]), short.tags)
    if status not in (postiz.POSTED, postiz.ID_PENDING) or not post_id:
        await idempotency.finish(action, key, detail or "refused")
        return ActionResult(False, f"clip {cid} not posted to {YT_CHANNEL_NAME}: "
                                   f"{detail}")
    await idempotency.settle(action, key, detail)
    url = url if url and POST_URL["youtube"].match(url) else None
    await execute_sql(
        """UPDATE clip_outputs SET status = 'posted', post_ref = $2, post_url = $3,
                  posted_at = now(), post_account = $4, post_url_source = $5
           WHERE id = $1 AND status IN ('made', 'draft_approved')""",
        cid, post_id, url, POST_ACCOUNT["youtube"], "youtube" if url else None)
    said = (f"at {url}" if url else
            "(its Shorts link is not known yet: send it with clips.record_link "
            "for its Whop card)")
    return ActionResult(
        True, f"clip {cid} posted to {YT_CHANNEL_NAME} {said}; {detail}",
        evidence={"postiz_post": post_id, "post_url": url},
        rollback_token=json.dumps({"id": cid, "postiz": post_id}))


async def unpublish_clip_youtube_postiz(token: str) -> ActionResult:
    """Deletes the Postiz post while it is still queued, and marks the clip
    withdrawn (never posted again; its leg's claim stays settled). A
    published Short is never deleted here (Tier 3): the answer is a MANUAL
    note and the clip stays posted."""
    from ..content import postiz
    data = json.loads(token)
    cid, post_id = int(data["id"]), str(data["postiz"])
    ok, said = await postiz.undo(post_id, where="YouTube")
    if not ok:
        return ActionResult(False, said)
    await execute_sql(
        """UPDATE clip_outputs SET status = 'withdrawn', post_ref = NULL,
                  post_url = NULL, posted_at = NULL, post_url_source = NULL
           WHERE id = $1 AND status = 'posted'""", cid)
    return ActionResult(True, f"{said}; clip {cid} withdrawn, never posted again")


def youtube_postiz_card(campaign: campaigns.Campaign, row: dict[str, Any]) -> Signal:
    """The card that posts one clip as a Short through Postiz: its file,
    its exact title and description, and that approving makes it public."""
    path = sources.out_dir(campaign.slug) / row["filename"]
    body = "\n".join([
        (f"Post clip {row['id']} for {campaign.name} to {YT_CHANNEL_NAME} as a "
         f"YouTube Short (through Postiz). Approving posts it now, PUBLIC, under "
         f"your name."),
        f"File: {path}",
        *_words(campaign, row),
        *_only_here(campaign, row),
        ("It must stay up 30 days, public; no paid boosting. Its Whop card "
         "follows once its link is known."),
    ])
    return Signal(
        title=f"Post clip {row['id']} as a YouTube Short: {campaign.name}",
        body=body, severity=Severity.NORMAL,
        suggests="clips.publish_clip_youtube_postiz",
        params={"clip_id": int(row["id"])}, attachment=str(path))


def not_a_short(campaign: campaigns.Campaign, row: dict[str, Any], why: str) -> Signal:
    """A YouTube clip that is not a Short: told, never carded."""
    return Signal(
        title=f"Clip {row['id']} is not posted to YouTube: {campaign.name}",
        body=(f"Clip {row['id']} for {campaign.name} is not carded for "
              f"{ACCOUNTS['youtube']}: {why}."),
        severity=Severity.INFO, params={"topic": "clips"})


# ---------------------------------------------------------- clips.record_link

async def _post_check(clip_id: int, post_url: str) -> tuple[dict | None, str | None]:
    row = (await _clips([int(clip_id)])).get(int(clip_id))
    if row is None:
        return None, f"no clip {clip_id}"
    unknown_url = row["status"] == "posted" and not row.get("post_url")
    if row["status"] not in (*WAITING, "handed_off") and not unknown_url:
        return None, f"clip {clip_id} is {row['status']}"
    pattern = POST_URL.get(str(row["platform"]))
    url = str(post_url or "").strip()
    if pattern is None or not pattern.match(url):
        return None, f"{url!r} is not a {row['platform']} post URL"
    return row, None


async def record_link_dry(clip_id: int, post_url: str) -> ActionResult:
    row, why = await _post_check(clip_id, post_url)
    if row is None:
        return ActionResult(False, why or "refused")
    return ActionResult(True, f"would record clip {clip_id} as posted at {post_url}")


async def record_link(clip_id: int, post_url: str) -> ActionResult:
    row, why = await _post_check(clip_id, post_url)
    if row is None:
        return ActionResult(False, why or "refused")
    await execute_sql(
        """UPDATE clip_outputs SET status = 'posted', post_url = $2,
                  posted_at = coalesce(posted_at, now()),
                  post_url_source = 'record_link' WHERE id = $1""",
        int(clip_id), post_url.strip())
    return ActionResult(True, f"clip {clip_id} posted at {post_url.strip()}; its "
                              f"Whop submission card follows",
                        rollback_token=json.dumps({"id": int(clip_id),
                                                   "status": row["status"]}))


async def record_link_rollback(token: str) -> ActionResult:
    data = json.loads(token)
    await execute_sql(
        """UPDATE clip_outputs SET status = $2, post_url = NULL, posted_at = NULL,
                  post_url_source = NULL
           WHERE id = $1 AND status = 'posted'""", int(data["id"]), str(data["status"]))
    return ActionResult(True, f"clip {data['id']} back to {data['status']}")


# ---------------------------------------------------------- clips.submit_to_whop

async def _submit_check(clip_id: int, post_url: str) -> tuple[dict | None, str | None]:
    row = (await _clips([int(clip_id)])).get(int(clip_id))
    if row is None:
        return None, f"no clip {clip_id}"
    if row["status"] != "posted":
        return None, f"clip {clip_id} is {row['status']}, not posted"
    if str(row["post_url"] or "") != str(post_url or "").strip():
        return None, f"clip {clip_id} was posted at {row['post_url']}, not {post_url}"
    return row, None


async def submit_to_whop_dry(clip_id: int, post_url: str) -> ActionResult:
    row, why = await _submit_check(clip_id, post_url)
    if row is None:
        return ActionResult(False, why or "refused")
    return ActionResult(True, f"would record {post_url} as submitted to "
                              f"{row['campaign']} on Whop, by Miro")


async def submit_to_whop(clip_id: int, post_url: str) -> ActionResult:
    """Records Miro's own submission. ATLAS does not submit to Whop."""
    row, why = await _submit_check(clip_id, post_url)
    if row is None:
        return ActionResult(False, why or "refused")
    await execute_sql(
        """UPDATE clip_outputs SET status = 'submitted', submitted_at = now(),
                  submitted_by = 'miro'
           WHERE id = $1 AND status = 'posted'""", int(clip_id))
    return ActionResult(True, f"clip {clip_id} ({post_url}) recorded as submitted "
                              f"to {row['campaign']} on Whop",
                        rollback_token=json.dumps({"id": int(clip_id)}))


async def submit_to_whop_rollback(token: str) -> ActionResult:
    cid = int(json.loads(token)["id"])
    await execute_sql(
        """UPDATE clip_outputs SET status = 'posted', submitted_at = NULL,
                  submitted_by = NULL
           WHERE id = $1 AND status = 'submitted'
             AND submitted_by IS DISTINCT FROM 'mac-agent'""", cid)
    return ActionResult(True, f"clip {cid} back to posted (ATLAS's record only; "
                              f"a submission on Whop is withdrawn there)")


def submit_card(campaign: campaigns.Campaign, row: dict[str, Any]) -> Signal:
    where = (campaign.whop_url or
             "whop_url is not on file for this campaign -- open it from "
             "Clipping Culture on Whop")
    return Signal(
        title=f"Submit clip {row['id']} to {campaign.name} on Whop",
        body=(f"Submit this post URL to {campaign.name}: {row['post_url']}\n"
              f"Where: {where}\n"
              f"It touches your payout account, so ATLAS does not submit it. "
              f"Approve once you have, and it is recorded as submitted."),
        severity=Severity.NORMAL, suggests="clips.submit_to_whop",
        params={"clip_id": int(row["id"]), "post_url": str(row["post_url"])})


def link_wanted(campaign: campaigns.Campaign, row: dict[str, Any]) -> Signal:
    """A posted clip whose link ATLAS could not read back: told to Miro (no
    action -- the link is his word only, through clips.record_link), since
    its Whop card needs it."""
    where = ACCOUNTS.get(str(row.get("platform")), str(row.get("platform")))
    return Signal(
        title=f"Clip {row['id']} is live without a link: {campaign.name}",
        body=(f"Clip {row['id']} for {campaign.name} is posted on {where}, but "
              f"its post URL could not be read back. Send ATLAS the link "
              f"(clip {row['id']}) and its Whop submission card follows."),
        severity=Severity.INFO, params={"topic": "clips"})


def carded(campaign: campaigns.Campaign, rows: list[dict[str, Any]]
           ) -> list[dict[str, Any]]:
    """The waiting rows that get a post card. Every one, unless the
    campaign forbids the same video on more than one account: then a file
    with a row already committed somewhere gets none, and a file with
    several waiting rows gets one, for the platform `cut.choose_platform`
    picks. Pure."""
    if campaign.cross_post_allowed:
        return [r for r in rows if r["status"] in WAITING]
    from .cut import choose_platform
    by_file: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        by_file.setdefault(str(row["filename"]), []).append(row)
    out = []
    for name, group in by_file.items():
        if any(str(r["status"]) in COMMITTED for r in group):
            continue
        waiting = [r for r in group if r["status"] in WAITING]
        uploaded = [r for r in waiting if r.get("post_ref")]
        if uploaded:
            # Already uploaded (a private Short): that one, nowhere else.
            out.append(uploaded[0])
            continue
        if len(waiting) < 2:
            out += waiting
            continue
        match = re.fullmatch(r"(\d+)-[0-9a-f]{12}-(\d+)\.mp4", name)
        seed = int(match.group(1)) + int(match.group(2)) - 1 if match else 0
        platform, _why = choose_platform(campaign, [str(r["platform"])
                                                    for r in waiting], seed)
        out += [r for r in waiting if r["platform"] == platform]
    return sorted(out, key=lambda r: int(r["id"]))


async def _grouped() -> dict[str, list[dict[str, Any]]]:
    """Every clip row that is waiting or went somewhere, by campaign, on the
    platforms the lane posts to. Reads only."""
    rows = await fetch_all(
        """SELECT id, campaign, filename, platform, caption, status, post_url,
                  post_ref, posted_at, post_url_source, start_s, end_s
           FROM clip_outputs
           WHERE status IN ('made', 'draft_pending', 'draft_submitted',
                            'draft_approved', 'handed_off', 'posted', 'submitted',
                            'withdrawn')
           ORDER BY id""")
    by_campaign: dict[str, list[dict[str, Any]]] = {}
    for row in map(dict, rows):
        if str(row.get("platform") or "instagram") in POST_ACCOUNT:
            by_campaign.setdefault(str(row["campaign"]), []).append(row)
    return by_campaign


async def ready_to_post(found: list[campaigns.Campaign] | None = None
                        ) -> list[tuple[campaigns.Campaign, dict[str, Any], str]]:
    """The waiting clips the intake (`kernel.clip_intake`) posts on its own,
    oldest first, each with its campaign and the Tier 1 action that posts
    it: the rows `carded` picks (the one-platform rule) whose account is
    reachable on an automatic leg. A YouTube window past the Shorts limit
    is left to `signals`, which tells it. Reads only; every check of the
    clip itself is the action's."""
    slugs = {c.slug: c for c in (found if found is not None
                                 else await campaigns.load_all())}
    out: list[tuple[campaigns.Campaign, dict[str, Any], str]] = []
    for slug, group in (await _grouped()).items():
        campaign = slugs.get(slug)
        if campaign is None:
            continue
        for row in carded(campaign, group):
            action = auto_action(row)
            if action is None or approval_refusal(campaign, row) is not None:
                continue            # a draft is Whop's until the brand approves
            if (str(row.get("platform")) == "youtube" and not row.get("post_ref")
                    and window_refusal(row) is not None):
                continue
            out.append((campaign, row, action))
    return sorted(out, key=lambda item: int(item[1]["id"]))


async def signals() -> list[Signal]:
    """The hand-off card per waiting clip whose account ATLAS cannot reach,
    and the Data API Short's Tier 2 card (`clips.publish_clip_youtube`),
    honouring the one-platform rule -- a clip on an automatic leg
    (`auto_action`) is the intake's, which posts it or raises its card
    with the reason; a note for a posted clip whose URL is not known; and,
    as the fallback for the agent on Miro's Mac that submits posts on Whop
    (Miro, 2026-09-29), one Whop card per posted clip with a known URL not
    yet submitted: at once when the agent will never see it
    (`whop.offered`), else once it is still unsubmitted
    `whop.FALLBACK_AFTER` after it posted."""
    from . import whop
    found = {c.slug: c for c in await campaigns.load_all()}
    out: list[Signal] = []
    for slug, group in (await _grouped()).items():
        campaign = found.get(slug)
        if campaign is None or campaigns.refusal(campaign) is not None:
            continue
        for row in carded(campaign, group):
            platform = str(row.get("platform") or "instagram")
            if approval_refusal(campaign, row) is not None:
                continue            # a draft is Whop's until the brand approves
            if platform == "youtube" and not row.get("post_ref") and (
                    why := window_refusal(row)) is not None:
                out.append(not_a_short(campaign, row, why))
                continue
            if auto_action(row) is not None:
                continue            # the intake's: posted on its own, or its card
            if platform == "youtube" and youtube_route(row) is not None:
                out.append(youtube_card(campaign, row))
            elif not row.get("post_ref"):
                # An uploaded Short whose channel is gone waits for it: a
                # hand post would be a second copy.
                out.append(ready_card(campaign, row))
        for row in group:
            if row["status"] != "posted":
                continue
            if not row.get("post_url"):
                out.append(link_wanted(campaign, row))
            elif whop.card_due(campaign, row):
                out.append(submit_card(campaign, row))
    return out
