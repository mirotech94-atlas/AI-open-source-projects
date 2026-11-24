"""The teaching Short's frame: each scene recomposed at 1080x1920.

docs/academy-video-system/components/ShortsFrame (the owner, 2026-10-17): the
`shorts-hook` headline from frame 0, the active panel recomposed for
portrait -- never the 16:9 lesson squeezed or letterboxed -- and the
`shorts-caption` plate, all inside the vertical safe area (x 72, below 220,
above 420 from the bottom). `shorts-caption` (60 px) is the smallest text
on screen: what the 16:9 slide set smaller is drawn at 60 or left out.

Every kind of scene is drawn natively here (NATIVE_KINDS) from the scene's
own words, except a live demo (CROP_KINDS): its picture is the recording,
so the Short takes the demo's prepared crop from the long render (the
step's box, already pushed in on by the long render's focus-zoom) and the
renderer lays it into DEMO_BOX; this page draws only its frame.

Text is never clipped: `layout` measures each block in rows of the
caption's line and drops what does not fit -- the oldest list items, a
node's second line, a heading -- never the step being said. The captions
themselves are libass's (tutorial_short.caption_ass) in CAPTION_BOX.

Standalone CSS from video_tokens only: the 16:9 slide CSS
(tutorial_design.BASE) sets sizes a Short must never show.
"""
from __future__ import annotations

import html
import math
from dataclasses import dataclass
from itertools import pairwise

from . import video_tokens as vt
from .tutorial_series import LAB_LABELS, SCENE_KINDS, Scene, plain

W, H = 1080, 1920
X = vt.SAFE_9_16_X                                   # 72
INNER_W = W - 2 * X                                  # 936
TOP, BOTTOM = vt.SAFE_9_16_TOP, H - vt.SAFE_9_16_BOTTOM   # 220, 1500
HOOK = vt.SHORTS_HOOK                                # 88/96 600
TEXT = vt.SHORTS_CAPTION                             # 60/70 500: the smallest size
# Two lines at most. Plex's glyph box is 1.3 em (ascender + descender), taller
# than the 96 px line: HOOK_INK above and below keeps every glyph in the box.
HOOK_INK = math.ceil((HOOK.size * vt.ASS_CELL - HOOK.line) / 2)
HOOK_BOX = (X, TOP, INNER_W, 2 * HOOK.line + 2 * HOOK_INK)
# The captions' zone, bottom of the safe area: two of libass's lines (its
# 1.3 em cell) and the plate's padding (tutorial_short.CAPTION_LINES,
# CAPTION_PAD).
CAPTION_PAD = vt.STEP_2
CAPTION_LINE = round(TEXT.size * vt.ASS_CELL)
CAPTION_H = 2 * CAPTION_LINE + 2 * CAPTION_PAD
CAPTION_BOX = (X, BOTTOM - CAPTION_H, INNER_W, CAPTION_H)
GAP = vt.STEP_6                                      # 48 between the three
PANEL_TOP = TOP + HOOK_BOX[3] + GAP
PANEL_BOX = (X, PANEL_TOP, INNER_W, CAPTION_BOX[1] - GAP - PANEL_TOP)
PANEL_PAD, BORDER = vt.STEP_4, 2
PANEL_INNER_W = INNER_W - 2 * (PANEL_PAD + BORDER)
PANEL_INNER_H = PANEL_BOX[3] - 2 * (PANEL_PAD + BORDER)
# What layout fills: the panel less the glyph ink a 60 px line's 1.3 em box
# puts past its 70 px line at the top and the bottom.
FIT_H = PANEL_INNER_H - 2 * math.ceil((TEXT.size * vt.ASS_CELL - TEXT.line) / 2)
# The demo's prepared crop (the recording's 16:9 hole), the full width of
# the safe area, centred in the panel's place.
DEMO_W, DEMO_H = INNER_W, round(INNER_W * 9 / 16) // 2 * 2
DEMO_BOX = (X, PANEL_TOP + (PANEL_BOX[3] - DEMO_H) // 2, DEMO_W, DEMO_H)

CROP_KINDS = ("demo",)
NATIVE_KINDS = tuple(k for k in SCENE_KINDS if k not in CROP_KINDS)

# Measure. An estimate a little wider than Plex's real advance (about
# 0.52 em for running text, 0.6 em for Mono), so a block is counted at
# least as tall as Chromium draws it.
SANS_ADVANCE, MONO_ADVANCE = 0.58, 0.62
ITEM_PAD = vt.STEP_1                                 # above and below a list row
SPACE = vt.STEP_2                                    # between blocks
MARK_W = 72                                          # a number, letter or tick before a row
NODE_PAD, ARROW_H = vt.STEP_1, 40
BUBBLE_PAD = vt.STEP_2
LETTERS = "ABCDEFG"


@dataclass(frozen=True)
class Block:
    """One thing the panel draws: `role` picks its look and its padding."""
    role: str        # head | item | code | node | bubble | arrow | sub
    text: str = ""
    mark: str = ""   # drawn before the text in MARK_W (a number, letter, tick, label)
    state: str = ""  # now | past | lit | dim | on | right | wrong
    second: str = ""  # a node's second line


def lines(text: str, width: int, *, mono: bool = False) -> int:
    """Rows `text` wraps to at TEXT size in `width` px, word by word (a word
    wider than the row breaks, as overflow-wrap:anywhere does)."""
    per = max(1, int(width // (TEXT.size * (MONO_ADVANCE if mono else SANS_ADVANCE))))
    total = 0
    for para in text.split("\n"):
        row = 0
        n = 1
        for word in para.split(" "):
            w = len(word)
            if row == 0:
                row = w
            elif row + 1 + w <= per:
                row += 1 + w
            else:
                n += 1
                row = w
            while row > per:
                n += 1
                row -= per
        total += n
    return total


def _block_h(b: Block) -> int:
    width = PANEL_INNER_W
    if b.role == "arrow":
        return ARROW_H
    if b.role == "code":
        return lines(b.text, width, mono=True) * TEXT.line
    if b.role == "bubble":
        inner = width - 2 * (BUBBLE_PAD + BORDER)
        return lines(b.text, inner) * TEXT.line + 2 * (BUBBLE_PAD + BORDER)
    if b.role == "node":
        inner = width - 2 * (vt.STEP_3 + BORDER)
        n = lines(b.text, inner) + (lines(b.second, inner) if b.second else 0)
        return n * TEXT.line + 2 * (NODE_PAD + BORDER)
    if b.mark:
        width -= MARK_W
    pad = 2 * ITEM_PAD if b.role == "item" else 0
    return lines(b.text, width) * TEXT.line + pad


def height(blocks: list[Block]) -> int:
    """The panel height `blocks` take, their spacing included."""
    if not blocks:
        return 0
    gaps = sum(0 if b.role == "code" and a.role == "code" else SPACE
               for a, b in pairwise(blocks))
    return sum(_block_h(b) for b in blocks) + gaps


def _fit(head: list[Block], body: list[Block], keep: int) -> list[Block]:
    """`head` + `body`, dropping body blocks from the front (never the
    `keep`th) and then the heading until it fits the panel."""
    body = list(body)
    while height(head + body) > FIT_H and keep > 0 and len(body) > 1:
        body.pop(0)
        keep -= 1
    while height(head + body) > FIT_H and head:
        head = head[1:]
    while height(head + body) > FIT_H and len(body) > 1:
        body.pop()                                   # after the step: never shown yet
    return head + body


def _items(sc: Scene, step: int, marks: list[str]) -> list[Block]:
    """The list rows said so far: the current one lit, earlier ones muted;
    rows not yet reached are not drawn (the slide hides them too)."""
    shown = sc.items[:step + 1] if sc.kind != "checklist" else sc.items
    out = []
    for i, item in enumerate(shown):
        first = item.split("\n")[0]
        state = "now" if i == step else "past" if i < step else ""
        out.append(Block("item", first, mark=marks[i] if i < len(marks) else "",
                         state=state))
    return out


def layout(sc: Scene, step: int) -> list[Block]:
    """What the panel shows for step `step` of `sc`, fitted to the panel."""
    kind = sc.kind
    step = max(0, step)
    head = [Block("head", sc.heading)] if sc.heading else []
    if kind == "code":
        code = sc.code.split("\n")
        lit = sc.marks[step] if sc.marks and step < len(sc.marks) else None
        rows = [Block("code", ln or " ", state="" if lit is None else
                      "lit" if lit[0] <= i < lit[1] else "dim")
                for i, ln in enumerate(code)]
        if height(head + rows) > FIT_H and lit is not None:
            rows = rows[lit[0]:lit[1]]               # the lines being said
        return _fit(head, rows, 0)
    if kind in ("diagram", "hook", "payoff"):
        upto = step if kind == "diagram" else len(sc.items)
        nodes: list[Block] = []
        for i, item in enumerate(sc.items):
            first, _, rest = item.partition("\n")
            if i:
                nodes.append(Block("arrow", state="on" if i <= upto else ""))
            nodes.append(Block("node", first, second=rest,
                               state="on" if i <= upto else ""))
        result = sc.code.split("\n") if kind == "payoff" or (
            kind == "hook" and step >= 1) else []
        heads = [] if kind != "diagram" else head

        def bubble() -> list[Block]:
            return [Block("bubble", "\n".join(result))] if result else []
        if height(heads + nodes + bubble()) > FIT_H:
            nodes = [Block(b.role, b.text, state=b.state) for b in nodes]   # one line each
        if height(heads + nodes + bubble()) > FIT_H:
            nodes = [b for b in nodes if b.role != "arrow"]
        while result and height(heads + nodes + bubble()) > FIT_H:
            result.pop()                 # a long result shows its first lines
        return _fit(heads, nodes + bubble(), len(nodes))
    if kind == "quiz":
        reveal = step >= 1 and sc.answer >= 0
        q = [Block("head", sc.heading)]
        opts = []
        for i, item in enumerate(sc.items):
            state = ("right" if i == sc.answer else "wrong") if reveal else ""
            opts.append(Block("item", item, mark="✓" if state == "right" else LETTERS[i],
                              state=state))
        after = ([Block("sub", sc.caption)] if reveal and sc.caption else [])
        got = q + opts + after
        if height(got) > FIT_H:
            got = q + opts
        return got if height(got) <= PANEL_INNER_H else opts
    if kind == "title":
        sub = plain(sc.said[0]).split(". ")[0].rstrip(".") + "." if sc.said else ""
        return _fit(head, [Block("sub", sub)] if sub else [], 0)
    if kind == "subscribe":
        return _fit(head, [Block("item", "Subscribe", state="now")], 0)
    if kind == "lab":
        # each row's label (Do, Expect, Stuck?, Challenge) on a line of its
        # own above it: a word too wide for the mark column
        rows = []
        for i, item in enumerate(sc.items[:step + 1]):
            label = LAB_LABELS[i] if i < len(LAB_LABELS) else ""
            state = "now" if i == step else "past"
            rows.append([Block("head", label)] * bool(label)
                        + [Block("item", item, state=state)])
        while len(rows) > 1 and height(head + [b for r in rows for b in r]) > FIT_H:
            rows.pop(0)                  # the oldest row first, never the one said
        return _fit(head, [b for r in rows for b in r], 0)
    if kind == "demo":
        return []
    marks = ([str(i + 1) for i in range(len(sc.items))]
             if kind in ("steps", "pause") else
             ["✓" if i <= step else "" for i in range(len(sc.items))]
             if kind == "checklist" else [])
    return _fit(head, _items(sc, step, marks), step)


# ------------------------------------------------------------------- page

def _px(style: vt.TypeStyle, weight: int | None = None) -> str:
    fam = vt.SANS_FAMILY if style.family == "sans" else vt.MONO_FAMILY
    return f"font:{weight or style.weight} {style.size}px/{style.line}px {fam}"


CSS = f"""
html,body{{margin:0;padding:0;width:{W}px;height:{H}px;background:{vt.NAVY};overflow:hidden}}
body{{position:relative;color:{vt.PAPER}}}
.hook{{position:absolute;left:{HOOK_BOX[0]}px;top:{HOOK_BOX[1]}px;width:{HOOK_BOX[2]}px;
height:{HOOK_BOX[3]}px;margin:0;padding:{HOOK_INK}px 0;box-sizing:border-box;display:flex;
align-items:flex-end;{HOOK.css()};
color:{vt.PAPER};overflow-wrap:anywhere;white-space:pre-line}}
.panel{{position:absolute;left:{PANEL_BOX[0]}px;top:{PANEL_BOX[1]}px;width:{PANEL_BOX[2]}px;
height:{PANEL_BOX[3]}px;box-sizing:border-box;padding:{PANEL_PAD}px;background:{vt.NAVY_RAISED};
border:{BORDER}px solid {vt.NAVY_LINE};border-radius:{vt.RADIUS_CARD}px;display:flex;
flex-direction:column;justify-content:center;gap:{SPACE}px}}
.panel>*{{flex:none}}
.b{{margin:0;{_px(TEXT)};color:{vt.PAPER};overflow-wrap:anywhere;display:flex;gap:0}}
.b .m{{flex:none;width:{MARK_W}px;{_px(TEXT, 600)};color:{vt.BLUE_TEXT}}}
.b .t{{flex:1;min-width:0}}
.head{{{_px(TEXT, 600)};color:{vt.BLUE_TEXT}}}
.sub{{color:{vt.PAPER_MUTED}}}
.item{{padding:{ITEM_PAD}px 0}}
.past{{color:{vt.PAPER_MUTED}}}
.now{{color:{vt.PAPER};box-shadow:inset -{INNER_W}px 0 0 {vt.BLUE_TINT}}}
.right .m,.right .t{{color:{vt.BLUE_TEXT}}}
.wrong{{color:{vt.PAPER_MUTED}}}
.codes{{display:flex;flex-direction:column}}
.code{{margin:0;{_px(vt.TypeStyle("code", "mono", TEXT.size, TEXT.line, 400))};
color:{vt.PAPER};white-space:break-spaces;overflow-wrap:anywhere}}
.code.dim{{color:{vt.PAPER_MUTED}}}
.node{{box-sizing:border-box;padding:{NODE_PAD}px {vt.STEP_3}px;border:{BORDER}px solid {vt.NAVY_LINE};
border-radius:{vt.RADIUS_CHIP}px;{_px(TEXT, 600)};color:{vt.PAPER_MUTED};overflow-wrap:anywhere}}
.node.on{{border-color:{vt.BLUE};color:{vt.PAPER}}}
.node .s{{display:block;{_px(TEXT, 400)};color:{vt.PAPER_MUTED}}}
.arrow{{height:{ARROW_H}px;display:flex;justify-content:center}}
.arrow i{{width:4px;height:100%;background:{vt.NAVY_LINE}}}
.arrow.on i{{background:{vt.BLUE}}}
.bubble{{box-sizing:border-box;padding:{BUBBLE_PAD}px {BUBBLE_PAD}px;background:{vt.NAVY};
border:{BORDER}px solid {vt.BLUE};border-radius:{vt.RADIUS_CARD}px;{_px(TEXT)};
color:{vt.PAPER};overflow-wrap:anywhere;white-space:pre-line}}
.hole{{position:absolute;left:{DEMO_BOX[0] - BORDER}px;top:{DEMO_BOX[1] - BORDER}px;
width:{DEMO_BOX[2]}px;height:{DEMO_BOX[3]}px;border:{BORDER}px solid {vt.NAVY_LINE};
border-radius:{vt.RADIUS_CHIP}px;background:{vt.NAVY_RAISED}}}
.close{{justify-content:center}}
.close .title{{{_px(TEXT, 600)};color:{vt.PAPER};margin:0;overflow-wrap:anywhere}}
"""


def _block(b: Block) -> str:
    t = html.escape(b.text)
    if b.role == "arrow":
        return f'<div class="arrow {b.state}"><i></i></div>'
    if b.role == "code":
        return f'<pre class="code {b.state}">{t}</pre>'
    if b.role == "node":
        second = f'<span class="s">{html.escape(b.second)}</span>' if b.second else ""
        return f'<div class="node {b.state}">{t}{second}</div>'
    if b.role == "bubble":
        return f'<div class="bubble">{t}</div>'
    mark = f'<span class="m">{html.escape(b.mark)}</span>' if b.mark else ""
    return f'<p class="b {b.role} {b.state}">{mark}<span class="t">{t}</span></p>'


def _page(hook: str, body: str) -> str:
    return (f'<!doctype html><html><head><meta charset="utf-8"><style>{vt.fonts_css()}'
            f'{CSS}</style></head><body><h1 class="hook">{html.escape(hook)}</h1>'
            f'{body}</body></html>')


def shot(sc: Scene | None, step: int, hook: str) -> str:
    """The frame for step `step` of `sc` under `hook` (an empty panel when
    no scene is being said); a demo draws only the hole its crop fills."""
    if sc is not None and sc.kind in CROP_KINDS:
        return _page(hook, '<div class="hole"></div>')
    blocks = layout(sc, step) if sc is not None else []
    inner = "".join(_block(b) for b in blocks)
    if any(b.role == "code" for b in blocks):
        codes = "".join(_block(b) for b in blocks if b.role == "code")
        rest = "".join(_block(b) for b in blocks if b.role != "code")
        inner = f'{rest}<div class="codes">{codes}</div>'
    return _page(hook, f'<div class="panel">{inner}</div>')


def close_shot(hook: str, title: str) -> str:
    """The closing card: the point made, the longer lesson named by its
    exact title -- no link, no "below", nothing the account cannot show."""
    body = (f'<div class="panel close"><p class="b head">The full lesson</p>'
            f'<p class="title">{html.escape(title)}</p></div>')
    return _page(hook, body)
