"""Goodnight (15.4). A spoken plan built only from real tables: the brief
hour, queued night-shift jobs, tomorrow's first meeting. Never a task
invented to sound busy -- a line the tables did not supply is a line this
file must not find."""
from __future__ import annotations

import ast
import pathlib
from datetime import datetime, timedelta

import pytest

from atlas.kernel import tonight
from atlas.kernel.contracts import AgendaItem
from atlas.kernel.registry import REGISTRY

# 22:40 local on a Thursday: "tomorrow" is Friday.
NOW = datetime(2026, 9, 24, 22, 40).astimezone()


class _Calendar:
    name, title = "calendar", "Calendar"

    def __init__(self, items: list[AgendaItem] | None = None,
                 fails: bool = False) -> None:
        self.items, self.fails = items or [], fails

    async def agenda(self, within):
        if self.fails:
            raise RuntimeError("calendar API returned 401")
        return self.items


class _Fiverr:
    name, title = "fiverr", "Fiverr"

    def __init__(self, items: list[AgendaItem]) -> None:
        self.items = items

    async def agenda(self, within):
        return self.items


def _at(days: int, hour: int, minute: int = 0) -> datetime:
    day = (NOW + timedelta(days=days)).date()
    return datetime(day.year, day.month, day.day, hour, minute).astimezone()


@pytest.fixture
def tables(monkeypatch):
    """Every table empty unless a test fills it."""
    data: dict[str, list[dict]] = {"nightshift": [], "research": [], "proposals": []}

    async def fake_fetch_all(sql, *args):
        if "FROM nightshift_jobs" in sql:
            return data["nightshift"]
        if "FROM research_reports" in sql:
            return data["research"]
        raise AssertionError(f"unexpected query: {sql}")

    async def fake_fetch_one(sql, *args):
        assert "FROM proposals" in sql
        assert "pending" in sql and "expires_at" in sql
        return {"n": len(data["proposals"])}

    monkeypatch.setattr(tonight, "fetch_all", fake_fetch_all)
    monkeypatch.setattr(tonight, "fetch_one", fake_fetch_one)
    monkeypatch.setattr(tonight, "_now", lambda: NOW)
    monkeypatch.setenv("BRIEF_HOUR", "7")
    monkeypatch.setenv("TOURNAMENT_HOUR", "3")
    monkeypatch.setattr(REGISTRY, "modules", {"calendar": _Calendar()})
    monkeypatch.setattr(REGISTRY, "disabled", {})

    async def channel() -> bool:
        return True

    monkeypatch.setattr(tonight, "_brief_channel", channel)
    return data


async def test_names_the_queued_task_and_the_first_meeting_and_nothing_else(
        tables, monkeypatch):
    tables["nightshift"] = [{"task": "add price alerts to flycheap",
                             "status": "queued"}]
    monkeypatch.setattr(REGISTRY, "modules", {"calendar": _Calendar([
        AgendaItem(when=_at(1, 9, 30), title="Standup with Dana", source="calendar"),
        AgendaItem(when=_at(1, 14), title="Dentist", source="calendar"),
    ])})
    said = await tonight.build()
    assert "add price alerts to flycheap" in said
    assert "Standup with Dana" in said
    assert "09:30" in said
    assert "tomorrow" in said.lower()
    assert "I'll have a prep card 30 minutes before" in said
    assert "Dentist" not in said                 # the FIRST item only
    assert "research" not in said.lower()        # none queued, none named
    assert "proposal" not in said.lower()        # none waiting, none named
    assert "07:00" in said and "03:00" in said   # the hours themselves
    assert said.endswith("Rest up.")


async def test_an_empty_night_invents_nothing(tables):
    said = await tonight.build()
    assert "night shift" not in said.lower()
    assert "research" not in said.lower()
    assert "proposal" not in said.lower()
    assert "Nothing is on the agenda" in said
    assert said.endswith("Rest up.")


async def test_running_and_queued_jobs_are_named_by_status(tables):
    tables["nightshift"] = [
        {"task": "refactor the scraper", "status": "running"},
        {"task": "a", "status": "queued"}, {"task": "b", "status": "queued"},
        {"task": "c", "status": "queued"}, {"task": "d", "status": "queued"},
        {"task": "e", "status": "queued"},
    ]
    said = await tonight.build()
    assert "refactor the scraper" in said
    assert "running" in said
    assert "and 2 more" in said
    assert "ready by morning" not in said.lower()     # never a promise


async def test_proposals_waiting_expire_and_never_run(tables):
    tables["proposals"] = [{}, {}, {}]
    said = await tonight.build()
    assert "3 proposals are waiting; they expire, they never run." in said


async def test_one_proposal_is_singular(tables):
    tables["proposals"] = [{}]
    said = await tonight.build()
    assert "1 proposal is waiting; it expires, it never runs." in said


async def test_queued_research_is_counted(tables):
    tables["research"] = [{"question": "q1"}, {"question": "q2"}]
    said = await tonight.build()
    assert "2 research reports are queued" in said


async def test_a_deadline_gets_no_prep_card_promise(tables, monkeypatch):
    monkeypatch.setattr(REGISTRY, "modules", {
        "calendar": _Calendar(),
        "fiverr": _Fiverr([AgendaItem(when=_at(1, 10), title="Logo delivery",
                                      kind="deadline", source="fiverr")]),
    })
    said = await tonight.build()
    assert "Logo delivery" in said
    assert "prep card" not in said


async def test_an_unreadable_calendar_is_said_not_hidden(tables, monkeypatch):
    monkeypatch.setattr(REGISTRY, "modules", {"calendar": _Calendar(fails=True)})
    said = await tonight.build()
    assert "couldn't read" in said.lower()
    assert "calendar" in said.lower()
    assert "Nothing is on the agenda" not in said


async def test_no_calendar_connected_is_said(tables, monkeypatch):
    monkeypatch.setattr(REGISTRY, "modules", {})
    monkeypatch.setattr(REGISTRY, "disabled", {"calendar": "missing env: GOOGLE_CLIENT_ID"})
    said = await tonight.build()
    assert "No calendar is connected" in said
    assert "Nothing is on the agenda" not in said


async def test_an_unreadable_table_is_said(tables, monkeypatch):
    async def down(sql, *args):
        raise ConnectionError("postgres refused")

    monkeypatch.setattr(tonight, "fetch_all", down)
    monkeypatch.setattr(tonight, "fetch_one", down)
    said = await tonight.build()
    assert "couldn't read the night-shift queue" in said
    assert "couldn't read the proposals" in said
    assert said.endswith("Rest up.")


async def test_after_midnight_the_morning_is_today(tables, monkeypatch):
    late = datetime(2026, 9, 25, 1, 15).astimezone()
    monkeypatch.setattr(tonight, "_now", lambda: late)
    day = late.date()
    meeting = datetime(day.year, day.month, day.day, 9, 30).astimezone()
    monkeypatch.setattr(REGISTRY, "modules", {"calendar": _Calendar([
        AgendaItem(when=meeting, title="Standup", source="calendar")])})
    said = await tonight.build()
    assert "Standup" in said
    assert "tomorrow" not in said.lower()


async def test_the_whole_thing_fits_in_one_spoken_answer(tables):
    tables["nightshift"] = [{"task": "x" * 400, "status": "queued"}] * 9
    tables["research"] = [{"question": "q"}] * 4
    tables["proposals"] = [{}] * 12
    said = await tonight.build()
    from atlas.api.voice import MAX_SPEAK_CHARS
    assert len(said) < MAX_SPEAK_CHARS


def test_tonight_cannot_reach_the_executor_or_a_model():
    path = pathlib.Path(tonight.__file__)
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            where = getattr(node, "module", "") or ""
            names = " ".join(a.name for a in node.names)
            assert "executor" not in f"{where} {names}"
            assert "llm" not in f"{where} {names}"


async def test_with_no_channel_the_brief_is_on_the_dashboard(tables, monkeypatch):
    async def none() -> bool:
        return False

    monkeypatch.setattr(tonight, "_brief_channel", none)
    said = await tonight.build()
    assert "the morning brief will be on the dashboard at 07:00" in said
    assert "goes out" not in said


async def test_with_a_channel_the_brief_goes_out(tables):
    said = await tonight.build()
    assert "the morning brief goes out at 07:00" in said


async def test_no_registered_channel_reads_as_the_dashboard(monkeypatch):
    from atlas.kernel import comms
    monkeypatch.setattr(comms, "CHANNELS", {})
    assert await tonight._brief_channel() is False


async def test_a_hung_agenda_is_said_as_unreadable_not_waited_on(tables, monkeypatch):
    import asyncio

    class _Hung:
        async def agenda(self, within):
            await asyncio.sleep(60)

    monkeypatch.setattr(tonight, "AGENDA_TIMEOUT_S", 0.05)
    monkeypatch.setattr(REGISTRY, "modules", {"calendar": _Hung()})
    said = await asyncio.wait_for(tonight.build(), 2)
    assert "I couldn't read the agenda from calendar." in said
