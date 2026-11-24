"""ffmpeg on the box: whether it is there, what it can do, and the exact
command lines the reel renderers run.

Everything here that runs a process uses `asyncio.create_subprocess_exec`
with an argv list -- never a shell, never `subprocess.run` (which would
block the event loop for the whole encode) -- and kills the process when
its timeout passes. A missing binary is a reason ("ffmpeg not installed"),
never a guess that it would have worked.

The argv builders are pure so their shape can be tested without ffmpeg:

  * `stills_argv` -- the local backend, the pipeline Miro proved by hand on
    production: each still fed as a SINGLE input frame (no `-loop 1 -t`,
    because zoompan's `d=N` multiplies per input frame and a looped scene 1
    eats the whole clip), `zoompan` per scene, the scenes concatenated,
    "Made with AI" burned into every frame with `drawtext`, and a PART 1
    end card for a cliffhanger.
  * `burn_argv` -- the Veo backend's last step: scale to 1080x1920 and burn
    the ASS captions (and their "Made with AI" tag) with the `ass` filter.
"""
from __future__ import annotations

import asyncio
import os
import re
import shutil
from dataclasses import dataclass

# 60 fps delivery (Miro, 2026-09-27: "do 60fps"): the Graph API's ceiling
# for a Reel. Veo renders 24, so a Veo reel is motion-interpolated up to it
# first (`smooth_argv`); the stills path renders at 60 directly.
FPS = 60
WIDTH, HEIGHT = 1080, 1920
TIMEOUT_S = 600.0
END_CARD_FRAMES = 45            # 1.5 s of PART 1

# drawtext needs a real font file on a box without fontconfig defaults.
# REELS_FONT_FILE wins; otherwise the first of these that exists.
FONT_CANDIDATES: tuple[str, ...] = (
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/dejavu/DejaVuSans-Bold.ttf",
    "/System/Library/Fonts/Supplemental/Arial Black.ttf",
    "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
)

# A path inside a filtergraph is quoted with '...'; these would need a
# second level of escaping, so a path carrying one is refused instead.
_UNSAFE_IN_FILTER = re.compile(r"['\\:;,\[\]]")


class FfmpegError(Exception):
    """ffmpeg is missing, failed, or overran. The message is safe to show."""


def binary(name: str = "ffmpeg") -> str | None:
    return shutil.which(name)


def missing() -> str | None:
    """A reason when ffmpeg or ffprobe is not installed, else None."""
    for name in ("ffmpeg", "ffprobe"):
        if binary(name) is None:
            return f"{name} not installed"
    return None


def font_file() -> str | None:
    chosen = os.getenv("REELS_FONT_FILE", "").strip()
    if chosen:
        return chosen if os.path.isfile(chosen) else None
    for path in FONT_CANDIDATES:
        if os.path.isfile(path):
            return path
    return None


def filter_path(path: str) -> str:
    """`path`, quoted for a filtergraph option, or FfmpegError."""
    if _UNSAFE_IN_FILTER.search(path):
        raise FfmpegError(f"refusing a path ffmpeg's filter syntax would "
                          f"misread: {os.path.basename(path)!r}")
    return f"'{path}'"


NICENESS = 19


def _lowest_priority() -> None:
    """In the child, before exec: renice to the bottom. A box that refuses
    it (no permission) still renders, just at normal priority."""
    try:
        os.nice(NICENESS)
    except OSError:
        pass


async def run(argv: list[str], *, timeout: float = TIMEOUT_S) -> bytes:
    """Run one command; stdout on success, FfmpegError otherwise."""
    if not argv or binary(argv[0]) is None:
        raise FfmpegError(f"{argv[0] if argv else 'ffmpeg'} not installed")
    # Lowest CPU priority, always: the server has one core, and a render --
    # above all the 24 -> 60 fps interpolation, 20-40 minutes a reel -- must
    # never starve the API, the checks or a live phone call of it.
    proc = await asyncio.create_subprocess_exec(
        *argv, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        preexec_fn=_lowest_priority)
    try:
        out, err = await asyncio.wait_for(proc.communicate(), timeout)
    except TimeoutError:
        proc.kill()
        await proc.wait()
        raise FfmpegError(f"{argv[0]} passed {timeout:.0f}s and was stopped") from None
    if proc.returncode != 0:
        tail = " ".join(err.decode("utf-8", "replace").split())[-300:]
        raise FfmpegError(f"{argv[0]} exited {proc.returncode}: {tail}")
    return out


@dataclass(frozen=True, slots=True)
class Capabilities:
    ffmpeg: bool
    drawtext: bool
    ass: bool
    zoompan: bool
    reason: str | None = None       # why something could not be confirmed


async def capabilities() -> Capabilities:
    """What this ffmpeg can do, read from `ffmpeg -filters`. A filter it
    does not list is not confirmed -- the check says so rather than assume
    the build has libass or libfreetype."""
    why = missing()
    if why:
        return Capabilities(False, False, False, False, why)
    try:
        out = await run(["ffmpeg", "-hide_banner", "-filters"], timeout=20)
    except FfmpegError as exc:
        return Capabilities(True, False, False, False, str(exc))
    names = {parts[1] for line in out.decode("utf-8", "replace").splitlines()
             if len(parts := line.split()) >= 2}
    return Capabilities(True, "drawtext" in names, "ass" in names,
                        "zoompan" in names)


async def duration(path: str) -> float:
    out = await run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                     "-of", "default=noprint_wrappers=1:nokey=1", path], timeout=30)
    try:
        return float(out.decode().strip())
    except ValueError:
        raise FfmpegError("ffprobe gave no readable duration") from None


# ------------------------------------------------------------ argv builders

def _encode_tail(out_path: str, preset: str = "medium") -> list[str]:
    # CRF 18 on the medium preset: visually lossless for 1080x1920, where
    # the old veryfast default smeared Veo's detail (2026-09-27, "make the
    # videos clearer"). The files stay well under the platforms' limits.
    return ["-c:v", "libx264", "-preset", preset, "-crf", "18",
            "-pix_fmt", "yuv420p",
            "-r", str(FPS), "-c:a", "aac", "-b:a", "128k",
            "-movflags", "+faststart", out_path]


def _drawtext(textfile: str, font: str | None, *, size: int, x: str, y: str,
              box: bool = True) -> str:
    opts = [f"textfile={filter_path(textfile)}"]
    if font:
        opts.append(f"fontfile={filter_path(font)}")
    opts += [f"fontsize={size}", "fontcolor=white", f"x={x}", f"y={y}"]
    if box:
        opts += ["box=1", "boxcolor=black@0.5", "boxborderw=14"]
    return "drawtext=" + ":".join(opts)


def stills_argv(scenes: list[str], frames: list[int], audio: str, out_path: str,
                *, label_file: str, font: str | None,
                scene_text: list[str | None] | None = None,
                end_card: str | None = None, end_text: str | None = None
                ) -> list[str]:
    """The local backend's one command. `scenes` are image paths, one per
    scene, each fed as a single input frame; `frames[i]` is how many output
    frames zoompan makes from scene i. `scene_text[i]` is a textfile drawn
    over that scene (the typographic cards, which have no picture of their
    own). `end_card` + `end_text` add the PART 1 card."""
    if len(scenes) != len(frames) or not scenes:
        raise FfmpegError("one frame count per scene is required")
    argv = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error"]
    for path in scenes:
        argv += ["-i", path]
    audio_index = len(scenes)
    argv += ["-i", audio]
    if end_card:
        argv += ["-i", end_card]
    chains, labels = [], []
    big = f"{WIDTH * 2}:{HEIGHT * 2}"
    for i, count in enumerate(frames):
        chain = (f"[{i}:v]scale={big}:force_original_aspect_ratio=increase,"
                 f"crop={big},zoompan=z='min(zoom+0.0006,1.15)':"
                 f"x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d={max(1, count)}:"
                 f"s={WIDTH}x{HEIGHT}:fps={FPS},setsar=1")
        text = (scene_text or [None] * len(scenes))[i]
        if text:
            chain += "," + _drawtext(text, font, size=64, x="(w-tw)/2",
                                     y="(h-th)/2", box=False)
        chains.append(chain + f"[v{i}]")
        labels.append(f"[v{i}]")
    if end_card and end_text:
        idx = audio_index + 1
        chains.append(
            f"[{idx}:v]scale={big},zoompan=z='1':d={END_CARD_FRAMES}:"
            f"s={WIDTH}x{HEIGHT}:fps={FPS},setsar=1,"
            + _drawtext(end_text, font, size=140, x="(w-tw)/2", y="(h-th)/2",
                        box=False) + "[vend]")
        labels.append("[vend]")
    chains.append("".join(labels) + f"concat=n={len(labels)}:v=1:a=0[vc]")
    chains.append("[vc]" + _drawtext(label_file, font, size=40, x="w-tw-36",
                                     y="56") + "[v]")
    argv += ["-filter_complex", ";".join(chains),
             "-map", "[v]", "-map", f"{audio_index}:a", "-af", LOUDNORM]
    return argv + _encode_tail(out_path)


def concat_list(clips: list[str]) -> str:
    """The concat demuxer's list file: one `file '<path>'` line per clip,
    in story order. Paths are ours (a temp dir), refused if they carry a
    quote the list syntax would misread."""
    for path in clips:
        if "'" in path or "\n" in path:
            raise FfmpegError(f"refusing a clip path the concat list would "
                              f"misread: {os.path.basename(path)!r}")
    return "".join(f"file '{path}'\n" for path in clips)


def concat_argv(list_file: str, out_path: str) -> list[str]:
    """Join Veo's clips, one per beat, into ONE re-encoded stream -- a
    stream copy across separately generated clips can carry mismatched
    timestamps into the burn step."""
    return (["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-f", "concat",
             "-safe", "0", "-i", list_file] + _encode_tail(out_path))


def audio_argv(video: str, out_path: str) -> list[str]:
    """The joined clip's sound, small enough for a transcription upload."""
    return ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i", video,
            "-vn", "-ac", "1", "-ar", "16000", "-c:a", "libmp3lame", "-b:a", "64k",
            out_path]


AMBIENCE_VOLUME = 0.2


def narrate_argv(video: str, narration: str, out_path: str, *,
                 video_seconds: float, narration_seconds: float) -> list[str]:
    """Lay the chosen Fish voice over Veo's clips: Veo's own sound stays
    under it as ambience at a fifth of its volume, and when the narration
    runs past the picture the last frame holds until the voice is done.
    `-shortest` is deliberately absent -- the voice is the thing here."""
    pad = max(0.0, narration_seconds - video_seconds)
    total = max(video_seconds, narration_seconds)
    graph = (f"[0:v]tpad=stop_mode=clone:stop_duration={pad:.3f}[v];"
             f"[0:a]volume={AMBIENCE_VOLUME},apad[bg];"
             f"[bg][1:a]amix=inputs=2:duration=longest:dropout_transition=0,"
             f"atrim=0:{total:.3f}[a]")
    return (["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
             "-i", video, "-i", narration, "-filter_complex", graph,
             "-map", "[v]", "-map", "[a]", "-t", f"{total:.3f}"]
            + _encode_tail(out_path))


def replace_audio_argv(video: str, narration: str, out_path: str, *,
                       video_seconds: float, narration_seconds: float
                       ) -> list[str]:
    """The same, for a clip with no sound of its own: the voice is the
    whole track."""
    pad = max(0.0, narration_seconds - video_seconds)
    total = max(video_seconds, narration_seconds)
    return (["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
             "-i", video, "-i", narration,
             "-vf", f"tpad=stop_mode=clone:stop_duration={pad:.3f}",
             "-map", "0:v", "-map", "1:a", "-t", f"{total:.3f}"]
            + _encode_tail(out_path))


# Social loudness (EBU R128 as the platforms apply it): -14 LUFS integrated,
# -1.5 dBTP. Without it a Veo clip and a Fish narration land at whatever
# level they came out, and the feed turns the quiet ones down to nothing.
LOUDNORM = "loudnorm=I=-14:TP=-1.5:LRA=11"
PUNCH = 0.05          # a 5% zoom punch on each new beat
PUNCH_S = 0.35        # held for the first third of a second
UHD_W, UHD_H = 2160, 3840


def export_4k() -> bool:
    """`REELS_EXPORT_4K` (on unless set to 0/false/no, Miro 2026-09-27: "make
    each reel 4K"): beside the 1080x1920 delivery file, write a 2160x3840
    ARCHIVE MASTER (lanczos + a light unsharp). It is an UPSCALE of Veo's
    native frames, and the card says so -- "4K master upscaled from 1080p",
    never native 4K. The master is never what gets posted: Instagram's
    Graph API refuses a Reel wider than 1920 px and above 60 fps, so the
    delivery file stays 1080x1920 at 60 fps."""
    return os.getenv("REELS_EXPORT_4K", "1").strip().lower() not in (
        "0", "false", "no", "off")


def _burn_vf(ass_file: str, beat_seconds: float | None, *, uhd: bool) -> str:
    vf = (f"scale={WIDTH}:{HEIGHT}:force_original_aspect_ratio=decrease,"
          f"pad={WIDTH}:{HEIGHT}:(ow-iw)/2:(oh-ih)/2,setsar=1")
    if beat_seconds:
        z = f"(1+{PUNCH}*lt(mod(t\\,{beat_seconds:g})\\,{PUNCH_S}))"
        vf += (f",scale=w='iw*{z}':h='ih*{z}':eval=frame,"
               f"crop={WIDTH}:{HEIGHT}")
    if uhd:
        vf += f",scale={UHD_W}:{UHD_H}:flags=lanczos,unsharp=5:5:0.6"
    return vf + f",ass={filter_path(ass_file)}"


def burn_argv(video: str, ass_file: str, out_path: str, *,
              beat_seconds: float | None = None, uhd: bool = False) -> list[str]:
    """Scale to 1080x1920 (the delivery file; `uhd` for the 4K archive
    master), punch in at each beat when the beats have a fixed length, burn
    the captions with libass last so the text is drawn at the output
    resolution, and normalise the loudness."""
    vf = _burn_vf(ass_file, beat_seconds, uhd=uhd)
    # The master is an archive copy of an upscale: `veryfast` at CRF 18
    # keeps it visually clean at a fraction of `medium`'s time on the
    # server's single core; the posted file keeps `medium`.
    return (["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i", video,
             "-vf", vf, "-af", LOUDNORM]
            + _encode_tail(out_path, "veryfast" if uhd else "medium"))


def normalize_argv(clip: str, out_path: str) -> list[str]:
    """A clip from another generator (Higgsfield) made to match Veo's before
    it is banked: WIDTHxHEIGHT (letterboxed, never stretched), square pixels,
    48 kHz stereo. Otherwise one reel would join clips of different sizes
    and sample rates, which the concat step does not reconcile. The frame
    rate is left as generated, so the 60 fps smoothing still has real
    motion to interpolate from."""
    vf = (f"scale={WIDTH}:{HEIGHT}:force_original_aspect_ratio=decrease,"
          f"pad={WIDTH}:{HEIGHT}:(ow-iw)/2:(oh-ih)/2,setsar=1")
    return ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i", clip,
            "-vf", vf, "-c:v", "libx264", "-preset", "medium", "-crf", "18",
            "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "128k", "-ar", "48000",
            "-ac", "2", "-movflags", "+faststart", out_path]


def trim_argv(clip: str, seconds: float, out_path: str) -> list[str]:
    """The first `seconds` of one Veo clip, re-encoded, so a calendar beat
    shorter than Veo's 8 s lasts as long as the script says."""
    return (["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i", clip,
             "-t", f"{seconds:.3f}"] + _encode_tail(out_path))


# Motion-compensated interpolation, 24 -> 60 fps: in-between frames are
# computed from the motion, not cross-faded or duplicated. Measured on a
# 10-core Mac, 2026-09-27: 161 s of CPU for 8 s of 1080x1920 -- so it runs
# ONCE per reel, before the delivery and master burns, with its own long
# deadline instead of TIMEOUT_S.
SMOOTH = ("minterpolate=fps=60:mi_mode=mci:mc_mode=aobmc:me_mode=bidir:"
          "vsbmc=1")
SMOOTH_TIMEOUT_S = 3600.0
# A 60 fps reel of up to 70 s on the server's one core, at nice 19: the
# delivery burn and the 4K master get their own deadlines, not TIMEOUT_S.
BURN_TIMEOUT_S = 3600.0
MASTER_TIMEOUT_S = 3 * 3600.0


def smooth_argv(video: str, out_path: str) -> list[str]:
    """The joined reel at 60 fps, motion-interpolated; the sound untouched."""
    return (["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i", video,
             "-vf", SMOOTH] + _encode_tail(out_path))


async def smooth(video: str, out_path: str) -> tuple[str, str]:
    """(the file to burn from, the note for the card). A failure or an
    overrun never fails the reel: it is burned from the native-motion join
    instead, and the note says so."""
    try:
        await run(smooth_argv(video, out_path), timeout=SMOOTH_TIMEOUT_S)
    except FfmpegError as exc:
        return video, (f"60 fps smoothing NOT applied ({exc}); native 24 fps "
                       f"motion in a 60 fps file")
    return out_path, "motion-interpolated from 24 to 60 fps"


async def probe(path: str) -> dict:
    """What a finished file actually is, read by ffprobe from its streams.
    FfmpegError when it cannot be read -- never a guessed value.

    {width, height, seconds, audio} as before, plus (RA-E5):
      video          a video stream exists
      video_seconds  that stream's own duration (0.0 when it has none) --
                     the playable length, not the container's claim
      rotation       degrees of display rotation (side data or `rotate`
                     tag); a 1920x1080 frame rotated to portrait is not a
                     native 1080x1920 render
      subtitles      a subtitle stream exists"""
    entries = ("format=duration:stream=codec_type,width,height,duration"
               ":stream_tags=rotate:stream_side_data=rotation")
    out = await run(["ffprobe", "-v", "error", "-show_entries", entries,
                     "-of", "json", path], timeout=30)
    return parse_probe(out)


def parse_probe(out: bytes) -> dict:
    import json as _json
    try:
        data = _json.loads(out.decode("utf-8", "replace"))
    except ValueError as exc:
        raise FfmpegError("ffprobe returned unreadable output") from exc
    streams = data.get("streams") or []
    video = next((s for s in streams if s.get("codec_type") == "video"), None)
    v = video or {}
    rotation = 0
    for side in v.get("side_data_list") or []:
        if side.get("rotation") not in (None, ""):
            rotation = int(float(side["rotation"]))
    if not rotation and (v.get("tags") or {}).get("rotate"):
        rotation = int(float(v["tags"]["rotate"]))

    def seconds(value: object) -> float:
        try:
            return float(value or 0)                        # type: ignore[arg-type]
        except (TypeError, ValueError):
            return 0.0

    return {"width": int(v.get("width") or 0),
            "height": int(v.get("height") or 0),
            "seconds": seconds((data.get("format") or {}).get("duration")),
            "audio": any(s.get("codec_type") == "audio" for s in streams),
            "video": video is not None,
            "video_seconds": seconds(v.get("duration")),
            "rotation": rotation % 360,
            "subtitles": any(s.get("codec_type") == "subtitle" for s in streams)}


SILENT_DB = -60.0
_MEAN = re.compile(r"mean_volume:\s*(-?[\d.]+|-inf) dB")


async def mean_volume(path: str) -> float:
    """The file's mean audio level in dB (volumedetect). FfmpegError when it
    cannot be read; -inf for digital silence. Relayed rule, 2026-09-28: a
    render whose sound is silent never posts."""
    if binary("ffmpeg") is None:
        raise FfmpegError("ffmpeg not installed")
    proc = await asyncio.create_subprocess_exec(
        "ffmpeg", "-hide_banner", "-nostats", "-i", path, "-vn",
        "-af", "volumedetect", "-f", "null", "-",
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        preexec_fn=_lowest_priority)
    try:
        _out, err = await asyncio.wait_for(proc.communicate(), 120)
    except TimeoutError:
        proc.kill()
        await proc.wait()
        raise FfmpegError("volumedetect passed 120s and was stopped") from None
    found = _MEAN.search(err.decode("utf-8", "replace"))
    if proc.returncode != 0 or not found:
        raise FfmpegError("the render's sound level could not be read")
    return float("-inf") if found.group(1) == "-inf" else float(found.group(1))


# Motion (RA-E5, plan D09; Miro's rule: real motion video only, never stills
# or slides). `freezedetect` reports each stretch whose frames stay within
# FREEZE_NOISE_DB of the stretch's first frame for at least FREEZE_MIN_S.
# The frames are resampled to MOTION_FPS first, so a 24 fps Veo clip and
# its 60 fps interpolated delivery file read the same.
#
# Calibrated 2026-09-28 with exactly this chain (fps=12, d=2) on 8 distinct
# Veo talking-character clips (720x1280, 24 fps), a 60 fps minterpolated
# 1080x1920 copy of one, the captioned clips and two joined reels: zero
# frozen seconds at -60, -50, -45 and -40 dB. The first Veo freeze seen at
# all was at -35 dB, and that was at native rate with d=1. So -50 dB leaves
# at least 10 dB of measured headroom. A 3-still slideshow encoded at CRF 18,
# with or without burned captions changing under it, is 100% frozen at
# -50 dB. NOT caught: a zoompan (Ken Burns) slideshow -- a production-style
# one read 0 s frozen -- nor stills held under FREEZE_MIN_S each.
MOTION_FPS = 12
FREEZE_NOISE_DB = -50
FREEZE_MIN_S = 2.0
MOTION_TIMEOUT_S = 900.0
_FREEZE = re.compile(r"lavfi\.freezedetect\.freeze_(start|duration|end):\s*([\d.]+)")


def motion_argv(path: str) -> list[str]:
    return ["ffmpeg", "-hide_banner", "-nostats", "-i", path, "-map", "0:v:0",
            "-vf", (f"fps={MOTION_FPS},freezedetect=n={FREEZE_NOISE_DB}dB:"
                    f"d={FREEZE_MIN_S:g}"),
            "-an", "-f", "null", "-"]


def parse_frozen(log: str, seconds: float) -> float:
    """Frozen seconds from freezedetect's log. A freeze still open when the
    stream ends prints only its `freeze_start` (ffmpeg 7.0): it runs to the
    end of the video, `seconds`."""
    total, open_at = 0.0, None
    for kind, value in _FREEZE.findall(log):
        at = float(value)
        if kind == "start":
            open_at = at
        elif kind == "duration":
            total += at
        else:
            open_at = None
    if open_at is not None:
        total += max(0.0, seconds - open_at)
    return min(total, seconds) if seconds > 0 else total


async def frozen_seconds(path: str, seconds: float) -> float:
    """How many of the video's `seconds` are frozen, read from the file by
    freezedetect. FfmpegError when it cannot be read -- never 0.0 on a
    guess."""
    if binary("ffmpeg") is None:
        raise FfmpegError("ffmpeg not installed")
    proc = await asyncio.create_subprocess_exec(
        *motion_argv(path), stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE, preexec_fn=_lowest_priority)
    try:
        _out, err = await asyncio.wait_for(proc.communicate(), MOTION_TIMEOUT_S)
    except TimeoutError:
        proc.kill()
        await proc.wait()
        raise FfmpegError(f"freezedetect passed {MOTION_TIMEOUT_S:.0f}s and was "
                          f"stopped") from None
    text = err.decode("utf-8", "replace")
    if proc.returncode != 0:
        tail = " ".join(text.split())[-300:]
        raise FfmpegError(f"ffmpeg exited {proc.returncode}: {tail}")
    return parse_frozen(text, seconds)
