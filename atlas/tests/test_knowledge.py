"""Knowledge mode: an expert answer for a question that is not about ATLAS's
own systems, and never routed to a specialist -- and never mistaken for one
just because a real question happens to share a word with a business name."""
from __future__ import annotations

import asyncio
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

from atlas.api import auth, voice
from atlas.kernel import knowledge
from atlas.modules.vault import library

SECRET = "v" * 40
PASSWORD = "a-long-enough-password"


@pytest.fixture(autouse=True)
def _no_library_write(monkeypatch):
    """Every test in this module drives `knowledge.answer()`, which now also
    keeps the answer in the library -- a real DB/network write that has no
    place in a test that doesn't ask for one. Silenced by default; tests
    that care about the write override it explicitly."""
    async def noop(*_a, **_kw):
        return None
    monkeypatch.setattr(library, "keep_answer", noop)


@pytest.fixture
def client(monkeypatch, memory_backend):
    monkeypatch.setenv("SESSION_SECRET", SECRET)
    monkeypatch.setenv("DASHBOARD_PASSWORD_HASH", auth.hash_password(PASSWORD))
    monkeypatch.setenv("MESH_ALLOWED_CIDRS", "127.0.0.0/8")
    auth._attempts.clear()
    from atlas.api.main import app

    async def no_boot() -> None:
        return None
    monkeypatch.setattr("atlas.api.main.boot", no_boot)
    monkeypatch.setattr("atlas.api.main.scheduler.loop", no_boot)

    with TestClient(app, client=("127.0.0.1", 52000)) as c:
        c.post("/api/login", json={"password": PASSWORD})
        yield c


# --------------------------------------------------------- knowledge.answer

async def test_answer_calls_think_with_the_expert_system_and_no_tools(monkeypatch):
    calls: list[dict] = []

    class FakeCompletion:
        text = "The knee is a hinge joint. More: it is stabilised by..."

    async def fake_think(need, system, messages, tools=None, max_tokens=2048):
        calls.append({"need": need, "system": system, "messages": messages,
                      "tools": tools, "max_tokens": max_tokens})
        return FakeCompletion()

    monkeypatch.setattr(knowledge, "think", fake_think)

    result = await knowledge.answer("what is the anatomy of the knee")

    assert result == FakeCompletion.text
    assert len(calls) == 1
    call = calls[0]
    assert call["need"] == knowledge.Need.HARD
    assert call["system"] == knowledge.EXPERT_SYSTEM
    assert call["tools"] is None
    assert call["max_tokens"] == 700
    assert len(call["messages"]) == 1
    message = call["messages"][0]
    assert message.role == "user"
    # Wrapped as untrusted, but as a principal: the words ARE the question,
    # not a quote picked up from somewhere else.
    assert "what is the anatomy of the knee" in message.content
    assert "voice" in message.content


# --------------------------------------------------- keeping the answer

async def test_answer_keeps_itself_in_the_library_with_source_model(monkeypatch):
    """Without Perplexity, the library entry's source names what actually
    answered -- the model alone -- and its notes are exactly what was said."""
    kept: list[tuple] = []

    class FakeCompletion:
        text = "The knee is a hinge joint."

    async def fake_think(need, system, messages, tools=None, max_tokens=2048):
        return FakeCompletion()

    async def fake_keep_answer(question, answer, source):
        kept.append((question, answer, source))

    monkeypatch.delenv("PERPLEXITY_API_KEY", raising=False)
    monkeypatch.setattr(knowledge, "think", fake_think)
    monkeypatch.setattr(library, "keep_answer", fake_keep_answer)

    result = await knowledge.answer("what is the anatomy of the knee")

    assert result == FakeCompletion.text
    assert kept == [("what is the anatomy of the knee", FakeCompletion.text, "model")]


async def test_answer_keeps_itself_with_perplexitys_citations_as_source(monkeypatch):
    """With Perplexity, the library entry's source is its citation URLs --
    not "model", and not the human-readable "Sources:" line -- and its notes
    are the composed answer the caller actually got back, sources block and
    all."""
    from atlas.modules import _perplexity

    kept: list[tuple] = []
    found = _perplexity.Answer(
        tool="perplexity_reason", text="a virus needs a host cell",
        citations=("https://example.org/a", "https://example.org/b"),
        read_at=datetime(2026, 9, 25, 16, 10, tzinfo=UTC), via="mcp")

    async def fake_call(tool, question, *, caller, timeout, deadline):
        return found, None

    class FakeCompletion:
        text = "A virus needs a host cell; a bacterium is a living cell."

    async def fake_think(need, system, messages, tools=None, max_tokens=2048):
        return FakeCompletion()

    async def fake_keep_answer(question, answer, source):
        kept.append((question, answer, source))

    monkeypatch.setenv("PERPLEXITY_API_KEY", "pk-test")
    monkeypatch.setattr(_perplexity, "call", fake_call)
    monkeypatch.setattr(knowledge, "think", fake_think)
    monkeypatch.setattr(library, "keep_answer", fake_keep_answer)

    result = await knowledge.answer(
        "what is the difference between a virus and a bacterium")

    assert "Sources" in result
    assert kept == [("what is the difference between a virus and a bacterium",
                     result, "https://example.org/a; https://example.org/b")]


async def test_answer_uses_model_as_source_when_perplexity_gives_no_citations(
        monkeypatch):
    from atlas.modules import _perplexity

    kept: list[tuple] = []
    found = _perplexity.Answer(
        tool="perplexity_reason", text="a virus needs a host cell",
        citations=(), read_at=datetime(2026, 9, 25, 16, 10, tzinfo=UTC),
        via="mcp")

    async def fake_call(tool, question, *, caller, timeout, deadline):
        return found, None

    class FakeCompletion:
        text = "answer text"

    async def fake_think(need, system, messages, tools=None, max_tokens=2048):
        return FakeCompletion()

    async def fake_keep_answer(question, answer, source):
        kept.append((question, answer, source))

    monkeypatch.setenv("PERPLEXITY_API_KEY", "pk-test")
    monkeypatch.setattr(_perplexity, "call", fake_call)
    monkeypatch.setattr(knowledge, "think", fake_think)
    monkeypatch.setattr(library, "keep_answer", fake_keep_answer)

    await knowledge.answer("what is a virus")

    assert kept[0][2] == "perplexity"


async def test_answer_never_stalls_when_the_library_write_hangs(monkeypatch):
    """An exhausted connection pool has no timeout of its own -- the library
    write is bounded on its own leash so a stalled write can only ever cost
    LIBRARY_WRITE_TIMEOUT, never hang the answer indefinitely."""
    class FakeCompletion:
        text = "an answer"

    async def fake_think(need, system, messages, tools=None, max_tokens=2048):
        return FakeCompletion()

    async def hangs_forever(question, answer, source):
        await asyncio.sleep(3600)

    monkeypatch.delenv("PERPLEXITY_API_KEY", raising=False)
    monkeypatch.setattr(knowledge, "LIBRARY_WRITE_TIMEOUT", 0.05)
    monkeypatch.setattr(knowledge, "think", fake_think)
    monkeypatch.setattr(library, "keep_answer", hangs_forever)

    result = await asyncio.wait_for(
        knowledge.answer("what is the anatomy of the knee"), timeout=2.0)

    assert result == FakeCompletion.text


# ------------------------------------------------------- voice classification

async def test_a_general_knowledge_question_never_reaches_a_specialist(
        client, monkeypatch):
    """A real general-knowledge question is answered directly by knowledge
    mode and never routed -- route() is a sentinel that raises if called."""

    async def fake_knowledge_answer(question, **_kw):
        assert question == "what is the anatomy of the knee"
        return "The knee is a hinge joint between the femur and tibia."

    async def unreachable(*_a, **_kw):
        raise AssertionError("route() must not be called for a knowledge question")

    monkeypatch.setattr(knowledge, "answer", fake_knowledge_answer)
    monkeypatch.setattr("atlas.kernel.agents.route", unreachable)

    body = client.post(
        "/api/voice/ask",
        json={"transcript": "what is the anatomy of the knee"}).json()

    assert body["action_request"] is False
    assert body["proposal"] is None
    assert body["answer"] == "The knee is a hinge joint between the femur and tibia."


async def test_a_question_naming_a_business_is_not_knowledge(client, monkeypatch):
    """'CloudHost' is one of ATLAS's own businesses. Even phrased as a general
    question, a transcript that names it must not be guessed at by a
    tool-less model -- it has to reach a specialist who can tell whether the
    module was actually meant, the same way any other estate question does."""
    from atlas.kernel.agents import build_roster

    build_roster()   # idempotent; makes sure "agency" is a known name

    async def fake_route(question, *_a, **_kw):
        assert "cannot act from voice" not in question
        return "CloudHost bills by the hour, capped at the monthly price."

    async def unreachable(_question):
        raise AssertionError("knowledge.answer() must not be called")

    monkeypatch.setattr("atlas.kernel.agents.route", fake_route)
    monkeypatch.setattr(knowledge, "answer", unreachable)

    body = client.post(
        "/api/voice/ask",
        json={"transcript": "explain how agency pricing works"},
    ).json()

    assert body["action_request"] is False
    assert body["answer"] == "CloudHost bills by the hour, capped at the monthly price."


@pytest.mark.parametrize("said", [
    "what is broken",
    "what is waiting on me",
    "what is my revenue this month",
])
async def test_a_question_about_his_own_estate_is_never_treated_as_knowledge(
        client, monkeypatch, said):
    """These all match a KNOWLEDGE_CUES phrase, and some name nothing in the
    registry at all -- but every one of them is asking about HIS systems,
    not the world's knowledge. Knowledge mode has no tools, so sending any
    of these there would be a model inventing a status or a number it has no
    way to know -- exactly what invariant 6 forbids. They must all still
    reach a specialist."""
    from atlas.kernel.agents import build_roster

    build_roster()   # idempotent; makes sure "flycheap" is a known name

    async def fake_route(question, *_a, **_kw):
        return "Four checks could not run."

    async def unreachable(_question):
        raise AssertionError("knowledge.answer() must not be called")

    monkeypatch.setattr("atlas.kernel.agents.route", fake_route)
    monkeypatch.setattr(knowledge, "answer", unreachable)

    body = client.post("/api/voice/ask", json={"transcript": said}).json()

    assert body["action_request"] is False
    assert body["answer"] == "Four checks could not run."


async def test_an_action_request_is_never_treated_as_knowledge(client, monkeypatch):
    """'Explain' is a knowledge cue, but a request to DO something wins --
    same rule as circumstances, checked first."""
    async def fake_route(question, *_a, **_kw):
        assert "cannot act from voice" in question
        return "I would restart the flycheap scraper."

    async def no_match(_transcript, **_kw):
        return None

    async def unreachable(_question):
        raise AssertionError("knowledge.answer() must not be called")

    monkeypatch.setattr("atlas.kernel.agents.route", fake_route)
    monkeypatch.setattr(voice, "_pick_action", no_match)
    monkeypatch.setattr(knowledge, "answer", unreachable)
    monkeypatch.setattr("atlas.kernel.comms.notify", lambda *_a, **_k: _noop())

    body = client.post(
        "/api/voice/ask",
        json={"transcript": "explain how to restart the flycheap scraper"},
    ).json()

    assert body["action_request"] is True


@pytest.mark.parametrize("said", [
    "what is the anatomy of the knee",
    "how does a jet engine work",
    "why do we dream",
    "explain how vaccines work",
    "what is the difference between a virus and a bacteria",
    "what are the symptoms of a concussion",
    "what is the history of the roman empire",
    "how to tie a bowline knot",
])
def test_knowledge_cues_are_recognised(said):
    assert voice.KNOWLEDGE_CUES.search(said), said


# ------------------------------------------------- fix round 1 (task-28-review)

@pytest.mark.parametrize("said", [
    "how does the calendar look",
    "explain how gitlab is doing",
    "how does tiktok look right now",
])
async def test_a_disabled_modules_name_still_counts_as_naming_atlas(
        client, monkeypatch, said):
    """`calendar`, `gitlab` and `tiktok` are real ATLAS modules that this
    test environment never configures, so `REGISTRY.modules` never holds
    them. A module missing its `requires_env` is disabled, not gone
    (invariant 7) -- `_known_names()` reads the module package directory
    itself so a disabled module's name still counts, and a question naming
    one still reaches a specialist rather than a tool-less model guessing
    about ATLAS's own infrastructure."""
    async def fake_route(question, *_a, **_kw):
        return "That module isn't configured yet."

    async def unreachable(_question):
        raise AssertionError("knowledge.answer() must not be called")

    monkeypatch.setattr("atlas.kernel.agents.route", fake_route)
    monkeypatch.setattr(knowledge, "answer", unreachable)

    body = client.post("/api/voice/ask", json={"transcript": said}).json()

    assert body["action_request"] is False
    assert body["answer"] == "That module isn't configured yet."


@pytest.mark.parametrize("said, expected", [
    # Real knowledge -- must not be swallowed by a bare check name (finding
    # 2) or a bare estate-vocabulary word (finding 3).
    ("what is the anatomy of the knee", True),
    ("how does public key cryptography work", True),          # flycheap.public
    ("why do computers need a database to store records", True),  # *.database
    ("explain how a web session works", True),                 # fiverr.session
    ("explain how an operating system schedules jobs", True),  # nightshift.jobs
    ("how does an index in a book work", True),                # vault.index
    ("how does a waiting list work for organ transplants", True),
    ("explain how credit card balance transfers work", True),
    ("what is a proposal in parliamentary procedure", True),
    ("what is the weather today", True),
    ("why do alert systems like tornado sirens work", True),
    # Still his own estate -- must not become knowledge.
    ("is the flycheap database ok", False),
    ("what is broken", False),
    ("what's broken", False),
    ("what is waiting on me", False),
    ("what's waiting on me", False),
    ("is anything down", False),
    ("what is my revenue this month", False),
])
def test_is_knowledge_classification(said, expected):
    from atlas.kernel.agents import build_roster

    build_roster()   # idempotent; makes sure business names are known
    assert voice.is_knowledge(said) is expected, said


async def _noop():
    return []


async def test_the_spoken_path_asks_the_quick_tool_on_a_short_leash(monkeypatch):
    """Out loud, 25 s of silence reads as frozen (2026-09-25, the AI Core
    stage sat on THINKING while perplexity_reason timed out). Spoken
    questions use perplexity_ask with a 12 s leash; Slack keeps reasoning."""
    from atlas.modules import _perplexity
    asked: list[tuple[str, float]] = []

    async def fake_call(tool, question, *, caller, timeout, deadline):
        asked.append((tool, timeout))
        return None, "not now"

    async def fake_think(need, system, messages, tools=None, max_tokens=2048):
        class C:
            text = "answer"
        return C()

    monkeypatch.setattr(_perplexity, "configured", lambda: True)
    monkeypatch.setattr(_perplexity, "call", fake_call)
    monkeypatch.setattr(knowledge, "think", fake_think)
    await knowledge.answer("what is the latest news on knee surgery", quick=True)
    await knowledge.answer("what is the anatomy of the knee")
    assert asked == [("perplexity_ask", knowledge.QUICK_TIMEOUT),
                     ("perplexity_reason", knowledge.PERPLEXITY_TIMEOUT)]
    assert knowledge.QUICK_TIMEOUT <= 12.0


async def test_the_voice_knowledge_branch_keeps_the_answer_in_the_library(
        client, monkeypatch):
    """Driven end to end through the real /api/voice/ask route and the real
    `knowledge.answer()` -- only `think` and Perplexity are faked -- so this
    proves the voice caller actually reaches the library write, not just
    that `knowledge.answer()` can."""
    kept: list[tuple] = []

    class FakeCompletion:
        text = "The knee is a hinge joint."

    async def fake_think(need, system, messages, tools=None, max_tokens=2048):
        return FakeCompletion()

    async def fake_keep_answer(question, answer, source):
        kept.append((question, answer, source))

    monkeypatch.delenv("PERPLEXITY_API_KEY", raising=False)
    monkeypatch.setattr(knowledge, "think", fake_think)
    monkeypatch.setattr(library, "keep_answer", fake_keep_answer)

    body = client.post(
        "/api/voice/ask",
        json={"transcript": "what is the anatomy of the knee"}).json()

    assert body["answer"] == FakeCompletion.text
    assert kept == [("what is the anatomy of the knee", FakeCompletion.text, "model")]


async def test_the_voice_route_asks_for_the_quick_answer(client, monkeypatch):
    seen: list[bool] = []

    async def fake_answer(question, *, quick=False):
        seen.append(quick)
        return "The knee is a hinge joint."

    monkeypatch.setattr(knowledge, "answer", fake_answer)
    body = client.post("/api/voice/ask",
                       json={"transcript": "what is the anatomy of the knee"}).json()
    assert body["answer"].startswith("The knee") and seen == [True]


@pytest.mark.parametrize("question, fresh", [
    ("what's up?", False),
    ("what is photosynthesis", False),
    ("how tall is the eiffel tower", False),
    ("what's the weather today", True),
    ("latest news on the election", True),
    ("bitcoin price right now", True),
    ("who won the game yesterday", True),
])
def test_only_a_question_about_something_current_is_searched(question, fresh):
    """Miro, 2026-10-06: quick spoken questions answer from the fast model;
    only something that changes is looked up."""
    from atlas.kernel import knowledge
    assert knowledge.needs_fresh_facts(question) is fresh


async def test_a_quick_timeless_question_never_waits_on_the_search(monkeypatch):
    from atlas.kernel import knowledge, llm

    async def search(*_a, **_k):
        raise AssertionError("a timeless spoken question is not searched")

    async def think(need, system, messages, max_tokens=0, **_k):
        return llm.Completion(text="Plants turn light into sugar.", provider="t", model="m")

    async def keep(*_a, **_k):
        return None
    monkeypatch.setattr(knowledge, "_from_perplexity", search)
    monkeypatch.setattr(knowledge, "think", think)
    monkeypatch.setattr(knowledge, "_keep", keep)
    assert await knowledge.answer("what is photosynthesis", quick=True) == \
        "Plants turn light into sugar."
