"""The safe-area layout fix (2026-10-18): every page the renderer draws for a
lesson sits inside safe-16-9, measured with the gate's own Chromium
measurement (video_qa.Pages), never a looser one.

The four lessons the gate held at d6613f3a (claude #1, n8n #0, n8n #1,
project #3) had a demo window bar above y 96, a step caption and store label
below y 984, and code cards that ran past it. The recording now sits in a
smaller hole (tutorial_demos.HOLE) and a code card that cannot fit at the 28 px
floor shows a window of its lines around the step's lit ones."""
from __future__ import annotations

import pytest

from atlas.modules.academy import tutorial_demos as td
from atlas.modules.academy import tutorial_design as design
from atlas.modules.academy import tutorial_series as ts
from atlas.modules.academy import tutorials as tut
from atlas.modules.academy import video_qa
from atlas.modules.academy import video_tokens as vt
from tests.chromium_support import chromium_ready

SAFE = vt.SAFE_16_9
HELD_BEFORE = (("claude", 1), ("n8n", 0), ("n8n", 1), ("project", 3))


def _pages(series_key: str, n: int) -> list[tuple[str, str]]:
    """Every 16:9 page `render` draws for the lesson, as `render` calls them."""
    s, ep = tut.resolve(series_key, n)
    out = [("result", design.result_title(ep, False)),
           ("scope", design.scope_card(ep, False))]
    lesson, _ = tut.card_line(s.key, ep, tut.beats(ep.scenes))
    chapters = max(b.chapter for b in lesson) + 1
    for i, b in enumerate(lesson):
        out.append((f"{b.scene.heading}[{b.step}]",
                    design.slide(b.scene, b.step, mark=s.mark, chapter=b.chapter,
                                 chapters=chapters, progress=i / len(lesson),
                                 countdown=b.countdown, hold=b.hold,
                                 steps_map=ep.steps_map, check=b.check)))
    out.append(("end", design.end_card(ep, tut.next_up(s.key, ep),
                                       shop=bool(tut.downloads_of(ep)))))
    return out


# ------------------------------------------------------------ the geometry

def test_the_demo_hole_leaves_the_bar_and_the_caption_inside_the_safe_area():
    x, y, w, h = td.HOLE
    assert w * 9 == h * 16 and w <= td.DEMO_W and h <= td.DEMO_H   # 16:9, never upscaled
    assert y - design.DEMO_BAR >= SAFE                              # the window bar
    row = y + h + design.CAP_GAP
    assert row + design.CAP_ROW <= design.H - SAFE                  # the caption row
    assert x >= design.RAIL_W and x + w <= design.W - SAFE
    assert td.LEGACY_HOLE == (258, 92, 1536, 864)


def test_a_short_crops_the_demo_where_its_render_laid_it():
    """A render made before 2026-10-18 has no demo_hole: its Shorts crop at
    the legacy hole; a new render records the new one."""
    old = tut.Manifest(series="n8n", episode=1, audio="fish", seconds=300.0,
                       chapters=[(0.0, "a")])
    assert tut.demo_hole_of(old) == td.LEGACY_HOLE
    new = tut.Manifest(series="n8n", episode=1, audio="fish", seconds=300.0,
                       chapters=[(0.0, "a")], demo_hole=list(td.HOLE))
    assert tut.demo_hole_of(new) == td.HOLE
    frames = [tut.ShortFrame("/w/p0.png", 0.0, 10.0, demo=True)]
    for hole in (td.LEGACY_HOLE, td.HOLE):
        argv = tut.short_argv("in.mp4", 0.0, 30.0, "out.mp4", frames=frames,
                              close="/w/close.png", hole=hole)
        graph = argv[argv.index("-filter_complex") + 1]
        x, y, w, h = hole
        assert f"crop={w}:{h}:{x}:{y}," in graph


# --------------------------------------------------------------- the code

def _code_scenes() -> list[tuple[str, int, ts.Scene]]:
    return [(s.key, ep.number, sc) for s in ts.SERIES for ep in s.episodes
            for sc in ep.scenes if sc.kind == "code"]


def test_a_code_card_never_shrinks_under_the_floor_and_shows_the_lit_lines():
    windowed = set()
    for key, n, sc in _code_scenes():
        room = design.code_room(sc)
        size = design._code_size(sc.code, room)
        assert vt.FLOOR_16_9 <= size <= vt.CODE.size
        lines = len(sc.code.split("\n"))
        longest = max(len(x) for x in sc.code.split("\n"))
        # every line stays inside its card at its size (the proof render's
        # 89-character JSON line ran past it before the card was widened)
        assert longest * design.MONO_ADVANCE * size <= design.CODE_BOX[0], (key, n)
        assert design.MAIN_LEFT + design.CODE_BOX[0] + 2 * (design.CARD_PAD_X + 2) \
            <= design.W - SAFE
        for step in range(len(sc.said)):
            a, b = design.code_window(sc, step)
            assert 0 <= a < b <= lines
            assert (b - a) * size * design.CODE_LINE <= room + 0.5     # fits the room
            if (a, b) != (0, lines):
                windowed.add((key, n, sc.heading))
            if sc.marks and step < len(sc.marks):
                lo, hi = sc.marks[step]
                if hi - lo <= b - a:
                    assert a <= lo and hi <= b, (key, n, sc.heading, step)
    # only n8n #1's 24-line workflow JSON is too long to show whole at 28 px
    assert windowed == {("n8n", 1, "The whole workflow as JSON")}


def test_the_json_window_follows_the_lit_lines():
    sc = next(sc for k, n, sc in _code_scenes() if sc.heading == "The whole workflow as JSON")
    first, second = design.code_window(sc, 0), design.code_window(sc, 1)
    assert first[0] == sc.marks[0][0] and first[1] >= sc.marks[0][1]
    assert second[0] <= sc.marks[1][0] and second[1] >= sc.marks[1][1]
    doc = design.slide(sc, 1, mark="m", chapter=0, chapters=1)
    assert doc.count('<span class="ln ') == second[1] - second[0]
    assert doc.count('<span class="ln lit">') == sc.marks[1][1] - sc.marks[1][0]


# ---------------------------------------------------------------- Chromium

needs_chromium = pytest.mark.skipif(not chromium_ready(), reason="Playwright/Chromium missing")


@needs_chromium
@pytest.mark.parametrize("series_key,n", HELD_BEFORE, ids=lambda x: str(x))
async def test_every_page_of_the_lessons_once_held_measures_inside(series_key, n):
    pages = video_qa.Pages()
    async with tut.Shooter() as sh:
        for _name, doc in _pages(series_key, n):
            pg = await sh._b.new_page(viewport={"width": design.W, "height": design.H})
            try:
                await pg.set_content(doc)
                await pg.evaluate("document.fonts.ready.then(() => null)")
                await pages.measure(pg, design.W, design.H)
            finally:
                await pg.close()
    assert pages.measured == len(_pages(series_key, n))
    assert video_qa.pages_refusal(pages.measured, pages.found) is None, pages.found[:3]
