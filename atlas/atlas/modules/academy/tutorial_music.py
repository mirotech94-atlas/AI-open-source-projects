"""The quiet ambient bed under the brand sting and the end card of every
Academy tutorial, and nowhere else.

Miro, 2026-10-17 (docs/academy-video-system/narration.md, "Delivery":
"Music, when used, sits under the brand sting and the end card only, ducked
to nothing beneath explanations"): explanations are voice only. This
replaced the 2026-10-13 bed under the whole lesson (rule 22 then: "add
background music in the intro and the ending clip and in between; make sure
it isn't too loud").

The music is synthesized by ffmpeg's lavfi generators -- no file, no
licence, no model -- so the same inputs always make the same bed:

  * two slow, detuned sine drones (the root and its fifth), each breathing
    on a 0.05 Hz volume LFO;
  * four chord tones above them, one coming to the front every 16 s on a
    smooth raised-cosine window (a 64 s cycle). The tones never stop or
    change pitch, only their level moves, so a change never clicks;
  * a brown-noise wash low-passed at 400 Hz;
  * mixed, echoed (`aecho`), low-passed at 1800 Hz so it never competes
    with the consonants, and set to a fixed level (BED_GAIN_DB): the
    file's mean sits near BED_LEVEL_DB = -20 dBFS, about where a narrated
    voice does.

`mix_argv` lays it under the finished render: the video copied, the voice
untouched (`normalize=0`). The bed sounds only inside the music `windows`
(the sting, the end card): at BED_DB_OPEN where nobody speaks, ducked by
the voice itself to about BED_DB if narration overlaps a window, faded in
and out over FADE (the `title-rise` token) just inside each window edge.
Outside the windows its samples are multiplied by exactly zero: silence,
not a quieter bed. The duration is the video's.
"""
from __future__ import annotations

import asyncio
import hashlib
import math
import os
import secrets
from collections.abc import Callable
from pathlib import Path

# 2: the 2026-10-17 windowed mix (the bed only under the sting and the end
# card), with the combined bump of 2026-10-18. script_key and ready() read
# it, so the unposted drafts re-render once.
MUSIC_VERSION = "2"
BED_DB = -24.0           # under speech, inside a window
BED_DB_OPEN = -16.0      # inside a window, nobody speaking (Miro 10-13: "isn't too loud")
# The fade at each window edge: the `title-rise` duration token, 300 ms
# (docs/academy-video-system/tokens.json), inside the window.
FADE = 0.3
# The EndCard's span when the outro part is not timed: the last 15 s
# (docs/academy-video-system/components/EndCard/README.md).
END_CARD_FALLBACK = 15.0
RATE = 48000
TIMEOUT_S = 900.0        # one core at nice 19, a 10-minute lesson

# The four roots a series may sit on (name, Hz): A2, G2, B2, C3.
ROOTS: tuple[tuple[str, float], ...] = (("a2", 110.0), ("g2", 98.0),
                                        ("b2", 123.47), ("c3", 130.81))
# Above the drone, the tone that leads each 16 s slot (ratios to the root):
# the major third and sixth, the fourth and the ninth, an octave up.
CHORD = (2.5, 10 / 3, 8 / 3, 9 / 4)
SLOT_S = 16
DETUNE = 1.003           # the fifth drifts against the root: a slow beat
# The bed's fixed level: its mean sits at BED_LEVEL_DB, about a narrated
# voice's, so BED_DB and BED_DB_OPEN read as "below the voice". The layers
# mix to about -24.6 dBFS mean; the gain is applied LAST, because a gain
# inside the chain clipped (measured 2026-10-13: a +24.6 dB amix weight gave
# a 3 dB crest factor instead of 11). Peak after it: about -9 dBFS.
BED_LEVEL_DB = -20.0
BED_GAIN_DB = 4.6

# Ducking (2026-10-13), measured with a male `say -v Daniel` narration at
# -19.5 LUFS (mean -22.3 dBFS, 44.1 kHz stereo, as the render's parts are):
# while he speaks, sidechaincompress's RMS detector reads about
# VOICE_DB = -17.2 dBFS. With threshold 0.045 (20*log10(0.045) = -26.9
# dBFS) and ratio 8 the bed loses (-17.2 - -26.9) * (1 - 1/8) = 9.7 * 0.875
# = 8.5 dB, so -16 dB in the open lands at -24.5 dB under speech (measured
# on the real mix: 8.5 dB). A louder voice ducks it further, never less --
# quieter is the safe side of "make sure it isn't too loud".
DUCK_THRESHOLD = 0.045
DUCK_RATIO = 8.0
VOICE_DB = -17.2


def ducked_db(voice_db: float = VOICE_DB) -> float:
    """The bed's level under a voice whose detector reads `voice_db` (the
    compressor's hard-knee arithmetic; silence never ducks)."""
    over = voice_db - 20 * math.log10(DUCK_THRESHOLD)
    return BED_DB_OPEN - max(0.0, over) * (1 - 1 / DUCK_RATIO)


def root_for(series_key: str) -> int:
    """Which of ROOTS a series sits on: stable across runs and machines
    (sha256, never Python's salted hash)."""
    return hashlib.sha256(series_key.encode()).digest()[0] % len(ROOTS)


def _window(k: int) -> str:
    # raised cosine squared, peaking at slot k of the 64 s cycle; written as
    # a product so the graph needs no escaped comma
    c = f"(0.5+0.5*cos(2*PI*(t-{SLOT_S * k})/{SLOT_S * len(CHORD)}))"
    return f"{c}*{c}"


def graph(seed: int = 0) -> str:
    """The lavfi graph of the bed on ROOTS[seed]."""
    hz = ROOTS[seed % len(ROOTS)][1]
    chord = "+".join(f"{_window(k)}*sin(2*PI*{hz * m:.3f}*t)"
                     for k, m in enumerate(CHORD))
    return (f"sine=f={hz:.3f}:r={RATE},"
            "volume='0.75+0.25*sin(2*PI*0.05*t)':eval=frame[s1];"
            f"sine=f={hz * 1.5 * DETUNE:.3f}:r={RATE},"
            "volume='0.75+0.25*sin(2*PI*0.05*t+PI)':eval=frame[s2];"
            f"aevalsrc='0.12*({chord})':s={RATE}[s3];"
            f"anoisesrc=c=brown:a=0.3:r={RATE}:seed={seed % len(ROOTS)},lowpass=f=400[n];"
            "[s1][s2][s3][n]amix=inputs=4:normalize=0,"
            "aecho=0.8:0.6:1000|1800:0.3|0.2,lowpass=f=1800,"
            f"volume={BED_GAIN_DB:g}dB[out0]")


def bed_argv(seconds: float, out: str, *, seed: int = 0) -> list[str]:
    return ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-f", "lavfi",
            "-i", graph(seed), "-t", f"{seconds:.3f}", "-ac", "2", "-ar", str(RATE),
            "-c:a", "aac", "-b:a", "128k", out]


def bed_path(seconds: float, series_key: str) -> Path:
    from .tutorials import root  # tutorials imports this module
    name = ROOTS[root_for(series_key)][0]
    return (root() / "cache"
            / f"music-{MUSIC_VERSION}-{name}-{math.ceil(seconds)}.m4a")


async def bed(seconds: float, series_key: str) -> str:
    """The bed for a render of `seconds`, rendered once and then read from
    the cache (as fixed_clip's bookends are)."""
    from ..content import ffmpeg
    path = bed_path(seconds, series_key)
    if path.is_file():
        return str(path)
    await asyncio.to_thread(path.parent.mkdir, parents=True, exist_ok=True)
    # ffmpeg picks the muxer from the extension: the temp name keeps .m4a;
    # its token keeps two renders filling the same bed apart
    tmp = path.with_name(f"{path.stem}.{secrets.token_hex(4)}.tmp.m4a")
    await ffmpeg.run(bed_argv(math.ceil(seconds), str(tmp), seed=root_for(series_key)),
                     timeout=TIMEOUT_S)
    await asyncio.to_thread(os.replace, tmp, path)
    return str(path)


Window = tuple[float, float]

# The joined render's sound runs ahead of its own clock: every part is its
# own AAC file, so each one's priming frame (1024 samples) sits under the
# previous part's last packet, at the same timestamp. A player drops that
# overlap; a filter graph does not, so before 2026-10-17 the bed met the
# voice by sample count, about 1.8 s off by the end of a 5-minute lesson
# (measured on n8n #1). That never mattered for a bed under everything; it
# does for a window. So the voice is put on its timestamps first (overlaps
# of 10 ms or more trimmed, as a player does), and the window times -- the
# render's clock -- are the times heard.
VOICE_ON_ITS_CLOCK = "aresample=async=1:min_hard_comp=0.010:first_pts=0"


def windows(seconds: float, *, sting: Window | None = None,
            end_card: Window | None = None) -> list[Window]:
    """Where the bed may sound in a file of `seconds`: the sting's span and
    the end card's, clamped to the file, merged where they touch, in order.
    `end_card` None is the EndCard's own rule, the last END_CARD_FALLBACK
    seconds. Batch 3 of the video system moves the sting (6-7 s): it passes
    its span here."""
    spans = [w for w in (sting, end_card if end_card is not None
                         else (seconds - END_CARD_FALLBACK, seconds)) if w is not None]
    out: list[Window] = []
    for a, b in sorted((max(0.0, a), min(seconds, b)) for a, b in spans):
        if b <= a:
            continue
        if out and a <= out[-1][1]:
            out[-1] = (out[-1][0], max(out[-1][1], b))
        else:
            out.append((a, b))
    return out


def _ramp(w: Window) -> float:
    """The fade at a window's edges: FADE, or half the window if shorter."""
    return min(FADE, (w[1] - w[0]) / 2)


def gain_at(t: float, spans: list[Window]) -> float:
    """The bed's gain (0-1) at `t`: the envelope `envelope()` gives ffmpeg."""
    return sum(min(1.0, max(0.0, min((t - a) / _ramp((a, b)), (b - t) / _ramp((a, b)))))
               for a, b in spans)


def envelope(spans: list[Window]) -> str:
    """`gain_at` as an ffmpeg expression of the sample time t ("0": no
    window, the bed is silent throughout)."""
    terms = [f"clip(min((t-{a:.3f})/{_ramp((a, b)):.3f},({b:.3f}-t)/{_ramp((a, b)):.3f}),0,1)"
             for a, b in spans]
    return "+".join(terms) or "0"


def mix_argv(video: str, bed_file: str, out: str, seconds: float,
             spans: list[Window] | None = None) -> list[str]:
    """The render with the bed under its music windows only (`spans`; None:
    `windows(seconds)`): the picture copied, the voice unchanged, the bed
    ducked by the voice (see VOICE_DB) and then gated per sample by the
    window envelope, so outside the windows it is exact silence. The file
    leaves at RATE, 48 kHz (the delivery spec; video_qa holds anything else),
    though the parts are joined at 44.1 kHz."""
    spans = windows(seconds) if spans is None else spans
    env = envelope(spans)
    g = (f"[1:a]aresample=44100,aformat=channel_layouts=stereo,"
         f"volume={BED_DB_OPEN:g}dB[bed];"
         f"[0:a]{VOICE_ON_ITS_CLOCK},asplit[v][sc];"
         f"[bed][sc]sidechaincompress=threshold={DUCK_THRESHOLD:g}:"
         f"ratio={DUCK_RATIO:g}:attack=20:release=600:makeup=1[duck];"
         f"[duck]aeval='val(0)*({env})|val(1)*({env})':c=same[gated];"
         "[v][gated]amix=inputs=2:duration=first:dropout_transition=0:normalize=0[a]")
    return ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i", video,
            "-i", bed_file, "-filter_complex", g, "-map", "0:v", "-map", "[a]",
            "-c:v", "copy", "-c:a", "aac", "-b:a", "160k", "-ar", str(RATE),
            "-movflags", "+faststart", out]


async def add_bed(video: str, series_key: str, out: str, *,
                  spans: Callable[[float], list[Window]] | None = None) -> float:
    """Write `video` with the bed under its music windows to `out`; the
    measured seconds of `video` (the mix keeps them). `spans` maps those
    seconds to the windows (None: `windows(seconds)`)."""
    from ..content import ffmpeg
    seconds = await ffmpeg.duration(video)
    music = await bed(seconds, series_key)
    got = windows(seconds) if spans is None else spans(seconds)
    await ffmpeg.run(mix_argv(video, music, out, seconds, got), timeout=TIMEOUT_S)
    return seconds
