"""A NotebookLM lesson's thumbnail frame, picked from its own video (2026-10-20).

the owner, 2026-10-20: every Academy tutorial thumbnail unique and made from the
actual video. A NotebookLM Video Overview is a run of drawn slides; the
drawn navy card made the recent lessons look alike.

  Candidates  CANDIDATES evenly spaced times between HEAD and TAIL of the
              video (its intro and recap/end card left out), each read as
              one keyframe (-skip_frame nokey: fast on a 4K file, and a
              keyframe of a slide video is a slide fully drawn) scaled to
              ANALYSE_W x ANALYSE_H grayscale through ffmpeg's stdout.
  Score       richness(): pure, on those bytes. A frame that is dark (a
              fade), flat (a blank or a solid card) or nearly edge-free is
              rejected; the rest score on histogram entropy and edge
              density. A frame past EARLY of the length (the recap and quiz
              cards) gives up LATE_PENALTY; scores within TIE of each other
              are a tie, and a tie goes to the earlier candidate.
  Unique      a 64-bit dHash (9x8 grayscale) of each candidate; one closer
              than THRESHOLD bits to another lesson's stored frame hash is
              passed over for the next best. When none is far enough the
              best is kept and the caller is told (never silently).
  THRESHOLD   10 of 64 bits: dHash distances of 0-5 are the same picture
              re-encoded or rescaled, 6-9 near-duplicates (the same slide
              with a word changed), 10+ different pictures. NotebookLM's
              slides share a paper background, so this is the floor that
              still tells two different slides apart; not yet measured on
              the channel's own frames.

  Cost        one keyframe seek a candidate (the hash is shrunk from the
              analysed frame), each held to the lesson's DEADLINE: a lesson
              that runs past it stops and says so, never hangs the tick.

Every byte of the video is untrusted (invariant 4): only ffmpeg decodes it,
and only numbers come back from it. Every ffmpeg reading it is held to
local files and pipes (-protocol_whitelist file,pipe): a crafted container
cannot make it fetch a URL or open another protocol.
"""
from __future__ import annotations

import asyncio
import json
import math
import time
from dataclasses import dataclass
from pathlib import Path

CANDIDATES = 24
HEAD, TAIL = 0.08, 0.10              # of the length: the intro, the recap and end card
ANALYSE_W, ANALYSE_H = 160, 90
HASH_W, HASH_H = 9, 8
THRESHOLD = 10                       # bits of 64: closer than this is "the same"
FRAME_W, FRAME_H = 1920, 1080        # the winner, for the thumbnail
# Rejections, on 0-255 luma at ANALYSE size.
DARK_MEAN = 24.0                     # a fade to or from black
FLAT_STD = 12.0                      # a blank or solid-colour card
EDGE_STEP = 24                       # a neighbour this much brighter or darker: an edge
MIN_EDGES = 0.015                    # under this share of edges: nothing drawn
BINS = 32
EDGE_FULL = 0.20                     # edge share that counts as fully rich
EARLY = 0.60                         # of the length: past this, recap and quiz cards
LATE_PENALTY = 0.15                  # what a late frame gives up
TIE = 0.02                           # scores this close are a tie: the earlier wins
DEADLINE = 300.0                     # seconds of ffmpeg one lesson may take, in all
RUN_TIMEOUT = 60.0                   # one seek
CUT_TIMEOUT = 120.0                  # the winner, at full size
WHITELIST = ["-protocol_whitelist", "file,pipe"]


@dataclass(frozen=True)
class Candidate:
    index: int
    at: float                        # seconds into the video
    score: float | None              # None: rejected
    why: str = ""                    # why it was rejected
    dhash: int = 0
    pos: float = 0.0                 # share of the video's length


def candidate_times(seconds: float, n: int = CANDIDATES) -> list[float]:
    """n times evenly spread over the middle of the video (HEAD..1-TAIL)."""
    if seconds <= 0 or n <= 0:
        return []
    a, b = seconds * HEAD, seconds * (1.0 - TAIL)
    step = (b - a) / n
    return [round(a + step * (i + 0.5), 3) for i in range(n)]


def richness(gray: bytes, w: int = ANALYSE_W, h: int = ANALYSE_H
             ) -> tuple[float | None, str]:
    """(score, "") for a frame worth showing, else (None, why). Pure."""
    if w <= 1 or h <= 1 or len(gray) < w * h:
        return None, "the frame came back short"
    px = gray[: w * h]
    n = w * h
    mean = sum(px) / n
    std = math.sqrt(sum((p - mean) ** 2 for p in px) / n)
    if mean < DARK_MEAN:
        return None, f"dark (mean {mean:.0f})"
    if std < FLAT_STD:
        return None, f"flat (std {std:.1f})"
    edges = pairs = 0
    for y in range(h):
        row = y * w
        for x in range(w):
            p = px[row + x]
            if x + 1 < w:
                pairs += 1
                edges += abs(p - px[row + x + 1]) >= EDGE_STEP
            if y + 1 < h:
                pairs += 1
                edges += abs(p - px[row + w + x]) >= EDGE_STEP
    share = edges / pairs
    if share < MIN_EDGES:
        return None, f"nearly nothing drawn (edges {share:.3f})"
    hist = [0] * BINS
    for p in px:
        hist[p * BINS // 256] += 1
    entropy = -sum(c / n * math.log2(c / n) for c in hist if c)
    score = 0.5 * entropy / math.log2(BINS) + 0.5 * min(share / EDGE_FULL, 1.0)
    return round(score, 6), ""


def shrink(gray: bytes, w: int = ANALYSE_W, h: int = ANALYSE_H) -> bytes:
    """The HASH_W x HASH_H box average of a w x h grayscale frame. Pure."""
    out = bytearray()
    for y in range(HASH_H):
        y0, y1 = y * h // HASH_H, max((y + 1) * h // HASH_H, y * h // HASH_H + 1)
        for x in range(HASH_W):
            x0, x1 = x * w // HASH_W, max((x + 1) * w // HASH_W, x * w // HASH_W + 1)
            total = sum(gray[r * w + c] for r in range(y0, y1) for c in range(x0, x1))
            out.append(round(total / ((y1 - y0) * (x1 - x0))))
    return bytes(out)


def dhash(gray: bytes, w: int = HASH_W, h: int = HASH_H) -> int:
    """The 64-bit difference hash of a 9x8 grayscale frame: bit set where a
    pixel is brighter than its right neighbour. Pure."""
    out = 0
    for y in range(h):
        for x in range(w - 1):
            out = (out << 1) | int(gray[y * w + x] > gray[y * w + x + 1])
    return out


def hamming(a: int, b: int) -> int:
    return (a ^ b).bit_count()


def hash_hex(v: int) -> str:
    return f"{v:016x}"


def pick(cands: list[Candidate], others: list[int], threshold: int = THRESHOLD
         ) -> tuple[Candidate | None, str]:
    """The best scoring candidate at least `threshold` bits from every other
    lesson's hash, with "" ; when every scored one is too close, the best
    with why; (None, why) when every candidate was rejected. Pure."""
    def rank(c: Candidate) -> tuple[int, int]:
        s = (c.score or 0.0) - (LATE_PENALTY if c.pos > EARLY else 0.0)
        return -round(s / TIE), c.index
    scored = sorted((c for c in cands if c.score is not None), key=rank)
    if not scored:
        why = "; ".join(sorted({c.why for c in cands if c.why})) or "no candidates"
        return None, f"every candidate frame was rejected ({why})"
    for c in scored:
        if all(hamming(c.dhash, o) >= threshold for o in others):
            return c, ""
    best = scored[0]
    near = min(hamming(best.dhash, o) for o in others)
    return best, (f"no candidate frame is {threshold}+ bits from every other lesson's; "
                  f"the best kept at {near} bits")


# ---------------------------------------------------------------- the I/O

def probe_argv(video: str) -> list[str]:
    return ["ffprobe", "-v", "error", *WHITELIST, "-show_entries", "format=duration",
            "-of", "json", "-i", video]


def gray_argv(video: str, at: float, w: int = ANALYSE_W, h: int = ANALYSE_H
              ) -> list[str]:
    return ["ffmpeg", "-hide_banner", "-loglevel", "error", *WHITELIST,
            "-skip_frame", "nokey", "-ss", f"{at:.3f}", "-i", video, "-frames:v", "1",
            "-an", "-vf", f"scale={w}:{h}:flags=area,format=gray", "-f", "rawvideo",
            "pipe:1"]


def frame_argv(video: str, at: float, out: str) -> list[str]:
    """The chosen frame at FRAME_W x FRAME_H, filled and cropped, never stretched."""
    return ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", *WHITELIST,
            "-skip_frame", "nokey", "-ss", f"{at:.3f}", "-i", video, "-frames:v", "1",
            "-an", "-vf",
            (f"scale={FRAME_W}:{FRAME_H}:force_original_aspect_ratio=increase:"
             f"flags=lanczos,crop={FRAME_W}:{FRAME_H},setsar=1"), "-q:v", "3", out]


def image_hash_argv(path: str) -> list[str]:
    return ["ffmpeg", "-hide_banner", "-loglevel", "error", *WHITELIST, "-i", path,
            "-frames:v", "1", "-vf", f"scale={HASH_W}:{HASH_H}:flags=area,format=gray",
            "-f", "rawvideo", "pipe:1"]


def _left(deadline: float, cap: float) -> float | None:
    """Seconds the next run may take, or None once the deadline has passed."""
    left = deadline - time.monotonic()
    return None if left < 1.0 else min(cap, left)


def _late() -> str:
    return f"the lesson passed its deadline of {DEADLINE:.0f}s and was stopped"


async def _seconds(video: Path, deadline: float) -> tuple[float | None, str | None]:
    from ..content import ffmpeg
    left = _left(deadline, 30.0)
    if left is None:
        return None, _late()
    try:
        raw = await ffmpeg.run(probe_argv(str(video)), timeout=left)
        return float(json.loads(raw)["format"]["duration"]), None
    except ffmpeg.FfmpegError as exc:
        return None, f"the video could not be probed: {exc}"
    except (ValueError, KeyError, TypeError):
        return None, "the video could not be probed: no readable duration"


async def candidates(video: Path, n: int = CANDIDATES, deadline: float | None = None
                     ) -> tuple[list[Candidate], str | None]:
    """Each candidate scored and hashed, or ([], why)."""
    from ..content import ffmpeg
    deadline = time.monotonic() + DEADLINE if deadline is None else deadline
    seconds, why = await _seconds(video, deadline)
    if seconds is None:
        return [], why
    times = candidate_times(seconds, n)
    if not times:
        return [], "the video has no length"
    out: list[Candidate] = []
    for i, at in enumerate(times):
        left = _left(deadline, RUN_TIMEOUT)
        if left is None:
            return [], _late()
        try:
            gray = await ffmpeg.run(gray_argv(str(video), at), timeout=left)
        except ffmpeg.FfmpegError as exc:
            out.append(Candidate(i, at, None, f"not read: {str(exc)[:80]}",
                                 pos=at / seconds))
            continue
        score, why = await asyncio.to_thread(richness, gray)
        small = shrink(gray) if score is not None else b""
        out.append(Candidate(i, at, score, why, dhash(small) if small else 0,
                             pos=at / seconds))
    return out, None


async def frame_hash_at(video: Path, at: float, timeout: float = RUN_TIMEOUT
                        ) -> int | None:
    """The dHash of the video's keyframe at `at`, made as candidates() makes it."""
    from ..content import ffmpeg
    try:
        gray = await ffmpeg.run(gray_argv(str(video), at), timeout=timeout)
    except ffmpeg.FfmpegError:
        return None
    if len(gray) < ANALYSE_W * ANALYSE_H:
        return None
    return dhash(await asyncio.to_thread(shrink, gray))


async def image_dhash(path: Path) -> int | None:
    """The dHash of an image file (the finished thumbnail), or None."""
    from ..content import ffmpeg
    try:
        small = await ffmpeg.run(image_hash_argv(str(path)), timeout=RUN_TIMEOUT)
    except ffmpeg.FfmpegError:
        return None
    return dhash(small) if len(small) >= HASH_W * HASH_H else None


async def choose(video: Path, others: list[int], out: Path,
                 deadline: float | None = None
                 ) -> tuple[Candidate | None, str, str | None]:
    """Pick the frame and write it to `out` (JPEG, FRAME_W x FRAME_H).
    (candidate, note, None) or (None, "", why). `note` is non-empty when
    the frame kept is not unique enough."""
    from ..content import ffmpeg
    deadline = time.monotonic() + DEADLINE if deadline is None else deadline
    cands, why = await candidates(video, deadline=deadline)
    if why is not None:
        return None, "", why
    best, note = pick(cands, others)
    if best is None:
        return None, "", note
    left = _left(deadline, CUT_TIMEOUT)
    if left is None:
        return None, "", _late()
    try:
        await ffmpeg.run(frame_argv(str(video), best.at, str(out)), timeout=left)
    except ffmpeg.FfmpegError as exc:
        return None, "", f"the frame could not be cut: {exc}"
    if not await asyncio.to_thread(lambda: out.is_file() and out.stat().st_size > 0):
        return None, "", "the frame was not written"
    return best, note, None
