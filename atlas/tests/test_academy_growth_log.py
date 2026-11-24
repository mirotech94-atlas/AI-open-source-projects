"""The weekly 7-day log of the Academy's Shorts (academy/growth_log.py):
the owner, 2026-10-12. Numbers only where they were read; never invented."""
from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta

from atlas.modules.academy import growth_log

NOW = datetime(2026, 10, 19, 13, 0, tzinfo=UTC)


def test_what_names_the_course_and_lesson():
    assert growth_log._what("lesson:sell/1.2") == "Sell With AI 1.2"
    assert growth_log._what("lesson:operator/0.1") == "Build a Safe AI Operator 0.1"
    assert growth_log._what("ai-for-every-age") == "ai-for-every-age"


async def test_rows_join_postiz_links_and_youtube_stats(monkeypatch):
    async def fetch_all(sql, *args):
        if "academy_promo_posts" in sql:
            return [{"at": NOW - timedelta(days=2), "slug": "lesson:sell/0.1", "media_id": 433,
                     "detail": "render 433: TikTok: posted — Postiz post tt1 · YouTube: Postiz post yt1"}]
        return [{"video_id": "abc123def", "views": 140, "likes": 9, "comments": 1,
                 "measured_at": NOW - timedelta(hours=3)}]
    monkeypatch.setattr("atlas.kernel.state.fetch_all", fetch_all)

    async def urls(since, now):
        return {"tt1": ("TikTok", "https://www.tiktok.com/@example_handle"),
                "yt1": ("YouTube", "https://www.youtube.com/watch?v=abc123def")}, None
    monkeypatch.setattr(growth_log, "_postiz_urls", urls)
    found, gaps = await growth_log.rows(7, NOW)
    assert gaps == [] and len(found) == 1 and found[0]["what"] == "Sell With AI 0.1"
    text = growth_log.render(found, gaps, NOW)
    assert "YouTube 140 views, 9 likes" in text and "watch?v=abc123def" in text
    # TikTok returned the profile, not the video: "pending", never a guess.
    assert "TikTok views not read (no TikTok metrics source) link pending" in text
    assert "YouTube views so far 140 across 1 read" in text
    assert "Views are not sales" in text


def test_an_empty_week_and_an_unread_source_are_said_plainly():
    assert "No Academy Short went out this week." in growth_log.render([], [], NOW)
    row = {"at": NOW, "what": "Sell With AI 1.2",
           "legs": [{"platform": "YouTube", "url": "", "video_id": None, "stats": None}]}
    text = growth_log.render([row], ["Postiz could not be read (ConnectError)"], NOW)
    assert "YouTube views not read yet (link pending)" in text
    assert "Not read: Postiz could not be read (ConnectError)." in text


async def test_the_section_never_raises(monkeypatch):
    async def boom(days, now=None):
        raise RuntimeError("db")
    monkeypatch.setattr(growth_log, "rows", boom)
    text = await growth_log.week_section()
    assert "UNKNOWN, not zero" in text


# ------------------------------------------- the Monday tutorial table (task 8)

async def _all_scopes(_ch):
    return {"https://www.googleapis.com/auth/yt-analytics.readonly",
            "https://www.googleapis.com/auth/youtube.force-ssl"}, None


async def test_the_tutorial_table_says_unknown_when_analytics_cannot_be_read(monkeypatch):
    async def posts(*_a, **_k):
        return [{"series": "n8n", "episode": 0, "video_id": "v1", "at": datetime.now(UTC)}]
    monkeypatch.setattr(growth_log, "_tutorial_posts", posts)

    async def analytics(_ch, _vid, _days):
        return None, "clips: not permitted — the token likely lacks the yt-analytics.readonly scope"
    monkeypatch.setattr(growth_log.api, "analytics", analytics)
    monkeypatch.setattr(growth_log.api, "token_scopes", _all_scopes)
    rows, gaps = await growth_log.tutorial_rows(7)
    text = growth_log.render_tutorials(rows, gaps)
    assert "UNKNOWN" in text and "yt-analytics.readonly" in text and "| 0 |" not in text.split("\n")[2]
    assert "UNKNOWN" in text.split("\n")[2]
    assert "Best formula: UNKNOWN" in text
    assert "Try: nothing until analytics can be read" in text


def _stats(views, avg_pct, likes=3, comments=1, avg_s=95):
    return {"views": views, "likes": likes, "comments": comments,
            "average_view_percentage": avg_pct, "average_view_duration": avg_s,
            "estimated_minutes_watched": 10, "read_at": NOW}


async def test_the_tutorial_table_names_best_and_worst_formula_and_one_change(monkeypatch):
    async def posts(*_a, **_k):
        return [{"series": "n8n", "episode": 0, "video_id": "a", "at": NOW},
                {"series": "n8n", "episode": 1, "video_id": "b", "at": NOW}]
    monkeypatch.setattr(growth_log, "_tutorial_posts", posts)
    stats = {"a": _stats(1200, 55.5), "b": _stats(80, 31.2)}

    async def analytics(_ch, vid, _days):
        return stats[vid], None
    monkeypatch.setattr(growth_log.api, "analytics", analytics)
    monkeypatch.setattr(growth_log.api, "token_scopes", _all_scopes)
    rows, gaps = await growth_log.tutorial_rows(7, NOW)
    assert gaps == [] and [r["stats"]["views"] for r in rows] == [1200, 80]
    text = growth_log.render_tutorials(rows, gaps, NOW)
    assert "episode | views | likes | comments | avg % | avg s" in text
    assert "1,200" in text and "56%" in text and "95" in text
    best = rows[0]["level_label"]
    worst = rows[1]["level_label"]
    assert f"Best formula: {best} · Worst: {worst}" in text
    assert "Try: pacing or demo zoom" in text
    assert "read Mon 13:00 UTC" in text

    stats["b"] = _stats(80, 62.0)
    rows, gaps = await growth_log.tutorial_rows(7, NOW)
    assert "Try: thumbnail or title (Tier 2 card)" in growth_log.render_tutorials(rows, gaps, NOW)


async def test_a_token_missing_scopes_is_named_never_guessed(monkeypatch):
    async def posts(*_a, **_k):
        return []
    monkeypatch.setattr(growth_log, "_tutorial_posts", posts)

    async def scopes(_ch):
        return {"https://www.googleapis.com/auth/youtube.upload"}, None
    monkeypatch.setattr(growth_log.api, "token_scopes", scopes)
    _rows, gaps = await growth_log.tutorial_rows(7, NOW)
    joined = " ".join(gaps)
    assert "yt-analytics.readonly" in joined and "youtube.force-ssl" in joined
    assert "the owner must re-consent the token" in joined

    async def unreadable(_ch):
        return None, "clips: tokeninfo: timed out"
    monkeypatch.setattr(growth_log.api, "token_scopes", unreadable)
    _rows, gaps = await growth_log.tutorial_rows(7, NOW)
    assert any("timed out" in g for g in gaps)


async def test_a_missing_figure_is_unknown_not_zero(monkeypatch):
    async def posts(*_a, **_k):
        return [{"series": "n8n", "episode": 0, "video_id": "a", "at": NOW}]
    monkeypatch.setattr(growth_log, "_tutorial_posts", posts)

    async def analytics(_ch, _vid, _days):
        return _stats(40, None, comments=None, avg_s=None), None
    monkeypatch.setattr(growth_log.api, "analytics", analytics)
    monkeypatch.setattr(growth_log.api, "token_scopes", _all_scopes)
    rows, gaps = await growth_log.tutorial_rows(7, NOW)
    text = growth_log.render_tutorials(rows, gaps, NOW)
    line = text.split("\n")[2]
    assert line.count("UNKNOWN") == 3 and "| 40 |" in line
    assert line.endswith("| 13:00")              # the row's own read time
    assert ("Not read: n8n 0: comments, averageViewPercentage, averageViewDuration "
            "not returned by the Analytics API.") in text


def test_a_partial_week_suggests_no_change_it_cannot_back():
    read = {"label": "n8n 0", "level_label": "Level 0 · Basics", "stats": _stats(900, 60.0)}
    unread = {"label": "n8n 1", "level_label": "Level 0 · Basics", "stats": None}
    text = growth_log.render_tutorials([read, unread], [], NOW)
    assert "Best formula: UNKNOWN · Worst: UNKNOWN" in text
    assert "Try: nothing until analytics can be read" in text
    unread["stats"] = None
    read["stats"] = _stats(900, 30.0)
    assert "Try: pacing or demo zoom" in growth_log.render_tutorials([read, unread], [], NOW)


async def test_the_week_section_carries_the_tutorials_heading(monkeypatch):
    async def found(days, now=None):
        return [], []
    monkeypatch.setattr(growth_log, "rows", found)

    async def tut(days=7, now=None):
        raise RuntimeError("db")
    monkeypatch.setattr(growth_log, "tutorial_rows", tut)
    text = await growth_log.week_section()
    assert "No Academy Short went out this week." in text
    assert "Tutorials" in text and "UNKNOWN, not zero" in text


def test_each_row_carries_its_own_read_time_and_formulas_sum_by_level():
    from datetime import timedelta as td
    early = _stats(100, 50.0)
    early["read_at"] = NOW - td(hours=2)
    rows = [{"label": "a 1", "level_label": "Level 0 · Basics", "stats": early},
            {"label": "a 2", "level_label": "Level 0 · Basics", "stats": _stats(150, 50.0)},
            {"label": "b 1", "level_label": "Level 1 · Builder", "stats": _stats(200, 50.0)},
            {"label": "b 2", "level_label": "Level 2 · Pro", "stats": None}]
    lines = growth_log.render_tutorials(rows, [], NOW).split("\n")
    assert lines[2].endswith("| 11:00") and lines[3].endswith("| 13:00")
    assert lines[5].endswith("| UNKNOWN") and "(read Mon 13:00 UTC)" in lines[0]
    rows.pop()
    text = growth_log.render_tutorials(rows, [], NOW)
    # Level 0 sums to 250 over Level 1's 200, though b 1 is the top single row.
    assert "Best formula: Level 0 · Basics · Worst: Level 1 · Builder" in text
    one = growth_log.render_tutorials(rows[:1], [], NOW)
    assert "Best formula: Level 0 · Basics (one episode)" in one and "Worst" not in one


# ------------------------------------------- the script runway line (2026-10-15)

async def test_the_tutorials_section_carries_the_script_runway(monkeypatch):
    async def found(days=7, now=None):
        return [], []
    monkeypatch.setattr(growth_log, "tutorial_rows", found)
    from atlas.modules.academy import tutorials

    async def runway():
        return {"claude": 2, "n8n": 1}
    monkeypatch.setattr(tutorials, "runway", runway)
    text = await growth_log._tutorials_section(7)
    last = text.split("\n")[-1]
    assert re.fullmatch(r"Script runway \(read \w{3} \d\d:\d\d UTC\): claude 2 · n8n 1",
                        last), last


async def test_an_unread_runway_is_unknown_in_the_section(monkeypatch):
    async def boom_rows(days=7, now=None):
        raise RuntimeError("db")
    monkeypatch.setattr(growth_log, "tutorial_rows", boom_rows)

    async def fetch_all(sql, *a):
        raise RuntimeError("db")
    monkeypatch.setattr("atlas.kernel.state.fetch_all", fetch_all)
    text = await growth_log._tutorials_section(7)
    assert "UNKNOWN, not zero" in text
    last = text.split("\n")[-1]
    assert re.match(r"Script runway \(tried \w{3} \d\d:\d\d UTC\): claude UNKNOWN", last)
    assert " 0" not in last.split("): ", 1)[1]


# ------------------------------------- end screens to add (the owner 2026-10-17)

def _end_posts(monkeypatch, found):
    async def posts(since):
        if isinstance(found, Exception):
            raise found
        return found
    monkeypatch.setattr(growth_log, "_end_screen_posts", posts)


def _posted(monkeypatch, table):
    """tutorials.posted, faked: {(series, episode): row or an exception}."""
    from atlas.modules.academy import tutorials

    async def posted(series, episode):
        got = table.get((series, episode))
        if isinstance(got, Exception):
            raise got
        return got
    monkeypatch.setattr(tutorials, "posted", posted)


async def test_the_end_screen_list_points_at_the_next_public_episode(monkeypatch):
    from atlas.modules.academy import tutorial_series as ts
    ep1 = ts.series("n8n").episode(1)
    _end_posts(monkeypatch, [{"series": "n8n", "episode": 1, "video_id": "vidOne1",
                              "playlist_id": "PLn8n", "at": NOW}])
    _posted(monkeypatch, {("n8n", 2): {"video_id": "vidTwo2", "public": True}})
    text = await growth_log.end_screen_section(7, NOW)
    lines = text.split("\n")
    assert lines[0].startswith(growth_log.END_SCREENS) and "Mon 13:00 UTC" in lines[0]
    assert ep1.title in text and "https://youtu.be/vidOne1" in text
    assert "next: n8n #2 https://youtu.be/vidTwo2" in text
    assert growth_log.STUDIO_LINE in text
    assert "640px" in growth_log.STUDIO_LINE and "15 s" in growth_log.STUDIO_LINE


async def test_an_unpublished_next_episode_falls_back_to_the_series_playlist(monkeypatch):
    _end_posts(monkeypatch, [{"series": "n8n", "episode": 1, "video_id": "vidOne1",
                              "playlist_id": "PLn8n", "at": NOW}])
    _posted(monkeypatch, {("n8n", 2): {"video_id": "vidTwo2", "public": False}})
    text = await growth_log.end_screen_section(7, NOW)
    assert "next: the series playlist https://www.youtube.com/playlist?list=PLn8n" in text


async def test_no_playlist_on_record_falls_back_to_start_here(monkeypatch):
    _end_posts(monkeypatch, [{"series": "n8n", "episode": 1, "video_id": "vidOne1",
                              "playlist_id": None, "at": NOW}])
    _posted(monkeypatch, {})
    text = await growth_log.end_screen_section(7, NOW)
    assert 'next: the "Start here" playlist' in text


async def test_a_failed_read_of_the_next_episode_is_unknown_never_a_fallback(monkeypatch):
    _end_posts(monkeypatch, [{"series": "n8n", "episode": 1, "video_id": "vidOne1",
                              "playlist_id": "PLn8n", "at": NOW}])
    _posted(monkeypatch, {("n8n", 2): RuntimeError("db")})
    text = await growth_log.end_screen_section(7, NOW)
    assert "next: UNKNOWN" in text and "playlist?list=" not in text


async def test_an_unread_week_of_end_screens_is_unknown(monkeypatch):
    _end_posts(monkeypatch, RuntimeError("db"))
    text = await growth_log.end_screen_section(7, NOW)
    assert text.startswith(growth_log.END_SCREENS) and "UNKNOWN" in text
    assert "None went public" not in text


async def test_a_quiet_week_says_none_went_public(monkeypatch):
    _end_posts(monkeypatch, [])
    text = await growth_log.end_screen_section(7, NOW)
    assert "No tutorial went public in the last 7 days" in text


async def test_the_week_section_carries_the_end_screen_list(monkeypatch):
    async def found(days=7, now=None):
        return [], []
    monkeypatch.setattr(growth_log, "rows", found)
    monkeypatch.setattr(growth_log, "tutorial_rows", found)
    _end_posts(monkeypatch, [])
    text = await growth_log.week_section()
    assert growth_log.END_SCREENS in text
