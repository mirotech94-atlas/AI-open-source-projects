"""Replace one span of a clip's audio with pieces of the same clip's audio.

    ~/.cache/atlas-presenter/venv/bin/python tools/presenter/splice.py \\
        presenter-inbox/outro.mov presenter-inbox/outro-io.mov \\
        --replace 9.62:10.10 --with 9.85:9.98 --with '1.16:1.26*2.5' \\
        --with 6.80:6.90

(the committed outro, 2026-10-13: his "I" from 9.85 s, the vowel of his
"for" from 1.16 s stretched 2.5x, room tone from the pause at 6.8 s.
The alternate: --with 9.85:10.02 --with '13.61:13.70*2.5' --with 6.80:6.885,
the vowel of "posted".)

Miro, 2026-10-13: in outro.mov he said "shop.mapfront.ai" by mistake and
asked for the one word to say ".io", from his own real sounds, the video
untouched. So the span of "A I" is replaced by his own "I" followed by an
"O" taken from elsewhere in the same take: each piece level-matched to the
span it replaces, joined with short equal-power crossfades, the room tone
under the gap kept continuous. Nothing is synthesised and nothing else
changes; the video stream is copied bit for bit.

The result is a new source file for process.py (PCM audio, so nothing is
lost before the final encode).
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

RATE = 44100
FADE = 0.012          # seconds of equal-power crossfade at every join


def span(text: str) -> tuple[float, float]:
    a, _, b = text.partition(":")
    lo, hi = float(a), float(b)
    if not 0 <= lo < hi:
        raise argparse.ArgumentTypeError(f"a span is START:END seconds, not {text!r}")
    return lo, hi


def piece(text: str) -> tuple[float, float, float]:
    """START:END, or START:END*STRETCH (the piece played STRETCH times as
    long, its pitch unchanged)."""
    body, _, k = text.partition("*")
    lo, hi = span(body)
    stretch = float(k) if k else 1.0
    if not 0.5 <= stretch <= 2.5:
        raise argparse.ArgumentTypeError(f"a stretch is 0.5-2.5, not {k!r}")
    return lo, hi, stretch


def stretched(seg, factor: float):
    """`seg` played `factor` times as long at the same pitch (ffmpeg atempo,
    WSOLA: whole waveform periods repeated, nothing synthesised)."""
    import numpy as np
    if abs(factor - 1.0) < 1e-6:
        return seg
    tempo = (1 / factor) ** 0.5           # two stages: atempo takes 0.5-2 each
    out = subprocess.run(["ffmpeg", "-v", "error", "-f", "f64le", "-ar", str(RATE), "-ac", "1",
                          "-i", "-", "-af", f"atempo={tempo:.6f},atempo={tempo:.6f}",
                          "-f", "f64le", "-"],
                         input=np.asarray(seg, dtype="<f8").tobytes(),
                         capture_output=True, check=True).stdout
    return np.frombuffer(out, dtype="<f8").copy()


def read(path: Path):
    import numpy as np
    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", str(path), "-vn", "-ac", "1",
                          "-ar", str(RATE), "-f", "f32le", "-"],
                         capture_output=True, check=True).stdout
    return np.frombuffer(raw, dtype="<f4").astype("float64")


def splice(x, replace: tuple[float, float], pieces: list[tuple]):
    """`x` with `replace` swapped for `pieces` of `x` (START, END[, STRETCH]),
    in order. The total length changes only by the pieces' length (as
    played) minus the span's: keep them equal and the lips stay in sync."""
    import numpy as np
    f = int(FADE * RATE)
    a, b = (int(t * RATE) for t in replace)
    ref = np.sqrt(np.mean(x[a:b] ** 2)) + 1e-12
    parts = []
    for lo, hi, *k in pieces:
        seg = stretched(x[int(lo * RATE) - f:int(hi * RATE) + f], k[0] if k else 1.0)
        want = round((hi - lo) * (k[0] if k else 1.0) * RATE) + 2 * f
        seg = np.pad(seg, (0, max(0, want - len(seg))))[:want].copy()
        gain = ref / (np.sqrt(np.mean(seg[f:-f] ** 2)) + 1e-12)
        # a voiced piece is evened to the span's level (within +-3 dB); a
        # quiet one is room tone and keeps its own
        seg *= 1.0 if gain > 3.0 else min(1.41, max(0.71, gain))
        parts.append(seg)
    t = np.linspace(0, np.pi / 2, 2 * f)
    fade_in, fade_out = np.sin(t), np.cos(t)
    out = x[:a + f].copy()
    for seg in [*parts, x[b - f:]]:
        head = out[-2 * f:] * fade_out + seg[:2 * f] * fade_in
        out = np.concatenate([out[:-2 * f], head, seg[2 * f:]])
    return out


def write(src: Path, audio, out: Path) -> None:
    import numpy as np
    pcm = out.with_suffix(".splice.f32")
    pcm.write_bytes(np.clip(audio, -1, 1).astype("<f4").tobytes())
    try:
        subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", str(src), "-f", "f32le",
                        "-ar", str(RATE), "-ac", "1", "-i", str(pcm), "-map", "0:v",
                        "-map", "1:a", "-c:v", "copy", "-c:a", "pcm_s16le", str(out)],
                       check=True)
    finally:
        pcm.unlink(missing_ok=True)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("source", type=Path)
    ap.add_argument("out", type=Path)
    ap.add_argument("--replace", type=span, required=True)
    ap.add_argument("--with", dest="pieces", type=piece, action="append", required=True)
    args = ap.parse_args(argv)
    write(args.source, splice(read(args.source), args.replace, args.pieces), args.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
