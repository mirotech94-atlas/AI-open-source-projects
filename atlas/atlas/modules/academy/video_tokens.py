"""The Atlas Academy video system's tokens, as named constants.

Source of truth: docs/academy-video-system/tokens.json (Miro's spec,
2026-10-17). This module mirrors it so the renderers never parse JSON at
import; tests/test_academy_video_tokens.py asserts every token in the file
has a constant here with the same value, and that the frame counts below
are the ones the duration notes state.

Scope (Miro, 2026-10-17): tutorial videos, their thumbnails and teaching
Shorts use this navy system and IBM Plex. The shop, store and API pages
keep the 2026-10-12 black and royal blue; nothing here
is for them.
"""
from __future__ import annotations

import base64
import functools
from dataclasses import dataclass
from pathlib import Path

TOKENS_JSON = (Path(__file__).resolve().parents[3]
               / "docs" / "academy-video-system" / "tokens.json")
FPS = 30

# ------------------------------------------------------------------ colour
NAVY = "#101b30"                     # the frame ground; never pure black
NAVY_RAISED = "#182540"              # cards, plates, tags on navy
NAVY_LINE = "#2a3a5c"                # hairlines and inactive connectors; not text
PAPER = "#f6f8ff"                    # primary text
PAPER_MUTED = "#b4bdd6"              # secondary text
BLUE = "#526de6"                     # the one accent: rules, rings, 24px+ labels
BLUE_TEXT = "#9db0f5"                # blue for small text
BLUE_TINT = "#1f2d57"                # the active-step fill, the ring halo
ON_BLUE = "#0b1226"                  # text on a solid blue fill (24px+ only)
AMBER = "#f2b544"                    # pending / processing, with a word or clock
SUCCESS = "#3cd9c0"                  # passed, with a check or the word
ERROR = "#ff7a7a"                    # the real error, with a cross or the word

COLORS: dict[str, str] = {
    "navy": NAVY, "navy-raised": NAVY_RAISED, "navy-line": NAVY_LINE, "paper": PAPER,
    "paper-muted": PAPER_MUTED, "blue": BLUE, "blue-text": BLUE_TEXT,
    "blue-tint": BLUE_TINT, "on-blue": ON_BLUE, "amber": AMBER, "success": SUCCESS,
    "error": ERROR,
}
STATE_COLORS = (AMBER, SUCCESS, ERROR)


# -------------------------------------------------------------------- type
SANS_FAMILY = '"IBM Plex Sans", "Segoe UI", system-ui, sans-serif'
MONO_FAMILY = '"IBM Plex Mono", "SFMono-Regular", Consolas, monospace'


@dataclass(frozen=True)
class TypeStyle:
    name: str
    family: str                      # "sans" | "mono"
    size: int                        # px
    line: int                        # px
    weight: int
    tracking: str = ""               # letter-spacing, "" for none

    def css(self) -> str:
        """`font:` and letter-spacing for this style, in the vendored faces."""
        fam = SANS_FAMILY if self.family == "sans" else MONO_FAMILY
        track = f";letter-spacing:{self.tracking}" if self.tracking else ""
        return f"font:{self.weight} {self.size}px/{self.line}px {fam}{track}"


PROMISE = TypeStyle("promise", "sans", 72, 80, 600, "-0.01em")
CHAPTER = TypeStyle("chapter", "sans", 56, 64, 600, "-0.01em")
CALLOUT = TypeStyle("callout", "sans", 36, 44, 500)
SUPPORT = TypeStyle("support", "sans", 28, 36, 400)
CAPTION = TypeStyle("caption", "sans", 44, 52, 500)
CODE = TypeStyle("code", "mono", 32, 44, 400)
VALUE_TAG = TypeStyle("value-tag", "mono", 26, 32, 500)
SHORTS_HOOK = TypeStyle("shorts-hook", "sans", 88, 96, 600, "-0.01em")
SHORTS_CAPTION = TypeStyle("shorts-caption", "sans", 60, 70, 500)
TYPE_STYLES = (PROMISE, CHAPTER, CALLOUT, SUPPORT, CAPTION, CODE, VALUE_TAG, SHORTS_HOOK,
               SHORTS_CAPTION)
# "Nothing on a 16:9 frame is set smaller than `support`" -- value-tag (26)
# is the spec's one exception, a mono value on a trace; the floor below is
# what the renderers hold every other piece of 16:9 text to.
FLOOR_16_9 = SUPPORT.size
WEIGHTS_SANS = (400, 500, 600)       # the vendored files; nothing else is drawn
WEIGHTS_MONO = (400, 500)
FONTS_DIR = Path(__file__).with_name("assets") / "fonts"
# The Short captions are burned by libass, which cannot open woff2 here: one
# TTF of the caption's weight, in its own folder (the `ass` filter's
# fontsdir), matched by Plex's legacy family name. libass sizes a font by
# ascender + descender, 1.3 em for Plex (1025 + 275 of 1000 units), so an
# ASS Fontsize is ASS_CELL x the px size the spec means.
LIBASS_FONTS_DIR = FONTS_DIR / "libass"
ASS_FONT = "IBM Plex Sans Medm"      # weight 500, the shorts-caption weight
ASS_CELL = 1.3


@functools.lru_cache(maxsize=1)
def fonts_css() -> str:
    """@font-face rules embedding the vendored IBM Plex files (the video
    system's faces); the store keeps reel_design.fonts_css."""
    out = []
    for fam, slug, weights in (("IBM Plex Sans", "sans", WEIGHTS_SANS),
                               ("IBM Plex Mono", "mono", WEIGHTS_MONO)):
        for w in weights:
            data = base64.b64encode((FONTS_DIR / f"plex-{slug}-{w}.woff2").read_bytes())
            out.append(f'@font-face{{font-family:"{fam}";src:url("data:font/woff2;base64,'
                       f'{data.decode()}") format("woff2");font-weight:{w}}}')
    return "".join(out)

# ----------------------------------------------------------------- spacing
STEP_1, STEP_2, STEP_3, STEP_4, STEP_6, STEP_8 = 8, 16, 24, 32, 48, 64
SAFE_16_9 = 96                       # title-safe inset on every 1920x1080 edge
SAFE_9_16_X = 72                     # side inset on a 1080x1920 Short
SAFE_9_16_TOP = 220
SAFE_9_16_BOTTOM = 420
ENDSCREEN_RESERVE = 640

# ------------------------------------------------------------------ radius
RADIUS_CHIP, RADIUS_CARD, RADIUS_RING = 8, 16, 999

# ---------------------------------------------------------------- duration
CROSSFADE_MS = 233
CURSOR_REVEAL_MS = 200
TITLE_RISE_MS = 300
FOCUS_ZOOM_MS = 367
TRACE_MS = 500
HOLD_TAG_MS = 3000
STING_MS = 1000


def frames(ms: int) -> int:
    """A duration token at FPS, rounded to the nearest frame."""
    return round(ms * FPS / 1000)


# The counts the tokens' notes state at 30 fps (the test checks both).
CROSSFADE_FRAMES = frames(CROSSFADE_MS)          # 7
CURSOR_REVEAL_FRAMES = frames(CURSOR_REVEAL_MS)  # 6
TITLE_RISE_FRAMES = frames(TITLE_RISE_MS)        # 9
FOCUS_ZOOM_FRAMES = frames(FOCUS_ZOOM_MS)        # 11
TRACE_FRAMES = frames(TRACE_MS)                  # 15
HOLD_TAG_FRAMES = frames(HOLD_TAG_MS)            # 90
STING_FRAMES = frames(STING_MS)                  # 30
TITLE_RISE_PX = 16                               # "Title fade + 16px rise"
FOCUS_ZOOM_SCALE = 1.15                          # "100->115%"

# ------------------------------------------------------------------ easing
SETTLE = "cubic-bezier(0.2, 0, 0, 1)"
DRAW = "cubic-bezier(0.4, 0, 0.2, 1)"

# Captions (README "Layout and safe areas"): paper on a navy plate at 80%.
CAPTION_PLATE_ALPHA = 0.8
