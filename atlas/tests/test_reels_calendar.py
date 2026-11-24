"""The 30-day reels calendar (Miro, 2026-09-27): the plan, the dates, the
captions, and the gates -- a sequel waits for its episode to PUBLISH."""
from __future__ import annotations

from datetime import date
from itertools import pairwise
from pathlib import Path

from atlas.modules.content import calendar as cal
from atlas.modules.content import captions
from atlas.modules.content.drafts import AI_LABEL


def test_both_packages_load_whole():
    assert len(cal.jobs("stories")) == 30
    assert len(cal.jobs("business")) == 30
    assert sum(len(j["scenes"]) for j in cal.jobs()) == 170


def test_every_beat_fits_veo_and_the_month_is_258_clips():
    parts = [p for j in cal.jobs() for p in cal.plan(j)]
    assert len(parts) == 259
    assert all(0 < p.length <= 8 for p in parts)
    per_day = [len(cal.plan(j)) for j in cal.jobs()]
    assert max(per_day) == 13, "more than one day's 10 clips: banked ahead"
    business = [p for j in cal.jobs("business") for p in cal.plan(j)]
    assert len(business) == 240 and all(0 < p.length <= 8 for p in business)


def test_only_characters_speak_and_a_line_is_never_cut_across_two_clips():
    """Miro, 2026-09-27: no narrator -- a narrator line is never spoken."""
    for j in cal.jobs():
        for beat in j["scenes"]:
            said = [d["text_ar"] for d in beat.get("dialogue_lines") or []
                    if not cal.is_narrator(d["speaker_ar"])]
            dealt = [t for p in cal.plan(j) if p.beat_id == beat["scene_id"]
                     for _, t in p.lines]
            assert dealt == said


def test_the_prompt_has_characters_talking_moving_and_no_narrator():
    parts = cal.plan(cal.jobs()[0])
    prompt = cal.clip_prompt(next(p for p in parts[1:] if p.lines))
    assert "Egyptian Arabic" in prompt and "NOT spoken" in prompt
    assert "to each other" in prompt and "no narrator" in prompt
    assert cal.MOTION in prompt and "never a still or a slideshow" in prompt
    assert all("narrator (voice only)" not in cal.clip_prompt(p) for p in parts)
    assert "No on-screen text" in prompt
    lesson = cal.clip_prompt(cal.plan(cal.jobs("business")[1])[1])
    assert lesson.startswith(cal.BUSINESS_CAST) and "Nour says to Sami, on camera" in lesson


def test_the_month_shifts_together_in_new_york_time(monkeypatch):
    monkeypatch.delenv("REELS_CALENDAR_START", raising=False)
    first = cal.slot_at(cal.jobs()[0])
    assert (first.date(), first.hour, first.minute) == (date(2026, 9, 28), 19, 30)
    monkeypatch.setenv("REELS_CALENDAR_START", "2026-11-01")
    day7 = cal.slot_at(cal.jobs()[6])
    assert day7.date() == date(2026, 11, 7) and day7.utcoffset().total_seconds() == -5 * 3600
    monkeypatch.setenv("REELS_CALENDAR_START", "soon")
    assert cal.start_date() == date(2026, 9, 28), "a bad date is not guessed at"


def test_no_dates_gate_anything_two_stories_then_a_lesson():
    """Miro, 2026-09-27: don't follow the 30-day calendar literally -- do
    everything now. Order only: two stories, one lesson, each package in
    its own order (so a sequel still follows its episode)."""
    order = [j["job_id"] for j in cal.scheduled()]
    assert len(order) == 60 and len(set(order)) == 60
    assert order[:3] == [cal.jobs()[0]["job_id"], cal.jobs()[1]["job_id"],
                         cal.jobs("business")[0]["job_id"]]
    stories = [i for i in order if not i.startswith("business-")]
    assert stories == [j["job_id"] for j in cal.jobs()]
    first, second = cal.jobs("business")[:2]
    assert cal.language(first) == "ar" and cal.language(second) == "en"
    assert "Educational idea, not an income guarantee" in cal.caption(second)


async def test_the_rotation_stands_aside_while_packaged_reels_remain(
        memory_backend, monkeypatch):
    monkeypatch.delenv("REELS_CALENDAR", raising=False)
    memory_backend.responses["FROM reel_calendar WHERE status = ANY"] = {"n": 3}
    assert await cal.active() is True
    memory_backend.responses["FROM reel_calendar WHERE status = ANY"] = {"n": 0}
    assert await cal.active() is False
    monkeypatch.setenv("REELS_CALENDAR", "off")
    memory_backend.responses["FROM reel_calendar WHERE status = ANY"] = {"n": 3}
    assert await cal.active() is False


def test_arabic_voice_gets_the_scripts_own_english_captions_in_order():
    """Miro, 2026-09-27: Arabic voice, English captions."""
    j = cal.jobs()[0]
    parts = cal.plan(j)
    cues = cal.cues(parts, [p.length for p in parts])
    spoken = [c for c in cues if not c.top]
    joined = " ".join(c.text for c in spoken).split()
    meant = " ".join(ln.split(": ", 1)[1] for b in j["scenes"]
                     for ln in b["english_meaning"].splitlines()
                     if not ln.startswith("Narrator")).split()
    assert joined == meant
    context = " ".join(c.text for c in cues if c.top)
    assert context.startswith("Seven years ago"), "the narrator's line, silent, on top"
    assert not any("\u0600" <= ch <= "\u06ff" for c in cues for ch in c.text)
    assert all(a.end <= b.start + 1e-6 for a, b in pairwise(spoken))
    assert any("Egyptian Arabic" in cal.clip_prompt(p) for p in parts)
    ass = captions.build_scripted_ass(cues, 70.0, arabic=False)
    assert ",Caption,," in ass and AI_LABEL in ass


def test_every_business_beat_is_captioned_in_english_across_its_whole_length():
    for j in cal.jobs("business")[:2]:
        parts = cal.plan(j)
        cues = cal.cues(parts, [p.length for p in parts])
        text = " ".join(c.text for c in cues)
        assert text.split() == " ".join(b["voice_en"] for b in j["scenes"]).split()


async def test_no_groq_key_means_no_english_captions_said_in_words(monkeypatch):
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    cues, why = await captions.english_segments(b"mp3")
    assert cues is None and "GROQ_API_KEY not set" in why


def test_the_caption_keeps_the_packages_disclosure_and_ends_with_the_label():
    text = cal.caption(cal.jobs()[0])
    assert "مولّدة بالذكاء الاصطناعي" in text
    assert text.endswith(AI_LABEL)


async def test_a_sequel_is_not_carded_before_its_episode_published(
        memory_backend, monkeypatch):
    from atlas.kernel import reels
    monkeypatch.setenv("REELS_CALENDAR_START", "2026-09-28")
    monkeypatch.setattr(cal, "ensure_loaded", _noop)
    d1 = cal.jobs()[0]["job_id"]
    rows = [{"job_id": j["job_id"], "day": j["day"], "title": j["title"],
             "status": "published", "draft_id": None, "media_id": None,
             "proposal_id": None, "note": None} for j in cal.jobs()]
    rows[0]["status"] = "declined"
    rows[2].update(status="rendered", media_id=77)
    memory_backend.responses["JOIN proposals"] = []
    memory_backend.responses["FROM reel_calendar WHERE package"] = rows
    proposed = []

    async def propose(*a, **k):
        proposed.append(a)
        return 1

    monkeypatch.setattr(reels, "propose", propose)
    from datetime import UTC, datetime
    lines = await reels._calendar_tick(datetime(2026, 9, 30, 18, 0, tzinfo=UTC))
    assert proposed == []
    assert any(f"waiting for {d1}" in ln for ln in lines)


async def _noop() -> None:
    return None


async def test_an_unanswered_or_failed_card_is_offered_again_only_a_no_holds(
        memory_backend):
    from atlas.kernel import reels
    memory_backend.responses["JOIN proposals"] = [
        {"job_id": "a", "status": "expired"}, {"job_id": "b", "status": "failed"},
        {"job_id": "c", "status": "declined"}, {"job_id": "d", "status": "executed"}]
    await reels._sync_calendar_cards()
    after = {args[0]: args[1] for sql, args in memory_backend.sql_log
             if sql.startswith("UPDATE reel_calendar")}
    assert after == {"a": "rendered", "b": "rendered", "c": "declined",
                     "d": "published"}


async def test_a_beat_veo_refuses_twice_blocks_its_day_and_spends_nothing_more(
        memory_backend, monkeypatch, tmp_path):
    from atlas.modules.content import veo
    monkeypatch.setattr(cal.ffmpeg, "missing", lambda: None)
    monkeypatch.setattr(veo, "configured", lambda: True)
    monkeypatch.setattr(cal, "clip_dir", lambda job_id: str(tmp_path / job_id))

    async def zero(*a, **k):
        return 0

    async def room():
        return None

    recorded = []

    async def record(seconds, media_id):
        recorded.append(seconds)

    async def refuse(prompt):
        raise veo.VeoError("blocked by safety filters", status_code=400)

    monkeypatch.setattr(cal, "clips_today", zero)
    monkeypatch.setattr(cal, "_reserve_clip", room)
    monkeypatch.setattr(veo, "record", record)
    monkeypatch.setattr(veo, "clip", refuse)
    memory_backend.responses["SET failures = failures + 1"] = {"status": "blocked"}
    result = await cal.render_job(cal.jobs()[4]["job_id"])
    assert result.ok and result.evidence["blocked"] is True
    assert "BLOCKED" in result.detail and "safety" in result.detail
    assert recorded == [-8], "a refusal Veo never accepted is refunded"


async def test_clips_are_smoothed_one_by_one_and_a_failure_keeps_veos_motion(
        monkeypatch, tmp_path):
    monkeypatch.setattr(cal, "clip_dir", lambda job_id: str(tmp_path / job_id))
    j = cal.jobs()[6]
    parts = cal.plan(j)
    import os
    os.makedirs(tmp_path / j["job_id"])
    for p in parts[:2]:
        (tmp_path / j["job_id"] / f"{p.key}.raw.mp4").write_bytes(b"raw")
    calls = []

    async def smooth(src, out):
        calls.append(src)
        if len(calls) == 1:
            Path(out).write_bytes(b"60fps")
            return out, "ok"
        return src, "failed"

    monkeypatch.setattr(cal.ffmpeg, "smooth", smooth)
    assert len(cal.missing(j)) == len(parts) - 2
    done, waiting = await cal._smooth_pending(j)
    assert (done, waiting) == (2, 0)
    assert cal.unsmoothed(j) == [] and cal.native_count(j) == 1
    assert (tmp_path / j["job_id"] / f"{parts[0].key}.mp4").read_bytes() == b"60fps"


async def test_the_daily_allowance_counts_from_pacific_midnight(memory_backend):
    from datetime import UTC, datetime
    memory_backend.responses["FROM veo_usage WHERE at >="] = {"seconds": 80}
    now = datetime(2026, 9, 28, 5, 0, tzinfo=UTC)          # 22:00 PT on the 27th
    assert await cal.clips_today(now) == 10
    since = memory_backend.sql_log[-1][1][0] if memory_backend.sql_log else None
    assert since is None or since.utcoffset().total_seconds() == -7 * 3600


async def test_nothing_packaged_ready_gets_a_short_veo_backup(
        memory_backend, monkeypatch):
    """Miro, 2026-09-27: a post every day, never a slideshow. Day 1 is a day
    late, so Sep 28 gets one short backup on Veo -- only from clips left over."""
    from atlas.kernel import reels
    from atlas.kernel.contracts import ActionResult
    from atlas.modules.content import reels as playbook
    async def yes():
        return True

    monkeypatch.setattr(cal, "active", yes)
    state = {cal.jobs()[0]["job_id"]: {"job_id": cal.jobs()[0]["job_id"], "day": 1,
                                        "status": "rendering",
                                        "note": "daily clip allowance used"}}
    used = {"n": 4}

    async def fake_state(_cal):
        return state

    async def nothing():
        return None

    async def zero():
        return 0

    async def today(now=None):
        return used["n"]

    async def pick(n, today=None):
        return [{"id": 1, "format": "wronged-man revenge saga", "max_scenes": 4},
                {"id": 2, "format": "clever pet outsmarts human", "max_scenes": 2}]

    ran = []

    async def execute(req):
        ran.append((req.action, dict(req.params)))
        return ActionResult(False, "stop here")

    monkeypatch.setattr(reels, "_calendar_state", fake_state)
    monkeypatch.setattr(reels, "no_platform_line", nothing)
    monkeypatch.setattr(cal, "clips_today", today)
    monkeypatch.setattr(playbook, "ensure_seeds", nothing)
    monkeypatch.setattr(playbook, "drafted_today", zero)
    monkeypatch.setattr(playbook, "pick_today", pick)
    monkeypatch.setattr(reels, "execute", execute)
    lines = await reels.reel_lines()
    assert ran == [("content.draft_reel", {"format_id": 2})], "one, and short"
    assert "backup reel" in lines[0] and "still being made" in lines[0]

    used["n"] = 9
    ran.clear()
    lines = await reels.reel_lines()
    assert ran == [] and lines[0].startswith("No backup reel today")

    state[cal.jobs()[0]["job_id"]] = {"job_id": cal.jobs()[0]["job_id"], "day": 1,
                                       "status": "carded", "note": None}
    lines = await reels.reel_lines()
    assert ran == [] and "today's post is the calendar's" in lines[0]


async def test_the_render_cannot_be_asked_for_the_stills_backend():
    from atlas.modules import content
    bad = await content.render_video_dry(1, backend="local")
    assert not bad.ok and "backend must be one of" in bad.detail


def test_calendar_captions_are_coloured_per_speaker():
    parts = cal.plan(cal.jobs()[0])
    cues = [c for c in cal.cues(parts, [p.length for p in parts]) if not c.top]
    nader = {c.color for c in cues if "signature" in c.text and "Everyone" not in c.text}
    fouad = {c.color for c in cues if "liars" in c.text}
    assert nader and fouad and nader != fouad


def test_only_series_reels_are_captioned():
    """Miro, 2026-09-27: captions on series only."""
    assert all(cal.is_series(j) for j in cal.jobs())
    assert not any(cal.is_series(j) for j in cal.jobs("business"))
    assert render_style_in(cal.clip_prompt(cal.plan(cal.jobs()[0])[0]))


def render_style_in(prompt: str) -> bool:
    from atlas.modules.content import render
    return render.STYLE in prompt


async def test_a_reserve_for_outside_renders_is_never_spent_here(memory_backend, monkeypatch):
    """The Code Chaos host timers take 8 clips at 07:03 UTC through Oct 2."""
    from datetime import UTC, datetime
    monkeypatch.setenv("VEO_RESERVED_CLIPS", "8")
    monkeypatch.setenv("VEO_RESERVED_UNTIL", "2026-10-02")
    at = datetime(2026, 9, 29, 7, 1, tzinfo=UTC)          # just after the reset
    memory_backend.responses["FROM veo_usage WHERE at >="] = {"seconds": 0}
    assert await cal.clips_today(at) == 8, "untouched reserve counts as spent"
    memory_backend.responses["FROM veo_usage WHERE at >="] = {"seconds": 64}
    assert await cal.clips_today(at) == 8, "the timers' own 8, not 16"
    after = datetime(2026, 10, 3, 8, 0, tzinfo=UTC)
    memory_backend.responses["FROM veo_usage WHERE at >="] = {"seconds": 0}
    assert await cal.clips_today(after) == 0


async def test_a_clip_that_banks_clears_an_earlier_strike(memory_backend, monkeypatch,
                                                           tmp_path):
    from atlas.modules.content import veo
    monkeypatch.setattr(cal.ffmpeg, "missing", lambda: None)
    monkeypatch.setattr(veo, "configured", lambda: True)
    monkeypatch.setattr(cal, "clip_dir", lambda job_id: str(tmp_path / job_id))

    async def two(*a, **k):
        return 8

    async def room():
        return None

    async def record(*a):
        return None

    async def clip(prompt):
        return b"mp4"

    async def no_smooth(j):
        return 0, 0

    monkeypatch.setattr(cal, "clips_today", two)
    monkeypatch.setattr(cal, "_reserve_clip", room)
    monkeypatch.setattr(cal, "_smooth_pending", no_smooth)
    monkeypatch.setattr(veo, "record", record)
    monkeypatch.setattr(veo, "clip", clip)
    result = await cal.render_job(cal.jobs()[0]["job_id"])
    assert result.ok and result.evidence["made"] == 2
    upd = [a for s, a in memory_backend.sql_log if "failures = CASE" in s]
    assert upd and upd[0][2] == 2


def test_short_reels_are_not_held_behind_day_1_and_sequels_wait(monkeypatch):
    """Miro, 2026-09-28: continue with other days, don't wait for day 1."""
    from atlas.kernel import reels
    jobs = cal.jobs()
    state = {j["job_id"]: {"status": "draft"} for j in cal.scheduled()}
    state[jobs[0]["job_id"]] = {"status": "rendering"}          # D01, 11 to go
    left = {j["job_id"]: len(cal.plan(j)) for j in cal.scheduled()}
    left[jobs[0]["job_id"]] = 11
    monkeypatch.setattr(cal, "missing", lambda j: [0] * left[j["job_id"]])
    monkeypatch.setattr(cal, "unsmoothed", lambda j: [])
    order = [j["job_id"] for j in reels._render_order(cal, state)]
    assert order[0] != jobs[0]["job_id"], "a shorter reel goes first"
    assert jobs[2]["job_id"] not in order, "RETURN 02 waits for RETURN 01 to post"
    assert jobs[0]["job_id"] in order
    assert left[order[0]] == min(left[i] for i in order)
