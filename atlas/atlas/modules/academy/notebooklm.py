"""Academy lessons rendered by Google NotebookLM, ingested on the server.

the owner, 2026-10-19: lesson videos are made in NotebookLM (Video Overview ->
Explainer: its sketch slides, its own narrator, its "Gemini Notebook" end
card) by a browser runner on the Mac, instead of ATLAS's Chromium + Fish
renderer. NotebookLM's look and voice are taken as they are; the honesty
and metadata gates stay (tutorials.notebooklm_refusal).

The handoff. The Mac runner asks this module what to render and with which
words, over ssh into the api container:

    python -m atlas.modules.academy.notebooklm next [--limit N]   JSON list
    python -m atlas.modules.academy.notebooklm source <series> <n>  the text

(and `next-shorts` / `short-source` for the Shorts: notebooklm_shorts.py;
`next-thumbs` for the thumbnails of posted lessons: notebooklm_thumbs.py;
`frame-thumbs [--list] [--limit N]` drops thumbnails made from the posted
lessons' own video frames, which the daily tick also does, one a day)

and copies each render into root()/notebooklm-inbox/<series>-<NN>/ as
`video.mp4`, then `meta.json` LAST (its presence means the drop is
complete): {"series", "episode", "source_sha256" (of the exact text
`source` printed), "notebook_url", "made_at"}.

The ingest (`academy.ingest_notebooklm`, Tier 1: files on our own disk,
nothing leaves the box) checks a drop, cheapest first, and either
  * HOLDS it in the inbox when a check cannot run (a reference transcript
    missing, ffprobe or Whisper unavailable): UNKNOWN is never a pass
    (invariant 7), said once a day in #atlas-academy-videos;
  * REFUSES it into notebooklm-inbox/refused/<name>-<ts>/ with reason.txt
    when the render is wrong: a stale script, the wrong codec or length, a
    number the script never says, a human-on-camera claim, or a run of
    eight words shared with a human tutorial; said in the channel;
  * or re-encodes it (slowed to ACADEMY_NOTEBOOKLM_PACE, pitch kept, and
    upscaled to ACADEMY_NOTEBOOKLM_HEIGHT; the owner, 2026-10-20) into the
    episode directory as a plain video.mp4 with its manifest (audio
    "notebooklm", the Whisper lines on the slowed clock as caption timings),
    the navy thumbnail and description.txt. The drop's own file waits in
    .ingested/; the rollback puts it back and restores whatever render the
    ingest replaced.

Everything in a drop is untrusted (invariant 4): the meta is checked field
by field, the transcript is only matched against fixed patterns and the
script, and no transcript text reaches a prompt or a Slack line.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import logging
import math
import os
import re
import shutil
import sys
import tempfile
from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from fractions import Fraction
from pathlib import Path
from typing import Any

from ...kernel.contracts import ActionResult, Health, Observation
from . import tutorial_series as ts

log = logging.getLogger("academy.example.notebooklm")

ACTION = "academy.ingest_notebooklm"
AUDIO = "notebooklm"                 # Manifest.audio of an ingested render
RENDERER = "notebooklm-video-overview-explainer"
VOICE_NOTE = "NotebookLM's own narrator (AI)"
INBOX = "notebooklm-inbox"
REFUSED = "refused"
SHORTS = "shorts"                    # the Shorts inbox (notebooklm_shorts.py)
THUMBS = "thumbs"                    # the thumbnails inbox (notebooklm_thumbs.py)
DROP_THUMB = "thumb.png"             # a lesson drop's optional NotebookLM thumbnail
THUMB_MARKER = "notebooklm-thumb.json"   # notebooklm_thumbs.MARKER
REPLACED = ".replaced"               # a render an ingest moved aside, for the rollback
META = "meta.json"
VIDEO = "video.mp4"
KEPT_META = "notebooklm.json"        # the drop's meta, kept next to the render
WAITING = "waiting on NotebookLM render"
WAITING_SINCE = ".waiting-since"     # when the lane began waiting on NotebookLM
LAST_INGEST = ".last-ingest"         # touched by every ingest
STALE_AFTER = timedelta(days=3)      # then the lane is said stale (never Fish)
# Posted lessons whose script changed after they went out, rendered again
# for the owner's relaunch card (tutorials.relaunch_tutorial, Tier 2): `next`
# lists them while their render on disk is not current, and the ingest
# stores the new render (every gate but "already uploaded" still runs). It
# never uploads: only the card does. Code only, reviewed in a commit: no
# env, no database row, no runtime override (invariant 3). Take an entry out
# once its relaunch has shipped. n8n #4: script changed 2026-10-25.
RERENDER_POSTED: frozenset[tuple[str, int]] = frozenset({("n8n", 4)})
DROP_NAME = re.compile(r"^([a-z][a-z0-9]*)-(\d{2,3})$")
SHA = re.compile(r"^[0-9a-f]{64}$")
NOTEBOOK_URL = re.compile(r"^https://notebooklm\.google\.com/[A-Za-z0-9/_?=&.-]{1,300}$")
# The file NotebookLM gives: H.264 + AAC, at least 720p.
VIDEO_CODEC, AUDIO_CODEC = "h264", "aac"
MIN_W, MIN_H = 1280, 720
# The same 8-word rule the study tool holds a note to (tools/academy_refs/
# study.py NGRAM; tools/ is not in the image, so the check is copied here and
# a test holds the two to the same answer).
NGRAM = 8
CUE_WORDS = 14                       # a caption line: at most this many words
CUE_GAP = 1.0                        # or a pause this long starts a new one
WHISPER_TIMEOUT = 300
# A narrator that claims a person at a keyboard: NotebookLM's voices are no
# one's, and the lesson must never imply the owner recorded it.
HUMAN_ON_CAMERA = re.compile(
    r"\b(on my screen|let me open my|i recorded|my terminal|i'?m owner|i am owner|"
    r"my name is owner|share my screen|on my (?:desktop|laptop|computer))\b")
NUMBER = re.compile(r"\d+(?:[.,]\d+)*")
# A number glued to the letters before it ("GB10", "H100", "RTX4090", "v1"):
# a model or product name, not a quantity. A unit after ("100GB") is a quantity.
IDENTIFIER = re.compile(r"(?<![A-Za-z0-9])([A-Za-z]+)(\d+(?:[.,]\d+)*)")
# the owner, 2026-10-17: a lesson opens on the finished result, never on a
# welcome. Read over the narration's first OPENING_SECONDS (Whisper's word
# timings), lower case with curly apostrophes made straight.
WELCOME = "opens with a welcome"     # the refusal's first words, counted below
OPENING_SECONDS = 12.0
WELCOME_OPENING = re.compile(
    r"\b(welcome|in this (?:video|explainer)|today,? we(?:'re| are)|"
    r"(?:hi|hello|hey),? everyone)\b")
# A refused drop is listed again by `next` and remade, spending NotebookLM's
# quota each time: after this many welcome refusals for one episode, the
# next drop passes this one gate (every other gate still applies).
WELCOME_WAIVER_AFTER = 2
# the owner, 2026-10-20: "make the voice speak slower" and "support of up until 4k
# on youtube". After every gate, the drop is re-encoded: slowed to PACE
# (pitch kept: atempo) and upscaled to HEIGHT (never down). The Shorts take
# the pace only: the clips gate wants exactly 1080x1920 (notebooklm_shorts).
PACE_ENV, HEIGHT_ENV = "ACADEMY_NOTEBOOKLM_PACE", "ACADEMY_NOTEBOOKLM_HEIGHT"
PACE_DEFAULT, PACE_MIN, PACE_MAX = 0.9, 0.75, 1.0
PACE_SKIP = 0.999                    # at or above this, no slowdown
HEIGHT_DEFAULT = 2160                # 4K UHD; 0 = no upscale
REENCODE_TIMEOUT = 3600.0            # at least; 4K libx264 is slow
PRESET = "veryfast"                  # 2026-10-20: medium took ~30 min a 299 s lesson
FPS_MIN, FPS_MAX = 1.0, 120.0        # a source rate outside this is not read
REENCODE_PER_SECOND = 20.0           # ...or this many seconds per output second
SECONDS_SLACK = 0.02                 # the re-probed length vs the asked one
ORIGINALS = ".ingested"              # the untouched drop file, for the rollback
ORIGINALS_KEPT = timedelta(days=7)   # then pruned (prune_originals)
ORIGINAL_AT = re.compile(r"-(\d{8}T\d{6}\d{6})Z$")
# The pace is raised to land this far under a length cap: ffmpeg's output
# never lands exactly on the asked length (frame and AAC packet rounding).
CAP_HEADROOM = 1.0


def fallback_enabled() -> bool:
    """ACADEMY_NOTEBOOKLM_FALLBACK (default false): only then may an episode
    without a NotebookLM render be rendered, or posted, with the Fish voice,
    or a Fish teaching Short be cut. the owner, 2026-10-19: NotebookLM videos only,
    never the old design; no number of days without a render turns it on. A
    lane that waits too long is said instead (stale, stale_note)."""
    return os.getenv("ACADEMY_NOTEBOOKLM_FALLBACK", "false").strip().lower() in (
        "1", "true", "yes", "on")


def idle_for(now: datetime | None = None) -> timedelta | None:
    """How long since the later of WAITING_SINCE (made at the first look) and
    LAST_INGEST (touched by every ingest). None when the marks can't be read
    or made: UNKNOWN, never fresh (invariant 7). Blocking: a stat."""
    box = inbox()
    since = box / WAITING_SINCE
    try:
        if not since.exists():
            box.mkdir(parents=True, exist_ok=True)
            since.touch()
        marks = [since.stat().st_mtime]
        last = box / LAST_INGEST
        if last.exists():
            marks.append(last.stat().st_mtime)
    except OSError as exc:
        log.warning("notebooklm: the inbox marks can't be read (%s)", type(exc).__name__)
        return None
    return (now or datetime.now(UTC)) - datetime.fromtimestamp(max(marks), UTC)


def stale(now: datetime | None = None) -> bool | None:
    """True when no NotebookLM render has been ingested for STALE_AFTER since
    the later mark; None when the marks can't be read. Blocking."""
    idle = idle_for(now)
    return None if idle is None else idle >= STALE_AFTER


def _what_posts() -> str:
    """The truthful half of the stale line: the switch decides."""
    if fallback_enabled():
        return "Fish renders may post (ACADEMY_NOTEBOOKLM_FALLBACK is on)"
    return "nothing old-design is posted"


STALE_NOTE = "notebooklm lane stale"
UNKNOWN_NOTE = "notebooklm lane unknown"
RUNNER = "Atlas Academy NotebookLM videos"


async def waiting_work() -> int | None:
    """How many lessons and Shorts are queued for NotebookLM right now (what
    the runner's `next` and `next-shorts` would list). 0 means nothing is
    owed, so no ingest is not a fault. None when a queue can't be read:
    UNKNOWN, never guessed (invariants 6 and 7). Never raises."""
    try:
        from . import notebooklm_shorts
        return len(await next_episodes()) + len(await notebooklm_shorts.next_shorts())
    except Exception:
        log.exception("notebooklm: the render queue could not be read")
        return None


def _runner_hint() -> str:
    return (f"The runner '{RUNNER}' is a Claude-in-Chrome task on the Mac, using the owner's "
            "Google sign-in (NotebookLM has no headless login, so it cannot move to "
            "the Spark). Check its notes in ~/Documents/Code/.academy-nlm/notes.md")


async def warn_stale() -> str | None:
    """Said once a day in the academy channel when the lane is stale (renders
    are owed and none landed) or its marks or queue can't be read: the lane
    never goes quiet without saying so. Idle with nothing queued is not a
    fault and says nothing. Fresh reads every time (invariant 6). Never raises."""
    try:
        idle = await asyncio.to_thread(idle_for)
    except Exception:
        log.exception("notebooklm: the staleness check raised")
        idle = None
    if idle is None:
        text = ("Academy: NotebookLM lane UNKNOWN: the inbox marks can't be read; "
                f"{_what_posts()}. {_runner_hint()} and the inbox.")
        await _tell_once(UNKNOWN_NOTE, text)
        return text
    if idle < STALE_AFTER:
        return None
    owed = await waiting_work()
    if owed == 0:
        return None
    if owed is None:
        text = (f"Academy: NotebookLM lane UNKNOWN: no ingest for {idle.days} days and "
                f"the render queue can't be read; {_what_posts()}.")
        await _tell_once(UNKNOWN_NOTE, text)
        return text
    text = (f"Academy: no NotebookLM video ingested for {idle.days} days with {owed} "
            f"waiting; {_what_posts()}. {_runner_hint()}.")
    await _tell_once(STALE_NOTE, text)
    return text


async def check_lane() -> Observation:
    """The academy tutorial lane's health: are NotebookLM renders landing?
    OK while an ingest (or the start of the wait) is within STALE_AFTER, or
    while nothing is queued (nothing owed is not stale); DEGRADED with the
    reason when renders are owed and none landed past it; UNKNOWN, never OK,
    when the marks or the queue can't be read (invariant 7). Local reads."""
    idle = await asyncio.to_thread(idle_for)
    if idle is None:
        return Observation("notebooklm_lane", Health.UNKNOWN,
                           detail="the inbox marks can't be read")
    days = float(idle.days)
    if idle >= STALE_AFTER:
        owed = await waiting_work()
        if owed is None:
            return Observation(
                "notebooklm_lane", Health.UNKNOWN, days, "days",
                f"no NotebookLM video ingested for {idle.days} days and the render "
                "queue can't be read")
        if owed == 0:
            return Observation(
                "notebooklm_lane", Health.OK, days, "days",
                f"nothing is waiting for NotebookLM; last ingest {idle.days} days ago")
        return Observation(
            "notebooklm_lane", Health.DEGRADED, days, "days",
            f"no NotebookLM video ingested for {idle.days} days with {owed} waiting; "
            f"{_what_posts()}. {_runner_hint()}.")
    return Observation("notebooklm_lane", Health.OK, days, "days",
                       f"last NotebookLM ingest (or wait start) {idle.days} days ago")


def _tut() -> Any:
    from . import tutorials  # tutorials imports this module lazily too
    return tutorials


def inbox() -> Path:
    return _tut().root() / INBOX


def refs_root() -> Path:
    """Where the study tool's reference transcripts are, one folder per
    episode (<series>_<NN>/*.txt, wrapped as untrusted data)."""
    got = os.getenv("ACADEMY_REFS_DIR", "").strip()
    return Path(got) if got else _tut().root() / "refs"


def ref_key(series_key: str, episode: int) -> str:
    return f"{series_key}_{episode:02d}"


def drop_name(series_key: str, episode: int) -> str:
    return f"{series_key}-{episode:02d}"


# ------------------------------------------------------------- the source

def source_text(ep: ts.Episode) -> str:
    """The plain text NotebookLM is given for `ep`: its title, its result and
    every narrated line in the order the lesson speaks them (lesson_pages.
    transcript), one per line, ending in a newline. The runner and the
    server both read it from here, so its sha256 ties a render to a script."""
    from . import lesson_pages
    lines = [ep.title, "", f"Result: {ts.result_of(ep)}", ""]
    lines += lesson_pages.transcript(ep, "")
    return "\n".join(lines).rstrip() + "\n"


def source_sha(ep: ts.Episode) -> str:
    return hashlib.sha256(source_text(ep).encode("utf-8")).hexdigest()


def current(d: Path, ep: ts.Episode) -> bool:
    """Whether `d` holds an ingested NotebookLM render of `ep`'s script as it
    is now. Blocking (reads the disk)."""
    tut = _tut()
    if tut.publishable_render(str(d / VIDEO)) is not None:
        return False
    m = tut.read_manifest(d)
    return m is not None and m.audio == AUDIO and m.source_sha256 == source_sha(ep)


# ------------------------------------------------------------ the transcript

def _words(text: str) -> list[str]:
    """study.py's tokens: lower case runs of letters, digits and apostrophes."""
    return re.findall(r"[a-z0-9']+", text.lower().replace("’", "'"))


def unwrapped(text: str) -> str:
    """A reference transcript without its untrusted-data wrapper (study.py)."""
    m = re.search(r"<untrusted_data[^>]*>\n(.*)\n</untrusted_data>", text, re.DOTALL)
    return m.group(1) if m else text


def shared_runs(said: str, reference: str, n: int = NGRAM) -> list[str]:
    """Runs of `n` words `said` shares with `reference` (study.shared_runs)."""
    t = _words(reference)
    grams = {tuple(t[i:i + n]) for i in range(len(t) - n + 1)}
    w = _words(said)
    return [" ".join(w[i:i + n]) for i in range(len(w) - n + 1)
            if tuple(w[i:i + n]) in grams]


def _numbers(text: str) -> set[str]:
    return {x.replace(",", "") for x in NUMBER.findall(text)}


_UNITS = {w: i for i, w in enumerate((
    "zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine",
    "ten", "eleven", "twelve", "thirteen", "fourteen", "fifteen", "sixteen",
    "seventeen", "eighteen", "nineteen"))}
_TENS = {w: 10 * i for i, w in enumerate((
    "twenty", "thirty", "forty", "fifty", "sixty", "seventy", "eighty", "ninety"),
    start=2)}
# Whisper writes "two hundred billion" as "200 billion": the scale word ends
# the number it writes in digits.
_SCALES = {"million", "billion", "trillion"}


def _spelled(text: str) -> set[str]:
    """The cardinals `text` spells out, as Whisper writes them in digits:
    "twenty-five" 25, "one hundred and twenty" 120, "one thousand" 1000,
    "two hundred billion" 200. A word that can't continue a number ends it
    ("twenty fifty" is 20 and 50, never 70)."""
    found: set[str] = set()
    total = cur = 0
    last = ""                       # the kind of the previous word in the run

    def flush() -> None:
        nonlocal total, cur, last
        if last:
            found.add(str(total + cur))
        total = cur = 0
        last = ""

    for w in _words(text):
        for _ in range(2):          # a word that ends a run may start the next
            if w in _UNITS and (last in ("", "hundred", "thousand", "and")
                                or (last == "tens" and _UNITS[w] < 10)):
                cur += _UNITS[w]
                last = "unit"
            elif w in _TENS and last in ("", "hundred", "thousand", "and"):
                cur += _TENS[w]
                last = "tens"
            elif w == "hundred" and last == "unit" and 0 < cur < 20:
                cur *= 100
                last = "hundred"
            elif (w == "thousand" and last in ("unit", "tens", "hundred")
                  and total == 0 and cur):
                total, cur = cur * 1000, 0
                last = "thousand"
            elif w == "and" and last in ("hundred", "thousand"):
                last = "and"
            elif w in _SCALES and last in ("unit", "tens", "hundred"):
                flush()
            else:
                if last:
                    flush()
                    continue
            break
    flush()
    return found


_DIGIT_WORDS = {"zero": "0", "one": "1", "two": "2", "three": "3", "four": "4",
                "five": "5", "six": "6", "seven": "7", "eight": "8", "nine": "9"}


def _digit_runs(text: str) -> set[str]:
    """Numbers the script reads digit by digit ("port five six seven eight"
    is 5678): three or more single-digit words in a row, joined. A run of two
    ("six seven") is left alone: it is as often two numbers as one."""
    found: set[str] = set()
    run: list[str] = []
    for w in [*_words(text), ""]:
        if w in _DIGIT_WORDS:
            run.append(_DIGIT_WORDS[w])
            continue
        if len(run) >= 3:
            found.add("".join(run))
        run = []
    return found


def numbers_refusal(heard: str, source: str) -> str | None:
    """Invariant 6 in the narration: every number the file says in digits
    ("3.5", "2026") is one the script says, in digits or in words (Whisper
    writes "five" as "5", "two hundred billion" as "200 billion").
    Spelled-out numbers it hears are not read. A number glued after letters
    the script also glues to a number ("GB100" against the script's "GB10")
    is part of a name and passes; a bare or spaced number is still read."""
    said = _numbers(source) | _spelled(source) | _digit_runs(source)
    words = set(_words(source))
    # Whisper mishears a name's digits ("GB10" as "GB100", local-03): a name
    # whose letters the script glues to some number is the script's name.
    named = {m.group(1).lower() for m in IDENTIFIER.finditer(source)}
    skip = {m.span(2) for m in IDENTIFIER.finditer(heard) if m.group(1).lower() in named}
    heard_numbers = {m.group().replace(",", "") for m in NUMBER.finditer(heard)
                     if m.span() not in skip}
    for tok in sorted(heard_numbers):
        if tok in said:
            continue
        if tok.isdigit() and ts.NUMBER_WORDS.get(int(tok)) in words:
            continue
        return f"the narration states a number the script doesn't ({tok})"
    return None


def human_refusal(heard: str) -> str | None:
    """None unless the narration claims a person on camera or at the keys."""
    got = HUMAN_ON_CAMERA.search(heard.lower().replace("’", "'"))
    if got is not None:
        return f"the narration claims a human on camera ({got.group(1)!r})"
    return None


def opening_refusal(words: list[Any]) -> str | None:
    """None unless the narration's first OPENING_SECONDS say a welcome or an
    intro ("welcome", "in this video", "today we're", "hi everyone")."""
    said = " ".join(w.text for w in words if w.start < OPENING_SECONDS)
    got = WELCOME_OPENING.search(said.lower().replace("’", "'"))
    if got is not None:
        return (f"{WELCOME} ({got.group(1)!r} in the first {OPENING_SECONDS:g} s); "
                "a lesson opens on the finished result")
    return None


def welcome_refusals(name: str) -> int:
    """How many drops of `name` were refused for their welcome opening
    (refused/<name>-<ts>/reason.txt). 0 when refused/ can't be read: no
    waiver on an unknown count. Blocking."""
    pat = re.compile(rf"^{re.escape(name)}-\d{{8}}T\d{{6}}Z$")
    try:
        dirs = [d for d in (inbox() / REFUSED).iterdir()
                if pat.match(d.name) and d.is_dir() and not d.is_symlink()]
        return sum((d / "reason.txt").read_text(errors="replace").startswith(WELCOME)
                   for d in dirs if (d / "reason.txt").is_file())
    except OSError:
        return 0


def reference_files(drop: Path, series_key: str, episode: int) -> list[Path]:
    """The episode's reference transcripts: the drop's own refs/ first, else
    refs_root()/<series>_<NN>/. Plain files only. Blocking."""
    for where in (drop / "refs", refs_root() / ref_key(series_key, episode)):
        if where.is_dir() and not where.is_symlink():
            got = [f for f in sorted(where.glob("*.txt"))
                   if f.is_file() and not f.is_symlink()]
            if got:
                return got
    return []


def overlap_refusal(heard: str, refs: list[Path]) -> tuple[str | None, str | None]:
    """(refusal, None), (None, None) when nothing is copied, or (None, why)
    when the check cannot run -- UNKNOWN, which holds (invariant 7). Names
    the reference file and the count, never the words (they are someone
    else's, and untrusted). Blocking."""
    if not refs:
        return None, ("no reference transcripts for the 8-word overlap check "
                      f"(looked in the drop's refs/ and {refs_root()})")
    for f in refs:
        try:
            text = unwrapped(f.read_text(errors="replace"))
        except OSError as exc:
            return None, f"a reference transcript could not be read ({type(exc).__name__})"
        hits = shared_runs(heard, text)
        if hits:
            return (f"the narration shares {len(hits)} run(s) of {NGRAM}+ words with "
                    f"the human tutorial {f.stem}"), None
    return None, None


def cues(words: list[Any]) -> list[tuple[float, float, str]]:
    """Whisper's words as caption lines (start, end, text): a line ends at a
    sentence's end, a pause of CUE_GAP s or CUE_WORDS words."""
    out: list[tuple[float, float, str]] = []
    line: list[Any] = []
    for w in words:
        if line and (w.start - line[-1].end >= CUE_GAP or len(line) >= CUE_WORDS):
            out.append((round(line[0].start, 2), round(line[-1].end, 2),
                        " ".join(x.text for x in line)))
            line = []
        line.append(w)
        if w.text.endswith((".", "?", "!")):
            out.append((round(line[0].start, 2), round(line[-1].end, 2),
                        " ".join(x.text for x in line)))
            line = []
    if line:
        out.append((round(line[0].start, 2), round(line[-1].end, 2),
                    " ".join(x.text for x in line)))
    return out


async def transcribe(video: Path) -> tuple[list[Any] | None, str | None]:
    """Whisper's words for the whole file: the same Groq helper the opener
    check and the reels use (content.captions.word_timings)."""
    from ..content import ffmpeg
    from ..content.captions import word_timings
    try:
        audio = await ffmpeg.run(["ffmpeg", "-v", "error", "-i", str(video), "-vn",
                                  "-ac", "1", "-ar", "16000", "-b:a", "32k", "-f", "mp3",
                                  "-"], timeout=WHISPER_TIMEOUT)
    except ffmpeg.FfmpegError as exc:
        return None, f"ffmpeg: {exc}"
    return await word_timings(bytes(audio), "notebooklm.mp3")


def probe_refusal(p: dict[str, Any], ep: ts.Episode) -> str | None:
    """The file NotebookLM gives, as the upload needs it."""
    if p.get("video_codec") != VIDEO_CODEC:
        return f"the video is {p.get('video_codec') or 'unreadable'}, not H.264"
    if not p.get("audio"):
        return "the file has no sound"
    if p.get("audio_codec") != AUDIO_CODEC:
        return f"the sound is {p.get('audio_codec') or 'unreadable'}, not AAC"
    w, h = int(p.get("width") or 0), int(p.get("height") or 0)
    if w < MIN_W or h < MIN_H:
        return f"the file is {w}x{h}, under {MIN_W}x{MIN_H}"
    secs = float(p.get("seconds") or 0.0)
    if not ep.min_seconds <= secs <= ep.max_seconds:
        return (f"it runs {secs:.0f} s, outside "
                f"{ep.min_seconds:.0f}-{ep.max_seconds:.0f} s")
    return None


# ------------------------------------------------------- pace and size

def pace_setting() -> float:
    """ACADEMY_NOTEBOOKLM_PACE (default 0.9), clamped to 0.75..1.0; 1.0 is
    the narrator's own speed. Unreadable is the default, never a guess."""
    raw = os.getenv(PACE_ENV, "").strip()
    try:
        got = float(raw) if raw else PACE_DEFAULT
    except ValueError:
        got = PACE_DEFAULT
    if not math.isfinite(got):
        got = PACE_DEFAULT
    return min(PACE_MAX, max(PACE_MIN, got))


def height_setting() -> int:
    """ACADEMY_NOTEBOOKLM_HEIGHT (default 2160): the lessons' output height.
    0 (or below) turns the upscale off."""
    raw = os.getenv(HEIGHT_ENV, "").strip()
    try:
        got = int(raw) if raw else HEIGHT_DEFAULT
    except ValueError:
        got = HEIGHT_DEFAULT
    return max(0, got)


def effective_pace(pace: float, seconds: float, max_seconds: float) -> float:
    """The pace a file is slowed to: `pace`, raised so the slowed file never
    runs past `max_seconds` (1.0 when it is already at or past it)."""
    if max_seconds <= 0 or seconds <= 0:
        return 1.0
    return min(1.0, max(pace, seconds / max_seconds))


def lesson_limit(ep: ts.Episode, seconds: float) -> float:
    """The longest a slowed lesson may run: CAP_HEADROOM under the episode's
    max, and under the topic line's "under five minutes" when that claim
    holds now (claim_refusal is run on the file's length at post time)."""
    limit = ep.max_seconds - CAP_HEADROOM
    if (ts.claim_refusal(ep, seconds) is None
            and ts.claim_refusal(ep, ts.UNDER_FIVE_SECONDS) is not None):
        limit = min(limit, ts.UNDER_FIVE_SECONDS - CAP_HEADROOM)
    return limit


def upscale_to(height: int, target: int) -> int | None:
    """The height to scale to, or None: off (0) or never down."""
    return target if target > 0 and height < target else None


def slowed_cues(timings: list[tuple[float, float, str]], pace: float
                ) -> list[tuple[float, float, str]]:
    """Caption cues on the slowed clock: every start and end over `pace`."""
    return [(round(a / pace, 2), round(b / pace, 2), t) for a, b, t in timings]


def pace_filters(pace: float) -> tuple[str | None, str | None]:
    """(the video filter, the audio filter) that slow a file to `pace`, pitch
    kept; (None, None) at full speed."""
    if pace >= PACE_SKIP:
        return None, None
    p = f"{pace:.6g}"
    return f"setpts=PTS/{p}", f"atempo={p}"


def fps_text(fps: Any) -> str | None:
    """The probe's frame rate as ffmpeg's fps filter takes it ("30",
    "30000/1001"), or None when it was not read or is not a sane rate."""
    try:
        got = float(fps)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(got) or not FPS_MIN <= got <= FPS_MAX:
        return None
    return str(Fraction(got).limit_denominator(1001))


def reencode_argv(src: str, out: str, pace: float, height: int | None,
                  fps: Any = None) -> list[str]:
    """The lesson's re-encode: slowed (setpts + atempo, then fps= the source's
    own rate, so nothing plays at a rate the drop never had) and upscaled
    with lanczos to `height` (width follows the aspect, even), H.264 + AAC."""
    vpace, apace = pace_filters(pace)
    rate = fps_text(fps) if vpace else None
    vf = [f for f in (vpace, f"fps={rate}" if rate else None,
                      f"scale=-2:{height}:flags=lanczos" if height else None) if f]
    argv = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i", src]
    if vf:
        argv += ["-vf", ",".join(vf)]
    if apace:
        argv += ["-af", apace]
    return argv + ["-c:v", "libx264", "-preset", PRESET, "-crf", "18", "-threads", "0",
                   "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k",
                   "-movflags", "+faststart", out]


def prune_originals(now: datetime | None = None) -> int:
    """Delete the untouched drop files in .ingested/ kept longer than
    ORIGINALS_KEPT (by the time in their folder's name; a name that does
    not read is kept). Our own copies on our own disk; an older rollback
    then restores nothing and says so. Blocking."""
    base = inbox() / ORIGINALS
    if not base.is_dir():
        return 0
    cut = (now or datetime.now(UTC)) - ORIGINALS_KEPT
    gone = 0
    for d in sorted(base.iterdir()):
        got = ORIGINAL_AT.search(d.name)
        if d.is_symlink() or not d.is_dir() or got is None:
            continue
        try:
            at = datetime.strptime(got.group(1), "%Y%m%dT%H%M%S%f").replace(tzinfo=UTC)
        except ValueError:
            continue
        if at < cut:
            shutil.rmtree(d, ignore_errors=True)
            gone += 1
    return gone


def reencode_timeout(out_seconds: float) -> float:
    return max(REENCODE_TIMEOUT, REENCODE_PER_SECOND * out_seconds)


def reencode_refusal(got: dict[str, Any], ep: ts.Episode, before: dict[str, Any],
                     pace: float, height: int | None) -> str | None:
    """None when the re-encoded file is what was asked: the upload's profile
    (probe_refusal), the asked height and width, and the slowed length."""
    if (why := probe_refusal(got, ep)) is not None:
        return why
    w, h = int(got.get("width") or 0), int(got.get("height") or 0)
    if height:
        want_w = int(before["width"]) * height / int(before["height"])
        if h != height or abs(w - want_w) > 2:
            return f"it is {w}x{h}, not {want_w:.0f}x{height}"
    elif (w, h) != (int(before["width"]), int(before["height"])):
        return f"it is {w}x{h}, not the drop's {before['width']}x{before['height']}"
    want = float(before["seconds"]) / pace
    secs = float(got.get("seconds") or 0.0)
    if abs(secs - want) > max(1.0, SECONDS_SLACK * want):
        return f"it runs {secs:.1f} s, not the {want:.1f} s asked"
    return None


# --------------------------------------------------------------- the drops

def _meta(drop: Path) -> dict[str, Any] | None:
    """The drop's meta.json, or None while it is missing or half-written."""
    p = drop / META
    if p.is_symlink() or not p.is_file():
        return None
    try:
        got = json.loads(p.read_text())
    except (OSError, ValueError):
        return None
    return got if isinstance(got, dict) else None


def complete_drops() -> list[Path]:
    """Each drop in the inbox whose meta.json is there. Blocking."""
    box = inbox()
    if not box.is_dir():
        return []
    return [d for d in sorted(box.iterdir())
            if d.is_dir() and not d.is_symlink() and not d.name.startswith(".")
            and d.name not in (REFUSED, SHORTS, THUMBS) and (d / META).is_file()]


def meta_refusal(meta: dict[str, Any], series_key: str, episode: int) -> str | None:
    if meta.get("series") != series_key or meta.get("episode") != episode:
        return (f"meta.json names {str(meta.get('series'))[:20]!r} "
                f"#{str(meta.get('episode'))[:6]}, not the folder's {series_key} #{episode}")
    if not isinstance(meta.get("source_sha256"), str) or not SHA.match(meta["source_sha256"]):
        return "meta.json has no source_sha256"
    return None


def _refuse(drop: Path, why: str) -> Path:
    """Move `drop` to refused/<name>-<ts>/ with reason.txt. Blocking."""
    at = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    dest = inbox() / REFUSED / f"{drop.name}-{at}"
    dest.parent.mkdir(parents=True, exist_ok=True)
    os.replace(drop, dest)
    (dest / "reason.txt").write_text(why + "\n")
    return dest


async def _tell(text: str) -> None:
    await _tut()._tell(text)


async def _tell_once(note: str, text: str) -> None:
    tut = _tut()
    if not await asyncio.to_thread(tut._already_told, note):
        await _tell(text)
        await asyncio.to_thread(tut._mark_told, note)


async def _thumbnail(ep: ts.Episode, out_dir: Path, badge: str) -> str | None:
    """The navy thumbnail (tutorial_thumbs, the same as every render's):
    thumb.png and thumb.jpg into `out_dir`. None, or why not."""
    from ..content import ffmpeg
    from . import tutorial_thumbs as thumbs
    tut = _tut()
    png = out_dir / "thumb.png"
    async with tut.Shooter() as shooter:
        with tempfile.TemporaryDirectory() as work:
            why = await thumbs.render(ep, shooter, str(png), level=ep.level,
                                      badge=badge, work=work)
    if why is not None:
        return why
    try:
        await ffmpeg.run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i",
                          str(png), "-q:v", "3", str(out_dir / "thumb.jpg")], timeout=60)
    except ffmpeg.FfmpegError as exc:
        return f"thumb.jpg: {exc}"
    # thumb_file() trusts a PNG only if it is not older than the JPG
    await asyncio.to_thread(os.utime, png)
    return None


RENDER_FILES = ("video.mp4", "manifest.json", "thumb.png", "thumb.jpg",
                "description.txt", KEPT_META, THUMB_MARKER)


def _set_aside(d: Path, name: str) -> str:
    """Move the render already in `d` to inbox/.replaced/<name>-<ts>/ and say
    where ("" when there was none). Blocking."""
    present = [f for f in RENDER_FILES if (d / f).exists() or (d / f).is_symlink()]
    if not present:
        return ""
    at = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    dest = inbox() / REPLACED / f"{name}-{at}"
    dest.mkdir(parents=True, exist_ok=True)
    for f in present:
        os.replace(d / f, dest / f)
    return str(dest)


def _kept(p: Path, name: str) -> bool:
    """`p` is `name` directly in a folder of inbox/.ingested/."""
    return p.name == name and p.parent.parent.resolve() == (inbox() / ORIGINALS).resolve()


def _within(p: Path, base: Path) -> bool:
    try:
        p.resolve().relative_to(base.resolve())
    except ValueError:
        return False
    return True


# ------------------------------------------------------------- the action

def _drop_for(series_key: str, episode: int) -> Path:
    return inbox() / drop_name(series_key, episode)


async def ingest_dry(series: str, episode: int) -> ActionResult:
    """What the run would take: never Whisper, never a move. True whenever a
    complete drop is there, so the run can refuse a bad one into refused/."""
    tut = _tut()
    got = tut.resolve(series, episode)
    if isinstance(got, str):
        return ActionResult(False, got)
    s, ep = got
    drop = _drop_for(s.key, ep.number)
    meta = await asyncio.to_thread(_meta, drop)
    if meta is None:
        return ActionResult(False, f"no complete NotebookLM drop at {drop.name}")
    why = meta_refusal(meta, s.key, ep.number)
    if why is None and meta["source_sha256"] != source_sha(ep):
        why = "stale script"
    try:
        size = (drop / VIDEO).stat().st_size
    except OSError:
        size = 0
    if why is not None:
        return ActionResult(True, f"would refuse the drop {drop.name} ({why}) into "
                                  f"{INBOX}/{REFUSED}/")
    return ActionResult(True, f"would ingest {drop.name} ({size / 1e6:.1f} MB) into "
                              f"{tut.episode_dir(s, ep).name} after the probe, Whisper "
                              "and transcript checks")


async def ingest(series: str, episode: int) -> ActionResult:
    """Check one drop and ingest, hold or refuse it (see the module doc)."""
    tut = _tut()
    got = tut.resolve(series, episode)
    if isinstance(got, str):
        return ActionResult(False, got)
    s, ep = got
    name = drop_name(s.key, ep.number)
    drop = _drop_for(s.key, ep.number)

    async def refuse(why: str) -> ActionResult:
        try:
            dest = await asyncio.to_thread(_refuse, drop, why)
        except OSError as exc:
            return await hold(f"the drop could not be moved to refused/ "
                              f"({type(exc).__name__}); refused: {why}")
        await _tell(f"Academy: the NotebookLM render for {s.key} #{ep.number} was "
                    f"refused: {why}. Moved to {INBOX}/{REFUSED}/{dest.name}.")
        return ActionResult(False, f"refused {name}: {why}")

    async def hold(why: str) -> ActionResult:
        await _tell_once(f"notebooklm hold {name}",
                         f"Academy: the NotebookLM render for {s.key} #{ep.number} is "
                         f"held in the inbox, not ingested: {why}.")
        return ActionResult(False, f"held {name}: {why}")

    if drop.is_symlink() or not drop.is_dir():
        return ActionResult(False, f"no NotebookLM drop at {name}")
    meta = await asyncio.to_thread(_meta, drop)
    if meta is None:
        return ActionResult(False, f"the drop {name} is not complete (no meta.json yet)")
    if (why := meta_refusal(meta, s.key, ep.number)) is not None:
        return await refuse(why)
    if not ep.scripted:
        return await refuse("the episode has no script")
    if (why := await asyncio.to_thread(ts.held, s.key, ep)) is not None:
        return await refuse(f"the episode is held: {why}")
    if meta["source_sha256"] != source_sha(ep):
        return await refuse("stale script: the render's source_sha256 is not the sha "
                            "of the episode's source text now; render it again")
    video = drop / VIDEO
    if video.is_symlink() or not video.is_file():
        return await refuse("video.mp4 is missing or not a plain file")
    try:
        row = await tut.posted(s.key, ep.number)
    except Exception as exc:                            # noqa: BLE001
        return await hold(f"the posts record could not be read ({type(exc).__name__})")
    if row and (s.key, ep.number) not in RERENDER_POSTED:
        return await refuse(f"the episode is already uploaded ({row.get('video_id')}); "
                            "a new file is a relaunch, the owner's card")
    # RERENDER_POSTED: the render is only stored here; the old video, its row
    # and any upload stay with the owner's relaunch card.
    refs = await asyncio.to_thread(reference_files, drop, s.key, ep.number)
    if not refs:
        _, why = overlap_refusal("", refs)
        return await hold(f"overlap check UNKNOWN: {why}")
    probe, why = await tut._probe(str(video))
    if probe is None:
        return await hold(f"the file could not be probed ({why})")
    if (why := probe_refusal(probe, ep)) is not None:
        return await refuse(why)
    words, why = await transcribe(video)
    if words is None:
        return await hold(f"the narration could not be transcribed ({why})")
    heard = " ".join(w.text for w in words)
    if (why := numbers_refusal(heard, source_text(ep))) is not None:
        return await refuse(why)
    if (why := human_refusal(heard)) is not None:
        return await refuse(why)
    waived = 0
    if (why := opening_refusal(words)) is not None:
        waived = await asyncio.to_thread(welcome_refusals, name)
        if waived < WELCOME_WAIVER_AFTER:
            return await refuse(why)
    why, unknown = await asyncio.to_thread(overlap_refusal, heard, refs)
    if unknown is not None:
        return await hold(f"overlap check UNKNOWN: {unknown}")
    if why is not None:
        return await refuse(why)
    done = await _install(s, ep, drop, meta, probe, cues(words))
    if done.ok and waived:
        await _tell_once(f"notebooklm welcome {name}",
                         f"Academy: the NotebookLM render for {s.key} #{ep.number} was "
                         f"accepted with a welcome opening, after {waived} refusals for "
                         "one; every other gate passed. Trim the opening by hand if "
                         "it should start on the result.")
    return done


async def _install(s: ts.Series, ep: ts.Episode, drop: Path, meta: dict[str, Any],
                   probe: dict[str, Any], timings: list[tuple[float, float, str]]
                   ) -> ActionResult:
    """Every gate passed: the render (slowed and upscaled, see pace_setting
    and height_setting), its manifest, thumbnail and description into the
    episode directory; the drop's untouched file is kept in .ingested/ for
    the rollback and its folder is gone. A re-encode that fails, or a file
    that does not probe as asked, holds the drop: the unprocessed file is
    never installed in its place."""
    from ..content import ffmpeg
    tut = _tut()
    name = drop.name
    d = tut.episode_dir(s, ep)
    before = dict(probe)
    src_seconds = float(before["seconds"])
    pace = effective_pace(pace_setting(), src_seconds, lesson_limit(ep, src_seconds))
    height = upscale_to(int(before["height"]), height_setting())
    work = pace < PACE_SKIP or height is not None

    async def held(why: str) -> ActionResult:
        await _tell_once(f"notebooklm hold {name}",
                         f"Academy: the NotebookLM render for {s.key} #{ep.number} "
                         f"is held in the inbox: {why}.")
        return ActionResult(False, f"held {name}: {why}")

    with tempfile.TemporaryDirectory(dir=str(inbox())) as staged:
        out = Path(staged) / VIDEO
        if work:
            try:
                await ffmpeg.run(reencode_argv(str(drop / VIDEO), str(out), pace, height,
                                               before.get("fps")),
                                 timeout=reencode_timeout(src_seconds / pace))
            except ffmpeg.FfmpegError as exc:
                return await held(f"the re-encode (pace {pace:.2f}, "
                                  f"{height or before['height']}p) failed: {exc}")
            got, why = await tut._probe(str(out))
            if got is None:
                return await held(f"the re-encoded file could not be probed ({why})")
            if (why := reencode_refusal(got, ep, before, pace, height)) is not None:
                return await held(f"the re-encoded file is not as asked: {why}")
            probe = got
            timings = slowed_cues(timings, pace)
        else:
            pace = 1.0
        seconds = round(float(probe["seconds"]), 2)
        width, tall = int(probe["width"]), int(probe["height"])
        minutes = tut.minutes_of(seconds)
        badge = tut.badge_text(minutes) if ep.title_hook else ""
        drawn = badge if badge and tut.thumbnail_takes_badge() else ""
        # 2026-10-20: the drop's own NotebookLM thumbnail when it has one and
        # it passes notebooklm_thumbs.normalise; else the drawn one, said.
        from . import notebooklm_thumbs
        thumb, notes = "drawn", []
        frame: dict[str, Any] = {}
        if (drop / DROP_THUMB).exists() or (drop / DROP_THUMB).is_symlink():
            got_thumb, why = await notebooklm_thumbs.normalise(drop / DROP_THUMB,
                                                               Path(staged))
            if got_thumb is not None:
                thumb, drawn = "notebooklm", ""          # NotebookLM's draws no badge
            else:
                notes.append(f"the NotebookLM thumbnail was not used: {why}")
                for f in ("thumb.jpg", "thumb.png"):
                    (Path(staged) / f).unlink(missing_ok=True)
        # 2026-10-20: else a frame of the lesson's own video (unique against
        # every other lesson's); the drawn card only when that fails, said.
        if thumb == "drawn":
            got_frame, why = await notebooklm_thumbs.frame_thumbnail(
                ep, drop / VIDEO, Path(staged), drawn, skip=d, skip_drop=name)
            if got_frame is not None:
                thumb, frame = notebooklm_thumbs.FRAME_SOURCE, got_frame
                if got_frame.get("note"):
                    notes.append(str(got_frame["note"]))
            else:
                notes.append(f"the video frame was not used ({why}); the drawn one is")
                for f in ("thumb.jpg", "thumb.png"):
                    (Path(staged) / f).unlink(missing_ok=True)
        if thumb == "drawn":
            why = await _thumbnail(ep, Path(staged), drawn)
            if why is not None:
                return await held(f"thumbnail: {why}")
        thumb_note = "; ".join(notes)
        url = str(meta.get("notebook_url") or "")
        made = str(meta.get("made_at") or "")
        try:
            made = datetime.fromisoformat(made).isoformat(timespec="seconds")
        except ValueError:
            made = ""
        m = tut.Manifest(
            series=s.key, episode=ep.number, audio=AUDIO, seconds=seconds,
            chapters=[], width=width, height=tall,
            design=RENDERER, voice_note=VOICE_NOTE,
            rendered_at=datetime.now(UTC).isoformat(timespec="seconds"),
            minutes=minutes, badge=drawn, timings=timings, renderer=RENDERER,
            source_sha256=source_sha(ep),
            notebook_url=url if NOTEBOOK_URL.match(url) else "", made_at=made)

        def place() -> tuple[str, str, str]:
            d.mkdir(parents=True, exist_ok=True)
            backup = _set_aside(d, name)
            original = kept_thumb = ""
            has_thumb = (drop / DROP_THUMB).is_file() and not (drop / DROP_THUMB).is_symlink()
            keep = inbox() / ORIGINALS / f"{name}-{datetime.now(UTC):%Y%m%dT%H%M%S%fZ}"
            if work or has_thumb:
                keep.mkdir(parents=True, exist_ok=True)
            if has_thumb:                                # the drop's, for the rollback
                os.replace(drop / DROP_THUMB, keep / DROP_THUMB)
                kept_thumb = str(keep / DROP_THUMB)
            if work:
                os.replace(out, d / VIDEO)
                os.replace(drop / VIDEO, keep / VIDEO)
                original = str(keep / VIDEO)
            else:
                os.replace(drop / VIDEO, d / VIDEO)
            for f in ("thumb.jpg", "thumb.png"):         # the PNG last: never older
                if (Path(staged) / f).is_file():
                    shutil.move(str(Path(staged) / f), str(d / f))
            if thumb == "notebooklm":
                (d / THUMB_MARKER).write_text(notebooklm_thumbs.marker_text(meta))
            elif frame:
                (d / THUMB_MARKER).write_text(
                    notebooklm_thumbs.marker_text(meta, frame))
            (d / "manifest.json").write_text(json.dumps(asdict(m), indent=1))
            (d / "description.txt").write_text(
                tut.description(s, ep, [], minutes))
            os.replace(drop / META, d / KEPT_META)
            shutil.rmtree(drop, ignore_errors=True)
            (inbox() / LAST_INGEST).touch()
            return backup, original, kept_thumb
        backup, original, kept_thumb = await asyncio.to_thread(place)
    token = json.dumps({"drop": str(drop), "dir": str(d), "backup": backup,
                        "original": original, "thumb": kept_thumb}, sort_keys=True)
    return ActionResult(True, f"ingested the NotebookLM render of {s.key} #{ep.number}: "
                              f"{seconds:.0f} s at {pace:.2f}x, {width}x{tall}, "
                              f"{len(timings)} caption lines",
                        evidence={"dir": str(d), "seconds": seconds, "pace": pace,
                                  "width": width, "height": tall,
                                  "reencoded": work, "thumbnail": thumb,
                                  **({"thumbnail_note": thumb_note} if thumb_note else {})},
                        rollback_token=token)


async def uningest(token: str) -> ActionResult:
    """Put the drop back in the inbox and restore the render it replaced."""
    try:
        undo = json.loads(token)
        drop, d, backup = Path(undo["drop"]), Path(undo["dir"]), str(undo["backup"])
        original = str(undo.get("original") or "")      # absent in older tokens
        kept_thumb = str(undo.get("thumb") or "")
    except (ValueError, KeyError, TypeError, AttributeError):
        return ActionResult(False, "unreadable rollback token")
    tut = _tut()
    if (not _within(drop, inbox()) or drop.parent.resolve() != inbox().resolve()
            or d.parent.resolve() != (tut.root() / "episodes").resolve()
            or (backup and not _within(Path(backup), inbox() / REPLACED))
            or not all(not p or _kept(Path(p), n)
                       for p, n in ((original, VIDEO), (kept_thumb, DROP_THUMB)))):
        return ActionResult(False, "not a NotebookLM ingest; left alone")

    if original and not Path(original).is_file():
        return ActionResult(False, f"the drop's own file was pruned after "
                                   f"{ORIGINALS_KEPT.days} days; nothing restored")

    def back() -> None:
        drop.mkdir(parents=True, exist_ok=True)
        if kept_thumb and Path(kept_thumb).is_file():
            os.replace(kept_thumb, drop / DROP_THUMB)
            if not original:
                Path(kept_thumb).parent.rmdir()
        if original:
            # The drop gets its untouched file back (a re-ingest slows it
            # once); the re-encoded one is ours and goes.
            os.replace(original, drop / VIDEO)
            Path(original).parent.rmdir()
            (d / VIDEO).unlink(missing_ok=True)
        else:
            os.replace(d / VIDEO, drop / VIDEO)
        for f in ("manifest.json", "thumb.png", "thumb.jpg", "description.txt",
                  THUMB_MARKER):
            (d / f).unlink(missing_ok=True)
        if backup:
            for f in Path(backup).iterdir():
                os.replace(f, d / f.name)
            Path(backup).rmdir()
        os.replace(d / KEPT_META, drop / META)          # meta.json last: complete
    try:
        await asyncio.to_thread(back)
    except OSError as exc:
        return ActionResult(False, f"the drop could not be put back ({type(exc).__name__})")
    return ActionResult(True, f"{drop.name} is back in the inbox"
                              + ("; the render it replaced is restored" if backup else ""))


# --------------------------------------------------------------- the tick

async def ingest_pending() -> list[str]:
    """Ingest every complete drop through the executor, one at a time. A
    folder whose name is no episode is refused here (local, our own disk).
    Then a stale or unreadable lane is said (warn_stale). Never raises: the
    daily tick goes on."""
    from ...kernel.executor import ExecutionRequest, execute
    tut = _tut()
    said: list[str] = []
    try:
        if (pruned := await asyncio.to_thread(prune_originals)):
            said.append(f"pruned {pruned} ingested original(s) older than "
                        f"{ORIGINALS_KEPT.days} days")
    except OSError as exc:
        said.append(f"the ingested originals could not be pruned ({type(exc).__name__})")
    try:
        drops = await asyncio.to_thread(complete_drops)
    except OSError as exc:
        said.append(f"the NotebookLM inbox could not be read ({type(exc).__name__})")
        drops = []
    for drop in drops:
        got = DROP_NAME.match(drop.name)
        found = tut.resolve(got.group(1), int(got.group(2))) if got else "no such name"
        if isinstance(found, str):
            why = f"the folder name {drop.name[:40]!r} is no catalog episode"
            try:
                await asyncio.to_thread(_refuse, drop, why)
            except OSError as exc:
                said.append(f"{drop.name}: {why}; not moved ({type(exc).__name__})")
                continue
            await _tell(f"Academy: a NotebookLM drop was refused: {why}.")
            said.append(f"{drop.name}: refused: {why}")
            continue
        s, ep = found
        try:
            done = await execute(ExecutionRequest(
                action=ACTION, params={"series": s.key, "episode": ep.number},
                reason=f"a NotebookLM render of {s.key} #{ep.number} is in the inbox"))
        except Exception:
            log.exception("notebooklm: the ingest of %s raised", drop.name)
            said.append(f"{drop.name}: the ingest raised (see the log)")
            continue
        said.append(f"{drop.name}: {done.detail}")
    # After the ingests, so a render that landed today counts.
    if (stale_line := await warn_stale()) is not None:
        said.append(stale_line)
    return said


# -------------------------------------------------------------- next / CLI

async def _not_needed() -> set[tuple[str, int]]:
    """Episodes with any posts row (public, uploaded private or withdrawn):
    each has its file already, bar RERENDER_POSTED (next_episodes). Raises
    when the record cannot be read."""
    from ...kernel.state import fetch_all
    rows = await fetch_all("SELECT DISTINCT series, episode FROM academy_tutorial_posts")
    return {(str(r["series"]), int(r["episode"])) for r in rows}


def _pending_drops() -> set[tuple[str, int]]:
    out: set[tuple[str, int]] = set()
    for d in complete_drops():
        got = DROP_NAME.match(d.name)
        if got:
            out.add((got.group(1), int(got.group(2))))
    return out


async def next_episodes(limit: int | None = None) -> list[dict[str, Any]]:
    """The lesson series' scripted, not yet posted (or RERENDER_POSTED), not
    held episodes (the channel welcome is no lesson) with no current
    NotebookLM render and no drop waiting, in each series' teaching order
    (tutorials.ladder). Each series' first episode comes before any second
    one, and within a rank the series with the shortest script runway
    first. Raises when the posts record or the runway cannot be read: an
    UNKNOWN runway is never guessed."""
    tut = _tut()
    gone = await _not_needed()
    days = await tut.runway()
    if any(v is None for v in days.values()):
        raise RuntimeError("the script runway could not be read (UNKNOWN)")
    waiting = await asyncio.to_thread(_pending_drops)
    rows: list[tuple[int, int, int, dict[str, Any]]] = []
    days = {k: int(v) for k, v in days.items() if v is not None}
    for order, s in enumerate(x for x in ts.SERIES if x.playlist):   # lessons only
        rank = 0
        for ep in tut.ladder(s):
            if not ep.scripted:
                continue
            if (s.key, ep.number) in gone and (s.key, ep.number) not in RERENDER_POSTED:
                continue
            if (s.key, ep.number) in waiting or ts.held(s.key, ep) is not None:
                continue
            if await asyncio.to_thread(current, tut.episode_dir(s, ep), ep):
                continue
            runway = days[s.key]
            rows.append((rank, runway, order, {
                "series": s.key, "episode": ep.number, "title": ep.title,
                "drop": drop_name(s.key, ep.number), "source_sha256": source_sha(ep),
                "min_seconds": ep.min_seconds, "max_seconds": ep.max_seconds,
                "runway_days": runway}))
            rank += 1
    rows.sort(key=lambda r: r[:3])
    out = [r[3] for r in rows]
    return out[:limit] if limit is not None else out


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="python -m atlas.modules.academy.notebooklm",
                                description="NotebookLM handoff for the Mac runner")
    sub = p.add_subparsers(dest="cmd", required=True)
    src = sub.add_parser("source", help="print an episode's source text")
    src.add_argument("series")
    src.add_argument("episode", type=int)
    nxt = sub.add_parser("next", help="JSON: the episodes to render next")
    nxt.add_argument("--limit", type=int, default=None)
    ssrc = sub.add_parser("short-source", help="print the text for an episode's Short")
    ssrc.add_argument("series")
    ssrc.add_argument("episode", type=int)
    nsh = sub.add_parser("next-shorts", help="JSON: the public lessons to make Shorts of")
    nsh.add_argument("--limit", type=int, default=None)
    nth = sub.add_parser("next-thumbs",
                         help="JSON: the public lessons whose thumbnail is not NotebookLM's")
    nth.add_argument("--limit", type=int, default=None)
    fth = sub.add_parser("frame-thumbs",
                         help="drop a thumbnail made from a frame of its own video for "
                              "each posted NotebookLM lesson still on the drawn one")
    fth.add_argument("--limit", type=int, default=None)
    fth.add_argument("--list", action="store_true",
                     help="JSON: only list those lessons; draw and drop nothing")
    a = p.parse_args(argv)
    if a.cmd in ("source", "short-source"):
        got = _tut().resolve(a.series, a.episode)
        if isinstance(got, str):
            print(got, file=sys.stderr)
            return 2
        _s, ep = got
        if not ep.scripted:
            print(f"{a.series} #{a.episode} has no script", file=sys.stderr)
            return 2
        if a.cmd == "short-source":
            from . import notebooklm_shorts
            sys.stdout.write(notebooklm_shorts.source_text(ep))
        else:
            sys.stdout.write(source_text(ep))
        return 0
    try:
        if a.cmd == "next-shorts":
            from . import notebooklm_shorts
            got_next = asyncio.run(notebooklm_shorts.next_shorts(a.limit))
        elif a.cmd == "frame-thumbs" and a.list:
            from . import notebooklm_thumbs
            got_next = asyncio.run(notebooklm_thumbs.frame_candidates())
            got_next = got_next[:a.limit] if a.limit is not None else got_next
        elif a.cmd == "frame-thumbs":
            from . import notebooklm_thumbs
            got_next = asyncio.run(notebooklm_thumbs.frame_backfill(a.limit))
        elif a.cmd == "next-thumbs":
            from . import notebooklm_thumbs
            got_next = asyncio.run(notebooklm_thumbs.next_thumbs(a.limit))
        else:
            got_next = asyncio.run(next_episodes(a.limit))
    except Exception as exc:                            # noqa: BLE001
        print(f"error: the runway is UNKNOWN: {type(exc).__name__}: could not read "
              "the Academy records", file=sys.stderr)
        return 1
    print(json.dumps(got_next, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
