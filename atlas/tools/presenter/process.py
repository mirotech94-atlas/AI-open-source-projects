"""Miro on camera: turn a raw webcam clip into a finished presenter clip.

    ~/.cache/atlas-presenter/venv/bin/python tools/presenter/process.py \\
        presenter-inbox/intro.mov --slot intro
    ~/.cache/atlas-presenter/venv/bin/python tools/presenter/process.py \\
        presenter-inbox/outro-io.mov --slot outro --end 16.82 --fade 0.2

(outro-io.mov is outro.mov with its one word fixed by splice.py; see there.
--end 16.82 stops it after "see you in the next one", before "peace out":
Miro, 2026-10-13.)

Runs on the Mac only (the server has one CPU and never mattes). Install once
with tools/presenter/setup.sh: a venv at ~/.cache/atlas-presenter/venv
(onnxruntime + numpy) and Robust Video Matting's ONNX model
(rvm_mobilenetv3_fp32.onnx, its sha256 checked; github.com/PeterL1n/
RobustVideoMatting, GPL-3.0, run as a local tool, not shipped).

What it does, and nothing else (Miro, 2026-10-13: never alter the face or
the voice beyond background, framing and loudness):
  1. trims the silence at both ends (an energy envelope of the audio, cut
     on video and audio together so the lips stay in sync), and removes any
     `--cut A:B` span asked for, each boundary snapped to the quietest
     moment within 0.6 s, joined with a short crossfade;
  2. takes the background out with RVM (temporal, so hair and edges hold),
     and refuses a take where it found almost no one (RVM mattes people
     only: an empty set must never open every tutorial);
  3. sets Miro on the ATLAS stage: the 3D mark idling (tutorial3d's
     loop-mark.mp4, the brand palette's black and royal blue) under a faint
     grid, Miro left of centre and bottom-aligned, the matte feathered a
     little and a soft #5B8CFF rim behind the edge; only uniform scaling;
  4. evens the loudness to the narration's: -21 LUFS integrated, -1.5 dBTP
     (two-pass loudnorm, linear). Measured on the server 2026-10-13 with
     ebur128: n8n #1's Fish narration -21.0 LUFS, the narrated ending -20.5;
  5. encodes 1920x1080 30 fps, H.264 CRF 20, AAC 44.1 kHz stereo, and writes
     <out>/<slot>.mp4 plus <out>/manifest.json (sha256, seconds, the line
     from atlas/modules/academy/presenter.py LINES, how it was made).

The real clips go to atlas/modules/academy/assets/presenter/ (the default
--out). A test or synthetic clip must use --out elsewhere: anything in that
directory with a matching manifest opens every published tutorial.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import math
import os
import re
import subprocess
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
ACADEMY = REPO / "atlas" / "modules" / "academy"
OUT_DEFAULT = ACADEMY / "assets" / "presenter"
STAGE_LOOP = ACADEMY / "assets" / "tutorial3d" / "loop-mark.mp4"
HOME = Path(os.getenv("ATLAS_PRESENTER_HOME", Path.home() / ".cache" / "atlas-presenter"))
MODEL = HOME / "rvm_mobilenetv3_fp32.onnx"
MODEL_SHA256 = "88d4531297118f595bf2fd60f6f566aec2e559393802d1f436c380f0cbbd2828"

W, H, FPS = 1920, 1080, 30
TARGET_X = 760            # where Miro's centre lands: left of the 3D mark
LUFS, TRUE_PEAK, LRA = -21.0, -1.5, 7.0
CRF = 20
MAX_BYTES = 8 * 1024 * 1024
MIN_COVERAGE = 0.03       # the median share of the frame the matte must cover
STEP = 0.02               # the energy envelope's window, seconds
LEAD_IN, TAIL = 0.20, 0.35   # kept before the first word and after the last
SPEECH_OVER_FLOOR = 12.0     # dB over the room's floor that counts as speech
SNAP_REACH, SNAP_WIDTH = 0.6, 0.12
CROSSFADE = 0.12
FEATHER = 1.2             # gblur sigma on the matte, px
RIM_BLUR, RIM_GAIN = 9.0, 0.75
BLUE = "0x5B8CFF"
GRID = "0xEEF2FA@0.035"


def lines() -> dict[str, str]:
    """presenter.LINES, loaded by path (this venv has none of ATLAS's deps)."""
    spec = importlib.util.spec_from_file_location("atlas_presenter", ACADEMY / "presenter.py")
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod             # dataclasses look their module up
    spec.loader.exec_module(mod)
    return dict(mod.LINES)


# ------------------------------------------------------------- pure helpers

def speech_window(levels: list[float], step: float, duration: float) -> tuple[float, float]:
    """(start, end) of the speech in a dB envelope: the first and last
    window SPEECH_OVER_FLOOR over the floor (the 10th percentile), with a
    breath kept either side."""
    if not levels:
        return 0.0, duration
    floor = sorted(levels)[len(levels) // 10]
    loud = [i for i, v in enumerate(levels) if v >= floor + SPEECH_OVER_FLOOR]
    if not loud:
        return 0.0, duration
    start = max(0.0, loud[0] * step - LEAD_IN)
    end = min(duration, (loud[-1] + 1) * step + TAIL)
    return round(start, 3), round(end, 3)


def snap(levels: list[float], step: float, t: float) -> float:
    """The quietest SNAP_WIDTH moment within SNAP_REACH of `t`: its middle."""
    k = max(1, round(SNAP_WIDTH / step))
    lo = max(0, round((t - SNAP_REACH) / step))
    hi = min(len(levels) - k, round((t + SNAP_REACH) / step))
    if hi < lo:
        return t
    best = min(range(lo, hi + 1), key=lambda i: (sum(levels[i:i + k]), abs(i * step - t)))
    return round((best + k / 2) * step, 3)


def keep(start: float, end: float, cuts: list[tuple[float, float]]) -> list[tuple[float, float]]:
    """The spans of [start, end] left after removing every cut."""
    out = [(start, end)]
    for a, b in sorted(cuts):
        nxt = []
        for s, e in out:
            if b <= s or a >= e:
                nxt.append((s, e))
                continue
            if a > s:
                nxt.append((s, a))
            if b < e:
                nxt.append((b, e))
        out = nxt
    return [(round(s, 3), round(e, 3)) for s, e in out if e - s > 0.05]


def framing(cx: float, src_w: int, src_h: int) -> tuple[int, int, int, int]:
    """(scaled width, scaled height, x, y): the frame scaled uniformly to the
    canvas height, bottom-aligned, Miro's centre `cx` (source px) at TARGET_X."""
    s = H / src_h
    sw, sh = round(src_w * s / 2) * 2, H
    return sw, sh, round(TARGET_X - cx * s), H - sh


def loudnorm_stats(stderr: str) -> dict[str, str]:
    """The JSON loudnorm prints at the end of its first pass."""
    got = re.findall(r"\{[^{}]*\"input_i\"[^{}]*\}", stderr)
    if not got:
        raise RuntimeError("loudnorm printed no measurement")
    return json.loads(got[-1])


# --------------------------------------------------------------- the steps

def run(argv: list[str], **kw) -> subprocess.CompletedProcess:
    return subprocess.run(argv, check=True, capture_output=True, **kw)


def probe(path: Path) -> dict:
    out = run(["ffprobe", "-v", "error", "-print_format", "json", "-show_streams",
               "-show_format", str(path)]).stdout
    return json.loads(out)


def video_size(info: dict) -> tuple[int, int]:
    v = next(s for s in info["streams"] if s["codec_type"] == "video")
    w, h = int(v["width"]), int(v["height"])
    rot = int((v.get("tags") or {}).get("rotate", 0) or 0)
    for side in v.get("side_data_list") or []:
        rot = int(side.get("rotation", rot) or rot)
    return (h, w) if abs(rot) % 180 == 90 else (w, h)


def envelope(path: Path) -> list[float]:
    """The audio's level in dB, one value per STEP, from mono 16 kHz."""
    import numpy as np
    raw = run(["ffmpeg", "-v", "error", "-i", str(path), "-vn", "-ac", "1", "-ar", "16000",
               "-f", "f32le", "-"]).stdout
    a = np.frombuffer(raw, dtype="<f4")
    w = int(16000 * STEP)
    n = len(a) // w
    rms = np.sqrt((a[:n * w].reshape(n, w) ** 2).mean(axis=1))
    return [float(x) for x in 20 * np.log10(rms + 1e-9)]


def edit(src: Path, spans: list[tuple[float, float]], out: Path) -> None:
    """The kept spans joined (a CROSSFADE between them), 30 fps, near
    lossless, audio as PCM: what every later step reads."""
    graph, vs, as_ = [], [], []
    for i, (a, b) in enumerate(spans):
        graph.append(f"[0:v]trim={a:.3f}:{b:.3f},setpts=PTS-STARTPTS,fps={FPS},"
                     f"settb=1/{FPS},format=yuv420p[v{i}]")
        graph.append(f"[0:a]atrim={a:.3f}:{b:.3f},asetpts=PTS-STARTPTS[a{i}]")
        vs.append(f"[v{i}]")
        as_.append(f"[a{i}]")
    v, a, t = vs[0], as_[0], spans[0][1] - spans[0][0]
    for i in range(1, len(spans)):
        graph.append(f"{v}{vs[i]}xfade=transition=fade:duration={CROSSFADE}:"
                     f"offset={t - CROSSFADE:.3f}[vx{i}]")
        graph.append(f"{a}{as_[i]}acrossfade=d={CROSSFADE}[ax{i}]")
        v, a = f"[vx{i}]", f"[ax{i}]"
        t += spans[i][1] - spans[i][0] - CROSSFADE
    graph += [f"{v}null[v]", f"{a}anull[a]"]
    run(["ffmpeg", "-y", "-v", "error", "-i", str(src), "-filter_complex", ";".join(graph),
         "-map", "[v]", "-map", "[a]", "-c:v", "libx264", "-preset", "veryfast", "-crf", "8",
         "-c:a", "pcm_s16le", str(out)])


def matte(src: Path, out: Path, model: Path) -> float:
    """Robust Video Matting over every frame of `src`: an ffv1 grey matte
    at the source size. Returns Miro's centre x (median over frames)."""
    import numpy as np
    import onnxruntime as ort
    w, h = video_size(probe(src))
    sess = ort.InferenceSession(str(model), providers=["CPUExecutionProvider"])
    ratio = np.array([min(1.0, 512 / max(w, h))], dtype=np.float32)
    dec = subprocess.Popen(["ffmpeg", "-v", "error", "-i", str(src), "-f", "rawvideo",
                            "-pix_fmt", "rgb24", "-"], stdout=subprocess.PIPE)
    enc = subprocess.Popen(["ffmpeg", "-y", "-v", "error", "-f", "rawvideo", "-pix_fmt", "gray",
                            "-s", f"{w}x{h}", "-r", str(FPS), "-i", "-", "-c:v", "ffv1",
                            str(out)], stdin=subprocess.PIPE)
    assert dec.stdout is not None and enc.stdin is not None
    size = w * h * 3
    frames = []
    while len(buf := dec.stdout.read(size)) == size:
        frames.append(buf)
    dec.wait()
    rec = [np.zeros((1, 1, 1, 1), dtype=np.float32)] * 4
    xs = np.arange(w, dtype=np.float32)
    centres, cover = [], []

    def step(buf: bytes) -> np.ndarray:
        nonlocal rec
        img = np.frombuffer(buf, dtype=np.uint8).reshape(h, w, 3)
        src_t = (img.astype(np.float32) / 255.0).transpose(2, 0, 1)[None]
        _fgr, pha, *rec = sess.run(None, {"src": src_t, "r1i": rec[0], "r2i": rec[1],
                                          "r3i": rec[2], "r4i": rec[3],
                                          "downsample_ratio": ratio})
        return pha[0, 0]

    for buf in frames[:FPS]:          # warm the recurrent state on the first second
        step(buf)
    for buf in frames:
        pha = step(buf)
        mass = pha.sum(axis=0)
        cover.append(float(pha.mean()))
        if mass.sum() > 1:
            centres.append(float((mass * xs).sum() / mass.sum()))
        enc.stdin.write((np.clip(pha, 0, 1) * 255 + 0.5).astype(np.uint8).tobytes())
    enc.stdin.close()
    if enc.wait() != 0:
        raise RuntimeError("the matte could not be encoded")
    if not centres or coverage_refusal(cover) is not None:
        raise SystemExit(coverage_refusal(cover) or "no person found in the clip")
    return float(np.median(centres))


def coverage_refusal(cover: list[float]) -> str | None:
    """None when the matte holds a person on most frames: the median share
    of the frame it keeps is at least MIN_COVERAGE."""
    if not cover:
        return "the clip has no frames"
    mid = sorted(cover)[len(cover) // 2]
    if mid < MIN_COVERAGE:
        return (f"the matte keeps {mid:.1%} of the frame (under {MIN_COVERAGE:.0%}): "
                "no person found, so no clip is written")
    return None


def loudness(src: Path) -> dict[str, str]:
    p = subprocess.run(["ffmpeg", "-hide_banner", "-nostats", "-i", str(src), "-vn", "-af",
                        f"loudnorm=I={LUFS}:TP={TRUE_PEAK}:LRA={LRA}:print_format=json",
                        "-f", "null", "-"], capture_output=True, text=True, check=True)
    return loudnorm_stats(p.stderr)


def composite_argv(src: Path, alpha: Path, out: Path, seconds: float, cx: float,
                   size: tuple[int, int], stats: dict[str, str],
                   stage: Path = STAGE_LOOP, fade: float = 0.05) -> list[str]:
    sw, sh, x, y = framing(cx, *size)
    norm = (f"loudnorm=I={LUFS}:TP={TRUE_PEAK}:LRA={LRA}:"
            f"measured_I={stats['input_i']}:measured_TP={stats['input_tp']}:"
            f"measured_LRA={stats['input_lra']}:measured_thresh={stats['input_thresh']}:"
            f"offset={stats['target_offset']}:linear=true")
    graph = [
        (f"[2:v]fps={FPS},scale={W}:{H},setsar=1,setpts=PTS-STARTPTS,"
         f"drawgrid=w=96:h=96:t=1:c={GRID},format=rgba[stage]"),
        f"[1:v]format=gray,gblur=sigma={FEATHER},split=3[a1][a2][a3]",
        "[0:v]format=rgba[src]",
        "[src][a1]alphamerge[p0]",
        f"[a2]gblur=sigma={RIM_BLUR}[ab]",
        f"[ab][a3]blend=all_mode=subtract,lut=c0=val*{RIM_GAIN}[rim]",
        f"color=c={BLUE}:s={size[0]}x{size[1]}:r={FPS}:d={seconds:.3f},format=rgba[blue]",
        "[blue][rim]alphamerge[g0]",
        f"[g0]scale={sw}:{sh}[glow]",
        f"[p0]scale={sw}:{sh}[person]",
        f"[stage][glow]overlay={x}:{y}:shortest=1[s1]",
        f"[s1][person]overlay={x}:{y}:shortest=1,format=yuv420p,setsar=1[v]",
        (f"[0:a]{norm},aresample=44100,aformat=sample_fmts=fltp:channel_layouts=stereo,"
         f"afade=t=in:d=0.02,afade=t=out:st={max(0.0, seconds - fade):.3f}:d={fade}[a]"),
    ]
    return ["ffmpeg", "-y", "-v", "error", "-i", str(src), "-i", str(alpha),
            "-stream_loop", "-1", "-t", f"{seconds:.3f}", "-i", str(stage),
            "-filter_complex", ";".join(graph), "-map", "[v]", "-map", "[a]",
            "-t", f"{seconds:.3f}", "-c:v", "libx264", "-preset", "slow", "-crf", str(CRF),
            "-g", str(FPS * 4), "-pix_fmt", "yuv420p", "-r", str(FPS),
            "-c:a", "aac", "-b:a", "160k", "-ar", "44100", "-ac", "2",
            "-movflags", "+faststart", str(out)]


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def write_manifest(out_dir: Path, slot: str, entry: dict) -> None:
    path = out_dir / "manifest.json"
    try:
        raw = json.loads(path.read_text())
    except (OSError, ValueError):
        raw = {}
    clips = raw.get("clips") if isinstance(raw.get("clips"), dict) else {}
    clips[slot] = entry
    tmp = path.with_suffix(".json.part")
    tmp.write_text(json.dumps({"version": 1, "clips": dict(sorted(clips.items()))},
                              indent=1) + "\n")
    os.replace(tmp, path)


def process(src: Path, slot: str, out_dir: Path, cuts: list[tuple[float, float]],
            model: Path = MODEL, work: Path | None = None, until: float | None = None,
            fade: float = 0.05) -> dict:
    """`until`: end the take there (seconds in the source), whatever is said
    after; `fade`: the audio's fade-out at the end, seconds."""
    said = lines().get(slot)
    if said is None:
        raise SystemExit(f"no slot {slot!r}; one of {', '.join(lines())}")
    if not model.is_file() or sha256(model) != MODEL_SHA256:
        raise SystemExit(f"the RVM model is missing or not the pinned one: {model} "
                         "(run tools/presenter/setup.sh)")
    info = probe(src)
    duration = float(info["format"]["duration"])
    levels = envelope(src)
    start, end = speech_window(levels, STEP, duration)
    if until is not None:
        end = min(end, until)
    snapped = [(snap(levels, STEP, a), snap(levels, STEP, b)) for a, b in cuts]
    spans = keep(start, end, snapped)
    out_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="presenter-", dir=work) as tmp:
        t = Path(tmp)
        edited, alpha = t / "edited.mkv", t / "alpha.mkv"
        edit(src, spans, edited)
        seconds = float(probe(edited)["format"]["duration"])
        seconds = math.floor(seconds * FPS) / FPS
        cx = matte(edited, alpha, model)
        stats = loudness(edited)
        out = out_dir / f"{slot}.mp4"
        run(composite_argv(edited, alpha, out, seconds, cx, video_size(probe(edited)), stats,
                           fade=fade))
    size = out.stat().st_size
    if size > MAX_BYTES:
        raise SystemExit(f"{out} is {size / 1e6:.1f} MB, over {MAX_BYTES / 1e6:.0f} MB")
    final = float(probe(out)["format"]["duration"])
    entry = {"file": out.name, "sha256": sha256(out), "seconds": round(final, 3),
             "said": said, "bytes": size, "source": src.name,
             "source_sha256": sha256(src), "kept": spans, "cuts_asked": cuts,
             "until": until, "fade_out": fade, "lufs": LUFS, "made_at": datetime.now(UTC).isoformat(timespec="seconds")}
    write_manifest(out_dir, slot, entry)
    return entry


def parse_cut(text: str) -> tuple[float, float]:
    a, _, b = text.partition(":")
    lo, hi = float(a), float(b)
    if not 0 <= lo < hi:
        raise argparse.ArgumentTypeError(f"a cut is START:END seconds, not {text!r}")
    return lo, hi


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("source", type=Path, help="the raw webcam clip")
    ap.add_argument("--slot", required=True, help="intro, outro or outro-full")
    ap.add_argument("--cut", type=parse_cut, action="append", default=[],
                    help="remove START:END seconds (snapped to the nearest pause)")
    ap.add_argument("--out", type=Path, default=OUT_DEFAULT,
                    help="where <slot>.mp4 and manifest.json go")
    ap.add_argument("--model", type=Path, default=MODEL)
    ap.add_argument("--end", type=float, default=None,
                    help="end the take at this second of the source")
    ap.add_argument("--fade", type=float, default=0.05,
                    help="seconds of audio fade-out at the end")
    args = ap.parse_args(argv)
    entry = process(args.source, args.slot, args.out, args.cut, args.model,
                    until=args.end, fade=args.fade)
    print(json.dumps({k: entry[k] for k in ("file", "seconds", "bytes", "kept", "said")},
                     indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
