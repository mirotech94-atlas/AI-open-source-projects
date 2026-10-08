"""Narration for the video lessons: one Fish Audio mp3 per segment, in the
reels' voice (REELS_FISH_VOICE_ID). Runs where the key lives (the api
container); the Mac never sees it. Skips an mp3 that already exists.

  python tools/lesson_video/tts.py <out dir> [ids...]
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

import httpx

from atlas.modules._fish import fish_request

# A one-word line is a real clip of a few hundred bytes; only an empty or
# near-empty body is a failure.
MIN_BYTES = 200


def main(argv: list[str]) -> None:
    out = Path(argv[0])
    ids = argv[1:] or sorted(p.name for p in out.iterdir() if (p / "segments.json").exists())
    voice = os.getenv("REELS_FISH_VOICE_ID", "").strip() or None
    with httpx.Client(timeout=120) as c:
        for lid in ids:
            d = out / lid
            segs = json.loads((d / "segments.json").read_text())
            (d / "audio").mkdir(exist_ok=True)
            for n, seg in enumerate(segs, 1):
                mp3 = d / "audio" / f"{n:03d}.mp3"
                if mp3.exists() and mp3.stat().st_size > MIN_BYTES:
                    continue
                url, headers, body = fish_request(seg["say"], voice)
                for attempt in range(3):
                    r = c.post(url, headers=headers, json=body)
                    if r.status_code == 200 and len(r.content) > MIN_BYTES:
                        mp3.write_bytes(r.content)
                        break
                    time.sleep(2 + attempt * 3)
                else:
                    raise SystemExit(f"{lid} segment {n}: Fish answered {r.status_code}")
            print(f"narrated {lid}: {len(segs)} segments")


if __name__ == "__main__":
    main(sys.argv[1:])
