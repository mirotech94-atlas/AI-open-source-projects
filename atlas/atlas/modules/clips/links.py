"""Post links from Postiz, for clips it posted before it knew them.

A clip posted through Postiz (`clips.publish_clip_tiktok`, and a Short
through `clips.publish_clip_youtube_postiz`) is recorded posted with its
Postiz post id in `post_ref`, and its URL only when Postiz's answer carried
one. TikTok returns none at publish time, so such a row has no `post_url`,
and `whop.pending` never offers it. This pass reads Postiz again and fills
the link in once Postiz reports the published URL (`releaseURL`).

Every `CLIPS_LINKS_MIN` minutes (default 60), from the scheduler
(`kernel.scheduler._maybe_clip_links`; `CLIPS_LINKS=off` stops it):

  * the rows: status `posted`, platform TikTok or YouTube, a `post_ref`,
    no `post_url`, posted within `LOOKBACK` (14 days);
  * ONE read of Postiz (`postiz.posts_between`, `GET /posts` over their
    posting window) -- never a post, an edit or a delete there or on the
    platform;
  * a row is filled in only when the Postiz post with its `post_ref` as id
    is `PUBLISHED`, not deleted, on the row's platform, and its `releaseURL`
    is that platform's post URL: for TikTok `https://(www.)tiktok.com/
    @atlasclips12/video/<n>` (`posting.POST_URL`, on the clips account);
    for YouTube a `youtube.com` / `youtu.be` URL for the id Postiz names,
    recorded as its `shorts/` link (`postiz.clip_youtube_url`). Anything
    else is left alone. Never a URL built from an id: it feeds a payout
    claim (invariant 6). A short link (`vm.tiktok.com`) is not taken either:
    it proves neither the account nor the video;
  * the write is guarded (`post_url IS NULL`, same `post_ref`, still
    `posted`), so a link Miro recorded, or a row that changed, is never
    overwritten. `post_url_source` is the platform's (`tiktok` /
    `youtube`, both in `whop.TRUSTED_SOURCES`): the URL is Postiz relaying
    the platform's own answer, as at publish time.

Postiz unset or unreachable: logged, and every row stays as it was. Its
answers are data: nothing here reaches a prompt. Writing ATLAS's own row is
not an outside-world write, so this runs outside the executor (like the
intake's own bookkeeping) and under ATLAS_WATCH_ONLY too; each link it
fills in is audited.

Seen live 2026-09-30: Postiz lists TikTok posts with the PROFILE URL as
`releaseURL`, so a TikTok clip is not filled in by this pass until Postiz
reports the video itself -- `clips.record_link` stays the way for those.

**A TikTok link handed in** (`record_tiktok_link`, behind `POST
/clips/links/{clip_id}` in `atlas/api/clips.py`): a task on Miro's Mac reads
the public profile in a browser and hands ATLAS candidate video URLs. Its
word is not the platform's, so nothing is written on it alone: the URL must
be exactly `https://www.tiktok.com/@<clips handle>/video/<id>`, and ATLAS
then reads TikTok's own public oEmbed for it (`content.tiktok.oembed`, the
ONE TikTok endpoint ATLAS calls; never a post, a like or a follow; the HTTP
client lives there because this package imports none, tests/test_clips.py)
and requires that TikTok names the clips account as the author, that its
title is the clip's caption (whitespace collapsed -- seen live 2026-09-30:
the 90-character caption of clip 8 came back whole, newlines as spaces),
and that the time TikTok minted the video id (its top 32 bits, unix
seconds) sits within `POST_WINDOW` of the row's `posted_at`. Verified
platform data is not a guess (invariant 6); the row is written through the
same guarded UPDATE as above, under source `tiktok`, and audited. Anything
TikTok does not confirm is refused and nothing is written; TikTok not
readable is 503, not a write.
"""
from __future__ import annotations

import logging
import os
import re
from datetime import UTC, datetime, timedelta
from typing import Any

from ...kernel.state import fetch_all, fetch_one
from ..content import postiz, tiktok
from . import posting

log = logging.getLogger("atlas.clips.links")

LOOKBACK = timedelta(days=14)
# publishDate can sit a little before ATLAS's posted_at: read a margin.
MARGIN = timedelta(days=1)
DEFAULT_INTERVAL_MIN = 60
# The platforms a clip posts to through Postiz, and the providers that mean
# them in Postiz's listing.
PROVIDERS: dict[str, tuple[str, ...]] = {
    "tiktok": postiz.TIKTOK_IDENTIFIERS,
    "youtube": postiz.YOUTUBE_IDENTIFIERS,
}
# The post_url_source each is recorded under: the platform's own (whop.py).
SOURCE = {"tiktok": "tiktok", "youtube": "youtube"}


def enabled() -> bool:
    """`CLIPS_LINKS=off` stops the pass; on by default."""
    return os.getenv("CLIPS_LINKS", "on").strip().lower() not in (
        "0", "off", "false", "no")


def interval() -> timedelta:
    """`CLIPS_LINKS_MIN`: minutes between passes, at least 10."""
    try:
        minutes = int(os.getenv("CLIPS_LINKS_MIN", str(DEFAULT_INTERVAL_MIN)))
    except ValueError:
        minutes = DEFAULT_INTERVAL_MIN
    return timedelta(minutes=max(10, minutes))


def link_for(platform: str, item: dict[str, Any]) -> str | None:
    """The post URL to record for a clip on `platform` from one item of
    Postiz's listing, or None. Pure: published, not deleted, the platform's
    provider, and the platform's own post URL on the clips account."""
    if str(item.get("state") or "").upper() != "PUBLISHED" or item.get("deletedAt"):
        return None
    integration = item.get("integration")
    provider = (str(integration.get("providerIdentifier") or "").lower()
                if isinstance(integration, dict) else "")
    if provider not in PROVIDERS.get(platform, ()):
        return None
    if platform == "tiktok":
        url = str(item.get("releaseURL") or "").strip()
        match = postiz.TIKTOK_URL.match(url)
        if (match is None or match.group(2).lower() != postiz.CLIPS_HANDLE
                or not posting.POST_URL["tiktok"].match(url)):
            return None
        return url
    if platform == "youtube":
        url = postiz.clip_youtube_url(item)
        return url if url and posting.POST_URL["youtube"].match(url) else None
    return None


async def waiting(now: datetime) -> list[dict[str, Any]]:
    """Posted Postiz-platform clips with a post id and no link yet."""
    rows = await fetch_all(
        """SELECT id, platform, post_ref, posted_at FROM clip_outputs
            WHERE status = 'posted' AND post_url IS NULL
              AND post_ref IS NOT NULL AND platform = ANY($1::text[])
              AND posted_at >= $2
            ORDER BY posted_at, id""", list(PROVIDERS), now - LOOKBACK)
    return [dict(r) for r in rows]


async def reconcile(now: datetime | None = None) -> list[str]:
    """One pass. Returns a line per clip filled in, or why none could be."""
    from .._http import explain
    now = now or datetime.now(UTC)
    if not postiz.configured():
        return [f"{postiz.KEY_ENV} unset; Postiz not read"]
    try:
        rows = await waiting(now)
    except Exception as exc:                            # noqa: BLE001
        log.warning("clip links: rows not read: %s", explain(exc))
        return [f"clip rows not read: {explain(exc)}"]
    if not rows:
        return []
    start = min(r["posted_at"] for r in rows) - MARGIN
    try:
        posts = await postiz.posts_between(start, now + MARGIN)
    except Exception as exc:                            # noqa: BLE001
        # Unreachable, refused or malformed: nothing is guessed, nothing
        # is written.
        said = str(exc) if isinstance(exc, postiz.PostizError) else explain(exc)
        log.warning("clip links: Postiz not read, %d clip(s) left as they are: %s",
                    len(rows), said)
        return [f"Postiz not read: {said}"]
    by_id = {str(p.get("id")): p for p in posts if p.get("id")}
    said: list[str] = []
    for row in rows:
        item = by_id.get(str(row["post_ref"]))
        if item is None:
            continue
        platform = str(row["platform"])
        url = link_for(platform, item)
        if url is None:
            continue
        cid = int(row["id"])
        if not await _fill(cid, str(row["post_ref"]), url, platform):
            continue
        log.info("clip links: clip %d's %s link from Postiz post %s: %s", cid,
                 platform, row["post_ref"], url)
        await _audit("clips.link_from_postiz", cid, platform, str(row["post_ref"]),
                     url)
        said.append(f"clip {cid}: {url}")
    return said


async def _fill(cid: int, post_ref: str, url: str, platform: str) -> bool:
    """The one write: the link, guarded so a row that changed, or a link
    already recorded (Miro's included), is never overwritten, and so one
    URL is never two clips'. True when this call wrote it."""
    done = await fetch_one(
        """UPDATE clip_outputs SET post_url = $3, post_url_source = $4
            WHERE id = $1 AND post_ref = $2 AND status = 'posted'
              AND post_url IS NULL
              AND NOT EXISTS (SELECT 1 FROM clip_outputs o
                               WHERE o.post_url = $3 AND o.id <> $1)
        RETURNING id""", cid, post_ref, url, SOURCE[platform])
    return done is not None


async def _audit(event: str, cid: int, platform: str, post_ref: str, url: str,
                 extra: dict[str, Any] | None = None) -> None:
    try:
        from ...kernel.audit import audit
        await audit(event, f"clip_outputs:{cid}",
                    {"platform": platform, "postiz_post": post_ref,
                     "post_url": url, "post_url_source": SOURCE[platform],
                     **(extra or {})})
    except Exception as exc:                            # noqa: BLE001
        from .._http import explain
        log.warning("clip links: clip %d's link not audited: %s", cid,
                    explain(exc))


# ------------------------------------------------------- a link handed in

# How far the time TikTok minted the video id may sit from the row's
# posted_at (seen live: 4 s).
POST_WINDOW = timedelta(minutes=15)
# oEmbed's title came back whole for a 90-character caption (2026-09-30).
# Should TikTok ever cut a longer one short, a title this long that the
# caption begins with still counts; a shorter prefix does not.
TITLE_MIN_PREFIX = 60
# The only shape a handed-in link may take: the canonical video URL on the
# clips account, no short link, no query, no other host. The handle is
# checked against `postiz.CLIPS_HANDLE`, the one place it is named.
VIDEO_URL = re.compile(r"^https://www\.tiktok\.com/@([A-Za-z0-9._]+)/video/(\d{15,25})$")
AUDIT_EVENT = "clips.link_verified_tiktok"


class Refused(Exception):
    """Why a handed-in link was not recorded. `status` is what the route
    answers: 400 malformed, 404 no such clip, 409 not eligible or not what
    TikTok says, 503 TikTok not read. `reason` is a plain sentence."""

    def __init__(self, status: int, reason: str) -> None:
        super().__init__(reason)
        self.status = status
        self.reason = reason


def normalise(text: Any) -> str:
    """Whitespace collapsed to single spaces, ends stripped: how oEmbed's
    title reads a caption with line breaks."""
    return " ".join(str(text or "").split())


def parse_video_url(url: str) -> tuple[str, int]:
    """(handle, video id) of an exact clips-account video URL, else Refused(400)."""
    text = str(url or "").strip()
    match = VIDEO_URL.match(text)
    if match is None or not posting.POST_URL["tiktok"].match(text):
        raise Refused(400, "url must be https://www.tiktok.com/@<handle>/video/<id>")
    handle = match.group(1)
    if handle.lower() != postiz.CLIPS_HANDLE:
        raise Refused(400, f"url is not a video on @{postiz.CLIPS_HANDLE}")
    return handle, int(match.group(2))


def video_time(video_id: int) -> datetime:
    """When TikTok minted this video id: its top 32 bits are unix seconds."""
    return datetime.fromtimestamp(video_id >> 32, UTC)


def title_matches(title: Any, caption: Any) -> bool:
    """Whether oEmbed's title is this caption, whitespace aside (or a long
    prefix of it, should TikTok cut it short)."""
    got, want = normalise(title), normalise(caption)
    if not got or not want:
        return False
    if got == want:
        return True
    return len(got) >= TITLE_MIN_PREFIX and want.startswith(got)


def ineligible(row: dict[str, Any]) -> str | None:
    """Why this clip_outputs row may not take a handed-in TikTok link, or
    None: posted to TikTok through Postiz (a post id), no link yet."""
    cid = row.get("id")
    if str(row.get("platform") or "") != "tiktok":
        return f"clip {cid} is a {row.get('platform') or 'clip'} clip, not TikTok"
    if str(row.get("status") or "") != "posted":
        return f"clip {cid} is {row.get('status') or 'not on file'}, not posted"
    if not row.get("post_ref"):
        return f"clip {cid} has no Postiz post id on file"
    if row.get("post_url"):
        return (f"clip {cid} already has its link ({row['post_url']}, source "
                f"{row.get('post_url_source') or 'unrecorded'}); it is not "
                f"overwritten")
    if row.get("posted_at") is None:
        return f"clip {cid} has no posting time on file"
    return None


async def oembed(url: str) -> dict[str, Any]:
    """TikTok's oEmbed record for `url` (`content.tiktok`): Refused(503)
    when it cannot be read, Refused(409) when TikTok knows no such video."""
    try:
        return await tiktok.oembed(url)
    except tiktok.NoSuchVideo as exc:
        raise Refused(409, str(exc)) from exc
    except tiktok.NotRead as exc:
        raise Refused(503, str(exc)) from exc


async def pending_tiktok() -> list[dict[str, Any]]:
    """The TikTok clips still waiting for a handed-in link: the rows
    `record_tiktok_link` would take one for, oldest first. The Mac task
    matches their captions and posting times against the public profile
    instead of guessing at ids."""
    rows = await fetch_all(
        """SELECT id, platform, status, post_ref, post_url, post_url_source,
                  caption, posted_at
             FROM clip_outputs
            WHERE platform = 'tiktok' AND status = 'posted' AND post_url IS NULL
            ORDER BY posted_at""")
    out: list[dict[str, Any]] = []
    for row in rows:
        row = dict(row)
        if ineligible(row) is not None:
            continue
        posted_at: datetime = row["posted_at"]
        if posted_at.tzinfo is None:
            posted_at = posted_at.replace(tzinfo=UTC)
        out.append({"clip_id": int(row["id"]), "caption": str(row.get("caption") or ""),
                    "posted_at": posted_at.isoformat()})
    return out


async def record_tiktok_link(clip_id: int, url: str) -> dict[str, Any]:
    """Record `url` as clip `clip_id`'s TikTok link once TikTok's own oEmbed
    confirms it is the clips account's video of this caption, minted within
    `POST_WINDOW` of the row's posting. Raises `Refused` otherwise, having
    written nothing. Returns what was recorded and what TikTok said."""
    handle, video_id = parse_video_url(url)
    url = str(url).strip()
    cid = int(clip_id)
    row = await fetch_one(
        """SELECT id, platform, status, post_ref, post_url, post_url_source,
                  caption, posted_at
             FROM clip_outputs WHERE id = $1""", cid)
    if row is None:
        raise Refused(404, f"no clip {cid}")
    row = dict(row)
    why = ineligible(row)
    if why is not None:
        raise Refused(409, why)
    other = await fetch_one(
        """SELECT id FROM clip_outputs WHERE post_url = $1 AND id <> $2
            ORDER BY id LIMIT 1""", url, cid)
    if other is not None:
        raise Refused(409, f"{url} is already recorded as clip {other['id']}'s link")
    posted_at: datetime = row["posted_at"]
    if posted_at.tzinfo is None:
        posted_at = posted_at.replace(tzinfo=UTC)
    minted = video_time(video_id)
    if abs(minted - posted_at) > POST_WINDOW:
        raise Refused(409, f"video {video_id} was minted at "
                           f"{minted:%Y-%m-%d %H:%M:%S} UTC, clip {cid} posted at "
                           f"{posted_at:%Y-%m-%d %H:%M:%S} UTC: more than "
                           f"{int(POST_WINDOW.total_seconds() // 60)} minutes apart")
    data = await oembed(url)
    author = normalise(data.get("author_unique_id")).lower()
    if author != handle.lower():
        raise Refused(409, f"TikTok names @{author or '?'} as the author, not "
                           f"@{handle}")
    product = str(data.get("embed_product_id") or "").strip()
    if product and product != str(video_id):
        raise Refused(409, f"TikTok's record is for video {product}, not "
                           f"{video_id}")
    title = data.get("title")
    if not title_matches(title, row.get("caption")):
        raise Refused(409, f"TikTok's title for video {video_id} is not clip "
                           f"{cid}'s caption")
    post_ref = str(row["post_ref"])
    if not await _fill(cid, post_ref, url, "tiktok"):
        raise Refused(409, f"clip {cid} changed while its link was verified; "
                           f"not recorded")
    verified = {"video_id": str(video_id), "author_unique_id": author,
                "title": normalise(title)[:300], "minted_at": minted.isoformat(),
                "posted_at": posted_at.isoformat()}
    log.info("clip links: clip %d's TikTok link verified with oEmbed: %s", cid, url)
    await _audit(AUDIT_EVENT, cid, "tiktok", post_ref, url,
                 {"via": "handed_in", **verified})
    return {"clip_id": cid, "post_url": url, "post_url_source": SOURCE["tiktok"],
            "verified": verified}
