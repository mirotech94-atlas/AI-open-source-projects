"""The daily Academy reel for the clips accounts (academy/reel.py +
clips.autopublish_promo): Miro, 2026-10-06, "make continuous reels about
ATLAS Academy". Catalog words only, one a day, gated in code."""
from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from atlas.kernel.contracts import ActionResult, Tier
from atlas.modules.academy import promo_copy, reel
from atlas.modules.academy.catalog import load
from atlas.modules.clips import promo
from atlas.modules.content.drafts import AI_LABEL

CAT = load()


def test_the_actions_are_tier_auto_and_the_daily_tick_is_registered():
    from atlas.kernel import scheduler
    from atlas.modules.academy import AcademyModule
    from atlas.modules.clips import ClipsModule
    render = next(a for a in AcademyModule().actions() if a.name == "academy.render_promo_reel")
    auto = next(a for a in ClipsModule().actions() if a.name == "clips.autopublish_promo")
    assert render.tier is Tier.AUTO and render.rollback is not None
    assert auto.tier is Tier.AUTO and auto.vet is promo.vet and auto.max_per_hour == 1
    assert set(auto.params) == {"media_id", "caption", "slug", "youtube_caption", "legs"}
    assert callable(scheduler._maybe_academy_reel)


def test_every_sellable_product_has_a_script_in_catalog_words():
    products = reel.sellable(CAT)
    assert products, "nothing sellable"
    catalog_text = " ".join(
        " ".join([p.title, p.summary, p.kind] + [v for _, v in p.label]) for p in CAT.products)
    for p in products:
        for day in range(3):
            beats = reel.script_for(p, day)
            # Hook first (2026-10-11), the product and the store last.
            assert beats[0][0] in promo_copy.for_slug(p.slug).hooks
            assert beats[0][0] == beats[0][1]
            assert beats[-1][0] == p.title and reel.STORE_SAID in beats[-1][1]
            assert reel.beat_kinds(beats) == ["hook"] + ["fact"] * (len(beats) - 2) + ["title"]
            words = sum(len(said.split()) for _, said in beats)
            assert 30 <= words <= 110, (p.slug, words)
            for shown, said in beats[1:-1]:
                # A value card is catalog text, whole or its first clause.
                assert shown in catalog_text, (p.slug, shown)
                assert said.rstrip(".") in catalog_text
            said = " ".join(s for _, s in beats).lower()
            for word in MONEY_WORDS:
                assert word not in said, (p.slug, word)
        # The hooks turn over day by day.
        assert reel.script_for(p, 0)[0] != reel.script_for(p, 1)[0]


# Nothing a promo says may promise money or results (CLAUDE.md, Mapfront
# rules): no income, no earnings, no reviews it does not have.
MONEY_WORDS = ("earn", "income", "profit", "guarantee", "reviews", "five stars",
               "passive", "per month", "while i sleep", "get rich", "$", "make money",
               "side income", "6 figures", "six figures")


def test_the_caption_opens_on_the_hook_title_and_links_the_product_page():
    from atlas.modules.content import guard
    for p in reel.sellable(CAT):
        copy = promo_copy.for_slug(p.slug)
        caption = reel.caption_for(p, tutorial="https://example/playlist")
        lines = caption.split("\n")
        # Line one is the Short's title: a hook, not the bare product name.
        assert lines[0] == copy.title != p.title
        assert promo.youtube_title(caption) == copy.title
        assert promo_copy.product_url(p.slug) in caption and "link in bio" in caption
        assert "Free step-by-step tutorials" in caption
        assert caption.endswith(AI_LABEL)
        assert promo.vet({"media_id": 1, "caption": caption}) is None
        assert guard.refusal(caption) is None, (p.slug, guard.refusal(caption))
        tags_line = next(line for line in lines if line.startswith("#"))
        assert 3 <= tags_line.count("#") <= 4              # Instagram: 5 at most
        assert len(caption) <= 2200
        low = caption.lower()
        for word in MONEY_WORDS:
            assert word not in low, (p.slug, word)
        assert "tutorials" not in reel.caption_for(p)       # no playlist, no line


def test_every_product_has_promo_copy_within_the_platform_limits():
    for p in reel.sellable(CAT):
        copy = promo_copy.for_slug(p.slug)
        assert copy is not None, p.slug
        assert len(copy.title) <= promo_copy.TITLE_MAX, (p.slug, len(copy.title))
        # One sentence: youtube_title takes the first, whole.
        assert promo.youtube_title(copy.title) == copy.title
        assert "free" not in copy.title.lower()            # it is sold
        tags = promo_copy.tags_for(p.slug)
        assert 15 <= len(tags) <= 25, (p.slug, len(tags))
        assert promo_copy.tags_chars(tags) <= promo_copy.TAGS_MAX_CHARS
        assert "ATLAS Academy" in tags
        assert len(copy.hashtags) == 3
        assert all(h == h.lower() and h.isalnum() for h in copy.hashtags)
        assert len(copy.hooks) >= 2 and all(len(h) <= 60 for h in copy.hooks)
        words = " ".join(copy.hooks + (copy.title,) + copy.tags).lower()
        for word in MONEY_WORDS:
            assert word not in words, (p.slug, word)


ABSOLUTE_CLAIM = re.compile(
    r"\b(can[\'\u2019]?t|cannot|can not|never|guarantee[ds]?|"
    r"won[\'\u2019]?t ever|100% safe|always safe)\b", re.IGNORECASE)


def test_promo_copy_makes_no_absolute_claim_about_safety_or_money():
    """Miro, 2026-10-11: the copy says what the mechanism does ("asks
    before it moves money", "drafts and waits for you"), never an absolute
    ("can't touch your money", "never sends") a buyer's setup could break."""
    for slug, copy in promo_copy.COPY.items():
        for line in copy.hooks + (copy.title,) + copy.tags + copy.hashtags:
            assert not ABSOLUTE_CLAIM.search(line), (slug, line)
    assert promo_copy.for_slug("safe-ai-operator").title == (
        "Build an AI agent that asks before it moves money (Claude + Python)")
    assert ABSOLUTE_CLAIM.search("Your AI can't touch your money.")
    assert ABSOLUTE_CLAIM.search("draft, never send")


def test_the_tag_count_is_youtubes():
    assert promo_copy.tags_chars(["ai", "claude ai"]) == 2 + 11 + 1
    assert promo_copy.tags_chars([]) == 0
    assert promo_copy.tags_for("x-unknown") == list(promo_copy.COMMON_TAGS)


async def test_the_youtube_leg_gets_shorts_the_playlist_and_the_tags(monkeypatch):
    p = CAT.product("claude-obsidian-vault-kit")

    async def playlist(slug):
        return "https://www.youtube.com/playlist?list=PLX-L_OHbwSlM"
    monkeypatch.setattr(promo_copy, "tutorial_url", playlist)
    caption = reel.caption_for(p, tutorial="yes")
    described = await promo.youtube_description(caption, p.slug)
    assert "#Shorts #claudeai" in described
    assert "Free step-by-step tutorials: https://www.youtube.com/playlist?list=" in described
    assert described.endswith(AI_LABEL) and len(described) <= 5000
    assert promo.youtube_tags(p.slug) == promo_copy.tags_for(p.slug)
    assert promo.youtube_tags("") == promo.YT_TAGS


async def test_no_playlist_is_linked_unless_the_channel_has_one(monkeypatch):
    seen = []

    async def fetch_one(sql, *args):
        seen.append(args)
    monkeypatch.setattr("atlas.kernel.state.fetch_one", fetch_one)
    assert await promo_copy.tutorial_url("local-business-automation-pack") is None
    assert seen == [("n8n",)]
    assert await promo_copy.tutorial_url("family-ai-scam-defence-workbook") is None
    assert len(seen) == 1                               # no series, no query

    async def found(sql, *args):
        return {"playlist_id": "PLX-L_OHbwSlM"}
    monkeypatch.setattr("atlas.kernel.state.fetch_one", found)
    assert (await promo_copy.tutorial_url("safe-ai-operator")
            == "https://www.youtube.com/playlist?list=PLX-L_OHbwSlM")


def test_a_product_with_no_live_buy_is_not_shown():
    from dataclasses import replace
    p = CAT.product("30-business-lessons")
    offers = tuple(replace(o, payment_link="") for o in p.offers)
    held = replace(p, offers=offers)
    assert held not in reel.sellable(replace(CAT, products=(held,)))


def test_short_takes_the_first_clause_whole():
    assert reel._short("Video course: 28 narrated video lessons of 4 to 8 minutes each, plus "
                       "the written guide (PDF)") == "Video course: 28 narrated video lessons of 4 to 8 minutes each"
    assert reel._short("35 pages, A5") == "35 pages"
    assert reel._short("x " * 60).count(" ") < 40


def test_rotation_shows_the_product_shown_longest_ago_never_shown_first():
    products = reel.sellable(CAT)
    now = datetime.now(UTC)
    shown = {p.slug: now - timedelta(days=i) for i, p in enumerate(products)}
    assert reel.next_slug(products, shown) == products[-1].slug
    shown.pop(products[2].slug)
    assert reel.next_slug(products, shown) == products[2].slug
    assert reel.next_slug([], {}) is None


async def test_render_refuses_an_unknown_or_unsellable_product(monkeypatch):
    assert not (await reel.render_promo_reel("nope")).ok
    dry = await reel.render_promo_reel_dry("30-business-lessons")
    assert dry.ok and "beats" in dry.detail


async def test_render_writes_the_cover_then_cards_and_a_done_row(monkeypatch, tmp_path):
    from atlas.modules.academy import reel_design
    from atlas.modules.content import ffmpeg, render, stills
    monkeypatch.setattr(reel_design, "available", lambda: False)   # the plain path
    monkeypatch.setattr("atlas.modules.content.DATA_DIR", str(tmp_path / "content"))
    monkeypatch.setattr(ffmpeg, "missing", lambda: None)

    async def narration(text):
        return b"MP3", "Fish, test", None
    monkeypatch.setattr(stills, "narration", narration)
    rows = []

    async def fetch_one(sql, *args):
        rows.append(sql)
        return {"id": 7}
    monkeypatch.setattr(reel, "fetch_one", fetch_one)

    async def new_row(draft_id, provider):
        assert provider == "academy" and draft_id == 7
        return 90
    monkeypatch.setattr(render, "_new_row", new_row)

    async def duration(path):
        return 24.0
    monkeypatch.setattr(ffmpeg, "duration", duration)
    seen = {}

    def argv(pictures, frames, audio, out, *, label_file, font, scene_text=None, **kw):
        seen["pictures"], seen["texts"] = pictures, scene_text
        with open(out, "wb") as fh:
            fh.write(b"MP4")
        return ["ffmpeg"]
    monkeypatch.setattr(ffmpeg, "stills_argv", argv)

    async def run(a, **kw):
        # The frame composer writes its output path (the last argument).
        if a and a[-1].endswith(".png"):
            Path(a[-1]).write_bytes(b"PNG")
        return b""
    monkeypatch.setattr(ffmpeg, "run", run)

    async def finish(media_id, draft_id, backend, built, notes, *, captions=None):
        return ActionResult(True, f"media id {media_id}", evidence={"media_id": media_id},
                            rollback_token=str(media_id))
    monkeypatch.setattr(render, "_finish", finish)
    result = await reel.render_promo_reel("30-business-lessons")
    assert result.ok and result.evidence["slug"] == "30-business-lessons"
    assert result.evidence["caption"].endswith(AI_LABEL)
    # Every beat is a composed 1080x1920 frame (the cover letterboxed on
    # the first), one per beat.
    assert all(pic.endswith(".png") for pic in seen["pictures"])
    assert len(seen["pictures"]) == len(reel.script_for(CAT.product("30-business-lessons")))


async def test_autopublish_holds_and_cards_when_a_check_fails(monkeypatch):
    from atlas.kernel import approvals
    monkeypatch.setenv("ACADEMY_PROMO_AUTOPOST", "off")
    cards = []

    async def fresh_card(action, params, reason, *, dedup_key, **kw):
        cards.append((action, params["media_id"], reason, dedup_key))
        return 311
    monkeypatch.setattr(approvals, "fresh_card", fresh_card)
    posted = []
    monkeypatch.setattr(promo, "publish_promo", lambda *a: posted.append(a))
    caption = f"x\n\n{AI_LABEL}"
    result = await promo.autopublish_promo(90, caption, "30-business-lessons")
    assert result.ok and "card #311" in result.detail and posted == []
    reason = ("Held, not posted itself: automatic posting is off "
              "(ACADEMY_PROMO_AUTOPOST). The Academy reel for 30-business-lessons.")
    assert cards == [("clips.publish_promo", 90, reason, "clips.promo.90")]


async def test_autopublish_honours_the_daily_cap(monkeypatch):
    monkeypatch.setenv("ACADEMY_PROMO_AUTOPOST", "on")
    monkeypatch.setenv("CLIPS_PROMO_PER_DAY", "1")

    async def one():
        return 1
    monkeypatch.setattr(promo, "posted_today", one)
    why = await promo.autopublish_check(90, f"x\n\n{AI_LABEL}")
    assert why is not None and "already posted today" in why


async def test_the_daily_cap_is_kept_and_never_a_card(monkeypatch):
    """2026-10-12 (memory approval-policy): a reel held by the day's count
    is not posted and not carded -- a card only asked Miro to break the cap.
    Any other hold is still the card (above)."""
    from atlas.kernel import approvals

    async def capped(*a, **k):
        return f"2 Academy reel(s) already posted today {promo.CAP_HELD}"
    monkeypatch.setattr(promo, "autopublish_check", capped)

    async def no_card(*a, **k):
        raise AssertionError("a cap hold must not raise a card")
    monkeypatch.setattr(approvals, "fresh_card", no_card)
    posted = []
    monkeypatch.setattr(promo, "publish_promo", lambda *a, **k: posted.append(a))
    result = await promo.autopublish_promo(90, f"x\n\n{AI_LABEL}", "sell-with-ai")
    assert result.ok and "no card" in result.detail and posted == []


async def test_autopublish_leaves_the_record_to_publish_promo(monkeypatch):
    """publish_promo records the post itself, so a card approved later counts
    the same as an automatic post (2026-10-10: card #341 left no record)."""
    monkeypatch.setenv("ACADEMY_PROMO_AUTOPOST", "on")

    async def none(*a, **k):
        return None
    monkeypatch.setattr(promo, "autopublish_check", none)

    async def publish(media_id, caption, legs="", youtube_caption=""):
        return ActionResult(True, "render 90: posted", rollback_token="{}")
    monkeypatch.setattr(promo, "publish_promo", publish)
    written = []

    async def execute_sql(sql, *args):
        written.append(args)
    monkeypatch.setattr("atlas.kernel.state.execute_sql", execute_sql)
    result = await promo.autopublish_promo(90, f"x\n\n{AI_LABEL}", "sell-with-ai")
    assert result.ok and written == []


class Day:
    """schedule_runs for one job, in memory, as kernel.occurrence keeps it."""

    def __init__(self, monkeypatch) -> None:
        from atlas.kernel import occurrence
        self.rows: dict[tuple[str, str], str] = {}

        async def claim(job, window, scheduled_for=None):
            if self.rows.get((job, window)) in ("running", "done"):
                return False
            self.rows[(job, window)] = "running"
            return True

        async def done(job, window):
            self.rows[(job, window)] = "done"

        async def release(job, window):
            self.rows[(job, window)] = "failed"
        monkeypatch.setattr(occurrence, "claim", claim)
        monkeypatch.setattr(occurrence, "done", done)
        monkeypatch.setattr(occurrence, "release", release)


@pytest.fixture(autouse=True)
def _old_design_allowed(monkeypatch):
    """The daily reel tests below exercise the Fish promo Short, which only
    runs with the NotebookLM fallback switch on (Miro, 2026-10-19)."""
    monkeypatch.setenv("ACADEMY_NOTEBOOKLM_FALLBACK", "true")


@pytest.mark.parametrize("slug", ["concept:cloud-or-local", "lesson:sell-with-ai:3",
                                  "ai-for-every-age"])
async def test_the_promo_reel_action_itself_renders_nothing_while_the_fallback_is_off(
        monkeypatch, slug):
    """academy.render_promo_reel (concept, lesson and product Shorts, all
    ATLAS-drawn and Fish-voiced) refuses before anything is read or drawn,
    whoever calls it (Miro, 2026-10-19: NotebookLM videos only)."""
    from atlas.modules.academy import concept_short, lesson_short
    monkeypatch.delenv("ACADEMY_NOTEBOOKLM_FALLBACK", raising=False)

    def never(*a, **k):
        raise AssertionError("went on to render")
    monkeypatch.setattr(concept_short, "find", never)
    monkeypatch.setattr(lesson_short, "find", never)
    monkeypatch.setattr(reel, "load", never)
    for run in (reel.render_promo_reel, reel.render_promo_reel_dry):
        got = await run(slug)
        assert not got.ok and "ACADEMY_NOTEBOOKLM_FALLBACK" in got.detail


@pytest.mark.parametrize("kind", ["lesson", "product"])
async def test_no_fish_promo_short_while_the_notebooklm_fallback_is_off(monkeypatch, kind):
    """Miro, 2026-10-19: NotebookLM only -- the daily Fish promo Short stops
    too. Nothing is claimed, rendered or posted."""
    from atlas.kernel import executor, occurrence
    monkeypatch.delenv("ACADEMY_NOTEBOOKLM_FALLBACK", raising=False)
    monkeypatch.setenv("ACADEMY_REEL_KIND", kind)
    calls = []

    async def execute(req):
        calls.append(req.action)
        return ActionResult(True, "posted")
    monkeypatch.setattr(executor, "execute", execute)

    async def claim(*a, **k):
        calls.append("claim")
        return True
    monkeypatch.setattr(occurrence, "claim", claim)
    line = await reel.daily()
    assert calls == []
    assert "NotebookLM" in line


async def test_daily_renders_the_next_product_then_autoposts(monkeypatch):
    from atlas.kernel import executor
    day = Day(monkeypatch)
    monkeypatch.setenv("ACADEMY_REEL_KIND", "product")   # lessons are the default

    async def no():
        return False
    monkeypatch.setattr(reel, "posted_today", no)

    async def shown():
        return {}
    monkeypatch.setattr(reel, "last_shown", shown)
    calls = []

    async def execute(req):
        calls.append((req.action, req.params))
        if req.action == "academy.render_promo_reel":
            return ActionResult(True, "ok", evidence={"media_id": 91, "caption": "c"})
        return ActionResult(True, "posted everywhere")
    monkeypatch.setattr(executor, "execute", execute)
    line = await reel.daily()
    first = reel.sellable(CAT)[0].slug
    assert calls == [("academy.render_promo_reel", {"slug": first}),
                     ("clips.autopublish_promo", {"media_id": 91, "caption": "c", "slug": first})]
    assert line == f"{first}: posted everywhere"
    assert list(day.rows.values()) == ["done"]


async def test_a_restart_does_not_render_the_day_again(monkeypatch):
    """2026-10-10: three deploys reset the scheduler's in-memory date and
    "AI for Every Age" was rendered four times. The day is claimed in
    Postgres; a held card (the post found nothing to record) still settles it."""
    monkeypatch.setenv("ACADEMY_REEL_KIND", "product")   # lessons are the default
    from atlas.kernel import executor, scheduler
    Day(monkeypatch)

    async def no():
        return False
    monkeypatch.setattr(reel, "posted_today", no)

    async def shown():
        return {}
    monkeypatch.setattr(reel, "last_shown", shown)
    renders = []

    async def execute(req):
        if req.action == "academy.render_promo_reel":
            renders.append(req.params["slug"])
            return ActionResult(True, "ok", evidence={"media_id": 91, "caption": "c"})
        return ActionResult(True, "held (cap); card #341 raised")
    monkeypatch.setattr(executor, "execute", execute)
    assert "card #341" in await reel.daily()
    monkeypatch.setattr(scheduler, "_academy_reel_slots", set())        # the restart
    assert await reel.daily() == "this slot's reel is already claimed"
    assert len(renders) == 1


async def test_a_failed_render_releases_the_day_for_a_retry(monkeypatch):
    monkeypatch.setenv("ACADEMY_REEL_KIND", "product")   # lessons are the default
    from atlas.kernel import executor
    day = Day(monkeypatch)

    async def no():
        return False
    monkeypatch.setattr(reel, "posted_today", no)

    async def shown():
        return {}
    monkeypatch.setattr(reel, "last_shown", shown)

    async def execute(req):
        return ActionResult(False, "ffmpeg (audio): ffprobe passed 30s and was stopped")
    monkeypatch.setattr(executor, "execute", execute)
    assert (await reel.daily()).startswith("render refused")
    assert list(day.rows.values()) == ["failed"]


async def test_daily_skips_a_product_shown_inside_the_window(monkeypatch):
    monkeypatch.setenv("ACADEMY_REEL_KIND", "product")   # lessons are the default
    from atlas.kernel import executor
    Day(monkeypatch)
    monkeypatch.setenv("ACADEMY_PROMO_REPOST_DAYS", "5")

    async def no():
        return False
    monkeypatch.setattr(reel, "posted_today", no)
    recent = datetime.now(UTC) - timedelta(days=2)

    async def shown():
        return {p.slug: recent for p in reel.sellable(CAT)}
    monkeypatch.setattr(reel, "last_shown", shown)
    called = []

    async def execute(req):
        called.append(req)
        return ActionResult(True, "x")
    monkeypatch.setattr(executor, "execute", execute)
    assert "nothing new to show" in await reel.daily() and called == []


async def test_daily_does_nothing_twice_a_day(monkeypatch):
    Day(monkeypatch)

    async def yes():
        return True
    monkeypatch.setattr(reel, "posted_today", yes)
    assert await reel.daily() == "already posted today"


@pytest.mark.parametrize(("env", "on"), [("", True), ("off", False), ("0", False)])
def test_the_lane_switch(monkeypatch, env, on):
    monkeypatch.setenv("ACADEMY_PROMO_REELS", env)
    assert reel.enabled() is on


# ------------------------------------------------------------ the editions

def test_every_catalog_product_has_its_own_edition_and_they_all_differ():
    from atlas.modules.academy import reel_design
    seen = {}
    for p in CAT.products:
        ed = reel_design.edition_for(p.slug)
        assert ed.name not in seen, (p.slug, ed.name, seen[ed.name]) if ed.name in seen else ""
        seen[ed.name] = p.slug
    assert len(seen) == len(CAT.products)
    # An unknown slug still gets one, and the same one every day.
    assert reel_design.edition_for("x-new") is reel_design.edition_for("x-new")


def test_a_page_carries_the_tag_the_product_name_and_only_catalog_words():
    from atlas.modules.academy import reel_design
    p = CAT.product("30-business-lessons")
    ed = reel_design.edition_for(p.slug)
    for kind, shown in zip(reel.beat_kinds(reel.script_for(p)),
                           [x for x, _ in reel.script_for(p)], strict=True):
        doc = reel_design.page(ed, kind, shown, p.title, "", 0)
        assert reel_design.TAG in doc and p.title in doc
        assert "<script" not in doc


async def test_the_hook_follows_how_often_the_product_was_shown(monkeypatch):
    async def count(sql, *args):
        assert args == ("safe-ai-operator",)
        return {"n": 4}
    monkeypatch.setattr(reel, "fetch_one", count)
    assert await reel.times_shown("safe-ai-operator") == 4

    async def broken(sql, *args):
        raise OSError("db down")
    monkeypatch.setattr(reel, "fetch_one", broken)
    assert await reel.times_shown("safe-ai-operator") == reel.variant_for()


def test_the_day_number_picks_the_hook():
    assert reel.variant_for(datetime(2026, 1, 2, tzinfo=UTC)) == 2
    p = CAT.product("safe-ai-operator")
    hooks = promo_copy.for_slug(p.slug).hooks
    assert [reel.script_for(p, d)[0][0] for d in range(len(hooks))] == list(hooks)


def test_the_hook_frame_is_readable_from_frame_zero_and_the_end_shows_the_store():
    from atlas.modules.academy import reel_design
    p = CAT.product("claude-obsidian-vault-kit")
    ed = reel_design.edition_for(p.slug)
    doc = reel_design.page(ed, "hook", "Let Claude run your week from Obsidian.",
                           p.title, "", 0)
    # `both` fill from a visible state: the first frame is the hook, never blank.
    assert 'class="hk"' in doc and "animation:slam" in doc and "both" in doc
    assert "Obsidian." in doc and "<script" not in doc
    end = reel_design.page(ed, "title", p.title, p.title, "data:image/png;base64,x", 0)
    assert "agency.storefront.test" in end and "data:image/png;base64,x" in end
    for name in {e.name for e in reel_design.EDITIONS.values()}:
        assert name in reel_design.HOOK_COLORS


def test_the_assembly_takes_frames_and_the_padded_audio_list(tmp_path):
    from atlas.modules.academy import reel_design
    argv = reel_design.assemble_argv("/f", "/a.txt", "/o.mp4")
    assert argv[-1] == "/o.mp4" and "%05d.png" in " ".join(argv) and "-shortest" in argv
    pad = reel_design.pad_argv("/b.mp3", 3.25, "/b.wav")
    assert "atrim=0:3.250" in " ".join(pad) and pad[-1] == "/b.wav"


async def test_daily_renders_the_next_lesson_by_default(monkeypatch):
    Day(monkeypatch)
    from atlas.kernel import executor
    monkeypatch.delenv("ACADEMY_REEL_KIND", raising=False)

    async def no():
        return False
    monkeypatch.setattr(reel, "posted_today", no)

    async def shown():
        return {}
    monkeypatch.setattr(reel, "last_shown", shown)
    monkeypatch.setattr(reel, "next_lesson", lambda s: "lesson:sell/1.2")
    calls = []

    async def execute(req):
        calls.append((req.action, req.params))
        if req.action == "academy.render_promo_reel":
            return ActionResult(True, "ok", evidence={"media_id": 92, "caption": "c"})
        return ActionResult(True, "posted")
    monkeypatch.setattr(executor, "execute", execute)
    assert await reel.daily() == "lesson:sell/1.2: posted"
    assert calls == [("academy.render_promo_reel", {"slug": "lesson:sell/1.2"}),
                     ("clips.autopublish_promo",
                      {"media_id": 92, "caption": "c", "slug": "lesson:sell/1.2"})]


async def test_a_lesson_with_no_showable_part_hands_the_slot_to_the_next(monkeypatch):
    """2026-10-12, 7 pm: lesson 1.1 refused at render ("no part of this
    lesson is clean to show") and the slot posted nothing. It now moves on
    to the next lesson in rotation."""
    from atlas.kernel import executor
    Day(monkeypatch)
    monkeypatch.setenv("ACADEMY_REEL_KIND", "lesson")
    monkeypatch.setenv("ACADEMY_CONCEPT_SHORTS", "off")

    async def no():
        return False
    monkeypatch.setattr(reel, "posted_today", no)

    async def shown():
        return {}
    monkeypatch.setattr(reel, "last_shown", shown)
    monkeypatch.setattr(reel, "lesson_candidates",
                        lambda _s: ["lesson:operator/1.1", "lesson:sell/1.1", "lesson:adults/1.1"])
    renders = []

    async def execute(req):
        if req.action == "academy.render_promo_reel":
            renders.append(req.params["slug"])
            if req.params["slug"] == "lesson:operator/1.1":
                return ActionResult(False, f"lesson:operator/1.1: {reel.UNSHOWABLE}")
            return ActionResult(True, "ok", evidence={"media_id": 92, "caption": "c"})
        return ActionResult(True, "posted everywhere")
    monkeypatch.setattr(executor, "execute", execute)
    line = await reel.daily()
    assert renders == ["lesson:operator/1.1", "lesson:sell/1.1"]
    assert line == "lesson:sell/1.1: posted everywhere"


def test_a_product_posted_in_the_last_two_days_skips_tiktok():
    """TikTok flagged the Automation Pack's concept Shorts as spam on
    2026-10-12 and 10-13, after the same product within a day. A repeat
    inside 48 h goes to YouTube only."""
    from datetime import UTC, datetime, timedelta
    now = datetime(2026, 10, 14, 16, tzinfo=UTC)
    shown = {"concept:reply-ready": now - timedelta(hours=24),
             "lesson:operator/0.1": now - timedelta(days=3)}
    assert reel.product_of("concept:missed-call") == "local-business-automation-pack"
    assert reel.product_of("lesson:sell/1.2") == "sell-with-ai"
    assert reel.legs_for("concept:missed-call", shown, now) == "youtube"
    assert reel.legs_for("concept:boundary", shown, now) == ""        # operator, 3 days ago
    assert reel.legs_for("lesson:sell/1.2", shown, now) == ""


def test_concept_shorts_alternate_products():
    from datetime import UTC, datetime, timedelta

    from atlas.modules.academy import concept_short as cs
    now = datetime(2026, 10, 14, 16, tzinfo=UTC)
    shown = {"concept:reply-ready": now - timedelta(days=2),
             "concept:missed-call": now - timedelta(days=1)}
    nxt = cs.find(cs.next_concept(shown))
    assert nxt.product != "local-business-automation-pack"           # not the Pack again
