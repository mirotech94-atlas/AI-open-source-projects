"""The ambient bed under every Academy tutorial (academy/tutorial_music.py;
Miro, 2026-10-13: "add background music in the intro and the ending clip
and in between; make sure it isn't too loud")."""
from __future__ import annotations

import asyncio
import json
import math
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from atlas.modules.academy import tutorial_music as tm
from atlas.modules.academy import tutorial_series as ts
from atlas.modules.content import ffmpeg

HAVE_FFMPEG = shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None


def _graph(argv: list[str]) -> str:
    return argv[argv.index("-i") + 1]


def test_the_bed_argv_is_deterministic_and_synthesized():
    a = tm.bed_argv(300.0, "/tmp/bed.m4a", seed=1)
    assert a == tm.bed_argv(300.0, "/tmp/bed.m4a", seed=1)
    assert a[:2] == ["ffmpeg", "-y"] and a[a.index("-f") + 1] == "lavfi"
    graph = _graph(a)
    for part in ("sine=", "anoisesrc=c=brown", "aecho=0.8:0.6:1000|1800:0.3|0.2",
                 "lowpass=f=1800", f"volume={tm.BED_GAIN_DB:g}dB", "[out0]"):
        assert part in graph, part
    assert a[a.index("-t") + 1] == "300.000"
    assert a[a.index("-ac") + 1] == "2" and a[a.index("-ar") + 1] == "48000"
    assert a[-1] == "/tmp/bed.m4a"
    # the noise is seeded by the root too: the same argv is the same bed
    assert "anoisesrc=c=brown:a=0.3:r=48000:seed=1," in graph
    # a different root is a different bed
    assert _graph(tm.bed_argv(300.0, "/tmp/bed.m4a", seed=2)) != graph


def test_the_root_is_stable_per_series_and_differs_across_series():
    keys = [s.key for s in ts.SERIES]
    assert [tm.root_for(k) for k in keys] == [tm.root_for(k) for k in keys]
    assert all(0 <= tm.root_for(k) < len(tm.ROOTS) for k in keys)
    assert len({tm.root_for(k) for k in keys}) > 1


def test_the_bed_is_cached_by_version_root_and_whole_seconds(tmp_path, monkeypatch):
    monkeypatch.setenv("ACADEMY_TUTORIALS_DIR", str(tmp_path))
    p = tm.bed_path(299.2, "n8n")
    name = tm.ROOTS[tm.root_for("n8n")][0]
    assert p == tmp_path / "cache" / f"music-{tm.MUSIC_VERSION}-{name}-300.m4a"


def test_the_mix_copies_the_video_and_gates_the_ducked_bed_to_its_windows():
    """Rule 22 since 2026-10-17: the bed at the open level, ducked by the
    voice, then multiplied by the window envelope -- no whole-file fades."""
    spans = [(0.0, 1.0), (295.0, 300.0)]
    a = tm.mix_argv("/v.mp4", "/bed.m4a", "/out.mp4", 300.0, spans)
    assert a == tm.mix_argv("/v.mp4", "/bed.m4a", "/out.mp4", 300.0, spans)
    graph = a[a.index("-filter_complex") + 1]
    assert f"volume={tm.BED_DB_OPEN:g}dB" in graph
    assert (f"sidechaincompress=threshold={tm.DUCK_THRESHOLD:g}:ratio={tm.DUCK_RATIO:g}"
            in graph)
    env = tm.envelope(spans)
    assert f"[duck]aeval='val(0)*({env})|val(1)*({env})':c=same[gated]" in graph
    assert "afade" not in graph          # the 10-13 whole-file fades are gone
    assert "normalize=0" in graph and "duration=first" in graph
    assert "-c:v" in a and a[a.index("-c:v") + 1] == "copy"
    assert ["-map", "0:v"] == a[a.index("-map"):a.index("-map") + 2]
    assert a[-1] == "/out.mp4"
    # without spans: the sting-less default, the EndCard's last 15 s
    bare = tm.mix_argv("/v", "/b", "/o", 300.0)
    assert tm.envelope(tm.windows(300.0)) in bare[bare.index("-filter_complex") + 1]


def test_the_fade_is_the_title_rise_token():
    """docs/academy-video-system/tokens.json: title-rise, 300 ms."""
    root = Path(__file__).resolve().parents[1]
    tokens = json.loads((root / "docs/academy-video-system/tokens.json").read_text())
    rise = next(t for t in tokens["duration"]["tokens"] if t["name"] == "title-rise")
    assert rise["value"] == "300ms" and tm.FADE == 0.3


def test_windows_clamp_merge_and_fall_back_to_the_last_fifteen_seconds():
    assert tm.windows(300.0, sting=(0.0, 1.0), end_card=(295.0, 300.0)) == [
        (0.0, 1.0), (295.0, 300.0)]
    assert tm.windows(300.0, sting=(6.0, 7.0)) == [(6.0, 7.0), (285.0, 300.0)]
    # a 2 s file: both windows clamp to it and merge into one
    assert tm.windows(2.0, sting=(0.0, 1.0), end_card=(-3.0, 2.0)) == [(0.0, 2.0)]
    assert tm.windows(300.0, sting=(0.0, 1.0), end_card=(300.0, 310.0)) == [(0.0, 1.0)]


def test_the_fades_hit_the_300_ms_token_inside_each_window_edge():
    spans = [(0.0, 1.0), (295.0, 300.0)]
    g = tm.gain_at
    assert g(0.0, spans) == 0.0 and g(0.15, spans) == pytest.approx(0.5)
    assert g(0.3, spans) == pytest.approx(1.0) and g(0.7, spans) == pytest.approx(1.0)
    assert g(0.85, spans) == pytest.approx(0.5) and g(1.0, spans) == 0.0
    for t in (1.0, 1.5, 100.0, 294.99):           # an explanation: off, not ducked
        assert g(t, spans) == 0.0
    assert g(295.3, spans) == pytest.approx(1.0) and g(299.7, spans) == pytest.approx(1.0)
    assert g(300.0, spans) == 0.0
    # a window under 2 x FADE ramps over half its length, never past an edge
    assert tm.gain_at(0.1, [(0.0, 0.4)]) == pytest.approx(0.5)
    assert tm.gain_at(0.2, [(0.0, 0.4)]) == pytest.approx(1.0)
    # the expression ffmpeg gets is the same envelope
    assert "/0.300" in tm.envelope(spans) and tm.envelope([]) == "0"


def test_a_short_file_never_ramps_past_its_ends():
    graph = tm.mix_argv("/v.mp4", "/b.m4a", "/o.mp4", 2.0)
    assert "clip(min((t-0.000)/0.300,(2.000-t)/0.300),0,1)" in graph[
        graph.index("-filter_complex") + 1]


def test_the_ducking_arithmetic_lands_near_the_level_under_speech():
    """Inside a window: -16 dB open, about -24 dB under a narrated voice
    (Miro 10-13 "isn't too loud": the in-window level stays at the open one)."""
    assert tm.BED_DB_OPEN == -16.0 and tm.BED_DB == -24.0
    assert abs(tm.ducked_db() - tm.BED_DB) < 1.0
    # the comment states the same numbers the code uses
    src = Path(tm.__file__).read_text()
    assert f"threshold {tm.DUCK_THRESHOLD:g}" in src and f"ratio {tm.DUCK_RATIO:g}" in src
    # silence never ducks
    assert tm.ducked_db(-60.0) == tm.BED_DB_OPEN


# ------------------------------------------------------------ real ffmpeg

def _probe(path: Path) -> dict:
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries",
                          "format=duration:stream=codec_type,channels", "-of", "json",
                          str(path)], capture_output=True, check=True, text=True).stdout
    return json.loads(out)


@pytest.mark.skipif(not HAVE_FFMPEG, reason="ffmpeg not installed")
async def test_a_real_bed_renders_three_seconds_of_stereo(tmp_path):
    out = tmp_path / "bed.m4a"
    await ffmpeg.run(tm.bed_argv(3.0, str(out)), timeout=60)
    got = await asyncio.to_thread(_probe, out)
    assert abs(float(got["format"]["duration"]) - 3.0) < 0.1
    assert [s["channels"] for s in got["streams"] if s["codec_type"] == "audio"] == [2]
    err = (await asyncio.to_thread(
        subprocess.run, ["ffmpeg", "-hide_banner", "-nostats", "-i", str(out), "-af",
                         "volumedetect", "-f", "null", "-"],
        capture_output=True, check=True, text=True)).stderr
    mean = float(re.search(r"mean_volume: (-?[\d.]+) dB", err).group(1))
    peak = float(re.search(r"max_volume: (-?[\d.]+) dB", err).group(1))
    assert abs(mean - tm.BED_LEVEL_DB) < 3.0 and peak < -3.0, (mean, peak)


@pytest.mark.skipif(not HAVE_FFMPEG, reason="ffmpeg not installed")
async def test_the_same_argv_renders_the_same_sound(tmp_path):
    sums = []
    for n in (1, 2):
        out = tmp_path / f"bed{n}.m4a"
        await ffmpeg.run(tm.bed_argv(2.0, str(out), seed=2), timeout=60)
        sums.append(await ffmpeg.run(["ffmpeg", "-v", "error", "-i", str(out),
                                      "-f", "md5", "-"], timeout=60))
    assert sums[0] == sums[1]


def test_the_cache_temp_name_is_unique_per_render():
    src = Path(tm.__file__).read_text()
    assert "secrets.token_hex(4)" in src


@pytest.mark.skipif(not HAVE_FFMPEG, reason="ffmpeg not installed")
async def test_a_real_mix_keeps_one_video_one_audio_and_the_length(tmp_path, monkeypatch):
    """The fixture is built like the render's parts: 44.1 kHz stereo AAC in
    an mp4, under a 48 kHz bed."""
    monkeypatch.setenv("ACADEMY_TUTORIALS_DIR", str(tmp_path / "root"))
    video = tmp_path / "dry.mp4"
    await ffmpeg.run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
                      "-f", "lavfi", "-i", "color=c=black:s=320x180:r=30:d=3",
                      "-f", "lavfi", "-i", "sine=f=220:r=44100:d=3",
                      "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac",
                      "-ar", "44100", "-ac", "2", "-shortest", str(video)], timeout=60)
    out = tmp_path / "video.mp4"
    seconds = await tm.add_bed(str(video), "n8n", str(out))
    got = await asyncio.to_thread(_probe, out)
    kinds = [s["codec_type"] for s in got["streams"]]
    assert sorted(kinds) == ["audio", "video"]
    assert math.isclose(float(got["format"]["duration"]), seconds, abs_tol=0.1)
    assert tm.bed_path(seconds, "n8n").is_file()           # cached for the next one
    assert not re.search(r"\.tmp", " ".join(p.name for p in
                                            (tmp_path / "root" / "cache").iterdir()))


def _level(path: Path, a: float, b: float) -> tuple[float, float]:
    """(mean, max) dBFS of `path` between a and b seconds."""
    err = subprocess.run(["ffmpeg", "-hide_banner", "-nostats", "-i", str(path), "-af",
                          f"atrim={a}:{b},volumedetect", "-f", "null", "-"],
                         capture_output=True, check=True, text=True).stderr
    return (float(re.search(r"mean_volume: (-?[\d.]+) dB", err).group(1)),
            float(re.search(r"max_volume: (-?[\d.]+) dB", err).group(1)))


@pytest.mark.skipif(not HAVE_FFMPEG, reason="ffmpeg not installed")
async def test_a_real_mix_is_silent_under_explanations_and_sounds_in_its_windows(
        tmp_path, monkeypatch):
    """Rule 22 (Miro, 2026-10-17): "ducked to nothing beneath explanations".
    A silent 20 s render, the sting at 0-1 s and the end card at 15-20 s."""
    monkeypatch.setenv("ACADEMY_TUTORIALS_DIR", str(tmp_path / "root"))
    video = tmp_path / "dry.mp4"
    await ffmpeg.run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
                      "-f", "lavfi", "-i", "color=c=black:s=320x180:r=30:d=20",
                      "-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo",
                      "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac",
                      "-t", "20", str(video)], timeout=60)
    out = tmp_path / "video.mp4"
    await tm.add_bed(str(video), "n8n", str(out),
                     spans=lambda s: tm.windows(s, sting=(0.0, 1.0), end_card=(15.0, s)))
    explain = await asyncio.to_thread(_level, out, 1.2, 14.8)
    sting = await asyncio.to_thread(_level, out, 0.35, 0.65)
    card = await asyncio.to_thread(_level, out, 15.5, 19.5)
    start = await asyncio.to_thread(_level, out, 0.0, 0.05)
    assert explain[1] < -60.0, explain                 # silence, not a quiet bed
    assert sting[0] > -45.0 and card[0] > -45.0, (sting, card)
    # the open level: the bed's own mean plus BED_DB_OPEN, never louder
    assert card[0] < tm.BED_LEVEL_DB + tm.BED_DB_OPEN + 3.0, card
    assert start[0] < sting[0] - 6.0, (start, sting)     # it fades in, no click


@pytest.mark.skipif(not HAVE_FFMPEG, reason="ffmpeg not installed")
async def test_the_windows_land_on_the_render_clock_not_the_sample_count(
        tmp_path, monkeypatch):
    """A render is a copy-concat of many AAC parts; each part's priming frame
    overlaps the part before at the same timestamp. Mixed by sample count,
    the end-card bed came about 1.8 s early on n8n #1 (2026-10-17), under
    Miro's last words. Forty 0.5 s parts here: about 0.9 s of overlap."""
    monkeypatch.setenv("ACADEMY_TUTORIALS_DIR", str(tmp_path / "root"))
    part = tmp_path / "part.mp4"
    await ffmpeg.run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
                      "-f", "lavfi", "-i", "color=c=black:s=160x90:r=30:d=0.5",
                      "-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo",
                      "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac",
                      "-t", "0.5", str(part)], timeout=60)
    listing = tmp_path / "parts.txt"
    listing.write_text(f"file '{part}'\n" * 40)
    dry = tmp_path / "dry.mp4"
    await ffmpeg.run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-f", "concat",
                      "-safe", "0", "-i", str(listing), "-c", "copy", str(dry)], timeout=60)
    seconds = await ffmpeg.duration(str(dry))
    out = tmp_path / "video.mp4"
    await tm.add_bed(str(dry), "n8n", str(out),
                     spans=lambda s: tm.windows(s, end_card=(s - 3.0, s)))
    wav = tmp_path / "out.wav"
    await ffmpeg.run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i", str(out),
                      "-vn", "-c:a", "pcm_s16le", str(wav)], timeout=60)
    heard = await ffmpeg.duration(str(wav))
    assert abs(heard - seconds) < 0.1, (heard, seconds)     # no samples ahead
    before = await asyncio.to_thread(_level, wav, seconds - 3.8, seconds - 3.05)
    inside = await asyncio.to_thread(_level, wav, seconds - 2.6, seconds - 0.4)
    assert before[1] < -60.0 and inside[0] > -45.0, (before, inside)
