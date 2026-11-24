"""clips.publish_promo: the Academy store's promo reel on the clips accounts
(the owner, 2026-10-05, "post on clips too"), explicit at every layer and never
a hole in the campaign-clip rule."""
from __future__ import annotations

import os

import pytest

from atlas.kernel.contracts import Tier
from atlas.modules import instagram
from atlas.modules.clips import promo
from atlas.modules.content import postiz

CAPTION = "My AI runs my businesses while I sleep.\n\nagency.example.com\n\nMade with AI"


@pytest.fixture(autouse=True)
def _old_design_allowed(monkeypatch):
    """The posting tests below drive ATLAS-rendered promo Shorts, which post
    only with the NotebookLM fallback on (the owner, 2026-10-19). The NotebookLM-
    only gate has its own tests at the end of this file."""
    monkeypatch.setenv("ACADEMY_NOTEBOOKLM_FALLBACK", "true")


def test_the_action_is_tier_ask_by_card_with_rollback_and_vet():
    from atlas.modules.clips import ClipsModule
    spec = next(a for a in ClipsModule().actions() if a.name == "clips.publish_promo")
    assert spec.tier is Tier.ASK and spec.card_only and spec.rollback is not None
    assert spec.vet is promo.vet and spec.max_per_hour == 1
    assert set(spec.params) == {"media_id", "caption", "legs", "youtube_caption"}


def test_vet_wants_the_ai_label_and_no_campaign_disclosure():
    assert promo.vet({"media_id": 1, "caption": CAPTION}) is None
    assert "Made with AI" in promo.vet({"media_id": 1, "caption": "hi"})
    assert "#Ad" in promo.vet({"media_id": 1, "caption": "hi #Ad\n\nMade with AI"})
    assert "media_id" in promo.vet({"media_id": "x", "caption": CAPTION})


def test_the_youtube_title_is_the_first_sentence_whole():
    assert promo.youtube_title(CAPTION) == "My AI runs my businesses while I sleep."
    # One paragraph with no line break (card 304, 2026-10-06): still the
    # first sentence, not the whole paragraph.
    one = "My AI runs my businesses while I sleep. It can't touch my money. #ai\n\nMade with AI"
    assert promo.youtube_title(one) == "My AI runs my businesses while I sleep."
    assert promo.youtube_title("no full stop here\n\nMade with AI") == "no full stop here"


def test_legs_default_to_all_three_and_a_subset_gets_its_own_claim():
    # the owner, 2026-10-11: YouTube and TikTok only unless a run names Instagram.
    assert promo.legs_of("") == ("tiktok", "youtube") == promo.legs_of(None)
    assert promo.legs_of("instagram,tiktok,youtube") == promo.LEGS
    assert promo.legs_of("youtube") == ("youtube",)
    assert promo.legs_of("tiktok, instagram") == ("instagram", "tiktok")
    assert "unknown legs" in promo.vet({"media_id": 1, "caption": CAPTION, "legs": "x"})
    assert promo.vet({"media_id": 1, "caption": CAPTION, "legs": "youtube"}) is None


# ---------------------------------------------------------------- Instagram

def test_the_clips_account_lists_store_promo_beside_campaign_clips():
    assert instagram.ACCOUNT_KINDS["clips"] == {"campaign_clip", "store_promo"}
    assert instagram._kind_refusal("clips", "store_promo") is None
    assert instagram._kind_refusal("stories", "store_promo")


def test_a_store_promo_posts_only_from_atlass_own_media_link(monkeypatch):
    monkeypatch.setenv("ATLAS_HOST", "atlas.example")
    ok = "https://atlas.example/media/abc"
    assert instagram._source_refusal("clips", "reel", ok, "store_promo") is None
    # A clip link is a campaign clip's, never a promo's; elsewhere is refused.
    for bad in ("https://atlas.example/media/clip.abc", "https://cdn.other/x.mp4"):
        assert "clips.publish_promo" in instagram._source_refusal(
            "clips", "reel", bad, "store_promo")
    assert "Reels only" in instagram._source_refusal("clips", "image", ok, "store_promo")
    # The campaign-clip rule is untouched: a plain media link is still refused.
    assert "clips.publish_clip" in instagram._source_refusal("clips", "reel", ok,
                                                             "campaign_clip")


def test_a_store_promo_carries_the_ai_label_not_a_disclosure():
    assert instagram._label_refusal("clips", CAPTION, "store_promo") is None
    refused = instagram._label_refusal("clips", "hi there", "store_promo")
    assert refused is not None and "Made with AI" in refused.detail
    # A campaign clip still needs its #Ad, and the AI label does not stand in.
    assert instagram._label_refusal("clips", CAPTION, "campaign_clip") is not None
    assert instagram._label_refusal("clips", CAPTION) is not None


def test_vet_publish_passes_the_content_kind_through(monkeypatch):
    monkeypatch.setenv("ATLAS_HOST", "atlas.example")
    params = {"account": "clips", "kind": "reel", "content_kind": "store_promo",
              "image_url": "https://atlas.example/media/abc", "caption": CAPTION}
    assert instagram.vet_publish(params) is None
    assert instagram.vet_publish({**params, "caption": "no label"})


# ------------------------------------------------------------------- Postiz

def _render(tmp_path, monkeypatch):
    root = tmp_path / "content"
    root.mkdir()
    monkeypatch.setattr("atlas.modules.content.DATA_DIR", str(root))
    path = root / "90.mp4"
    path.write_bytes(b"PROMO")
    return root, path


def test_the_promo_file_floor_is_a_plain_mp4_in_the_content_dir(tmp_path, monkeypatch):
    root, path = _render(tmp_path, monkeypatch)
    assert postiz._promo_file(str(path))
    (root / "deeper").mkdir()
    (root / "deeper" / "x.mp4").write_bytes(b"x")
    assert not postiz._promo_file(str(root / "deeper" / "x.mp4"))
    other = tmp_path / "elsewhere.mp4"
    other.write_bytes(b"x")
    assert not postiz._promo_file(str(other))
    link = root / "91.mp4"
    os.symlink(path, link)
    assert not postiz._promo_file(str(link))
    assert not postiz._promo_file(str(root / "92.mov"))


async def test_a_campaign_clip_doorway_still_refuses_a_render(tmp_path, monkeypatch):
    _, path = _render(tmp_path, monkeypatch)
    monkeypatch.setenv("POSTIZ_API_KEY", "k")
    monkeypatch.setenv("POSTIZ_TIKTOK_CLIPS_INTEGRATION", "atlasclips12")
    monkeypatch.setenv("CLIPS_DATA_DIR", str(tmp_path / "clips"))
    status, said, *_ = await postiz.post_tiktok_clip(str(path), CAPTION)
    assert status == postiz.REFUSED and "clips lane cut" in said


async def test_the_promo_doorway_refuses_a_campaign_clip(tmp_path, monkeypatch):
    _render(tmp_path, monkeypatch)
    monkeypatch.setenv("POSTIZ_API_KEY", "k")
    monkeypatch.setenv("POSTIZ_TIKTOK_CLIPS_INTEGRATION", "atlasclips12")
    out = tmp_path / "clips" / "atlus-persona-metaphor" / "out"
    out.mkdir(parents=True)
    clip = out / "1-abcdef012345-1.mp4"
    clip.write_bytes(b"CLIP")
    status, said, *_ = await postiz.post_tiktok_promo(str(clip), CAPTION)
    assert status == postiz.REFUSED and "clips.publish_promo" in said


async def test_a_promo_on_the_clips_tiktok_is_ai_made_not_brand_content(
        tmp_path, monkeypatch):
    from tests.test_reels_postiz import KEY, _post_body, _two_accounts
    _, path = _render(tmp_path, monkeypatch)
    monkeypatch.setenv("POSTIZ_API_KEY", KEY)
    monkeypatch.delenv("POSTIZ_API_URL", raising=False)
    monkeypatch.setenv("POSTIZ_TIKTOK_CLIPS_INTEGRATION", "atlasclips12")
    seen = _two_accounts(monkeypatch)
    status, _, post_id, _ = await postiz.post_tiktok_promo(str(path), CAPTION)
    assert status == postiz.ID_PENDING and post_id == "pz-9"
    body = _post_body(seen)
    assert body["posts"][0]["integration"] == {"id": "tt-1"}     # the clips account
    settings = body["posts"][0]["settings"]
    assert settings["video_made_with_ai"] is True
    assert settings["brand_content_toggle"] is False
    assert settings["privacy_level"] == "PUBLIC_TO_EVERYONE"
    assert body["posts"][0]["value"][0]["content"] == CAPTION


async def test_a_long_youtube_title_is_refused_never_cut(tmp_path, monkeypatch):
    _, path = _render(tmp_path, monkeypatch)
    monkeypatch.setenv("POSTIZ_API_KEY", "k")
    monkeypatch.setenv("POSTIZ_YOUTUBE_CLIPS_INTEGRATION", "AtlasClips (@example_handle)")
    status, said, *_ = await postiz.post_youtube_promo(str(path), "x" * 101, CAPTION, [])
    assert status == postiz.REFUSED and "2-100" in said


# ------------------------------------------------------------------ the run

class Claims:
    """academy_promo_claims and academy_promo_posts, in memory: the claim
    is refused while another render of the product holds the leg."""

    def __init__(self, slug: str = "ai-for-every-age") -> None:
        self.slug, self.held, self.posts, self.log = slug, {}, [], []

    def install(self, monkeypatch) -> Claims:
        async def slug_of(media_id):
            return self.slug

        async def recently_posted(slug, media_id):
            return next((f"{slug} was already posted" for s, m, _ in self.posts
                         if s == slug and m != media_id), None)

        async def claim_leg(slug, leg, media_id):
            held = self.held.get((slug, leg))
            if held is not None and held[1] != "released":
                self.log.append(("refused", leg, media_id))
                return False
            self.held[(slug, leg)] = (media_id, "sending")
            self.log.append(("claimed", leg, media_id))
            return True

        async def release_leg(slug, leg, media_id):
            self.held[(slug, leg)] = (media_id, "released")

        async def settle_leg(slug, leg, media_id, detail):
            self.held[(slug, leg)] = (media_id, "sent")

        async def record(slug, media_id, detail):
            self.posts.append((slug, media_id, detail))
        for name, fn in (("slug_of", slug_of), ("recently_posted", recently_posted),
                         ("claim_leg", claim_leg), ("release_leg", release_leg),
                         ("settle_leg", settle_leg), ("_record", record)):
            monkeypatch.setattr(promo, name, fn)
        return self

async def test_publish_promo_posts_each_leg_once_and_records_the_tokens(
        tmp_path, monkeypatch):
    _, path = _render(tmp_path, monkeypatch)
    monkeypatch.setenv("ATLAS_HOST", "atlas.example")
    row = {"id": 90, "status": "done", "path": str(path), "media_token": "tok",
           "media_token_expires_at": None}
    from atlas.modules.content import crosspost

    async def media(_id):
        return row, None

    async def public_url(_row):
        return "https://atlas.example/media/tok", None
    monkeypatch.setattr(crosspost, "_media", media)
    monkeypatch.setattr(crosspost, "_public_url", public_url)
    from atlas.kernel import idempotency
    claims = []

    async def claim(action, key):
        claims.append((action, key))
        return idempotency.CLAIMED

    async def settle(action, key, detail=""):
        claims.append(("settled", key))
    monkeypatch.setattr(idempotency, "claim", claim)
    monkeypatch.setattr(idempotency, "settle", settle)
    legs = []

    async def ig(media_id, caption, url):
        legs.append(("instagram", caption, url))
        return promo.POSTED, "media 1", "clips:1"

    async def tt(p, caption):
        legs.append(("tiktok", p, caption))
        return postiz.POSTED, "Postiz post pz-1", "pz-1", None

    async def yt(p, title, description, tags):
        legs.append(("youtube", title, tags))
        return postiz.ID_PENDING, "Postiz post pz-2", "pz-2", None
    monkeypatch.setattr(promo, "_instagram", ig)
    monkeypatch.setattr(postiz, "post_tiktok_promo", tt)
    monkeypatch.setattr(postiz, "post_youtube_promo", yt)
    store = Claims().install(monkeypatch)
    result = await promo.publish_promo(90, CAPTION)
    assert result.ok and claims[0] == ("clips.publish_promo", "promo:90")
    assert store.posts and store.posts[0][:2] == ("ai-for-every-age", 90)
    assert all(v == (90, "sent") for v in store.held.values()) and len(store.held) == 2
    assert ("settled", "promo:90") in claims
    # The default run: TikTok and YouTube, no Instagram (the owner, 2026-10-11).
    assert [leg[0] for leg in legs] == ["tiktok", "youtube"]
    assert legs[1][1] == "My AI runs my businesses while I sleep."
    assert result.rollback_token == '{"tiktok": "pz-1", "youtube": "pz-2"}'
    assert ("ai-for-every-age", "instagram") not in store.held


async def test_a_youtube_only_run_posts_one_leg_under_its_own_key(tmp_path, monkeypatch):
    _, path = _render(tmp_path, monkeypatch)
    monkeypatch.setenv("ATLAS_HOST", "atlas.example")
    from atlas.modules.content import crosspost

    async def media(_id):
        return {"id": 90, "status": "done", "path": str(path)}, None

    async def public_url(_row):
        return "https://atlas.example/media/tok", None
    monkeypatch.setattr(crosspost, "_media", media)
    monkeypatch.setattr(crosspost, "_public_url", public_url)
    from atlas.kernel import idempotency
    keys = []

    async def claim(action, key):
        keys.append(key)
        return idempotency.CLAIMED

    async def settle(action, key, detail=""):
        pass
    monkeypatch.setattr(idempotency, "claim", claim)
    monkeypatch.setattr(idempotency, "settle", settle)
    touched = []

    async def no_ig(*a):
        touched.append("instagram")
        return promo.POSTED, "x", "clips:1"

    async def no_tt(*a):
        touched.append("tiktok")
        return postiz.POSTED, "x", "pz-1", None

    async def yt(p, title, description, tags):
        touched.append("youtube")
        return postiz.POSTED, "Postiz post pz-2, YouTube id abc", "pz-2", None
    monkeypatch.setattr(promo, "_instagram", no_ig)
    monkeypatch.setattr(postiz, "post_tiktok_promo", no_tt)
    monkeypatch.setattr(postiz, "post_youtube_promo", yt)
    Claims().install(monkeypatch)
    result = await promo.publish_promo(90, CAPTION, legs="youtube")
    assert result.ok and touched == ["youtube"] and keys == ["promo:90:youtube"]
    assert result.rollback_token == '{"youtube": "pz-2"}'


async def test_a_second_run_for_the_same_render_posts_nothing(tmp_path, monkeypatch):
    _, path = _render(tmp_path, monkeypatch)
    monkeypatch.setenv("ATLAS_HOST", "atlas.example")
    from atlas.modules.content import crosspost

    async def media(_id):
        return {"id": 90, "status": "done", "path": str(path)}, None
    monkeypatch.setattr(crosspost, "_media", media)
    from atlas.kernel import idempotency

    async def claim(action, key):
        return idempotency.SENT
    monkeypatch.setattr(idempotency, "claim", claim)
    posted = []
    monkeypatch.setattr(postiz, "post_tiktok_promo",
                        lambda *a: posted.append(a))
    result = await promo.publish_promo(90, CAPTION)
    assert not result.ok and posted == []


async def test_the_instagram_leg_runs_as_the_clips_account_under_this_parent(
        monkeypatch):
    from atlas.kernel import legs
    from atlas.modules import instagram as ig
    monkeypatch.setenv("INSTAGRAM_CLIPS_ACCOUNT_ID", "1")
    monkeypatch.setenv("INSTAGRAM_CLIPS_TOKEN", "t")
    gated = []

    async def gate(leg, parent, *, account=None, **ctx):
        gated.append((leg, parent, account))

    async def nothing(*a, **k):
        return None
    monkeypatch.setattr(legs, "gate", gate)
    monkeypatch.setattr(legs, "start", nothing)
    monkeypatch.setattr(legs, "finish", nothing)
    calls = []

    async def publish(caption, url, kind="image", account="main", *, content_kind):
        calls.append((account, kind, content_kind))
        from atlas.kernel.contracts import ActionResult
        return ActionResult(True, "media 7", rollback_token="clips:7")
    monkeypatch.setattr(ig, "publish", publish)
    status, _, token = await promo._instagram(90, CAPTION, "https://atlas.example/media/t")
    assert status == promo.POSTED and token == "clips:7"
    assert gated == [("instagram.publish", "clips.publish_promo", "clips")]
    assert calls == [("clips", "reel", "store_promo")]


@pytest.mark.parametrize("missing", ["INSTAGRAM_CLIPS_ACCOUNT_ID", "INSTAGRAM_CLIPS_TOKEN"])
async def test_an_unset_clips_instagram_is_not_configured(monkeypatch, missing):
    monkeypatch.setenv("INSTAGRAM_CLIPS_ACCOUNT_ID", "1")
    monkeypatch.setenv("INSTAGRAM_CLIPS_TOKEN", "t")
    monkeypatch.delenv(missing)
    status, said, _ = await promo._instagram(90, CAPTION, "https://x/m")
    assert status == promo.NOT_CONFIGURED and "clips" in said


async def test_the_promo_check_probes_the_file_without_the_still_frame_rule(monkeypatch):
    """The editions are composed frames by the owner's choice; the stories lane's
    motion rule would hold every one of them (card #312, 2026-10-07)."""
    from atlas.modules.content import quality
    monkeypatch.setenv("ACADEMY_PROMO_AUTOPOST", "on")

    async def none():
        return 0
    monkeypatch.setattr(promo, "posted_today", none)
    seen = {}

    async def check_media(media_id, caption, *, series, seconds, motion=True):
        seen.update(media_id=media_id, series=series, seconds=seconds, motion=motion)
    monkeypatch.setattr(quality, "check_media", check_media)
    assert await promo.autopublish_check(90, CAPTION) is None
    assert seen == {"media_id": 90, "series": False, "seconds": promo.SECONDS, "motion": False}


# ------------------------------------------- once per product and platform
#
# 2026-10-10: "AI for Every Age" went out twice on every platform. Card #341
# (render 348) was approved at 17:13 while a restart's re-run rendered 356
# and auto-posted it at 18:03. Each render had its own `promo:<id>` key.

def _two_renders(tmp_path, monkeypatch):
    root, _ = _render(tmp_path, monkeypatch)
    for mid in (348, 356):
        (root / f"{mid}.mp4").write_bytes(b"PROMO")
    monkeypatch.setenv("ATLAS_HOST", "atlas.example")
    from atlas.kernel import idempotency
    from atlas.modules.content import crosspost

    async def media(media_id):
        return {"id": media_id, "status": "done", "path": str(root / f"{media_id}.mp4")}, None

    async def public_url(row):
        return f"https://atlas.example/media/{row['id']}", None

    async def claim(action, key):
        return idempotency.CLAIMED

    async def nothing(*a, **k):
        return None
    monkeypatch.setattr(crosspost, "_media", media)
    monkeypatch.setattr(crosspost, "_public_url", public_url)
    monkeypatch.setattr(idempotency, "claim", claim)
    for name in ("settle", "finish", "release"):
        monkeypatch.setattr(idempotency, name, nothing)
    sent = []

    async def ig(media_id, caption, url):
        sent.append(("instagram", media_id))
        return promo.POSTED, "media", f"clips:{media_id}"

    async def tt(path, caption):
        sent.append(("tiktok", path))
        return postiz.ID_PENDING, "pz", f"tt-{path}", None

    async def yt(path, title, description, tags):
        sent.append(("youtube", path))
        return postiz.ID_PENDING, "pz", f"yt-{path}", None
    monkeypatch.setattr(promo, "_instagram", ig)
    monkeypatch.setattr(postiz, "post_tiktok_promo", tt)
    monkeypatch.setattr(postiz, "post_youtube_promo", yt)
    return sent


async def test_two_renders_of_one_product_post_once_per_platform(tmp_path, monkeypatch):
    import asyncio
    sent = _two_renders(tmp_path, monkeypatch)
    store = Claims().install(monkeypatch)

    async def not_yet(slug, media_id):
        # In the real race neither run has written academy_promo_posts yet
        # (it is written after the legs), so only the leg claim stands.
        return None
    monkeypatch.setattr(promo, "recently_posted", not_yet)
    first, second = await asyncio.gather(promo.publish_promo(348, CAPTION),
                                         promo.publish_promo(356, CAPTION))
    assert sorted(leg for leg, _ in sent) == ["tiktok", "youtube"]
    assert [r.ok for r in (first, second)].count(True) == 1
    loser = second if first.ok else first
    assert "already posted" in loser.detail
    assert len(store.posts) == 1


async def test_the_held_card_is_refused_once_another_render_went_out(tmp_path, monkeypatch):
    """Card #350 (render 405) was still pending after render 417 of the same
    product posted itself: approving it must post nothing."""
    sent = _two_renders(tmp_path, monkeypatch)
    store = Claims().install(monkeypatch)
    assert (await promo.publish_promo(356, CAPTION)).ok
    sent.clear()
    store.held.clear()                    # even with no leg claim left (pre-table posts)
    late = await promo.publish_promo(348, CAPTION)
    assert not late.ok and "already posted" in late.detail and sent == []


async def test_a_refused_leg_is_released_for_a_later_render(tmp_path, monkeypatch):
    sent = _two_renders(tmp_path, monkeypatch)
    store = Claims().install(monkeypatch)

    async def refused(path, title, description, tags):
        sent.append(("youtube", path))
        return postiz.REFUSED, "title too long", None, None
    monkeypatch.setattr(postiz, "post_youtube_promo", refused)
    assert (await promo.publish_promo(348, CAPTION)).ok
    assert store.held[("ai-for-every-age", "youtube")] == (348, "released")
    assert store.held[("ai-for-every-age", "tiktok")] == (348, "sent")
    # The same render's YouTube leg re-run alone (as card 306 did) posts it.

    async def ok_youtube(path, title, description, tags):
        sent.append(("youtube", path))
        return postiz.ID_PENDING, "pz", "yt", None
    monkeypatch.setattr(postiz, "post_youtube_promo", ok_youtube)
    again = await promo.publish_promo(348, CAPTION, legs="youtube")
    assert again.ok and store.held[("ai-for-every-age", "youtube")] == (348, "sent")


async def test_the_claim_is_one_atomic_upsert_that_honours_the_window(monkeypatch):
    from datetime import timedelta
    seen = {}

    async def fetch_one(sql, *args):
        seen["sql"], seen["args"] = " ".join(sql.split()), args
    monkeypatch.setattr("atlas.kernel.state.fetch_one", fetch_one)
    monkeypatch.setenv("ACADEMY_PROMO_REPOST_DAYS", "5")
    assert await promo.claim_leg("ai-for-every-age", "youtube", 356) is False
    assert "ON CONFLICT (slug, leg) DO UPDATE" in seen["sql"]
    assert "status = 'released'" in seen["sql"] and "RETURNING" in seen["sql"]
    assert seen["args"] == ("ai-for-every-age", "youtube", 356, timedelta(days=5))

    async def broken(sql, *args):
        raise OSError("db down")
    monkeypatch.setattr("atlas.kernel.state.fetch_one", broken)
    assert await promo.claim_leg("x", "youtube", 1) is False     # never post blind


async def test_the_slug_comes_from_the_render_not_the_card(monkeypatch):
    sources = {405: "academy:local-business-automation-pack", 1: "reddit:abc",
               470: "concept:reply-ready", 435: "lesson:adults/0.1"}

    async def fetch_one(sql, *args):
        return {"source": sources[args[0]]} if args[0] in sources else None
    monkeypatch.setattr("atlas.kernel.state.fetch_one", fetch_one)
    assert await promo.slug_of(405) == "local-business-automation-pack"
    assert await promo.slug_of(1) == "" and await promo.slug_of(2) == ""
    # Lesson and concept Shorts keep their whole key, so they are recorded.
    assert await promo.slug_of(470) == "concept:reply-ready"
    assert await promo.slug_of(435) == "lesson:adults/0.1"


def test_today_is_a_calendar_date_not_a_rolling_day():
    """10-09's reel went out at 16:06; 10-10's was ready at 16:03 and was
    held as "already posted today". A calendar date (New York's, since
    2026-10-12) does not do that."""
    from datetime import UTC, datetime
    start = promo.day_start(datetime(2026, 10, 10, 16, 3, tzinfo=UTC))
    assert start == datetime(2026, 10, 10, 4, 0, tzinfo=UTC)          # midnight EDT
    assert datetime(2026, 10, 9, 16, 6, tzinfo=UTC) < start


@pytest.mark.parametrize(("env", "days"), [("", 5), ("3", 3), ("0", 1), ("x", 5)])
def test_the_repost_window(monkeypatch, env, days):
    monkeypatch.setenv("ACADEMY_PROMO_REPOST_DAYS", env)
    assert promo.repost_days() == days

def test_instagram_switched_off_is_not_configured_everywhere(monkeypatch):
    """the owner, 2026-10-11: Instagram off. Every caller gets its token through
    instagram._cfg or the DM reader, and both say not configured."""
    from atlas.modules import instagram
    from atlas.modules.messages import instagram_dm
    monkeypatch.setenv("INSTAGRAM_CLIPS_ACCOUNT_ID", "1")
    monkeypatch.setenv("INSTAGRAM_CLIPS_TOKEN", "t")
    monkeypatch.setenv("INSTAGRAM_ACCOUNT_ID", "1")
    monkeypatch.setenv("INSTAGRAM_TOKEN", "t")
    monkeypatch.setenv("ATLAS_DISABLED_MODULES", "screening, instagram ,tiktok")
    assert instagram.switched_off()
    with pytest.raises(instagram.NoAccount, match="switched off"):
        instagram._cfg("clips")
    assert instagram_dm._cfg() is None
    monkeypatch.setenv("ATLAS_DISABLED_MODULES", "screening,tiktok")
    assert not instagram.switched_off() and instagram._cfg("clips") == ("1", "t")


def test_the_day_is_new_yorks_date():
    """2026-10-12: two Shorts posted by hand at 9 pm New York (01:21 UTC)
    belong to the 11th, so the 12th's noon and 7 pm slots still post."""
    from datetime import UTC, datetime
    start = promo.day_start(datetime(2026, 10, 12, 1, 21, tzinfo=UTC))
    assert start == datetime(2026, 10, 11, 4, 0, tzinfo=UTC)        # midnight EDT
    assert promo.day_start(datetime(2026, 10, 12, 16, 5, tzinfo=UTC)) == \
        datetime(2026, 10, 12, 4, 0, tzinfo=UTC)
    assert promo.day_start(datetime(2026, 12, 1, 3, 0, tzinfo=UTC)) == \
        datetime(2026, 11, 30, 5, 0, tzinfo=UTC)                     # midnight EST


def test_a_youtube_caption_is_vetted_like_the_caption():
    ok = "Permission first\nFrom Sell With AI.\n\nMade with AI"
    assert promo.vet({"media_id": 1, "caption": CAPTION, "youtube_caption": ok}) is None
    why = promo.vet({"media_id": 1, "caption": CAPTION, "youtube_caption": "no label"})
    assert why and why.startswith("YouTube caption:")


def test_promos_carry_tiktoks_own_brand_disclosure_not_paid_partnership():
    """the owner's own products (the growth plan, 2026-10-12)."""
    assert postiz.PROMO_TIKTOK_FLAGS["brand_organic_toggle"] is True
    assert postiz.PROMO_TIKTOK_FLAGS["brand_content_toggle"] is False


# ------------------------------------------------- NotebookLM only (2026-10-19)
#
# the owner: every Academy Short is NotebookLM's. The post itself refuses an
# ATLAS render (a teaching cut, a lesson/concept/product reel) while
# ACADEMY_NOTEBOOKLM_FALLBACK is off, read from the render's draft source in
# the database -- and a source that can't be read is refused too.

NLM_SOURCE = "academy:tutorial:claude:1:notebooklm"
OLD_SOURCES = ("academy:tutorial:local:0:payoff", "academy:concept:cloud-or-local",
               "academy:lesson:sell-with-ai:3", "academy:ai-for-every-age")


def _source(monkeypatch, source):
    async def fetch_one(sql, *args):
        if isinstance(source, Exception):
            raise source
        return None if source is None else {"source": source}
    monkeypatch.setattr("atlas.kernel.state.fetch_one", fetch_one)


def _nothing_posts(monkeypatch):
    async def never(*a, **k):
        raise AssertionError("went on to check, card or post")
    for name in ("autopublish_check", "_card", "publish_promo", "_ready"):
        monkeypatch.setattr(promo, name, never)


@pytest.mark.parametrize("source", OLD_SOURCES)
async def test_an_atlas_render_never_autoposts_while_the_fallback_is_off(
        monkeypatch, source):
    monkeypatch.delenv("ACADEMY_NOTEBOOKLM_FALLBACK", raising=False)
    _source(monkeypatch, source)
    _nothing_posts(monkeypatch)
    got = await promo.autopublish_promo(868, CAPTION, slug="x")
    assert not got.ok and "ACADEMY_NOTEBOOKLM_FALLBACK" in got.detail
    dry = await promo.autopublish_promo_dry(868, CAPTION, slug="x")
    assert not dry.ok and "ACADEMY_NOTEBOOKLM_FALLBACK" in dry.detail


@pytest.mark.parametrize("source", [None, RuntimeError("no pool"), ""])
async def test_a_render_whose_source_cant_be_read_is_refused(monkeypatch, source):
    monkeypatch.delenv("ACADEMY_NOTEBOOKLM_FALLBACK", raising=False)
    _source(monkeypatch, source)
    _nothing_posts(monkeypatch)
    got = await promo.autopublish_promo(868, CAPTION)
    assert not got.ok and "can't be read" in got.detail


async def test_an_approved_card_of_an_atlas_render_is_refused_too(monkeypatch):
    """clips.publish_promo (the owner's tap on an old card) asks the same."""
    monkeypatch.delenv("ACADEMY_NOTEBOOKLM_FALLBACK", raising=False)
    _source(monkeypatch, OLD_SOURCES[0])
    from atlas.modules.content import crosspost

    async def never(*a, **k):
        raise AssertionError("read the file to post it")
    monkeypatch.setattr(crosspost, "_media", never)
    for run in (promo.publish_promo, promo.publish_promo_dry):
        got = await run(868, CAPTION)
        assert not got.ok and "NotebookLM" in got.detail


async def test_a_notebooklm_short_still_posts_while_the_fallback_is_off(monkeypatch):
    monkeypatch.delenv("ACADEMY_NOTEBOOKLM_FALLBACK", raising=False)
    _source(monkeypatch, NLM_SOURCE)
    assert await promo.old_design_refusal(900) is None
    seen = []

    async def ok_check(*a, **k):
        return None

    async def publish(media_id, caption, legs="", youtube_caption=""):
        from atlas.kernel.contracts import ActionResult
        seen.append(media_id)
        return ActionResult(True, "posted")
    monkeypatch.setattr(promo, "autopublish_check", ok_check)
    monkeypatch.setattr(promo, "publish_promo", publish)
    got = await promo.autopublish_promo(900, CAPTION, slug="tutorial:claude:1:notebooklm")
    assert got.ok and seen == [900]


async def test_the_fallback_switch_lets_an_atlas_render_through_the_gate(monkeypatch):
    _source(monkeypatch, OLD_SOURCES[0])
    assert await promo.old_design_refusal(868) is None


async def test_every_notebooklm_short_source_passes_the_gate(monkeypatch):
    """The sources notebooklm_shorts._make writes, for every catalogued
    episode, pass the post gate and its own SOURCE pattern alike."""
    from atlas.modules.academy import notebooklm_shorts as nls
    from atlas.modules.academy import tutorial_series as ts
    monkeypatch.delenv("ACADEMY_NOTEBOOKLM_FALLBACK", raising=False)
    sources = [f"academy:{nls.slug(s, ep)}" for s in ts.SERIES for ep in s.episodes]
    assert sources
    for source in sources:
        assert nls.SOURCE.match(source) and promo._NOTEBOOKLM_SOURCE.match(source)
        _source(monkeypatch, source)
        assert await promo.old_design_refusal(1) is None, source
