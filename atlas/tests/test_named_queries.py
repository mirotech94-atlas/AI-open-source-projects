"""The per-agent shifts name these queries (plan 2026-10-30, A2). They stay
read-only, take no parameters, and the mail one is private like its module."""
from __future__ import annotations

import pytest

from atlas.kernel import private_data
from atlas.tools import builtin

NEW = ("agency_sites_state", "agency_leads_pipeline", "sales_pipeline",
       "mail_followups_open", "academy_content_plan", "academy_title_tests")


@pytest.mark.parametrize("name", NEW)
def test_each_query_is_a_single_select_without_parameters(name):
    sql = builtin.NAMED_QUERIES[name].strip()
    assert sql.upper().startswith("SELECT"), name
    assert ";" not in sql, name
    assert "$" not in sql, name
    for verb in ("INSERT ", "UPDATE ", "DELETE ", "DROP ", "ALTER "):
        assert verb not in sql.upper(), (name, verb)


@pytest.mark.parametrize("name", NEW)
async def test_each_query_returns_rows_through_sql_query(monkeypatch, name):
    seen: list[str] = []

    async def fetch_all(sql, *args):
        seen.append(sql)
        assert args == ()
        return [{"n": 1}]

    monkeypatch.setattr(builtin, "fetch_all", fetch_all)
    assert await builtin.sql_query(name) == [{"n": 1}]
    assert seen == [builtin.NAMED_QUERIES[name]]


async def test_open_followups_are_left_out_for_an_agent_that_is_not_private(
        monkeypatch):
    sql = builtin.NAMED_QUERIES["mail_followups_open"]
    assert "'mail' AS property" in sql
    assert "mail" in private_data.PRIVATE_MODULES

    async def fetch_all(sql, *args):
        return [{"property": "mail", "subject": "x"}]

    monkeypatch.setattr(builtin, "fetch_all", fetch_all)
    monkeypatch.setattr(private_data, "calling_agent", lambda: "sites")
    monkeypatch.setattr(private_data, "agent_is_private", lambda name: False)
    assert await builtin.sql_query("mail_followups_open") == []
    monkeypatch.setattr(private_data, "agent_is_private", lambda name: True)
    assert await builtin.sql_query("mail_followups_open") == [
        {"property": "mail", "subject": "x"}]
