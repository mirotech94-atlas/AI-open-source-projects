"""The 30-day reels calendar the owner supplied (2026-09-27), rendered day by day.

Two packages ship beside this file, as the owner received them:

  * `calendars/stories-v2.json` -- the AI-stories account's month: 30
    original Egyptian-Arabic reels (RETURN, COURT, FOOD, KINDNESS), 170
    timed beats, each with its dialogue, its label-free Arabic subtitle and
    a generation prompt. Revision 2 replaces the whole of revision 1, which
    was never loaded.
  * `calendars/business-v1.json` -- a business-education month. the owner
    chose (2026-09-27): the same AI-stories account, the day after the
    stories month ends, one lesson a day, Egyptian Arabic on odd days and
    English on even ones. A lesson is narrated, not acted; every generated
    screen is illustrative, and the package's own caption says so
    ("Demonstrations and hypothetical examples are labeled"). Its $29 and
    $99 offers stay drafts: nothing here sells.

The package JSON is reviewed repo content, not ingested text: it is quoted
into Veo's prompts as given and never reaches a reasoning model.

**How a day is made.** A beat longer than Veo's 8 s clip is split into
parts of equal length, each part its own clip with its share of the lines.
Each part costs one 8 s Veo clip, trimmed to the part's length (or to the
time its lines need, when that is longer, capped at the clip). The month is
259 clips; the owner's Gemini tier allows 10 a day (`VEO_DAILY_CLIPS`), and the
longest RETURN episodes need 13, so clips are banked AHEAD: every tick
spends what is left of today's allowance on the earliest unfinished day,
up to `LOOKAHEAD_DAYS` ahead. The allowance is counted from midnight
Pacific, when Google resets it. The seconds still count against
`VEO_MONTHLY_SECONDS`, reserved one clip at a time under the render lock.

**Captions** are in English whatever the voice (the owner, 2026-09-27: Arabic
voice, English captions, so everyone can follow): the package's own
translation of each line (`english_meaning`, speaker labels stripped), on
screen while its Arabic line is spoken -- never a transcription.

**Nothing posts here.** `render_job` is Tier AUTO (a file and a draft
row); the day's publish card is raised by `kernel.reels` on the day, a RETURN
sequel only once the episode before it actually published.
"""
from __future__ import annotations

import asyncio
import json
import math
import os
import re
import shutil
import tempfile
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from functools import lru_cache
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from ...kernel.contracts import ActionResult
from ...kernel.state import execute_sql, fetch_all, fetch_one
from . import captions, ffmpeg, higgsfield, render, veo
from .drafts import AI_LABEL

HERE = Path(__file__).parent / "calendars"
PACKAGES = {"stories": "stories-v2.json", "business": "business-v1.json"}
ZONE = ZoneInfo("America/New_York")
DEFAULT_DAILY_CLIPS = 10          # Tier 1 Prepay: 10 Veo clips a day
LOOKAHEAD_DAYS = 3
SPEECH_WPS = 2.3                  # Egyptian Arabic, performed: words a second
MIN_PART_S = 2.0
MAX_REFUSALS = 2
NARRATOR = "راوي"


# ----------------------------------------------------------------- the files

@lru_cache(maxsize=2)
def package(name: str) -> dict[str, Any]:
    with open(HERE / PACKAGES[name], encoding="utf-8") as fh:
        return json.load(fh)


def jobs(name: str = "stories") -> list[dict[str, Any]]:
    return list(package(name)["jobs"])


# Two stories to one business lesson (the owner, 2026-09-27), unless the weekly
# Improver's mix says otherwise (kernel/reel_money.py): `load_mix` reads it
# once a calendar tick. No mix, or an unreadable one, keeps two to one.
STORIES_PER_LESSON = 2.0
_stories_per_lesson = STORIES_PER_LESSON


async def load_mix(today: date | None = None) -> float:
    """This week's stories-per-lesson from `reel_mix`; never raises."""
    global _stories_per_lesson
    try:
        from ...kernel import reel_money
        ratio = await reel_money.stories_per_lesson(today or today_local())
    except Exception:                                   # noqa: BLE001
        ratio = STORIES_PER_LESSON
    _stories_per_lesson = ratio
    return ratio


def scheduled(stories_per_lesson: float | None = None) -> list[dict[str, Any]]:
    """Every job, in the order ATLAS makes them. the owner, 2026-09-27: "don't take
    the 30-day calendar literally -- do everything now". The packages are a
    plan, not a timetable: no dates gate anything. Two stories, then one
    business lesson, repeated -- or the mix's ratio (`load_mix`) -- each
    package keeps its own order, so a RETURN sequel still comes after its
    episode."""
    ratio = stories_per_lesson or _stories_per_lesson
    if not (0 < ratio < 1000):                          # also catches NaN
        ratio = STORIES_PER_LESSON
    stories, lessons = jobs("stories"), jobs("business")
    out: list[dict[str, Any]] = []
    s = n = 0
    while s < len(stories) or n < len(lessons):
        while s < len(stories) and (s < ratio * (n + 1) or n >= len(lessons)):
            out.append(stories[s])
            s += 1
        if n < len(lessons):
            out.append(lessons[n])
            n += 1
    return out


def is_series(j: dict[str, Any]) -> bool:
    """A story reel is an episode of a named series (RETURN, COURT, FOOD,
    KINDNESS); a business lesson is not."""
    return not is_business(j) and bool(j.get("series"))


def is_business(j: dict[str, Any]) -> bool:
    return str(j["job_id"]).startswith("business-")


def language(j: dict[str, Any]) -> str:
    """'ar' or 'en'. A business lesson alternates: Arabic on odd days."""
    if is_business(j):
        return "ar" if int(j["day"]) % 2 else "en"
    return "ar" if str(j.get("language", "ar")).startswith("ar") else "en"


def job(job_id: str) -> dict[str, Any] | None:
    for name in PACKAGES:
        for j in jobs(name):
            if j["job_id"] == job_id:
                return j
    return None


def enabled() -> bool:
    return os.getenv("REELS_CALENDAR", "on").strip().lower() not in (
        "0", "off", "false", "no")


def start_date() -> date:
    """`REELS_CALENDAR_START` (YYYY-MM-DD) shifts the whole month together;
    otherwise the package's own first day. A bad value is not guessed at:
    the package date stands."""
    raw = os.getenv("REELS_CALENDAR_START", "").strip()
    if raw:
        try:
            return date.fromisoformat(raw)
        except ValueError:
            pass
    return date.fromisoformat(package("stories")["start_date"])


def slot_at(j: dict[str, Any], start: date | None = None) -> datetime | None:
    """The proposed New York slot, shifted to `start`. A business lesson
    runs the day after the stories month, at its package's test slot."""
    first = start or start_date()
    if is_business(j):
        hh, _, mm = str(j.get("proposed_test_slot") or "19:30").partition(":")
        day = first + timedelta(days=len(jobs("stories")) + int(j["day"]) - 1)
        return datetime.combine(day, time(int(hh), int(mm or 0)), tzinfo=ZONE)
    raw = j.get("proposed_publish_at")
    if not raw:
        return None
    local = datetime.fromisoformat(raw).astimezone(ZONE)
    day = first + timedelta(days=int(j["day"]) - 1)
    return datetime.combine(day, time(local.hour, local.minute), tzinfo=ZONE)


def today_local(now: datetime | None = None) -> date:
    return (now or datetime.now(UTC)).astimezone(ZONE).date()


OPEN = ("draft", "rendering", "rendered", "carded")


async def active() -> bool:
    """While any packaged reel is still to be made or answered, the daily
    rotation stands aside so Veo's clips finish the packages (the owner's choice,
    2026-09-27). Unreadable counts as active: the rotation must not spend
    the calendar's clips on a guess."""
    if not enabled():
        return False
    try:
        row = await fetch_one(
            "SELECT count(*) AS n FROM reel_calendar WHERE status = ANY($1::text[])",
            list(OPEN))
    except Exception:                                   # noqa: BLE001
        return True
    return bool(row) and int(row["n"]) > 0


def daily_clips() -> int:
    try:
        return max(0, int(os.getenv("VEO_DAILY_CLIPS", str(DEFAULT_DAILY_CLIPS))))
    except ValueError:
        return DEFAULT_DAILY_CLIPS


# ----------------------------------------------------------------- the plan

@dataclass(frozen=True, slots=True)
class Part:
    beat_id: str
    index: int                                  # 1-based within the beat
    count: int                                  # parts in the beat
    seconds: float                              # the script's share of the beat
    lines: tuple[tuple[str, str], ...]          # (speaker_ar, text_ar)
    prompt: str                                 # the beat's generation prompt
    tongue: str = "Egyptian Arabic"             # what the lines are spoken in
    english: tuple[str, ...] = ()               # the caption for each line
    context: str = ""                           # a narrator line, never spoken

    @property
    def key(self) -> str:
        return f"{self.beat_id}-{self.index}"

    @property
    def words(self) -> int:
        return sum(len(text.split()) for _, text in self.lines)

    @property
    def length(self) -> float:
        """How long this part plays: its share of the beat, or the time its
        lines need when that is longer -- never past Veo's clip."""
        needed = self.words / SPEECH_WPS + 0.8 if self.lines else 0.0
        return min(float(veo.CLIP_SECONDS), max(MIN_PART_S, self.seconds, needed))


def _share(lines: list[tuple[str, str]], count: int) -> list[list[tuple[str, str]]]:
    """Whole lines dealt across `count` parts, balanced by words and kept in
    order. A line is never cut in half: two clips cannot share one mouth."""
    out: list[list[tuple[str, str]]] = [[] for _ in range(count)]
    total = sum(len(t.split()) for _, t in lines) or 1
    done = 0
    for speaker, text in lines:
        slot = min(count - 1, int(done * count / total))
        out[slot].append((speaker, text))
        done += len(text.split())
    return out


_SENTENCE = re.compile(r"(?<=[.!?؟،,;])\s+")


_SPEAKER = re.compile(r"^[^:]{1,40}:\s*")


def _english(j: dict[str, Any], beat: dict[str, Any], count: int) -> list[str]:
    """The English caption for each spoken line (the owner, 2026-09-27: Arabic
    voice, English captions, so everyone can follow). A story beat's
    `english_meaning` has one line per dialogue line, in order; a lesson's
    `voice_en` is cut at its sentences like the Arabic is."""
    if beat.get("dialogue_lines"):
        lines = beat["dialogue_lines"]
        meant = [ln for ln in str(beat.get("english_meaning") or "").splitlines()
                 if ln.strip()]
        if len(meant) != len(lines):
            return []
        return [_SPEAKER.sub("", m.strip()) for d, m in zip(lines, meant, strict=True)
                if d.get("text_ar") and not is_narrator(d.get("speaker_ar", ""))]
    said = str(beat.get("voice_en") or "").strip()
    return [part for part in _SENTENCE.split(said) if part.strip()]


# the owner, 2026-09-27: "only the characters should be speaking to one another
# -- don't be narrator." A lesson is therefore acted, not narrated: an
# on-screen host talks to a shop owner who answers with his face and hands.
HOST = "Nour"
BUSINESS_CAST = (
    "CAST (identical in every shot, original stylized 3D characters, not "
    "real people): Nour, the host, a woman of 30 with a short dark bob, "
    "round tortoiseshell glasses and a mustard cardigan; Sami, a shop owner "
    "of 45 with a grey-flecked beard and a navy work apron. They stand at "
    "Sami's shop counter, face each other and talk.")


def is_narrator(speaker: str) -> bool:
    return str(speaker).strip().startswith(NARRATOR)


def _lines(j: dict[str, Any], beat: dict[str, Any]) -> list[tuple[str, str]]:
    """Who says what in a beat -- characters only. A story beat's narrator
    lines are not spoken at all (they become a silent on-screen context
    line); a lesson's sentences are Nour's, said to Sami on camera, cut at
    the sentence so a long one is dealt across two clips."""
    if beat.get("dialogue_lines"):
        return [(str(d.get("speaker_ar", "")), str(d.get("text_ar", "")))
                for d in beat["dialogue_lines"]
                if d.get("text_ar") and not is_narrator(d.get("speaker_ar", ""))]
    said = str(beat.get(f"voice_{language(j)}") or "").strip()
    return [(HOST, part) for part in _SENTENCE.split(said) if part.strip()]


def _context(beat: dict[str, Any]) -> str:
    """A story beat's narrator line, in English, for the silent on-screen
    context caption -- the plot survives without a narrator's voice."""
    lines = beat.get("dialogue_lines") or []
    meant = [ln for ln in str(beat.get("english_meaning") or "").splitlines()
             if ln.strip()]
    if len(meant) != len(lines):
        return ""
    return " ".join(_SPEAKER.sub("", m.strip()) for d, m in zip(lines, meant,
                                                               strict=True)
                    if is_narrator(d.get("speaker_ar", "")))


def plan(j: dict[str, Any]) -> list[Part]:
    parts: list[Part] = []
    for beat in j["scenes"]:
        span = float(beat["end_seconds"]) - float(beat["start_seconds"])
        lines = _lines(j, beat)
        words = sum(len(t.split()) for _, t in lines)
        # Enough parts for the time the words need, not just the script's.
        need = max(span, words / SPEECH_WPS + 0.8 * max(1, len(lines)))
        count = max(1, min(len(lines) or 1, math.ceil(need / veo.CLIP_SECONDS)),
                    math.ceil(span / veo.CLIP_SECONDS))
        beat_id = str(beat.get("scene_id") or beat.get("id"))
        english = _english(j, beat, len(lines))
        aligned = len(english) == len(lines)
        shares = _share(lines, count)
        # English dealt the same way as the spoken lines: one to one when
        # they align, else in proportion across the beat's parts.
        english_shares = (None if aligned else
                          _share([("", e) for e in english], count))
        taken = 0
        for i, share in enumerate(shares, 1):
            if aligned:
                own = tuple(english[taken:taken + len(share)])
            else:
                own = tuple(t for _, t in (english_shares or [[]] * count)[i - 1])
            taken += len(share)
            prompt = str(beat["generation_prompt"])
            if is_business(j):
                prompt = f"{BUSINESS_CAST}\n{prompt}"
            parts.append(Part(beat_id, i, count, span / count, tuple(share),
                              prompt,
                              "Egyptian Arabic" if language(j) == "ar" else "English",
                              own, _context(beat) if i == 1 else ""))
    return parts


MOTION = render.MOTION


def clip_prompt(part: Part) -> str:
    """The beat's own prompt, the motion rule, then what THIS clip is and
    who says what to whom. Only characters speak, on camera, lip-synced;
    there is no narrator. The names are for Veo to know whose mouth moves;
    they are never spoken, and there is no text in the picture."""
    head = (f"{part.prompt}\n\n{render.STYLE}\n{MOTION}\n\nSHOT {part.index} of {part.count} for "
            f"beat {part.beat_id}; this shot lasts about {part.length:.0f} seconds.")
    if not part.lines:
        return (f"{head}\nNo dialogue in this shot: the characters act it out "
                f"with movement and expressions; natural sound, no narrator, "
                f"no voice-over. No on-screen text.")
    said = []
    for speaker, text in part.lines:
        if part.prompt.startswith(BUSINESS_CAST) and speaker == HOST:
            said.append(f"- Nour says to Sami, on camera: «{text}» (Sami listens, "
                        f"nods, reacts and gestures)")
        elif "صوت" in speaker:
            said.append(f"- {speaker}, heard as a remembered voice while the "
                        f"listener reacts on camera: «{text}»")
        else:
            said.append(f"- {speaker} says, on camera, to the other character: «{text}»")
    return (f"{head}\nThe characters speak these lines to each other aloud in "
            f"{part.tongue}, lip-synced, facing and reacting to each other (the "
            f"names before the colons are NOT spoken; there is no narrator):\n"
            + "\n".join(said)
            + f"\nThe speech starts within half a second and is finished by "
              f"{part.length:.0f} s. No on-screen text, subtitles or logos.")


def cues(parts: list[Part], lengths: list[float]) -> list[captions.Cue]:
    """The English captions over the spoken lines. When a beat's English
    lines match its spoken lines one to one, each is on screen while its
    own line is spoken; otherwise the beat's English is spread across the
    whole beat, so no part of it goes uncaptioned."""
    out: list[captions.Cue] = []
    at = 0.0
    colors: dict[str, int] = {}

    def paint(speaker: str) -> str:
        return captions.speaker_color(colors.setdefault(speaker.strip(), len(colors)))

    beats: dict[str, list[tuple[Part, float, float]]] = {}
    for part, length in zip(parts, lengths, strict=True):
        beats.setdefault(part.beat_id, []).append((part, at, length))
        at += length
    for group in beats.values():
        context = " ".join(p.context for p, _, _ in group if p.context)
        if context:
            begin = group[0][1]
            end = group[-1][1] + group[-1][2]
            out += [captions.Cue(c.start, c.end, c.text, top=True)
                    for c in captions.spread(context, begin + 0.1, end - 0.1,
                                             size=12)]
        aligned = all(len(p.english) == len(p.lines) for p, _, _ in group)
        if not aligned:
            english = " ".join(e for p, _, _ in group for e in p.english)
            start = group[0][1] + 0.2
            end = group[-1][1] + group[-1][2] - 0.3
            who = next((sp for p, _, _ in group for sp, _ in p.lines), "")
            out += [captions.Cue(c.start, c.end, c.text, color=paint(who))
                    for c in captions.spread(english, start, max(start + 0.5, end))]
            continue
        for part, begin, length in group:
            words = part.words
            if not words:
                continue
            speaking = min(length - 0.1, words / SPEECH_WPS + 0.2)
            t = begin + 0.2
            for (speaker, text), english in zip(part.lines, part.english, strict=True):
                span = max(0.3, (speaking - 0.2) * len(text.split()) / words)
                out += [captions.Cue(c.start, c.end, c.text, color=paint(speaker))
                        for c in captions.spread(english, t, t + span)]
                t += span
    return out


def caption(j: dict[str, Any]) -> str:
    """The approved caption: the package's own caption in the reel's
    language (it already carries the package's AI or illustration
    disclosure), then the "Made with AI" label every AI-stories caption
    ends with."""
    lang = language(j)
    body = str(j.get(f"caption_{lang}") or j.get("caption_ar")
               or j.get("caption_en") or j["title"]).strip()
    return f"{body}\n\n{AI_LABEL}"


def script_text(j: dict[str, Any]) -> str:
    """The draft row's body: what the owner reads in the dashboard."""
    if is_business(j):
        return (f"CALENDAR: {j['job_id']} (business day {j['day']}, "
                f"{language(j)})\nTITLE: {j['title']}\n\n"
                f"{j.get('script_' + language(j), '')}\n\nGUARDRAIL: "
                f"{j.get('guardrail', '')}\n\nCAPTION: {caption(j)}")
    return (f"CALENDAR: {j['job_id']} (day {j['day']}, {j.get('series', '')} "
            f"{j.get('episode', '')})\nTITLE: {j['title']}\n\n"
            f"{j.get('dialogue_ar', '')}\n\nMEANING:\n{j.get('english_meaning', '')}"
            f"\n\nCAPTION: {caption(j)}")


# ----------------------------------------------------------------- the bank

def clip_dir(job_id: str) -> str:
    from . import DATA_DIR
    return f"{DATA_DIR}/calendar/{job_id}"


def clip_path(job_id: str, part: Part) -> str:
    """A banked clip: at 60 fps, ready to trim."""
    return f"{clip_dir(job_id)}/{part.key}.mp4"


def raw_path(job_id: str, part: Part) -> str:
    """Veo's 24 fps clip as downloaded, before it is smoothed."""
    return f"{clip_dir(job_id)}/{part.key}.raw.mp4"


def missing(j: dict[str, Any]) -> list[Part]:
    """Parts Veo still has to make: neither banked nor downloaded."""
    return [p for p in plan(j) if not os.path.exists(clip_path(j["job_id"], p))
            and not os.path.exists(raw_path(j["job_id"], p))]


def unsmoothed(j: dict[str, Any]) -> list[Part]:
    return [p for p in plan(j) if not os.path.exists(clip_path(j["job_id"], p))
            and os.path.exists(raw_path(j["job_id"], p))]


def native_count(j: dict[str, Any]) -> int:
    """Clips kept at Veo's own motion because smoothing them failed."""
    return sum(os.path.exists(clip_path(j["job_id"], p) + ".native")
               for p in plan(j))


# The server has one CPU core (2026-09-27), so 24 -> 60 fps runs per 8 s
# clip as it arrives -- minutes each, spread across the day -- never as one
# pass over a whole reel. A tick smooths for at most SMOOTH_TICK_S and
# leaves the rest for the next.
SMOOTH_TICK_S = 1500.0


async def _smooth_pending(j: dict[str, Any]) -> tuple[int, int]:
    """Smooth downloaded clips until the tick's time is up. Returns (done,
    still waiting). A clip that cannot be smoothed is banked at its own
    motion, marked, and the card says how many."""
    started = asyncio.get_running_loop().time()
    done = 0
    pending = unsmoothed(j)
    for part in pending:
        if asyncio.get_running_loop().time() - started > SMOOTH_TICK_S:
            break
        raw, dest = raw_path(j["job_id"], part), clip_path(j["job_id"], part)
        tmp = dest + ".smoothing.mp4"
        # Only the seconds the reel will use are smoothed: on the server's
        # one core that is ~3 min of CPU per second of video (measured
        # 2026-09-27), and a part plays ~5 of Veo's 8 s on average.
        cut = dest + ".cut.mp4"
        try:
            await ffmpeg.run(ffmpeg.trim_argv(raw, min(part.length + 0.25,
                                                       veo.CLIP_SECONDS), cut))
            source = cut
        except ffmpeg.FfmpegError:
            source = raw
        used, _note = await ffmpeg.smooth(source, tmp)
        await asyncio.to_thread(_unlink, cut)
        if used == tmp:
            await asyncio.to_thread(os.replace, tmp, dest)
        else:
            await asyncio.to_thread(_mark_native, raw, dest)
        await asyncio.to_thread(_unlink, raw)
        done += 1
    return done, len(pending) - done


def _mark_native(raw: str, dest: str) -> None:
    shutil.copyfile(raw, dest)
    with open(dest + ".native", "w") as fh:
        fh.write("smoothing failed; Veo's own 24 fps motion\n")


def _unlink(path: str) -> None:
    try:
        os.unlink(path)
    except FileNotFoundError:
        pass


# The Gemini API's per-day quotas reset at midnight Pacific time, not at
# New York midnight: counting from the wrong midnight would spend a day's
# allowance that Google has not given back yet.
QUOTA_ZONE = ZoneInfo("America/Los_Angeles")


def reserved_clips(now: datetime | None = None) -> int:
    """Clips held back each day for work outside this pipeline:
    `VEO_RESERVED_CLIPS` a day through `VEO_RESERVED_UNTIL` (a Pacific date).
    2026-09-28: the Code Chaos host timers render 8 clips at 07:03 UTC, three
    minutes after Veo's reset, through Oct 2 -- without this, a tick at 07:00
    would spend them first and the episode would fail on a 429."""
    until = os.getenv("VEO_RESERVED_UNTIL", "").strip()
    try:
        clips = max(0, int(os.getenv("VEO_RESERVED_CLIPS", "0")))
        if not until or clips == 0:
            return 0
        day = (now or datetime.now(UTC)).astimezone(QUOTA_ZONE).date()
        return clips if day <= date.fromisoformat(until) else 0
    except ValueError:
        return 0


async def clips_today(now: datetime | None = None) -> int:
    """Veo clips accepted since the quota's own midnight (Pacific) -- every
    render counts, the rotation's too, because the quota is the project's --
    or the day's reserve, whichever is more: a reserve not yet spent is
    counted as spent, so nothing here takes it."""
    now = now or datetime.now(UTC)
    midnight = datetime.combine(now.astimezone(QUOTA_ZONE).date(), time(0),
                                tzinfo=QUOTA_ZONE)
    row = await fetch_one(
        "SELECT coalesce(sum(seconds), 0) AS seconds FROM veo_usage WHERE at >= $1",
        midnight)
    used = max(0, int(row["seconds"]) if row else 0) // veo.CLIP_SECONDS
    return max(used, reserved_clips(now))


async def ensure_loaded() -> None:
    """One `reel_calendar` row per job, inserted once and never duplicated
    (the job id is the key, so a restart or a redeploy changes nothing)."""
    for name in PACKAGES:
        for j in jobs(name):
            await execute_sql(
                """INSERT INTO reel_calendar (job_id, package, day, title)
                   VALUES ($1, $2, $3, $4) ON CONFLICT (job_id) DO NOTHING""",
                j["job_id"], name, int(j["day"]), str(j["title"]))


async def _reserve_clip() -> str | None:
    """Reserve one clip's seconds under the render lock, or say why not."""
    cap = veo.monthly_cap()
    async with render._VEO_CAP_LOCK:
        used = await veo.seconds_this_month()
        if used + veo.CLIP_SECONDS > cap:
            return (f"Veo cap: {used}/{cap} s used this month (read "
                    f"{datetime.now(UTC):%H:%M} UTC)")
        await veo.record(veo.CLIP_SECONDS, None)
    return None


async def _bank(j: dict[str, Any], budget: int) -> tuple[int, str | None, bool]:
    """Make up to `budget` of this job's missing clips on Veo, in story
    order, then -- once Veo's DAILY allowance is used up (the budget, which
    counts the reserve, or Veo's own daily-quota 429) -- the rest on
    Higgsfield under its monthly dollar cap, when it is configured.
    Returns (made, why it stopped early, whether a generator REFUSED a clip
    -- as opposed to a quota or a cap, which only mean "later")."""
    made = 0
    stopped: str | None = None
    for part in missing(j):
        if made >= budget:
            break
        if (why := await _reserve_clip()) is not None:
            return made, why, False                 # Veo's month: no overflow
        try:
            data = await veo.clip(clip_prompt(part))
        except veo.VeoError as exc:
            if not exc.accepted:
                await veo.record(-veo.CLIP_SECONDS, None)
            if exc.status_code == 429:
                what = "daily quota" if exc.daily_quota else "quota"
                said = f"Veo {what} exhausted at {part.key}; resumes next tick"
                if not exc.daily_quota:
                    return made, said, False
                stopped = said
                break
            return made, f"Veo refused {part.key}: {exc}", True
        dest = raw_path(j["job_id"], part)
        await asyncio.to_thread(_save, dest, data)
        made += 1
    else:
        return made, None, False                    # nothing left to make
    if not higgsfield.configured():
        return made, stopped, False
    more, why, refused = await _overflow(j)
    if more or why:
        stopped = "; ".join(x for x in (
            stopped, f"{more} clip(s) made on Higgsfield" if more else None,
            why) if x)
    return made + more, stopped, refused


async def _overflow(j: dict[str, Any]) -> tuple[int, str | None, bool]:
    """The job's still-missing clips on Higgsfield, each one's price
    reserved first against `HIGGSFIELD_MONTHLY_USD`; a clip that would
    cross the cap is never started. (made, why it stopped, refused).

    Never raises (short of cancellation): anything unexpected on this path
    is a failed overflow, logged, and `render_job` carries on with what was
    banked. Nothing is refunded then -- whether Higgsfield charged is not
    known, and the cap may over-count but never under-count."""
    made = [0]
    try:
        return await _overflow_parts(j, made)
    except Exception as exc:                            # noqa: BLE001
        import logging
        said = higgsfield._masked(str(exc))[:200]
        logging.getLogger("atlas.content").warning(
            "Higgsfield overflow for %s failed unexpectedly: %s: %s",
            j.get("job_id"), type(exc).__name__, said)
        return made[0], (f"Higgsfield overflow failed ({type(exc).__name__}); "
                         f"what it reserved stays booked"), False


async def _overflow_parts(j: dict[str, Any],
                          made: list[int]) -> tuple[int, str | None, bool]:
    story = higgsfield.set_story(str(j["job_id"]))     # one resolution per day (mix)
    try:
        return await _overflow_parts_of(j, made)
    finally:
        higgsfield.reset_story(story)


async def _overflow_parts_of(j: dict[str, Any],
                             made: list[int]) -> tuple[int, str | None, bool]:
    for part in missing(j):
        prompt = clip_prompt(part)
        price, why = await higgsfield.reserve(prompt, job_id=j["job_id"],
                                              part=part.key)
        if price is None:
            return made[0], f"Higgsfield overflow stopped: {why}", False
        try:
            data = await higgsfield.clip(prompt)
        except higgsfield.HiggsfieldError as exc:
            if not exc.charged:
                await higgsfield.record(-price.usd, job_id=j["job_id"],
                                        part=part.key, seconds=price.seconds,
                                        res=price.resolution,
                                        price_read=price.read)
            if exc.refused:
                return made[0], f"Higgsfield refused {part.key}: {exc}", True
            return made[0], f"Higgsfield stopped at {part.key}: {exc}", False
        note = await _bank_foreign(j, part, data)
        await asyncio.to_thread(_write_marker, higgsfield_marker(j["job_id"], part),
                                f"higgsfield {higgsfield.MODEL} {price.said()}"
                                f"{note}\n")
        made[0] += 1
    return made[0], None, False


def higgsfield_marker(job_id: str, part: Part) -> str:
    """Beside a clip Higgsfield made: which backend, model and price."""
    return clip_path(job_id, part) + ".higgsfield"


def higgsfield_count(j: dict[str, Any]) -> int:
    return sum(os.path.exists(higgsfield_marker(j["job_id"], p)) for p in plan(j))


def higgsfield_unpriced(j: dict[str, Any]) -> int:
    """Higgsfield clips of this job whose price was not read (booked at the
    worst case), as their markers say."""
    count = 0
    for p in plan(j):
        try:
            with open(higgsfield_marker(j["job_id"], p), encoding="utf-8") as fh:
                count += "price not read" in fh.read()
        except OSError:
            continue
    return count


def _write_marker(path: str, text: str) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)


async def _bank_foreign(j: dict[str, Any], part: Part, data: bytes) -> str:
    """Bank a clip another generator made, normalised to Veo's frame size
    and sound first. If normalising fails the paid clip is banked as it
    came, and the marker says so -- never silently."""
    dest = raw_path(j["job_id"], part)
    came, norm = dest + ".hf.mp4", dest + ".norm.mp4"
    await asyncio.to_thread(_save, came, data)
    try:
        await ffmpeg.run(ffmpeg.normalize_argv(came, norm))
    except ffmpeg.FfmpegError as exc:
        await asyncio.to_thread(os.replace, came, dest)
        return f"; NOT normalised ({exc})"
    await asyncio.to_thread(os.replace, norm, dest)
    await asyncio.to_thread(_unlink, came)
    return ""


def _save(dest: str, data: bytes) -> None:
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    tmp = dest + ".part"
    with open(tmp, "wb") as fh:
        fh.write(data)
    os.replace(tmp, dest)           # a half-written clip never looks banked


async def _assemble(j: dict[str, Any]) -> ActionResult:
    """Every clip is banked: trim, join, caption, master, and file it as a
    story_script draft with a finished content_media row."""
    parts = plan(j)
    row = await fetch_one(
        """INSERT INTO content_drafts (kind, source, title, body)
           VALUES ('story_script', $1, $2, $3) RETURNING id""",
        f"calendar:{j['job_id']}", str(j["title"]), script_text(j))
    draft_id = int(row["id"])
    media_id = await render._new_row(draft_id, "veo")
    work = await asyncio.to_thread(tempfile.mkdtemp, prefix="atlas-cal-")
    try:
        try:
            trimmed, lengths = [], []
            for i, part in enumerate(parts):
                src = clip_path(j["job_id"], part)
                length = min(part.length, await ffmpeg.duration(src))
                out = os.path.join(work, f"t{i:03d}.mp4")
                await ffmpeg.run(ffmpeg.trim_argv(src, length, out))
                trimmed.append(out)
                lengths.append(length)
            listing = os.path.join(work, "clips.txt")
            joined = os.path.join(work, "joined.mp4")
            ass = os.path.join(work, "captions.ass")
            out = os.path.join(work, "out.mp4")
            await asyncio.to_thread(render._write, listing,
                                    ffmpeg.concat_list(trimmed))
            await ffmpeg.run(ffmpeg.concat_argv(listing, joined))
            total = await ffmpeg.duration(joined)
            # the owner, 2026-09-27: captions on series only. A story reel is an
            # episode of its series; a business lesson has none -- only the
            # "Made with AI" tag, which every reel carries.
            burned = cues(parts, lengths) if is_series(j) else []
            await asyncio.to_thread(render._write, ass, captions.build_scripted_ass(
                burned, total, arabic=False))
            await ffmpeg.run(ffmpeg.burn_argv(joined, ass, out),
                             timeout=ffmpeg.BURN_TIMEOUT_S)
            master = await render.write_master(joined, ass, media_id)
        except ffmpeg.FfmpegError as exc:
            return await render._fail(media_id, f"ffmpeg: {exc}")
        native = native_count(j)
        fps_note = ("motion-interpolated from 24 to 60 fps"
                    + (f" ({native} of {len(parts)} clips kept at Veo's own "
                       f"motion: smoothing failed)" if native else ""))
        overflow = higgsfield_count(j)
        made_by = (f"{len(parts)} Veo clip(s) at {veo.resolution()}" if not overflow
                   else f"{len(parts) - overflow} Veo clip(s) at {veo.resolution()} "
                        f"+ {overflow} Higgsfield overflow clip(s) "
                        f"({higgsfield.MODEL}, {higgsfield.resolution()})")
        if unpriced := await asyncio.to_thread(higgsfield_unpriced, j):
            made_by += (f" -- price not read for {unpriced} Higgsfield clip(s), "
                        f"booked at worst case")
        notes = [(f"{made_by}, trimmed to "
                  f"{total:.0f} s (target {j.get('duration_target_seconds')} s)"),
                 ((f"{'Egyptian Arabic voice, ' if language(j) == 'ar' else ''}"
              f"English captions burned from the script's own translation")
             if is_series(j) else "no captions (not a series), only the AI tag"),
                 fps_note, "loudness normalised to -14 LUFS",
                 "delivery 1080x1920 at 60 fps"]
        if master:
            notes.append(master)
        result = await render._finish(media_id, draft_id, "veo", out, notes,
                                      captions=bool(burned))
    finally:
        await asyncio.to_thread(shutil.rmtree, work, True)
    if result.ok:
        await execute_sql(
            """UPDATE reel_calendar SET status = 'rendered', draft_id = $2,
                      media_id = $3, note = NULL, updated_at = now()
                WHERE job_id = $1""", j["job_id"], draft_id, media_id)
    return result


# ----------------------------------------------------------------- the action

async def _stories_job(job_id: str) -> tuple[dict[str, Any] | None, str | None]:
    j = job(job_id)
    if j is None:
        return None, f"no calendar job {job_id!r}"
    return j, None


async def render_job(job_id: str) -> ActionResult:
    """Bank this day's missing clips within today's allowance and, once all
    are banked, assemble the reel. Tier AUTO: files and draft rows only."""
    j, why = await _stories_job(job_id)
    if j is None:
        return ActionResult(False, why or "no job")
    if (why := ffmpeg.missing()) is not None:
        return ActionResult(False, f"refused: {why}")
    if not veo.configured():
        return ActionResult(False, "Veo not configured (GEMINI_API_KEY unset)")
    before = len(missing(j))
    budget = max(0, daily_clips() - await clips_today())
    made, stopped, refused = await _bank(j, budget) if before else (0, None, False)
    left = before - made
    smoothed, waiting = await _smooth_pending(j)
    if not left and waiting:
        await execute_sql(
            """UPDATE reel_calendar SET status = 'rendering', note = $2,
                      updated_at = now() WHERE job_id = $1""",
            j["job_id"], f"{waiting} clip(s) still being smoothed to 60 fps")
        return ActionResult(True, (
            f"{j['job_id']}: every clip is made; {smoothed} smoothed to 60 fps "
            f"this tick, {waiting} to go"),
            evidence={"complete": False, "made": made, "left": 0,
                      "smoothing": waiting})
    read = datetime.now(UTC)
    if refused:
        # Twice refused and the day is blocked, in words, and the month
        # moves on: retrying a refusal every half hour would spend the
        # allowance (a clip Veo took before refusing stays counted) and
        # stall every later day behind it.
        row = await fetch_one(
            """UPDATE reel_calendar SET failures = failures + 1,
                      status = CASE WHEN failures + 1 >= $3 THEN 'blocked'
                                    ELSE 'rendering' END,
                      note = $2, updated_at = now()
                WHERE job_id = $1 RETURNING status""",
            j["job_id"], stopped, MAX_REFUSALS)
        blocked = bool(row) and row["status"] == "blocked"
        return ActionResult(True, (
            f"{j['job_id']}: {made} clip(s) banked, then {stopped}"
            + (f" -- refused {MAX_REFUSALS} times, so the day is BLOCKED until "
               f"the owner looks at it" if blocked else "; one more try next tick")),
            evidence={"complete": False, "made": made, "left": left,
                      "blocked": blocked})
    if left:
        # A clip Veo DID make clears earlier strikes: Veo sometimes finishes
        # with no video (its content filter) and the same prompt works on the
        # next try (D01-B01-1, 2026-09-28) -- two strikes are for a beat Veo
        # keeps refusing, not for one hiccup long ago.
        await execute_sql(
            """UPDATE reel_calendar SET status = 'rendering', note = $2,
                      failures = CASE WHEN $3 > 0 THEN 0 ELSE failures END,
                      updated_at = now() WHERE job_id = $1""",
            j["job_id"], stopped or "daily clip allowance used", made)
        return ActionResult(True, (
            f"{j['job_id']}: {made} clip(s) banked, {left} still to make "
            f"({stopped or f'today allowance of {daily_clips()} clips used'}; "
            f"read {read:%H:%M} UTC)"),
            evidence={"complete": False, "made": made, "left": left})
    result = await _assemble(j)
    evidence = dict(result.evidence or {})
    evidence.update({"complete": result.ok, "made": made, "left": 0})
    return ActionResult(result.ok, f"{j['job_id']}: {result.detail}",
                        evidence=evidence, rollback_token=result.rollback_token)


async def render_job_dry(job_id: str) -> ActionResult:
    j, why = await _stories_job(job_id)
    if j is None:
        return ActionResult(False, why or "no job")
    want = len(missing(j))
    budget = max(0, daily_clips() - await clips_today())
    overflow = (f"; the other {want - budget} on Higgsfield while "
                f"${higgsfield.monthly_cap():.2f} this month allows"
                if want > budget and higgsfield.configured() else "")
    return ActionResult(True, (
        f"would bank up to {min(want, budget)} of {want} missing clip(s) for "
        f"{j['job_id']} on Veo ({budget} left of today's {daily_clips()})"
        f"{overflow}, then assemble once none are missing"))


async def render_job_rollback(token: str) -> ActionResult:
    """Like `content.render_video`'s: the finished media is marked failed and
    the day goes back to rendering; the banked clips stay (they were paid for)."""
    await execute_sql(
        "UPDATE content_media SET status = 'failed' WHERE id = $1", int(token))
    await execute_sql(
        """UPDATE reel_calendar SET status = 'rendering', media_id = NULL,
                  updated_at = now() WHERE media_id = $1""", int(token))
    return ActionResult(True, f"media {token} marked failed")


# ----------------------------------------------------------------- reading

async def rows(package_name: str = "stories") -> list[dict[str, Any]]:
    return [dict(r) for r in await fetch_all(
        """SELECT job_id, day, title, status, draft_id, media_id, proposal_id,
                  note, card_day, redos
             FROM reel_calendar WHERE package = $1 ORDER BY day""", package_name)]


async def redo(job_id: str, why: str) -> None:
    """A finished reel that failed a quality check is made again, once: its
    banked clips are dropped (they may be the cause) and the day goes back
    to the queue. A second failure is a card for the owner."""
    await asyncio.to_thread(shutil.rmtree, clip_dir(job_id), True)
    await execute_sql(
        """UPDATE reel_calendar SET status = 'draft', media_id = NULL,
                  redos = redos + 1, note = $2, updated_at = now()
            WHERE job_id = $1""", job_id, f"being made again: {why}"[:300])


def dependencies(j: dict[str, Any]) -> list[str]:
    return [str(d) for d in j.get("dependencies") or []]
