"""The Atlas Academy video system's opening and end card (the owner, 2026-10-17;
docs/academy-video-system: ResultTitle, BrandSting, WorkflowTrace, EndCard;
episode-structure.md, narration.md). Batches 3 and 5 of .video-gap.md."""
from __future__ import annotations

from pathlib import Path

import pytest

from atlas.modules.academy import tutorial_design as design
from atlas.modules.academy import tutorial_motion as motion
from atlas.modules.academy import tutorial_series as ts
from atlas.modules.academy import tutorials as tut
from atlas.modules.academy import video_tokens as vt
from tests.chromium_support import chromium_ready

LESSONS = [(s.key, e) for s in ts.SERIES for e in s.episodes
           if e.scripted and e.opener_topic]


# ------------------------------------------------------------ the result

@pytest.mark.parametrize("key,ep", LESSONS, ids=[f"{k}-{e.number}" for k, e in LESSONS])
def test_every_scripted_lesson_names_its_result_in_three_words_or_fewer(key, ep):
    """ResultTitle: the badge is three words or fewer, sentence case; one
    string the opening, the end card and the thumbnail can share."""
    assert ep.result, f"{key} #{ep.number} names no result"
    words = ep.result.split()
    assert 1 <= len(words) <= ts.RESULT_WORDS_MAX, ep.result
    assert ep.result[0].isupper() and ep.result == ep.result.strip()
    assert not ep.result.endswith((".", "!", "?")) and "!" not in ep.result
    assert ts.badge_of(ep) == ep.result == ts.result_of(ep)


def test_no_episode_claims_a_verified_result_yet():
    """`success` only marks what the script states as tested on camera; no
    script states it today, so every badge is the navy plate."""
    assert not [e.number for s in ts.SERIES for e in s.episodes if e.verified]


def test_the_result_falls_back_to_the_payoff_then_the_thumbnail_words():
    ep = ts.series("claude").episode(4)
    bare = ts.replace(ep, result="")
    assert ts.result_of(bare) == ep.payoff.code.split("\n")[0]
    assert ts.result_of(ts.replace(bare, payoff=None)) == "Claude subagents"
    # the badge never borrows a hook or a question: a neutral, true label
    assert ts.badge_of(bare) == ts.RESULT_BADGE
    assert len(ts.RESULT_BADGE.split()) <= ts.RESULT_WORDS_MAX


def test_a_result_reads_inside_a_sentence():
    assert ts.lead_lower("A proofreader subagent") == "a proofreader subagent"
    assert ts.lead_lower("Your MCP server") == "your MCP server"
    assert ts.lead_lower("Hermes, installed") == "Hermes, installed"
    assert ts.lead_lower("MCP basics") == "MCP basics"


# ------------------------------------------------------ the opening's pages

needs_chromium = pytest.mark.skipif(not chromium_ready(), reason="Playwright/Chromium missing")
SUBAGENTS = ts.series("claude").episode(4)

_BOX_JS = """(sel) => { const e = document.querySelector(sel); if (!e) return null;
  const r = e.getBoundingClientRect(); const s = getComputedStyle(e);
  return [r.left, r.top, r.right, r.bottom, s.backgroundColor, s.visibility,
          e.innerText]; }"""


async def _boxes(doc: str, *sels: str) -> list:
    from atlas.modules.academy.tutorials import Shooter
    async with Shooter() as sh:
        pg = await sh._b.new_page(viewport={"width": design.W, "height": design.H})
        try:
            await pg.set_content(doc)
            await pg.evaluate("document.fonts.ready.then(() => null)")
            return [await pg.evaluate(_BOX_JS, s) for s in sels]
        finally:
            await pg.close()


def _inside_safe(box) -> bool:
    x0, y0, x1, y1 = box[:4]
    s = vt.SAFE_16_9
    return x0 >= s and y0 >= s and x1 <= design.W - s and y1 <= design.H - s


@needs_chromium
async def test_frame_zero_shows_the_result_and_the_badge_sits_inside_the_safe_area():
    bubble, badge, h1 = await _boxes(design.result_title(SUBAGENTS), ".result .bubble",
                                     ".badge", "h1")
    assert bubble is not None and bubble[5] == "visible"
    assert SUBAGENTS.payoff.code in bubble[6]           # the result, from the page itself
    assert badge[6].strip() == SUBAGENTS.result and _inside_safe(badge)
    assert _inside_safe(bubble) and h1 is None          # one headline: the badge
    # the badge alone moves: everything else is on frame 0 (Shooter.layers
    # cuts only the groups the spec names out of the base still)
    spec = design.result_motion(SUBAGENTS)
    assert [sel for sel, _ in spec.groups] == [".badge"]
    assert spec.trace == (len(SUBAGENTS.payoff.items) - 2, len(SUBAGENTS.payoff.items) - 1)


@needs_chromium
async def test_the_badge_is_success_only_when_the_result_is_verified():
    plain_badge, = await _boxes(design.result_title(SUBAGENTS), ".badge")
    ok, = await _boxes(design.result_title(ts.replace(SUBAGENTS, verified=True)),
                       ".badge")
    assert plain_badge[4] == "rgb(24, 37, 64)"          # navy-raised #182540
    assert ok[4] == "rgb(60, 217, 192)"                 # success #3cd9c0
    assert "<svg" in design.result_title(ts.replace(SUBAGENTS, verified=True))
    assert "badge ok" not in design.result_title(SUBAGENTS)
    # verified without a named result is still the neutral plate
    bare = ts.replace(SUBAGENTS, verified=True, result="")
    assert "badge ok" not in design.result_title(bare)


@needs_chromium
async def test_the_scope_line_sits_inside_the_safe_area_without_the_badge():
    scope, badge = await _boxes(design.scope_card(SUBAGENTS), ".scope", ".badge")
    assert badge is None and _inside_safe(scope)
    assert scope[6].startswith("Claude Code subagents") and "Claude Code" in scope[6]


def test_the_sting_is_thirty_frames_rise_draw_hold_out():
    assert design.STING_FRAMES == 30 == round(vt.STING_MS * vt.FPS / 1000)
    a0, dy0, l0 = design.sting_state(0)
    assert a0 == 0.0 and dy0 == vt.TITLE_RISE_PX and l0 == 0.0
    a6, dy6, l6 = design.sting_state(6)
    assert a6 == 1.0 and dy6 == 0.0 and l6 == 0.0         # in after 6 frames
    assert design.sting_state(18)[2] == 1.0                # drawn by 12 more
    assert design.sting_state(12)[2] > 0.5                 # `draw`: past half by mid
    assert design.sting_state(22) == (1.0, 0.0, 1.0)       # the hold
    assert design.sting_state(30)[0] == 0.0                # out over the last 6
    assert 0 < design.sting_state(27)[0] < 1


def test_a_sting_frame_is_flat_navy_with_only_the_wordmark():
    doc = design.sting_frame(20)
    assert "Atlas <span>Academy</span>" in doc and 'class="line"' in doc
    for gone in ('class="rail"', 'class="store"', 'class="prog"', 'class="scrim"',
                 "Subscribe", "Made with AI"):
        assert gone not in doc


# --------------------------------------------------------- the opening part


@pytest.fixture
def opening_fakes(monkeypatch, tmp_path):
    """opening_part with the voice, Chromium and ffmpeg faked."""
    from atlas.modules.content import ffmpeg
    monkeypatch.setenv("ACADEMY_TUTORIALS_DIR", str(tmp_path))
    state = {"argv": [], "voice": 2.2, "shot": [], "layers": [], "said": []}

    class Shooter:
        pages = tut.video_qa.Pages()          # batch 9: render records it
        async def shoot(self, doc, path, w=0, h=0):
            state["shot"].append(doc)
            return path

        async def layers(self, doc, base, spec, *, crossfade):
            state["layers"].append((doc, spec, crossfade))
            # the motion schedule's own times, which opening_part replaces
            enters = [motion.Enter(f"{base}.e0.png", 96, 870, 0.067)]
            tr = motion.Trace("dot.png", 300, 830, 520, 0.4) if spec.trace else None
            return base + ".rest.png", enters, tr

    async def narrate(text, base, preview):
        state["said"].append(text)
        return base + ".wav", "fish", None

    async def segment(shooter, doc, said, base, preview, **kw):
        state["scope"] = (doc, said, kw)
        return base + ".mp4", 8.4, "fish", None

    async def run(argv, timeout=0):
        state["argv"].append(list(argv))
        if argv[-1].endswith(".mp4"):                 # what a real ffmpeg writes
            Path(argv[-1]).parent.mkdir(parents=True, exist_ok=True)
            Path(argv[-1]).write_bytes(b"mp4")
        return b""

    async def duration(path):
        return state["voice"]
    monkeypatch.setattr(tut, "_narrate", narrate)
    monkeypatch.setattr(tut, "_segment", segment)
    monkeypatch.setattr(ffmpeg, "run", run)
    monkeypatch.setattr(ffmpeg, "duration", duration)
    state["shooter"] = Shooter()
    return state


async def test_the_opening_is_the_result_for_six_seconds_then_the_sting_then_the_scope(
        opening_fakes, tmp_path):
    ep = SUBAGENTS
    got, why = await tut.opening_part(ep, opening_fakes["shooter"], str(tmp_path))
    assert why is None, why
    result, sting, scope = got.parts
    assert result.endswith("intro-result.mp4") and "sting-" in sting
    assert scope.endswith("scope.mp4")
    assert got.seconds == pytest.approx(7.0 + 8.4)
    a, c = ts.opening_lines(ep)
    assert got.timings == [(0.0, 6.0, a), (7.0, 15.4, c)]
    assert opening_fakes["said"] == [f"{ts.OPENER_CUE} {a}"]
    assert a == "Here is the result: a proofreader subagent."
    # the result's argv: exactly 6 s, on navy, no crossfade from anything
    argv = next(x for x in opening_fakes["argv"] if x[-1] == result)
    assert argv[argv.index("-t", argv.index("-filter_complex")) + 1] == "6.000"
    assert "-loop" not in argv[:argv.index(str(tmp_path / "intro-result.rest.png")) + 2]
    graph = argv[argv.index("-filter_complex") + 1]
    assert "fade=t=out" not in graph                    # frame 0 is the result itself
    assert "fade=t=in:st=0.4:" in graph                 # the badge at frame 12
    assert "-3)/0.5" in graph or "(t-3)/0.5" in graph   # the trace from 3 s, 15 frames
    doc, spec, crossfade = opening_fakes["layers"][0]
    assert doc == design.result_title(ep) and spec == design.result_motion(ep)
    assert crossfade is False
    # the scope line: its own page and words, no watermark
    sdoc, said, kw = opening_fakes["scope"]
    assert sdoc == design.scope_card(ep) and said == c
    assert kw["spec"] == design.SCOPE_MOTION and kw["watermark"] is False


async def test_a_result_line_too_long_for_its_six_seconds_is_refused(opening_fakes,
                                                                    tmp_path):
    opening_fakes["voice"] = 5.6                       # + the 0.6 s breath > 6 s
    got, why = await tut.opening_part(SUBAGENTS, opening_fakes["shooter"], str(tmp_path))
    assert got is None and "before the sting at 6 s" in why


async def test_the_sting_is_drawn_once_thirty_frames_and_encoded_like_a_segment(
        opening_fakes, tmp_path):
    sh = opening_fakes["shooter"]
    path, why = await tut.sting_clip(sh)
    assert why is None and opening_fakes["shot"] == [design.sting_frame(f)
                                                      for f in range(30)]
    argv = opening_fakes["argv"][-1]
    assert argv[argv.index("-t") + 1] == "1.000" and argv[argv.index("-framerate") + 1] == "30"
    seg = tut.segment_argv("s.png", "a.wav", 4.0, "o.mp4")
    assert argv[argv.index("-c:v"):-1] == seg[seg.index("-c:v"):-1]
    assert "anoisesrc" not in " ".join(argv)            # no whoosh, no swell
    # cached: a second call draws nothing
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_bytes(b"x")
    again, _ = await tut.sting_clip(sh)
    assert again == path and len(opening_fakes["shot"]) == 30


# ------------------------------------------------------------ the end card

SCRIPTED_LESSONS = [(k, e) for k, e in LESSONS]


@pytest.mark.parametrize("key,ep", SCRIPTED_LESSONS,
                         ids=[f"{k}-{e.number}" for k, e in SCRIPTED_LESSONS])
def test_the_next_title_comes_from_the_catalog_never_invented(key, ep):
    """The next scripted episode's title (up to its measured minutes, which
    exist only once it renders), else the series playlist's name."""
    s = ts.series(key)
    nxt = ts.next_up(key, ep)
    later = ts._later(s.episodes, ep)
    if later is not None and later.scripted:
        assert nxt.kind == "episode" and nxt.title == ts.card_title(later)
        for minutes in range(1, 16):                    # whatever it measures
            assert ts.title_for(later, minutes).startswith(nxt.title)
        assert nxt.why.rstrip(".").lower() == later.opener_topic.lower()
    else:
        assert nxt.kind == "playlist" and nxt.title == s.playlist == ts.PLAYLISTS[key]
        assert s.playlist_description.startswith(nxt.why)


def test_the_welcome_points_to_the_first_lesson_of_the_catalog():
    nxt = ts.next_up("channel", ts.series("channel").episode(1))
    first = next(e for s in ts.SERIES if s.playlist for e in s.episodes if e.scripted)
    assert nxt == ts.NextUp("episode", ts.card_title(first),
                            first.opener_topic[:1].upper() + first.opener_topic[1:] + ".")


def test_the_end_card_says_the_result_the_next_lesson_and_the_shop_only_with_files():
    ep = ts.series("n8n").episode(1)
    nxt = ts.next_up("n8n", ep)
    doc = design.end_card(ep, nxt, shop=True)
    assert "You now have your first workflow." in doc
    assert "Next lesson" in doc and nxt.title in doc and nxt.why in doc
    assert "Practice files: <b>agency.example.com</b> (link in the description)" in doc
    assert "agency.example.com" not in design.end_card(ep, nxt, shop=False)
    # the render asks for the shop line only when the lesson has lab files
    assert tut.end_card_text(ep).endswith("|shop") and ts.downloads_of(ep)
    bare = ts.series("claude").episode(4)
    assert not ts.downloads_of(bare) and not tut.end_card_text(bare).endswith("shop")
    # the series' last scripted lesson has no scripted next one: the playlist
    last = [e for e in ts.series("claude").episodes if e.scripted][-1]
    assert ts.next_up("claude", last).kind == "playlist"
    playlist = design.end_card(last, ts.next_up("claude", last), shop=False)
    assert "Next in the series" in playlist and ts.PLAYLISTS["claude"].replace(
        "&", "&amp;") in playlist


@pytest.mark.parametrize("key,ep", SCRIPTED_LESSONS[:3] + SCRIPTED_LESSONS[-2:],
                         ids=lambda x: getattr(x, "slug", x))
def test_no_subscribe_or_goodbye_is_drawn_in_the_opening_or_the_end_card(key, ep):
    docs = [design.result_title(ep), design.scope_card(ep),
            *(design.sting_frame(f) for f in (0, 15, 29)),
            design.end_card(ep, ts.next_up(key, ep), shop=True)]
    for doc in docs:
        low = doc.lower()
        for word in ("subscribe", "thanks for watching", "thank you", ">like<",
                     ">comment<", 'class="sub"', "made with ai", "<img"):
            assert word not in low, (word, doc[:80])


@needs_chromium
async def test_the_end_card_keeps_the_reserve_empty_and_its_words_inside_the_safe_area():
    ep = ts.series("n8n").episode(1)
    doc = design.end_card(ep, ts.next_up("n8n", ep), shop=True)
    card, done, nxt, shop = await _boxes(doc, ".endcard", ".done", ".next", ".shopline")
    for box in (card, done, nxt, shop):
        assert _inside_safe(box)
        assert box[2] <= design.RESERVE_X - vt.STEP_8 + 0.5      # left of the reserve
    assert design.RESERVE_X == design.W - vt.SAFE_16_9 - vt.ENDSCREEN_RESERVE == 1184


def test_an_end_card_too_short_or_not_at_the_end_is_refused():
    m = tut.Manifest("n8n", 1, "fish", 300.0, [(0.0, "A")],
                     parts=["intro-a.mp4", "outro-card.mp4"], opening="result",
                     sting_at=6.0, sting_seconds=1.0, card_at=285.0, card_seconds=15.0)
    assert tut.ending_refusal(m) is None
    m.card_at, m.card_seconds = 290.0, 10.0
    assert "under 15 s" in tut.ending_refusal(m)
    m.card_at, m.card_seconds = 280.0, 15.0
    assert "not at the video's end" in tut.ending_refusal(m)
    m.card_at, m.presenter = 285.0, ["outro:abc"]
    assert "the owner's clip" in tut.ending_refusal(m)
    m.presenter, m.seconds, m.card_at = [], 20.0, 5.0
    assert "end screen needs 25 s" in tut.ending_refusal(m)


async def test_the_publish_gate_refuses_an_end_card_naming_a_stale_next_title(
        tmp_path, monkeypatch):
    from atlas.modules.academy import tutorial_music as tm
    nxt = ts.next_up("n8n", ts.series("n8n").episode(0))
    monkeypatch.setattr(tut, "episode_dir", lambda *_: tmp_path)
    monkeypatch.setattr(tut, "publishable_render", lambda _p: None)
    m = tut.Manifest("n8n", 0, "fish", 290.0, [(0.0, "A"), (60.0, "B"), (120.0, "C")],
                     parts=["intro-a", "outro-b"], opening="result", sting_at=6.0,
                     sting_seconds=1.0, card_at=275.0, card_seconds=15.0,
                     next_title="An Old Title", music=tm.MUSIC_VERSION, body_seconds=200.0)
    monkeypatch.setattr(tut, "read_manifest", lambda _d: m)

    async def probe(_path):
        return {"width": design.W, "height": design.H, "audio": True, "seconds": 290.0}, None
    monkeypatch.setattr(tut, "_probe", probe)
    got, why = await tut.ready("n8n", 0)
    assert got is None and "An Old Title" in why and nxt.title in why


def _have_ffmpeg() -> bool:
    import shutil
    return shutil.which("ffmpeg") is not None


@needs_chromium
@pytest.mark.skipif(not _have_ffmpeg(), reason="ffmpeg missing")
async def test_the_rendered_end_card_s_last_frame_leaves_the_reserve_flat_navy(tmp_path):
    """A real render of the EndCard part (Chromium and ffmpeg): it lasts 15 s
    and its last frame, inside the 640 px reserve and the safe area, is flat
    navy -- nothing painted for the native end-screen element."""
    Image = pytest.importorskip("PIL.Image")

    from atlas.modules.content import ffmpeg
    ep = ts.series("n8n").episode(1)
    doc = design.end_card(ep, ts.next_up("n8n", ep), shop=True)
    async with tut.Shooter() as sh:
        out, secs, _note, why = await tut._segment(
            sh, doc, "", str(tmp_path / "outro-card"), False, spec=design.END_CARD_MOTION,
            watermark=False, silent=design.END_CARD_MIN, hold_to=design.END_CARD_MIN)
    assert why is None and secs == pytest.approx(15.0, abs=0.05)
    last = str(tmp_path / "last.png")
    await ffmpeg.run(["ffmpeg", "-y", "-loglevel", "error", "-sseof", "-0.2", "-i", out,
                      "-update", "1", "-frames:v", "1", last])
    img = Image.open(last).convert("RGB")
    assert img.size == (design.W, design.H)
    navy = tuple(int(vt.NAVY[i:i + 2], 16) for i in (1, 3, 5))
    s = vt.SAFE_16_9
    off = [(x, y) for x in range(design.RESERVE_X, design.W - s, 8)
           for y in range(s, design.H - s, 8)
           if max(abs(a - b) for a, b in zip(img.getpixel((x, y)), navy, strict=True)) > 3]
    assert not off, off[:5]
    # and the card itself is there on the last frame (it never fades away)
    words = [img.getpixel((x, y)) for x in range(s, design.RESERVE_X, 4)
             for y in range(s, design.H - s, 4)]
    assert any(min(p) > 200 for p in words)             # paper-white text


async def test_no_presenter_clip_reaches_any_render_argv(monkeypatch, tmp_path):
    """the owner, 2026-10-17: his intro and outro clips stay on disk, unused. With
    the committed clips visible, a whole render -- the real _segment,
    opening_part and end card, only Chromium, the voice and ffmpeg faked --
    passes neither file to any ffmpeg call."""
    from atlas.modules.academy import presenter, reel_design
    from atlas.modules.academy import tutorial_music as tm
    from atlas.modules.content import ffmpeg
    monkeypatch.delenv("ACADEMY_PRESENTER_DIR", raising=False)
    intro = presenter.clip("intro")
    assert intro is not None
    assert presenter.clip("outro") is None   # retired: it named a dead address
    monkeypatch.setenv("ACADEMY_TUTORIALS_DIR", str(tmp_path))
    argvs: list[list[str]] = []

    class Shooter:
        pages = tut.video_qa.Pages()          # batch 9: render records it
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return None

        async def shoot(self, doc, path, w=0, h=0):
            return path

        async def layers(self, doc, base, spec, *, crossfade):
            return base + ".png", [], None

    async def narrate(text, base, preview):
        return base + ".wav", "fish", None

    async def run(argv, timeout=0):
        argvs.append(list(argv))
        if argv and argv[-1].endswith((".mp4", ".jpg")):
            Path(argv[-1]).parent.mkdir(parents=True, exist_ok=True)
            Path(argv[-1]).write_bytes(b"x")
        return b""

    async def duration(path):
        return 2.0 if path.endswith(".wav") else 290.0

    async def add_bed(video, key, out, **kw):
        Path(out).write_bytes(b"x")
        return 290.0
    async def thumbnail(ep, shooter, out_png, **kw):    # batch 7: the real-image thumbnail
        Path(out_png).write_bytes(b"\x89PNG\r\n\x1a\nfake")
    from atlas.modules.academy import tutorial_thumbs
    monkeypatch.setattr(tutorial_thumbs, "render", thumbnail)
    monkeypatch.setattr(tut, "Shooter", Shooter)
    monkeypatch.setattr(tut, "_narrate", narrate)
    monkeypatch.setattr(tm, "add_bed", add_bed)
    monkeypatch.setattr(ffmpeg, "run", run)
    monkeypatch.setattr(ffmpeg, "duration", duration)
    monkeypatch.setattr(ffmpeg, "missing", lambda: None)
    monkeypatch.setattr(reel_design, "available", lambda: True)
    _d, m, why = await tut.render("claude", 4)
    assert why is None, why
    flat = " ".join(" ".join(a) for a in argvs)
    assert str(intro.path) not in flat
    assert "assets/presenter" not in flat and m.presenter == []
    assert str(design.STING) not in flat                   # no 3D sting either
    assert m.parts[0] == "intro-result.mp4" and m.parts[-1] == "outro-card.mp4"
    assert m.parts[1].startswith("sting-") and (m.sting_at, m.sting_seconds) == (6.0, 1.0)


@needs_chromium
async def test_the_sting_wordmark_reads_across_with_the_line_drawn_under_it():
    """Found in the 2026-10-17 preview: the rail's vertical `.mark` rule
    turned the wordmark on its side. The lockup is horizontal, centred, the
    line as wide as the word once drawn, all inside the safe area."""
    word, line, lockup = await _boxes(design.sting_frame(20), ".sting .word",
                                      ".sting .line", ".sting .lockup")
    assert word[2] - word[0] > 3 * (word[3] - word[1])          # across, not down
    assert abs(line[2] - word[2]) < 2 and abs(line[0] - word[0]) < 2
    assert line[1] > word[3] and _inside_safe(lockup)
    assert abs((lockup[0] + lockup[2]) / 2 - design.W / 2) < 2


@needs_chromium
async def test_the_result_s_trace_track_runs_node_centre_to_node_centre():
    """The trace dot runs along the flow's track: the track spans the first
    node's centre to the last one's, not the full frame width."""
    doc = design.result_title(SUBAGENTS)
    flow, first, last = await _boxes(
        doc, ".result .flow", ".result .node:first-child",
        ".result .node:last-child")
    assert flow[0] == pytest.approx(first[0], abs=1)            # the flow hugs its nodes
    assert flow[2] == pytest.approx(last[2], abs=1)


def test_the_end_card_shows_a_success_check_only_for_a_verified_lesson():
    """README: a success chip means the test really passed. An unverified
    lesson's "You now have ..." row carries a neutral arrow, not the check."""
    from dataclasses import replace

    from atlas.modules.academy import tutorial_design as td
    from atlas.modules.academy.tutorial_series import SERIES

    ep = next(e for s in SERIES if s.key == "n8n" for e in s.episodes if e.result)
    plain = td.end_card(replace(ep, verified=False), None, shop=False)
    assert td.GLYPH_NEXT in plain and td.GLYPH_CHECK not in plain
    assert 'class="done ok"' not in plain
    ok = td.end_card(replace(ep, verified=True), None, shop=False)
    assert td.GLYPH_CHECK in ok and 'class="done ok"' in ok
