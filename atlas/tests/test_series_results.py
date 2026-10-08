"""Miro, 2026-09-29: the AI-stories reels "improve over time with each video".
A new series is planned from what the last ones earned -- real numbers only,
an unread series is "not read" (never 0), and unreadable results plan the
series exactly as before."""
from __future__ import annotations

import json
import logging
from datetime import UTC, datetime, timedelta

from atlas.kernel import reels as day
from atlas.kernel.llm import Completion
from atlas.modules.content import reels as playbook
from atlas.modules.content import series

NOW = datetime(2026, 9, 29, 9, 0, tzinfo=UTC)
FRAGMENT = "metrics_read_at"          # the digest query, for MemoryBackend


def _row(sid, title, fmt, part=None, platform=None, views=None, likes=None,
         comments=None, shares=None, age=10.0, total=4, status="done"):
    posted = NOW - timedelta(days=age + 1)
    return {"series_id": sid, "title": title, "format": fmt, "status": status,
            "total_parts": total, "part_no": part, "platform": platform,
            "posted_at": posted if platform else None, "views": views,
            "likes": likes, "comments": comments, "shares": shares,
            "metrics_read_at": (posted + timedelta(days=age)
                                if views is not None else None)}


def fixture_rows():
    return [
        # 1: read on both platforms; per platform, never summed across them.
        _row(1, "Fridge Wars", "fruit saga", 1, "instagram", 20_000, 900, 50, 50),
        _row(1, "Fridge Wars", "fruit saga", 1, "tiktok", 5_000, 100, 0, 0),
        _row(1, "Fridge Wars", "fruit saga", 2, "instagram", 10_000, None, 5, 5),
        _row(1, "Fridge Wars", "fruit saga", 2, "tiktok"),       # tiktok unread
        # 2: posted, recorded, never read -> not read, never 0.
        _row(2, "The Parking Spot", "Egyptian Arabic series", 1, "instagram"),
        _row(2, "The Parking Spot", "Egyptian Arabic series", 2, "instagram"),
        # 3: active, nothing posted yet.
        _row(3, "Night Shift", "series", status="active"),
        # 4: Arabic and strong on Instagram, 3 mature parts.
        *[_row(4, "The Return", "Egyptian Arabic series", n, "instagram",
               40_000, 3000, 100, 100, total=6) for n in (1, 2, 3)],
        # 5: weak; its second part was read the day after posting (not ranked).
        _row(5, "Toast Trial", "object drama series", 1, "instagram", 2_000,
             50, 5, 5, total=3),
        _row(5, "Toast Trial", "object drama series", 2, "instagram", 2_000,
             50, 5, 5, total=3),
        _row(5, "Toast Trial", "object drama series", 3, "instagram", 900,
             10, 1, 1, age=1.0, total=3),
    ]


def test_the_digest_joins_parts_to_posts_to_their_latest_reading():
    got = {i["series_id"]: i for i in series.digest(fixture_rows())}
    fridge = got[1]
    assert fridge["parts_posted"] == 2 and fridge["read"]
    ig, tt = fridge["platforms"]["instagram"], fridge["platforms"]["tiktok"]
    assert ig["views"] == 30_000 and ig["views_per_part"] == 15_000
    assert ig["per_part_views"] == {1: 20_000, 2: 10_000}
    # Engagement only from the post whose four counts were all read.
    assert ig["engagement"] == (900 + 50 + 50) / 20_000
    assert tt["parts_read"] == 1 and tt["views"] == 5_000     # not 5_000 + 0
    assert ig["read_at"] == NOW - timedelta(days=1)
    unread = got[2]
    assert unread["parts_posted"] == 2 and unread["read"] is False
    assert unread["platforms"] == {}                          # never a zero
    assert got[3]["parts_posted"] == 0 and got[3]["read"] is False
    toast = got[5]["platforms"]["instagram"]
    assert toast["parts_read"] == 3 and toast["parts_mature"] == 2
    assert toast["mature_views_per_part"] == 2_000


def test_the_block_ranks_mature_series_on_one_platform_with_read_times():
    block = series.results_block(series.digest(fixture_rows()))
    assert block is not None
    assert block.startswith("Past series on instagram")
    best, weakest = block.split("Weakest:")
    assert "The Return" in best and "40,000 views per part" in best
    assert "Toast Trial" in weakest and "2,000 views per part" in weakest
    assert "Fridge Wars" in best                   # 3 eligible: 2 top, 1 bottom
    assert "The Parking Spot" not in block and "Night Shift" not in block
    assert "read 2026-09-28 09:00 UTC" in block
    assert "Egyptian Arabic 1/2 vs 0/1" in block
    assert "average length 5.0 vs 3.0 parts" in block


def test_no_block_without_two_mature_parts():
    rows = [_row(1, "Fridge Wars", "fruit saga", 1, "instagram", 20_000),
            _row(1, "Fridge Wars", "fruit saga", 2, "instagram", 20_000, age=1),
            _row(2, "The Parking Spot", "series", 1, "instagram")]
    assert series.results_block(series.digest(rows)) is None
    assert series.results_block([]) is None


def test_the_format_nudge_uses_the_reel_format_weighting():
    earned = series.format_bonus(series.digest(fixture_rows()), NOW)
    assert earned["egyptian arabic series"] > earned.get("fruit saga", 0) > 0
    assert "object drama series" not in earned     # under both bars: nothing


def _planner(seen):
    async def fake_think(need, system, messages, max_tokens=0, **kw):
        seen.append((system, messages[0].content))
        return Completion(text=json.dumps({
            "title": "Kettle Court", "characters": "Kiki, a kettle; Mo, a mug",
            "parts": ["a", "b", "c"]}), provider="t", model="t")
    return fake_think


async def test_the_plan_carries_the_results_when_there_are_some(
        memory_backend, monkeypatch):
    memory_backend.responses[FRAGMENT] = fixture_rows()
    memory_backend.responses["INSERT INTO reel_series"] = {"id": 9}
    seen: list = []
    monkeypatch.setattr(series, "think", _planner(seen))
    result = await series.plan_series("object drama series")
    assert result.ok
    system, ask = seen[0]
    assert "Original characters only" in system and "real ending" in system
    assert ask.startswith("Format: object drama series")
    assert "<untrusted_data source='reel_series_results'>" in ask
    assert "The Return" in ask and "NEW title" in ask
    wrapped = ask.split("<untrusted_data", 1)[1]
    assert "The Return" in wrapped                   # titles are quoted as data


async def test_no_data_yet_plans_exactly_as_today(memory_backend, monkeypatch, caplog):
    memory_backend.responses[FRAGMENT] = [
        _row(2, "The Parking Spot", "series", 1, "instagram")]
    memory_backend.responses["INSERT INTO reel_series"] = {"id": 9}
    seen: list = []
    monkeypatch.setattr(series, "think", _planner(seen))
    with caplog.at_level(logging.WARNING, logger="atlas.content"):
        assert (await series.plan_series("fruit saga")).ok
    assert seen[0][1] == "Format: fruit saga"
    assert "unreadable" not in caplog.text


async def test_unreadable_results_plan_exactly_as_today_and_say_so(
        memory_backend, monkeypatch, caplog):
    memory_backend.responses["INSERT INTO reel_series"] = {"id": 9}
    seen: list = []
    monkeypatch.setattr(series, "think", _planner(seen))

    async def broken():
        raise RuntimeError("reel_metrics: connection refused")

    monkeypatch.setattr(series, "results_digest", broken)
    with caplog.at_level(logging.WARNING, logger="atlas.content"):
        assert (await series.plan_series("fruit saga")).ok
    assert seen[0][1] == "Format: fruit saga"
    assert "series results unreadable" in caplog.text


async def test_a_title_already_on_file_is_refused(memory_backend, monkeypatch):
    memory_backend.responses[FRAGMENT] = []
    memory_backend.responses["INSERT INTO reel_series"] = None   # NOT EXISTS hit
    monkeypatch.setattr(series, "think", _planner([]))
    result = await series.plan_series("fruit saga")
    assert not result.ok and "already a series" in result.detail


async def test_the_next_format_leans_to_what_earned_within_miros_priority(
        memory_backend, monkeypatch):
    first, earner = "haunted office saga", "desert cliffhanger series"
    assert playbook.priority(first) == playbook.priority(earner)
    assert playbook.bucket_of(first) == playbook.bucket_of(earner) == "serial"

    async def top(n):
        return [{"id": 1, "format": first}, {"id": 2, "format": earner},
                {"id": 3, "format": "fruit saga"}]              # produce: skipped

    async def digest():
        return series.digest([
            _row(1, "Dune Letters", earner, n, "instagram", 40_000, 3000, 100, 100,
                 age=5) for n in (1, 2)])

    asked: list[str] = []

    async def fake_execute(req):
        from atlas.kernel.contracts import ActionResult
        asked.append(req.params["format_name"])
        return ActionResult(True, "planned")

    async def nothing(*a, **k):
        return None

    monkeypatch.setattr(playbook, "top_formats", top)
    monkeypatch.setattr(series, "results_digest", digest)
    monkeypatch.setattr(series, "ensure_seeds", nothing)
    monkeypatch.setattr(series, "current", nothing)
    monkeypatch.setattr(day, "_sync_series_cards", nothing)
    monkeypatch.setattr(day, "execute", fake_execute)
    await day.series_step()
    assert asked == [earner]

    async def broken():
        raise RuntimeError("reel_metrics unreadable")

    monkeypatch.setattr(series, "results_digest", broken)
    await day.series_step()
    assert asked[-1] == first                        # as before: no penalty
