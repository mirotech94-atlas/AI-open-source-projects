"""The beat planner: a lesson's segments and their narration lengths in, a
timeline of short, moving beats and burned-in caption cues out.

Pure: no ffmpeg, no browser, no files. Durations (and, if you have them, the
pauses found in each narration) are inputs, so the planner is tested without
the lesson data.

Miro, 2026-11-14: ATLAS Academy black and blue look; captions always burned in; animated
simulated diagrams only (no real screen recordings); lessons as short as
possible; no frozen frame over 6 s. So:

  * the timeline is sting + the narration as recorded + checkpoint pauses +
    a short end card. Nothing is padded and nothing holds past its audio.
  * every beat is at most MAX_BEAT seconds and shows something new (a list
    item, a table cell, a key phrase, a node lit): the picture never stands
    still for 6 s, and nothing appears before its words are spoken.
  * what is drawn is only words from the lesson text. A long item is shown
    by a key phrase of it; its full words are in the caption. Nothing is
    shrunk below the type floor: an item too long for its box is split into
    more beats, never squeezed.
  * a screen-recording beat does not exist (Miro's answer). A step that
    would have shown a tool is drawn as an animated diagram (`flow`, `code`).
"""
from __future__ import annotations

import html as _html
import math
import re
from dataclasses import dataclass, field
from html.parser import HTMLParser

# ------------------------------------------------------------------ timing
FPS = 30
STING = 1.0              # the brand sting, video_tokens.STING_MS
END_CARD = 4.0           # "at most 4 s"
PAUSE = 1.2              # one checkpoint pause, "at most 1.5 s"
PAUSE_MAX = 1.5
MAX_BEAT = 5.0           # planned beat length; the gate's hard limit is FREEZE_MAX
FREEZE_MAX = 6.0         # Miro: no frozen frame over 6 s
MIN_BEAT = 1.2           # a piece shorter than this joins its neighbour
CUE_MAX_S = 5.0
CUE_MAX_CHARS = 84       # two caption lines at 44 px across 1728 px hold ~120
CHECK_HEADS = ("check", "quick check", "exercise", "try it")

# ------------------------------------------------------------------- type
# px sizes the scenes draw with. The 16:9 floor is video_tokens.FLOOR_16_9
# (28). The 60+ course gets a larger floor: 36 px (the `callout` token) --
# the docs name no 60+ floor, so this is a default Miro can change here.
FLOOR = 28
FLOOR_SENIOR = 36
STYLE = {
    False: dict(key=56, item=36, chip=28, cell=32, cap=44, items=4, words=7, key_chars=70,  # noqa: C408
                item_chars=64, cell_chars=34, rows=5, lines=12, code=30),
    True: dict(key=72, item=44, chip=36, cell=40, cap=52, items=3, words=5, key_chars=48,  # noqa: C408
               item_chars=46, cell_chars=26, rows=3, lines=8, code=36),
}
MAIN_W = 1500            # the text column, px
AVG_EM = 0.55            # Plex Sans average advance, in em (a safe over-estimate)

STOP_TAIL = {"a", "an", "the", "of", "to", "and", "or", "in", "on", "for", "with", "that",
             "is", "are", "your", "you", "at", "by", "from", "as", "but", "so", "it", "its",
             "this", "these", "those", "be", "can", "will", "not", "if", "when", "than"}
STOP_HEAD = {"and", "but", "so", "then", "also", "now", "or", "because"}


def chars_per_line(px: int, width: int = MAIN_W) -> int:
    return max(1, int(width / (px * AVG_EM)))


def fits(text: str, px: int, lines: int, width: int = MAIN_W) -> bool:
    """True when `text` at `px` fits `lines` lines of `width`: the planner's
    own character budget (the render re-checks the real DOM)."""
    return math.ceil(max(1, len(text)) / chars_per_line(px, width)) <= lines


# ------------------------------------------------------------------- types
@dataclass(frozen=True)
class Seg:
    kind: str
    heading: str
    html: str
    say: str
    seconds: float


@dataclass(frozen=True)
class Cue:
    t0: float
    t1: float
    text: str


@dataclass(frozen=True)
class Beat:
    kind: str                # sting title map key quote list flow table code checkpoint end
    t0: float
    t1: float
    seg: int                 # index into the segments, -1 for sting / pause / end
    data: dict = field(default_factory=dict, compare=False)

    @property
    def seconds(self) -> float:
        return round(self.t1 - self.t0, 3)


@dataclass(frozen=True)
class Plan:
    beats: tuple[Beat, ...]
    cues: tuple[Cue, ...]
    audio: tuple[tuple[int, float, float], ...]   # (segment, t0, t1) where its mp3 plays
    pauses: tuple[tuple[float, float], ...]
    total: float
    senior: bool
    audio_seconds: float

    def sting_span(self) -> tuple[float, float]:
        return (0.0, STING)

    def end_span(self) -> tuple[float, float]:
        return (round(self.total - END_CARD, 3), self.total)


# ----------------------------------------------------------- text helpers
def norm(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()


def sentences(say: str) -> list[str]:
    """The narration split into sentences, words verbatim."""
    parts = re.split(r'(?<=[.!?])["”)]?\s+(?=["“(]?[A-Z0-9])', say.strip())
    return [p.strip() for p in parts if p.strip()]


def _frac_chars(s: str) -> int:
    return max(1, len(s))


def align(sents: list[str], seconds: float, silences: list[tuple[float, float]] | None = None
          ) -> list[tuple[float, float]]:
    """Each sentence's (start, end) inside a narration of `seconds`.

    `silences` is (middle, length) of every pause found in the mp3 (ffmpeg
    silencedetect). With n sentences the n-1 longest pauses are the
    sentence boundaries, when that leaves every sentence a believable speed
    (0.4-2.5x the segment's average characters per second). Otherwise, and
    with no pauses, characters stand in for time."""
    n = len(sents)
    if n <= 1:
        return [(0.0, seconds)] * max(n, 1) if n else []
    chars = [_frac_chars(s) for s in sents]
    total = sum(chars)

    def by_chars() -> list[tuple[float, float]]:
        out, t = [], 0.0
        for c in chars:
            d = seconds * c / total
            out.append((round(t, 3), round(t + d, 3)))
            t += d
        out[-1] = (out[-1][0], round(seconds, 3))
        return out

    if silences and len(silences) >= n - 1:
        pick = sorted(sorted(silences, key=lambda s: -s[1])[:n - 1])
        cuts = [0.0] + [m for m, _ in pick] + [seconds]
        spans = [(round(cuts[i], 3), round(cuts[i + 1], 3)) for i in range(n)]
        cps = total / seconds
        ok = all(b > a and 0.4 <= (chars[i] / (b - a)) / cps <= 2.5
                 for i, (a, b) in enumerate(spans))
        if ok:
            return spans
    return by_chars()


def _split_at(text: str, frac: float = 0.5) -> tuple[str, str]:
    """Split `text` near `frac` of its length, at a clause mark if one is
    near, else at a space."""
    mid = int(len(text) * frac)
    best = None
    for m in re.finditer(r"[,;:—](?=\s)|\s-\s", text):
        pos = m.end()
        if 12 <= pos <= len(text) - 12 and (best is None or abs(pos - mid) < abs(best - mid)):
            best = pos
    if best is not None and abs(best - mid) <= len(text) * 0.3:
        return text[:best].strip(), text[best:].strip()
    sp = [m.start() for m in re.finditer(r"\s", text) if 8 <= m.start() <= len(text) - 8]
    if not sp:
        return text[:mid].strip(), text[mid:].strip()
    cut = min(sp, key=lambda p: abs(p - mid))
    return text[:cut].strip(), text[cut:].strip()


def chunks(text: str, t0: float, t1: float, *, max_chars: int = CUE_MAX_CHARS,
           max_s: float = CUE_MAX_S) -> list[Cue]:
    """`text` spoken over [t0, t1] as caption cues, each short enough to
    read and to be a beat. Time inside a sentence is by characters."""
    dur = t1 - t0
    if len(text) <= max_chars and dur <= max_s or len(text) < 24:
        return [Cue(round(t0, 3), round(t1, 3), text)]
    a, b = _split_at(text)
    if not a or not b:
        return [Cue(round(t0, 3), round(t1, 3), text)]
    tm = t0 + dur * len(a) / (len(a) + len(b))
    return (chunks(a, t0, tm, max_chars=max_chars, max_s=max_s)
            + chunks(b, tm, t1, max_chars=max_chars, max_s=max_s))


# --------------------------------------------------------- html -> visual
class _Plain(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.text: list[str] = []
        self.bold: list[str] = []
        self._b = 0

    def handle_starttag(self, tag, attrs):
        if tag == "b":
            self._b += 1
            self.bold.append("")

    def handle_endtag(self, tag):
        if tag == "b" and self._b:
            self._b -= 1

    def handle_data(self, data):
        self.text.append(data)
        if self._b and self.bold:
            self.bold[-1] += data


def plain(fragment: str) -> tuple[str, list[str]]:
    p = _Plain()
    p.feed(fragment)
    return re.sub(r"\s+", " ", "".join(p.text)).strip(), [b.strip() for b in p.bold if b.strip()]


@dataclass(frozen=True)
class Visual:
    kind: str                         # para quote list ol table code title
    items: tuple[str, ...] = ()
    head: tuple[str, ...] = ()        # table columns
    rows: tuple[tuple[str, ...], ...] = ()
    bold: tuple[str, ...] = ()


def visual(seg: Seg) -> Visual:
    h = seg.html.strip()
    if seg.kind == "title":
        return Visual("title", (plain(re.sub(r"<p class=sub>.*", "", h))[0],))
    m = re.match(r"<(ul|ol)>(.*)</\1>$", h, re.DOTALL)
    if m:
        lis = re.findall(r"<li>(.*?)</li>", m.group(2), re.DOTALL)
        return Visual("ol" if m.group(1) == "ol" else "list",
                      tuple(plain(x)[0] for x in lis))
    if h.startswith("<table>"):
        rows = [[plain(c)[0] for c in re.findall(r"<t[hd]>(.*?)</t[hd]>", r, re.DOTALL)]
                for r in re.findall(r"<tr>(.*?)</tr>", h, re.DOTALL)]
        return Visual("table", head=tuple(rows[0]) if rows else (),
                      rows=tuple(tuple(r) for r in rows[1:]))
    if h.startswith("<blockquote>"):
        t, b = plain(h)
        return Visual("quote", (t,), bold=tuple(b))
    if h.startswith("<pre>"):
        body = _html.unescape(re.sub(r"</?pre>", "", h))
        return Visual("code", tuple(body.splitlines()))
    t, b = plain(h)
    return Visual("para", (t,), bold=tuple(b))


def key_phrase(text: str, *, senior: bool = False, bold: tuple[str, ...] = ()) -> str:
    """A few words of `text`, in its own order, to be the picture of a
    spoken sentence: its bold words if it has them, else its first clause.
    Never words that are not in the lesson."""
    st = STYLE[senior]
    cap = st["words"]
    for b in bold:
        if norm(b) and norm(b) in norm(text) and len(b.split()) <= cap + 2:
            return b.strip(" .,;:")
    clause = re.split(r"[,;:—]|\s-\s|[.!?]\s", text.strip(), maxsplit=1)[0]
    words = clause.split()
    while words and norm(words[0]) in STOP_HEAD:
        words.pop(0)
    words = words[:cap]
    while len(words) > 2 and norm(words[-1]) in STOP_TAIL:
        words.pop()
    out = " ".join(words).strip(" .,;:—-")
    if len(out) > st["key_chars"]:
        out = out[:st["key_chars"]].rsplit(" ", 1)[0]
    return out or text[:st["key_chars"]]


def label(text: str, *, chars: int, senior: bool = False) -> str:
    """A list item or table cell as drawn: itself when it fits `chars`, else
    its key phrase widened to the budget (first clause), cut at a word."""
    t = text.strip()
    if len(t) <= chars:
        return t.rstrip(".")
    clause = re.split(r"[;:—]|,\s|[.!?]\s", t, maxsplit=1)[0].strip()
    base = clause if len(clause) <= chars else t
    if len(base) <= chars:
        return base.rstrip(".,")
    return base[:chars].rsplit(" ", 1)[0].rstrip(" .,;:—-")


# -------------------------------------------------------------- the plan
def _step_titles(segs: list[Seg]) -> list[str]:
    seen: list[str] = []
    for s in segs:
        h = s.heading.strip()
        if (not h or s.kind == "title" or h.lower() in ("in this lesson", "next")
                or h in seen):
            continue
        seen.append(h)
    return seen


def _is_check(heading: str) -> bool:
    h = heading.strip().lower()
    return any(h.startswith(c) for c in CHECK_HEADS)


def _locate(spoken: list[str], sents: list[str]) -> list[int] | None:
    """For each unit's spoken words, the index of the sentence it starts, or
    None when any cannot be found in order."""
    out, cur = [], 0
    ns = [norm(s) for s in sents]
    for sp in spoken:
        key = norm(sp)[:14]
        if not key:
            return None
        for i in range(cur, len(ns)):
            if ns[i].startswith(key) or key in ns[i][:len(key) + 24]:
                out.append(i)
                cur = i + 1
                break
        else:
            return None
    return out


def _pieces(t0: float, t1: float, cue_edges: list[float]) -> list[tuple[float, float]]:
    """[t0, t1] cut at every caption edge inside it, then pieces under
    MIN_BEAT joined to a neighbour, then any over MAX_BEAT halved."""
    edges = [t0] + [e for e in cue_edges if t0 + 0.05 < e < t1 - 0.05] + [t1]
    segs = [(edges[i], edges[i + 1]) for i in range(len(edges) - 1)]
    merged: list[tuple[float, float]] = []
    for a, b in segs:
        if merged and (b - a < MIN_BEAT or merged[-1][1] - merged[-1][0] < MIN_BEAT) \
                and b - merged[-1][0] <= MAX_BEAT:
            merged[-1] = (merged[-1][0], b)
        else:
            merged.append((a, b))
    out: list[tuple[float, float]] = []
    for a, b in merged:
        k = max(1, math.ceil((b - a) / MAX_BEAT - 1e-9))
        for i in range(k):
            out.append((a + (b - a) * i / k, a + (b - a) * (i + 1) / k))
    return [(round(a, 3), round(b, 3)) for a, b in out]


def _window(items: list[str], now: int, k: int) -> list[str]:
    return items[max(0, now - k + 1): now + 1]


def plan(segs: list[Seg], *, senior: bool = False, silences: list | None = None,
         next_title: str = "", lesson: str = "") -> Plan:
    """The whole lesson as beats and cues. `silences[i]` is segment i's
    pauses (or None)."""
    st = STYLE[senior]
    steps = _step_titles(segs)
    beats: list[Beat] = [Beat("sting", 0.0, STING, -1)]
    cues: list[Cue] = []
    audio: list[tuple[int, float, float]] = []
    pauses: list[tuple[float, float]] = []
    t = STING
    for i, seg in enumerate(segs):
        a0, a1 = round(t, 3), round(t + seg.seconds, 3)
        audio.append((i, a0, a1))
        sents = sentences(seg.say)
        spans = align(sents, seg.seconds, (silences or [None] * len(segs))[i])
        seg_cues: list[Cue] = []
        for s, (x, y) in zip(sents, spans, strict=False):
            seg_cues += chunks(s, a0 + x, a0 + y)
        cues += seg_cues
        edges = sorted({c.t0 for c in seg_cues} | {c.t1 for c in seg_cues})
        vis = visual(seg)
        n0 = len(beats)
        step = (steps.index(seg.heading.strip()) + 1 if seg.heading.strip() in steps else 0)
        ctx = dict(head=seg.heading.strip(), step=step, steps=len(steps))  # noqa: C408
        starts = [a0 + x for x, _ in spans]                  # sentence starts, lesson time

        if vis.kind == "title":
            sub = re.search(r"<p class=sub>(.*?)</p>", seg.html, re.DOTALL)
            for p0, p1 in _pieces(a0, a1, []):
                beats.append(Beat("title", p0, p1, i, dict(  # noqa: C408
                    title=vis.items[0], lesson=lesson, sub=plain(sub.group(1))[0] if sub else "")))
        elif vis.kind in ("list", "ol") and seg.heading.strip().lower() == "in this lesson":
            idx = _locate([x.rstrip(".") for x in vis.items], sents)
            _unit_beats(beats, "map", vis.items, idx, starts, a0, a1, edges, sents, spans,
                        lambda k, n, vis=vis: {"steps": list(vis.items), "at": k}, None)
        elif vis.kind in ("list", "ol"):
            idx = _locate([x.rstrip(".,;:") for x in vis.items], sents)
            flow = (vis.kind == "ol" and 3 <= len(vis.items) <= 5)
            _unit_beats(beats, "flow" if flow else "list", vis.items, idx, starts, a0, a1,
                        edges, sents, spans, None, (ctx, st, vis, senior))
        elif vis.kind == "table":
            _table_beats(beats, vis, sents, starts, a0, a1, edges, spans, ctx, st, senior)
        elif vis.kind == "code":
            lines = list(vis.items)[: st["lines"]]
            ps = _pieces(a0, a1, edges)
            for j, (p0, p1) in enumerate(ps):
                focus = min(len(lines) - 1, j * len(lines) // len(ps)) if lines else -1
                beats.append(Beat("code", p0, p1, i, dict(
                    ctx, lines=lines, focus=focus, more=len(vis.items) > len(lines))))
        else:                                               # para / quote: key phrases
            kind = "quote" if vis.kind == "quote" else "key"
            for c in seg_cues:
                if c.t1 - c.t0 < 0.05:
                    continue
                phrase = key_phrase(c.text, senior=senior, bold=vis.bold)
                beats.append(Beat(kind, c.t0, c.t1, i, dict(ctx, phrase=phrase)))
            beats[:] = _fuse_keys(beats)
        beats[n0:] = [Beat(b.kind, b.t0, b.t1, i if b.seg < -1 else b.seg, b.data)
                      for b in beats[n0:]]
        t = a1
        nxt = segs[i + 1] if i + 1 < len(segs) else None
        if _is_check(seg.heading) and not (nxt and _is_check(nxt.heading)):
            beats.append(Beat("checkpoint", round(t, 3), round(t + PAUSE, 3), -1,
                              dict(ctx, label="Checkpoint")))
            pauses.append((round(t, 3), round(t + PAUSE, 3)))
            t += PAUSE
    end0 = round(t, 3)
    beats[:] = _no_twins(beats)
    beats.append(Beat("end", end0, round(end0 + END_CARD, 3), -1,
                      dict(lesson=lesson, next=next_title)))  # noqa: C408
    return Plan(tuple(beats), tuple(cues), tuple(audio), tuple(pauses),
                round(end0 + END_CARD, 3), senior, round(sum(s.seconds for s in segs), 3))


def _no_twins(beats: list[Beat]) -> list[Beat]:
    """Two neighbouring beats that would draw the same picture get an `alt`
    flip (the accent changes and the enter replays), so the picture is never
    the same for two beats in a row."""
    out: list[Beat] = []
    for b in beats:
        p = out[-1] if out else None
        if p and p.kind == b.kind and b.kind not in ("sting", "checkpoint") and \
                {k: v for k, v in p.data.items() if k != "alt"} == \
                {k: v for k, v in b.data.items() if k != "alt"}:
            b = Beat(b.kind, b.t0, b.t1, b.seg, dict(b.data, alt=not p.data.get("alt", False)))
        out.append(b)
    return out


def _fuse_keys(beats: list[Beat]) -> list[Beat]:
    """Neighbouring key beats of one segment that came out with the same
    phrase merge, if the result stays within MAX_BEAT (so no two frames
    in a row are identical)."""
    out: list[Beat] = []
    for b in beats:
        p = out[-1] if out else None
        if (p and p.kind == b.kind and p.seg == b.seg and p.data.get("phrase") == b.data.get("phrase")
                and b.t1 - p.t0 <= MAX_BEAT):
            out[-1] = Beat(p.kind, p.t0, b.t1, p.seg, p.data)
        else:
            out.append(b)
    return out


def _unit_beats(beats, kind, items, idx, starts, a0, a1, edges, sents, spans, mapdata, rest):
    """Beats of a list: one run per item, from the start of the sentence
    that speaks it to the start of the next item's."""
    n = len(items)
    if idx is None or len(idx) != n:                         # characters stand in
        w = [max(1, len(x)) for x in items]
        tot = sum(w)
        bounds, t = [], a0
        for x in w:
            bounds.append(t)
            t += (a1 - a0) * x / tot
    else:
        bounds = [starts[k] for k in idx]
    # the words before the first item (the section heading) get a beat of their own
    first = bounds[0]
    runs = []
    if first - a0 > 0.4:
        runs.append((-1, a0, first))
    else:
        bounds[0] = a0
    for k in range(n):
        end = bounds[k + 1] if k + 1 < n else a1
        runs.append((k, bounds[k], end))
    for k, r0, r1 in runs:
        for p0, p1 in _pieces(r0, r1, edges):
            _emit(beats, kind, k, p0, p1, p0 - r0, items, mapdata, rest)


def _emit(beats, kind, k, p0, p1, into, items, mapdata, rest):
    seg = -2                                                 # filled by the caller's index
    if kind == "map":
        data = mapdata(max(k, 0), len(items))
        beats.append(Beat("map", p0, p1, seg, data))
        return
    ctx, st, vis, senior = rest
    labels = [label(x, chars=st["item_chars"], senior=senior) for x in items]
    if k < 0:
        beats.append(Beat("key", p0, p1, seg, dict(ctx, phrase=ctx["head"] or "")))
        return
    sub = key_phrase(items[k], senior=senior) if into > 0.5 and len(items[k]) > st["item_chars"] else ""
    if kind == "flow":
        beats.append(Beat("flow", p0, p1, seg, dict(
            ctx, nodes=[label(x, chars=22, senior=senior) for x in items], now=k, sub=sub)))
    else:
        beats.append(Beat("list", p0, p1, seg, dict(
            ctx, items=_window(labels, k, st["items"]), now=min(k, st["items"] - 1),
            sub=sub, ordered=vis.kind == "ol", first=max(0, k - st["items"] + 1))))


def _table_beats(beats, vis, sents, starts, a0, a1, edges, spans, ctx, st, senior):
    cols = len(vis.head)
    cells = [(r, c) for r in range(len(vis.rows)) for c in range(cols)
             if c < len(vis.rows[r]) and vis.rows[r][c].strip()]
    spoken = [f"{vis.head[c]}: {vis.rows[r][c]}".rstrip(".") for r, c in cells]
    idx = _locate(spoken, sents)
    if idx is None or len(idx) != len(cells):
        w = [max(1, len(s)) for s in spoken]
        tot, t, bounds = sum(w), a0, []
        for x in w:
            bounds.append(t)
            t += (a1 - a0) * x / tot
    else:
        bounds = [starts[k] for k in idx]
    runs = []
    if cells and bounds[0] - a0 > 0.4:
        runs.append((None, a0, bounds[0]))
    elif cells:
        bounds[0] = a0
    for j, (r, c) in enumerate(cells):
        runs.append(((r, c), bounds[j], bounds[j + 1] if j + 1 < len(cells) else a1))
    head = [label(h, chars=st["cell_chars"]) for h in vis.head]
    for rc, r0, r1 in runs:
        for p0, p1 in _pieces(r0, r1, edges):
            if rc is None:
                beats.append(Beat("key", p0, p1, -2, dict(ctx, phrase=ctx["head"] or "")))
                continue
            r, c = rc
            lo = max(0, r - st["rows"] + 1)
            shown = []
            for rr in range(lo, r + 1):
                shown.append([label(vis.rows[rr][cc], chars=st["cell_chars"])
                              if (rr < r or cc <= c) and cc < len(vis.rows[rr]) else ""
                              for cc in range(cols)])
            beats.append(Beat("table", p0, p1, -2, dict(
                ctx, cols=head, rows=shown, now=(len(shown) - 1, c))))


# --------------------------------------------------------------- checking
def budget_problems(plan_: Plan) -> list[str]:
    """What the planned beats cannot hold at their type size: the pure
    character-budget half of the overflow check."""
    st = STYLE[plan_.senior]
    bad: list[str] = []
    for b in plan_.beats:
        d = b.data
        if b.kind in ("key", "quote") and not fits(d["phrase"], st["key"], 2):
            bad.append(f"{b.t0:.1f}s key phrase too long: {d['phrase']!r}")
        if b.kind == "list":
            for x in d["items"]:
                if not fits(x, st["item"], 2, MAIN_W - 120):
                    bad.append(f"{b.t0:.1f}s list item too long: {x!r}")
        if b.kind == "table":
            width = MAIN_W // max(1, len(d["cols"]))
            for row in d["rows"]:
                for x in row:
                    if not fits(x, st["cell"], 3, width - 60):
                        bad.append(f"{b.t0:.1f}s cell too long: {x!r}")
        if b.kind == "code":
            for x in d["lines"]:
                if len(x) > MAIN_W // int(st["code"] * 0.62):
                    bad.append(f"{b.t0:.1f}s code line too long: {x!r}")
    for c in plan_.cues:
        if not fits(c.text, st["cap"], 2, 1728):
            bad.append(f"{c.t0:.1f}s caption too long: {c.text!r}")
    return bad
