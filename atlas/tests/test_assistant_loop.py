"""The assistant loop: a fake model that calls tools then answers, and the
rules the loop enforces around it -- a name outside the catalog is
discarded, bad parameters are refused, the call cap stops it, an unverified
figure never leaves, every result is quoted, and a private question stays on
the local model."""
from __future__ import annotations

import json

import pytest

from atlas.kernel.llm import Completion, LocalModelUnavailable, Message, Need
from atlas.kernel.tools import TOOLS, Tool
from atlas.modules.assistant import loop, modes
from atlas.modules.assistant.budget import EVENT


@pytest.fixture(autouse=True)
def _budget_readable(memory_backend):
    memory_backend.responses["FROM actions_log WHERE event = $1 AND subject = ANY"] = {
        "n": 0}
    return memory_backend


def _call(name: str, **params) -> dict:
    return {"name": name, "input": params, "id": f"call-{name}"}


def _completion(text: str = "", calls: list[dict] | None = None, tokens: int = 10):
    return Completion(text=text, provider="fake", model="fake-1",
                      input_tokens=tokens, output_tokens=tokens,
                      tool_calls=calls or None)


class Script:
    """A model that answers from a script, recording every turn."""

    def __init__(self, *replies):
        self.replies = list(replies)
        self.turns: list[dict] = []

    async def __call__(self, need, system, messages, tools=None, max_tokens=2048, **_kw):
        self.turns.append({"need": need, "system": system,
                           "messages": list(messages), "tools": tools})
        if not self.replies:
            return _completion("(no more scripted replies)")
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply


def _wrapped_tool_results(turns: list[dict]) -> list[str]:
    out = []
    for turn in turns:
        for m in turn["messages"]:
            if m.role == "user" and " -> " in m.content:
                out.append(m.content)
    return out


# ------------------------------------------------------------- the loop

async def test_a_tool_call_then_an_answer_with_the_figure_grounded(monkeypatch):
    script = Script(
        _completion(calls=[_call("calculate", expression="17 * 23")]),
        _completion("17 times 23 is 391."))
    monkeypatch.setattr(loop, "think", script)

    got = await loop.ask("what is 17 * 23?", who="UMIRO")

    assert got.text == "17 times 23 is 391."
    assert got.tools_used == ("calculate",)
    assert got.unverified == () and got.stopped is None
    # The result reached the model quoted, never bare.
    quoted = _wrapped_tool_results(script.turns)
    assert quoted and "<untrusted_data source='tool:calculate'>" in quoted[0]
    assert '"result": 391' in quoted[0]
    # The first turn carried the closed tool set and the identity.
    # Arithmetic is Ask: the calculator, units and the clock, nothing outside.
    assert {t["name"] for t in script.turns[0]["tools"]} == set(modes.ASK_TOOLS)
    assert got.mode == "ask"
    assert "ATLAS, Miro's assistant" in script.turns[0]["system"]
    assert "Never claim" in script.turns[0]["system"]
    assert script.turns[0]["need"] is Need.REASON
    rendered = got.render()
    assert "Sources: none" in rendered and "ATLAS's own tools" in rendered


async def test_a_name_outside_the_catalog_is_discarded_before_anything_happens(
        monkeypatch, memory_backend):
    ran: list[str] = []

    async def must_not_run(**_kw):
        ran.append("ran")
        return {"ok": True}

    monkeypatch.setitem(TOOLS, "run_shell", Tool("run_shell", "x", {
        "type": "object", "properties": {"cmd": {"type": "string"}}}, read=must_not_run))
    script = Script(
        _completion(calls=[_call("run_shell", cmd="rm -rf /"),
                           _call("gitlab_issue", title="x"),
                           _call("calculate", expression="1 + 1")]),
        _completion("1 + 1 is 2."))
    monkeypatch.setattr(loop, "think", script)

    got = await loop.ask("what is 1 + 1?")

    assert ran == []
    assert got.tools_used == ("calculate",)
    results = _wrapped_tool_results(script.turns)[0]
    assert "run_shell -> discarded: not a tool here" in results
    assert "gitlab_issue -> discarded: not a tool here" in results
    events = [(r.event, r.subject) for r in memory_backend.audit_rows]
    assert ("assistant.discarded", "run_shell") in events
    assert (EVENT, "run_shell") not in events


async def test_parameters_that_fail_the_schema_are_refused_not_run(monkeypatch):
    script = Script(
        _completion(calls=[_call("convert_units", value="five", from_unit="mi",
                                 to_unit="km"),
                           _call("calculate", expression="2 + 2", extra="x"),
                           _call("time_now")]),
        _completion("Done."))
    monkeypatch.setattr(loop, "think", script)

    got = await loop.ask("convert five miles")

    results = _wrapped_tool_results(script.turns)[0]
    assert "convert_units -> refused: convert_units: value must be a number" in results
    assert "calculate -> refused: calculate: undeclared parameter(s) extra" in results
    assert got.tools_used == ("time_now",)
    assert len(got.refusals) == 2


async def test_the_call_cap_stops_the_loop_and_says_so(monkeypatch):
    monkeypatch.setenv(loop.CALLS_ENV, "2")
    script = Script(
        _completion(calls=[_call("calculate", expression="1 + 1"),
                           _call("calculate", expression="2 + 2"),
                           _call("calculate", expression="3 + 3")]),
        _completion("1 + 1 is 2 and 2 + 2 is 4."))
    monkeypatch.setattr(loop, "think", script)

    got = await loop.ask("add some numbers")

    assert got.tools_used == ("calculate", "calculate")
    assert got.stopped and "limit of 2 tool calls" in got.stopped
    results = _wrapped_tool_results(script.turns)[0]
    assert "refused: the limit of 2 tool calls" in results
    assert "stopped" in got.render()


async def test_a_model_that_never_stops_calling_tools_is_bounded_in_turns(monkeypatch):
    """Past the call cap every call is refused; a model that keeps asking
    anyway -- on a provider that reports no tokens -- ends after a bounded
    number of turns, not at the wall-clock deadline."""
    monkeypatch.setenv(loop.CALLS_ENV, "1")
    endless = [_completion(calls=[_call("calculate", expression="1 + 1")], tokens=0)
               for _ in range(50)]
    script = Script(*endless)
    monkeypatch.setattr(loop, "think", script)

    got = await loop.ask("keep adding")

    assert len(script.turns) == loop.max_calls() + 2
    assert got.tools_used == ("calculate",)
    assert got.stopped and "tool calls" in got.stopped
    assert got.text == "I could not finish answering that."


async def test_atlas_own_words_are_never_cut_for_their_figures(monkeypatch):
    """`local_down` names the model ("llama3.2:3b"); that is ATLAS's line,
    not a guess, and must leave whole."""
    script = Script(LocalModelUnavailable("llama3.2:3b: connection refused"))
    monkeypatch.setattr(loop, "think", script)
    got = await loop.ask("what is in my inbox?")
    assert "llama3.2:3b" in got.text and got.unverified == ()


async def test_an_unverified_number_gets_one_nudge_then_is_cut(monkeypatch):
    script = Script(
        _completion("The Eiffel Tower is 330 m tall. It opened in 1889."),
        _completion("The Eiffel Tower is 330 m tall. It is in Paris."))
    monkeypatch.setattr(loop, "think", script)

    got = await loop.ask("how tall is the Eiffel Tower?")

    # Turn two carried the nudge naming the figures.
    nudge = script.turns[1]["messages"][-1]
    assert nudge.role == "user" and "330" in nudge.content and "1889" in nudge.content
    assert got.unverified == ("330",)
    assert "330" not in got.text and "It is in Paris." in got.text
    assert loop.CUT_LINE.format(numbers="330") in got.render()


async def test_a_figure_a_tool_returned_passes_and_a_rounding_of_it_too(monkeypatch):
    async def fake_weather(place: str):
        return {"place": place, "temperature_c": 18.3, "wind_kmh": 12.4,
                "sources": [{"url": "https://api.open-meteo.com/v1/forecast?x",
                             "read_at": "2026-10-06T15:00:00+00:00"}]}

    monkeypatch.setitem(TOOLS, "weather_now", Tool(
        "weather_now", "w", {"type": "object", "properties": {"place": {"type": "string"}},
                             "required": ["place"]}, read=fake_weather))
    script = Script(
        _completion(calls=[_call("weather_now", place="Paris")]),
        _completion("About 18 degrees in Paris, wind 12.4 km/h."))
    monkeypatch.setattr(loop, "think", script)

    got = await loop.ask("what's the weather in Paris?")

    assert got.unverified == ()
    assert got.sources == (loop.Source("https://api.open-meteo.com/v1/forecast?x",
                                       "2026-10-06T15:00:00+00:00"),)
    rendered = got.render()
    assert "Sources:" in rendered
    assert "- https://api.open-meteo.com/v1/forecast?x (read 15:00 UTC, 2026-10-06)" in rendered


async def test_a_url_the_model_wrote_that_no_tool_returned_is_cut(monkeypatch):
    script = Script(
        _completion(calls=[_call("time_now", timezone="UTC")]),
        _completion("It is daytime. See https://evil.example/phish for more."))
    monkeypatch.setattr(loop, "think", script)

    got = await loop.ask("is it daytime?")

    assert "evil.example" not in got.text and "It is daytime." in got.text


async def test_the_question_is_wrapped_as_the_principals_words(monkeypatch):
    script = Script(_completion("Yes."))
    monkeypatch.setattr(loop, "think", script)
    await loop.ask("is water wet?", channel="slack")
    first = script.turns[0]["messages"][0]
    assert first.content.startswith("<request source='slack'>")
    assert "is water wet?" in first.content


# ----------------------------------------------------------------- budgets

async def test_a_spent_daily_cap_refuses_the_outside_read(monkeypatch, memory_backend):
    memory_backend.responses["FROM actions_log WHERE event = $1 AND subject = ANY"] = {
        "n": 40}

    async def must_not_run(**_kw):
        raise AssertionError("search must not run past the cap")

    monkeypatch.setitem(TOOLS, "search_web", Tool("search_web", "s", {
        "type": "object", "properties": {"query": {"type": "string"}},
        "required": ["query"]}, read=must_not_run))
    script = Script(
        _completion(calls=[_call("search_web", query="capital of Peru")]),
        _completion("I could not search."))
    monkeypatch.setattr(loop, "think", script)

    got = await loop.ask("what is the capital of Peru?", mode="search")

    results = _wrapped_tool_results(script.turns)[0]
    assert "search_web -> refused: search_web: today's cap of 40" in results
    assert got.tools_used == ()


async def test_an_unreadable_budget_spends_nothing(monkeypatch, memory_backend):
    memory_backend.responses.clear()           # the count query now raises

    async def must_not_run(**_kw):
        raise AssertionError("read_page must not run on an unreadable budget")

    monkeypatch.setitem(TOOLS, "read_page", Tool("read_page", "r", {
        "type": "object", "properties": {"url": {"type": "string"}},
        "required": ["url"]}, read=must_not_run))
    script = Script(
        _completion(calls=[_call("read_page", url="https://example.org")]),
        _completion("Could not read it."))
    monkeypatch.setattr(loop, "think", script)

    got = await loop.ask("summarise https://example.org")

    assert "daily budget could not be read" in _wrapped_tool_results(script.turns)[0]
    assert got.tools_used == ()


async def test_the_token_budget_stops_the_loop(monkeypatch):
    monkeypatch.setenv(loop.TOKENS_ENV, "2000")
    script = Script(
        _completion(calls=[_call("calculate", expression="1 + 1")], tokens=600),
        _completion(calls=[_call("calculate", expression="2 + 2")], tokens=600),
        _completion("never reached"))
    monkeypatch.setattr(loop, "think", script)

    got = await loop.ask("add")

    assert got.stopped and "token budget" in got.stopped
    assert got.tools_used == ("calculate",)


# ------------------------------------------------------------------- audit

async def test_every_tool_call_is_audited_by_name_without_its_content(
        monkeypatch, memory_backend):
    script = Script(
        _completion(calls=[_call("calculate", expression="40 + 2")]),
        _completion("42."))
    monkeypatch.setattr(loop, "think", script)

    await loop.ask("what is 40 + 2?", who="UMIRO")

    rows = [r for r in memory_backend.audit_rows if r.event == EVENT]
    assert len(rows) == 1 and rows[0].subject == "calculate"
    data = json.loads(rows[0].payload)["data"]
    assert data["params"] == ["expression"] and data["who"] == "UMIRO"
    assert "40 + 2" not in rows[0].payload
    events = [r.event for r in memory_backend.audit_rows]
    assert events[0] == "assistant.ask" and events[-1] == "assistant.answered"


# ----------------------------------------------------------------- private

async def test_a_private_question_runs_on_the_local_model_with_local_tools_only(
        monkeypatch):
    script = Script(_completion("I cannot read your inbox from here."))
    monkeypatch.setattr(loop, "think", script)

    got = await loop.ask("who emailed me this morning?")

    assert got.private is True
    assert script.turns[0]["need"] is Need.PRIVATE
    offered = {t["name"] for t in script.turns[0]["tools"]}
    assert offered == set(loop.LOCAL_TOOLS)
    assert not offered & {"search_web", "read_page", "weather_now", "wikipedia_summary",
                          "define_word", "convert_currency"}
    assert "local model" in script.turns[0]["system"]
    assert "nothing left the box" in got.render()


async def test_no_local_model_is_an_honest_refusal_never_a_cloud_call(monkeypatch):
    script = Script(LocalModelUnavailable("no local model configured"))
    monkeypatch.setattr(loop, "think", script)

    got = await loop.ask("what is in my calendar tomorrow?")

    assert got.private and got.stopped == "the local model was unavailable"
    assert got.text.startswith("The local model is down")
    assert len(script.turns) == 1


async def test_a_model_chain_failure_is_said_not_raised(monkeypatch):
    script = Script(RuntimeError("all providers failed for reason"))
    monkeypatch.setattr(loop, "think", script)
    got = await loop.ask("why is the sky blue?")
    assert got.stopped and "no model answered" in got.stopped
    assert got.text and "Sources" not in got.render().split("\n")[0]


async def test_an_empty_question_asks_for_one():
    assert (await loop.ask("   ")).text == "You didn't ask anything."


def test_messages_are_the_kernels_shape():
    assert Message("user", "x").role == "user"
