"""Task 55: the AI-stories reel's TikTok leg, through the Postiz Public API.

Each step against a fake transport:

* the MP4 is uploaded first and the upload's `path` is what the post carries;
* the TikTok integration is found in the list, and none is a refusal;
* the integration's settings are read before posting, and a setting it does
  not declare (Postiz would silently drop it) or a value it does not allow
  is refused -- nothing posted blind;
* the post is DIRECT_POST, PUBLIC_TO_EVERYONE, duet and stitch on -- never
  UPLOAD -- with the "Made with AI" caption;
* no releaseId means "posted, id pending", never an invented id;
* undo deletes only a still-queued post; a published one is a MANUAL note;
* the key is only ever the Authorization header, never in a reason.
"""
from __future__ import annotations

import json

import httpx
import pytest

from atlas.modules.content import postiz
from atlas.modules.content.drafts import AI_LABEL

KEY = "pz-secret-key"
CAPTION = f"The sea was gone.\n\n{AI_LABEL}"
SETTINGS = {"settings": [
    {"key": "privacy_level", "options": [
        {"value": "PUBLIC_TO_EVERYONE"}, {"value": "SELF_ONLY"}]},
    {"key": "duet"}, {"key": "stitch"}, {"key": "comment"},
    {"key": "autoAddMusic", "options": ["yes", "no"]},
    {"key": "brand_content_toggle"}, {"key": "brand_organic_toggle"},
    {"key": "video_made_with_ai"},
    {"key": "content_posting_method", "options": ["DIRECT_POST", "UPLOAD"]},
]}


@pytest.fixture(autouse=True)
def keyed(monkeypatch):
    monkeypatch.setenv("POSTIZ_API_KEY", KEY)
    monkeypatch.delenv("POSTIZ_API_URL", raising=False)
    monkeypatch.delenv("POSTIZ_TIKTOK_CLIPS_INTEGRATION", raising=False)


def _fake(monkeypatch, *, integrations=None, settings=SETTINGS, post=None,
          missing=None, state="PUBLISHED"):
    seen: list[tuple[str, str, bytes]] = []

    def handler(request):
        assert request.headers["authorization"] == KEY
        assert KEY not in str(request.url)
        path = request.url.path.removeprefix("/public/v1")
        seen.append((request.method, path, request.content))
        if (request.method, path) == ("POST", "/upload"):
            return httpx.Response(200, json={"id": "up-1", "path":
                                             "https://uploads.postiz.com/reel.mp4"})
        if path == "/integrations":
            return httpx.Response(200, json=integrations if integrations is not None
                                  else [{"id": "ig-x", "identifier": "instagram"},
                                        {"id": "tt-1",
                                         "identifier": "tiktok-business",
                                         "name": "atlasstories7",
                                         "disabled": False}])
        if path == "/integration-settings/tt-1":
            return httpx.Response(200, json=settings)
        if (request.method, path) == ("POST", "/posts"):
            return httpx.Response(200, json=post if post is not None
                                  else [{"postId": "pz-9", "integration": "tt-1"}])
        if path == "/posts/pz-9/missing":
            if missing is None:
                return httpx.Response(404, json={"message": "Not Found"})
            return httpx.Response(200, json=missing)
        if (request.method, path) == ("PUT", "/posts/pz-9/release-id"):
            return httpx.Response(200, json={"ok": True})
        if (request.method, path) == ("GET", "/posts"):
            return httpx.Response(200, json={"posts": [{"id": "pz-9",
                                                        "state": state}]})
        if (request.method, path) == ("DELETE", "/posts/pz-9"):
            return httpx.Response(200, json={"ok": True})
        return httpx.Response(500)

    real = httpx.AsyncClient

    def factory(*a, **k):
        k.pop("transport", None)
        return real(*a, transport=httpx.MockTransport(handler), **k)

    monkeypatch.setattr(postiz.httpx, "AsyncClient", factory)
    return seen


def _post_body(seen) -> dict:
    return json.loads(next(body for method, path, body in seen
                           if (method, path) == ("POST", "/posts")))


async def test_upload_first_and_the_post_carries_the_uploaded_path(monkeypatch):
    seen = _fake(monkeypatch)
    status, _, post_id = await postiz.post_tiktok(b"MP4BYTES", CAPTION)
    assert status == postiz.ID_PENDING and post_id == "pz-9"
    steps = [(m, p) for m, p, _ in seen]
    assert steps[:4] == [("POST", "/upload"), ("GET", "/integrations"),
                         ("GET", "/integration-settings/tt-1"), ("POST", "/posts")]
    assert b"MP4BYTES" in seen[0][2]                 # the file itself, multipart
    body = _post_body(seen)
    value = body["posts"][0]["value"][0]
    assert value["image"] == [{"id": "up-1",
                               "path": "https://uploads.postiz.com/reel.mp4"}]
    assert value["content"].endswith(AI_LABEL)
    assert body["posts"][0]["integration"] == {"id": "tt-1"}


async def test_the_post_is_direct_post_and_public_never_upload(monkeypatch):
    seen = _fake(monkeypatch)
    await postiz.post_tiktok(b"MP4", CAPTION)
    settings = _post_body(seen)["posts"][0]["settings"]
    assert settings == {"__type": "tiktok", "privacy_level": "PUBLIC_TO_EVERYONE",
                        "duet": True, "stitch": True, "comment": True,
                        "autoAddMusic": "no", "brand_content_toggle": False,
                        "brand_organic_toggle": False, "video_made_with_ai": True,
                        "content_posting_method": "DIRECT_POST"}
    assert "UPLOAD" not in json.dumps(settings)


async def test_no_tiktok_integration_is_a_refusal(monkeypatch):
    seen = _fake(monkeypatch, integrations=[
        {"id": "tt-1", "identifier": "tiktok", "disabled": True}])
    status, detail, post_id = await postiz.post_tiktok(b"MP4", CAPTION)
    assert status == postiz.REFUSED and post_id is None
    assert "no enabled TikTok integration" in detail
    assert ("POST", "/posts") not in [(m, p) for m, p, _ in seen]


async def test_an_undeclared_setting_is_refused_before_posting(monkeypatch):
    seen = _fake(monkeypatch, settings={"settings": [
        {"key": "privacy_level"}, {"key": "content_posting_method"},
        {"key": "stitch"}]})
    status, detail, _ = await postiz.post_tiktok(b"MP4", CAPTION)
    assert status == postiz.REFUSED
    assert "'duet'" in detail and "silently dropped" in detail
    assert ("POST", "/posts") not in [(m, p) for m, p, _ in seen]


async def test_a_disallowed_value_is_refused_before_posting(monkeypatch):
    seen = _fake(monkeypatch, settings={"settings": [
        {"key": "privacy_level", "options": ["FOLLOWER_OF_CREATOR"]},
        {"key": "duet"}, {"key": "stitch"}, {"key": "comment"},
        {"key": "autoAddMusic"}, {"key": "brand_content_toggle"},
        {"key": "brand_organic_toggle"}, {"key": "video_made_with_ai"},
        {"key": "content_posting_method"}]})
    status, detail, _ = await postiz.post_tiktok(b"MP4", CAPTION)
    assert status == postiz.REFUSED and "PUBLIC_TO_EVERYONE" in detail
    assert ("POST", "/posts") not in [(m, p) for m, p, _ in seen]


async def test_unreadable_settings_are_never_posted_through(monkeypatch):
    _fake(monkeypatch, settings="nothing useful")
    status, detail, _ = await postiz.post_tiktok(b"MP4", CAPTION)
    assert status == postiz.REFUSED and "could not be read" in detail


def test_the_settings_reader_takes_a_mapping_too():
    rules = postiz.declared({"privacy_level": {"enum": ["PUBLIC_TO_EVERYONE"]},
                             "duet": {}, "stitch": {},
                             "content_posting_method": ["DIRECT_POST"]})
    assert rules == {"privacy_level": {"PUBLIC_TO_EVERYONE"}, "duet": None,
                     "stitch": None, "content_posting_method": {"DIRECT_POST"}}
    listed = {"settings": [{"key": key} for key in postiz.TIKTOK_SETTINGS]}
    assert postiz.validate(listed, postiz.TIKTOK_SETTINGS) is None


async def test_a_missing_release_id_is_id_pending_never_invented(monkeypatch):
    _fake(monkeypatch)                       # no releaseId; /missing is a 404
    status, detail, _ = await postiz.post_tiktok(b"MP4", CAPTION)
    assert status == "posted, id pending"
    assert "no id yet" in detail and "TikTok id" not in detail


async def test_a_single_matching_candidate_is_connected(monkeypatch):
    seen = _fake(monkeypatch, missing=[
        {"id": "7400000000001", "content": "The sea was gone. Made with AI"},
        {"id": "7399999999999", "content": "an older video"}])
    status, detail, _ = await postiz.post_tiktok(b"MP4", CAPTION)
    assert status == postiz.POSTED and "TikTok id 7400000000001" in detail
    put = next(body for m, p, body in seen if m == "PUT")
    assert json.loads(put) == {"releaseId": "7400000000001"}


async def test_an_ambiguous_candidate_list_stays_pending(monkeypatch):
    _fake(monkeypatch, missing=[{"id": "1", "content": "The sea was gone."},
                                {"id": "2", "content": "The sea was gone. again"}])
    status, _, _ = await postiz.post_tiktok(b"MP4", CAPTION)
    assert status == postiz.ID_PENDING


async def test_a_release_id_in_the_answer_is_used(monkeypatch):
    _fake(monkeypatch, post=[{"postId": "pz-9", "releaseId": "7400000000042"}])
    status, detail, _ = await postiz.post_tiktok(b"MP4", CAPTION)
    assert status == postiz.POSTED and "7400000000042" in detail


async def test_undo_deletes_a_queued_post(monkeypatch):
    seen = _fake(monkeypatch, state="QUEUE")
    ok, _ = await postiz.undo("pz-9")
    assert ok is True and ("DELETE", "/posts/pz-9") in [(m, p) for m, p, _ in seen]


async def test_undo_leaves_a_published_post_and_notes_it(monkeypatch):
    seen = _fake(monkeypatch, state="PUBLISHED")
    ok, detail = await postiz.undo("pz-9")
    assert ok is False and detail.startswith("MANUAL:")
    assert "Tier 3" in detail
    assert not any(m == "DELETE" for m, _, _ in seen)


async def test_unset_key_is_not_configured(monkeypatch):
    monkeypatch.delenv("POSTIZ_API_KEY")
    status, detail, _ = await postiz.post_tiktok(b"MP4", CAPTION)
    assert status == postiz.NOT_CONFIGURED and "POSTIZ_API_KEY" in detail


async def test_a_postiz_error_never_carries_the_key(monkeypatch):
    def handler(request):
        return httpx.Response(401, json={"message": f"bad key {KEY}"})

    real = httpx.AsyncClient
    monkeypatch.setattr(postiz.httpx, "AsyncClient", lambda *a, **k: real(
        transport=httpx.MockTransport(handler)))
    status, detail, _ = await postiz.post_tiktok(b"MP4", CAPTION)
    assert status == postiz.REFUSED and KEY not in detail and "401" in detail


async def test_postiz_api_url_overrides_the_base(monkeypatch):
    monkeypatch.setenv("POSTIZ_API_URL", "https://postiz.example/api/public/v1/")
    assert postiz.base() == "https://postiz.example/api/public/v1"


async def test_the_key_is_sent_raw_never_as_bearer(monkeypatch):
    headers: list[str] = []

    def handler(request):
        headers.append(request.headers["authorization"])
        return httpx.Response(401, json={"message": "Invalid API key"})

    real = httpx.AsyncClient
    monkeypatch.setattr(postiz.httpx, "AsyncClient", lambda *a, **k: real(
        transport=httpx.MockTransport(handler)))
    await postiz.post_tiktok(b"MP4", CAPTION)
    assert headers == [KEY]
    assert not headers[0].lower().startswith("bearer")


@pytest.mark.parametrize(("listed", "found"), [
    ([{"id": "a", "identifier": "tiktok-business", "disabled": False}], "a"),
    ([{"id": "b", "identifier": "tiktok"}], "b"),
    ([{"id": "c", "identifier": "tiktok-business", "disabled": True},
      {"id": "d", "identifier": "TikTok-Business"}], "d"),
    ([{"id": "e", "identifier": "instagram"}], None),
])
async def test_the_tiktok_integration_is_matched_by_identifier(monkeypatch, listed,
                                                              found):
    def handler(request):
        return httpx.Response(200, json=listed)

    real = httpx.AsyncClient
    monkeypatch.setattr(postiz.httpx, "AsyncClient", lambda *a, **k: real(
        transport=httpx.MockTransport(handler)))
    async with postiz.httpx.AsyncClient() as c:
        if found is None:
            with pytest.raises(postiz.PostizError):
                await postiz._tiktok_integration(c)
        else:
            assert await postiz._tiktok_integration(c) == found


def test_the_documented_settings_answer_is_read():
    """`integrations:settings` per the postiz skill: rules prose plus a JSON
    schema under `output.settings`."""
    answer = {"output": {
        "rules": "Use DIRECT_POST to publish; UPLOAD only lands in the inbox.",
        "maxLength": 2200,
        "settings": {"type": "object", "properties": {
            "privacy_level": {"type": "string", "description": "who can see it",
                              "enum": ["PUBLIC_TO_EVERYONE", "SELF_ONLY"]},
            "duet": {"type": "boolean"}, "stitch": {"type": "boolean"},
            "comment": {"type": "boolean"},
            "autoAddMusic": {"type": "string", "enum": ["yes", "no"]},
            "brand_content_toggle": {"type": "boolean"},
            "brand_organic_toggle": {"type": "boolean"},
            "video_made_with_ai": {"type": "boolean"},
            "content_posting_method": {"enum": ["DIRECT_POST", "UPLOAD"]}},
            "required": ["privacy_level"]}}}
    assert postiz.declared(answer) == {
        "privacy_level": {"PUBLIC_TO_EVERYONE", "SELF_ONLY"}, "duet": None,
        "stitch": None, "comment": None, "autoAddMusic": {"yes", "no"},
        "brand_content_toggle": None, "brand_organic_toggle": None,
        "video_made_with_ai": None,
        "content_posting_method": {"DIRECT_POST", "UPLOAD"}}
    assert postiz.validate(answer, postiz.TIKTOK_SETTINGS) is None
    del answer["output"]["settings"]["properties"]["stitch"]
    assert "'stitch'" in postiz.validate(answer, postiz.TIKTOK_SETTINGS)


async def test_a_single_unmatched_candidate_is_not_bound(monkeypatch):
    seen = _fake(monkeypatch, missing=[{"id": "7400000000009",
                                        "url": "https://cover.jpg"}])
    status, _, _ = await postiz.post_tiktok(b"MP4", CAPTION)
    assert status == postiz.ID_PENDING
    assert not any(m == "PUT" for m, _, _ in seen)


SELF_ONLY = {"output": {"settings": {"properties": {
    "privacy_level": {"enum": ["SELF_ONLY", "MUTUAL_FOLLOW_FRIENDS"]},
    "duet": {}, "stitch": {}, "content_posting_method": {"enum": ["DIRECT_POST"]}}}}}


async def test_a_private_only_integration_names_the_audit(monkeypatch):
    seen = _fake(monkeypatch, settings=SELF_ONLY)
    ok, said = await postiz.readiness()
    assert ok is False and said.startswith("private until TikTok's audit passes")
    status, detail, _ = await postiz.post_tiktok(b"MP4", CAPTION)
    assert status == postiz.REFUSED and "TikTok's audit" in detail
    assert ("POST", "/posts") not in [(m, p) for m, p, _ in seen]


async def test_readiness_only_reads(monkeypatch):
    seen = _fake(monkeypatch)
    ok, said = await postiz.readiness()
    assert ok is True and "public" in said
    assert all(m == "GET" for m, _, _ in seen)


# ------------------------------------------- two TikTok accounts, never crossed

STORIES = {"id": "tt-1", "identifier": "tiktok-business", "name": "atlasstories7"}
CLIPS = {"id": "tt-2", "identifier": "tiktok-business", "name": "atlasclips12"}
OTHER = {"id": "tt-3", "identifier": "tiktok", "name": "someoneelse"}


@pytest.mark.parametrize("clips_env", [None, "tt-2", "atlasclips12", "@example_handle"])
@pytest.mark.parametrize("listed", [[STORIES, CLIPS], [CLIPS, STORIES]])
def test_the_reels_lane_never_picks_the_clips_tiktok(monkeypatch, clips_env, listed):
    if clips_env:
        monkeypatch.setenv("POSTIZ_TIKTOK_CLIPS_INTEGRATION", clips_env)
    assert postiz.pick_integration(listed, "stories") == ("tt-1", "atlasstories7")


def test_the_reels_lane_refuses_a_guess_among_several(monkeypatch):
    with pytest.raises(postiz.PostizError, match="does not guess"):
        postiz.pick_integration([OTHER, {**OTHER, "id": "tt-4", "name": "x"}],
                                "stories")
    # Only the clips account connected: the reels lane has nothing to post to.
    with pytest.raises(postiz.PostizError, match="no enabled TikTok"):
        postiz.pick_integration([CLIPS], "stories")
    monkeypatch.setenv("POSTIZ_TIKTOK_CLIPS_INTEGRATION", "tt-3")
    with pytest.raises(postiz.PostizError, match="no enabled TikTok"):
        postiz.pick_integration([OTHER], "stories")


@pytest.mark.parametrize("clips_env", ["tt-2", "atlasclips12", "@AtlasClips12"])
@pytest.mark.parametrize("listed", [[STORIES, CLIPS], [CLIPS, STORIES]])
def test_the_clips_lane_takes_only_the_integration_its_env_names(monkeypatch,
                                                                 clips_env, listed):
    monkeypatch.setenv("POSTIZ_TIKTOK_CLIPS_INTEGRATION", clips_env)
    assert postiz.pick_integration(listed, "clips") == ("tt-2", "atlasclips12")


@pytest.mark.parametrize(("clips_env", "listed", "why"), [
    (None, [STORIES, CLIPS], "never guessed"),
    ("tt-9", [STORIES, CLIPS], "no enabled TikTok integration matching"),
    ("tt-2", [STORIES, {**CLIPS, "disabled": True}], "no enabled"),
    ("tt-1", [STORIES, CLIPS], "AI-stories TikTok"),
    ("atlasstories7", [STORIES], "AI-stories TikTok"),
])
def test_the_clips_lane_never_picks_the_stories_tiktok(monkeypatch, clips_env,
                                                       listed, why):
    if clips_env:
        monkeypatch.setenv("POSTIZ_TIKTOK_CLIPS_INTEGRATION", clips_env)
    with pytest.raises(postiz.PostizError, match=why):
        postiz.pick_integration(listed, "clips")


def _clip_file(tmp_path, monkeypatch):
    root = tmp_path / "clips"
    monkeypatch.setenv("CLIPS_DATA_DIR", str(root))
    out = root / "atlus-persona-metaphor" / "out"
    out.mkdir(parents=True)
    path = out / "1-abcdef012345-1.mp4"
    path.write_bytes(b"CLIPBYTES")
    return path


CLIP_CAPTION = ("Save up to 75% on select ATLUS titles during the ATLUS Developer "
                "Sale on Steam.\n\n#Ad\n#ATLUS #SteamSale\n@atlus_west")


def _two_accounts(monkeypatch, **kw):
    return _fake(monkeypatch, integrations=[
        {**STORIES, "id": "tt-9"}, {**CLIPS, "id": "tt-1"}], **kw)


async def test_a_clip_posts_to_the_clips_integration_as_brand_content(monkeypatch,
                                                                      tmp_path):
    path = _clip_file(tmp_path, monkeypatch)
    monkeypatch.setenv("POSTIZ_TIKTOK_CLIPS_INTEGRATION", "atlasclips12")
    seen = _two_accounts(monkeypatch)
    status, _, post_id, url = await postiz.post_tiktok_clip(str(path), CLIP_CAPTION)
    assert status == postiz.ID_PENDING and post_id == "pz-9" and url is None
    assert b"CLIPBYTES" in seen[0][2]
    body = _post_body(seen)
    assert body["posts"][0]["integration"] == {"id": "tt-1"}
    settings = body["posts"][0]["settings"]
    assert settings["video_made_with_ai"] is False
    assert settings["brand_content_toggle"] is True
    assert settings["autoAddMusic"] == "no"
    assert settings["content_posting_method"] == "DIRECT_POST"
    assert settings["privacy_level"] == "PUBLIC_TO_EVERYONE"
    assert body["posts"][0]["value"][0]["content"] == CLIP_CAPTION


@pytest.mark.parametrize(("answer", "url"), [
    ({"postId": "pz-9", "releaseId": "74",
      "releaseURL": "https://www.tiktok.com/@example_handle/video/74"},
     "https://www.tiktok.com/@example_handle/video/74"),
    # A URL on another account is not this post's: never recorded.
    ({"postId": "pz-9", "releaseId": "74",
      "releaseURL": "https://www.tiktok.com/@atlasstories7/video/74"}, None),
    # An id alone is never turned into a URL.
    ({"postId": "pz-9", "releaseId": "74"}, None),
])
async def test_a_clips_url_is_only_the_one_postiz_returned(monkeypatch, tmp_path,
                                                           answer, url):
    path = _clip_file(tmp_path, monkeypatch)
    monkeypatch.setenv("POSTIZ_TIKTOK_CLIPS_INTEGRATION", "tt-1")
    _two_accounts(monkeypatch, post=[answer])
    status, _, _, got = await postiz.post_tiktok_clip(str(path), CLIP_CAPTION)
    assert status == postiz.POSTED and got == url


async def test_a_clip_needs_its_integration_configured(monkeypatch, tmp_path):
    path = _clip_file(tmp_path, monkeypatch)
    seen = _two_accounts(monkeypatch)
    status, detail, _, _ = await postiz.post_tiktok_clip(str(path), CLIP_CAPTION)
    assert status == postiz.NOT_CONFIGURED
    assert "POSTIZ_TIKTOK_CLIPS_INTEGRATION" in detail and not seen
    ok, said = await postiz.readiness("clips")
    assert ok is False and "POSTIZ_TIKTOK_CLIPS_INTEGRATION" in said
    monkeypatch.setenv("POSTIZ_TIKTOK_CLIPS_INTEGRATION", "tt-1")
    monkeypatch.delenv("POSTIZ_API_KEY")
    assert "POSTIZ_API_KEY" in (postiz.clips_gap() or "")


@pytest.mark.parametrize("where", ["outside", "source", "symlink", "caption"])
async def test_the_clips_tiktok_posts_only_a_lane_cut_file(monkeypatch, tmp_path,
                                                          where):
    good = _clip_file(tmp_path, monkeypatch)
    monkeypatch.setenv("POSTIZ_TIKTOK_CLIPS_INTEGRATION", "tt-1")
    seen = _two_accounts(monkeypatch)
    outside = tmp_path / "any.mp4"
    outside.write_bytes(b"x")
    source = good.parent.parent / "source" / "raw.mp4"
    source.parent.mkdir()
    source.write_bytes(b"x")
    link = good.parent / "link.mp4"
    link.symlink_to(outside)
    caption = good.parent / "1-abcdef012345-1.mp4.tiktok.txt"
    caption.write_text("x")
    path = {"outside": outside, "source": source, "symlink": link,
            "caption": caption}[where]
    status, detail, _, _ = await postiz.post_tiktok_clip(str(path), CLIP_CAPTION)
    assert status == postiz.REFUSED and "clips.publish_clip_tiktok" in detail
    assert not seen


async def test_clips_readiness_reads_the_clips_integration(monkeypatch):
    monkeypatch.setenv("POSTIZ_TIKTOK_CLIPS_INTEGRATION", "tt-1")
    seen = _two_accounts(monkeypatch)
    ok, _ = await postiz.readiness("clips")
    assert ok is True
    assert ("GET", "/integration-settings/tt-1") in [(m, p) for m, p, _ in seen]
    assert all(m == "GET" for m, _, _ in seen)


# ----------------------------------- the clips YouTube channel (Shorts), Postiz

YT_CLIPS = {"id": "yt-1", "identifier": "youtube", "name": "AtlasClips"}
# A second YouTube integration in the same workspace -- say, the reels
# lane's Shorts channel. Only the one the clips env names is ever taken.
YT_OTHER = {"id": "yt-2", "identifier": "youtube", "name": "AtlasStories"}
YT_SETTINGS = {"output": {"rules": "title required", "settings": {"properties": {
    "title": {"type": "string"},
    "type": {"enum": ["public", "private", "unlisted"]},
    "selfDeclaredMadeForKids": {"enum": ["yes", "no"]},
    "thumbnail": {"type": "object"},
    "tags": {"type": "array"},
}}}}
YT_TITLE = ("Save up to 75% on select ATLUS titles during the ATLUS Developer Sale "
            "on Steam. #Ad @atlus")
YT_CAPTION = ("Save up to 75% on select ATLUS titles during the ATLUS Developer Sale "
              "on Steam.\n\n#Ad\n#ATLUS #SteamSale\n@atlus")


def _yt_fake(monkeypatch, *, integrations=None, settings=YT_SETTINGS, post=None,
             state="PUBLISHED"):
    seen: list[tuple[str, str, bytes]] = []

    def handler(request):
        assert request.headers["authorization"] == KEY
        path = request.url.path.removeprefix("/public/v1")
        seen.append((request.method, path, request.content))
        if (request.method, path) == ("POST", "/upload"):
            return httpx.Response(200, json={"id": "up-1", "path":
                                             "https://uploads.postiz.com/clip.mp4"})
        if path == "/integrations":
            return httpx.Response(200, json=integrations if integrations is not None
                                  else [STORIES, CLIPS, YT_OTHER, YT_CLIPS])
        if path.startswith("/integration-settings/"):
            return httpx.Response(200, json=settings)
        if (request.method, path) == ("POST", "/posts"):
            return httpx.Response(200, json=post if post is not None
                                  else [{"postId": "pz-9", "integration": "yt-1"}])
        if (request.method, path) == ("GET", "/posts"):
            return httpx.Response(200, json={"posts": [{"id": "pz-9",
                                                        "state": state}]})
        if (request.method, path) == ("DELETE", "/posts/pz-9"):
            return httpx.Response(200, json={"ok": True})
        return httpx.Response(500)

    real = httpx.AsyncClient

    def factory(*a, **k):
        k.pop("transport", None)
        return real(*a, transport=httpx.MockTransport(handler), **k)

    monkeypatch.setattr(postiz.httpx, "AsyncClient", factory)
    return seen


@pytest.mark.parametrize("env", ["yt-1", "AtlasClips", "@example_handle"])
@pytest.mark.parametrize("listed", [[YT_OTHER, YT_CLIPS, STORIES, CLIPS],
                                    [YT_CLIPS, CLIPS, YT_OTHER]])
def test_the_clips_shorts_take_only_the_youtube_integration_their_env_names(
        monkeypatch, env, listed):
    monkeypatch.setenv("POSTIZ_YOUTUBE_CLIPS_INTEGRATION", env)
    monkeypatch.setenv("POSTIZ_TIKTOK_CLIPS_INTEGRATION", "tt-2")
    assert postiz.pick_integration(listed, postiz.YT_ACCOUNT) == ("yt-1", "atlasclips")


@pytest.mark.parametrize(("env", "listed", "why"), [
    (None, [YT_CLIPS], "never guessed"),
    ("yt-9", [YT_CLIPS, YT_OTHER], "no enabled integration matching"),
    ("yt-1", [{**YT_CLIPS, "disabled": True}], "no enabled"),
    # Pointed at a TikTok account by mistake: never a Short there.
    ("tt-1", [STORIES, YT_CLIPS], "not YouTube"),
    ("atlasclips12", [CLIPS, YT_CLIPS], "not YouTube"),
    ("atlasstories7", [STORIES], "not YouTube"),
])
def test_the_clips_shorts_never_pick_another_integration(monkeypatch, env, listed,
                                                         why):
    if env:
        monkeypatch.setenv("POSTIZ_YOUTUBE_CLIPS_INTEGRATION", env)
    with pytest.raises(postiz.PostizError, match=why):
        postiz.pick_integration(listed, postiz.YT_ACCOUNT)


def test_the_clips_shorts_refuse_the_reels_shorts_channel(monkeypatch):
    # The reels lane's Shorts integration is not the one the clips env
    # names, so the clips lane never takes it, even when it is listed first.
    monkeypatch.setenv("POSTIZ_YOUTUBE_CLIPS_INTEGRATION", "yt-1")
    assert postiz.pick_integration([YT_OTHER, YT_CLIPS],
                                   postiz.YT_ACCOUNT)[0] == "yt-1"
    with pytest.raises(postiz.PostizError, match="no enabled integration"):
        postiz.pick_integration([YT_OTHER], postiz.YT_ACCOUNT)


@pytest.mark.parametrize("env", [None, "yt-1"])
def test_the_reels_lane_never_picks_a_youtube_integration(monkeypatch, env):
    if env:
        monkeypatch.setenv("POSTIZ_YOUTUBE_CLIPS_INTEGRATION", env)
    both = [YT_CLIPS, YT_OTHER, STORIES]
    assert postiz.pick_integration(both, "stories") == ("tt-1", "atlasstories7")
    with pytest.raises(postiz.PostizError, match="no enabled TikTok"):
        postiz.pick_integration([YT_CLIPS, YT_OTHER], "stories")
    # Nor does the clips TikTok leg.
    monkeypatch.setenv("POSTIZ_TIKTOK_CLIPS_INTEGRATION", "yt-1")
    with pytest.raises(postiz.PostizError, match="no enabled TikTok"):
        postiz.pick_integration([YT_CLIPS, CLIPS], "clips")


def test_a_course_post_never_goes_to_the_clips_youtube(monkeypatch):
    from atlas.modules.course import channels
    monkeypatch.setenv("POSTIZ_YOUTUBE_CLIPS_INTEGRATION", "yt-7")
    assert channels.is_clips({"id": "yt-7", "identifier": "youtube",
                              "name": "the owner"})


async def test_a_short_posts_to_the_clips_youtube_as_a_public_short(monkeypatch,
                                                                    tmp_path):
    path = _clip_file(tmp_path, monkeypatch)
    monkeypatch.setenv("POSTIZ_YOUTUBE_CLIPS_INTEGRATION", "yt-1")
    seen = _yt_fake(monkeypatch, post=[{
        "postId": "pz-9", "releaseId": "abcDEF12345",
        "releaseURL": "https://www.youtube.com/watch?v=abcDEF12345"}])
    status, detail, post_id, url = await postiz.post_youtube_clip(
        str(path), YT_TITLE, YT_CAPTION, ["ATLUS", "SteamSale"])
    assert status == postiz.POSTED and post_id == "pz-9", detail
    assert url == "https://www.youtube.com/shorts/abcDEF12345"
    assert b"CLIPBYTES" in seen[0][2]
    assert ("GET", "/integration-settings/yt-1") in [(m, p) for m, p, _ in seen]
    body = _post_body(seen)
    post = body["posts"][0]
    assert post["integration"] == {"id": "yt-1"}
    assert post["value"][0]["content"] == YT_CAPTION          # the full caption
    assert post["settings"] == {
        "__type": "youtube", "type": "public", "selfDeclaredMadeForKids": "no",
        "title": YT_TITLE,
        "tags": [{"value": "ATLUS", "label": "ATLUS"},
                 {"value": "SteamSale", "label": "SteamSale"}]}
    # YouTube's answer carries the id: no TikTok missing-content dance.
    assert not any("/missing" in p for _, p, _ in seen)


@pytest.mark.parametrize(("answer", "status", "url"), [
    ({"postId": "pz-9"}, postiz.ID_PENDING, None),
    ({"postId": "pz-9", "releaseId": "abcDEF12345"}, postiz.POSTED, None),
    ({"postId": "pz-9", "releaseId": "abcDEF12345",
      "releaseURL": "https://www.youtube.com/shorts/abcDEF12345"}, postiz.POSTED,
     "https://www.youtube.com/shorts/abcDEF12345"),
    # A URL for another video is not this post's.
    ({"postId": "pz-9", "releaseId": "abcDEF12345",
      "releaseURL": "https://www.youtube.com/shorts/zzzzzzzzzzz"}, postiz.POSTED,
     None),
    ({"postId": "pz-9", "releaseId": "abcDEF12345",
      "releaseURL": "https://evil.example/shorts/abcDEF12345"}, postiz.POSTED, None),
])
async def test_a_shorts_url_is_only_the_one_postiz_returned(monkeypatch, tmp_path,
                                                            answer, status, url):
    path = _clip_file(tmp_path, monkeypatch)
    monkeypatch.setenv("POSTIZ_YOUTUBE_CLIPS_INTEGRATION", "yt-1")
    _yt_fake(monkeypatch, post=[answer])
    got, _, _, got_url = await postiz.post_youtube_clip(str(path), YT_TITLE,
                                                        YT_CAPTION, [])
    assert got == status and got_url == url


async def test_a_short_needs_its_integration_configured(monkeypatch, tmp_path):
    path = _clip_file(tmp_path, monkeypatch)
    seen = _yt_fake(monkeypatch)
    status, detail, _, _ = await postiz.post_youtube_clip(str(path), YT_TITLE,
                                                          YT_CAPTION, [])
    assert status == postiz.NOT_CONFIGURED and not seen
    assert "set POSTIZ_YOUTUBE_CLIPS_INTEGRATION" in detail
    ok, said = await postiz.readiness(postiz.YT_ACCOUNT)
    assert ok is False and "POSTIZ_YOUTUBE_CLIPS_INTEGRATION" in said
    monkeypatch.setenv("POSTIZ_YOUTUBE_CLIPS_INTEGRATION", "yt-1")
    monkeypatch.delenv("POSTIZ_API_KEY")
    assert "POSTIZ_API_KEY" in (postiz.yt_clips_gap() or "")


@pytest.mark.parametrize("where", ["outside", "source", "symlink"])
async def test_the_clips_youtube_posts_only_a_lane_cut_file(monkeypatch, tmp_path,
                                                           where):
    good = _clip_file(tmp_path, monkeypatch)
    monkeypatch.setenv("POSTIZ_YOUTUBE_CLIPS_INTEGRATION", "yt-1")
    seen = _yt_fake(monkeypatch)
    outside = tmp_path / "reel.mp4"                 # a reel is never a clip
    outside.write_bytes(b"x")
    source = good.parent.parent / "source" / "raw.mp4"
    source.parent.mkdir()
    source.write_bytes(b"x")
    link = good.parent / "link.mp4"
    link.symlink_to(outside)
    path = {"outside": outside, "source": source, "symlink": link}[where]
    status, detail, _, _ = await postiz.post_youtube_clip(str(path), YT_TITLE,
                                                          YT_CAPTION, [])
    assert status == postiz.REFUSED and "clips.publish_clip_youtube_postiz" in detail
    assert not seen


@pytest.mark.parametrize("title", ["x", "y" * 101])
async def test_a_shorts_title_is_refused_never_cut(monkeypatch, tmp_path, title):
    path = _clip_file(tmp_path, monkeypatch)
    monkeypatch.setenv("POSTIZ_YOUTUBE_CLIPS_INTEGRATION", "yt-1")
    seen = _yt_fake(monkeypatch)
    status, detail, _, _ = await postiz.post_youtube_clip(str(path), title,
                                                          YT_CAPTION, [])
    assert status == postiz.REFUSED and "never cut short" in detail and not seen


@pytest.mark.parametrize(("settings", "why"), [
    ({"output": {"settings": {"properties": {"title": {}, "type": {}}}}},
     "does not declare 'selfDeclaredMadeForKids'"),
    ({"output": {"settings": {"properties": {
        "title": {}, "type": {"enum": ["private"]},
        "selfDeclaredMadeForKids": {}, "tags": {}}}}}, "does not allow type"),
    # A tags enum that does not hold the campaign's tags: refused, no crash.
    ({"output": {"settings": {"properties": {
        "title": {}, "type": {}, "selfDeclaredMadeForKids": {},
        "tags": {"enum": ["other"]}}}}}, "does not allow tags"),
    ({}, "YouTube settings could not be read"),
])
async def test_a_short_is_never_posted_blind(monkeypatch, tmp_path, settings, why):
    path = _clip_file(tmp_path, monkeypatch)
    monkeypatch.setenv("POSTIZ_YOUTUBE_CLIPS_INTEGRATION", "yt-1")
    seen = _yt_fake(monkeypatch, settings=settings)
    status, detail, _, _ = await postiz.post_youtube_clip(
        str(path), YT_TITLE, YT_CAPTION, ["ATLUS"])
    assert status == postiz.REFUSED and why in detail
    assert not any(p == "/posts" and m == "POST" for m, p, _ in seen)


async def test_shorts_readiness_reads_only(monkeypatch):
    monkeypatch.setenv("POSTIZ_YOUTUBE_CLIPS_INTEGRATION", "yt-1")
    seen = _yt_fake(monkeypatch)
    ok, said = await postiz.readiness(postiz.YT_ACCOUNT)
    assert ok is True and "Short" in said
    assert ("GET", "/integration-settings/yt-1") in [(m, p) for m, p, _ in seen]
    assert all(m == "GET" for m, _, _ in seen)


@pytest.mark.parametrize("state", ["PUBLISHED", "QUEUE"])
async def test_a_published_short_is_never_deleted_by_undo(monkeypatch, state):
    seen = _yt_fake(monkeypatch, state=state)
    ok, said = await postiz.undo("pz-9", where="YouTube")
    deleted = ("DELETE", "/posts/pz-9") in [(m, p) for m, p, _ in seen]
    if state == "QUEUE":
        assert ok and deleted
    else:
        assert not ok and not deleted
        assert said.startswith("MANUAL") and "YouTube Studio" in said
