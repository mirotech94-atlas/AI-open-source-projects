"""Lesson recordings (Part D5): plan, record and list the real screen clips.

    python -m tools.lesson_recordings.record --plan   [--data DIR]
    python -m tools.lesson_recordings.record --record [--data DIR] [--out DIR]

Only segments that show a real command (a ``<pre>`` block) or a node-by-node
walk-through are recorded, and only against real local software through the
helpers in ``tools/tutorial_demos/record.py`` (imported, never edited). Claude
screens are animated diagrams, so those segments are listed under
``simulated`` in the manifest and no clip is made for them.

Manifest (data/academy-staging/recordings/manifest.json):
    {"clips": [{course, lesson, segment, path, focus: [[t, x, y, w, h]...],
                recorded_at, tool}],
     "simulated": [{course, lesson, segment, reason}]}
``clips`` holds only files that exist. Clips stay in staging, never in git.
Every field is checked for real names, keys and paths before it is written.
"""

from __future__ import annotations

import argparse
import contextlib
import importlib.util
import json
import os
import re
import sys
import tempfile
from pathlib import Path

SIMULATED_COURSES = {"adults", "seniors"}  # Claude screens: animated diagrams only
COURSE_DIRS = {
    "operator": "lesson-video",
    "adults": "lesson-video-adults",
    "seniors": "lesson-video-seniors",
    "sell": "lesson-video-sell",
}
SIM_REASON = "Claude screens are animated diagrams, no login"
CLIP_KEYS = ("course", "lesson", "segment", "path", "focus", "recorded_at", "tool")

_WALK = re.compile(r"^\s*(node\s*\d*\b|walk-?through\b)", re.IGNORECASE)
_PRE = re.compile(r"<pre[\s>]", re.IGNORECASE)
_SCRUB = [
    re.compile(r"/Users/[^/\s]+"),
    re.compile(r"/home/[^/\s]+"),
    re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+"),
    re.compile(r"\bsk-[A-Za-z0-9_-]{12,}"),
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}"),
    re.compile(r"\bAKIA[A-Z0-9]{12,}"),
    re.compile(r"\bBearer\s+[A-Za-z0-9._-]{10,}"),
]
_FAKE_MAIL = re.compile(
    r"@[\w-]+\.(test|invalid|example|localhost)$|@example\.(com|org|net)$", re.IGNORECASE
)


class ManifestError(ValueError):
    pass


class Blocked(RuntimeError):
    pass


def classify(seg: dict, course: str) -> str:
    """command | walkthrough | simulated | none. Inline <code> never counts."""
    if seg.get("kind") == "title":
        return "none"
    has_pre = bool(_PRE.search(seg.get("html", "")))
    if not (has_pre or _WALK.match(seg.get("heading", ""))):
        return "none"
    if course in SIMULATED_COURSES:
        return "simulated"
    return "command" if has_pre else "walkthrough"


def scrub_hits(text: str) -> list[str]:
    hits = []
    for rx in _SCRUB:
        for m in rx.finditer(text):
            s = m.group(0)
            if "@" in s and _FAKE_MAIL.search(s):
                continue
            hits.append(s)
    return hits


def _check_clip(c: dict) -> None:
    if list(c) != list(CLIP_KEYS):
        raise ManifestError(f"clip keys must be {CLIP_KEYS}, got {list(c)}")
    if not c["tool"]:
        raise ManifestError("clip has an empty tool")
    if not Path(c["path"]).is_file():
        raise ManifestError(f"clip file missing: {c['path']}")
    for f in c["focus"]:
        if not (isinstance(f, list) and len(f) == 5):
            raise ManifestError(f"focus must be [t,x,y,w,h]: {f}")
    for k in ("course", "lesson", "tool", "recorded_at"):
        if scrub_hits(str(c[k])):
            raise ManifestError(f"scrub hit in {k}: {c[k]}")


def write_manifest(out: Path, clips: list[dict], simulated: list[dict]) -> None:
    for c in clips:
        _check_clip(c)
    for s in simulated:
        if scrub_hits(json.dumps(s)):
            raise ManifestError(f"scrub hit in simulated entry: {s}")
    out.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=out.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as fh:
            json.dump({"clips": clips, "simulated": simulated}, fh, indent=1)
        Path(tmp).replace(out)
    except BaseException:
        with contextlib.suppress(OSError):
            Path(tmp).unlink()
        raise


def lesson_ids(d: Path) -> list[str]:
    return sorted(
        (p.name for p in d.iterdir() if (p / "segments.json").is_file()),
        key=lambda s: [int(x) if x.isdigit() else x for x in s.split(".")],
    )


def plan(data: Path, courses: dict[str, str]) -> tuple[list[dict], list[dict]]:
    real: list[dict] = []
    sim: list[dict] = []
    for course, sub in courses.items():
        base = data / sub
        if not base.is_dir():
            continue
        for lid in lesson_ids(base):
            segs = json.loads((base / lid / "segments.json").read_text())
            for i, s in enumerate(segs):
                kind = classify(s, course)
                if kind == "simulated":
                    sim.append(
                        {"course": course, "lesson": lid, "segment": i, "reason": SIM_REASON}
                    )
                elif kind != "none":
                    real.append({"course": course, "lesson": lid, "segment": i, "kind": kind})
    return real, sim


def playwright_available() -> bool:
    return importlib.util.find_spec("playwright") is not None


def require_recorder() -> None:
    if not playwright_available():
        raise Blocked("playwright is not installed here; run on the Mac recording venv")


def _record_all(real: list[dict], out: Path) -> list[dict]:
    """Real recording through tools/tutorial_demos/record.py (Capture, encode, n8n_up).

    Needs the Mac venv, the n8n image and the DEMO_HOME scratch folder described
    in that module. Nothing is faked: without them this raises Blocked.
    """
    raise Blocked("recording needs the n8n container and DEMO_HOME scratch folder, not set up")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="lesson_recordings")
    ap.add_argument("--plan", action="store_true")
    ap.add_argument("--record", action="store_true")
    ap.add_argument("--data", type=Path, default=Path("data"))
    ap.add_argument("--out", type=Path, default=Path("data/academy-staging/recordings"))
    ap.add_argument("--course", action="append", default=[], metavar="NAME=SUBDIR")
    a = ap.parse_args(argv)
    courses = dict(c.split("=", 1) for c in a.course) or COURSE_DIRS
    real, sim = plan(a.data, courses)
    if a.plan or not a.record:
        print(f"real {len(real)}, simulated {len(sim)}")
        return 0
    try:
        require_recorder()
        clips = _record_all(real, a.out)
    except Blocked as e:
        print(f"BLOCKED: {e}", file=sys.stderr)
        return 2
    write_manifest(a.out / "manifest.json", clips, sim)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
