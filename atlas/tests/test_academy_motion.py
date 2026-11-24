"""The motion guide (Miro, 2026-10-15): the Atlas Academy Video Motion and
Editing Guide's presets, its rules for when things move, the brand, and the
render-cost budget on the one-CPU server. docs/academy-motion-guide.md."""
from __future__ import annotations

import re

import pytest

from atlas.modules.academy import tutorial_demos as td
from atlas.modules.academy import tutorial_design as design
from atlas.modules.academy import tutorial_motion as motion
from atlas.modules.academy import tutorial_series as ts
from atlas.modules.academy import tutorial_short as tshort
from atlas.modules.academy import tutorials as tut
from atlas.modules.academy import video_tokens as vt

N8N = ts.series("n8n")


def _graph(argv: list[str]) -> str:
    return argv[argv.index("-filter_complex") + 1]


# ------------------------------------------------------------ the presets

@pytest.mark.parametrize("name", sorted(motion.GUIDE_RANGES))
def test_every_preset_is_inside_the_guides_range(name):
    value, least, most = motion.GUIDE_RANGES[name]
    assert least <= value <= most, name


def test_the_presets_are_whole_frames_at_thirty_fps():
    assert motion.FPS == tut.FPS == 30
    assert tut.TRANSITION == motion.sec(motion.CROSSFADE_FRAMES) == 0.233
    assert motion.sec(motion.RISE_FRAMES) == 0.3
    # a backdrop change is a background move, still well under a second
    assert tut.XFADE == motion.sec(motion.BACKDROP_FRAMES) <= 0.4


def test_the_ease_settles_at_exactly_one_and_stays():
    expr = motion.ease(0.5, 0.3)
    for t, want in ((0.0, 0.0), (0.5, 0.0), (0.8, 1.0), (5.0, 1.0)):
        p = min(max((t - 0.5) / 0.3, 0), 1)
        assert 1 - (1 - p) ** 3 == pytest.approx(want)
    assert expr.startswith("(1-pow(1-clip((t-0.5)/0.3,0,1),3))")
    assert motion.rise_y(400, 0.2) == f"400+16*(1-{motion.ease(0.2, 0.3)})"
    assert motion.rise_y(400, 0.2, px=0) == "400"


# ---------------------------------------------------- one focal point

def _slides():
    for s in ts.SERIES:
        for ep in s.episodes:
            if not ep.scripted:
                continue
            for sc in tut.opening(ep) + ep.scenes:
                for k in range(len(sc.said)):
                    yield sc, k


def test_motions_run_one_after_another_never_together():
    seen = 0
    for sc, k in _slides():
        spec = design.motion_of(sc, k)
        for crossfade in (True, False):
            plan = motion.schedule(spec, crossfade=crossfade)
            t = motion.sec(motion.CROSSFADE_FRAMES) if crossfade else 0.0
            for _what, start, frames in plan:
                assert start >= t - 1e-9, (sc.kind, k, plan)
                t = start + motion.sec(frames)
            seen += 1
    assert seen > 100


def test_only_what_is_new_moves():
    ep = N8N.episode(1)
    bullets = next(sc for sc in ep.scenes if sc.kind == "bullets")
    first, later = design.motion_of(bullets, 0), design.motion_of(bullets, 1)
    assert [g for g, _ in first.groups] == [design.HEAD, design.NOW_ITEM]
    assert [g for g, _ in later.groups] == [design.NOW_ITEM]     # the heading stays put
    # a lit code line or a demo step is a state change: nothing travels
    code = next(sc for sc in ep.scenes if sc.kind == "code")
    assert not design.motion_of(code, 1)
    demo = next(sc for sc in ep.scenes if sc.kind == "demo")
    assert all(not design.motion_of(demo, k) for k in range(len(demo.said)))


def test_the_payoff_traces_the_workflow_once_then_shows_the_result():
    ep = N8N.episode(1)
    spec = design.motion_of(ep.payoff, 0)
    assert spec.trace == (0, len(ep.payoff.items) - 1)
    plan = motion.schedule(spec, crossfade=False)
    assert [w for w, _, _ in plan] == [design.HEAD, "trace", ".bubble"]
    trace = plan[1]
    assert trace[2] == motion.TRACE_FRAMES * (len(ep.payoff.items) - 1)
    # the result is a checkpoint: it fades, it does not travel
    assert dict(spec.groups)[".bubble"] is True
    # the whole opening move settles inside the guide's 3-6 s
    assert motion.settles_by(spec, crossfade=False) <= 3.0


def test_a_diagram_step_traces_one_connector_to_the_node_it_lights():
    sc = ts.Scene("Flow", "diagram", "Flow", ("a", "b", "c"), ("A\na", "B\nb", "C\nc"))
    assert design.motion_of(sc, 0).trace is None
    assert design.motion_of(sc, 2).trace == (1, 2)
    plan = motion.schedule(design.motion_of(sc, 2), crossfade=True)
    assert [w for w, _, _ in plan] == ["trace", design.NODE_NOW]


def test_a_segment_too_short_to_settle_is_drawn_whole():
    spec = motion.Spec(((design.HEAD, False), (design.NOW_ITEM, False)))
    end = motion.settles_by(spec, crossfade=True)
    assert not motion.fits(spec, end + motion.HOLD_AFTER - 0.01, crossfade=True)
    assert motion.fits(spec, end + motion.HOLD_AFTER, crossfade=True)


# ------------------------------------------------------ the transitions

def test_every_step_crossfades_and_nothing_wipes():
    argv = tut.segment_argv("/w/s.png", "/w/a.mp3", 6.0, "/w/o.mp4", bg="/k/loop-n8n.mp4",
                            prev="/w/p.png")
    graph = _graph(argv)
    assert f"fade=t=out:st=0:d={tut.TRANSITION}:alpha=1[p]" in graph
    assert "[x][p]overlay=0:0:" in graph and "eval=frame" not in graph
    i = argv.index("/w/p.png")
    assert argv[i - 3:i - 1] == ["-t", f"{tut.TRANSITION:.3f}"]
    import inspect
    assert "transition" not in inspect.signature(tut.segment_argv).parameters
    assert "wipeleft" not in inspect.getsource(tut)


def test_an_entering_element_fades_and_rises_over_its_frames_then_holds():
    e = motion.Enter("/w/e0.png", 236, 300, 0.233)
    argv = tut.segment_argv("/w/s.png", "/w/a.mp3", 6.0, "/w/o.mp4", bg="/k/loop-n8n.mp4",
                            prev="/w/p.png", enters=[e], watermark=str(design.WATERMARK))
    graph = _graph(argv)
    i = argv.index("/w/e0.png")
    # looped only for its own frames (and one), never the whole segment
    assert argv[i - 7:i - 2] == ["-loop", "1", "-framerate", "30", "-t"]
    assert float(argv[i - 2]) == pytest.approx(e.end + 1 / 30, abs=1e-3)
    assert f"fade=t=in:st=0.233:d={motion.sec(motion.RISE_FRAMES):g}:alpha=1[e0]" in graph
    assert f"overlay=x=236:y='{motion.rise_y(300, 0.233)}':eof_action=repeat" in graph
    # under the watermark, after the crossfade
    assert graph.index("[x][p]overlay") < graph.index("[e0]overlay")
    assert graph.index("[e0]overlay") < graph.index("[y][wm]overlay")


def test_the_trace_dot_is_one_still_moving_along_its_connector():
    t = motion.Trace("/w/dot.png", 400, 930, 640, 0.5, hops=2)
    argv = tut.segment_argv("/w/s.png", "/w/a.mp3", 6.0, "/w/o.mp4", trace=t)
    i = argv.index("/w/dot.png")
    assert argv[i - 1] == "-i" and "-loop" not in argv[i - 3:i]      # decoded once
    graph = _graph(argv)
    d = motion.sec(motion.TRACE_FRAMES * 2)
    assert f"overlay=x='400+530*{motion.ease(0.5, d)}':y=640:eof_action=repeat" in graph


def test_a_demo_pushes_in_only_when_its_region_moves():
    step = td.Step("c", (), box=(512, 240, 1024, 576))
    demo = ("/d/demo.mp4", 2.0, 6.0, 1.0, step)
    plain = _graph(tut.segment_argv("/w/s.png", "/w/a.mp3", 6.0, "/w/o.mp4", demo=demo))
    assert "zoompan" not in plain
    pushed = _graph(tut.segment_argv("/w/s.png", "/w/a.mp3", 6.0, "/w/o.mp4", demo=demo,
                                     focus=True))
    f = motion.FOCUS_FRAMES
    assert f"[da]trim=end_frame={f}," in pushed and f"[db]trim=start_frame={f}," in pushed
    # after the push the step is the exact rule-18 crop, as without it
    assert f"{td.crop_filter(step, td.HOLE_W, td.HOLE_H)},setsar=1[dc]" in pushed
    bx, by, bw, bh = motion.focus_box(step.box, td.DEMO_W, td.DEMO_H)
    assert bw * 9 == bh * 16 and bw <= td.DEMO_W and bh <= td.DEMO_H
    assert bw / 1024 == pytest.approx(motion.FOCUS_SCALE, abs=0.02)
    assert bx <= 512 and by <= 240 and bx + bw >= 512 + 1024 and by + bh >= 240 + 576
    # a window too short for the push just cuts
    short = ("/d/demo.mp4", 2.0, 0.3, 1.0, step)
    assert "zoompan" not in _graph(tut.segment_argv("/w/s.png", "/w/a.mp3", 6.0,
                                                    "/w/o.mp4", demo=short, focus=True))


def test_the_focus_box_stays_inside_the_recording():
    for demo in td.DEMOS.values():
        for step in demo.steps:
            x, y, w, h = motion.focus_box(step.box, td.DEMO_W, td.DEMO_H)
            assert 0 <= x and 0 <= y and x + w <= td.DEMO_W and y + h <= td.DEMO_H
            assert not (x % 2 or y % 2 or w % 2 or h % 2)


async def test_demo_steps_cut_and_push_in_while_slides_crossfade(monkeypatch, tmp_path):
    """In a render: a demo step after a demo step has no fade from the one
    before (a software state is cut), the push-in comes only when the box
    moves, and countdown seconds and pause holds never move."""
    from atlas.modules.academy import reel_design
    from atlas.modules.content import ffmpeg
    monkeypatch.setenv("ACADEMY_TUTORIALS_DIR", str(tmp_path))
    calls: list[dict] = []

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
        calls.append(kw)
        return base + ".mp4", max(kw.get("silent") or 4.0, kw.get("hold_to", 0.0)), "n", None

    async def fixed(kind, shooter, preview=False):
        return str(tmp_path / f"{kind}-x.mp4"), 6.0, None

    async def opening(ep, shooter, work, preview=False):
        return tut.Opening([str(tmp_path / "intro-result.mp4")], 15.0, []), None

    async def run(argv, timeout=0):
        return b""

    async def duration(path):
        return 290.0

    async def add_bed(video, key, out, **_kw):
        return 0.0
    from atlas.modules.academy import tutorial_music
    monkeypatch.setattr(tutorial_music, "add_bed", add_bed)
    monkeypatch.setattr(tut, "Shooter", Shooter)
    monkeypatch.setattr(tut, "_segment", segment)
    monkeypatch.setattr(tut, "fixed_clip", fixed)
    monkeypatch.setattr(tut, "opening_part", opening)
    monkeypatch.setattr(ffmpeg, "run", run)
    monkeypatch.setattr(ffmpeg, "duration", duration)
    monkeypatch.setattr(ffmpeg, "missing", lambda: None)
    monkeypatch.setattr(reel_design, "available", lambda: True)
    _d, _m, why = await tut.render("n8n", 1)
    assert why is None, why
    demos = [k for k in calls if "demo" in k]
    assert demos
    boxes = []
    for i, k in enumerate(calls):
        if "demo" not in k:
            continue
        before = calls[i - 1]
        if "demo" in before:
            assert k.get("prev") is None                       # cut, not dissolved
        box = k["demo"][3].box
        # a push-in only when the region moves between two demo steps
        assert k["focus"] == ("demo" in before and boxes[-1] != box)
        boxes.append(box)
    for k in calls:
        if k.get("silent"):
            # held still; a checkpoint's silent hold moves only its tag
            spec = k["spec"]
            assert not spec or (spec.groups == ((design.CHECK_TAG, False),)
                                and spec.exits == (design.CHECK_TAG,) and not spec.trace)
    assert any(k.get("spec") for k in calls if "demo" not in k)


# ---------------------------------------------------------- the bookends

def test_the_end_card_rises_in_once_and_stays_to_the_last_frame():
    """The EndCard (2026-10-17): title-rise in, then held; nothing fades out
    and nothing loops (the presenter plates and the 3D card are gone)."""
    assert design.END_CARD_MOTION.groups == ((".endcard", False),)
    assert design.END_CARD_MOTION.trace is None
    enter = motion.Enter("/w/card.e0.png", 96, 96, 0.067)
    argv = tut.segment_argv("/w/card.png", "/w/a.wav", 15.0, "/w/o.mp4", enters=[enter])
    graph = _graph(argv)
    assert f"fade=t=in:st=0.067:d={motion.sec(motion.RISE_FRAMES):g}:alpha=1" in graph
    assert "fade=t=out" not in graph and "eof_action=repeat" in graph
    assert "-stream_loop" not in argv                 # no 3D loop under it: flat navy
    assert argv[argv.index("-t", argv.index("-map")) + 1] == "15.000"


# ------------------------------------------------------------ the brand

def test_the_new_pieces_draw_only_the_brand():
    css = design.BASE
    for piece in (".flow::after", ".dot{"):
        rule = css[css.index(piece):css.index("}", css.index(piece))]
        for hexes in re.findall(r"#[0-9A-Fa-f]{6}", rule):
            assert hexes.upper() in {c.upper() for c in design.PALETTE}, (piece, hexes)
    dot = design.trace_dot()
    assert design.BLUE in dot and "transparent" in dot
    names = {n for n, *_ in design.TEXT_PAIRS}
    assert "trace dot on its track" in names


def test_the_short_captions_are_the_brand_in_the_free_band():
    m = tut.Manifest("n8n", 1, "fish", 300.0, [(0.0, "Intro"), (10.0, "A")],
                     timings=[(5.0, 9.0, "one two three four five six seven"),
                              (9.0, 12.0, "[excited] n8n reads JSON"), (40.0, 44.0, "later")])
    cut = tshort.Cut("payoff", 8.0, 38.0, "A")
    cues = tshort.caption_cues(m, cut)
    assert cues[0][0] == 0.0 and all(len(t.split()) <= tshort.CAPTION_WORDS for *_, t in cues)
    assert [t for *_, t in cues][-1] == "n8n reads JSON"          # no cue, names as written
    assert all(0 <= a < b <= 30.0 for a, b, _ in cues)
    ass = tshort.caption_ass(m, cut)
    assert "&H00FFF8F6" in ass                                      # paper #f6f8ff type
    assert "&H33301B10" in ass                                      # navy #101b30 at 80%
    # 2026-10-17: in the portrait frame's caption zone, under the panel
    from atlas.modules.academy import short_frame as sf
    assert 1920 - tshort.CAPTION_BOTTOM >= sf.CAPTION_BOX[1] > sf.PANEL_BOX[1]
    assert tshort.caption_ass(m, tshort.Cut("quiz", 100.0, 130.0, "B")) == ""
    frames = [tut.ShortFrame("/w/a.png", 0.0, 30.0)]
    argv = tut.short_argv("in.mp4", 8.0, 38.0, "out.mp4", frames=frames,
                          close="/w/c.png", ass="/w/c.ass")
    fonts = f"fontsdir='{vt.LIBASS_FONTS_DIR}'"
    assert f"[cat]ass='/w/c.ass':{fonts},format=yuv420p" in _graph(argv)
    assert ",ass=" not in _graph(tut.short_argv("in.mp4", 8.0, 38.0, "out.mp4",
                                                frames=frames, close="/w/c.png"))


# ------------------------------------------------------- the cost budget

def test_a_teaching_segment_stays_inside_the_render_budget():
    """Server: one CPU. Against the 2026-10-14 segment (slide over the loop,
    the step before fading out, the watermark), motion adds no full-frame
    overlay held for the segment, no per-frame zoom on slides, one encode,
    and only small layers looped for their own few frames."""
    before = motion.budget(tut.segment_argv(
        "/w/s.png", "/w/a.mp3", 6.0, "/w/o.mp4", bg="/k/loop-n8n.mp4", prev="/w/p.png",
        watermark=str(design.WATERMARK)))
    enters = [motion.Enter("/w/e0.png", 236, 150, 0.233),
              motion.Enter("/w/e1.png", 236, 300, 0.533)]
    argv = tut.segment_argv(
        "/w/s.png", "/w/a.mp3", 6.0, "/w/o.mp4", bg="/k/loop-n8n.mp4", prev="/w/p.png",
        watermark=str(design.WATERMARK), enters=enters,
        trace=motion.Trace("/w/dot.png", 400, 900, 640, 0.833))
    after = motion.budget(argv)
    assert after.full_overlays == before.full_overlays
    assert after.zoom_frames == 0 and after.encodes == before.encodes == 1
    assert after.timed_layers - before.timed_layers == len(enters)
    for i, a in enumerate(argv):                 # every looped input is short
        if a == "-loop":
            assert float(argv[i + 5]) <= 2.0, argv[i:i + 7]
    tail = argv[argv.index("-c:v"):]
    assert tail[:6] == ["-c:v", "libx264", "-preset", "veryfast", "-crf", "21"]


def test_a_demo_push_in_zooms_its_first_frames_only():
    step = td.Step("c", (), box=(256, 144, 1024, 576))
    argv = tut.segment_argv("/w/s.png", "/w/a.mp3", 8.0, "/w/o.mp4",
                            demo=("/d/demo.mp4", 0.0, 8.0, 1.0, step), focus=True)
    assert motion.budget(argv).zoom_frames == motion.FOCUS_FRAMES <= 12


def test_the_slide_still_shoots_once_when_nothing_moves():
    assert not motion.STILL
    assert motion.schedule(motion.STILL, crossfade=True) == []
