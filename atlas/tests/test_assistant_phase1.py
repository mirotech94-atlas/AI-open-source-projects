"""Phase 1 of Miro's Atlas Implementation Plan (Ask + Search): the free-tier
prompt budget, the two modes, the feature flag, honest snippets, units
through calculation, and the run record with its cancel signal."""
from __future__ import annotations

import asyncio
import hashlib
import json
import pathlib

import pytest

from atlas.kernel.llm import Completion
from atlas.kernel.tools import TOOLS, Tool
from atlas.modules import assistant
from atlas.modules.assistant import loop, modes, runs

ROOT = pathlib.Path(__file__).resolve().parent.parent


@pytest.fixture(autouse=True)
def _budget_readable(memory_backend):
    memory_backend.responses["FROM actions_log WHERE event = $1 AND subject = ANY"] = {
        "n": 0}
    return memory_backend


def _call(name: str, **params) -> dict:
    return {"name": name, "input": params, "id": f"call-{name}"}


def _completion(text: str = "", calls: list[dict] | None = None, tokens: int = 0):
    return Completion(text=text, provider="fake", model="fake-1",
                      input_tokens=tokens, output_tokens=tokens,
                      tool_calls=calls or None)


class Script:
    """A model that answers from a script and measures every prompt."""

    def __init__(self, *replies):
        self.replies = list(replies)
        self.turns: list[dict] = []
        self.sizes: list[int] = []

    async def __call__(self, need, system, messages, tools=None, max_tokens=2048, **_kw):
        self.sizes.append(len(system) + sum(len(m.content) for m in messages)
                          + len(json.dumps(tools or [])))
        self.turns.append({"need": need, "system": system,
                           "messages": list(messages), "tools": tools,
                           "max_tokens": max_tokens})
        reply = self.replies.pop(0) if self.replies else _completion("Done.")
        if isinstance(reply, BaseException):
            raise reply
        if callable(reply):
            return await reply()
        return reply


def _offered(script: Script, turn: int = 0) -> set[str]:
    return {t["name"] for t in script.turns[turn]["tools"] or []}


LONG_PAGE = "lorem ipsum dolor sit amet " * 400 + " The tower is 330 metres tall."


def _fake_page(monkeypatch, text: str = LONG_PAGE) -> list[str]:
    read: list[str] = []

    async def fake_read_page(url: str):
        read.append(url)
        return {"url": url, "final_url": url, "title": "T" * 120, "text": text,
                "read_at": "2026-10-06T15:00:00+00:00",
                "sources": [{"url": url, "read_at": "2026-10-06T15:00:00+00:00"}]}

    monkeypatch.setitem(TOOLS, "read_page", Tool(
        "read_page", TOOLS["read_page"].description, TOOLS["read_page"].schema,
        read=fake_read_page))
    return read


# ------------------------------------------------------------ 1. prompt size

@pytest.mark.parametrize("question, mode", [
    ("tell me the latest Artemis mission news", "search"),
    ("summarise https://example.org/a", None),
])
async def test_no_turn_is_sent_over_the_free_tier_prompt_budget(
        monkeypatch, question, mode):
    """The worst case: six long pages read one after another, then a nudge.
    Before Phase 1 this reached 43,782 characters (~14,600 tokens) on one
    turn; Groq's free tier refuses over 8,000 tokens a minute with a 413."""
    _fake_page(monkeypatch)
    replies = [_completion(calls=[_call("read_page", url=f"https://example.org/{i}")])
               for i in range(6)]
    replies += [_completion("It has 12345 pages."), _completion("It is long.")]
    script = Script(*replies)
    monkeypatch.setattr(loop, "think", script)

    got = await loop.ask(question, mode=mode)

    assert len(got.tools_used) == 6
    assert max(script.sizes) <= loop.PROMPT_CHAR_BUDGET, script.sizes
    assert got.max_prompt_chars == max(script.sizes)
    # Under ~6k tokens with the reply, at a conservative 3 characters a token.
    assert (max(script.sizes) // loop.CHARS_PER_TOKEN
            + loop.MAX_TOKENS_TURN) < 6000
    assert all(t["max_tokens"] == loop.MAX_TOKENS_TURN for t in script.turns)


async def test_many_results_in_one_turn_share_the_budget_and_stay_wrapped(monkeypatch):
    _fake_page(monkeypatch)
    script = Script(
        _completion(calls=[_call("read_page", url=f"https://example.org/{i}")
                           for i in range(6)]),
        _completion("Read them."))
    monkeypatch.setattr(loop, "think", script)

    await loop.ask("summarise these pages", mode="search")

    assert max(script.sizes) <= loop.PROMPT_CHAR_BUDGET
    results = script.turns[1]["messages"][-1].content
    # Every result still opens and closes its wrapper: clipped, then wrapped.
    assert results.count("<untrusted_data source='tool:read_page'>") == 6
    assert results.count("</untrusted_data>") == 6


async def test_older_results_are_shown_as_a_note_but_still_ground_the_answer(
        monkeypatch):
    """Only what is SHOWN shrinks: a figure from a page read two turns ago,
    and from beyond the clip, still passes the grounding check."""
    _fake_page(monkeypatch)
    script = Script(
        _completion(calls=[_call("read_page", url="https://example.org/1")]),
        _completion(calls=[_call("calculate", expression="2 + 2")]),
        _completion("The tower is 330 metres tall."))
    monkeypatch.setattr(loop, "think", script)

    got = await loop.ask("how tall is the tower?", mode="search")

    last = script.turns[2]["messages"]
    assert any("read earlier" in m.content and "read_page" in m.content for m in last)
    assert not any("lorem ipsum" in m.content for m in last)
    assert "330" in got.text and got.unverified == ()


def test_the_model_sees_one_line_per_tool_not_the_docstrings():
    for name in assistant.MODULE.tools():
        assert len(TOOLS[name].description) <= 110, name
    schemas = [TOOLS[t].as_api_schema() for t in assistant.MODULE.tools()]
    assert len(json.dumps(schemas)) < 3500


# ------------------------------------------------------------------ 2. modes

@pytest.mark.parametrize("question, mode", [
    ("explain photosynthesis simply", modes.ASK),
    ("what is a hash map", modes.ASK),
    ("what is 17 * 23", modes.ASK),
    ("convert 5 miles to km", modes.ASK),
    ("compare TCP and UDP", modes.ASK),
    ("what's the weather in Paris", modes.SEARCH),
    ("latest news on the Artemis mission", modes.SEARCH),
    ("what is the price of an iPhone 17", modes.SEARCH),
    ("convert 100 USD to EUR", modes.SEARCH),
    ("summarise https://example.org/post", modes.SEARCH),
    ("who won the 2026 world cup", modes.SEARCH),
    ("when did the Berlin wall fall", modes.SEARCH),
    ("how tall is the Eiffel tower", modes.SEARCH),
    ("how many seconds are in 3 days", modes.ASK),
    ("how many people lived in Rome in 1900", modes.SEARCH),
    ("is it true that goldfish have a 3 second memory", modes.SEARCH),
])
def test_the_router_picks_search_only_for_fresh_or_outside_facts(question, mode):
    from atlas.modules.assistant import skills
    skill = skills.select(question).name
    assert modes.route(question, skill=skill)[0] == mode, question


async def test_a_chosen_ask_never_calls_search_or_read_page(monkeypatch):
    called: list[str] = []

    async def never(**_kw):
        called.append("outside")
        return {}

    for name in ("search_web", "read_page"):
        monkeypatch.setitem(TOOLS, name, Tool(name, "x", TOOLS[name].schema, read=never))
    script = Script(
        _completion(calls=[_call("search_web", query="photosynthesis"),
                           _call("read_page", url="https://example.org")]),
        _completion("Plants turn light into sugar."))
    monkeypatch.setattr(loop, "think", script)

    got = await loop.ask("what is the capital of Peru?", mode="ask")

    assert called == [] and got.tools_used == () and got.mode == "ask"
    assert _offered(script) == set(modes.ASK_TOOLS)
    assert "discarded" in script.turns[1]["messages"][-1].content


async def test_a_plain_general_question_is_answered_with_no_outside_tool(monkeypatch):
    script = Script(_completion("Plants use light, water and air to make sugar."))
    monkeypatch.setattr(loop, "think", script)

    got = await loop.ask("explain photosynthesis simply")

    assert got.mode == "ask" and got.tools_used == () and got.sources == ()
    assert _offered(script) <= set(modes.ASK_TOOLS)
    assert "Mode: Ask" in script.turns[0]["system"]


async def test_forced_search_offers_the_outside_reads(monkeypatch):
    script = Script(_completion("Done."))
    monkeypatch.setattr(loop, "think", script)
    got = await loop.ask("explain photosynthesis simply", mode="search")
    assert got.mode == "search" and got.mode_why == "asked for by name"
    assert {"search_web", "read_page", "wikipedia_summary"} <= _offered(script)


async def test_privacy_beats_a_forced_search(monkeypatch):
    script = Script(_completion("I can't read your mail from here."))
    monkeypatch.setattr(loop, "think", script)
    got = await loop.ask("who emailed me today?", mode="search")
    assert got.private and script.turns[0]["need"].value == "private"
    assert not _offered(script) & {"search_web", "read_page", "weather_now",
                                   "wikipedia_summary", "define_word",
                                   "convert_currency"}


async def test_a_question_about_atlas_keeps_its_own_reads_in_ask(monkeypatch):
    script = Script(_completion("Running."))
    monkeypatch.setattr(loop, "think", script)
    got = await loop.ask("how are you doing, atlas?")
    assert got.mode == "ask" and got.skill == "atlas"
    assert {"atlas_status", "atlas_module_status"} <= _offered(script)
    assert not _offered(script) & {"search_web", "read_page"}


@pytest.mark.parametrize("skill", sorted(
    __import__("atlas.modules.assistant.skills", fromlist=["SKILLS"]).SKILLS))
@pytest.mark.parametrize("mode", [modes.ASK, modes.SEARCH])
async def test_no_prompt_names_a_tool_the_model_was_not_given(monkeypatch, skill, mode):
    """Told to "check Wikipedia" with no Wikipedia tool, a free-tier model
    writes "according to Wikipedia" anyway -- no digit, no URL, nothing to
    catch it. So no prompt names a tool outside what that turn offers."""
    script = Script(_completion("Done."))
    monkeypatch.setattr(loop, "think", script)
    await loop.ask("tell me about it", skill=skill, mode=mode)
    offered = _offered(script)
    named = {t for t in assistant.MODULE.tools() if t in script.turns[0]["system"]}
    assert named <= offered, (skill, mode, named - offered)


async def test_ask_mode_cuts_a_remembered_figure_and_points_at_search(monkeypatch):
    """Invariant 6 holds in Ask: "1789" is a figure no tool read. The answer
    says how to have it read."""
    script = Script(_completion("It began in 1789 with the storming of a prison."),
                    _completion("It began in 1789 with the storming of a prison."))
    monkeypatch.setattr(loop, "think", script)

    got = await loop.ask("explain the French Revolution simply")

    assert got.mode == "ask" and got.unverified == ("1789",)
    assert "Nothing in Ask mode can read them" in script.turns[1]["messages"][-1].content
    assert "Say `search <question>`" in got.render()


# ---------------------------------------------------------------- 3. the flag

def test_the_flag_is_off_unless_set(monkeypatch):
    monkeypatch.delenv(assistant.ENABLED_ENV, raising=False)
    assert assistant.enabled() is False
    for value in ("0", "false", "no", "", "maybe"):
        monkeypatch.setenv(assistant.ENABLED_ENV, value)
        assert assistant.enabled() is False, value
    for value in ("1", "true", "YES", "on"):
        monkeypatch.setenv(assistant.ENABLED_ENV, value)
        assert assistant.enabled() is True, value
    text = (ROOT / ".env.example").read_text()
    assert "ASSISTANT_ENABLED=" in text


async def test_off_every_door_is_closed(monkeypatch):
    from atlas.api import slack_events
    monkeypatch.delenv(assistant.ENABLED_ENV, raising=False)
    monkeypatch.setenv("SLACK_ASSISTANT_CHANNEL", "C0ASK")
    assert await assistant.MODULE.command("ask what is the capital of Peru?",
                                          channel="slack", who="UMIRO") is None
    assert await assistant.MODULE.command("atlas, what is a hash map?",
                                          channel="slack", who="UMIRO") is None
    assert slack_events._assistant_channel("C0ASK") is False
    assert "off" in (await TOOLS[assistant.VOICE_TOOL].read(question="x"))["error"]


def test_off_an_ask_in_slack_takes_the_old_path(monkeypatch):
    """Exactly the old behaviour: `ask what is ...?` is a knowledge question
    as it was before the module, never the assistant."""
    from atlas.api import slack_events
    from atlas.kernel import knowledge

    monkeypatch.delenv(assistant.ENABLED_ENV, raising=False)
    monkeypatch.delenv("SLACK_ASSISTANT_CHANNEL", raising=False)
    old: list[str] = []
    replied: list = []

    async def fake_knowledge(question, **_kw):
        old.append(question)
        return "Lima, from knowledge."

    async def never(*_a, **_kw):
        raise AssertionError("the assistant is off")

    async def fake_notify(msg):
        replied.append(msg)
        return ["slack"]

    monkeypatch.setattr(knowledge, "answer", fake_knowledge)
    monkeypatch.setattr(assistant, "handle", never)
    monkeypatch.setattr("atlas.kernel.comms.notify", fake_notify)
    monkeypatch.setattr(slack_events, "_is_conversation", lambda t: False)
    monkeypatch.setattr(slack_events, "_is_knowledge_question", lambda t: True)

    asyncio.run(slack_events._request("ask what is the capital of Peru?", "UMIRO",
                                      "CRANDOM", "7.9"))

    assert old and replied[0].body == "Lima, from knowledge."


# --------------------------------------------------------------- 4. snippets

async def test_an_unopened_hit_is_an_excerpt_and_a_read_page_is_read(monkeypatch):
    from atlas.modules import _web
    _fake_page(monkeypatch, "Lima is the capital of Peru.")

    async def fake_search(query, limit=5):
        return [{"title": "Lima", "url": "https://example.org/lima", "snippet": "Lima"},
                {"title": "Peru", "url": "https://example.org/peru", "snippet": "Peru"}
                ], None

    monkeypatch.setattr(_web, "search", fake_search)
    script = Script(
        _completion(calls=[_call("search_web", query="capital of Peru")]),
        _completion(calls=[_call("read_page", url="https://example.org/lima")]),
        _completion("Lima is the capital of Peru."))
    monkeypatch.setattr(loop, "think", script)

    got = await loop.ask("what is the capital of Peru?", mode="search")

    kinds = {s.url: s.kind for s in got.sources}
    assert kinds == {"https://example.org/lima": "read",
                     "https://example.org/peru": "excerpt"}
    rendered = got.render()
    assert "- https://example.org/lima (read 15:00 UTC, 2026-10-06)" in rendered
    assert ("https://example.org/peru (search result excerpt" in rendered
            and "page not opened" in rendered)
    assert rendered.count("https://example.org/lima") == 1


# ------------------------------------------------------------------ 5. units

async def test_the_plans_unit_example_end_to_end(monkeypatch):
    """"Convert 2.5 km to metres, then divide by 40 minutes": the real
    convert_units and calculate, only the model faked; units kept."""
    script = Script(
        _completion(calls=[_call("convert_units", value=2.5, from_unit="km",
                                 to_unit="metres")]),
        _completion(calls=[_call("calculate", expression="2500 m / 40 min")]),
        _completion("2.5 km is 2,500 m; over 40 minutes that is 62.5 m/min."))
    monkeypatch.setattr(loop, "think", script)

    got = await loop.ask("Convert 2.5 km to metres, then divide by 40 minutes")

    assert got.mode == "ask"
    assert got.tools_used == ("convert_units", "calculate")
    assert got.unverified == () and "2,500 m" in got.text and "62.5 m/min" in got.text
    results = script.turns[2]["messages"][-1].content
    assert '"unit": "m/min"' in results and '"display": "62.5 m/min"' in results


@pytest.mark.parametrize("expression, display", [
    ("2500 m / 40 min", "62.5 m/min"),
    ("3 m * 2 m", "6 m^2"),
    # A power on a unit is the unit's: 2 s**2 is 2 s², not (2 s)².
    ("10 kg * 9.8 m / 2 s ** 2", "49 kg·m/s^2"),
    ("5 m / 2 s^2", "2.5 m/s^2"),
    ("3 m ** 2", "3 m^2"),
    ("(3 m) ** 2", "9 m^2"),
    ("min(3 m, 5 m)", "3 m"),
    ("1500 * 2", "3,000"),
    ("max(1, 2)", "2"),
])
def test_calculate_carries_units(expression, display):
    from atlas.modules.assistant.tools import calculate
    assert calculate(expression)["display"] == display


@pytest.mark.parametrize("expression, error", [
    ("1 km + 500 m", "convert one first"),
    ("sqrt(9 m)", "bare number"),
    ("2 ** 3 m", "exponent cannot carry a unit"),
    ("7 m % 2", "bare numbers"),
])
def test_calculate_refuses_to_mix_or_convert_units_silently(expression, error):
    from atlas.modules.assistant.tools import calculate
    assert error in calculate(expression)["error"]


def test_a_thousands_separator_in_the_answer_matches_the_bare_figure():
    from atlas.kernel.grounding import unverified_numbers
    assert unverified_numbers("2,500 m and 1,234,567 people",
                              ['{"result": 2500.0}', '{"n": 1234567}']) == []
    assert unverified_numbers("2,600 m", ['{"result": 2500.0}']) == ["2,600"]


# ------------------------------------------------------------- 6. run record

def test_the_schema_has_assistant_runs():
    schema = (ROOT / "db" / "schema.sql").read_text()
    block = schema.split("CREATE TABLE IF NOT EXISTS assistant_runs", 1)[1].split(");")[0]
    for column in ("id", "at", "question_sha", "mode", "skill", "status",
                   "tool_calls", "tokens", "provider", "model", "latency_ms", "error"):
        assert column in block, column
    for status in runs.STATUSES:
        assert f"'{status}'" in block
    assert "question " not in block.replace("question_sha", "")


async def test_each_run_is_recorded_without_its_text(monkeypatch, memory_backend):
    script = Script(_completion(calls=[_call("calculate", expression="2 + 2")],
                                tokens=5),
                    _completion("4.", tokens=5))
    monkeypatch.setattr(loop, "think", script)

    got = await loop.ask("what is 2 + 2?")

    rows = [(sql, args) for sql, args in memory_backend.sql_log
            if "assistant_runs" in sql]
    insert, update = rows[0], rows[-1]
    assert insert[0].startswith("INSERT INTO assistant_runs")
    assert insert[1] == (got.run_id, hashlib.sha256(b"what is 2 + 2?").hexdigest(),
                         "ask", got.skill, "running")
    assert "what is 2 + 2" not in json.dumps([a for _, a in rows], default=str)
    assert update[0].startswith("UPDATE assistant_runs")
    assert update[1][:6] == (got.run_id, "completed", 1, 20, "fake", "fake-1")
    assert f"run {got.run_id}" in got.render() and got.status == "completed"
    assert runs.live() == []


async def test_a_failed_record_never_costs_the_answer(monkeypatch, memory_backend):
    async def broken(*_a, **_kw):
        raise ConnectionError("database down")

    monkeypatch.setattr(runs, "execute_sql", broken)
    script = Script(_completion("Hello."))
    monkeypatch.setattr(loop, "think", script)
    got = await loop.ask("say hello")
    assert got.text == "Hello." and got.status == "completed"


async def test_cancel_stops_the_run_before_its_next_tool_call(monkeypatch,
                                                              memory_backend):
    called: list[str] = []

    async def counted(expression: str):
        called.append(expression)
        return {"result": 1}

    monkeypatch.setitem(TOOLS, "calculate", Tool(
        "calculate", "c", TOOLS["calculate"].schema, read=counted))

    async def cancel_then_call():
        await assistant.cancel(runs.live()[0].run_id, by="UMIRO")
        return _completion(calls=[_call("calculate", expression="1 + 1")])

    script = Script(cancel_then_call, _completion("never"))
    monkeypatch.setattr(loop, "think", script)

    got = await loop.ask("add one and one")

    assert called == [] and len(script.turns) == 1
    assert got.status == "cancelled" and got.text == loop.CANCELLED
    statuses = [a[1] for sql, a in memory_backend.sql_log
                if sql.startswith("UPDATE assistant_runs")]
    assert "cancelled" in statuses


async def test_a_run_cancelled_by_its_caller_is_finished_as_cancelled(
        monkeypatch, memory_backend):
    started = asyncio.Event()

    async def hang():
        started.set()
        await asyncio.sleep(3600)

    script = Script(hang)
    monkeypatch.setattr(loop, "think", script)
    task = asyncio.create_task(loop.ask("take forever"))
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    update = [a for sql, a in memory_backend.sql_log
              if sql.startswith("UPDATE assistant_runs SET status = $2, tool_calls")]
    assert update and update[-1][1] == "cancelled"
    assert runs.live() == []


async def test_cancel_names_what_it_did():
    assert "not running here" in await assistant.cancel("deadbeef")


async def test_stop_in_the_channel_stops_only_his_own_runs(memory_backend):
    mine = await runs.start("q", mode="ask", skill="answer", who="UMIRO", channel="slack")
    theirs = await runs.start("q", mode="ask", skill="answer", who="UOTHER",
                              channel="slack")
    try:
        said = await runs.cancel_mine("UMIRO", channel="slack")
        assert mine.run_id in said and mine.cancelled and not theirs.cancelled
        assert "Nothing of yours" in await runs.cancel_mine("UNOBODY")
    finally:
        for run in (mine, theirs):
            await runs.finish(run, status="cancelled", tool_calls=0, tokens=0,
                              provider="", model="", latency_ms=0)


@pytest.mark.parametrize("text, stops", [
    ("stop", True), ("Stop.", True), ("atlas, stop!", True),
    ("stop the scraper", False), ("don't stop", False)])
def test_stop_matches_the_whole_message_only(text, stops):
    assert bool(assistant.STOP.match(text)) is stops


def test_ask_cancel_and_stop_reach_the_runs_from_slack(monkeypatch):
    from atlas.api import slack_events

    monkeypatch.setenv(assistant.ENABLED_ENV, "1")
    monkeypatch.setenv("SLACK_ASSISTANT_CHANNEL", "C0ASK")
    said: list[str] = []
    cancelled: list[tuple] = []

    async def fake_cancel(run_id, *, by=""):
        cancelled.append(("one", run_id, by))
        return f"Stopping run {run_id}"

    async def fake_cancel_mine(who, *, channel=""):
        cancelled.append(("mine", who, channel))
        return "Stopping yours"

    async def fake_notify(msg):
        said.append(msg.body)
        return ["slack"]

    monkeypatch.setattr(assistant, "cancel", fake_cancel)
    monkeypatch.setattr(runs, "cancel_mine", fake_cancel_mine)
    monkeypatch.setattr("atlas.kernel.comms.notify", fake_notify)

    asyncio.run(slack_events._request("ask cancel ab12cd34", "UMIRO", "CRANDOM", "1.1"))
    asyncio.run(slack_events._request("stop", "UMIRO", "C0ASK", "1.2"))

    assert cancelled == [("one", "ab12cd34", "UMIRO"), ("mine", "UMIRO", "slack")]
    assert said == ["Stopping run ab12cd34", "Stopping yours"]


def test_search_never_steals_an_agency_job_in_slack(monkeypatch):
    """`search up ... and make them one` is the agency's mission, as before."""
    from atlas.api import slack_events

    monkeypatch.setenv(assistant.ENABLED_ENV, "1")
    monkeypatch.delenv("SLACK_ASSISTANT_CHANNEL", raising=False)
    missions_saw: list[str] = []

    async def fake_mission(text, user, ts):
        missions_saw.append(text)
        return "On it -- the agency has the job."

    async def never(*_a, **_kw):
        raise AssertionError("the assistant must not take an order")

    async def fake_notify(msg):
        return ["slack"]

    monkeypatch.setattr(slack_events, "_mission", fake_mission)
    monkeypatch.setattr(assistant, "handle", never)
    monkeypatch.setattr("atlas.kernel.comms.notify", fake_notify)

    asyncio.run(slack_events._request(
        "search up the 50 local businesses that have a very poor website or no "
        "website at all and make them one", "UMIRO", "CRANDOM", "1.3"))

    assert missions_saw
