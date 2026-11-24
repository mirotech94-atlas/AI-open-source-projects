"""The look of the ATLAS Academy tutorials: 1920x1080 slides and the thumbnail.

Design plan (frontend-design pass, 2026-10-09; the interactive pass and the
thumbnail redesign 2026-10-10), made for this brief rather than taken from a
template:

  Colour   (2026-10-17, Miro: the Atlas Academy video system,
           docs/academy-video-system; video_tokens.py) the frame is navy
           #101b30, never pure black; cards and plates are navy-raised
           #182540 on a navy-line #2a3a5c hairline; type is paper #f6f8ff and
           paper-muted #b4bdd6; blue #526de6 is the one accent ("where we are
           now": the rule, the ring, the wire being drawn, 24px+ labels) and
           blue-text #9db0f5 is blue for small text; amber, success and error
           are states, always with a word or glyph (the right quiz answer is
           success with its tick). The store keeps the 2026-10-12 black and
           royal blue. Every text/background pair is in TEXT_PAIRS at the px
           it is drawn, tested to WCAG (4.5:1 under 24px, 3:1 from 24px),
           alpha composited over the worst pixel the 3D can put under it.
  Surfaces business owners watch to learn a task, so the page changes with
           the job: explaining (title, bullets, steps, diagram, hook, payoff, pause,
           subscribe, playlists) is navy over the moving 3D; working (code,
           checklist, lab, quiz) is a navy-raised sheet ruled with the
           faint grid, laid on the bench, the 3D still turning in the strip
           to its right. The rail stays navy on both, so the watermark and
           the chapter ticks never move surface.
  Type     (2026-10-17) IBM Plex Sans and, for anything read as code, IBM
           Plex Mono, vendored (assets/fonts). The spec's scale only:
           promise 72, chapter 56, callout 36, support 28 (the floor on a
           16:9 frame), caption 44, code 32; nothing smaller.
  Layout   a workbench, left aligned. A narrow rail on the left carries the
           series name and one tick per chapter; a blue line on a raised
           track along the bottom edge fills as the lesson goes, so a
           viewer always knows how far through they are. Code sits on a navy
           card laid across the sheet, and the line being talked about is
           lit on a blue band while the rest steps back to muted.
  Interrupts (2026-10-10, "don't make it boring, make it interactive"): a
           quiz is a question with lettered answers, a "comment your answer"
           note and a 3-2-1 ring, then the right answer lit; a pause card is
           a big pause mark with the task; the subscribe beat is one button;
           the recap is a checklist that ticks itself.
  Motion   (2026-10-15, the Video Motion and Editing Guide and its sample;
           tutorial_motion.py, docs/academy-motion-guide.md) tutorials.py
           makes it, not the page: a step crossfades from the step before in
           seven frames (no wipe), and only what is new moves -- the heading,
           then the item being said, rising 16 px into place over nine frames
           with an eased stop (`motion_of` names them by selector). A workflow
           is traced once: a blue dot runs one connector along the track
           under the nodes and stops at the next node. Every slide is still
           one PNG; what enters is cut from the same page at its own size.
  Thumbnail what the top videos in the niche share (2026-10-10): two to four
           words, very large, one of them on a blue slab; one focal object
           on the right; navy field, high contrast, readable at phone size.
           Ours: the words on the left, the second line on the slab;
           the subject drawn on the right on a dotted canvas (the n8n editor's
           own texture) -- wired node tiles for n8n, a plug between Claude and
           its tools for MCP -- with a hand-drawn arrow at the result. No
           faces, no logos: the products are named in words only.

  3D (2026-10-10, Miro: "make the design for each thumbnail etc animated and
           3D and actually interesting"): the workbench at night. Every
           slide is now a transparent layer over a 3D loop of its series --
           a black, faintly reflective bench printed with the n8n dot grid,
           machined node tiles, cables with blue data pulses, a Claude
           hub with its tools orbiting, a dashboard, an agent core, a GPU box
           -- lit cool white from the right and light blue from behind; blue
           is the only thing that glows. The subject sits in the right third
           and a black scrim baked into the slide keeps the left legible. The
           opening (2026-10-17) is the result on flat navy, a 1 s drawn
           BrandSting at 6-7 s and the scope line; the ending is the flat
           navy EndCard, its right 640 px left empty for the native element.
           Thumbnails are a 3D still of the series with the words extruded.
           The 3D is rendered once on the Mac (tools/tutorial3d/build.py,
           three.js) and committed under assets/tutorial3d/; the one-CPU
           server only overlays and encodes.

No "Made with AI" label is drawn on a video frame (Miro, 2026-10-17: the
disclosure is YouTube's containsSyntheticMedia flag and the description
line); the store's address sits in the corner of every slide, and on the
end card only as a secondary line when the lesson has practice files. A
preview rendered without the Fish voice carries a red banner across
every frame (`preview=True`); tutorials.py also refuses to publish it.
"""
from __future__ import annotations

import base64
import functools
import hashlib
import html
import math
import re
from dataclasses import replace
from pathlib import Path

from . import logo
from . import tutorial_motion as motion
from . import video_tokens as vt
from .reel_design import ASSETS, TAG
from .tutorial_series import (
    INTRO_BYLINE,
    LAB_DIR,
    LAB_LABELS,
    LIST_KINDS,
    STORE_URL,
    UNDER_FIVE,
    Episode,
    NextUp,
    Scene,
    badge_of,
    lead_lower,
    plain,
)
from .video_tokens import fonts_css

W, H = 1920, 1080
TW, TH = 1280, 720
DESIGN_VERSION = "8"                 # 5: black + royal blue (Miro, 2026-10-12)
                                     # 6: demo crop boxes, 2026-10-13
                                     # 7: the motion guide, 2026-10-15
                                     # 8: the navy video system (2026-10-17) and
                                     #    the safe-area layout (2026-10-18), one
                                     #    combined bump with THUMB_VERSION,
                                     #    tutorial_short.LOOK_VERSION and
                                     #    tutorial_music.MUSIC_VERSION
STORE_SHOWN = STORE_URL.removeprefix("https://").split("/")[0]    # the host
STORE_FILES_SHOWN = STORE_URL.removeprefix("https://")             # host/academy

# ------------------------------------------------------------- the palette
# Miro, 2026-10-17: videos, thumbnails and Shorts use the Atlas Academy video
# system (docs/academy-video-system, video_tokens.py): a navy frame, never
# pure black; navy-raised cards on a navy-line hairline; paper and
# paper-muted type; blue the one accent, as text only at 24px and up
# (blue-text below that); amber, success and error for states, always with
# a word or glyph. The store keeps the 2026-10-12 black and royal blue.
NAVY = vt.NAVY                       # the frame
NAVY_RAISED = vt.NAVY_RAISED         # cards, plates, the working sheet
NAVY_LINE = vt.NAVY_LINE             # hairlines, tracks, inactive wires; never text
PAPER = vt.PAPER                     # type
PAPER_MUTED = vt.PAPER_MUTED         # secondary type, what was already said
BLUE = vt.BLUE                       # the accent: rules, rings, now, 24px+ labels
BLUE_TEXT = vt.BLUE_TEXT             # blue for small text, links, the second voice
BLUE_TINT = vt.BLUE_TINT             # the active card's fill
ON_BLUE = vt.ON_BLUE                 # type on a blue fill
AMBER, SUCCESS, ERROR = vt.AMBER, vt.SUCCESS, vt.ERROR
# tutorials.py lays a slide with no loop on design.INK.
INK = NAVY
PALETTE = tuple(vt.COLORS.values())
SANS = '"IBM Plex Sans"'             # the faces in video_tokens.fonts_css
MONO = '"IBM Plex Mono"'
LETTERS = "ABCDEFG"

# Slides that are worked through rather than listened to sit on a raised
# sheet with a faint grid; everything else is black over the 3D.
SHEET_KINDS = ("code", "checklist", "lab", "quiz")
SHEET_RIGHT = 1480                   # the sheet's right edge; the 3D turns beyond it
SCRIM_TEXT = 0.94                    # the scrim's alpha wherever free text may run
                                     # (blue clears 3:1 over the kit's worst there)
TEXT_RIGHT = 1416                    # free text on the scrim stops here (74% across)


def rgba(colour: str, alpha: float) -> str:
    """A palette colour at `alpha`, so every tint is a step of a token."""
    r, g, b = (int(colour[i:i + 2], 16) for i in (1, 3, 5))
    return f"rgba({r},{g},{b},{alpha:g})"


def _rgb(colour: str) -> tuple[float, float, float]:
    return tuple(int(colour[i:i + 2], 16) / 255 for i in (1, 3, 5))  # type: ignore[return-value]


def over(layers: tuple[tuple[str, float], ...], base: str) -> tuple[float, float, float]:
    """`layers` (colour, alpha), bottom first, composited over `base`, in
    sRGB as the browser and ffmpeg blend them."""
    out = _rgb(base)
    for colour, a in layers:
        c = _rgb(colour)
        out = tuple(a * x + (1 - a) * y for x, y in zip(c, out, strict=True))  # type: ignore[assignment]
    return out


def luminance(rgb: tuple[float, float, float]) -> float:
    def lin(c: float) -> float:
        return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4
    r, g, b = (lin(c) for c in rgb)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast(fg: tuple[float, float, float], bg: tuple[float, float, float]) -> float:
    """WCAG 2.x contrast ratio."""
    a, b = sorted((luminance(fg), luminance(bg)), reverse=True)
    return (a + 0.05) / (b + 0.05)


# The brightest thing the 3D kit can put behind a slide: its type and its
# blue glows (emissive, several times the blue), so the kit's worst is
# taken as the lightest colour of the palette. Kept there even though the
# kit's cards are dark now: never lowered to make a pair pass.
KIT_WORST = PAPER
SCRIM = ((NAVY, SCRIM_TEXT),)
SHEET: tuple[tuple[str, float], ...] = ((NAVY_RAISED, 1.0),)
WATERMARK_ALPHA = logo.WATERMARK_ALPHA  # baked into watermark.svg (logo.py)
Ink = str | tuple[str, float]
NON_TEXT = 0                         # px for a state or mark, not text: 3:1


def need(px: int) -> float:
    """The WCAG minimum for a pair (tokens.json's contrast notes): 4.5:1 for
    text under 24px, 3:1 at 24px and up and for a non-text state."""
    return 4.5 if 0 < px < 24 else 3.0


# (what, text colour [or (colour, alpha)], the layers under it, the base
# under those, the px it is drawn at [NON_TEXT for a state or a mark]).
TEXT_PAIRS: tuple[tuple[str, Ink, tuple[tuple[str, float], ...], str, int], ...] = (
    ("heading on the scrim", PAPER, SCRIM, KIT_WORST, vt.CHAPTER.size),
    ("title on the scrim", PAPER, SCRIM, KIT_WORST, vt.PROMISE.size),
    ("line being said", PAPER, SCRIM, KIT_WORST, vt.CAPTION.size),
    ("line already said", PAPER_MUTED, SCRIM, KIT_WORST, vt.CAPTION.size),
    ("sub line, secondary", PAPER_MUTED, SCRIM, KIT_WORST, vt.CALLOUT.size),
    ("step number now", BLUE, SCRIM, KIT_WORST, vt.CHAPTER.size),
    ("now marker on the scrim", BLUE, SCRIM, KIT_WORST, NON_TEXT),
    ("trace dot on its track", BLUE, SCRIM, KIT_WORST, NON_TEXT),
    ("node title", PAPER, ((NAVY_RAISED, 1.0),), NAVY, vt.CALLOUT.size),
    ("node note", PAPER_MUTED, ((NAVY_RAISED, 1.0),), NAVY, vt.SUPPORT.size),
    ("node now", PAPER, ((BLUE_TINT, 1.0),), NAVY, vt.CALLOUT.size),
    ("node now, note", PAPER_MUTED, ((BLUE_TINT, 1.0),), NAVY, vt.SUPPORT.size),
    ("node now edge", BLUE, ((NAVY, SCRIM_TEXT),), KIT_WORST, NON_TEXT),
    ("lit wire", BLUE, SCRIM, KIT_WORST, NON_TEXT),
    ("hook bubble", PAPER, ((NAVY_RAISED, 1.0),), NAVY, vt.CALLOUT.size),
    ("hook bubble where", PAPER_MUTED, ((NAVY_RAISED, 1.0),), NAVY, vt.SUPPORT.size),
    ("store address", BLUE_TEXT, ((NAVY_RAISED, 1.0),), NAVY, vt.SUPPORT.size),
    ("rail series name", PAPER_MUTED, ((NAVY, 1.0),), NAVY, vt.SUPPORT.size),
    ("rail tick now", BLUE, ((NAVY, 1.0),), NAVY, NON_TEXT),
    # the Grid A0 mark in its video colours (logo.watermark_svg(video=True)):
    # navy tile, the "A" and the words faded, the "0" in blue. A logotype
    # (WCAG exempts logos); held to 3:1 as a mark on the solid navy rail.
    ("watermark A on its tile", (PAPER, WATERMARK_ALPHA), ((NAVY, 1.0),), NAVY, NON_TEXT),
    ("watermark 0 on its tile", BLUE, ((NAVY, 1.0),), NAVY, NON_TEXT),
    ("progress fill on its track", BLUE, ((NAVY_RAISED, 1.0),), NAVY_RAISED, NON_TEXT),
    ("subscribe button", ON_BLUE, ((BLUE, 1.0),), NAVY, vt.PROMISE.size),
    ("pause resume", BLUE_TEXT, SCRIM, KIT_WORST, vt.CALLOUT.size),
    ("pause icon", BLUE, SCRIM, KIT_WORST, NON_TEXT),
    ("heading on the sheet", PAPER, SHEET, NAVY, vt.CHAPTER.size),
    ("line already said, sheet", PAPER_MUTED, SHEET, NAVY, vt.CAPTION.size),
    ("secondary, sheet", PAPER_MUTED, SHEET, NAVY, vt.CALLOUT.size),
    ("now bar on the sheet", BLUE, SHEET, NAVY, NON_TEXT),
    ("code on its card", PAPER, ((NAVY, 1.0),), NAVY_RAISED, vt.FLOOR_16_9),
    ("code stepped back", PAPER_MUTED, ((NAVY, 1.0),), NAVY_RAISED, vt.FLOOR_16_9),
    ("code line lit", ON_BLUE, ((BLUE, 1.0),), NAVY, vt.FLOOR_16_9),
    ("code caption", PAPER_MUTED, SHEET, NAVY, vt.CALLOUT.size),
    ("quiz answer", PAPER, SHEET, NAVY, vt.CAPTION.size),
    ("quiz letter", BLUE_TEXT, SHEET, NAVY, vt.CHAPTER.size),
    ("quiz answer right", ON_BLUE, ((BLUE, 1.0),), NAVY, vt.CAPTION.size),
    ("quiz answer wrong", PAPER_MUTED, SHEET, NAVY, vt.CAPTION.size),
    ("quiz comment", PAPER_MUTED, SHEET, NAVY, vt.CALLOUT.size),
    ("quiz comment lit", ON_BLUE, ((BLUE, 1.0),), NAVY, vt.CALLOUT.size),
    ("quiz ring number", BLUE, ((NAVY, 1.0),), NAVY_RAISED, 190),
    ("checklist box", PAPER_MUTED, SHEET, NAVY, NON_TEXT),
    ("checklist tick", BLUE, ((NAVY_RAISED, 1.0),), NAVY, NON_TEXT),
    ("lab file", PAPER, ((NAVY, 1.0),), NAVY_RAISED, vt.CALLOUT.size),
    ("lab file label", PAPER_MUTED, ((NAVY, 1.0),), NAVY_RAISED, vt.CALLOUT.size),
    ("lab step label", PAPER_MUTED, SHEET, NAVY, vt.SUPPORT.size),
    ("lab step label now", PAPER, SHEET, NAVY, vt.SUPPORT.size),
    ("playlist card", PAPER, ((NAVY_RAISED, 1.0),), NAVY, vt.CHAPTER.size),
    ("playlist card note", PAPER_MUTED, ((NAVY_RAISED, 1.0),), NAVY, vt.CALLOUT.size),
    ("playlist now note", PAPER_MUTED, ((BLUE_TINT, 1.0),), NAVY, vt.CALLOUT.size),
    ("live demo label", ON_BLUE, ((BLUE, 1.0),), NAVY, vt.SUPPORT.size),
    ("demo address", BLUE_TEXT, ((NAVY, 1.0),), NAVY_RAISED, vt.SUPPORT.size),
    ("demo caption", PAPER, ((NAVY, 1.0),), NAVY, vt.CALLOUT.size),
    ("demo step badge", ON_BLUE, ((BLUE, 1.0),), NAVY, vt.CALLOUT.size),
    ("demo store", BLUE_TEXT, ((NAVY, 1.0),), NAVY, vt.SUPPORT.size),
    ("intro today", BLUE_TEXT, SCRIM, KIT_WORST, vt.CALLOUT.size),
    ("intro clock badge", ON_BLUE, ((BLUE, 1.0),), NAVY, vt.CALLOUT.size),
    ("ending action", PAPER, ((NAVY_RAISED, 1.0),), NAVY, vt.CALLOUT.size),
    ("presenter plate name", PAPER, ((NAVY_RAISED, 1.0),), NAVY, vt.CHAPTER.size),
    ("presenter plate line", PAPER_MUTED, ((NAVY_RAISED, 1.0),), NAVY, vt.SUPPORT.size),
    ("presenter subscribe", ON_BLUE, ((BLUE, 1.0),), NAVY, vt.CALLOUT.size),
    ("preview banner", "#FFFFFF", (("#C62828", 1.0),), NAVY, vt.SUPPORT.size),
    # the teaching components (2026-10-18, batch 4): TEACH_CSS, TOKEN_CSS
    ("step map, step k of n", BLUE_TEXT, SCRIM, KIT_WORST, vt.SUPPORT.size),
    ("step map, step k of n, sheet", BLUE_TEXT, SHEET, NAVY, vt.SUPPORT.size),
    ("step map node", PAPER_MUTED, ((NAVY_RAISED, 1.0),), NAVY, vt.SUPPORT.size),
    ("step map node now", PAPER, ((BLUE_TINT, 1.0),), NAVY, vt.SUPPORT.size),
    ("step map node now edge", BLUE, ((NAVY, SCRIM_TEXT),), KIT_WORST, NON_TEXT),
    ("step map checked", SUCCESS, ((BLUE_TINT, 1.0),), NAVY, vt.SUPPORT.size),
    ("lower third name", PAPER, ((NAVY_RAISED, 1.0),), NAVY, vt.CALLOUT.size),
    ("lower third role", PAPER_MUTED, ((NAVY_RAISED, 1.0),), NAVY, vt.SUPPORT.size),
    ("checkpoint, you should now see", PAPER_MUTED, ((NAVY_RAISED, 1.0),), NAVY,
     vt.SUPPORT.size),
    ("checkpoint value", PAPER, ((NAVY_RAISED, 1.0),), NAVY, vt.CALLOUT.size),
    ("checkpoint checked", SUCCESS, ((NAVY_RAISED, 1.0),), NAVY, vt.SUPPORT.size),
    ("checkpoint checked edge", SUCCESS, ((NAVY, 1.0),), NAVY, NON_TEXT),
    ("error header", ERROR, ((NAVY_RAISED, 1.0),), NAVY, vt.CALLOUT.size),
    ("error text", PAPER, ((NAVY_RAISED, 1.0),), NAVY, vt.CODE.size),
    ("error edge", ERROR, SCRIM, KIT_WORST, NON_TEXT),
    ("recovery label", PAPER_MUTED, SCRIM, KIT_WORST, vt.SUPPORT.size),
    ("recovery row", PAPER_MUTED, SCRIM, KIT_WORST, vt.CALLOUT.size),
    ("recovery verified", SUCCESS, SCRIM, KIT_WORST, vt.SUPPORT.size),
    ("trace token", ON_BLUE, ((BLUE, 1.0),), NAVY, vt.VALUE_TAG.size),
    ("trace value", BLUE_TEXT, ((NAVY, 1.0),), NAVY_RAISED, vt.VALUE_TAG.size),
    ("node ran", SUCCESS, ((BLUE_TINT, 1.0),), NAVY, vt.SUPPORT.size),
    ("node ran edge", SUCCESS, ((NAVY, SCRIM_TEXT),), KIT_WORST, NON_TEXT),
)


def contrast_report() -> list[tuple[str, float, float]]:
    """(what, ratio, minimum) for every pair the slides draw."""
    rows = []
    for name, fg, layers, base, px in TEXT_PAIRS:
        least = need(px)
        bg = over(layers, base)
        colour, alpha = (fg, 1.0) if isinstance(fg, str) else fg
        bg_hex = "#" + "".join(f"{round(c * 255):02x}" for c in bg)
        ink = over(((colour, alpha),), bg_hex)
        rows.append((name, round(contrast(ink, bg), 2), least))
    return rows

# ------------------------------------------------------------- the 3D kit
# Pre-rendered on the Mac (tools/tutorial3d/build.py); see kit.json there.
KIT = ASSETS / "tutorial3d"
KIT_SERIES = ("channel", "claude", "n8n", "project", "business", "agents", "local",
              "explained", "security", "future")
# 2026-10-18: the AI series' scenes (tools/tutorial3d/build.py SERIES_AI)
# were listed in their own manifest until the combined bump folded them into
# kit.json, so the kit stamp changed once.
KIT_AI_SERIES = ("explained", "security", "future")
# A series whose scene is not named after it: "security" is already a topic
# loop (a padlock), so AI Security's own scene is the vault.
SERIES_SCENE = {"security": "vault"}
LOOP_SECONDS = 10.0                  # every loop is seamless at this length
# docs/academy-youtube-plan.md rule 17 (Miro, 2026-10-13): the sting is at
# most one second, so the payoff is on screen in the first seconds.
STING_SECONDS = 1.0
STING = KIT / "sting.mp4"            # the mark assembling, before the intro's words
INTRO_LOOP = KIT / "loop-mark.mp4"   # the mark idling, under the intro's words
OUTRO_LOOP = KIT / "loop-outro.mp4"  # the Subscribe button (the mid-lesson beat only)


# The watermark (Miro, 2026-10-11: "video with watermark as well"): the
# ATLAS Academy mark (Grid A0 over its wordmark since 2026-10-16, built by
# tools/academy_logo.py), semi-transparent (the alpha is in the PNG), at the top
# of the left rail on every teaching segment -- the one corner no slide,
# caption, quiz ring, progress line or live-demo bar uses -- inside safe-16-9
# (2026-10-17). The intro and the ending show the mark full size already, so
# they carry none. One file: logo.watermark_svg(), written and rasterised by
# tools/academy_logo.py. Since the combined bump (2026-10-18) it is the navy
# video mark, logo.watermark_svg(video=True); the store-coloured
# watermark.png stays on disk for the store, never laid on a video.
BRAND = ASSETS / "brand"
WATERMARK = BRAND / "watermark-video.png"
WATERMARK_SVG = BRAND / "watermark-video.svg"
WATERMARK_VIDEO, WATERMARK_VIDEO_SVG = WATERMARK, WATERMARK_SVG
WATERMARK_STORE = BRAND / "watermark.png"
WATERMARK_STORE_SVG = BRAND / "watermark.svg"
WATERMARK_XY = (vt.SAFE_16_9, vt.SAFE_16_9)  # the rail's column, inside the safe area


@functools.lru_cache(maxsize=1)
def look_stamp() -> str:
    """What else drew a render besides DESIGN_VERSION: the thumbnail system's
    version and the watermark file. In tutorials.script_key only, so a new
    mark or thumbnail look renders unpublished episodes again but never the
    cached intro and ending (they carry neither)."""
    from .tutorial_thumbs import THUMB_VERSION
    try:
        mark = hashlib.sha256(WATERMARK.read_bytes()).hexdigest()[:12]
    except OSError:
        mark = "no-mark"
    return f"thumbs{THUMB_VERSION}-{mark}"


def loop_path(series_key: str) -> Path:
    """The series' 3D loop: every slide of its episodes sits on it."""
    return KIT / f"loop-{SERIES_SCENE.get(series_key, series_key)}.mp4"


def still_path(series_key: str) -> Path:
    """The series' 3D still (1280x720): every thumbnail of the series."""
    return KIT / f"still-{SERIES_SCENE.get(series_key, series_key)}.jpg"


# Topic backdrops (Miro, 2026-10-11: "make the background keep changing and
# not stick to one ... change background depending on what you are talking
# about"). Every topic is one seamless 10 s loop of the kit; the series
# loops double as topics where they already show the thing.
TOPICS: dict[str, str] = {
    "welcome": "loop-channel.mp4",       # the playlists standing on the bench
    "workflow": "loop-n8n.mp4",          # a chain of nodes, data running the wires
    "mcp-hub": "loop-claude.mp4",        # Claude in the middle, tools orbiting
    "project": "loop-project.mp4",       # the hub wired into a node chain
    "money": "loop-business.mp4",        # a dashboard, bars rising
    "agent": "loop-agents.mp4",          # the faceted core on its rings
    "gpu": "loop-local.mp4",             # the GPU box, the core above it
    "subscribe": "loop-outro.mp4",       # the Subscribe button pressing itself
    "trigger": "loop-trigger.mp4",       # a lightning tile sending rings out
    "api": "loop-api.mp4",               # requests out to a server, answers back
    "message": "loop-message.mp4",       # chat bubbles stacking up
    "code": "loop-code.mp4",             # a code card, lines lighting in turn
    "memory": "loop-memory.mp4",         # cards filing into a stack
    "tools": "loop-tools.mp4",           # tool tiles plugging into a strip
    "server": "loop-server.mp4",         # a terminal monolith, lines scrolling
    "cloud": "loop-cloud.mp4",           # a cloud and a box, one cable between
    "security": "loop-security.mp4",     # a padlock closing, a shield
    "quiz": "loop-quiz.mp4",             # A, B, C tiles, one lighting up
    "recap": "loop-recap.mp4",           # a checklist ticking itself
    "layers": "loop-explained.mp4",      # a network of layers in nested glass boxes
    "vault": "loop-vault.mp4",           # a shield, a key turning in its lock
    "horizon": "loop-future.mp4",        # a path running out to a horizon of light
}
SERIES_TOPIC = {"channel": "welcome", "claude": "mcp-hub", "n8n": "workflow",
                "project": "project", "business": "money", "agents": "agent",
                "local": "gpu", "explained": "layers", "security": "vault",
                "future": "horizon"}
# Where a topic goes when the scene before already stood on it: the next
# best picture for the same words, so two scenes running never share a loop.
TOPIC_ALT: dict[str, tuple[str, ...]] = {
    "welcome": ("workflow", "mcp-hub"), "workflow": ("trigger", "api"),
    "mcp-hub": ("tools", "server"), "project": ("workflow", "mcp-hub"),
    "money": ("message", "recap"), "agent": ("tools", "memory"),
    "gpu": ("server", "cloud"), "subscribe": ("welcome",), "trigger": ("workflow", "api"),
    "api": ("cloud", "code"), "message": ("api", "workflow"), "code": ("server", "api"),
    "memory": ("agent", "code"), "tools": ("mcp-hub", "agent"), "server": ("code", "cloud"),
    "cloud": ("server", "gpu"), "security": ("server", "code"), "quiz": ("recap",),
    "recap": ("workflow", "mcp-hub"),
    "layers": ("agent", "memory"), "vault": ("security", "server"),
    "horizon": ("agent", "cloud"),
}
# Words -> topic, first match wins; read from the chapter, the heading and
# the items (never the narration: a passing mention must not move the 3D).
TOPIC_WORDS: tuple[tuple[str, str], ...] = (
    (r"\b(webhook\w*|trigger\w*)\b", "trigger"),
    (r"\b(slack|e-?mails?|messages?|chat\w*|repl\w+)\b", "message"),
    (r"\b(safe|safety|secur\w*|credential\w*|password\w*|tokens?|keys?|logins?|approv\w*)\b",
     "security"),
    (r"\b(cloud|local|remote)\b", "cloud"),
    (r"\b(http|api|request\w*|fetch)\b", "api"),
    (r"\b(json|code|python|expression\w*|server\.py)\b", "code"),
    (r"\b(mcp|hosts?|clients?|protocol)\b", "mcp-hub"),
    (r"\b(memory|remember\w*|notes)\b", "memory"),
    (r"\b(tools?|plugins?|skills?|connectors?|resources|prompts)\b", "tools"),
    (r"\b(terminal|install\w*|docker|command\w*|uv|stdio|self-host\w*)\b", "server"),
    (r"\b(gpu|dgx|nvidia|hermes)\b", "gpu"),
    (r"\b(agents?|brain|loop)\b", "agent"),
    (r"\b(money|business|leads?|customers?|invoice\w*|shop|sales?)\b", "money"),
    (r"\b(welcome|playlists?|channel|series)\b", "welcome"),
    (r"\b(workflows?|nodes?|n8n|automation|editor|canvas|executions?)\b", "workflow"),
)
KIND_TOPIC = {"quiz": "quiz", "checklist": "recap", "subscribe": "subscribe",
              "playlists": "welcome", "lab": "code"}


def topic_path(topic: str) -> Path:
    return KIT / TOPICS[topic]


def scene_topic(scene: Scene, series_key: str) -> str:
    """The topic a scene stands on before the no-repeat pass: its explicit
    `backdrop`, else its kind's own picture, else the first topic its words
    name, else the series' own."""
    if scene.backdrop:
        return scene.backdrop
    if scene.kind in KIND_TOPIC:
        return KIND_TOPIC[scene.kind]
    text = " ".join((scene.chapter, scene.heading, *scene.items)).lower()
    for pattern, topic in TOPIC_WORDS:
        if re.search(pattern, text):
            return topic
    return SERIES_TOPIC.get(series_key, "workflow")


def backdrops(scenes: tuple[Scene, ...] | list[Scene], series_key: str,
              before: str = "") -> list[str]:
    """One topic per scene. A derived topic that repeats the scene before's
    moves to its alternate; an explicit `backdrop` is the author's and stays.
    A demo scene's picture is its recording ("demo")."""
    out: list[str] = []
    last = before
    for sc in scenes:
        if sc.kind == "demo":
            out.append("demo")
            last = "demo"
            continue
        topic = scene_topic(sc, series_key)
        if topic == last and not sc.backdrop:
            topic = next((t for t in TOPIC_ALT.get(topic, ()) if t != last), topic)
        out.append(topic)
        last = topic
    return out


def kit_files() -> list[Path]:
    return ([STING, INTRO_LOOP, OUTRO_LOOP] + [loop_path(k) for k in KIT_SERIES]
            + [still_path(k) for k in KIT_SERIES]
            + sorted({topic_path(t) for t in TOPICS}))


def kit_missing() -> str | None:
    """None when every file of the 3D kit is on disk; otherwise which are not."""
    gone = [p.name for p in [*kit_files(), WATERMARK] if not p.is_file()]
    return f"the 3D kit is missing {', '.join(gone)} (tools/tutorial3d/build.py)" if gone else None


@functools.lru_cache(maxsize=1)
def kit_stamp() -> str:
    """Which kit drew a render (kit.json's hash): a new kit renders the
    cached intro and ending again."""
    try:
        return hashlib.sha256((KIT / "kit.json").read_bytes()).hexdigest()[:12]
    except OSError:
        return "no-kit"


@functools.lru_cache(maxsize=16)
def still_uri(series_key: str) -> str:
    p = still_path(series_key)
    if not p.is_file():
        return ""
    return "data:image/jpeg;base64," + base64.b64encode(p.read_bytes()).decode()

# The type scale (video_tokens): every size on a 16:9 frame is one of these,
# none under SUPPORT (28px).
F_PROMISE, F_CHAPTER, F_CALLOUT = vt.PROMISE.css(), vt.CHAPTER.css(), vt.CALLOUT.css()
F_SUPPORT, F_CAPTION, F_CODE = vt.SUPPORT.css(), vt.CAPTION.css(), vt.CODE.css()
SAFE = vt.SAFE_16_9                  # 96: nothing readable outside it
RAIL_W = SAFE + 104                  # the rail's column runs SAFE..RAIL_W (the mark's width)
MAIN_LEFT = 236
CARD_PAD_X = 40                      # a code card's side padding
CAP_W = SHEET_RIGHT - 300            # a code caption's measure

BASE = f"""
*{{box-sizing:border-box;margin:0;padding:0}}
html,body{{width:{W}px;height:{H}px;overflow:hidden;background:{NAVY};color:{PAPER};
font-family:{SANS},system-ui,sans-serif;-webkit-font-smoothing:antialiased}}
.store{{position:absolute;right:{SAFE}px;bottom:{SAFE}px;{F_SUPPORT};color:{BLUE_TEXT};
padding:{vt.STEP_1}px {vt.STEP_2}px;border-radius:{vt.RADIUS_CHIP}px;background:{NAVY_RAISED};
border:2px solid {NAVY_LINE}}}
.prog{{position:absolute;left:0;right:0;bottom:0;height:8px;background:{NAVY_RAISED}}}
.prog i{{display:block;height:100%;background:{BLUE}}}
.rail{{position:absolute;left:0;top:0;bottom:0;width:{RAIL_W}px;background:{NAVY};
border-right:2px solid {NAVY_LINE};display:flex;flex-direction:column;align-items:center;
padding:{SAFE + 64 + vt.STEP_4}px 0 {SAFE}px {SAFE}px}}
.mark{{writing-mode:vertical-rl;transform:rotate(180deg);{F_SUPPORT};color:{PAPER_MUTED};flex:1}}
.ticks{{display:flex;flex-direction:column;gap:12px;margin-top:{vt.STEP_4}px}}
.tick{{width:30px;height:6px;border-radius:3px;background:{NAVY_LINE}}}
.tick.done{{background:{PAPER_MUTED}}}
.tick.now{{background:{BLUE}}}
.main{{position:absolute;left:{MAIN_LEFT}px;right:150px;top:150px;bottom:170px}}
h1{{{F_CHAPTER};color:{PAPER};margin-bottom:{vt.STEP_6}px;max-width:{TEXT_RIGHT - MAIN_LEFT}px}}
h1.small{{margin-bottom:{vt.STEP_4}px}}
.list{{list-style:none;display:flex;flex-direction:column;gap:{vt.STEP_4}px}}
.list li{{{F_CAPTION};color:{PAPER_MUTED};padding-left:40px;border-left:6px solid transparent;
max-width:{TEXT_RIGHT - MAIN_LEFT}px}}
.list li.now{{color:{PAPER};border-left-color:{BLUE}}}
.list li.hidden{{visibility:hidden}}
.num li{{display:grid;grid-template-columns:96px 1fr;padding-left:0;border-left:0}}
.num li b{{{F_CHAPTER};color:transparent;-webkit-text-stroke:2px {PAPER_MUTED}}}
.num li.now b{{-webkit-text-stroke:0;color:{BLUE}}}
.check li{{display:grid;grid-template-columns:96px 1fr;align-items:center;padding-left:0;border-left:0}}
.box{{width:58px;height:58px;border-radius:{vt.RADIUS_CHIP}px;border:4px solid {PAPER_MUTED}}}
.check li.done .box,.check li.now .box{{border-color:{BLUE};background:{NAVY_RAISED}}}
.box svg{{width:50px;height:50px}}
.title h1{{{F_PROMISE};margin-top:120px}}
.title .sub{{{F_CALLOUT};color:{PAPER_MUTED};max-width:{TEXT_RIGHT - MAIN_LEFT}px}}
.flow{{display:flex;align-items:center;margin-top:60px;position:relative}}
.flow::after{{content:"";position:absolute;left:200px;right:200px;top:calc(100% + 26px);
height:2px;border-radius:1px;background:{NAVY_LINE}}}
.flow.four::after{{left:155px;right:155px}}
.dot{{position:absolute;left:6px;top:6px;width:24px;height:24px;border-radius:50%;
background:{BLUE};box-shadow:0 0 0 4px {rgba(BLUE, .25)}}}
.node{{width:400px;min-height:240px;border-radius:{vt.RADIUS_CARD}px;border:2px dashed {NAVY_LINE};
padding:{vt.STEP_3}px {vt.STEP_4}px;display:flex;flex-direction:column;justify-content:center}}
.node.on{{border:2px solid {NAVY_LINE};background:{NAVY_RAISED}}}
.node.now{{border-color:{BLUE};background:{BLUE_TINT}}}
.node .n1{{{F_CALLOUT};color:{PAPER}}}
.node .n2{{{F_SUPPORT};color:{PAPER_MUTED};margin-top:{vt.STEP_1}px}}
.node:not(.on) *{{visibility:hidden}}
.flow.four .node{{width:310px;padding:{vt.STEP_3}px}}
.flow.four .wire{{width:90px}}
.wire{{width:130px;height:240px;flex:none}}
.card{{background:{NAVY};color:{PAPER};border-radius:{vt.RADIUS_CARD}px;
padding:{vt.STEP_3}px {CARD_PAD_X}px;border:2px solid {NAVY_LINE}}}
.main:has(> .card){{right:{SAFE}px}}
.card pre{{font-family:{MONO},monospace;white-space:pre;color:{PAPER}}}
.card pre .ln{{display:block;border-radius:6px;padding:0 10px;margin:0 -10px}}
.card pre .ln.dim{{color:{PAPER_MUTED}}}
.card pre .ln.lit{{background:{BLUE};color:{ON_BLUE}}}
h1.code{{margin-bottom:{vt.STEP_3}px}}
.cap{{margin-top:{vt.STEP_3}px;{F_CALLOUT};color:{PAPER_MUTED};max-width:{CAP_W}px}}
.bubble{{margin-top:{vt.STEP_8}px;max-width:1180px;background:{NAVY_RAISED};color:{PAPER};
border-radius:{vt.RADIUS_CARD}px;border:2px solid {NAVY_LINE};border-left:8px solid {BLUE};
padding:{vt.STEP_3}px {vt.STEP_6}px}}
.bubble .where{{{F_SUPPORT};color:{PAPER_MUTED};margin-bottom:{vt.STEP_1}px}}
.bubble p{{{F_CALLOUT}}}
.bubble.hidden{{visibility:hidden}}
.opts{{list-style:none;display:flex;flex-direction:column;gap:{vt.STEP_3}px;max-width:1080px}}
.opts li{{display:grid;grid-template-columns:92px 1fr;align-items:center;{F_CAPTION};
padding:{vt.STEP_2}px {vt.STEP_3}px;border-radius:{vt.RADIUS_CARD}px;border:2px solid {NAVY_LINE};
color:{PAPER};background:{NAVY_RAISED}}}
.opts li b{{{F_CHAPTER};color:{BLUE_TEXT}}}
.opts li.right{{background:{BLUE};border-color:{BLUE};color:{ON_BLUE}}}
.opts li.right b{{color:{ON_BLUE}}}
.opts li.wrong,.opts li.wrong b{{color:{PAPER_MUTED}}}
.opts li.wrong{{border-style:dashed}}
.say{{display:inline-flex;align-items:center;gap:{vt.STEP_2}px;margin-top:40px;{F_CALLOUT};
color:{PAPER_MUTED};padding:12px 18px;margin-left:-18px;border-radius:{vt.RADIUS_CHIP}px}}
.say svg{{width:44px;height:44px}}
.say.lit{{color:{ON_BLUE};background:{BLUE}}}
.ring{{position:absolute;right:40px;top:260px;width:360px;height:360px;border-radius:50%;
background:{NAVY_RAISED}}}
.ring b{{position:absolute;inset:0;display:flex;align-items:center;justify-content:center;
font:600 190px/1 {SANS};color:{BLUE}}}
.answer{{margin-top:36px;{F_CALLOUT};color:{PAPER};max-width:1100px}}
.paused{{display:flex;align-items:center;gap:48px;margin-bottom:{vt.STEP_6}px}}
.paused svg{{width:150px;height:150px;flex:none}}
.paused h1{{margin:0}}
.resume{{display:inline-flex;align-items:center;gap:18px;margin-top:56px;{F_CALLOUT};
color:{BLUE_TEXT}}}
.resume svg{{width:46px;height:46px}}
.lab li{{display:grid;grid-template-columns:220px 1fr;align-items:baseline;padding-left:0;
border-left:0}}
.lab li b{{{F_SUPPORT};font-weight:600;color:{PAPER_MUTED}}}
.lab li.now b{{color:{PAPER};text-decoration:underline 6px {BLUE};text-underline-offset:12px}}
.dl{{display:inline-flex;align-items:center;gap:18px;margin-top:{vt.STEP_6}px;
padding:{vt.STEP_2}px {vt.STEP_3}px;border-radius:{vt.RADIUS_CARD}px;background:{NAVY};
border:2px solid {NAVY_LINE};color:{PAPER};{F_CALLOUT}}}
.dl em{{font-style:normal;color:{PAPER_MUTED}}}
.subbtn{{display:inline-flex;align-items:center;gap:26px;margin-top:20px;padding:34px 52px;
border-radius:{vt.RADIUS_CARD}px;background:{BLUE};color:{ON_BLUE};{F_PROMISE}}}
.subbtn svg{{width:72px;height:72px}}
.subline{{margin-top:44px;{F_CALLOUT};color:{PAPER_MUTED};max-width:{TEXT_RIGHT - MAIN_LEFT}px}}
.lists{{display:flex;gap:{vt.STEP_6}px}}
.pl{{width:690px;border-radius:{vt.RADIUS_CARD}px;border:2px dashed {NAVY_LINE};padding:44px}}
.pl.on{{border:2px solid {NAVY_LINE};background:{NAVY_RAISED}}}
.pl.now{{border-color:{BLUE};background:{BLUE_TINT}}}
.pl:not(.on) *{{visibility:hidden}}
.pl .p1{{{F_CHAPTER}}}
.pl .p2{{margin-top:22px;{F_CALLOUT};color:{PAPER_MUTED}}}
.pl svg{{width:76px;height:76px;margin-bottom:26px}}
.preview{{position:absolute;left:0;right:0;top:0;height:64px;background:#c62828;color:#fff;
font:600 28px/64px {SANS};text-align:center;z-index:20}}
"""

# The working sheet (SHEET_KINDS): a navy-raised surface laid on the bench
# from the rail to SHEET_RIGHT, ruled with a faint grid (the technical
# layer), a navy-line hairline at its edge, the 3D still turning in the
# strip beyond it. The rail is solid navy, so the watermark and the ticks
# never change surface.
GRID = (f"background-image:linear-gradient({rgba(PAPER, .035)} 1px,transparent 1px),"
        f"linear-gradient(90deg,{rgba(PAPER, .035)} 1px,transparent 1px);"
        "background-size:48px 48px")
SHEET_CSS = f"""
div.sheet{{position:absolute;left:{RAIL_W}px;top:0;bottom:8px;width:{SHEET_RIGHT - RAIL_W}px;
background-color:{NAVY_RAISED};{GRID};border-right:2px solid {NAVY_LINE}}}
body.sheet .rail{{background:{NAVY};border-right:0}}
body.sheet .check li.done span,body.sheet .check li.now span{{color:{PAPER}}}
body.sheet .opts li{{background:transparent}}
body.sheet .opts li.right{{background:{BLUE}}}
body.sheet .ring{{background:{NAVY}}}
"""

ICON_CHAT = ('<svg viewBox="0 0 48 48"><path d="M8 10h32a4 4 0 0 1 4 4v16a4 4 0 0 1-4 4H22l-10 8v-8H8'
             'a4 4 0 0 1-4-4V14a4 4 0 0 1 4-4z" fill="none" stroke="currentColor" '
             'stroke-width="4" stroke-linejoin="round"/></svg>')
ICON_PLAY = ('<svg viewBox="0 0 48 48"><path d="M14 8l26 16-26 16z" fill="currentColor"/></svg>')
ICON_BELL = (f'<svg viewBox="0 0 48 48"><path d="M24 6a12 12 0 0 1 12 12v9l5 7H7l5-7v-9A12 12 0 0 1 24 6z'
             f'" fill="{ON_BLUE}"/><circle cx="24" cy="40" r="5" fill="{ON_BLUE}"/></svg>')
# Miro, 2026-10-18: a recap tick and the right quiz answer are the neutral
# blue; `success` green is only for a step marked verified.
ICON_TICK = (f'<svg viewBox="0 0 50 50"><path d="M12 26l9 9 17-19" fill="none" stroke="{BLUE}" '
             f'stroke-width="7" stroke-linecap="round" stroke-linejoin="round"/></svg>')


def _pause_icon(lit: bool) -> str:
    fill = BLUE if lit else "none"
    return (f'<svg viewBox="0 0 150 150"><circle cx="75" cy="75" r="68" fill="{fill}" '
            f'stroke="{BLUE}" stroke-width="8"/><rect x="50" y="42" width="16" height="66" rx="5" '
            f'fill="{ON_BLUE if lit else BLUE}"/><rect x="84" y="42" width="16" height="66" rx="5" '
            f'fill="{ON_BLUE if lit else BLUE}"/></svg>')


# A slide is a transparent layer over its series' 3D loop. The navy scrim
# is part of the layer: SCRIM_TEXT wherever free text may run (to
# TEXT_RIGHT), falling away on the right where the 3D subject stands; cards
# that may sit over the subject are opaque navy-raised.
_SCRIM_STOP = round(TEXT_RIGHT / W * 100)
LAYER_CSS = f"""
html,body{{background:transparent}}
.scrim{{position:absolute;inset:0;background:linear-gradient(90deg,{rgba(NAVY, .97)} 0,
{rgba(NAVY, SCRIM_TEXT)} {_SCRIM_STOP}%,{rgba(NAVY, .55)} 87%,{rgba(NAVY, .25)} 100%)}}
.node:not(.on),.pl:not(.on){{background:{rgba(NAVY, .6)}}}
"""


def surface(kind: str) -> str:
    """"sheet" (the raised, gridded working sheet) or "scrim" (black over
    the 3D)."""
    return "sheet" if kind in SHEET_KINDS else "scrim"


def _doc(body: str, *, w: int = W, h: int = H, css: str = "", preview: bool = False,
         layer: bool = True, sheet: bool = False) -> str:
    """A page to screenshot. `layer` (every frame of the video): transparent,
    with the scrim, to be laid over the 3D loop; the thumbnail is opaque.
    `sheet`: the raised working sheet instead of the scrim."""
    banner = ('<div class="preview">Preview: placeholder audio, not the Fish voice. '
              'Never uploaded.</div>') if preview else ""
    size = f"html,body{{width:{w}px;height:{h}px}}"
    scrim = ('<div class="sheet"></div>' if sheet else '<div class="scrim"></div>') if layer else ""
    extra = (LAYER_CSS + SHEET_CSS) if layer else ""
    cls = ' class="sheet"' if sheet and layer else ""
    return (f'<!doctype html><html><head><meta charset="utf-8"><style>{fonts_css()}{BASE}'
            f'{size}{extra}{css}</style></head><body{cls}>{scrim}{banner}{body}</body></html>')


def sting_layer(preview: bool = False) -> str:
    """What sits over the 3D sting: only a preview's banner. Miro,
    2026-10-17: no on-screen "Made with AI" label on video frames; the
    disclosure is YouTube's containsSyntheticMedia flag and the
    description line."""
    banner = ('<div class="preview">Preview: placeholder audio, not the Fish voice. '
              'Never uploaded.</div>') if preview else ""
    size = f"html,body{{width:{W}px;height:{H}px}}"
    return (f'<!doctype html><html><head><meta charset="utf-8"><style>{fonts_css()}{BASE}'
            f'{size}{LAYER_CSS}</style></head><body>{banner}</body></html>')


def _rail(mark: str, chapter: int, chapters: int) -> str:
    ticks = "".join(
        f'<div class="tick {"now" if i == chapter else "done" if i < chapter else ""}"></div>'
        for i in range(chapters))
    return (f'<div class="rail"><div class="mark">{html.escape(mark)}</div>'
            f'<div class="ticks">{ticks}</div></div>')


def _chrome(mark: str, chapter: int, chapters: int, progress: float) -> str:
    pct = max(0.0, min(1.0, progress)) * 100
    return (_rail(mark, chapter, chapters)
            + f'<div class="store">{STORE_SHOWN}</div>'
            f'<div class="prog"><i style="width:{pct:.1f}%"></i></div>')


def _wire(lit: bool) -> str:
    colour = BLUE if lit else rgba(PAPER, .18)
    return (f'<svg class="wire" viewBox="0 0 130 240"><path d="M8 120 H108" stroke="{colour}" '
            f'stroke-width="6" stroke-linecap="round"/><path d="M96 104 L116 120 L96 136" '
            f'fill="none" stroke="{colour}" stroke-width="6" stroke-linecap="round" '
            f'stroke-linejoin="round"/></svg>')


CODE_LINE = vt.CODE.line / vt.CODE.size     # 44/32
# The card's inside, width x height: the card runs from MAIN_LEFT to the
# safe right edge (2026-10-18), so a 28 px line of up to 89 characters (n8n
# #1's workflow JSON) stays inside it.
CODE_BOX = (W - SAFE - MAIN_LEFT - 2 * (CARD_PAD_X + 2), 600)
MONO_ADVANCE = 0.6                           # Plex Mono's advance, in em
# 2026-10-18 (the safe-area gate): the card's lines are budgeted from the
# safe bottom (H - SAFE) down from .main's top, after the heading
# (h1.code), the card's padding and border and the caption, each estimated
# on the long side so the estimate never under-counts a wrapped line.
MAIN_TOP = 150                               # .main's top
CARD_CHROME = 2 * (vt.STEP_3 + 2)            # the card's padding and border, top and bottom
HEAD_CHARS = 34                              # a chapter-size heading this long is one line
CAP_CHARS = 54                               # caption characters a callout line holds


def code_room(scene: Scene) -> float:
    """The px a code scene's lines may fill: what is left of the safe area
    under the heading and above the caption."""
    head = vt.CHAPTER.line * (1 if len(scene.heading) <= HEAD_CHARS else 2)
    cap = (vt.STEP_3 + vt.CALLOUT.line * math.ceil(len(scene.caption) / CAP_CHARS)
           if scene.caption else 0)
    return H - SAFE - MAIN_TOP - head - vt.STEP_3 - CARD_CHROME - cap


def code_fit(code: str, room: float = CODE_BOX[1]) -> int:
    """The largest size up to `code` (32) at which `code` fits the card,
    `room` px tall."""
    lines = code.split("\n")
    longest = max((len(x) for x in lines), default=1)
    by_height = int(room / (max(1, len(lines)) * CODE_LINE))
    by_width = int(CODE_BOX[0] / (max(1, longest) * MONO_ADVANCE))
    return min(vt.CODE.size, by_height, by_width)


def _code_size(code: str, room: float = CODE_BOX[1]) -> int:
    """`code` (32) where it fits, never under the 16:9 floor (28)."""
    return max(vt.FLOOR_16_9, code_fit(code, room))


def code_window(scene: Scene, step: int) -> tuple[int, int]:
    """The lines [a, b) of `scene.code` the card shows at `step`: all of
    them when they fit the room at the floor; otherwise as many as fit,
    starting at the step's lit lines (moved up only to stay full at the
    end), so the type is never shrunk under 28 px and nothing runs past the
    safe area."""
    n = len(scene.code.split("\n"))
    room = code_room(scene)
    rows = max(1, int(room // (_code_size(scene.code, room) * CODE_LINE)))
    if n <= rows:
        return 0, n
    lit = scene.marks[step] if scene.marks and step < len(scene.marks) else (0, 0)
    a = max(0, min(lit[0], n - rows))
    return a, a + rows


def code_focus(scene: Scene, step: int) -> float:
    """Where on the frame (0 top .. 1 bottom) step `step`'s lit lines sit,
    for the renderer's slow push-in. 0.5 when nothing is lit."""
    if not scene.marks or step >= len(scene.marks):
        return 0.5
    a, b = scene.marks[step]
    n = max(1, len(scene.code.split("\n")))
    # The card starts about a third of the way down the frame.
    return round(0.33 + 0.5 * ((a + b) / 2) / n, 3)


def _flow(items: tuple[str, ...], lit_upto: int, now: int, *,
          io: tuple[str, ...] = (), ran: int = -1) -> str:
    """The wired nodes. WorkflowTrace (2026-10-17): `io[i]`, the real value
    seen at node i, as a value-tag under its note once the node is on; `ran`,
    the node the trace stops at, bordered `success` with "Ran" only when the
    script marks that step verified (the caller passes -1 otherwise)."""
    parts = []
    for i, item in enumerate(items):
        if i:
            parts.append(_wire(i <= lit_upto))
        first, _, rest = item.partition("\n")
        cls = ("node" + (" on" if i <= lit_upto else "") + (" now" if i == now else "")
               + (" nd-ok" if i == ran else ""))
        value = (f'<span class="io">{html.escape(io[i])}</span>'
                 if i < len(io) and io[i] else "")
        state = (f'<span class="nd-st">{GLYPH_CHECK}{RAN_WORD}</span>' if i == ran else "")
        parts.append(f'<div class="{cls}"><div class="n1">{html.escape(first)}</div>'
                     f'<div class="n2">{html.escape(rest)}</div>{value}{state}</div>')
    four = " four" if len(items) > 3 else ""
    return f'<div class="flow{four}">{"".join(parts)}</div>'


def _code(scene: Scene, step: int) -> str:
    size = _code_size(scene.code, code_room(scene))
    lit = scene.marks[step] if scene.marks and step < len(scene.marks) else None
    a, b = code_window(scene, step)
    rows = []
    for i, line in enumerate(scene.code.split("\n")[a:b], start=a):
        cls = "" if lit is None else ("lit" if lit[0] <= i < lit[1] else "dim")
        rows.append(f'<span class="ln {cls}">{html.escape(line) or " "}</span>')
    cap = f'<p class="cap">{html.escape(scene.caption)}</p>' if scene.caption else ""
    return (f'<h1 class="small code">{html.escape(scene.heading)}</h1>'
            f'<div class="card"><pre style="font-size:{size}px;line-height:{CODE_LINE:g}">'
            f'{"".join(rows)}</pre></div>{cap}')


def _ring(n: int) -> str:
    frac = n / 3
    c = 2 * 3.14159 * 160
    return (f'<div class="ring"><svg viewBox="0 0 360 360"><circle cx="180" cy="180" r="160" '
            f'fill="none" stroke="{rgba(PAPER, .14)}" stroke-width="22"/>'
            f'<circle cx="180" cy="180" r="160" fill="none" stroke="{BLUE}" stroke-width="22" '
            f'stroke-linecap="round" stroke-dasharray="{c * frac:.1f} {c:.1f}" '
            f'transform="rotate(-90 180 180)"/></svg><b>{n}</b></div>')


def _quiz(scene: Scene, step: int, countdown: int | None) -> str:
    reveal = step >= 1 and countdown is None
    poll = scene.answer < 0
    rows = []
    for i, item in enumerate(scene.items):
        cls = ("right" if i == scene.answer else "wrong") if reveal and not poll else ""
        mark = "✓" if cls == "right" else LETTERS[i]
        rows.append(f'<li class="{cls}"><b>{mark}</b><span>{html.escape(item)}</span></li>')
    say = ("Comment A or B" if poll and len(scene.items) == 2 else "Comment your answer")
    if reveal and not poll:
        # Miro, 2026-10-13 / rule 19: the tick never shares a frame with the
        # comment prompt -- once the answer is shown, the ask turns into an invitation.
        say = "Did you get it? Tell me in the comments"
    lit = " lit" if (poll and reveal) or countdown is not None else ""
    head = ("Quick question" if poll else "Quick quiz")
    inner = (f'<h1 class="small">{head}: {html.escape(scene.heading)}</h1>'
             f'<ul class="opts">{"".join(rows)}</ul>'
             f'<div class="say{lit}">{ICON_CHAT}<span>{say}</span></div>')
    if reveal and scene.caption and not poll:
        inner += f'<p class="answer">{html.escape(scene.caption)}</p>'
    if countdown is not None:
        inner += _ring(countdown)
    return inner


def slide(scene: Scene, step: int, *, mark: str, chapter: int, chapters: int,
          preview: bool = False, progress: float = 0.0, countdown: int | None = None,
          hold: bool = False, steps_map: tuple[str, ...] = (), check: int = -1) -> str:
    """One build step of one scene, as an HTML document. `countdown` draws a
    quiz's silent 3-2-1 frame; `hold` a pause card's silent wait. The
    teaching components (`overlays`): `steps_map` the episode's StepMap,
    `check` the index into scene.checks of a checkpoint beat (-1 none)."""
    extra = overlays(scene, step, steps_map=steps_map, check=check,
                     still=countdown is not None or hold)
    if scene.kind == "demo":
        from .tutorial_demos import address
        return demo_slide(scene, step, address=address(scene.demo, step), mark=mark,
                          chapter=chapter, chapters=chapters, preview=preview, progress=progress,
                          extra=extra)
    head = f"<h1>{html.escape(scene.heading)}</h1>"
    if scene.error is not None:
        inner = error_card(scene, step)
    elif scene.kind == "title":
        sub = html.escape(plain(scene.said[0]).split(". ")[0].rstrip(".") + ".")
        inner = f'<div class="title">{head}<p class="sub">{sub}</p></div>'
    elif scene.kind == "code":
        inner = _code(scene, step)
    elif scene.kind == "diagram":
        ran = step if scene.verified and step > 0 else -1
        inner = f'{head}{_flow(scene.items, step, step, io=scene.io, ran=ran)}'
    elif scene.kind == "hook":
        lines = "".join(f"<p>{html.escape(x)}</p>" for x in scene.code.split("\n"))
        hidden = "" if step >= 1 else " hidden"
        where = f'<div class="where">{html.escape(scene.caption)}</div>' if scene.caption else ""
        inner = (f'{head}{_flow(scene.items, len(scene.items), -1)}'
                 f'<div class="bubble{hidden}">{where}{lines}</div>')
    elif scene.kind == "payoff":
        # Rule 17: the hook's picture with the result already in the bubble
        # from step 0, under the smaller heading -- the finished thing first.
        lines = "".join(f"<p>{html.escape(x)}</p>" for x in scene.code.split("\n"))
        where = f'<div class="where">{html.escape(scene.caption)}</div>' if scene.caption else ""
        inner = (f'<h1 class="small">{html.escape(scene.heading)}</h1>'
                 f'{_flow(scene.items, len(scene.items), -1)}'
                 f'<div class="bubble">{where}{lines}</div>')
    elif scene.kind == "quiz":
        inner = _quiz(scene, step, countdown)
    elif scene.kind == "subscribe":
        inner = (f'{head}<div class="subbtn">{ICON_BELL}<span>Subscribe</span></div>'
                 f'<p class="subline">{html.escape(plain(scene.said[0]))}</p>')
    elif scene.kind == "playlists":
        cards = []
        for i, item in enumerate(scene.items):
            first, _, rest = item.partition("\n")
            cls = "pl" + (" on" if i <= step else "") + (" now" if i == step else "")
            cards.append(f'<div class="{cls}"><span style="color:{BLUE}">{ICON_PLAY}</span>'
                         f'<div class="p1">{html.escape(first)}</div>'
                         f'<div class="p2">{html.escape(rest)}</div></div>')
        inner = f'{head}<div class="lists">{"".join(cards)}</div>'
    elif scene.kind == "pause":
        rows = []
        for i, item in enumerate(scene.items):
            cls = "now" if i == step and not hold else "hidden" if i > step else ""
            rows.append(f'<li class="{cls}"><b>{i + 1}</b><span>{html.escape(item)}</span></li>')
        resume = (f'<div class="resume">{ICON_PLAY}<span>Resume when you\'re ready</span></div>'
                  if hold else "")
        inner = (f'<div class="paused">{_pause_icon(hold)}{head}</div>'
                 f'<ul class="list num">{"".join(rows)}</ul>{resume}')
    elif scene.kind == "lab":
        # docs/academy-youtube-plan.md rule 11: do, expect, stuck?, challenge,
        # one revealed per step, and where the lab file is downloaded.
        rows = []
        for i, item in enumerate(scene.items):
            cls = "now" if i == step else "hidden" if i > step else ""
            label = LAB_LABELS[i] if i < len(LAB_LABELS) else ""
            rows.append(f'<li class="{cls}"><b>{html.escape(label)}</b>'
                        f'<span>{html.escape(item)}</span></li>')
        dl = (f'<div class="dl"><em>Lab file</em>{html.escape(STORE_FILES_SHOWN)}/{LAB_DIR}/'
              f'{html.escape(scene.resource)}</div>') if scene.resource else ""
        inner = (f'<h1 class="small">{html.escape(scene.heading)}</h1>'
                 f'<ul class="list lab">{"".join(rows)}</ul>{dl}')
    elif scene.kind == "checklist":
        rows = []
        for i, item in enumerate(scene.items):
            cls = "now" if i == step else "done" if i < step else ""
            tick = ICON_TICK if i <= step else ""
            rows.append(f'<li class="{cls}"><div class="box">{tick}</div>'
                        f'<span>{html.escape(item)}</span></li>')
        inner = f'{head}<ul class="list check">{"".join(rows)}</ul>'
    else:
        numbered = scene.kind == "steps"
        rows = []
        for i, item in enumerate(scene.items):
            cls = "now" if i == step else "hidden" if i > step else ""
            mark_n = f"<b>{i + 1}</b>" if numbered else ""
            rows.append(f'<li class="{cls}">{mark_n}<span>{html.escape(item)}</span></li>')
        inner = f'{head}<ul class="list{" num" if numbered else ""}">{"".join(rows)}</ul>'
    teach = bool(extra) or scene.error is not None or (
        scene.kind == "diagram" and (any(scene.io) or scene.verified))
    return _doc(_chrome(mark, chapter, chapters, progress) + f'<div class="main">{inner}</div>'
                + extra, preview=preview, sheet=surface(scene.kind) == "sheet",
                css=TEACH_CSS if teach else "")


# The demo layer (2026-10-18): the recording sits in tutorial_demos.HOLE,
# scaled to 1344x756, so its window bar (DEMO_BAR tall, from y 96) and the
# step caption under it (CAP_GAP below the hole) and the store beside the
# caption all sit inside safe-16-9. The recorded pixels are not text we draw.
DEMO_BAR = 50                         # the window bar above the hole
CAP_GAP = 22                          # the hole's bottom to the caption row
CAP_ROW = vt.CALLOUT.line + 8         # the caption row: the step chip, 4 px padded
DEMO_CSS = f"""
html,body{{background:transparent}}
.win{{position:absolute;border-radius:{vt.RADIUS_CARD}px {vt.RADIUS_CARD}px 0 0;
background:{NAVY_RAISED};border:2px solid {NAVY_LINE};border-bottom:0;
display:flex;align-items:center;gap:12px;padding:0 18px}}
.win i{{width:14px;height:14px;border-radius:50%;background:{PAPER_MUTED};flex:none}}
.win i:first-child{{background:{BLUE}}}
.addr{{margin-left:14px;flex:1;height:38px;border-radius:19px;background:{NAVY};color:{BLUE_TEXT};
font:400 28px/38px {SANS};padding:0 18px;white-space:nowrap;overflow:hidden;
text-overflow:ellipsis}}
.live{{font:600 28px/1 {SANS};color:{ON_BLUE};background:{BLUE};
border-radius:{vt.RADIUS_CHIP}px;padding:4px 12px;flex:none;max-width:760px;overflow:hidden;
white-space:nowrap;text-overflow:ellipsis}}
.frame{{position:absolute;border:2px solid {NAVY_LINE};border-top:0;pointer-events:none}}
.cap{{position:absolute;display:flex;align-items:center;gap:22px;margin:0}}
.cap b{{flex:none;{F_CALLOUT};font-weight:600;color:{ON_BLUE};background:{BLUE};
border-radius:{vt.RADIUS_CHIP}px;padding:4px 14px}}
.cap span{{{F_CALLOUT};color:{PAPER};white-space:nowrap;overflow:hidden;
text-overflow:ellipsis}}
.dtag{{position:absolute;right:{SAFE}px;display:flex;align-items:center}}
.dtag .s{{{F_SUPPORT};color:{BLUE_TEXT}}}
"""


def demo_slide(scene: Scene, step: int, *, address: str, mark: str, chapter: int,
               chapters: int, preview: bool = False, progress: float = 0.0,
               extra: str = "") -> str:
    """The layer over a live demo: a browser (or window) bar with the real
    address, the recording's hole, the step's caption under it, and the
    label, the store, the rail and the progress line like every slide. No
    scrim: the browser is the picture. `scene.items[step]` is the caption."""
    from .tutorial_demos import HOLE_H, HOLE_W, HOLE_X, HOLE_Y
    caption = scene.items[step] if step < len(scene.items) else ""
    pct = max(0.0, min(1.0, progress)) * 100
    bar = (f'<div class="win" style="left:{HOLE_X}px;top:{HOLE_Y - DEMO_BAR}px;'
           f'width:{HOLE_W}px;height:{DEMO_BAR}px"><i></i><i></i><i></i>'
           f'<div class="addr">{html.escape(address)}</div>'
           # 2026-10-12: the chapter's title on every chapter, a live demo too
           f'<div class="live">Live demo · {html.escape(scene.heading)}</div></div>'
           f'<div class="frame" style="left:{HOLE_X - 2}px;top:{HOLE_Y}px;width:{HOLE_W + 4}px;'
           f'height:{HOLE_H + 2}px"></div>')
    row = HOLE_Y + HOLE_H + CAP_GAP
    cap = (f'<div class="cap" style="left:{HOLE_X}px;top:{row}px;'
           f'width:{HOLE_W - 420}px"><b>{step + 1}/{len(scene.said)}</b>'
           f'<span>{html.escape(caption)}</span></div>')
    chrome = (_rail(mark, chapter, chapters) + bar + cap
              + f'<div class="dtag" style="top:{row}px;height:{CAP_ROW}px">'
              f'<span class="s">{STORE_SHOWN}</span></div>'
              f'<div class="prog"><i style="width:{pct:.1f}%"></i></div>')
    banner = ('<div class="preview">Preview: placeholder audio, not the Fish voice. '
              'Never uploaded.</div>') if preview else ""
    size = f"html,body{{width:{W}px;height:{H}px}}"
    # the teaching components' corner (a LowerThird, a CheckpointTag), when any
    teach, cls = (TEACH_CSS, ' class="demo"') if extra else ("", "")
    return (f'<!doctype html><html><head><meta charset="utf-8"><style>{fonts_css()}{BASE}'
            f'{size}{DEMO_CSS}{teach}</style></head><body{cls}>{banner}{chrome}{extra}'
            '</body></html>')


# ------------------------------------------------------ teaching components
# docs/academy-video-system/components (Miro, 2026-10-17; .video-gap.md batch
# 4): StepMap, LowerThird, CheckpointTag, ErrorRecovery and the WorkflowTrace
# token, each drawn only from what the script fills in (tutorial_series:
# Episode.steps_map, Scene.map_step / introduces / checks / error / tokens /
# io / verified) and never otherwise. `success` and its check appear only on
# a scene marked verified. Their CSS joins a page only when one is drawn, and
# every class is prefixed (sm-, lt-, ck-, er-, tk) so no motion selector of
# the slides (NOW_ITEM, NODE_NOW, .flow .node) can match them.
# The recording's hole (tutorial_demos.HOLE; a test holds the two equal): a
# demo's tag and lower third sit in its bottom-left corner, inside safe-16-9.
DEMO_LEFT, DEMO_BOTTOM = 288, 146 + 756
CHECK_SEE = "You should now see"
CHECK_WORD = "Checked"                # beside the check on a verified tag
RAN_WORD = "Ran"                      # a traced node that ran on camera
GLYPH_CROSS = ('<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" '
               'stroke-width="2.5" stroke-linecap="round"><path d="M6 6l12 12M18 6L6 18"/>'
               '</svg>')
TEACH_CSS = f"""
.sm{{position:absolute;left:{MAIN_LEFT}px;top:{SAFE}px;display:flex;align-items:center;
gap:{vt.STEP_2}px;z-index:6}}
.sm-of{{{F_SUPPORT};color:{BLUE_TEXT}}}
.sm-row{{display:flex;align-items:center;list-style:none}}
.sm-n{{display:flex;align-items:center;gap:{vt.STEP_1}px;{F_SUPPORT};color:{PAPER_MUTED};
padding:4px {vt.STEP_2}px;border-radius:{vt.RADIUS_CHIP}px;border:2px solid {NAVY_LINE};
background:{NAVY_RAISED}}}
.sm-n.sm-now{{color:{PAPER};background:{BLUE_TINT};border-color:{BLUE}}}
.sm-n.sm-ok{{border-color:{SUCCESS}}}
.sm-n svg{{width:28px;height:28px;color:{SUCCESS}}}
.sm-n em{{font-style:normal;color:{SUCCESS}}}
.sm-w{{width:{vt.STEP_4}px;height:2px;background:{NAVY_LINE}}}
.sm-w.sm-in{{background:{BLUE}}}
.lt{{position:absolute;left:{MAIN_LEFT}px;bottom:{SAFE}px;display:flex;flex-direction:column;
padding:{vt.STEP_2}px {vt.STEP_4}px;border-radius:{vt.RADIUS_CARD}px;background:{NAVY_RAISED};
border:2px solid {NAVY_LINE};border-left:8px solid {BLUE};z-index:6}}
.lt-n{{{F_CALLOUT};color:{PAPER}}}
.lt-r{{{F_SUPPORT};color:{PAPER_MUTED}}}
.ck{{position:absolute;left:{MAIN_LEFT}px;bottom:{SAFE}px;max-width:{TEXT_RIGHT - MAIN_LEFT}px;
padding:{vt.STEP_2}px {vt.STEP_4}px;border-radius:{vt.RADIUS_CARD}px;background:{NAVY_RAISED};
border:2px solid {NAVY_LINE};z-index:6}}
.ck.ck-ok{{border:4px solid {SUCCESS}}}
.ck-k{{display:flex;align-items:center;gap:{vt.STEP_1}px;{F_SUPPORT};color:{PAPER_MUTED}}}
.ck-k svg{{width:32px;height:32px;color:{SUCCESS}}}
.ck-k em{{font-style:normal;color:{SUCCESS}}}
.ck-t{{{F_CALLOUT};color:{PAPER}}}
.ck-t code{{{F_CODE};color:{PAPER}}}
body.demo .lt,body.demo .ck{{left:{DEMO_LEFT + vt.STEP_3}px;
bottom:{H - DEMO_BOTTOM + vt.STEP_3}px}}
.er-p{{background:{NAVY_RAISED};border:4px solid {ERROR};border-radius:{vt.RADIUS_CARD}px;
padding:{vt.STEP_3}px;max-width:{SHEET_RIGHT - MAIN_LEFT - 100}px;margin-bottom:{vt.STEP_4}px}}
.er-h{{display:flex;align-items:center;gap:{vt.STEP_1}px;{F_CALLOUT};color:{ERROR};
margin-bottom:{vt.STEP_1}px}}
.er-h svg{{width:36px;height:36px}}
.er-t{{{F_CODE};color:{PAPER};white-space:pre-wrap;margin:0}}
.er-rows{{list-style:none;display:flex;flex-direction:column;gap:{vt.STEP_2}px}}
.er-rows li{{display:grid;grid-template-columns:220px 1fr;align-items:baseline;
max-width:{TEXT_RIGHT - MAIN_LEFT}px}}
.er-rows li.er-hide{{visibility:hidden}}
.er-rows b{{display:flex;align-items:center;gap:{vt.STEP_1}px;{F_SUPPORT};font-weight:600;
color:{PAPER_MUTED}}}
.er-rows span{{{F_CALLOUT};color:{PAPER_MUTED}}}
.er-rows li.er-now span{{color:{PAPER}}}
.er-rows li.er-ok b{{color:{SUCCESS}}}
.er-rows b svg{{width:28px;height:28px}}
.io{{display:inline-block;margin-top:{vt.STEP_1}px;{vt.VALUE_TAG.css()};color:{BLUE_TEXT};
background:{NAVY};border-radius:{vt.RADIUS_CHIP}px;padding:2px {vt.STEP_1}px;
align-self:flex-start}}
.node.nd-ok{{border-color:{SUCCESS}}}
.nd-st{{display:flex;align-items:center;gap:{vt.STEP_1}px;{F_SUPPORT};color:{SUCCESS};
margin-top:{vt.STEP_1}px}}
.nd-st svg{{width:28px;height:28px}}
"""
# The token alone, cut to its box (tutorials.Shooter.token): a value-tag on a
# blue fill, the spec's one 26 px exception, in mono.
TOKEN_CSS = f"""
html,body{{background:transparent}}
.tk{{position:absolute;left:0;top:0;white-space:nowrap;{vt.VALUE_TAG.css()};color:{ON_BLUE};
background:{BLUE};border-radius:{vt.RADIUS_CHIP}px;padding:4px {vt.STEP_2}px}}
"""
SM_NOW = ".sm .sm-now"                # the StepMap's active node lifts in
LOWER = ".lt"
CHECK_TAG = ".ck"
ERROR_ROW_NOW = ".er-rows li.er-now"


def _esc_code(text: str) -> str:
    """`text` escaped, its `backticked` parts set as code."""
    parts = html.escape(text).split("`")
    return "".join(f"<code>{x}</code>" if i % 2 else x for i, x in enumerate(parts))


def step_map(steps: tuple[str, ...], at: int, *, verified: bool = False) -> str:
    """StepMap: "Step k of N" and the episode's nodes, the one at `at` lit,
    the connector into it blue; a check and its word only when verified.
    "" when there is no map or no step."""
    if not steps or not 0 <= at < len(steps):
        return ""
    row = []
    for i, name in enumerate(steps):
        if i:
            row.append(f'<li class="sm-w{" sm-in" if i == at else ""}"></li>')
        cls = "sm-n" + (" sm-now" if i == at else "") + (" sm-ok" if i == at and verified
                                                          else "")
        ok = (f"{GLYPH_CHECK}<em>{CHECK_WORD}</em>" if i == at and verified else "")
        row.append(f'<li class="{cls}"><span>{html.escape(name)}</span>{ok}</li>')
    return (f'<div class="sm"><span class="sm-of">Step {at + 1} of {len(steps)}</span>'
            f'<ol class="sm-row">{"".join(row)}</ol></div>')


def lower_third(scene: Scene) -> str:
    """LowerThird: the tool's name and its one-line role, "" with none."""
    if not scene.introduces:
        return ""
    name, role = scene.introduces
    return (f'<div class="lt"><span class="lt-n">{html.escape(name)}</span>'
            f'<span class="lt-r">{html.escape(role)}</span></div>')


def checkpoint_tag(look: str, *, verified: bool = False) -> str:
    """CheckpointTag: "You should now see" and what to look for; the success
    border and check only when the scene is verified."""
    ok = f"{GLYPH_CHECK}<em>{CHECK_WORD}</em> · " if verified else ""
    return (f'<div class="ck{" ck-ok" if verified else ""}"><span class="ck-k">{ok}'
            f'{CHECK_SEE}</span><p class="ck-t">{_esc_code(look)}</p></div>')


def error_card(scene: Scene, step: int) -> str:
    """ErrorRecovery: the real error under its 4 px `error` border from step
    0, then Cause, Fix and Check revealed one a step; the last row reads
    Verified, in `success` with its check, only on a verified scene."""
    from .tutorial_series import ERROR_ROW_VERIFIED, ERROR_ROWS
    card = scene.error
    if card is None:
        return ""
    rows = []
    for i, (label, text) in enumerate(zip(ERROR_ROWS, (card.cause, card.fix, card.check),
                                          strict=True)):
        last = i == len(ERROR_ROWS) - 1
        ok = last and scene.verified
        cls = ("er-now" if i == step - 1 else "er-hide" if i >= step else "")
        cls += " er-ok" if ok else ""
        mark = f"{GLYPH_CHECK}{ERROR_ROW_VERIFIED}" if ok else html.escape(label)
        rows.append(f'<li class="{cls.strip()}"><b>{mark}</b>'
                    f'<span>{html.escape(text)}</span></li>')
    return (f'<h1 class="small">{html.escape(scene.heading)}</h1>'
            f'<div class="er-p"><div class="er-h">{GLYPH_CROSS}<span>Error</span></div>'
            f'<pre class="er-t">{html.escape(card.text)}</pre></div>'
            f'<ul class="er-rows">{"".join(rows)}</ul>')


def trace_token(label: str) -> str:
    """The WorkflowTrace token alone: a value-tag, to be cut to its box."""
    return _doc(f'<div class="tk">{html.escape(label)}</div>', layer=False, css=TOKEN_CSS)


def overlays(scene: Scene, step: int, *, steps_map: tuple[str, ...] = (),
             check: int = -1, still: bool = False) -> str:
    """What the teaching components lay over a lesson slide at this beat:
    the StepMap (a scene on the map, never a demo: the app is the picture),
    the LowerThird (the scene's first narrated step; never a countdown or a
    hold, `still`), the CheckpointTag (a check beat). "" when the script
    fills none of them in."""
    out = ""
    if scene.kind != "demo":
        out += step_map(steps_map, scene.map_step)
    if step == 0 and check < 0 and not still:
        out += lower_third(scene)
    if 0 <= check < len(scene.checks):
        out += checkpoint_tag(scene.checks[check][1], verified=scene.verified)
    return out


# ------------------------------------------------------------------ motion
# What moves on each slide (tutorial_motion.Spec): CSS selectors, the
# heading first and then what this step adds, so the renderer can cut each
# from the page and let it rise into place. Only what is new moves (the
# guide: "animate only the active step"); a state that only changes (a
# code line lit, a quiz answer marked) crossfades and nothing travels.
HEAD = ".main h1"
NOW_ITEM = ".main .list li.now"
NODE_NOW = ".node.now, .wire:has(+ .node.now)"


def motion_of(scene: Scene, step: int) -> motion.Spec:
    """The motion of build step `step` of `scene` (STILL for none)."""
    kind = scene.kind
    first = step == 0
    head = ((".title h1", False),) if kind == "title" else (
        ((".paused", False),) if kind == "pause" else ((HEAD, False),))
    head = head if first else ()
    if kind == "demo":
        return motion.STILL                       # a cut: the software state is the picture
    if scene.error is not None:
        # ErrorRecovery: the error is cut in whole (no fade); each row rises
        return motion.STILL if first else motion.Spec(((ERROR_ROW_NOW, False),))
    if kind == "title":
        return motion.Spec(head + ((".title .sub", False),))
    if kind in ("bullets", "steps", "pause", "lab"):
        return motion.Spec(head + ((NOW_ITEM, False),))
    if kind == "checklist":
        return motion.Spec(head + ((".main .check li.now", True),))
    if kind == "diagram":
        return motion.Spec(head + ((NODE_NOW, False),),
                           trace=(step - 1, step) if step else None,
                           token=token_into(scene, step) if step else "")
    if kind == "payoff":
        # The opening's 3-6 s (the guide): trace the workflow once, stop at
        # the last node, then the result fades in beside it.
        n = len(scene.items)
        return motion.Spec(head + ((".bubble", True),),
                           trace=(0, n - 1) if first and n > 1 else None)
    if kind == "hook":
        return motion.Spec(head + ((".bubble:not(.hidden)", False),) if step else head)
    if kind == "quiz":
        return motion.Spec(head + ((".opts", False),) if first
                           else ((".answer", True),))
    if kind == "code":
        return motion.Spec(head + ((".card", False),)) if first else motion.STILL
    if kind == "subscribe":
        return motion.Spec(head + ((".subbtn", False),))
    if kind == "playlists":
        return motion.Spec(head + ((".pl.now", False),))
    return motion.Spec(head)


def token_into(scene: Scene, node: int) -> str:
    """The WorkflowTrace token the script gives for the connector into
    `node`, "" when it gives none (the bare dot runs)."""
    return scene.tokens[node] if 0 <= node < len(scene.tokens) else ""


def beat_motion(scene: Scene, step: int, *, orient: bool = False, check: int = -1,
                still: bool = False) -> motion.Spec:
    """motion_of, plus the teaching components that enter at this beat: the
    StepMap's active node lifting on an orientation beat (`orient`), the
    LowerThird on the scene's first step and the CheckpointTag on a check
    beat, those two leaving after hold-tag. A demo stays a cut underneath:
    only the overlays rise."""
    base = motion.STILL if still or check >= 0 else motion_of(scene, step)
    groups = list(base.groups)
    exits: list[str] = []
    if orient and scene.kind != "demo" and scene.map_step >= 0 and not still:
        groups.append((SM_NOW, False))
    if check >= 0:
        groups.append((CHECK_TAG, False))
        exits.append(CHECK_TAG)
    elif step == 0 and scene.introduces and not still:
        groups.append((LOWER, False))
        exits.append(LOWER)
    return replace(base, groups=tuple(groups), exits=tuple(exits))


# The cards around the lesson: a one-line promise rises, then its detail.
TOPIC_MOTION = motion.Spec(((".today, .topic", False), (".clock", False)))
INTRO_MOTION = motion.Spec(((".big", False), (".line, .under", False)))


def trace_dot() -> str:
    """The workflow trace's dot: 36 px, transparent around it."""
    return _doc('<div class="dot"></div>', w=36, h=36, layer=False,
                css="html,body{background:transparent}")


def steps_of(scene: Scene) -> int:
    return len(scene.said)


def shows_list(scene: Scene) -> bool:
    return scene.kind in LIST_KINDS


INTRO_CSS = f"""
.big{{position:absolute;left:160px;top:330px;{F_PROMISE};max-width:1000px}}
.line{{position:absolute;left:164px;top:520px;width:520px;height:8px;background:{BLUE};
border-radius:4px}}
.under{{position:absolute;left:164px;top:576px;{F_CALLOUT};color:{PAPER_MUTED}}}
.today{{position:absolute;left:164px;top:250px;{F_CALLOUT};color:{BLUE_TEXT}}}
.topic{{position:absolute;left:160px;top:320px;{F_PROMISE};max-width:1180px}}
.clock{{position:absolute;left:164px;bottom:190px;display:inline-flex;align-items:center;
gap:{vt.STEP_2}px;padding:{vt.STEP_2}px {vt.STEP_4}px;border-radius:{vt.RADIUS_RING}px;
background:{BLUE};color:{ON_BLUE};{F_CALLOUT};font-weight:600}}
.clock svg{{width:44px;height:44px}}
"""


def intro(preview: bool = False) -> str:
    return _doc('<div class="big">Welcome to<br>ATLAS Academy</div>'
                f'<div class="line"></div><div class="under">{html.escape(INTRO_BYLINE)}</div>',
                css=INTRO_CSS, preview=preview)


ICON_CLOCK = (f'<svg viewBox="0 0 56 56"><circle cx="28" cy="31" r="21" fill="none" stroke="{ON_BLUE}" '
              f'stroke-width="5"/><path d="M28 31V19M22 5h12" stroke="{ON_BLUE}" stroke-width="5" '
              f'stroke-linecap="round"/></svg>')


def topic(ep: Episode, preview: bool = False, minutes: int | None = None) -> str:
    """The episode's topic card. Since 2026-10-12 a lesson's opener ("Welcome
    back ... in under <minutes> minutes") is said over it, and the clock
    shows those same measured minutes; without `minutes`, an "under five
    minutes" topic line shows "Under 5 minutes"."""
    shown = (minutes if minutes is not None
             else 5 if UNDER_FIVE.search(ep.topic_said) else None)
    clock = (f'<div class="clock">{ICON_CLOCK}<span>Under {shown} minutes</span></div>'
             if shown is not None else "")
    return _doc('<div class="today">Today</div>'
                f'<div class="topic">{html.escape(ep.topic_shown)}</div>{clock}',
                css=INTRO_CSS, preview=preview)


# ------------------------------------------------------------ the opening
# The Atlas Academy video system (Miro, 2026-10-17; docs/academy-video-system
# episode-structure.md "Opening (0-20 s)"): the result on screen from frame 0
# (ResultTitle), its badge rising at frame 12; one trace along one connector
# at 3-6 s (WorkflowTrace); the 1 s BrandSting at 6-7 s on a flat navy
# frame; then the scope line over the same result. No logo first, no
# welcome, no presenter clip. Opaque navy pages: nothing under them moves.
RESULT_SECONDS = 6.0                 # the result and its trace, before the sting
STING_AT = RESULT_SECONDS            # the sting sits at 6-7 s
BADGE_FRAME = 12                     # "frame 12, after the app is readable"
TRACE_AT = 3.0                       # the trace runs from 3 s, then holds
RESULT_CSS = f"""
.result{{position:absolute;left:{SAFE}px;right:{SAFE}px;top:{SAFE + vt.STEP_6}px}}
.result .flow{{margin-top:0;display:inline-flex}}
.result .bubble{{max-width:{W - 2 * SAFE}px}}
.badge{{position:absolute;left:{SAFE}px;bottom:{SAFE}px;display:inline-flex;
align-items:center;gap:{vt.STEP_2}px;padding:{vt.STEP_2}px {vt.STEP_4}px;
border-radius:{vt.RADIUS_RING}px;background:{NAVY_RAISED};border:2px solid {NAVY_LINE};
color:{PAPER};{F_PROMISE}}}
.badge.ok{{background:{SUCCESS};border-color:{SUCCESS};color:{ON_BLUE}}}
.badge svg{{width:56px;height:56px;flex:none}}
.scope{{position:absolute;left:{SAFE}px;bottom:{SAFE}px;max-width:{W - 2 * SAFE}px}}
.scope p{{{F_CALLOUT};color:{PAPER}}}
.scope span{{display:block;margin-top:{vt.STEP_1}px;{F_SUPPORT};color:{PAPER_MUTED}}}
"""
# A single-stroke 2 px check (the system's glyph set), in the text's colour.
GLYPH_CHECK = ('<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" '
               'stroke-linecap="round" stroke-linejoin="round"><path d="M4 12.5l5 5L20 7"/></svg>')
RESULT_MOTION_BADGE = ".badge"
SCOPE_MOTION = motion.Spec(((".scope", False),))
GLYPH_NEXT = ('<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" '
              'stroke-linecap="round" stroke-linejoin="round"><path d="M4 12h15M13 6l6 6-6 6"/>'
              '</svg>')


def _result_picture(ep: Episode) -> str:
    """The payoff's flow, every node on, and its result in the bubble: the
    finished thing, from the first frame."""
    sc = ep.payoff
    if sc is None:
        return ""
    lines = "".join(f"<p>{html.escape(x)}</p>" for x in sc.code.split("\n"))
    where = f'<div class="where">{html.escape(sc.caption)}</div>' if sc.caption else ""
    flow = _flow(sc.items, len(sc.items), -1) if sc.items else ""
    return f'<div class="result">{flow}<div class="bubble">{where}{lines}</div></div>'


def _banner(preview: bool) -> str:
    return ('<div class="preview">Preview: placeholder audio, not the Fish voice. '
            'Never uploaded.</div>') if preview else ""


def result_title(ep: Episode, preview: bool = False) -> str:
    """ResultTitle (0-6 s): the result and, bottom-left inside safe-16-9, the
    badge in `promise` type. `success` with its check only when the episode
    marks the result verified; otherwise the navy-raised plate."""
    ok = ep.verified and bool(ep.result)
    glyph = GLYPH_CHECK if ok else ""
    badge = (f'<div class="badge{" ok" if ok else ""}">{glyph}'
             f'<span>{html.escape(badge_of(ep))}</span></div>')
    return _doc(_banner(preview) + _result_picture(ep) + badge, css=RESULT_CSS,
                layer=False)


def result_trace(ep: Episode) -> tuple[int, int] | None:
    """The one connector the opening traces: into the last node (the
    result), when the payoff draws a flow of two nodes or more."""
    n = len(ep.payoff.items) if ep.payoff is not None else 0
    return (n - 2, n - 1) if n >= 2 else None


def result_motion(ep: Episode) -> motion.Spec:
    """What moves in the result's six seconds: the badge, and the trace --
    carrying the payoff's token into the last node when the script gives
    one (WorkflowTrace), else the dot."""
    trace = result_trace(ep)
    token = token_into(ep.payoff, trace[1]) if trace and ep.payoff is not None else ""
    return motion.Spec(((RESULT_MOTION_BADGE, False),), trace=trace, token=token)


def scope_card(ep: Episode, preview: bool = False) -> str:
    """7 s on (the ResultTitle's support line): the same result, the badge
    gone, and the lesson's scope and what you need, bottom-left."""
    topic = ep.opener_topic[:1].upper() + ep.opener_topic[1:]
    needs = (f"<span>What you need: {html.escape('; '.join(ep.needs))}</span>"
             if ep.needs else "")
    scope = f'<div class="scope"><p>{html.escape(topic)}</p>{needs}</div>'
    return _doc(_banner(preview) + _result_picture(ep) + scope, css=RESULT_CSS,
                layer=False)


# BrandSting (1000 ms, 30 frames): the wordmark rises in over 6 frames on
# `settle`, a `blue` line draws beneath it over 12 on `draw`, it holds, and
# it goes out over the last 6. Flat navy, nothing else on screen, drawn
# frame by frame (sting_frame) and cached once (tutorials.sting_clip).
STING_FRAMES = vt.STING_FRAMES
STING_IN, STING_LINE, STING_OUT = 6, 12, 6
STING_WORD = ("Atlas", "Academy")
STING_CSS = f"""
html,body{{background:{NAVY}}}
.sting{{position:absolute;left:0;right:0;top:0;bottom:0;display:grid;place-items:center}}
.sting .lockup{{display:grid;gap:{vt.STEP_3}px;justify-items:stretch}}
.sting .word{{font:600 120px/1 {SANS};letter-spacing:-.02em;color:{PAPER}}}
.sting .word span{{color:{PAPER_MUTED};font-weight:500}}
.sting .line{{height:10px;background:{BLUE};transform-origin:left}}
"""


def _bezier(x1: float, y1: float, x2: float, y2: float, x: float) -> float:
    """CSS cubic-bezier(x1, y1, x2, y2) at progress `x` (0-1)."""
    x = min(1.0, max(0.0, x))
    lo, hi = 0.0, 1.0
    for _ in range(40):                       # the curve's t for this x
        t = (lo + hi) / 2
        bx = 3 * (1 - t) ** 2 * t * x1 + 3 * (1 - t) * t ** 2 * x2 + t ** 3
        lo, hi = (t, hi) if bx < x else (lo, t)
    t = (lo + hi) / 2
    return 3 * (1 - t) ** 2 * t * y1 + 3 * (1 - t) * t ** 2 * y2 + t ** 3


SETTLE_CURVE = (0.2, 0.0, 0.0, 1.0)          # vt.SETTLE
DRAW_CURVE = (0.4, 0.0, 0.2, 1.0)            # vt.DRAW


def sting_state(frame: int) -> tuple[float, float, float]:
    """(opacity, rise px, line scale) of the sting at `frame` (0-29)."""
    rise = _bezier(*SETTLE_CURVE, frame / STING_IN)
    line = _bezier(*DRAW_CURVE, (frame - STING_IN) / STING_LINE)
    out = _bezier(*SETTLE_CURVE, (frame - (STING_FRAMES - STING_OUT)) / STING_OUT)
    return round(rise * (1 - out), 4), round(vt.TITLE_RISE_PX * (1 - rise), 2), round(line, 4)


def sting_frame(frame: int, preview: bool = False) -> str:
    """One frame of the BrandSting."""
    alpha, dy, scale = sting_state(frame)
    word = (f'{html.escape(STING_WORD[0])} <span>{html.escape(STING_WORD[1])}</span>')
    body = (f'<div class="sting"><div class="lockup" style="opacity:{alpha};'
            f'transform:translateY({dy}px)"><div class="word">{word}</div>'
            f'<div class="line" style="transform:scaleX({scale})"></div></div></div>')
    return _doc(_banner(preview) + body, css=STING_CSS, layer=False)


# ------------------------------------------------------------ the end card
# EndCard (Miro, 2026-10-17; docs/academy-video-system/components/EndCard):
# the last END_CARD_MIN seconds or more on flat navy. Left, inside
# safe-16-9: a check and "You now have <result>.", the next lesson's title
# and why it follows, and (only with lab files) a secondary shop line. The
# right vt.ENDSCREEN_RESERVE px inside the safe area stay flat navy and empty
# for the native element Miro adds in Studio: no Subscribe, no painted
# thumbnail, nothing that loops. It rises in once and stays to the last frame.
END_CARD_MIN = 15.0
CARD_LEFT_W = W - 2 * SAFE - vt.ENDSCREEN_RESERVE - vt.STEP_8      # 1024
RESERVE_X = W - SAFE - vt.ENDSCREEN_RESERVE                         # 1184
SHOP_LINE = "Practice files: {store} (link in the description)"
END_CSS = f"""
.endcard{{position:absolute;left:{SAFE}px;top:{SAFE}px;bottom:{SAFE}px;width:{CARD_LEFT_W}px;
display:flex;flex-direction:column;justify-content:center;gap:{vt.STEP_8}px}}
.done{{display:flex;gap:{vt.STEP_2}px;align-items:center;{F_CALLOUT};color:{PAPER}}}
.done svg{{width:48px;height:48px;flex:none;color:{BLUE_TEXT}}}
.done.ok svg{{color:{SUCCESS}}}
.next .k{{display:block;{F_SUPPORT};color:{PAPER_MUTED};margin-bottom:{vt.STEP_2}px}}
.next .t{{display:block;{F_CHAPTER};color:{PAPER};margin-bottom:{vt.STEP_3}px}}
.next .a{{display:flex;align-items:center;gap:{vt.STEP_2}px;{F_CALLOUT};color:{BLUE_TEXT}}}
.next .a i{{display:block;flex:none;width:48px;height:4px;background:{BLUE}}}
.shopline{{{F_SUPPORT};color:{PAPER_MUTED}}}
.shopline b{{color:{BLUE_TEXT};font-weight:500}}
"""
END_CARD_MOTION = motion.Spec(((".endcard", False),))


def end_card(ep: Episode, nxt: NextUp | None, *, shop: bool, preview: bool = False) -> str:
    """The EndCard: what you now have, the one next step, and the shop line
    only when the lesson has practice files. The reserve is left unpainted."""
    rows = []
    if ep.result:
        # A success check only for a lesson whose result was verified; otherwise
        # a neutral arrow (README: "a success chip means the test really passed").
        mark, cls = (GLYPH_CHECK, "done ok") if ep.verified else (GLYPH_NEXT, "done")
        rows.append(f'<div class="{cls}">{mark}<span>You now have '
                    f'{html.escape(lead_lower(ep.result))}.</span></div>')
    if nxt is not None:
        label = "Next lesson" if nxt.kind == "episode" else "Next in the series"
        rows.append(f'<div class="next"><span class="k">{label}</span>'
                    f'<span class="t">{html.escape(nxt.title)}</span>'
                    f'<span class="a"><i></i>{html.escape(nxt.why)}</span></div>')
    if shop:
        line = html.escape(SHOP_LINE).replace("{store}", f"<b>{STORE_SHOWN}</b>")
        rows.append(f'<div class="shopline">{line}</div>')
    return _doc(_banner(preview) + f'<div class="endcard">{"".join(rows)}</div>',
                css=END_CSS, layer=False)


# ---------------------------------------------------------------- thumbnail

THUMB_CSS = f"""
body{{background:radial-gradient(circle at 76% 52%,{NAVY_LINE} 0,{NAVY} 62%)}}
.dots{{position:absolute;inset:0;background-image:radial-gradient({rgba(PAPER, .13)} 2px,
transparent 2.5px);background-size:30px 30px;-webkit-mask-image:linear-gradient(90deg,transparent 30%,
#000 60%)}}
.chip{{position:absolute;left:64px;top:56px;font:600 28px/1 {SANS};padding:12px 18px;border-radius:10px;
background:{rgba(PAPER, .1)};color:{BLUE_TEXT}}}
.ep{{position:absolute;right:64px;top:52px;font:600 44px/1 {SANS};color:{PAPER}}}
.t{{position:absolute;left:60px;top:190px;width:740px}}
.t .l1{{display:block;font:600 150px/.9 {SANS};letter-spacing:-.045em;color:{PAPER};
text-shadow:0 6px 0 rgba(0,0,0,.35)}}
.t .l2{{display:inline-block;margin-top:22px;font:600 140px/1 {SANS};letter-spacing:-.045em;
color:{ON_BLUE};background:{BLUE};padding:10px 24px 18px;border-radius:14px;transform:rotate(-2deg);
box-shadow:0 14px 0 rgba(0,0,0,.35)}}
.t.long .l1{{font-size:112px}}
.t.long .l2{{font-size:100px}}
.t.xlong .l1{{font-size:100px}}
.t.xlong .l2{{font-size:90px}}
.motif{{position:absolute;right:30px;top:110px;width:520px;height:560px}}
.tag{{top:auto;bottom:30px;right:auto;left:64px;font-size:20px;padding:8px 12px}}
"""


def _tile(x: int, y: int, glyph: str, lit: bool, w: int = 150) -> str:
    stroke = BLUE if lit else BLUE_TEXT
    glow = ' filter="url(#glow)"' if lit else ""
    return (f'<g{glow}><rect x="{x}" y="{y}" width="{w}" height="{w}" rx="34" fill="{INK}" '
            f'stroke="{stroke}" stroke-width="9"/></g>'
            f'<g transform="translate({x + w / 2} {y + w / 2})" stroke="{stroke}" fill="none" '
            f'stroke-width="9" stroke-linecap="round" stroke-linejoin="round">{glyph}</g>')


GLYPH_BOLT = '<path d="M8 -38 L-22 6 H4 L-8 38 L24 -8 H-2 Z"/>'
GLYPH_GLOBE = ('<circle r="32"/><path d="M-32 0 H32 M0 -32 C-18 -14 -18 14 0 32 '
               'C18 14 18 -14 0 -32"/>')
GLYPH_CHAT = '<path d="M-30 -24 H30 V14 H-6 L-22 30 V14 H-30 Z"/>'
GLYPH_FORM = '<path d="M-26 -34 H26 V34 H-26 Z M-14 -14 H14 M-14 2 H14 M-14 18 H4"/>'
GLYPH_DB = ('<ellipse cy="-20" rx="28" ry="10"/><path d="M-28 -20 V22 C-28 34 28 34 28 22 V-20 '
            'M-28 2 C-28 14 28 14 28 2"/>')
GLYPH_FILE = '<path d="M-22 -32 H8 L24 -16 V32 H-22 Z M8 -32 V-16 H24"/>'
GLYPH_CAL = '<path d="M-28 -24 H28 V30 H-28 Z M-28 -8 H28 M-14 -34 V-18 M14 -34 V-18"/>'
GLYPH_LOOP = '<path d="M-24 -10 A26 26 0 1 1 -8 22 M-24 -10 L-30 -30 M-24 -10 L-4 -16"/>'

DEFS = ('<defs><filter id="glow" x="-50%" y="-50%" width="200%" height="200%">'
        '<feGaussianBlur stdDeviation="10" result="b"/><feMerge><feMergeNode in="b"/>'
        '<feMergeNode in="SourceGraphic"/></feMerge></filter></defs>')


def _arrow(d: str, head: str) -> str:
    return (f'<path d="{d}" fill="none" stroke="{PAPER}" stroke-width="9" stroke-linecap="round"/>'
            f'<path d="{head}" fill="none" stroke="{PAPER}" stroke-width="9" '
            'stroke-linecap="round" stroke-linejoin="round"/>')


def _wired(a: tuple[int, int], b: tuple[int, int], lit: bool) -> str:
    colour = BLUE if lit else BLUE_TEXT
    (x1, y1), (x2, y2) = a, b
    mx = (x1 + x2) / 2
    glow = ' filter="url(#glow)"' if lit else ""
    return (f'<path d="M{x1} {y1} C{mx} {y1} {mx} {y2} {x2} {y2}" fill="none" stroke="{colour}" '
            f'stroke-width="9" stroke-linecap="round"{glow}/>')


def _motif(series_key: str) -> str:
    """The episode's subject, drawn (never a logo): wired node tiles for the
    n8n-shaped series, a plug between Claude and its tools for MCP, two
    playlist cards for the channel welcome."""
    svg = '<svg class="motif" viewBox="0 0 520 560">' + DEFS
    if series_key in ("claude", "local", "agents"):
        left = ("Claude" if series_key == "claude" else
                "Hermes" if series_key == "local" else "Agent")
        body = (f'<rect x="10" y="190" width="190" height="180" rx="36" fill="{BLUE}" '
                'filter="url(#glow)"/>'
                f'<text x="105" y="295" text-anchor="middle" font-family={SANS} '
                f'font-weight="600" font-size="46" fill="{ON_BLUE}">{left}</text>'
                # the plug: a rounded connector with its slot
                f'<rect x="222" y="244" width="86" height="72" rx="20" fill="{INK}" '
                f'stroke="{PAPER}" stroke-width="8"/><rect x="246" y="268" width="38" height="24" '
                f'rx="8" fill="{PAPER}"/>'
                + _wired((200, 280), (222, 280), True)
                + _wired((308, 280), (370, 120), True)
                + _wired((308, 280), (370, 280), True)
                + _wired((308, 280), (370, 440), True)
                + _tile(370, 52, GLYPH_FILE, False, 136)
                + _tile(370, 212, GLYPH_DB, False, 136)
                + _tile(370, 372, GLYPH_CAL, False, 136)
                + _arrow("M150 520 C150 470 210 420 250 330", "M222 350 L252 322 L268 360"))
        return svg + body + "</svg>"
    if series_key == "channel":
        body = ""
        for i, (y, glyph) in enumerate(((60, GLYPH_GLOBE), (300, GLYPH_BOLT))):
            stroke = BLUE if i == 0 else BLUE_TEXT
            body += (f'<rect x="40" y="{y}" width="440" height="200" rx="34" fill="{INK}" '
                     f'stroke="{stroke}" stroke-width="9"/>'
                     f'<path d="M100 {y + 64} L156 {y + 100} L100 {y + 136} Z" fill="{stroke}"/>'
                     f'<rect x="200" y="{y + 70}" width="220" height="18" rx="9" fill="{PAPER}"/>'
                     f'<rect x="200" y="{y + 112}" width="150" height="14" rx="7" '
                     f'fill="{PAPER_MUTED}"/>')
        return svg + body + "</svg>"
    first = GLYPH_FORM if series_key in ("project", "business") else GLYPH_BOLT
    body = (_wired((180, 145), (300, 280), True) + _wired((450, 280), (180, 415), True)
            + _tile(30, 70, first, False)
            + _tile(300, 205, GLYPH_GLOBE, True)
            + _tile(30, 340, GLYPH_CHAT, True)
            + _arrow("M330 520 C300 470 260 440 196 430", "M222 404 L192 430 L226 452"))
    return svg + body + "</svg>"


# The 3D thumbnail: the series' 3D still fills the frame (its subject on the
# right), an ink falloff on the left carries the words, and the words have
# depth -- the first line extruded like a machined letter, the second on an
# accent slab (blue since 2026-10-12) with a thick edge, the block turned a
# few degrees into the scene.
def _rgb255(colour: str) -> tuple[int, int, int]:
    return tuple(int(colour[i:i + 2], 16) for i in (1, 3, 5))  # type: ignore[return-value]


def _extrude(steps: int, top: tuple[int, int, int], bottom: tuple[int, int, int],
             shadow: str) -> str:
    out = []
    for i in range(1, steps + 1):
        k = i / steps
        r, g, b = (round(a + (z - a) * k) for a, z in zip(top, bottom, strict=True))
        out.append(f"{i}px {i}px 0 rgb({r},{g},{b})")
    return ",".join(out + [shadow])


THUMB_3D_CSS = f"""
body{{background-color:{INK};background-size:cover;background-position:center}}
.fall{{position:absolute;inset:0;background:linear-gradient(90deg,{rgba(NAVY, .92)} 0,
{rgba(NAVY, .78)} 38%,{rgba(NAVY, .2)} 62%,{rgba(NAVY, 0)} 75%)}}
.t{{transform:perspective(1400px) rotateY(9deg);transform-origin:0 50%}}
.t .l1{{text-shadow:{_extrude(8, _rgb255(PAPER_MUTED), _rgb255(PAPER_MUTED), "0 18px 30px rgba(0,0,0,.75)")}}}
.t .l2{{box-shadow:{",".join(f"0 {i}px 0 {rgba(ON_BLUE, .35 + i * .065)}" for i in range(1, 11))},
0 26px 34px rgba(0,0,0,.6);text-shadow:0 2px 0 rgba(255,255,255,.25)}}
.chip{{background:{rgba(NAVY, .7)};backdrop-filter:none}}
.ep{{text-shadow:{_extrude(4, _rgb255(PAPER_MUTED), _rgb255(PAPER_MUTED), "0 8px 16px rgba(0,0,0,.7)")}}}
"""


def thumbnail(title: str, series_key: str, mark: str, number: int, *, badge: str = "",
              ep: Episode | None = None) -> str:
    """The episode's thumbnail (tutorial_thumbs, 2026-10-11): one of five
    layouts chosen from what the video is, its own focal picture, the level
    tab and the measured `badge` ("6 MIN"). Without the episode the layout
    is derived from the words alone."""
    from . import tutorial_thumbs as tt
    spec = tt.spec_for(ep) if ep is not None else tt.derive(_Words(title))
    level = ep.level if ep is not None else 0
    inner, extra = tt.body(spec, title=title, level=level, badge=badge,
                           still=still_uri(series_key), tag=TAG)
    return _doc(inner, w=TW, h=TH, css=tt.page_css(spec, title) + extra, layer=False)


class _Words:
    """An episode that is only its thumbnail words (callers without one)."""

    def __init__(self, title: str) -> None:
        self.thumb_title, self.summary, self.slug, self.scenes = title, "", "", ()


def thumbnail_v3(title: str, series_key: str, mark: str, number: int) -> str:
    """The 2026-10-10 thumbnail (series 3D still, words on an amber slab),
    kept for the record of what the first public videos were made with."""
    l1, _, l2 = title.partition("\n")
    most = max(len(l1), len(l2))
    long = " xlong" if most > 12 else " long" if most > 10 else ""
    ep = f'<div class="ep">Ep {number}</div>' if series_key != "channel" else ""
    words = (f'<div class="chip">{html.escape(mark)}</div>{ep}'
             f'<div class="t{long}"><span class="l1">{html.escape(l1)}</span>'
             f'<span class="l2">{html.escape(l2)}</span></div><div class="tag">{TAG}</div>')
    still = still_uri(series_key)
    if not still:                       # no 3D kit: the drawn motif, as before
        return _doc(f'<div class="dots"></div>{words}{_motif(series_key)}',
                    w=TW, h=TH, css=THUMB_CSS, layer=False)
    css = THUMB_CSS + THUMB_3D_CSS + f'body{{background-image:url("{still}")}}'
    return _doc(f'<div class="fall"></div>{words}', w=TW, h=TH, css=css, layer=False)
