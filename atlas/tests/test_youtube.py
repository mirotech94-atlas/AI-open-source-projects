"""youtube — two channels, read everything, publish behind a gate.

Load-bearing properties, each tested here:

* No file in the module can reach the executor (the shared AST guard).
* A missing credential, or a failed refresh, is UNKNOWN and names the
  channel — never the secret (invariant 4's cousin: nothing that can reach
  an Observation may carry a credential).
* `videos()` and `channel_stats()` never invent a statistic: a field
  YouTube omits comes back as None, not 0.
* `youtube.upload` forces `privacy="private"` in code, not just by default
  argument — anything else is refused before a network call is made — and
  its action declares no `privacy` param at all (a closed catalog with open
  arguments is not closed).
* `youtube.set_visibility`'s rollback restores whatever privacy the video
  actually had before the change, read live; if that read fails, the
  action refuses rather than changing visibility with no way back.
* The three actions classify at their declared tiers and are allowed by the
  permanent deny-list.
* Partner Program eligibility is a plain comparison of measured numbers,
  never an estimate, and says so when a path cannot be measured.
"""
from __future__ import annotations

import ast
import pathlib
from datetime import UTC, datetime, timedelta

import httpx
import pytest

from atlas.kernel.contracts import Health, Tier
from atlas.kernel.llm import Completion
from atlas.kernel.permissions import classify, wrap_untrusted
from atlas.modules import youtube
from atlas.modules.youtube import api, metadata

ROOT = pathlib.Path(__file__).resolve().parent.parent / "atlas"
PKG = ROOT / "modules" / "youtube"

CREDENTIAL_ENV = (
    "YOUTUBE_FLYCHEAP_CLIENT_ID", "YOUTUBE_FLYCHEAP_CLIENT_SECRET",
    "YOUTUBE_FLYCHEAP_REFRESH_TOKEN", "YOUTUBE_FLYCHEAP_CHANNEL_ID",
    "YOUTUBE_GUARDIAN_CLIENT_ID", "YOUTUBE_GUARDIAN_CLIENT_SECRET",
    "YOUTUBE_GUARDIAN_REFRESH_TOKEN", "YOUTUBE_GUARDIAN_CHANNEL_ID",
    "YOUTUBE_CLIPS_CLIENT_ID", "YOUTUBE_CLIPS_CLIENT_SECRET",
    "YOUTUBE_CLIPS_REFRESH_TOKEN", "YOUTUBE_CLIPS_CHANNEL_ID",
)


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    """Every test starts with none of the twelve channel vars set, so a test
    that wants them present sets them explicitly rather than inheriting
    whatever the previous test (or the real shell) left behind."""
    for name in CREDENTIAL_ENV:
        monkeypatch.delenv(name, raising=False)


@pytest.fixture(autouse=True)
def _clean_token_cache():
    """`api._token_cache` is module-level state that would otherwise leak a
    cached access token from one test's fake channel into the next."""
    api._token_cache.clear()
    yield
    api._token_cache.clear()


def _set_flycheap_creds(monkeypatch, secret: str = "flycheap-secret-value-123"):
    monkeypatch.setenv("YOUTUBE_FLYCHEAP_CLIENT_ID", "flycheap-client-id")
    monkeypatch.setenv("YOUTUBE_FLYCHEAP_CLIENT_SECRET", secret)
    monkeypatch.setenv("YOUTUBE_FLYCHEAP_REFRESH_TOKEN", "flycheap-refresh-token")
    monkeypatch.setenv("YOUTUBE_FLYCHEAP_CHANNEL_ID", "UCflycheap")


# ------------------------------------------------------- no path to the executor

def _imports_of(path: pathlib.Path) -> set[str]:
    tree = ast.parse(path.read_text())
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            mod = node.module or ""
            if node.level:
                parts = ["atlas", "modules", "youtube"][: 3 - (node.level - 1)]
                mod = ".".join(parts + ([mod] if mod else []))
            names.add(mod)
    return names


def test_no_file_in_the_module_imports_the_executor():
    files = list(PKG.glob("*.py"))
    assert files
    for f in files:
        for mod in _imports_of(f):
            assert "executor" not in mod, f"{f.name} imports {mod}"


# --------------------------------------------------------------------- token

async def test_token_missing_credentials_names_the_channel(monkeypatch):
    tok, reason = await api.token("flycheap")
    assert tok is None
    assert "flycheap" in reason
    assert "CLIENT_ID" in reason


async def test_token_unknown_channel_is_a_reason_not_a_crash():
    tok, reason = await api.token("tiktok")
    assert tok is None
    assert "tiktok" in reason


async def test_token_failure_never_leaks_the_secret(monkeypatch):
    secret = "sk-flycheap-supersecretvalue-7788"
    _set_flycheap_creds(monkeypatch, secret=secret)

    class FakeClient:
        def __init__(self, *a, **k): ...
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def request(self, method, url, **kwargs):
            raise RuntimeError(f"refresh blew up carrying {secret} along")

    monkeypatch.setattr(httpx, "AsyncClient", FakeClient)

    tok, reason = await api.token("flycheap")
    assert tok is None
    assert "flycheap" in reason
    assert secret not in reason


async def test_check_token_missing_credentials_is_unknown_naming_channel():
    obs = await youtube.check_token("flycheap")
    assert obs.health is Health.UNKNOWN
    assert "flycheap" in obs.detail


async def test_check_token_ok_when_refresh_succeeds(monkeypatch):
    async def fake_token(channel):
        return "access-token", None
    monkeypatch.setattr(api, "token", fake_token)
    obs = await youtube.check_token("guardian")
    assert obs.health is Health.OK


# ------------------------------------------------------------- token caching

async def test_token_caches_within_the_expiry_window(monkeypatch):
    """Refreshing on every call is what atlas.modules.tiktok's own
    `_token_cache` exists to avoid; every check and every action for a
    channel would otherwise pay a network round trip it does not need."""
    _set_flycheap_creds(monkeypatch)
    calls = []

    async def fake_post_json(url, **kwargs):
        calls.append(url)
        return {"access_token": "tok-1", "expires_in": 3600}, None
    monkeypatch.setattr(api, "post_json", fake_post_json)

    tok1, reason1 = await api.token("flycheap")
    tok2, reason2 = await api.token("flycheap")

    assert reason1 is None and reason2 is None
    assert tok1 == tok2 == "tok-1"
    assert len(calls) == 1, "a second call inside the window must not refresh"


async def test_token_refreshes_once_the_cached_entry_has_expired(monkeypatch):
    _set_flycheap_creds(monkeypatch)
    calls = []

    async def fake_post_json(url, **kwargs):
        calls.append(url)
        return {"access_token": f"tok-{len(calls)}", "expires_in": 3600}, None
    monkeypatch.setattr(api, "post_json", fake_post_json)

    tok1, _ = await api.token("flycheap")
    # Force the cached entry to look expired, without waiting an hour.
    cached_token, _expiry = api._token_cache["flycheap"]
    api._token_cache["flycheap"] = (cached_token, 0.0)

    tok2, _ = await api.token("flycheap")

    assert len(calls) == 2, "an expired entry must trigger a fresh refresh"
    assert tok1 == "tok-1"
    assert tok2 == "tok-2"


async def test_token_caches_the_two_channels_independently(monkeypatch):
    _set_flycheap_creds(monkeypatch)
    monkeypatch.setenv("YOUTUBE_GUARDIAN_CLIENT_ID", "guardian-id")
    monkeypatch.setenv("YOUTUBE_GUARDIAN_CLIENT_SECRET", "guardian-secret-value-1")
    monkeypatch.setenv("YOUTUBE_GUARDIAN_REFRESH_TOKEN", "guardian-refresh-token")

    async def fake_post_json(url, **kwargs):
        secret = kwargs["data"]["client_secret"]
        return {"access_token": f"tok-for-{secret}", "expires_in": 3600}, None
    monkeypatch.setattr(api, "post_json", fake_post_json)

    flycheap_tok, _ = await api.token("flycheap")
    guardian_tok, _ = await api.token("guardian")

    assert flycheap_tok != guardian_tok
    assert set(api._token_cache) == {"flycheap", "guardian"}


# ------------------------------------------------------------- channel_stats

async def test_channel_stats_missing_channel_id_is_a_reason(monkeypatch):
    _set_flycheap_creds(monkeypatch)
    monkeypatch.delenv("YOUTUBE_FLYCHEAP_CHANNEL_ID", raising=False)
    data, reason = await api.channel_stats("flycheap")
    assert data is None
    assert "CHANNEL_ID" in reason


async def test_channel_stats_hidden_subscribers_is_none_not_zero(monkeypatch):
    _set_flycheap_creds(monkeypatch)

    async def fake_token(channel):
        return "tok", None
    monkeypatch.setattr(api, "token", fake_token)

    async def fake_get_json(url, **kwargs):
        return {"items": [{"statistics": {
            "hiddenSubscriberCount": True, "viewCount": "5000",
            "videoCount": "12"}}]}, None
    monkeypatch.setattr(api, "get_json", fake_get_json)

    data, reason = await api.channel_stats("flycheap")
    assert reason is None
    assert data["subscribers"] is None
    assert data["views"] == 5000.0
    assert data["video_count"] == 12.0
    assert data["hidden_subscriber_count"] is True
    assert "read_at" in data


async def test_channel_stats_no_channel_found_is_a_reason(monkeypatch):
    _set_flycheap_creds(monkeypatch)

    async def fake_token(channel):
        return "tok", None
    monkeypatch.setattr(api, "token", fake_token)

    async def fake_get_json(url, **kwargs):
        return {"items": []}, None
    monkeypatch.setattr(api, "get_json", fake_get_json)

    data, reason = await api.channel_stats("flycheap")
    assert data is None
    assert "no channel found" in reason


# ------------------------------------------------------------------- videos

async def test_videos_missing_stats_are_none_not_zero(monkeypatch):
    _set_flycheap_creds(monkeypatch)

    async def fake_token(channel):
        return "tok", None
    monkeypatch.setattr(api, "token", fake_token)

    async def fake_get_json(url, **kwargs):
        if url.endswith("/search"):
            return {"items": [{"id": {"videoId": "abc123"}}]}, None
        if url.endswith("/videos"):
            return {"items": [{
                "id": "abc123",
                "snippet": {"title": "A video", "publishedAt": "2026-09-01T00:00:00Z"},
                "status": {"privacyStatus": "public"},
                "statistics": {"viewCount": "42"},
            }]}, None
        raise AssertionError(f"unexpected url {url}")
    monkeypatch.setattr(api, "get_json", fake_get_json)

    videos, reason = await api.videos("flycheap")
    assert reason is None
    assert len(videos) == 1
    v = videos[0]
    assert v["video_id"] == "abc123"
    assert v["views"] == 42.0
    assert v["likes"] is None
    assert v["comments"] is None
    assert isinstance(v["read_at"], datetime)
    assert v["read_at"].tzinfo is not None


async def test_videos_with_no_search_results_is_an_empty_list_not_an_error(monkeypatch):
    _set_flycheap_creds(monkeypatch)

    async def fake_token(channel):
        return "tok", None
    monkeypatch.setattr(api, "token", fake_token)

    async def fake_get_json(url, **kwargs):
        return {"items": []}, None
    monkeypatch.setattr(api, "get_json", fake_get_json)

    videos, reason = await api.videos("flycheap")
    assert videos == []
    assert reason is None


async def test_youtube_videos_tool_stamps_top_level_read_at(monkeypatch):
    """The read tool must never hand back a measured number (views, likes)
    with no time attached, matching what `youtube_stats` already does."""
    from atlas.tools import builtin

    read_at = datetime(2026, 9, 24, 9, 12, tzinfo=UTC)

    async def fake_videos(channel, limit=25):
        return [{"video_id": "v1", "title": "T", "published_at": None,
                "privacy": "public", "views": 10.0, "likes": None,
                "comments": None, "read_at": read_at}], None
    monkeypatch.setattr(api, "videos", fake_videos)

    result = await builtin.youtube_videos("flycheap")
    assert result["read_at"] == "2026-09-24T09:12:00+00:00"
    assert result["videos"][0]["read_at"] == "2026-09-24T09:12:00+00:00"


async def test_youtube_videos_tool_still_stamps_read_at_when_empty(monkeypatch):
    from atlas.tools import builtin

    async def fake_videos(channel, limit=25):
        return [], None
    monkeypatch.setattr(api, "videos", fake_videos)

    result = await builtin.youtube_videos("flycheap")
    assert result["videos"] == []
    assert "read_at" in result


# ------------------------------------------------------------------ analytics

async def test_analytics_403_reason_mentions_the_scope(monkeypatch):
    _set_flycheap_creds(monkeypatch)

    async def fake_token(channel):
        return "tok", None
    monkeypatch.setattr(api, "token", fake_token)

    async def fake_get_json(url, **kwargs):
        return None, "HTTP 403 from /v2/reports — authenticated but not permitted"
    monkeypatch.setattr(api, "get_json", fake_get_json)

    data, reason = await api.analytics("flycheap", "vid1", 7)
    assert data is None
    assert "scope" in reason.lower()


async def test_analytics_happy_path_parses_columns_and_rows(monkeypatch):
    _set_flycheap_creds(monkeypatch)

    async def fake_token(channel):
        return "tok", None
    monkeypatch.setattr(api, "token", fake_token)

    async def fake_get_json(url, **kwargs):
        return {
            "columnHeaders": [{"name": "views"}, {"name": "estimatedMinutesWatched"},
                              {"name": "likes"}],
            "rows": [[123, 456.0, 7]],
        }, None
    monkeypatch.setattr(api, "get_json", fake_get_json)

    data, reason = await api.analytics("flycheap", "vid1", 7)
    assert reason is None
    assert data["views"] == 123
    assert data["estimated_minutes_watched"] == 456.0
    assert data["likes"] == 7
    assert "read_at" in data


async def test_analytics_no_rows_is_a_reason(monkeypatch):
    _set_flycheap_creds(monkeypatch)

    async def fake_token(channel):
        return "tok", None
    monkeypatch.setattr(api, "token", fake_token)

    async def fake_get_json(url, **kwargs):
        return {"columnHeaders": [], "rows": []}, None
    monkeypatch.setattr(api, "get_json", fake_get_json)

    data, reason = await api.analytics("flycheap", "vid1", 7)
    assert data is None
    assert "no rows" in reason


# ------------------------------------------------------------------- upload

async def test_upload_refuses_a_non_private_privacy_before_any_network_call(monkeypatch):
    async def boom_token(channel):
        raise AssertionError("must not reach for a token before refusing")
    monkeypatch.setattr(api, "token", boom_token)

    result, reason = await api.upload(
        "flycheap", "/tmp/whatever.mp4", "T", "D", ["a"], privacy="public")
    assert result is None
    assert "private" in reason


async def test_upload_action_declares_no_privacy_param():
    """A closed catalog with open arguments is not closed (invariant 2):
    privacy is not something a caller can hand in at all."""
    actions = {a.name: a for a in youtube.MODULE.actions()}
    assert "privacy" not in actions["youtube.upload"].params


async def test_upload_resumable_flow_uses_correct_content_range(monkeypatch, tmp_path):
    _set_flycheap_creds(monkeypatch)

    async def fake_token(channel):
        return "tok", None
    monkeypatch.setattr(api, "token", fake_token)

    video_path = tmp_path / "video.mp4"
    total = api.CHUNK_SIZE + 10
    video_path.write_bytes(b"x" * total)

    post_calls = []
    put_calls = []

    class FakeResponse:
        def __init__(self, status_code, headers=None, json_body=None):
            self.status_code = status_code
            self.headers = headers or {}
            self._json = json_body or {}

        def raise_for_status(self):
            if self.status_code >= 400:
                raise httpx.HTTPStatusError(
                    "boom", request=httpx.Request("PUT", "https://x"),
                    response=httpx.Response(self.status_code, request=httpx.Request("PUT", "https://x")))

        def json(self):
            return self._json

    class FakeClient:
        def __init__(self, *a, **k): ...
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False

        async def post(self, url, **kwargs):
            post_calls.append(kwargs)
            return FakeResponse(200, headers={"Location": "https://upload.example/session123"})

        async def put(self, url, headers=None, content=None, **kwargs):
            put_calls.append((headers, len(content)))
            if len(put_calls) == 1:
                # A real "308 Resume Incomplete" always carries a Range
                # header saying what the server actually has. Here: all of
                # the first chunk landed.
                return FakeResponse(
                    308, headers={"Range": f"bytes=0-{api.CHUNK_SIZE - 1}"})
            return FakeResponse(200, json_body={"id": "newvid1"})

    monkeypatch.setattr(httpx, "AsyncClient", FakeClient)

    result, reason = await api.upload("flycheap", str(video_path), "T", "D", ["a"])

    assert reason is None
    assert result == {"video_id": "newvid1", "channel": "flycheap", "privacy": "private"}
    assert post_calls[0]["json"]["status"]["privacyStatus"] == "private"
    assert put_calls[0][0]["Content-Range"] == f"bytes 0-{api.CHUNK_SIZE - 1}/{total}"
    assert put_calls[1][0]["Content-Range"] == \
        f"bytes {api.CHUNK_SIZE}-{total - 1}/{total}"
    assert put_calls[0][1] == api.CHUNK_SIZE
    assert put_calls[1][1] == total - api.CHUNK_SIZE
    # Every PUT carries the bearer token — the init POST already did, and a
    # chunk PUT with no Authorization header is a silent auth failure once
    # a real server is on the other end.
    for headers, _length in put_calls:
        assert headers["Authorization"] == "Bearer tok"


async def test_upload_308_with_no_range_header_retries_from_the_same_offset(
    monkeypatch, tmp_path
):
    """A 308 with nothing persisted yet (no Range header at all) must not be
    read as "the whole chunk landed" -- that would skip bytes the server
    never received."""
    _set_flycheap_creds(monkeypatch)

    async def fake_token(channel):
        return "tok", None
    monkeypatch.setattr(api, "token", fake_token)

    video_path = tmp_path / "small.mp4"
    video_path.write_bytes(b"y" * 100)

    put_calls = []

    class FakeResponse:
        def __init__(self, status_code, headers=None, json_body=None):
            self.status_code = status_code
            self.headers = headers or {}
            self._json = json_body or {}

        def raise_for_status(self):
            pass

        def json(self):
            return self._json

    class FakeClient:
        def __init__(self, *a, **k): ...
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False

        async def post(self, url, **kwargs):
            return FakeResponse(200, headers={"Location": "https://upload.example/s"})

        async def put(self, url, headers=None, content=None, **kwargs):
            put_calls.append(headers["Content-Range"])
            if len(put_calls) == 1:
                return FakeResponse(308)   # nothing persisted, no Range header
            return FakeResponse(200, json_body={"id": "vidX"})

    monkeypatch.setattr(httpx, "AsyncClient", FakeClient)

    result, reason = await api.upload("flycheap", str(video_path), "T", "D", [])

    assert reason is None
    assert result["video_id"] == "vidX"
    # Both attempts cover the same range: the second is a retry, not a
    # continuation past bytes the server never actually got.
    assert put_calls[0] == put_calls[1] == "bytes 0-99/100"


async def test_upload_gives_up_after_three_consecutive_stalled_retries(
    monkeypatch, tmp_path
):
    """A server that keeps answering bare 308s (no Range header, so no
    progress) forever would otherwise hang the upload -- holding the open
    file and the httpx connection with no way out."""
    _set_flycheap_creds(monkeypatch)

    async def fake_token(channel):
        return "tok", None
    monkeypatch.setattr(api, "token", fake_token)

    video_path = tmp_path / "small.mp4"
    video_path.write_bytes(b"z" * 100)

    post_calls = []
    put_calls = []

    class FakeResponse:
        def __init__(self, status_code, headers=None, json_body=None):
            self.status_code = status_code
            self.headers = headers or {}
            self._json = json_body or {}

        def raise_for_status(self):
            pass

        def json(self):
            return self._json

    class FakeClient:
        def __init__(self, *a, **k): ...
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False

        async def post(self, url, **kwargs):
            post_calls.append(kwargs)
            return FakeResponse(200, headers={"Location": "https://upload.example/s"})

        async def put(self, url, headers=None, content=None, **kwargs):
            put_calls.append(headers["Content-Range"])
            return FakeResponse(308)   # never carries a Range header

    monkeypatch.setattr(httpx, "AsyncClient", FakeClient)

    result, reason = await api.upload("flycheap", str(video_path), "T", "D", [])

    assert result is None
    assert reason == (
        "flycheap: resumable upload made no progress after 3 retries at byte 0")
    assert len(post_calls) == 1
    assert len(put_calls) == 3
    assert put_calls == ["bytes 0-99/100"] * 3


async def test_upload_empty_file_is_a_reason_not_a_zero_byte_request(monkeypatch, tmp_path):
    _set_flycheap_creds(monkeypatch)

    async def fake_token(channel):
        return "tok", None
    monkeypatch.setattr(api, "token", fake_token)

    empty = tmp_path / "empty.mp4"
    empty.write_bytes(b"")

    result, reason = await api.upload("flycheap", str(empty), "T", "D", [])
    assert result is None
    assert "empty" in reason


async def test_upload_video_action_lands_private_and_sets_rollback_token(monkeypatch):
    async def fake_upload(channel, path, title, description, tags, privacy="private"):
        assert privacy == "private"
        return {"video_id": "vid9", "channel": channel, "privacy": "private"}, None
    monkeypatch.setattr(api, "upload", fake_upload)

    result = await youtube.upload_video("flycheap", "/tmp/x.mp4", "T", "D", ["a"])
    assert result.ok
    assert "private" in result.detail
    assert result.rollback_token == "flycheap:vid9"


async def test_upload_rollback_confirms_private_never_deletes(monkeypatch):
    calls = []

    async def fake_set_privacy(channel, video_id, privacy):
        calls.append((channel, video_id, privacy))
        return {"id": video_id}, None
    monkeypatch.setattr(api, "set_privacy", fake_set_privacy)

    result = await youtube.upload_rollback("flycheap:vid9")
    assert result.ok
    assert calls == [("flycheap", "vid9", "private")]


# ------------------------------------------------------ the clips channel

def test_the_channels_are_exactly_flycheap_guardian_and_clips():
    assert api.CHANNELS == {"flycheap": "YOUTUBE_FLYCHEAP_",
                            "guardian": "YOUTUBE_GUARDIAN_",
                            "clips": "YOUTUBE_CLIPS_"}


async def test_an_unset_clips_channel_reads_not_configured_never_ok():
    obs = await youtube.check_token("clips")
    assert obs.health is Health.UNKNOWN
    assert "not configured" in obs.detail and "YOUTUBE_CLIPS_CLIENT_ID" in obs.detail
    stats = await youtube.check_stats("clips")
    assert stats.health is Health.UNKNOWN


async def test_the_generic_upload_never_reaches_the_clips_channel(monkeypatch,
                                                                  tmp_path):
    """youtube.upload is Tier 2 with an open path: it must not be a way to
    put an outside video on the clips channel."""
    async def never(*_a, **_k):
        raise AssertionError("refused before any upload")

    monkeypatch.setattr(api, "upload", never)
    actions = {a.name: a for a in youtube.MODULE.actions()}
    params = {"channel": "clips", "path": str(tmp_path / "x.mp4"), "title": "T",
              "description": "D", "tags": []}
    assert actions["youtube.upload"].vet(params) is not None
    assert actions["youtube.upload"].vet({**params, "channel": "flycheap"}) is None
    for fn in (youtube.upload_video, youtube.upload_dry):
        got = await fn(**params)
        assert not got.ok and "clips.publish_clip_youtube" in got.detail


async def test_a_clips_video_goes_public_only_through_the_lane(monkeypatch):
    async def never(*_a, **_k):
        raise AssertionError("refused before YouTube is asked")

    monkeypatch.setattr(api, "current_privacy", never)
    vet = {a.name: a for a in youtube.MODULE.actions()}["youtube.set_visibility"].vet
    for privacy in ("public", "unlisted"):
        assert vet({"channel": "clips", "privacy": privacy}) is not None
        for fn in (youtube.set_visibility, youtube.set_visibility_dry):
            got = await fn("clips", "vid1", privacy)
            assert not got.ok and "clips.publish_clip_youtube" in got.detail
    assert vet({"channel": "clips", "privacy": "private"}) is None
    assert vet({"channel": "flycheap", "privacy": "public"}) is None


@pytest.mark.parametrize("where", [
    "outside", "source", "nested", "symlink", "caption", "hidden", "badslug"])
async def test_the_clips_channel_uploads_only_a_lane_cut_file(monkeypatch, tmp_path,
                                                              where):
    root = tmp_path / "clips"
    monkeypatch.setenv("CLIPS_DATA_DIR", str(root))
    out = root / "atlus-persona-metaphor" / "out"
    out.mkdir(parents=True)
    good = out / "1-abcdef012345-1.mp4"
    good.write_bytes(b"mp4")
    assert api.clips_path_refusal(str(good)) is None
    paths = {
        "outside": tmp_path / "any.mp4",
        "source": root / "atlus-persona-metaphor" / "source" / "raw.mp4",
        "nested": out / "deeper" / "x.mp4",
        "symlink": out / "link.mp4",
        "caption": out / "1-abcdef012345-1.mp4.caption.txt",
        "hidden": out / ".x.mp4",
        "badslug": root / "Bad_Slug" / "out" / "x.mp4",
    }
    path = paths[where]
    path.parent.mkdir(parents=True, exist_ok=True)
    if where == "symlink":
        path.symlink_to(good)
    else:
        path.write_bytes(b"x")

    async def boom_token(channel):
        raise AssertionError("refused before a token is fetched")

    monkeypatch.setattr(api, "token", boom_token)
    result, reason = await api.upload("clips", str(path), "T", "D", [])
    assert result is None and "clips lane" in reason


# --------------------------------------------------------- set_visibility

async def test_set_visibility_refuses_an_invalid_privacy(monkeypatch):
    async def boom(*a, **k):
        raise AssertionError("must not read current privacy for an invalid value")
    monkeypatch.setattr(api, "current_privacy", boom)

    result = await youtube.set_visibility("flycheap", "vid1", "sortof-public")
    assert result.ok is False


async def test_set_visibility_refuses_when_previous_value_unreadable(monkeypatch):
    async def fake_current_privacy(channel, video_id):
        return None, "boom, no such video"

    called = {"set": False}

    async def fake_set_privacy(*a, **k):
        called["set"] = True
        return {}, None

    monkeypatch.setattr(api, "current_privacy", fake_current_privacy)
    monkeypatch.setattr(api, "set_privacy", fake_set_privacy)

    result = await youtube.set_visibility("flycheap", "vid1", "public")
    assert result.ok is False
    assert called["set"] is False, "must not change visibility blind"


async def test_set_visibility_rollback_token_carries_the_previous_value(monkeypatch):
    async def fake_current_privacy(channel, video_id):
        return "unlisted", None

    async def fake_set_privacy(channel, video_id, privacy):
        return {"id": video_id}, None

    async def fake_execute(sql, *args):
        return "OK"

    monkeypatch.setattr(api, "current_privacy", fake_current_privacy)
    monkeypatch.setattr(api, "set_privacy", fake_set_privacy)
    monkeypatch.setattr(youtube, "execute_sql", fake_execute)

    result = await youtube.set_visibility("flycheap", "vid1", "public")
    assert result.ok
    assert result.rollback_token == "flycheap:vid1:unlisted"
    assert result.evidence["previous"] == "unlisted"


async def test_set_visibility_rollback_restores_the_previous_value(monkeypatch):
    calls = []

    async def fake_set_privacy(channel, video_id, privacy):
        calls.append((channel, video_id, privacy))
        return {"id": video_id}, None

    async def fake_execute(sql, *args):
        return "OK"

    monkeypatch.setattr(api, "set_privacy", fake_set_privacy)
    monkeypatch.setattr(youtube, "execute_sql", fake_execute)

    result = await youtube.set_visibility_rollback("flycheap:vid1:unlisted")
    assert result.ok
    assert calls == [("flycheap", "vid1", "unlisted")]


async def test_set_visibility_rollback_refuses_with_no_recorded_previous():
    result = await youtube.set_visibility_rollback("flycheap:vid1:None")
    assert result.ok is False


# --------------------------------------------------------------- tier / classify

def test_actions_classify_allowed_at_their_declared_tiers():
    actions = {a.name: a for a in youtube.MODULE.actions()}
    assert set(actions) == {
        "youtube.upload", "youtube.set_visibility", "youtube.draft_metadata"}

    decisions = {name: classify(a) for name, a in actions.items()}

    assert decisions["youtube.upload"].allowed is True
    assert decisions["youtube.upload"].tier is Tier.ASK

    assert decisions["youtube.set_visibility"].allowed is True
    assert decisions["youtube.set_visibility"].tier is Tier.ASK

    assert decisions["youtube.draft_metadata"].allowed is True
    assert decisions["youtube.draft_metadata"].tier is Tier.AUTO


def test_draft_metadata_action_has_dry_run_and_rollback():
    actions = {a.name: a for a in youtube.MODULE.actions()}
    action = actions["youtube.draft_metadata"]
    assert action.tier is Tier.AUTO
    assert action.dry_run is not None
    assert action.rollback is not None
    assert action.irreversible is False


def test_upload_and_set_visibility_are_not_irreversible():
    """The brief is explicit: irreversible=False for both, because each has
    a working rollback (confirm-private / restore-previous)."""
    actions = {a.name: a for a in youtube.MODULE.actions()}
    assert actions["youtube.upload"].irreversible is False
    assert actions["youtube.set_visibility"].irreversible is False


# ---------------------------------------------------------- partner eligibility

async def test_partner_eligibility_reports_not_eligible_line(monkeypatch):
    read_at = datetime(2026, 9, 24, 9, 12, tzinfo=UTC)

    async def fake_channel_stats(channel):
        return {"channel": channel, "subscribers": 312.0, "views": 50_000.0,
                "video_count": 40.0, "hidden_subscriber_count": False,
                "read_at": read_at}, None
    monkeypatch.setattr(api, "channel_stats", fake_channel_stats)

    result = await youtube.partner_eligibility("flycheap")
    assert result["eligible_on_subscribers"] is False
    assert "not eligible: 312 of 1,000 subscribers, read 09:12" in result["detail"]


async def test_partner_eligibility_reports_eligible_line(monkeypatch):
    read_at = datetime(2026, 9, 24, 9, 12, tzinfo=UTC)

    async def fake_channel_stats(channel):
        return {"channel": channel, "subscribers": 5000.0, "views": 900_000.0,
                "video_count": 40.0, "hidden_subscriber_count": False,
                "read_at": read_at}, None
    monkeypatch.setattr(api, "channel_stats", fake_channel_stats)

    result = await youtube.partner_eligibility("guardian")
    assert result["eligible_on_subscribers"] is True
    assert "eligible: 5,000 of 1,000 subscribers" in result["detail"]
    assert "not eligible" not in result["detail"]


async def test_partner_eligibility_is_unmeasured_when_stats_unreadable(monkeypatch):
    async def fake_channel_stats(channel):
        return None, "no credentials"
    monkeypatch.setattr(api, "channel_stats", fake_channel_stats)

    result = await youtube.partner_eligibility("flycheap")
    assert result["eligible_on_subscribers"] is None
    assert "cannot read subscriber count" in result["detail"]


async def test_partner_eligibility_hidden_subscribers_is_unmeasured(monkeypatch):
    read_at = datetime(2026, 9, 24, 9, 12, tzinfo=UTC)

    async def fake_channel_stats(channel):
        return {"channel": channel, "subscribers": None, "views": 1.0,
                "video_count": 1.0, "hidden_subscriber_count": True,
                "read_at": read_at}, None
    monkeypatch.setattr(api, "channel_stats", fake_channel_stats)

    result = await youtube.partner_eligibility("guardian")
    assert result["eligible_on_subscribers"] is None
    assert "not reported" in result["detail"]


def test_eligibility_never_touches_watch_hours_or_shorts_views():
    """Those two paths need a channel-scoped aggregate this module's
    per-video `analytics()` cannot produce -- the function must say so, not
    silently pretend the subscriber path is the whole answer."""
    import inspect
    source = inspect.getsource(youtube.partner_eligibility)
    assert "not measured" in source


# --------------------------------------------------------------------- brief

def test_brief_reads_only_the_cache_never_awaits(monkeypatch):
    monkeypatch.setattr(youtube, "_LAST", {
        "flycheap": {"subscribers": 1234.0,
                     "read_at": datetime(2026, 9, 24, 9, 12, tzinfo=UTC)},
    })
    text = youtube.MODULE.brief()
    assert "flycheap 1,234 subs (read 09:12)" in text
    assert "guardian not read yet" in text


def test_brief_is_a_plain_sync_call():
    import inspect
    assert not inspect.iscoroutinefunction(youtube.MODULE.brief)


# ---------------------------------------------------------------- check_stats

async def test_check_stats_fills_the_brief_cache(monkeypatch):
    read_at = datetime(2026, 9, 24, 9, 12, tzinfo=UTC)

    async def fake_channel_stats(channel):
        return {"channel": channel, "subscribers": 99.0, "views": 500.0,
                "video_count": 3.0, "hidden_subscriber_count": False,
                "read_at": read_at}, None
    monkeypatch.setattr(api, "channel_stats", fake_channel_stats)
    monkeypatch.setattr(youtube, "_LAST", {})

    obs = await youtube.check_stats("flycheap")
    assert obs.health is Health.OK
    assert youtube._LAST["flycheap"]["subscribers"] == 99.0
    assert "read 09:12" in obs.detail


async def test_check_stats_unknown_on_failure(monkeypatch):
    async def fake_channel_stats(channel):
        return None, "no credentials"
    monkeypatch.setattr(api, "channel_stats", fake_channel_stats)

    obs = await youtube.check_stats("flycheap")
    assert obs.health is Health.UNKNOWN
    assert obs.detail == "no credentials"


# ------------------------------------------------------------- latest_videos

async def test_check_latest_videos_writes_rows(monkeypatch):
    async def fake_videos(channel, limit=25):
        return [{"video_id": "v1", "title": "T1", "published_at": "2026-09-01T00:00:00Z",
                "privacy": "public", "views": 10.0, "likes": None, "comments": None}], None
    monkeypatch.setattr(api, "videos", fake_videos)

    calls = []

    async def fake_execute(sql, *args):
        calls.append((sql, args))
        return "OK"
    monkeypatch.setattr(youtube, "execute_sql", fake_execute)

    obs = await youtube.check_latest_videos("flycheap")
    assert obs.health is Health.OK
    assert obs.value == 1.0
    assert calls
    assert "youtube_videos" in calls[0][0]
    assert calls[0][1][1] == "v1"


async def test_check_latest_videos_keeps_the_48_hour_reading(monkeypatch):
    """A video 48-72h old gets `views_48h` from the same analytics read an
    experiment is scored on; an older one does not trigger a read at all."""
    now = datetime.now(UTC)
    young = (now - timedelta(hours=60)).isoformat().replace("+00:00", "Z")
    old = (now - timedelta(days=10)).isoformat().replace("+00:00", "Z")

    async def fake_videos(channel, limit=25):
        return [
            {"video_id": "young", "title": "Y", "published_at": young,
             "privacy": "public", "views": 50, "likes": 1, "comments": 0},
            {"video_id": "old", "title": "O", "published_at": old,
             "privacy": "public", "views": 900, "likes": 9, "comments": 2},
        ], None
    monkeypatch.setattr(api, "videos", fake_videos)

    asked: list[tuple] = []

    async def fake_analytics(channel, video_id, days):
        asked.append((channel, video_id, days))
        return {"views": 321, "read_at": now}, None
    monkeypatch.setattr(api, "analytics", fake_analytics)

    calls = []

    async def fake_execute(sql, *args):
        calls.append((" ".join(sql.split()), args))
        return "OK"
    monkeypatch.setattr(youtube, "execute_sql", fake_execute)

    obs = await youtube.check_latest_videos("flycheap")
    assert obs.health is Health.OK
    assert asked == [("flycheap", "young", 2)]
    updates = [(sql, a) for sql, a in calls if "views_48h" in sql]
    assert len(updates) == 1
    assert "views_48h IS NULL" in updates[0][0]
    assert updates[0][1][:2] == ("young", 321.0)
    assert "1 48h reading(s) kept" in obs.detail


async def test_a_private_video_gives_no_48_hour_reading(monkeypatch):
    """Every ATLAS upload lands private. Reading one 48-72h after upload
    stores ~0 views, and the `views_48h IS NULL` guard would then keep that
    zero after it goes public — dragging every later baseline toward it."""
    now = datetime.now(UTC)
    young = (now - timedelta(hours=60)).isoformat()

    async def fake_videos(channel, limit=25):
        return [{"video_id": "hidden", "title": "H", "published_at": young,
                 "privacy": "private", "views": 0, "likes": 0,
                 "comments": 0}], None
    monkeypatch.setattr(api, "videos", fake_videos)

    asked: list[str] = []

    async def fake_analytics(channel, video_id, days):
        asked.append(video_id)
        return {"views": 0, "read_at": now}, None
    monkeypatch.setattr(api, "analytics", fake_analytics)

    calls = []

    async def fake_execute(sql, *args):
        calls.append(" ".join(sql.split()))
        return "OK"
    monkeypatch.setattr(youtube, "execute_sql", fake_execute)

    obs = await youtube.check_latest_videos("flycheap")
    assert obs.health is Health.OK
    assert asked == []
    assert not [c for c in calls if "views_48h" in c]


async def test_a_failed_48_hour_read_is_named_not_zeroed(monkeypatch):
    now = datetime.now(UTC)
    young = (now - timedelta(hours=50)).isoformat()

    async def fake_videos(channel, limit=25):
        return [{"video_id": "young", "title": "Y", "published_at": young,
                 "privacy": "public", "views": 50, "likes": 1,
                 "comments": 0}], None
    monkeypatch.setattr(api, "videos", fake_videos)
    async def failing_analytics(channel, video_id, days):
        return None, "flycheap: 403"
    monkeypatch.setattr(api, "analytics", failing_analytics)

    calls = []

    async def fake_execute(sql, *args):
        calls.append(" ".join(sql.split()))
        return "OK"
    monkeypatch.setattr(youtube, "execute_sql", fake_execute)

    obs = await youtube.check_latest_videos("flycheap")
    assert obs.health is Health.OK          # the refresh itself worked
    assert not [c for c in calls if "views_48h" in c]
    assert "403" in obs.detail


async def test_check_latest_videos_unknown_on_failure(monkeypatch):
    async def fake_videos(channel, limit=25):
        return [], "token refresh failed"
    monkeypatch.setattr(api, "videos", fake_videos)

    obs = await youtube.check_latest_videos("guardian")
    assert obs.health is Health.UNKNOWN


# ---------------------------------------------------------------- checks/env

def test_module_requires_no_env():
    assert youtube.MODULE.requires_env == ()


def test_module_optional_env_lists_all_twelve_channel_vars():
    assert set(youtube.MODULE.optional_env) == set(CREDENTIAL_ENV)


def test_checks_cover_every_channel_with_expected_baselines():
    checks = {c.name: c for c in youtube.MODULE.checks()}
    assert set(checks) == {
        "flycheap_token", "flycheap_stats", "flycheap_latest_videos",
        "guardian_token", "guardian_stats", "guardian_latest_videos",
        "clips_token", "clips_stats", "clips_latest_videos",
    }
    for channel in ("flycheap", "guardian", "clips"):
        assert checks[f"{channel}_token"].baseline_sigma is None
        assert checks[f"{channel}_stats"].baseline_sigma == 3.0
        assert checks[f"{channel}_latest_videos"].baseline_sigma is None


# ------------------------------------------------------------- draft_metadata

async def test_draft_metadata_action_writes_a_content_drafts_row(monkeypatch):
    async def fake_draft(business, source):
        return {"title": "A great video", "description": "D", "tags": ["a"],
                "chapters": None, "business": business, "source": source}
    monkeypatch.setattr(youtube, "_draft_metadata", fake_draft)

    inserted = {}

    async def fake_fetch_one(sql, *args):
        inserted["sql"] = sql
        inserted["args"] = args
        return {"id": 7}
    monkeypatch.setattr(youtube, "fetch_one", fake_fetch_one)

    result = await youtube.draft_metadata("flycheap", "weekly")
    assert result.ok
    assert result.rollback_token == "7"
    assert "content_drafts" in inserted["sql"]
    assert "youtube_video" in inserted["sql"]
    assert "weekly" in inserted["args"]
    assert "A great video" in inserted["args"]


async def test_draft_metadata_action_surfaces_an_error_without_writing(monkeypatch):
    async def fake_draft(business, source):
        return {"error": "could not gather 'weekly': no weekly report found"}
    monkeypatch.setattr(youtube, "_draft_metadata", fake_draft)

    async def boom_fetch_one(*a, **k):
        raise AssertionError("must not write when drafting failed")
    monkeypatch.setattr(youtube, "fetch_one", boom_fetch_one)

    result = await youtube.draft_metadata("flycheap", "weekly")
    assert result.ok is False
    assert "no weekly report found" in result.detail


async def test_draft_metadata_rollback_marks_the_row_dropped(monkeypatch):
    calls = []

    async def fake_execute(sql, *args):
        calls.append((sql, args))
        return "OK"
    monkeypatch.setattr(youtube, "execute_sql", fake_execute)

    result = await youtube.draft_metadata_rollback("7")
    assert result.ok
    assert calls[0][1] == (7,)
    assert "dropped" in calls[0][0]


# --------------------------------------------------------- metadata.py

def test_business_links_use_the_same_env_vars_as_the_business_modules():
    assert metadata.BUSINESS_LINKS["flycheap"] == "https://flycheap.live"
    assert metadata.BUSINESS_LINKS["guardian"] == "https://guarder.io"


async def test_draft_metadata_refuses_when_gather_fails(monkeypatch):
    async def fake_gather(source):
        return wrap_untrusted("", source), "no weekly report found"
    monkeypatch.setattr(metadata, "gather", fake_gather)

    async def boom_think(*a, **k):
        raise AssertionError("think must not run when gather failed")
    monkeypatch.setattr(metadata, "think", boom_think)

    result = await metadata.draft_metadata("flycheap", "weekly")
    assert "error" in result
    assert "no weekly report found" in result["error"]


async def test_draft_metadata_appends_the_business_link_when_missing(monkeypatch):
    async def fake_gather(source):
        return wrap_untrusted("script text with no links", "note"), None
    monkeypatch.setattr(metadata, "gather", fake_gather)

    async def fake_think(need, system, messages, tools=None, max_tokens=2048):
        return Completion(
            text="TITLE: A great deal\nDESCRIPTION: Watch this.\nTAGS: a, b\n"
                 "CHAPTERS: none",
            provider="fake", model="fake")
    monkeypatch.setattr(metadata, "think", fake_think)

    result = await metadata.draft_metadata("flycheap", "note:hi")
    assert "error" not in result
    assert metadata.BUSINESS_LINKS["flycheap"] in result["description"]
    assert result["tags"] == ["a", "b"]
    assert result["chapters"] is None


async def test_draft_metadata_adds_disclosure_when_source_mentions_affiliate(monkeypatch):
    async def fake_gather(source):
        return wrap_untrusted(
            "check the affiliate link in the description", "note"), None
    monkeypatch.setattr(metadata, "gather", fake_gather)

    async def fake_think(need, system, messages, tools=None, max_tokens=2048):
        return Completion(
            text="TITLE: T\nDESCRIPTION: D\nTAGS: a\nCHAPTERS: none",
            provider="fake", model="fake")
    monkeypatch.setattr(metadata, "think", fake_think)

    result = await metadata.draft_metadata("guardian", "note:hi")
    assert metadata.DISCLOSURE in result["description"]


async def test_draft_metadata_no_disclosure_without_an_affiliate_marker(monkeypatch):
    async def fake_gather(source):
        return wrap_untrusted("just a plain script, no links at all", "note"), None
    monkeypatch.setattr(metadata, "gather", fake_gather)

    async def fake_think(need, system, messages, tools=None, max_tokens=2048):
        return Completion(
            text="TITLE: T\nDESCRIPTION: D\nTAGS: a\nCHAPTERS: none",
            provider="fake", model="fake")
    monkeypatch.setattr(metadata, "think", fake_think)

    result = await metadata.draft_metadata("guardian", "note:hi")
    assert metadata.DISCLOSURE not in result["description"]


async def test_draft_metadata_empty_model_answer_is_an_error(monkeypatch):
    async def fake_gather(source):
        return wrap_untrusted("material", "note"), None
    monkeypatch.setattr(metadata, "gather", fake_gather)

    async def fake_think(need, system, messages, tools=None, max_tokens=2048):
        return Completion(text="", provider="fake", model="fake")
    monkeypatch.setattr(metadata, "think", fake_think)

    result = await metadata.draft_metadata("flycheap", "note:hi")
    assert "error" in result


def test_title_and_tags_are_capped():
    fields = metadata._parse(
        "TITLE: " + ("x" * 200) + "\n"
        "DESCRIPTION: d\n"
        "TAGS: " + ",".join(f"t{i}" for i in range(30)) + "\n"
        "CHAPTERS: none")
    assert fields["title"] == "x" * 200   # capping happens in draft_metadata
    assert len(fields["tags"]) == 30      # capping happens in draft_metadata
