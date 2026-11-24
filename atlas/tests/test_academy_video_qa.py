"""Batch 9 of the Atlas Academy video system: the pre-publish QA gates
(academy/video_qa.py, docs/academy-video-system/qa-checklist.md). Each gate
refuses what breaks the spec, and refuses when its measurement is missing
or cannot run (invariant 7): unknown never passes."""
from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from atlas.modules.academy import tutorial_music as tm
from atlas.modules.academy import tutorial_series as ts
from atlas.modules.academy import tutorials as tut
from atlas.modules.academy import video_qa as qa
from atlas.modules.academy import video_tokens as vt
from atlas.modules.content import ffmpeg
from tests.chromium_support import chromium_ready

HAVE_FFMPEG = shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None
needs_ffmpeg = pytest.mark.skipif(not HAVE_FFMPEG, reason="ffmpeg missing")
needs_chromium = pytest.mark.skipif(not chromium_ready(), reason="Playwright/Chromium missing")

GOOD = {"width": 1920, "height": 1080, "seconds": 300.0, "audio": True,
        "video_codec": "h264", "profile": "High", "pix_fmt": "yuv420p", "fps": 30.0,
        "avg_fps": 29.9996, "audio_codec": "aac", "sample_rate": 48000}


def _manifest(seconds: float = 300.0, **kw) -> tut.Manifest:
    """A result-first lesson: the sting at 6-7 s, a 15 s end card to the
    end, the bed in exactly those windows, pages measured clean."""
    base = {"opening": "result", "sting_at": 6.0, "sting_seconds": 1.0,
            "card_at": seconds - 15.0, "card_seconds": 15.0,
            "music": tm.MUSIC_VERSION,
            "music_windows": [(6.0, 7.0), (seconds - 15.0, seconds)],
            "pages_measured": 40, "min_text_px": 28.0}
    return tut.Manifest("n8n", 0, "fish", seconds, **{**base, **kw})


def _line(n: int) -> str:
    return " ".join(["word"] * n)


# ------------------------------------------------------------------ encoding

def test_the_delivery_spec_passes():
    assert qa.encoding_refusal(GOOD, size=qa.LESSON_SIZE) is None


@pytest.mark.parametrize("change,said", [
    ({"sample_rate": 44100}, "44.1 kHz, not 48 kHz"),
    ({"profile": "Main"}, "Main, not High profile"),
    ({"video_codec": "hevc"}, "hevc, not H.264"),
    ({"pix_fmt": "yuv444p"}, "yuv444p, not yuv420p"),
    ({"fps": 25.0}, "25 fps, not 30"),
    ({"fps": 60.0, "avg_fps": 60.0}, "60 fps, not 30"),
    ({"avg_fps": 29.5}, "drifts to 29.50 fps"),
    ({"audio_codec": "mp3"}, "mp3, not AAC"),
    ({"audio": False}, "no sound"),
    ({"width": 1280, "height": 720}, "1280x720, not 1920x1080"),
])
def test_each_departure_from_the_delivery_spec_is_refused(change, said):
    why = qa.encoding_refusal({**GOOD, **change}, size=qa.LESSON_SIZE)
    assert why is not None and said in why


@pytest.mark.parametrize("probe", [{}, {"width": 1920, "height": 1080, "audio": True,
                                        "seconds": 300.0}])
def test_an_encoding_that_was_not_read_is_held(probe):
    """Invariant 7: a probe without the encoding fields is never a pass."""
    assert qa.encoding_refusal(probe, size=qa.LESSON_SIZE) == qa.UNREAD


@pytest.mark.parametrize("seconds,ok", [(19.9, False), (20.0, True), (40.0, True),
                                        (40.2, False)])
def test_a_short_is_1080_by_1920_and_20_to_40_seconds(seconds, ok):
    p = {**GOOD, "width": 1080, "height": 1920, "seconds": seconds}
    why = qa.encoding_refusal(p, size=qa.SHORT_SIZE, seconds=qa.SHORT_SECONDS)
    assert (why is None) if ok else "outside 20-40 s" in why
    assert "not 1080x1920" in qa.encoding_refusal(GOOD, size=qa.SHORT_SIZE)


def test_ffprobe_json_is_read_into_the_gate_fields():
    data = {"streams": [
        {"codec_type": "video", "codec_name": "h264", "profile": "High", "width": 1920,
         "height": 1080, "pix_fmt": "yuv420p", "r_frame_rate": "30/1",
         "avg_frame_rate": "141265920/4708921"},
        {"codec_type": "audio", "codec_name": "aac", "sample_rate": "44100",
         "r_frame_rate": "0/0"}], "format": {"duration": "306.59"}}
    got = qa.read_probe(data)
    assert got["fps"] == 30.0 and abs(got["avg_fps"] - 30) < 0.001
    assert (got["sample_rate"], got["seconds"], got["audio"]) == (44100, 306.59, True)
    assert "44.1 kHz" in qa.encoding_refusal(got, size=qa.LESSON_SIZE)
    assert qa.read_probe({})["fps"] is None


async def test_a_missing_ffprobe_holds_the_short(monkeypatch, tmp_path):
    monkeypatch.setattr(ffmpeg, "binary", lambda _name="ffmpeg": None)
    got, why = await qa.probe(str(tmp_path / "x.mp4"))
    assert got is None and "not installed" in why
    held = await qa.short_refusal(str(tmp_path / "x.mp4"), qa.Pages(measured=3))
    assert held is not None and held.startswith(qa.UNREAD)


async def _tiny(path: Path, *, rate: int, seconds: float = 1.0, size: str = "320x180",
                draw: str = "") -> None:
    vf = ["-vf", draw] if draw else []
    await ffmpeg.run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
                      "-f", "lavfi", "-i", f"color=c={vt.NAVY}:s={size}:r=30:d={seconds}",
                      "-f", "lavfi", "-i", f"sine=f=220:r={rate}:d={seconds}", *vf,
                      "-c:v", "libx264", "-profile:v", "high", "-pix_fmt", "yuv420p",
                      "-c:a", "aac", "-ar", str(rate), "-shortest", str(path)], timeout=60)


@needs_ffmpeg
async def test_the_real_probe_reads_a_tiny_file(tmp_path):
    good, slow = tmp_path / "good.mp4", tmp_path / "slow.mp4"
    await _tiny(good, rate=48000)
    await _tiny(slow, rate=44100)
    got, why = await qa.probe(str(good))
    assert why is None and got["profile"] == "High" and got["fps"] == 30.0
    assert qa.encoding_refusal(got) is None
    got, _ = await qa.probe(str(slow))
    assert "44.1 kHz" in qa.encoding_refusal(got)
    got, why = await qa.probe(str(tmp_path / "missing.mp4"))
    assert got is None and why


# --------------------------------------------------------------------- music

def test_music_inside_the_sting_and_the_end_card_passes():
    assert qa.music_refusal(_manifest()) is None
    # the manifest rounds to 0.01 s: 291.59 against a window from 291.592993
    m = _manifest(306.59, card_at=291.59, music_windows=[(6.0, 7.0), (291.593, 306.593)])
    assert qa.music_refusal(m) is None


@pytest.mark.parametrize("windows,said", [
    ([], "were not recorded"),
    ([(0.0, 300.0)], "outside the sting and the end card"),     # the bed under everything
    ([(6.0, 7.0), (250.0, 300.0)], "250.0-300.0"),               # under the recap
    ([(6.0, 9.0), (285.0, 300.0)], "6.0-9.0"),                   # past the sting
])
def test_music_anywhere_else_is_refused(windows, said):
    why = qa.music_refusal(_manifest(music_windows=windows))
    assert why is not None and said in why


def test_the_welcome_has_no_sting_window():
    m = _manifest(opening="welcome", music_windows=[(6.0, 7.0), (285.0, 300.0)])
    assert "6.0-7.0" in qa.music_refusal(m)


# ---------------------------------------------------------------- speech rate

def _paced(wpm: float, lines: int = 20, seconds: float = 6.0) -> list:
    """Narrated lines of `seconds` each at `wpm`, after the sting."""
    n = round(wpm * seconds / 60)
    return [(7.0 + i * seconds, 7.0 + (i + 1) * seconds, _line(n)) for i in range(lines)]


@pytest.mark.parametrize("wpm,ok", [(110, True), (140, True), (160, True),
                                    (100, False), (170, False)])
def test_the_pace_is_held_to_narration_md(wpm, ok):
    why = qa.rate_refusal(_manifest(timings=_paced(wpm)))
    assert (why is None) if ok else (why is not None and "words a minute" in why)


def test_the_result_line_and_the_end_card_line_are_not_the_pace():
    """The result line is held to the sting (6 s) and the card's line to
    15 s; neither is speech pace, so neither is counted."""
    lines = [(0.0, 6.0, "Here is the result."), *_paced(140),
             (285.0, 300.0, "Next time: the three kinds of trigger.")]
    rate, secs = qa.speech_rate(_manifest(timings=lines))
    assert rate == pytest.approx(140, abs=1) and secs == pytest.approx(120.0)


def test_voice_cues_are_not_words():
    assert qa.words("A workflow. [chuckling] One you never cook twice.") == 7
    assert qa.words("Type {{ $json.city }} in the field, 2 times.") == 7


@pytest.mark.parametrize("timings", [[], _paced(140, lines=2)])
def test_a_pace_that_cannot_be_measured_is_held(timings):
    assert "cannot be checked" in qa.rate_refusal(_manifest(timings=timings))


# --------------------------------------------------- type size and safe area

def _text(px: float, box=(200, 200, 400, 240), mono: bool = False, text: str = "Label"):
    left, top, right, bottom = box
    return {"text": text, "px": px, "mono": mono, "left": left, "top": top,
            "right": right, "bottom": bottom}


def test_a_16_9_page_holds_28px_and_96px_in():
    rule = qa.RULES[qa.LESSON_SIZE]
    assert qa.faults([_text(28), _text(26, mono=True)], rule) == []   # value-tag's exception
    assert "under 28 px" in qa.faults([_text(24)], rule)[0]
    assert "under 26 px" in qa.faults([_text(24, mono=True)], rule)[0]
    assert "outside the 16:9 safe area" in qa.faults([_text(28, (40, 200, 300, 240))],
                                                     rule)[0]
    assert qa.faults([_text(28, (95.6, 96, 1824.4, 984))], rule) == []   # sub-pixel


def test_a_short_page_holds_60px_and_its_safe_area():
    rule = qa.RULES[qa.SHORT_SIZE]
    assert qa.faults([_text(60, (72, 220, 1008, 1500))], rule) == []
    assert "under 60 px" in qa.faults([_text(59, (100, 300, 400, 380))], rule)[0]
    assert "safe area" in qa.faults([_text(60, (100, 1520, 400, 1600))], rule)[0]


def test_unmeasured_pages_are_held_and_faults_are_said():
    assert "not measured" in qa.pages_refusal(0, [])
    assert qa.pages_refusal(5, []) is None
    why = qa.pages_refusal(5, ['"A" is 20 px, under 28 px', '"B" sits outside'])
    assert why.startswith('the drawn text breaks the spec: "A" is 20 px') and "1 more" in why
    # the manifest keeps the first few; the count says all of them
    assert "(and 31 more)" in qa.pages_refusal(44, ["a", "b"], 32)


class _Page:
    def __init__(self, texts=None, fail=False):
        self.texts, self.fail, self.calls = texts or [], fail, 0

    async def evaluate(self, _js, _arg):
        self.calls += 1
        if self.fail:
            raise RuntimeError("target closed")
        return self.texts


async def test_pages_measure_only_video_sizes_and_a_failed_measure_is_a_fault():
    pages = qa.Pages()
    thumb = _Page([_text(10)])
    await pages.measure(thumb, 1280, 720)              # the thumbnail: not a frame
    await pages.measure(_Page(), 36, 36)               # the trace dot
    assert thumb.calls == 0 and pages.measured == 0
    await pages.measure(_Page([_text(36), _text(30)]), 1920, 1080)
    await pages.measure(_Page([_text(30)]), 1920, 1080)
    assert pages.record() == {"pages_measured": 2, "min_text_px": 30.0, "text_faults": [],
                              "text_fault_count": 0}
    await pages.measure(_Page(fail=True), 1920, 1080)
    assert "could not be measured" in pages.record()["text_faults"][0]
    await pages.measure(_Page([_text(20)]), 1920, 1080)
    await pages.measure(_Page([_text(20)]), 1920, 1080)
    assert len(pages.found) == 2                       # each fault said once


@needs_chromium
async def test_the_shooter_measures_the_page_it_draws(tmp_path):
    """A real page through tutorials.Shooter: a small label near the edge is
    two faults; the preview banner is never measured."""
    doc = ('<!doctype html><html><body style="margin:0;width:1920px;height:1080px">'
           '<div class="preview" style="position:absolute;top:0;font-size:12px">'
           'Preview banner</div>'
           '<p style="position:absolute;left:20px;top:500px;margin:0;font:20px sans-serif">'
           'Too small, too near</p>'
           '<p style="position:absolute;left:200px;top:200px;margin:0;font:40px sans-serif">'
           'Fine</p><p style="visibility:hidden;font-size:8px">hidden</p></body></html>')
    async with tut.Shooter() as sh:
        await sh.shoot(doc, str(tmp_path / "a.png"))
        await sh.shoot(doc, str(tmp_path / "t.png"), 1280, 720)
    rec = sh.pages.record()
    assert rec["pages_measured"] == 1 and rec["min_text_px"] == 20.0
    near = '"Too small, too near"'
    assert sorted(rec["text_faults"]) == [f"{near} is 20 px, under 28 px",
                                          f"{near} sits outside the 16:9 safe area"]


# ---------------------------------------------------------- end-card reserve

def _navy(n: int) -> bytes:
    return bytes(qa.NAVY_RGB) * n


def test_off_navy_counts_drawn_pixels():
    assert qa.off_navy(_navy(100)) == 0
    near = bytes(c + 3 for c in qa.NAVY_RGB)          # the YUV round trip
    assert qa.off_navy(_navy(99) + near) == 0
    assert qa.off_navy(_navy(99) + b"\xff\xff\xff") == 1


async def test_the_reserve_is_sampled_on_the_end_card(monkeypatch):
    x0, y0, x1, y1 = qa.RESERVE
    assert (x0, y0, x1, y1) == (1184, 96, 1824, 984)
    seen = []

    async def frame(_v, at, box):
        seen.append(at)
        return _navy((box[2] - box[0]) * (box[3] - box[1]))
    monkeypatch.setattr(qa, "frame_rgb", frame)
    assert await qa.reserve_refusal("v.mp4", _manifest()) is None
    assert seen == [286.0, 292.5, pytest.approx(300 - 2 / 30, abs=0.001)]


async def test_a_drawn_or_unreadable_reserve_is_held(monkeypatch):
    size = (qa.RESERVE[2] - qa.RESERVE[0]) * (qa.RESERVE[3] - qa.RESERVE[1])

    async def drawn(_v, _at, _box):
        return _navy(size - 1) + b"\xff\xff\xff"
    monkeypatch.setattr(qa, "frame_rgb", drawn)
    assert "not empty navy at 286.0 s (1 pixels" in await qa.reserve_refusal(
        "v", _manifest())

    async def short(_v, _at, _box):
        return b""
    monkeypatch.setattr(qa, "frame_rgb", short)
    assert "could not be read" in await qa.reserve_refusal("v", _manifest())

    async def broken(_v, _at, _box):
        raise ffmpeg.FfmpegError("ffmpeg not installed")
    monkeypatch.setattr(qa, "frame_rgb", broken)
    assert "could not be read" in await qa.reserve_refusal("v", _manifest())
    assert "no end card" in await qa.reserve_refusal("v", _manifest(card_seconds=0.0))


@needs_ffmpeg
async def test_the_real_reserve_check_reads_a_decoded_frame(tmp_path):
    flat, marked = tmp_path / "flat.mp4", tmp_path / "marked.mp4"
    await _tiny(flat, rate=48000, seconds=2.0, size="1920x1080")
    await _tiny(marked, rate=48000, seconds=2.0, size="1920x1080",
                draw="drawbox=x=1500:y=500:w=120:h=60:color=white:t=fill")
    m = _manifest(2.0, card_at=0.0, card_seconds=2.0)
    assert await qa.reserve_refusal(str(flat), m) is None
    assert "not empty navy" in await qa.reserve_refusal(str(marked), m)


# --------------------------------------------------------- the gate in ready()

async def test_ready_runs_the_qa_gates_and_holds_on_any(tmp_path, monkeypatch):
    seconds = 290.0
    nxt = ts.next_up("n8n", ts.series("n8n").episode(0))
    m = _manifest(seconds, chapters=[(0.0, "Intro"), (60.0, "A"), (120.0, "B")],
                  parts=["intro-a", "outro-b"], body_seconds=200.0,
                  next_title=nxt.title if nxt else "", timings=_paced(140))
    monkeypatch.setattr(tut, "episode_dir", lambda *_: tmp_path)
    monkeypatch.setattr(tut, "publishable_render", lambda _p: None)
    monkeypatch.setattr(tut, "read_manifest", lambda _d: m)
    probe = {**GOOD, "seconds": seconds, "sample_rate": 44100}

    async def probed(_path):
        return probe, None
    monkeypatch.setattr(tut, "_probe", probed)

    async def navy(_v, _at, box):
        return _navy((box[2] - box[0]) * (box[3] - box[1]))
    monkeypatch.setattr(qa, "frame_rgb", navy)

    async def heard(_d, _m):
        return "Here is the result: the n8n basics.", None
    monkeypatch.setattr(tut, "opener_heard", heard)
    got, why = await tut.ready("n8n", 0)
    assert got is None and why == "QA: the sound is 44.1 kHz, not 48 kHz"
    probe["sample_rate"] = 48000
    m.pages_measured = 0
    got, why = await tut.ready("n8n", 0)
    assert got is None and why.startswith("QA: the drawn pages were not measured")
    m.pages_measured = 40
    got, why = await tut.ready("n8n", 0)
    assert why is None or not why.startswith("QA:"), why     # every QA gate passed


# ---------------------------------------------------------- flags, not gates

def test_the_looping_subscribe_beat_is_flagged_on_the_card_not_held():
    m = _manifest(backdrops=["trigger", "subscribe", "code"])
    assert "looping 3D Subscribe" in qa.notes(m)[0]
    s = ts.series("n8n")
    assert qa.notes(m)[0] in tut.review_reason(s, s.episode(0), m)
    assert qa.notes(_manifest(backdrops=["trigger"])) == []


# ------------------------------------------- the encodes the gate holds to

def test_the_lesson_mix_and_the_short_leave_at_48_khz_h264_high():
    mix = tm.mix_argv("in.mp4", "bed.m4a", "out.mp4", 30.0)
    assert mix[mix.index("-ar") + 1] == "48000" and mix.index("-ar") > mix.index("-c:a")
    short = tut.short_argv("in.mp4", 0.0, 30.0, "out.mp4", close="/w/close.png",
                           frames=[tut.ShortFrame("/w/p.png", 0.0, 30.0)])
    assert short[short.index("-ar") + 1] == "48000"
    assert short[short.index("-profile:v") + 1] == "high"


@needs_ffmpeg
async def test_a_real_mix_of_44_1_khz_parts_passes_the_encoding_gate(tmp_path,
                                                                    monkeypatch):
    """The parts are joined at 44.1 kHz; the mixed file is what ships."""
    monkeypatch.setenv("ACADEMY_TUTORIALS_DIR", str(tmp_path / "root"))
    dry, out = tmp_path / "dry.mp4", tmp_path / "video.mp4"
    await _tiny(dry, rate=44100, seconds=2.0)
    await tm.add_bed(str(dry), "n8n", str(out))
    got, why = await qa.probe(str(out))
    assert why is None and qa.encoding_refusal(got) is None, got
