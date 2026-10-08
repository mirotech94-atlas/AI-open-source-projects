"""Draw ATLAS's face: a particle bust, in pure Python, committed as SVG and PNG.

Miro's ask: "when he calls me the contact photo should show ATLAS or the
humanoid." A phone shows a contact photo, so ATLAS needs one — and a picture
that arrives as a binary blob nobody can regenerate is a dependency, not an
asset. This file is the source; `ui/public/atlas-avatar.svg` and
`ui/public/atlas-avatar.png` are its output, committed so the running process
never has to render anything.

    python tools/render_avatar.py        rewrites both files

The PNG is what a phone actually shows (iOS Contacts will not take an SVG
photo), and it is rasterised here too, by hand: the picture is nothing but
filled discs on black, so a disc painter and a PNG writer (zlib + struct,
8-bit RGB, 512x512) are the whole of it. No cairo, no PIL — nothing on the
box that can be missing.

No PIL, no numpy, no fonts: a point cloud sampled on the surface of a head,
a neck and a pair of shoulders, projected straight down the z axis, with
depth carried by radius and opacity so the silhouette reads as a volume
rather than a stencil. Cyan throughout, with an amber cluster over the face,
on black.

It is deterministic on purpose — same maths, same jitter seed, same file
every time — and `tests/test_selfmodel.py` regenerates it and compares, so
the committed files can never drift away from the code that made them.
"""
from __future__ import annotations

import math
import pathlib
import random
import struct
import zlib

WIDTH = HEIGHT = 1024

CYAN = "#39d6ff"
AMBER = "#ffb347"
BACKGROUND = "#000000"

SEED = 20260924

# The head, as an ellipsoid, in canvas pixels.
HEAD_X, HEAD_Y = 512.0, 372.0
HEAD_A, HEAD_B, HEAD_C = 168.0, 202.0, 168.0
HEAD_POINTS = 3000

# The neck, as a cylinder sunk into the head above and the shoulders below.
NECK_TOP, NECK_BOTTOM = 540.0, 716.0
NECK_R = 66.0
NECK_POINTS = 600

# The shoulders, as the upper cap of a wide, shallow ellipsoid whose centre
# sits off the bottom of the canvas.
BODY_X, BODY_Y = 512.0, 1010.0
BODY_A, BODY_B, BODY_C = 440.0, 300.0, 226.0
BODY_SAMPLES = 5600          # oversampled; only the upper cap is kept
BODY_CUT = -46.0             # keep points at least this far above the centre

# Depth bands, darkest (furthest) first. A point's band is picked from its
# z, so the back of the head reads as the back of the head.
BANDS = (0.26, 0.46, 0.7, 1.0)

JITTER = 2.2                 # px, so the cloud does not read as a grid

PNG_SIZE = 512               # plenty for a contact photo
MIN_DISC = 0.75              # px at PNG scale, so the smallest point still lands


def _fibonacci_sphere(count: int) -> list[tuple[float, float, float]]:
    """Evenly spread unit vectors. No RNG: the spiral is the whole point."""
    golden = math.pi * (3.0 - math.sqrt(5.0))
    out: list[tuple[float, float, float]] = []
    for i in range(count):
        y = 1.0 - (2.0 * i) / max(1, count - 1)
        radius = math.sqrt(max(0.0, 1.0 - y * y))
        theta = golden * i
        out.append((math.cos(theta) * radius, y, math.sin(theta) * radius))
    return out


def _is_face(x: float, y: float, z: float) -> bool:
    """The front oval of the head, where a face would be. Local units, each
    axis normalised to its own radius; an ellipse rather than a box, so the
    amber reads as a face and not as a screen."""
    return z > 0.3 and (x / 0.44) ** 2 + ((y + 0.08) / 0.5) ** 2 < 1.0


def _jaw(y: float) -> float:
    """Horizontal scale for a point on the head: the lower half narrows
    towards the chin, which is most of what separates a head from a ball."""
    return 1.0 - 0.22 * max(0.0, -y) ** 2


def _points() -> list[tuple[float, float, float, str]]:
    """(screen x, screen y, depth 0..1, colour) for every point in the bust."""
    rng = random.Random(SEED)
    out: list[tuple[float, float, float, str]] = []

    def add(px: float, py: float, depth: float, colour: str) -> None:
        out.append((px + rng.uniform(-JITTER, JITTER),
                    py + rng.uniform(-JITTER, JITTER),
                    depth, colour))

    for x, y, z in _fibonacci_sphere(HEAD_POINTS):
        colour = AMBER if _is_face(x, y, z) else CYAN
        add(HEAD_X + x * HEAD_A * _jaw(y), HEAD_Y - y * HEAD_B,
            (z + 1.0) / 2.0, colour)

    for i in range(NECK_POINTS):
        # A cylinder shell: the angle walks the golden ratio so successive
        # rings do not line up into stripes.
        theta = 2.0 * math.pi * ((i * 0.6180339887498949) % 1.0)
        t = i / max(1, NECK_POINTS - 1)
        x, z = math.cos(theta), math.sin(theta)
        add(HEAD_X + x * NECK_R, NECK_TOP + t * (NECK_BOTTOM - NECK_TOP),
            (z + 1.0) / 2.0, CYAN)

    for x, y, z in _fibonacci_sphere(BODY_SAMPLES):
        if y * BODY_B < BODY_CUT:
            continue                     # the lower half is off the canvas
        py = BODY_Y - y * BODY_B
        if py > HEIGHT + 8:
            continue
        add(BODY_X + x * BODY_A, py, (z + 1.0) / 2.0, CYAN)

    return out


def _band(depth: float) -> int:
    return min(len(BANDS) - 1, max(0, int(depth * len(BANDS))))


def _radius(depth: float) -> float:
    return round(0.9 + 1.3 * depth, 1)


def _layers() -> list[tuple[int, str, list[tuple[float, float, float]]]]:
    """(band, colour, [(x, y, r)]) back to front, cyan before amber inside a
    band, so the face sits on top of the head it belongs to. Coordinates are
    rounded exactly as the SVG writes them, so both files draw one picture."""
    grouped: dict[tuple[str, int], list[tuple[float, float, float]]] = {}
    for px, py, depth, colour in _points():
        grouped.setdefault((colour, _band(depth)), []).append(
            (round(px, 1), round(py, 1), _radius(depth)))
    return [(band, colour, grouped[(colour, band)])
            for band in range(len(BANDS)) for colour in (CYAN, AMBER)
            if grouped.get((colour, band))]


def render() -> str:
    """The whole SVG, as text. Pure: same input, same bytes, every time."""

    lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        "<!-- Generated by tools/render_avatar.py. Do not edit by hand. -->",
        (f'<svg xmlns="http://www.w3.org/2000/svg" width="{WIDTH}" '
         f'height="{HEIGHT}" viewBox="0 0 {WIDTH} {HEIGHT}">'),
        f'<rect width="{WIDTH}" height="{HEIGHT}" fill="{BACKGROUND}"/>',
    ]
    for band, colour, discs in _layers():
        lines.append(f'<g fill="{colour}" opacity="{BANDS[band]}">')
        lines.extend(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="{r:.1f}"/>'
                     for x, y, r in discs)
        lines.append("</g>")
    lines.append("</svg>")
    return "\n".join(lines) + "\n"


def _rgb(colour: str, opacity: float) -> bytes:
    """The colour at this opacity over black, which is all the SVG's
    `opacity` amounts to on this background."""
    return bytes(round(int(colour[i:i + 2], 16) * opacity) for i in (1, 3, 5))


def _chunk(kind: bytes, body: bytes) -> bytes:
    return (struct.pack(">I", len(body)) + kind + body
            + struct.pack(">I", zlib.crc32(kind + body) & 0xFFFFFFFF))


def render_png(size: int = PNG_SIZE) -> bytes:
    """The same bust as a `size` x `size` 8-bit RGB PNG. Each point is a
    filled disc of its own radius, scaled; no anti-aliasing. Pure: the same
    pixels every time (the compressed bytes depend on the zlib build)."""
    scale = size / WIDTH
    pixels = bytearray(size * size * 3)
    for band, colour, discs in _layers():
        rgb = _rgb(colour, BANDS[band])
        for x, y, r in discs:
            cx, cy = x * scale, y * scale
            rr = max(r * scale, MIN_DISC)
            for iy in range(max(0, int(cy - rr)), min(size, int(cy + rr) + 1)):
                dy = iy + 0.5 - cy
                for ix in range(max(0, int(cx - rr)),
                                min(size, int(cx + rr) + 1)):
                    dx = ix + 0.5 - cx
                    if dx * dx + dy * dy <= rr * rr:
                        at = (iy * size + ix) * 3
                        pixels[at:at + 3] = rgb
    stride = size * 3
    raw = b"".join(b"\x00" + bytes(pixels[y * stride:(y + 1) * stride])
                   for y in range(size))
    return (b"\x89PNG\r\n\x1a\n"
            + _chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 2, 0, 0, 0))
            + _chunk(b"IDAT", zlib.compress(raw, 9))
            + _chunk(b"IEND", b""))


def main() -> None:
    public = pathlib.Path(__file__).resolve().parent.parent / "ui" / "public"
    svg, png = public / "atlas-avatar.svg", public / "atlas-avatar.png"
    svg.write_text(render())
    png.write_bytes(render_png())
    for out in (svg, png):
        print(f"wrote {out} ({out.stat().st_size // 1024} KB)")


if __name__ == "__main__":
    main()
