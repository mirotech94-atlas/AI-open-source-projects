"""A Short from one lesson: the part that matters, in the lesson's own voice.

Miro, 2026-10-11: "make the reels shorts about each video and the
important parts to it as well, not just any random part of the video --
make it entertaining and animated". The daily Academy reel now takes one
lesson at a time, in rotation across every course whose lessons are on
the server (`data/lesson-video/<course>/<lesson>/segments.json` with one
`audio/NNN.mp3` per segment, as tools/lesson_video built them).

What "the important part" is, decided here, in code, without a model:

  * a lesson is sections (each segment's `heading`); a section is scored
    by what its heading says it is -- "Why it matters", "The idea", "One
    thing to do today" first; the table of contents, exercises, quizzes,
    "Next" and the step-by-step mechanics never;
  * a section with a short rule line in it ("No campaign, no clip.") scores
    higher: that is the line people remember;
  * the section is taken whole, in order, up to a word budget, so a
    warning is never lifted away from its rule (lesson 1.2 opens by quoting
    the scam it warns against);
  * a section any of whose words trips the brand guard or names income,
    earnings or profit is skipped, never trimmed mid-thought.

What is said is the lesson's own narration (its mp3s, played as recorded);
the only new line is the close, one Fish call naming the lesson and the
course. What is shown is the same words, timed to the voice word by word
(reel_design.caption_page), in a different edition each day.
"""
from __future__ import annotations

import html
import itertools
import json
import os
import re
from dataclasses import dataclass
from pathlib import Path

from .. import store_origin

ROOT_ENV = "ACADEMY_LESSON_ROOT"
DEFAULT_ROOT = "data/lesson-video"
# A course's own cover, where its product carries more than one edition.
COVERS: dict[str, str] = {"seniors": "covers/safe-and-simple-ai-60plus.jpg",
                          "adults": "covers/everyday-ai-for-adults.jpg"}
# course directory -> (catalog product slug, the course's name as said)
COURSES: dict[str, tuple[str, str]] = {
    "operator": ("safe-ai-operator", "Build a Safe AI Operator"),
    "sell": ("sell-with-ai", "Sell With AI"),
    "adults": ("ai-for-every-age", "Everyday AI for Adults"),
    "seniors": ("ai-for-every-age", "Safe and Simple AI, for 60 plus"),
}
STORE = store_origin.host()
STORE_SAID = store_origin.spoken()
MIN_WORDS = 30                    # a part shorter than this says too little
# What a section is, by its heading. Unlisted headings score 1.
HEADING_SCORE: tuple[tuple[str, int], ...] = (
    ("why it matters", 6), ("the idea", 5), ("one thing to do today", 5),
    ("safety reminder", 4), ("stay safe", 4), ("try it", 3), ("walk-through", 2),
    ("in this lesson", 0), ("next", 0), ("exercise", 0), ("quick check", 0),
    ("check", 0), ("step by step", 0), ("what you will do", 0), ("", 0),
)
UNSAID = re.compile(r"\b(income|earn(s|ed|ing|ings)?|profit\w*|revenue|"
                    r"make money|get rich|passive|guarantee\w*|\$\d)", re.IGNORECASE)
PUNCHY = 10                       # a line this short is a rule worth showing


@dataclass(frozen=True)
class Lesson:
    course: str
    lesson: str                   # "1.2"
    path: Path

    @property
    def key(self) -> str:
        return f"lesson:{self.course}/{self.lesson}"


@dataclass(frozen=True)
class Beat:
    kind: str                     # hook | line | cta
    text: str
    audio: str                    # mp3 path, "" for the close (said by Fish)
    heading: str = ""             # the lesson's own heading for this part


def root() -> Path:
    return Path(os.getenv(ROOT_ENV, DEFAULT_ROOT))


def _order(lesson: str) -> tuple[int, ...]:
    return tuple(int(p) for p in re.findall(r"\d+", lesson))


COURSES_ENV = "ACADEMY_LESSON_COURSES"
# Miro, 2026-10-12 (the growth plan): business first -- small-business
# owners and freelancers. The adults and 60+ courses wait until the main
# series has a baseline; ACADEMY_LESSON_COURSES brings them back.
DEFAULT_COURSES = ("operator", "sell")


def active_courses() -> tuple[str, ...]:
    raw = os.getenv(COURSES_ENV, "").strip()
    if not raw:
        return DEFAULT_COURSES
    named = tuple(c.strip().lower() for c in raw.split(",") if c.strip().lower() in COURSES)
    return named or DEFAULT_COURSES


def lessons(base: Path | None = None) -> list[Lesson]:
    """Every lesson whose segments and narration line up, course by course
    in COURSES order, in the courses rotating now; a lesson whose mp3 count
    differs is skipped."""
    base = base or root()
    out = []
    for course in active_courses():
        folder = base / course
        if not folder.is_dir():
            continue
        for d in sorted((p for p in folder.iterdir() if p.is_dir()),
                        key=lambda p: _order(p.name)):
            seg = d / "segments.json"
            if not seg.is_file():
                continue
            try:
                n = len(json.loads(seg.read_text(encoding="utf-8")))
            except (OSError, ValueError):
                continue
            if n and len(list((d / "audio").glob("*.mp3"))) == n:
                out.append(Lesson(course, d.name, d))
    return out


def rotation(all_lessons: list[Lesson]) -> list[Lesson]:
    """Courses interleaved, each in lesson order: day one is the first
    lesson of each course in turn, so no course runs for a month alone."""
    by: dict[str, list[Lesson]] = {}
    for les in all_lessons:
        by.setdefault(les.course, []).append(les)
    out, i = [], 0
    while any(i < len(v) for v in by.values()):
        out += [v[i] for v in by.values() if i < len(v)]
        i += 1
    return out


def _guard(text: str) -> bool:
    from ..content import guard
    return guard.refusal(text) is None and not UNSAID.search(text)


def lesson_title(segments: list[dict]) -> str:
    """The lesson's title as its first slide shows it."""
    first = segments[0] if segments else {}
    m = re.search(r"<h1>(.*?)</h1>", str(first.get("html") or ""), re.DOTALL)
    if m:
        return html.unescape(re.sub(r"<[^>]+>", "", m.group(1))).strip()
    said = str(first.get("say") or "")
    return said.split(". ", 1)[-1].rstrip(".").strip()


def _score(heading: str) -> int:
    h = heading.strip().lower()
    for prefix, score in HEADING_SCORE:
        if (h == prefix) if prefix in ("", "next", "check") else h.startswith(prefix):
            return score
    return 1


def sections(segments: list[dict]) -> list[tuple[str, list[int]]]:
    """(heading, segment indexes) in lesson order, the title slide excluded."""
    out: list[tuple[str, list[int]]] = []
    for i, s in enumerate(segments):
        if s.get("kind") == "title":
            continue
        head = str(s.get("heading") or "")
        if out and out[-1][0] == head:
            out[-1][1].append(i)
        else:
            out.append((head, [i]))
    return out


# A line that leans on something said before it: a pronoun or connective
# up front ("That's why...", "So the rule...", "Two: ..."), or a pointer
# back ("the list above", "earlier"). Such a line may be in a Short only
# when the line before it is too.
_BACKREF = re.compile(
    r"^(that|this|these|those|it|its|they|them|their|so|then|but|and|also|or|"
    r"which|here|there|one|two|three|four|five|six|first|second|third|next|"
    r"finally|another|both|each|same)\b", re.IGNORECASE)
# Pointers past the run ("the checklist above"): never in a Short. A
# reference inside the same section ("that list") is covered by the
# leading-word rule, since such a line is only kept after its own context.
_POINTER = re.compile(r"\b(above|below|earlier|previous(ly)?|as before|from before)\b",
                      re.IGNORECASE)
WPS = 2.5                        # words a second, when no mp3 is measured
BODY_MAX_S = 34.0                # the section and the action together
SHORT_MAX_S = 55.0               # title, body, action, close and pads
CLOSE_S = 6.0                    # the Fish close, before it is made
PAD_S = 0.7


def standalone(text: str) -> bool:
    """Can this line open a run: does it lean on nothing said before it?
    An empty body is a line that is all heading, which leans on nothing."""
    t = " ".join((text or "").split())
    return not _BACKREF.match(t) and not _POINTER.search(t)


def _secs(segments: list[dict], i: int, durations: list[float] | None) -> float:
    if durations and i < len(durations) and durations[i] > 0:
        return durations[i]
    return len(str(segments[i].get("say") or "").split()) / WPS


def _said(segments: list[dict], idxs: list[int]) -> str:
    return " ".join(str(segments[i].get("say") or "") for i in idxs)


def _body(segment: dict) -> str:
    """A line without the section heading the narration says first: a
    section's opener begins with its own heading ("One thing to do today.
    Open your notes..."), which is a title, not a word leaning back."""
    text = " ".join(str(segment.get("say") or "").split())
    head = " ".join(str(segment.get("heading") or "").split()).rstrip(".")
    if head and text.lower().startswith((head.lower() + ".", head.lower() + ":")):
        text = text[len(head) + 1:].lstrip()
    elif head and text.lower().startswith(head.lower() + " "):
        return ""                   # the heading is the start of its own sentence: standalone
    return text


def _usable(segments: list[dict], run: list[int]) -> bool:
    """Every line in the run either opens it standalone or follows the line
    before it in the lesson; nothing points back past the run; the words
    are clean to say."""
    if not run or not standalone(_body(segments[run[0]])):
        return False
    for a, b in itertools.pairwise(run):
        if b != a + 1 and not standalone(_body(segments[b])):
            return False
    if any(_POINTER.search(str(segments[i].get("say") or "")) for i in run):
        return False
    return _guard(_said(segments, run))


def pick(segments: list[dict], durations: list[float] | None = None
         ) -> tuple[list[int], list[int]] | None:
    """(the section's lines, the action's lines) for the lesson's most
    important part, or None when no section reads on its own.

    A section is taken from its opener, because the opener states what the
    section is about ("Permission is one of three things..."): whole when it
    fits; otherwise its opener and its closing line, when the closer stands
    on its own ("No campaign, no clip."); otherwise not at all. The action
    is the first line of the lesson's "One thing to do today" or "Try it",
    when it stands on its own and fits."""
    best: tuple[int, int, list[int]] | None = None

    def secs(run: list[int]) -> float:
        return sum(_secs(segments, i, durations) for i in run)

    for order, (head, idxs) in enumerate(sections(segments)):
        score = _score(head)
        if score <= 0:
            continue
        whole = list(idxs)
        if secs(whole) <= BODY_MAX_S:
            run = whole
            score += 1
        elif len(idxs) > 1 and secs([idxs[0], idxs[-1]]) <= BODY_MAX_S \
                and standalone(_body(segments[idxs[-1]])):
            run = [idxs[0], idxs[-1]]
        else:
            continue
        if secs(run) < MIN_WORDS / WPS or not _usable(segments, run):
            continue
        if any(len(str(segments[i].get("say") or "").split()) <= PUNCHY for i in run):
            score += 2
        cand = (score, -order, run)
        if best is None or cand[:2] > best[:2]:
            best = cand
    if best is None:
        return None
    run = best[2]
    action: list[int] = []
    for head, idxs in sections(segments):
        h = head.strip().lower()
        if not h.startswith(("one thing to do today", "try it")):
            continue
        first = idxs[0]
        if first in run:
            break
        if secs(run) + secs([first]) <= BODY_MAX_S and _usable(segments, [first]):
            action = [first]
        break
    return run, action


_FUNCTION_WORDS = frozenset({"with", "of", "the", "a", "an", "to", "and", "or", "in", "on",
                             "for", "at", "by", "from", "your", "my", "is", "are", "that"})


def caption_text(text: str) -> str:
    """A segment's words as they are shown: the section heading the
    narration repeats at the start ("The idea. ...") stays, it is said."""
    return " ".join(text.split())


HASHTAGS_TIKTOK = "#atlasacademy #aiautomation #smallbusiness #learnai"
HASHTAGS_YOUTUBE = "#atlasacademy #aiautomation"


def _hook_first(segments: list[dict], run: list[int]) -> list[int]:
    """The run with its shortest standalone line moved to the front when it
    is a rule worth opening on ("No campaign, no clip.") and lifting it
    leaves the line after it standing: open with the point, then the
    reasoning (the plan's "show the result first"). Otherwise as it was."""
    best = None
    for pos, i in enumerate(run):
        body = _body(segments[i])
        n = len(body.split())
        if pos == 0 or not (3 <= n <= PUNCHY) or not standalone(body):
            continue
        nxt = run[pos + 1] if pos + 1 < len(run) else None
        if nxt is not None and not standalone(_body(segments[nxt])):
            continue
        if best is None or n < best[1]:
            best = (pos, n)
    if best is None:
        return run
    pos = best[0]
    return [run[pos]] + run[:pos] + run[pos + 1:]


def build(les: Lesson, durations: list[float] | None = None
          ) -> tuple[str, list[Beat], str] | None:
    """(lesson title, beats, TikTok caption) for this lesson, or None when
    it has no part that reads on its own.

    The Short opens on the lesson's point in its own voice (no "Lesson 1
    point 2" intro: the lesson and its title ride on screen), then the
    section with the lesson's heading above it, then the lesson's action
    when there is one, then one close naming the course and the store.
    `youtube_text` gives the YouTube title and description."""
    from ..content.drafts import AI_LABEL
    segments = json.loads((les.path / "segments.json").read_text(encoding="utf-8"))
    chosen = pick(segments, durations)
    if not chosen:
        return None
    run, action = chosen
    run = _hook_first(segments, run)
    title = lesson_title(segments)
    product_slug, course = COURSES[les.course]
    audio = sorted((les.path / "audio").glob("*.mp3"))
    beats = []
    for n, i in enumerate(run + action):
        beats.append(Beat("hook" if n == 0 else "line",
                          caption_text(str(segments[i]["say"])), str(audio[i]),
                          heading=str(segments[i].get("heading") or "")))
    close = f"That's lesson {les.lesson} of {course}. The whole course is at {STORE_SAID}."
    beats.append(Beat("cta", close, ""))
    total = (sum(_secs(segments, i, durations) for i in [*run, *action])
             + CLOSE_S + PAD_S * len(beats))
    if total > SHORT_MAX_S:
        return None
    hook = _body(segments[run[0]]) or title
    first = hook if len(hook) <= 90 and _guard(hook) else title
    if not _guard(first):
        first = f"Lesson {les.lesson}: {course}"
    # TikTok shows no link under 1,000 followers: name the page, never
    # promise a link in the bio (the growth plan, 2026-10-12).
    caption = (f"{first}\n"
               f"{title}: lesson {les.lesson} of {course}. The whole course is at "
               f"{STORE}/{product_slug}/\n{HASHTAGS_TIKTOK}\n\n{AI_LABEL}")
    return title, beats, caption


def youtube_text(les: Lesson, title: str, beats: list[Beat]) -> str:
    """The YouTube Short's description; its first line is the title."""
    from ..content.drafts import AI_LABEL
    product_slug, course = COURSES[les.course]
    yt_title = title if 2 <= len(title) <= 90 and _guard(title) \
        else f"Lesson {les.lesson}: {course}"
    part = next((b.heading for b in beats if b.kind in ("hook", "line") and b.heading), "")
    about = f' The part on "{part}".' if part and _guard(part) else ""
    return (f"{yt_title}\n"
            f"From {course}, lesson {les.lesson}.{about} The whole course: "
            f"https://{STORE}/{product_slug}/\n{HASHTAGS_YOUTUBE}\n\n{AI_LABEL}")


def durations_of(les: Lesson) -> list[float]:
    """Each segment's narration length, read by ffprobe (blocking)."""
    import subprocess
    out = []
    for mp3 in sorted((les.path / "audio").glob("*.mp3")):
        r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                            "-of", "csv=p=0", str(mp3)], capture_output=True, text=True,
                           check=False)
        try:
            out.append(float(r.stdout.strip()))
        except ValueError:
            out.append(0.0)
    return out


def find(key: str, base: Path | None = None) -> Lesson | None:
    """The lesson a `lesson:<course>/<id>` key names."""
    m = re.fullmatch(r"lesson:([a-z0-9-]+)/([0-9.]+)", key or "")
    if not m or m.group(1) not in COURSES:
        return None
    d = (base or root()) / m.group(1) / m.group(2)
    return Lesson(m.group(1), m.group(2), d) if (d / "segments.json").is_file() else None
