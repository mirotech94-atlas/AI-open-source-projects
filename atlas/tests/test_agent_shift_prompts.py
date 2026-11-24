"""Each roster agent's daily shift names ITS modules (plan 2026-10-30, A1):
the one generic prompt named "fiverr, inroots, content", and every outcome
talked about Fiverr. The prefix stays, so `agent_runs` still finds shifts."""
from __future__ import annotations

from datetime import UTC, datetime

import pytest

from atlas.kernel import agents, scheduler

OWN_MODULES = {
    "inbox": ("module_status mail", "module_status calendar"),
    "sites": ("module_status agency", "sql_query agency_sites_state",
              "agency_leads_pipeline"),
    "content": ("module_status academy", "module_status youtube",
                "sql_query academy_content_plan", "academy_title_tests"),
    "qa": ("module_status qa", "read_history qa gate_health"),
    "sales": ("module_status sales", "module_status agency"),
    "bizops": ("module_status bizops",),
    "seo": ("module_status seo",),
    "research": ("module_status research",),
    "mmt": ("module_status mmt", "module_status linkedin"),
}


@pytest.fixture
def roster(monkeypatch):
    monkeypatch.delenv("ATLAS_DISABLED_MODULES", raising=False)
    agents.build_roster()
    return agents.ROSTER


def test_every_roster_agent_has_its_own_shift(roster):
    assert set(roster) == set(agents.AGENT_NAMES)
    for name in agents.AGENT_NAMES:
        text = agents.shift_prompt(name)
        assert text.startswith(agents.SHIFT_PREFIX), name
        assert text != agents.SHIFT_PROMPT, name
        low = text.lower()
        assert "fiverr" not in low and "inroots" not in low, name
        for needle in OWN_MODULES[name]:
            assert needle in text, f"{name} shift does not name {needle!r}"


def test_no_shift_reads_another_agents_mailbox(roster):
    for name in agents.AGENT_NAMES:
        if name != "inbox":
            assert "module_status mail" not in agents.shift_prompt(name), name


def test_the_inbox_shift_never_drafts_or_sends(roster):
    text = agents.shift_prompt("inbox").lower()
    assert "mail_draft_reply" not in text
    assert "never send" in text


def test_an_agent_without_a_shift_gets_the_generic_prompt(monkeypatch):
    class Bare:
        name = "ops"

    monkeypatch.setattr(agents, "ROSTER", {"ops": Bare()})
    assert agents.shift_prompt("ops") == agents.SHIFT_PROMPT
    assert agents.shift_prompt("nobody") == agents.SHIFT_PROMPT


def test_the_content_shift_leaves_church_out_when_it_is_disabled(monkeypatch):
    monkeypatch.setenv("ATLAS_DISABLED_MODULES", "browser,church,clips")
    agents.build_roster()
    content = agents.ROSTER["content"]
    assert "ECC" not in content.system
    assert "church" not in agents.shift_prompt("content")
    assert "academy" in agents.shift_prompt("content")
    monkeypatch.delenv("ATLAS_DISABLED_MODULES")
    agents.build_roster()
    assert agents.ROSTER["content"].system.startswith(agents._church_system())
    assert "module_status church" in agents.shift_prompt("content")


async def test_the_scheduler_runs_each_agent_with_its_own_shift(monkeypatch):
    class FakeAgent:
        def __init__(self, name: str, shift: str) -> None:
            self.name, self.shift, self.calls = name, shift, []

        async def run(self, task: str, context=None) -> str:
            self.calls.append(task)
            return "done"

    fake = FakeAgent("sites", "read agency only")
    monkeypatch.setattr(agents, "ROSTER", {"sites": fake})
    monkeypatch.setattr(agents, "readiness", lambda name: (True, []))
    monkeypatch.setattr(agents, "shift_due", lambda *a, **k: True)
    then = datetime(2026, 10, 29, 5, tzinfo=UTC)

    async def fetch_all(sql, *args):
        return [{"agent": "sites", "last_any": then, "last_shift": then,
                 "failures": []}]

    async def no_alert(*a, **k) -> None:
        return None

    monkeypatch.setattr(scheduler, "fetch_all", fetch_all)
    monkeypatch.setattr(scheduler, "_alert_shift", no_alert)
    monkeypatch.setattr(scheduler, "_now", lambda: datetime(2026, 10, 30, 5,
                                                             tzinfo=UTC))
    await scheduler._maybe_agent_shifts()
    assert fake.calls == [agents.SHIFT_PREFIX + " read agency only"]
