"""The Content agent (six agents phase 8): `content` replaces the `church`
roster entry and owns academy, youtube, church and reddit work; the church
job, tiers and posting rules are untouched; the Monday content_weekly job
runs only through the executor; the channel study reads only."""
from __future__ import annotations

from datetime import UTC, datetime

from atlas.kernel import board, departments, missions
from atlas.kernel.contracts import Tier


def _returns(value):
    async def fake(*_a, **_kw):
        return value
    return fake


def test_the_roster_has_content_and_no_church():
    from atlas.kernel.agents import ROSTER, _church_system, build_roster
    from atlas.tools.builtin import register_all
    register_all()
    build_roster()
    assert "content" in ROSTER and "church" not in ROSTER
    agent = ROSTER["content"]
    assert agent.system.startswith(_church_system())
    assert {"module_status", "module_brief", "read_metrics", "sql_query"} <= agent.tools
    from atlas.kernel.tools import TOOLS
    assert all(TOOLS[t].action is None for t in agent.tools if t in TOOLS)


def test_the_persona_moved_with_the_agent():
    from atlas.kernel.personas import PERSONAS
    assert "content" in PERSONAS and "church" not in PERSONAS
    assert PERSONAS["content"].name == "Mark"


def test_the_department_lists_content_reddit_and_the_weekly_job():
    dept = {d.key: d for d in departments.DEPARTMENTS}["content"]
    assert "content" in dept.agents and "church" not in dept.agents
    assert "reddit" in dept.modules and "church" in dept.modules
    assert {"church", "content_weekly"} <= set(dept.jobs)
    assert "content_weekly" in departments.JOBS


def test_content_owns_academy_youtube_church_and_reddit_work():
    owner = missions.BY_AGENT["content"]
    assert set(owner.modules) == {"academy", "youtube", "church", "reddit"}
    for ask in ("draft a reddit post", "how is the ECC calendar",
                "start a title test on the academy video", "youtube comment replies"):
        assert missions.owner_for(ask).agent == "content", ask
    # The reels agent left the roster on 2026-10-28; no owner claims its work.
    assert getattr(missions.owner_for("make the next reels episode"), "agent",
                   None) != "reels"


def test_church_cards_read_content_ecc():
    header = board.card_header(7, "church.schedule_youtube_shorts", {"post_ids": [1]},
                               None, {"agent": "Content"})
    assert header.startswith("[Content · ECC] #7")
    header = board.card_header(8, "reddit.submit_post", {"draft_id": 1}, None, {})
    assert header.startswith("[Content · Atlas Agency] #8")


def test_the_church_tiers_and_gate_are_unchanged():
    from atlas.kernel import qa_gate
    from atlas.modules import church
    acts = {a.name: a for a in church.MODULE.actions()}
    for name in ("church.schedule_youtube_shorts", "church.schedule_facebook_reels"):
        assert acts[name].tier is Tier.ASK and name in qa_gate.GATED_NAMES


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
    body, _ = study.compose(await study.collect("ecc"))
    assert "- T: UNKNOWN (no analytics scope)" in body
    rid, why = await study.run("guardian")
    assert rid is None and "--channel" in why
