"""The Brain's departments (`kernel/departments.py`): the mapping and the SOPs.

Proved here, with no database:
  * every roster agent, every scheduler job (`scheduler.POLICIES`) and every
    action any module declares lands in exactly one department;
  * every name the file declares exists -- a department cannot hold an
    agent, job or module the code does not register (an invented seat);
  * each head is a roster agent that is a member of its department, with
    the handoffs / OWNERS entry its reason cites, or "you";
  * a job's SOP is generated from its policy and its actions' facts, and a
    Tier 2 action's SOP asks for sign-off while a Tier 1 action's does not;
  * the % is unknown with no executions, never 0 or 100.
"""
from __future__ import annotations

import importlib
import pkgutil

import pytest

import atlas.modules as modules_pkg
from atlas.kernel import agents as agent_module
from atlas.kernel import departments as dept
from atlas.kernel.brain import pct, tally_executions
from atlas.kernel.contracts import Action, ActionResult, Tier
from atlas.kernel.departments import DEPARTMENTS, JOBS, YOU, Layer
from atlas.kernel.scheduler import POLICIES


def _declared() -> tuple[dict[str, Action], set[str]]:
    """Every action any module declares, whatever the environment says, and
    every module name (the action namespace)."""
    actions: dict[str, Action] = {}
    names: set[str] = set()
    for info in pkgutil.iter_modules(modules_pkg.__path__):
        if info.name.startswith("_"):
            continue
        module = getattr(importlib.import_module(f"atlas.modules.{info.name}"),
                         "MODULE", None)
        if module is None:
            continue
        names.add(module.name)
        for action in module.actions():
            actions[action.name] = action
    return actions, names


@pytest.fixture(scope="module")
def world():
    mp = pytest.MonkeyPatch()
    # guardian.contain is declared only while its write token is set.
    mp.setenv("GUARDIAN_WRITE_TOKEN", "test-write-token-not-real")
    agent_module.build_roster()
    actions, modules = _declared()
    yield {"actions": actions, "modules": modules,
           "agents": dict(agent_module.ROSTER)}
    mp.undo()


def _count(kind: str) -> dict[str, int]:
    seen: dict[str, int] = {}
    for d in DEPARTMENTS:
        for n in getattr(d, kind):
            seen[n] = seen.get(n, 0) + 1
    return seen


# ------------------------------------------------------------ completeness

def test_every_agent_job_and_action_lands_in_exactly_one_department(world):
    assert len(world["agents"]) >= 19
    placed = dept.place(world["agents"], POLICIES, world["actions"])
    assert placed.unplaced == {}, placed.unplaced
    assert set(placed.agents) == set(world["agents"])
    assert set(placed.jobs) == set(POLICIES)
    assert set(placed.actions) == set(world["actions"])
    for kind in ("agents", "jobs", "modules"):
        twice = {n: k for n, k in _count(kind).items() if k > 1}
        assert not twice, f"{kind} in more than one department: {twice}"


def test_every_module_even_one_with_no_actions_has_a_department(world):
    assert {m for m in world["modules"] if dept.department_of_module(m) is None} == set()


def test_every_name_the_file_declares_exists(world):
    """Both directions: a name here that the code does not register would be
    an invented seat on the page."""
    assert set(_count("agents")) <= set(world["agents"])
    assert set(_count("jobs")) <= set(POLICIES)
    assert set(_count("modules")) <= world["modules"]


def test_every_job_is_described_and_names_only_real_actions(world):
    assert set(JOBS) == set(POLICIES)
    for job, spec in JOBS.items():
        assert spec.when and spec.reads and spec.decides, job
        assert isinstance(spec.layer, Layer)
        for a in spec.actions:
            assert a in world["actions"], f"{job} names {a}, which no module declares"


def test_each_head_is_a_member_with_the_evidence_it_cites(world):
    from atlas.kernel.missions import BY_AGENT
    keys = [d.key for d in DEPARTMENTS]
    assert len(keys) == len(set(keys))
    assert sum(1 for d in DEPARTMENTS if d.core) == 1
    for d in DEPARTMENTS:
        assert d.head_why, d.key
        if d.head == YOU:
            assert not d.handoffs_to_head and not d.head_owns_missions, d.key
            continue
        assert d.head in world["agents"], d.key
        assert d.head in d.agents, f"{d.key}: its head {d.head} is not a member"
        for who in d.handoffs_to_head:
            assert d.head in world["agents"][who].handoff, (d.key, who)
        if d.head_owns_missions:
            assert d.head in BY_AGENT, d.key
        for target in d.head_hands_off_to:
            assert target in world["agents"][d.head].handoff, (d.key, target)
        if d.head_has_tool:
            assert d.head_has_tool in world["agents"][d.head].tools, d.key
            holders = [n for n, a in world["agents"].items() if d.head_has_tool in a.tools]
            assert holders == [d.head], holders   # "the one agent that can"
        # Every head cites evidence the roster or OWNERS can check.
        assert (d.handoffs_to_head or d.head_owns_missions or d.head_hands_off_to
                or d.head_has_tool), d.key


def test_departments_from_the_reel_are_all_there():
    names = {d.key for d in DEPARTMENTS}
    assert names == {"knowledge", "money", "content", "sales", "ops", "admin",
                     "strategy"}
    assert dept.BY_KEY["money"].head == "markets"
    assert dept.BY_KEY["strategy"].head == YOU


def test_actions_and_pause_scopes_follow_their_namespace():
    assert dept.department_of_action("markets.paper_idea") == "money"
    assert dept.department_of_action("stripe.retry_invoice") == "money"
    assert dept.department_of_action("content.publish_reel") == "content"
    assert dept.department_of_action("nosuch.thing") is None
    assert dept.department_of_scope("instagram:clips") == "content"
    assert dept.department_of_scope("shadow:markets") == "money"
    assert dept.department_of_scope("all") is None


def test_layers_are_derived_for_agents_and_declared_for_jobs(world):
    assert dept.agent_layer(world["agents"]["concierge"].tools) is Layer.AGENTIC
    assert dept.agent_layer(world["agents"]["markets"].tools) is Layer.AGENT
    assert JOBS["checks"].layer is Layer.AUTOMATION
    assert JOBS["agency_meeting"].layer is Layer.AGENTIC
    assert {x["key"] for x in dept.LAYERS} == set(Layer)


# ------------------------------------------------------------------- SOPs

async def _ok(**_: object) -> ActionResult:
    return ActionResult(True, "ok")


async def _undo(_: str) -> ActionResult:
    return ActionResult(True, "undone")


def _facts(action: Action, tier: int, verifier: bool = False,
           home: str | None = None) -> dept.ActionFacts:
    return dept.action_facts(action.name, action, tier,
                             has_verifier=lambda n: verifier,
                             home_for=lambda t: home)


def test_a_jobs_sop_comes_from_its_policy_and_its_actions():
    card = Action(name="markets.paper_rebalance", tier=Tier.ASK,
                  summary="Rebalance the paper playlist", run=_ok, card_only=True)
    facts = {card.name: _facts(card, 2, verifier=True)}
    steps = dept.job_sop("playlist", POLICIES["playlist"], facts)
    kinds = [s["kind"] for s in steps]
    assert kinds == ["trigger", "reads", "decides", "acts", "signoff", "result"]
    trigger = steps[0]["text"]
    assert POLICIES["playlist"].missed.value in trigger
    assert JOBS["playlist"].when in trigger
    signoff = steps[4]
    assert [a["name"] for a in signoff["actions"]] == ["markets.paper_rebalance"]
    assert signoff["actions"][0]["tier"] == 2
    assert signoff["actions"][0]["needs_you"] is True
    assert "Card-only" in signoff["text"]
    assert "#markets" in steps[-1]["text"]
    assert "read back after it runs: markets.paper_rebalance" in steps[-1]["text"]


def test_an_interval_job_says_it_keeps_no_per_run_record():
    steps = dept.job_sop("checks", POLICIES["checks"], {})
    assert "no per-run record" in steps[0]["text"]
    assert any(s["kind"] == "acts" and "only reads" in s["text"] for s in steps)
    assert not any(s["kind"] == "signoff" for s in steps)


def test_a_tier_2_actions_sop_asks_for_sign_off_and_a_tier_1_does_not():
    ask = Action(name="linkedin.publish_post", tier=Tier.ASK, summary="Publish a post",
                 run=_ok, topic_param="page")
    auto = Action(name="markets.alert", tier=Tier.AUTO, summary="Send an alert",
                  run=_ok, dry_run=_ok, rollback=_undo)
    s2 = dept.action_sop(_facts(ask, 2, home="C1"), ["the ideas job"])
    assert [s["kind"] for s in s2] == ["trigger", "decides", "signoff", "result"]
    assert "Tier 2" in s2[2]["text"] and s2[2]["actions"][0]["needs_you"]
    assert "by page" in s2[-1]["text"] and "its own Slack channel" in s2[-1]["text"]
    assert "no read-back verifier" in s2[-1]["text"]
    s1 = dept.action_sop(_facts(auto, 1), [])
    assert "signoff" not in [s["kind"] for s in s1]
    assert "Tier 1" in s1[2]["text"]


def test_the_sop_tier_is_the_classified_one_not_the_declaration():
    """A Tier 1 declaration without a rollback is classified Tier 2; the SOP
    shows what the gate will do, the classified tier."""
    from atlas.kernel.permissions import classify
    bare = Action.__new__(Action)
    object.__setattr__(bare, "name", "markets.alert")
    for k, v in (("tier", Tier.AUTO), ("summary", "x"), ("run", _ok),
                 ("dry_run", _ok), ("rollback", None), ("irreversible", False)):
        object.__setattr__(bare, k, v)
    decision = classify(bare)
    f = dept.action_facts("markets.alert", bare, int(decision.tier),
                          has_verifier=lambda n: False, home_for=None)
    assert f.tier == 2


def test_an_agents_sop_lists_its_actions_and_what_needs_you():
    ask = Action(name="agency.send_outreach", tier=Tier.ASK, summary="Send", run=_ok)
    auto = Action(name="agency.audit", tier=Tier.AUTO, summary="Audit", run=_ok,
                  dry_run=_ok, rollback=_undo)
    facts = {ask.name: _facts(ask, 2), auto.name: _facts(auto, 1)}
    acts = dept.agent_actions(["agency_send_outreach", "web_search"], ("agency",),
                              {"agency_send_outreach": "agency.send_outreach",
                               "web_search": None},
                              facts, in_catalog=lambda n: True)
    assert [a.name for a in acts] == ["agency.audit", "agency.send_outreach"]
    steps = dept.agent_sop("agency", title="Agency", brief="Local business sites.",
                           need="reason", memory_scope=("agency",),
                           handoff={"growth"}, read_tools=["web_search"],
                           shift_hours=24.0, acts=acts)
    assert [s["kind"] for s in steps] == [
        "trigger", "reads", "decides", "acts", "signoff", "result"]
    assert "daily shift" in steps[0]["text"]
    assert "web_search" in steps[1]["text"]
    assert "growth" in steps[2]["text"]
    assert [a["name"] for a in steps[4]["actions"]] == ["agency.send_outreach"]


# -------------------------------------------------------------- the share

def test_no_executions_is_unknown_never_zero_or_a_hundred():
    assert pct(0, 0) is None
    assert pct(3, 1) == 75.0
    assert pct(0, 2) == 0.0
    assert pct(5, 0) == 100.0


def test_a_start_row_without_a_tier_is_left_out_of_both_sides():
    rows = [{"subject": "markets.alert", "payload": '{"data": {"tier": 1}}'},
            {"subject": "markets.alert", "payload": '{"data": {}}'},
            {"subject": "markets.paper_idea", "payload": '{"data": {"tier": 2}}'},
            {"subject": "markets.alert", "payload": "not json"}]
    t = tally_executions(rows)
    assert t["excluded"] == 2
    assert dict(t["per_action"]["markets.alert"]) == {"auto": 1}
    assert dict(t["per_action"]["markets.paper_idea"]) == {"approved": 1}
