"""Missions (kernel/missions.py): an order bigger than one action goes to the
agent that owns it.

the owner, 2026-09-29, by voice: "Hey Atlas, search up the 50 local businesses
that have a very poor website or no website at all and make them one." It
was answered as small talk. These pin that it is recognised as an order on
every surface, that it reaches the agency, that the agency's recipe only
runs names from its own closed catalog (Tier 1 through the executor, Tier 2
as cards), that a missing area is asked for rather than invented, that
watch-only and a pause hold it, and that the report counts what the
database says. No database, no model, no network: all faked here.
"""
from __future__ import annotations

import json
import re
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from atlas.api import voice
from atlas.kernel import intent, missions
from atlas.kernel.contracts import ActionResult, FailureClass, Tier
from atlas.kernel.registry import REGISTRY

OWNER = ("Hey Atlas, search up the 50 local businesses that have a very poor "
        "website or no website at all and make them one.")


# ------------------------------------------------------------ recognition

@pytest.mark.parametrize("said", [
    OWNER,
    "find me 20 plumbers in Astoria with no website",
    "get Mark to post the course",
    "have Sam find leads in Queens",
    "can you look up local businesses with bad websites in Queens",
    "go and find 10 bakeries with no website",
    "ask Felix to find leads in Queens",
])
def test_the_orders_are_recognised(said):
    assert voice.ACTION_INTENT.search(said), said
    assert voice.is_order(said), said


@pytest.mark.parametrize("said", [
    "how do I search for a plumber",
    "look up the capital of Peru",
    "find me a good pasta recipe",
    "what did you make of it",
    "how many clicks did flycheap get today",
    "open the guardian build report",
])
def test_a_question_or_a_lookup_nobody_owns_is_not_an_order(said):
    assert not voice.is_order(said), said


def test_the_owners_sentence_goes_to_the_agency_and_its_recipe():
    owner = missions.owner_for(OWNER)
    assert owner is not None and owner.agent == "sites"
    assert owner.recipe == missions.AGENCY_SITES
    assert missions.is_agency_job(OWNER)
    assert missions.is_agency_job("find me 20 plumbers in Astoria with no website")
    # One site for a client already in the conversation is one card, not a
    # search: the reels' sentence stays with the ordinary picker.
    assert not missions.is_agency_job(
        "check the email, build them a website and invoice them 3.5 grand")


@pytest.mark.parametrize("said,agent,job", [
    ("get Mark to post the course", "content", "post the course"),
    ("have Sam find leads in Queens", "sales", "find leads in Queens"),
    ("Hey Atlas, ask Felix to find leads in Queens", "sites", "find leads in Queens"),
    ("tell the seo agent to audit joes", "seo", "audit joes"),
])
def test_an_agent_named_is_the_agent_asked(said, agent, job):
    owner, rest = missions.addressed(said)
    assert owner.agent == agent and rest == job


def test_get_me_is_not_an_address():
    assert missions.addressed("get me the flycheap numbers") is None


# ------------------------------------------------------- the closed catalog

class _Action:
    def __init__(self, name: str, params: dict[str, str], required: tuple[str, ...]):
        self.name, self.params = name, params
        self.summary = f"{name} summary"
        args = ", ".join(required)
        scope: dict[str, Any] = {}
        exec(f"async def run({args}): pass", scope)      # noqa: S102
        self.run = scope["run"]


class _Decision:
    def __init__(self, tier: Tier) -> None:
        self.tier, self.allowed, self.reason = tier, True, ""


AGENCY = {
    "agency.prospect": (("query", "lat", "lng", "radius_m"), Tier.AUTO),
    "agency.verify": (("lead_id",), Tier.AUTO),
    "agency.audit": (("lead_id",), Tier.AUTO),
    "agency.rank": ((), Tier.AUTO),
    "agency.mockup": (("lead_id",), Tier.AUTO),
    "agency.build_site": (("lead_id",), Tier.AUTO),
    "agency.deploy_site": (("slug",), Tier.ASK),
}


@pytest.fixture
def catalog(monkeypatch):
    """The agency's actions, a Tier 3 one, and one from another module."""
    for name, (required, tier) in AGENCY.items():
        monkeypatch.setitem(REGISTRY.actions, name,
                            _Action(name, {p: p for p in required}, required))
        monkeypatch.setitem(REGISTRY.decisions, name, _Decision(tier))
    monkeypatch.setitem(REGISTRY.actions, "agency.buy_domain",
                        _Action("agency.buy_domain", {"domain": "d"}, ("domain",)))
    monkeypatch.setitem(REGISTRY.decisions, "agency.buy_domain", _Decision(Tier.NEVER))
    monkeypatch.setitem(REGISTRY.actions, "markets.alert",
                        _Action("markets.alert", {"key": "k"}, ("key",)))
    monkeypatch.setitem(REGISTRY.decisions, "markets.alert", _Decision(Tier.AUTO))
    return REGISTRY


AGENCY_OWNER = missions.BY_AGENT["sites"]


def test_the_owner_catalog_is_its_own_modules_and_never_tier_3(catalog):
    names = missions.owner_catalog(AGENCY_OWNER)
    assert "agency.prospect" in names and "agency.deploy_site" in names
    assert "agency.buy_domain" not in names          # Tier 3: never offered
    assert "markets.alert" not in names              # another agent's


def test_an_invented_step_is_refused(catalog):
    assert "not in the catalog" in missions.vet_step(
        AGENCY_OWNER, "agency.email_everyone", {})


def test_a_tier_3_step_is_refused(catalog):
    assert "permanently denied" in missions.vet_step(
        AGENCY_OWNER, "agency.buy_domain", {"domain": "x.com"})


def test_another_agents_step_is_refused(catalog):
    assert "not the sites agent's" in missions.vet_step(
        AGENCY_OWNER, "markets.alert", {"key": "k"})


def test_params_are_validated_both_ways(catalog):
    assert "needs lead_id" in missions.vet_step(AGENCY_OWNER, "agency.audit", {})
    assert "does not take" in missions.vet_step(
        AGENCY_OWNER, "agency.audit", {"lead_id": 1, "sql": "drop"})
    assert missions.vet_step(AGENCY_OWNER, "agency.audit", {"lead_id": 1}) is None


@pytest.mark.parametrize("picked", ["agency.email_everyone", "agency.buy_domain",
                                    "markets.alert"])
async def test_the_narrowed_picker_discards_any_name_outside_the_list(
        catalog, monkeypatch, picked):
    class Completion:
        text = json.dumps({"action": picked, "params": {}})

    async def think(*_a, **_kw):
        return Completion()

    monkeypatch.setattr("atlas.kernel.llm.think", think)
    got = await intent.choose("do it", only=missions.owner_catalog(AGENCY_OWNER))
    assert got is None


# ------------------------------------------------------------- one step

@pytest.fixture
def spy(monkeypatch):
    seen: dict[str, list] = {"execute": [], "propose": []}

    async def execute(req):
        seen["execute"].append(req)
        return ActionResult(True, "ran", evidence={"lead_ids": [1, 2]})

    async def propose(action, params, reason, **kw):
        seen["propose"].append((action, params, reason, kw))
        return 77

    async def audit(*_a, **_kw):
        return None

    monkeypatch.setattr("atlas.kernel.executor.execute", execute)
    monkeypatch.setattr("atlas.kernel.approvals.propose", propose)
    monkeypatch.setattr("atlas.kernel.audit.audit", audit)
    return seen


def _mission(**over) -> dict[str, Any]:
    m = {"id": 9, "agent": "agency", "recipe": missions.AGENCY_SITES,
         "request": OWNER, "channel": "voice", "who": "dashboard", "thread": "",
         "slots": {"n": 2, "business": None, "area": "Astoria"},
         "status": "running", "phase": "", "state": {}, "steps": [],
         "report": None, "deadline": datetime.now(UTC) + timedelta(days=1)}
    m.update(over)
    return m


async def test_a_tier_2_step_becomes_a_card_and_never_runs(catalog, spy):
    m = _mission()
    outcome, pid = await missions.run_step(m, AGENCY_OWNER, "agency.deploy_site",
                                           {"slug": "joes-plumbing"})
    assert outcome == "card" and pid == 77
    assert spy["execute"] == []
    assert spy["propose"][0][0] == "agency.deploy_site"
    assert m["steps"][-1]["proposal_id"] == 77


async def test_a_tier_1_step_goes_through_the_executor_unapproved(catalog, spy):
    m = _mission()
    outcome, _result = await missions.run_step(m, AGENCY_OWNER, "agency.audit",
                                               {"lead_id": 3})
    assert outcome == "ok" and spy["propose"] == []
    req = spy["execute"][0]
    assert req.action == "agency.audit" and req.params == {"lead_id": 3}
    # Nobody approved each step: the audit trail must not say otherwise.
    assert req.approved_by is None and "Mission #9" in req.reason


async def test_a_refused_step_reaches_neither_door(catalog, spy):
    m = _mission()
    outcome, why = await missions.run_step(m, AGENCY_OWNER, "agency.buy_domain",
                                           {"domain": "x.com"})
    assert outcome == "refused" and "permanently denied" in why
    assert spy["execute"] == [] and spy["propose"] == []


async def test_a_hold_from_the_executor_is_a_wait_not_a_failure(catalog, monkeypatch):
    from atlas.kernel.executor import HOLD

    async def held(req):
        return ActionResult(False, "watch-only", evidence={HOLD: "watch_only"},
                            failure_class=FailureClass.DISABLED, dispatched=False)

    monkeypatch.setattr("atlas.kernel.executor.execute", held)
    outcome, _ = await missions.run_step(_mission(), AGENCY_OWNER, "agency.rank", {})
    assert outcome == "held"


# ----------------------------------------------------------- slots and area

def _no_model(monkeypatch, answer: dict | None = None):
    async def model_slots(_request):
        return answer or {}
    monkeypatch.setattr(missions, "_model_slots", model_slots)


async def test_the_count_comes_from_his_digits_and_is_capped(monkeypatch):
    _no_model(monkeypatch)
    slots = await missions.fill_slots(OWNER)
    assert slots["n"] == 50 and slots["n_said"] and not slots["n_capped"]
    monkeypatch.setenv("AGENCY_MISSION_MAX_N", "20")
    slots = await missions.fill_slots(OWNER)
    assert slots["n"] == 20 and slots["n_capped"]


async def test_a_slot_he_did_not_say_is_dropped(monkeypatch):
    _no_model(monkeypatch, {"business": "dentists", "area": "Brooklyn"})
    slots = await missions.fill_slots("find me 20 plumbers in Astoria with no website")
    # The model's area and trade are not in his words: neither survives,
    # and the area he did say is read from the sentence.
    assert slots["business"] is None and slots["area"] == "Astoria"
    _no_model(monkeypatch, {"business": "plumbers", "area": "Astoria"})
    slots = await missions.fill_slots("find me 20 plumbers in Astoria with no website")
    assert slots["business"] == "plumbers" and slots["area"] == "Astoria"


def test_home_area_is_parsed_or_ignored(monkeypatch):
    monkeypatch.setenv("AGENCY_HOME_AREA", "40.7644,-73.9235,4000,Astoria, Queens")
    home = missions.home_area()
    assert (home.lat, home.lng, home.radius_m, home.label) == (
        40.7644, -73.9235, 4000, "Astoria, Queens")
    monkeypatch.setenv("AGENCY_HOME_AREA", "somewhere nice")
    assert missions.home_area() is None
    monkeypatch.setenv("AGENCY_HOME_AREA", "95,0,4000,Nowhere")
    assert missions.home_area() is None


# --------------------------------------------------------------- take()

@pytest.fixture
def store(monkeypatch, catalog):
    """The missions table, in memory; nothing kicked off in the background."""
    rows: dict[int, dict] = {}

    async def insert(**kw):
        mid = len(rows) + 1
        rows[mid] = {"id": mid, **kw, "state": kw.get("state") or {},
                     "steps": kw.get("steps") or [], "phase": ""}
        return mid

    async def save(m):
        rows[m["id"]] = {**rows.get(m["id"], {}), **m}

    async def nothing(*_a, **_kw):
        return None

    async def spend():
        return 3

    monkeypatch.setattr(missions, "_insert_mission", insert)
    monkeypatch.setattr(missions, "_save", save)
    monkeypatch.setattr(missions, "_supersede_questions", nothing)
    monkeypatch.setattr(missions, "_notify", nothing)
    monkeypatch.setattr(missions, "_kick", lambda _mid: None)
    monkeypatch.setattr("atlas.modules.agency.prospect.spend_this_month", spend)
    monkeypatch.setenv("GOOGLE_PLACES_API_KEY", "test-key")
    monkeypatch.setenv("PERPLEXITY_API_KEY", "test-key")
    monkeypatch.delenv("AGENCY_HOME_AREA", raising=False)
    _no_model(monkeypatch)
    return rows


async def test_no_area_and_no_home_asks_one_question(store, spy):
    got = await missions.take("find 50 local businesses with no website",
                              channel="voice", who_="dashboard")
    assert got.spoken.startswith(missions.QUESTION)
    assert not got.started
    assert store[got.mission_id]["status"] == "needs_input"
    assert spy["execute"] == [] and spy["propose"] == []


async def test_no_area_uses_the_home_area_and_says_so(store, monkeypatch):
    monkeypatch.setenv("AGENCY_HOME_AREA", "40.7644,-73.9235,4000,Astoria, Queens")
    got = await missions.take(OWNER, channel="voice", who_="dashboard")
    assert got.started and store[got.mission_id]["status"] == "running"
    # Said: who has it, what and where, and that updates go to Slack.
    assert "Astoria, Queens" in got.spoken and "Felix" in got.spoken
    assert "Slack" in got.spoken
    assert "AGENCY_HOME_AREA" not in got.spoken
    # The full account -- the switch, the channel, the approvals -- is detail.
    assert "AGENCY_HOME_AREA" in got.detail and "agency Slack channel" in got.detail
    assert "nothing is sent or published" in got.detail
    assert store[got.mission_id]["slots"]["n"] == 50


async def test_a_named_area_is_his_and_is_never_a_coordinate(store):
    got = await missions.take("find me 20 plumbers in Astoria with no website",
                              channel="slack", who_="U1", thread="1.2")
    row = store[got.mission_id]
    assert row["slots"]["area"] == "Astoria" and row["slots"]["n"] == 20
    assert "lat" not in json.dumps(row["slots"])


async def test_his_short_answer_starts_the_waiting_mission(store, monkeypatch):
    asked = await missions.take("find 30 local businesses with no website",
                                channel="voice", who_="dashboard")
    pending = store[asked.mission_id]

    async def question(channel):
        return pending if channel == "voice" else None

    monkeypatch.setattr(missions, "_pending_question", question)
    assert await missions.answer("thanks", channel="voice") is None
    assert await missions.answer("what time is it?", channel="voice") is None
    got = await missions.answer("Astoria", channel="voice")
    assert got.started and store[got.mission_id]["slots"]["area"] == "Astoria"
    assert store[got.mission_id]["slots"]["n"] == 30
    assert pending["status"] == "superseded"


@pytest.mark.parametrize("said", ["restart flycheap", "open the fleet",
                                  "system check", "find me 5 plumbers"])
async def test_an_order_is_never_taken_for_the_area(store, monkeypatch, said):
    asked = await missions.take("find 30 local businesses with no website",
                                channel="voice", who_="dashboard")

    async def question(_channel):
        return store[asked.mission_id]

    monkeypatch.setattr(missions, "_pending_question", question)
    assert await missions.answer(said, channel="voice") is None
    assert store[asked.mission_id]["status"] == "needs_input"


def test_the_surfaces_only_offer_his_answer_what_is_nothing_else():
    import inspect

    from atlas.api import slack_events
    ask = inspect.getsource(voice._ask)   # `ask` wraps it with the HUD's task row
    assert "not wants_action and view_target(transcript) is None" in ask
    assert "None if ordered else await missions.answer" in inspect.getsource(
        slack_events._mission)


async def test_a_phone_line_never_starts_one(store, spy):
    got = await missions.take(OWNER, channel="phone", who_="phone", principal=False)
    assert not got.started and got.mission_id is None and store == {}
    assert "Slack or the dashboard" in got.spoken
    assert "Slack or on the dashboard" in got.detail


async def test_no_perplexity_key_says_so_up_front(store, monkeypatch):
    """Only verified businesses are worked on; without Perplexity nothing
    can be verified, so no Places request is spent finding leads."""
    monkeypatch.delenv("PERPLEXITY_API_KEY")
    got = await missions.take(OWNER, channel="voice", who_="dashboard")
    assert not got.started and "PERPLEXITY_API_KEY" in got.spoken
    assert store[got.mission_id]["status"] == "stopped"


async def test_no_places_key_says_so_up_front(store, monkeypatch):
    monkeypatch.delenv("GOOGLE_PLACES_API_KEY")
    got = await missions.take(OWNER, channel="voice", who_="dashboard")
    assert not got.started and "search isn't set up" in got.spoken
    assert "GOOGLE_PLACES_API_KEY" not in got.spoken
    assert "GOOGLE_PLACES_API_KEY" in got.detail
    assert store[got.mission_id]["status"] == "stopped"


async def test_a_denied_request_is_never_a_mission(store):
    assert await missions.take(
        "find 10 businesses with no website and pay them $50 each",
        channel="voice", who_="dashboard") is None


async def test_not_a_recipe_job_falls_through(store):
    assert await missions.take("restart the flycheap server", channel="voice",
                               who_="dashboard") is None


# ------------------------------------------------------------ the runner

class FakeAgency:
    """The agency tables as `lead_facts` reads them, and an executor that
    writes to them the way the real actions would."""

    def __init__(self) -> None:
        self.leads: dict[int, missions.LeadFact] = {}
        self.pending: dict[int, tuple[int, set[str]]] = {}
        self.ran: list[tuple[str, dict]] = []
        self.next_id = 100
        self.sites_fail = False
        # lead id -> the verdict agency.verify records; verified by default.
        self.verdicts: dict[int, str] = {}
        self.verify_unread = ""

    async def facts(self, ids):
        return [self.leads[i] for i in ids if i in self.leads]

    async def execute(self, req):
        self.ran.append((req.action, req.params))
        p = req.params
        if req.action == "agency.prospect":
            new = []
            for website, severity, checks in ((None, 3, {"no_website"}),
                                              ("http://ok.example", 1, {"no_schema"}),
                                              ("http://old.example", 5,
                                               {"no_https", "not_mobile"})):
                self.next_id += 1
                self.leads[self.next_id] = missions.LeadFact(
                    self.next_id, f"Shop {self.next_id}", website, None)
                self.pending[self.next_id] = (severity, checks)
                new.append(self.next_id)
            return ActionResult(True, "3 new lead(s) from 3 result(s)",
                                evidence={"lead_ids": new})
        if req.action == "agency.verify":
            lead = self.leads[p["lead_id"]]
            lead.verdict = self.verdicts.get(lead.id, "verified")
            return ActionResult(True, f"lead {lead.id}: {lead.verdict}",
                                evidence={"verdict": lead.verdict,
                                          "unread": self.verify_unread})
        if req.action == "agency.audit":
            lead = self.leads[p["lead_id"]]
            severity, checks = self.pending[lead.id]
            lead.severity, lead.checks = severity, frozenset(checks)
            return ActionResult(True, "audited")
        if req.action == "agency.rank":
            for lead in self.leads.values():
                lead.score = float(lead.severity or 0) / 10
            return ActionResult(True, "ranked")
        if req.action == "agency.mockup":
            self.leads[p["lead_id"]].mockup = True
            return ActionResult(True, "mocked")
        if req.action == "agency.build_site":
            if self.sites_fail:
                return ActionResult(False, "disk full")
            self.leads[p["lead_id"]].site = f"shop-{p['lead_id']}"
            return ActionResult(True, "built")
        raise AssertionError(req.action)


@pytest.fixture
def agency(monkeypatch, catalog, store):
    fake = FakeAgency()
    monkeypatch.setattr(missions, "lead_facts", fake.facts)
    monkeypatch.setattr("atlas.kernel.executor.execute", fake.execute)

    async def no_propose(*_a, **_kw):
        raise AssertionError("the recipe raises no card")

    async def not_paused(*_a, **_kw):
        return None

    async def audit(*_a, **_kw):
        return None

    monkeypatch.setattr("atlas.kernel.approvals.propose", no_propose)
    monkeypatch.setattr("atlas.kernel.pause.active_for", not_paused)
    monkeypatch.setattr("atlas.kernel.audit.audit", audit)
    monkeypatch.setenv("ATLAS_WATCH_ONLY", "false")
    monkeypatch.setenv("AGENCY_HOME_AREA", "40.7644,-73.9235,4000,Astoria, Queens")
    monkeypatch.setattr(missions, "_paced", __import__("collections").deque())
    # The real pace (the module ceiling less two) would stop these at 8.
    monkeypatch.setattr(missions, "pace_per_hour", lambda: 100)
    return fake


async def _run_to_the_end(m: dict, passes: int = 30) -> dict:
    for _ in range(passes):
        await missions._advance(m)
        if m["status"] not in ("running", "waiting"):
            break
    return m


async def test_the_recipe_runs_in_order_and_only_what_qualifies_is_built(agency):
    m = _mission(slots={"n": 2, "business": None, "area": None})
    await _run_to_the_end(m)
    assert m["status"] == "done"
    names = [name for name, _p in agency.ran]
    assert names[0] == "agency.prospect"
    assert names.index("agency.rank") > max(i for i, n in enumerate(names)
                                             if n == "agency.audit")
    built = {p["lead_id"] for n, p in agency.ran if n == "agency.build_site"}
    # The no-website lead and the severity-5 lead qualify; the tidy site does not.
    assert built == {101, 103}
    mocked = [p["lead_id"] for n, p in agency.ran if n == "agency.mockup"]
    assert sorted(mocked) == [101, 103]
    # The home area was used, from the environment, never a model.
    assert m["state"]["area"]["source"] == "AGENCY_HOME_AREA"
    prospect = next(p for n, p in agency.ran if n == "agency.prospect")
    assert (prospect["lat"], prospect["lng"]) == (40.7644, -73.9235)


async def test_the_report_counts_what_the_tables_say(agency):
    m = _mission(slots={"n": 2, "business": None, "area": None})
    await _run_to_the_end(m)
    # The tables are the truth: a site removed after the build is not counted.
    agency.leads[103].site = None
    read_at = datetime(2026, 9, 29, 14, 5, tzinfo=UTC)
    text = missions.report(m, await agency.facts(m["state"]["lead_ids"]),
                           read_at=read_at)
    assert "2026-09-29 14:05" in text
    assert "3 new lead(s) found; 3 audited" in text
    assert "2 qualify (1 with no website, 1 with a poor one" in text
    assert "1 site(s) built of the 2 asked for" in text
    assert "1 of the 2 sites were not built" in text
    assert "outreach and deploying: not done" in text


async def test_each_lead_is_verified_before_it_is_audited(agency):
    m = _mission(slots={"n": 2, "business": None, "area": None})
    await _run_to_the_end(m)
    names = [(n, p.get("lead_id")) for n, p in agency.ran]
    for lead in (101, 102, 103):
        assert names.index(("agency.verify", lead)) < names.index(("agency.audit", lead))


async def test_disqualified_and_unknown_leads_are_never_audited_or_built(agency):
    agency.verdicts = {101: "disqualified", 103: "unknown"}
    m = _mission(slots={"n": 2, "business": None, "area": None})
    await _run_to_the_end(m)
    touched = {p.get("lead_id") for n, p in agency.ran
               if n in ("agency.audit", "agency.mockup", "agency.build_site")}
    assert 101 not in touched and 103 not in touched
    # A second search found three more; the counts come from the tables.
    assert "4 verified, 1 disqualified, 1 unknown (never contacted)" in m["report"]


async def test_verification_stops_when_perplexity_is_capped(agency):
    agency.verdicts = {101: "unknown", 102: "unknown", 103: "unknown"}
    agency.verify_unread = "daily verification cap reached (40/40)"
    m = _mission(slots={"n": 2, "business": None, "area": None})
    await _run_to_the_end(m)
    verified = [p["lead_id"] for n, p in agency.ran if n == "agency.verify"]
    assert verified == [101]
    # No second billed search for leads that could never be verified.
    assert [n for n, _p in agency.ran].count("agency.prospect") == 1
    assert "verify: daily verification cap reached" in m["report"]
    assert not [n for n, _p in agency.ran if n == "agency.audit"]


async def test_what_was_not_done_is_said_with_its_reason(agency):
    agency.sites_fail = True
    m = _mission(slots={"n": 2, "business": None, "area": None})
    await _run_to_the_end(m)
    assert m["status"] == "done"
    assert "build_site: disk full" in m["report"]
    assert "0 site(s) built of the 2 asked for" in m["report"]


async def test_watch_only_holds_the_mission_and_runs_nothing(agency, monkeypatch):
    monkeypatch.setenv("ATLAS_WATCH_ONLY", "true")
    m = _mission(slots={"n": 2, "business": None, "area": None})
    await missions._advance(m)
    assert m["status"] == "waiting" and "watch-only" in m["state"]["waiting"]
    assert agency.ran == []


async def test_watch_only_spends_no_places_request_on_a_named_area(agency, monkeypatch):
    async def locate(*_a, **_kw):
        raise AssertionError("a billed Places read while ATLAS is on hold")

    monkeypatch.setattr("atlas.modules.agency.prospect.locate", locate)
    monkeypatch.setenv("ATLAS_WATCH_ONLY", "true")
    m = _mission(slots={"n": 2, "business": "plumbers", "area": "Park Slope"})
    await missions._advance(m)
    assert m["status"] == "waiting" and "area" not in m["state"]
    assert agency.ran == []


async def test_a_pause_holds_the_mission_and_runs_nothing(agency, monkeypatch):
    class Stopped:
        def says(self):
            return "the agency module is paused"

    async def paused(*_a, **_kw):
        return Stopped()

    monkeypatch.setattr("atlas.kernel.pause.active_for", paused)
    m = _mission(slots={"n": 2, "business": None, "area": None})
    await missions._advance(m)
    assert m["status"] == "waiting" and "paused" in m["state"]["waiting"]
    assert agency.ran == []


async def test_a_mission_past_its_deadline_lapses_with_a_report(agency):
    m = _mission(deadline=datetime.now(UTC) - timedelta(minutes=1),
                 state={"waiting": "ATLAS is in watch-only mode"})
    await missions._advance(m)
    assert m["status"] == "expired" and "ran out of time" in m["report"]
    assert agency.ran == []


async def test_the_pace_stops_a_pass_under_the_module_ceiling(agency, monkeypatch):
    monkeypatch.setattr(missions, "pace_per_hour", lambda: 2)
    m = _mission(slots={"n": 2, "business": None, "area": None})
    await missions._advance(m)
    await missions._advance(m)
    assert len(agency.ran) == 2
    assert m["status"] == "waiting" and "pace" in m["state"]["waiting"]


# ----------------------------------------------------- the generic path

async def test_an_agent_named_takes_it_from_its_own_catalog(catalog, monkeypatch, spy):
    seen = {}

    async def choose(request, *, principal=True, only=None):
        seen["request"], seen["only"] = request, only
        return "agency.deploy_site", {"slug": "joes"}

    async def insert(**_kw):
        return 5

    monkeypatch.setattr(intent, "choose", choose)
    monkeypatch.setattr(missions, "_insert_mission", insert)
    got = await missions.delegate("have Felix deploy joes", channel="slack", who_="U1")
    assert seen["request"] == "deploy joes"
    assert seen["only"] == missions.owner_catalog(AGENCY_OWNER)
    assert got.proposal_id == 77 and "card #77" in got.detail
    assert got.spoken.startswith("Felix's on it") and "#77" not in got.spoken
    assert spy["execute"] == []                       # a card, never a run


async def test_nobody_owns_it_so_nobody_is_handed_it(catalog):
    assert await missions.delegate("optimize the cpu on the roots server",
                                   channel="slack", who_="U1") is None


# ----------------------------------------------------------- the surfaces

async def test_slack_hands_the_owners_sentence_to_the_mission(monkeypatch):
    from atlas.api import slack_events

    taken = {}

    async def take(words, **kw):
        taken["words"], taken["kw"] = words, kw
        return missions.Reply("Hazel (Agency) has it — mission #3", 3, started=True)

    async def must_not(*_a, **_kw):
        raise AssertionError("the mission comes before the one-action picker")

    async def no_answer(*_a, **_kw):
        return None

    monkeypatch.setattr(missions, "take", take)
    monkeypatch.setattr(missions, "answer", no_answer)
    monkeypatch.setattr("atlas.kernel.intent.act", must_not)
    said = await slack_events._mission(f"atlas, {OWNER}", "U1", "1.2")
    assert said.startswith("Hazel (Agency) has it")
    assert taken["kw"]["channel"] == "slack" and taken["kw"]["thread"] == "1.2"


@pytest.mark.parametrize("said,knowledge", [
    ("look up the capital of Peru", True),
    # His own estate: to the specialists who measure, never a web answer.
    ("find out why cloudco is slow", False),
    ("get me the cloudco numbers", False),
])
def test_slack_answers_a_lookup_from_knowledge_only_when_it_is_not_his(said, knowledge):
    from atlas.api import slack_events
    assert slack_events._is_knowledge_question(said) is knowledge
    assert not slack_events._is_conversation(said)


def test_make_sure_is_not_an_order_to_make_something():
    assert not voice.is_order("make sure the backups ran")


def test_the_missions_step_has_a_missed_run_policy():
    from atlas.kernel import scheduler
    from atlas.kernel.missed import Missed
    policy = scheduler.POLICIES["missions"]
    assert policy.missed is Missed.RUN_ONCE and policy.why


@pytest.fixture
def client(monkeypatch, memory_backend):
    from fastapi.testclient import TestClient

    from atlas.api import auth
    monkeypatch.setenv("SESSION_SECRET", "m" * 40)
    monkeypatch.setenv("DASHBOARD_PASSWORD_HASH", auth.hash_password("a-long-password!"))
    monkeypatch.setenv("MESH_ALLOWED_CIDRS", "127.0.0.0/8")
    auth._attempts.clear()
    memory_backend.responses["INSERT INTO voice_turns"] = None
    from atlas.api.main import app

    async def no_boot() -> None:
        return None
    monkeypatch.setattr("atlas.api.main.boot", no_boot)
    monkeypatch.setattr("atlas.api.main.scheduler.loop", no_boot)
    with TestClient(app, client=("127.0.0.1", 52003)) as c:
        c.post("/api/login", json={"password": "a-long-password!"})
        yield c


def test_the_dashboard_hands_the_owners_sentence_to_the_agency(client, monkeypatch):
    seen = {}

    async def take(words, **kw):
        seen.update(kw, words=words)
        return missions.Reply("Hazel (Agency) has it — mission #4", 4, started=True)

    async def no_answer(*_a, **_kw):
        return None

    async def must_not(*_a, **_kw):
        raise AssertionError("never small talk, never a one-action card")

    monkeypatch.setattr(missions, "take", take)
    monkeypatch.setattr(missions, "answer", no_answer)
    monkeypatch.setattr("atlas.kernel.agents.route", must_not)
    monkeypatch.setattr("atlas.kernel.intent.choose", must_not)
    body = client.post("/api/voice/ask", json={"transcript": OWNER}).json()
    assert body["kind"] == "mission" and body["action_request"] is True
    assert body["answer"].startswith("Hazel (Agency) has it")
    assert seen["channel"] == "voice" and seen["principal"] is True


async def test_the_phone_line_recognises_it_but_never_starts_it(monkeypatch):
    seen = {}

    async def take(words, **kw):
        seen.update(kw)
        return missions.Reply("not from a phone")

    async def not_improve(*_a, **_kw):
        return None

    monkeypatch.setattr(missions, "take", take)
    monkeypatch.setattr("atlas.kernel.improve.handle", not_improve)
    text, cards = await voice.spoken_turn(OWNER, source="phone")
    assert text == "not from a phone" and cards == []
    assert seen["principal"] is False


def test_the_agency_room_lists_the_missions(client, monkeypatch):
    async def recent(limit):
        return [{"id": 4, "agent": "agency", "recipe": missions.AGENCY_SITES,
                 "request": OWNER, "status": "running", "phase": "search",
                 "slots": {"n": 50}, "state": {"area": {"label": "Astoria"}},
                 "steps": [{"action": "agency.deploy_site", "proposal_id": 12}],
                 "report": None, "created_at": datetime(2026, 9, 29, tzinfo=UTC),
                 "finished_at": None}]

    monkeypatch.setattr(missions, "recent", recent)
    body = client.get("/api/agency/missions").json()
    got = body["missions"][0]
    # A row stored under the retired `agency` agent reads as Felix (sites).
    assert got["persona"] == "Felix" and got["cards"] == [12]
    assert got["area"] == {"label": "Astoria"} and body["read_at"]


def test_an_unreadable_missions_table_is_a_503_not_an_empty_list(client, monkeypatch):
    async def broken(limit):
        raise ConnectionError("db down")

    monkeypatch.setattr(missions, "recent", broken)
    assert client.get("/api/agency/missions").status_code == 503


# ------------------------------------------------------------- said briefly
#
# the owner, 2026-09-29: "Not too chatty -- simple and straight to the point." The
# acknowledgement was five sentences with two env var names and a file path.
# Every reply a mission speaks is at most two short sentences; the pacing,
# the approvals and the switches go to Slack (`Reply.detail`).

_CONFIG = re.compile(r"\b[A-Z][A-Z0-9]+_[A-Z0-9_]*|[\w./-]+\.(?:py|sh|json)\b|#\d")


def assert_brief(said: str) -> None:
    sentences = [s for s in re.split(r"(?<=[.!?])\s+", said.strip()) if s]
    assert len(sentences) <= 2, said
    assert len(said.split()) <= 35, said
    assert not _CONFIG.search(said), said


@pytest.mark.parametrize("watch,sites", [(True, False), (False, False),
                                         (False, True), (True, True)])
async def test_the_mission_acknowledgement_is_one_breath(store, monkeypatch,
                                                         watch, sites):
    monkeypatch.setattr("atlas.kernel.executor.watch_only", lambda: watch)
    monkeypatch.setattr(missions, "_sites_ready", lambda: sites)
    got = await missions.take("find 50 local businesses in New York with no website",
                              channel="voice", who_="dashboard")
    assert got.started
    assert_brief(got.spoken)
    assert got.spoken.startswith("On it — Felix's finding 50")
    assert "New York" in got.spoken and "Slack" in got.spoken
    # A blocker is one clause, never dropped: watch-only is the default and
    # a job that cannot run must not sound started.
    assert ("watch-only" in got.spoken) is watch
    assert ("GetLayers" in got.spoken) is (not sites)
    # The long form keeps what the spoken line leaves out.
    assert f"mission #{got.mission_id}" in got.detail
    assert "nothing is sent or published" in got.detail


async def test_every_refusal_is_short_and_keeps_its_reason(store, monkeypatch):
    replies = [missions.NOT_HERE_SAID.format(name="Hazel"), missions.QUESTION]
    monkeypatch.delenv("GOOGLE_PLACES_API_KEY")
    replies.append((await missions.take(OWNER, channel="voice", who_="d")).spoken)
    monkeypatch.setenv("GOOGLE_PLACES_API_KEY", "k")

    async def spent():
        return 500

    monkeypatch.setattr("atlas.modules.agency.prospect.spend_this_month", spent)
    monkeypatch.setattr("atlas.modules.agency.prospect.cap", lambda: 500)
    budget = await missions.take(OWNER, channel="voice", who_="d")
    assert "budget" in budget.spoken and "500/500" in budget.detail
    assert "read " in budget.detail                 # the figure carries its time
    replies.append(budget.spoken)
    for said in replies:
        assert_brief(said)
    assert "can't search" in replies[2] and "set up" in replies[2]


@pytest.mark.parametrize("why,said", [
    ("agency.buy_domain is permanently denied", "never allowed"),
    ("agency.x is not in the catalog", "no action"),
    ("markets.alert is not the agency agent's to run", "isn't their job"),
    ("agency.audit needs lead_id", "needs lead id"),
])
def test_a_vetting_reason_is_said_without_action_names(why, said):
    out = missions._why_said(why)
    assert said in out and "agency." not in out and "_" not in out


async def test_a_delegated_answer_is_introduced_by_name(monkeypatch, catalog):
    class Agent:
        title = "Content & YouTube"

        async def run(self, task, context=None):
            assert "at most two short sentences" in task
            return "Three posts went out, as of 7:52."

    async def insert(**_kw):
        return 6

    async def nothing(*_a, **_kw):
        return None

    monkeypatch.setattr(intent, "choose", nothing)
    monkeypatch.setattr(missions, "_insert_mission", insert)
    monkeypatch.setitem(__import__("atlas.kernel.agents", fromlist=["ROSTER"]).ROSTER,
                        "content", Agent())
    got = await missions.delegate("have Mark summarise his day", channel="voice",
                                  who_="dashboard")
    assert got.spoken == "Here's Mark: Three posts went out, as of 7:52."
    assert got.detail.startswith("Mark (")
