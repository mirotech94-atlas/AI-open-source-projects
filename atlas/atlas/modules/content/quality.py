"""The one quality gate a reel passes before it is eligible to post (RA-E5,
plan D09 / S09).

Every reel reaches the platforms through `kernel.reels.post_or_card`, whether
ATLAS rendered it (the daily reels, a series part, a 30-day calendar day) or
the host's episode job made it and `import-reel` filed it. That function
runs `check_media` first, whatever `REELS_AUTOPUBLISH` says: a reel that
fails is a quality exception with its reason, never a silent pass. Miro's
rule (2026-09-28, settled) decides what happens next: a failed check is
made again once, then it becomes a card, the exception on its first line.
`importer.check` runs the same `check_file` before a row is written.

What is checked, from the actual file (ffprobe / ffmpeg), not from what a
render said about itself:

  * native 1080x1920 from the video stream, with no display rotation;
  * a video stream whose own playable duration sits inside the reel's band:
      - a series episode 60-90 s, anything else 30-60 s (`SERIES_SECONDS`,
        `OTHER_SECONDS`: Miro's lengths, relayed 2026-09-27, the same ones
        `content.reels.beat_range` drafts to);
      - a 30-day calendar reel: its own planned length, the sum of the part
        lengths `calendar.plan` cuts it to from the package Miro supplied,
        +/- `CALENDAR_SLACK_S` for trim and concat drift -- the package, not
        the drafting rule, sets how long those reels are (a FOOD reel is
        12-35 s by design);
  * an audio track whose mean volume is above `ffmpeg.SILENT_DB`;
  * motion (Miro's rule: real motion video only, never stills or slides):
    `ffmpeg.frozen_seconds` runs `freezedetect` over the frames, and more
    than `FROZEN_MAX` of the runtime frozen fails. It catches a slideshow
    of still frames; it does NOT catch a Ken Burns (zoompan) slideshow,
    whose every frame differs a little from the last -- measured on a
    production-style zoompan reel, 0 s frozen. ATLAS's own stills path is
    refused at render time (`content.NO_SLIDES`); a zoompan slideshow filed
    by `import-reel` still passes this gate;
  * the "Made with AI" disclosure at the end of the caption, and no real
    brand or person in it (`guard`);
  * captions on a series episode: a subtitle stream in the file, or the
    render's own record (`content_media.captions`) that it burned a
    non-empty set of cues. Media rendered before that column existed fall
    back to the render's own audited detail ("captions burned ..." or
    "captions NOT burned"), so a reel already made is not remade for want of
    a column. Burned-in pixels cannot be read back, so a series file with
    none of these is an exception, not a pass.

A check that cannot run (ffprobe or ffmpeg missing, the file gone) fails the gate:
"could not be checked" is never "passed".

Not checked here, still open under RA-E5: rights and identity continuity.
`crosspost.autopublish_check` keeps its own older probe as a second net.
"""
from __future__ import annotations

from typing import Any

from ...kernel.state import fetch_all, fetch_one
from .drafts import AI_LABEL

SERIES_SECONDS = (60.0, 90.0)
OTHER_SECONDS = (30.0, 60.0)
# Trimming each part to a frame boundary and concatenating ~10 of them drifts
# a few frames per join; 1.5 s covers that and nothing a missing beat
# (a whole Veo clip, 8 s) could hide in. A value for Miro to confirm.
CALENDAR_SLACK_S = 1.5
EXCEPTION = "quality exception"
# More than half the runtime frozen is a slideshow, not a video. Real reels
# hold a frame now and then -- an older narrated draft clones its last frame
# while the voice finishes, the PART 1 end card -- but never for half of it.
# `ffmpeg.FREEZE_NOISE_DB` / `FREEZE_MIN_S` say what "frozen" means.
FROZEN_MAX = 0.5


def band(series: bool) -> tuple[float, float]:
    return SERIES_SECONDS if series else OTHER_SECONDS


def calendar_band(job: dict[str, Any]) -> tuple[float, float]:
    """A calendar reel's length, from its package's own plan."""
    from . import calendar as cal
    planned = sum(part.length for part in cal.plan(job))
    return max(0.0, planned - CALENDAR_SLACK_S), planned + CALENDAR_SLACK_S


def _playable(info: dict[str, Any]) -> float:
    return float(info.get("video_seconds") or info.get("seconds") or 0.0)


async def check_file(path: str, caption: str, *, series: bool,
                     seconds: tuple[float, float], captions: bool | None = None,
                     motion: bool = True
                     ) -> tuple[dict[str, Any] | None, str | None]:
    """(probe info, None) when the file passes every check; (None, why).
    `motion=False` skips the still-frame rule: Miro, 2026-10-06, the
    Academy's designed reels (typographic editions over the product's
    cover, clips.autopublish_promo) are made of composed frames by his
    choice; the stories lane keeps the rule."""
    from . import ffmpeg, guard
    if not str(caption or "").rstrip().endswith(AI_LABEL):
        return None, f"the caption does not end with the {AI_LABEL!r} disclosure"
    if (why := guard.refusal(caption)) is not None:
        return None, why
    try:
        info = await ffmpeg.probe(path)
    except ffmpeg.FfmpegError as exc:
        return None, f"the file could not be checked: {exc}"
    if not info.get("video"):
        return None, "the file has no video stream"
    if (info["width"], info["height"]) != (ffmpeg.WIDTH, ffmpeg.HEIGHT):
        return None, (f"the file is {info['width']}x{info['height']}, not "
                      f"{ffmpeg.WIDTH}x{ffmpeg.HEIGHT}")
    if info.get("rotation"):
        return None, (f"the frame is rotated {info['rotation']} degrees for "
                      f"display; not a native {ffmpeg.WIDTH}x{ffmpeg.HEIGHT} render")
    low, high = seconds
    length = _playable(info)
    if not low <= length <= high:
        kind = "a series episode" if series else "this reel"
        return None, (f"it plays {length:.1f} s; {kind} must run "
                      f"{low:.0f}-{high:.0f} s")
    if not info.get("audio"):
        return None, "the file has no sound"
    try:
        level = await ffmpeg.mean_volume(path)
    except ffmpeg.FfmpegError as exc:
        return None, f"the sound could not be checked: {exc}"
    if level <= ffmpeg.SILENT_DB:
        return None, f"the sound is silent ({level:.0f} dB)"
    if series and not (captions or info.get("subtitles")):
        return None, ("a series episode needs captions, and there is no subtitle "
                      "stream and no record that captions were burned")
    if not motion:
        return info, None
    # Last: the one check that decodes every frame.
    try:
        frozen = await ffmpeg.frozen_seconds(path, length)
    except ffmpeg.FfmpegError as exc:
        return None, f"the motion could not be checked: {exc}"
    if frozen > FROZEN_MAX * length:
        return None, (f"it is not motion video: {frozen:.1f} s of its {length:.1f} s "
                      f"is a still frame (over {FROZEN_MAX:.0%}); stills or "
                      f"slides never post")
    return info, None


async def check_media(media_id: int, caption: str, *, series: bool,
                      seconds: tuple[float, float], motion: bool = True) -> str | None:
    """None when this rendered or imported reel may post (or be carded as
    clean); otherwise the quality exception's reason."""
    try:
        row = await fetch_one(
            "SELECT path, status, captions FROM content_media WHERE id = $1",
            int(media_id))
    except Exception as exc:  # noqa: BLE001
        from .._http import explain
        return f"the render's record could not be read: {explain(exc)}"
    if row is None or not row["path"] or row["status"] != "done":
        return f"media {media_id} has no finished file"
    captions = row["captions"]
    if captions is None and series:
        captions = await _captions_from_render_log(int(media_id))
    _, why = await check_file(str(row["path"]), caption, series=series,
                              seconds=seconds, captions=captions, motion=motion)
    return why


RENDERS = ("content.render_video", "content.render_calendar_job")


async def _captions_from_render_log(media_id: int) -> bool | None:
    """For media rendered before `content_media.captions`: what the render
    itself reported, from its `execute.finish` row. None when not found."""
    try:
        rows = await fetch_all(
            """SELECT payload FROM actions_log
                WHERE event = 'execute.finish' AND subject = ANY($1::text[])
                  AND payload::jsonb -> 'data' -> 'evidence' ->> 'media_id' = $2
                ORDER BY id DESC LIMIT 1""", list(RENDERS), str(media_id))
    except Exception:  # noqa: BLE001
        return None
    if not rows:
        return None
    try:
        import json
        detail = str(json.loads(rows[0]["payload"])["data"].get("detail") or "")
    except (KeyError, TypeError, ValueError, AttributeError):
        return None
    if "captions NOT burned" in detail:
        return False
    if "captions burned" in detail:
        return True
    return None
