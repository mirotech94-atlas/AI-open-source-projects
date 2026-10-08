"""clips.publish_promo: the Academy store's promo reel on the clips accounts
(Miro, 2026-10-05, "post on clips too"), explicit at every layer and never
a hole in the campaign-clip rule."""
from __future__ import annotations

import os

import pytest

from atlas.kernel.contracts import Tier
from atlas.modules import instagram
from atlas.modules.clips import promo
from atlas.modules.content import postiz

CAPTION = "My AI runs my businesses while I sleep.\n\nshop.mapfront.io\n\nMade with AI"


def test_the_action_is_tier_ask_by_card_with_rollback_and_vet():
    from atlas.modules.clips import ClipsModule
    spec = next(a for a in ClipsModule().actions() if a.name == "clips.publish_promo")
    assert spec.tier is Tier.ASK and spec.card_only and spec.rollback is not None
    assert spec.vet is promo.vet and spec.max_per_hour == 1
    assert set(spec.params) == {"media_id", "caption", "legs"}


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
    assert promo.legs_of("") == promo.LEGS and promo.legs_of(None) == promo.LEGS
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
    monkeypatch.setenv("POSTIZ_YOUTUBE_CLIPS_INTEGRATION", "AtlasClips (@miroatlas)")
    status, said, *_ = await postiz.post_youtube_promo(str(path), "x" * 101, CAPTION, [])
    assert status == postiz.REFUSED and "2-100" in said


# ------------------------------------------------------------------ the run

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
    result = await promo.publish_promo(90, CAPTION)
    assert result.ok and claims[0] == ("clips.publish_promo", "promo:90")
    assert ("settled", "promo:90") in claims
    assert [leg[0] for leg in legs] == ["instagram", "tiktok", "youtube"]
    assert legs[0][2] == "https://atlas.example/media/tok"
    assert legs[2][1] == "My AI runs my businesses while I sleep."
    assert result.rollback_token == ('{"instagram": "clips:1", "tiktok": "pz-1", '
                                     '"youtube": "pz-2"}')


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
    """The editions are composed frames by Miro's choice; the stories lane's
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
