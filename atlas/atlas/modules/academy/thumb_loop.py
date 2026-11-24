"""Animated thumbnails: a still thumbnail (the drawn ATLAS card or a
NotebookLM-made image) becomes a short silent looping MP4 and animated WebP
for the store and the agency portal. YouTube still gets the static PNG.

Only ffmpeg touches the bytes (no image library under atlas/). Pure, no
outside writes: it reads one image and writes two files next to `out_stem`.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

W, H = 640, 360
SECONDS = 4
FPS = 24
MAX_INPUT_BYTES = 8 * 1024 * 1024
DEMUXER = {"png": "png_pipe", "jpeg": "jpeg_pipe"}


def _kind(src: Path) -> str:
    """Image kind by its first bytes; every input is untrusted (invariant 4)."""
    with src.open("rb") as fh:
        head = fh.read(8)
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if head.startswith(b"\xff\xd8\xff"):
        return "jpeg"
    raise ValueError("thumbnail source is not a PNG or JPEG")


def _filter() -> str:
    frames = SECONDS * FPS
    # Slow zoom 1.00 -> 1.06 -> 1.00 (a full cycle, so the loop has no seam),
    # then a soft brightness breathe on the same cycle.
    zoom = f"1+0.06*sin(PI*on/{frames})"
    return (
        f"scale=1280:720,zoompan=z='{zoom}':d={frames}:s={W}x{H}:fps={FPS}"
        f":x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)',"
        f"eq=brightness='0.02*sin(2*PI*n/{frames})'"
    )


def animate(src: str | Path, out_stem: str | Path) -> tuple[Path, Path]:
    """Write `<out_stem>.mp4` and `<out_stem>.webp`; return both paths."""
    src, stem = Path(src), Path(out_stem)
    if not src.is_file() or src.stat().st_size > MAX_INPUT_BYTES:
        raise ValueError("thumbnail source is missing or too large")
    kind = _kind(src)
    mp4, webp = stem.with_suffix(".mp4"), stem.with_suffix(".webp")
    base = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
            "-protocol_whitelist", "file", "-f", DEMUXER[kind], "-loop", "1",
            "-i", str(src), "-t", str(SECONDS), "-vf", _filter(), "-an"]
    jobs = [
        base + ["-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", "28",
                "-movflags", "+faststart", str(mp4)],
        base + ["-c:v", "libwebp", "-lossless", "0", "-quality", "70",
                "-loop", "0", str(webp)],
    ]
    for argv in jobs:
        done = subprocess.run(argv, capture_output=True, text=True, timeout=120)  # noqa: PLW1510
        if done.returncode != 0:
            raise RuntimeError(f"ffmpeg: {done.stderr.strip()[:200]}")
    return mp4, webp
