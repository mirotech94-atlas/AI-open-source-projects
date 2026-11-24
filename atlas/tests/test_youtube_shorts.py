"""YouTube Shorts through Postiz (Miro, 2026-09-29): the best AI reel of the
week, three a week, curated -- never a mirror.

What is load-bearing:

* the Postiz YouTube leg: only the integration `POSTIZ_YOUTUBE_INTEGRATION`
  names, only among YouTube integrations; the settings Postiz verified
  (`__type: youtube`, `title`, `type: public`, `selfDeclaredMadeForKids:
  "no"`, `tags: [{value, label}]`) and nothing it does not declare; every
  failure raised, never silent;
* the pick: an AI-stories-lane reel posted in the last 7 days, not on
  YouTube yet, ranked by read views (or newest, said so), through a gate:
  1080x1920 with sound, <= 60 s, the caption guards, series order;
* `content.publish_short`: Tier 1, media_id only, claimed once, recorded in
  reel_posts as 'youtube', rolled back by deleting a queued post or a
  MANUAL note for a published one;
* leg sync never treats YouTube as a leg;
* the `shorts_lane` check: UNKNOWN when unset or unreachable, never OK.
"""
from __future__ import annotations

import json
from datetime import UTC, date, datetime, timedelta

import httpx
import pytest

from atlas.kernel import executor, state
from atlas.kernel import reels as day
from atlas.kernel.contracts import Health, Tier
from atlas.kernel.permissions import classify as tier_of
from atlas.kernel.registry import REGISTRY
from atlas.modules import content
from atlas.modules.content import crosspost, ffmpeg, football, legsync, postiz, shorts
from atlas.modules.content.drafts import AI_LABEL

KEY = "pz-secret-key"
_REAL = httpx.AsyncClient          # captured once: _fake may run twice a test
YT_ID = "cmun6w6hm0fcfno0yqjqptqmo"
NOW = datetime.now(UTC)
CAPTION = (f"He saved the wrong seat.\nA quiet bus, a louder secret. "
           f"#aistory #storytime\n\n{AI_LABEL}")
# Postiz's own answer for `youtube`, read 2026-09-29 (integrationSchema).
YT_SCHEMA = {"output": {
    "rules": "YouTube must have on video attachment, it cannot be empty",
    "maxLength": 5000,
    "settings": {"properties": {
        "title": {"not": {"type": "null"}, "maxLength": 100, "type": "string",
                  "minLength": 2},
        "type": {"not": {"type": "null"}, "type": "string",
                 "enum": ["public", "private", "unlisted"]},
        "selfDeclaredMadeForKids": {"type": "string", "enum": ["yes", "no"]},
        "thumbnail": {"properties": {"id": {"type": "string"},
                                     "path": {"type": "string"}},
                      "type": "object", "required": ["id", "path"]},
        "tags": {"$ref": "#/definitions/Array", "type": "array",
                 "items": {"properties": {"value": {"type": "string"},
                                          "label": {"type": "string"}},
                           "type": "object", "required": ["value", "label"]}}},
        "type": "object", "required": ["title", "type"]},
    "tools": []}}
INTEGRATIONS = [
    {"id": "cmumubciv07romt0yeqqo60ib", "name": "atlasclips",
     "identifier": "tiktok-business", "disabled": False},
    {"id": "cmugfna3v00ucmr0yrk0svxnh", "name": "atlasstories7",
     "identifier": "tiktok-business", "disabled": False},
    {"id": YT_ID, "name": "AtlasClips", "identifier": "youtube", "disabled": False},
]


@pytest.fixture(autouse=True)
def keyed(monkeypatch):
    monkeypatch.setenv("POSTIZ_API_KEY", KEY)
    monkeypatch.setenv("POSTIZ_YOUTUBE_INTEGRATION", YT_ID)
    monkeypatch.delenv("POSTIZ_API_URL", raising=False)
    monkeypatch.delenv("SHORTS_PER_WEEK", raising=False)


def _fake(monkeypatch, *, integrations=None, settings=YT_SCHEMA, post=None,
          post_status=200, state_="PUBLISHED", unreachable=False):
    seen: list[tuple[str, str, bytes]] = []

    def handler(request):
        assert request.headers["authorization"] == KEY
        if unreachable:
            raise httpx.ConnectError("refused", request=request)
        path = request.url.path.removeprefix("/public/v1")
        seen.append((request.method, path, request.content))
        if (request.method, path) == ("POST", "/upload"):
            return httpx.Response(200, json={"id": "up-1", "path":
                                             "https://uploads.postiz.com/short.mp4"})
        if path == "/integrations":
            return httpx.Response(200, json=INTEGRATIONS if integrations is None
                                  else integrations)
        if path == f"/integration-settings/{YT_ID}":
            return httpx.Response(200, json=settings)
        if (request.method, path) == ("POST", "/posts"):
            return httpx.Response(post_status, json=post if post is not None
                                  else [{"postId": "yt-post-1",
                                         "integration": YT_ID}])
        if (request.method, path) == ("GET", "/posts"):
            return httpx.Response(200, json={"posts": [{"id": "yt-post-1",
                                                        "state": state_}]})
        if (request.method, path) == ("DELETE", "/posts/yt-post-1"):
            return httpx.Response(200, json={"ok": True})
        return httpx.Response(500)

    real = _REAL

    def factory(*a, **k):
        k.pop("transport", None)
        return real(*a, transport=httpx.MockTransport(handler), **k)

    monkeypatch.setattr(postiz.httpx, "AsyncClient", factory)
    return seen


def _body(seen) -> dict:
    return json.loads(next(b for m, p, b in seen if (m, p) == ("POST", "/posts")))


# ------------------------------------------------------------ the Postiz leg

def test_the_integration_is_only_the_one_the_env_names():
    assert postiz.pick_youtube(INTEGRATIONS) == (YT_ID, "AtlasClips")


def test_the_integration_may_be_named_by_its_name(monkeypatch):
    monkeypatch.setenv("POSTIZ_YOUTUBE_INTEGRATION", "@atlasclips")
    # "atlasclips" is also a TikTok's name: only YouTube ones are considered.
    assert postiz.pick_youtube(INTEGRATIONS)[0] == YT_ID


def test_the_mcp_listing_shape_platform_is_read_too():
    listed = [{"id": YT_ID, "name": "AtlasClips", "platform": "youtube"}]
    assert postiz.pick_youtube(listed)[0] == YT_ID


def test_unset_is_never_guessed(monkeypatch):
    monkeypatch.delenv("POSTIZ_YOUTUBE_INTEGRATION")
    with pytest.raises(postiz.PostizError, match="never guessed"):
        postiz.pick_youtube(INTEGRATIONS)
    assert "POSTIZ_YOUTUBE_INTEGRATION" in (postiz.youtube_gap() or "")


def test_a_tiktok_id_in_the_env_never_routes_a_short_there(monkeypatch):
    monkeypatch.setenv("POSTIZ_YOUTUBE_INTEGRATION", "cmugfna3v00ucmr0yrk0svxnh")
    with pytest.raises(postiz.PostizError, match="no enabled YouTube"):
        postiz.pick_youtube(INTEGRATIONS)


def test_a_disabled_or_duplicated_channel_is_refused():
    off = [{**INTEGRATIONS[2], "disabled": True}]
    with pytest.raises(postiz.PostizError, match="no enabled"):
        postiz.pick_youtube(off)
    two = [INTEGRATIONS[2], {**INTEGRATIONS[2]}]
    with pytest.raises(postiz.PostizError, match="more than one"):
        postiz.pick_youtube(two)


def test_the_verified_schema_accepts_exactly_what_a_short_sends():
    wanted = postiz.youtube_settings("He saved the wrong seat", ["aistory"])
    assert wanted == {"__type": "youtube", "title": "He saved the wrong seat",
                      "type": "public", "selfDeclaredMadeForKids": "no",
                      "tags": [{"value": "aistory", "label": "aistory"}]}
    assert postiz.validate_youtube(YT_SCHEMA, wanted) is None


def test_the_schema_refuses_undeclared_disallowed_and_missing_keys():
    base = postiz.youtube_settings("A title", [])
    assert "does not declare 'madeWithAi'" in postiz.validate_youtube(
        YT_SCHEMA, {**base, "madeWithAi": True})
    assert "does not allow selfDeclaredMadeForKids=False" in postiz.validate_youtube(
        YT_SCHEMA, {**base, "selfDeclaredMadeForKids": False})
    no_type = {k: v for k, v in base.items() if k != "type"}
    assert "requires type" in postiz.validate_youtube(YT_SCHEMA, no_type)
    assert "could not be read" in postiz.validate_youtube("??", base)


async def test_a_short_is_uploaded_then_posted_public_not_for_kids(monkeypatch):
    seen = _fake(monkeypatch)
    post_id, url = await postiz.post_youtube_short(
        b"MP4BYTES", "He saved the wrong seat", "desc\n\nMade with AI", ["aistory"])
    assert post_id == "yt-post-1" and url is None      # no URL returned: none made
    assert [(m, p) for m, p, _ in seen] == [
        ("POST", "/upload"), ("GET", "/integrations"),
        ("GET", f"/integration-settings/{YT_ID}"), ("POST", "/posts")]
    assert b"MP4BYTES" in seen[0][2]
    body = _body(seen)
    post = body["posts"][0]
    assert body["type"] == "now" and post["integration"] == {"id": YT_ID}
    assert post["value"][0]["content"] == "desc\n\nMade with AI"
    assert post["value"][0]["image"] == [{"id": "up-1", "path":
                                          "https://uploads.postiz.com/short.mp4"}]
    assert post["settings"] == {"__type": "youtube",
                                "title": "He saved the wrong seat",
                                "type": "public", "selfDeclaredMadeForKids": "no",
                                "tags": [{"value": "aistory", "label": "aistory"}]}


async def test_only_a_youtube_url_postiz_returned_is_kept(monkeypatch):
    _fake(monkeypatch, post=[{"postId": "yt-post-1",
                              "releaseURL": "https://youtube.com/shorts/abcDEF12345"}])
    assert (await postiz.post_youtube_short(b"x", "Title", "d", []))[1] == (
        "https://youtube.com/shorts/abcDEF12345")
    _fake(monkeypatch, post=[{"postId": "yt-post-1",
                              "releaseURL": "https://evil.example/shorts/x"}])
    assert (await postiz.post_youtube_short(b"x", "Title", "d", []))[1] is None


async def test_every_failure_is_raised_never_silent(monkeypatch):
    seen = _fake(monkeypatch, post_status=500)
    with pytest.raises(postiz.PostizError, match="Postiz post: HTTP 500"):
        await postiz.post_youtube_short(b"x", "Title", "d", [])
    _fake(monkeypatch, post={"ok": True})
    with pytest.raises(postiz.PostizError, match="named no post id"):
        await postiz.post_youtube_short(b"x", "Title", "d", [])
    seen = _fake(monkeypatch, settings={"output": {"settings": {"properties": {
        "title": {}}, "required": ["title", "type"]}}})
    with pytest.raises(postiz.PostizError, match="does not declare 'type'"):
        await postiz.post_youtube_short(b"x", "Title", "d", [])
    assert ("POST", "/posts") not in [(m, p) for m, p, _ in seen]
    seen = _fake(monkeypatch)
    with pytest.raises(postiz.PostizError, match="title is 101"):
        await postiz.post_youtube_short(b"x", "t" * 101, "d", [])
    with pytest.raises(postiz.PostizError, match="'<' and '>'"):
        await postiz.post_youtube_short(b"x", "a <b> title", "d", [])
    assert seen == []                    # refused before any request


async def test_the_key_never_reaches_a_reason(monkeypatch):
    _fake(monkeypatch, unreachable=True)
    with pytest.raises(postiz.PostizError) as err:
        await postiz.post_youtube_short(b"x", "Title", "d", [])
    assert KEY not in str(err.value) and "unreachable" in str(err.value)


# ------------------------------------------------------------ the words

def test_the_title_is_the_reels_own_never_rewritten():
    assert shorts.title_for("reel_format:3", "Reel: twist", CAPTION) == (
        "He saved the wrong seat.")
    assert shorts.title_for("series:4:2", "The Night Bus — Part 2", CAPTION) == (
        "The Night Bus — Part 2")
    assert shorts.title_for("reel_format:3", "Course reel: lesson 2",
                            f"#ai\n\n{AI_LABEL}") == "lesson 2"
    long = shorts.title_for("reel_format:3", "", "word " * 40 + f"\n\n{AI_LABEL}")
    assert len(long) <= 100 and long.endswith("…") and "  " not in long


def test_the_description_is_the_caption_plus_the_disclosure_and_tags_its_hashtags():
    desc = shorts.description_for(CAPTION)
    assert desc.startswith(CAPTION) and desc.endswith(shorts.DISCLOSURE)
    assert AI_LABEL in desc and "AI-generated" in desc
    assert shorts.tags_for(CAPTION) == ["aistory", "storytime"]


def test_rank_by_read_views_else_newest_with_a_note():
    old, new = NOW - timedelta(days=3), NOW - timedelta(hours=3)
    read = NOW - timedelta(hours=1)
    ranked, note = shorts.rank([
        shorts.Candidate(1, old, CAPTION, 900.0, read),
        shorts.Candidate(2, new, CAPTION, None, None),
        shorts.Candidate(3, new, CAPTION, 4000.0, read)])
    assert [c.media_id for c in ranked] == [3, 1, 2]
    assert "ranked by views read" in note and f"{read:%H:%M} UTC" in note
    ranked, note = shorts.rank([shorts.Candidate(1, old, CAPTION),
                                shorts.Candidate(2, new, CAPTION)])
    assert [c.media_id for c in ranked] == [2, 1] and "newest" in note


def test_next_due_is_mon_wed_fri():
    monday = date(2026, 9, 28)
    assert shorts.next_due(monday, 3, 0) == monday
    assert shorts.next_due(monday, 3, 1) == date(2026, 9, 30)
    assert shorts.next_due(date(2026, 10, 2), 3, 3) == date(2026, 10, 5)
    assert shorts.next_due(monday, 0, 0) is None


# ------------------------------------------------------------ the fake world

def _row(rid, at, event, subject, data):
    return {"id": rid, "at": at, "event": event, "subject": subject,
            "payload": json.dumps({"event": event, "subject": subject,
                                   "data": data})}


def run_rows(media_id: int, *, at: datetime, caption: str = CAPTION,
             rid: int = 1, subject: str = "content.autopublish_reel") -> list[dict]:
    return [
        _row(rid, at - timedelta(seconds=40), "execute.start", subject,
             {"params": {"media_id": media_id, "caption": caption}}),
        _row(rid + 1, at, "execute.finish", subject,
             {"ok": True, "detail": f"reel {media_id}: TikTok (via Postiz): posted",
              "evidence": {"instagram": {"status": "posted", "detail": ""},
                           "facebook": {"status": "posted", "detail": ""},
                           "tiktok": {"status": "posted", "detail": ""}}}),
    ]


class World(state.MemoryBackend):
    """actions_log, content_media/drafts, reel_posts, reel_metrics and
    effect_claims, kept as state."""

    def __init__(self, tmp_path) -> None:
        super().__init__()
        self.runs: list[dict] = []
        self.posts: list[dict] = []          # {media_id, platform, post_ref, at}
        self.metrics: list[dict] = []        # {media_id, views, read_at}
        self.claims: dict[str, str] = {}
        self.drafts: dict[int, dict] = {}    # media -> source/title/format_source
        self.files: dict[int, str] = {}
        self.tmp = tmp_path

    def media(self, mid: int, *, source="reel_format:3", title="Reel: twist",
              format_source="research") -> None:
        path = self.tmp / f"{mid}.mp4"
        path.write_bytes(b"MP4-" + str(mid).encode())
        self.files[mid] = str(path)
        self.drafts[mid] = {"shorts_source": source, "shorts_title": title,
                            "shorts_format_source": format_source}

    async def fetch(self, sql, *args):
        flat = " ".join(sql.split())
        self.sql_log.append((flat, args))
        if "FROM actions_log WHERE event IN" in flat:
            own = [{"id": 10_000 + r.id, "at": r.at, "event": r.event,
                    "subject": r.subject, "payload": r.payload}
                   for r in self.audit_rows
                   if r.event in ("execute.start", "execute.finish")]
            return list(self.runs) + own
        if "platform = 'youtube' AND media_id = ANY" in flat:
            return [{"media_id": p["media_id"]} for p in self.posts
                    if p["platform"] == "youtube" and p["media_id"] in args[0]]
        if "AS shorts_media" in flat:
            return [{"shorts_media": m["media_id"], "views": m["views"],
                     "read_at": m["read_at"]} for m in self.metrics
                    if m["media_id"] in args[0]]
        if "FROM reel_posts WHERE media_id = ANY" in flat:
            return [{"media_id": p["media_id"], "platform": p["platform"]}
                    for p in self.posts if p["media_id"] in args[0]]
        if "FROM effect_claims WHERE action = $1 AND status = 'sending'" in flat:
            return [{"key": k} for k, v in self.claims.items() if v == "sending"]
        if "FROM effect_claims WHERE action = $1 AND key = ANY" in flat:
            return [{"key": k, "status": v} for k, v in self.claims.items()
                    if k in args[1]]
        return self._canned(sql)

    async def fetchrow(self, sql, *args):
        flat = " ".join(sql.split())
        self.sql_log.append((flat, args))
        if "INSERT INTO effect_claims" in flat:
            if args[1] in self.claims:
                return None
            self.claims[args[1]] = "sending"
            return {"key": args[1]}
        if "SELECT status FROM effect_claims" in flat:
            status = self.claims.get(args[1])
            return {"status": status} if status else None
        if "AS shorts_source" in flat:
            return self.drafts.get(int(args[0]))
        if "FROM content_media WHERE id" in flat:
            mid = int(args[0])
            return ({"id": mid, "status": "done", "path": self.files[mid],
                     "media_token": "t", "media_token_expires_at": NOW}
                    if mid in self.files else None)
        if "platform = 'youtube' AND at >= $1" in flat:
            return {"n": sum(1 for p in self.posts if p["platform"] == "youtube"
                             and p["at"] >= args[0])}
        if "WHERE platform = 'youtube' ORDER BY at DESC" in flat:
            tubes = sorted((p for p in self.posts if p["platform"] == "youtube"),
                           key=lambda p: p["at"])
            return tubes[-1] if tubes else None
        return self._canned(sql)

    async def execute(self, sql, *args):
        flat = " ".join(sql.split())
        if "UPDATE effect_claims SET status = 'sent'" in flat:
            self.claims[args[1]] = "sent"
        elif "DELETE FROM effect_claims" in flat:
            if self.claims.get(args[1]) == "sending":
                del self.claims[args[1]]
        elif "INSERT INTO reel_posts" in flat and "'youtube'" in flat:
            self.posts.append({"media_id": int(args[0]), "platform": "youtube",
                               "post_ref": str(args[1]), "at": datetime.now(UTC)})
        return await super().execute(sql, *args)


@pytest.fixture
def world(monkeypatch, tmp_path):
    fake = World(tmp_path)
    previous = state.set_backend(fake)
    seconds: dict[str, float] = {}

    async def probe(path):
        return {"width": 1080, "height": 1920, "audio": True,
                "seconds": seconds.get(path, 32.0)}

    async def volume(path):
        return -18.0

    async def not_football(media_id):
        return False

    monkeypatch.setattr(ffmpeg, "probe", probe)
    monkeypatch.setattr(ffmpeg, "mean_volume", volume)
    monkeypatch.setattr(football, "is_football_media", not_football)
    monkeypatch.setattr(executor, "_recent", type(executor._recent)(
        executor._recent.default_factory))
    fake.seconds = seconds
    try:
        yield fake
    finally:
        state.set_backend(previous)


@pytest.fixture
def hands(monkeypatch):
    monkeypatch.setenv("ATLAS_WATCH_ONLY", "false")
    for spec in content.MODULE.actions():
        monkeypatch.setitem(REGISTRY.actions, spec.name, spec)
        monkeypatch.setitem(REGISTRY.decisions, spec.name, tier_of(spec))


def _yt(world, mid, at=None):
    world.posts.append({"media_id": mid, "platform": "youtube",
                        "post_ref": f"yt-{mid}", "at": at or datetime.now(UTC)})


# ------------------------------------------------------------ the pick

async def test_the_best_read_reel_wins_and_one_on_youtube_is_skipped(world):
    for mid in (1, 2, 3):
        world.media(mid)
    world.runs = (run_rows(1, at=NOW - timedelta(days=2), rid=1)
                  + run_rows(2, at=NOW - timedelta(days=1), rid=3)
                  + run_rows(3, at=NOW - timedelta(hours=5), rid=5))
    world.metrics = [{"media_id": 1, "views": 5000.0, "read_at": NOW},
                     {"media_id": 2, "views": 9000.0, "read_at": NOW}]
    _yt(world, 2)
    short, note, skipped = await shorts.pick()
    assert short.media_id == 1 and "views read" in note and skipped == []
    assert short.title == "He saved the wrong seat."
    assert short.tags == ["aistory", "storytime"]


async def test_unread_falls_back_to_the_newest_finished_reel(world):
    for mid in (1, 2):
        world.media(mid)
    world.runs = (run_rows(1, at=NOW - timedelta(days=2), rid=1)
                  + run_rows(2, at=NOW - timedelta(hours=2), rid=3))
    short, note, _ = await shorts.pick()
    assert short.media_id == 2 and "newest" in note


async def test_a_reel_over_60_s_is_skipped_never_cut(world):
    for mid in (1, 2):
        world.media(mid)
    world.seconds[world.files[2]] = 61.5
    world.runs = (run_rows(1, at=NOW - timedelta(days=2), rid=1)
                  + run_rows(2, at=NOW - timedelta(hours=2), rid=3))
    short, _note, skipped = await shorts.pick()
    assert short.media_id == 1
    assert skipped == ["media 2: the render is 61.5 s, over 60 s"]


async def test_other_lanes_and_bad_renders_are_skipped(world, monkeypatch):
    world.media(1, source="calendar:business-20260928-d01", title="B")
    world.media(2)
    world.media(3)
    world.runs = (run_rows(1, at=NOW - timedelta(hours=1), rid=1)
                  + run_rows(2, at=NOW - timedelta(hours=2), rid=3)
                  + run_rows(3, at=NOW - timedelta(hours=3), rid=5))

    async def probe(path):
        size = (1920, 1080) if path == world.files[2] else (1080, 1920)
        return {"width": size[0], "height": size[1], "audio": True, "seconds": 30.0}

    monkeypatch.setattr(ffmpeg, "probe", probe)
    short, _note, skipped = await shorts.pick()
    assert short.media_id == 3
    assert "not an AI-stories-lane reel" in skipped[0]
    assert "1920x1080, not 1080x1920" in skipped[1]


async def test_football_and_course_are_lane_reels(world):
    world.media(1, format_source="football", title="Football reel: Salah")
    world.media(2, format_source="course", title="Course reel: lesson 1")
    world.runs = (run_rows(1, at=NOW - timedelta(hours=1), rid=1)
                  + run_rows(2, at=NOW - timedelta(hours=2), rid=3))
    short, _n, _s = await shorts.pick()
    assert short.media_id == 1 and short.lane == "football"
    assert (await shorts.gate(2, CAPTION))[0].lane == "course"


async def test_a_caption_the_guards_refuse_is_skipped(world):
    world.media(1)
    world.runs = run_rows(1, at=NOW - timedelta(hours=1),
                          caption=f"Nike made him run.\n\n{AI_LABEL}")
    short, _n, skipped = await shorts.pick()
    assert short is None and "media 1:" in skipped[0]


async def test_series_part_n_waits_for_part_n_minus_1_on_youtube(world, monkeypatch):
    world.media(7, source="series:4:1", title="The Night Bus — Part 1")
    world.media(8, source="series:4:2", title="The Night Bus — Part 2")
    world.seconds[world.files[7]] = 75.0          # part 1 cannot go up
    world.runs = (run_rows(7, at=NOW - timedelta(days=2), rid=1)
                  + run_rows(8, at=NOW - timedelta(hours=2), rid=3))
    world.metrics = [{"media_id": 8, "views": 90000.0, "read_at": NOW}]

    async def before(media_id):
        return [7] if media_id == 8 else []

    monkeypatch.setattr(legsync, "predecessors", before)
    short, _n, skipped = await shorts.pick()
    assert short is None
    assert "series order" in skipped[0] and "media 7" in skipped[0]
    _yt(world, 7)
    assert (await shorts.gate(8, CAPTION))[0] is not None


# ------------------------------------------------------------ never a clip

async def test_a_clip_outputs_id_with_no_content_media_row_is_refused(world,
                                                                      monkeypatch):
    """A clip lives in clip_outputs, never content_media: an id with no
    content_media draft is refused even if a run names it."""
    seen = _fake(monkeypatch)
    world.runs = run_rows(41, at=NOW - timedelta(hours=1))
    result = await shorts.publish_short(41)
    assert not result.ok and "has no draft" in result.detail
    assert not any(m == "POST" for m, _p, _b in seen)
    assert all("clip_outputs" not in sql for sql, _ in world.sql_log)


async def test_a_file_under_the_clips_dir_is_refused(world, monkeypatch, tmp_path):
    clips = tmp_path / "clips"
    out = clips / "acme" / "out"
    out.mkdir(parents=True)
    monkeypatch.setenv("CLIPS_DATA_DIR", str(clips))
    world.media(1)
    world.files[1] = str(out / "c1.mp4")
    (out / "c1.mp4").write_bytes(b"clip")
    world.runs = run_rows(1, at=NOW - timedelta(hours=1))
    short, why = await shorts.gate(1, CAPTION)
    assert short is None and "clips lane's data dir" in why
    seen = _fake(monkeypatch)
    assert not (await shorts.publish_short(1)).ok and seen == []


async def test_a_symlink_into_the_clips_dir_is_resolved_and_refused(
        world, monkeypatch, tmp_path):
    clips = tmp_path / "clips" / "acme" / "out"
    clips.mkdir(parents=True)
    (clips / "c1.mp4").write_bytes(b"clip")
    monkeypatch.setenv("CLIPS_DATA_DIR", str(tmp_path / "clips"))
    world.media(1)
    link = tmp_path / "reels" / "1.mp4"
    link.parent.mkdir()
    link.symlink_to(clips / "c1.mp4")
    world.files[1] = str(link)
    assert shorts.clips_file(str(link))
    short, why = await shorts.gate(1, CAPTION)
    assert short is None and "clips lane's data dir" in why


def test_a_reel_outside_the_clips_dir_is_not_a_clip(monkeypatch, tmp_path):
    monkeypatch.setenv("CLIPS_DATA_DIR", str(tmp_path / "clips"))
    (tmp_path / "clipsX").mkdir()
    assert not shorts.clips_file(str(tmp_path / "clipsX" / "r.mp4"))
    assert not shorts.clips_file(str(tmp_path / "media" / "r.mp4"))
    assert shorts.clips_file("data/app/clips/acme/out/c.mp4")


def test_the_clips_youtube_env_is_never_read(monkeypatch):
    monkeypatch.setenv("POSTIZ_YOUTUBE_CLIPS_INTEGRATION", YT_ID)
    monkeypatch.delenv("POSTIZ_YOUTUBE_INTEGRATION")
    assert postiz.youtube_gap() is not None
    with pytest.raises(postiz.PostizError, match="never guessed"):
        postiz.pick_youtube(INTEGRATIONS)


# ------------------------------------------------------------ the action

def test_publish_short_is_tier_one_with_a_dry_run_a_rollback_and_media_id_only():
    spec = {a.name: a for a in content.MODULE.actions()}[shorts.ACTION]
    assert spec.tier is Tier.AUTO and int(tier_of(spec).tier) == 1
    assert spec.dry_run is shorts.publish_short_dry
    assert spec.rollback is shorts.unpublish_short
    assert set(spec.params) == {"media_id"}          # never a title or caption


async def test_the_step_posts_the_pick_through_the_executor(world, hands,
                                                            monkeypatch):
    seen = _fake(monkeypatch)
    world.media(1)
    world.runs = run_rows(1, at=NOW - timedelta(hours=3))
    lines = await day.shorts_lines(datetime(2026, 9, 28, 15, tzinfo=UTC))  # Monday
    assert len(lines) == 1 and lines[0].startswith("Shorts: posted"), lines
    assert "yt-post-1" in lines[0]
    assert [p for p in world.posts if p["platform"] == "youtube"] == [
        {"media_id": 1, "platform": "youtube", "post_ref": "yt-post-1",
         "at": world.posts[-1]["at"]}]
    assert world.claims == {"1:youtube": "sent"}
    body = _body(seen)["posts"][0]
    assert body["settings"]["title"] == "He saved the wrong seat."
    assert body["value"][0]["content"] == shorts.description_for(CAPTION)
    starts = [json.loads(r.payload) for r in world.audit_rows
              if r.event == "execute.start"]
    assert [s["subject"] for s in starts] == [shorts.ACTION]
    assert starts[0]["data"]["params"] == {"media_id": 1}
    finish = next(json.loads(r.payload) for r in world.audit_rows
                  if r.event == "execute.finish")
    assert finish["data"]["ok"]


async def test_a_second_run_posts_nothing(world, monkeypatch):
    _fake(monkeypatch)
    world.media(1)
    world.runs = run_rows(1, at=NOW - timedelta(hours=3))
    assert (await shorts.publish_short(1)).ok
    again = await shorts.publish_short(1)
    assert not again.ok and "already on YouTube" in again.detail
    world.posts.clear()                           # even with the row gone
    again = await shorts.publish_short(1)
    assert not again.ok and "already sent" in again.detail


async def test_the_weeks_cap_holds_inside_the_action(world, monkeypatch):
    seen = _fake(monkeypatch)
    monkeypatch.setenv("SHORTS_PER_WEEK", "1")
    world.media(1)
    world.runs = run_rows(1, at=NOW - timedelta(hours=3))
    _yt(world, 99)
    result = await shorts.publish_short(1)
    assert not result.ok and "this week's 1 Short(s) are done" in result.detail
    assert seen == []


async def test_a_reel_that_did_not_post_this_week_is_refused(world, monkeypatch):
    seen = _fake(monkeypatch)
    world.media(1)
    result = await shorts.publish_short(1)
    assert not result.ok and "did not post as an AI reel" in result.detail
    assert seen == []


async def test_an_uncertain_post_keeps_its_claim_a_clean_refusal_releases_it(
        world, monkeypatch):
    world.media(1)
    world.runs = run_rows(1, at=NOW - timedelta(hours=3))
    _fake(monkeypatch, post_status=502)
    result = await shorts.publish_short(1)
    assert not result.ok and "may have posted" in result.detail
    assert world.claims == {"1:youtube": "sending"}
    world.claims.clear()
    _fake(monkeypatch, post_status=400)
    result = await shorts.publish_short(1)
    assert not result.ok and world.claims == {}


async def test_a_post_in_doubt_never_holds_the_lane(world, monkeypatch):
    """A 502 on the post leaves its claim (it may have landed): the next
    pick moves on to the next reel, and the check names it for a person."""
    for mid in (1, 2):
        world.media(mid)
    world.runs = (run_rows(1, at=NOW - timedelta(hours=1), rid=1)
                  + run_rows(2, at=NOW - timedelta(hours=2), rid=3))
    _fake(monkeypatch, post_status=502)
    assert not (await shorts.publish_short(1)).ok
    assert world.claims == {"1:youtube": "sending"}
    short, _note, _skipped = await shorts.pick()
    assert short.media_id == 2
    _fake(monkeypatch)
    obs = await shorts.check_shorts_lane()
    assert obs.health is Health.DEGRADED
    assert "never confirmed for media 1" in obs.detail


async def test_a_football_reel_passes_the_football_caption_guard(world, monkeypatch):
    async def football_(media_id):
        return True

    monkeypatch.setattr(football, "is_football_media", football_)
    world.media(1, format_source="football", title="Football reel: Salah")
    caption = (f"Mohamed Salah: 32 league goals in 2017-18, a Premier League "
               f"record for a 38-game season. #football #soccer\n\n{AI_LABEL}")
    short, why = await shorts.gate(1, caption)
    assert why is None and short.lane == "football"
    assert short.title.startswith("Mohamed Salah: 32 league goals")


async def test_the_dry_run_reads_only(world, monkeypatch):
    seen = _fake(monkeypatch)
    world.media(1)
    world.runs = run_rows(1, at=NOW - timedelta(hours=3))
    result = await shorts.publish_short_dry(1)
    assert result.ok and "He saved the wrong seat." in result.detail
    assert all(m == "GET" for m, _p, _b in seen)
    assert world.claims == {} and not any(p["platform"] == "youtube"
                                          for p in world.posts)


async def test_rollback_deletes_a_queued_post_else_a_manual_note(monkeypatch):
    seen = _fake(monkeypatch, state_="QUEUE")
    result = await shorts.unpublish_short(json.dumps({"youtube": "yt-post-1"}))
    assert result.ok and ("DELETE", "/posts/yt-post-1") in [(m, p) for m, p, _ in seen]
    seen = _fake(monkeypatch, state_="PUBLISHED")
    result = await shorts.unpublish_short(json.dumps({"youtube": "yt-post-1"}))
    assert not result.ok and "MANUAL" in result.detail
    assert "YouTube Studio" in result.detail and "Tier 3" in result.detail
    assert ("DELETE", "/posts/yt-post-1") not in [(m, p) for m, p, _ in seen]
    assert not (await shorts.unpublish_short("nope")).ok


# ------------------------------------------------------------ the step

async def test_the_step_is_off_unset_or_not_due_without_reading_anything(
        monkeypatch, memory_backend):
    monkeypatch.setenv("SHORTS_PER_WEEK", "0")
    assert await day.shorts_lines() == ["Shorts: off (SHORTS_PER_WEEK=0)"]
    monkeypatch.delenv("SHORTS_PER_WEEK")
    monkeypatch.delenv("POSTIZ_YOUTUBE_INTEGRATION")
    lines = await day.shorts_lines()
    assert lines[0].startswith("Shorts: not configured")
    assert memory_backend.sql_log == []
    monkeypatch.setenv("POSTIZ_YOUTUBE_INTEGRATION", YT_ID)
    memory_backend.responses["platform = 'youtube' AND at >= $1"] = {"n": 1}
    tuesday = datetime(2026, 9, 29, 15, tzinfo=UTC)
    lines = await day.shorts_lines(tuesday)
    assert lines[0].startswith("Shorts: none due today (1 of 1 this week")


# ------------------------------------------------------------ leg sync

async def test_leg_sync_never_treats_youtube_as_a_leg(world):
    world.media(1)
    world.runs = (run_rows(1, at=NOW - timedelta(hours=3))
                  + run_rows(1, at=NOW - timedelta(hours=1), rid=9,
                             subject=shorts.ACTION))
    world.posts = [{"media_id": 1, "platform": p, "post_ref": p, "at": NOW}
                   for p in ("instagram", "tiktok")]
    assert await legsync.gaps() == []
    assert "youtube" not in legsync.LEGS
    refused = await legsync.post_missing_leg_dry(1, "youtube")
    assert not refused.ok and "no such leg 'youtube'" in refused.detail


# ------------------------------------------------------------ the check

async def test_the_check_is_unknown_when_unset(monkeypatch):
    monkeypatch.delenv("POSTIZ_YOUTUBE_INTEGRATION")
    obs = await shorts.check_shorts_lane()
    assert obs.health is Health.UNKNOWN
    assert "POSTIZ_YOUTUBE_INTEGRATION" in obs.detail


async def test_the_check_is_unknown_when_postiz_is_unreachable(monkeypatch):
    _fake(monkeypatch, unreachable=True)
    obs = await shorts.check_shorts_lane()
    assert obs.health is Health.UNKNOWN and "unreachable" in obs.detail


async def test_the_check_is_unknown_when_the_channel_is_gone(monkeypatch):
    _fake(monkeypatch, integrations=INTEGRATIONS[:2])
    obs = await shorts.check_shorts_lane()
    assert obs.health is Health.UNKNOWN and "no enabled YouTube" in obs.detail


async def test_the_check_says_the_last_short_and_the_next_due(world, monkeypatch):
    _fake(monkeypatch)
    _yt(world, 5, at=datetime(2026, 9, 28, 14, 0, tzinfo=UTC))
    obs = await shorts.check_shorts_lane()
    assert obs.health in (Health.OK, Health.DEGRADED)
    assert "last Short Mon 28 Sep 14:00 UTC (Postiz post yt-5)" in obs.detail
    assert "next due" in obs.detail and "read " in obs.detail
    assert "AtlasClips" in obs.detail


async def test_the_check_is_unknown_when_reel_posts_is_unreadable(
        monkeypatch, memory_backend):
    _fake(monkeypatch)
    obs = await shorts.check_shorts_lane()          # no canned answer: raises
    assert obs.health is Health.UNKNOWN and "unreadable" in obs.detail


def test_the_check_is_registered():
    names = {c.name for c in content.MODULE.checks()}
    assert "shorts_lane" in names


# ------------------------------------------------------------ other readers

async def test_youtube_rows_are_not_read_as_reel_metrics(memory_backend):
    from atlas.modules.content import metrics
    memory_backend.responses["FROM reel_posts"] = []
    await metrics.check_reel_metrics()
    sql = next(s for s, _ in memory_backend.sql_log if "FROM reel_posts" in s)
    assert "platform IN ('instagram', 'tiktok')" in sql


async def test_the_render_gate_is_the_autopublish_one_plus_the_shorts_limit(world):
    world.media(1)
    path = world.files[1]
    assert await crosspost.render_refusal(path) is None
    world.seconds[path] = 90.0
    assert await crosspost.render_refusal(path) is None       # reels: no cap
    assert "over 60 s" in await crosspost.render_refusal(path, shorts.MAX_SECONDS)
    world.seconds[path] = 3.0
    assert "under 5 s" in await crosspost.render_refusal(path, shorts.MAX_SECONDS)
