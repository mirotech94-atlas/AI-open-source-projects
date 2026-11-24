"""Render a lesson as a moving video: ATLAS Academy black and blue look, animated simulated diagrams,
burned-in captions, the existing male British narration untouched.

    python tools/lesson_video/motion.py <lesson_dir> [--senior] [--out DIR] [--id 3.2]

`lesson_dir` holds segments.json and audio/NNN.mp3 (read only: nothing is ever
written there). Output goes to data/academy-staging/video/ by default:
    lesson-<id>.mp4, lesson-<id>.render.json (read by gate.py)

Never writes under data/app/ and never into a lesson folder: the live
data/app/academy/files/*.mp4 are hard links of the old slide videos, and an
ffmpeg -y over one would truncate both names. Output is a temp file renamed
into place (os.replace), which swaps the name, not the old inode.

Picture: every page is drawn by time (setT). The first 0.8 s of a beat is
shot at 30 fps (the enter), the rest at 6 per second (drifting grid, pulses,
travelling dots, the progress bar). Beats are at most 5 s. Captions are one libass pass over the
finished picture. Audio is the mp3s end to end (tempo 1.0, no pad) plus the
planned sting, checkpoint and end-card silences, loudness-normalised to the
tutorials.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
FFMPEG = shutil.which("ffmpeg") or "/Users/mac/.local/bin/ffmpeg"
FFPROBE = shutil.which("ffprobe") or "/Users/mac/.local/bin/ffprobe"
STAGING = ROOT / "data" / "academy-staging" / "video"
FPS = 30
LUFS = -19.5
CRF = "21"
TEMPO = 1.0               # the old slides ran atempo 0.94 plus a 1 s pad per segment; not here
SILENCE_DB, SILENCE_MIN = "-35dB", 0.15
ZOOM = 0.02             # each beat pushes in 2% over its length, so a held picture is never still


def _mod(name: str):
    key = f"lv_{name}"
    if key in sys.modules:
        return sys.modules[key]
    spec = importlib.util.spec_from_file_location(key, HERE / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[key] = mod
    spec.loader.exec_module(mod)
    return mod


beats_mod = _mod("beats")


# ------------------------------------------------------------------ guards
def guard_out(path: Path) -> Path:
    """Refuse anything under the live tree (data/app/) or a lesson-video folder."""
    p = path.as_posix()
    if "data/app/" in p + "/" or re.search(r"data/lesson-video[^/]*/", p + "/"):
        raise ValueError(f"refusing to write under the live tree / a lesson folder: {path}")
    return path


def replace_atomic(tmp: Path, data: bytes | None, dest: Path) -> None:
    """Put `dest` in place by renaming a finished temp file over it. Another
    name of the old file (a hard link) keeps the old content."""
    if data is not None:
        tmp.write_bytes(data)
    os.replace(tmp, dest)


def _run(argv: list[str], **kw) -> subprocess.CompletedProcess:
    r = subprocess.run(argv, capture_output=True, text=True, check=False, **kw)
    if r.returncode != 0:
        raise RuntimeError(f"{argv[0]} failed ({r.returncode}): {r.stderr[-1500:]}")
    return r


# -------------------------------------------------------------------- input
def probe_seconds(mp3: Path) -> float:
    r = _run([FFPROBE, "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0",
              str(mp3)])
    return float(r.stdout.strip())


def silences(mp3: Path) -> list[tuple[float, float]]:
    """(middle, length) of every pause in a narration (ffmpeg silencedetect)."""
    r = subprocess.run([FFMPEG, "-hide_banner", "-nostats", "-i", str(mp3), "-af",
                        f"silencedetect=noise={SILENCE_DB}:d={SILENCE_MIN}", "-f", "null", "-"],
                       capture_output=True, text=True, check=False)
    out, start = [], None
    for m in re.finditer(r"silence_(start|end): (-?[0-9.]+)", r.stderr):
        v = float(m.group(2))
        if m.group(1) == "start":
            start = max(0.0, v)
        elif start is not None:
            out.append(((start + v) / 2, v - start))
            start = None
    return out


def load_lesson(lesson_dir: Path):
    """The lesson's segments (with each narration's length), the mp3 paths and
    each mp3's pauses. Read only."""
    raw = json.loads((lesson_dir / "segments.json").read_text())
    segs, files, sil = [], [], []
    for n, s in enumerate(raw, 1):
        mp3 = lesson_dir / "audio" / f"{n:03d}.mp3"
        if not mp3.exists():
            raise FileNotFoundError(mp3)
        segs.append(beats_mod.Seg(s["kind"], s.get("heading", ""), s["html"], s["say"],
                                  round(probe_seconds(mp3), 3)))
        files.append(mp3)
        sil.append(silences(mp3))
    return segs, files, sil


# ------------------------------------------------------------------ captions
def _stamp(t: float) -> str:
    cs = round(max(0.0, t) * 100)
    h, cs = divmod(cs, 360000)
    m, cs = divmod(cs, 6000)
    s, cs = divmod(cs, 100)
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


def _ass_colour(hex_colour: str, alpha: int = 0) -> str:
    return f"&H{alpha:02X}{hex_colour[5:7]}{hex_colour[3:5]}{hex_colour[1:3]}".upper()


def caption_ass(cues, senior: bool) -> str:
    """Every spoken word as a caption: paper on a navy plate at 80%, the video
    system's caption type (44 px, 52 px for 60+), bottom centre inside the safe area."""
    from atlas.modules.academy import video_tokens as vt
    st = beats_mod.STYLE[senior]
    pad = 14
    text = _ass_colour("#EEF2FA")
    box = _ass_colour("#0A0D14", round(255 * (1 - vt.CAPTION_PLATE_ALPHA)))
    size = round(st["cap"] * vt.ASS_CELL)
    head = ("[Script Info]\nScriptType: v4.00+\nPlayResX: 1920\nPlayResY: 1080\nWrapStyle: 0\n"
            "ScaledBorderAndShadow: yes\n\n[V4+ Styles]\nFormat: Name, Fontname, Fontsize, "
            "PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, "
            "StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, "
            "MarginL, MarginR, MarginV, Encoding\n"
            f"Style: Lesson,{vt.ASS_FONT},{size},{text},{text},{box},{box},0,0,0,0,100,100,0,0,3,"
            f"{pad},0,2,{vt.SAFE_16_9 + pad + 96},{vt.SAFE_16_9 + pad + 96},{vt.SAFE_16_9 + pad},1\n\n"
            "[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n")
    ev = [f"Dialogue: 0,{_stamp(c.t0)},{_stamp(c.t1)},Lesson,,0,0,0,,"
          f"{c.text.replace(chr(10), ' ').replace('{', '(').replace('}', ')')}" for c in cues]
    return head + "\n".join(ev) + "\n"


# -------------------------------------------------------------------- audio
def build_audio(plan, files: list[Path], work: Path) -> Path:
    """sting silence + mp3s end to end + checkpoint pauses + end-card silence,
    then loudnorm (two passes, linear) to the tutorials' level."""
    parts: list[tuple[str, str | float]] = [("sil", beats_mod.STING)]
    pause_at = {round(a, 2): b - a for a, b in plan.pauses}
    for i, a0, a1 in plan.audio:
        parts.append(("mp3", str(files[i])))
        if round(a1, 2) in pause_at:
            parts.append(("sil", pause_at[round(a1, 2)]))
    parts.append(("sil", beats_mod.END_CARD))
    argv, filt = [FFMPEG, "-y", "-hide_banner", "-nostats"], []
    fmt = "aresample=48000,aformat=sample_fmts=fltp:channel_layouts=stereo"
    for n, (kind, v) in enumerate(parts):
        if kind == "sil":
            argv += ["-f", "lavfi", "-t", f"{v:.4f}", "-i", "anullsrc=r=48000:cl=stereo"]
        else:
            argv += ["-i", v]
        tempo = f",atempo={TEMPO}" if TEMPO != 1.0 else ""
        filt.append(f"[{n}:a]{fmt}{tempo}[a{n}]")
    cat = "".join(f"[a{n}]" for n in range(len(parts)))
    raw = work / "raw.wav"
    _run(argv + ["-filter_complex", ";".join(filt) + f";{cat}concat=n={len(parts)}:v=0:a=1[o]",
                 "-map", "[o]", "-c:a", "pcm_f32le", str(raw)])
    r = subprocess.run([FFMPEG, "-hide_banner", "-nostats", "-i", str(raw), "-af",
                        f"loudnorm=I={LUFS}:TP=-2:LRA=11:print_format=json", "-f", "null", "-"],
                       capture_output=True, text=True, check=False)
    j = json.loads(r.stderr[r.stderr.rfind("{"): r.stderr.rfind("}") + 1])
    norm = work / "norm.wav"
    ln = (f"loudnorm=I={LUFS}:TP=-2:LRA=11:measured_I={j['input_i']}:measured_LRA={j['input_lra']}"
          f":measured_TP={j['input_tp']}:measured_thresh={j['input_thresh']}"
          f":offset={j['target_offset']}:linear=true,aresample=48000")
    _run([FFMPEG, "-y", "-hide_banner", "-nostats", "-i", str(raw), "-af", ln,
          "-c:a", "pcm_s16le", str(norm)])
    return norm


# -------------------------------------------------------------------- video
def frame_of(t: float) -> int:
    return round(t * FPS)


def shoot(plan, work: Path, senior: bool) -> tuple[list[tuple[Path, int]], list[str]]:
    """The picture as (png, frames to show) in order, and the overflow report."""
    from playwright.sync_api import sync_playwright
    scenes = _mod("scenes")
    frames_dir = work / "frames"
    frames_dir.mkdir(parents=True, exist_ok=True)
    seq: list[tuple[Path, int]] = []
    overflow: list[str] = []
    n_png = 0

    def png() -> Path:
        nonlocal n_png
        n_png += 1
        return frames_dir / f"{n_png:05d}.jpg"

    with sync_playwright() as pw:
        browser = pw.chromium.launch(channel="chrome")
        page = browser.new_page(viewport={"width": scenes.W, "height": scenes.H})
        # the brand sting: one page drawn at 30 instants
        page.set_content(scenes.sting_page(), wait_until="load")
        page.evaluate("document.fonts.ready")
        for f in range(frame_of(beats_mod.STING)):
            page.evaluate(f"setT({f / FPS:.4f})")
            p = png()
            page.screenshot(path=str(p), type="jpeg", quality=95)
            seq.append((p, 1))
        prev_kind, prev = "sting", None
        for b in plan.beats:
            if b.kind == "sting":
                continue
            nf = frame_of(b.t1) - frame_of(b.t0)
            fade = b.kind != prev_kind
            page.set_content(scenes.scene(b, senior=senior, fade=fade, prev=prev,
                                          total=plan.total), wait_until="load")
            page.evaluate("document.fonts.ready")
            prev_kind, prev = b.kind, b
            # dense (every frame) while things enter, then a few per second
            step = max(1, round(FPS / scenes.AMBIENT_FPS))
            dense = min(nf, round(scenes.DENSE_S * FPS))
            marks = list(range(dense)) + list(range(dense, nf, step))
            for n, f in enumerate(marks):
                page.evaluate(f"setT({f / FPS:.4f})")
                p = png()
                page.screenshot(path=str(p), type="jpeg", quality=95)
                last = marks[n + 1] if n + 1 < len(marks) else nf
                seq.append((p, last - f))
            page.evaluate("setT(9)")  # settled: a beat shorter than its enter reads mid-move
            for msg in page.evaluate("overflow()"):
                overflow.append(f"{b.t0:.1f}s {b.kind}: {msg}")
        browser.close()
    return seq, overflow


def concat_list(seq: list[tuple[Path, int]], path: Path) -> None:
    lines = []
    for p, n in seq:
        lines += [f"file '{p}'", f"duration {n / FPS:.6f}"]
    lines.append(f"file '{seq[-1][0]}'")
    path.write_text("\n".join(lines) + "\n")


def zoom_expr(plan) -> str:
    """z(t): 1 at each beat's start, 1+ZOOM at its end (sting and end card still)."""
    terms = [f"between(t,{b.t0:.3f},{b.t1:.3f})*(t-{b.t0:.3f})/{b.t1 - b.t0:.3f}"
             for b in plan.beats if b.kind not in ("sting", "end")]
    return f"(1+{ZOOM}*({'+'.join(terms)}))"


def encode(seq, audio: Path, ass: Path, work: Path, tmp_out: Path, plan) -> None:
    from atlas.modules.academy import video_tokens as vt
    lst = work / "frames.txt"
    concat_list(seq, lst)
    z = zoom_expr(plan)
    graph = (f"fps={FPS},scale=w='2*trunc(960*{z})':h='2*trunc(540*{z})':eval=frame:flags=bicubic,"
             f"crop=1920:1080,ass={ass.name}:fontsdir={vt.LIBASS_FONTS_DIR},format=yuv420p")
    script = work / "vf.txt"
    script.write_text(graph)
    _run([FFMPEG, "-y", "-hide_banner", "-nostats", "-f", "concat", "-safe", "0", "-i", str(lst),
          "-i", str(audio), "-filter_script:v", str(script), "-c:v", "libx264", "-preset", "medium", "-crf", CRF,
          "-r", str(FPS), "-c:a", "aac", "-b:a", "160k", "-movflags", "+faststart",
          "-map", "0:v", "-map", "1:a", "-shortest", "-f", "mp4", str(tmp_out)], cwd=str(work))


def contact_sheet(mp4: Path, png: Path, total: float, cols: int = 6, rows: int = 5) -> None:
    every = max(1.0, total / (cols * rows))
    _run([FFMPEG, "-y", "-hide_banner", "-nostats", "-i", str(mp4), "-vf",
          f"fps=1/{every:.3f},scale=480:-1,tile={cols}x{rows}:padding=4:color=0x101b30",
          "-frames:v", "1", str(png)])


# -------------------------------------------------------------------- driver
def render(lesson_dir: Path, out_dir: Path, lesson_id: str, *, senior: bool = False) -> dict:
    t_start = time.time()
    guard_out(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    segs, files, sil = load_lesson(lesson_dir)
    title = next((beats_mod.visual(s).items[0] for s in segs if s.kind == "title"), lesson_id)
    nxt = next((beats_mod.plain(s.html)[0] for s in segs if s.heading.strip().lower() == "next"), "")
    nxt = beats_mod.label(nxt, chars=beats_mod.STYLE[senior]["item_chars"]) if nxt else ""
    plan = beats_mod.plan(segs, senior=senior, silences=sil, next_title=nxt, lesson=title)
    problems = beats_mod.budget_problems(plan)
    work = Path(tempfile.mkdtemp(prefix=f"lv-{lesson_id}-", dir=out_dir))
    try:
        audio = build_audio(plan, files, work)
        seq, overflow = shoot(plan, work, senior)
        ass = work / "cap.ass"
        ass.write_text(caption_ass(plan.cues, senior))
        dest = out_dir / f"lesson-{lesson_id}.mp4"
        tmp = out_dir / f".lesson-{lesson_id}.tmp.mp4"
        encode(seq, audio, ass, work, tmp, plan)
        replace_atomic(tmp, None, dest)
        report = {
            "lesson": lesson_id, "expected_s": plan.total, "audio_s": plan.audio_seconds,
            "end_card_s": beats_mod.END_CARD, "sting_s": beats_mod.STING,
            "pauses": [list(p) for p in plan.pauses], "senior": senior,
            "cues": [[c.t0, c.t1, c.text] for c in plan.cues],
            "beats": {k: sum(1 for b in plan.beats if b.kind == k)
                      for k in sorted({b.kind for b in plan.beats})},
            "longest_beat_s": max((b.seconds for b in plan.beats
                                   if b.kind not in ("sting", "end")), default=0),
            "overflow": overflow + problems, "captions_burned": True, "tempo": TEMPO,
            "render_s": round(time.time() - t_start, 1),
            "size_mb": round(dest.stat().st_size / 1e6, 2),
        }
        (out_dir / f"lesson-{lesson_id}.render.json").write_text(json.dumps(report, indent=1))
        return report
    finally:
        shutil.rmtree(work, ignore_errors=True)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("lesson_dir", type=Path)
    ap.add_argument("--id", help="lesson id (default: the folder name)")
    ap.add_argument("--senior", action="store_true", help="the 60+ template")
    ap.add_argument("--out", type=Path, default=STAGING)
    a = ap.parse_args(argv)
    rep = render(a.lesson_dir, a.out, a.id or a.lesson_dir.name, senior=a.senior)
    print(json.dumps({k: rep[k] for k in ("lesson", "expected_s", "render_s", "size_mb")}
                     | {"overflow": len(rep["overflow"])}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
