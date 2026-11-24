"""Burned-in captions for a Veo reel: Groq's word timings, as an ASS file.

Groq's Whisper (`whisper-large-v3`, `GROQ_API_KEY`) is asked for
`verbose_json` with word AND segment timestamps. The words become an ASS
subtitle track in the style the owner chose on production: size 58 bold,
three-word chunks, the word being spoken in yellow, MarginV 330 -- plus a
"Made with AI" tag on screen for the whole clip, and a PART 1 card over
the last second and a half of a cliffhanger. ffmpeg's `ass` filter (libass,
confirmed in the production image) burns it in.

The font is DejaVu Sans, bold, not the Arial Black the style was first
drawn with: the production image carries only DejaVu, and libass falls
back silently from a font it cannot find (production, 2026-09-24).

The key is sent only as the bearer header. Every failure is a reason
through `explain()`, never an exception carrying a URL.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass

import httpx

from .drafts import AI_LABEL

GROQ_URL = "https://api.groq.com/openai/v1/audio/transcriptions"
GROQ_MODEL = "whisper-large-v3"
KEY_ENV = "GROQ_API_KEY"
CHUNK = 3

# ASS colours are &HAABBGGRR&.
WHITE = "&H00FFFFFF&"
YELLOW = "&H0000FFFF&"


@dataclass(frozen=True, slots=True)
class Word:
    text: str
    start: float
    end: float


async def word_timings(audio: bytes, filename: str = "reel.mp3"
                       ) -> tuple[list[Word] | None, str | None]:
    """`(words, None)` or `(None, reason)`. Never raises."""
    key = os.getenv(KEY_ENV, "").strip()
    if not key:
        return None, f"{KEY_ENV} not set, so no word timings"
    try:
        async with httpx.AsyncClient(timeout=120) as c:
            r = await c.post(
                GROQ_URL, headers={"Authorization": f"Bearer {key}"},
                files={"file": (filename, audio, "audio/mpeg")},
                data={"model": GROQ_MODEL, "response_format": "verbose_json",
                      "timestamp_granularities[]": ["word", "segment"]})
            r.raise_for_status()
            body = r.json()
    except Exception as exc:                            # noqa: BLE001
        from .._http import explain
        return None, f"Groq transcription failed: {explain(exc)}".replace(key, "<redacted>")
    words = []
    for item in body.get("words") or []:
        try:
            words.append(Word(str(item["word"]).strip(), float(item["start"]),
                              float(item["end"])))
        except (KeyError, TypeError, ValueError):
            continue
    words = [w for w in words if w.text]
    if not words:
        return None, "Groq returned no word timings"
    return words, None


GROQ_TRANSLATE_URL = "https://api.groq.com/openai/v1/audio/translations"


async def english_segments(audio: bytes, filename: str = "reel.mp3"
                           ) -> tuple[list[Cue] | None, str | None]:
    """An Egyptian-Arabic reel's speech as English caption cues (the owner,
    2026-09-27: Arabic voice, English captions): Whisper's translation
    endpoint, segment timings. `(cues, None)` or `(None, reason)`; never
    raises, and the key is never in a reason."""
    key = os.getenv(KEY_ENV, "").strip()
    if not key:
        return None, f"{KEY_ENV} not set, so no English captions"
    try:
        async with httpx.AsyncClient(timeout=120) as c:
            r = await c.post(
                GROQ_TRANSLATE_URL, headers={"Authorization": f"Bearer {key}"},
                files={"file": (filename, audio, "audio/mpeg")},
                data={"model": GROQ_MODEL, "response_format": "verbose_json"})
            r.raise_for_status()
            body = r.json()
    except Exception as exc:                            # noqa: BLE001
        from .._http import explain
        return None, (f"Groq translation failed: {explain(exc)}"
                      .replace(key, "<redacted>"))
    out: list[Cue] = []
    for seg in body.get("segments") or []:
        try:
            out += spread(str(seg["text"]).strip(), float(seg["start"]),
                          float(seg["end"]))
        except (KeyError, TypeError, ValueError):
            continue
    if not out:
        return None, "Groq returned no translated speech"
    return out, None


def _stamp(seconds: float) -> str:
    seconds = max(0.0, seconds)
    cs = round(seconds * 100)
    h, cs = divmod(cs, 360000)
    m, cs = divmod(cs, 6000)
    s, cs = divmod(cs, 100)
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


def _clean(text: str) -> str:
    """ASS reads { } as override blocks and \\N as a line break."""
    return text.replace("\\", "").replace("{", "(").replace("}", ")")


def chunks(words: list[Word], size: int = CHUNK) -> list[list[Word]]:
    return [words[i:i + size] for i in range(0, len(words), size)]


HEADER = """[Script Info]
ScriptType: v4.00+
PlayResX: 1080
PlayResY: 1920
WrapStyle: 0
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, \
BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, \
BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Caption,DejaVu Sans,66,&H00FFFFFF,&H00FFFFFF,&H00000000,&H64000000,-1,0,0,0,\
100,100,1,0,1,6,3,2,60,60,330,1
Style: Tag,DejaVu Sans,34,&H00FFFFFF,&H00FFFFFF,&H00000000,&H80000000,-1,0,0,0,\
100,100,0,0,3,2,0,9,40,40,60,1
Style: Part,DejaVu Sans,120,&H00FFFFFF,&H00FFFFFF,&H00000000,&H00000000,-1,0,0,0,\
100,100,0,0,1,6,3,5,60,60,0,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""


def build_ass(words: list[Word] | None, total: float, *,
              part_one: bool = False, tag: str = AI_LABEL,
              end_text: str = "") -> str:
    """The whole ASS file. With no words (Groq unset or failed) it still
    carries the "Made with AI" tag, so the label is on every frame either
    way."""
    events = [f"Dialogue: 1,{_stamp(0)},{_stamp(total)},Tag,,0,0,0,,{tag}"]
    for group in chunks(words or []):
        for i, active in enumerate(group):
            end = group[i + 1].start if i + 1 < len(group) else active.end
            end = max(end, active.start + 0.05)
            text = " ".join(
                (f"{{\\c{YELLOW}}}{_clean(w.text)}{{\\c{WHITE}}}" if j == i
                 else _clean(w.text))
                for j, w in enumerate(group))
            events.append(f"Dialogue: 0,{_stamp(active.start)},{_stamp(end)},"
                          f"Caption,,0,0,0,,{text}")
    end = end_text or ("PART 1" if part_one else "")
    if end and total > 0:
        events.append(f"Dialogue: 2,{_stamp(max(0.0, total - 1.5))},"
                      f"{_stamp(total)},Part,,0,0,0,,{_clean(end)}")
    return HEADER + "\n".join(events) + "\n"


# ------------------------------------------------ scripted (calendar) captions
#
# The 30-day calendar supplies the exact Arabic each character speaks, so
# its captions are that text, not a transcription: Whisper's rendering of
# Egyptian dialect is a guess, and the package says to use its own
# `text_ar` lines. The timing is the part's own window in the assembled
# reel, split across chunks by word count. Arabic is shaped and ordered
# right-to-left by libass (FriBidi + HarfBuzz); the font is Noto Sans
# Arabic, from the production image's fonts-noto-core -- DejaVu's Arabic
# coverage is partial, and libass falls back silently from a missing glyph.

ARABIC_FONT = "Noto Sans Arabic"
CUE_WORDS = 6

ARABIC_STYLE = (f"Style: Arabic,{ARABIC_FONT},64,&H00FFFFFF,&H00FFFFFF,"
                "&H00000000,&H64000000,-1,0,0,0,100,100,0,0,1,6,3,2,60,60,330,1")
# A narrator's line from the script, never spoken: small italic text under
# the top edge, so the plot survives without a narrator's voice.
CONTEXT_STYLE = ("Style: Context,DejaVu Sans,44,&H00FFFFFF,&H00FFFFFF,"
                 "&H00000000,&H80000000,0,-1,0,0,100,100,0,0,3,2,0,8,80,80,170,1")


# One colour per speaker, in order of first appearance (the owner, 2026-09-27:
# captions colour-coded per character, as in the talking-car skits). ASS
# colours are &HBBGGRR&: yellow, cyan, pink, lime, orange.
SPEAKER_COLORS = ("&H0000FFFF&", "&H00FFFF00&", "&H00C080FF&", "&H0000FF80&",
                  "&H000080FF&")


def speaker_color(index: int) -> str:
    return SPEAKER_COLORS[index % len(SPEAKER_COLORS)]


def dialogue_cues(beats: list[list[tuple[str, str]]], beat_seconds: float
                  ) -> list[Cue]:
    """Captions for a dialogue reel from its own script: each beat's lines
    over that beat's window, split by words, each speaker in their colour."""
    order: dict[str, int] = {}
    out: list[Cue] = []
    for i, lines in enumerate(beats):
        words = sum(len(t.split()) for _, t in lines) or 1
        t = i * beat_seconds + 0.3
        span_all = beat_seconds - 0.6
        for speaker, text in lines:
            color = speaker_color(order.setdefault(speaker.strip().lower(), len(order)))
            span = span_all * len(text.split()) / words
            out += [Cue(c.start, c.end, c.text, color=color)
                    for c in spread(text, t, t + span)]
            t += span
    return out


@dataclass(frozen=True, slots=True)
class Cue:
    start: float
    end: float
    text: str
    top: bool = False       # a silent context line, drawn at the top
    color: str = ""         # the speaker's colour, &HBBGGRR&; "" is white


def spread(text: str, start: float, end: float, *,
           size: int = CUE_WORDS) -> list[Cue]:
    """One spoken line over [start, end): chunks of at most `size` words,
    each on screen for its share of the words -- short readable phrases,
    not single flashing words."""
    words = text.split()
    if not words or end <= start:
        return []
    groups = [words[i:i + size] for i in range(0, len(words), size)]
    per_word = (end - start) / len(words)
    cues, t = [], start
    for group in groups:
        span = per_word * len(group)
        cues.append(Cue(t, t + span, " ".join(group)))
        t += span
    return cues


def build_scripted_ass(cues: list[Cue], total: float, *, arabic: bool = True,
                       tag: str = AI_LABEL, end_text: str = "") -> str:
    """The ASS file for a scripted reel: the given cues in the Arabic (or
    the ordinary Caption) style, plus the "Made with AI" tag on every frame."""
    header = HEADER.replace("\n\n[Events]",
                            f"\n{ARABIC_STYLE}\n{CONTEXT_STYLE}\n\n[Events]", 1)
    style = "Arabic" if arabic else "Caption"
    events = [f"Dialogue: 1,{_stamp(0)},{_stamp(total)},Tag,,0,0,0,,{tag}"]
    for cue in cues:
        if cue.end <= cue.start or not cue.text.strip():
            continue
        paint = f"{{\\c{cue.color}}}" if cue.color and not cue.top else ""
        events.append(f"Dialogue: 0,{_stamp(cue.start)},{_stamp(min(cue.end, total))},"
                      f"{'Context' if cue.top else style},,0,0,0,,"
                      f"{paint}{_clean(cue.text)}")
    if end_text and total > 0:
        events.append(f"Dialogue: 2,{_stamp(max(0.0, total - 1.5))},"
                      f"{_stamp(total)},Part,,0,0,0,,{_clean(end_text)}")
    return header + "\n".join(events) + "\n"


# ------------------------------------------------------------- stat reels
#
# The football lane (the owner, 2026-09-29): captions carry the stats, always on,
# big and bold, every number larger still and in yellow. The numbers were
# verified against the fetched facts before this is ever called
# (`content.football.verify`); this only draws them.

STAT_STYLE = ("Style: Stat,DejaVu Sans,82,&H00FFFFFF,&H00FFFFFF,&H00000000,"
              "&H96000000,-1,0,0,0,100,100,1,0,1,7,4,2,70,70,560,1")
SOURCE_STYLE = ("Style: Source,DejaVu Sans,34,&H00FFFFFF,&H00FFFFFF,&H00000000,"
                "&H80000000,0,0,0,0,100,100,0,0,1,2,0,8,50,50,90,1")
NUMBER_SIZE = 150
_DIGITS = re.compile(r"\d+(?:[.,:\-–/]\d+)*")


def stat_text(text: str) -> str:
    """One caption line, cleaned, with every number drawn big and yellow."""
    return _DIGITS.sub(lambda m: f"{{\\fs{NUMBER_SIZE}\\c{YELLOW}}}{m.group(0)}{{\\r}}",
                       _clean(text))


def build_stat_ass(beats: list[str], beat_seconds: float, total: float, *,
                   tag: str = AI_LABEL, source: str = "") -> str:
    """One bold stat caption per beat, over that beat's window; the source
    line ("Stats: API-Football, read ...") at the top for the whole reel,
    and the AI tag on every frame."""
    header = HEADER.replace("\n\n[Events]", f"\n{STAT_STYLE}\n{SOURCE_STYLE}"
                                            f"\n\n[Events]", 1)
    events = [f"Dialogue: 1,{_stamp(0)},{_stamp(total)},Tag,,0,0,0,,{tag}"]
    if source:
        events.append(f"Dialogue: 1,{_stamp(0)},{_stamp(total)},Source,,0,0,0,,"
                      f"{_clean(source)}")
    for i, text in enumerate(beats):
        start = i * beat_seconds + 0.2
        end = min((i + 1) * beat_seconds - 0.1, total)
        if end <= start or not text.strip():
            continue
        events.append(f"Dialogue: 0,{_stamp(start)},{_stamp(end)},Stat,,0,0,0,,"
                      f"{stat_text(text)}")
    return header + "\n".join(events) + "\n"


# ------------------------------------------------------------- course reels
#
# The course lane (`content.course`, the owner 2026-09-29): bold captions of the
# spoken script, a "MY COURSE" label at the top for the whole reel, and an
# end card with the price and "LINK IN BIO". The script's own words, never a
# transcription: Groq's timings only place them.

COURSE_STYLE = ("Style: Course,DejaVu Sans,78,&H00FFFFFF,&H00FFFFFF,&H00000000,"
                "&H96000000,-1,0,0,0,100,100,1,0,1,7,4,2,70,70,300,1")
LABEL_STYLE = ("Style: Label,DejaVu Sans,40,&H00FFFFFF,&H00FFFFFF,&H00000000,"
               "&H96000000,-1,0,0,0,100,100,2,0,3,3,0,7,50,50,60,1")
END_STYLE = ("Style: End,DejaVu Sans,96,&H00FFFFFF,&H00FFFFFF,&H00000000,"
             "&H00000000,-1,0,0,0,100,100,1,0,1,6,3,5,70,70,0,1")
PRICE_SIZE = 190


def course_cues(words: list[Word] | None, sentences: list[str],
                spoken: float) -> list[Cue]:
    """Caption cues: Groq's word timings in chunks when they were read,
    otherwise the script's sentences spread over the voice by word count."""
    if words:
        return [Cue(g[0].start, max(g[-1].end, g[0].start + 0.3),
                    " ".join(w.text for w in g))
                for g in chunks(words, CUE_WORDS - 2)]
    total = sum(len(s.split()) for s in sentences) or 1
    out: list[Cue] = []
    t = 0.2
    span = max(0.5, spoken - 0.3)
    for s in sentences:
        share = span * len(s.split()) / total
        out += spread(s, t, t + share, size=CUE_WORDS - 2)
        t += share
    return out


def build_course_ass(cues: list[Cue], total: float, *, end_start: float,
                     end_lines: list[str], label: str, tag: str = AI_LABEL,
                     price: str = "") -> str:
    """The whole ASS file for a course reel: the AI tag and the course label
    on every frame, the bold captions until the end card, then the end
    card's lines (the price drawn larger still, in yellow)."""
    header = HEADER.replace("\n\n[Events]", f"\n{COURSE_STYLE}\n{LABEL_STYLE}"
                                            f"\n{END_STYLE}\n\n[Events]", 1)
    events = [f"Dialogue: 1,{_stamp(0)},{_stamp(total)},Tag,,0,0,0,,{tag}",
              (f"Dialogue: 1,{_stamp(0)},{_stamp(total)},Label,,0,0,0,,"
               f"{_clean(label)}")]
    for cue in cues:
        end = min(cue.end, end_start)
        if end <= cue.start or not cue.text.strip():
            continue
        events.append(f"Dialogue: 0,{_stamp(cue.start)},{_stamp(end)},Course,,"
                      f"0,0,0,,{_clean(cue.text)}")
    lines = []
    for line in end_lines:
        text = _clean(line)
        if price and line.strip() == price:
            text = f"{{\\fs{PRICE_SIZE}\\c{YELLOW}}}{text}{{\\r}}"
        lines.append(text)
    if lines and total > end_start:
        card = "\\N".join(lines)
        events.append(f"Dialogue: 2,{_stamp(end_start)},{_stamp(total)},End,,"
                      f"0,0,0,,{card}")
    return header + "\n".join(events) + "\n"
