"""A synthetic "talking" webcam clip to exercise process.py end to end.

    ~/.cache/atlas-presenter/venv/bin/python tools/presenter/synthetic.py OUT.mov

Six seconds at 1620x1080 30 fps, like Miro's webcam: a coloured head-and-
shoulders silhouette swaying a little in front of a grey room, over a
hummed tone that starts after 0.8 s of quiet and stops 0.9 s before the end,
with a pause in the middle (so the trim and a --cut have something to find).
No photo of anyone: shapes and a sine wave only.
"""
from __future__ import annotations

import math
import subprocess
import sys
from pathlib import Path

W, H, FPS, SECONDS = 1620, 1080, 30, 6.0
RATE = 44100


def frames():
    import numpy as np
    yy, xx = np.mgrid[0:H, 0:W].astype(np.float32)
    room = np.empty((H, W, 3), dtype=np.uint8)
    room[...] = (58, 60, 64)
    room[int(H * 0.62):] = (44, 46, 50)          # a desk edge
    for i in range(int(SECONDS * FPS)):
        t = i / FPS
        cx = W * 0.62 + 30 * math.sin(t * 1.3)
        head = ((xx - cx) / 150) ** 2 + ((yy - 430 - 6 * math.sin(t * 5)) / 190) ** 2 <= 1
        body = ((xx - cx) / 360) ** 2 + ((yy - 1180) / 420) ** 2 <= 1
        neck = (abs(xx - cx) < 70) & (yy > 560) & (yy < 800)
        img = room.copy()
        img[body | neck] = (40, 96, 72)
        img[head] = (214, 170, 140)
        yield img.tobytes()


def main(out: str) -> int:
    import numpy as np
    n = int(SECONDS * RATE)
    t = np.arange(n) / RATE
    voice = (0.25 * np.sin(2 * math.pi * 180 * t) * (0.6 + 0.4 * np.sin(2 * math.pi * 3 * t)))
    on = ((t > 0.8) & (t < 2.7)) | ((t > 3.3) & (t < SECONDS - 0.9))
    audio = (voice * on + 0.0005 * np.random.default_rng(1).standard_normal(n))
    wav = Path(out).with_suffix(".tone.f32")
    wav.write_bytes(audio.astype("<f4").tobytes())
    enc = subprocess.Popen(["ffmpeg", "-y", "-v", "error", "-f", "rawvideo", "-pix_fmt", "rgb24",
                            "-s", f"{W}x{H}", "-r", str(FPS), "-i", "-", "-f", "f32le",
                            "-ar", str(RATE), "-ac", "1", "-i", str(wav), "-c:v", "libx264",
                            "-crf", "18", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest",
                            out], stdin=subprocess.PIPE)
    assert enc.stdin is not None
    for f in frames():
        enc.stdin.write(f)
    enc.stdin.close()
    code = enc.wait()
    wav.unlink(missing_ok=True)
    return code


if __name__ == "__main__":
    sys.exit(main(sys.argv[1]))
