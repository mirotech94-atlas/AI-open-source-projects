"""Miro on camera: the presenter clips that open and close every tutorial.

Miro, 2026-10-13: "put me on camera in the intro and the ending, so viewers
know a real human is behind the channel". Miro records the clips on a webcam;
tools/presenter/process.py (run on the Mac: the server has one CPU and never
mattes) takes out the background, sets Miro on the ATLAS set, evens the
loudness to the narration's and writes the finished clips here with a
manifest. Nothing else touches the face or the voice.

Slots (Miro, 2026-10-13, via the coordinator): ONE intro clip opens every
episode, the channel welcome and every lesson alike; the narrated part after
it begins "Today we'll be learning about ...". The ending is his whole outro
take, uncut. 2026-11-14: the recorded outro names an address that no longer
exists, so it is retired (its manifest entry is gone and the narrated ending is
used) until he records it again with the line below.

A slot is used only when its file is on disk, its sha256 is the manifest's
and the manifest's line is LINES[slot] word for word: the captions and the
transcript say LINES, so a clip whose words are not the code's is not used.
A missing or mismatched slot falls back to the narrated version, so nothing
breaks before (or after) the files arrive.

Standard library only: process.py loads this file by path from its own venv.
"""
from __future__ import annotations

import functools
import hashlib
import json
import os
from dataclasses import dataclass
from pathlib import Path

DIR_DEFAULT = Path(__file__).resolve().parent / "assets" / "presenter"
MANIFEST = "manifest.json"

# What Miro says in each clip, word for word (his own wording, 2026-10-13).
# The captions and the transcript carry these.
LINES: dict[str, str] = {
    "intro": ("Welcome back, guys, to my new channel! My name is Myron. "
              "Welcome to ATLAS Academy."),
    "outro": ("Thank you guys for watching, make sure to subscribe, like and comment "
              "what video you would like to see next. You can also check out "
              "agency.storefront.test, which has all the courses needed and videos, these "
              "videos posted as well. And yeah, I'll see you in the next one."),
}
OPENING = "intro"                    # every episode opens with this slot
ENDING = "outro"                     # and ends with this one
SLOTS = tuple(LINES)


@dataclass(frozen=True)
class Clip:
    slot: str
    path: Path
    sha256: str
    seconds: float
    said: str


def directory() -> Path:
    """ACADEMY_PRESENTER_DIR, or the committed assets/presenter."""
    raw = os.getenv("ACADEMY_PRESENTER_DIR", "").strip()
    return Path(raw) if raw else DIR_DEFAULT


def read_manifest(d: Path | None = None) -> dict:
    try:
        raw = json.loads(((d or directory()) / MANIFEST).read_text())
    except (OSError, ValueError):
        return {}
    clips = raw.get("clips") if isinstance(raw, dict) else None
    return clips if isinstance(clips, dict) else {}


def file_sha256(path: Path) -> str:
    """The file's sha256, remembered per (path, size, mtime): a stat, not a
    re-read, on every later call."""
    st = path.stat()
    return _sha(str(path), st.st_size, st.st_mtime_ns)


@functools.lru_cache(maxsize=32)
def _sha(path: str, _size: int, _mtime: int) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def clip(slot: str) -> Clip | None:
    """The finished clip for `slot`, or None (the narrated fallback)."""
    if slot not in LINES:
        return None
    d = directory()
    entry = read_manifest(d).get(slot)
    if not isinstance(entry, dict):
        return None
    name = str(entry.get("file") or "")
    if not name or "/" in name or name.startswith("."):
        return None
    path = d / name
    try:
        if path.is_symlink() or not path.is_file():
            return None
        sha = file_sha256(path)
        seconds = float(entry.get("seconds") or 0)
    except (OSError, TypeError, ValueError):
        return None
    if sha != entry.get("sha256") or entry.get("said") != LINES[slot] or seconds <= 0:
        return None
    return Clip(slot, path, sha, seconds, LINES[slot])


def stamp(*slots: str) -> str:
    """What the given slots' clips are, for the script and cache keys: ""
    when none of them is present, so a render without clips keys exactly as
    it did before the presenter existed."""
    got = [c for c in (clip(s) for s in slots) if c is not None]
    if not got:
        return ""
    raw = "|".join(f"{c.slot}:{c.sha256}:{c.said}" for c in got)
    return hashlib.sha256(raw.encode()).hexdigest()[:16]
