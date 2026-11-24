"""Teaching Shorts cut from a published tutorial.

Growth plan §4.3 and docs/academy-youtube-plan.md "Teaching Shorts" (the owner,
2026-10-13): Shorts are trailers for lessons, not ads for products. Two or
three cuts per lesson -- its payoff, its "Stuck?" beat and its quiz
-- each ending on the answer, posted to YouTube and TikTok with the full
video linked in the caption. 20-40 s since 2026-10-17 (the owner, the video
system), closing card included. Promo Shorts are at most one in five: the
single teaching Short already beat every promo Short.

This module only decides where to cut and what to say and show. The
Short itself is `tutorials.short_argv`: since 2026-10-17 each scene step
recomposed natively at 1080x1920 (short_frame, ShortsFrame) under the hook,
a demo as its prepared crop, over the long render's sound, then a closing
card naming the longer lesson -- no more 16:9 inside 9:16 over a blur;
`reel.daily()` picks the slot. The manifest is read duck-typed (`.chapters`, `.seconds`,
`.timings`, `.intro_seconds`) so this stays free of the renderer.

Each cut is anchored on the episode's own scene, found in the manifest by
its words (`lines_of`): the payoff, the stuck beat (a "Not working?"
chapter's scene, else a lab's "Stuck?" step) and the first quiz with a
reveal -- a quiz is a beat inside a chapter, never a chapter of its own.
A render whose lines name none of them falls back to the chapters.

The payoff chapter starts at 0:00 when a short opener is folded into it, so
the payoff cut starts at the first line at or after the manifest's
`intro_seconds` (past the intro take and the opener; without one, past the
first line) and runs into the first steps
("the result, then the first steps"). The stuck and quiz cuts END on their
chapter's end, the fix or the reveal being the last thing seen. A chapter
that is missing gives no cut, never a guessed one.

No music in a teaching Short (rule 22, the owner 2026-10-17: music under the
brand sting and the end card only, and a Short has neither). The cut copies
the source's sound, so a cut stops where the manifest's `music_windows`
begin (the end card closes the last chapter) and starts after one it would
open on; a render made before 2026-10-17 lists no windows -- its bed runs
under the whole file, and a cut from it still carries it.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass, replace
from typing import Protocol

from . import short_frame as sf
from . import video_tokens as vt
from .tutorial_series import (
    LAB_LABELS,
    PAYOFF_CHAPTER,
    SHORT_MAX,
    SHORT_MIN,
    Episode,
    Scene,
    Series,
    hashtags_for,
    plain,
    title_for,
)

KINDS = ("payoff", "stuck", "quiz")
QUIZ_CHAPTER = "Quick quiz"
LAB_CHAPTER = "Lab"                 # its "Stuck?" beat is inside it
STUCK_WORDS = ("Not working?", "Stuck?", "No tool?")
# The Short slots in order (the owner, 2026-10-13): teaching Short, teaching
# Short, lesson Short, concept Short, promo -- promo never first.
ROTATION = ("teach", "teach", "lesson", "concept", "promo")
PROMO_SHARE_MAX = 1 / 5
# What the cut looks like; bumped whenever the picture or the sound of a
# teaching Short changes, so a finished render in an older look is cut
# again, never reused (tutorials.rendered_teaching). 1: phrase captions;
# 2 (2026-10-16): word highlight, punch-in, edge fades; 3 (the combined
# bump, 2026-10-18): the navy video system's captions (IBM Plex, paper on
# an 80% navy plate) and batch 6's Short (20-40 s cuts at whole lines, the
# hook from frame 0, native portrait shots, no punch-in, the closing card).
LOOK_VERSION = 3


def look_tag() -> str:
    """The end of a teaching Short's draft title: which look it was cut in."""
    return f"[look {LOOK_VERSION}]"


class CutSource(Protocol):
    """What `cuts` reads from a `tutorials.Manifest`."""

    chapters: list[tuple[float, str]]
    seconds: float
    timings: list[tuple[float, float, str]]
    intro_seconds: float            # where the opening (intro take + opener) ends
    # Also read, with getattr so a source without it has none:
    # `music_windows`, the (start, end) spans the bed sounds in (2026-10-17).


@dataclass(frozen=True)
class Cut:
    kind: str           # "payoff" | "stuck" | "quiz"
    start: float
    end: float
    caption: str        # the chapter it was cut from
    hook: str = ""      # the shorts-hook headline on screen from frame 0
    lesson: str = ""    # the longer lesson's title as uploaded, named at the end


# 2026-10-17 (the owner, the video system): a Short is 20-40 s in all. The cut is
# the lesson's own seconds; the closing card that names the longer lesson
# follows it, so a cut leaves it room. A cut is trimmed only where a line of
# the manifest starts or ends -- never mid-word -- and one that cannot make
# CUT_MIN that way is not made.
CLOSE_SECONDS = 3.0
CUT_MIN = SHORT_MIN
CUT_MAX = SHORT_MAX - CLOSE_SECONDS


def lesson_scenes(ep: Episode) -> tuple[Scene, ...]:
    """Every scene the render narrates after the opener, in order: the
    payoff, the hook (tutorials.opening), then the lesson."""
    return tuple(sc for sc in (ep.payoff, ep.hook) if sc is not None) + ep.scenes


@dataclass(frozen=True)
class Line:
    """One timed line of the manifest and the scene step that said it."""
    start: float
    end: float
    said: str
    scene: Scene | None             # None: a line of no scene (the opener, the topic)
    step: int = -1


def lines_of(m: CutSource, ep: Episode) -> list[Line]:
    """The manifest's timings in order, each matched by its words (cues
    stripped) to the next scene step that says them. The render times every
    narrated step in this order (tutorials.beats), so a line that matches
    none -- the intro take, the opener, the topic line -- is no scene's."""
    narrated = [(sc, k, plain(said)) for sc in lesson_scenes(ep)
                for k, said in enumerate(sc.said)]
    out, j = [], 0
    for a, b, said in m.timings or []:
        words = plain(said)
        hit = next((i for i in range(j, len(narrated)) if narrated[i][2] == words), None)
        if hit is None:
            out.append(Line(a, b, words, None))
            continue
        j = hit + 1
        out.append(Line(a, b, words, narrated[hit][0], narrated[hit][1]))
    return out


def _chapter_span(chapters: list[tuple[float, str]], i: int, total: float
                  ) -> tuple[float, float]:
    start = chapters[i][0]
    end = chapters[i + 1][0] if i + 1 < len(chapters) else total
    return start, end


def _find(names: list[str], kind: str) -> int | None:
    if kind == "payoff":
        return next((k for k, n in enumerate(names) if n == PAYOFF_CHAPTER), None)
    if kind == "quiz":
        return next((k for k, n in enumerate(names) if n == QUIZ_CHAPTER), None)
    stuck = next((k for k, n in enumerate(names) if n.startswith(STUCK_WORDS)), None)
    if stuck is not None:
        return stuck
    return next((k for k, n in enumerate(names) if n == LAB_CHAPTER), None)


def _bounds(m: CutSource) -> tuple[list[float], list[float]]:
    """Where a cut may start and end: a line's start or end, or a chapter's
    start (a chapter opens on a line) and the file's end."""
    chapter = [s for s, _ in m.chapters]
    starts = sorted({a for a, _, _ in m.timings or []} | set(chapter))
    ends = sorted({b for _, b, _ in m.timings or []} | set(chapter[1:]) | {m.seconds})
    return starts, ends


def _fit(m: CutSource, a: float, b: float, keep: tuple[float, float], *,
         trim_end: bool) -> tuple[float, float] | None:
    """(a, b) held to CUT_MIN-CUT_MAX at line boundaries, always containing
    `keep` (the mistake, the question, the answer): too short, it reaches
    back to an earlier line; too long, it drops lines from its end
    (`trim_end`, the stuck beat: its mistake comes first) or its start."""
    starts, ends = _bounds(m)
    if b - a < CUT_MIN:
        a = max((s for s in starts if s <= b - CUT_MIN), default=-1.0)
    elif b - a > CUT_MAX and trim_end:
        b = max((e for e in ends if a + CUT_MIN <= e <= a + CUT_MAX), default=-1.0)
    elif b - a > CUT_MAX:
        a = min((s for s in starts if b - CUT_MAX <= s <= b - CUT_MIN), default=-1.0)
    if a < 0 or b < 0 or not CUT_MIN <= b - a <= CUT_MAX + 1e-6:
        return None
    if a > keep[0] + 1e-6 or b < keep[1] - 1e-6:
        return None
    return a, b


def _payoff(m: CutSource, i: int) -> tuple[float, float] | None:
    """From the payoff's first line (past a folded-in opener) for 20-37 s,
    ending on a line's end, or a chapter's when there are no timings."""
    a, b = _chapter_span(m.chapters, i, m.seconds)
    timings = list(m.timings or [])
    start = a
    if timings:
        # Final review I5: since the presenter merge the opening is two lines
        # (the owner's intro take, then the narrated opener), both before
        # intro_seconds. A body line starts at the unrounded end, which the
        # manifest rounds to 0.01 s: hence the half-step of slack.
        intro = float(m.intro_seconds or 0.0)
        lines = [t for t in timings if a <= t[0] < b and t[0] >= intro - 0.005]
        if a == 0.0 and lines and lines[0][0] == timings[0][0]:
            lines = lines[1:]                      # the opener, folded in
        if lines:
            start = lines[0][0]
        ends = [t[1] for t in timings if t[0] >= start]
    else:
        ends = [s for s, _ in m.chapters if s > start] + [m.seconds]
    return _longest(start, ends)


def _longest(start: float, ends: list[float]) -> tuple[float, float] | None:
    """From `start` to the furthest line end CUT_MIN-CUT_MAX on: never a
    cut that would stop mid-line."""
    fit = [e for e in ends if start + CUT_MIN <= e <= start + CUT_MAX]
    return (start, max(fit)) if fit else None


def _scene_span(m: CutSource, ep: Episode, kind: str
                ) -> tuple[float, float, str] | None | bool:
    """The cut anchored on the episode's own scene (rule 17's payoff, the
    stuck beat, a quiz with its reveal), from the manifest's matched lines:
    a quiz is a beat inside a chapter, so its span is found by its words,
    never by a chapter name. None when the scene was said but no cut of it
    fits; False when the render's lines name no such scene at all."""
    every = lines_of(m, ep)
    lines = [ln for ln in every if ln.scene is not None]
    chapter_at = {id(sc): name for sc, name in _chapter_names(ep)}
    if kind == "payoff":
        own = [ln for ln in lines if ln.scene is ep.payoff]
        if not own:
            return False
        span = _longest(own[0].start, [ln.end for ln in every if ln.start >= own[0].start])
        return None if span is None else (*span, PAYOFF_CHAPTER)
    if kind == "quiz":
        quizzes = [[ln for ln in lines if ln.scene is sc] for sc in ep.scenes
                   if sc.kind == "quiz" and sc.answer >= 0]
        quizzes = [own for own in quizzes if len(own) >= 2]
        for own in quizzes:
            got = _fit(m, own[0].start, own[-1].end, (own[0].start, own[-1].end),
                       trim_end=False)
            if got is not None:
                assert own[0].scene is not None
                return (*got, chapter_at.get(id(own[0].scene), ""))
        return None if quizzes else False
    sc = stuck_scene(ep)
    own = [ln for ln in lines if ln.scene is sc] if sc is not None else []
    if sc is None or not own:
        return False
    if sc.kind == "lab":                 # its "Stuck?" step ends it
        k = LAB_LABELS.index("Stuck?")
        mine = [ln for ln in own if ln.step <= k]
        if not mine or mine[-1].step != k:
            return None
        got = _fit(m, mine[0].start, mine[-1].end, (mine[-1].start, mine[-1].end),
                   trim_end=False)
    else:                                # the mistake first, then its fixes
        got = _fit(m, own[0].start, own[-1].end, (own[0].start, own[0].end),
                   trim_end=True)
    return None if got is None else (*got, chapter_at.get(id(sc), sc.heading))


def _chapter_names(ep: Episode) -> list[tuple[Scene, str]]:
    """Each lesson scene with the chapter it sits in (a beat joins the one
    before it)."""
    out, name = [], ""
    for sc in lesson_scenes(ep):
        name = sc.chapter or name
        out.append((sc, name))
    return out


def stuck_scene(ep: Episode) -> Scene | None:
    """The episode's stuck beat: the scene of a "Not working?"-style
    chapter, else a lab (its "Stuck?" step)."""
    named = next((sc for sc in ep.scenes if sc.chapter.startswith(STUCK_WORDS)), None)
    return named or next((sc for sc in ep.scenes if sc.kind == "lab"), None)


# ---------------------------------------------------------------- the hook
# ShortsFrame (the owner, 2026-10-17): a shorts-hook headline on screen from frame
# 0 -- three to five words in sentence case: the cut's result, its mistake or
# its question. Taken from the episode's own fields, whole or one clause of
# them (a sentence, or a part between arrows, dots or a colon), never written
# for the Short: a cut whose fields give none is not made. HOOK_CHARS keeps
# it to two lines at 88 px inside the 936 px between the side insets.
HOOK_WORDS = (3, 5)
HOOK_CHARS = 36
_CLAUSE = re.compile(r"(?<=[.?!…])\s+|\s+[→·]\s+|:\s+")
_NOT_SHOWN = re.compile(r"[<>{}#✔()]|\blink\b|https?:", re.IGNORECASE)


def sentence_case(text: str) -> str:
    """The first letter capitalised, nothing else touched (product names)."""
    return text[:1].upper() + text[1:]


def _clauses(text: str) -> list[str]:
    whole = plain(text.replace("\n", " "))
    sentences = [x for x in re.split(r"(?<=[.?!…])\s+", whole) if x]
    if any(_NOT_SHOWN.search(x) for x in sentences):
        # a placeholder, a link or a symbol is never a hook: drop its sentence
        sentences = [x for x in sentences if not _NOT_SHOWN.search(x)]
        whole = " ".join(sentences)
    parts = [whole] + [c for x in sentences for c in _CLAUSE.split(x)]
    return [c.strip().rstrip(".…").strip() for c in parts if c.strip()]


def hook_from(fields: list[str]) -> str | None:
    """The first field, else clause of a field, of HOOK_WORDS words that
    fits HOOK_CHARS, in sentence case; None when there is none."""
    for text in fields:
        for c in _clauses(text):
            words = len([w for w in c.split() if re.search(r"\w", w)])
            if HOOK_WORDS[0] <= words <= HOOK_WORDS[1] and len(c) <= HOOK_CHARS:
                return sentence_case(c)
    return None


def _hook_fields(m: CutSource, ep: Episode, kind: str, end: float) -> list[str]:
    """What the hook may say, in order: the payoff's result, the stuck
    beat's mistake, the quiz's question (its heading, then the questions
    its line asks)."""
    if kind == "payoff":
        return [ep.payoff.caption, ep.payoff.code] if ep.payoff else []
    if kind == "stuck":
        sc = stuck_scene(ep)
        if sc is None:
            return []
        if sc.kind == "lab":
            k = LAB_LABELS.index("Stuck?")
            return [sc.items[k]] if len(sc.items) > k else []
        return [*sc.items[:1], sc.heading]
    quizzes = [sc for sc in ep.scenes if sc.kind == "quiz" and sc.answer >= 0]
    # the quiz whose reveal ends the cut; a chapter cut shows the first
    ending = [ln.scene for ln in lines_of(m, ep)
              if ln.scene in quizzes and abs(ln.end - end) < 0.01]
    quiz = ending[0] if ending else (quizzes[0] if quizzes else None)
    if quiz is None:
        return []
    asked = [c for c in _clauses(quiz.said[0]) if c.endswith("?")] if quiz.said else []
    return [quiz.heading, *asked]


def clear_of_music(a: float, b: float, spans: list[tuple[float, float]]
                   ) -> tuple[float, float]:
    """(a, b) moved off every music window: a window inside the cut ends it
    there, one it starts in moves its start past it."""
    for w0, w1 in spans:
        if w0 <= a < w1:
            a = w1
        elif a < w0 < b:
            b = w0
    return a, b


def _chapter_cut(m: CutSource, kind: str, i: int) -> tuple[float, float] | None:
    """A render whose lines name no scene of the episode: the chapter of
    that name, the fix or the reveal last, held to the bounds at lines."""
    if kind == "payoff":
        return _payoff(m, i)
    a, b = _chapter_span(m.chapters, i, m.seconds)
    return _fit(m, a, b, (b, b), trim_end=False)


def cuts(m: CutSource, ep: Episode) -> list[Cut]:
    """The payoff, stuck and quiz cuts that this render has, in that order:
    each CUT_MIN-CUT_MAX, starting and ending on a line, clear of music."""
    out: list[Cut] = []
    names = [n for _, n in m.chapters]
    music = list(getattr(m, "music_windows", None) or [])
    for kind in KINDS:
        found = _scene_span(m, ep, kind)
        if found is None:
            continue                     # said, but no whole-line cut fits
        if found is not False:
            assert isinstance(found, tuple)
            a, b, name = found
        else:
            i = _find(names, kind)
            span = _chapter_cut(m, kind, i) if i is not None else None
            if span is None:
                continue
            (a, b), name = span, names[i]
        starts, ends = _bounds(m)
        if kind == "payoff":
            # a window in the payoff's first seconds (the sting, 6-7 s since
            # 2026-10-17): start on the first line after it and run on to
            # the longest whole-line cut again, rather than stop short
            for w0, w1 in music:
                if a < w1 and w0 < min(b, a + CUT_MIN):
                    a = min((s for s in starts if s >= w1 - 1e-6), default=w1)
                    b = (_longest(a, [e for e in ends if e > a]) or (a, a))[1]
        if clear_of_music(a, b, music) != (a, b):
            a, b = clear_of_music(a, b, music)
            # back onto a line inside what the music left
            a = min((s for s in starts if s >= a - 1e-6), default=a)
            b = max((e for e in ends if e <= b + 1e-6), default=b)
        if not CUT_MIN <= b - a <= CUT_MAX + 1e-6 or clear_of_music(a, b, music) != (a, b):
            continue
        hook = hook_from(_hook_fields(m, ep, kind, b))
        if hook is None:
            continue                     # nothing of its own to open on
        # the title as uploaded: the formula with the render's minutes
        lesson = title_for(ep, int(getattr(m, "minutes", 0) or 0) or None)
        out.append(Cut(kind, round(a, 2), round(b, 2), name, hook, lesson))
    return out


def caption(s: Series, ep: Episode, cut: Cut, video_id: str) -> str:
    """The YouTube/TikTok text: the hook, the longer lesson by its exact
    title and its address, the series' three hashtags then #shorts fourth
    (the owner 2026-10-18, tags-and-hashtags.md)."""
    # An unhooked episode keeps its title; no "X?: ..." before the colon.
    x = (ep.title_hook or ep.title).rstrip("?!. ")
    hook = {"payoff": f"{x}: here's the result.",
            "stuck": f"{x}: the error everyone hits, and the fix.",
            "quiz": f"{x}: can you answer this?"}[cut.kind]
    return (f"{hook}\nFull lesson: {cut.lesson or ep.title}\n"
            f"https://youtu.be/{video_id}\n" + " ".join(hashtags_for(s.key, ep, short=True)))


def slot_kind(i: int) -> str:
    """The i-th Short slot's kind: promo is one in five, never first."""
    return ROTATION[i % len(ROTATION)]


# ----------------------------------------------------------------- shots
# What the Short shows (ShortsFrame, the owner 2026-10-17): the scene step being
# said, recomposed at 1080x1920 (short_frame), over the long render's sound
# for the same seconds, then the closing card. Each shot runs from its
# line's start to the next line's start, so the silent beats -- a quiz's
# 3-2-1, a pause's hold -- stay on the step before them and the shots tile
# the cut with no hole.

@dataclass(frozen=True)
class Shot:
    start: float                    # from the cut's start
    end: float
    scene: Scene | None             # None: no scene step is said here
    step: int = -1


def shots(m: CutSource, ep: Episode, cut: Cut) -> list[Shot]:
    """The cut as shots, from 0 to its length, back to back."""
    total = round(cut.end - cut.start, 2)
    # the cut's ends are rounded to 0.01 s: a line starting within half a
    # step of either belongs to the side it rounds to
    said = [ln for ln in lines_of(m, ep)
            if ln.scene is not None and cut.start - 0.005 <= ln.start < cut.end - 0.005]
    if not said:
        return [Shot(0.0, total, None)]
    out: list[Shot] = []
    for i, ln in enumerate(said):
        a = 0.0 if i == 0 else round(ln.start - cut.start, 2)
        b = total if i == len(said) - 1 else round(said[i + 1].start - cut.start, 2)
        if out and out[-1].scene is ln.scene and out[-1].step == ln.step:
            out[-1] = replace(out[-1], end=b)
            continue
        out.append(Shot(a, b, ln.scene, ln.step))
    return out


# ------------------------------------------------------------- captions
# The motion guide (2026-10-15), Shorts: "readable phrase-level captions and
# correct technical names; keep important text away from app controls". The
# script's own words (the manifest's timings, cues already stripped), a few
# words a phrase. 2026-10-17 (the video system): shorts-caption 60px IBM
# Plex Sans 500, paper on a navy plate at 80%, the word being said in
# blue-text, two lines at most, bottom-anchored in the frame's caption zone
# under the panel (short_frame.CAPTION_BOX): x 72 and 420 from the bottom.
CAPTION_WORDS = 5
CAPTION_PX = vt.SHORTS_CAPTION.size
CAPTION_LINES = 2
CAPTION_PAD = sf.CAPTION_PAD        # the plate's padding round the words (ASS Outline)
CAPTION_SIDE = sf.X + CAPTION_PAD   # ASS MarginL / MarginR: the plate's edge at x 72
CAPTION_BOTTOM = vt.SAFE_9_16_BOTTOM + CAPTION_PAD   # ASS MarginV, bottom-aligned
# A phrase longer than this is split: two lines of 60 px Plex in the 904 px
# between the margins hold about 27 characters each, and wrap by words.
CAPTION_CHARS = 40
# The plate's ASS alpha: 0 is opaque, 255 clear, so 80% opaque is 0x33.
PLATE_ALPHA = round(255 * (1 - vt.CAPTION_PLATE_ALPHA))


def _ass_colour(hex_colour: str, alpha: int = 0) -> str:
    """#RRGGBB as ASS's &HAABBGGRR (alpha 0 is opaque)."""
    r, g, b = hex_colour[1:3], hex_colour[3:5], hex_colour[5:7]
    return f"&H{alpha:02X}{b}{g}{r}".upper()


def _short_cues(cues: list) -> list:
    """`cues` with each phrase past CAPTION_CHARS split by its words, the
    halves sharing its time by word count, until each fits two lines (a
    single word never splits)."""
    from ..content.captions import spread
    out = []
    for cue in cues:
        words = cue.text.split()
        if len(cue.text) <= CAPTION_CHARS or len(words) < 2:
            out.append(cue)
            continue
        half = math.ceil(len(words) / 2)
        out += _short_cues(spread(cue.text, cue.start, cue.end, size=half))
    return out


def _timed_cues(m: CutSource, cut: Cut
                ) -> list[tuple[float, float, str, float, float]]:
    """caption_cues plus each phrase's whole span (unclipped by the cut),
    all timed from the cut's start: the words keep their own times when a
    cut starts mid-phrase."""
    from ..content.captions import spread
    out = []
    for a, b, said in m.timings or []:
        if b <= cut.start or a >= cut.end:
            continue
        # the timings keep the voice's [cue]s; the screen never shows one
        for cue in _short_cues(spread(plain(said), a, b, size=CAPTION_WORDS)):
            s, e = max(cue.start, cut.start), min(cue.end, cut.end)
            if e - s >= 0.2:
                out.append((round(s - cut.start, 2), round(e - cut.start, 2), cue.text,
                            cue.start - cut.start, cue.end - cut.start))
    return out


def caption_cues(m: CutSource, cut: Cut) -> list[tuple[float, float, str]]:
    """(start, end, phrase) inside the cut, timed from the cut's start."""
    return [(s, e, t) for s, e, t, _, _ in _timed_cues(m, cut)]


def _ass_inline(hex_colour: str) -> str:
    """#RRGGBB as an inline ASS colour (&HBBGGRR&), for \\1c."""
    r, g, b = hex_colour[1:3], hex_colour[3:5], hex_colour[5:7]
    return f"&H{b}{g}{r}&".upper()


def word_events(text: str, start: float, end: float, said_from: float,
                said_to: float, lit: str, rest: str) -> list[tuple[float, float, str]]:
    """The phrase `text` (on screen start-end) as one event a word: the
    whole phrase each time, only the word being said in `lit`, the others
    in `rest`. The manifest times lines, not words, so a phrase's words
    share [said_from, said_to) by their letters: the word times are
    ESTIMATED, never measured. A word said before `start` (a cut that opens
    mid-phrase) is never lit; the events run back to back from start to
    end, on the centisecond grid the stamps use."""
    from ..content.captions import _clean
    words = [_clean(w) for w in text.split()]
    if not words:
        return []
    total = round((end - start) * 100)
    weights = [len(w) for w in words]
    span, cum, edges = said_to - said_from, 0, [0]
    for w in weights[:-1]:
        cum += w
        t = said_from + span * cum / sum(weights)
        edges.append(min(total, max(0, round((t - start) * 100))))
    edges.append(total)
    out = []
    for i in range(len(words)):
        if edges[i + 1] <= edges[i]:
            continue
        shown = " ".join(f"{{\\1c{lit}}}{w}{{\\1c{rest}}}" if k == i else w
                         for k, w in enumerate(words))
        out.append((round(start + edges[i] / 100, 2),
                    round(start + edges[i + 1] / 100, 2), shown))
    return out


def caption_ass(m: CutSource, cut: Cut) -> str:
    """The ASS file of `cut`'s phrases in the video system's type and
    colours (paper on a navy plate at 80%), only the word being said in
    blue-text (`word_events`), or "" when the render has no timings there.
    libass burns it with fontsdir=video_tokens.LIBASS_FONTS_DIR."""
    from ..content.captions import _stamp
    cues = _timed_cues(m, cut)
    if not cues:
        return ""
    text, box = _ass_colour(vt.PAPER), _ass_colour(vt.NAVY, PLATE_ALPHA)
    lit, rest = _ass_inline(vt.BLUE_TEXT), _ass_inline(vt.PAPER)
    size = round(CAPTION_PX * vt.ASS_CELL)
    head = ("[Script Info]\nScriptType: v4.00+\nPlayResX: 1080\nPlayResY: 1920\n"
            "WrapStyle: 0\nScaledBorderAndShadow: yes\n\n[V4+ Styles]\n"
            "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, "
            "OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, "
            "ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, "
            "MarginR, MarginV, Encoding\n"
            f"Style: Lesson,{vt.ASS_FONT},{size},{text},{text},{box},{box},0,0,0,0,100,100,"
            f"0,0,3,{CAPTION_PAD},0,2,{CAPTION_SIDE},{CAPTION_SIDE},{CAPTION_BOTTOM},1\n\n"
            "[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, "
            "MarginV, Effect, Text\n")
    events = [f"Dialogue: 0,{_stamp(s)},{_stamp(e)},Lesson,,0,0,0,,{shown}"
              for a, b, t, fa, fb in cues
              for s, e, shown in word_events(t, a, b, fa, fb, lit, rest)]
    return head + "\n".join(events) + "\n"


# The only motion a Short adds is its fade out at the end (2026-10-17: the
# hook is on screen from frame 0, so nothing fades in). The punch-in of
# 2026-10-16 is gone: the one zoom allowed is the focus-zoom into a prepared
# crop, which a demo's crop already carries from the long render.
EDGE_FADE = 0.15
