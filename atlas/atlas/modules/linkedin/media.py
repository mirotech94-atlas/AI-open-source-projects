"""Generated media for the `image` and `video` post kinds.

Task 64: the owner wants pictures, not just a link and some words. This module
makes the picture -- one square Gemini still for `image`, a short silent
slideshow of two stills for `video` -- by calling straight into
`atlas.modules.content.stills` (the Gemini image call Task 55 already built
for the AI-stories reels) and `atlas.modules.content.ffmpeg` (the encode).
Neither is copied; both are reused exactly as they are, at a different
aspect ratio and with no narration.

`poll` carries no media: the composer's own poll UI is the picture.

Every prompt this module builds says, in words, what the image or video may
never be: a real person, a fake screenshot, a fake statistic or chart, a
readable logo. The brand-look line is baked in here, not left to whoever
calls it -- and it names Vault, never Guardian (Task 64: the product was
renamed; the LinkedIn page's own name is the owner's separate call to make).

Never raises. A failed still or a missing ffmpeg is `(None, reason)`; the
caller (`sweep.fill_media_for`) leaves the draft as `needs_media` and says
why in its log rather than publish a post its kind promised a picture for.
"""
from __future__ import annotations

import asyncio
import os
import uuid
from pathlib import Path

from . import KNOWN_PAGES

MEDIA_DIR = Path(os.getenv("LINKEDIN_MEDIA_DIR", "/app/data/linkedin-agent/media"))

# LinkedIn shows a page post's image or video roughly square in the feed;
# the reels pipeline's own stills are 9:16 for a very different surface, so
# this module asks Gemini for its own shape rather than reusing that one.
IMAGE_ASPECT = "1:1"
VIDEO_SECONDS = 7.0     # inside the brief's 6-8 s range
VIDEO_STILLS = 2        # two scenes, each held long enough to read


def brand_look(page: str) -> str:
    """A short, page-specific visual language, so every generated image or
    video looks like it belongs to the same page rather than to whichever
    stock phrase the model reached for that day."""
    if page == "flycheap":
        return ("a clean, modern travel-tech look: deep blue and white, an "
                "abstract flight-route line or a stylised boarding-pass "
                "motif -- no cockpit, no airline livery, no aircraft tail")
    return ("a calm, secure vault look: navy and gold, an abstract vault "
            "door or lockbox motif -- no bank logo, no real institution")


def _forbidden() -> str:
    return ("No real person, no fake screenshot, no fake statistic or "
            "chart, no readable logo or wordmark, no text baked into the "
            "picture.")


def image_prompt(page: str, hook: str) -> str:
    spec = KNOWN_PAGES[page]
    return (f"A square 1:1 illustration for a LinkedIn post from "
            f"{spec['name']}, about: {hook[:160]}. {brand_look(page)}. "
            f"{_forbidden()}")


def video_prompt(page: str, hook: str) -> str:
    spec = KNOWN_PAGES[page]
    return (f"A square 1:1 short video scene for a LinkedIn post from "
            f"{spec['name']}, about: {hook[:160]}. {brand_look(page)}. "
            f"{_forbidden()}")


async def _still(prompt: str, *, aspect: str = IMAGE_ASPECT
                 ) -> tuple[bytes | None, str]:
    from ..content import stills
    data, why = await stills.still(prompt, aspect=aspect)
    return data, (why or "")


def _write(dest: Path, data: bytes) -> str:
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(data)
    return str(dest)


async def make_image(page: str, prompt: str) -> tuple[str | None, str]:
    """One square still, saved under `MEDIA_DIR`. `(path, note)`, or
    `(None, reason)` -- never raises."""
    data, why = await _still(prompt, aspect=IMAGE_ASPECT)
    if data is None:
        return None, why or "Gemini returned no image"
    dest = MEDIA_DIR / f"{page}-{uuid.uuid4().hex[:12]}.png"
    path = await asyncio.to_thread(_write, dest, data)
    return path, f"Gemini still ({IMAGE_ASPECT})"


def _slideshow_argv(paths: list[str], frames_per: int, out_path: str) -> list[str]:
    """One ffmpeg command: a silent, square Ken-Burns slideshow over
    `paths`, no audio track. LinkedIn autoplays a feed video muted, and the
    brief asks for pictures over words, not narration."""
    from ..content import ffmpeg
    size = 1080
    big = size * 2
    argv = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error"]
    for path in paths:
        argv += ["-i", path]
    chains = []
    for i in range(len(paths)):
        chains.append(
            f"[{i}:v]scale={big}:{big}:force_original_aspect_ratio=increase,"
            f"crop={big}:{big},zoompan=z='min(zoom+0.0008,1.1)':"
            f"x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d={max(1, frames_per)}:"
            f"s={size}x{size}:fps={ffmpeg.FPS},setsar=1[v{i}]")
    concat_in = "".join(f"[v{i}]" for i in range(len(paths)))
    chains.append(f"{concat_in}concat=n={len(paths)}:v=1:a=0[outv]")
    argv += ["-filter_complex", ";".join(chains), "-map", "[outv]",
             "-c:v", "libx264", "-preset", "medium", "-crf", "20",
             "-pix_fmt", "yuv420p", "-r", str(ffmpeg.FPS),
             "-movflags", "+faststart", out_path]
    return argv


async def make_video(page: str, prompt: str) -> tuple[str | None, str]:
    """A short silent slideshow from `VIDEO_STILLS` Gemini stills, encoded
    with ffmpeg. `(path, note)`, or `(None, reason)` -- never raises. The
    reels pipeline's own Veo path is deliberately not called here: Veo
    spends against `VEO_MONTHLY_SECONDS`, a cap this module has no business
    drawing on for a LinkedIn page post."""
    from ..content import ffmpeg
    if (why := ffmpeg.missing()) is not None:
        return None, f"refused: {why}"
    stills_paths: list[str] = []
    work = MEDIA_DIR / f"{page}-{uuid.uuid4().hex[:12]}"
    for i in range(VIDEO_STILLS):
        data, why = await _still(f"{prompt} (scene {i + 1} of {VIDEO_STILLS})",
                                 aspect=IMAGE_ASPECT)
        if data is None:
            return None, why or "Gemini returned no image"
        dest = work / f"still_{i}.png"
        stills_paths.append(await asyncio.to_thread(_write, dest, data))
    out = str(work / "video.mp4")
    per = max(1, round(VIDEO_SECONDS * ffmpeg.FPS / len(stills_paths)))
    try:
        await ffmpeg.run(_slideshow_argv(stills_paths, per, out))
    except ffmpeg.FfmpegError as exc:
        return None, f"ffmpeg: {exc}"
    return out, (f"silent square slideshow ({VIDEO_STILLS} Gemini stills, "
                 f"no narration)")


async def make(kind: str, page: str, hook: str) -> tuple[str | None, str]:
    """Dispatch by kind. Only `image` and `video` have a maker; anything
    else (a poll, or a kind this module has never heard of) is refused by
    name rather than silently producing nothing."""
    if kind == "image":
        return await make_image(page, image_prompt(page, hook))
    if kind == "video":
        return await make_video(page, video_prompt(page, hook))
    return None, f"no media maker for kind {kind!r} (a poll needs none)"
