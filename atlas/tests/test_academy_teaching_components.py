"""The Atlas Academy video system's teaching components (the owner, 2026-10-17;
docs/academy-video-system/components: StepMap, LowerThird, CheckpointTag,
ErrorRecovery, WorkflowTrace; .video-gap.md batch 4). Each is drawn only from
what the script fills in, never invented, and `success` only where the
script marks the scene verified."""
from __future__ import annotations

import os
import re
import shutil

import pytest

from atlas.modules.academy import tutorial_demos as td
from atlas.modules.academy import tutorial_design as design
from atlas.modules.academy import tutorial_motion as motion
from atlas.modules.academy import tutorial_series as ts
from atlas.modules.academy import tutorials as tut
from atlas.modules.academy import video_tokens as vt
from tests.chromium_support import chromium_ready

N8N1 = ts.series("n8n").episode(1)
SCRIPTED = [(s, e) for s in ts.SERIES for e in s.episodes if e.scripted]
# The marks of a state the script must have verified (never drawn otherwise).
SUCCESS_MARKS = ("sm-ok", "ck-ok", "er-ok", "nd-ok", design.CHECK_WORD, design.RAN_WORD,
                 ts.ERROR_ROW_VERIFIED)
COMPONENT_CLASSES = ('class="sm"', 'class="lt"', 'class="ck', 'class="er-p"', 'class="io"',
                     'class="tk"')

# A fixture, never a script: its "error" is a made-up string for this test
# only (no script may carry one that was not copied), and nothing here reaches
# a render of the catalog.
CARD = ts.ErrorCard(text="Fixture error in 'HTTP Request'\nnot an app's real words",
                    source="fixture", cause="The URL has a typo.",
                    fix="HTTP Request > URL: correct the address",
                    check="Execute step: results[0].name is Lisbon")
ERROR = ts.error_scene("Fixing a red node", "Fixing a red node", CARD,
                       ("e", "c", "f", "k"))
DIAGRAM = ts.Scene("Data moving", "diagram", "Where the city goes", ("a", "b", "c"),
                   items=("Webhook\na city comes in", "HTTP Request\nasks the API",
                          "Edit Fields\nbuilds the message"),
                   tokens=("", "city: Lisbon", "country: Portugal"),
                   io=("body.city: Lisbon", "results[0].country", ""))


def _body(doc: str) -> str:
    """The page without its stylesheet: what is drawn, not what could be."""
    return re.sub(r"<style>.*?</style>", "", doc, flags=re.DOTALL)


def _docs(ep: ts.Episode, mark: str = "m") -> list[tuple[ts.Scene, str]]:
    out = []
    for b in tut.beats(ep.scenes):
        out.append((b.scene, design.slide(b.scene, b.step, mark=mark, chapter=b.chapter,
                                          chapters=6, countdown=b.countdown, hold=b.hold,
                                          steps_map=ep.steps_map, check=b.check)))
    return out


# ------------------------------------------------------- neutral defaults

def test_a_scene_names_no_component_by_default():
    sc = ts.Scene("A", "bullets", "A", ("x",), items=("y",))
    assert (sc.map_step, sc.introduces, sc.checks, sc.error, sc.tokens, sc.io,
            sc.verified) == (-1, (), (), None, (), (), False)
    assert ts.Episode(1, "s", "t", "a\nb", "s", (sc,), level=1).steps_map == ()
    assert design.overlays(sc, 0, steps_map=("A", "B")) == ""
    doc = design.slide(sc, 0, mark="m", chapter=0, chapters=1, steps_map=("A", "B"))
    assert design.TEACH_CSS not in doc
    assert not any(c in doc for c in COMPONENT_CLASSES)


def test_every_component_field_keys_the_render():
    """Since the combined bump (2026-10-18) no field of a Scene, an
    ErrorCard or an Episode is kept out of repr, which script_key hashes:
    a change to any teaching component renders the draft again."""
    import dataclasses
    for cls in (ts.Scene, ts.ErrorCard, ts.Episode):
        hidden = [f.name for f in dataclasses.fields(cls) if not f.repr]
        assert hidden == [], (cls.__name__, hidden)
    sc = ts.Scene("A", "bullets", "A", ("x",), items=("y",))
    for change in ({"map_step": 0}, {"introduces": ("n8n", "x")}, {"checks": ((0, "y"),)},
                   {"tokens": ("t",)}, {"io": ("v",)}, {"verified": True}, {"error": CARD}):
        filled = ts.replace(sc, **change)
        assert repr(sc) != repr(filled), change
        ep = ts.replace(N8N1, scenes=(filled, *N8N1.scenes[1:]))
        assert tut.script_key(ep) != tut.script_key(N8N1), change
    assert tut.script_key(ts.replace(N8N1, steps_map=("A",))) != tut.script_key(N8N1)
    assert "steps_map=" in repr(N8N1) and "map_step=" in repr(N8N1.scenes[2])


@pytest.mark.parametrize("s,ep", SCRIPTED, ids=lambda x: getattr(x, "slug", ""))
def test_every_script_s_component_data_can_be_drawn(s, ep):
    assert ts.components_refusal(ep) is None


@pytest.mark.parametrize("s,ep", SCRIPTED, ids=lambda x: getattr(x, "slug", ""))
def test_no_component_draws_success_unless_the_script_marks_it_verified(s, ep):
    """The components' own success marks only (the quiz's right answer, the
    recap ticks and the end card's check are other batches' styling)."""
    for sc, doc in _docs(ep, s.mark):
        assert "Tested" not in doc
        if not sc.verified:
            assert not [m for m in SUCCESS_MARKS if m in _body(doc)], sc.heading
    # nothing in the catalog is marked verified yet (batch 3's rule)
    assert not [sc.heading for sc in ep.scenes if sc.verified]


def test_bad_component_data_is_refused():
    sc = ts.Scene("A", "bullets", "A", ("x", "y"), items=("p", "q"))
    ep = ts.Episode(1, "s", "t", "a\nb", "s", (sc,), level=1, steps_map=("A", "B"))
    bad = {
        "nodes; 4 at most": ts.replace(ep, steps_map=("a", "b", "c", "d", "e")),
        "not on the step map": ts.replace(ep, scenes=(ts.replace(sc, map_step=2),)),
        "introduced twice": ts.replace(ep, scenes=(
            ts.replace(sc, introduces=("n8n", "x")),
            ts.replace(sc, chapter="B", introduces=("N8N", "y")))),
        "nothing to look for": ts.replace(ep, scenes=(ts.replace(sc, checks=((2, "x"),)),)),
        "with no node": ts.replace(ep, scenes=(ts.replace(sc, tokens=("", "a", "b")),)),
        "into the first node": ts.replace(ep, scenes=(ts.replace(sc, tokens=("a",)),)),
        "copied text, source": ts.replace(ep, scenes=(ts.replace(
            ERROR, error=ts.replace(CARD, source="")),)),
        "said in 4 lines": ts.replace(ep, scenes=(ts.replace(ERROR, said=("e",)),)),
    }
    for want, got in bad.items():
        why = ts.components_refusal(got)
        assert why is not None and want in why, (want, why)


# --------------------------------------------------------------- StepMap

def test_the_step_map_reads_step_k_of_n_and_lights_one_node():
    html = design.step_map(("Webhook", "HTTP Request", "Slack"), 1)
    assert "Step 2 of 3" in html and html.count("sm-now") == 1
    assert html.count('class="sm-w sm-in"') == 1          # the connector into it
    assert "sm-ok" not in html and design.CHECK_WORD not in html
    ok = design.step_map(("Webhook", "HTTP Request"), 0, verified=True)
    assert "sm-ok" in ok and design.CHECK_WORD in ok and "<svg" in ok
    assert design.step_map((), 0) == "" and design.step_map(("A",), 1) == ""


def test_n8n_1_maps_its_payoff_s_nodes_and_never_onto_a_demo():
    assert N8N1.steps_map == ("Webhook", "HTTP Request", "Slack")
    assert len(N8N1.steps_map) <= ts.STEPS_MAP_MAX
    assert all(name in " ".join(N8N1.payoff.items) for name in N8N1.steps_map)
    docs = _docs(N8N1)
    shown = [sc.heading for sc, doc in docs if 'class="sm"' in doc]
    assert "The test request" in shown and "Your Slack node" in shown
    for sc, doc in docs:
        if sc.kind == "demo":
            assert 'class="sm"' not in doc          # the app is the picture
        assert doc.count('class="sm"') <= 1
    # Edit Fields stands in for Slack in step 3's demo: it is not mapped
    step3 = next(sc for sc in N8N1.scenes if sc.chapter == "Step 3: the message")
    assert step3.map_step == -1


def test_the_step_map_is_at_one_position_inside_the_safe_area():
    css = design.TEACH_CSS
    assert f".sm{{position:absolute;left:{design.MAIN_LEFT}px;top:{vt.SAFE_16_9}px;" in css
    assert css.count(".sm{") == 1 and "body.sheet .sm" not in css


def test_the_map_node_lifts_only_on_an_orientation_beat():
    sc = next(sc for sc in N8N1.scenes if sc.heading == "The test request")
    lift = design.beat_motion(sc, 0, orient=True)
    assert (design.SM_NOW, False) in lift.groups
    assert (design.SM_NOW, False) not in design.beat_motion(sc, 0).groups
    assert (design.SM_NOW, False) not in design.beat_motion(sc, 1, orient=False).groups


# ------------------------------------------------------------- LowerThird

def test_the_lower_third_names_the_tool_once_where_it_is_first_on_screen():
    names = [(i, sc.introduces) for i, sc in enumerate(N8N1.scenes) if sc.introduces]
    assert names == [(2, ("n8n", "Cloud or self-hosted"))]
    first = N8N1.scenes[2]
    assert first.kind == "demo" and td.DEMOS[first.demo].app == "n8n"
    assert not [sc for sc in N8N1.scenes[:2] if sc.kind == "demo"]
    docs = [doc for sc, doc in _docs(N8N1) if 'class="lt"' in doc]
    assert len(docs) == 1 and "Cloud or self-hosted" in docs[0]


def test_the_lower_third_is_on_the_first_narrated_step_only():
    sc = ts.replace(ts.Scene("A", "quiz", "Q", ("q", "a"), items=("x", "y"), answer=0),
                    introduces=("Claude", "the AI app"))
    assert 'class="lt"' in design.overlays(sc, 0)
    assert design.overlays(sc, 1) == ""
    assert design.overlays(sc, 0, still=True) == ""        # a countdown second
    spec = design.beat_motion(sc, 0)
    assert (design.LOWER, False) in spec.groups and spec.exits == (design.LOWER,)


# ----------------------------------------------------------- CheckpointTag

def test_the_checkpoint_holds_silent_for_hold_tag_after_its_step():
    plan = tut.beats(N8N1.scenes)
    checks = [b for b in plan if b.check >= 0]
    assert len(checks) == 1
    b = checks[0]
    before = plan[plan.index(b) - 1]
    assert (before.scene, before.step) == (b.scene, b.step) and before.said
    assert b.silent == tut.CHECK_SECONDS and not b.said
    assert tut.CHECK_SECONDS == motion.sec(2 + 2 * vt.TITLE_RISE_FRAMES + vt.HOLD_TAG_FRAMES)
    assert not b.last_of_scene and b.step == 1
    assert b.scene.demo == "n8n-message"
    assert b.scene.checks == ((1, "The message `Lisbon is in Portugal`"),)
    # the value the recorded step's OUTPUT shows: the payoff's own line
    assert "`Lisbon is in Portugal`" in b.scene.checks[0][1]
    assert N8N1.payoff.code == "Lisbon is in Portugal"
    assert "the message is ready" in td.DEMOS["n8n-message"].steps[1].caption


def test_the_checkpoint_tag_names_its_value_and_is_neutral_unless_verified():
    tag = design.checkpoint_tag("`Lisbon`, at `body.city`")
    assert design.CHECK_SEE in tag and "<code>Lisbon</code>" in tag
    assert "<code>body.city</code>" in tag and "ck-ok" not in tag
    ok = design.checkpoint_tag("x", verified=True)
    assert "ck-ok" in ok and design.CHECK_WORD in ok and "<svg" in ok
    assert f".ck.ck-ok{{border:4px solid {vt.SUCCESS}}}" in design.TEACH_CSS


def test_the_checkpoint_rises_in_holds_and_rises_out():
    sc = next(sc for sc in N8N1.scenes if sc.checks)
    spec = design.beat_motion(sc, 1, check=0)
    assert spec.groups == ((design.CHECK_TAG, False),) and spec.exits == (design.CHECK_TAG,)
    assert not spec.trace
    plan = motion.schedule(spec, crossfade=False)
    start, frames = plan[0][1], plan[0][2]
    assert frames == vt.TITLE_RISE_FRAMES
    out = round(start + motion.sec(frames + motion.HOLD_FRAMES), 3)
    e = motion.Enter("t.png", 10, 900, start, frames, motion.RISE_PX, out)
    assert e.gone == round(out + motion.sec(vt.TITLE_RISE_FRAMES), 3)
    assert e.gone <= tut.CHECK_SECONDS


def test_an_element_that_leaves_fades_and_rises_out_then_is_gone():
    e = motion.Enter("lt.png", 282, 830, 0.067, 9, 16, out=3.367)
    argv = tut.segment_argv("s.png", "a.wav", 5.0, "o.mp4", enters=[e])
    graph = argv[argv.index("-filter_complex") + 1]
    assert "fade=t=out:st=3.367:d=0.3:alpha=1" in graph
    assert "eof_action=pass" in graph
    assert f"-16*{motion.ease(3.367, 0.3)}" in graph
    i = argv.index("lt.png")
    assert argv[i - 2] == f"{e.gone + 1 / 30:.3f}"


# ----------------------------------------------------------- ErrorRecovery

def test_the_error_card_shows_the_copied_error_then_one_row_a_step():
    first = design.error_card(ERROR, 0)
    assert "Fixture error in &#x27;HTTP Request&#x27;" in first
    assert first.count("er-hide") == 3 and "er-now" not in first
    last = design.error_card(ERROR, 3)
    assert "er-hide" not in last and last.count("er-now") == 1
    for label in ts.ERROR_ROWS:
        assert f"<b>{label}</b>" in last
    assert ts.ERROR_ROW_VERIFIED not in last and "er-ok" not in last
    ok = design.error_card(ts.replace(ERROR, verified=True), 3)
    assert ts.ERROR_ROW_VERIFIED in ok and "er-ok" in ok and "<b>Check</b>" not in ok
    assert f"border:4px solid {vt.ERROR}" in design.TEACH_CSS


def test_the_error_is_cut_in_and_its_rows_rise():
    assert design.beat_motion(ERROR, 0) == motion.STILL
    assert design.beat_motion(ERROR, 2).groups == ((design.ERROR_ROW_NOW, False),)
    doc = design.slide(ERROR, 0, mark="m", chapter=0, chapters=1)
    assert design.TEACH_CSS in doc and "GLYPH" not in doc


def test_no_scripted_episode_draws_an_error_card_yet():
    """ErrorRecovery needs an error copied verbatim with its source; no
    script carries one today, so none is drawn (never a paraphrase)."""
    assert not [sc for _s, ep in SCRIPTED for sc in ep.scenes if sc.error is not None]


# ---------------------------------------------------------- WorkflowTrace

def test_the_trace_carries_the_script_s_token_one_connector_a_beat():
    for step in range(1, 3):
        spec = design.motion_of(DIAGRAM, step)
        assert spec.trace == (step - 1, step) and spec.token == DIAGRAM.tokens[step]
    assert design.motion_of(DIAGRAM, 0).trace is None
    bare = ts.replace(DIAGRAM, tokens=())
    assert design.motion_of(bare, 1).token == ""            # the dot, nothing invented


def test_the_opening_trace_takes_a_payoff_token_only_when_given():
    assert design.result_motion(N8N1).token == ""
    pay = ts.replace(N8N1.payoff, tokens=("", "", "text: x"))
    spec = design.result_motion(ts.replace(N8N1, payoff=pay))
    assert spec.trace == (1, 2) and spec.token == "text: x"


def test_values_show_under_lit_nodes_and_ran_only_when_verified():
    doc = _body(design.slide(DIAGRAM, 1, mark="m", chapter=0, chapters=1))
    assert doc.count('class="io"') == 2 and "body.city: Lisbon" in doc
    assert "nd-ok" not in doc and design.RAN_WORD not in doc
    ok = _body(design.slide(ts.replace(DIAGRAM, verified=True), 1, mark="m", chapter=0,
                            chapters=1))
    assert ok.count("nd-ok") == 1 and design.RAN_WORD in ok
    first = _body(design.slide(ts.replace(DIAGRAM, verified=True), 0, mark="m",
                               chapter=0, chapters=1))
    assert "nd-ok" not in first                          # nothing has travelled yet


def test_the_token_is_a_mono_value_tag_on_blue():
    doc = design.trace_token("city: Lisbon")
    assert vt.VALUE_TAG.css() in doc and f"background:{vt.BLUE}" in doc
    assert f"color:{vt.ON_BLUE}" in doc and "city: Lisbon" in doc


# ------------------------------------------------------------ the palette

def test_the_component_css_draws_only_tokens():
    colours = {c.lower() for c in re.findall(r"#[0-9a-fA-F]{6}\b", design.TEACH_CSS)}
    assert colours <= {c.lower() for c in vt.COLORS.values()}
    sizes = {int(n) for n in re.findall(r"font:\s*\d+\s+(\d+)px", design.TEACH_CSS)}
    assert sizes <= {s.size for s in vt.TYPE_STYLES}
    assert min(sizes - {vt.VALUE_TAG.size}) >= vt.FLOOR_16_9


def test_the_demo_corner_matches_the_recording_s_hole():
    assert design.DEMO_LEFT == td.HOLE_X
    assert design.DEMO_BOTTOM == td.HOLE_Y + td.HOLE_H


# ---------------------------------------------------------------- Chromium

needs_chromium = pytest.mark.skipif(not chromium_ready(), reason="Playwright/Chromium missing")

_BOX_JS = """(sel) => { const e = document.querySelector(sel); if (!e) return null;
  const r = e.getBoundingClientRect(); return [r.left, r.top, r.right, r.bottom]; }"""


async def _boxes(doc: str, *sels: str) -> list:
    async with tut.Shooter() as sh:
        pg = await sh._b.new_page(viewport={"width": design.W, "height": design.H})
        try:
            await pg.set_content(doc)
            await pg.evaluate("document.fonts.ready.then(() => null)")
            return [await pg.evaluate(_BOX_JS, s) for s in sels]
        finally:
            await pg.close()


def _inside_safe(box) -> bool:
    s = vt.SAFE_16_9
    return box[0] >= s and box[1] >= s and box[2] <= design.W - s and box[3] <= design.H - s


@needs_chromium
async def test_the_components_sit_inside_the_safe_area_and_the_map_never_moves():
    docs = {sc.heading: doc for sc, doc in _docs(N8N1)}
    code = await _boxes(docs["The test request"], ".sm", ".main h1")
    steps = await _boxes(docs["Your Slack node"], ".sm", ".main h1")
    assert code[0] == steps[0] and _inside_safe(code[0])
    for sm, h1 in (code, steps):
        assert sm[3] <= h1[1] + 2                     # above the heading
        assert sm[2] <= design.W - vt.SAFE_16_9 - vt.ENDSCREEN_RESERVE
    demo = next(doc for sc, doc in _docs(N8N1) if 'class="lt"' in doc)
    lt, = await _boxes(demo, ".lt")
    assert _inside_safe(lt)
    assert lt[0] >= td.HOLE_X and lt[3] <= td.HOLE_Y + td.HOLE_H     # in the app's corner
    ck_doc = next(doc for sc, doc in _docs(N8N1) if 'class="ck' in doc)
    ck, = await _boxes(ck_doc, ".ck")
    assert _inside_safe(ck) and ck[0] >= td.HOLE_X and ck[3] <= td.HOLE_Y + td.HOLE_H
    err = await _boxes(design.slide(ERROR, 3, mark="m", chapter=0, chapters=1),
                       ".er-p", ".er-rows")
    assert all(_inside_safe(b) for b in err)


def _have_ffmpeg() -> bool:
    return shutil.which("ffmpeg") is not None


async def _mean(path: str, t: float, box: tuple[int, int, int, int]) -> tuple[int, ...]:
    """The mean colour of `box` (x, y, w, h) in the frame at `t` s."""
    proc = await __import__("asyncio").create_subprocess_exec(
        "ffmpeg", "-loglevel", "error", "-ss", f"{t:.3f}", "-i", path, "-frames:v", "1",
        "-vf", f"crop={box[2]}:{box[3]}:{box[0]}:{box[1]},scale=1:1:flags=area",
        "-f", "rawvideo", "-pix_fmt", "rgb24", "-", stdout=-1)
    out, _ = await proc.communicate()
    return tuple(out[:3])


@needs_chromium
@pytest.mark.skipif(not _have_ffmpeg(), reason="ffmpeg missing")
async def test_a_rendered_checkpoint_rises_in_holds_and_is_gone_at_its_end(tmp_path):
    """A real render (Chromium and ffmpeg) of a checkpoint beat over a slide:
    the tag is not there at frame 0, is there through the hold, and has risen
    out by the segment's last frame."""
    from atlas.modules.content import ffmpeg
    sc = next(sc for sc in N8N1.scenes if sc.heading == "The test request")
    sc = ts.replace(sc, checks=((1, "`Lisbon`, at `body.city`"),))
    doc = design.slide(sc, 1, mark="n8n", chapter=1, chapters=6,
                       steps_map=N8N1.steps_map, check=0)
    base = str(tmp_path / "ck")
    async with tut.Shooter() as sh:
        png, enters, trace = await sh.layers(doc, base, design.beat_motion(sc, 1, check=0),
                                             crossfade=False)
    assert len(enters) == 1 and enters[0].out is not None and trace is None
    assert os.path.isfile(base + ".png")
    e = enters[0]
    audio, out = str(tmp_path / "s.wav"), str(tmp_path / "ck.mp4")
    await ffmpeg.run(tut.silence_argv(tut.CHECK_SECONDS, audio, False))
    await ffmpeg.run(tut.segment_argv(png, audio, tut.CHECK_SECONDS, out, enters=enters))
    assert abs(await ffmpeg.duration(out) - tut.CHECK_SECONDS) < 0.1
    box = (e.x + 60, e.y + 60, 200, 40)                  # inside the tag
    before = await _mean(out, 0.0, box)
    held = await _mean(out, 2.0, box)
    after = await _mean(out, tut.CHECK_SECONDS - 0.05, box)
    assert max(abs(a - b) for a, b in zip(before, held, strict=True)) > 6
    assert max(abs(a - b) for a, b in zip(before, after, strict=True)) <= 3


async def test_a_checkpoint_beat_is_a_cut_so_its_tag_leaves_in_time(monkeypatch, tmp_path):
    """The checkpoint holds the same picture: no crossfade from the step
    before, so the tag rises from frame 2 and is gone inside CHECK_SECONDS."""
    from atlas.modules.academy import reel_design, tutorial_music
    from atlas.modules.content import ffmpeg
    monkeypatch.setenv("ACADEMY_TUTORIALS_DIR", str(tmp_path))
    calls: list[dict] = []

    class Shooter:
        pages = tut.video_qa.Pages()          # batch 9: render records it

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return None

    async def segment(shooter, doc, said, base, preview, **kw):
        calls.append({"doc": doc, **kw})
        return base + ".mp4", kw.get("silent") or 3.0, "", None

    async def opening(ep, shooter, work, preview=False):
        return tut.Opening(parts=[str(tmp_path / "o.mp4")], seconds=10.0, timings=[]), None

    async def run(argv, timeout=0):
        return b""

    async def duration(path):
        return 290.0

    async def add_bed(video, key, out, **_kw):
        return 0.0

    async def thumb(ep, shooter, out_png, **kw):
        from pathlib import Path
        Path(out_png).write_bytes(b"\x89PNG\r\n\x1a\nfake")
    monkeypatch.setattr(tutorial_music, "add_bed", add_bed)
    monkeypatch.setattr(tut, "Shooter", Shooter)
    monkeypatch.setattr(tut, "_segment", segment)
    monkeypatch.setattr(tut, "opening_part", opening)
    monkeypatch.setattr(tut.thumbs, "render", thumb)
    monkeypatch.setattr(ffmpeg, "run", run)
    monkeypatch.setattr(ffmpeg, "duration", duration)
    monkeypatch.setattr(ffmpeg, "missing", lambda: None)
    monkeypatch.setattr(reel_design, "available", lambda: True)
    slide_check = ts.replace(N8N1, scenes=tuple(
        ts.replace(sc, checks=((1, "`x`"),)) if sc.heading == "The test request" else sc
        for sc in N8N1.scenes))
    monkeypatch.setattr(tut, "resolve", lambda key, n: (ts.series("n8n"), slide_check))
    _d, _m, why = await tut.render("n8n", 1)
    assert why is None, why
    checks = [k for k in calls if 'class="ck' in _body(k["doc"])]
    assert len(checks) == 2                        # the slide's and the demo's
    assert all(k.get("prev") is None for k in checks)
    assert all(k["silent"] == tut.CHECK_SECONDS for k in checks)
