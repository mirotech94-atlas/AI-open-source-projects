"""The social share image (item 13 of the launch list): a 1200x630 PNG drawn
from the site's own name, tagline and colours when the client gave none.

A rendered graphic, never a photo: flat colour, a band, and the business's
name set in a blocky pixel face. Nothing here depends on Pillow (not a
dependency of this project, and adding one is Miro's call), so the PNG is
written with `zlib` + `struct` and the letters come from a 5x7 bitmap font
scaled up. Uppercase only; an accented letter is shown without its accent
and anything the font has no glyph for becomes a space rather than a
guessed shape. Deterministic: the same inputs always give the same bytes,
which is what lets a rebuild tell "ATLAS's image, unchanged" from "one the
client replaced".
"""
from __future__ import annotations

import struct
import unicodedata
import zlib

WIDTH, HEIGHT = 1200, 630

# 5x7 glyphs: seven rows of five pixels per character.
_FONT_ROWS: dict[str, tuple[str, ...]] = {
    "A": ("01110", "10001", "10001", "11111", "10001", "10001", "10001"),
    "B": ("11110", "10001", "10001", "11110", "10001", "10001", "11110"),
    "C": ("01110", "10001", "10000", "10000", "10000", "10001", "01110"),
    "D": ("11110", "10001", "10001", "10001", "10001", "10001", "11110"),
    "E": ("11111", "10000", "10000", "11110", "10000", "10000", "11111"),
    "F": ("11111", "10000", "10000", "11110", "10000", "10000", "10000"),
    "G": ("01110", "10001", "10000", "10111", "10001", "10001", "01111"),
    "H": ("10001", "10001", "10001", "11111", "10001", "10001", "10001"),
    "I": ("01110", "00100", "00100", "00100", "00100", "00100", "01110"),
    "J": ("00111", "00010", "00010", "00010", "00010", "10010", "01100"),
    "K": ("10001", "10010", "10100", "11000", "10100", "10010", "10001"),
    "L": ("10000", "10000", "10000", "10000", "10000", "10000", "11111"),
    "M": ("10001", "11011", "10101", "10101", "10001", "10001", "10001"),
    "N": ("10001", "10001", "11001", "10101", "10011", "10001", "10001"),
    "O": ("01110", "10001", "10001", "10001", "10001", "10001", "01110"),
    "P": ("11110", "10001", "10001", "11110", "10000", "10000", "10000"),
    "Q": ("01110", "10001", "10001", "10001", "10101", "10010", "01101"),
    "R": ("11110", "10001", "10001", "11110", "10100", "10010", "10001"),
    "S": ("01111", "10000", "10000", "01110", "00001", "00001", "11110"),
    "T": ("11111", "00100", "00100", "00100", "00100", "00100", "00100"),
    "U": ("10001", "10001", "10001", "10001", "10001", "10001", "01110"),
    "V": ("10001", "10001", "10001", "10001", "10001", "01010", "00100"),
    "W": ("10001", "10001", "10001", "10101", "10101", "10101", "01010"),
    "X": ("10001", "10001", "01010", "00100", "01010", "10001", "10001"),
    "Y": ("10001", "10001", "01010", "00100", "00100", "00100", "00100"),
    "Z": ("11111", "00001", "00010", "00100", "01000", "10000", "11111"),
    "0": ("01110", "10001", "10011", "10101", "11001", "10001", "01110"),
    "1": ("00100", "01100", "00100", "00100", "00100", "00100", "01110"),
    "2": ("01110", "10001", "00001", "00010", "00100", "01000", "11111"),
    "3": ("11111", "00010", "00100", "00010", "00001", "10001", "01110"),
    "4": ("00010", "00110", "01010", "10010", "11111", "00010", "00010"),
    "5": ("11111", "10000", "11110", "00001", "00001", "10001", "01110"),
    "6": ("00110", "01000", "10000", "11110", "10001", "10001", "01110"),
    "7": ("11111", "00001", "00010", "00100", "01000", "01000", "01000"),
    "8": ("01110", "10001", "10001", "01110", "10001", "10001", "01110"),
    "9": ("01110", "10001", "10001", "01111", "00001", "00010", "01100"),
    " ": ("00000",) * 7,
    ".": ("00000", "00000", "00000", "00000", "00000", "01100", "01100"),
    ",": ("00000", "00000", "00000", "00000", "01100", "00100", "01000"),
    "'": ("01100", "00100", "01000", "00000", "00000", "00000", "00000"),
    "-": ("00000", "00000", "00000", "11111", "00000", "00000", "00000"),
    "&": ("01100", "10010", "10100", "01000", "10101", "10010", "01101"),
    "!": ("00100", "00100", "00100", "00100", "00100", "00000", "00100"),
    "?": ("01110", "10001", "00001", "00010", "00100", "00000", "00100"),
    ":": ("00000", "01100", "01100", "00000", "01100", "01100", "00000"),
    "/": ("00000", "00001", "00010", "00100", "01000", "10000", "00000"),
    "(": ("00010", "00100", "01000", "01000", "01000", "00100", "00010"),
    ")": ("01000", "00100", "00010", "00010", "00010", "00100", "01000"),
    "+": ("00000", "00100", "00100", "11111", "00100", "00100", "00000"),
    "#": ("01010", "01010", "11111", "01010", "11111", "01010", "01010"),
    "@": ("01110", "10001", "10111", "10101", "10111", "10000", "01111"),
}


def _plain(text: str) -> str:
    """Uppercase, accents removed, anything without a glyph as a space."""
    folded = unicodedata.normalize("NFKD", text or "")
    folded = "".join(c for c in folded if not unicodedata.combining(c)).upper()
    folded = folded.replace("’", "'").replace("–", "-").replace("—", "-")
    return " ".join("".join(c if c in _FONT_ROWS else " " for c in folded).split())


def _rgb(hex_colour: str) -> tuple[int, int, int]:
    h = hex_colour.lstrip("#")
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)


def _luminance(rgb: tuple[int, int, int]) -> float:
    def channel(c: int) -> float:
        v = c / 255
        return v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4
    r, g, b = (channel(c) for c in rgb)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def _contrast(a: tuple[int, int, int], b: tuple[int, int, int]) -> float:
    la, lb = sorted((_luminance(a), _luminance(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)


def _readable(fg: tuple[int, int, int], bg: tuple[int, int, int]) -> tuple[int, int, int]:
    """`fg` when it reads on `bg` (WCAG 4.5:1), else black or white."""
    if _contrast(fg, bg) >= 4.5:
        return fg
    return (0, 0, 0) if _contrast((0, 0, 0), bg) >= _contrast((255, 255, 255), bg) \
        else (255, 255, 255)


class _Canvas:
    def __init__(self, bg: tuple[int, int, int]) -> None:
        self.buf = bytearray(bytes(bg) * (WIDTH * HEIGHT))

    def rect(self, x: int, y: int, w: int, h: int, colour: tuple[int, int, int]) -> None:
        x0, x1 = max(0, x), min(WIDTH, x + w)
        if x1 <= x0:
            return
        run = bytes(colour) * (x1 - x0)
        for row in range(max(0, y), min(HEIGHT, y + h)):
            start = (row * WIDTH + x0) * 3
            self.buf[start:start + len(run)] = run

    def text(self, line: str, x: int, y: int, scale: int,
             colour: tuple[int, int, int]) -> None:
        for i, ch in enumerate(line):
            rows = _FONT_ROWS.get(ch, _FONT_ROWS[" "])
            gx = x + i * 6 * scale
            for ry, bits in enumerate(rows):
                for rx, bit in enumerate(bits):
                    if bit == "1":
                        self.rect(gx + rx * scale, y + ry * scale, scale, scale, colour)

    def png(self) -> bytes:
        stride = WIDTH * 3
        raw = b"".join(b"\x00" + bytes(self.buf[r * stride:(r + 1) * stride])
                       for r in range(HEIGHT))

        def chunk(kind: bytes, data: bytes) -> bytes:
            return (struct.pack(">I", len(data)) + kind + data
                    + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF))
        header = struct.pack(">IIBBBBB", WIDTH, HEIGHT, 8, 2, 0, 0, 0)
        return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header)
                + chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b""))


def _wrap(text: str, max_chars: int, max_lines: int) -> list[str]:
    lines: list[str] = []
    for word in text.split():
        word = word[:max_chars]
        if lines and len(lines[-1]) + 1 + len(word) <= max_chars:
            lines[-1] += " " + word
        else:
            lines.append(word)
    if len(lines) > max_lines:
        lines = lines[:max_lines]
        lines[-1] = lines[-1][: max_chars - 3].rstrip() + "..."
    return lines


def share_image(name: str, tagline: str | None, colours: dict[str, str]) -> bytes:
    """A 1200x630 PNG: background colour, a primary-colour band down the
    left and along the bottom, the name large and the tagline (when the
    client gave one) smaller underneath."""
    bg = _rgb(colours["background"])
    primary = _rgb(colours["primary"])
    ink = _readable(_rgb(colours["text"]), bg)
    accent = _readable(primary, bg)
    canvas = _Canvas(bg)
    canvas.rect(0, 0, 36, HEIGHT, primary)
    canvas.rect(0, HEIGHT - 36, WIDTH, 36, primary)

    left, usable = 100, WIDTH - 160
    title = _plain(name) or "WELCOME"
    # The largest size at which the whole name fits on two lines; only the
    # smallest size ever shortens it.
    for scale in (14, 12, 10, 8, 7, 6):
        lines = _wrap(title, usable // (6 * scale), 99)
        if len(lines) <= 2:
            break
    lines = _wrap(title, usable // (6 * scale), 2)
    y = 120
    for line in lines:
        canvas.text(line, left, y, scale, ink)
        y += 9 * scale
    canvas.rect(left, y + 10, 160, 10, accent)
    y += 60
    sub = _plain(tagline or "")
    if sub:
        for line in _wrap(sub, usable // (6 * 5), 3):
            if y + 7 * 5 > HEIGHT - 60:
                break
            canvas.text(line, left, y, 5, ink)
            y += 9 * 5
    return canvas.png()


def png_size(data: bytes) -> tuple[int, int] | None:
    """(width, height) from a PNG's IHDR, or None when `data` is not one."""
    if len(data) < 24 or data[:8] != b"\x89PNG\r\n\x1a\n" or data[12:16] != b"IHDR":
        return None
    return struct.unpack(">II", data[16:24])
