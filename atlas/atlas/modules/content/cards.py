"""Typographic story cards, as PNGs, in pure Python.

The free render path's picture when there are no Gemini stills (no key, or
Gemini refused): a dark 1080x1920 vertical card with a thin accent bar. The
words themselves are drawn onto it by ffmpeg's `drawtext` from a text file
(`ffmpeg.stills_argv`'s `scene_text`), so no font is rasterised here and
nothing on the box needs PIL -- which is not installed.

The PNG encoder is the one `tools/render_avatar.py` writes the contact
photo with (zlib + struct, 8-bit RGB, one filter byte per row), lifted here
because `tools/` is a script directory the running package cannot import.
The fill is built by bytes multiplication, not a per-pixel loop, and callers
run `card()` under `asyncio.to_thread`: 1080x1920 RGB is 6 MB of rows.
"""
from __future__ import annotations

import struct
import textwrap
import zlib

WIDTH, HEIGHT = 1080, 1920
BACKGROUND = (11, 13, 18)
ACCENT = (57, 214, 255)             # the avatar's cyan
ACCENT_Y, ACCENT_H = 360, 10
MARGIN = 120
WRAP = 20                            # characters per line at drawtext size 64


def _chunk(kind: bytes, body: bytes) -> bytes:
    return (struct.pack(">I", len(body)) + kind + body
            + struct.pack(">I", zlib.crc32(kind + body) & 0xFFFFFFFF))


def png(width: int, height: int, rows: list[bytes]) -> bytes:
    """An 8-bit RGB PNG from `height` rows of `width * 3` bytes each."""
    raw = b"".join(b"\x00" + row for row in rows)
    return (b"\x89PNG\r\n\x1a\n"
            + _chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
            + _chunk(b"IDAT", zlib.compress(raw, 6))
            + _chunk(b"IEND", b""))


def card(width: int = WIDTH, height: int = HEIGHT) -> bytes:
    """A dark card with a short accent bar near the top."""
    plain = bytes(BACKGROUND) * width
    bar_w = max(1, width // 6)
    left = (width - bar_w) // 2
    bar = (bytes(BACKGROUND) * left + bytes(ACCENT) * bar_w
           + bytes(BACKGROUND) * (width - left - bar_w))
    top = ACCENT_Y * height // HEIGHT
    tall = max(1, ACCENT_H * height // HEIGHT)
    rows = [bar if top <= y < top + tall else plain for y in range(height)]
    return png(width, height, rows)


def wrapped(text: str, width: int = WRAP) -> str:
    """A scene's words, broken into lines drawtext draws one under another."""
    return "\n".join(textwrap.wrap(" ".join(text.split()), width)) or " "
