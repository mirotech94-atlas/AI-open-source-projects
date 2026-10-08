"""Import a finished reel made outside ATLAS and post it everywhere.

    python -m atlas.cli import-reel data/import/<file>.mp4 "<caption>" \\
        [--series "<title>" --part N --of M]

Miro, 2026-09-28: "Veo makes the clips for the reel series, sends them to
ATLAS, and ATLAS uploads them to all the platforms available -- simple." The
episode jobs render on Veo and call this. It files the video -- one
`content_media` row with its public `/media` token, one `story_script`
draft -- and hands it to `kernel.reels.post_or_card`, the same path ATLAS's
own reels take: `content.autopublish_reel` posts it to Instagram, the
Facebook Page and TikTok when every automatic check passes, and anything
that fails one (or automatic posting switched off, or the day's automatic
posts used) becomes the ordinary Tier 2 card, the reason first.

The file must already sit under `IMPORT_DIR` (on the host:
/opt/atlas/data/app/import/), so the command can never be pointed at an
arbitrary path. Refused before any row is written: a caption without the
AI disclosure or naming a real brand or person, and a file ffprobe cannot
read. Everything else about the file -- native 1080x1920, a length inside
the series (60-90 s) or other (30-60 s) band, real sound, captions on a
series episode -- is `content.quality`'s gate inside `post_or_card`: a
file that fails it is filed and carded with the quality exception first
(RA-E5; it cannot be remade here). Captions on a series episode are known
from a subtitle stream or the job's own .srt/.vtt/.ass beside the file.
The same series part imported twice is refused, never posted twice.
"""
from __future__ import annotations

import asyncio
import os
import secrets
import shutil
from dataclasses import dataclass
from datetime import UTC, datetime

from ...kernel.state import execute_sql, fetch_one
from .drafts import AI_LABEL

IMPORT_DIR = "data/import"


@dataclass(frozen=True)
class Imported:
    ok: bool
    detail: str
    media_id: int | None = None
    proposal_id: int | None = None


def _inside(path: str) -> str | None:
    """The resolved path when it is a regular .mp4 file under IMPORT_DIR."""
    root = os.path.realpath(IMPORT_DIR)
    real = os.path.realpath(path)
    if os.path.commonpath([root, real]) != root or real == root:
        return None
    if not real.endswith(".mp4") or not os.path.isfile(real):
        return None
    return real


def is_series(source: str) -> bool:
    """An episode of a series was imported with --series and --part."""
    return source.startswith("import:") and ":part " in source


def source_of(series: str | None, part: int | None, path: str) -> str:
    if series and part:
        return f"import:{series}:part {part}"
    return f"import:{os.path.basename(path)}"


def card_reason(caption: str, series: str | None, part: int | None,
                total: int | None, info: dict) -> str:
    what = (f"{series}, part {part} of {total}" if series and part and total
            else f"{series}, part {part}" if series and part else "a finished reel")
    from ...kernel.reels import PLATFORMS
    return (f"Series episode: {what}, made on Veo by the episode job and "
            f"imported. {info['seconds']:.0f} s, {info['width']}x{info['height']}, "
            f"file checked by the quality gate. Where it goes: {PLATFORMS} "
            f"(TikTok gets the #fyp "
            f"caption variant). Caption: {caption[:300]}.")


SIDECARS = (".srt", ".vtt", ".ass")


def captions_of(real: str, info: dict) -> bool | None:
    """Whether the episode carries captions: a subtitle stream in the file,
    or the episode job's own caption file beside it (<name>.srt/.vtt/.ass,
    not empty) -- the job's record that it burned them, as `content_media.
    captions` is ATLAS's own. None when neither: not known is not "yes"."""
    if info.get("subtitles"):
        return True
    stem = os.path.splitext(real)[0]
    for ext in SIDECARS:
        side = stem + ext
        if os.path.isfile(side) and os.path.getsize(side) > 0:
            return True
    return None


async def check(path: str, caption: str) -> tuple[dict | None, str | None]:
    """(probe info, None) when the file may be filed; (None, why) when it is
    refused before any row exists: no AI disclosure, a real brand or person
    in the caption, or a file ffprobe cannot read. Every quality check on
    the file itself (size, length, sound, captions) is `content.quality`'s,
    run by `post_or_card` after filing, so a failure there is a quality
    exception on a card -- not an exit code nobody reads (RA-E5)."""
    from . import ffmpeg, guard
    if not caption.rstrip().endswith(AI_LABEL):
        return None, (f"the caption must end with the {AI_LABEL!r} disclosure "
                      f"(every leg carries the AI label)")
    if (why := guard.refusal(caption)) is not None:
        return None, why
    try:
        info = await ffmpeg.probe(path)
    except ffmpeg.FfmpegError as exc:
        return None, f"the file could not be read: {exc}"
    if not info.get("video"):
        return None, "the file has no video stream"
    return info, None


async def import_reel(path: str, caption: str, *, series: str | None = None,
                      part: int | None = None,
                      total_parts: int | None = None) -> Imported:
    from . import DATA_DIR, MEDIA_TOKEN_TTL
    real = _inside(path)
    if real is None:
        return Imported(False, f"not an .mp4 file under {IMPORT_DIR}/: {path}")
    caption = caption.strip()
    source = source_of(series, part, real)
    if await fetch_one("SELECT id FROM content_drafts WHERE source = $1 "
                       "AND status <> 'dropped'", source):
        return Imported(False, f"already imported ({source}); not carded twice")
    info, why = await check(real, caption)
    if info is None:
        return Imported(False, f"refused: {why}")

    title = (f"{series} — part {part}" + (f" of {total_parts}" if total_parts else "")
             if series and part else os.path.basename(real))
    draft = await fetch_one(
        """INSERT INTO content_drafts (kind, source, title, body, status)
           VALUES ('story_script', $1, $2, $3, 'rendered') RETURNING id""",
        source, title[:200], caption)
    media = await fetch_one(
        """INSERT INTO content_media (draft_id, provider, kind, status)
           VALUES ($1, 'import', 'video', 'rendering') RETURNING id""",
        draft["id"])
    media_id = int(media["id"])
    dest = f"{DATA_DIR}/{media_id}.mp4"
    try:
        await asyncio.to_thread(os.makedirs, DATA_DIR, exist_ok=True)
        await asyncio.to_thread(shutil.copyfile, real, dest)
    except OSError as exc:
        await execute_sql("UPDATE content_media SET status = 'failed' WHERE id = $1",
                          media_id)
        await execute_sql("UPDATE content_drafts SET status = 'dropped' "
                          "WHERE id = $1", draft["id"])
        return Imported(False, f"the file could not be copied: {exc}")
    await execute_sql(
        """UPDATE content_media SET status = 'done', path = $2, media_token = $3,
                  media_token_expires_at = $4, captions = $5 WHERE id = $1""",
        media_id, dest, secrets.token_urlsafe(32),
        datetime.now(UTC) + MEDIA_TOKEN_TTL, captions_of(real, info))

    from ...kernel import reels
    params = {"media_id": media_id, "caption": caption}
    # No remake is possible for a file made outside ATLAS: a quality
    # exception goes straight to a card, the reason on its first line.
    how, pid, said = await reels.post_or_card(
        params, card_reason(caption, series, part, total_parts, info),
        dedup=f"import-reel:{source}", media_id=media_id,
        rollback_plan=reels.CALENDAR_ROLLBACK, series=is_series(source),
        on_fail="card")
    if how == "posted":
        return Imported(True, f"posted media {media_id} on its own: {said}",
                        media_id=media_id)
    if how == "wait":
        # The day's automatic posts are used. Nothing retries a one-off
        # import on its own, so it is marked waiting and the reels tick
        # posts it once the cap frees (`post_waiting`) -- no card, no
        # meddling (Miro, 2026-09-28).
        await execute_sql("UPDATE content_drafts SET status = 'waiting' "
                          "WHERE id = $1", draft["id"])
        return Imported(True, f"filed media {media_id}; today's automatic posts "
                              f"are used, so it posts itself on the next free "
                              f"day ({said})", media_id=media_id)
    if how == "carded":
        return Imported(True, f"card #{pid} raised for media {media_id} "
                              f"(held from posting itself); {said}",
                        media_id=media_id, proposal_id=pid)
    return Imported(False, f"filed as media {media_id}, but neither posted nor "
                           f"carded: {said}", media_id=media_id)


async def post_waiting() -> list[str]:
    """Post imported episodes held by a used daily cap, oldest first, one per
    call. Called from the reels tick. Returns one line for the digest."""
    row = await fetch_one(
        """SELECT d.id AS draft_id, d.body, d.source, m.id AS media_id
             FROM content_drafts d JOIN content_media m ON m.draft_id = d.id
            WHERE d.status = 'waiting' AND d.source LIKE 'import:%'
              AND m.status = 'done'
            ORDER BY d.at LIMIT 1""")
    if row is None:
        return []
    from ...kernel import reels
    media_id = int(row["media_id"])
    params = {"media_id": media_id, "caption": str(row["body"])}
    how, pid, said = await reels.post_or_card(
        params, f"Series episode (imported, waited for a free day). Where it "
                f"goes: {reels.PLATFORMS}.",
        dedup=f"import-reel:waiting:{media_id}", media_id=media_id,
        rollback_plan=reels.CALENDAR_ROLLBACK,
        series=is_series(str(row.get("source") or "")), on_fail="card")
    if how == "wait":
        return []
    status = {"posted": "published", "carded": "rendered"}.get(how, "rendered")
    await execute_sql("UPDATE content_drafts SET status = $2 WHERE id = $1",
                      row["draft_id"], status)
    return [f"Imported episode (media {media_id}): {how}"
            + (f" as card #{pid}" if pid else "") + f" -- {said[:120]}"]
