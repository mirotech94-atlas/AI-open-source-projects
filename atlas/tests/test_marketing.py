"""The marketing agent: experiments, a playbook, and money found not spent.

The hard limits come first, because they are what stops this from becoming a
system that promotes itself. Nothing under `kernel/marketing` may import the
executor, call `permissions.classify`, or name a `Tier`. The learning loop is
a memory write and a standing-rule *candidate* — never a tier change.

After that: the measurement discipline. A platform read that failed, or a
baseline with too little history behind it, is `unmeasured` with the reason.
It is never `flat`, and it is never a number nobody measured.
"""
from __future__ import annotations

import ast
import pathlib
from datetime import UTC, datetime, timedelta

import pytest

from atlas.kernel.marketing import cycle, experiments, monetize, playbook

ROOT = pathlib.Path(__file__).resolve().parent.parent / "atlas"
PKG = ROOT / "kernel" / "marketing"


@pytest.fixture(autouse=True)
def _platform_modules_on(monkeypatch):
    """Every platform module counts as switched on unless a test says not."""
    from atlas.kernel.registry import REGISTRY
    for name in ("youtube", "instagram", "tiktok"):
        monkeypatch.setitem(REGISTRY.modules, name, object())


# ------------------------------------------------------------ hard limits

def _imports_of(path: pathlib.Path) -> set[str]:
    tree = ast.parse(path.read_text())
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            mod = node.module or ""
            if node.level:           # relative: resolve against kernel.marketing
                base = ("atlas.kernel.marketing" if node.level == 1
                        else "atlas.kernel" if node.level == 2 else "atlas")
                mod = f"{base}.{mod}" if mod else base
            names.add(mod)
            names.update(f"{mod}.{a.name}" for a in node.names)
    return names


def _marketing_files() -> list[pathlib.Path]:
    files = sorted(PKG.glob("*.py"))
    assert files, "kernel/marketing has no files to scan"
    return files


def test_nothing_in_kernel_marketing_imports_the_executor():
    for f in _marketing_files():
        for mod in _imports_of(f):
            assert "executor" not in mod, f"{f.name} imports {mod}"


def test_nothing_in_kernel_marketing_classifies_or_assigns_a_tier():
    """`classify` is how a tier is decided and `Tier` is how one is written
    down. The marketing layer does neither: it proposes, and the catalog in
    `atlas/modules/marketing` carries the tiers."""
    for f in _marketing_files():
        source = f.read_text()
        tree = ast.parse(source)
        for mod in _imports_of(f):
            tail = mod.rsplit(".", 1)[-1]
            assert tail not in {"classify", "Tier", "PERMANENT_DENY"}, \
                f"{f.name} imports {mod}"
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute) and node.attr == "classify":
                pytest.fail(f"{f.name} calls .classify")
            if isinstance(node, ast.Name) and node.id == "Tier":
                pytest.fail(f"{f.name} names Tier")
        assert "tier=" not in source, f"{f.name} assigns a tier"


def test_the_marketing_module_owns_the_tiers_and_they_are_all_auto():
    from atlas.kernel.contracts import Tier
    from atlas.modules.marketing import MODULE

    names = {a.name for a in MODULE.actions()}
    assert names == {"marketing.propose_experiment", "marketing.propose_idea",
                     "marketing.record_outcome"}
    for action in MODULE.actions():
        assert action.tier is Tier.AUTO
        assert action.dry_run is not None      # invariant 8
        assert action.rollback is not None


# ------------------------------------------------------------- the playbook

def _memory(content: str, *, business="flycheap", platform="youtube",
            variant="route-deal short", outcome="won", days_ago=1):
    from atlas.kernel.memory import Kind, Memory
    return Memory(
        id=1, kind=Kind.SEMANTIC, namespace="playbook", content=content,
        meta={"business": business, "platform": platform, "variant": variant,
              "outcome": outcome},
        importance=0.7,
        at=datetime.now(UTC) - timedelta(days=days_ago),
    )


@pytest.mark.asyncio
async def test_the_playbook_ranks_a_recent_win_above_an_old_one(monkeypatch):
    old = _memory("old win", outcome="won", days_ago=200)
    new = _memory("new win", outcome="won", days_ago=1)

    async def fake_recall(query, namespaces, **kw):
        assert namespaces == ["playbook"]
        return [old, new]

    monkeypatch.setattr(playbook, "recall", fake_recall)
    lessons = await playbook.read("flycheap")
    assert [lesson.content for lesson in lessons] == ["new win", "old win"]


@pytest.mark.asyncio
async def test_the_playbook_only_returns_this_business(monkeypatch):
    async def fake_recall(query, namespaces, **kw):
        return [_memory("flycheap lesson", business="flycheap"),
                _memory("guardian lesson", business="guardian")]

    monkeypatch.setattr(playbook, "recall", fake_recall)
    lessons = await playbook.read("guardian")
    assert [lesson.content for lesson in lessons] == ["guardian lesson"]


# -------------------------------------------------------------- propose

@pytest.mark.asyncio
async def test_propose_reads_the_playbook_and_the_baseline_and_thinks_once(
        monkeypatch):
    calls: dict[str, int] = {"think": 0, "playbook": 0, "baseline": 0,
                             "predict": 0}

    async def fake_playbook(business, **kw):
        calls["playbook"] += 1
        return [_lesson("route-deal shorts won twice")]

    async def fake_baseline(platform, metric, **kw):
        calls["baseline"] += 1
        return experiments.Baseline(
            value=1000.0, sigma=200.0, n=9, metric=metric,
            observed_at=datetime(2026, 9, 24, 9, 12, tzinfo=UTC),
            window="4-week median"), None

    async def fake_predict(action, severity, **kw):
        calls["predict"] += 1
        assert action == "youtube.set_visibility"
        return _abstaining_prediction()

    async def fake_think(need, system, messages, **kw):
        calls["think"] += 1
        assert "route-deal shorts won twice" in system + messages[0].content
        assert "1,000" in system + messages[0].content     # the baseline number
        return _completion('{"hypothesis": "route-deal shorts beat explainers",'
                           ' "variant": "route-deal short",'
                           ' "draft_kind": "short_script",'
                           ' "draft_source": "weekly"}')

    monkeypatch.setattr(experiments.playbook, "read", fake_playbook)
    monkeypatch.setattr(experiments, "baseline_for", fake_baseline)
    monkeypatch.setattr(experiments.policy, "predict", fake_predict)
    monkeypatch.setattr(experiments, "think", fake_think)
    monkeypatch.setattr(experiments, "recent_posts",
                        _async_return([{"title": "a short", "views": 900}]))

    proposal, reason = await experiments.propose("flycheap", "youtube")
    assert reason is None
    assert proposal["variant"] == "route-deal short"
    assert proposal["metric"] == "views_48h"
    assert calls == {"think": 1, "playbook": 1, "baseline": 1, "predict": 1}


@pytest.mark.asyncio
async def test_propose_puts_no_probability_in_the_prompt_when_judgment_abstains(
        monkeypatch):
    seen: dict[str, str] = {}

    async def fake_think(need, system, messages, **kw):
        seen["text"] = system + messages[0].content
        return _completion('{"hypothesis": "h", "variant": "v",'
                           ' "draft_kind": "short_script", "draft_source": "weekly"}')

    monkeypatch.setattr(experiments.playbook, "read", _async_return([]))
    monkeypatch.setattr(experiments, "baseline_for",
                        _async_return((None, "no measured posts yet")))
    monkeypatch.setattr(experiments.policy, "predict",
                        _async_return(_abstaining_prediction()))
    monkeypatch.setattr(experiments, "think", fake_think)
    monkeypatch.setattr(experiments, "recent_posts", _async_return([]))

    proposal, reason = await experiments.propose("flycheap", "youtube")
    assert reason is None
    assert "%" not in seen["text"]
    assert "not enough decisions" in seen["text"] or "abstain" in seen["text"]
    # No baseline was measured, so the prompt says so rather than carrying one.
    assert "no measured posts yet" in seen["text"]
    assert proposal["baseline"] is None


# ------------------------------------------------------------- measurement

def _experiment_row(**over):
    row = {
        "id": 7, "business": "flycheap", "platform": "youtube",
        "hypothesis": "route-deal shorts beat explainer shorts on views/48h",
        "variant": "route-deal short", "draft_id": 3,
        "posted_ref": "vid123",
        "posted_at": datetime(2026, 9, 20, 9, 0, tzinfo=UTC),
        "measure_at": datetime(2026, 9, 22, 9, 0, tzinfo=UTC),
        "metric": "views_48h", "baseline": None, "observed": None,
        "outcome": None, "lesson": None,
    }
    row.update(over)
    return row


@pytest.mark.asyncio
async def test_a_failed_platform_read_is_unmeasured_with_the_reason(monkeypatch):
    recorded: list[dict] = []
    monkeypatch.setattr(experiments, "_due", _async_return([_experiment_row()]))
    monkeypatch.setattr(experiments, "read_metric",
                        _async_return((None, "youtube: 403 not permitted")))
    monkeypatch.setattr(experiments, "baseline_for",
                        _async_fail("baseline_for must not be reached"))
    record = _capture(recorded)

    results = await experiments.measure_due(record=record)
    assert len(results) == 1
    assert results[0]["outcome"] == "unmeasured"
    assert "403 not permitted" in results[0]["lesson"]
    assert recorded[0]["observed"] is None
    assert recorded[0]["baseline"] is None


@pytest.mark.asyncio
async def test_no_baseline_is_unmeasured_and_never_flat(monkeypatch):
    """The worst available bug: calling an unmeasurable experiment 'flat'
    invents a comparison nobody made."""
    recorded: list[dict] = []
    monkeypatch.setattr(experiments, "_due", _async_return([_experiment_row()]))
    monkeypatch.setattr(experiments, "read_metric",
                        _async_return((_reading(1640.0), None)))
    monkeypatch.setattr(experiments, "baseline_for",
                        _async_return((None, "only 2 measured posts in 4 weeks")))
    record = _capture(recorded)
    monkeypatch.setattr(experiments, "execute_sql", _async_return("OK"))

    results = await experiments.measure_due(record=record)
    assert results[0]["outcome"] == "unmeasured"
    assert results[0]["outcome"] != "flat"
    assert "only 2 measured posts in 4 weeks" in results[0]["lesson"]
    assert recorded[0]["baseline"] is None


@pytest.mark.parametrize(
    ("observed", "value", "sigma", "expected"),
    [
        (1640.0, 1000.0, 200.0, "won"),     # z = +3.2
        (1150.0, 1000.0, 200.0, "flat"),    # z = +0.75, inside one sigma
        (700.0, 1000.0, 200.0, "lost"),     # z = -1.5
        (1210.0, 1000.0, None, "won"),      # +21%, no sigma
        (1150.0, 1000.0, None, "flat"),     # +15%, no sigma
        (780.0, 1000.0, None, "lost"),      # -22%, no sigma
    ],
)
def test_the_outcome_thresholds(observed, value, sigma, expected):
    base = experiments.Baseline(value=value, sigma=sigma, n=8,
                                metric="views_48h",
                                observed_at=datetime.now(UTC),
                                window="4-week median")
    assert experiments.verdict(observed, base) == expected


@pytest.mark.asyncio
async def test_the_lesson_carries_the_measured_delta_and_the_time(monkeypatch):
    recorded: list[dict] = []
    read_at = datetime(2026, 9, 22, 9, 12, tzinfo=UTC)
    monkeypatch.setattr(experiments, "_due", _async_return([_experiment_row()]))
    monkeypatch.setattr(experiments, "read_metric",
                        _async_return((_reading(1640.0, at=read_at), None)))
    monkeypatch.setattr(experiments, "baseline_for", _async_return((
        experiments.Baseline(value=1000.0, sigma=None, n=8, metric="views_48h",
                             observed_at=read_at, window="4-week median"),
        None)))
    record = _capture(recorded)
    monkeypatch.setattr(experiments, "execute_sql", _async_return("OK"))

    results = await experiments.measure_due(record=record)
    lesson = results[0]["lesson"]
    assert lesson.startswith("flycheap · youtube · route-deal short: won")
    assert "+64%" in lesson
    assert "views_48h" in lesson
    assert "4-week median" in lesson
    assert "09:12" in lesson


@pytest.mark.asyncio
async def test_attach_sets_the_measure_window_48_hours_after_posting(
        memory_backend):
    posted = datetime(2026, 9, 20, 9, 0, tzinfo=UTC)
    await experiments.attach(7, "vid123", posted_at=posted)
    sql, args = memory_backend.sql_log[-1]
    assert "UPDATE marketing_experiments" in sql
    assert "vid123" in args
    assert posted + timedelta(hours=48) in args


@pytest.mark.asyncio
async def test_approving_a_publish_starts_the_experiments_clock(
        monkeypatch, memory_backend):
    """The seam that makes the loop a loop.

    Without this, `attach` is never called, `_due` never returns anything, no
    outcome is ever recorded and the playbook stays empty forever — 38 green
    tests over a feature that does nothing.
    """
    from atlas.modules import youtube

    monkeypatch.setattr(youtube.api, "current_privacy",
                        _async_return(("private", None)))
    monkeypatch.setattr(youtube.api, "set_privacy", _async_return(({}, None)))

    result = await youtube.set_visibility("flycheap", "vid123", "public",
                                          experiment_id=7)
    assert result.ok
    measure_at = result.evidence["measure_at"]
    assert measure_at is not None

    attached = [(sql, args) for sql, args in memory_backend.sql_log
                if "UPDATE marketing_experiments" in sql]
    assert attached, "going public did not start the experiment's clock"
    sql, args = attached[-1]
    experiment_id, posted_ref, posted_at, written_measure_at = args
    assert (experiment_id, posted_ref) == (7, "vid123")
    assert written_measure_at == posted_at + timedelta(hours=48)
    assert written_measure_at == measure_at

    # And the row is now exactly what `_due` looks for.
    assert "posted_ref" in sql and "measure_at" in sql


@pytest.mark.asyncio
async def test_a_publish_that_is_not_public_starts_no_clock(
        monkeypatch, memory_backend):
    """48 hours of views on an unlisted video is not a result."""
    from atlas.modules import youtube

    monkeypatch.setattr(youtube.api, "current_privacy",
                        _async_return(("private", None)))
    monkeypatch.setattr(youtube.api, "set_privacy", _async_return(({}, None)))

    result = await youtube.set_visibility("flycheap", "vid123", "unlisted",
                                          experiment_id=7)
    assert result.ok
    assert result.evidence["measure_at"] is None
    assert not [s for s, _ in memory_backend.sql_log
                if "UPDATE marketing_experiments" in s]


def test_the_publish_action_declares_the_experiment_parameter():
    """`executor._validate_params` refuses anything the action did not
    declare, so the hook is only reachable if the catalog names it."""
    from atlas.modules.youtube import MODULE

    publish = next(a for a in MODULE.actions()
                   if a.name == "youtube.set_visibility")
    assert "experiment_id" in publish.params


# ---------------------------------------------------------- the weekly plan

def _closed(**over):
    row = {"business": "flycheap", "platform": "youtube",
           "variant": "explainer short", "outcome": "lost"}
    row.update(over)
    return row


@pytest.mark.asyncio
async def test_the_weekly_plan_never_repeats_a_losing_variant(monkeypatch):
    history = [_closed(variant="explainer short", outcome="lost"),
               _closed(variant="route-deal short", outcome="won")]
    monkeypatch.setattr(cycle, "closed_experiments", _async_return(history))

    async def fake_propose(business, platform):
        return ({"business": business, "platform": platform,
                 "hypothesis": "h", "variant": "explainer short",
                 "draft_kind": "short_script", "draft_source": "weekly",
                 "metric": "views_48h", "baseline": None}, None)

    monkeypatch.setattr(cycle.experiments, "propose", fake_propose)
    monkeypatch.setattr(cycle.monetize, "ideas",
                        _async_return({"ideas": [], "note": "nothing measured"}))
    monkeypatch.setattr(cycle, "standing_rule_candidates", _async_return([]))

    plan = await cycle.weekly("flycheap")
    variants = {e["variant"] for e in plan["experiments"]}
    assert "explainer short" not in variants, \
        "a variant that already lost was proposed again"


@pytest.mark.asyncio
async def test_the_weekly_plan_repeats_a_winner_at_most_once_per_platform(
        monkeypatch):
    monkeypatch.setattr(cycle, "closed_experiments", _async_return(
        [_closed(variant="route-deal short", outcome="won")]))

    async def fake_propose(business, platform):
        return ({"business": business, "platform": platform,
                 "hypothesis": "h", "variant": "route-deal short",
                 "draft_kind": "short_script", "draft_source": "weekly",
                 "metric": "views_48h", "baseline": None}, None)

    monkeypatch.setattr(cycle.experiments, "propose", fake_propose)
    monkeypatch.setattr(cycle.monetize, "ideas",
                        _async_return({"ideas": [], "note": ""}))
    monkeypatch.setattr(cycle, "standing_rule_candidates", _async_return([]))

    plan = await cycle.weekly("flycheap")
    per_platform: dict[str, int] = {}
    for e in plan["experiments"]:
        if e["variant"] == "route-deal short":
            per_platform[e["platform"]] = per_platform.get(e["platform"], 0) + 1
    assert all(n <= 1 for n in per_platform.values())
    assert len(plan["experiments"]) <= 3


@pytest.mark.asyncio
async def test_the_plan_drafts_a_standing_rule_and_never_enacts_one(monkeypatch):
    pred = _confident_prediction()
    monkeypatch.setattr(cycle.policy, "predict", _async_return(pred))
    text = await cycle.standing_rule_candidates()
    assert text, "no standing-rule candidate was drafted at p >= 0.95"
    assert "youtube.set_visibility" in text[0]["text"]
    assert text[0]["enacted"] is False
    assert "only you can" in text[0]["note"].lower()


# ---------------------------------------------------------- monetization

@pytest.mark.asyncio
async def test_no_measured_fact_means_no_ideas_and_a_plain_note(monkeypatch):
    monkeypatch.setattr(monetize, "_youtube_facts",
                        _async_return((None, "no YOUTUBE_FLYCHEAP_CHANNEL_ID set")))
    monkeypatch.setattr(monetize, "_flycheap_facts",
                        _async_return((None, "flycheap /api/health unreachable")))
    monkeypatch.setattr(monetize, "_guardian_facts",
                        _async_return((None, "no GUARDIAN_API_TOKEN set")))
    monkeypatch.setattr(monetize, "_mirotech_facts",
                        _async_return((None, "no eBay token")))
    monkeypatch.setattr(monetize, "_agency_facts",
                        _async_return((None, "agency_leads unreadable")))

    result = await monetize.ideas("flycheap")
    assert result["ideas"] == []
    assert "no measured facts" in result["note"]
    assert "flycheap /api/health unreachable" in result["note"]


@pytest.mark.asyncio
async def test_every_idea_carries_evidence_with_an_observed_at(monkeypatch):
    read_at = datetime(2026, 9, 24, 9, 12, tzinfo=UTC)
    monkeypatch.setattr(monetize, "_youtube_facts", _async_return((
        {"channel": "flycheap", "subscribers": 1240,
         "eligible_on_subscribers": True, "observed_at": read_at,
         "detail": "eligible: 1,240 of 1,000 subscribers, read 09:12"}, None)))
    monkeypatch.setattr(monetize, "_flycheap_facts",
                        _async_return((None, "nothing measures bookings")))
    monkeypatch.setattr(monetize, "_guardian_facts", _async_return((None, "x")))
    monkeypatch.setattr(monetize, "_mirotech_facts", _async_return((None, "x")))
    monkeypatch.setattr(monetize, "_agency_facts", _async_return((None, "x")))

    result = await monetize.ideas("flycheap")
    assert result["ideas"]
    for idea in result["ideas"]:
        assert idea["requires"]
        assert idea["est_note"]
        assert idea["evidence"]
        for fact in idea["evidence"].values():
            assert fact.get("observed_at"), f"{idea['idea']} has a fact with no time"


@pytest.mark.asyncio
async def test_anything_that_costs_money_is_listed_not_actioned(monkeypatch):
    for name in ("_youtube_facts", "_guardian_facts", "_mirotech_facts",
                 "_agency_facts"):
        monkeypatch.setattr(monetize, name, _async_return((None, "unmeasured")))
    monkeypatch.setattr(monetize, "_flycheap_facts", _async_return((
        {"users": 412, "active_routes": 88,
         "observed_at": datetime(2026, 9, 24, 9, 0, tzinfo=UTC),
         "bookings_measured": False,
         "detail": "412 users, 88 active routes, read 09:00"}, None)))

    result = await monetize.ideas("flycheap")
    spends = [i for i in result["ideas"] if i.get("costs_money")]
    for idea in spends:
        assert idea["requires"] == "money out — Tier 3, you decide"
        assert idea["mechanism"]
    # And no idea is ever emitted as something ATLAS could run.
    assert all("action" not in idea for idea in result["ideas"])


@pytest.mark.asyncio
async def test_mirotech_never_claims_a_sold_price_it_did_not_measure(monkeypatch):
    for name in ("_youtube_facts", "_guardian_facts", "_flycheap_facts",
                 "_agency_facts"):
        monkeypatch.setattr(monetize, name, _async_return((None, "unmeasured")))
    monkeypatch.setattr(monetize, "_mirotech_facts", _async_return((
        {"listings": 31, "sold_prices_measured": False,
         "observed_at": datetime(2026, 9, 24, 8, 0, tzinfo=UTC),
         "detail": "31 active listings, read 08:00"}, None)))

    result = await monetize.ideas("mirotech")
    assert result["ideas"]
    joined = " ".join(i["est_note"] for i in result["ideas"])
    assert "sold" in joined.lower()
    assert "not measured" in joined.lower() or "active asks" in joined.lower()


# ------------------------------------------------------------- the wiring

def test_the_scheduler_runs_the_marketing_step():
    import inspect

    from atlas.kernel import scheduler
    assert hasattr(scheduler, "_maybe_marketing")
    source = inspect.getsource(scheduler.loop)
    assert '("marketing", _maybe_marketing)' in source


def test_a_plan_is_filed_through_the_executor_and_nowhere_else():
    """The planner returns requests; only `executor.execute` runs them. A
    cycle that called the action functions directly would be the one corner of
    ATLAS where ATLAS_WATCH_ONLY, the audit line and the hourly ceiling do not
    apply."""
    import inspect

    from atlas.kernel import scheduler
    source = inspect.getsource(scheduler._maybe_marketing)
    assert "execute(ExecutionRequest(" in source
    # And no file under kernel/marketing reaches the action functions itself
    # -- not even measure_due, which closes experiments through the recorder
    # the scheduler hands it, and that recorder goes through the executor.
    for f in _marketing_files():
        hits = f.read_text().count("modules.marketing")
        assert hits == 0, \
            (f"{f.name} has {hits} reference(s) to the action module; "
             f"planning files hand requests to the executor instead")
    measure = inspect.getsource(scheduler._record_outcome)
    assert "execute(ExecutionRequest(" in measure
    assert '"marketing.record_outcome"' in measure
    assert "measure_due(record=_record_outcome)" in inspect.getsource(
        scheduler._maybe_marketing)


def test_the_request_parameters_are_exactly_what_the_actions_declare():
    """`executor._validate_params` refuses an undeclared parameter, so a plan
    whose keys drifted from the catalog would be silently dropped at 07:00 on
    a Sunday."""
    from atlas.modules.marketing import MODULE

    declared = {a.name: set(a.params) for a in MODULE.actions()}
    plan = {
        "experiments": [{
            "business": "flycheap", "platform": "youtube", "hypothesis": "h",
            "variant": "v", "draft_kind": "short_script",
            "draft_source": "weekly", "metric": "views_48h", "baseline": 1.0,
        }],
        "ideas": [{
            "business": "flycheap", "idea": "i", "mechanism": "affiliate",
            "evidence": {"users": {"observed_at": "2026-09-24T09:00:00+00:00"}},
            "requires": "r", "est_note": "e", "score": 0.5,
        }],
    }
    requests = cycle.as_requests(plan)
    assert len(requests) == 2
    for action, params, reason in requests:
        assert action in declared, action
        assert not set(params) - declared[action], \
            f"{action} would be refused: {sorted(set(params) - declared[action])}"
        assert reason


def test_the_marketing_day_defaults_to_sunday(monkeypatch):
    from atlas.kernel import scheduler
    monkeypatch.delenv("MARKETING_DAY", raising=False)
    assert scheduler._marketing_day() == 6
    monkeypatch.setenv("MARKETING_DAY", "not a number")
    assert scheduler._marketing_day() == 6


def test_the_marketing_agent_can_only_call_registered_non_tier_three_tools():
    from atlas.kernel.agents import ROSTER, build_roster
    from atlas.kernel.permissions import denial_reason
    from atlas.kernel.tools import TOOLS
    from atlas.tools.builtin import register_all

    register_all()
    build_roster()
    agent = ROSTER["marketing"]
    missing = agent.tools - set(TOOLS)
    assert not missing, f"marketing names unregistered tool(s): {sorted(missing)}"
    for name in sorted(agent.tools):
        action = TOOLS[name].action
        if action:
            why = denial_reason(action)
            assert why is None, f"{name} -> {action} is Tier 3: {why}"


def test_the_marketing_agent_owns_the_playbook_scope():
    from atlas.kernel.agents import ROSTER, build_roster
    build_roster()
    agent = ROSTER["marketing"]
    assert "playbook" in agent.memory_scope
    assert "experiments" in agent.memory_scope
    assert "growth" in ROSTER, "marketing replaces nothing; growth stays"


def test_the_new_read_and_write_tools_are_registered():
    from atlas.kernel.tools import TOOLS
    from atlas.tools.builtin import register_all
    register_all()
    for name in ("playbook_read", "experiments_list", "monetization_list"):
        assert name in TOOLS and TOOLS[name].action is None
    assert TOOLS["experiment_propose"].action == "marketing.propose_experiment"
    assert TOOLS["monetization_propose"].action == "marketing.propose_idea"


@pytest.mark.asyncio
async def test_the_brief_reports_the_overnight_outcome_and_the_top_idea(
        monkeypatch):
    from atlas.kernel import brief as brief_module

    async def fake_fetch(sql, *args):
        flat = " ".join(sql.split())
        if "FROM marketing_experiments" in flat and "outcome IS NULL" in flat:
            return [{"business": "flycheap", "platform": "youtube",
                     "variant": "route-deal short", "hypothesis": "h",
                     "at": datetime(2026, 9, 24, 8, 0, tzinfo=UTC)}]
        if "FROM marketing_experiments" in flat:
            return [{"business": "flycheap", "platform": "youtube",
                     "variant": "route-deal short", "outcome": "won",
                     "lesson": "flycheap · youtube · route-deal short: won "
                               "(+64% views_48h vs 4-week median, measured 09:12)",
                     "measured_at": datetime(2026, 9, 24, 9, 12, tzinfo=UTC)}]
        if "FROM monetization_ideas" in flat:
            return [{"idea": "YouTube Partner Program", "business": "flycheap",
                     "requires": "enable it in YouTube Studio", "score": 0.9,
                     "est_note": "eligible on subscribers, read 09:12"}]
        return []

    monkeypatch.setattr(brief_module, "fetch_all", fake_fetch)
    lines = await brief_module._marketing(
        datetime(2026, 9, 23, 7, 0, tzinfo=UTC))
    joined = "\n".join(lines)
    assert "won" in joined
    assert "+64%" in joined
    assert "YouTube Partner Program" in joined
    assert "enable it in YouTube Studio" in joined


@pytest.mark.asyncio
async def test_an_overdue_unmeasured_experiment_is_degraded_not_ok(monkeypatch):
    from atlas.kernel.contracts import Health
    from atlas.modules import marketing as module

    monkeypatch.setattr(module, "fetch_all", _async_return(
        [{"outcome": None, "lesson": None, "measured_at": None}] * 3))
    obs = await module.check_experiments_overdue()
    assert obs.health is Health.DEGRADED
    assert "3" in obs.detail


@pytest.mark.asyncio
async def test_a_check_that_cannot_run_reports_unknown(monkeypatch):
    from atlas.kernel.contracts import Health
    from atlas.modules import marketing as module

    monkeypatch.setattr(module, "fetch_one", _async_fail("no database"))
    monkeypatch.setattr(module, "fetch_all", _async_fail("no database"))
    for check in (module.check_experiments_overdue, module.check_playbook_size):
        obs = await check()
        assert obs.health is Health.UNKNOWN
        assert obs.detail


# ------------------------------------------------------- the module actions

@pytest.mark.asyncio
async def test_recording_an_outcome_writes_the_lesson_to_the_playbook(
        monkeypatch, memory_backend):
    from atlas.modules import marketing as module

    written: list[tuple] = []

    async def fake_remember(kind, namespace, content, **kw):
        written.append((str(kind), namespace, content, kw.get("meta")))
        return 42

    monkeypatch.setattr(module, "remember", fake_remember)
    monkeypatch.setattr(module, "execute_sql", _async_return("OK"))
    memory_backend.responses["SELECT business, platform, variant"] = {
        "business": "flycheap", "platform": "youtube",
        "variant": "route-deal short"}

    result = await module.record_outcome(
        7, "won", "flycheap · youtube · route-deal short: won (+64% ...)",
        observed=1640.0, baseline=1000.0, metric="views_48h")
    assert result.ok
    assert written[0][1] == "playbook"
    assert "route-deal short" in written[0][2]
    assert result.rollback_token == "7:42"


@pytest.mark.asyncio
async def test_rolling_back_an_outcome_reopens_the_experiment(monkeypatch):
    from atlas.modules import marketing as module

    statements: list[str] = []

    async def fake_execute(sql, *args):
        statements.append(" ".join(sql.split()))
        return "OK"

    monkeypatch.setattr(module, "execute_sql", fake_execute)
    result = await module.record_outcome_rollback("7:42")
    assert result.ok
    joined = " ".join(statements)
    assert "memories" in joined
    assert "outcome = NULL" in joined


@pytest.mark.asyncio
async def test_proposing_an_experiment_writes_a_row_and_a_draft(monkeypatch):
    from atlas.kernel.contracts import ActionResult
    from atlas.modules import marketing as module

    async def fake_fetch_one(sql, *args):
        return {"id": 11}

    async def fake_draft(kind, source):
        return ActionResult(True, "drafted", evidence={"id": 5},
                            rollback_token="5")

    monkeypatch.setattr(module, "fetch_one", fake_fetch_one)
    monkeypatch.setattr(module, "execute_sql", _async_return("OK"))
    monkeypatch.setattr(module, "content_draft", fake_draft)

    result = await module.propose_experiment(
        "flycheap", "youtube", "route-deal shorts beat explainers",
        "route-deal short", draft_kind="short_script", draft_source="weekly")
    assert result.ok
    assert result.rollback_token == "11:5"


@pytest.mark.asyncio
async def test_an_experiment_proposal_refuses_an_unknown_platform(monkeypatch):
    from atlas.modules import marketing as module
    monkeypatch.setattr(module, "fetch_one",
                        _async_fail("must not reach the database"))
    result = await module.propose_experiment(
        "flycheap", "myspace", "h", "v", draft_kind="short_script",
        draft_source="weekly")
    assert not result.ok
    assert "myspace" in result.detail


# ----------------------------------------------------- review fixes (task 30)

@pytest.mark.asyncio
async def test_a_lesson_written_by_the_action_is_readable_by_the_playbook(
        monkeypatch):
    """The round trip, end to end, because the two halves disagreed.

    `record_outcome` wrote meta without `business`, and `playbook.read`
    filters on `business` — so every lesson written in production was
    invisible to every prompt that followed. Green unit tests on each half,
    an empty playbook forever.
    """
    from atlas.kernel.memory import Kind, Memory
    from atlas.modules import marketing as module

    rows: list[Memory] = []

    async def fake_remember(kind, namespace, content, **kw):
        rows.append(Memory(id=len(rows) + 1, kind=Kind(str(kind)),
                           namespace=namespace, content=content,
                           meta=kw.get("meta") or {},
                           importance=kw.get("importance", 0.5),
                           at=datetime.now(UTC)))
        return len(rows)

    async def fake_fetch_one(sql, *args):
        return {"business": "flycheap", "platform": "youtube",
                "variant": "route-deal short"}

    monkeypatch.setattr(module, "remember", fake_remember)
    monkeypatch.setattr(module, "execute_sql", _async_return("OK"))
    monkeypatch.setattr(module, "fetch_one", fake_fetch_one)

    result = await module.record_outcome(
        7, "won", "flycheap · youtube · route-deal short: won (+64% ...)",
        observed=1640.0, baseline=1000.0, metric="views_48h")
    assert result.ok

    async def fake_recall(query, namespaces, **kw):
        return list(rows)

    monkeypatch.setattr(playbook, "recall", fake_recall)
    lessons = await playbook.read("flycheap")
    assert lessons, "the lesson the action just wrote is invisible to the reader"
    assert lessons[0].platform == "youtube"
    assert lessons[0].variant == "route-deal short"
    assert lessons[0].outcome == "won"
    # And it does not leak into another business's playbook.
    assert await playbook.read("guardian") == []


@pytest.mark.asyncio
async def test_an_unmeasured_outcome_reaches_the_playbook_at_low_weight(
        monkeypatch):
    """The gap has to be visible. A run of unmeasured experiments is the
    signal that the loop stopped working, and hiding it from the playbook
    hides it from every prompt."""
    from atlas.modules import marketing as module

    written: list[dict] = []

    async def fake_remember(kind, namespace, content, **kw):
        written.append({"content": content, "meta": kw.get("meta"),
                        "importance": kw.get("importance")})
        return 1

    monkeypatch.setattr(module, "remember", fake_remember)
    monkeypatch.setattr(module, "execute_sql", _async_return("OK"))
    monkeypatch.setattr(module, "fetch_one", _async_return(
        {"business": "flycheap", "platform": "youtube", "variant": "v"}))

    await module.record_outcome(7, "unmeasured",
                                "flycheap · youtube · v: unmeasured (403)")
    assert written, "an unmeasured outcome left no trace in the playbook"
    assert written[0]["importance"] < 0.5
    assert written[0]["meta"]["outcome"] == "unmeasured"


@pytest.mark.asyncio
async def test_the_youtube_baseline_is_a_median_of_48_hour_readings(
        monkeypatch):
    """Not of lifetime views. A lifetime counter refreshed every six hours
    and a 48-hour analytics count are different measurements, and comparing
    them is the same class of error as calling an unmeasured run flat."""
    seen: dict[str, str] = {}

    async def fake_fetch_all(sql, *args):
        seen["sql"] = " ".join(sql.split())
        return [{"value": v} for v in (900, 1000, 1100, 1200, 1300)]

    monkeypatch.setattr(experiments, "fetch_all", fake_fetch_all)
    base, reason = await experiments.baseline_for(
        "youtube", "views_48h", business="flycheap")
    assert reason is None
    assert "views_48h" in seen["sql"]
    assert "views_48h IS NOT NULL" in seen["sql"]
    assert base.value == 1100
    assert base.metric == "views_48h"


@pytest.mark.asyncio
async def test_too_few_comparable_readings_names_how_many(monkeypatch):
    monkeypatch.setattr(experiments, "fetch_all",
                        _async_return([{"value": 1.0}, {"value": 2.0}]))
    base, reason = await experiments.baseline_for(
        "youtube", "views_48h", business="flycheap")
    assert base is None
    assert "2 of 5 comparable readings" in reason


@pytest.mark.asyncio
async def test_a_baseline_excludes_the_experiments_own_post(monkeypatch):
    captured: dict = {}

    async def fake_fetch_all(sql, *args):
        captured["sql"] = " ".join(sql.split())
        captured["args"] = args
        return [{"value": float(v)} for v in range(5)]

    monkeypatch.setattr(experiments, "fetch_all", fake_fetch_all)
    await experiments.baseline_for("youtube", "views_48h", business="flycheap",
                                   exclude_ref="vid123")
    assert "video_id <> " in captured["sql"]
    assert "vid123" in captured["args"]


@pytest.mark.asyncio
async def test_a_non_youtube_baseline_never_mixes_checks(monkeypatch):
    """`observations` holds every check on the property. A median across a
    token check and an engagement check is a number about nothing."""
    captured: dict = {}

    async def fake_fetch_all(sql, *args):
        captured["sql"] = " ".join(sql.split())
        captured["args"] = args
        return [{"value": float(v)} for v in range(5)]

    monkeypatch.setattr(experiments, "fetch_all", fake_fetch_all)
    base, reason = await experiments.baseline_for("instagram", "reach_48h")
    assert reason is None
    assert "c.name = $" in captured["sql"]
    assert "engagement" in captured["args"]
    # What was computed is the engagement check's account reach per day, not
    # a per-post reach_48h, and the Baseline says so.
    assert base.metric != "reach_48h"
    assert "engagement" in base.metric


@pytest.mark.asyncio
async def test_a_reading_is_never_scored_against_a_different_measurement(
        monkeypatch):
    """A per-post reach against an account-level median is not a verdict."""
    recorded: list[dict] = []
    monkeypatch.setattr(experiments, "_due", _async_return(
        [_experiment_row(platform="instagram", metric="reach_48h")]))
    monkeypatch.setattr(experiments, "read_metric", _async_return((
        experiments.Reading(value=900.0, metric="reach_48h",
                            observed_at=datetime.now(UTC),
                            source="instagram.insights"), None)))
    monkeypatch.setattr(experiments, "baseline_for", _async_return((
        experiments.Baseline(value=100.0, sigma=None, n=9,
                             metric="instagram.engagement (account reach/day)",
                             observed_at=datetime.now(UTC)), None)))
    record = _capture(recorded)
    monkeypatch.setattr(experiments, "execute_sql", _async_return("OK"))

    results = await experiments.measure_due(record=record)
    assert results[0]["outcome"] == "unmeasured"
    assert "not comparable" in results[0]["lesson"]
    assert recorded[0]["baseline"] is None


@pytest.mark.asyncio
async def test_a_platform_with_no_named_check_is_unmeasured_not_guessed(
        monkeypatch):
    monkeypatch.setattr(experiments, "fetch_all",
                        _async_fail("must not query"))
    base, reason = await experiments.baseline_for("linkedin", "impressions")
    assert base is None
    assert "linkedin" in reason


@pytest.mark.asyncio
async def test_measure_due_records_the_48_hour_reading_for_next_time(
        monkeypatch):
    """Each measurement feeds the next baseline. Without this write the
    4-week median would never have five comparable readings in it."""
    statements: list[tuple[str, tuple]] = []

    async def fake_execute(sql, *args):
        statements.append((" ".join(sql.split()), args))
        return "OK"

    monkeypatch.setattr(experiments, "_due", _async_return([_experiment_row()]))
    monkeypatch.setattr(experiments, "read_metric",
                        _async_return((_reading(1640.0), None)))
    monkeypatch.setattr(experiments, "baseline_for", _async_return((
        experiments.Baseline(value=1000.0, sigma=None, n=5, metric="views_48h",
                             observed_at=datetime.now(UTC),
                             window="4-week median"), None)))
    record = _capture([])
    monkeypatch.setattr(experiments, "execute_sql", fake_execute)

    await experiments.measure_due(record=record)
    written = [s for s, _ in statements if "views_48h" in s]
    assert written, "the 48-hour reading was measured and then thrown away"
    assert any("vid123" in args for _, args in statements)


@pytest.mark.asyncio
async def test_measure_due_keeps_the_experiments_own_video_out_of_its_baseline(
        monkeypatch):
    seen: dict = {}

    async def fake_baseline(platform, metric, **kw):
        seen.update(kw)
        return None, "baseline has 0 of 5 comparable readings"

    monkeypatch.setattr(experiments, "_due", _async_return([_experiment_row()]))
    monkeypatch.setattr(experiments, "read_metric",
                        _async_return((_reading(1640.0), None)))
    monkeypatch.setattr(experiments, "baseline_for", fake_baseline)
    record = _capture([])
    monkeypatch.setattr(experiments, "execute_sql", _async_return("OK"))

    results = await experiments.measure_due(record=record)
    assert seen["exclude_ref"] == "vid123"
    assert results[0]["outcome"] == "unmeasured"
    assert "0 of 5 comparable readings" in results[0]["lesson"]


@pytest.mark.asyncio
async def test_an_unmeasured_closure_keeps_the_check_degraded(monkeypatch):
    """A row closed `unmeasured` is not a measured row. Counting only
    `outcome IS NULL` made a permanently broken loop read OK."""
    from atlas.kernel.contracts import Health
    from atlas.modules import marketing as module

    captured: dict = {}
    recent = datetime.now(UTC) - timedelta(days=1)

    async def fake_fetch_all(sql, *args):
        captured["sql"] = " ".join(sql.split())
        return ([{"outcome": None, "lesson": None, "measured_at": None}]
                + [{"outcome": "unmeasured", "measured_at": recent,
                    "lesson": "flycheap · youtube · v: unmeasured (403)"}] * 3)

    monkeypatch.setattr(module, "fetch_all", fake_fetch_all)
    obs = await module.check_experiments_overdue()
    assert obs.health is Health.DEGRADED
    assert "unmeasured" in captured["sql"]
    assert "3" in obs.detail and "unmeasured" in obs.detail


@pytest.mark.asyncio
async def test_an_unmeasured_experiment_stops_counting_after_seven_days(
        monkeypatch):
    """Ruling, fix round 2: after 7 days an unmeasured experiment is history,
    not a live degradation, so the check can clear once the cause is fixed."""
    from atlas.kernel.contracts import Health
    from atlas.modules import marketing as module

    now = datetime.now(UTC)
    old = {"outcome": "unmeasured", "measured_at": now - timedelta(days=8),
           "lesson": "flycheap · youtube · a: unmeasured (old reason)"}
    new = {"outcome": "unmeasured",
           "measured_at": now - timedelta(days=2),
           "lesson": "flycheap · youtube · b: unmeasured (analytics 403)"}
    captured: dict = {}

    async def fake_fetch_all(sql, *args):
        captured["args"] = args
        return [old, new]

    monkeypatch.setattr(module, "fetch_all", fake_fetch_all)
    obs = await module.check_experiments_overdue()
    assert obs.health is Health.DEGRADED
    assert obs.value == 1.0                     # the 8-day-old row is history
    assert "analytics 403" in obs.detail
    assert f"read {new['measured_at']:%H:%M}" in obs.detail
    assert "old reason" not in obs.detail
    # The SQL bound is the same seven days.
    cutoff = [a for a in captured["args"] if isinstance(a, datetime)]
    assert any(abs((now - timedelta(days=7)) - c) < timedelta(minutes=1)
               for c in cutoff)

    async def only_old(sql, *args):
        return [old]

    monkeypatch.setattr(module, "fetch_all", only_old)
    obs = await module.check_experiments_overdue()
    assert obs.health is Health.OK


@pytest.mark.asyncio
@pytest.mark.parametrize("platform", ["instagram", "tiktok"])
async def test_an_experiment_on_a_platform_with_no_hook_is_refused(
        monkeypatch, platform):
    """Ruling, fix round 2: an experiment that cannot be attached can never
    close, whoever files it — the planner or the agent by hand."""
    from atlas.modules import marketing as module

    monkeypatch.setattr(module, "fetch_one",
                        _async_fail("must not reach the database"))
    for fn in (module.propose_experiment, module.propose_experiment_dry):
        result = await fn("flycheap", platform, "h", "v",
                          draft_kind="short_script", draft_source="weekly")
        assert not result.ok
        assert (f"{platform} has no experiment hook yet; only youtube can "
                f"close an experiment") in result.detail


@pytest.mark.asyncio
async def test_the_weekly_plan_only_uses_platforms_that_can_close(monkeypatch):
    """Filing an instagram experiment that can never be measured manufactures
    a queue of permanent `unmeasured` rows and a DEGRADED check nobody can
    clear."""
    asked: list[str] = []

    async def fake_propose(business, platform):
        asked.append(platform)
        return ({"business": business, "platform": platform,
                 "hypothesis": "h", "variant": f"{platform} variant",
                 "draft_kind": "short_script", "draft_source": "weekly",
                 "metric": "views_48h", "baseline": None}, None)

    monkeypatch.setattr(cycle, "closed_experiments", _async_return([]))
    monkeypatch.setattr(cycle.experiments, "propose", fake_propose)
    monkeypatch.setattr(cycle.monetize, "ideas",
                        _async_return({"ideas": [], "note": ""}))
    monkeypatch.setattr(cycle, "standing_rule_candidates", _async_return([]))

    plan = await cycle.weekly("flycheap")
    assert asked == ["youtube"]
    assert {e["platform"] for e in plan["experiments"]} == {"youtube"}
    # Measurement still knows about all three: an experiment attached before
    # the hooks existed must still be measurable.
    assert set(experiments.PLATFORMS) >= {"youtube", "instagram", "tiktok"}
    assert cycle.PLATFORMS_WITH_HOOKS == ("youtube",)


def test_the_hourly_ceiling_fits_a_whole_weekly_plan():
    """Four businesses x three experiments hit a ceiling of six halfway
    through, and the executor's refusal is silent unless someone counts."""
    from atlas.kernel.marketing.monetize import BUSINESSES
    from atlas.modules.marketing import MODULE

    by_name = {a.name: a for a in MODULE.actions()}
    most = len(BUSINESSES) * cycle.EXPERIMENTS_PER_WEEK
    assert by_name["marketing.propose_experiment"].max_per_hour >= most
    assert (by_name["marketing.propose_idea"].max_per_hour
            >= len(BUSINESSES) * cycle.IDEAS_PER_WEEK)


def test_a_refused_plan_item_is_a_warning_and_reaches_the_brief():
    import inspect

    from atlas.kernel import brief as brief_module
    from atlas.kernel import scheduler

    source = inspect.getsource(scheduler._maybe_marketing)
    assert "log.warning" in source
    assert "record_filing" in source
    assert "last_filing" in inspect.getsource(brief_module._marketing)


@pytest.mark.asyncio
async def test_the_brief_says_how_many_plan_items_were_not_filed(monkeypatch):
    from atlas.kernel import brief as brief_module
    from atlas.kernel.marketing import cycle as cycle_module

    monkeypatch.setattr(brief_module, "fetch_all", _async_return([]))
    cycle_module.record_filing(
        filed=4, refused=8, reasons=["rate limit"],
        at=datetime(2026, 9, 27, 7, 0, tzinfo=UTC))
    try:
        lines = await brief_module._marketing(
            datetime(2026, 9, 26, 7, 0, tzinfo=UTC))
    finally:
        cycle_module.record_filing(filed=0, refused=0, reasons=[], at=None)
    joined = "\n".join(lines)
    assert "8" in joined and "not filed" in joined
    assert "rate limit" in joined


def test_a_zero_baseline_is_never_a_win():
    base = experiments.Baseline(value=0.0, sigma=None, n=5, metric="views_48h",
                                observed_at=datetime.now(UTC),
                                window="4-week median")
    assert experiments.verdict(1640.0, base) == "flat"
    assert experiments.verdict(0.0, base) == "flat"


@pytest.mark.asyncio
async def test_a_source_that_does_not_apply_is_not_listed_as_unreadable(
        monkeypatch):
    """"guardian: not a guardian question" in a flycheap note is noise that
    buries the one line that matters."""
    monkeypatch.setattr(monetize, "_youtube_facts",
                        _async_return((None, "no CHANNEL_ID set")))
    monkeypatch.setattr(monetize, "_flycheap_facts",
                        _async_return((None, "flycheap /api/health unreachable")))
    for name in ("_guardian_facts", "_mirotech_facts", "_agency_facts"):
        monkeypatch.setattr(monetize, name,
                            _async_return((None, monetize.NOT_APPLICABLE)))

    result = await monetize.ideas("flycheap")
    assert result["ideas"] == []
    assert "no measured facts" in result["note"]
    assert "flycheap /api/health unreachable" in result["note"]
    for absent in ("guardian", "mirotech", "agency"):
        assert absent not in result["note"], \
            f"{absent} is not applicable here and should not be listed"


def test_a_missed_marketing_sunday_is_recovered_not_skipped(monkeypatch):
    """The docstring claimed Monday recovery and the weekday guard prevented
    it: on Monday `weekday() 0 < MARKETING_DAY 6` returned, and the ISO week
    had already rolled over, so a missed Sunday waited a full week. The
    window is now the MARKETING_DAY date under a run-once policy (RA-F2;
    the restart cases are in test_missed_runs.py)."""
    from datetime import timedelta

    from atlas.kernel import missed, scheduler

    monkeypatch.delenv("BRIEF_HOUR", raising=False)        # 07:00
    monkeypatch.delenv("MARKETING_DAY", raising=False)     # Sunday
    policy = scheduler.POLICIES["marketing"]
    assert policy.missed is missed.Missed.RUN_ONCE

    def due_after(now, filed: set[str]):
        cands = policy.windows(now, timedelta(weeks=2))
        run, _ = missed.plan(policy, cands,
                             {w.key for w in cands} - filed, now)
        return [w.key for w in run]

    assert due_after(datetime(2026, 9, 27, 8, 0, tzinfo=UTC), set()) == [
        "2026-09-27"]                                              # a Sunday
    assert due_after(datetime(2026, 9, 30, 8, 0, tzinfo=UTC),
                     {"2026-09-27"}) == []                         # filed
    # Down all of Sunday 4 October: it files on the Monday instead of waiting.
    assert due_after(datetime(2026, 10, 5, 8, 0, tzinfo=UTC),
                     {"2026-09-27"}) == ["2026-10-04"]


# ------------------------------------------------------------------ helpers

def _completion(text: str):
    from atlas.kernel.llm import Completion
    return Completion(text=text, provider="test", model="test")


def _lesson(content: str, *, business="flycheap", platform="youtube",
            variant="route-deal short", outcome="won", days_ago=1):
    return playbook.Lesson(
        content=content, business=business, platform=platform, variant=variant,
        outcome=outcome, at=datetime.now(UTC) - timedelta(days=days_ago),
        score=1.0)


def _reading(value: float, *, at: datetime | None = None):
    return experiments.Reading(
        value=value, metric="views_48h",
        observed_at=at or datetime(2026, 9, 22, 9, 12, tzinfo=UTC),
        source="youtube.analytics")


def _abstaining_prediction():
    from atlas.kernel.judgment.policy import Prediction
    return Prediction(None, "abstain", (), True, "not enough decisions yet",
                      3, True)


def _confident_prediction():
    from atlas.kernel.judgment.policy import Prediction
    return Prediction(0.97, "logistic", ("you approve shorts", "reversible"),
                      False, "", 40, True)


def _async_return(value):
    async def _fn(*args, **kwargs):
        return value
    return _fn


def _async_fail(message: str):
    async def _fn(*args, **kwargs):
        raise RuntimeError(message)
    return _fn


def _capture(sink: list):
    async def _fn(experiment_id, outcome, lesson, *, observed=None,
                  baseline=None, metric=None):
        sink.append({"experiment_id": experiment_id, "outcome": outcome,
                     "lesson": lesson, "observed": observed,
                     "baseline": baseline, "metric": metric})
        from atlas.kernel.contracts import ActionResult
        return ActionResult(True, "recorded")
    return _fn


# ------------------------------------ closing an experiment goes through the executor

@pytest.mark.asyncio
@pytest.mark.parametrize("watch_only", ["true", "false"])
async def test_the_scheduler_closes_an_experiment_only_through_the_executor(
        monkeypatch, memory_backend, watch_only):
    """`marketing.record_outcome` is a Tier 1 action. Reached from the daily
    measurement it gets the executor's audit line, ATLAS_WATCH_ONLY, the
    dry run and the hourly ceiling -- watch-only leaves the experiment open."""
    from atlas.kernel import executor, scheduler
    from atlas.kernel.contracts import Action, ActionResult, Tier
    from atlas.kernel.permissions import classify
    from atlas.kernel.registry import REGISTRY

    monkeypatch.setenv("ATLAS_WATCH_ONLY", watch_only)
    ran: list = []

    async def run(experiment_id, outcome, lesson, *, observed=None,
                  baseline=None, metric=None):
        ran.append(("run", experiment_id, outcome))
        return ActionResult(True, "closed", rollback_token="7:1")

    async def dry(experiment_id, outcome, lesson, *, observed=None,
                  baseline=None, metric=None):
        ran.append(("dry", experiment_id, outcome))
        return ActionResult(True, "would close")

    async def undo(_t):
        return ActionResult(True, "reopened")

    from atlas.modules.marketing import MODULE
    real = next(a for a in MODULE.actions() if a.name == "marketing.record_outcome")
    action = Action(name=real.name, tier=Tier.AUTO, summary="s", run=run,
                    dry_run=dry, rollback=undo, params=real.params)
    monkeypatch.setitem(REGISTRY.actions, action.name, action)
    monkeypatch.setitem(REGISTRY.decisions, action.name, classify(action))
    executor._recent.clear()

    monkeypatch.setattr(experiments, "_due", _async_return([_experiment_row()]))
    monkeypatch.setattr(experiments, "read_metric",
                        _async_return((None, "youtube: 403 not permitted")))
    await experiments.measure_due(record=scheduler._record_outcome)

    events = [r.event for r in memory_backend.audit_rows]
    if watch_only == "true":
        assert ran == [] and "execute.watch_only" in events
    else:
        assert ran == [("dry", 7, "unmeasured"), ("run", 7, "unmeasured")]
        assert "execute.finish" in events



async def test_an_experiment_on_a_switched_off_platform_is_refused(monkeypatch):
    """2026-09-27: youtube was off by Miro's word, and twelve YouTube
    experiments still drafted the weekly report as 'short scripts'."""
    from atlas.kernel.registry import REGISTRY
    from atlas.modules import marketing as module
    monkeypatch.delitem(REGISTRY.modules, "youtube")

    async def must_not_write(*a, **k):
        raise AssertionError("nothing may be written")

    monkeypatch.setattr(module, "fetch_one", must_not_write)
    monkeypatch.setattr(module, "content_draft", must_not_write)
    for fn in (module.propose_experiment, module.propose_experiment_dry):
        out = await fn("guardian", "youtube", "h", "v", draft_kind="short_script",
                       draft_source="weekly")
        assert not out.ok and "switched off" in out.detail
