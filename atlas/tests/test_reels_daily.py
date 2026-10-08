

async def test_daily_writes_nothing_outside_a_booted_process(monkeypatch):
    """A hand run with no catalog loaded once filed a bogus digest."""
    from atlas.kernel import reels
    from atlas.kernel.registry import REGISTRY
    monkeypatch.setattr(REGISTRY, "actions", {})
    touched = []

    async def boom(*a, **k):
        touched.append(a)
        raise AssertionError("nothing may be read or written")

    monkeypatch.setattr(reels, "already_sent_today", boom)
    monkeypatch.setattr(reels, "send_digest", boom)
    await reels.daily()
    assert touched == []


async def test_the_digest_carries_a_tiktok_line_with_its_read_time(memory_backend):
    from datetime import UTC, datetime

    from atlas.kernel import reels
    read = datetime(2026, 9, 27, 21, 5, tzinfo=UTC)
    memory_backend.responses["FROM reel_metrics"] = [
        {"post_ref": "pz-1", "at": read, "views": 1200.0, "likes": 80.0, "comments": None},
        {"post_ref": "pz-2", "at": read, "views": 300.0, "likes": 20.0, "comments": None}]
    line = (await reels.tiktok_lines())[0]
    assert line.startswith("TikTok (read 21:05 UTC): 2 post(s) measured, 1,500 views")
    assert "not measured comments" in line
    assert "moderation: unknown" in line


async def test_no_tiktok_posts_is_said_not_zeroed(memory_backend):
    from atlas.kernel import reels
    memory_backend.responses["FROM reel_metrics"] = []
    line = (await reels.tiktok_lines())[0]
    assert "no post measured" in line and "0 views" not in line


async def test_the_daily_digest_is_stored_but_said_only_weekly(memory_backend, monkeypatch):
    """Miro, 2026-10-06: content posts itself with no card and is reported
    in the weekly. The day's digest is still written (the weekly reads it,
    and the day is not run twice) but no #reels message goes out unless
    REELS_DIGEST=daily."""
    from atlas.kernel import reels
    sent = []

    async def fake_notify(msg):
        sent.append(msg)
        return ["slack"]

    monkeypatch.setattr(reels, "notify", fake_notify)
    monkeypatch.delenv(reels.DIGEST_ENV, raising=False)
    assert await reels.send_digest(["posted 2"]) == []
    assert sent == []
    stored = [args for sql, args in memory_backend.sql_log if "INSERT INTO reports" in sql]
    assert stored and stored[0][0] == reels.REPORT_KIND and stored[0][3] == "weekly"
    monkeypatch.setenv(reels.DIGEST_ENV, "daily")
    assert await reels.send_digest(["posted 2"]) == ["slack"]
    assert sent[0].title == reels.TITLE


async def test_the_week_section_counts_from_the_ledgers(memory_backend):
    from atlas.kernel import reels
    memory_backend.responses["FROM reel_posts"] = [
        {"platform": "instagram", "n": 7}, {"platform": "tiktok", "n": 7}]
    memory_backend.responses["FROM course_posts"] = {"n": 2}
    memory_backend.responses["FROM clip_outputs"] = {"n": 3}
    memory_backend.responses["FROM proposals"] = {"n": 1}
    out = await reels.week_section(7)
    assert out.startswith("*Content this week*")
    assert "AI reels posted: instagram 7, tiktok 7" in out
    assert "Course promos posted: 2" in out and "Campaign clips posted: 3" in out
    assert "Held at a gate (carded): 1" in out
