"""The ATLAS Academy thumbnail system and the tutorial watermark (2026-10-11)."""
from __future__ import annotations

import re
import struct
from itertools import pairwise
from pathlib import Path

import pytest

from atlas.modules.academy import tutorial_design as design
from atlas.modules.academy import tutorial_thumbs as tt
from atlas.modules.academy import tutorials as tut
from atlas.modules.academy.tutorial_series import SERIES, Scene

EPISODES = [(s, ep) for s in SERIES for ep in s.episodes]


class Ep:
    """An episode as derive() reads it."""

    def __init__(self, words: str, summary: str = "", scenes: tuple = (), slug: str = "x"):
        self.thumb_title, self.summary, self.scenes, self.slug = words, summary, scenes, slug


# ------------------------------------------------------------ choosing

def test_the_layout_follows_what_the_video_is():
    assert tt.derive(Ep("What is\nZapier?")).archetype == "question"
    assert tt.derive(Ep("Agent or\nworkflow?")).archetype == "versus"
    assert tt.derive(Ep("Agent or\nworkflow?")).data == ("Agent", "workflow")
    assert tt.derive(Ep("MCP not\nworking?")).archetype == "fix"
    assert tt.derive(Ep("No more\nno-shows", "booking reminders")).focal == "calendar"
    code = Scene("Code", "code", "The file", ("said",), code="---\nname: x\n---")
    assert tt.derive(Ep("Your first\nSKILL.md", scenes=(code,))).archetype == "command"
    assert tt.derive(Ep("n8n on\nDocker")).focal == "terminal"
    flow = Scene("Flow", "diagram", "How", ("a", "b"), items=("Form", "Claude"))
    built = tt.derive(Ep("Plan the\nproject", scenes=(flow,)))
    assert built.archetype == "build" and built.data == ("Form", "Claude")
    assert tt.derive(Ep("Weekly\nreport", "every Monday a report")).focal == "bars"


def test_an_episode_spec_wins_over_the_table_and_the_table_over_deriving():
    ep = SERIES[1].episodes[1]
    assert tt.spec_for(ep) == tt.THUMBS[ep.slug]

    own = Ep("What is\nit?", slug=ep.slug)
    own.thumb = {"archetype": "versus", "focal": "pair", "data": ("A", "B")}
    assert tt.spec_for(own).archetype == "versus"
    assert tt.spec_for(Ep("What is\nit?", slug="not-in-the-table")).archetype == "question"


def test_a_scripted_episode_shows_its_own_code_not_the_tables_stand_in():
    code = Scene("Hooks", "code", "The file", ("said",),
                 code='# a hook\n{"PreToolUse": [{\n  "matcher": "Edit"\n}]}', marks=((2, 3),))
    ep = Ep("Claude Code\nhooks", scenes=(code,), slug="claude-code-hooks")
    spec = tt.spec_for(ep)
    assert spec.archetype == "command" and spec.extra["name"] == "settings.json"
    assert spec.data == ('{"PreToolUse": [{', '  "matcher": "Edit"', "}]}") and spec.lit == 1
    unscripted = tt.spec_for(Ep("Claude Code\nhooks", slug="claude-code-hooks"))
    assert unscripted == tt.THUMBS["claude-code-hooks"]


def test_the_table_names_only_real_episodes_and_real_layouts():
    slugs = {ep.slug for _s, ep in EPISODES}
    assert set(tt.THUMBS) <= slugs
    for spec in tt.THUMBS.values():
        assert spec.archetype in tt.ARCHETYPES
        assert spec.focal in (*tt.FOCALS, "pair")


SCRIPTED_EPS = [(s, ep) for s, ep in EPISODES if ep.scripted]


@pytest.mark.parametrize("s,ep", SCRIPTED_EPS,
                         ids=[f"{s.key}-{e.number}" for s, e in SCRIPTED_EPS])
def test_every_thumbnail_reads_at_phone_list_size(s, ep):
    """Scripted episodes; an unscripted one is held by tt.render's own check."""
    spec, look = tt.spec_for(ep), tt.look_for(ep)
    assert tt.legibility_refusal(spec, tt.words_of(ep), look.family) is None
    l1, l2 = tt.word_lines(spec, tt.words_of(ep))
    assert len(f"{l1} {l2}".split()) <= tt.MAX_WORDS
    assert tt.family_px(look.family, l1, l2) >= tt.MIN_WORD_PX     # 13.65 px at 168x94


def test_legibility_refuses_too_many_words_and_too_long_lines():
    assert "words" in tt.legibility_refusal(tt.Thumb("build", "flow"),
                                            "One two three\nfour five six")
    assert "px" in tt.legibility_refusal(tt.Thumb("question", "flow"),
                                         "Understanding everything\nabout configurations")
    assert "layout" in tt.legibility_refusal(tt.Thumb("collage", "flow"), "A b\nc")


def test_every_episode_gets_its_own_spec():
    pictures = [(tt.spec_for(ep).archetype, tt.spec_for(ep).focal, tt.spec_for(ep).data,
                 tt.word_lines(tt.spec_for(ep), ep.thumb_title)) for _s, ep in EPISODES]
    assert len(set(pictures)) == len(pictures)
    docs = {design.thumbnail(ep.thumb_title, s.key, s.mark, ep.number, ep=ep)
            for s, ep in EPISODES}
    assert len(docs) == len(EPISODES)


# ------------------------------------------- batch 7 (the owner, 2026-10-17)
# "create thumbnails for each video and different as well": a family of
# compositions per episode, one real image, the result's words.

SCRIPTED = [(s, ep) for s, ep in EPISODES if ep.scripted]
PNG = b"\x89PNG\r\n\x1a\n"


def _page(ep, badge="", image="data:image/png;base64,AAAA"):
    """The page without its embedded fonts (base64 can spell anything)."""
    from atlas.modules.academy.video_tokens import fonts_css
    return tt.page(ep, level=ep.level, badge=badge, image=image).replace(fonts_css(), "")


def test_the_words_are_the_thumb_title_then_the_result_then_the_payoff():
    payoff = Scene("p", "payoff", "h", ("s",), caption="your tool, your rules")
    ep = Ep("Your server\nin n8n", scenes=(payoff,))
    ep.payoff = payoff
    assert tt.words_of(ep) == "Your server\nin n8n"
    ep.result = "Your own\nMCP server"
    assert tt.words_of(ep) == "Your server\nin n8n"
    no_title = Ep("")
    no_title.result = "Your own\nMCP server"
    assert tt.words_of(no_title) == "Your own\nMCP server"
    bare = Ep("")
    bare.payoff = payoff
    assert tt.words_of(bare) == "your tool, your rules"
    # the page shows exactly the result's words, never the table's override
    ep = next(e for _s, e in SCRIPTED if e.slug == "agent-tools-memory-planning")
    l1, l2 = tt.words_of(ep).split("\n")
    doc = _page(ep)
    assert f">{l1}<" in doc and f">{l2}<" in doc and "What makes" not in doc


@pytest.mark.parametrize("s,ep", SCRIPTED, ids=[f"{s.key}-{e.number}" for s, e in SCRIPTED])
def test_every_thumbnail_has_two_to_five_words_in_sentence_case(s, ep):
    words = tt.words_of(ep).split()
    assert 2 <= len(words) <= 5, words
    names = {"ATLAS", "MCP", "AI", "GB", "SKILL.md"}                  # names, not shouting
    assert not any(w.strip(",?") not in names and w.isupper() and len(w) > 1
                   for w in words), words
    doc = _page(ep, badge="5 MIN")
    assert "uppercase" not in doc and ">5 min<" in doc and "MIN" not in doc


def test_neighbouring_episodes_never_share_a_family_and_every_family_is_used():
    used = set()
    for s in SERIES:
        eps = [e for e in s.episodes if e.scripted]
        fams = [tt.look_for(e).family for e in eps]
        assert all(a != b for a, b in pairwise(fams)), (s.key, fams)
        used |= set(fams)
    assert used == set(tt.FAMILIES)


def test_the_family_follows_from_the_episode_and_is_the_same_every_time():
    first = {e.slug: tt.look_for(e) for _s, e in SCRIPTED}
    assert first == {e.slug: tt.look_for(e) for _s, e in SCRIPTED}
    # a demo episode leads with its recording; a question with its question
    assert tt.ranked(SERIES[2].episode(0))[0] in ("bleed", "focal")
    assert "question" in tt.ranked(SERIES[1].episode(0))
    assert "question" not in tt.ranked(SERIES[2].episode(1))       # no question mark
    # each series sets its accent on its own edge
    assert len({tt.look_for(s.episodes[0]).accent for s in SERIES[:4]}) == 4


def test_a_family_takes_only_words_it_can_set_large_enough():
    for _s, ep in SCRIPTED:
        look = tt.look_for(ep)
        l1, l2 = tt.words_of(ep).split("\n")
        assert tt.family_px(look.family, l1, l2) >= tt.MIN_WORD_PX, (ep.slug, look)


def test_the_real_image_is_the_demo_frame_else_the_payoff_frame():
    from atlas.modules.academy.tutorial_demos import DEMOS
    demo = {(s.key, e.number) for s, e in SCRIPTED if tt.source(e).kind == "demo"}
    assert demo == {("claude", 1), ("n8n", 0), ("n8n", 1)}
    src = tt.source(SERIES[2].episode(0))
    assert src.key == "n8n-tour" and src.step == len(DEMOS["n8n-tour"].steps) - 1
    pay = tt.source(SERIES[2].episode(4))
    assert pay.kind == "slide" and pay.scene is SERIES[2].episode(4).payoff
    welcome = tt.source(SERIES[0].episodes[0])          # no payoff: its own picture
    assert welcome.kind == "slide" and welcome.scene.kind in ("diagram", "playlists", "code")
    view = tt.view_doc(pay.scene, "bubble")
    assert ".rail" in view and "display:none" in view


@pytest.mark.parametrize("s,ep", SCRIPTED, ids=[f"{s.key}-{e.number}" for s, e in SCRIPTED])
def test_one_real_image_no_glow_no_3d_still_no_ai_label(s, ep):
    doc = _page(ep, badge="5 MIN")
    assert doc.count("data:image/png;base64,AAAA") == 1
    assert "data:image/jpeg" not in doc                  # the series' 3D still
    for gone in ("filter", "blur(", "glow", "drop-shadow", "Made with AI", design.TAG):
        assert gone not in doc, gone
    assert "<text" not in doc                            # no drawn focal labels


def test_glyphs_are_single_stroke_2px_on_a_24_box():
    docs = [_page(ep) for _s, ep in SCRIPTED]
    boxes = [b for d in docs for b in re.findall(r'<svg[^>]*viewBox="([^"]+)"', d)]
    assert boxes and set(boxes) == {"0 0 24 24"}
    widths = {w for d in docs for w in re.findall(r'stroke-width="([^"]+)"', d)}
    assert widths == {"2"}
    assert not any(re.search(r'<path[^>]*fill="#', d) for d in docs)


@pytest.mark.parametrize("s,ep", SCRIPTED, ids=[f"{s.key}-{e.number}" for s, e in SCRIPTED])
def test_every_text_size_reads_at_phone_list_size(s, ep):
    """168x94 on screen: words >= 13 px tall there, every label >= 7 px."""
    doc = _page(ep, badge="5 MIN")
    sizes = [int(n) for n in re.findall(r"font(?:-size)?:\s*(?:\d{3}\s+)?(\d+)px", doc)]
    assert sizes and min(sizes) * tt.LIST_SCALE >= tt.MIN_LIST_PX
    look = tt.look_for(ep)
    l1, l2 = tt.words_of(ep).split("\n")
    assert tt.family_px(look.family, l1, l2) * tt.LIST_SCALE >= 13


async def test_a_thumbnail_over_two_megabytes_is_squeezed_or_refused(monkeypatch, tmp_path):
    from atlas.modules.content import ffmpeg
    big = tmp_path / "thumb.png"
    big.write_bytes(PNG + b"x" * (tt.MAX_BYTES + 1))
    runs = []

    async def run(argv, timeout=0):
        runs.append(argv)
        Path(argv[-1]).write_bytes(PNG + b"x" * 100)
        return b""
    monkeypatch.setattr(ffmpeg, "run", run)
    assert await tt.fit_size(str(big)) is None
    assert big.stat().st_size < tt.MAX_BYTES and "paletteuse" in " ".join(runs[0])

    async def stubborn(argv, timeout=0):
        Path(argv[-1]).write_bytes(PNG + b"x" * (tt.MAX_BYTES + 1))
        return b""
    big.write_bytes(PNG + b"x" * (tt.MAX_BYTES + 1))
    monkeypatch.setattr(ffmpeg, "run", stubborn)
    assert "2 MB" in await tt.fit_size(str(big))
    (tmp_path / "empty.png").write_bytes(b"")
    assert "empty" in await tt.fit_size(str(tmp_path / "empty.png"))


async def test_render_refuses_without_its_real_image(tmp_path):
    class Blank:
        async def shoot(self, doc, path, w=0, h=0):
            Path(path).write_bytes(b"")
            return path
    ep = SERIES[2].episode(4)
    why = await tt.render(ep, Blank(), str(tmp_path / "t.png"), level=2, badge="",
                          work=str(tmp_path))
    assert why and "picture" in why


async def test_render_draws_the_page_over_the_shot_frame(tmp_path):
    shots = []

    class Shooter:
        async def shoot(self, doc, path, w=0, h=0):
            shots.append((doc, w, h))
            Path(path).write_bytes(PNG + b"frame")
            return path
    ep = SERIES[2].episode(4)
    out = tmp_path / "t.png"
    assert await tt.render(ep, Shooter(), str(out), level=2, badge="5 MIN",
                           work=str(tmp_path)) is None
    (_view, w, h), (page, tw, th) = shots
    assert (w, h) == tt.frame_size(tt.look_for(ep).family) and (tw, th) == (tt.TW, tt.TH)
    assert "data:image/png;base64," in page and ">5 min<" in page and out.is_file()


# ------------------------------------------------------------ watermark

def _png_size(path) -> tuple[int, int]:
    head = path.read_bytes()[:24]
    assert head[:8] == b"\x89PNG\r\n\x1a\n"
    return struct.unpack(">II", head[16:24])


def test_the_watermark_is_one_swappable_file_with_its_alpha_baked_in():
    assert design.WATERMARK.is_file() and design.WATERMARK_SVG.is_file()
    assert design.kit_missing() is None
    w, h = _png_size(design.WATERMARK)
    x, y = design.WATERMARK_XY
    # 2026-10-17: in the rail's column, inside safe-16-9, above the series name
    assert (x, y) == (design.SAFE, design.SAFE) and x + w <= design.RAIL_W
    assert y + h <= design.SAFE + 64                # the series name starts below it
    assert 'opacity="0.62"' in design.WATERMARK_SVG.read_text()


def test_every_teaching_segment_carries_the_watermark_in_its_one_encode():
    argv = tut.segment_argv("/w/s.png", "/w/a.mp3", 6.0, "/w/o.mp4", bg="/k/loop-n8n.mp4",
                            prev="/w/p.png", watermark=str(design.WATERMARK))
    graph = argv[argv.index("-filter_complex") + 1]
    i = argv.index(str(design.WATERMARK))
    assert argv[i - 1] == "-i" and "-loop" not in argv[i - 3:i]     # decoded once
    x, y = design.WATERMARK_XY
    # after the slide transition, so it stays put while slides wipe
    assert graph.index("[x][p]overlay") < graph.index(f"[y][wm]overlay={x}:{y}")
    assert argv.count("libx264") == 1


def test_the_sting_and_a_segment_without_it_carry_no_watermark():
    argv = tut.segment_argv("/w/s.png", "/w/a.mp3", 9.0, "/w/o.mp4",
                            bg=str(design.INTRO_LOOP), lead=str(design.STING),
                            lead_png="/w/lead.png", watermark=str(design.WATERMARK))
    graph = argv[argv.index("-filter_complex") + 1]
    # the mark is on the body only; the sting is concatenated before it untouched
    assert graph.index("[wm]overlay") < graph.index("[body]")
    assert "[lv][la][body][a]concat" in graph
    plain = tut.segment_argv("/w/s.png", "/w/a.mp3", 3.0, "/w/o.mp4")
    assert "[wm]" not in plain[plain.index("-filter_complex") + 1]


async def test_segments_ask_for_the_watermark_and_the_bookends_do_not(monkeypatch):
    from atlas.modules.content import ffmpeg
    runs = []

    class Shooter:
        async def shoot(self, doc, path, w=0, h=0):
            return path

    async def run(argv, timeout=0):
        runs.append(argv)
        return ""

    async def narrate(text, base, preview):
        return base + ".wav", "fish", None

    async def duration(path):
        return 2.0
    monkeypatch.setattr(ffmpeg, "run", run)
    monkeypatch.setattr(ffmpeg, "duration", duration)
    monkeypatch.setattr(tut, "_narrate", narrate)
    await tut._segment(Shooter(), "<p>", "words", "/tmp/x", False)
    await tut._segment(Shooter(), "<p>", "words", "/tmp/y", False, watermark=False)
    assert str(design.WATERMARK) in runs[0] and str(design.WATERMARK) not in runs[1]


# ------------------------------------------------------------ cache keys

def test_a_new_mark_or_thumbnail_look_renders_drafts_again_but_not_the_bookends(
        monkeypatch, tmp_path):
    ep = SERIES[2].episodes[1]
    key, intro = tut.script_key(ep), tut.cache_key("intro", "hi", False)
    new = tmp_path / "watermark.png"
    new.write_bytes(b"another mark")
    monkeypatch.setattr(design, "WATERMARK", new)
    design.look_stamp.cache_clear()
    try:
        assert tut.script_key(ep) != key
        assert tut.cache_key("intro", "hi", False) == intro
        monkeypatch.setattr(tt, "THUMB_VERSION", "test-bump")
        design.look_stamp.cache_clear()
        changed = tut.script_key(ep)
        assert changed != key
    finally:
        design.look_stamp.cache_clear()
