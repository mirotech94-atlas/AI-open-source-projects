"""ATLAS Academy tutorials on YouTube: render, then publish into a playlist.

Miro, 2026-10-09: teach n8n workflows and Claude's MCP in long-form videos,
each with the same intro and ending, in playlist series. What each episode
says is code-held (tutorial_series.py); how it looks is tutorial_design.py.

How a video is made (all on the server, no paid model):
  * every build step of every scene is one 1920x1080 still (Playwright,
    the same Chromium the reels use) held for exactly its narration plus a
    breath -- one screenshot per step, never per frame, so a ten-minute
    video is a few dozen PNGs, not fourteen thousand;
  * narration is the Fish voice the reels use (`content.stills.narration`,
    FISH_AUDIO_API_KEY + REELS_FISH_VOICE_ID, both only on the server);
  * the intro and the ending are rendered once and cached under the
    tutorials directory, keyed by their words, the voice and the design
    version, so every episode reuses the same two clips and a change to any
    of the three renders them again;
  * every segment is encoded the same way, so ffmpeg joins them without
    re-encoding; chapters come from the measured length of each segment,
    never from a word count.

A preview (`preview=True`, for a machine without the Fish key) uses the
system voice or silence, carries a red banner on every frame, is written
as `video.PREVIEW.mp4` with `audio: placeholder` in its manifest, and is
refused by `publishable_render`, by the publish gate and by the YouTube
upload itself.

How it reaches YouTube. The Academy reel reaches the clips YouTube channel
(AtlasClips, @miroatlas) as a Short through Postiz
(`clips.autopublish_promo` -> `postiz.post_youtube_promo`), Tier 1 under
Miro's standing rule (2026-10-06) with a card when a gate fails. Postiz
gives no playlist or thumbnail call, so the tutorials go to the same
channel through the YouTube Data API module that already exists
(`youtube.api`, the YOUTUBE_CLIPS_* OAuth client): upload private, set the
thumbnail, add to the series playlist (found by title, made if missing),
then public. The same shape as the reel: `academy.autopublish_tutorial` is
Tier 1 behind the gates in `autopublish_check`; anything held becomes the
Tier 2 `academy.publish_tutorial` card with the reason first. The channel
is ACADEMY_TUTORIALS_YT_CHANNEL (default "clips").

Rollback is honest: the video goes back to private and leaves the
playlist; a playlist this run created goes private (deleting either is
Tier 3); a custom thumbnail cannot be put back by the API and is said so.

Review in Slack first (Miro, 2026-10-10: "show me the videos first in
Slack"). The daily tick renders the next scripted episode and raises the
Tier 2 `academy.publish_tutorial` card in #atlas-academy-videos (topic
`academy_videos`: ACADEMY_TUTORIALS_SLACK_CHANNEL, default that channel's
id), with a second message under it carrying the thumbnail, the title,
the full description, the tags, the length, "Intro ✓ / Ending ✓" and a
link to watch the whole MP4 (`/media/tut.<token>`, atlas/api/media.py; the
app has no files:write, so no upload). Nothing reaches YouTube until he
approves; on approval the card publishes at once (upload, thumbnail,
playlist, public). The Tier 1 `academy.autopublish_tutorial` stays, off
unless ACADEMY_TUTORIALS_AUTOPUBLISH=on. One draft a day; on the very
first day (nothing drafted or posted ever) two, so the welcome and the
first Claude episode arrive together. A planned episode without a script
is never rendered: the draft channel is told "script not written".

Every lesson (Miro, 2026-10-17; docs/academy-video-system): its result for
6 s, the 1 s BrandSting at 6-7 s and the scope line, then the lesson with
its quizzes, pauses and reminders, and the EndCard last (at least 15 s, the
right 640 px left empty) -- checked from the manifest before a card is
raised. The channel welcome opens on its narrated intro instead.

3D (2026-10-10): every slide is a transparent PNG laid over its series' 3D
loop (tutorial_design.KIT, rendered once on the Mac by
tools/tutorial3d/build.py; this box has one CPU and no GPU and never draws
3D). Each segment enters the loop where the segment before left it, so the
3D runs unbroken across cuts; the opening and the EndCard stand on flat
navy. Still cheap on one CPU: the
slide is decoded once, then two overlays and one veryfast encode per
segment, and the concat never re-encodes.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import math
import os
import re
import secrets
import shutil
import tempfile
from dataclasses import asdict, dataclass, field, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Literal
from zoneinfo import ZoneInfo

from ...kernel.contracts import ActionResult, FailureClass
from .. import store_origin
from . import presenter, tutorial_music, tutorial_short, video_qa, video_tokens
from . import tutorial_demos as demos
from . import tutorial_design as design
from . import tutorial_motion as motion
from . import tutorial_thumbs as thumbs
from .tutorial_series import (
    COUNTDOWN,
    HASHTAGS_MAX,
    HASHTAGS_MIN,
    INTRO_SAID,
    LAB_DIR,
    LAB_FILES,
    NUMBER_WORDS,
    PAUSE_HOLD,
    PAYOFF_CHAPTER,
    RESULT_SAID,
    SERIES,
    SHORT_MAX,
    SHORT_MIN,
    STORE_URL,
    TAGS_LIMIT,
    TITLE_PATTERNS,
    TITLE_SHOWN_MAX,
    WELCOME,
    WELCOME_WORDS,
    Episode,
    Scene,
    Series,
    claim_refusal,
    downloads_of,
    full_tags,
    generic_chapters,
    hashtags_for,
    held,
    lab_url,
    lead_lower,
    next_line,
    next_up,
    opener_said,
    opener_voiced,
    opening_lines,
    plain,
    result_of,
    series,
    tags_cost,
    tags_refusal,
    title_for,
    topic_line,
)
from .tutorial_series_ai import OPINION_LINE, OPINION_SERIES

log = logging.getLogger("atlas.academy.tutorials")

AI_LABEL = "Made with AI"           # content.drafts.AI_LABEL, said on every ATLAS video
# Miro 2026-10-17 (docs/academy-video-system/narration.md): every description
# says the narration is a synthetic (AI) voice, on the line above the label.
# It names no voice: since 2026-10-19 a lesson may carry NotebookLM's own
# narrator (notebooklm.py) rather than the Fish voice.
VOICE_LINE = ("Narration: synthetic voice (AI). Script written and checked by "
              "ATLAS Academy.")
# The teaching Short's form of it: a caption is short and its first line is
# the Short's title (clips.promo.youtube_title), so it goes above the label.
VOICE_SHORT = "Synthetic voice (AI)."
FPS = 30
PAD = 0.6                            # a breath after each step's narration
SCENE_GAP = 0.6                      # Miro, 2026-10-11 "no rush": more after a scene
MIN_SECONDS, MAX_SECONDS = 240.0, 600.0   # Miro: 4 to 10 minutes
CHAPTER_MIN_SECONDS = 10.0           # YouTube: chapters start 00:00, >= 3, >= 10 s each
DESCRIPTION_MAX = 5000
TITLE_MAX = 100
VIDEO_ID = re.compile(r"^[A-Za-z0-9_-]{11}$")
TAGS_MAX = 500                       # YouTube's tags limit, counted as it counts
# 2026-10-15 (the motion guide, tutorial_motion.py): a step crossfades in
# seven frames, a change of backdrop in twelve; nothing wipes.
TRANSITION = motion.sec(motion.CROSSFADE_FRAMES)   # fade from the step before
XFADE = motion.sec(motion.BACKDROP_FRAMES)         # crossfade when the backdrop changes
# The picture a segment stood on: ("loop", path, offset) or ("demo", path,
# at, step), the step's box cropping the frame the fade starts from.
PrevBg = tuple[Literal["loop"], str, float] | tuple[Literal["demo"], str, float, demos.Step]
ACTION = "academy.publish_tutorial"
ACTION_PIN = "academy.pin_comment"   # 2026-10-13: the quiz + lab comment
WITHDRAWN = "(withdrawn)"            # its rollback's text: an edit, never a delete
REVIEW_TOPIC = "academy_videos"      # channels.slack: #atlas-academy-videos
REVIEW_LINK_TTL = timedelta(days=7)
# The automatic (Tier 1) post's global cap only. Miro 2026-10-11: "one
# video for each series each day" -- seven series, so the cap across them
# is never lower than one per series (per_series_daily stays 1).
PER_DAY_DEFAULT = 7
PER_SERIES_DAILY_DEFAULT = 1         # Miro, 2026-10-11: one video per series per day
# Miro, 2026-10-18: Where AI Is Going posts once a calendar month (UTC, the
# same clock as the daily count); every other series stays daily and the
# global cap stays PER_DAY_DEFAULT.
MONTHLY_SERIES: tuple[str, ...] = ("future",)
DEFAULT_HOUR_UTC = 17
NEW_YORK = ZoneInfo("America/New_York")   # teaching_day: New York's date
# Miro, 2026-10-11: "make him talk slower, no rush" (0.9). 2026-10-18: all
# 30 lessons measured 156-161 words a minute at 0.9, above narration.md's
# 130-150 for explanation; 0.8 brings the model to 144 (FISH_WORDS_PER_SECOND
# x 0.8 x 60), the measured lessons to about 139-143. Fish's prosody speed
# takes 0.5-2.0, default 1 (docs.fish.audio, Text to Speech API reference,
# read 2026-10-18: "Valid range: 0.5 to 2.0"). The voice is unchanged.
VOICE_SPEED_DEFAULT = 0.8
# Narration.md "Pace", explaining: what the default speed must land in.
PACE_TARGET = (140.0, 145.0)
# Fish's pace, measured: n8n #0 (599 words said, 34 lines, 2 quizzes, a
# pause) rendered 232 s at normal speed on 2026-10-11 -- about 3.0 words a
# second. The estimate below divides by this times the voice speed.
FISH_WORDS_PER_SECOND = 3.0
# The cautious side of that pace, for a ceiling estimated before a render
# (the render itself is measured and gated).
CAUTIOUS_WPS = FISH_WORDS_PER_SECOND * 0.9
# The preview's system voice: Daniel, British male, installed on Miro's Mac.
# Miro, 2026-10-11: "don't use the girl voice, only use the guy voice at all
# times" -- `say`'s default (Samantha) is female.
PREVIEW_VOICE = "Daniel"
# Miro, 2026-10-12: "the voice should have feelings and laughter". Fish S2
# reads the scripts' [cue]s; S1 (FISH_AUDIO_MODEL's default, deprecated,
# retiring 2026-12-31) would not. docs.fish.audio models-overview and
# pricing, read 2026-10-12: "s2.1-pro" is the recommended model, and s1,
# s2-pro and s2.1-pro all cost $15.00 per million UTF-8 bytes. The same
# reference_id (the British male voice) is sent either way.
FISH_MODEL_DEFAULT = "s2.1-pro"


def fish_model() -> str:
    """ACADEMY_TUTORIALS_FISH_MODEL, the tutorials' Fish model; default
    s2.1-pro. The reels, phone and menu keep FISH_AUDIO_MODEL."""
    return os.getenv("ACADEMY_TUTORIALS_FISH_MODEL", "").strip() or FISH_MODEL_DEFAULT


def takes_cues(model: str) -> bool:
    """Whether a Fish model reads [bracket] cues (the S2 family) rather
    than speaking them."""
    return model.strip().lower().startswith("s2")


def voiced(text: str, model: str | None) -> str:
    """What the voice is sent: the cues for an S2 model, the words alone for
    anything else (S1, the preview's `say`, silence)."""
    return text if model is not None and takes_cues(model) else plain(text)


def root() -> Path:
    return Path(os.getenv("ACADEMY_TUTORIALS_DIR", "data/academy-tutorials"))


def channel() -> str:
    return os.getenv("ACADEMY_TUTORIALS_YT_CHANNEL", "clips").strip() or "clips"


def enabled() -> bool:
    return os.getenv("ACADEMY_TUTORIALS", "on").strip().lower() not in (
        "0", "off", "false", "no")


def autopost_enabled() -> bool:
    return os.getenv("ACADEMY_TUTORIALS_AUTOPOST", "on").strip().lower() not in (
        "0", "off", "false", "no")


def autopublish_enabled() -> bool:
    """Miro, 2026-10-15: "make sure videos are being posted automatically
    with no stopping" -- a tutorial goes public by itself once every gate
    passes (autopublish_check, the strict publish); a render that fails a
    gate still comes to him as a card. ON unless ACADEMY_TUTORIALS_AUTOPUBLISH
    says off. (2026-10-10/11, superseded: every video reviewed in Slack
    first.) The older ACADEMY_TUTORIALS_AUTOPOST stays an off-switch only:
    it can never turn this back on."""
    return (os.getenv("ACADEMY_TUTORIALS_AUTOPUBLISH", "on").strip().lower()
            not in ("0", "off", "false", "no") and autopost_enabled())


def tags_length(tags: tuple[str, ...] | list[str]) -> int:
    """Tags as YouTube counts them against its 500: the commas between, and
    two quote marks around any tag with a space."""
    return sum(len(t) + (2 if " " in t else 0) for t in tags) + max(0, len(tags) - 1)


def per_day() -> int:
    """ACADEMY_TUTORIALS_PER_DAY: the cap on automatic posts across every
    series (ACADEMY_TUTORIALS_AUTOPUBLISH only). The drafts are paced per
    series instead: per_series_daily()."""
    try:
        return max(0, int(os.getenv("ACADEMY_TUTORIALS_PER_DAY", str(PER_DAY_DEFAULT))))
    except ValueError:
        return PER_DAY_DEFAULT


def per_series_daily() -> int:
    """ACADEMY_TUTORIALS_PER_SERIES_DAILY: drafts a day for each series
    (Miro, 2026-10-11: "we need one video for each series each day")."""
    try:
        return max(0, int(os.getenv("ACADEMY_TUTORIALS_PER_SERIES_DAILY",
                                    str(PER_SERIES_DAILY_DEFAULT))))
    except ValueError:
        return PER_SERIES_DAILY_DEFAULT


def voice_speed() -> float:
    """ACADEMY_TUTORIALS_VOICE_SPEED, Fish's prosody speed for the tutorials
    only (0.5-2.0, docs.fish.audio read 2026-10-11 and 2026-10-18); default
    0.8."""
    try:
        v = float(os.getenv("ACADEMY_TUTORIALS_VOICE_SPEED", str(VOICE_SPEED_DEFAULT)))
    except ValueError:
        return VOICE_SPEED_DEFAULT
    return max(0.5, min(2.0, v)) if math.isfinite(v) else VOICE_SPEED_DEFAULT


def estimate_seconds(ep: Episode, speed: float | None = None,
                     wps: float = FISH_WORDS_PER_SECOND) -> tuple[float, float]:
    """(the whole video, the teaching body), estimated from the words before
    any render: the speech at `wps` x `speed`, a PAD after every line, a
    SCENE_GAP after every scene, the silent countdowns and holds, the sting.
    A lower `wps` is the cautious side for a ceiling or an "under five
    minutes" claim; the render itself is still measured and gated."""
    rate = wps * (voice_speed() if speed is None else speed)
    body = _spoken(ep.scenes, rate)
    total = (body + _spoken(opening(ep), rate)
             + _opening_estimate(ep, rate) + _ending_estimate(rate))
    return total, body


def opening(ep: Episode) -> tuple[Scene, ...]:
    """Scenes played between the opening and the lesson: none since
    2026-10-17. The payoff's result is the opening itself (ResultTitle) and
    the scope line says what the hook said (what you need, the lab)."""
    return ()


def first_scene_kind(ep: Episode) -> str:
    """What the video opens on: "payoff" (its result, ResultTitle) for every
    scripted lesson; the welcome opens on its intro card."""
    return "payoff" if ep.opener_topic and ep.payoff is not None else "intro"


def _opening_estimate(ep: Episode, rate: float) -> float:
    """Estimated seconds of the opening: for a lesson the result's fixed
    RESULT_SECONDS, the 1 s sting and the narrated scope line; for the
    welcome its narrated intro and topic line."""
    if ep.opener_topic:
        scope = opening_lines(ep)[1]
        return (design.RESULT_SECONDS + design.STING_SECONDS
                + len(plain(scope).split()) / rate + PAD)
    said = [opener_said(ep), topic_line(ep)]
    return sum(len(x.split()) / rate + PAD for x in said if x)


def _ending_estimate(rate: float) -> float:
    """The EndCard (2026-10-17): at least END_CARD_MIN seconds. The recap's
    closing sentence moves onto it and is already counted in the lesson, so
    this is the cautious side (it may also run a little longer)."""
    return design.END_CARD_MIN


def _spoken(scenes: tuple[Scene, ...], rate: float) -> float:
    """Estimated seconds of `scenes` at `rate` words a second."""
    t = 0.0
    for b in beats(scenes):
        t += b.silent if b.silent else len(b.said.split()) / rate + PAD
        t += SCENE_GAP if b.last_of_scene else 0.0
    return t


# docs/academy-youtube-plan.md rule 9 (the reference tutorials of 20 minutes
# or less reach their first hands-on chapter at a median of 82 s): in an
# episode whose title promises a build, the first scene where the viewer
# does something starts within HANDS_ON_SECONDS of 0:00.
HANDS_ON_KINDS = ("demo", "steps", "code", "lab")
HANDS_ON_SECONDS = 90.0


def hands_on_at(ep: Episode, speed: float | None = None,
                wps: float = FISH_WORDS_PER_SECOND) -> float | None:
    """Estimated seconds from 0:00 (the result, the sting, the scope line) to
    the start of the first hands-on scene; None when the episode has none."""
    rate = wps * (voice_speed() if speed is None else speed)
    t = _opening_estimate(ep, rate)
    t += _spoken(opening(ep), rate)
    for i, sc in enumerate(ep.scenes):
        if sc.kind in HANDS_ON_KINDS:
            return t + _spoken(ep.scenes[:i], rate)
    return None


def minutes_of(seconds: float) -> int:
    """The whole video's length rounded UP to the next minute
    (docs/academy-youtube-plan.md rule 1: 4:01 is "5 minutes")."""
    return max(1, math.ceil(round(seconds, 3) / 60))


def opener_refusal(ep: Episode, spoken: int, seconds: float) -> str | None:
    """Invariant 6: an opening that says "in under N minutes" must say the
    title's N, and the video must run under N. Since 2026-10-17 the opening
    says no number (spoken 0, the result-first opening); a render made
    before then still carries its claim and is held to it."""
    if not ep.opener_topic or spoken == 0:
        return None
    if spoken not in NUMBER_WORDS:
        return f"the opener has no spoken minutes ({spoken!r})"
    if spoken != minutes_of(seconds):
        return (f"the opener says under {spoken} minutes but the video runs "
                f"{seconds:.0f} s, {minutes_of(seconds)} minutes in the title")
    if seconds >= spoken * 60:
        return (f"the opener says under {spoken} minutes but the video runs "
                f"{seconds:.0f} s")
    return None


# 2026-10-13: five lessons went live saying "my new channel" -- renders
# made before the opener rule shipped, published by a gate that read only the
# manifest. So the gate listens: the first HEARD_SECONDS of the file go
# through the same Groq Whisper the reels use, and the words must be the
# opener's. A check that cannot run holds the publish (invariant 7).
# 2026-10-17 (Miro; the video system's result-first opening): a lesson's
# file must say "Here is the result" (RESULT_MARK) and none of the welcome
# words; the old "welcome back" mark is gone. The result line ends before
# the sting at 6 s, well inside HEARD_SECONDS.
HEARD_SECONDS = 30
WELCOME_MARK = "new channel"         # the channel welcome, and only it, says it
RESULT_MARK = RESULT_SAID.lower()    # "here is the result"


def _heard_words(heard: str) -> str:
    """Whisper's text as " word word ": lower case, "here's" read as "here
    is", a digit as its word, punctuation gone."""
    said = re.sub(r"\b(\d{1,2})\b",
                  lambda m: NUMBER_WORDS.get(int(m.group(1)), m.group(1)),
                  heard.lower().replace("\u2019", "'"))
    said = re.sub(r"\bhere's\b", "here is", said)
    words = re.sub(r"[^a-z ]+", " ", said)
    return f" {re.sub(r' +', ' ', words).strip()} "


def heard_refusal(ep: Episode, heard: str) -> str | None:
    """None when `heard` (Whisper's text for the file's first seconds) is the
    opening this episode claims: a lesson states its result ("here is the
    result") and says no welcome word ("welcome", "guys", "new channel");
    the channel welcome says "new channel"."""
    words = _heard_words(heard)
    if not ep.opener_topic:
        if WELCOME_MARK not in words:
            return f"the welcome's opener was not heard: {heard[:80]!r}"
        return None
    if f" {RESULT_MARK} " not in words:
        return (f"the lesson's opening does not state its result; the file says "
                f"{heard[:80]!r}")
    if said := [w for w in WELCOME_WORDS if f" {w} " in words]:
        return (f"the lesson's opening has a welcome ({', '.join(said)}); the file "
                f"says {heard[:80]!r}")
    return None


async def opener_heard(d: Path, m: Manifest) -> tuple[str | None, str | None]:
    """The words Whisper hears in the render's first HEARD_SECONDS:
    `(text, None)` or `(None, why)`. Reads the finished video.mp4, not the
    cached intro part, so it is the uploaded file that is checked."""
    from ..content import ffmpeg
    from ..content.captions import word_timings
    try:
        audio = await ffmpeg.run(["ffmpeg", "-v", "error", "-t", str(HEARD_SECONDS),
                                  "-i", str(d / "video.mp4"), "-vn", "-ac", "1",
                                  "-ar", "16000", "-f", "mp3", "-"], timeout=60)
    except ffmpeg.FfmpegError as exc:
        return None, f"ffmpeg: {exc}"
    words, why = await word_timings(bytes(audio), "opener.mp3")
    if words is None:
        return None, why
    return " ".join(w.text for w in words), None


def final_title(ep: Episode, seconds: float) -> str:
    """The title as uploaded: the level's formula with the measured minutes
    (the welcome, and an unmeasured render, keep the written title)."""
    return title_for(ep, minutes_of(seconds) if seconds > 0 else None)


def title_refusal(ep: Episode, title: str, seconds: float) -> str | None:
    """None when `title` follows its level's formula, its minutes are not
    under the real length, and it is at most 60 characters with the tool
    name first (Miro 2026-10-18); otherwise why it may not be published."""
    if len(title) > TITLE_MAX:
        return f"the title is over {TITLE_MAX} characters"
    if ep.title_hook:
        got = TITLE_PATTERNS[ep.formula_level].match(title)
        if got is None:
            return (f"the title does not follow the Level {ep.formula_level} formula: "
                    f"{title!r}")
        if int(got["min"]) < minutes_of(seconds):
            return (f"the title says {got['min']} minutes but the video runs "
                    f"{seconds:.0f} s ({minutes_of(seconds)} minutes rounded up)")
    # tags-and-hashtags.md: a check, never a truncation
    if len(title) > TITLE_SHOWN_MAX:
        return (f"the title is {len(title)} characters, over {TITLE_SHOWN_MAX}; "
                f"shorten its hook: {title!r}")
    # "Tool name first" is advice for the hook, not a hold: a hold here stopped
    # whole series (2026-10-18). tool_first() lists the ones to reword.
    return None


def badge_text(minutes: int) -> str:
    """The thumbnail's time badge (the plan's rule 4): "5 MIN"."""
    return f"{minutes} MIN"


def thumbnail_takes_badge() -> bool:
    """Whether tutorial_design.thumbnail draws a `badge` yet. The look is
    the design module's; until it takes one, the badge is computed and kept
    in the manifest, and the thumbnail is drawn as before."""
    import inspect
    return "badge" in inspect.signature(design.thumbnail).parameters


def thumbnail_doc(ep: Episode, s: Series, badge: str, image: str = "") -> str:
    """The episode's thumbnail page (tutorial_thumbs, batch 7): its family
    and its real `image` follow from the episode, the badge from the
    measured minutes (drawn in sentence case)."""
    return thumbs.page(ep, level=ep.level, badge=badge, image=image)


def thumb_file(d: Path) -> Path:
    """The thumbnail a render uploads: its PNG (batch 7), or an older
    render's JPG until that draft is rendered again. A PNG older than the
    JPG is an old preview's (they shared the folder) and never uploaded;
    a render writes its JPG first, so its own PNG is never older."""
    png, jpg = d / "thumb.png", d / "thumb.jpg"
    if not png.is_file():
        return jpg
    if jpg.is_file() and png.stat().st_mtime < jpg.stat().st_mtime:
        return jpg
    return png


def upload_tags(s: Series, ep: Episode) -> tuple[str, ...]:
    return full_tags(s.key, ep, datetime.now(UTC).year)


def hour_utc() -> int:
    try:
        return max(0, min(23, int(os.getenv("ACADEMY_TUTORIALS_HOUR_UTC",
                                            str(DEFAULT_HOUR_UTC)))))
    except ValueError:
        return DEFAULT_HOUR_UTC


# ------------------------------------------------------------------ catalog

def resolve(series_key: str, episode: int | str) -> tuple[Series, Episode] | str:
    """The (series, episode) the params name, or why not. Closed: only what
    the catalog holds."""
    s = series(str(series_key))
    if s is None:
        return f"no tutorial series {series_key!r}; one of {', '.join(x.key for x in SERIES)}"
    try:
        n = int(episode)
    except (TypeError, ValueError):
        return f"episode must be a number, not {episode!r}"
    ep = s.episode(n)
    if ep is None:
        return f"{s.playlist} has no episode {n}"
    return s, ep


def episode_dir(s: Series, ep: Episode) -> Path:
    return root() / "episodes" / f"{s.key}-{ep.number:02d}-{ep.slug}"


def vet(params: dict) -> str | None:
    got = resolve(params.get("series", ""), params.get("episode", ""))
    return got if isinstance(got, str) else None


def vet_video(params: dict) -> str | None:
    if (why := vet(params)) is not None:
        return why
    if not VIDEO_ID.match(str(params.get("video_id") or "")):
        return "video_id must be an 11-character YouTube video id"
    return None


# ------------------------------------------------------------- the manifest

@dataclass
class Manifest:
    series: str
    episode: int
    audio: str                       # "fish" or "placeholder"
    seconds: float
    chapters: list[tuple[float, str]] = field(default_factory=list)
    width: int = design.W
    height: int = design.H
    design: str = design.DESIGN_VERSION
    voice_note: str = ""
    rendered_at: str = ""
    # The segments in order, by file name: the cached intro first and the
    # cached ending last, always (Miro, 2026-10-10).
    parts: list[str] = field(default_factory=list)
    intro_seconds: float = 0.0
    outro_seconds: float = 0.0
    body_seconds: float = 0.0        # the teaching: not intro, topic, hook or ending
    script: str = ""                 # script_key(): the words and the look rendered
    backdrop: str = ""               # the series' 3D loop under every slide
    minutes: int = 0                 # minutes_of(seconds): the title's number
    badge: str = ""                  # the time badge drawn on the thumbnail ("" none)
    # 2026-10-11: the picture under every segment after it, in order: a topic
    # (tutorial_design.TOPICS) or "demo:<key>" for a live-demo recording
    backdrops: list[str] = field(default_factory=list)
    # 2026-10-12: the opener as voiced, and its "under N minutes" (0 = the
    # welcome, which claims none); opener_refusal holds N to `seconds`.
    opener: str = ""
    spoken_minutes: int = 0
    # 2026-10-13 (rule 16): every narrated line as (start, end, said), for
    # the caption track; cues stripped at write time.
    timings: list[tuple[float, float, str]] = field(default_factory=list)
    # 2026-10-13: Miro's clips in this render, "<slot>:<sha256 prefix>"
    # (none: the narrated intro and ending).
    presenter: list[str] = field(default_factory=list)
    # The tutorial_music.MUSIC_VERSION bed mixed into the file ("" = none:
    # ready() refuses it). Rule 22 since 2026-10-17: only inside
    # `music_windows` (the sting, the end card), silence everywhere else.
    music: str = ""
    # 2026-10-17: the (start, end) spans the bed sounds in; empty in a render
    # made before then, whose bed runs under the whole file.
    music_windows: list[tuple[float, float]] = field(default_factory=list)
    # 2026-10-17 (the video system's result-first opening): "result" when
    # the file opens on the result with the sting at sting_at for
    # sting_seconds, "welcome" for the channel welcome's narrated intro,
    # "" in a render made before then.
    opening: str = ""
    sting_at: float = 0.0
    sting_seconds: float = 0.0
    # 2026-10-17 (the EndCard): it starts at card_at and lasts card_seconds
    # to the file's end; it names next_title (a "episode" or a "playlist").
    card_at: float = 0.0
    card_seconds: float = 0.0
    next_title: str = ""
    next_kind: str = ""
    # Batch 9 (video_qa.Pages): the pages Chromium drew at a video size, the
    # smallest text on them and what broke the type floor or the safe area.
    # 0 measured (a render made before then) holds the post (invariant 7).
    pages_measured: int = 0
    min_text_px: float = 0.0
    text_faults: list[str] = field(default_factory=list)
    text_fault_count: int = 0        # all of them; text_faults keeps the first few
    # 2026-10-18: where the demo recording sat on the frame (x, y, w, h),
    # tutorial_demos.HOLE; [] in a render made before, which used
    # tutorial_demos.LEGACY_HOLE. A teaching Short crops the demo there.
    demo_hole: list[int] = field(default_factory=list)
    # 2026-10-19 (notebooklm.py): a render NotebookLM made, ingested from the
    # Mac runner's drop (audio "notebooklm"): which renderer, the sha256 of
    # the source text it was given, its notebook and when it was made.
    renderer: str = ""
    source_sha256: str = ""
    notebook_url: str = ""
    made_at: str = ""


def kit_stamp_of(ep: Episode) -> str:
    """The kit that draws `ep`: kit.json's stamp, the same for every episode
    since the combined bump folded the AI series' kit-ai.json into it."""
    return design.kit_stamp()


def script_key(ep: Episode) -> str:
    """Changes whenever the episode's words or the design change, so a render
    of an older script or look is never reused. `ep!r` is every field of
    the episode and its scenes, the teaching components included (no field
    is kept out of repr since the combined bump of 2026-10-18)."""
    raw = (f"{ep!r}|{topic_line(ep)}|{design.DESIGN_VERSION}|{kit_stamp_of(ep)}|"
           f"{design.look_stamp()}|"
           f"{INTRO_SAID}|{opener_said(ep)}|card={end_card_text(ep)}"
           f"|speed={voice_speed():g}|gap={SCENE_GAP:g}|model={fish_model()}"
           f"|{demos.stamp(demo_keys(ep))}|music={tutorial_music.MUSIC_VERSION}")
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def demo_keys(ep: Episode) -> list[str]:
    """The live-demo recordings an episode shows, in order."""
    return [sc.demo for sc in opening(ep) + ep.scenes if sc.kind == "demo"]


def music_spans(seconds: float, *, card_seconds: float,
                sting: tuple[float, float] | None) -> list[tuple[float, float]]:
    """Rule 22 (Miro, 2026-10-17; docs/academy-video-system/narration.md):
    where the bed sounds in a render of `seconds`: the sting's span (6-7 s
    in a result-first lesson; None, the welcome has no sting) and the
    EndCard's, the last `card_seconds` of the file."""
    return tutorial_music.windows(seconds, sting=sting,
                                  end_card=(seconds - card_seconds, seconds))


def sting_span(m: Manifest) -> tuple[float, float] | None:
    """Where the manifest's sting plays: its own record since 2026-10-17
    (none in the welcome), the first STING_SECONDS in an older render."""
    if m.opening == "result":
        return (m.sting_at, round(m.sting_at + m.sting_seconds, 3))
    if m.opening == "welcome":
        return None
    return (0.0, design.STING_SECONDS)


# A render made before the EndCard ended on Miro's clip and a 5 s silent 3D
# card, or on the narrated ending; its bed window is read the way it was mixed.
LEGACY_CARD_SECONDS = 5.0


def music_windows(m: Manifest) -> list[tuple[float, float]]:
    """`music_spans` from a manifest's own timings."""
    if m.card_seconds > 0:
        tail = m.card_seconds
    elif any(p.startswith("outro:") for p in m.presenter):
        tail = LEGACY_CARD_SECONDS
    elif m.outro_seconds > 0:
        tail = min(m.outro_seconds, tutorial_music.END_CARD_FALLBACK)
    else:
        tail = tutorial_music.END_CARD_FALLBACK
    return music_spans(m.seconds, card_seconds=tail, sting=sting_span(m))


def reusable(s: Series, ep: Episode) -> bool:
    """A finished render of exactly this script and look, with its bookends.
    Reads the disk: call it off the event loop."""
    d = episode_dir(s, ep)
    if publishable_render(str(d / "video.mp4")) is not None:
        return False
    m = read_manifest(d)
    return m is not None and m.script == script_key(ep) and bookends_refusal(m) is None


def bookends_refusal(m: Manifest) -> str | None:
    """None when the render opens as the video system says and ends with the
    ending: a lesson on its result with the 1 s sting at 6-7 s
    (opening_refusal), the welcome on its narrated intro."""
    if not m.parts or not m.parts[0].startswith("intro-"):
        return "the video does not start with its opening"
    if (why := opening_refusal(m)) is not None:
        return why
    if not m.parts[-1].startswith("outro-"):
        return "the video does not end with its end card"
    return ending_refusal(m)


def ending_refusal(m: Manifest) -> str | None:
    """Miro, 2026-10-17 (docs/academy-video-system EndCard): the end card
    runs at least END_CARD_MIN seconds, to the file's last frame, in a video
    of at least ENDSCREEN_VIDEO_MIN seconds; no presenter clip before it."""
    if m.presenter:
        return "the video ends on Miro's clip, not on the end card"
    if m.card_seconds < design.END_CARD_MIN - 0.5 / FPS:
        return (f"the end card lasts {m.card_seconds:.1f} s, under "
                f"{design.END_CARD_MIN:g} s")
    if abs(m.card_at + m.card_seconds - m.seconds) > 0.1:
        return (f"the end card ends at {m.card_at + m.card_seconds:.1f} s, not at the "
                f"video's end ({m.seconds:.1f} s)")
    if m.seconds < ENDSCREEN_VIDEO_MIN:
        return (f"the video runs {m.seconds:.0f} s; an end screen needs "
                f"{ENDSCREEN_VIDEO_MIN:g} s")
    return None


# YouTube shows end screens on videos of at least 25 s (support.google.com
# /youtube/answer/6388789, read 2026-10-17 in .video-gap.md).
ENDSCREEN_VIDEO_MIN = 25.0


def end_card_text(ep: Episode) -> str:
    """What the end card says, from the catalog: it keys the render (a newly
    scripted next lesson redraws it) and ready() holds a render to it."""
    s = next((x for x in SERIES if ep in x.episodes), None)
    nxt = next_up(s.key, ep) if s is not None else None
    return "|".join([ep.result, nxt.kind if nxt else "", nxt.title if nxt else "",
                     nxt.why if nxt else "", "shop" if downloads_of(ep) else ""])


def opening_refusal(m: Manifest) -> str | None:
    """Miro, 2026-10-17 (docs/academy-video-system/episode-structure.md): a
    lesson opens on its result, the sting at exactly STING_AT for
    STING_SECONDS, and no presenter clip; the welcome opens on its intro."""
    if any(p.startswith(f"{presenter.OPENING}:") for p in m.presenter):
        return "the video opens on Miro's intro clip, not on the result"
    if m.opening == "welcome" and m.series == "channel":
        return None
    if m.opening != "result":
        return "the video was made before the result-first opening; render it again"
    if (abs(m.sting_at - design.STING_AT) > 0.5 / FPS
            or abs(m.sting_seconds - design.STING_SECONDS) > 0.5 / FPS):
        return (f"the sting plays at {m.sting_at:.2f}-{m.sting_at + m.sting_seconds:.2f} s,"
                f" not {design.STING_AT:g}-{design.STING_AT + design.STING_SECONDS:g} s")
    return None


def demo_hole_of(m: Manifest) -> tuple[int, int, int, int]:
    """Where `m`'s render laid the demo recording: its own record, or the
    pre-2026-10-18 hole for a render that has none."""
    if len(m.demo_hole) == 4:
        x, y, w, h = (int(v) for v in m.demo_hole)
        return x, y, w, h
    return demos.LEGACY_HOLE


def read_manifest(d: Path) -> Manifest | None:
    try:
        raw = json.loads((d / "manifest.json").read_text())
        raw["chapters"] = [(float(t), str(n)) for t, n in raw.get("chapters") or []]
        raw["timings"] = [(float(a), float(b), str(t))
                          for a, b, t in raw.get("timings") or []]
        raw["music_windows"] = [(float(a), float(b))
                                for a, b in raw.get("music_windows") or []]
        return Manifest(**raw)
    except (OSError, ValueError, TypeError):
        return None


PUBLISHABLE_AUDIO = ("fish", "notebooklm")


def publishable_render(path: str) -> str | None:
    """None when `path` is a finished tutorial render: a plain `video.mp4`
    (never a symlink) directly in an episode directory under the tutorials
    root, whose manifest says the Fish voice or an ingested NotebookLM
    render (2026-10-19). Otherwise why not. Reads the disk: call it off the
    event loop."""
    p = Path(path)
    if p.is_symlink() or p.name != "video.mp4" or not p.is_file():
        return "not a finished tutorial video.mp4"
    episodes = (root() / "episodes").resolve()
    if p.resolve().parent.parent != episodes:
        return "not inside the tutorials' episodes directory"
    m = read_manifest(p.parent)
    if m is None:
        return "no readable manifest next to it"
    if m.audio not in PUBLISHABLE_AUDIO:
        return "it was rendered with placeholder audio (a preview), never uploaded"
    return None


# ------------------------------------------------------- description, chapters

def stamp(seconds: float) -> str:
    s = int(seconds)
    return f"{s // 60}:{s % 60:02d}" if s < 3600 else f"{s // 3600}:{s % 3600 // 60:02d}:{s % 60:02d}"


def chapters_refusal(chapters: list[tuple[float, str]], total: float) -> str | None:
    if len(chapters) < 3:
        return "fewer than three chapters"
    if (generic := generic_chapters([name for _, name in chapters])):
        return (f"chapter name(s) {', '.join(map(repr, generic))} say nothing a viewer "
                "searches for; name what the chapter does")
    if chapters[0][0] != 0:
        return "the first chapter does not start at 0:00"
    ends = [t for t, _ in chapters[1:]] + [total]
    for (t, name), end in zip(chapters, ends, strict=True):
        if end - t < CHAPTER_MIN_SECONDS:
            return f"chapter {name!r} is under {CHAPTER_MIN_SECONDS:.0f} s"
    return None


def _srt_stamp(seconds: float) -> str:
    ms = round(seconds * 1000)
    h, rest = divmod(ms, 3_600_000)
    mn, rest = divmod(rest, 60_000)
    s, ms = divmod(rest, 1000)
    return f"{h:02d}:{mn:02d}:{s:02d},{ms:03d}"


def shown(said: str) -> str:
    """A line as read, not as voiced: "agency dot storefront dot test"
    is agency.storefront.test on screen (the store's spoken host first, whole)."""
    text = plain(said).replace(store_origin.spoken(), store_origin.host())
    return text.replace(" dot ", ".")


def caption_track(m: Manifest, ep: Episode) -> str:
    """Rule 16: the script as an SRT track, the cues stripped, one cue per
    narrated line in the order the render spoke them."""
    out = []
    for i, (a, b, said) in enumerate(m.timings, 1):
        out.append(f"{i}\n{_srt_stamp(a)} --> {_srt_stamp(b)}\n{shown(said)}\n")
    return "\n".join(out)


def description(s: Series, ep: Episode, chapters: list[tuple[float, str]],
                minutes: int | None = None) -> str:
    # The plan's rule 6: the level and the time lead ("Level 0 · Basics ·
    # 5 minutes"), then what you'll be able to do. The channel welcome is no
    # lesson and carries neither.
    # Miro 2026-10-18 (tags-and-hashtags.md): the first two lines, the ones
    # shown before "more", are the promised result and the prerequisite
    # (rule 10's "What you need"); then the level and time (rule 6), the
    # summary, and for Where AI Is Going the opinion label.
    if s.playlist:
        when = f" · {minutes} minutes" if minutes else ""
        need = ("What you need: " + "; ".join(ep.needs) + "." if ep.needs
                else f"{ep.level_label}{when}")
        head = [f"What you'll have: {lead_lower(result_of(ep))}.", need, "",
                *([f"{ep.level_label}{when}"] if ep.needs else []), ep.summary]
        if s.key in OPINION_SERIES:
            head += ["", OPINION_LINE]
    else:
        head = [ep.summary]
    # Rule 12: the lab files, free from the store itself, near the top.
    files = downloads_of(ep)
    if files:
        head += ["", "Free lab files (no sign-up):"] + [f"{lab_url(n)}" for n in files]
    lines = [*head, "", f"More from ATLAS Academy: {STORE_URL}"]
    if ep.ask:
        lines += ["", ep.ask]
    lines += ["", ("Subscribe for the next episode, like if it helped, and comment "
                   "what you'd like to see next.")]
    # A NotebookLM render (2026-10-19) has no measured chapters: none are
    # listed rather than guessed.
    if chapters:
        lines += ["", "Chapters"] + [f"{stamp(t)} {name}" for t, name in chapters]
    if ep.links:
        lines += ["", "Links"] + [f"{name}: {url}" for name, url in ep.links]
    lines += [""]
    if s.playlist:
        lines += [f"This video is part of the playlist \"{s.playlist}\"."]
    lines += [f"ATLAS Academy: {STORE_URL}"]
    # 2026-10-18 (tags-and-hashtags.md): 3-5 hashtags at the end, the
    # strongest three first; only the voice line and the label follow.
    lines += ["", " ".join(hashtags_for(s.key, ep))]
    lines += ["", VOICE_LINE, AI_LABEL]
    return "\n".join(lines)


def downloads_refusal(ep: Episode) -> str | None:
    """None when the live store serves every lab file the description links,
    byte for byte; otherwise why not. A description never links a file the
    store does not have yet: the store republishes itself after a deploy
    (publish.republish_if_changed), and until then the video waits. Blocking
    (it reads the live release from disk)."""
    names = downloads_of(ep)
    if not names:
        return None
    from ..agency import hosting
    from .publish import SLUG
    root = hosting.PUBLISH_ROOT
    try:
        live = hosting.live_release(root, SLUG)
        base = hosting.release_dir(root, SLUG, live) if live else None
    except (ValueError, OSError):
        base = None
    for name in names:
        want = LAB_FILES.get(name)
        if want is None:
            return f"the lab file {name!r} is not in LAB_FILES"
        try:
            got = (base / LAB_DIR / name).read_bytes() if base else None
        except OSError:
            got = None
        if got != want.encode():
            return (f"the live store does not serve {lab_url(name)} as built yet; "
                    "publish the store (academy.publish_shop) first")
    return None


HASHTAG = re.compile(r"(?<![\w#])#[A-Za-z]\w*")


def hashtags_refusal(text: str) -> str | None:
    """YouTube Help, "Use hashtags for video search" (answer/6390658): the
    first three show above the title, more than 60 are all ignored, and
    stuffing is a spam-policy matter. Ours (Miro 2026-10-18): 3-5, all on one
    line at the end, just above the voice line; none in the body; #shorts
    never first."""
    lines = text.rstrip().splitlines()
    try:
        at = lines.index(VOICE_LINE) - 2           # the hashtag line, a blank between
    except ValueError:
        return "the description does not disclose the synthetic voice (AI)"
    tail = HASHTAG.findall(lines[at]) if at >= 0 else []
    if not HASHTAGS_MIN <= len(tail) <= HASHTAGS_MAX or " ".join(tail) != lines[at]:
        return (f"the description must end with one line of {HASHTAGS_MIN}-"
                f"{HASHTAGS_MAX} hashtags above the voice line")
    if tail[0].lower() == "#shorts":
        return "#shorts is never the first hashtag"
    body = [h for i, ln in enumerate(lines) if i != at for h in HASHTAG.findall(ln)]
    if body:
        return f"hashtags in the description body: {' '.join(body)}"
    return None


def description_refusal(text: str) -> str | None:
    """Refused rather than cut: a cut could drop the store link or the label."""
    if len(text) > DESCRIPTION_MAX:
        return f"the description is {len(text)} characters, over {DESCRIPTION_MAX}"
    if STORE_URL not in text:
        return f"the description does not carry {STORE_URL}"
    if VOICE_LINE not in text:
        return "the description does not disclose the synthetic voice (AI)"
    if not text.rstrip().endswith(AI_LABEL):
        return f"the description does not end with {AI_LABEL!r}"
    return hashtags_refusal(text)


# --------------------------------------------------------------- narration

async def _narrate(text: str, out_base: str, preview: bool) -> tuple[str, str, str | None]:
    """(audio file, voice note, None) or ("", "", why). The Fish voice, or
    for a preview the system voice (macOS `say`) or silence."""
    from ..content import stills
    if not preview:
        model = fish_model()
        audio, note, why = await stills.narration(voiced(text, model), speed=voice_speed(),
                                                  model=model)
        if audio is None:
            return "", "", why or "no narration"
        path = out_base + ".mp3"
        await asyncio.to_thread(Path(path).write_bytes, audio)
        return path, note, None
    text = plain(text)                   # `say` would read a cue aloud
    if shutil.which("say"):
        path = out_base + ".aiff"
        proc = await asyncio.create_subprocess_exec(
            "say", "-v", PREVIEW_VOICE, "-r", str(round(175 * voice_speed())), "-o", path,
            text,
            stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
        if await proc.wait() == 0 and os.path.isfile(path):
            return path, "PLACEHOLDER: the system voice, not Fish", None
    path = out_base + ".wav"
    seconds = max(2.0, len(text.split()) / (2.5 * voice_speed()))
    from ..content import ffmpeg
    await ffmpeg.run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-f", "lavfi",
                      "-i", "anullsrc=r=44100:cl=stereo", "-t", f"{seconds:.2f}", path])
    return path, "PLACEHOLDER: silence timed to the words", None


# What sounds under the sting before the mix: nothing (2026-10-17).
STING_SILENCE = "anullsrc=r=44100:cl=stereo"


def segment_argv(png: str, audio: str, seconds: float, out: str, *,
                 prev: str | None = None,
                 bg: str | None = None, bg_offset: float = 0.0,
                 lead: str | None = None, lead_png: str | None = None,
                 lead_seconds: float = design.STING_SECONDS,
                 prev_bg: PrevBg | None = None,
                 demo: tuple[str, float, float, float, demos.Step] | None = None,
                 watermark: str | None = None,
                 enters: tuple[motion.Enter, ...] | list[motion.Enter] = (),
                 trace: motion.Trace | None = None,
                 focus: bool = False) -> list[str]:
    """One slide held for `seconds` over its narration, padded with silence,
    laid over the series' 3D loop `bg` (a solid ink field without one).

    The slide is a transparent PNG decoded once and held (`eof_action=
    repeat`); the loop starts `bg_offset` seconds in, so the 3D runs on
    unbroken from the segment before. `prev` (the step before's slide) fades
    out over the first TRANSITION seconds (2026-10-15: a crossfade at every
    step, a new chapter too; nothing wipes). `lead` (the 3D sting) plays first,
    under `lead_png` (the AI label, a preview's banner), over silence: the
    bed is the sting's only sound (rule 22; the pink-noise swell was removed
    2026-10-17, only the line moves in the sting).
    One CPU: no per-frame scaling or zoom but one crop+scale per demo
    frame, only two overlays and the encode;
    every segment is encoded identically, so the concat needs no re-encode.

    2026-10-11: `prev_bg` ("loop", path, offset) or ("demo", path, at, step) is the
    picture the segment before stood on, when it was another one: it fades
    out over XFADE seconds, so a change of topic crossfades. `demo` (path,
    start, source seconds, speed) puts a live-demo recording on the ink
    field at its box instead of a loop: that window of the file, sped up
    `speed` times, its last frame held to the end (tutorial_demos.fit).

    2026-10-13 (rule 18): `demo`'s fifth element is the step; the recording
    is cut straight to `step.box` (recording pixels) and scaled to fill the
    window (tutorial_demos.crop_filter), and a demo `prev_bg` must carry its
    step too, so the fade out of it shows what was on screen, never the whole
    page (ValueError without it).

    `watermark` (the ATLAS Academy mark, its alpha baked in) is laid at
    design.WATERMARK_XY over the finished body, after the transition, so it
    stays put while slides wipe; never over the `lead` sting. One more
    still decoded once and one overlay: nothing per frame but the blend.

    2026-10-15 (the motion guide, tutorial_motion): `png` is then the slide
    without what enters, and each of `enters` (an element cut from the same
    page at its own size) fades in and rises into place at its time, looped
    only for its few frames and then held; `trace` is the workflow dot, one
    36 px still whose x eases along a connector. `focus` pushes a demo step
    in from 115% of its box over FOCUS_FRAMES frames (zoompan on those
    frames only, the plain crop after) when the region shown moves. All of
    it sits under the watermark."""
    from .tutorial_demos import DEMO_H, DEMO_W, HOLE_H, HOLE_W, HOLE_X, HOLE_Y
    ink = f"color=c={design.INK}:s={design.W}x{design.H}:r={FPS}"
    argv = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i", png, "-i", audio]
    graph: list[str] = []
    n = 2
    if demo is not None:
        path, start, used, speed, step = demo
        crop = demos.crop_filter(step, HOLE_W, HOLE_H)
        argv += ["-ss", f"{start:.3f}", "-t", f"{used:.3f}", "-i", path, "-f", "lavfi", "-i", ink]
        f = motion.FOCUS_FRAMES
        if focus and used / speed * FPS > f + 1:
            push = motion.focus_filter(step.box, DEMO_W, DEMO_H, HOLE_W, HOLE_H)
            graph += [f"[{n}:v]setpts=(PTS-STARTPTS)/{speed:.4f},fps={FPS},split[da][db]",
                      f"[da]trim=end_frame={f},setpts=PTS-STARTPTS,{push},setsar=1[dz]",
                      f"[db]trim=start_frame={f},setpts=PTS-STARTPTS,{crop},setsar=1[dc]",
                      (f"[dz][dc]concat=n=2:v=1:a=0,"
                       f"tpad=stop_mode=clone:stop_duration={seconds:.3f},setsar=1,"
                       "format=yuv420p[dv]")]
        else:
            graph.append(f"[{n}:v]setpts=(PTS-STARTPTS)/{speed:.4f},{crop},fps={FPS},"
                         f"tpad=stop_mode=clone:stop_duration={seconds:.3f},setsar=1,"
                         "format=yuv420p[dv]")
        graph += [f"[{n + 1}:v]format=yuv420p[ink]",
                  f"[ink][dv]overlay={HOLE_X}:{HOLE_Y}:eof_action=repeat:format=yuv420[bg0]"]
        n += 2
    else:
        if bg is not None:
            argv += ["-stream_loop", "-1", "-ss", f"{bg_offset % design.LOOP_SECONDS:.3f}",
                     "-i", bg]
        else:
            argv += ["-f", "lavfi", "-i", ink]
        graph.append(f"[{n}:v]fps={FPS},scale={design.W}:{design.H},setsar=1,"
                     "setpts=PTS-STARTPTS,format=yuv420p[bg0]")
        n += 1
    if prev_bg is not None and seconds > XFADE * 2:
        kind, path, at = prev_bg[:3]
        fade = f"format=yuva420p,fade=t=out:st=0:d={XFADE}:alpha=1[pb]"
        if kind == "demo":
            argv += ["-ss", f"{max(0.0, at):.3f}", "-i", path,
                     "-f", "lavfi", "-t", f"{XFADE:.3f}", "-i", ink]
            if len(prev_bg) < 4 or not isinstance(prev_bg[3], demos.Step):
                raise ValueError("a demo prev_bg needs its step: the fade would show "
                                 "the whole, uncropped page")
            cut = f"{demos.crop_filter(prev_bg[3], HOLE_W, HOLE_H)},"
            graph += [(f"[{n}:v]trim=end_frame=1,setpts=PTS-STARTPTS,{cut}fps={FPS},"
                       f"tpad=stop_mode=clone:stop_duration={XFADE},setsar=1,format=yuv420p[pd]"),
                      f"[{n + 1}:v]format=yuv420p[pk]",
                      f"[pk][pd]overlay={HOLE_X}:{HOLE_Y}:eof_action=repeat:format=yuv420,{fade}"]
            n += 2
        else:
            argv += ["-stream_loop", "-1", "-ss", f"{at % design.LOOP_SECONDS:.3f}",
                     "-t", f"{XFADE:.3f}", "-i", path]
            graph.append(f"[{n}:v]fps={FPS},scale={design.W}:{design.H},setsar=1,"
                         f"setpts=PTS-STARTPTS,{fade}")
            n += 1
        graph.append("[bg0][pb]overlay=0:0:eof_action=pass:format=yuv420[bg]")
    else:
        graph.append("[bg0]null[bg]")
    graph += ["[0:v]format=yuva420p[s]",
              "[bg][s]overlay=0:0:eof_action=repeat:format=yuv420[x]"]
    if prev is not None and seconds > TRANSITION * 2:
        argv += ["-loop", "1", "-framerate", str(FPS), "-t", f"{TRANSITION:.3f}", "-i", prev]
        graph.append(f"[{n}:v]format=yuva420p,fade=t=out:st=0:d={TRANSITION}:alpha=1[p]")
        graph.append("[x][p]overlay=0:0:eof_action=pass:format=yuv420[y0]")
        n += 1
    else:
        graph.append("[x]null[y0]")
    cur = "y0"
    for i, e in enumerate(enters):
        # its frames only: faded in from its start, then the last one held;
        # one that leaves (e.out) fades and rises out, then is gone
        d = motion.sec(e.frames)
        last = e.end if e.gone is None else e.gone
        argv += ["-loop", "1", "-framerate", str(FPS), "-t", f"{last + 1 / FPS:.3f}",
                 "-i", e.png]
        fade_out = (f",fade=t=out:st={e.out:g}:d={d:g}:alpha=1" if e.out is not None
                    else "")
        after = "pass" if e.out is not None else "repeat"
        y = motion.rise_y(e.y, e.start, d, e.px, e.out)
        graph += [(f"[{n}:v]format=yuva420p,fade=t=in:st={e.start:g}:d={d:g}:alpha=1"
                   f"{fade_out}[e{i}]"),
                  (f"[{cur}][e{i}]overlay=x={e.x}:y='{y}'"
                   f":eof_action={after}:format=yuv420[m{i}]")]
        cur = f"m{i}"
        n += 1
    if trace is not None:
        argv += ["-i", trace.png]
        d = motion.sec(motion.TRACE_FRAMES * trace.hops)
        x = f"{trace.x0}+{trace.x1 - trace.x0}*{motion.ease(trace.start, d)}"
        graph += [f"[{n}:v]format=yuva420p[td]",
                  f"[{cur}][td]overlay=x='{x}':y={trace.y}:eof_action=repeat:format=yuv420[mt]"]
        cur = "mt"
        n += 1
    graph.append(f"[{cur}]null[y]")
    if watermark is not None:
        argv += ["-i", watermark]
        wx, wy = design.WATERMARK_XY
        graph += [f"[{n}:v]format=yuva420p[wm]",
                  f"[y][wm]overlay={wx}:{wy}:eof_action=repeat:format=yuv420[yw]"]
        n += 1
    else:
        graph.append("[y]null[yw]")
    graph.append(f"[yw]format=yuv420p,settb=1/{FPS},setsar=1[body]")
    graph.append("[1:a]aresample=44100,aformat=channel_layouts=stereo,apad,"
                 f"atrim=0:{seconds:.3f},asetpts=PTS-STARTPTS[a]")
    total = seconds
    if lead is not None:
        # Rule 17 (2026-10-13): sting.mp4 is the 4 s mark assembling; play
        # its LAST lead_seconds, so the mark is whole, never half-built.
        argv += ["-sseof", f"-{lead_seconds:.1f}", "-i", lead]
        lead_v = f"[{n}:v]fps={FPS},scale={design.W}:{design.H},setsar=1,format=yuv420p[l0]"
        n += 1
        if lead_png is not None:
            argv += ["-i", lead_png]
            graph += [lead_v, f"[{n}:v]format=yuva420p[lt]",
                      "[l0][lt]overlay=0:0:eof_action=repeat:format=yuv420[l1]"]
            n += 1
        else:
            graph += [lead_v, "[l0]null[l1]"]
        graph.append(f"[l1]trim=0:{lead_seconds:.3f},setpts=PTS-STARTPTS,format=yuv420p,"
                     f"settb=1/{FPS},setsar=1[lv]")
        # silence under the sting (2026-10-17): the swell of pink noise went;
        # the music bed is mixed under this span after the join (rule 22).
        # An intro cached with the old swell is keyed on DESIGN_VERSION 7
        # (cache_key), so the combined bump drew it again.
        argv += ["-f", "lavfi", "-t", f"{lead_seconds:.3f}", "-i", STING_SILENCE]
        graph.append(f"[{n}:a]aformat=sample_rates=44100:channel_layouts=stereo[la]")
        graph.append("[lv][la][body][a]concat=n=2:v=1:a=1[v][ao]")
        total += lead_seconds
    else:
        graph.append("[body]null[v]")
        graph.append("[a]anull[ao]")
    return argv + ["-filter_complex", ";".join(graph),
                   "-map", "[v]", "-map", "[ao]", "-t", f"{total:.3f}",
                   "-c:v", "libx264", "-preset", "veryfast", "-crf", "21", "-g", str(FPS * 4),
                   "-pix_fmt", "yuv420p", "-r", str(FPS),
                   "-c:a", "aac", "-b:a", "160k", "-ar", "44100", "-ac", "2", out]


def silence_argv(seconds: float, out: str, beep: bool) -> list[str]:
    """Silence for a quiz's countdown second or a pause card's hold; a soft
    tick at the start of a countdown second."""
    argv = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-f", "lavfi",
            "-t", f"{seconds:.3f}", "-i", "anullsrc=r=44100:cl=stereo"]
    if beep:
        argv += ["-f", "lavfi", "-i", "sine=frequency=880:duration=0.09",
                 "-filter_complex",
                 "[1:a]volume=0.5[b];[0:a][b]amix=inputs=2:duration=first[a]",
                 "-map", "[a]"]
    return [*argv, out]


def concat_listing(parts: list[str]) -> str:
    """The concat demuxer's list, every path ABSOLUTE. ffmpeg resolves a
    relative entry against the list file's own folder (a temp dir), not
    the working directory -- so the cached intro, kept under the relative
    ACADEMY_TUTORIALS_DIR, was "No such file" and both first drafts failed
    on the server (2026-10-10)."""
    return "".join(f"file '{os.path.abspath(p)}'\n" for p in parts)


def concat_argv(listing: str, out: str) -> list[str]:
    return ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-f", "concat",
            "-safe", "0", "-i", listing, "-c", "copy", "-movflags", "+faststart", out]


class Shooter:
    """One Chromium for every still of a render."""

    def __init__(self) -> None:
        self._p = self._b = None
        self.pages = video_qa.Pages()    # batch 9: every frame page, measured

    async def __aenter__(self) -> Shooter:  # noqa: PYI034
        from playwright.async_api import async_playwright
        self._p = await async_playwright().start()
        self._b = await self._p.chromium.launch(headless=True)
        return self

    async def __aexit__(self, *exc: object) -> None:
        if self._b is not None:
            await self._b.close()
        if self._p is not None:
            await self._p.stop()

    async def shoot(self, doc: str, path: str, w: int = design.W, h: int = design.H) -> str:
        pg = await self._b.new_page(viewport={"width": w, "height": h})
        try:
            await pg.set_content(doc)
            await pg.evaluate("document.fonts.ready.then(() => null)")
            await self.pages.measure(pg, w, h)
            # A slide is a transparent layer over the 3D loop; an opaque page
            # (the thumbnail) shoots the same with or without this.
            await pg.screenshot(path=path, omit_background=True)
        finally:
            await pg.close()
        return path

    async def token(self, label: str, path: str) -> tuple[int, int]:
        """WorkflowTrace's token (tutorial_design.trace_token) cut to its own
        box at `path`: (width, height) on even pixels."""
        pg = await self._b.new_page(viewport={"width": design.W, "height": 200})
        try:
            await pg.set_content(design.trace_token(label))
            await pg.evaluate("document.fonts.ready.then(() => null)")
            r = await pg.evaluate(
                "() => { const r = document.querySelector('.tk').getBoundingClientRect();"
                " return [r.width, r.height]; }")
            w, h = (int(math.ceil(v / 2) * 2) for v in r)
            await pg.screenshot(path=path, omit_background=True,
                                clip={"x": 0, "y": 0, "width": w, "height": h})
        finally:
            await pg.close()
        return w, h

    async def layers(self, doc: str, base: str, spec: motion.Spec, *, crossfade: bool
                     ) -> tuple[str, list[motion.Enter], motion.Trace | None]:
        """The slide cut for its motion (tutorial_motion, 2026-10-15): the
        whole slide at `base`.png (what the next step fades out of), the
        slide without what enters at `base`.rest.png, each entering group
        alone, cut to its box, and the trace dot. (rest png, enters, trace);
        a group that matches nothing on the page is left out."""
        plan = {what: (start, frames) for what, start, frames
                in motion.schedule(spec, crossfade=crossfade)}
        pg = await self._b.new_page(viewport={"width": design.W, "height": design.H})
        try:
            await pg.set_content(doc)
            await pg.evaluate("document.fonts.ready.then(() => null)")
            await self.pages.measure(pg, design.W, design.H)
            full = base + ".png"
            # What leaves (a lower third, a checkpoint tag) is gone by the
            # segment's end, so the slide the next step fades from has none.
            gone = (await pg.add_style_tag(
                content=f"{','.join(spec.exits)}{{visibility:hidden!important}}")
                if spec.exits else None)
            await pg.screenshot(path=full, omit_background=True)
            if gone is not None:
                await gone.evaluate("e => e.remove()")
            boxes = await pg.evaluate(_BOXES_JS, [sel for sel, _ in spec.groups])
            groups = [(sel, check, box) for (sel, check), box
                      in zip(spec.groups, boxes, strict=True) if box]
            enters: list[motion.Enter] = []
            if groups:
                hide = ",".join(sel for sel, _, _ in groups)
                tag = await pg.add_style_tag(
                    content=f"{hide}{{visibility:hidden!important}}")
                rest = base + ".rest.png"
                await pg.screenshot(path=rest, omit_background=True)
                await tag.evaluate("e => e.remove()")
                for k, (sel, check, (x, y, w, h)) in enumerate(groups):
                    alone = ",".join(f"{p},{p} *" for p in sel.split(","))
                    tag = await pg.add_style_tag(
                        content=("body *{visibility:hidden!important}"
                                 f"{alone}{{visibility:visible!important}}"))
                    png = f"{base}.e{k}.png"
                    await pg.screenshot(path=png, omit_background=True,
                                        clip={"x": x, "y": y, "width": w, "height": h})
                    await tag.evaluate("e => e.remove()")
                    start, frames = plan[sel]
                    out = (round(start + motion.sec(frames + motion.HOLD_FRAMES), 3)
                           if sel in spec.exits else None)
                    enters.append(motion.Enter(png, x, y, start, frames,
                                               0 if check else motion.RISE_PX, out))
            else:
                rest = full
            trace = None
            if spec.trace is not None and "trace" in plan:
                nodes = await pg.evaluate(_NODES_JS)
                a, b = spec.trace
                if 0 <= a < len(nodes) and 0 <= b < len(nodes) and a != b:
                    if spec.token:
                        # WorkflowTrace: the labelled value-tag rides the
                        # track instead of the dot, the same ease and stop
                        dot = base + ".token.png"
                        tw, th = await self.token(spec.token, dot)
                    else:
                        dot = os.path.join(os.path.dirname(base), "trace-dot.png")
                        tw = th = 36
                        if not os.path.isfile(dot):
                            await self.shoot(design.trace_dot(), dot, 36, 36)
                    y = max(n[3] for n in nodes) + TRACE_BELOW - th // 2
                    trace = motion.Trace(dot, round(nodes[a][0] + nodes[a][2] / 2) - tw // 2,
                                         round(nodes[b][0] + nodes[b][2] / 2) - tw // 2,
                                         round(y), plan["trace"][0], hops=abs(b - a))
        finally:
            await pg.close()
        return rest, enters, trace


# Where the trace runs: the track's centre this far below the nodes'
# bottom (tutorial_design's .flow::after, top:calc(100% + 26px), 2 px).
TRACE_BELOW = 27
# Each group's box on the page, padded for its shadow, on even pixels and
# inside the frame (null when nothing visible matches).
_BOXES_JS = """(sels) => sels.map(sel => {
  const els = [...document.querySelectorAll(sel)].filter(
      e => getComputedStyle(e).visibility !== 'hidden');
  if (!els.length) return null;
  let x0 = 1e9, y0 = 1e9, x1 = -1e9, y1 = -1e9;
  for (const e of els) { const r = e.getBoundingClientRect();
    x0 = Math.min(x0, r.left); y0 = Math.min(y0, r.top);
    x1 = Math.max(x1, r.right); y1 = Math.max(y1, r.bottom); }
  const pad = 40, W = innerWidth, H = innerHeight;
  const ax = Math.max(0, Math.floor((x0 - pad) / 2) * 2);
  const ay = Math.max(0, Math.floor((y0 - pad) / 2) * 2);
  const bx = Math.min(W, Math.ceil((x1 + pad) / 2) * 2);
  const by = Math.min(H, Math.ceil((y1 + pad + 24) / 2) * 2);
  return [ax, ay, bx - ax, by - ay];
})"""
_NODES_JS = """() => [...document.querySelectorAll('.flow .node')].map(e => {
  const r = e.getBoundingClientRect(); return [r.left, r.top, r.width, r.bottom]; })"""


async def _segment(shooter: Shooter, doc: str, said: str, base: str, preview: bool, *,
                   prev: str | None = None, spec: motion.Spec = motion.STILL,
                   focus: bool = False,
                   silent: float = 0.0, beep: bool = False,
                   bg: str | None = None, bg_offset: float = 0.0,
                   lead: str | None = None, tail: float = 0.0,
                   prev_bg: PrevBg | None = None,
                   demo: tuple[str, float, float, demos.Step] | None = None,
                   watermark: bool = True, hold_to: float = 0.0
                   ) -> tuple[str, float, str, str | None]:
    """(segment mp4, seconds, voice note, None) or ("", 0, "", why). With
    `silent` > 0 the still is held that long over silence (a countdown
    second, a pause card's hold) instead of narration. `bg` is the 3D loop
    under the slide, entered `bg_offset` seconds in; `lead` a clip (the 3D
    sting) played first, under the AI label. `tail` is extra silence after
    the narration (SCENE_GAP at a scene's end). `prev_bg` crossfades from the
    picture before; `demo` (path, start, length, step) is a live-demo step's
    window of its recording, fitted to the narration (tutorial_demos.fit) and
    cropped to the step's box. `hold_to` holds the slide at least that many
    seconds (the end card's 15 s), its narration padded with silence.

    `spec` (tutorial_design.motion_of) is what moves on the slide; it moves
    only when the segment is long enough to settle and then hold
    (tutorial_motion.fits). The whole slide is always written to
    `base`.png: the next step fades out of it."""
    from ..content import ffmpeg
    if silent > 0:
        audio, note = base + ".wav", ""
        await ffmpeg.run(silence_argv(silent, audio, beep))
        seconds = silent
    else:
        audio, note, why = await _narrate(said, base + ".voice", preview)
        if why is not None:
            return "", 0.0, "", why
        # Whole frames, and the clock reads the encoded file: chapters match
        # the render, not the request.
        seconds = math.ceil((await ffmpeg.duration(audio) + PAD + tail) * FPS) / FPS
    seconds = max(seconds, math.ceil(hold_to * FPS) / FPS)
    crossfade = prev is not None and seconds > TRANSITION * 2
    moves = bool(spec) and motion.fits(spec, seconds, crossfade=crossfade)
    if moves:
        png, enters, trace = await shooter.layers(doc, base, spec, crossfade=crossfade)
    else:
        png, enters, trace = await shooter.shoot(doc, base + ".png"), [], None
    lead_png = (await shooter.shoot(design.sting_layer(preview), base + ".lead.png")
                if lead is not None else None)
    out = base + ".mp4"
    window = None
    if demo is not None:
        from .tutorial_demos import fit
        speed, used = fit(demo[2], seconds)
        window = (demo[0], demo[1], used, speed, demo[3])
    await ffmpeg.run(segment_argv(png, audio, seconds, out, prev=prev,
                                  bg=bg, bg_offset=bg_offset, lead=lead, lead_png=lead_png,
                                  prev_bg=prev_bg, demo=window,
                                  watermark=str(design.WATERMARK) if watermark else None,
                                  enters=enters, trace=trace, focus=focus),
                     timeout=900)
    return out, await ffmpeg.duration(out), note, None


def cache_key(kind: str, said: str, preview: bool, shown: str = "") -> str:
    """The cached intro/outro is the same clip only while its words, what it
    shows, the voice, its speed and the look are the same."""
    voice = "preview" if preview else (os.getenv("REELS_FISH_VOICE_ID", "").strip()
                                      or os.getenv("FISH_AUDIO_VOICE_ID", "").strip()
                                      or "fish-default") + f"|{fish_model()}"
    # 2026-10-17: the welcome's intro no longer opens on the 3D sting, so an
    # intro cached with it (keyed "sting=1") is never reused.
    sting = "|sting=none" if kind == "intro" else ""
    raw = (f"{kind}|{said}|{voice}|{design.DESIGN_VERSION}|{design.kit_stamp()}"
           f"|speed={voice_speed():g}" + (f"|shown={shown}" if shown else "") + sting)
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


async def fixed_clip(kind: str, shooter: Shooter, preview: bool = False
                     ) -> tuple[str, float, str | None]:
    """The channel welcome's intro (INTRO_SAID over the intro card):
    (path, seconds, None), rendered once and then read from the cache. A
    lesson opens on its result instead (opening_part); every video ends on
    its own end card (2026-10-17), so "intro" is the only kind."""
    from ..content import ffmpeg
    if kind != "intro":
        return "", 0.0, f"no fixed {kind!r} clip: every video ends on its end card"
    said = opener_voiced(WELCOME)
    cache = root() / "cache"
    path = cache / f"{kind}-{cache_key(kind, said, preview)}.mp4"
    if path.is_file():
        return str(path), await ffmpeg.duration(str(path)), None
    await asyncio.to_thread(cache.mkdir, parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=f"tutorial-{kind}-") as work:
        # The words over the mark idling (no sting first since 2026-10-17);
        # no watermark: the intro shows the mark full size.
        seg, seconds, _note, why = await _segment(
            shooter, design.intro(preview), said, os.path.join(work, kind), preview,
            watermark=False, spec=design.INTRO_MOTION, bg=str(design.INTRO_LOOP))
        if why is not None:
            return "", 0.0, why
        tmp = str(path) + ".part"
        await asyncio.to_thread(shutil.copyfile, seg, tmp)
        await asyncio.to_thread(os.replace, tmp, path)
    return str(path), seconds, None


# ------------------------------------------------------------ the opening
# Miro, 2026-10-17 (docs/academy-video-system/episode-structure.md, "Opening
# (0-20 s)"): three parts, their edges fixed in code, never by a take's
# length. The result (ResultTitle) for exactly RESULT_SECONDS, the badge
# rising at frame 12 and one trace into the result at 3 s, under "Here is
# the result: ..."; the BrandSting, exactly 1 s, silent but for the music
# bed (rule 22); then the scope line over the same result.

def sting_argv(frames: str, out: str) -> list[str]:
    """The sting's 30 drawn frames (`frames`, an image2 pattern) as one part,
    over silence, encoded exactly as segment_argv encodes (the concat stays
    a copy)."""
    secs = f"{design.STING_SECONDS:.3f}"
    return ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
            "-framerate", str(FPS), "-i", frames,
            "-f", "lavfi", "-t", secs, "-i", STING_SILENCE,
            "-map", "0:v", "-map", "1:a", "-t", secs,
            "-vf", f"format=yuv420p,settb=1/{FPS},setsar=1",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "21", "-g", str(FPS * 4),
            "-pix_fmt", "yuv420p", "-r", str(FPS),
            "-c:a", "aac", "-b:a", "160k", "-ar", "44100", "-ac", "2", out]


def sting_key(preview: bool) -> str:
    """The cached sting is the same file while its frames are."""
    raw = "|".join(design.sting_frame(f, preview) for f in range(design.STING_FRAMES))
    return hashlib.sha256(f"{raw}|{FPS}".encode()).hexdigest()[:16]


async def sting_clip(shooter: Shooter, preview: bool = False
                     ) -> tuple[str, str | None]:
    """The BrandSting, drawn once frame by frame and then read from the
    cache: (path, None) or ("", why)."""
    from ..content import ffmpeg
    cache = root() / "cache"
    path = cache / f"sting-{sting_key(preview)}.mp4"
    if path.is_file():
        return str(path), None
    await asyncio.to_thread(cache.mkdir, parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="tutorial-sting-") as work:
        for f in range(design.STING_FRAMES):
            await shooter.shoot(design.sting_frame(f, preview),
                                os.path.join(work, f"f{f:02d}.png"))
        out = os.path.join(work, "sting.mp4")
        try:
            await ffmpeg.run(sting_argv(os.path.join(work, "f%02d.png"), out), timeout=300)
        except ffmpeg.FfmpegError as exc:
            return "", f"ffmpeg: {exc}"
        tmp = str(path) + ".part"
        await asyncio.to_thread(shutil.copyfile, out, tmp)
        await asyncio.to_thread(os.replace, tmp, path)
    return str(path), None


@dataclass
class Opening:
    """A lesson's opening as rendered: its parts in order (the result, the
    sting, the scope line), their seconds, and each narrated line timed."""
    parts: list[str]
    seconds: float
    timings: list[tuple[float, float, str]]
    note: str = ""
    last_png: str = ""               # the scope line's still: the next step fades from it


async def opening_part(ep: Episode, shooter: Shooter, work: str, preview: bool = False
                       ) -> tuple[Opening | None, str | None]:
    """The result, the sting and the scope line, rendered into `work`."""
    from ..content import ffmpeg
    said_result, said_scope = opening_lines(ep)
    base = os.path.join(work, "intro-result")
    audio, note, why = await _narrate(opener_voiced(ep), base + ".voice", preview)
    if why is not None:
        return None, f"the result line: {why}"
    spoken = await ffmpeg.duration(audio)
    if spoken + PAD > design.RESULT_SECONDS:
        return None, (f"the result line runs {spoken:.1f} s; with its breath it must end "
                      f"before the sting at {design.STING_AT:g} s")
    png, enters, trace = await shooter.layers(design.result_title(ep, preview), base,
                                              design.result_motion(ep), crossfade=False)
    # The badge at frame 12, the trace at 3 s: the spec's times, not the
    # motion schedule's (which runs one thing after another from frame 2).
    enters = [replace(e, start=motion.sec(design.BADGE_FRAME)) for e in enters]
    trace = replace(trace, start=design.TRACE_AT) if trace is not None else None
    result = base + ".mp4"
    try:
        await ffmpeg.run(segment_argv(png, audio, design.RESULT_SECONDS, result,
                                      enters=enters, trace=trace), timeout=900)
    except ffmpeg.FfmpegError as exc:
        return None, f"the result: ffmpeg: {exc}"
    sting, why = await sting_clip(shooter, preview)
    if why is not None:
        return None, f"the sting: {why}"
    scope_base = os.path.join(work, "scope")
    seg, secs, note2, why = await _segment(shooter, design.scope_card(ep, preview),
                                           said_scope, scope_base, preview,
                                           spec=design.SCOPE_MOTION, watermark=False)
    if why is not None:
        return None, f"the scope line: {why}"
    after = design.STING_AT + design.STING_SECONDS
    return Opening(parts=[result, sting, seg], seconds=round(after + secs, 3),
                   timings=[(0.0, design.RESULT_SECONDS, said_result),
                            (after, round(after + secs, 3), said_scope)],
                   note="; ".join(x for x in (note, note2) if x),
                   last_png=scope_base + ".png"), None


@dataclass
class Beat:
    """One segment of an episode's lesson, planned before anything is drawn."""
    scene: Scene
    step: int
    said: str = ""                   # the words, cues taken out: shown, counted, timed
    voiced: str = ""                 # the line as written, cues in: for the voice
    countdown: int | None = None
    hold: bool = False
    silent: float = 0.0
    chapter: int = 0
    first_of_scene: bool = False
    last_of_scene: bool = False
    check: int = -1                  # a CheckpointTag beat: its index in scene.checks


# CheckpointTag (docs/academy-video-system narration.md: "After every
# CheckpointTag, stop talking for 2-3 seconds"): the step's picture held
# silent while the tag rises in (title-rise), is read (hold-tag) and rises
# out -- the narration's pause, two frames in.
CHECK_SECONDS = motion.sec(2 + 2 * motion.RISE_FRAMES + motion.HOLD_FRAMES)


def beats(scenes: tuple[Scene, ...]) -> list[Beat]:
    """Every segment of `scenes`, in order: each narrated step, a quiz's
    silent 3-2-1 before its answer, a checkpoint's silent hold after its
    step, a pause card's silent hold."""
    out: list[Beat] = []
    for sc, ch in zip(scenes, chapters_of_scenes(scenes), strict=True):
        checks = {step: i for i, (step, _look) in enumerate(sc.checks)}
        for k, said in enumerate(sc.said):
            if sc.kind == "quiz" and k == 1 and sc.answer >= 0:
                for n in range(COUNTDOWN, 0, -1):
                    out.append(Beat(sc, 0, countdown=n, silent=1.0, chapter=ch))
            last = k == len(sc.said) - 1 and sc.kind != "pause"
            out.append(Beat(sc, k, said=plain(said), voiced=said, chapter=ch,
                            first_of_scene=k == 0, last_of_scene=last))
            if k in checks:
                out.append(Beat(sc, k, silent=CHECK_SECONDS, chapter=ch, check=checks[k]))
        if sc.kind == "pause":
            out.append(Beat(sc, len(sc.items) - 1, hold=True, silent=PAUSE_HOLD, chapter=ch))
    return out


def card_line(series_key: str, ep: Episode, lesson: list[Beat]
              ) -> tuple[list[Beat], str]:
    """(the lesson's beats, the words said over the end card). The recap
    closes on the next lesson (rule 20, next_line); that closing sentence
    moves onto the EndCard (2026-10-17), so the card appears as it is said
    and the narration finishes its sentence over it. "" when the lesson does
    not close on it (the welcome): the card is silent but for the bed."""
    if not lesson or lesson[-1].silent:
        return lesson, ""
    try:
        line = next_line(series_key, ep)
    except ValueError:
        return lesson, ""
    last = lesson[-1]
    voiced = last.voiced.rstrip()
    if not voiced.endswith(line):
        return lesson, ""
    before = voiced[:-len(line)].rstrip()
    if not plain(before):
        return lesson[:-1], line
    return [*lesson[:-1], replace(last, said=plain(before), voiced=before)], line


def chapters_of_scenes(scenes: tuple[Scene, ...]) -> list[int]:
    out, n = [], -1
    for sc in scenes:
        if sc.chapter or n < 0:
            n += 1
        out.append(n)
    return out


async def render(series_key: str, episode: int, *, preview: bool = False
                 ) -> tuple[Path | None, Manifest | None, str | None]:
    """Render one episode into its directory: video, thumbnail, manifest.
    A lesson: the result, the sting at 6-7 s and the scope line (Miro,
    2026-10-17), the lesson, the ending. The channel welcome: its narrated
    intro, its topic line, the lesson, the ending."""
    from ..content import ffmpeg
    from .reel_design import available
    got = resolve(series_key, episode)
    if isinstance(got, str):
        return None, None, got
    s, ep = got
    if not ep.scripted:
        return None, None, f"script not written for {s.key} #{ep.number} \"{ep.title}\""
    if (why := held(s.key, ep)) is not None:
        return None, None, why
    if s.key not in design.KIT_SERIES:
        return None, None, (f"the 3D kit has no loop or still for the {s.key} series yet "
                            "(tools/tutorial3d/build.py)")
    if (why := ffmpeg.missing()) is not None:
        return None, None, why
    if not available():
        return None, None, "Playwright/Chromium is not installed; the slides cannot be drawn"
    if (why := design.kit_missing()) is not None:
        return None, None, why
    if (why := demos.missing(demo_keys(ep))) is not None:
        return None, None, why
    lesson_open = bool(ep.opener_topic)
    if lesson_open and ep.payoff is None:
        return None, None, f"{s.key} #{ep.number} has no payoff: nothing to open on"
    # 2026-10-11: the 3D follows the topic; a live demo shows its recording
    lead_topic = design.SERIES_TOPIC.get(s.key, "workflow")
    topic_of = dict(zip(map(id, ep.scenes),
                        design.backdrops(ep.scenes, s.key, before=lead_topic),
                        strict=True))
    backdrop = str(design.topic_path(lead_topic))
    used_topics: list[str] = []
    out_dir = episode_dir(s, ep)
    await asyncio.to_thread(out_dir.mkdir, parents=True, exist_ok=True)
    # A lesson's first chapter is its result (rule 17); the welcome's, Intro.
    chapters: list[tuple[float, str]] = [(0.0, PAYOFF_CHAPTER if lesson_open else "Intro")]
    # Every narrated part as (start, end, said): the caption track (rule 16).
    timings: list[tuple[float, float, str]] = []
    notes: set[str] = set()
    lesson, card_said = card_line(s.key, ep, beats(ep.scenes))
    first: list[Beat] = []
    total = max(1, len(lesson))
    n_chapters = (max((b.chapter for b in lesson), default=0) + 1) if lesson else 1
    with tempfile.TemporaryDirectory(prefix="tutorial-") as work:
        async with Shooter() as shooter:
            # 2026-10-17: the opening claims no minutes; the title carries
            # them, measured (rule 2).
            spoken = 0
            # Miro, 2026-10-17: his on-camera intro is no longer played (the
            # clip stays on disk, unused); a lesson opens on its result.
            parts: list[str] = []
            clock, t_intro = 0.0, 0.0
            prev: str | None = None
            if lesson_open:
                opened, why = await opening_part(ep, shooter, work, preview)
                if why is not None or opened is None:
                    return None, None, f"opening: {why}"
                parts += opened.parts
                timings += opened.timings
                t_intro = clock = opened.seconds
                notes.add(opened.note)
                prev = opened.last_png or None
                loop_at = None
            else:
                intro, t_intro, why = await fixed_clip("intro", shooter, preview)
                if why is not None:
                    return None, None, f"intro: {why}"
                parts.append(intro)
                timings.append((0.0, t_intro, opener_said(ep)))
                clock = t_intro
                loop_at = t_intro            # where the intro's 3D loop stands
            # Each topic's loop resumes where it was left, so the 3D never
            # jumps back at a cut; a change of topic crossfades.
            tclock: dict[str, float] = {}
            last: PrevBg | None = None
            last_topic = ""
            shown_box: tuple[int, int, int, int] | None = None
            if topic_line(ep):
                seg, secs, note, why = await _segment(
                    shooter, design.topic(ep, preview), topic_line(ep),
                    os.path.join(work, "topic"), preview, bg=backdrop, bg_offset=0.0,
                    spec=design.TOPIC_MOTION,
                    prev_bg=(("loop", str(design.INTRO_LOOP), loop_at)
                             if loop_at is not None else None))
                if why is not None:
                    return None, None, f"topic line: {why}"
                parts.append(seg)
                timings.append((clock, clock + secs, topic_line(ep)))
                clock += secs
                tclock[lead_topic] = secs
                last, last_topic = ("loop", backdrop, secs), lead_topic
            done = 0
            body_start = None
            shown_step = -1                  # the StepMap node last oriented on
            for group, plan in (("hook", first), ("lesson", lesson)):
                for i, b in enumerate(plan):
                    if group == "lesson" and body_start is None:
                        body_start = clock
                    # Rule 17: the payoff opens its own chapter (a Short is cut
                    # from it by name); the hook rides inside it, as it rode
                    # inside Intro before.
                    payoff = b.scene.kind == "payoff"
                    if ((group == "lesson" or payoff) and b.first_of_scene
                            and b.scene.chapter):
                        name = b.scene.chapter
                        if clock < CHAPTER_MIN_SECONDS and len(chapters) == 1:
                            chapters[0] = (0.0, name)     # too soon for its own chapter
                        else:
                            chapters.append((clock, name))
                    doc = design.slide(
                        b.scene, b.step, mark=s.mark, chapter=b.chapter if group == "lesson"
                        else 0, chapters=n_chapters, preview=preview,
                        progress=done / total, countdown=b.countdown, hold=b.hold,
                        steps_map=ep.steps_map, check=b.check)
                    topic = topic_of[id(b.scene)]
                    space: dict = {}
                    # 2026-10-15 (the motion guide): only what is new moves;
                    # a countdown second or a pause's hold is held still.
                    # The teaching components (batch 4) rise over it: the
                    # StepMap's node when the map moves on, a lower third, a
                    # checkpoint tag (design.beat_motion).
                    still = b.countdown is not None or b.hold
                    orient = (b.first_of_scene and b.scene.map_step >= 0
                              and b.scene.map_step != shown_step)
                    if b.scene.map_step >= 0:
                        shown_step = b.scene.map_step
                    space["spec"] = design.beat_motion(b.scene, b.step, orient=orient,
                                                       check=b.check, still=still)
                    if topic == "demo" and b.check >= 0:
                        # the checkpoint holds the step's last frame, silent
                        a, length = demos.window(b.scene.demo, b.step)
                        clip = str(demos.video_path(b.scene.demo))
                        step = demos.DEMOS[b.scene.demo].steps[b.step]
                        space["demo"] = (clip, a + length - 0.05, 0.05, step)
                        space["focus"] = False           # the same picture, held
                        shown_box = step.box
                        here = ("demo", clip, a + length - 0.05, step)
                    elif topic == "demo":
                        a, length = demos.window(b.scene.demo, b.step)
                        clip = str(demos.video_path(b.scene.demo))
                        step = demos.DEMOS[b.scene.demo].steps[b.step]
                        space["demo"] = (clip, a, length, step)
                        # push in when the region shown moves from one demo
                        # step to the next, never on a timer; coming in from a
                        # slide the crossfade is the one motion
                        space["focus"] = last_topic == "demo" and step.box != shown_box
                        shown_box = step.box
                        here = ("demo", clip, a + length - 0.05, step)
                    else:
                        shown_box = None
                        loop = str(design.topic_path(topic))
                        space["bg"] = loop
                        space["bg_offset"] = tclock.get(topic, 0.0) % design.LOOP_SECONDS
                        here = ("loop", loop, 0.0)
                    if last is not None and topic != last_topic:
                        space["prev_bg"] = last
                    # A software state is cut, never dissolved: a demo step
                    # after a demo step has no fade from the one before.
                    # ErrorRecovery: the error appears as a cut, never a fade.
                    # A checkpoint holds the same picture: nothing to dissolve,
                    # and its tag must rise, hold and leave inside CHECK_SECONDS.
                    cut = (b.countdown is not None or (topic == "demo" and last_topic == "demo")
                           or b.check >= 0
                           or (b.scene.error is not None and b.step == 0 and not b.silent))
                    seg, secs, note, why = await _segment(
                        shooter, doc, b.voiced or b.said,
                        os.path.join(work, f"{group}{i:03d}"),
                        preview, prev=None if cut else prev,
                        silent=b.silent, beep=b.countdown is not None,
                        tail=SCENE_GAP if b.last_of_scene else 0.0, **space)
                    if why is not None:
                        return None, None, f"{b.scene.chapter or b.scene.kind}: {why}"
                    parts.append(seg)
                    if b.said and not b.silent:
                        timings.append((clock, clock + secs, b.voiced or b.said))
                    clock += secs
                    notes.add(note)
                    if topic != "demo":
                        tclock[topic] = tclock.get(topic, 0.0) + secs
                        here = ("loop", here[1], tclock[topic])
                    last, last_topic = here, topic
                    used_topics.append(topic if topic != "demo" else f"demo:{b.scene.demo}")
                    prev = os.path.join(work, f"{group}{i:03d}.png")
                    done += 1
            body = clock - (body_start if body_start is not None else clock)
            if (why := claim_refusal(ep, body)) is not None:
                return None, None, why
            # The EndCard (Miro, 2026-10-17): its own part, the last one, at
            # least END_CARD_MIN seconds; no presenter clip, no goodbye.
            nxt = next_up(s.key, ep)
            card_doc = design.end_card(ep, nxt, shop=bool(downloads_of(ep)),
                                       preview=preview)
            outro, t_outro, note, why = await _segment(
                shooter, card_doc, card_said, os.path.join(work, "outro-card"), preview,
                spec=design.END_CARD_MOTION, watermark=False,
                silent=0.0 if card_said else design.END_CARD_MIN,
                hold_to=design.END_CARD_MIN)
            if why is not None:
                return None, None, f"end card: {why}"
            # The card sits inside the last chapter: alone it is about 15 s.
            parts.append(outro)
            notes.add(note)
            if card_said:
                timings.append((clock, clock + t_outro, card_said))
            clock += t_outro
            listing = os.path.join(work, "parts.txt")
            await asyncio.to_thread(Path(listing).write_text, concat_listing(parts))
            name = "video.PREVIEW.mp4" if preview else "video.mp4"
            final = out_dir / name
            dry = out_dir / f"{name}.dry.mp4"

            # Rule 22: the bed under the sting (6-7 s) and the end card only.
            sting = ((design.STING_AT, design.STING_AT + design.STING_SECONDS)
                     if lesson_open else None)

            def spans(seconds: float) -> list[tuple[float, float]]:
                return music_spans(seconds, card_seconds=t_outro, sting=sting)

            async def join() -> float:
                """Rule 22 (Miro, 2026-10-17): every join is mixed with the
                quiet ambient bed at once -- under the sting and the end card
                only, silence under the lesson -- and the MIXED file is measured.
                The mix copies the picture and keeps the parts' times, so
                the chapters and timings stand; but the AAC re-encode adds
                about 18 ms, which can roll a minute over, so the title's
                minutes are read from the file as it will be uploaded."""
                await ffmpeg.run(concat_argv(listing, str(dry)), timeout=900)
                try:
                    await tutorial_music.add_bed(str(dry), s.key, str(final),
                                                 spans=spans)
                finally:
                    await asyncio.to_thread(dry.unlink, missing_ok=True)
                return await ffmpeg.duration(str(final))

            measured = await join()
            # 2026-10-13 (rule 17): YouTube drops every chapter when one is
            # under 10 s: a first chapter that short gives 0:00 to the next.
            if len(chapters) > 1 and chapters[1][0] < CHAPTER_MIN_SECONDS:
                chapters = [(0.0, chapters[1][1]), *chapters[2:]]
            thumb_png = os.path.join(work, "thumb.png")
            # The plan's time badge ("5 MIN"), from the joined file's own
            # length: the same number the title and ready() read.
            badge = badge_text(minutes_of(measured)) if ep.title_hook else ""
            drawn = badge if badge and thumbnail_takes_badge() else ""
            # Batch 7: a 1280x720 PNG of at most 2 MB, over the episode's own
            # demo frame or payoff slide; no picture, no render.
            if (why := await thumbs.render(ep, shooter, thumb_png, level=ep.level,
                                           badge=drawn, work=work)) is not None:
                return None, None, f"thumbnail: {why}"
        # thumb.png is what YouTube gets; thumb.jpg stays for the review card.
        # The JPG first: thumb_file() trusts a PNG only if it is not older.
        await ffmpeg.run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i",
                          thumb_png, "-q:v", "3", str(out_dir / "thumb.jpg")], timeout=60)
        await asyncio.to_thread(shutil.copyfile, thumb_png, str(out_dir / "thumb.png"))
        # A drawn thumbnail: no longer NotebookLM's (notebooklm_thumbs.MARKER).
        await asyncio.to_thread((out_dir / "notebooklm-thumb.json").unlink,
                                missing_ok=True)
    m = Manifest(series=s.key, episode=ep.number,
                 audio="placeholder" if preview else "fish",
                 seconds=round(measured, 2), chapters=chapters,
                 voice_note="; ".join(sorted(x for x in notes if x)),
                 rendered_at=datetime.now(UTC).isoformat(timespec="seconds"),
                 parts=[os.path.basename(p) for p in parts],
                 intro_seconds=round(t_intro, 2),
                 outro_seconds=round(t_outro, 2),
                 body_seconds=round(body, 2), script=script_key(ep),
                 backdrop=os.path.basename(backdrop), backdrops=used_topics,
                 minutes=minutes_of(measured), badge=drawn,
                 opener=opener_said(ep), spoken_minutes=spoken,
                 timings=timings,
                 presenter=[],
                 music=tutorial_music.MUSIC_VERSION,
                 music_windows=spans(measured),
                 opening="result" if lesson_open else "welcome",
                 sting_at=design.STING_AT if lesson_open else 0.0,
                 sting_seconds=design.STING_SECONDS if lesson_open else 0.0,
                 card_at=round(measured - t_outro, 2), card_seconds=round(t_outro, 2),
                 next_title=nxt.title if nxt else "", next_kind=nxt.kind if nxt else "",
                 demo_hole=list(demos.HOLE),
                 **shooter.pages.record())
    manifest = out_dir / ("manifest.PREVIEW.json" if preview else "manifest.json")
    await asyncio.to_thread(manifest.write_text, json.dumps(asdict(m), indent=1))
    await asyncio.to_thread((out_dir / "description.txt").write_text,
                            description(s, ep, chapters, minutes_of(measured)))
    return out_dir, m, None


# ------------------------------------------------------------------ actions

async def render_tutorial(series: str, episode: int) -> ActionResult:
    """Tier 1: writes files under the tutorials directory, nothing leaves the box."""
    out, m, why = await render(series, int(episode))
    if why is not None or out is None or m is None:
        return ActionResult(False, f"render refused: {why}")
    return ActionResult(True, f"{series} episode {episode}: {m.seconds:.0f} s, "
                              f"{len(m.chapters)} chapters ({m.voice_note})",
                        evidence={"dir": str(out), "seconds": m.seconds},
                        rollback_token=str(out))


async def render_tutorial_dry(series: str, episode: int) -> ActionResult:
    got = resolve(series, episode)
    if isinstance(got, str):
        return ActionResult(False, got)
    s, ep = got
    if not ep.scripted:
        return ActionResult(False, f"script not written for {s.key} #{ep.number} "
                                   f"\"{ep.title}\"")
    if (why := held(s.key, ep)) is not None:
        return ActionResult(False, why)
    words = sum(len(plain(x).split()) for sc in ep.scenes for x in sc.said)
    steps = len(beats(ep.scenes))
    return ActionResult(True, f"would render {s.playlist or s.mark} #{ep.number} "
                              f"\"{ep.title}\": {steps} segments, {words} words narrated "
                              "in the Fish voice, between the cached intro and ending")


async def unrender_tutorial(token: str) -> ActionResult:
    """Drop the render this run wrote (our own file; the catalog stays)."""
    d = Path(token)
    if d.resolve().parent != (root() / "episodes").resolve():
        return ActionResult(False, "not a tutorial render directory; left alone")
    for name in ("video.mp4", "manifest.json", "thumb.png", "thumb.jpg", "description.txt"):
        try:
            await asyncio.to_thread(os.remove, d / name)
        except OSError:
            pass
    return ActionResult(True, f"render {d.name} withdrawn")


async def _probe(path: str) -> tuple[dict | None, str | None]:
    """{width, height, audio, seconds} and the encoding the batch 9 gate
    reads (video_qa.read_probe), or (None, why)."""
    return await video_qa.probe(path)


async def posted(series_key: str, episode: int) -> dict | None:
    """This episode's live row (uploaded, maybe not yet public), or None."""
    from ...kernel.state import fetch_one
    row = await fetch_one(
        """SELECT id, video_id, playlist_id, item_id, thumb_set, captions_set, public
           FROM academy_tutorial_posts
           WHERE series = $1 AND episode = $2 AND NOT withdrawn
           ORDER BY id DESC LIMIT 1""", series_key, episode)
    return dict(row) if row else None


async def posted_today() -> int:
    from ...kernel.state import fetch_one
    row = await fetch_one(
        "SELECT count(*) AS n FROM academy_tutorial_posts WHERE public AND at > $1",
        datetime.now(UTC) - timedelta(hours=24))
    return int(row["n"]) if row else 0


async def ready(series_key: str, episode: int
                ) -> tuple[tuple[Series, Episode, Path, Manifest, str] | None, str | None]:
    """Everything about the render a publish needs, checked: the file is a
    Fish-narrated render, 1920x1080 with sound, 4-10 minutes, the chapters
    and the description pass YouTube's rules and carry the store link."""
    got = resolve(series_key, episode)
    if isinstance(got, str):
        return None, got
    s, ep = got
    d = episode_dir(s, ep)
    video = str(d / "video.mp4")
    if (why := await asyncio.to_thread(publishable_render, video)) is not None:
        return None, f"no publishable render: {why}"
    m = read_manifest(d)
    if m is None:
        return None, "the manifest is unreadable"
    if m.audio == "notebooklm":
        text, why = await notebooklm_refusal(s, ep, d, m, video)
        if text is None:
            return None, why
        if (why := _qa_video(s, ep, d, m, text)) is not None:
            return None, why
        return (s, ep, d, m, text), None
    # The description is built from today's script (its lab files, what you
    # need): a render of an older script would go out under words it never
    # said. 2026-10-11, when the labs were added to scripted episodes.
    if m.script and m.script != script_key(ep):
        return None, "the render is of an older script; render it again"
    # Rule 22: every lesson carries the current bed (since 2026-10-17 under
    # the sting and the end card only). Defence in depth: script_key carries
    # MUSIC_VERSION, so the check above refuses first.
    if m.music != tutorial_music.MUSIC_VERSION:
        return None, ("the render has no background music (or an older bed); "
                      "render it again")
    probe, why = await _probe(video)
    if probe is None:
        return None, why
    if (probe["width"], probe["height"]) != (design.W, design.H) or not probe["audio"]:
        return None, (f"the file is {probe['width']}x{probe['height']}"
                      f"{'' if probe['audio'] else ' without sound'}, not 1920x1080 with sound")
    if not ep.min_seconds <= probe["seconds"] <= ep.max_seconds:
        return None, (f"it runs {probe['seconds']:.0f} s, outside "
                      f"{ep.min_seconds:.0f}-{ep.max_seconds:.0f} s")
    if (why := bookends_refusal(m)) is not None:
        return None, why
    # The end card names the next lesson from the catalog as it is now
    # (script_key carries it too; defence in depth): never a stale title.
    nxt = next_up(s.key, ep)
    if m.next_title != (nxt.title if nxt else ""):
        return None, (f"the end card names {m.next_title!r} but the next lesson is now "
                      f"{nxt.title if nxt else 'none'!r}; render it again")
    if (why := claim_refusal(ep, m.body_seconds or probe["seconds"])) is not None:
        return None, why
    if (why := chapters_refusal(m.chapters, probe["seconds"])) is not None:
        return None, f"chapters: {why}"
    text = description(s, ep, m.chapters, minutes_of(m.seconds))
    if (why := description_refusal(text)) is not None:
        return None, why
    if (why := title_refusal(ep, final_title(ep, m.seconds), probe["seconds"])) is not None:
        return None, why
    if m.badge and m.badge != badge_text(minutes_of(m.seconds)):
        return None, (f"the thumbnail badge says {m.badge!r} but the title says "
                      f"{minutes_of(m.seconds)} minutes")
    # Invariant 6: an opening that still claims "under N minutes" (a render
    # from before 2026-10-17) says the title's N, true of the file itself.
    if (why := opener_refusal(ep, m.spoken_minutes, probe["seconds"])) is not None:
        return None, why
    if m.spoken_minutes and m.spoken_minutes != minutes_of(m.seconds):
        return None, (f"the opener says {m.spoken_minutes} minutes but the title "
                      f"says {minutes_of(m.seconds)}")
    # Batch 9 (docs/academy-video-system/qa-checklist.md): the encoding, the
    # music windows, the pace, the drawn type and safe area, and the end
    # card's empty reserve, each measured; one that cannot run holds it.
    if (why := await video_qa.lesson_refusal(video, m, probe)) is not None:
        return None, f"QA: {why}"
    # Invariant 7: a file whose opening cannot be heard is held, never passed.
    heard, why = await opener_heard(d, m)
    if heard is None:
        return None, f"the opener could not be heard, so the file is not published: {why}"
    if (why := heard_refusal(ep, heard)) is not None:
        return None, why
    if (why := tags_refusal(s.key, ep)) is not None:
        return None, why
    if not thumb_file(d).is_file():
        return None, "no thumbnail was rendered"
    if (why := await asyncio.to_thread(downloads_refusal, ep)) is not None:
        return None, why
    if (why := _qa_video(s, ep, d, m, text)) is not None:
        return None, why
    return (s, ep, d, m, text), None


QA_PREFIX = "QA (video): "


def _qa_video(s: Series, ep: Episode, d: Path, m: Manifest, text: str) -> str | None:
    """The QA module's render check (modules/qa/video.py), pure. Its refusal
    is marked so `_publish` can raise the remediation task once."""
    from ..qa import video as qa_video
    why = qa_video.refusal(s, ep, d, m, text)
    return f"{QA_PREFIX}{why}" if why else None


def render_sha(d: Path) -> str:
    """Names one render: the manifest's bytes and the video's size and
    time (never the whole file read)."""
    import hashlib
    digest = hashlib.sha256()
    for name in ("manifest.json", "video.mp4"):
        path = d / name
        if path.is_file():
            st = path.stat()
            digest.update(f"{name}\0{st.st_size}\0{st.st_mtime_ns}\n".encode())
            if name == "manifest.json":
                digest.update(path.read_bytes())
    return digest.hexdigest()


async def _qa_remediation(series: str, episode: int, why: str) -> None:
    """One open remediation task per (series, episode, render): the render
    stays where it is; someone fixes it and the next tick tries again."""
    from ...kernel import qa_gate
    got = resolve(series, episode)
    if isinstance(got, str):
        return
    d = episode_dir(*got)
    sha = await asyncio.to_thread(render_sha, d)
    try:
        await qa_gate.remediate(ACTION,
                                f"tutorial:{series}:{episode}@{sha[:12]}", sha,
                                [why.removeprefix(QA_PREFIX)])
    except Exception as exc:  # noqa: BLE001
        log.warning("QA remediation for %s %s not raised: %s", series, episode, exc)


async def notebooklm_refusal(s: Series, ep: Episode, d: Path, m: Manifest, video: str
                             ) -> tuple[str | None, str | None]:
    """ready()'s profile for an ingested NotebookLM render (Miro, 2026-10-19):
    (the description, None) or (None, why). NotebookLM's look, narrator and
    end card are taken as they are, so the Fish render's gates are not run
    here: the palette and type QA, the music windows, the pace, 1920x1080 at
    30 fps, the sting and EndCard, the end card's next lesson, the "here is
    the result" opener and the chapters (it has none measured). Every
    honesty and metadata gate is: the script it was made from, the file
    (H.264 + AAC, at least 1280x720 with sound) and its length, the claim,
    the description (store link, synthetic-voice line, label, hashtags), the
    title, the badge, the tags, the thumbnail and the lab downloads."""
    from . import notebooklm
    if m.source_sha256 != notebooklm.source_sha(ep):
        return None, ("the NotebookLM render is of an older script; the runner must "
                      "render it again")
    probe, why = await _probe(video)
    if probe is None:
        return None, why
    if (why := notebooklm.probe_refusal(probe, ep)) is not None:
        return None, why
    if (why := claim_refusal(ep, probe["seconds"])) is not None:
        return None, why
    text = description(s, ep, m.chapters, minutes_of(m.seconds))
    if (why := description_refusal(text)) is not None:
        return None, why
    if (why := title_refusal(ep, final_title(ep, m.seconds), probe["seconds"])) is not None:
        return None, why
    if m.badge and m.badge != badge_text(minutes_of(m.seconds)):
        return None, (f"the thumbnail badge says {m.badge!r} but the title says "
                      f"{minutes_of(m.seconds)} minutes")
    if (why := tags_refusal(s.key, ep)) is not None:
        return None, why
    if not thumb_file(d).is_file():
        return None, "no thumbnail was rendered"
    if (why := await asyncio.to_thread(downloads_refusal, ep)) is not None:
        return None, why
    return text, None


async def publish_tutorial_dry(series: str, episode: int) -> ActionResult:
    if (refused := await old_design_refusal(series, episode)) is not None:
        return ActionResult(False, refused)
    got, why = await ready(series, int(episode))
    if got is None:
        return ActionResult(False, why or "not ready")
    s, ep, _d, m, _text = got
    row = await posted(s.key, ep.number)
    if row and row.get("public"):
        return ActionResult(False, f"{s.key} episode {ep.number} is already public on YouTube")
    first = (f"would finish {row['video_id']} (already uploaded, private)" if row else
             f"would upload \"{final_title(ep, m.seconds)}\" ({m.seconds:.0f} s) to the "
             f"{channel()} "
             "YouTube channel (private)")
    where = f"add it to the playlist \"{s.playlist}\"" if s.playlist else "no playlist"
    return ActionResult(True, f"{first}, set its thumbnail, {where} and make it public")


FORCE_SSL = "https://www.googleapis.com/auth/youtube.force-ssl"
NO_FORCE_SSL = "the token lacks youtube.force-ssl"


async def lacks_force_ssl(ch: str) -> str | None:
    """Final review M4: comments and captions need youtube.force-ssl. Why
    this channel's token cannot be used for them, or None when it can.
    Scopes that cannot be read are unknown, never OK."""
    from ..youtube import api
    scopes, why = await api.token_scopes(ch)
    if scopes is None:
        return f"the token's scopes could not be read ({why})"
    if FORCE_SSL not in scopes:
        return NO_FORCE_SSL
    return None


async def _publish(series: str, episode: int, *, strict: bool,
                   fresh: bool = False) -> ActionResult:
    """Upload (private), thumbnail, playlist, then public. Resumable: a video
    already uploaded is finished, never uploaded twice. `strict` (the
    automatic post) makes nothing public unless the thumbnail and the
    playlist both took; Miro's card (not strict) goes public and says what
    is missing. `fresh` (the relaunch, 2026-10-13) uploads a NEW video and
    row even though the episode is already public, under its own claim."""
    from ...kernel import idempotency
    from ...kernel.state import execute_sql, fetch_one
    from ..youtube import api
    got, why = await ready(series, int(episode))
    if got is None:
        if why and why.startswith(QA_PREFIX):
            await _qa_remediation(series, int(episode), why)
        return ActionResult(False, why or "not ready")
    s, ep, d, m, text = got
    ch = channel()
    row = await posted(s.key, ep.number)
    key = f"tutorial:{s.key}:{ep.number}"
    if fresh:
        # 2026-10-13 (relaunch): the first upload's claim is settled, so the
        # second one claims a key of its own, named by the row it replaces.
        # Named by the newest row of ANY kind (withdrawn too), so a relaunch
        # rolled back and tried again claims a new key, never a spent one.
        rows = await _episode_rows(s.key, ep.number)
        key = f"{key}:fresh:{max((int(x['id']) for x in rows), default=0)}"
        row = None
    if row and row.get("public"):
        return ActionResult(False, f"{s.key} episode {ep.number} is already public on YouTube")
    said: list[str] = []
    if row is None:
        state = await idempotency.claim(ACTION, key)
        if state != idempotency.CLAIMED:
            return ActionResult(False, idempotency.refusal(ACTION, state))
        up, why = await api.upload(ch, str(d / "video.mp4"), final_title(ep, m.seconds),
                                   text, list(upload_tags(s, ep)),
                                   category_id=api.EDUCATION,
                                   language=api.TUTORIAL_LANGUAGE,
                                   synthetic=True)   # Miro 2026-10-17: AI voice
        if up is None:
            await idempotency.finish(ACTION, key, why)
            return ActionResult(False, f"upload refused: {why}")
        made = await fetch_one(
            """INSERT INTO academy_tutorial_posts (series, episode, video_id, detail)
               VALUES ($1, $2, $3, 'uploaded private') RETURNING id""",
            s.key, ep.number, up["video_id"])
        await idempotency.settle(ACTION, key, f"uploaded {up['video_id']}")
        row = {"id": int(made["id"]) if made else 0, "video_id": up["video_id"],
               "playlist_id": None, "item_id": None, "thumb_set": False, "captions_set": False,
               "public": False}
        said.append(f"uploaded {up['video_id']} (private)")
    vid = str(row["video_id"])
    undo: dict = {"channel": ch, "video_id": vid}
    if not row.get("thumb_set"):
        thumb, why = await api.set_thumbnail(ch, vid, str(thumb_file(d)))
        row["thumb_set"] = thumb is not None
        said.append("thumbnail set" if thumb else f"thumbnail NOT set: {why}")
    # Rule 16: our own en-GB track. Not part of `complete`: a missing track
    # never holds a publish; it is said on the card.
    if not row.get("captions_set"):
        if not m.timings:              # an older render: an empty track is no track
            said.append("captions NOT set: the render predates caption timings; "
                        "render again")
        elif (no := await lacks_force_ssl(ch)) is not None:
            again = "; Miro must re-consent" if no == NO_FORCE_SSL else ""
            said.append(f"captions NOT set: {no}{again}")
        else:
            cap, why = await api.set_captions(ch, vid, caption_track(m, ep))
            row["captions_set"] = cap is not None
            said.append("captions set" if cap else f"captions NOT set: {why}")
    if not row.get("item_id") and s.playlist:
        pl, why = await api.find_playlist(ch, s.playlist)
        if pl == {}:
            pl, why = await api.create_playlist(ch, s.playlist, s.playlist_description)
            if pl:
                undo["created_playlist"] = {"id": pl["id"], "title": s.playlist}
        item = None
        if pl:
            item, why = await api.add_playlist_item(ch, pl["id"], vid)
        if item:
            row["playlist_id"], row["item_id"] = pl["id"], item["item_id"]
            said.append(f"added to \"{s.playlist}\"")
        else:
            said.append(f"NOT in the playlist: {why}")
    if row.get("item_id"):
        undo["item_id"] = row["item_id"]
    complete = bool(row.get("thumb_set") and (row.get("item_id") or not s.playlist))
    public = None
    if complete or not strict:
        public, why = await api.set_privacy(ch, vid, "public", synthetic=True)
        said.append("public" if public is not None else f"still private: {why}")
    else:
        said.append("left private: not public without its thumbnail and playlist")
    detail = f"{final_title(ep, m.seconds)}: " + "; ".join(said) + f" -- https://youtu.be/{vid}"
    await execute_sql(
        """UPDATE academy_tutorial_posts SET thumb_set = $2, playlist_id = $3,
                  item_id = $4, public = $5, detail = $6, captions_set = $7,
                  at = CASE WHEN $5 THEN now() ELSE at END
           WHERE id = $1""",
        int(row["id"]), bool(row.get("thumb_set")), row.get("playlist_id"),
        row.get("item_id"), public is not None, detail[:1000],
        bool(row.get("captions_set")))
    evidence = {"video_id": vid, "seconds": m.seconds, "public": public is not None,
                "in_playlist": bool(row.get("item_id")),
                "thumbnail": bool(row.get("thumb_set"))}
    if public is None:
        return ActionResult(False, detail, evidence=evidence)
    return ActionResult(True, detail, evidence=evidence,
                        rollback_token=json.dumps(undo, sort_keys=True))


async def publish_tutorial(series: str, episode: int) -> ActionResult:
    """Miro's card: finish and make public, saying anything that is missing.
    A card made for a Fish render before 2026-10-19 is refused (2026-10-19)."""
    if (refused := await old_design_refusal(series, episode)) is not None:
        return ActionResult(False, refused)
    return await _publish(series, episode, strict=False)


async def unpublish_tutorial(token: str) -> ActionResult:
    """Private again and out of the playlist; nothing is deleted (Tier 3)."""
    from ...kernel.state import execute_sql
    from ..youtube import api
    try:
        undo = json.loads(token)
        ch, vid = str(undo["channel"]), str(undo["video_id"])
    except (ValueError, KeyError, TypeError):
        return ActionResult(False, "unreadable rollback token")
    said = []
    _, why = await api.set_privacy(ch, vid, "private", synthetic=True)
    said.append("video private" if why is None else f"video NOT made private: {why}")
    if undo.get("item_id"):
        ok, why = await api.take_out_of_playlist(ch, str(undo["item_id"]))
        said.append("out of the playlist" if ok else f"still in the playlist: {why}")
    made = undo.get("created_playlist")
    if made:
        _, why = await api.set_playlist_privacy(ch, made["id"], made["title"], "private")
        said.append("the playlist this run made is private" if why is None
                    else f"playlist NOT made private: {why}")
    said.append("a custom thumbnail stays (the API cannot restore the old one)")
    await execute_sql("UPDATE academy_tutorial_posts SET withdrawn = true WHERE video_id = $1",
                      vid)
    ok = all("NOT" not in x and "still" not in x for x in said)
    return ActionResult(ok, f"{vid}: " + "; ".join(said))


# ------------------------------------------------- the relaunch
#
# 2026-10-13 (growth plan, task 9): five lessons went public before the
# "Welcome back... in under N minutes" opener. YouTube cannot replace a
# video's file, so a relaunch uploads today's render as a NEW video and makes
# the old one private -- never deleted (Tier 3). It changes which video the
# channel's viewers have, so it is Miro's Tier 2 card, never automatic.

RERENDER = "render it again first"


async def _episode_rows(series_key: str, episode: int) -> list[dict]:
    """Every post row of this episode, withdrawn included, newest first."""
    from ...kernel.state import fetch_all
    rows = await fetch_all(
        """SELECT id, video_id, playlist_id, item_id, public, withdrawn
           FROM academy_tutorial_posts WHERE series = $1 AND episode = $2
           ORDER BY id DESC""", series_key, episode)
    return [dict(x) for x in rows or []]


async def _relaunch_check(series: str, episode: int
                          ) -> tuple[tuple[Series, Episode, Manifest, dict, dict | None]
                                     | None, str | None]:
    """The current render, the old public row and a newer private upload a
    failed relaunch left behind (or None), or why not. ready() holds the
    render to today's script; a manifest that names no script or carries no
    caption timings predates them, and is refused here too. An ingested
    NotebookLM render names no script_key: its source_sha256 of today's
    source text stands for it (2026-10-25, notebooklm.RERENDER_POSTED)."""
    # ready() runs here and again inside _publish (an extra ffprobe and
    # opener transcription): accepted, so the card's refusal can say
    # "render it again first" before anything is uploaded.
    got, why = await ready(series, int(episode))
    if got is None:
        return None, f"{why or 'not ready'}; {RERENDER}"
    s, ep, _d, m, _text = got
    from . import notebooklm
    if m.audio == notebooklm.AUDIO:
        scripted = m.source_sha256 == notebooklm.source_sha(ep)
    else:
        scripted = bool(m.script)
    if not scripted or not m.timings:
        return None, ("the render predates today's script and its caption "
                      f"timings; {RERENDER}")
    live = [x for x in await _episode_rows(s.key, ep.number) if not x.get("withdrawn")]
    public = [x for x in live if x.get("public")]
    if len(public) > 1:
        return None, (f"{s.key} episode {ep.number} has {len(public)} public videos ("
                      + ", ".join(str(x["video_id"]) for x in public)
                      + "): an earlier relaunch did not finish; roll it back first")
    if not public:
        return None, (f"{s.key} episode {ep.number} has no public video to relaunch "
                      "(not public on YouTube); use academy.publish_tutorial")
    old = public[0]
    pending = next((x for x in live if int(x["id"]) > int(old["id"])), None)
    return (s, ep, m, old, pending), None


async def relaunch_tutorial_dry(series: str, episode: int) -> ActionResult:
    if (refused := await old_design_refusal(series, episode)) is not None:
        return ActionResult(False, refused)
    got, why = await _relaunch_check(series, episode)
    if got is None:
        return ActionResult(False, why or "not ready")
    _s, ep, m, old, pending = got
    first = (f"would finish {pending['video_id']} (a relaunch's private upload)"
             if pending else "would upload, as a new video,")
    return ActionResult(True, (
        f"{first} today's render \"{final_title(ep, m.seconds)}\" "
        f"({minutes_of(m.seconds)} minutes, {m.seconds:.0f} s) on the {channel()} "
        f"YouTube channel with its thumbnail, captions and playlist, and make it "
        f"public; then the old video {old['video_id']} goes private, leaves its "
        f"playlist and its row is withdrawn. nothing is deleted"))


async def relaunch_tutorial(series: str, episode: int) -> ActionResult:
    """Miro's card: a new upload of today's render (or the finish of one a
    failed relaunch left private), then the old video private and out of its
    playlist. Only a NotebookLM render, unless the fallback (2026-10-19)."""
    from ...kernel.state import execute_sql, fetch_all
    from ..youtube import api
    if (refused := await old_design_refusal(series, episode)) is not None:
        return ActionResult(False, refused)
    got, why = await _relaunch_check(series, episode)
    if got is None:
        return ActionResult(False, why or "not ready")
    s, _ep, _m, old, pending = got
    old_vid, old_id = str(old["video_id"]), int(old["id"])
    # A pending private upload is the newest live row, so posted() resumes
    # it: fresh=False finishes it instead of uploading a third video.
    r = await _publish(s.key, int(episode), strict=False, fresh=pending is None)
    new_vid = str((r.evidence or {}).get("video_id") or "")
    if not r.ok or not new_vid:
        return ActionResult(False, (
            f"the new upload did not go public, so {old_vid} stays public: {r.detail}"
            " (approve the relaunch again to finish that upload)"),
            evidence=r.evidence)
    ch = channel()
    try:
        item = json.loads(r.rollback_token or "{}").get("item_id")
    except ValueError:
        item = None
    undo: dict = {"channel": ch, "old_video_id": old_vid, "new_video_id": new_vid,
                  "old_row_id": old_id, "new_item_id": item, "comment_ids": [],
                  "old_playlist_id": None, "old_item_id": None}
    _, why = await api.set_privacy(ch, old_vid, "private", synthetic=True)
    if why is not None:
        # kernel/executor.py writes the rollback token into execute.finish
        # whatever `ok` says, so the undo stays reachable from a failure.
        return ActionResult(False, (
            f"new video {new_vid} is public, but the old one {old_vid} was NOT made "
            f"private ({why}): both are public; roll back, or make {old_vid} "
            "private in Studio"), evidence={"video_id": new_vid},
            rollback_token=json.dumps(undo, sort_keys=True))
    said = [f"old video {old_vid} private"]
    item_kept = old.get("item_id")
    if old.get("item_id"):
        ok, why = await api.take_out_of_playlist(ch, str(old["item_id"]))
        if ok:
            undo["old_playlist_id"] = old.get("playlist_id")
            undo["old_item_id"] = old["item_id"]
            item_kept = None
            said.append("out of its playlist")
        else:
            said.append(f"still in its playlist: {why}")
    await execute_sql(
        "UPDATE academy_tutorial_posts SET withdrawn = true, item_id = $3, "
        "detail = coalesce(detail, '') || $2 WHERE id = $1",
        old_id, f" -- relaunched as {new_vid}", item_kept)
    # The old video's comment belongs to it; the new one gets its own from
    # the daily pin pass. The rows only: nothing is sent to YouTube.
    rows = await fetch_all(
        "UPDATE academy_tutorial_comments SET withdrawn = true "
        "WHERE video_id = $1 AND NOT withdrawn RETURNING id", old_vid)
    undo["comment_ids"] = [int(x["id"]) for x in rows or []]
    return ActionResult(True, (
        f"relaunched: new video {new_vid} public (https://youtu.be/{new_vid}); "
        + ", ".join(said) + f"; nothing deleted -- {r.detail}"),
        evidence={"video_id": new_vid, "old_video_id": old_vid},
        rollback_token=json.dumps(undo, sort_keys=True))


async def unrelaunch_tutorial(token: str) -> ActionResult:
    """The true undo: the new video private (and out of its playlist), the old
    one public and back in its playlist, the rows as they were. Nothing is
    deleted (Tier 3)."""
    from ...kernel.state import execute_sql
    from ..youtube import api
    try:
        undo = json.loads(token)
        ch, old_vid = str(undo["channel"]), str(undo["old_video_id"])
        new_vid, old_id = str(undo["new_video_id"]), int(undo["old_row_id"])
        comments = [int(x) for x in undo.get("comment_ids") or []]
    except (ValueError, KeyError, TypeError):
        return ActionResult(False, "unreadable rollback token")
    said = []
    _, why = await api.set_privacy(ch, new_vid, "private", synthetic=True)
    said.append(f"new {new_vid} private" if why is None
                else f"new {new_vid} NOT made private: {why}")
    if undo.get("new_item_id"):
        ok, why = await api.take_out_of_playlist(ch, str(undo["new_item_id"]))
        said.append("new one out of the playlist" if ok
                    else f"new one still in the playlist: {why}")
    _, why = await api.set_privacy(ch, old_vid, "public", synthetic=True)
    said.append(f"old {old_vid} public again" if why is None
                else f"old {old_vid} NOT made public: {why}")
    if undo.get("old_playlist_id"):
        back, why = await api.add_playlist_item(ch, str(undo["old_playlist_id"]), old_vid)
        if back:
            await execute_sql("UPDATE academy_tutorial_posts SET item_id = $2 "
                              "WHERE id = $1", old_id, back["item_id"])
            said.append("old one back in its playlist")
        else:
            said.append(f"old one NOT back in its playlist: {why}")
    await execute_sql("UPDATE academy_tutorial_posts SET withdrawn = true "
                      "WHERE video_id = $1", new_vid)
    await execute_sql("UPDATE academy_tutorial_posts SET withdrawn = false "
                      "WHERE id = $1", old_id)
    if comments:
        await execute_sql("UPDATE academy_tutorial_comments SET withdrawn = false "
                          "WHERE id = ANY($1::bigint[])", comments)
    ok = all("NOT" not in x and "still" not in x for x in said)
    return ActionResult(ok, "; ".join(said))


# ------------------------------------------------- the pinned comment
#
# 2026-10-13 (growth plan, task 6): the episode's quiz and its free lab file
# in a comment under the public video. Tier 1 (Miro's standing policy: a
# routine action approves itself in code), so invariant 8 holds it to a real
# dry run and a real rollback. YouTube has no API for pinning: the detail
# says "pin it in Studio". A comment is never deleted (Tier 3): the rollback
# edits it to "(withdrawn)".

def pinned_comment(s: Series, ep: Episode) -> str:
    """The comment's text: the first quiz with its lettered options, each lab
    file's link, then the store."""
    quiz = next((sc for sc in ep.scenes if sc.kind == "quiz"), None)
    lines = []
    if quiz:
        options = " / ".join(f"{design.LETTERS[i]}. {plain(x)}"
                             for i, x in enumerate(quiz.items))
        lines.append(f"Quick quiz: {plain(quiz.heading)} {options} "
                     "— comment your answer.")
    for f in downloads_of(ep):
        lines.append(f"Free lab file (no sign-up): {lab_url(f)}")
    lines.append(f"More: {STORE_URL}")
    return "\n".join(lines)


async def _public_video(series: str, episode: int) -> tuple[Series, Episode, str] | str:
    """The episode and its public video's id, or why there is none."""
    got = resolve(series, episode)
    if isinstance(got, str):
        return got
    s, ep = got
    row = await posted(s.key, ep.number)
    if not row or not row.get("public"):
        return f"{s.key} #{ep.number} is not public yet"
    return s, ep, str(row["video_id"])


async def pin_comment_dry(series: str, episode: int) -> ActionResult:
    got = await _public_video(series, episode)
    if isinstance(got, str):
        return ActionResult(False, got)
    s, ep, vid = got
    return ActionResult(True, f"would post under video {vid} on the {channel()} channel, "
                              f"for Miro to pin in Studio:\n{pinned_comment(s, ep)}")


async def pin_comment(series: str, episode: int) -> ActionResult:
    """Post the comment once per video: the claim is taken before the post,
    so a retry never posts a second one."""
    from ...kernel import idempotency
    from ...kernel.state import fetch_one
    from ..youtube import api
    got = await _public_video(series, episode)
    if isinstance(got, str):
        return ActionResult(False, got)
    s, ep, vid = got
    key = f"pin:{s.key}:{ep.number}:{vid}"
    state = await idempotency.claim(ACTION_PIN, key)
    if state != idempotency.CLAIMED:
        return ActionResult(False, idempotency.refusal(ACTION_PIN, state))
    text = pinned_comment(s, ep)
    made, why = await api.post_comment(channel(), vid, text)
    if made is None:
        await idempotency.finish(ACTION_PIN, key, why)
        return ActionResult(False, f"comment refused: {why}")
    try:
        await fetch_one(
            """INSERT INTO academy_tutorial_comments
                      (series, episode, video_id, comment_id, text)
               VALUES ($1, $2, $3, $4, $5) RETURNING id""",
            s.key, ep.number, vid, made["id"], text)
    finally:
        # The comment is up: settle the claim even if its row failed, so a
        # retry never posts a second one.
        await idempotency.settle(ACTION_PIN, key, f"comment {made['id']}")
    undo = {"series": s.key, "episode": ep.number, "video_id": vid,
            "comment_id": made["id"], "channel": channel()}
    return ActionResult(True, f"posted comment {made['id']}; pin it in Studio "
                              "(no API for pinning)",
                        rollback_token=json.dumps(undo, sort_keys=True))


async def unpin_comment(token: str) -> ActionResult:
    """The rollback: edit the episode's comment to "(withdrawn)" and mark its
    row. Never a delete -- deletion is Tier 3."""
    from ...kernel.state import execute_sql, fetch_one
    from ..youtube import api
    try:
        undo = json.loads(token)
        comment_id = str(undo["comment_id"])
    except (ValueError, KeyError, TypeError):
        return ActionResult(False, "unreadable rollback token")
    row = await fetch_one(
        """SELECT id, comment_id, video_id, withdrawn FROM academy_tutorial_comments
           WHERE comment_id = $1
           ORDER BY withdrawn, id DESC LIMIT 1""", comment_id)
    ch = str(undo.get("channel") or channel())
    if row and row.get("withdrawn"):
        return ActionResult(True, f"comment {comment_id} is already withdrawn")
    if not row:
        return await _withdraw_unrecorded(ch, comment_id, undo)
    _, why = await api.update_comment(ch, str(row["comment_id"]), WITHDRAWN)
    if why is not None:
        return ActionResult(False, f"comment {row['comment_id']} NOT withdrawn: {why}")
    await execute_sql("UPDATE academy_tutorial_comments SET withdrawn = true WHERE id = $1",
                      row["id"])
    return ActionResult(True, f"comment {row['comment_id']} under {row['video_id']} "
                              "now reads \"(withdrawn)\"; unpin it in Studio")


async def _withdraw_unrecorded(ch: str, comment_id: str, undo: dict) -> ActionResult:
    """Final review M3: the comment is up but its row never landed
    (pin_comment settles its claim regardless). Withdraw it from the token,
    then record a withdrawn row so the video is never posted under again."""
    from ...kernel.state import execute_sql
    from ..youtube import api
    _, why = await api.update_comment(ch, comment_id, WITHDRAWN)
    if why is not None:
        return ActionResult(False, f"comment {comment_id} NOT withdrawn: {why}")
    said = f"comment {comment_id} now reads \"(withdrawn)\"; unpin it in Studio"
    try:
        series, episode = str(undo["series"]), int(undo["episode"])
        video_id = str(undo["video_id"])
    except (KeyError, TypeError, ValueError):
        return ActionResult(True, f"{said} (no row recorded: the token names no video)")
    try:
        await execute_sql(
            """INSERT INTO academy_tutorial_comments
                      (series, episode, video_id, comment_id, text, withdrawn)
               VALUES ($1, $2, $3, $4, $5, true)""",
            series, episode, video_id, comment_id, WITHDRAWN)
    except Exception as exc:  # noqa: BLE001
        return ActionResult(True, f"{said} (its row was not recorded: "
                                  f"{type(exc).__name__})")
    return ActionResult(True, f"{said} (under {video_id}; recorded withdrawn)")


PINS_PER_DAY = 3                     # task 6, fix round 1: comments the tick posts a day


async def unpinned() -> list[dict]:
    """Public, live tutorials whose video has never had a comment, oldest
    first. A withdrawn comment still counts (fix round 2): a rolled-back
    episode is not posted under again, and never takes the day's slots."""
    from ...kernel.state import fetch_all
    rows = await fetch_all(
        """SELECT p.series, p.episode, min(p.at) AS at FROM academy_tutorial_posts p
            WHERE p.public AND NOT p.withdrawn
              AND NOT EXISTS (SELECT 1 FROM academy_tutorial_comments c
                               WHERE c.video_id = p.video_id)
            GROUP BY p.series, p.episode ORDER BY at""")
    return [dict(r) for r in rows]


async def pinned_today() -> int:
    from ...kernel.state import fetch_one
    row = await fetch_one(
        "SELECT count(*) AS n FROM academy_tutorial_comments WHERE at > $1",
        datetime.now(UTC) - timedelta(hours=24))
    return int(row["n"]) if row else 0


async def pin_pass() -> list[str]:
    """The tick's comment pass: academy.pin_comment (Tier 1, through the
    executor) under each public tutorial that has none, PINS_PER_DAY at most
    in 24 hours."""
    from ...kernel.executor import ExecutionRequest, execute
    room = PINS_PER_DAY - await pinned_today()
    said = []
    todo = (await unpinned())[:max(room, 0)]
    if todo and (no := await lacks_force_ssl(channel())) is not None:
        # Final review M4: no call, so no claim, until Miro re-consents.
        return [f"comments held: {no}"]
    for row in todo:
        key, n = str(row["series"]), int(row["episode"])
        done = await execute(ExecutionRequest(
            action=ACTION_PIN, params={"series": key, "episode": n},
            reason=f"the quiz + lab comment under {key} #{n}"))
        said.append(f"{key} #{n} comment: {done.detail}")
    return said


async def autopublish_check(series: str, episode: int) -> str | None:
    """None when this episode may post itself now; otherwise why not."""
    from ..youtube import api
    if not autopublish_enabled():
        return ("automatic posting is switched off (ACADEMY_TUTORIALS_AUTOPUBLISH "
                "or ACADEMY_TUTORIALS_AUTOPOST)")
    if (n := await posted_today()) >= per_day():
        return f"{n} tutorial(s) already posted today (ACADEMY_TUTORIALS_PER_DAY)"
    _got, why = await ready(series, int(episode))
    if why is not None:
        return why
    scopes, why = await api.token_scopes(channel())
    if scopes is None:
        return f"the YouTube token's scopes could not be read ({why})"
    if not scopes & set(api.PLAYLIST_SCOPES):
        prefix = api.CHANNELS.get(channel(), "YOUTUBE_?_")
        return (f"the {channel()} token cannot manage playlists: re-authorise "
                f"{prefix}REFRESH_TOKEN with the youtube scope")
    return None


async def _card(series: str, episode: int, why: str) -> ActionResult:
    from ...kernel import approvals
    pid = await approvals.fresh_card(
        ACTION, {"series": series, "episode": int(episode)},
        f"{HELD_PREFIX} {why}. The {series} tutorial, episode {episode}.",
        dedup_key=f"academy.tutorial.{series}.{episode}")
    return ActionResult(True, f"held ({why}); card #{pid} raised" if pid else
                        f"held ({why}); no card could be raised")


async def autopublish_tutorial_dry(series: str, episode: int) -> ActionResult:
    why = await autopublish_check(series, episode)
    if why is not None:
        return ActionResult(True, f"would hold ({why}) and raise the card instead")
    return await publish_tutorial_dry(series, episode)


async def autopublish_tutorial(series: str, episode: int) -> ActionResult:
    """Post the episode by itself, or card it, reason first. A run that
    uploaded but could not finish leaves the video private and cards the
    rest (the card finishes the same upload, never a second one). Miro,
    2026-10-19: an episode with no current NotebookLM render is refused here
    too, not only by its caller (_post), unless ACADEMY_NOTEBOOKLM_FALLBACK."""
    if (refused := await old_design_refusal(series, episode)) is not None:
        return ActionResult(False, refused)
    why = await autopublish_check(series, episode)
    if why is not None:
        return await _card(series, episode, why)
    result = await _publish(series, episode, strict=True)
    if not result.ok:
        return await _card(series, episode, result.detail[:300])
    return result


# ------------------------------------------------------- playlist, on its own

async def add_to_series_playlist_dry(series: str, episode: int, video_id: str) -> ActionResult:
    got = resolve(series, episode)
    if isinstance(got, str):
        return ActionResult(False, got)
    s, _ep = got
    return ActionResult(True, f"would add {video_id} to \"{s.playlist}\" on the "
                              f"{channel()} channel (made public if it does not exist)")


async def add_to_series_playlist(series: str, episode: int, video_id: str) -> ActionResult:
    from ..youtube import api
    got = resolve(series, episode)
    if isinstance(got, str):
        return ActionResult(False, got)
    s, _ep = got
    if not s.playlist:
        return ActionResult(False, f"the {s.key} video belongs to no playlist")
    ch = channel()
    undo: dict = {"channel": ch}
    pl, why = await api.find_playlist(ch, s.playlist)
    if pl == {}:
        pl, why = await api.create_playlist(ch, s.playlist, s.playlist_description)
        if pl:
            undo["created_playlist"] = {"id": pl["id"], "title": s.playlist}
    if not pl:
        return ActionResult(False, f"no playlist: {why}")
    item, why = await api.add_playlist_item(ch, pl["id"], video_id)
    if item is None:
        return ActionResult(False, f"not added: {why}")
    undo["item_id"] = item["item_id"]
    return ActionResult(True, f"{video_id} added to \"{s.playlist}\"",
                        rollback_token=json.dumps(undo, sort_keys=True))


async def undo_series_playlist(token: str) -> ActionResult:
    from ..youtube import api
    try:
        undo = json.loads(token)
        ch = str(undo["channel"])
    except (ValueError, KeyError, TypeError):
        return ActionResult(False, "unreadable rollback token")
    said, ok = [], True
    if undo.get("item_id"):
        done, why = await api.take_out_of_playlist(ch, str(undo["item_id"]))
        ok &= done
        said.append("taken out of the playlist" if done else f"still in it: {why}")
    if undo.get("created_playlist"):
        made = undo["created_playlist"]
        _, why = await api.set_playlist_privacy(ch, made["id"], made["title"], "private")
        ok &= why is None
        said.append("the playlist made is private" if why is None else f"playlist: {why}")
    return ActionResult(ok, "; ".join(said) or "nothing to undo")


# ------------------------------------------------------ review in Slack first

def review_reason(s: Series, ep: Episode, m: Manifest) -> str:
    """The card's words (verbose: Miro reads them before approving). No
    link here: a proposal's reason is audited for good, and the watch link
    carries a token. The link is in the preview message under the card."""
    where = f"the playlist \"{s.playlist}\"" if s.playlist else "no playlist"
    if m.audio == "notebooklm":
        shape = (f"{stamp(m.seconds)} long · NotebookLM render (its own narrator, "
                 "slides and end card) · no chapters")
    else:
        shape = (f"{stamp(m.seconds)} long · Intro ✓ ({m.intro_seconds:.0f} s) / Ending ✓ "
                 f"({m.outro_seconds:.0f} s) · teaching {stamp(m.body_seconds)} · "
                 f"{len(m.chapters)} chapters")
    return "\n".join([
        f"Draft for YouTube: *{final_title(ep, m.seconds)}*",
        shape,
        f"Hashtags: {' '.join(hashtags_for(s.key, ep))}",
        (f"Tags ({tags_length(upload_tags(s, ep))}/{TAGS_MAX}): "
         f"{', '.join(upload_tags(s, ep))}"),
        (f"On approve: upload to the {channel()} YouTube channel, thumbnail, {where}, "
         "then public. Nothing goes to YouTube until you approve; watch it in the "
         "message below first."),
        *video_qa.notes(m),
    ])


def _media_url(token: str) -> str | None:
    host = os.getenv("ATLAS_HOST", "").strip()
    return f"https://{host}/media/{token}" if host else None


def review_blocks(pid: int, s: Series, ep: Episode, m: Manifest, text: str,
                  token: str | None, until: datetime | None, *,
                  image: bool = True) -> list[dict]:
    """The preview message under card #pid: the thumbnail, the watch link,
    the title, the full description, the tags and the bookends."""
    from ...channels.slack import _sections, clip
    video = _media_url(token) if token else None
    blocks: list[dict] = [{"type": "header", "text": {
        "type": "plain_text",
        "text": clip(f"#{pid} draft: {final_title(ep, m.seconds)}")}}]
    if video and image:
        blocks.append({"type": "image", "image_url": video + ".jpg",
                       "alt_text": ep.thumb_title.replace("\n", " ")})
    watch = (f"*Watch the full video:* <{video}|{s.key} #{ep.number} video.mp4> "
             f"(link good until {until:%Y-%m-%d %H:%M} UTC)" if video and until else
             "*Watch:* no public link (ATLAS_HOST is unset), so watch it on the server")
    blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": (
        f"{watch}\n*Length:* {stamp(m.seconds)} · *Intro* ✓ {m.intro_seconds:.0f} s · "
        f"*Ending* ✓ {m.outro_seconds:.0f} s\n*Title:* {final_title(ep, m.seconds)}")}})
    blocks += _sections("*Description*\n" + text)
    blocks.append({"type": "context", "elements": [{"type": "mrkdwn", "text": (
        f"Tags: {', '.join(upload_tags(s, ep))}")[:2900]}]})
    blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": (
        f"Approve or decline on card #{pid} above. Approving publishes it at once.")}})
    return blocks[:48]


async def _reviewed(series_key: str, episode: int, pid: int) -> bool:
    from ...kernel.state import fetch_one
    row = await fetch_one(
        """SELECT id FROM academy_tutorial_reviews
           WHERE series = $1 AND episode = $2 AND proposal_id = $3 LIMIT 1""",
        series_key, episode, pid)
    return row is not None


async def _tell(text: str) -> None:
    """A plain line in the drafts channel (no buttons): what could not be
    drafted and why."""
    from ...kernel.comms import Outbound, notify
    from ...kernel.contracts import Severity
    try:
        await notify(Outbound(title=text[:150], body=text, topic=REVIEW_TOPIC,
                              severity=Severity.NORMAL))
    except Exception as exc:  # noqa: BLE001
        log.warning("tutorials: could not tell the drafts channel: %s", exc)


async def review_tutorial(series_key: str, episode: int) -> str:
    """Raise the episode's draft card in #atlas-academy-videos and post the
    preview under it. Returns what happened, said plainly -- including
    Slack's own error when the bot cannot post there (not_in_channel)."""
    from ...kernel import approvals, comms
    from ...kernel.state import execute_sql
    got, why = await ready(series_key, int(episode))
    if got is None:
        await _tell(f"Academy draft not made for {series_key} #{episode}: {why}")
        return f"not drafted: {why}"
    s, ep, _d, m, text = got
    pid = await approvals.fresh_card(
        ACTION, {"series": s.key, "episode": ep.number}, review_reason(s, ep, m),
        # Its own key: a card left from the older held-post path never
        # stands in for a review draft.
        dedup_key=f"academy.tutorial.{s.key}.{ep.number}.review",
        evidence={"seconds": m.seconds, "intro_seconds": m.intro_seconds,
                  "outro_seconds": m.outro_seconds, "body_seconds": m.body_seconds},
        rollback_plan="the video goes private and leaves its playlist; nothing is deleted")
    if pid is None:
        return "no card could be raised (see the audit log)"
    if await _reviewed(s.key, ep.number, pid):
        return f"card #{pid} is already waiting in #atlas-academy-videos"
    token = "tut." + secrets.token_urlsafe(32)
    until = datetime.now(UTC) + REVIEW_LINK_TTL
    await execute_sql(
        """INSERT INTO academy_tutorial_reviews
             (series, episode, proposal_id, media_token, token_expires_at, detail)
           VALUES ($1, $2, $3, $4, $5, 'drafted')""",
        s.key, ep.number, pid, token, until)
    in_slack = comms.sent_ref(f"#{pid} — ", "slack") is not None
    slack = comms.CHANNELS.get("slack")
    if slack is None or not hasattr(slack, "post_blocks"):
        return f"card #{pid} raised; no Slack to post the preview to"
    target = slack.channel_for(REVIEW_TOPIC)
    title = f"#{pid} draft: {final_title(ep, m.seconds)}"
    ts_, err = await slack.post_blocks(
        channel=target, text=title, blocks=review_blocks(pid, s, ep, m, text, token, until))
    if err and err not in ("not_in_channel", "channel_not_found", "missing_scope"):
        # An image Slack could not fetch rejects the whole message: again, without it.
        ts_, err = await slack.post_blocks(
            channel=target, text=title,
            blocks=review_blocks(pid, s, ep, m, text, token, until, image=False))
    said = f"card #{pid}" + (" in Slack" if in_slack else
                             " raised but NOT delivered to Slack")
    if err == "not_in_channel":
        why = (f"Slack says not_in_channel: the bot is not in {target}; type "
               "/invite @Atlas in #atlas-academy-videos")
        await execute_sql("UPDATE academy_tutorial_reviews SET detail = $2 WHERE media_token = $1",
                          token, why[:500])
        return f"{said}; preview NOT posted: {why}"
    if err:
        return f"{said}; preview NOT posted: Slack said {err}"
    return f"{said}; preview posted ({ts_})"


# ------------------------------------------------------------- the daily tick

def order() -> list[tuple[str, int]]:
    """Every episode, the welcome first, then by level round-robin across
    the series (Miro, 2026-10-10, "teach the basics first and then
    advanced"). The local preview walks it; the daily drafts go by
    `lineup()`, one ladder per series."""
    out: list[tuple[str, int]] = [(s.key, e.number) for s in SERIES if not s.playlist
                                  for e in s.episodes]
    lessons = [s for s in SERIES if s.playlist]
    for level in sorted({e.level for s in lessons for e in s.episodes}):
        groups = [[e for e in s.episodes if e.level == level] for s in lessons]
        for i in range(max((len(g) for g in groups), default=0)):
            for s, g in zip(lessons, groups, strict=True):
                if i < len(g):
                    out.append((s.key, g[i].number))
    return out


def ladder(s: Series) -> list[Episode]:
    """A series' episodes in teaching order: by level, then number."""
    return sorted(s.episodes, key=lambda e: (e.level, e.number))


def next_in_series(s: Series, done: set[tuple[str, int]]
                   ) -> tuple[int | None, str | None]:
    """(the series' next episode to draft, None), or (None, the note) when
    that next episode has no script -- strict order: an unscripted lower
    episode is never skipped -- or (None, None) when the series is done. A
    held episode (tutorial_series.held: it needs a recording or Miro) is
    passed over until released; when only held ones are left, the note
    names the first (Miro 2026-10-18, video system pack)."""
    hold: str | None = None
    for ep in ladder(s):
        if (s.key, ep.number) in done:
            continue
        if (why := held(s.key, ep)) is not None:
            hold = hold or why
            continue
        if ep.scripted:
            return ep.number, None
        return None, f"script not written for {s.key} #{ep.number}"
    return None, hold


def fair_order(last: dict[str, datetime] | None = None) -> list[Series]:
    """The series, the one whose last public post is oldest first: a series
    never posted first, ties in catalog order (2026-10-16: in catalog order
    the same series lost the hourly render ceiling every day)."""
    if not last:
        return list(SERIES)
    floor = datetime.min.replace(tzinfo=UTC)
    return sorted(SERIES, key=lambda s: (s.key in last, last.get(s.key) or floor))


def lineup(done: set[tuple[str, int]], today: dict[str, int],
           last: dict[str, datetime] | None = None,
           month: dict[str, int] | None = None
           ) -> tuple[list[tuple[str, int]], list[str]]:
    """Today's drafts, one series after another (Miro, 2026-10-11: "one video
    for each series each day") in `fair_order(last)`: each series' next
    episodes, up to per_series_daily() less what it already drafted today;
    and the notes for the series whose next episode has no script. A
    MONTHLY_SERIES series takes at most one a calendar month: none once
    `month` (its episodes this month, from `_done`) says one went out."""
    out: list[tuple[str, int]] = []
    notes: list[str] = []
    seen = set(done)
    month = month or {}
    for s in fair_order(last):
        room = per_series_daily() - today.get(s.key, 0)
        if s.key in MONTHLY_SERIES:
            room = min(room, 1 - month.get(s.key, 0))
        for _ in range(max(0, room)):
            n, note = next_in_series(s, seen)
            if note is not None:
                notes.append(note)
            if n is None:
                break
            out.append((s.key, n))
            seen.add((s.key, n))
    return out, notes


async def _done() -> tuple[set[tuple[str, int]], dict[str, int], dict[str, datetime],
                           dict[str, int]]:
    """(episodes posted or drafted, how many each series drafted -- or,
    posting by itself, posted -- today, UTC, each series' last public post,
    never a withdrawn one, and how many each series made public this
    calendar month, UTC -- plus, while posting waits for a tap, drafted --
    never a withdrawn one) in one read. The month count is what keeps a
    MONTHLY_SERIES series to one a month in `lineup`."""
    from ...kernel.state import fetch_all
    rows = await fetch_all(
        """SELECT 'post' AS kind, series, episode, at, public, withdrawn
             FROM academy_tutorial_posts
           UNION ALL
           SELECT 'draft' AS kind, series, episode, at, FALSE AS public,
                  FALSE AS withdrawn
             FROM academy_tutorial_reviews""")
    day = datetime.now(UTC).date()
    counted = "post" if autopublish_enabled() else "draft"
    done = {(str(r["series"]), int(r["episode"])) for r in rows}
    today: dict[str, int] = {}
    last: dict[str, datetime] = {}
    month: dict[str, int] = {}
    for r in rows:
        at = r["at"]
        if not isinstance(at, datetime):
            continue
        key = str(r["series"])
        if r["kind"] == counted and at.astimezone(UTC).date() == day:
            today[key] = today.get(key, 0) + 1
        public = r["kind"] == "post" and r.get("public") and not r.get("withdrawn")
        if public and (key not in last or at > last[key]):
            last[key] = at
        when = at.astimezone(UTC).date()
        if ((public or (r["kind"] == "draft" and counted == "draft"))
                and (when.year, when.month) == (day.year, day.month)):
            month[key] = month.get(key, 0) + 1
    return done, today, last, month


# Miro, 2026-10-15: "make sure videos are being posted automatically with no
# stopping". A scheduled writer adds the next scripts (tutorial_scripts/,
# docs/academy-script-writer.md); the runway says how many days of posting
# each series has left at one a day, so a short one is said before it stops.
RUNWAY_WARN_DAYS = 3
RUNWAY_NOTE = "script runway"
# 2026-10-16: an episode held on a failed-gate card (_card: action ACTION,
# dedup key academy.tutorial.<series>.<n>, reason HELD_PREFIX) for longer
# than this will not post by itself, so it is not runway.
HELD_PREFIX = "Held, not posted itself:"
HELD_STALE = timedelta(days=2)


def _month_start(year: int, month: int) -> datetime:
    """The first instant of a calendar month, UTC; months past 12 roll over."""
    year, month = year + (month - 1) // 12, (month - 1) % 12 + 1
    return datetime(year, month, 1, tzinfo=UTC)


def monthly_runway_days(scripts: int, posted_this_month: bool,
                        now: datetime | None = None) -> int:
    """Days of posting a MONTHLY_SERIES series has left: each script covers a
    calendar month (UTC), starting this month when nothing went public yet
    this month, else next month. Counted to the first day of the month after
    the last covered one; 0 when this month's episode is due and unscripted."""
    now = (now or datetime.now(UTC)).astimezone(UTC)
    first = now.month + (1 if posted_this_month else 0)
    end = _month_start(now.year, first + scripts)
    if not posted_this_month and scripts == 0:
        return 0
    return max(0, (end.date() - now.date()).days)


async def runway() -> dict[str, int | None]:
    """Per lesson series, the scripted episodes that can still post: not
    public, never withdrawn (a withdrawn episode counts as done in lineup()),
    and not held on a failed-gate card older than HELD_STALE. Days of posting
    left at one a day; for a MONTHLY_SERIES series, at one a calendar month
    (`monthly_runway_days`), so one script a month is enough. None (UNKNOWN)
    for every series when either record cannot be read -- never 0, never a
    guess. Read-only; never raises."""
    from ...kernel.state import fetch_all
    lessons = [s for s in SERIES if s.playlist]
    try:
        rows = await fetch_all(
            """SELECT series, episode, at, public, withdrawn
                 FROM academy_tutorial_posts WHERE public OR withdrawn""")
        cards = await fetch_all(
            """SELECT DISTINCT ON (dedup_key) dedup_key, reason, created_at
                 FROM proposals WHERE action = $1 AND dedup_key LIKE 'academy.tutorial.%'
                ORDER BY dedup_key, created_at DESC""", ACTION)
    except Exception as exc:  # noqa: BLE001
        log.warning("tutorials: the runway could not be read: %s", type(exc).__name__)
        return {s.key: None for s in lessons}
    gone = {(str(r["series"]), int(r["episode"])) for r in rows}
    now = datetime.now(UTC)
    this_month = {str(r["series"]) for r in rows
                  if r.get("public") and not r.get("withdrawn")
                  and isinstance(r.get("at"), datetime)
                  and (r["at"].astimezone(UTC).year, r["at"].astimezone(UTC).month)
                  == (now.year, now.month)}
    cutoff = now - HELD_STALE
    for c in cards:
        parts = str(c["dedup_key"]).split(".")
        at = c["created_at"]
        if (len(parts) == 4 and parts[3].isdigit()
                and str(c["reason"] or "").startswith(HELD_PREFIX)
                and isinstance(at, datetime) and at < cutoff):
            gone.add((parts[2], int(parts[3])))
    left = {s.key: sum(1 for e in s.episodes
                       if e.scripted and (s.key, e.number) not in gone
                       and held(s.key, e) is None)
            for s in lessons}
    return {k: monthly_runway_days(n, k in this_month, now) if k in MONTHLY_SERIES else n
            for k, n in left.items()}


def runway_text(days: dict[str, int | None]) -> str:
    """"claude 2 · n8n 1 · local UNKNOWN"."""
    return " · ".join(f"{k} {'UNKNOWN' if n is None else n}" for k, n in days.items())


def runway_short(days: dict[str, int | None]) -> dict[str, int | None]:
    """The series under RUNWAY_WARN_DAYS, and every UNKNOWN one (a runway
    that cannot be read is never taken as long enough)."""
    return {k: n for k, n in days.items() if n is None or n < RUNWAY_WARN_DAYS}


async def warn_runway() -> str | None:
    """Once a day (UTC), one line to #atlas-academy-videos naming each series
    whose scripts run out within RUNWAY_WARN_DAYS days, or whose runway is
    UNKNOWN. The line said, or None. Never raises: the tick goes on."""
    try:
        short = runway_short(await runway())
        at = datetime.now(UTC)                         # invariant 6: the read time
        if not short or await asyncio.to_thread(_already_told, RUNWAY_NOTE):
            return None
        parts = [f"{k} UNKNOWN" if n is None else f"{k} {n} day{'' if n == 1 else 's'}"
                 for k, n in short.items()]
        line = (f"Academy script runway under {RUNWAY_WARN_DAYS} days (read "
                f"{at:%a %H:%M} UTC): {', '.join(parts)}.")
        await _tell(line)
        await asyncio.to_thread(_mark_told, RUNWAY_NOTE)
        return line
    except Exception:
        log.exception("tutorials: the runway warning failed")
        return None


def _told_path() -> Path:
    return root() / "told-today.json"


def _already_told(note: str) -> bool:
    """Whether `note` was already said in the channel today (UTC). A file,
    not memory: every deploy after the hour runs the tick again, and the
    channel hears each note once a day (Slack policy: less chatter)."""
    try:
        got = json.loads(_told_path().read_text())
    except (OSError, ValueError):
        return False
    return (isinstance(got, dict) and got.get("day") == datetime.now(UTC).date().isoformat()
            and note in (got.get("notes") or []))


def _mark_told(note: str) -> None:
    day = datetime.now(UTC).date().isoformat()
    try:
        got = json.loads(_told_path().read_text())
    except (OSError, ValueError):
        got = {}
    notes = list(got.get("notes") or []) if isinstance(got, dict) and got.get("day") == day else []
    notes.append(note)
    try:
        _told_path().parent.mkdir(parents=True, exist_ok=True)
        _told_path().write_text(json.dumps({"day": day, "notes": notes}))
    except OSError as exc:
        log.warning("tutorials: could not keep today's notes: %s", exc)


async def finish_thumbnails() -> list[str]:
    """Set the thumbnail on any approved, posted episode that went up
    without one. YouTube answers 403 to thumbnails.set until the channel is
    phone-verified (youtube.com/verify) -- both first videos went public
    that way on 2026-10-11 -- so this retries daily and finishes them the
    day verification lands. It completes a publish Miro already approved;
    it never posts anything new."""
    from ...kernel.state import execute_sql, fetch_all
    from ..youtube import api
    rows = await fetch_all(
        """SELECT id, series, episode, video_id FROM academy_tutorial_posts
           WHERE NOT withdrawn AND NOT thumb_set AND video_id IS NOT NULL""")
    out: list[str] = []
    for row in rows:
        got = resolve(str(row["series"]), int(row["episode"]))
        if isinstance(got, str):
            continue
        s, ep = got
        thumb = thumb_file(episode_dir(s, ep))
        if not thumb.is_file():
            continue
        done, why = await api.set_thumbnail(channel(), str(row["video_id"]), str(thumb))
        if done is None:
            out.append(f"{s.key} #{ep.number}: thumbnail still not set: {why}")
            continue
        await execute_sql(
            "UPDATE academy_tutorial_posts SET thumb_set = TRUE, detail = detail || $2 "
            "WHERE id = $1", int(row["id"]), " | thumbnail set later")
        out.append(f"{s.key} #{ep.number}: thumbnail set")
    return out


BACKFILL_PER_HOUR = 3                # new thumbnails rendered for live videos, per hour


def _backfill_path() -> Path:
    return root() / "thumb-backfill.json"


async def backfill_thumbnails(now: datetime | None = None) -> list[str]:
    """Render the new thumbnail (tutorial_thumbs.NEXT_VERSION) for each
    public tutorial, at most BACKFILL_PER_HOUR an hour, each video once
    (thumb-backfill.json records which got it), next to its render as
    thumb-v<N>.png. On since the combined video-system bump
    (tutorial_thumbs.BACKFILL, 2026-10-18).

    It SETS nothing: changing a live video's thumbnail is a public write,
    and these drawn ones have no catalogued action (academy.publish_tutorial
    and autopublish_tutorial set one only on a first publish; the one that
    sets a posted video's thumbnail, academy.apply_notebooklm_thumbnail,
    takes only NotebookLM's). Until one exists they wait on disk."""
    if not thumbs.BACKFILL:
        return []
    from ...kernel.state import fetch_all
    now = now or datetime.now(UTC)
    path = _backfill_path()
    try:
        record = json.loads(await asyncio.to_thread(path.read_text))
    except (OSError, ValueError):
        record = {}
    done: dict = dict(record.get("done") or {})
    recent = [x for x in record.get("at") or []
              if now - datetime.fromisoformat(x) < timedelta(hours=1)]
    budget = BACKFILL_PER_HOUR - len(recent)
    rows = await fetch_all(
        """SELECT series, episode, video_id FROM academy_tutorial_posts
           WHERE public AND NOT withdrawn AND video_id IS NOT NULL ORDER BY id""")
    todo = [r for r in rows if done.get(str(r["video_id"])) != thumbs.NEXT_VERSION]
    out: list[str] = []
    if budget <= 0 or not todo:
        return out
    async with Shooter() as shooter:
        for row in todo:
            if budget <= 0:
                break
            got = resolve(str(row["series"]), int(row["episode"]))
            if isinstance(got, str):
                continue
            s, ep = got
            d = episode_dir(s, ep)
            m = read_manifest(d)
            badge = m.badge if m is not None else ""
            target = d / f"thumb-v{thumbs.NEXT_VERSION}.png"
            await asyncio.to_thread(d.mkdir, parents=True, exist_ok=True)
            with tempfile.TemporaryDirectory() as work:
                why = await thumbs.render(ep, shooter, str(target), level=ep.level,
                                          badge=badge, work=work)
            if why is not None:
                out.append(f"{s.key} #{ep.number}: new thumbnail not drawn: {why}")
                continue
            done[str(row["video_id"])] = thumbs.NEXT_VERSION
            recent.append(now.isoformat())
            budget -= 1
            out.append(f"{s.key} #{ep.number}: new thumbnail drawn ({target.name}); not set: "
                       "no catalogued action sets a live video's thumbnail")
    await asyncio.to_thread(path.parent.mkdir, parents=True, exist_ok=True)
    await asyncio.to_thread(path.write_text, json.dumps({"done": done, "at": recent}))
    return out


# A draft whose card can no longer publish what it shows: the card failed
# or was superseded after an approval (ready() refused a render of an older
# script), or it is still waiting but the script or look changed under it.
# 2026-10-12, card #364: a design deploy landed between the card and Miro's
# tap, and the episode then counted as drafted forever.
REDRAFT_STATUSES = ("failed", "superseded")


async def _stale_drafts() -> list[tuple[int, str, int, str]]:
    """(proposal id, series, episode, its status) for each unposted draft
    whose latest card is pending on an older render, or failed/superseded."""
    from ...kernel.state import fetch_all
    rows = await fetch_all(
        """SELECT DISTINCT ON (r.series, r.episode)
                  r.series, r.episode, p.id, p.status
             FROM academy_tutorial_reviews r JOIN proposals p ON p.id = r.proposal_id
            WHERE NOT EXISTS (SELECT 1 FROM academy_tutorial_posts t
                               WHERE t.series = r.series AND t.episode = r.episode)
            ORDER BY r.series, r.episode, r.at DESC""")
    out: list[tuple[int, str, int, str]] = []
    for row in rows:
        key, n, status = str(row["series"]), int(row["episode"]), str(row["status"])
        if status in REDRAFT_STATUSES:
            out.append((int(row["id"]), key, n, status))
            continue
        if status != "pending":
            continue
        got = resolve(key, n)
        if isinstance(got, str):
            continue
        s, ep = got
        m = await asyncio.to_thread(read_manifest, episode_dir(s, ep))
        if m is not None and getattr(m, "audio", "") == "notebooklm":
            from . import notebooklm
            if m.source_sha256 != notebooklm.source_sha(ep):
                out.append((int(row["id"]), key, n, status))
            continue
        if m is None or (m.script and m.script != script_key(ep)):
            out.append((int(row["id"]), key, n, status))
    return out


async def redraft_stale() -> list[str]:
    """Retire each stale draft card and draft the episode again: render the
    current script (through the executor) and raise a fresh card. A waiting
    card is marked 'superseded' (terminal, never approvable) only while it
    is still pending, so a tap that already claimed it is not overridden. A
    declined or expired draft is left alone: no is no, and silence is not
    a reason to ask again the same day."""
    from ...kernel.audit import audit
    from ...kernel.executor import ExecutionRequest, execute
    from ...kernel.state import execute_sql
    from . import notebooklm
    out: list[str] = []
    for pid, key, n, status in await _stale_drafts():
        # A render that refused is not tried again the same day (said once).
        # Marked only on a refusal, so a deploy that restarts the box mid-
        # render (2026-10-12, agents #1) tries again on the next run.
        note = f"redraft {key} #{n} card #{pid}"
        if await asyncio.to_thread(_already_told, note):
            continue
        got = resolve(key, n)
        nlm = not isinstance(got, str) and await asyncio.to_thread(notebooklm_current, *got)
        if not nlm and not await asyncio.to_thread(notebooklm.fallback_enabled):
            # 2026-10-19: no Fish render; the card waits for NotebookLM's.
            out.append(await _waiting(key, n))
            continue
        if status == "pending":
            await execute_sql(
                "UPDATE proposals SET status = 'superseded' "
                "WHERE id = $1 AND status = 'pending'", pid)
        await audit("academy.tutorial.redraft", ACTION,
                    {"id": pid, "series": key, "episode": n, "was": status})
        rendered = nlm or (not isinstance(got, str)
                           and await asyncio.to_thread(reusable, *got))
        if not rendered:
            made = await execute(ExecutionRequest(
                action="academy.render_tutorial", params={"series": key, "episode": n},
                reason=f"card #{pid} showed an older render of {key} #{n}"))
            if not made.ok:
                await asyncio.to_thread(_mark_told, note)
                out.append(f"{key} #{n}: re-render refused: {made.detail}")
                await _tell(f"Academy draft #{pid} ({key} #{n}) is out of date and "
                            f"could not be rendered again: {made.detail}")
                continue
        out.append(f"{key} #{n} (was card #{pid}): {await review_tutorial(key, n)}")
    return out


# 2026-10-16: academy.render_tutorial runs at most twice an hour (the
# executor's rolling window, in memory) and the academy module ten times.
# A day with six series to render was refused from the third on, told to
# Slack as a failure and never retried. An episode the ceiling held now
# waits for the next hour (scheduler: catch_up once retry_due), said once a
# day; any other refusal is told and carded as before.
RETRY_AFTER = timedelta(minutes=61)    # the rolling window, and a minute
CEILING_NOTE = "tutorial hourly ceiling"
# (when the ceiling first held this run, what it held); None: nothing held.
_deferred: tuple[datetime, list[tuple[str, int]]] | None = None


def _clock() -> datetime:
    return datetime.now(UTC)


def deferred() -> list[tuple[str, int]]:
    """The episodes the hourly ceiling held on the last run."""
    return list(_deferred[1]) if _deferred is not None else []


def retry_due(now: datetime) -> bool:
    """Whether a held episode can be tried again: the same UTC day, and the
    rolling hour that refused it is over (every run it counted started
    within the hour before the refusal). Another day's run takes it up."""
    if _deferred is None or not _deferred[1]:
        return False
    at = _deferred[0]
    return (now.astimezone(UTC).date() == at.astimezone(UTC).date()
            and now - at >= RETRY_AFTER)


def _over_ceiling(result: ActionResult) -> bool:
    """Refused by an hourly ceiling (the action's, the module's or the
    global one) before it ran: a wait, not a failure."""
    return result.failure_class is FailureClass.BUDGET and result.dispatched is False


def notebooklm_current(s: Series, ep: Episode) -> bool:
    """Whether the episode has an ingested NotebookLM render of its script as
    it is now. Blocking."""
    from . import notebooklm
    return notebooklm.current(episode_dir(s, ep), ep)


async def old_design_refusal(series: str, episode: int) -> str | None:
    """Why this episode may not go public: Miro, 2026-10-19, only NotebookLM
    renders post. None when its current render is NotebookLM's, or when
    ACADEMY_NOTEBOOKLM_FALLBACK is on. Every lesson-posting action asks,
    not only the daily pass that calls them."""
    from . import notebooklm
    got = resolve(series, int(episode))
    if isinstance(got, str):
        return got
    if (await asyncio.to_thread(notebooklm_current, *got)
            or await asyncio.to_thread(notebooklm.fallback_enabled)):
        return None
    return (f"not posted: {notebooklm.WAITING}; NotebookLM only "
            "(ACADEMY_NOTEBOOKLM_FALLBACK is off)")


async def _waiting(key: str, n: int) -> str:
    """The note for an episode with no NotebookLM render while the Fish
    fallback is off, said in the drafts channel once a day."""
    note = f"{key} #{n}: waiting on NotebookLM render"
    if not await asyncio.to_thread(_already_told, note):
        await _tell(f"Academy: {note}; nothing posted for that series today "
                    "(NotebookLM only: ACADEMY_NOTEBOOKLM_FALLBACK is off).")
        await asyncio.to_thread(_mark_told, note)
    return note


async def _post(todo: list[tuple[str, int]]) -> list[str]:
    """Render and post (or draft) each episode in turn, one render at a time:
    execute() awaits the run, so nothing overlaps on the one CPU.

    Miro, 2026-10-19: an episode's ingested NotebookLM render is the one
    posted. Without one, the episode is skipped and the channel told
    "waiting on NotebookLM render" -- never rendered, and never a Fish render
    already on disk posted -- unless the fallback is on (notebooklm.fallback_enabled)."""
    global _deferred
    from ...kernel.executor import ExecutionRequest, execute
    from . import notebooklm
    said: list[str] = []
    held: list[tuple[str, int]] = []
    first: datetime | None = None
    for key, n in todo:
        got = resolve(key, n)
        nlm = not isinstance(got, str) and await asyncio.to_thread(notebooklm_current, *got)
        if not nlm and not await asyncio.to_thread(notebooklm.fallback_enabled):
            said.append(await _waiting(key, n))
            continue
        # A render of this very script, look and voice speed is not made
        # again; an older one is.
        rendered = nlm or (not isinstance(got, str)
                           and await asyncio.to_thread(reusable, *got))
        if not rendered:
            made = await execute(ExecutionRequest(
                action="academy.render_tutorial", params={"series": key, "episode": n},
                reason=f"today's Academy tutorial for {key}: #{n}"))
            if _over_ceiling(made):
                held.append((key, n))
                first = first or _clock()
                continue
            if not made.ok:
                said.append(f"{key} #{n}: render refused: {made.detail}")
                await _tell(f"Academy draft not made for {key} #{n}: {made.detail}")
                continue
        if autopublish_enabled():
            posted_ = await execute(ExecutionRequest(
                action="academy.autopublish_tutorial", params={"series": key, "episode": n},
                reason=f"today's Academy tutorial for {key}: #{n}"))
            if _over_ceiling(posted_):
                held.append((key, n))
                first = first or _clock()
                continue
            if not posted_.ok and posted_.dispatched is False:
                # Refused before it ran (watch-only, a pause): no card came
                # from autopublish_tutorial, so it is raised here -- never
                # silently dropped (2026-10-15).
                posted_ = await _card(key, n, posted_.detail[:300])
            said.append(f"{key} #{n}: {posted_.detail}")
        else:
            said.append(f"{key} #{n}: {await review_tutorial(key, n)}")
    _deferred = (first, held) if first else None
    if held:
        names = ", ".join(f"{k} #{n}" for k, n in held)
        said.append(f"{names}: held by the hourly ceiling; trying again next hour")
        if not await asyncio.to_thread(_already_told, CEILING_NOTE):
            await _tell(f"Academy: the hourly ceiling held {names}; each is tried "
                        "again next hour, one render at a time.")
            await asyncio.to_thread(_mark_told, CEILING_NOTE)
    return said


async def daily() -> str:
    """One video per series per day (Miro, 2026-10-11): each series' next
    scripted episode, the series posted longest ago first, rendered one
    after another (one CPU). Since 2026-10-15 each posts itself through the
    Tier 1 academy.autopublish_tutorial once every gate passes (capped
    across series by ACADEMY_TUTORIALS_PER_DAY); a failed gate is a card in
    #atlas-academy-videos. With ACADEMY_TUTORIALS_AUTOPUBLISH=off, each is
    a draft card instead. A series whose next episode has no script is
    skipped, and the channel is told once a day. What the hourly ceiling
    held is tried again by catch_up()."""
    global _deferred
    try:
        for line in await finish_thumbnails():
            log.info("academy thumbnails: %s", line)
    except Exception:
        log.exception("academy thumbnails: the retry pass failed")
    try:
        for line in await backfill_thumbnails():
            log.info("academy thumbnail backfill: %s", line)
    except Exception:
        log.exception("academy thumbnails: the backfill pass failed")
    # 2026-10-19: the Mac runner's NotebookLM renders in first, so a redraft
    # and today's post can take one that arrived since yesterday.
    try:
        from . import notebooklm
        for line in await notebooklm.ingest_pending():
            log.info("academy notebooklm: %s", line)
    except Exception:
        log.exception("academy notebooklm: the ingest pass failed")
    # Their Shorts (notebooklm_shorts.py) after them: made here, posted in
    # the teaching Short's slot (teaching_pass).
    try:
        from . import notebooklm_shorts
        for line in await notebooklm_shorts.ingest_pending():
            log.info("academy notebooklm short: %s", line)
    except Exception:
        log.exception("academy notebooklm: the Shorts ingest pass failed")
    # 2026-10-20: NotebookLM thumbnails for lessons already posted
    # (notebooklm_thumbs.py), each through the executor.
    # 2026-10-20: then one posted lesson still on the drawn card gets a
    # thumbnail made from a frame of its own video, dropped into that inbox
    # so the pass below sets it through the executor.
    try:
        from . import notebooklm_thumbs
        for line in await notebooklm_thumbs.frame_backfill():
            log.info("academy video-frame thumbnail: %s", line)
    except Exception:
        log.exception("academy notebooklm: the video-frame thumbnail pass failed")
    try:
        from . import notebooklm_thumbs
        for line in await notebooklm_thumbs.apply_pending():
            log.info("academy notebooklm thumbnail: %s", line)
    except Exception:
        log.exception("academy notebooklm: the thumbnails pass failed")
    try:
        for line in await redraft_stale():
            log.info("academy redraft: %s", line)
    except Exception:
        log.exception("academy redraft: the stale-draft pass failed")
    # Before the early return below: a day with nothing left to draft is the
    # day the runway matters most.
    await warn_runway()
    said: list[str] = []
    done, today, last, month = await _done()
    todo, notes = lineup(done, today, last, month)
    for note in notes:
        said.append(note)
        if not await asyncio.to_thread(_already_told, note):
            await _tell(f"Academy: {note}; nothing rendered for that series today.")
            await asyncio.to_thread(_mark_told, note)
    if not todo:
        _deferred = None
        said.insert(0, "today's drafts are already made" if any(today.values())
                    else "no scripted episode is left to draft")
        await _lab_playlist()
        return "; ".join(said + await _pins())
    said += await _post(todo)
    # After the day's uploads (~1600 quota units each): the lab playlist's
    # changes (~53 each) never spend what a post needs (review of 3108ed1e).
    await _lab_playlist()
    # Teaching Shorts left this tick (Miro, 2026-10-13: two Shorts a day): a
    # 17 UTC teaching Short spent the 23 UTC reel's slot every day. They have
    # their own slot now (teaching_tick).
    return "; ".join(said + await _pins())


async def catch_up() -> str:
    """The episodes the hourly ceiling held, still due today, and nothing
    else: no thumbnail, redraft or comment pass, and an episode held on a
    card or refused is not tried again today. The held list is restamped
    first, so a run that raises midway waits another hour rather than
    starting again every scheduler cycle."""
    global _deferred
    held = set(deferred())
    if not held:
        return "nothing was held by the hourly ceiling"
    _deferred = (_clock(), deferred())
    done, today, last, month = await _done()
    todo, _notes = lineup(done, today, last, month)
    todo = [t for t in todo if t in held]
    if not todo:
        _deferred = None
        return "what the hourly ceiling held is already done"
    return "; ".join(await _post(todo))


async def _lab_playlist() -> None:
    """Miro, 2026-10-22: every public lab also in the one lab playlist
    (lab_playlist.py), each change through the executor. Never raises."""
    try:
        from . import lab_playlist
        for line in await lab_playlist.lab_playlist_backfill():
            log.info("academy lab playlist: %s", line)
    except Exception:
        log.exception("academy lab playlist: the pass failed")


async def _pins() -> list[str]:
    """pin_pass, never stopping the tick."""
    try:
        return await pin_pass()
    except Exception:
        log.exception("academy comments: the pin pass failed")
        return ["the comment pass failed (see the log)"]


# ------------------------------------------------------------ teaching Shorts
#
# 2026-10-13 (growth plan task 7, part B): one teaching Short a day, cut
# from a public tutorial (tutorial_short.py decides where and what to say).
# The render is Tier 1 (a file on our own disk and a content_media row);
# the post is clips.autopublish_promo's, gated there, CLIPS_PROMO_PER_DAY
# counting every Short the channel posts. The draft's source carries the
# slug, so publish_promo records the post under it and a cut is never
# posted twice.

ACTION_TEACH = "academy.render_teaching_short"
TEACH_MAX_SECONDS = tutorial_short.CUT_MAX   # 37: the closing card takes the Short to 40


def teaching_slug(s: Series, ep: Episode, kind: str) -> str:
    return f"tutorial:{s.key}:{ep.number}:{kind}"


def teaching_tags(slug: str) -> list[str] | None:
    """A teaching Short's YouTube tags (2026-10-18): its lesson's upload tags
    (upload_tags: the channel's, the series' and the lesson's own), kept in
    order while they fit TAGS_LIMIT as tags_cost counts them. None when
    `slug` is not a teaching Short's (teaching_slug) of a catalogued lesson."""
    parts = slug.split(":")
    if len(parts) != 4 or parts[0] != "tutorial":
        return None
    got = resolve(parts[1], parts[2])
    if isinstance(got, str):
        return None
    out: list[str] = []
    for tag in upload_tags(*got):
        if tags_cost([*out, tag]) <= TAGS_LIMIT:
            out.append(tag)
    return out


def teaching_caption(s: Series, ep: Episode, cut: tutorial_short.Cut,
                     video_id: str) -> str:
    """The post's text, TikTok's and YouTube's alike: tutorial_short's hook,
    link and hashtags, the synthetic-voice line (Miro 2026-10-17), then the
    AI label every promo caption ends with."""
    return f"{tutorial_short.caption(s, ep, cut, video_id)}\n{VOICE_SHORT}\n{AI_LABEL}"


# Miro, 2026-10-19: every video on the channel is NotebookLM's. A teaching
# cut always draws ATLAS's own portrait pages and a closing card naming the
# lesson over the cut (short_frames), so no cut is NotebookLM's alone, even
# of a NotebookLM lesson: none is made while the fallback is off.
TEACHING_OFF = ("no teaching Short: every teaching cut draws ATLAS pages and a "
                "closing card over the lesson (the old design); Academy Shorts come "
                "from the NotebookLM Shorts inbox (ACADEMY_NOTEBOOKLM_FALLBACK is off)")


def vet_teaching(params: dict) -> str | None:
    if (why := vet(params)) is not None:
        return why
    kind = str(params.get("kind") or "")
    if kind not in tutorial_short.KINDS:
        return f"kind must be one of {', '.join(tutorial_short.KINDS)}, not {kind!r}"
    return None


async def _teaching_cut(series: str, episode: int, kind: str
                        ) -> tuple[Series, Episode, str, Path, tutorial_short.Cut] | str:
    """The public episode, its video id, its render and the cut, or why not.
    An unknown kind is refused before anything is read."""
    if kind not in tutorial_short.KINDS:
        return f"kind must be one of {', '.join(tutorial_short.KINDS)}, not {kind!r}"
    from . import notebooklm
    if not await asyncio.to_thread(notebooklm.fallback_enabled):
        return TEACHING_OFF
    got = await _public_video(series, episode)
    if isinstance(got, str):
        return got
    s, ep, vid = got
    d = episode_dir(s, ep)
    m = await asyncio.to_thread(read_manifest, d)
    if m is None:
        return f"{s.key} #{ep.number} has no render manifest"
    if m.audio == "notebooklm":
        # 2026-10-19: its Short is NotebookLM's own (notebooklm_shorts.py).
        return (f"{s.key} #{ep.number} is a NotebookLM render; no teaching Short is "
                "cut (its Short comes from the NotebookLM Shorts inbox)")
    video = d / "video.mp4"
    if (why := await asyncio.to_thread(publishable_render, str(video))) is not None:
        return f"{s.key} #{ep.number}: {why}"
    cut = next((c for c in tutorial_short.cuts(m, ep) if c.kind == kind), None)
    if cut is None:
        return f"{s.key} #{ep.number} has no {kind} cut in its render"
    if all(sh.scene is None for sh in tutorial_short.shots(m, ep, cut)):
        # 2026-10-17: a Short draws the scene steps it cuts; a render whose
        # lines name none would be a frame with nothing in it
        return (f"{s.key} #{ep.number}: no scene step is said in its {kind} cut; "
                f"{RERENDER}")
    # cuts() already holds every cut to CUT_MAX at whole lines; this only
    # guards the 40 s, keeping the end (the answer).
    cut = replace(cut, start=max(cut.start, cut.end - TEACH_MAX_SECONDS))
    return s, ep, vid, video, cut


async def render_teaching_short_dry(series: str, episode: int, kind: str) -> ActionResult:
    got = await _teaching_cut(series, episode, kind)
    if isinstance(got, str):
        return ActionResult(False, got)
    s, ep, vid, _, cut = got
    return ActionResult(True, f"would cut {cut.start:.1f}-{cut.end:.1f} s "
                              f"(\"{cut.caption}\") of {s.key} #{ep.number} (video {vid}) "
                              f"into a 1080x1920 Short, slug "
                              f"{teaching_slug(s, ep, cut.kind)}")


async def render_teaching_short(series: str, episode: int, kind: str) -> ActionResult:
    """One teaching Short as a finished content_media row, the way the
    Academy reel's renders are, so clips.autopublish_promo takes it."""
    from ...kernel.state import fetch_one
    from ..content import ffmpeg, render
    got = await _teaching_cut(series, episode, kind)
    if isinstance(got, str):
        return ActionResult(False, got)
    s, ep, vid, video, cut = got
    if (why := ffmpeg.missing()) is not None:
        return ActionResult(False, why)
    slug = teaching_slug(s, ep, cut.kind)
    text = teaching_caption(s, ep, cut, vid)
    row = await fetch_one(
        """INSERT INTO content_drafts (kind, source, title, body)
           VALUES ($1, $2, $3, $4) RETURNING id""",
        "academy_promo", f"academy:{slug}",
        f"{ep.title} ({cut.kind}) {tutorial_short.look_tag()}", text)
    draft_id = int(row["id"])
    media_id = await render._new_row(draft_id, "academy")
    # Fix round 1 (2026-10-13): whatever goes wrong past this line, the row
    # is marked failed, never left at 'rendering'.
    try:
        with tempfile.TemporaryDirectory(prefix="academy-teach-") as work:
            out = os.path.join(work, "out.mp4")
            # 2026-10-15 (the motion guide): the lesson's own words as
            # phrase-level captions, when the render has their timings; a
            # build whose ffmpeg cannot burn them cuts without, and the
            # record says which. 2026-10-17: over the cut's pages drawn
            # natively at 1080x1920 (short_frame), then the closing card.
            m = await asyncio.to_thread(read_manifest, episode_dir(s, ep))
            if m is None:
                return await render._fail(media_id, "the render manifest went away")
            pages = video_qa.Pages()
            frames, close = await short_frames(ep, m, cut, work, pages=pages)
            ass = tutorial_short.caption_ass(m, cut)
            burned = False
            if ass:
                path = os.path.join(work, "captions.ass")
                await asyncio.to_thread(Path(path).write_text, ass)
                try:
                    await ffmpeg.run(short_argv(str(video), cut.start, cut.end, out,
                                                frames=frames, close=close, ass=path,
                                                hole=demo_hole_of(m)))
                    burned = True
                except ffmpeg.FfmpegError as exc:
                    log.warning("teaching Short captions not burned: %s", exc)
            if not burned:
                await ffmpeg.run(short_argv(str(video), cut.start, cut.end, out,
                                            frames=frames, close=close,
                                            hole=demo_hole_of(m)))
            # Batch 9: the finished Short's encoding, length and drawn pages;
            # a Short that fails one is held (its row failed), never posted.
            if (why := await video_qa.short_refusal(out, pages)) is not None:
                return await render._fail(media_id, f"QA: {why}")
            note = (f"{cut.end - cut.start:.0f} s from {s.key} #{ep.number}'s "
                    f"\"{cut.caption}\" ({cut.start:.1f}-{cut.end:.1f} s) and "
                    f"{tutorial_short.CLOSE_SECONDS:g} s naming the lesson, "
                    f"hook \"{cut.hook}\", {len(frames)} portrait page(s), "
                    + ("phrase captions burned" if burned else "no captions burned"))
            result = await render._finish(media_id, draft_id, "academy", out, [note],
                                          captions=burned)
    except ffmpeg.FfmpegError as exc:
        return await render._fail(media_id, f"ffmpeg: {exc}")
    except Exception as exc:  # noqa: BLE001
        return await render._fail(media_id, f"{type(exc).__name__}: {exc}"[:300])
    if not result.ok:
        return result
    return ActionResult(True, f"{slug}: {result.detail}",
                        evidence={**(result.evidence or {}), "slug": slug,
                                  "caption": text, "youtube_caption": text},
                        rollback_token=result.rollback_token)


async def unrender_teaching_short(token: str) -> ActionResult:
    """Mark the render failed and drop its file (reel.py's rollback): a
    withdrawn render on our own disk, nothing public."""
    from . import reel
    return await reel.unrender_promo_reel(token)


def _kinds_by_episode(rows: list) -> dict[tuple[str, int], list[str]]:
    """tutorial:<series>:<episode>:<kind> slugs, grouped by episode."""
    out: dict[tuple[str, int], list[str]] = {}
    for r in rows:
        parts = str(r["slug"]).split(":")
        if len(parts) == 4 and parts[2].isdigit():
            out.setdefault((parts[1], int(parts[2])), []).append(parts[3])
    return out


async def untaught() -> list[dict]:
    """Public, live tutorials, oldest first, each with the kinds already
    posted as teaching Shorts (academy_promo_posts.slug) and the kinds
    passed over until a later day (academy_teaching_skips, final review I2)."""
    from ...kernel.state import fetch_all
    rows = await fetch_all(
        """SELECT series, episode, min(at) AS at
             FROM academy_tutorial_posts WHERE public AND NOT withdrawn
            GROUP BY series, episode ORDER BY at""")
    taught = _kinds_by_episode(await fetch_all(
        "SELECT DISTINCT slug FROM academy_promo_posts WHERE slug LIKE 'tutorial:%'"))
    skipped = _kinds_by_episode(await fetch_all(
        "SELECT slug FROM academy_teaching_skips WHERE until > now()"))
    out = []
    for r in rows:
        ep = (str(r["series"]), int(r["episode"]))
        out.append({**dict(r), "taught": taught.get(ep, []),
                    "skipped": skipped.get(ep, [])})
    return out


async def skip_teaching(slug: str, why: str) -> None:
    """Pass this cut over for seven days (final review I2): its render was refused or its
    post was carded. Our own table, best effort: a failed write only means
    the cut is looked at again tomorrow."""
    from ...kernel.state import execute_sql
    try:
        await execute_sql(
            """INSERT INTO academy_teaching_skips (slug, until, why)
               VALUES ($1, now() + interval '7 days', $2)
               ON CONFLICT (slug) DO UPDATE
                 SET until = EXCLUDED.until, why = EXCLUDED.why""",
            slug, why[:300])
    except Exception:
        log.exception("academy teaching Short: the skip for %s was not written", slug)


# Attempts that reach the executor in one run (a render, or a post of a
# finished render). Looking is free (2026-10-15): the oldest public
# tutorials predate the payoff and the timings, and refusing them used to
# spend the whole budget every day before a newer render was reached.
TEACH_CANDIDATES = 3


async def rendered_teaching(slug: str) -> int | None:
    """A finished render of this cut in today's look whose file is still
    there, or None: a cut held by the day's cap or carded is posted from
    it, not re-made. A render in an older look (its draft title does not
    end on tutorial_short.look_tag()) is cut again; the source keeps the
    slug, so what was posted stays posted."""
    from ...kernel.state import fetch_one
    row = await fetch_one(
        """SELECT m.id, m.path FROM content_media m
             JOIN content_drafts d ON d.id = m.draft_id
            WHERE d.source = $1 AND m.status = 'done' AND d.title LIKE $2
            ORDER BY m.id DESC LIMIT 1""", f"academy:{slug}",
        f"%{tutorial_short.look_tag()}")
    if row is None or not row["path"]:
        return None
    if not await asyncio.to_thread(os.path.isfile, str(row["path"])):
        return None
    return int(row["id"])


async def _next_cut(row: dict) -> tuple[Series, Episode, str, tutorial_short.Cut] | str:
    """This episode's first kind not yet posted that can render now (the
    same read-only checks the render makes), or why there is none."""
    key, n = str(row["series"]), int(row["episode"])
    done = set(row.get("taught") or ()) | set(row.get("skipped") or ())
    why = f"{key} #{n}: every cut is posted or passed over"
    for kind in tutorial_short.KINDS:
        if kind in done:
            continue
        got = await _teaching_cut(key, n, kind)
        if isinstance(got, str):
            why = got
            continue
        s, ep, vid, _, cut = got
        return s, ep, vid, cut
    return why


async def teaching_pass() -> list[str]:
    """At most one teaching Short a run: the oldest public tutorial with a
    cut not yet posted (payoff, then stuck, then quiz) that can render,
    posted through the executor. Nothing is rendered once the day's promo
    slots are used (fix round 1); a finished render of the cut is posted
    instead of a new one; an episode whose cut cannot render, or whose
    caption the brand guard would card every day, is passed over -- both
    read-only, so every row is walked (2026-10-15). At most TEACH_CANDIDATES
    attempts reach the executor a run. A cut whose render is refused, or
    whose post is carded for anything but the day's cap, rests a week
    (skip_teaching) and the next candidate is tried."""
    from ...kernel.executor import ExecutionRequest, execute
    from ..clips import promo
    from ..content import guard
    if await promo.posted_today() >= promo.per_day():
        return ["teaching Short held: the day's promo slots are used"]
    # 2026-10-19: a NotebookLM lesson has no cut (_teaching_cut); its Short
    # comes from the Mac runner (notebooklm_shorts.py) and goes first.
    from . import notebooklm_shorts
    said, stop = await notebooklm_shorts.post_next()
    if stop:
        return said
    # Miro, 2026-10-19: NotebookLM videos only. The teaching cuts below are
    # Fish, old-design Shorts: they run only with the switch on.
    from . import notebooklm
    if not await asyncio.to_thread(notebooklm.fallback_enabled):
        return [*said, TEACHING_OFF]
    rows = [r for r in await untaught()
            if not set(tutorial_short.KINDS) <= (set(r.get("taught") or ())
                                                 | set(r.get("skipped") or ()))]
    tries = 0
    for row in rows:
        if tries >= TEACH_CANDIDATES:
            break
        got = await _next_cut(row)
        if isinstance(got, str):
            log.info("academy teaching Short: %s", got)
            continue
        s, ep, vid, cut = got
        key, n = s.key, ep.number
        # One video-id source for the caption: _public_video, as the render.
        text = teaching_caption(s, ep, cut, vid)
        if (why := guard.refusal(text)) is not None:
            said.append(f"{key} #{n} teaching Short skipped: {why}")
            continue
        slug = teaching_slug(s, ep, cut.kind)
        tries += 1                   # the refusals above were read-only; this acts
        media_id = await rendered_teaching(slug)
        if media_id is None:
            made = await execute(ExecutionRequest(
                action=ACTION_TEACH,
                params={"series": key, "episode": n, "kind": cut.kind},
                reason=f"today's teaching Short: {slug}"))
            if not made.ok:
                # Final review I2: rested a week, so it no longer heads the
                # queue every day; the next candidate is tried now.
                said.append(f"{slug}: render refused: {made.detail}")
                await skip_teaching(slug, f"render refused: {made.detail}")
                continue
            ev = made.evidence or {}
            media_id = int(ev.get("media_id") or 0)
            text = str(ev.get("caption") or text)
        params: dict[str, object] = {"media_id": media_id, "caption": text,
                                     "slug": slug, "youtube_caption": text}
        posted_ = await execute(ExecutionRequest(
            action="clips.autopublish_promo", params=params,
            reason=f"today's teaching Short: {slug}"))
        said.append(f"{slug}: {posted_.detail}")
        if posted_.detail.startswith("held (") and promo.CAP_HELD not in posted_.detail:
            # Carded (not the day's cap): Miro has the card; rest the cut a
            # week and try the next candidate. A cap hold ends the run.
            await skip_teaching(slug, posted_.detail)
            continue
        if posted_.ok and not posted_.detail.startswith("held ("):
            # Miro 2026-10-17: the AI flag on the Short (academy.synthetic);
            # never raises, never holds the post; the daily catch-up retries.
            from . import synthetic
            if flagged := await synthetic.after_post(slug):
                said.append(flagged)
        return said
    return said


async def _teach() -> list[str]:
    """teaching_pass, never stopping the tick."""
    try:
        return await teaching_pass()
    except Exception:
        log.exception("academy teaching Shorts: the pass failed")
        return ["the teaching Short pass failed (see the log)"]


# Miro, 2026-10-13: two Shorts a day (CLIPS_PROMO_PER_DAY stays 2). The
# engine posts a lesson Short at 16 UTC and a reel at 23 UTC; the teaching
# Short goes for the 23 UTC slot on alternate New York days only, so the reel
# keeps the other half. The teaching tick is spawned before the reel's 23 UTC
# slot; the cut takes seconds, the reel render minutes, so the teaching Short
# normally takes the slot and the reel holds silently (no card,
# clips.promo.autopublish_promo); if the reel wins, the teaching Short waits
# two days.
DEFAULT_TEACHING_HOUR_UTC = 23


def teaching_hour_utc() -> int:
    try:
        return max(0, min(23, int(os.getenv("ACADEMY_TEACHING_HOUR_UTC",
                                            str(DEFAULT_TEACHING_HOUR_UTC)))))
    except ValueError:
        return DEFAULT_TEACHING_HOUR_UTC


def teaching_day(now: datetime) -> bool:
    """True on the New York days whose day-of-year is even: the teaching
    Short's days. New York's date, as clips.promo.day_start counts the cap."""
    return now.astimezone(NEW_YORK).timetuple().tm_yday % 2 == 0


async def teaching_tick() -> str:
    """The teaching Short's own scheduler slot (academy_teaching): one
    teaching_pass on a teaching day, nothing on the others; then, every day,
    the AI-flag catch-up (academy.synthetic) and the metadata refresh of
    already-public videos (academy.metadata, 23 UTC: clear of the 17 UTC
    lessons, so it never takes a post's hourly slot). Never raises."""
    from . import metadata, synthetic
    if not teaching_day(datetime.now(UTC)):
        said = "not a teaching day"
    else:
        said = "; ".join(await _teach()) or "no teaching Short to post"
    # Every day: the AI flag on the Academy Shorts of the last 14 days that
    # still lack it (Miro 2026-10-17). Never raises.
    if flagged := await synthetic.catch_up():
        said = f"{said}; {flagged}"
    # Every day (Miro 2026-10-18): a few public videos brought up to the
    # current tags and description, each once. Never raises.
    if refreshed := await metadata.catch_up():
        said = f"{said}; {refreshed}"
    return said


# ------------------------------------------------- Series 6: Shorts (planned)

def short_window(m: Manifest, chapter: str) -> tuple[float, float] | None:
    """The 20-40 s a Short is cut from: the named chapter of the long
    render, from its start, at most SHORT_MAX long. None when the chapter is
    not there or runs under SHORT_MIN."""
    names = [n for _, n in m.chapters]
    if chapter not in names:
        return None
    i = names.index(chapter)
    start = m.chapters[i][0]
    end = m.chapters[i + 1][0] if i + 1 < len(m.chapters) else m.seconds
    if end - start < SHORT_MIN:
        return None
    return round(start, 3), round(min(end, start + SHORT_MAX), 3)


@dataclass(frozen=True)
class ShortFrame:
    """One page of a teaching Short (short_frame), held from `start` to
    `end` seconds of the cut; a `demo` page has the long render's demo crop
    laid into its hole."""
    png: str
    start: float
    end: float
    demo: bool = False


def short_argv(src: str, start: float, end: float, out: str, *,
               frames: list[ShortFrame], close: str,
               ass: str | None = None,
               hole: tuple[int, int, int, int] = demos.HOLE) -> list[str]:
    """The teaching Short (ShortsFrame, 2026-10-17): its pages, each held
    for its seconds -- a demo's with the long render's demo crop (the
    recording's hole, `hole`: the render's own, demo_hole_of(m), already
    the step's box) scaled into short_frame.DEMO_BOX -- then the closing card for
    tutorial_short.CLOSE_SECONDS; `ass` (tutorial_short.caption_ass) burned
    over them in the vendored Plex. The sound is the long render's own for
    the cut, silence under the card: no music, nothing added. The hook is
    on the first page, so nothing fades in; picture and sound fade out at
    the very end. No zoom of its own: a demo's focus-zoom is the long
    render's, into the step's prepared box."""
    from ..content.ffmpeg import filter_path
    from . import short_frame
    fps = motion.FPS
    fonts = filter_path(str(video_tokens.LIBASS_FONTS_DIR))
    pages = [*frames, ShortFrame(close, end - start,
                                 end - start + tutorial_short.CLOSE_SECONDS)]
    # a page shorter than a frame is dropped: ffmpeg reads -t 0 as "no limit"
    pages = [f for f in pages if round(f.end * fps) > round(f.start * fps)]
    counts = [round(f.end * fps) - round(f.start * fps) for f in pages]
    total = sum(counts) / fps
    fade = tutorial_short.EDGE_FADE
    tail = max(0.0, total - fade)
    argv = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
            "-ss", f"{start:.3f}", "-to", f"{end:.3f}", "-i", src]
    for f, n in zip(pages, counts, strict=True):
        argv += ["-loop", "1", "-framerate", str(fps), "-t", f"{n / fps:.3f}", "-i", f.png]
    graph: list[str] = []
    demos_at = [i for i, f in enumerate(pages) if f.demo]
    if demos_at:
        graph.append(f"[0:v]split={len(demos_at)}"
                     + "".join(f"[s{i}]" for i in demos_at))
    dx, dy, dw, dh = short_frame.DEMO_BOX
    for i, f in enumerate(pages):
        page = f"[{i + 1}:v]fps={fps},format=yuv420p,setsar=1"
        if not f.demo:
            graph.append(f"{page}[v{i}]")
            continue
        graph.append(
            f"[s{i}]trim=start={f.start:.3f}:end={f.end:.3f},setpts=PTS-STARTPTS,"
            f"crop={hole[2]}:{hole[3]}:{hole[0]}:{hole[1]},"
            f"scale={dw}:{dh},setsar=1[d{i}];{page}[p{i}];"
            f"[p{i}][d{i}]overlay={dx}:{dy}:eof_action=repeat[v{i}]")
    burn = f"ass={filter_path(ass)}:fontsdir={fonts}," if ass else ""
    graph.append("".join(f"[v{i}]" for i in range(len(pages)))
                 + f"concat=n={len(pages)}:v=1:a=0[cat]")
    graph.append(f"[cat]{burn}format=yuv420p,fade=t=out:st={tail:.3f}:d={fade:g}[v]")
    graph.append(f"[0:a]apad=whole_dur={total:.3f},atrim=0:{total:.3f},"
                 f"afade=t=out:st={tail:.3f}:d={fade:g}[a]")
    return argv + ["-filter_complex", ";".join(graph),
                   "-map", "[v]", "-map", "[a]", "-r", str(fps), "-c:v", "libx264",
                   "-profile:v", "high", "-preset", "medium", "-crf", "21",
                   "-c:a", "aac", "-b:a", "160k", "-ar", str(video_qa.SAMPLE_RATE),
                   "-movflags", "+faststart", out]


async def short_frames(ep: Episode, m: Manifest, cut: tutorial_short.Cut, work: str,
                       *, pages: video_qa.Pages | None = None
                       ) -> tuple[list[ShortFrame], str]:
    """The Short's pages drawn in Chromium (one each for the steps said in
    the cut, short_frame.shot) and its closing card: (frames, card png).
    `pages` collects their measurements (batch 9)."""
    from . import short_frame
    w, h = short_frame.W, short_frame.H
    out = []
    async with Shooter() as shooter:
        if pages is not None:
            shooter.pages = pages
        for i, sh in enumerate(tutorial_short.shots(m, ep, cut)):
            png = await shooter.shoot(short_frame.shot(sh.scene, sh.step, cut.hook),
                                      os.path.join(work, f"shot{i:02d}.png"), w, h)
            demo = sh.scene is not None and sh.scene.kind in short_frame.CROP_KINDS
            out.append(ShortFrame(png, sh.start, sh.end, demo))
        close = await shooter.shoot(short_frame.close_shot(cut.hook, cut.lesson or ep.title),
                                    os.path.join(work, "close.png"), w, h)
    return out, close


async def _preview_main(out: str, only: list[str]) -> None:
    """`python -m atlas.modules.academy.tutorials preview DIR [series:episode
    ...]`: render scripted catalog episodes with placeholder audio, for a
    local look only."""
    os.environ.setdefault("ACADEMY_TUTORIALS_DIR", out)
    for key, n in order():
        if only and f"{key}:{n}" not in only:
            continue
        got = resolve(key, n)
        if isinstance(got, str) or not got[1].scripted:
            continue
        d, m, why = await render(key, n, preview=True)
        said = why if why is not None or m is None else (
            f"{d} {m.seconds:.0f} s (teaching {m.body_seconds:.0f} s)")
        print(key, n, said)


if __name__ == "__main__":
    import sys
    asyncio.run(_preview_main(sys.argv[2] if len(sys.argv) > 2 else "data/academy-tutorials",
                              sys.argv[3:]))
