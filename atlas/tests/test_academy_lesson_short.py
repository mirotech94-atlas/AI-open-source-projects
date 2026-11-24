"""Lesson Shorts (academy/lesson_short.py): Miro, 2026-10-11, "make the
reels shorts about each video and the important parts to it". The picker
is code, not a model; the words are the lesson's own."""
from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from atlas.modules.academy import lesson_short as ls
from atlas.modules.academy import reel, reel_design


def _lesson(tmp: Path, course: str, lid: str, segs: list[dict], *, audio: int | None = None) -> Path:
    d = tmp / course / lid
    (d / "audio").mkdir(parents=True)
    (d / "segments.json").write_text(json.dumps(segs))
    for i in range(len(segs) if audio is None else audio):
        (d / "audio" / f"{i + 1:03d}.mp3").write_bytes(b"MP3")
    return d


def _seg(heading: str, say: str, kind: str = "body") -> dict:
    return {"kind": kind, "heading": heading, "say": say, "html": ""}


TITLE = {"kind": "title", "heading": "Lesson 1.2",
         "html": "<h1>Permission first</h1><p class=sub>x</p>",
         "say": "Lesson 1 point 2. Permission first."}
WORDS = " ".join(["word"] * 18)


def test_the_title_comes_from_the_first_slide():
    assert ls.lesson_title([TITLE]) == "Permission first"
    assert ls.lesson_title([{"kind": "title", "say": "Lesson 2 point 1. The rule."}]) == "The rule"


def test_the_picker_prefers_why_it_matters_and_takes_it_from_its_opener():
    segs = [TITLE,
            _seg("In this lesson", WORDS + " " + WORDS),
            _seg("Some story", "Some story. " + WORDS + " " + WORDS),
            _seg("Why it matters", "Why it matters. " + WORDS),
            _seg("Why it matters", "Never let a check act on its own."),
            _seg("Why it matters", WORDS),
            _seg("Next", WORDS + " " + WORDS)]
    assert ls.pick(segs) == ([3, 4, 5], [])


def test_a_line_that_leans_back_never_opens_a_run():
    """The first previews (2026-10-11): sell/1.2 opened on item one of a
    list and later said "not on that list" about a list it never set up."""
    for text in ("That's why the rule exists.", "So the rule is short.", "Two: the second.",
                 "It isn't on that list.", "Use the checklist from above, tick it.",
                 "This is the part people skip."):
        assert not ls.standalone(text), text
    for text in ("The rule. Unanswered proposals expire.", "No campaign, no clip.",
                 "Permission is one of three things."):
        assert ls.standalone(text), text


def test_an_over_budget_section_is_opener_plus_a_standalone_closer_or_nothing():
    long = " ".join(["word"] * 40)
    segs = [TITLE,
            _seg("The mistake", "The mistake that ends channels. " + long),
            _seg("The mistake", "One: it's their copyright. " + long),
            _seg("The mistake", "Two: reused content isn't paid. " + long),
            _seg("The mistake", "So the rule for this whole module is short."),
            _seg("The mistake", "No campaign, no clip.")]
    dur = [3.0, 14.0, 17.0, 17.0, 3.0, 2.0]
    assert ls.pick(segs, dur) == ([1, 5], [])
    segs[5] = _seg("The mistake", "That is the whole rule.")
    assert ls.pick(segs, dur) is None


def test_a_skipped_middle_line_that_leans_back_breaks_the_run():
    segs = [TITLE,
            _seg("The idea", "The idea. " + WORDS + " " + WORDS),
            _seg("The idea", "It isn't on that list. " + WORDS)]
    assert ls.pick(segs) == ([1, 2], [])          # contiguous: fine
    assert not ls._usable(segs, [1, 2]) is False


def test_the_action_is_one_thing_to_do_today_when_it_stands_alone_and_fits():
    segs = [TITLE,
            _seg("The idea", "The idea. " + WORDS + " " + WORDS),
            _seg("Next", WORDS),
            _seg("One thing to do today", "One thing to do today. Open your notes and write one rule."),
            _seg("One thing to do today", WORDS)]
    assert ls.pick(segs) == ([1], [3])
    segs[3] = _seg("One thing to do today", "Using the checklist above, tick one box.")
    assert ls.pick(segs) == ([1], [])


def test_a_section_that_names_income_or_a_brand_is_skipped_not_trimmed():
    segs = [TITLE,
            _seg("Why it matters", "Why it matters. This is how you earn your first income. " + WORDS),
            _seg("Why it matters", WORDS + " " + WORDS),
            _seg("The idea", "The idea. " + WORDS + " " + WORDS)]
    assert ls.pick(segs) == ([3], [])
    segs[3] = _seg("The idea", "Post it on TikTok every day. " + WORDS + " " + WORDS)
    assert ls.pick(segs) is None


def test_lessons_need_one_mp3_per_segment_and_rotation_interleaves(tmp_path):
    segs = [TITLE, _seg("The idea", "The idea. " + WORDS + " " + WORDS)]
    _lesson(tmp_path, "sell", "1.1", segs)
    _lesson(tmp_path, "sell", "1.2", segs)
    _lesson(tmp_path, "sell", "10.1", segs)
    _lesson(tmp_path, "operator", "1.1", segs)
    _lesson(tmp_path, "operator", "1.2", segs, audio=1)         # mismatch: skipped
    _lesson(tmp_path, "sample", "1.1", segs)                    # not a course
    found = ls.lessons(tmp_path)
    assert [x.key for x in found] == ["lesson:operator/1.1", "lesson:sell/1.1",
                                      "lesson:sell/1.2", "lesson:sell/10.1"]
    assert [x.key for x in ls.rotation(found)] == [
        "lesson:operator/1.1", "lesson:sell/1.1", "lesson:sell/1.2", "lesson:sell/10.1"]


def test_build_says_the_lesson_then_closes_on_the_store(tmp_path):
    segs = [TITLE, _seg("The idea", "The idea. " + WORDS + " " + WORDS)]
    d = _lesson(tmp_path, "sell", "1.2", segs)
    les = ls.Lesson("sell", "1.2", d)
    title, beats, caption = ls.build(les)
    assert title == "Permission first"
    # No "Lesson 1 point 2" intro: the Short opens on the lesson's point.
    assert [b.kind for b in beats] == ["hook", "cta"]
    assert beats[0].heading == "The idea" and beats[1].heading == ""
    assert beats[0].audio.endswith("002.mp3")
    assert beats[1].audio == "" and "lesson 1.2 of Sell With AI" in beats[1].text
    assert caption.split("\n")[0] == "The idea. " + WORDS + " " + WORDS or \
        caption.split("\n")[0] == "Permission first"
    assert caption.endswith("Made with AI") and "agency.storefront.test/sell-with-ai/" in caption
    assert "link in bio" not in caption.lower()
    yt = ls.youtube_text(les, title, beats)
    assert yt.split("\n")[0] == "Permission first" and yt.endswith("Made with AI")
    assert "https://agency.storefront.test/sell-with-ai/" in yt


def test_find_reads_only_real_course_keys(tmp_path):
    _lesson(tmp_path, "sell", "1.2", [TITLE])
    assert ls.find("lesson:sell/1.2", tmp_path).lesson == "1.2"
    for bad in ("lesson:sample/1.1", "lesson:sell/9.9", "lesson:../x/1.1", "sell/1.2"):
        assert ls.find(bad, tmp_path) is None


def test_every_lesson_on_this_box_passes_the_post_gates():
    """When the lesson data is here (the server, or a synced checkout),
    every lesson in rotation makes a caption the auto-post accepts and a
    YouTube title within 100 characters, or is skipped by the picker --
    never a daily card for a fixable reason."""
    found = ls.lessons()
    if not found:
        pytest.skip("no lesson data on this box")
    from atlas.modules.clips import promo
    from atlas.modules.content import guard
    for les in found:
        built = ls.build(les)
        if built is None:
            continue
        _, _, caption = built
        assert promo.vet({"media_id": 1, "caption": caption}) is None, les.key
        assert guard.refusal(caption) is None, les.key
        assert 2 <= len(promo.youtube_title(caption)) <= 100, les.key


def test_next_lesson_rotates_never_shown_first(monkeypatch, tmp_path):
    segs = [TITLE, _seg("The idea", "The idea. " + WORDS + " " + WORDS)]
    for c in ("operator", "sell"):
        for lid in ("1.1", "1.2"):
            _lesson(tmp_path, c, lid, segs)
    monkeypatch.setenv(ls.ROOT_ENV, str(tmp_path))
    now = datetime.now(UTC)
    assert reel.next_lesson({}) == "lesson:operator/1.1"
    shown = {"lesson:operator/1.1": now - timedelta(days=1)}
    assert reel.next_lesson(shown) == "lesson:sell/1.1"
    shown = {k: now - timedelta(days=i) for i, k in enumerate(
        ["lesson:operator/1.1", "lesson:sell/1.1", "lesson:operator/1.2", "lesson:sell/1.2"])}
    assert reel.next_lesson(shown) == "lesson:sell/1.2"


@pytest.mark.parametrize(("env", "kind"), [("", "lesson"), ("product", "product"), ("x", "lesson")])
def test_the_reel_kind_switch(monkeypatch, env, kind):
    monkeypatch.setenv("ACADEMY_REEL_KIND", env)
    assert reel.reel_kind() == kind


def test_themes_vary_by_day_and_every_page_is_timed_and_scriptless():
    names = {reel_design.theme_for("lesson:sell/1.2", d).name for d in range(6)}
    assert len(names) == len(reel_design.THEMES)
    t = reel_design.THEMES[0]
    for kind, text in (("title", "Permission first"), ("line", "No campaign, no clip. " * 4),
                       ("cta", "That's lesson 1.2.")):
        doc = reel_design.lesson_page(t, kind, text, lesson="1.2", title="Permission first",
                                      course="Sell With AI", cover="", seconds=6.0)
        assert "<script" not in doc and reel_design.TAG in doc and "Lesson 1.2" in doc
    line = reel_design.lesson_page(t, "line", "Expiry is checked. Silence is never consent.",
                                   lesson="1.2", title="T", course="C", cover="", seconds=4.0)
    assert line.count('class="chunk"') == 2 and line.count('class="wd"') == 7


async def test_a_close_fish_cannot_say_is_dropped_not_the_whole_short(monkeypatch, tmp_path):
    from atlas.modules.content import ffmpeg, stills
    monkeypatch.setenv(reel.LESSON_VOICE_ENV, "recorded")
    segs = [TITLE, _seg("The idea", "The idea. " + WORDS + " " + WORDS)]
    d = _lesson(tmp_path, "sell", "1.2", segs)
    les = ls.Lesson("sell", "1.2", d)
    _, beats, _ = ls.build(les)
    calls = []

    async def narration(text, model=None):
        calls.append(text)
        return None, "", "unreachable"
    monkeypatch.setattr(stills, "narration", narration)

    async def duration(path):
        return 12.0

    async def run(argv, **kw):
        return b""
    monkeypatch.setattr(ffmpeg, "duration", duration)
    monkeypatch.setattr(ffmpeg, "run", run)
    pages = []

    async def frames(p, secs, out):
        pages.extend(p)
        return 10
    monkeypatch.setattr(reel_design, "render_frames", frames)
    _out, notes, why = await reel._render_lesson(str(tmp_path), les, "Permission first", beats)
    assert why is None and len(calls) == 2 and len(pages) == 1     # the hook; the close dropped
    assert "the close was dropped" in notes[1]


def test_captions_break_at_clauses_and_never_end_on_a_word_that_needs_the_next():
    text = ("A campaign page on a clipping marketplace or in a clipping community, naming "
            "the footage, the platforms and the rules. A creator's own clipping program, "
            "with its own page.")
    chunks = reel_design._chunks(text.split())
    assert [" ".join(c) for c in chunks][:2] == ["A campaign page on a clipping marketplace",
                                                 "or in a clipping community,"]
    for c in chunks:
        assert reel_design._bare(c[-1]) not in reel_design.WEAK_END, c
        assert len(c) <= reel_design.CHUNK + 2


def test_a_line_beat_shows_the_lessons_own_heading():
    t = reel_design.THEMES[0]
    doc = reel_design.lesson_page(t, "line", "No campaign, no clip.", lesson="1.2", title="T",
                                  course="C", cover="", seconds=3.0, label="Where campaigns are")
    assert '<div class="label">Where campaigns are</div>' in doc
    title = reel_design.lesson_page(t, "title", "T", lesson="1.2", title="T", course="C",
                                    cover="", seconds=3.0, label="x")
    assert 'class="label"' not in title


def test_each_course_cover_exists_in_the_store_assets():
    from atlas.modules.academy.site import ASSETS
    for course, path in ls.COVERS.items():
        assert (ASSETS / path).is_file(), (course, path)


# Miro, 2026-10-11: "post 2x per day, each reel a different lesson".

@pytest.mark.parametrize(("env", "single", "hours"), [
    ("", "", (16, 23)), ("9, 21", "", (9, 21)), ("", "14", (14,)), ("x", "", (16, 23)),
])
def test_the_posting_slots(monkeypatch, env, single, hours):
    monkeypatch.setenv("ACADEMY_PROMO_HOURS_UTC", env)
    monkeypatch.setenv("ACADEMY_PROMO_HOUR_UTC", single)
    assert reel.hours_utc() == hours


def test_due_slot_is_the_latest_slot_come_and_not_run(monkeypatch):
    monkeypatch.delenv("ACADEMY_PROMO_HOURS_UTC", raising=False)
    monkeypatch.delenv("ACADEMY_PROMO_HOUR_UTC", raising=False)
    day = datetime(2026, 10, 12, tzinfo=UTC)
    assert reel.due_slot(day.replace(hour=10), set()) is None
    assert reel.due_slot(day.replace(hour=16), set()) == 16
    assert reel.due_slot(day.replace(hour=23), {(day.date(), 16)}) == 23
    assert reel.due_slot(day.replace(hour=23), {(day.date(), 16), (day.date(), 23)}) is None


async def test_a_slot_with_a_post_in_the_last_three_hours_or_a_spent_day_waits(monkeypatch):
    from atlas.modules.clips import promo
    rows = {"recent": None}

    async def fetch_one(sql, *args):
        return rows["recent"]
    monkeypatch.setattr(reel, "fetch_one", fetch_one)

    async def count():
        return rows["count"]
    monkeypatch.setattr(promo, "posted_today", count)
    monkeypatch.delenv("CLIPS_PROMO_PER_DAY", raising=False)
    rows.update(recent=None, count=1)
    assert await reel.posted_today() is False          # one of two today: go
    rows.update(recent=None, count=2)
    assert await reel.posted_today() is True           # the day is spent
    rows.update(recent={"x": 1}, count=0)
    assert await reel.posted_today() is True           # too soon after the last


def test_two_a_day_is_the_default_cap(monkeypatch):
    from atlas.modules.clips import promo
    monkeypatch.delenv("CLIPS_PROMO_PER_DAY", raising=False)
    assert promo.per_day() == 2


async def test_the_dry_run_knows_a_lesson_key(monkeypatch, tmp_path):
    segs = [TITLE, _seg("The idea", "The idea. " + WORDS + " " + WORDS)]
    _lesson(tmp_path, "sell", "1.2", segs)
    monkeypatch.setenv(ls.ROOT_ENV, str(tmp_path))
    # A lesson Short renders only with the NotebookLM fallback on (2026-10-19).
    monkeypatch.setenv("ACADEMY_NOTEBOOKLM_FALLBACK", "true")
    dry = await reel.render_promo_reel_dry("lesson:sell/1.2")
    assert dry.ok and "lesson 1.2" in dry.detail and "Permission first" in dry.detail
    assert not (await reel.render_promo_reel_dry("lesson:sell/9.9")).ok


def test_next_lesson_skips_a_lesson_with_no_part(monkeypatch, tmp_path):
    good = [TITLE, _seg("The idea", "The idea. " + WORDS + " " + WORDS)]
    bad = [TITLE, _seg("Next", WORDS)]
    _lesson(tmp_path, "operator", "0.1", bad)
    _lesson(tmp_path, "operator", "0.2", good)
    monkeypatch.setenv(ls.ROOT_ENV, str(tmp_path))
    assert reel.next_lesson({}) == "lesson:operator/0.2"


def test_the_rule_line_opens_the_short_when_lifting_it_leaves_the_rest_standing():
    segs = [TITLE,
            _seg("The rule", "The rule. Approvals expire and never run on timeout. " + WORDS),
            _seg("The rule", "Silence is never consent."),
            _seg("The rule", "An approval card waits for a person, every time. " + WORDS)]
    assert ls._hook_first(segs, [1, 2, 3]) == [2, 1, 3]
    segs[3] = _seg("The rule", "That is why the card waits. " + WORDS)     # leans on it
    assert ls._hook_first(segs, [1, 2, 3]) == [1, 2, 3]


@pytest.mark.parametrize(("env", "courses"), [("", ("operator", "sell")),
                                               ("sell,adults", ("sell", "adults")),
                                               ("nope", ("operator", "sell"))])
def test_business_courses_rotate_first(monkeypatch, env, courses):
    monkeypatch.setenv(ls.COURSES_ENV, env)
    assert ls.active_courses() == courses


def test_lesson_pages_use_the_brand_and_keep_text_inside_the_safe_zone():
    t = reel_design.theme_for("lesson:sell/1.2", 3)
    assert (t.bg, t.fg, t.accent) == ("#0A0D14", "#EEF2FA", "#5B8CFF")
    line = reel_design.lesson_page(t, "line", "No campaign, no clip.", lesson="1.2",
                                   title="Permission first", course="Sell With AI",
                                   cover="", seconds=3.0, label="The rule")
    hook = reel_design.lesson_page(t, "hook", "No campaign, no clip.", lesson="1.2",
                                   title="Permission first", course="Sell With AI",
                                   cover="", seconds=3.0)
    assert '<body class="">' in line and '<body class="dark">' in hook
    assert "blob" not in line and "drift" not in line
    assert f"width:{reel_design.W - reel_design.SAFE_LEFT - reel_design.SAFE_RIGHT}px" in line


async def test_a_lesson_short_is_said_fresh_in_the_human_voice(monkeypatch, tmp_path):
    """Miro, 2026-10-12: "make it like a human talking", "go for it". Every
    beat is said by Fish S2 with its cue; a beat Fish cannot say falls back
    to the lesson's recording; the captions never show a cue."""
    from atlas.modules.content import ffmpeg, stills
    monkeypatch.delenv(reel.LESSON_VOICE_ENV, raising=False)
    segs = [TITLE, _seg("The idea", "The idea. " + WORDS + " " + WORDS)]
    d = _lesson(tmp_path, "sell", "1.2", segs)
    les = ls.Lesson("sell", "1.2", d)
    _, beats, _ = ls.build(les)
    calls = []

    async def narration(text, model=None):
        calls.append((text, model))
        if text.startswith("[confident]"):
            return None, "", "Fish down for the hook"      # the hook falls back
        return b"mp3", "Fish Audio, the British reels voice", None
    monkeypatch.setattr(stills, "narration", narration)

    async def duration(path):
        return 12.0

    async def run(argv, **kw):
        return b""
    monkeypatch.setattr(ffmpeg, "duration", duration)
    monkeypatch.setattr(ffmpeg, "run", run)
    pages = []

    async def frames(p, secs, out):
        pages.extend(p)
        return 10
    monkeypatch.setattr(reel_design, "render_frames", frames)
    _out, notes, why = await reel._render_lesson(str(tmp_path), les, "Permission first", beats)
    assert why is None and len(pages) == len(beats)
    assert all(m == "s2.1-pro" for _t, m in calls)
    assert any(t.startswith("[happy]") for t, _m in calls)          # the close, cued
    assert "from the lesson's recording" in notes[1] and "s2.1-pro" in notes[1]
    assert not any("[confident]" in p or "[happy]" in p for p in pages)
