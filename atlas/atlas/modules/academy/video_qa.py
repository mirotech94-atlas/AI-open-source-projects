"""The pre-publish QA gates of the Atlas Academy video system (batch 9).

docs/academy-video-system/qa-checklist.md is run by hand on a phone; the
rows a machine can measure are measured here, on the finished file and on
the pages the render drew, so a lesson or a teaching Short that fails one
is held, never posted. `tutorials.ready()` calls `lesson_refusal`; the
teaching Short's render calls `short_refusal` before its row is finished.

Each gate returns None (pass) or one short plain sentence (held). A gate
that cannot run -- ffprobe missing, a frame that will not decode, a page
that was never measured -- returns a sentence too: invariant 7, unknown is
never OK.

  * Encoding (production-system.md "Delivery spec"): H.264 High, 30 fps,
    yuv420p, AAC at 48 kHz, read by ffprobe from the final file; a lesson
    is 1920x1080, a Short 1080x1920 and 20-40 s. The 16-24 Mbps band is
    not gated (open question 11 in the gap analysis).
  * Music (narration.md "Delivery"; rule 22): the bed sounds only under
    the sting and the end card. The mix multiplies the bed by an envelope
    that is exactly zero outside the windows it is given
    (tutorial_music.mix_argv), and the render records those windows; the
    honest cheap check is that every recorded window lies inside the
    sting's span or the end card's. A measurement of the file itself would
    need a voice-only reference, and the render deletes its dry join.
  * Timing (result 0-6 s, sting 6-7 s, end card >= 15 s to the last
    frame) is tutorials.bookends_refusal's, already in ready(); not here.
  * End-card reserve (qa-checklist.md "Continuation"): the right 640 px of
    the end card inside the safe area (x 1184-1824, y 96-984) are flat
    navy, sampled from three decoded frames of the final file.
  * Speech rate (narration.md "Pace"): 130-150 wpm for explanation,
    110-120 on a value or code, never above 160. Measured over the whole
    narration from the manifest's timed lines, so the band is the spec's
    whole range, RATE_MIN-RATE_MAX; the result line (a fixed 6 s) and the
    end-card line (held 15 s) are left out, they are not speech pace.
  * Type size and safe area (qa-checklist.md "Readability"): every text
    node Chromium drew on a 16:9 page is at least `support` (28 px; the
    mono `value-tag`, 26 px, is the spec's one exception) and on a Short
    at least `shorts-caption` (60 px), and its glyph box sits inside the
    format's safe area. Measured at render time on the actual pages
    (`Pages.measure`, called by tutorials.Shooter), recorded in the
    manifest and re-read here.

Not a gate: `notes()` names what the checklist forbids but the code still
does everywhere, so a hold would stop every video; it is shown on the
review card instead.
"""
from __future__ import annotations

import asyncio
import json
import re
from dataclasses import dataclass, field
from fractions import Fraction
from typing import TYPE_CHECKING, Any

from . import video_tokens as vt

if TYPE_CHECKING:
    from .tutorials import Manifest

FPS = vt.FPS

# ---------------------------------------------------------------- encoding
VIDEO_CODEC = "h264"
PROFILE = "High"
PIX_FMT = "yuv420p"
AUDIO_CODEC = "aac"
SAMPLE_RATE = 48000
FPS_SLACK = 0.05                     # a joined file's average: 29.9993 is 30
LESSON_SIZE = (1920, 1080)
SHORT_SIZE = (1080, 1920)
SHORT_SECONDS = (20.0, 40.0)         # D4: 20-40 s

PROBE_ENTRIES = ("stream=codec_type,codec_name,profile,width,height,pix_fmt,"
                 "r_frame_rate,avg_frame_rate,sample_rate:format=duration")
UNREAD = "the file's encoding could not be read, so it is held"


def _rate(text: object) -> float | None:
    """ffprobe's "30/1" as 30.0; None for "0/0" or anything unreadable."""
    try:
        r = Fraction(str(text))
    except (ValueError, ZeroDivisionError):
        return None
    return float(r) if r > 0 else None


def read_probe(data: dict[str, Any]) -> dict[str, Any]:
    """ffprobe's JSON (PROBE_ENTRIES) as the gates read it."""
    streams = data.get("streams") or []
    v = next((x for x in streams if x.get("codec_type") == "video"), {})
    a = next((x for x in streams if x.get("codec_type") == "audio"), {})
    try:
        seconds = float((data.get("format") or {}).get("duration") or 0)
    except (TypeError, ValueError):
        seconds = 0.0
    try:
        rate = int(a.get("sample_rate") or 0)
    except (TypeError, ValueError):
        rate = 0
    return {"width": v.get("width"), "height": v.get("height"), "seconds": seconds,
            "audio": bool(a), "video_codec": v.get("codec_name"),
            "profile": v.get("profile"), "pix_fmt": v.get("pix_fmt"),
            "fps": _rate(v.get("r_frame_rate")), "avg_fps": _rate(v.get("avg_frame_rate")),
            "audio_codec": a.get("codec_name"), "sample_rate": rate}


async def probe(path: str) -> tuple[dict[str, Any] | None, str | None]:
    """(read_probe of `path`, None) or (None, why): ffprobe on the file."""
    from ..content import ffmpeg
    try:
        out = await ffmpeg.run(["ffprobe", "-v", "error", "-show_entries", PROBE_ENTRIES,
                                "-of", "json", path], timeout=60)
        data = json.loads(out.decode() or "{}")
    except (ffmpeg.FfmpegError, ValueError) as exc:
        return None, f"ffprobe: {exc}"
    return read_probe(data), None


def encoding_refusal(p: dict[str, Any], *, size: tuple[int, int] | None = None,
                     seconds: tuple[float, float] | None = None) -> str | None:
    """The delivery spec, read from the probe of the final file."""
    keys = ("video_codec", "profile", "pix_fmt", "fps", "audio_codec", "sample_rate")
    if not p.get("video_codec") or any(k not in p for k in keys):
        return UNREAD
    if p["video_codec"] != VIDEO_CODEC:
        return f"the video is {p['video_codec']}, not H.264"
    if p["profile"] != PROFILE:
        return f"the video is H.264 {p['profile'] or 'of no profile'}, not High profile"
    if p["pix_fmt"] != PIX_FMT:
        return f"the picture is {p['pix_fmt']}, not yuv420p"
    fps, avg = p["fps"], p.get("avg_fps")
    if fps is None or abs(fps - FPS) > 1e-6:
        return f"the video runs at {fps or 0:g} fps, not {FPS}"
    if avg is not None and abs(avg - FPS) > FPS_SLACK:
        return f"the frame rate drifts to {avg:.2f} fps on average, not {FPS}"
    if size is not None and (p.get("width"), p.get("height")) != size:
        return f"the file is {p.get('width')}x{p.get('height')}, not {size[0]}x{size[1]}"
    if not p.get("audio"):
        return "the file has no sound"
    if p["audio_codec"] != AUDIO_CODEC:
        return f"the sound is {p['audio_codec']}, not AAC"
    if p["sample_rate"] != SAMPLE_RATE:
        return (f"the sound is {p['sample_rate'] / 1000:g} kHz, not "
                f"{SAMPLE_RATE / 1000:g} kHz")
    if seconds is not None and not seconds[0] <= p.get("seconds", 0.0) <= seconds[1]:
        return (f"it runs {p.get('seconds', 0.0):.1f} s, outside "
                f"{seconds[0]:g}-{seconds[1]:g} s")
    return None


# ------------------------------------------------------------------- music
SPAN_SLACK = 1.5 / FPS               # the manifest rounds to 0.01 s


def music_refusal(m: Manifest) -> str | None:
    """Every window the bed was mixed in lies inside the sting's span (a
    result-first lesson) or the end card's; a render with none recorded
    had its bed under the whole lesson."""
    if not m.music_windows:
        return "the music windows were not recorded, so the bed may run under the lesson"
    allowed: list[tuple[float, float]] = []
    if m.opening == "result":
        allowed.append((m.sting_at, m.sting_at + m.sting_seconds))
    if m.card_seconds > 0:
        allowed.append((m.card_at, m.card_at + m.card_seconds))
    for a, b in m.music_windows:
        if not any(lo - SPAN_SLACK <= a and b <= hi + SPAN_SLACK for lo, hi in allowed):
            return (f"music plays at {a:.1f}-{b:.1f} s, outside the sting and the "
                    "end card")
    return None


# ------------------------------------------------------------- speech rate
# narration.md "Pace": 130-150 wpm explaining, 110-120 reading a value or
# code, never above 160. The whole narration mixes the two, so the overall
# band is the spec's slowest pace to its ceiling.
RATE_MIN = 110.0
RATE_MAX = 160.0
RATE_MIN_SECONDS = 30.0              # less narration than this is not a rate
_CUE = re.compile(r"\[[^\]]*\]")
_WORD = re.compile(r"[A-Za-z0-9][A-Za-z0-9'’.%$-]*")


def words(said: str) -> int:
    """Spoken words in a timed line: a [cue] for the voice is not one."""
    return len(_WORD.findall(_CUE.sub(" ", said)))


def speech_rate(m: Manifest) -> tuple[float, float] | None:
    """(words a minute, seconds measured) over the narrated lines, the
    result line and the end-card line left out; None with no lines."""
    n, secs = 0, 0.0
    for a, b, said in m.timings:
        if m.opening == "result" and a < m.sting_at:
            continue                 # the result line: held to the sting, a fixed 6 s
        if m.card_seconds > 0 and a >= m.card_at - SPAN_SLACK:
            continue                 # the end card's line: held 15 s
        if b > a:
            n, secs = n + words(said), secs + (b - a)
    return (n / secs * 60, secs) if secs > 0 else None


def rate_refusal(m: Manifest) -> str | None:
    got = speech_rate(m)
    if got is None or got[1] < RATE_MIN_SECONDS:
        return "the narration's timings are missing, so its pace cannot be checked"
    wpm, _ = got
    if wpm > RATE_MAX:
        return f"the narration runs at {wpm:.0f} words a minute, faster than {RATE_MAX:g}"
    if wpm < RATE_MIN:
        return f"the narration runs at {wpm:.0f} words a minute, slower than {RATE_MIN:g}"
    return None


# ------------------------------------------------- type size and safe area

@dataclass(frozen=True)
class PageRule:
    name: str
    floor: float                     # px: nothing drawn smaller
    mono_floor: float                # px: the mono value-tag's exception
    safe: tuple[float, float, float, float]   # x0, y0, x1, y1


W16, H16 = LESSON_SIZE
W9, H9 = SHORT_SIZE
RULES: dict[tuple[int, int], PageRule] = {
    LESSON_SIZE: PageRule("16:9", vt.SUPPORT.size, vt.VALUE_TAG.size,
                          (vt.SAFE_16_9, vt.SAFE_16_9, W16 - vt.SAFE_16_9,
                           H16 - vt.SAFE_16_9)),
    SHORT_SIZE: PageRule("Short", vt.SHORTS_CAPTION.size, vt.SHORTS_CAPTION.size,
                         (vt.SAFE_9_16_X, vt.SAFE_9_16_TOP, W9 - vt.SAFE_9_16_X,
                          H9 - vt.SAFE_9_16_BOTTOM)),
}
# The preview banner (tutorial_design .preview) is on preview frames only,
# which are never uploaded: not measured.
SKIP = ".preview"
BOX_SLACK = 0.5                      # sub-pixel layout
FAULTS_KEPT = 8
# Each drawn text node: its words, size, family and line boxes. A line's
# client rect is the font's content area (1.3 em in Plex), which overhangs
# a tighter line-height; it is inset to the line box, the box CSS places
# against the safe edge. A node in a hidden, transparent or undisplayed
# element is not drawn and not measured.
MEASURE_JS = """(skip) => {
  const out = [];
  const walk = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
  for (let n = walk.nextNode(); n; n = walk.nextNode()) {
    const text = n.textContent.replace(/\\s+/g, ' ').trim();
    const el = n.parentElement;
    if (!text || !el || el.closest(skip)) continue;
    if (!el.checkVisibility({opacityProperty: true, visibilityProperty: true})) continue;
    const cs = getComputedStyle(el);
    const lh = parseFloat(cs.lineHeight);
    const r = document.createRange(); r.selectNodeContents(n);
    let x0 = 1e9, y0 = 1e9, x1 = -1e9, y1 = -1e9;
    for (const b of r.getClientRects()) {
      if (b.width < 1 || b.height < 1) continue;
      const inset = isNaN(lh) ? 0 : Math.max(0, (b.height - lh) / 2);
      x0 = Math.min(x0, b.left); x1 = Math.max(x1, b.right);
      y0 = Math.min(y0, b.top + inset); y1 = Math.max(y1, b.bottom - inset);
    }
    if (x1 < x0) continue;
    out.push({text: text.slice(0, 40), px: parseFloat(cs.fontSize),
              mono: /mono/i.test(cs.fontFamily),
              left: x0, top: y0, right: x1, bottom: y1});
  }
  return out;
}"""


def faults(texts: list[dict[str, Any]], rule: PageRule) -> list[str]:
    """What breaks `rule` among a page's measured texts, said plainly."""
    out = []
    x0, y0, x1, y1 = rule.safe
    for t in texts:
        floor = rule.mono_floor if t.get("mono") else rule.floor
        if t["px"] < floor:
            out.append(f"\"{t['text']}\" is {t['px']:g} px, under {floor:g} px")
        if (t["left"] < x0 - BOX_SLACK or t["top"] < y0 - BOX_SLACK
                or t["right"] > x1 + BOX_SLACK or t["bottom"] > y1 + BOX_SLACK):
            out.append(f"\"{t['text']}\" sits outside the {rule.name} safe area")
    return out


@dataclass
class Pages:
    """What tutorials.Shooter measured on every page it drew at a video
    size: pages measured, the smallest text, and each fault once."""
    measured: int = 0
    min_px: float = 0.0
    found: list[str] = field(default_factory=list)

    async def measure(self, page: Any, w: int, h: int) -> None:
        """Measure one drawn page (a Playwright page at its final state).
        A page of no video size (the thumbnail, the trace dot) is not a
        frame and is skipped; one that cannot be measured is a fault."""
        rule = RULES.get((w, h))
        if rule is None:
            return
        try:
            texts = await page.evaluate(MEASURE_JS, SKIP)
        except Exception as exc:  # noqa: BLE001
            self.add([f"a {rule.name} page could not be measured ({type(exc).__name__})"])
            return
        self.measured += 1
        sizes = [float(t["px"]) for t in texts]
        if sizes:
            low = min(sizes)
            self.min_px = low if not self.min_px else min(self.min_px, low)
        self.add(faults(texts, rule))

    def add(self, said: list[str]) -> None:
        self.found += [s for s in dict.fromkeys(said) if s not in self.found]

    def record(self) -> dict[str, Any]:
        """The Manifest's fields for it."""
        return {"pages_measured": self.measured, "min_text_px": round(self.min_px, 1),
                "text_faults": self.found[:FAULTS_KEPT],
                "text_fault_count": len(self.found)}


def pages_refusal(measured: int, found: list[str], total: int = 0) -> str | None:
    """`total` is how many faults there were; the manifest keeps only the
    first FAULTS_KEPT of them in `found`."""
    if found:
        n = max(total, len(found))
        more = f" (and {n - 1} more)" if n > 1 else ""
        return f"the drawn text breaks the spec: {found[0]}{more}"
    if measured <= 0:
        return "the drawn pages were not measured, so their type and safe area are unknown"
    return None


# ------------------------------------------------------- end-card reserve
RESERVE = (W16 - vt.SAFE_16_9 - vt.ENDSCREEN_RESERVE, vt.SAFE_16_9,
           W16 - vt.SAFE_16_9, H16 - vt.SAFE_16_9)   # x0, y0, x1, y1: 1184-1824 x 96-984
NAVY_RGB = tuple(int(vt.NAVY[i:i + 2], 16) for i in (1, 3, 5))
NAVY_SLACK = 6                       # levels per channel: the YUV 4:2:0 round trip


def reserve_times(m: Manifest) -> list[float]:
    """Where the end card is sampled: just after it settles, its middle,
    and two frames before the file's last."""
    a, s = m.card_at, m.card_seconds
    return [round(a + min(1.0, s / 4), 3), round(a + s / 2, 3),
            round(m.seconds - 2 / FPS, 3)]


def off_navy(raw: bytes) -> int:
    """How many pixels of an rgb24 frame stray more than NAVY_SLACK from
    navy in any channel."""
    import numpy as np
    px = np.frombuffer(raw, dtype=np.uint8).reshape(-1, 3).astype(np.int16)
    return int((np.abs(px - np.array(NAVY_RGB, dtype=np.int16)) > NAVY_SLACK)
               .any(axis=1).sum())


async def frame_rgb(video: str, at: float, box: tuple[int, int, int, int]) -> bytes:
    """One decoded frame at `at` s, cropped to `box`, as rgb24 bytes.
    ffmpeg.FfmpegError when it cannot be read."""
    from ..content import ffmpeg
    x0, y0, x1, y1 = box
    return await ffmpeg.run(["ffmpeg", "-v", "error", "-ss", f"{at:.3f}", "-i", video,
                             "-frames:v", "1", "-vf", f"crop={x1 - x0}:{y1 - y0}:{x0}:{y0}",
                             "-f", "rawvideo", "-pix_fmt", "rgb24", "-"], timeout=60)


async def reserve_refusal(video: str, m: Manifest) -> str | None:
    """The end card's right 640 px, inside the safe area, are empty navy."""
    from ..content import ffmpeg
    if m.card_seconds <= 0:
        return "no end card was recorded, so its reserve cannot be checked"
    x0, y0, x1, y1 = RESERVE
    size = (x1 - x0) * (y1 - y0) * 3
    for at in reserve_times(m):
        try:
            raw = await frame_rgb(video, at, RESERVE)
        except ffmpeg.FfmpegError as exc:
            return f"the end card could not be read at {at:.1f} s ({exc}), so it is held"
        if len(raw) != size:
            return f"the end card could not be read at {at:.1f} s, so it is held"
        if (n := await asyncio.to_thread(off_navy, raw)) > 0:
            return (f"the end card's right 640 px are not empty navy at {at:.1f} s "
                    f"({n} pixels drawn)")
    return None


# ----------------------------------------------------------------- the gates

async def lesson_refusal(video: str, m: Manifest, probe_: dict[str, Any]) -> str | None:
    """Every batch 9 gate on a finished lesson: None, or why it is held.
    `probe_` is read_probe of `video`."""
    for why in (encoding_refusal(probe_, size=LESSON_SIZE), music_refusal(m),
                rate_refusal(m), pages_refusal(m.pages_measured, m.text_faults,
                                             m.text_fault_count)):
        if why is not None:
            return why
    return await reserve_refusal(video, m)


async def short_refusal(path: str, pages: Pages) -> str | None:
    """Every batch 9 gate on a rendered teaching Short."""
    got, why = await probe(path)
    if got is None:
        return f"{UNREAD} ({why})"
    if (why := encoding_refusal(got, size=SHORT_SIZE, seconds=SHORT_SECONDS)) is not None:
        return why
    return pages_refusal(pages.measured, pages.found)


# ------------------------------------------------------- flags, not gates
LOOPING_SUBSCRIBE = "subscribe"      # tutorial_design.TOPICS: the 3D Subscribe loop


def notes(m: Manifest) -> list[str]:
    """What the checklist forbids that this render still does, shown on the
    review card and never holding the post: qa-checklist.md "Motion" asks
    for no looping background motion, and the mid-lesson Subscribe beat
    stands on the 3D Subscribe button pressing itself in a loop."""
    if LOOPING_SUBSCRIBE in m.backdrops:
        return [("QA note (not held): the Subscribe beat stands on the looping 3D "
                 "Subscribe button; the checklist asks for no looping background "
                 "motion.")]
    return []
