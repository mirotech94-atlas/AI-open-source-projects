"""The Academy YouTube thumbnail: one template, built as HTML/CSS, shot locally with Chrome.

Miro, 2026-11-14: the channel's layout. 1280x720 PNG on a dark ground
(#0A0D14); the title in big bold white caps, top-centre, on two lines; a glowing
royal-blue (#5B8CFF) isometric icon in the centre; three or four small labelled
callouts around it. The words come from the lesson or the product, never from
a model. No network: fonts are the store's own files. Text widths are estimated from
the character count, so the limits hold without a browser.

`build_html(...)` is pure Python (no browser, no image library) and returns the
page plus the `Layout`; `render(...)` screenshots it with Playwright Chrome
(lazy import, like tools/lesson_video/motion.py). It returns the `Layout` (every text box it drew) so a test, or the
staging step, can prove nothing overflows. `stage(...)` writes `<stem>.png`
into the staging folder, where `lesson_youtube._thumb` picks it up.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from html import escape
from pathlib import Path

W, H = 1280, 720
GROUND = "#0A0D14"
BLUE = "#5B8CFF"
BLUE_LIGHT = "#8FB0FF"
WHITE = "#FFFFFF"
PANEL = "#131A28"
MARGIN = 48
TITLE_BOX = (MARGIN, 36, W - MARGIN, 262)       # the title sits inside this
TITLE_MAX_PX, TITLE_MIN_PX = 104, 56
CALLOUT_PX, CALLOUT_MAX_W = 30, 330
CAPS_EM = 0.68        # widest-case advance of a bold caps glyph, in em
LABEL_EM = 0.66
CENTRE = (W // 2, 480)
ICON_BOX = (CENTRE[0] - 150, CENTRE[1] - 150, CENTRE[0] + 150, CENTRE[1] + 150)
FONTS = Path(__file__).parent / "assets" / "fonts"
# anchors of the four callouts: (side the box grows away from, edge x, y centre)
_SLOTS = (("left", 330, 360), ("right", 950, 360), ("left", 330, 560), ("right", 950, 560))


@dataclass
class Layout:
    title_lines: list[str]
    title_px: int
    boxes: dict[str, tuple[int, int, int, int]] = field(default_factory=dict)
    callouts: list[str] = field(default_factory=list)


def _width(text: str, px: int, em: float) -> int:
    return int(len(text) * px * em)


def _wrap_two(text: str, px: int, max_w: int) -> list[str] | None:
    """The words on at most two lines no wider than max_w, or None. The split
    is the most even one that fits."""
    words = text.split()
    if not words:
        return [""]
    if _width(" ".join(words), px, CAPS_EM) <= max_w:
        return [" ".join(words)]
    best: tuple[int, list[str]] | None = None
    for i in range(1, len(words)):
        a, b = " ".join(words[:i]), " ".join(words[i:])
        w = max(_width(a, px, CAPS_EM), _width(b, px, CAPS_EM))
        if w <= max_w and (best is None or w < best[0]):
            best = (w, [a, b])
    return best[1] if best else None


def fit_title(title: str) -> tuple[list[str], int]:
    """Two lines of caps at the largest size that fits the title box; at the
    smallest size a too-long title is cut with an ellipsis, never overflows."""
    text = " ".join(title.upper().split()) or "ATLAS ACADEMY"
    max_w = TITLE_BOX[2] - TITLE_BOX[0]
    max_h = TITLE_BOX[3] - TITLE_BOX[1]
    for px in range(TITLE_MAX_PX, TITLE_MIN_PX - 1, -4):
        lines = _wrap_two(text, px, max_w)
        if lines is not None and len(lines) * int(px * 1.08) <= max_h:
            return lines, px
    words = text.split()
    while words:
        words.pop()
        lines = _wrap_two(" ".join(words) + "\u2026", TITLE_MIN_PX, max_w)
        if lines is not None:
            return lines, TITLE_MIN_PX
    return [text[:12] + "\u2026"], TITLE_MIN_PX


def _fit_label(text: str, max_w: int) -> str:
    text = " ".join(text.upper().split())
    if _width(text, CALLOUT_PX, LABEL_EM) <= max_w:
        return text
    while text and _width(text + "\u2026", CALLOUT_PX, LABEL_EM) > max_w:
        text = text[:-1]
    return text.rstrip() + "\u2026"


def _cube(cx: int, cy: int, s: int) -> str:
    dx, dy = int(s * 0.866), s // 2

    def poly(pts, fill):
        a = " ".join(f"{x},{y}" for x, y in pts)
        return (f'<polygon points="{a}" fill="{fill}" stroke="{BLUE_LIGHT}" '
                'stroke-width="3" stroke-linejoin="round"/>')
    return (poly([(cx - dx, cy - dy), (cx, cy), (cx, cy + s), (cx - dx, cy + dy)], "#2A4A99")
            + poly([(cx + dx, cy - dy), (cx, cy), (cx, cy + s), (cx + dx, cy + dy)], "#3B66D6")
            + poly([(cx, cy - s), (cx + dx, cy - dy), (cx, cy), (cx - dx, cy - dy)], BLUE))


def build_html(title: str, callouts: list[str]) -> tuple[str, Layout]:
    """The thumbnail as one self-contained HTML page, and what it lays out
    where. Pure Python: every lesson word is escaped."""
    lines, px = fit_title(title)
    layout = Layout(title_lines=lines, title_px=px)
    line_h = int(px * 1.08)
    y0 = TITLE_BOX[1] + (TITLE_BOX[3] - TITLE_BOX[1] - len(lines) * line_h) // 2
    title_html = "".join(f'<div class="t" style="top:{y0 + i * line_h}px;height:{line_h}px;'
                         f'line-height:{line_h}px;font-size:{px}px">{escape(ln)}</div>'
                         for i, ln in enumerate(lines))
    for i, ln in enumerate(lines):
        w = min(_width(ln, px, CAPS_EM), TITLE_BOX[2] - TITLE_BOX[0])
        x = (W - w) // 2
        layout.boxes[f"title{i}"] = (x, y0 + i * line_h, x + w, y0 + (i + 1) * line_h)
    cx, cy = CENTRE
    icon = "".join(_cube(cx, cy + off, 110) for off in (70, 0))
    parts = []
    for (side, edge_x, y), text in zip(_SLOTS, [c for c in callouts if c.strip()][:4],
                                       strict=False):
        text = _fit_label(text, CALLOUT_MAX_W - 40)
        pw, ph = _width(text, CALLOUT_PX, LABEL_EM) + 40, CALLOUT_PX + 28
        x0 = edge_x - pw if side == "left" else edge_x
        x0 = max(MARGIN, min(x0, W - MARGIN - pw))
        box = (x0, y - ph // 2, x0 + pw, y + ph // 2)
        near = box[2] if side == "left" else box[0]
        tx = cx + (-95 if side == "left" else 95)
        parts.append(f'<line x1="{near}" y1="{y}" x2="{tx}" y2="{y + 20}" stroke="{BLUE}" '
                     'stroke-width="3"/>')
        parts.append(f'<div class="c" style="left:{box[0]}px;top:{box[1]}px;width:{pw - 6}px;'
                     f'height:{ph - 6}px;line-height:{ph - 6}px">{escape(text)}</div>')
        layout.boxes[f"callout{len(layout.callouts)}"] = box
        layout.callouts.append(text)
    lines_svg = "".join(p for p in parts if p.startswith("<line"))
    divs = "".join(p for p in parts if p.startswith("<div"))
    font_url = (FONTS / "cabinet-800.woff2").as_uri()
    label_url = (FONTS / "atkinson-next-var.woff").as_uri()
    html = f"""<!doctype html><html><head><meta charset="utf-8"><style>
@font-face{{font-family:T;src:url("{font_url}")}}
@font-face{{font-family:L;src:url("{label_url}");font-weight:100 900}}
html,body{{margin:0;width:{W}px;height:{H}px;background:{GROUND};overflow:hidden}}
.wash{{position:absolute;left:{cx - 330}px;top:{cy - 230}px;width:660px;height:460px;
border-radius:50%;background:rgba(91,140,255,.18);filter:blur(80px)}}
svg{{position:absolute;left:0;top:0}}
.icon{{filter:drop-shadow(0 0 22px {BLUE}) drop-shadow(0 0 40px {BLUE})}}
.t{{position:absolute;left:0;width:{W}px;text-align:center;color:{WHITE};
font:800 {px}px T,Impact,sans-serif;white-space:nowrap}}
.c{{position:absolute;box-sizing:content-box;padding:0 17px;background:{PANEL};
border:3px solid {BLUE};border-radius:14px;color:{WHITE};font:700 {CALLOUT_PX}px L,sans-serif;
white-space:nowrap;overflow:hidden}}
</style></head><body><div class="wash"></div>
<svg width="{W}" height="{H}" viewBox="0 0 {W} {H}">{lines_svg}<g class="icon">{icon}</g></svg>
{title_html}{divs}</body></html>"""
    return html, layout


def inside(box: tuple[int, int, int, int]) -> bool:
    l_, t_, r_, b_ = box
    return l_ >= MARGIN - 1 and t_ >= 0 and r_ <= W - MARGIN + 1 and b_ <= H - 16


def render(title: str, callouts: list[str], out_png: str | Path) -> Layout:
    """Screenshot the page to `out_png` with Playwright Chrome."""
    import tempfile

    from playwright.sync_api import sync_playwright
    html, layout = build_html(title, callouts)
    out = Path(out_png)
    out.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as td:
        page_file = Path(td) / "thumb.html"
        page_file.write_text(html, encoding="utf-8")
        with sync_playwright() as pw:
            browser = pw.chromium.launch(channel="chrome")
            try:
                page = browser.new_page(viewport={"width": W, "height": H})
                page.goto(page_file.as_uri())
                page.evaluate("document.fonts.ready")
                page.screenshot(path=str(out), type="png")
            finally:
                browser.close()
    return layout


# ------------------------------------------------------------------ the words

def lesson_callouts(course_title: str, num: str, seconds: float | None) -> list[str]:
    out = [f"Lesson {num}", course_title, "AI voice, captions"]
    if seconds:
        out.insert(1, f"{max(1, round(seconds / 60))} min")
    return out[:4]


def product_callouts(inclusions: list[str]) -> list[str]:
    return [i for i in inclusions if i.strip()][:4]


def stage(name: str, title: str, callouts: list[str],
          staging: str | Path | None = None) -> Path:
    """Write `<name>.png` where the uploader looks (`lesson_youtube._thumb`).
    `name` is the staged stem, e.g. `lesson-adults-7.5`."""
    base = Path(staging) if staging else Path(
        os.getenv("ACADEMY_LESSONS_STAGING", "data/academy-staging/video"))
    out = base / f"{name}.png"
    render(title, callouts, out)
    return out
