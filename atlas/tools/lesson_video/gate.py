"""The gate a lesson video must pass before it replaces the stop-gap slide video.

    python tools/lesson_video/gate.py <lesson>.mp4 [--render render.json] [--out DIR]

Checks (Miro, 2026-11-14; final-plan 2.4):
  freeze    no still picture over 6 s between the sting and the end card
            (ffmpeg freezedetect on the picture area; the caption band is
            cropped off so a changing caption cannot hide a still picture)
  length    the file is within 2% of sting + audio + checkpoint pauses + end card
  loudness  integrated loudness like the tutorials (-19.5 LUFS +-1.5), true peak <= -1 dBTP
  captions  burned in: bright caption pixels in the caption band at sampled cue
            midpoints, and no soft subtitle stream standing in for them
  overflow  the render's own DOM overflow report is empty (missing report = fail)
  streams   1920x1080, 30 fps, h264 + aac

A check that cannot run is a FAIL, never a pass (CLAUDE.md invariant 7).
Writes `<lesson>.mp4.gate.json` as {"pass", "checks", "read_at"}; beside the
mp4 only when it has one name (a hard-linked live file is never touched), else
into --out.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

FFMPEG = shutil.which("ffmpeg") or "/Users/mac/.local/bin/ffmpeg"
FFPROBE = shutil.which("ffprobe") or "/Users/mac/.local/bin/ffprobe"

FREEZE_MAX = 6.0
STING = 1.0
END_CARD = 4.0
LUFS_REF = -19.5          # the tutorials (atlas/modules/academy/tutorial_music.py reference)
LUFS_TOL = 1.5
TP_MAX = -1.0
LEN_TOL = 0.02
FREEZE_NOISE = "-60dB"    # ffmpeg freezedetect's default: under 0.1% mean change reads as still
FREEZE_MIN = 2.0          # report stills of 2 s or more; only > FREEZE_MAX fails
CAP_Y, CAP_H = 824, 160   # the caption band; scenes keep it empty
CAP_BRIGHT = 190          # paper text on the dark plate reaches ~247


# ------------------------------------------------------------------- pure
def parse_freezedetect(log: str, total: float) -> list[tuple[float, float]]:
    """(start, end) of every still stretch. A start with no end runs to `total`."""
    out: list[tuple[float, float]] = []
    start = None
    for m in re.finditer(r"freeze_(start|end): ([0-9.]+)", log):
        v = float(m.group(2))
        if m.group(1) == "start":
            if start is not None:
                out.append((start, total))
            start = v
        elif start is not None:
            out.append((start, v))
            start = None
    if start is not None:
        out.append((start, total))
    return out


def check_freeze(freezes: list[tuple[float, float]] | None, *, total: float,
                 end_card: float = END_CARD, sting: float = STING) -> dict:
    if freezes is None:
        return {"pass": False, "reason": "freezedetect did not run"}
    lo, hi = sting, total - end_card
    clipped = [(max(a, lo), min(b, hi)) for a, b in freezes if min(b, hi) > max(a, lo)]
    longest = max((b - a for a, b in clipped), default=0.0)
    worst = max(clipped, key=lambda x: x[1] - x[0], default=None)
    return {"pass": longest <= FREEZE_MAX, "longest": round(longest, 2), "limit": FREEZE_MAX,
            "at": [round(worst[0], 2), round(worst[1], 2)] if worst else None,
            "stills": len(clipped)}


def check_length(expected: float | None, actual: float | None) -> dict:
    if not expected or actual is None:
        return {"pass": False, "reason": "expected or actual length unknown",
                "expected": expected, "actual": actual}
    off = abs(actual - expected) / expected
    return {"pass": off <= LEN_TOL, "expected": round(expected, 2), "actual": round(actual, 2),
            "off_pct": round(off * 100, 2), "limit_pct": LEN_TOL * 100}


def check_loudness(lufs: float | None, true_peak: float | None) -> dict:
    if lufs is None or true_peak is None:
        return {"pass": False, "reason": "loudness did not read"}
    return {"pass": abs(lufs - LUFS_REF) <= LUFS_TOL and true_peak <= TP_MAX,
            "lufs": round(lufs, 1), "true_peak": round(true_peak, 1),
            "target": LUFS_REF, "tolerance": LUFS_TOL, "tp_max": TP_MAX}


def check_overflow(report: list | None) -> dict:
    if report is None:
        return {"pass": False, "reason": "no overflow report from the render"}
    return {"pass": not report, "overflow": list(report)[:10], "count": len(report)}


def check_streams(info: dict | None) -> dict:
    if not info:
        return {"pass": False, "reason": "ffprobe did not read"}
    v = next((s for s in info.get("streams", []) if s.get("codec_type") == "video"), {})
    a = next((s for s in info.get("streams", []) if s.get("codec_type") == "audio"), {})
    subs = [s for s in info.get("streams", []) if s.get("codec_type") == "subtitle"]
    fps = v.get("r_frame_rate", "0/1").split("/")
    rate = float(fps[0]) / float(fps[1] or 1) if len(fps) == 2 else 0.0
    ok = (v.get("width") == 1920 and v.get("height") == 1080 and abs(rate - 30) < 0.01
          and v.get("codec_name") == "h264" and a.get("codec_name") == "aac" and not subs)
    return {"pass": bool(ok), "size": f"{v.get('width')}x{v.get('height')}", "fps": round(rate, 2),
            "video": v.get("codec_name"), "audio": a.get("codec_name"), "soft_subtitles": len(subs)}


def check_captions(brights: list[float] | None, cues: int, soft_subs: int = 0) -> dict:
    if brights is None or not cues:
        return {"pass": False, "reason": "no caption cues or samples to check"}
    lit = sum(1 for y in brights if y >= CAP_BRIGHT)
    return {"pass": lit == len(brights) and len(brights) > 0 and soft_subs == 0,
            "sampled": len(brights), "with_caption": lit, "cues": cues, "soft_subtitles": soft_subs}


def result_json(checks: dict) -> str:
    return json.dumps({"pass": all(c.get("pass") is True for c in checks.values()) and bool(checks),
                       "checks": checks,
                       "read_at": dt.datetime.now(dt.UTC).isoformat(timespec="seconds")},
                      indent=2, sort_keys=True)


# ----------------------------------------------------------------- reading
def _run(argv: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(argv, capture_output=True, text=True, check=False)


def probe(path: Path) -> dict | None:
    r = _run([FFPROBE, "-v", "error", "-show_streams", "-show_format", "-of", "json", str(path)])
    try:
        return json.loads(r.stdout)
    except ValueError:
        return None


def read_freeze(path: Path, total: float) -> list[tuple[float, float]] | None:
    r = _run([FFMPEG, "-hide_banner", "-nostats", "-i", str(path), "-an", "-vf",
              f"crop=1920:{CAP_Y}:0:0,freezedetect=n={FREEZE_NOISE}:d={FREEZE_MIN}",
              "-f", "null", "-"])
    if r.returncode != 0:
        return None
    return parse_freezedetect(r.stderr, total)


def read_loudness(path: Path) -> tuple[float | None, float | None]:
    r = _run([FFMPEG, "-hide_banner", "-nostats", "-i", str(path), "-vn", "-af",
              "ebur128=peak=true", "-f", "null", "-"])
    s = r.stderr.rsplit("Summary:", 1)[-1]
    i = re.search(r"I:\s+(-?[0-9.]+) LUFS", s)
    p = re.search(r"Peak:\s+(-?[0-9.]+) dBFS", s)
    return (float(i.group(1)) if i else None, float(p.group(1)) if p else None)


def read_caption_band(path: Path, times: list[float]) -> list[float] | None:
    out = []
    for t in times:
        r = _run([FFMPEG, "-hide_banner", "-nostats", "-ss", f"{t:.3f}", "-i", str(path),
                  "-frames:v", "1", "-vf",
                  f"crop=1920:{CAP_H}:0:{CAP_Y},signalstats,metadata=print:file=-", "-f", "null", "-"])
        m = re.search(r"lavfi\.signalstats\.YMAX=([0-9.]+)", r.stdout + r.stderr)
        if not m:
            return None
        out.append(float(m.group(1)))
    return out


def run(mp4: Path, render: Path | None = None, out_dir: Path | None = None) -> dict:
    rep = json.loads(render.read_text()) if render and render.exists() else {}
    info = probe(mp4)
    actual = float(info["format"]["duration"]) if info and "format" in info else None
    total = actual or 0.0
    cues = rep.get("cues") or []
    mids = [(c[0] + c[1]) / 2 for c in cues]
    if len(mids) > 24:
        step = len(mids) / 24
        mids = [mids[int(k * step)] for k in range(24)]
    soft = sum(1 for s in (info or {}).get("streams", []) if s.get("codec_type") == "subtitle")
    lufs, tp = read_loudness(mp4)
    checks = {
        "streams": check_streams(info),
        "length": check_length(rep.get("expected_s"), actual),
        "freeze": check_freeze(read_freeze(mp4, total), total=total,
                               end_card=rep.get("end_card_s", END_CARD)),
        "loudness": check_loudness(lufs, tp),
        "captions": check_captions(read_caption_band(mp4, mids) if mids else None, len(cues), soft),
        "overflow": check_overflow(rep.get("overflow")),
    }
    text = result_json(checks)
    dest_dir = mp4.parent if mp4.stat().st_nlink == 1 and out_dir is None else (out_dir or mp4.parent)
    if mp4.stat().st_nlink > 1 and out_dir is None:
        raise SystemExit(f"{mp4} has {mp4.stat().st_nlink} names (hard-linked live file): "
                         "pass --out DIR so nothing is written beside it")
    dest_dir.mkdir(parents=True, exist_ok=True)
    (dest_dir / f"{mp4.name}.gate.json").write_text(text + "\n")
    result = json.loads(text)
    if result.get("pass") is True and dest_dir == mp4.parent:
        # Staging step: bind the YouTube upload approval to these exact bytes.
        try:
            from atlas.modules.academy import lesson_youtube
        except ImportError:        # run outside the repo: no approval is written
            lesson_youtube = None
        if lesson_youtube is not None:
            lesson_youtube.write_approval(mp4)
    return result


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("mp4", type=Path)
    ap.add_argument("--render", type=Path, help="render.json beside the video (default: found)")
    ap.add_argument("--out", type=Path, help="write the .gate.json here")
    a = ap.parse_args(argv)
    render = a.render or a.mp4.with_name(a.mp4.stem + ".render.json")
    res = run(a.mp4, render, a.out)
    print(json.dumps({"pass": res["pass"],
                      **{k: v["pass"] for k, v in res["checks"].items()}}))
    return 0 if res["pass"] else 1


if __name__ == "__main__":
    sys.exit(main())
