"""Personal content stays with the local-model agents (7da37bf, extended).

`module_brief("mail")` returned the senders and subjects of his unread
mail to any agent that held the tool -- ops, growth, commerce, and every
business agent through `businesses.BASE_READS` -- and those run on cloud
models. The read tools now refuse a private module unless the calling
agent (`tools.CURRENT_AGENT`, set by `invoke` alone) is `Need.PRIVATE`.
"""
from __future__ import annotations

from datetime import UTC, datetime

import pytest

from atlas.kernel import agents
from atlas.kernel.contracts import InboxItem, Severity
from atlas.kernel.private_data import PRIVATE_MODULES
from atlas.kernel.registry import REGISTRY
from atlas.kernel.tools import TOOLS, Tool, invoke
from atlas.tools import builtin

NOW = datetime(2026, 9, 24, 9, 0, tzinfo=UTC)


class Mail:
    def brief(self):
        return "mail: 3 unread"

    async def inbox(self, limit=10):
        return (InboxItem(at=NOW, sender="Dr Aziz", subject="your results",
                          urgency=Severity.HIGH),)


@pytest.fixture
def roster(monkeypatch, memory_backend):
    monkeypatch.setattr(agents, "ROSTER", {})
    agents.build_roster()
    monkeypatch.setitem(REGISTRY.modules, "mail", Mail())
    for name, fn in (("module_brief", builtin.module_brief),
                     ("module_status", builtin.module_status),
                     ("read_metrics", builtin.read_metrics),
                     ("read_history", builtin.read_history),
                     ("sql_query", builtin.sql_query)):
        monkeypatch.setitem(TOOLS, name, Tool(name, "", {}, read=fn))

    async def rows(sql, *args):
        return [{"property": "mail", "check": "unreplied", "at": NOW,
                 "health": "ok", "value": 3.0, "unit": "threads",
                 "detail": "Dr Aziz: your results", "observed_at": NOW,
                 "interval_s": 300},
                {"property": "flycheap", "check": "api", "at": NOW,
                 "health": "ok", "value": 1.0, "unit": None, "detail": "fine",
                 "observed_at": NOW, "interval_s": 300}]

    monkeypatch.setattr(builtin, "fetch_all", rows)
    monkeypatch.setattr("atlas.kernel.state.fetch_all", rows)
    return agents.ROSTER


@pytest.mark.parametrize("agent", ["ops", "growth", "commerce", "sentinel",
                                   "flycheap", "concierge"])
@pytest.mark.parametrize("tool,params", [
    ("module_brief", {"module": "mail"}),
    ("module_status", {"module": "mail"}),
    ("read_metrics", {"property": "mail"}),
    ("read_history", {"property": "mail", "check": "unreplied"}),
    ("sql_query", {"query": "latest_by_check", "params": ["mail", 5]}),
])
async def test_a_cloud_agent_asking_for_mail_is_refused(roster, agent, tool, params):
    res = await invoke(tool, params, agent=agent, allowed={tool})
    assert res.content["refused"] == "private_module"
    assert "local model" in res.content["error"]
    assert "Dr Aziz" not in str(res.content)


async def test_a_cross_property_query_leaves_private_rows_out(roster):
    res = await invoke("sql_query", {"query": "cross_property_window",
                                     "params": ["2026-09-24T09:00:00Z"]},
                       agent="ops", allowed={"sql_query"})
    assert [r["property"] for r in res.content] == ["flycheap"]


async def test_the_inbox_agent_gets_the_mail_brief(roster):
    assert roster["inbox"].need.name == "PRIVATE"
    res = await invoke("module_brief", {"module": "mail"}, agent="inbox",
                       allowed={"module_brief"})
    assert res.content["brief"] == "mail: 3 unread"
    assert res.content["inbox"][0]["sender"] == "Dr Aziz"


async def test_the_school_agent_reads_its_own_module(roster):
    res = await invoke("module_status", {"module": "school"}, agent="school",
                       allowed={"module_status"})
    assert "refused" not in res.content


async def test_a_call_from_outside_invoke_is_not_a_private_agent(roster):
    got = await builtin.module_brief("mail")
    assert got["refused"] == "private_module"


async def test_a_public_module_is_read_by_anyone(roster):
    res = await invoke("module_status", {"module": "flycheap"}, agent="ops",
                       allowed={"module_status"})
    assert "refused" not in res.content


def test_the_set_names_every_personal_module():
    assert {"mail", "messages", "messaging", "vault", "school",
            "calendar"} <= PRIVATE_MODULES


async def test_module_status_reads_a_stale_ok_as_unknown(roster, monkeypatch):
    old = datetime(2026, 9, 1, tzinfo=UTC)

    async def stale(sql, *args):
        return [{"check": "api", "health": "ok", "value": 1.0, "unit": None,
                 "detail": "", "observed_at": old, "interval_s": 300}]

    monkeypatch.setattr("atlas.kernel.state.fetch_all", stale)
    got = await builtin.module_status("flycheap")
    assert got["checks"][0]["health"] == "unknown"
