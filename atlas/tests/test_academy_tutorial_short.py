"""Teaching Shorts cut from a published tutorial (academy/tutorial_short.py,
growth plan §4.3): the payoff, the "Stuck?" beat and the quiz, 30-60 s each,
ending on the answer; promo Shorts at most one in five (the owner, 2026-10-13)."""
from __future__ import annotations

import re
from dataclasses import replace
from itertools import pairwise

from atlas.modules.academy import tutorial_series as ts
from atlas.modules.academy import tutorial_short as tsh
from atlas.modules.academy import tutorials as tut


def _manifest(**kw: object) -> tut.Manifest:
    base: dict[str, object] = {
        "series": "claude", "episode": 1, "audio": "fish", "seconds": 340.0,
        "chapters": [(0.0, "Intro"), (12.0, ts.PAYOFF_CHAPTER),
                     (60.0, "What is MCP?"), (200.0, "Not working?"),
                     (290.0, "Quick quiz"), (320.0, "Recap")]}
    base.update(kw)
    return tut.Manifest(**base)


def _ep() -> ts.Episode:
    """claude #4: a payoff, a "Not working?" beat and quizzes, each with a hook."""
    return ts.series("claude").episode(4)


# claude #4 as rendered (a local preview, 2026-10-18): every line's (start,
# end), the opener's two first and the narrated ending last, and its chapters.
# Its quizzes are beats inside chapters; its "Not working?" chapter runs on
# into the last quiz.
C4_TIMES = [
    (1.0, 6.87), (6.87, 14.37), (14.37, 23.47), (23.47, 31.13), (31.13, 37.13),
    (37.13, 44.37), (44.37, 53.03), (53.03, 60.63), (60.63, 69.37), (69.37, 75.3),
    (75.3, 79.37), (79.37, 87.7), (87.7, 94.97), (94.97, 97.9), (97.9, 105.9),
    (105.9, 112.9), (115.9, 121.4), (121.4, 126.0), (126.0, 133.77), (133.77, 147.24),
    (147.24, 153.37), (153.37, 158.5), (158.5, 163.54), (167.54, 177.44),
    (177.44, 186.84), (186.84, 197.44), (197.44, 204.37), (204.37, 209.34),
    (209.34, 219.44), (219.44, 226.64), (229.64, 236.07), (236.07, 241.34),
    (241.34, 244.77), (244.77, 250.04), (250.04, 263.64), (263.64, 280.07)]
C4_CHAPTERS = [(0.0, "Intro"), (14.37, ts.PAYOFF_CHAPTER), (37.13, "What a subagent is"),
               (60.63, "Where they live"), (79.37, "The file, line by line"),
               (126.0, "Using it"), (167.54, "Side by side"), (197.44, "Not working?"),
               (236.07, "Recap")]


def _c4(**kw: object) -> tuple[tut.Manifest, ts.Episode]:
    """claude #4's manifest: the opener, every scene line said in order,
    then the ending, on the measured times."""
    ep = ts.series("claude").episode(4)
    said = (["Welcome back.", "Today we'll be learning about subagents."]
            + [x for sc in tsh.lesson_scenes(ep) for x in sc.said] + ["Thank you."])
    assert len(said) == len(C4_TIMES)
    base: dict[str, object] = {
        "series": "claude", "episode": 4, "audio": "fish", "seconds": 285.11,
        "chapters": C4_CHAPTERS, "intro_seconds": 14.37,
        "timings": [(a, b, t) for (a, b), t in zip(C4_TIMES, said, strict=True)]}
    base.update(kw)
    return tut.Manifest(**base), ep


def test_a_short_is_20_to_40_seconds_with_its_closing_card():
    """the owner, 2026-10-17: Shorts are 20-40 s; the card naming the longer
    lesson is inside the 40."""
    assert (ts.SHORT_MIN, ts.SHORT_MAX) == (20.0, 40.0)
    assert tsh.CUT_MIN == 20.0 and tsh.CUT_MAX + tsh.CLOSE_SECONDS == 40.0


def test_three_cuts_from_a_real_render_each_ending_on_the_answer():
    m, ep = _c4()
    cuts = tsh.cuts(m, ep)
    assert [c.kind for c in cuts] == ["payoff", "stuck", "quiz"]
    starts = {round(a, 2) for a, _, _ in m.timings}
    ends = {round(b, 2) for _, b, _ in m.timings}
    for c in cuts:
        assert tsh.CUT_MIN <= c.end - c.start <= tsh.CUT_MAX, c
        assert c.start in starts and c.end in ends, c       # never mid-line
    payoff, stuck, quiz = cuts
    # the result first (past both opener lines), then the first steps
    assert (payoff.start, payoff.end) == (14.37, 44.37)
    # the "Not working?" chapter runs on into the last quiz: the stuck cut
    # ends on its own last fix, never on the quiz
    assert (stuck.start, stuck.end) == (197.44, 219.44) and stuck.caption == "Not working?"
    # the quiz is a beat, found by its words: question, 3-2-1, reveal last,
    # reaching back one whole line to make 20 s
    assert (quiz.start, quiz.end) == (97.9, 121.4)
    assert quiz.caption == "The file, line by line"


def test_every_line_is_matched_to_the_scene_step_that_says_it():
    m, ep = _c4()
    lines = tsh.lines_of(m, ep)
    assert [ln.scene for ln in lines[:2]] == [None, None]   # the opener
    assert lines[2].scene is ep.payoff and lines[2].step == 0
    assert lines[-1].scene is None                         # the ending
    quiz = next(sc for sc in ep.scenes if sc.kind == "quiz")
    assert [(ln.start, ln.step) for ln in lines if ln.scene is quiz] == [
        (105.9, 0), (115.9, 1)]


def test_a_stuck_beat_too_long_drops_lines_from_its_end_keeping_the_mistake():
    m, ep = _c4()
    # the stuck beat's three lines made 50 s: its last fix is dropped at a
    # line's end, the mistake (its first line) still opens the cut
    times = list(C4_TIMES)
    times[26:29] = [(197.44, 207.44), (207.44, 222.44), (222.44, 247.44)]
    times[29:] = [(x + 28.0, y + 28.0) for x, y in C4_TIMES[29:]]
    m.timings = [(x, y, t) for (x, y), (_, _, t) in zip(times, m.timings, strict=True)]
    stuck = next(c for c in tsh.cuts(m, ep) if c.kind == "stuck")
    assert (stuck.start, stuck.end) == (197.44, 222.44)


def test_a_quiz_too_long_drops_lines_from_its_start_never_the_question():
    m, ep = _c4()
    # a long code line before the quiz: the reach-back cannot take it whole
    m.timings = [(a, b, t) if i != 14 else (80.0, b, t)
                 for i, (a, b, t) in enumerate(m.timings)]
    quiz = next(c for c in tsh.cuts(m, ep) if c.kind == "quiz")
    assert quiz.end == 121.4 and quiz.start <= 105.9        # the question stays
    assert quiz.start in {a for a, _, _ in m.timings}
    assert quiz.end - quiz.start <= tsh.CUT_MAX


def test_a_stuck_beat_that_cannot_reach_20_seconds_on_a_line_gives_no_cut():
    m, ep = _c4()
    # a 9 s stuck beat after a minute of silence: the only line to reach
    # back to makes the cut longer than CUT_MAX
    times = list(C4_TIMES)
    times[26:29] = [(197.44, 200.0), (200.0, 203.0), (203.0, 206.0)]
    m.timings = [(a, b, t) for (a, b), (_, _, t) in zip(times, m.timings, strict=True)
                 if not 120.0 < a < 197.0]
    m.chapters = [(s, n) for s, n in m.chapters if not 120.0 < s < 197.0]
    assert "stuck" not in [c.kind for c in tsh.cuts(m, ep)]


def test_a_lab_ends_on_its_stuck_step():
    ep = ts.series("n8n").episode(1)
    lab = tsh.stuck_scene(ep)
    assert lab is not None and lab.kind == "lab"
    said, t, timings = [x for sc in tsh.lesson_scenes(ep) for x in sc.said], 10.0, []
    for x in said:
        timings.append((t, t + 6.0, x))
        t += 6.0
    m = _manifest(series="n8n", seconds=t + 20.0, timings=timings,
                  chapters=[(0.0, "Intro"), (10.0, ts.PAYOFF_CHAPTER)])
    stuck = next(c for c in tsh.cuts(m, ep) if c.kind == "stuck")
    k = ts.LAB_LABELS.index("Stuck?")
    line = next(ln for ln in tsh.lines_of(m, ep) if ln.scene is lab and ln.step == k)
    assert stuck.end == line.end and 20.0 <= stuck.end - stuck.start <= tsh.CUT_MAX


def test_a_render_whose_lines_name_no_scene_falls_back_to_the_chapters():
    m = _manifest(timings=[(12.0, 20.0, "a"), (20.0, 40.0, "b"), (40.0, 60.0, "c"),
                           (200.0, 250.0, "d"), (250.0, 270.0, "e"), (270.0, 290.0, "f"),
                           (290.0, 300.0, "g"), (305.0, 320.0, "h")])
    assert [(c.kind, c.start, c.end) for c in tsh.cuts(m, _ep())] == [
        ("payoff", 12.0, 40.0),       # the longest run of whole lines
        ("stuck", 270.0, 290.0),      # trimmed from its start: the fix ends it
        ("quiz", 290.0, 320.0)]


def test_a_cut_with_no_line_end_in_reach_is_never_made_mid_sentence():
    m = _manifest(timings=[(12.0, 100.0, "One very long unbroken line.")])
    assert "payoff" not in [c.kind for c in tsh.cuts(m, _ep())]


def test_the_payoff_cut_skips_the_opener_folded_into_the_payoff_chapter():
    opener = "Under ten minutes, you'll connect Claude to your files."
    m = _manifest(seconds=300.0,
                  chapters=[(0.0, ts.PAYOFF_CHAPTER), (40.0, "What is MCP?"),
                            (200.0, "Stuck? Read this"), (250.0, "Quick quiz"),
                            (285.0, "Recap")],
                  timings=[(0.0, 6.0, opener), (6.5, 14.0, "Here's the result."),
                           (14.5, 30.0, "First, open settings."),
                           (30.5, 50.0, "Then add the server."),
                           (50.5, 70.0, "Now ask it something.")])
    payoff = tsh.cuts(m, _ep())[0]
    assert payoff.kind == "payoff"
    assert payoff.start == 6.5           # never the opener, never before it
    assert payoff.end == 30.0            # ends on a line, not mid-sentence


def test_the_payoff_cut_skips_both_opening_lines_the_intro_covers():
    """Final review I5: since the presenter merge a lesson opens on TWO
    timings -- the owner's intro take, then the narrated opener -- before the
    payoff. The cut starts at the first line at or after intro_seconds."""
    m = _manifest(seconds=300.0, intro_seconds=9.12,
                  chapters=[(0.0, ts.PAYOFF_CHAPTER), (40.0, "What is MCP?"),
                            (200.0, "Stuck? Read this"), (250.0, "Quick quiz"),
                            (285.0, "Recap")],
                  timings=[(0.8, 3.5, "Hi, I'm the owner."),
                           (3.5, 9.116, "Welcome back. Under ten minutes..."),
                           (9.116, 14.0, "Here's the result."),
                           (14.5, 30.0, "First, open settings."),
                           (30.5, 50.0, "Then add the server."),
                           (50.5, 70.0, "Now ask it something.")])
    payoff = tsh.cuts(m, _ep())[0]
    assert payoff.kind == "payoff"
    assert payoff.start == 9.12          # the result, never either opener line
    assert payoff.end == 30.0


def test_the_payoff_falls_back_to_the_chapter_start_when_the_intro_covers_every_line():
    m = _manifest(seconds=300.0, intro_seconds=26.0,
                  chapters=[(0.0, ts.PAYOFF_CHAPTER), (40.0, "What is MCP?"),
                            (285.0, "Recap")],
                  timings=[(0.8, 3.5, "Hi, I'm the owner."), (3.5, 9.0, "The opener."),
                           (25.0, 30.0, "Later line.")])
    payoff = tsh.cuts(m, _ep())[0]
    assert (payoff.start, payoff.end) == (0.0, 30.0)


def test_a_missing_chapter_gives_no_cut_rather_than_a_guess():
    m = _manifest(chapters=[(0.0, "Intro"), (12.0, ts.PAYOFF_CHAPTER),
                            (60.0, "What is MCP?"), (320.0, "Recap")],
                  timings=[(12.0, 20.0, "a"), (20.0, 40.0, "b")])
    assert [c.kind for c in tsh.cuts(m, _ep())] == ["payoff"]


def test_the_short_caption_links_the_full_video_and_leads_with_the_product_hashtag():
    s, ep = ts.series("claude"), _ep()
    for kind in ("payoff", "stuck", "quiz"):
        text = tsh.caption(s, ep, tsh.Cut(kind, 290.0, 320.0, "Quick quiz"),
                           "iRyhxkLk-Yo")
        assert "\nhttps://youtu.be/iRyhxkLk-Yo\n" in text
        assert f"Full lesson: {ep.title}" in text          # no minutes: the title as written
        short = ts.hashtags_for("claude", ep, short=True)
        assert text.rstrip().endswith(" ".join(short))
        # 2026-10-18: the series' three, then #shorts fourth, never first
        assert len(short) == 4 and short[3] == "#shorts" and short[:3] == \
            ts.SERIES_HASHTAGS["claude"][:3]
        assert "Instagram" not in text and "$" not in text
        assert text.startswith(ep.title_hook)
        first_tag = text.rstrip().rsplit("\n", 1)[-1].split()[0]
        assert first_tag == ts.SERIES_HASHTAGS[s.key][0]


def test_the_caption_hook_drops_trailing_punctuation_before_the_colon():
    s, ep = ts.series("claude"), _ep()
    for end in ("?", "!", ".", "?!"):
        e = replace(ep, title_hook="Does it work" + end)
        for kind in ("payoff", "stuck", "quiz"):
            first = tsh.caption(s, e, tsh.Cut(kind, 0.0, 30.0, "x"), "v").splitlines()[0]
            assert first.startswith("Does it work: "), first


def test_promo_shorts_are_at_most_one_in_five():
    plan = [tsh.slot_kind(i) for i in range(10)]
    assert plan.count("promo") <= 2 and plan[0] != "promo"
    for start in range(20):
        window = [tsh.slot_kind(i) for i in range(start, start + 10)]
        assert window.count("promo") <= 10 * tsh.PROMO_SHARE_MAX
    assert tsh.ROTATION.count("promo") / len(tsh.ROTATION) <= tsh.PROMO_SHARE_MAX


# ------------------------------------------- word-highlight captions (2026-10-16)
# YouTube Create's animated captions, kept automatic (the owner, 2026-10-16): the
# phrase-level cues stay; inside each, only the word being said is royal
# blue on the white type -- one Dialogue a word, the whole phrase shown,
# that word coloured. The manifest has line timings only, so the words
# share their line's time by letter count (estimated, never measured).

def _events(ass: str) -> list[tuple[float, float, str]]:
    out = []
    for line in ass.splitlines():
        if not line.startswith("Dialogue:"):
            continue
        parts = line.split(",", 9)

        def sec(stamp: str) -> float:
            h, m, s = stamp.split(":")
            return round(int(h) * 3600 + int(m) * 60 + float(s), 2)
        out.append((sec(parts[1]), sec(parts[2]), parts[9]))
    return out


def _lit(text: str) -> list[str]:
    """The words drawn in the highlight colour."""
    import re

    from atlas.modules.academy import video_tokens as vt
    blue = tsh._ass_inline(vt.BLUE_TEXT)
    return re.findall(r"\{\\1c" + re.escape(blue) + r"\}([^{]*)\{", text)


def _plain(text: str) -> str:
    import re
    return re.sub(r"\{[^}]*\}", "", text)


def test_only_the_word_being_said_is_blue_text_on_the_paper_type():
    """2026-10-17: the single-word highlight stays, in blue-text."""
    from atlas.modules.academy import video_tokens as vt
    m = _manifest(timings=[(12.0, 15.0, "Claude reads [excited] your files"),
                           (15.0, 20.0, "then it answers in plain words today")])
    cut = tsh.Cut("payoff", 12.0, 50.0, ts.PAYOFF_CHAPTER)
    ass = tsh.caption_ass(m, cut)
    style = next(ln for ln in ass.splitlines() if ln.startswith("Style: Lesson,"))
    assert style.split(",")[3] == tsh._ass_colour(vt.PAPER)        # paper type
    events = _events(ass)
    cues = tsh.caption_cues(m, cut)
    assert [w for _, _, t in events for w in _lit(t)] == [
        "Claude", "reads", "your", "files", "then", "it", "answers", "in", "plain",
        "words", "today"]
    for a, b, phrase in cues:
        mine = [e for e in events if a <= e[0] < b]
        # one word lit at a time, back to back across the whole phrase,
        # and the whole phrase on screen every time
        assert mine[0][0] == a and mine[-1][1] == b
        assert all(x[1] == y[0] for x, y in pairwise(mine))
        for _, _, text in mine:
            assert len(_lit(text)) == 1 and _plain(text) == phrase
            assert "[" not in text and "]" not in text


def test_the_words_share_their_line_by_character_weight_never_a_made_up_beat():
    m = _manifest(timings=[(12.0, 14.0, "a bbbbbbbbb")])
    cut = tsh.Cut("payoff", 12.0, 50.0, ts.PAYOFF_CHAPTER)
    events = _events(tsh.caption_ass(m, cut))
    assert [(a, b, _lit(t)) for a, b, t in events] == [
        (0.0, 0.2, ["a"]), (0.2, 2.0, ["bbbbbbbbb"])]       # 1 of 10 letters


def test_a_cue_cut_off_by_the_cut_start_keeps_the_words_on_their_own_time():
    """A reach-back cut can start mid-line: the words already said are not
    lit again; the rest keep the times of the whole line."""
    m = _manifest(timings=[(10.0, 14.0, "aaaa bbbb cccc dddd")])
    cut = tsh.Cut("payoff", 12.0, 50.0, ts.PAYOFF_CHAPTER)
    events = _events(tsh.caption_ass(m, cut))
    assert [(a, b, _lit(t)) for a, b, t in events] == [
        (0.0, 1.0, ["cccc"]), (1.0, 2.0, ["dddd"])]
    assert all(_plain(t) == "aaaa bbbb cccc dddd" for *_, t in events)


def test_braces_in_a_word_never_open_an_ass_override():
    m = _manifest(timings=[(12.0, 14.0, "use {name} here")])
    cut = tsh.Cut("payoff", 12.0, 50.0, ts.PAYOFF_CHAPTER)
    events = _events(tsh.caption_ass(m, cut))
    assert [w for *_, t in events for w in _lit(t)] == ["use", "(name)", "here"]
    assert all(_plain(t) == "use (name) here" for *_, t in events)


def test_the_highlight_reads_on_the_caption_box_even_over_the_brightest_blur():
    """60 px is large text (3:1 from 24px, tokens.json): paper and the
    blue-text highlight on the 80% navy plate, over the brightest blur."""
    from atlas.modules.academy import tutorial_design as design
    from atlas.modules.academy import video_tokens as vt

    def rgb(h: str) -> tuple[float, float, float]:
        return tuple(int(h[i:i + 2], 16) / 255 for i in (1, 3, 5))  # type: ignore[return-value]
    box = ((vt.NAVY, (255 - tsh.PLATE_ALPHA) / 255),)
    assert abs((255 - tsh.PLATE_ALPHA) / 255 - vt.CAPTION_PLATE_ALPHA) < 0.01
    for ink in (vt.PAPER, vt.BLUE_TEXT):
        worst = design.contrast(rgb(ink), design.over(box, design.KIT_WORST))
        dark = design.contrast(rgb(ink), design.over(box, vt.NAVY))
        assert worst >= design.need(vt.SHORTS_CAPTION.size) and dark >= 4.5, ink


def test_the_short_captions_are_plex_inside_the_vertical_safe_area():
    """2026-10-17: shorts-caption 60px IBM Plex Sans (the libass TTF, sized
    by its 1.3 em cell), two lines at most, bottom-anchored: the plate and
    its padding inside x 72 and above 420 from the bottom, in the frame's
    caption zone under the panel (short_frame.CAPTION_BOX)."""
    from atlas.modules.academy import short_frame as sf
    from atlas.modules.academy import video_tokens as vt
    m = _manifest(timings=[(12.0, 15.0, "Claude reads your files")])
    ass = tsh.caption_ass(m, tsh.Cut("payoff", 12.0, 50.0, ts.PAYOFF_CHAPTER))
    style = next(ln for ln in ass.splitlines() if ln.startswith("Style: Lesson,"))
    f = style.removeprefix("Style: ").split(",")
    assert f[1] == vt.ASS_FONT and int(f[2]) == round(vt.SHORTS_CAPTION.size * vt.ASS_CELL)
    assert f[7] == "0"                                  # weight from the face (500)
    assert any(vt.LIBASS_FONTS_DIR.glob("*.ttf"))
    pad, line = int(f[16]), int(f[2])
    align, left, right, margin_v = int(f[18]), int(f[19]), int(f[20]), int(f[21])
    assert align == 2                                   # bottom centre
    assert left - pad >= vt.SAFE_9_16_X and right - pad >= vt.SAFE_9_16_X
    bottom = 1920 - margin_v + pad                      # the plate's lower edge
    assert bottom <= 1920 - vt.SAFE_9_16_BOTTOM
    top = bottom - tsh.CAPTION_LINES * line - 2 * pad
    assert top >= sf.CAPTION_BOX[1] >= sf.PANEL_BOX[1] + sf.PANEL_BOX[3]
    assert sf.CAPTION_BOX[3] == tsh.CAPTION_LINES * line + 2 * pad


def test_no_caption_phrase_runs_past_two_lines():
    """Five words of long technical names can outrun two lines at 60 px:
    such a phrase is split in two, each half on its share of the time."""
    said = ("configuration.json authentication troubleshooting "
            "environment-variables documentation")
    m = _manifest(timings=[(12.0, 18.0, said)])
    cues = tsh.caption_cues(m, tsh.Cut("payoff", 12.0, 50.0, ts.PAYOFF_CHAPTER))
    assert len(cues) >= 2 and all(len(t) <= tsh.CAPTION_CHARS for _, _, t in cues)
    assert cues[0][0] == 0.0 and cues[-1][1] == 6.0
    assert all(x[1] == y[0] for x, y in pairwise(cues))


# ------------------------------------- the Short as drawn (ShortsFrame, 2026-10-17)

def _frames(n: int = 2, demo: int = -1) -> list[tut.ShortFrame]:
    step = 30.0 / n
    return [tut.ShortFrame(f"/w/shot{i}.png", i * step, (i + 1) * step, i == demo)
            for i in range(n)]


def _graph(argv: list[str]) -> str:
    return argv[argv.index("-filter_complex") + 1]


def test_the_shots_tile_the_cut_holding_the_silent_countdown_on_the_question():
    m, ep = _c4()
    quiz = next(c for c in tsh.cuts(m, ep) if c.kind == "quiz")
    shots = tsh.shots(m, ep, quiz)
    assert shots[0].start == 0.0 and shots[-1].end == round(quiz.end - quiz.start, 2)
    assert all(x.end == y.start for x, y in pairwise(shots))
    q = next(sc for sc in ep.scenes if sc.kind == "quiz")
    # the question runs on through the 3-2-1 (112.9-115.9) to the reveal
    question = next(sh for sh in shots if sh.scene is q and sh.step == 0)
    reveal = next(sh for sh in shots if sh.scene is q and sh.step == 1)
    assert question.end == reveal.start == round(115.9 - quiz.start, 2)
    assert shots[-1] == reveal


def test_no_shot_is_empty_when_a_line_starts_on_the_cuts_rounded_end():
    """The cut's end is rounded to 0.01 s; the next line starting a hair
    before it is the next cut's, never a zero-length shot (ffmpeg reads
    `-t 0` as no limit and loops the page for ever)."""
    m, ep = _c4()
    m.timings = [(a - 0.003 if abs(a - 44.37) < 0.01 else a, b, t)
                 for a, b, t in m.timings]
    for cut in tsh.cuts(m, ep):
        assert all(sh.end > sh.start for sh in tsh.shots(m, ep, cut)), cut
    argv = tut.short_argv("in.mp4", 0.0, 30.0, "out.mp4", close="/w/close.png",
                          frames=[*_frames(2), tut.ShortFrame("/w/z.png", 30.0, 30.0)])
    held = [float(argv[i + 1]) for i, a in enumerate(argv) if a == "-t"]
    assert all(t > 0 for t in held) and "/w/z.png" not in argv


def test_the_hook_is_on_the_first_frame_and_nothing_fades_in():
    """the owner, 2026-10-17: the hook from frame 0, no fade-in; a fade-out at
    the end is fine."""
    argv = tut.short_argv("in.mp4", 12.0, 42.0, "out.mp4", frames=_frames(),
                          close="/w/close.png", ass="/w/c.ass")
    graph = _graph(argv)
    assert "fade=t=in" not in graph and "afade=t=in" not in graph
    assert "fade=t=out:st=32.850:d=0.15[v]" in graph            # 30 + 3, at the end
    assert "afade=t=out:st=32.850:d=0.15[a]" in graph
    first_png = argv[argv.index("-loop") + 7]
    assert first_png == "/w/shot0.png"                          # the hook's page first


def test_a_short_runs_its_cut_then_the_closing_card_inside_40_seconds():
    argv = tut.short_argv("in.mp4", 12.0, 42.0, "out.mp4", frames=_frames(3),
                          close="/w/close.png")
    held = [float(argv[i + 1]) for i, a in enumerate(argv) if a == "-t"]
    assert round(sum(held), 3) == 30.0 + tsh.CLOSE_SECONDS <= ts.SHORT_MAX
    assert argv[argv.index("/w/close.png") - 2] == f"{tsh.CLOSE_SECONDS:.3f}"
    assert "concat=n=4:v=1:a=0" in _graph(argv)


def test_the_captions_burn_in_the_vendored_plex_over_every_page():
    from atlas.modules.academy import video_tokens as vt
    argv = tut.short_argv("in.mp4", 12.0, 42.0, "out.mp4", frames=_frames(),
                          close="/w/close.png", ass="/w/c.ass")
    assert f"[cat]ass='/w/c.ass':fontsdir='{vt.LIBASS_FONTS_DIR}'," in _graph(argv)


def test_a_short_adds_no_music_of_its_own():
    """Rule 22: one sound, the lesson's own for the cut, silence under the
    card; the other inputs are the pages."""
    argv = tut.short_argv("/v.mp4", 10.0, 40.0, "/o.mp4", frames=_frames(),
                          close="/w/close.png")
    inputs = [argv[i + 1] for i, a in enumerate(argv) if a == "-i"]
    assert inputs[0] == "/v.mp4" and all(x.endswith(".png") for x in inputs[1:])
    graph = _graph(argv)
    assert "[0:a]apad=" in graph and not re.search(r"\[[1-9]\d*:a\]", graph)
    assert "lavfi" not in argv and "amix" not in graph


def test_no_zoom_but_a_demos_own_focus_zoom_into_its_prepared_crop():
    """The punch-in is gone. A demo page shows the long render's demo crop
    -- the recording's hole, where the step's box (tutorial_demos) is already
    pushed in on by the long render's focus-zoom -- scaled into the hole;
    nothing else in a Short moves or zooms."""
    from atlas.modules.academy import short_frame as sf
    from atlas.modules.academy import tutorial_demos as td
    for demo in (-1, 1):
        graph = _graph(tut.short_argv("in.mp4", 12.0, 42.0, "out.mp4",
                                      frames=_frames(2, demo), close="/w/close.png"))
        assert "zoompan" not in graph and "scale=1080:-2" not in graph
        assert "boxblur" not in graph                        # no blurred backdrop
        crops = re.findall(r"crop=(\d+):(\d+):(\d+):(\d+)", graph)
        if demo < 0:
            assert crops == []
        else:
            x0, y0, w0, h0 = td.HOLE
            assert crops == [(str(w0), str(h0), str(x0), str(y0))]
            x, y, w, h = sf.DEMO_BOX
            assert f"scale={w}:{h}" in graph and f"overlay={x}:{y}" in graph
            assert "trim=start=15.000:end=30.000" in graph   # its own seconds
    assert not hasattr(tsh, "punch") and not hasattr(tsh, "PUNCH_SCALE")


def test_the_closing_card_and_the_caption_name_the_longer_lesson_by_its_title():
    m, ep = _c4(minutes=5)
    s = ts.series("claude")
    title = tut.final_title(ep, 285.11)
    for cut in tsh.cuts(m, ep):
        assert cut.lesson == title
        text = tsh.caption(s, ep, cut, "iRyhxkLk-Yo")
        assert f"Full lesson: {title}\n" in text
        assert "link in" not in text.lower() and "below" not in text.lower()


def test_the_look_tag_names_the_version_of_the_cut():
    assert tsh.LOOK_VERSION >= 2 and tsh.look_tag() == f"[look {tsh.LOOK_VERSION}]"


# ------------------------------------------- no music in a Short (rule 22)

def test_a_cut_never_reaches_into_the_end_card_music():
    """Rule 22 (the owner, 2026-10-17): music under the sting and the end card
    only, and a teaching Short has neither. The quiz closes the last chapter,
    whose end is the file's end -- the end card's window -- so the cut stops
    where the window begins; the answer is still the last thing seen."""
    m = _manifest(seconds=330.0,
                  chapters=[(0.0, "Intro"), (12.0, ts.PAYOFF_CHAPTER),
                            (60.0, "What is MCP?"), (200.0, "Not working?"),
                            (270.0, "Quick quiz")],
                  timings=[(12.0, 30.0, "a"), (30.0, 40.0, "b"), (285.0, 300.0, "x"),
                           (300.0, 322.0, "y")],
                  music_windows=[(0.0, 1.0), (322.0, 330.0)])
    quiz = next(c for c in tsh.cuts(m, _ep()) if c.kind == "quiz")
    assert quiz.end == 322.0 and 20.0 <= quiz.end - quiz.start <= tsh.CUT_MAX
    for c in tsh.cuts(m, _ep()):
        for w0, w1 in m.music_windows:
            assert c.end <= w0 or c.start >= w1, (c, (w0, w1))


def test_a_cut_that_would_open_in_a_window_starts_after_it():
    assert tsh.clear_of_music(0.5, 40.0, [(0.0, 1.0)]) == (1.0, 40.0)
    assert tsh.clear_of_music(10.0, 40.0, [(0.0, 1.0), (300.0, 310.0)]) == (10.0, 40.0)
    assert tsh.clear_of_music(280.0, 320.0, [(300.0, 310.0)]) == (280.0, 300.0)


def test_a_render_before_the_windows_lists_none_and_cuts_as_before():
    """A render made before 2026-10-17 has no music_windows (its bed runs
    under the whole file): its cuts are what they were."""
    assert tsh.cuts(_manifest(), _ep()) == tsh.cuts(_manifest(music_windows=[]), _ep())


def test_a_payoff_opening_in_the_sting_window_starts_after_it_and_still_runs_20_s():
    """Batches 3+5 (2026-10-17): the sting's music sits at 6-7 s, inside the
    payoff chapter. A payoff that would open in it starts on the first line
    after it and still runs a whole-line 20-37 s; one past it is untouched."""
    m = _manifest(seconds=300.0, chapters=[(0.0, "Intro"), (4.0, ts.PAYOFF_CHAPTER),
                                           (60.0, "Next")],
                  timings=[(5.0, 9.0, "a"), (9.0, 20.0, "b"), (20.0, 32.0, "c"),
                           (32.0, 45.0, "d"), (45.0, 70.0, "e")],
                  music_windows=[(6.0, 7.0), (290.0, 300.0)])
    payoff = next(c for c in tsh.cuts(m, _ep()) if c.kind == "payoff")
    assert (payoff.start, payoff.end) == (9.0, 45.0)
    m, ep = _c4(music_windows=[(6.0, 7.0), (280.11, 285.11)])
    payoff = next(c for c in tsh.cuts(m, ep) if c.kind == "payoff")
    assert (payoff.start, payoff.end) == (14.37, 44.37)
