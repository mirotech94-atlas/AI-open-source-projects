"""Skill acquisition for the client-sites lane: vetted text, Tier ASK, card
only, never code. A new template is a handoff to the coding agent, never a
file ATLAS writes."""
from __future__ import annotations

import pytest

from atlas.kernel import skills
from atlas.kernel.contracts import ActionResult, Tier
from atlas.modules.agency import AgencyModule, _store, skillbook

GOOD = {"name": "Booking page copy", "triggers": "booking page, salon booking",
        "body": "Say what happens after a booking in one line. Link only to "
                "https://example.com/guide when a source is needed."}


def _action(name: str):
    return next(a for a in AgencyModule().actions() if a.name == name)


@pytest.fixture(autouse=True)
def _clean_dynamic():
    yield
    for name in list(skills.dynamic_names()):
        skills.unload_dynamic(name)


@pytest.mark.parametrize("over, why", [
    ({"body": "x" * 1501}, "1500"),
    ({"body": "use <script>alert(1)</script>"}, "markup"),
    ({"body": "Hello {{ name }}"}, "templating"),
    ({"body": "see http://example.com"}, "https"),
    ({"body": "see javascript:alert(1)"}, "https"),
    ({"body": "see ftp://example.com/x"}, "https"),
    ({"body": ""}, "empty"),
    ({"triggers": "X"}, "trigger"),
    ({"triggers": "ok trigger, 9bad"}, "trigger"),
    ({"name": "Never guess a number"}, "built-in"),
    ({"name": "<b>x</b>"}, "name"),
])
def test_a_skill_is_text_only(over, why):
    problem = skillbook.skill_problem(**{**GOOD, **over})
    assert problem and why in problem, problem


def test_a_good_skill_passes():
    assert skillbook.skill_problem(**GOOD) is None


def test_the_actions_are_tier_ask_card_only():
    for name in ("agency.add_skill", "agency.request_template"):
        action = _action(name)
        assert action.tier is Tier.ASK and action.card_only and action.verbose_card
        assert action.dry_run is not None and action.rollback is not None
    assert set(_action("agency.add_skill").params) == {"name", "triggers", "body"}


async def test_approving_stores_active_and_loads_it(monkeypatch):
    stored: list = []

    async def activate(name, triggers, body):
        stored.append((name, triggers, body))
        return 3
    monkeypatch.setattr(_store, "activate_skill", activate)
    result = await skillbook.add_skill(**GOOD)
    assert result.ok and result.rollback_token == "Booking page copy"
    assert stored == [("Booking page copy", ["booking page", "salon booking"],
                       GOOD["body"])]
    hits = skills.skills_for("draft the salon booking page", "sites")
    assert [s.name for s in hits if s.name == "Booking page copy"]
    assert not [s for s in skills.skills_for("salon booking page", "qa")
                if s.name == "Booking page copy"]
    rendered = next(s for s in hits if s.name == "Booking page copy").render()
    assert rendered.startswith("### Booking page copy\n")
    assert skillbook.HEADER in rendered


async def test_a_refused_skill_is_not_stored(monkeypatch):
    async def never(*_a):
        raise AssertionError("must not store")
    monkeypatch.setattr(_store, "activate_skill", never)
    result = await skillbook.add_skill(**{**GOOD, "body": "<b>"})
    assert not result.ok


async def test_the_rollback_retires_and_unloads(monkeypatch):
    retired: list = []

    async def retire(name):
        retired.append(name)
        return True
    monkeypatch.setattr(_store, "retire_skill", retire)
    skills.load_dynamic([{"name": "Booking page copy",
                          "triggers": ["booking page"], "body": "text"}])
    result = await skillbook.add_skill_rollback("Booking page copy")
    assert result.ok and retired == ["Booking page copy"]
    assert "Booking page copy" not in skills.dynamic_names()


def test_load_dynamic_is_idempotent_and_skips_bad_rows():
    rows = [{"name": "Booking page copy", "triggers": ["booking page"], "body": "a"},
            {"name": "Never guess a number", "triggers": ["number"], "body": "b"},
            {"name": "Bad", "triggers": ["ok one"], "body": "<script>"}]
    assert skills.load_dynamic(rows, problem=skillbook.row_problem) == 1
    assert skills.load_dynamic(rows, problem=skillbook.row_problem) == 1
    assert [s.name for s in skills.SKILLS].count("Booking page copy") == 1
    assert [s.name for s in skills.SKILLS].count("Never guess a number") == 1


async def test_setup_loads_only_active_rows(monkeypatch, memory_backend):
    memory_backend.responses["FROM agency_skills WHERE status = 'active'"] = [
        {"name": "Booking page copy", "triggers": ["booking page"], "body": "a"}]
    monkeypatch.setenv("AGENCY_SITES", "x:x.example")
    await AgencyModule().setup()
    assert "Booking page copy" in skills.dynamic_names()
    sql = [s for s, _a in memory_backend.sql_log if "agency_skills" in s]
    assert sql and "status = 'active'" in sql[0]


def test_the_static_demo_skill_is_seeded():
    (skill,) = [s for s in skills.SKILLS if s.name == "Building a client demo"]
    assert set(skill.agents) == {"sites", "content"}
    for word in ("noindex", "Demo concept by Mapfront", "agency.deploy_demo"):
        assert word in skill.body
    assert skills.skills_for("build a client demo for a salon", "sites")


def test_the_sites_agent_reads_only():
    """Reads, plus one write into ATLAS's own database: sites_draft_doc
    (agency.draft_doc, Tier AUTO), which stores a document draft and raises
    its Tier ASK publish card (2026-10-29). Nothing it holds publishes."""
    from atlas.kernel.agents import ROSTER, build_roster
    from atlas.kernel.llm import Need
    if "sites" not in ROSTER:
        build_roster()
    agent = ROSTER["sites"]
    assert agent.need is Need.DRAFT
    assert agent.tools == {"module_status", "module_brief", "read_metrics", "sql_query",
                           "sites_draft_doc"}
    assert agent.handoff == {"content"}
    assert "agency.deploy_demo" in agent.system


# --------------------------------------------------------- template request

async def test_a_template_request_is_a_handoff_never_a_file(monkeypatch, tmp_path):
    drafted: list = []
    queued: list = []

    async def draft(**kw):
        drafted.append(kw)
        return 41

    async def queue(handoff_id="", topic=""):
        queued.append(handoff_id)
        return ActionResult(True, "queued", rollback_token=handoff_id)
    from atlas.modules import delegate
    monkeypatch.setattr(delegate, "draft", draft)
    monkeypatch.setattr(delegate, "queue", queue)
    cwd = tmp_path / "cwd"
    cwd.mkdir()
    monkeypatch.chdir(cwd)
    result = await skillbook.request_template(family="salon-booking",
                                              notes="a salon with a booking page")
    assert result.ok and result.rollback_token == "41" and queued == ["41"]
    brief = drafted[0]["brief"]
    assert "frontend-design" in brief and "GetLayers" in brief and "CD" in brief
    assert "salon-booking" in brief
    assert list(cwd.iterdir()) == []


@pytest.mark.parametrize("family", ["", "Salon", "../x", "a" * 40])
async def test_a_bad_template_family_is_refused(family):
    assert not (await skillbook.request_template(family=family, notes="x")).ok


async def test_the_template_rollback_cancels_the_handoff(monkeypatch):
    seen: list = []

    async def cancel(token):
        seen.append(token)
        return ActionResult(True, "cancelled")
    from atlas.modules import delegate
    monkeypatch.setattr(delegate, "cancel", cancel)
    assert (await skillbook.request_template_rollback("41")).ok and seen == ["41"]
