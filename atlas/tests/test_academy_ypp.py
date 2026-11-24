"""The Monday "Monetisation progress" section (academy/ypp.py), 2026-10-16:
where @atlasacademyphp stands against the YouTube Partner Program bar, from
fresh reads only. A figure not read is UNKNOWN with its reason, never 0;
every figure carries its read time; no pace is projected from an UNKNOWN."""
from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import pytest

from atlas.modules.academy import growth_log, ypp

NOW = datetime(2026, 10, 19, 13, 0, tzinfo=UTC)
READ = datetime(2026, 10, 19, 12, 58, tzinfo=UTC)
TODAY = NOW.date()
ALL = {"https://www.googleapis.com/auth/yt-analytics.readonly",
       "https://www.googleapis.com/auth/youtube.force-ssl"}


def _day(n: int) -> str:
    return (TODAY - timedelta(days=n)).isoformat()


def _wire(monkeypatch, *, stats=None, scopes=(ALL, None), content=None, subs=None,
          vids=None):
    """Mock every read ypp makes; each argument is the (value, reason) pair."""
    calls: list[str] = []

    async def channel_stats(ch):
        calls.append("stats")
        return stats or ({"subscribers": 312.0, "hidden_subscriber_count": False,
                          "read_at": READ}, None)

    async def token_scopes(ch):
        calls.append("scopes")
        return scopes

    async def channel_report(ch, *, start, end, metrics, dimensions):
        calls.append(dimensions)
        if "creatorContentType" in dimensions:
            assert metrics == "views,estimatedMinutesWatched"
            assert (end - start).days == 364
            return content or ({"rows": [], "read_at": READ}, None)
        assert metrics == "subscribersGained,subscribersLost"
        assert (end - start).days == 27
        return subs or ({"rows": [], "read_at": READ}, None)

    async def videos(ch, limit=25):
        calls.append("videos")
        return vids or ([], None)

    monkeypatch.setattr(ypp.api, "channel_stats", channel_stats)
    monkeypatch.setattr(ypp.api, "token_scopes", token_scopes)
    monkeypatch.setattr(ypp.api, "channel_report", channel_report)
    monkeypatch.setattr(ypp.api, "videos", videos)
    return calls


def test_the_bar_is_youtubes_published_one():
    assert (ypp.SUBSCRIBERS, ypp.WATCH_HOURS, ypp.SHORTS_VIEWS) == (
        1_000, 4_000, 10_000_000)


async def test_sums_split_long_form_from_shorts_and_by_window(monkeypatch):
    rows = [
        # long-form minutes: 300 days ago (in the 365) and 10 days ago (in the 28)
        {"day": _day(300), "creatorContentType": "VIDEO_ON_DEMAND", "views": 50,
         "estimatedMinutesWatched": 6000},
        {"day": _day(10), "creatorContentType": "VIDEO_ON_DEMAND", "views": 20,
         "estimatedMinutesWatched": 1680},
        # Shorts minutes never count as watch hours
        {"day": _day(10), "creatorContentType": "SHORTS", "views": 2800,
         "estimatedMinutesWatched": 9999},
        # Shorts views 120 days ago are outside the 90
        {"day": _day(120), "creatorContentType": "SHORTS", "views": 100000,
         "estimatedMinutesWatched": 1},
        {"day": _day(60), "creatorContentType": "SHORTS", "views": 5000,
         "estimatedMinutesWatched": 1},
    ]
    _wire(monkeypatch, content=({"rows": rows, "read_at": READ}, None),
          subs=({"rows": [{"day": _day(3), "subscribersGained": 40,
                           "subscribersLost": 12}], "read_at": READ}, None))
    got = await ypp.ypp_progress(NOW)
    hours, shorts, subs = got["watch_hours"], got["shorts_views"], got["subscribers"]
    assert hours.value == pytest.approx((6000 + 1680) / 60)
    assert hours.rate_per_day == pytest.approx(1680 / 60 / 28)
    assert shorts.value == 7800 and shorts.rate_per_day == pytest.approx(2800 / 28)
    assert subs.value == 312 and subs.rate_per_day == pytest.approx(28 / 28)
    assert hours.read_at == READ and subs.read_at == READ
    assert ypp.percent(subs) == pytest.approx(31.2)
    text = ypp.render(got, NOW)
    assert "Subscribers: 312 / 1,000 (31.2%)" in text
    assert "Long-form watch hours, last 365 days: 128.0 / 4,000 (3.2%)" in text
    assert "Shorts views, last 90 days: 7,800 / 10,000,000 (0.1%)" in text


async def test_a_missing_analytics_scope_is_unknown_never_zero(monkeypatch):
    calls = _wire(monkeypatch, scopes=({"https://www.googleapis.com/auth/youtube"}, None))
    got = await ypp.ypp_progress(NOW)
    for key in ("watch_hours", "shorts_views"):
        f = got[key]
        assert f.value is None and f.rate_per_day is None
        assert "yt-analytics.readonly" in f.reason and "re-consent" in f.reason
    # the Data API subscriber count still reads; only its rate is UNKNOWN
    subs = got["subscribers"]
    assert subs.value == 312 and subs.rate_per_day is None
    assert "yt-analytics.readonly" in subs.rate_reason
    assert "day,creatorContentType" not in calls and "day" not in calls
    text = ypp.render(got, NOW)
    assert "Long-form watch hours, last 365 days: UNKNOWN / 4,000" in text
    assert "Subscribers: 312 / 1,000 (31.2%)" in text
    assert "Pace: UNKNOWN" in text
    assert " 0 / 4,000" not in text and "0.0 / 4,000" not in text


async def test_an_api_5xx_is_unknown_with_its_reason(monkeypatch):
    _wire(monkeypatch,
          stats=(None, "clips: HTTP 503 from /youtube/v3/channels"),
          content=(None, "clips: HTTP 503 from /v2/reports"),
          subs=(None, "clips: HTTP 503 from /v2/reports"),
          vids=([], "clips: HTTP 500 from /youtube/v3/search"))
    got = await ypp.ypp_progress(NOW)
    assert got["subscribers"].value is None and "503" in got["subscribers"].reason
    assert got["watch_hours"].value is None and "503" in got["watch_hours"].reason
    assert got["shorts_views"].value is None
    assert got["uploads"]["count"] is None and "500" in got["uploads"]["reason"]
    text = ypp.render(got, NOW)
    assert text.count("UNKNOWN /") == 3
    assert "Uploads, last 6 months: UNKNOWN" in text
    assert "HTTP 503" in text


async def test_a_hidden_subscriber_count_is_unknown(monkeypatch):
    _wire(monkeypatch, stats=({"subscribers": None, "hidden_subscriber_count": True,
                               "read_at": READ}, None))
    got = await ypp.ypp_progress(NOW)
    assert got["subscribers"].value is None and "hides" in got["subscribers"].reason


async def test_a_row_without_a_number_makes_the_figure_unknown(monkeypatch):
    rows = [{"day": _day(5), "creatorContentType": "VIDEO_ON_DEMAND", "views": 3,
             "estimatedMinutesWatched": None}]
    _wire(monkeypatch, content=({"rows": rows, "read_at": READ}, None))
    got = await ypp.ypp_progress(NOW)
    assert got["watch_hours"].value is None and got["shorts_views"].value == 0


def _fig(value, rate, target=1_000, window=None, rate_reason=None):
    return ypp.Figure("Subscribers", target, value, READ, None if value is not None
                      else "not read", rate, rate_reason, window)


def test_pace_with_a_zero_rate_is_not_on_pace():
    assert ypp.pace(_fig(312, 0.0), NOW).startswith("not on pace")
    assert ypp.pace(_fig(312, -0.5), NOW).startswith("not on pace")


def test_pace_with_a_positive_rate_projects_a_date():
    # 688 to go at 4/day: 172 days from 19 Oct 2026 is 9 Apr 2027
    text = ypp.pace(_fig(312, 4.0), NOW)
    assert "09 Apr 2027" in text and "4.0/day" in text


def test_pace_never_projects_from_unknown():
    assert ypp.pace(_fig(None, 4.0), NOW).startswith("UNKNOWN")
    assert ypp.pace(_fig(312, None, rate_reason="scope"), NOW).startswith("UNKNOWN")


def test_pace_says_reached_and_window_ceilings():
    assert ypp.pace(_fig(1_200, 0.0), NOW) == "reached"
    # 1,000 Shorts views a day can never hold 10M inside a 90-day window
    text = ypp.pace(_fig(80_000, 1_000.0, ypp.SHORTS_VIEWS, 90), NOW)
    assert text.startswith("not on pace") and "90,000" in text


async def test_uploads_count_public_ones_in_six_months_and_say_when_capped(monkeypatch):
    def v(days, privacy="public"):
        at = (NOW - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")
        return {"video_id": f"v{days}", "published_at": at, "privacy": privacy,
                "read_at": READ}
    _wire(monkeypatch, vids=([v(2), v(40, "private"), v(100), v(300)], None))
    got = await ypp.ypp_progress(NOW)
    up = got["uploads"]
    assert up["count"] == 2 and not up["capped"]
    assert up["last_public"].date() == date(2026, 10, 17)
    assert "Uploads, last 6 months: 2 public (last 17 Oct 2026)" in ypp.render(got, NOW)

    _wire(monkeypatch, vids=([v(d) for d in range(1, ypp.UPLOAD_PAGE + 1)], None))
    got = await ypp.ypp_progress(NOW)
    assert got["uploads"]["capped"]
    assert f"{ypp.UPLOAD_PAGE}+ public" in ypp.render(got, NOW)


async def test_the_section_shows_read_times_the_note_and_shopping(monkeypatch):
    _wire(monkeypatch)
    text = ypp.render(await ypp.ypp_progress(NOW), NOW)
    assert text.startswith(ypp.TITLE)
    assert "read Mon 12:58 UTC" in text
    assert "YouTube Studio → Earn is authoritative" in text
    assert "qualified" in text
    assert "The store runs on Stripe" in text and "Shopify" in text


async def test_unknown_reads_distinctly_from_a_measured_zero(monkeypatch):
    zero = [{"day": _day(5), "creatorContentType": "VIDEO_ON_DEMAND", "views": 0,
             "estimatedMinutesWatched": 0}]
    _wire(monkeypatch, content=({"rows": zero, "read_at": READ}, None))
    measured = ypp.render(await ypp.ypp_progress(NOW), NOW)
    assert "Long-form watch hours, last 365 days: 0.0 / 4,000 (0.0%)" in measured
    _wire(monkeypatch, content=(None, "clips: HTTP 503 from /v2/reports"))
    unread = ypp.render(await ypp.ypp_progress(NOW), NOW)
    assert "Long-form watch hours, last 365 days: UNKNOWN / 4,000" in unread
    assert "0.0 / 4,000" not in unread


async def test_the_monday_digest_carries_the_section_and_never_raises(monkeypatch):
    async def found(days, now=None):
        return [], []
    monkeypatch.setattr(growth_log, "rows", found)
    monkeypatch.setattr(growth_log, "tutorial_rows", found)

    async def runway():
        return {}
    from atlas.modules.academy import tutorials
    monkeypatch.setattr(tutorials, "runway", runway)

    real_progress = ypp.ypp_progress

    async def boom(now=None):
        raise RuntimeError("down")
    monkeypatch.setattr(ypp, "ypp_progress", boom)
    text = await growth_log.week_section()
    assert ypp.TITLE in text and "UNKNOWN, not zero" in text

    _wire(monkeypatch)
    monkeypatch.setattr(ypp, "ypp_progress", real_progress)
    text = await growth_log.week_section()
    assert "Subscribers: 312 / 1,000" in text and "Pace:" in text
