"""A clip Postiz posted without its URL gets it, from Postiz, when Postiz has it.

What these pin down (`atlas/modules/clips/links.py`):
  * Postiz is read through `GET /posts?startDate&endDate` only -- the
    shape seen live on 2026-09-30 -- and nothing else is ever sent to it;
  * a TikTok row is filled in only with `tiktok.com/@example_handle/video/<n>`
    and source `tiktok`; the PROFILE URL Postiz actually lists for TikTok
    today, another account's video, a short link or another host are left
    alone;
  * a YouTube row gets the `shorts/` link of the id Postiz names, source
    `youtube`;
  * both sources are ones whop.py offers to the Mac agent;
  * Postiz unreachable (or unset) leaves every row as it was; a row whose
    link is already set is never overwritten; a post not PUBLISHED is not
    taken;
  * the scheduler runs the pass and declares its missed-run policy.
"""
from __future__ import annotations

import copy
import json
from datetime import UTC, datetime, timedelta

import httpx
import pytest

from atlas.kernel import scheduler, state
from atlas.modules.clips import links, whop
from atlas.modules.content import postiz
from tests.acceptance_support import DSN, needs_pg

NOW = datetime(2026, 9, 30, 18, 0, tzinfo=UTC)
TT_REF, TT_REF2, YT_REF = ("postiz_example_id_01", "postiz_example_id_02",
                           "postiz_example_id_03")
VIDEO = "https://www.tiktok.com/@example_handle/video/7691328586581755917"
PROFILE = "https://www.tiktok.com/@example_handle"


def _item(post_id: str, release_url: str, *, provider: str = "tiktok-business",
          name: str = "atlasclips", state_: str = "PUBLISHED",
          release_id: str = "v_pub_url~v2.7691328586581755917",
          publish: str = "2026-09-30T14:02:00.000Z") -> dict:
    """One item of Postiz's `GET /posts` listing, as seen live 2026-09-30."""
    return {
        "id": post_id, "content": "clip caption #Ad", "creationMethod": "API",
        "group": "40a6d2cb-2513-4863-894c-e483ee64825b", "intervalInDays": None,
        "publishDate": publish, "releaseId": release_id, "releaseURL": release_url,
        "settings": "{}", "state": state_, "tags": [],
        "integration": {"id": "postiz_example_id_04", "name": name,
                        "picture": "https://example.invalid/p.png",
                        "providerIdentifier": provider},
    }


def _row(cid: int, platform: str, ref: str, *, post_url: str | None = None,
         source: str | None = None, status: str = "posted",
         posted_at: datetime | None = None) -> dict:
    return {"id": cid, "platform": platform, "post_ref": ref, "status": status,
            "post_url": post_url, "post_url_source": source,
            "posted_at": posted_at or NOW - timedelta(hours=4)}


class Rows:
    """clip_outputs, in memory, answering the pass's two statements the way
    Postgres would (the SELECT's filter and the guarded UPDATE)."""

    def __init__(self, *rows: dict) -> None:
        self.rows = {r["id"]: dict(r) for r in rows}
        self.selects = 0

    async def fetch_all(self, sql: str, *args):
        assert sql.lstrip().startswith("SELECT")
        self.selects += 1
        platforms, since = args
        return [dict(r) for r in sorted(self.rows.values(),
                                        key=lambda r: (r["posted_at"], r["id"]))
                if r["status"] == "posted" and r["post_url"] is None
                and r["post_ref"] and r["platform"] in platforms
                and r["posted_at"] >= since]

    async def fetch_one(self, sql: str, *args):
        assert sql.lstrip().startswith("UPDATE clip_outputs")
        cid, ref, url, source = args
        row = self.rows.get(cid)
        if (row is None or row["post_ref"] != ref or row["status"] != "posted"
                or row["post_url"] is not None):
            return None
        row["post_url"], row["post_url_source"] = url, source
        return {"id": cid}


@pytest.fixture
def db(monkeypatch, memory_backend):
    def install(*rows: dict) -> Rows:
        fake = Rows(*rows)
        monkeypatch.setattr(links, "fetch_all", fake.fetch_all)
        monkeypatch.setattr(links, "fetch_one", fake.fetch_one)
        return fake
    return install


def _postiz(monkeypatch, items: list[dict] | None = None, *, down: bool = False
            ) -> list[httpx.Request]:
    """Postiz answering `GET /posts` with `items`; every request recorded."""
    monkeypatch.setenv("POSTIZ_API_KEY", "k" * 24)
    monkeypatch.delenv("POSTIZ_API_URL", raising=False)
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if down:
            raise httpx.ConnectError("connection refused", request=request)
        if request.method == "GET" and request.url.path == "/public/v1/posts":
            return httpx.Response(200, json={"posts": items or []})
        return httpx.Response(404, json={"message": "Not Found"})

    real = httpx.AsyncClient
    monkeypatch.setattr(postiz.httpx, "AsyncClient", lambda *a, **k: real(
        *a, transport=httpx.MockTransport(handler), **k))
    return seen


def _only_reads(seen: list[httpx.Request]) -> None:
    assert seen, "Postiz was never read"
    for request in seen:
        assert request.method == "GET" and request.url.path == "/public/v1/posts"
        assert request.url.params["startDate"] and request.url.params["endDate"]


# ------------------------------------------------------------------ TikTok

async def test_a_tiktok_video_url_is_written_with_the_platform_source(monkeypatch, db):
    rows = db(_row(1, "tiktok", TT_REF))
    seen = _postiz(monkeypatch, [_item(TT_REF, VIDEO)])
    said = await links.reconcile(NOW)
    assert said == [f"clip 1: {VIDEO}"]
    assert rows.rows[1]["post_url"] == VIDEO
    assert rows.rows[1]["post_url_source"] == "tiktok"
    assert "tiktok" in whop.TRUSTED_SOURCES
    _only_reads(seen)
    # The window covers the row's posting time, with a margin.
    params = seen[0].url.params
    assert datetime.fromisoformat(params["startDate"]) <= NOW - timedelta(hours=4)
    assert datetime.fromisoformat(params["endDate"]) >= NOW


async def test_the_profile_url_postiz_lists_today_is_not_a_post_url(monkeypatch, db):
    rows = db(_row(1, "tiktok", TT_REF), _row(3, "tiktok", TT_REF2))
    before = copy.deepcopy(rows.rows)
    _postiz(monkeypatch, [_item(TT_REF, PROFILE),
                          _item(TT_REF2, PROFILE,
                                release_id="v_pub_url~v2.7691028377495193613")])
    assert await links.reconcile(NOW) == []
    assert rows.rows == before


@pytest.mark.parametrize("url", [
    "https://www.tiktok.com/@atlasstories7/video/7691328586581755917",  # other account
    "https://vm.tiktok.com/ZMabcdef/",                                  # short link
    "http://www.tiktok.com/@example_handle/video/7691328586581755917",   # not https
    "https://tiktok.com.evil.example/@example_handle/video/1",            # other host
    "https://www.instagram.com/reel/Cxyz/",                             # other platform
    "https://www.youtube.com/shorts/Z0mGgLsD02w",
    "",
])
async def test_anything_but_the_clips_accounts_video_is_ignored(monkeypatch, db, url):
    rows = db(_row(1, "tiktok", TT_REF))
    _postiz(monkeypatch, [_item(TT_REF, url)])
    assert await links.reconcile(NOW) == []
    assert rows.rows[1]["post_url"] is None
    assert rows.rows[1]["post_url_source"] is None


async def test_a_post_not_published_or_on_another_provider_is_not_taken(
        monkeypatch, db):
    rows = db(_row(1, "tiktok", TT_REF), _row(3, "tiktok", TT_REF2))
    _postiz(monkeypatch, [_item(TT_REF, VIDEO, state_="QUEUE"),
                          _item(TT_REF2, VIDEO, provider="youtube")])
    assert await links.reconcile(NOW) == []
    assert rows.rows[1]["post_url"] is None and rows.rows[3]["post_url"] is None


async def test_a_deleted_post_is_not_taken(monkeypatch, db):
    rows = db(_row(1, "tiktok", TT_REF))
    item = _item(TT_REF, VIDEO)
    item["deletedAt"] = "2026-09-30T15:00:00.000Z"
    _postiz(monkeypatch, [item])
    assert await links.reconcile(NOW) == []
    assert rows.rows[1]["post_url"] is None


async def test_only_the_rows_own_postiz_post_is_used(monkeypatch, db):
    rows = db(_row(1, "tiktok", TT_REF))
    _postiz(monkeypatch, [_item("cmsomeotherpost", VIDEO)])
    assert await links.reconcile(NOW) == []
    assert rows.rows[1]["post_url"] is None


# ------------------------------------------------------------------ YouTube

async def test_a_youtube_short_gets_its_shorts_link_with_the_youtube_source(
        monkeypatch, db):
    rows = db(_row(5, "youtube", YT_REF))
    _postiz(monkeypatch, [_item(YT_REF, "https://www.youtube.com/watch?v=Z0mGgLsD02w",
                                provider="youtube", name="AtlasClips",
                                release_id="Z0mGgLsD02w")])
    assert await links.reconcile(NOW) == [
        "clip 5: https://www.youtube.com/shorts/Z0mGgLsD02w"]
    assert rows.rows[5]["post_url"] == "https://www.youtube.com/shorts/Z0mGgLsD02w"
    assert rows.rows[5]["post_url_source"] == "youtube"
    assert "youtube" in whop.TRUSTED_SOURCES


@pytest.mark.parametrize("url,release", [
    ("https://www.youtube.com/watch?v=Z0mGgLsD02w", "SomeOtherId1"),  # id mismatch
    ("https://www.tiktok.com/@example_handle/video/1", "Z0mGgLsD02w"),
    ("https://www.youtube.com/@example_handle", "Z0mGgLsD02w"),              # channel page
    ("https://evil.example/watch?v=Z0mGgLsD02w", "Z0mGgLsD02w"),
])
async def test_a_youtube_row_ignores_anything_but_its_videos_url(monkeypatch, db, url,
                                                                 release):
    rows = db(_row(5, "youtube", YT_REF))
    _postiz(monkeypatch, [_item(YT_REF, url, provider="youtube", release_id=release)])
    assert await links.reconcile(NOW) == []
    assert rows.rows[5]["post_url"] is None


# ------------------------------------------------------ nothing is guessed

async def test_postiz_unreachable_leaves_every_row_untouched(monkeypatch, db):
    rows = db(_row(1, "tiktok", TT_REF), _row(5, "youtube", YT_REF))
    before = copy.deepcopy(rows.rows)
    seen = _postiz(monkeypatch, down=True)
    said = await links.reconcile(NOW)
    assert len(said) == 1 and said[0].startswith("Postiz not read")
    assert "k" * 24 not in said[0]
    assert rows.rows == before
    _only_reads(seen)


async def test_postiz_refusing_leaves_every_row_untouched(monkeypatch, db):
    rows = db(_row(1, "tiktok", TT_REF))
    monkeypatch.setenv("POSTIZ_API_KEY", "k" * 24)
    real = httpx.AsyncClient
    monkeypatch.setattr(postiz.httpx, "AsyncClient", lambda *a, **k: real(
        *a, transport=httpx.MockTransport(
            lambda r: httpx.Response(401, json={"message": "Invalid API key"})), **k))
    said = await links.reconcile(NOW)
    assert said and "HTTP 401" in said[0]
    assert rows.rows[1]["post_url"] is None


async def test_postiz_unset_reads_nothing_and_writes_nothing(monkeypatch, db):
    rows = db(_row(1, "tiktok", TT_REF))
    monkeypatch.delenv("POSTIZ_API_KEY", raising=False)
    said = await links.reconcile(NOW)
    assert said == ["POSTIZ_API_KEY unset; Postiz not read"]
    assert rows.selects == 0 and rows.rows[1]["post_url"] is None


async def test_a_link_already_set_is_never_overwritten(monkeypatch, db):
    mine = "https://www.tiktok.com/@example_handle/video/1111111111111111111"
    rows = db(_row(1, "tiktok", TT_REF, post_url=mine, source="record_link"))
    seen = _postiz(monkeypatch, [_item(TT_REF, VIDEO)])
    assert await links.reconcile(NOW) == []
    assert rows.rows[1]["post_url"] == mine
    assert rows.rows[1]["post_url_source"] == "record_link"
    assert seen == []           # nothing waits, so Postiz is not even read


async def test_a_link_set_meanwhile_is_not_overwritten(monkeypatch, db):
    """Recorded by the owner between the read and the write: the guarded UPDATE
    leaves his."""
    rows = db(_row(1, "tiktok", TT_REF))
    mine = "https://www.tiktok.com/@example_handle/video/1111111111111111111"
    real_posts = postiz.posts_between

    async def posts_between(start, end):
        found = await real_posts(start, end)
        rows.rows[1].update(post_url=mine, post_url_source="record_link")
        return found

    _postiz(monkeypatch, [_item(TT_REF, VIDEO)])
    monkeypatch.setattr(postiz, "posts_between", posts_between)
    assert await links.reconcile(NOW) == []
    assert rows.rows[1]["post_url"] == mine
    assert rows.rows[1]["post_url_source"] == "record_link"


async def test_old_or_unposted_rows_are_not_looked_up(monkeypatch, db):
    rows = db(_row(1, "tiktok", TT_REF, posted_at=NOW - timedelta(days=15)),
              _row(3, "tiktok", TT_REF2, status="submitted"),
              _row(4, "instagram", "1789", ))
    seen = _postiz(monkeypatch, [_item(TT_REF, VIDEO), _item(TT_REF2, VIDEO)])
    assert await links.reconcile(NOW) == []
    assert seen == []
    assert all(r["post_url"] is None for r in rows.rows.values())


async def test_each_link_written_is_audited(monkeypatch, db, memory_backend):
    db(_row(1, "tiktok", TT_REF))
    _postiz(monkeypatch, [_item(TT_REF, VIDEO)])
    await links.reconcile(NOW)
    events = [json.loads(r.payload) for r in memory_backend.audit_rows]
    assert {"event": "clips.link_from_postiz", "subject": "clip_outputs:1",
            "data": {"platform": "tiktok", "postiz_post": TT_REF,
                     "post_url": VIDEO, "post_url_source": "tiktok"}} in events


# ------------------------------------------------------------------ scheduler

def test_the_scheduler_runs_the_pass_and_declares_its_policy():
    import inspect
    assert '("clip_links", _maybe_clip_links)' in inspect.getsource(scheduler.loop)
    assert "clip_links" in scheduler.POLICIES


def test_the_pass_can_be_turned_off_and_is_never_too_frequent(monkeypatch):
    monkeypatch.setenv("CLIPS_LINKS", "off")
    assert not links.enabled()
    monkeypatch.setenv("CLIPS_LINKS", "on")
    assert links.enabled()
    monkeypatch.setenv("CLIPS_LINKS_MIN", "1")
    assert links.interval() == timedelta(minutes=10)
    monkeypatch.setenv("CLIPS_LINKS_MIN", "nonsense")
    assert links.interval() == timedelta(minutes=60)


# ------------------------------------------------------- the real SQL, on pg

@pytest.fixture
async def pg(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", DSN)
    pool, state._pool = state._pool, None
    if pool is not None:
        await pool.close()
    previous = state.set_backend(state.PostgresBackend())
    from atlas import cli
    from atlas.modules.clips import campaigns
    await cli.init_db()
    await campaigns.ensure_seeds()
    tag = f"test-links-{datetime.now(UTC).timestamp()}"
    try:
        yield tag
    finally:
        await state.execute_sql("DELETE FROM clip_outputs WHERE run_token = $1", tag)
        await state.execute_sql("DELETE FROM clip_sources WHERE sha256 = $1", tag)
        state.set_backend(previous)
        pool, state._pool = state._pool, None
        if pool is not None:
            await pool.close()


async def _pg_row(tag: str, platform: str, ref: str, post_url: str | None,
                  source: str | None) -> int:
    slug = "cod-mw4-warzone-operator"
    src = await state.fetch_one(
        "SELECT id FROM clip_sources WHERE campaign = $1 AND sha256 = $2", slug, tag)
    if src is None:
        src = await state.fetch_one(
            "INSERT INTO clip_sources (campaign, filename, sha256, bytes) "
            "VALUES ($1, $2, $3, 1) RETURNING id", slug, f"{tag}.mp4", tag)
    row = await state.fetch_one(
        """INSERT INTO clip_outputs (campaign, source_id, run_token, start_s, end_s,
                  filename, platform, caption, status, post_ref, post_url,
                  post_url_source, posted_at)
           VALUES ($1, $2, $3, 0, 30, $4, $5, 'c #Ad', 'posted', $6, $7, $8,
                   now() - interval '2 hours')
           RETURNING id""",
        slug, int(src["id"]), tag, f"{tag}-{ref}.mp4", platform, ref, post_url,
        source)
    return int(row["id"])


@needs_pg
async def test_on_postgres_only_the_empty_link_is_filled(monkeypatch, pg):
    tag = pg
    mine = "https://www.tiktok.com/@example_handle/video/1111111111111111111"
    ref_a, ref_b, ref_c = f"{tag}-a", f"{tag}-b", f"{tag}-c"
    empty = await _pg_row(tag, "tiktok", ref_a, None, None)
    kept = await _pg_row(tag, "tiktok", ref_b, mine, "record_link")
    profile = await _pg_row(tag, "tiktok", ref_c, None, None)
    _postiz(monkeypatch, [_item(ref_a, VIDEO), _item(ref_b, VIDEO),
                          _item(ref_c, PROFILE)])
    said = await links.reconcile()
    assert f"clip {empty}: {VIDEO}" in said
    got = {int(r["id"]): (r["post_url"], r["post_url_source"])
           for r in await state.fetch_all(
               "SELECT id, post_url, post_url_source FROM clip_outputs "
               "WHERE run_token = $1", tag)}
    assert got[empty] == (VIDEO, "tiktok")
    assert got[kept] == (mine, "record_link")
    assert got[profile] == (None, None)
