"""The teaching Short's portrait frame (academy/short_frame.py, ShortsFrame,
Miro 2026-10-17): the hook from frame 0, each scene recomposed natively at
1080x1920 in the Short's type scale, the demo as its prepared crop, the
closing card naming the longer lesson -- every text box inside the safe
area and nothing under shorts-caption (60 px)."""
from __future__ import annotations

import re

import pytest

from atlas.modules.academy import short_frame as sf
from atlas.modules.academy import tutorial_series as ts
from atlas.modules.academy import video_tokens as vt

SAFE = (vt.SAFE_9_16_X, vt.SAFE_9_16_TOP, sf.W - vt.SAFE_9_16_X, sf.H - vt.SAFE_9_16_BOTTOM)


def _sizes(doc: str) -> list[int]:
    """Every font size the document sets, @font-face rules aside."""
    css = re.sub(r"@font-face\{[^}]*\}", "", doc)
    return ([int(x) for x in re.findall(r"font:\s*\d+\s+(\d+)px", css)]
            + [int(x) for x in re.findall(r"font-size:\s*(\d+)px", css)])


def _scenes() -> list[ts.Scene]:
    out = []
    for s in ts.SERIES:
        for ep in s.episodes:
            out += [sc for sc in (ep.payoff, ep.hook, *ep.scenes) if sc is not None]
    return out


def _inside(box: tuple[int, int, int, int]) -> bool:
    x, y, w, h = box
    return (x >= SAFE[0] and y >= SAFE[1] and x + w <= SAFE[2] and y + h <= SAFE[3])


def test_the_frame_is_1080_by_1920_and_every_box_sits_inside_the_safe_area():
    assert (sf.W, sf.H) == (1080, 1920)
    for box in (sf.HOOK_BOX, sf.PANEL_BOX, sf.DEMO_BOX, sf.CAPTION_BOX):
        assert _inside(box), box
    # top to bottom, never overlapping: hook, panel, captions
    _, hy, _, hh = sf.HOOK_BOX
    px, py, pw, ph = sf.PANEL_BOX
    cy = sf.CAPTION_BOX[1]
    assert hy + hh <= py and py + ph <= cy
    dx, dy, dw, dh = sf.DEMO_BOX
    assert px <= dx and dx + dw <= px + pw and py <= dy and dy + dh <= py + ph


def test_the_hook_is_shorts_hook_type_in_two_lines_at_most():
    doc = sf.shot(None, 0, "A specialist, one file")
    hook = doc[doc.index(".hook{"):doc.index("}", doc.index(".hook{"))]
    assert vt.SHORTS_HOOK.css() in hook
    assert sf.HOOK_BOX[3] == 2 * vt.SHORTS_HOOK.line + 2 * sf.HOOK_INK
    assert ">A specialist, one file<" in doc


@pytest.mark.parametrize("kind", ts.SCENE_KINDS)
def test_every_scene_kind_draws_with_nothing_under_60px(kind):
    # a kind no episode uses yet is drawn from a made-up scene
    scenes = [sc for sc in _scenes() if sc.kind == kind] or [
        ts.Scene("A chapter", kind, "A heading", ("First line. Then more.", "Second."),
                 items=("One thing", "Another thing"), code="x = 1")]
    for sc in scenes:
        for step in range(max(1, len(sc.said))):
            doc = sf.shot(sc, step, "Three short words")
            sizes = _sizes(doc)
            assert sizes and min(sizes) >= vt.SHORTS_CAPTION.size, (kind, sizes)
            assert "Made with AI" not in doc


def test_every_kind_but_the_demo_is_recomposed_natively():
    assert set(sf.NATIVE_KINDS) | set(sf.CROP_KINDS) == set(ts.SCENE_KINDS)
    assert sf.CROP_KINDS == ("demo",)
    for sc in _scenes():
        if sc.kind == "demo":
            assert 'class="hole"' in sf.shot(sc, 0, "Three short words")


def test_what_a_scene_shows_fits_the_panel_without_clipping():
    """Text is dropped to fit, never clipped: the rows each step draws, by
    the panel's own measure, fit its height."""
    for sc in _scenes():
        for step in range(max(1, len(sc.said))):
            blocks = sf.layout(sc, step)
            assert sf.height(blocks) <= sf.FIT_H <= sf.PANEL_INNER_H, (sc.kind, sc.heading, step)


def test_the_current_step_is_never_the_one_dropped():
    sc = next(sc for sc in _scenes() if sc.kind == "steps" and len(sc.items) >= 3)
    last = len(sc.items) - 1
    text = " ".join(b.text for b in sf.layout(sc, last))
    assert sc.items[last] in text


def test_the_closing_card_names_the_lesson_by_its_title_and_promises_no_link():
    title = "Claude Code Subagents Tutorial: Specialists That Work for You"
    doc = sf.close_shot("A specialist, one file", title)
    assert title in doc
    assert min(_sizes(doc)) >= vt.SHORTS_CAPTION.size
    shown = re.sub(r"<style>.*</style>", "", doc, flags=re.DOTALL).lower()
    assert "link" not in shown and "http" not in shown and "below" not in shown


def test_no_frame_says_made_with_ai():
    """Miro, 2026-10-17: disclosed in the post and the platform flag, never
    on screen."""
    for doc in (sf.shot(None, 0, "Three short words"),
                sf.close_shot("Three short words", "A title")):
        assert "Made with AI" not in doc and "AI-generated" not in doc
