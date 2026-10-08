"""The daily Academy reel for the clips accounts (academy/reel.py +
clips.autopublish_promo): Miro, 2026-10-06, "make continuous reels about
ATLAS Academy". Catalog words only, one a day, gated in code."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from atlas.kernel.contracts import ActionResult, Tier
from atlas.modules.academy import reel
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
    assert set(auto.params) == {"media_id", "caption", "slug"}
    assert callable(scheduler._maybe_academy_reel)


def test_every_sellable_product_has_a_script_in_catalog_words():
    products = reel.sellable(CAT)
    assert products, "nothing sellable"
    catalog_text = " ".join(
        " ".join([p.title, p.summary, p.kind] + [v for _, v in p.label]) for p in CAT.products)
    for p in products:
        beats = reel.script_for(p)
        assert beats[0][0] == p.title and beats[-1][0].startswith(reel.STORE)
        words = sum(len(said.split()) for _, said in beats)
        assert 30 <= words <= 130, (p.slug, words)
        for shown, said in beats[2:-1]:
            # A fact card is the label row's value, whole or its first clause.
            assert shown in catalog_text, (p.slug, shown)
            assert said.rstrip(".") in catalog_text
        said = " ".join(s for _, s in beats).lower()
        for word in ("earn", "income", "profit", "guarantee", "reviews", "five stars"):
            assert word not in said, (p.slug, word)


def test_the_caption_carries_the_label_the_store_and_a_live_price_only():
    p = CAT.product("30-business-lessons")
    caption = reel.caption_for(p)
    assert caption.endswith(AI_LABEL) and reel.STORE in caption and "From $9." in caption
    # The first line is the product's name alone: the YouTube Short's title
    # (card 2026-10-07: title + summary on one line was 140 characters).
    assert caption.split("\n")[0] == p.title
    assert promo.youtube_title(caption) == p.title
    assert promo.vet({"media_id": 1, "caption": caption}) is None
    from atlas.modules.content import guard
    assert guard.refusal(caption) is None


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


async def test_autopublish_posts_and_records_the_day(monkeypatch):
    monkeypatch.setenv("ACADEMY_PROMO_AUTOPOST", "on")

    async def none(*a, **k):
        return None
    monkeypatch.setattr(promo, "autopublish_check", none)

    async def publish(media_id, caption, legs=""):
        return ActionResult(True, "render 90: posted", rollback_token="{}")
    monkeypatch.setattr(promo, "publish_promo", publish)
    written = []

    async def execute_sql(sql, *args):
        written.append(args)
    monkeypatch.setattr("atlas.kernel.state.execute_sql", execute_sql)
    result = await promo.autopublish_promo(90, f"x\n\n{AI_LABEL}", "sell-with-ai")
    assert result.ok and written == [("sell-with-ai", 90, "render 90: posted")]


async def test_daily_renders_the_next_product_then_autoposts(monkeypatch):
    from atlas.kernel import executor

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


async def test_daily_does_nothing_twice_a_day(monkeypatch):
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


def test_the_day_variant_swaps_title_and_hook():
    from datetime import date
    even = reel.variant_for(datetime(2026, 1, 2, tzinfo=UTC))   # day 2
    odd = reel.variant_for(datetime(2026, 1, 1, tzinfo=UTC))    # day 1
    assert (even, odd) == (0, 1)
    assert date  # the import is used by the type above


def test_the_assembly_takes_frames_and_the_padded_audio_list(tmp_path):
    from atlas.modules.academy import reel_design
    argv = reel_design.assemble_argv("/f", "/a.txt", "/o.mp4")
    assert argv[-1] == "/o.mp4" and "%05d.png" in " ".join(argv) and "-shortest" in argv
    pad = reel_design.pad_argv("/b.mp3", 3.25, "/b.wav")
    assert "atrim=0:3.250" in " ".join(pad) and pad[-1] == "/b.wav"
