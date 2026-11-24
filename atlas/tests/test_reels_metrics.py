"""Task 55 (fix round): the playbook learns from what posted.

* A posted reel is recorded per platform with its format.
* The daily read stores each post's numbers with the time they were read;
  a metric not given is NULL, never 0; Instagram insights are UNKNOWN until
  the token has instagram_manage_insights, and the check says so.
* TikTok is read through Postiz's post analytics; a missing TikTok id is
  reported unread, never bound by the check (a check only reads).
* A format whose posts beat 10k views or a high engagement rate is ranked
  higher, weighted by recency; an unread number neither helps nor hurts.
"""
from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import httpx
import pytest

from atlas.kernel.contracts import Health
from atlas.modules import instagram
from atlas.modules.content import crosspost, metrics, postiz
from atlas.modules.content import reels as playbook

NOW = datetime(2026, 9, 25, 12, 0, tzinfo=UTC)


# ------------------------------------------------------------- scoring

def test_views_and_engagement_each_earn_a_point_halved_by_age():
    rows = [
        {"format_id": 1, "views": 25_000.0, "engagement": 0.09, "posted_at": NOW},
        {"format_id": 2, "views": 9_000.0, "engagement": 0.07, "posted_at": NOW},
        {"format_id": 3, "views": 50_000.0, "engagement": None,
         "posted_at": NOW - timedelta(days=14)},
        {"format_id": 4, "views": None, "engagement": None, "posted_at": NOW},
        {"format_id": 5, "views": 500.0, "engagement": 0.01, "posted_at": NOW},
    ]
    got = metrics.bonus(rows, NOW)
    assert got == {1: 2.0, 2: 1.0, 3: 0.5}             # 4 and 5 earn nothing
    assert 4 not in got and 5 not in got               # and lose nothing


async def test_an_old_post_read_today_decays_by_when_it_was_posted(memory_backend):
    """Review I1: a 28-day-old reel, re-read this morning, counts a quarter
    of yesterday's -- the decay runs from the post, not from the read."""
    posted_28_days_ago = datetime.now(UTC) - timedelta(days=28)
    memory_backend.responses["DISTINCT ON (m.platform, m.post_ref)"] = [
        {"format_id": 1, "views": 20_000.0, "engagement": None,
         "posted_at": posted_28_days_ago}]
    got = await metrics.score_bonus()
    assert got[1] == pytest.approx(0.25, abs=0.01)
    sql = next(q for q, _ in memory_backend.sql_log if "DISTINCT ON" in q)
    assert "JOIN reel_posts p" in sql and "p.at AS posted_at" in sql


def test_engagement_needs_every_count():
    assert metrics.engagement({"views": 1000, "likes": 50, "comments": 5,
                               "shares": 5}) == 0.06
    assert metrics.engagement({"views": 1000, "likes": 50, "comments": None,
                               "shares": 5}) is None
    assert metrics.engagement({"views": 0, "likes": 0, "comments": 0,
                               "shares": 0}) is None


async def test_results_lift_a_format_in_the_ranking(memory_backend, monkeypatch):
    memory_backend.responses["GROUP BY lower(f.format)"] = [
        {"id": 1, "format": "love story", "weight": 2.0, "max_scenes": 4,
         "source": "owner"},
        {"id": 2, "format": "mystery hook", "weight": 2.0, "max_scenes": 3,
         "source": "owner"},
    ]
    memory_backend.responses["GROUP BY source"] = []

    async def earned():
        return {2: 1.5}

    monkeypatch.setattr(metrics, "score_bonus", earned)
    top = await playbook.top_formats(2)
    assert [f["id"] for f in top] == [2, 1]
    assert top[0]["weight"] == 3.5 and top[0]["results"] == 1.5


async def test_unreadable_results_rank_without_them(memory_backend):
    memory_backend.responses["GROUP BY lower(f.format)"] = [
        {"id": 1, "format": "love story", "weight": 2.0, "max_scenes": 4,
         "source": "owner"}]
    memory_backend.responses["GROUP BY source"] = []
    top = await playbook.top_formats(1)            # reel_metrics un-canned
    assert top[0]["weight"] == 2.0 and top[0]["results"] == 0.0


# ------------------------------------------------------------- recording

async def test_a_posted_reel_is_recorded_per_measured_platform(memory_backend):
    memory_backend.responses["JOIN content_drafts d"] = {"source": "reel_format:7"}
    await crosspost._record_posts(42, {"instagram": "stories:ig-9",
                                       "facebook": "fb-1", "tiktok": "pz-3"})
    rows = [a for sql, a in memory_backend.sql_log if "INSERT INTO reel_posts" in sql]
    assert rows == [(42, 7, "instagram", "ig-9"), (42, 7, "tiktok", "pz-3")]


async def test_recording_never_fails_the_post(memory_backend):
    await crosspost._record_posts(42, {"instagram": "stories:ig-9"})   # un-canned


# ------------------------------------------------------------- the daily read

@pytest.fixture
def posted(memory_backend, monkeypatch):
    memory_backend.responses["FROM reel_posts"] = [
        {"platform": "instagram", "post_ref": "ig-9", "format_id": 7},
        {"platform": "tiktok", "post_ref": "pz-3", "format_id": 7}]
    monkeypatch.setenv("INSTAGRAM_STORIES_ACCOUNT_ID", "sid")
    monkeypatch.setenv("INSTAGRAM_STORIES_TOKEN", "stok")
    monkeypatch.setenv("POSTIZ_API_KEY", "pz-key")
    return memory_backend


def _postiz(monkeypatch, answers: list, candidates=None):
    seen: list[tuple[str, str]] = []

    def handler(request):
        path = request.url.path.removeprefix("/public/v1")
        seen.append((request.method, path))
        if path == "/analytics/post/pz-3":
            return httpx.Response(200, json=answers.pop(0))
        if path == "/posts/pz-3/missing":
            return httpx.Response(200, json=candidates or [])
        if path == "/posts/pz-3/release-id":
            return httpx.Response(200, json={"ok": True})
        return httpx.Response(500)

    real = httpx.AsyncClient
    monkeypatch.setattr(postiz.httpx, "AsyncClient", lambda *a, **k: real(
        transport=httpx.MockTransport(handler)))
    return seen


TIKTOK = [{"label": "Views", "data": [{"total": "8000", "date": "d1"},
                                       {"total": "12500", "date": "d2"}]},
          {"label": "Likes", "data": [{"total": "900"}]},
          {"label": "Comments", "data": [{"total": "40"}]}]


async def test_instagram_is_unknown_without_the_permission_and_says_so(
        posted, monkeypatch):
    async def refused(path, account="main", **params):
        raise instagram.GraphError("Graph API HTTP 403 on /v21.0/ig-9/insights", 403)

    monkeypatch.setattr(instagram, "_get", refused)
    _postiz(monkeypatch, [TIKTOK])
    obs = await metrics.check_reel_metrics()
    assert obs.health == Health.UNKNOWN
    assert "read 1 of 2" in obs.detail and "UTC" in obs.detail
    # Two were posted; one was read. The value is posted, the read count is
    # evidence -- and the health, not the value, says it is not OK.
    assert obs.value == 2.0 and obs.evidence == {"read": 1, "unread": 1}
    assert "instagram_manage_insights" in obs.detail
    rows = [a for sql, a in posted.sql_log if "INSERT INTO reel_metrics" in sql]
    assert rows == [("tiktok", "pz-3", 7, 12500.0, 900.0, 40.0, None, None)]


async def test_a_readable_instagram_post_is_stored(posted, monkeypatch):
    async def ok(path, account="main", **params):
        assert path == "ig-9/insights" and account == "stories"
        assert params["metric"] == metrics.IG_METRICS
        assert params["metric"].startswith("views,")      # not the deprecated "plays"
        return {"data": [{"name": n, "values": [{"value": v}]} for n, v in
                         (("views", 20000), ("likes", 1000), ("comments", 100),
                          ("shares", 100), ("saved", 30))]}

    monkeypatch.setattr(instagram, "_get", ok)
    _postiz(monkeypatch, [TIKTOK])
    obs = await metrics.check_reel_metrics()
    assert obs.health == Health.OK and "read 2 of 2" in obs.detail
    # The number is the reel posts in the window, with its unit.
    assert obs.value == 2.0 and obs.unit == metrics.UNIT == "reel posts, 30 d"
    ig = next(a for sql, a in posted.sql_log
              if "INSERT INTO reel_metrics" in sql and a[0] == "instagram")
    assert ig[3:] == (20000.0, 1000.0, 100.0, 100.0, 0.06)


async def test_the_check_never_connects_a_missing_tiktok_id(posted, monkeypatch):
    """A check only reads (invariant 1): a missing TikTok id is reported,
    not bound -- even when Postiz offers a single candidate."""
    async def refused(path, account="main", **params):
        raise instagram.GraphError("Graph API HTTP 403", 403)

    monkeypatch.setattr(instagram, "_get", refused)
    seen = _postiz(monkeypatch, [{"missing": True}],
                   candidates=[{"id": "7400000000001", "url": "https://cover"}])
    obs = await metrics.check_reel_metrics()
    assert [m for m, _ in seen] == ["GET"]             # the analytics read, only
    assert "TikTok id pending — connect it by hand (Postiz)" in obs.detail
    assert obs.health == Health.UNKNOWN
    assert not any("INSERT INTO reel_metrics" in sql for sql, _ in posted.sql_log)


async def test_no_posts_is_a_measured_nothing(memory_backend):
    memory_backend.responses["FROM reel_posts"] = []
    obs = await metrics.check_reel_metrics()
    assert obs.health == Health.OK and obs.value == 0.0


def test_the_postiz_analytics_parser_never_turns_absent_into_zero():
    got = postiz._metrics(TIKTOK)
    assert got == {"views": 12500.0, "likes": 900.0, "comments": 40.0,
                   "shares": None}
    assert postiz._metrics(json.loads("[]")) == dict.fromkeys(
        ("views", "likes", "comments", "shares"))
