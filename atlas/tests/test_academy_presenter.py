"""Miro on camera (2026-10-13): the presenter clips open and close every
tutorial when they are on disk; without them, the narrated bookends."""
from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
from pathlib import Path

import pytest

from atlas.modules.academy import presenter, tutorial_music
from atlas.modules.academy import tutorial_design as design
from atlas.modules.academy import tutorial_series as ts
from atlas.modules.academy import tutorials as tut

TOOLS = Path(__file__).resolve().parents[1] / "tools" / "presenter"
N8N = ts.series("n8n")


def put_clip(d: Path, slot: str, data: bytes, seconds: float = 6.0,
             said: str | None = None) -> None:
    """A stand-in clip and its manifest entry in presenter directory `d`."""
    (d / f"{slot}.mp4").write_bytes(data)
    path = d / presenter.MANIFEST
    raw = json.loads(path.read_text()) if path.is_file() else {"version": 1, "clips": {}}
    raw["clips"][slot] = {"file": f"{slot}.mp4", "sha256": hashlib.sha256(data).hexdigest(),
                          "seconds": seconds,
                          "said": presenter.LINES[slot] if said is None else said}
    path.write_text(json.dumps(raw))


@pytest.fixture
def clips(monkeypatch, tmp_path):
    d = tmp_path / "presenter"
    d.mkdir()
    monkeypatch.setenv("ACADEMY_PRESENTER_DIR", str(d))
    put_clip(d, "intro", b"intro-take-1", 5.9)
    put_clip(d, "outro", b"outro-take-1", 17.2)
    return d


# ------------------------------------------------------------ the clips




def test_no_clip_means_the_narrated_fallback(tmp_path, monkeypatch):
    assert presenter.clip("intro") is None and presenter.stamp("intro", "outro") == ""
    monkeypatch.setenv("ACADEMY_PRESENTER_DIR", str(tmp_path / "nowhere"))
    assert presenter.clip("outro") is None


def test_a_clip_is_used_only_when_its_bytes_and_words_are_the_manifests(clips):
    assert presenter.clip("intro") is not None
    (clips / "intro.mp4").write_bytes(b"a different take")        # sha no longer matches
    assert presenter.clip("intro") is None
    put_clip(clips, "intro", b"take", said="Hello there.")          # not the code's line
    assert presenter.clip("intro") is None
    assert presenter.clip("nonsense") is None


def test_the_script_key_no_longer_follows_the_clips(monkeypatch, tmp_path, clips):
    """Miro, 2026-10-17: neither clip plays any more, so a new take, or no
    clip at all, renders nothing again."""
    ep = N8N.episode(1)
    with_clips = tut.script_key(ep)
    put_clip(clips, "intro", b"intro-take-2", 5.0)
    put_clip(clips, "outro", b"outro-take-2", 17.0)
    assert tut.script_key(ep) == with_clips
    monkeypatch.setenv("ACADEMY_PRESENTER_DIR", str(tmp_path / "empty"))
    assert tut.script_key(ep) == with_clips


# ------------------------------------------------------------ the words

def test_miros_intro_is_never_the_opening_any_more():
    """Miro, 2026-10-17: his on-camera intro (5.9 s) is dropped from the
    videos; they open on the result. The clip stays on disk, unused, and a
    lesson heard saying its welcome line is refused."""
    ep = N8N.episode(1)
    line = presenter.LINES["intro"]
    assert ts.opener_said(ep).startswith("Here is the result")
    assert line not in ts.opener_said(ep)
    welcome = ts.series("channel").episodes[0]
    assert ts.opener_said(welcome) == ts.INTRO_SAID
    heard = f"{line} Here is the result: your first workflow."
    assert "welcome" in tut.heard_refusal(ep, heard)


def test_the_estimate_ignores_the_clips(clips, monkeypatch):
    """The clips play no more (2026-10-17): the estimate is the same with
    them on disk or not, and ends on the EndCard's 15 s."""
    ep = N8N.episode(1)
    total, body = tut.estimate_seconds(ep)
    monkeypatch.setenv("ACADEMY_PRESENTER_DIR", str(clips / "none"))
    assert tut.estimate_seconds(ep) == (total, body)
    assert tut._ending_estimate(1.0) == design.END_CARD_MIN


# ------------------------------------------------------------ the render

@pytest.fixture
def render_with(monkeypatch, tmp_path, clips):
    """Everything a render touches, faked, with Miro's clips on disk."""
    from atlas.modules.academy import reel_design
    from atlas.modules.content import ffmpeg
    monkeypatch.setenv("ACADEMY_TUTORIALS_DIR", str(tmp_path / "tut"))
    state: dict = {"said": [], "intros": [], "presenter": [], "measured": 290.0,
                   "openings": []}

    class Shooter:
        pages = tut.video_qa.Pages()          # batch 9: render records it
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return None

        async def shoot(self, doc, path, w=0, h=0):
            return path

    async def thumbnail(ep, shooter, out_png, **kw):    # batch 7: the real-image thumbnail
        from pathlib import Path
        Path(out_png).write_bytes(b"\x89PNG\r\n\x1a\nfake")
    from atlas.modules.academy import tutorial_thumbs
    monkeypatch.setattr(tutorial_thumbs, "render", thumbnail)

    async def segment(shooter, doc, said, base, preview, **kw):
        state["said"].append(said)
        return base + ".mp4", max(kw.get("silent") or 4.0, kw.get("hold_to", 0.0)), "n", None

    async def fixed(kind, shooter, preview=False):
        state["intros"].append(kind)
        return "/c/intro-n.mp4", 12.0, None

    async def opening(ep, shooter, work, preview=False):
        state["openings"].append(ep.slug)
        a, c = ts.opening_lines(ep)
        return tut.Opening([f"{work}/intro-result.mp4", "/c/sting-k.mp4",
                            f"{work}/scope.mp4"], 15.0,
                           [(0.0, 6.0, a), (7.0, 15.0, c)]), None

    async def run(argv, timeout=0):
        return b""

    async def duration(path):
        m = state["measured"]
        return m(len(state["intros"])) if callable(m) else m

    # rule 22 (2026-10-13): every join is mixed with the music bed; the fake
    # mix keeps the length, as the real one does to within a few ms
    state["mixed"] = []

    async def add_bed(video, series_key, out, **_kw):
        state["mixed"].append((video, series_key, out))
        return 0.0
    monkeypatch.setattr(tutorial_music, "add_bed", add_bed)
    monkeypatch.setattr(tut, "Shooter", Shooter)
    monkeypatch.setattr(tut, "_segment", segment)
    monkeypatch.setattr(tut, "fixed_clip", fixed)
    monkeypatch.setattr(tut, "opening_part", opening)
    monkeypatch.setattr(ffmpeg, "run", run)
    monkeypatch.setattr(ffmpeg, "duration", duration)
    monkeypatch.setattr(ffmpeg, "missing", lambda: None)
    monkeypatch.setattr(reel_design, "available", lambda: True)
    return state


async def test_a_lesson_opens_on_its_result_and_ends_on_its_card_with_the_clips_on_disk(
        render_with):
    """Miro, 2026-10-17: his intro (5.9 s) and outro (16.4 s) are dropped
    from the videos; the clips stay on disk, unused."""
    assert presenter.clip("intro") and presenter.clip("outro")     # on disk
    _d, m, why = await tut.render("n8n", 1)
    assert why is None, why
    assert not hasattr(tut, "presenter_part") and not hasattr(tut, "presenter_argv")
    assert m.parts[0] == "intro-result.mp4" and m.opening == "result"
    assert m.parts[-1] == "outro-card.mp4" and m.presenter == []
    assert tut.bookends_refusal(m) is None
    assert m.opener == ts.opener_said(N8N.episode(1)) and m.spoken_minutes == 0
    lines = [w for _a, _b, w in m.timings]
    assert presenter.LINES["intro"] not in lines and presenter.LINES["outro"] not in lines
    assert lines[0].startswith("Here is the result")


async def test_the_welcome_opens_on_its_narrated_intro_never_miros_clip(render_with):
    _d, m, why = await tut.render("channel", 1)
    assert why is None, why
    assert m.parts[0] == "intro-n.mp4" and render_with["intros"] == ["intro"]
    assert m.presenter == [] and m.parts[-1] == "outro-card.mp4"
    assert m.opener == ts.INTRO_SAID and m.spoken_minutes == 0
    assert m.opening == "welcome" and m.sting_seconds == 0.0


async def test_without_clips_the_render_is_the_narrated_one(render_with, monkeypatch,
                                                            tmp_path):
    monkeypatch.setenv("ACADEMY_PRESENTER_DIR", str(tmp_path / "none"))
    _d, m, why = await tut.render("n8n", 1)
    assert why is None, why
    assert m.parts[0] == "intro-result.mp4" and m.parts[-1] == "outro-card.mp4"
    assert m.presenter == [] and m.opener.startswith("Here is the result")


# ------------------------------------------------------------ the tools

def _tool(name: str):
    spec = importlib.util.spec_from_file_location(f"presenter_{name}", TOOLS / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


def test_process_trims_to_the_speech_and_snaps_cuts_to_pauses():
    pr = _tool("process")
    levels = [-60.0] * 50 + [-35.0] * 100 + [-60.0] * 20 + [-35.0] * 80 + [-60.0] * 50
    start, end = pr.speech_window(levels, 0.02, 6.0)
    assert start == pytest.approx(1.0 - pr.LEAD_IN) and end == pytest.approx(5.0 + pr.TAIL)
    assert 3.0 <= pr.snap(levels, 0.02, 3.3) <= 3.4          # into the pause
    assert pr.keep(0.5, 5.0, [(3.0, 3.4)]) == [(0.5, 3.0), (3.4, 5.0)]
    assert pr.keep(0.5, 5.0, []) == [(0.5, 5.0)]
    assert pr.speech_window([], 0.02, 4.0) == (0.0, 4.0)


def test_process_frames_miro_and_refuses_an_empty_matte():
    pr = _tool("process")
    sw, sh, x, _y = pr.framing(1037.0, 1620, 1080)
    assert (sw, sh) == (1620, 1080) and x + 1037 == pr.TARGET_X   # uniform scale only
    assert pr.coverage_refusal([0.2] * 30) is None
    assert "no person" in pr.coverage_refusal([0.0] * 30)
    assert pr.loudnorm_stats('noise {\n "input_i" : "-40.1",\n "input_tp" : "-20"\n}')[
        "input_i"] == "-40.1"
    assert pr.lines() == presenter.LINES


def test_splice_keeps_the_length_so_the_lips_stay_in_sync():
    np = pytest.importorskip("numpy")
    sp = _tool("splice")
    x = np.sin(np.arange(sp.RATE * 3) * 2 * np.pi * 120 / sp.RATE) * 0.1
    y = sp.splice(x, (1.0, 1.48), [(2.0, 2.13), (0.2, 0.25, 1.0), (2.5, 2.8)])
    assert abs(len(y) - len(x)) <= 2
    assert np.allclose(y[:sp.RATE // 2], x[:sp.RATE // 2])        # untouched before
    assert np.allclose(y[-sp.RATE:], x[-sp.RATE:])                # and after
