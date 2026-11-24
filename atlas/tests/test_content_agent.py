"""The Content agent (six agents phase 8): `content` replaces the `community`
roster entry and owns academy, youtube, community and reddit work; the community
job, tiers and posting rules are untouched; the Monday content_weekly job
runs only through the executor; the channel study reads only."""
from __future__ import annotations

from datetime import UTC, datetime


def _returns(value):
    async def fake(*_a, **_kw):
        return value
    return fake














def test_content_weekly_is_monday_eight_new_york_skip_stale():
    from atlas.kernel import scheduler
    from atlas.kernel.missed import Missed
    policy = scheduler.POLICIES["content_weekly"]
    assert policy.missed is Missed.SKIP_STALE and policy.recorded
    windows = scheduler._content_windows(datetime(2026, 11, 3, 15, tzinfo=UTC),
                                         policy.horizon)
    at = windows[-1].scheduled_for
    assert at.weekday() == 0 and at.hour == 8 and str(at.tzinfo) == "America/New_York"
    assert "content_weekly" in scheduler._DETACHED


async def test_the_weekly_job_acts_only_through_the_executor(monkeypatch):
    from atlas.kernel import executor
    from atlas.kernel.contracts import ActionResult
    from atlas.modules.academy import growth
    called = []

    async def execute(req):
        called.append(req.action)
        return ActionResult(False, "watch-only")

    monkeypatch.setattr(executor, "execute", execute)
    monkeypatch.setattr(growth, "open_tests", _returns(["v1"]))
    monkeypatch.setattr(growth, "send_alerts", _returns([]))
    out = await growth.run_weekly()
    assert called == ["academy.weekly_growth_report", "academy.plan_content",
                      "academy.switch_title"]
    assert out["alerts"] == 0


async def test_the_channel_study_names_unknown_when_it_cannot_read(monkeypatch):
    from atlas.modules.youtube import api, study
    monkeypatch.setattr(api, "uploads_playlist", _returns((None, "no token")))
    body, _ = study.compose(await study.collect("clips"))
    assert body.startswith("UNKNOWN: uploads playlist unreadable: no token")


async def test_the_channel_study_never_calls_search(monkeypatch):
    from atlas.modules.youtube import api, study
    monkeypatch.setattr(api, "videos", None, raising=False)
    monkeypatch.setattr(api, "uploads_playlist", _returns(("UU1", None)))
    monkeypatch.setattr(api, "playlist_items", _returns(([{"video_id": "a"}], None)))
    monkeypatch.setattr(api, "video_details", _returns((
        [{"video_id": "a", "title": "T", "privacy": "public",
          "published_at": "2026-10-01"}], None)))
    monkeypatch.setattr(api, "analytics", _returns((None, "no analytics scope")))
    body, _ = study.compose(await study.collect("amsm"))
    assert "- T: UNKNOWN (no analytics scope)" in body
    rid, why = await study.run("guardian")
    assert rid is None and "--channel" in why
