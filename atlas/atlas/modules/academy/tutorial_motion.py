"""How the ATLAS Academy tutorials move: the motion presets, in frames.

Miro, 2026-10-15: "apply this motion and editing style" -- the Atlas Academy
Video Motion and Editing Guide (PDF, 2026-10-14) and its 30 s motion sample.
docs/academy-motion-guide.md maps every rule of the guide to what is built
here and what is not. In short, the sample's grammar:

  * a step changes by a short crossfade, never a slide or a wipe;
  * what is new rises a few pixels into place as it fades in, with an eased
    stop, and only what is new moves (the guide: "animate only the active
    step"; "one moving focal point at a time");
  * a workflow is traced once: one dot runs along one connector and stops
    at the next node, then the node lights;
  * a live demo cuts between its steps (a software state is never
    dissolved) and pushes in on the region it shows when that region moves;
  * the presenter's name enters as a lower third and leaves after about
    three seconds; the end card fades in and stays to the last frame.

Every value is a starting point the guide gives for a 30 fps project, kept
inside the guide's range (`GUIDE_RANGES`, tested). Nothing here runs on a
timer: each motion is tied to the step that changes something.

Cost (the server is one CPU). The slide is still one transparent PNG per
step, decoded once. What enters is cut from the same page as a small PNG
the size of the element, looped only for its few animation frames and then
held by `overlay=eof_action=repeat`; the trace dot is a 36 px PNG; the
demo push-in runs `zoompan` on FOCUS_FRAMES frames only and the plain crop
after. `budget()` is what the tests hold every segment to.
"""
from __future__ import annotations

from dataclasses import dataclass

from . import video_tokens as vt

FPS = vt.FPS
VERSION = "1"                        # in tutorial_design.DESIGN_VERSION's history

# ---------------------------------------------------------------- presets
# The guide's table ("03 / Motion presets"), 30 fps, now read from the video
# system's duration tokens (video_tokens, 2026-10-17): every preset is one of
# them. The checkpoint tag, the lower third and the outro fade are
# `title-rise` (9 frames) there, where the guide said 8, 8 and 8-12.
CROSSFADE_FRAMES = vt.CROSSFADE_FRAMES        # 7 (6-8): between explanation graphics
RISE_FRAMES = vt.TITLE_RISE_FRAMES            # 9 (8-10): title fade + rise
RISE_PX = vt.TITLE_RISE_PX                    # 16 px of travel
CHECK_FRAMES = vt.TITLE_RISE_FRAMES           # 9: checkpoint tag, a fade with no travel
TRACE_FRAMES = vt.TRACE_FRAMES                # 15 (12-18) along one connector
HOLD_FRAMES = vt.HOLD_TAG_FRAMES              # 90: a tag or lower third, read
FOCUS_FRAMES = vt.FOCUS_ZOOM_FRAMES           # 11 (9-12): focus zoom
FOCUS_SCALE = vt.FOCUS_ZOOM_SCALE             # from 115% of the region to 100%
LOWER_IN, LOWER_HOLD, LOWER_OUT = (vt.TITLE_RISE_FRAMES, vt.HOLD_TAG_FRAMES,
                                   vt.TITLE_RISE_FRAMES)  # 9, 3 s, 9
LOWER_AT = 9                         # the plate enters this many frames into the clip
OUTRO_FADE_FRAMES = vt.TITLE_RISE_FRAMES      # 9: into the end card, which then stays
# The 3D backdrop changing topic is not in the guide's table: it is the
# background, not a focal point, so it runs as long as an outro fade.
BACKDROP_FRAMES = 12

GUIDE_RANGES: dict[str, tuple[float, float, float]] = {
    # name: (value, least, most), as the guide states them
    "crossfade": (CROSSFADE_FRAMES, 6, 8),
    "rise": (RISE_FRAMES, 8, 10),
    "rise_px": (RISE_PX, 12, 20),
    "checkpoint": (CHECK_FRAMES, 8, 10),        # title-rise's 8-10 since 2026-10-17
    "trace": (TRACE_FRAMES, 12, 18),
    "focus": (FOCUS_FRAMES, 9, 12),
    "focus_scale": (FOCUS_SCALE, 1.15, 1.15),
    "lower_in": (LOWER_IN, 8, 10),
    "lower_hold": (LOWER_HOLD, 75, 105),       # "about 3 s"
    "lower_out": (LOWER_OUT, 8, 10),
    "outro_fade": (OUTRO_FADE_FRAMES, 8, 12),
}


def sec(frames: float) -> float:
    """Frames at FPS as seconds, to the millisecond."""
    return round(frames / FPS, 3)


def ease(start: float, length: float, var: str = "t") -> str:
    """An ffmpeg expression for the eased progress (0 -> 1) of a motion that
    starts at `start` s and lasts `length` s: cubic ease-out, a gentle stop.
    Settles at exactly 1 and stays there."""
    return f"(1-pow(1-clip(({var}-{start:g})/{length:g},0,1),3))"


def rise_y(y: int, start: float, length: float = sec(RISE_FRAMES),
           px: int = RISE_PX, out: float | None = None) -> str:
    """The overlay y of a layer rising `px` into place at `y`, and, from
    `out`, rising `px` on out of it."""
    if px == 0:
        return str(y)
    leave = f"-{px}*{ease(out, length)}" if out is not None else ""
    return f"{y}+{px}*(1-{ease(start, length)}){leave}"


# --------------------------------------------------------------- planning

@dataclass(frozen=True)
class Enter:
    """One element entering: its PNG (cut to its box) and where it lands."""
    png: str
    x: int
    y: int
    start: float                     # seconds into the segment
    frames: int = RISE_FRAMES
    px: int = RISE_PX                # 0: a checkpoint fade, no travel
    # When it leaves (seconds into the segment), rising out over `frames`;
    # None = it stays to the end. The LowerThird and the CheckpointTag leave
    # after `hold-tag` (docs/academy-video-system: title-rise in, hold-tag,
    # title-rise out).
    out: float | None = None

    @property
    def end(self) -> float:
        return round(self.start + sec(self.frames), 3)

    @property
    def gone(self) -> float | None:
        """When it has left, or None when it stays."""
        return None if self.out is None else round(self.out + sec(self.frames), 3)


@dataclass(frozen=True)
class Trace:
    """The dot running along the connectors from (x0, y) to (x1, y)."""
    png: str
    x0: int
    x1: int
    y: int
    start: float
    hops: int = 1                    # connectors run, TRACE_FRAMES each

    @property
    def end(self) -> float:
        return round(self.start + sec(TRACE_FRAMES * self.hops), 3)


@dataclass(frozen=True)
class Box:
    """A region on the page, in CSS pixels (the slide is 1:1)."""
    x: int
    y: int
    w: int
    h: int


@dataclass(frozen=True)
class Spec:
    """What moves on one slide: CSS selectors per group, in the order they
    enter (the heading first, then the new item), whether each is a
    checkpoint fade, and the trace (from node index -> to node index)."""
    groups: tuple[tuple[str, bool], ...] = ()     # (selector, checkpoint)
    trace: tuple[int, int] | None = None
    # Groups (by selector) that leave HOLD_FRAMES after they settle: a
    # LowerThird or a CheckpointTag. The slide the next step fades from is
    # drawn without them.
    exits: tuple[str, ...] = ()
    # WorkflowTrace: the value-tag the trace carries instead of the bare dot
    # ("" = the dot), copied from the executed workflow.
    token: str = ""

    def __bool__(self) -> bool:
        return bool(self.groups) or self.trace is not None


STILL = Spec()


def schedule(spec: Spec, *, crossfade: bool) -> list[tuple[str, float, int]]:
    """When each motion of `spec` runs, in sequence, one at a time: (what,
    start seconds, frames). The crossfade first (when there is a step
    before), then the heading, then the trace, then the new item. Nothing
    overlaps: one moving focal point at a time."""
    out: list[tuple[str, float, int]] = []
    t = sec(CROSSFADE_FRAMES) if crossfade else sec(2)
    groups = list(spec.groups)
    # The heading (group 0 when there are two) rises before the trace.
    if len(groups) == 2:
        sel, check = groups.pop(0)
        frames = CHECK_FRAMES if check else RISE_FRAMES
        out.append((sel, t, frames))
        t = round(t + sec(frames), 3)
    if spec.trace is not None:
        a, b = spec.trace
        frames = TRACE_FRAMES * max(1, abs(b - a))
        out.append(("trace", t, frames))
        t = round(t + sec(frames), 3)
    for sel, check in groups:
        frames = CHECK_FRAMES if check else RISE_FRAMES
        out.append((sel, t, frames))
        t = round(t + sec(frames), 3)
    return out


def settles_by(spec: Spec, *, crossfade: bool) -> float:
    """When the last motion of `spec` has stopped (0 for a still)."""
    plan = schedule(spec, crossfade=crossfade)
    return max((round(s + sec(f), 3) for _, s, f in plan), default=0.0)


# A segment too short to move in and still hold the result this long
# afterwards is drawn whole from its first frame (the guide: "hold the
# important information").
HOLD_AFTER = 1.0


def fits(spec: Spec, seconds: float, *, crossfade: bool) -> bool:
    return settles_by(spec, crossfade=crossfade) + HOLD_AFTER <= seconds


# ------------------------------------------------------------- demo focus

def focus_box(box: tuple[int, int, int, int], frame_w: int, frame_h: int
              ) -> tuple[int, int, int, int]:
    """The region FOCUS_SCALE times `box` around its centre, 16:9 like it,
    pulled inside the recording: where a demo step's push-in starts."""
    x, y, w, h = box
    w, h = min(w, frame_w), min(h, frame_h)
    x, y = max(0, min(x, frame_w - w)), max(0, min(y, frame_h - h))
    big_w = min(frame_w, round(w * FOCUS_SCALE / 16) * 16)
    big_h = min(frame_h, big_w * h // w)
    big_h -= big_h % 2
    cx, cy = x + w / 2, y + h / 2
    bx = int(max(0, min(cx - big_w / 2, frame_w - big_w)))
    by = int(max(0, min(cy - big_h / 2, frame_h - big_h)))
    return bx - bx % 2, by - by % 2, big_w, big_h


def focus_filter(box: tuple[int, int, int, int], frame_w: int, frame_h: int,
                 out_w: int, out_h: int) -> str:
    """The push-in's first FOCUS_FRAMES frames: cut the wider region, then
    `zoompan` eases it down to `box` exactly (zoom = visible width ratio,
    x/y the box's offset inside the wider cut, both linear in the eased
    progress, so the rectangle interpolates without a wobble)."""
    x, y, w, h = box
    w, h = min(w, frame_w), min(h, frame_h)
    x, y = max(0, min(x, frame_w - w)), max(0, min(y, frame_h - h))
    bx, by, bw, bh = focus_box(box, frame_w, frame_h)
    e = ease(0, FOCUS_FRAMES - 1, var="on")
    shrink = 1 - w / bw
    return (f"crop={bw}:{bh}:{bx}:{by},"
            f"zoompan=z='1/(1-{shrink:.5f}*{e})':x='{x - bx}*{e}':y='{y - by}*{e}'"
            f":d=1:s={out_w}x{out_h}:fps={FPS}")


# ----------------------------------------------------------------- budget

@dataclass(frozen=True)
class Cost:
    """What a segment's ffmpeg command asks of every frame: full-frame
    overlays (`overlay=0:0`), stills looped for a few frames (`-loop 1 ...
    -t`: the step before fading out, each entering element), frames through
    a per-frame resampler (zoompan) and encodes."""
    full_overlays: int
    timed_layers: int
    zoom_frames: int
    encodes: int


def budget(argv: list[str]) -> Cost:
    """Read a segment's argv back into its Cost (for the tests and the
    render-cost report): counts what the filter graph asks of every frame."""
    graph = argv[argv.index("-filter_complex") + 1] if "-filter_complex" in argv else ""
    full = graph.count("overlay=0:0")
    layer = 0
    for i, a in enumerate(argv):
        if a == "-loop" and i + 5 < len(argv) and argv[i + 4] == "-t":
            layer += 1
    zoom = FOCUS_FRAMES if "zoompan" in graph else 0
    return Cost(full, layer, zoom, argv.count("libx264"))
