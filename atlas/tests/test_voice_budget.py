"""A voice turn is small, marked as Miro's, and never ends in silence.

Miro, 2026-10-06: "ATLAS has nothing to respond with when talking with
him; make sure one of the free tiers is talking to me always and never
stopping." The fast answer's prompt carried every check (~16,300
characters for the 142 registered here), the free daily allowances were
spent by background work, and when no model answered the turn was typed
and never said.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import httpx
import pytest
from fastapi.testclient import TestClient

from atlas.api import auth, voice
from atlas.kernel import llm, quota, spoken
from atlas.kernel.llm import Completion, Need

SECRET = "v" * 40
PASSWORD = "a-long-enough-password"

# The pinned size of a fast voice turn's system prompt, whatever the board
# holds. ~4 characters a token (an estimate): about 750 tokens at most.
VOICE_PROMPT_MAX_CHARS = 3000


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


def _board(monkeypatch, n: int, *, failing_every: int = 3) -> None:
    fresh = datetime.now(UTC) - timedelta(minutes=1)
    rows = [{"module": f"module_{i:03d}", "check": f"a_long_check_name_{i:03d}",
             "interval_s": 300,
             "health": "failing" if i % failing_every == 0 else "ok",
             "detail": "x" * 400, "at": fresh} for i in range(n)]
    rows.append({"module": "cloudhost", "check": "balance", "interval_s": 300,
                 "health": "ok", "detail": "0.6 months of runway", "at": fresh})
    rows.append({"module": "cloudhost", "check": "instances", "interval_s": 300,
                 "health": "ok", "detail": None, "at": None})

    async def fetch_all(sql, *a):
        if "FROM proposals" in sql:
            return [{"action": "gitlab.open_issue"}]
        return rows
    monkeypatch.setattr(voice, "fetch_all", fetch_all)


# ------------------------------------------------------------ small turns

@pytest.mark.parametrize("checks", [10, 142, 400])
async def test_a_voice_prompt_stays_small_however_big_the_board(monkeypatch, checks):
    _board(monkeypatch, checks)
    system = await voice.voice_system("how is cloudhost looking")
    assert len(system) <= VOICE_PROMPT_MAX_CHARS, len(system)


async def test_the_small_board_keeps_what_the_question_needs(monkeypatch):
    _board(monkeypatch, 142)
    system = await voice.voice_system("how is vipsy doing")      # as heard
    # Every row of the module he named, said as heard, with its read time.
    assert "cloudhost.balance: ok — 0.6 months of runway (read " in system
    # Never measured is unknown, never fine (invariant 7).
    assert "cloudhost.instances: unknown — never measured" in system
    # The count says what is not shown, and the rest is not passed off as fine.
    assert "144 checks: 95 fine, 1 unknown, 48 not fine" in system
    assert "more not fine" in system
    # Details are data, not instructions (invariant 4), and the style holds.
    assert "<untrusted_data source='checks'>" in system
    assert voice.VOICE_STYLE in system and "as of 7:52" in system
    # The improve line only when he asks to improve something.
    assert voice.IMPROVE_LINE not in system
    assert voice.IMPROVE_LINE in await voice.voice_system("improve the vault")


async def test_an_unreadable_board_says_so(monkeypatch):
    async def broken(sql, *a):
        raise ConnectionError("db down")
    monkeypatch.setattr(voice, "fetch_all", broken)
    assert "unreachable" in await voice.voice_context("how is cloudhost")


async def test_the_fast_answer_is_small_on_the_voice_lane_and_miros(client, monkeypatch):
    _board(monkeypatch, 142)
    seen: dict = {}

    async def think(need, system, messages, **kw):
        seen.update(need=need, system=system, kw=kw,
                    interactive=quota.is_interactive())
        return Completion(text="CloudHost's fine, as of 7:52.", provider="groq",
                          model="llama-3.1-8b-instant")
    monkeypatch.setattr("atlas.kernel.llm.think", think)
    # ATLAS answers this one itself, not the agent that owns CloudHost.
    monkeypatch.setattr("atlas.kernel.missions.voice_agent", lambda _t: None)
    body = client.post("/api/voice/ask",
                       json={"transcript": "how is cloudhost looking"}).json()
    assert body["answer"] == "CloudHost's fine, as of 7:52."
    assert seen["need"] == Need.TRIVIAL
    assert len(seen["system"]) <= VOICE_PROMPT_MAX_CHARS
    assert seen["kw"]["max_tokens"] == voice.VOICE_MAX_TOKENS
    assert seen["interactive"] is True                 # may spend Miro's reserve


# ------------------------------------------------------------ never silent

def _spent_groq() -> llm.GroqProvider:
    """A deployment Groq said is out of tokens, back at a stated time."""
    p = llm.GroqProvider("openai/gpt-oss-20b")
    quota.observe(p, httpx.Response(429, text=(
        "Rate limit reached for model `openai/gpt-oss-20b` on tokens per day "
        "(TPD): Limit 200000, Used 199990, Requested 900. Please try again in "
        "1h0m0s."), request=httpx.Request("POST", "https://api.groq.com")))
    return p


async def test_out_of_allowance_says_so_with_the_providers_reset_time(
        client, monkeypatch):
    spent = _spent_groq()
    llm.ROUTES[Need.TRIVIAL] = [spent]
    reset = quota.next_reset([spent])
    assert reset is not None
    asked_agent: list[str] = []

    async def no_answer(*_a, **_k):
        raise RuntimeError("all providers failed for trivial")

    async def route(question, *_a, **_k):
        asked_agent.append(question)
        return "should not be asked"

    monkeypatch.setattr("atlas.kernel.llm.think", no_answer)
    monkeypatch.setattr("atlas.kernel.agents.route", route)
    body = client.post("/api/voice/ask",
                       json={"transcript": "how is cloudhost looking"}).json()
    assert body["answer"] is None
    assert "used today's allowance" in body["error"]
    assert reset.strftime("%H:%M") in body["error"]     # the provider's time
    assert body["spoken"] == body["error"]              # said, not only typed
    # The same spent pools are not asked again through the agent.
    assert asked_agent == []


def test_an_unknown_reset_is_later_today_never_a_guess(monkeypatch):
    p = llm.GroqProvider("openai/gpt-oss-20b")
    llm.ROUTES[Need.TRIVIAL] = [p]
    request = httpx.Request("POST", "https://api.groq.com")
    rationed = httpx.HTTPStatusError(
        "429", request=request, response=httpx.Response(429, request=request))
    try:
        raise RuntimeError("all providers failed") from rationed
    except RuntimeError as exc:
        line = voice.no_model_line(exc)
    assert "later today" in line
    assert not any(ch.isdigit() for ch in line)


def test_an_outage_is_not_called_an_allowance():
    llm.ROUTES[Need.TRIVIAL] = [llm.GroqProvider("openai/gpt-oss-20b")]
    assert voice.no_model_line(RuntimeError("boom")) == voice.NO_MODEL_DOWN


@pytest.mark.parametrize("line", [voice.NO_MODEL_DOWN,
                                  voice.NO_MODEL_SPENT.format(when="they reset later today"),
                                  voice.NO_MODEL_SPENT.format(
                                      when="the first one comes back at 14:05")])
def test_the_honest_lines_are_brief(line):
    from tests.test_voice_brevity import assert_brief
    assert_brief(line)


async def test_a_mac_command_runs_with_no_model_at_all(client, monkeypatch):
    seen: dict = {}

    async def no_answer(*_a, **_k):
        raise RuntimeError("all providers failed for trivial")

    async def run(transcript, **kw):
        seen.update(transcript=transcript, **kw)
        return spoken.Heard(True, "Opened Notes.")

    monkeypatch.setattr("atlas.kernel.llm.think", no_answer)
    monkeypatch.setattr("atlas.kernel.agents.route", no_answer)
    monkeypatch.setattr(spoken, "run_spoken_commands", run)
    body = client.post("/api/voice/ask", json={"transcript": "open Notes app"}).json()
    assert body["answer"] == "Opened Notes."
    assert body["kind"] == "mac_command"
    assert seen["origin"] == spoken.DASHBOARD


async def test_the_phone_line_hears_the_honest_line_not_an_exception(monkeypatch):
    async def board(_transcript):
        return "LIVE STATUS"

    async def no_answer(*_a, **_k):
        raise RuntimeError("all providers failed for trivial")

    async def nothing(*_a, **_k):
        return None

    from atlas.kernel import improve, missions, workboard
    monkeypatch.setattr(voice, "voice_context", board)
    monkeypatch.setattr(workboard, "context_text", lambda *_a, **_k: _str(""))
    monkeypatch.setattr(improve, "handle", nothing)
    monkeypatch.setattr(missions, "take", nothing)
    monkeypatch.setattr("atlas.kernel.llm.think", no_answer)
    said, cards = await voice.spoken_turn("how is cloudhost", source="phone")
    assert said == voice.NO_MODEL_DOWN and cards == []


async def _str(value: str) -> str:
    return value


async def test_the_spoken_chain_records_where_it_was_said():
    seen: list = []

    async def execute(request):
        seen.append(request)
        from atlas.kernel.contracts import ActionResult
        return ActionResult(True, "ok")

    class Step:
        action, params, said = "mac.open_app", {"app": "Notes"}, "open Notes"

    monkey = spoken.speakable
    try:
        spoken.speakable = lambda _name: True          # type: ignore[assignment]
        await spoken.run_chain([Step()], "open Notes", execute=execute,
                               origin=spoken.DASHBOARD)
    finally:
        spoken.speakable = monkey                       # type: ignore[assignment]
    assert seen[0].approved_by == "voice:dashboard"
    assert seen[0].reason.startswith("Asked out loud on the dashboard")


async def test_a_slack_message_is_miros_turn(monkeypatch):
    from atlas.api import slack_events
    seen: list[bool] = []

    async def post(body, channel, ts):
        seen.append(quota.is_interactive())

    monkeypatch.setattr(slack_events, "search_terms", lambda _t: "x")
    monkeypatch.setattr(slack_events, "_reply", post)

    async def found(_terms):
        seen.append(quota.is_interactive())
        return []
    from atlas.kernel import search
    monkeypatch.setattr(search, "search", found)
    monkeypatch.setattr(search, "render", lambda *_a: "nothing")
    await slack_events._request("search x", "U1", "C1", "1.0")
    assert seen and all(seen)


# ------------------------------------- one spent model is not "all spent"

def _healthy_groq_with_headers() -> llm.GroqProvider:
    """A model that answered a moment ago with plenty left -- and, like every
    Groq answer, a request-window reset two minutes away."""
    p = llm.GroqProvider("llama-3.1-8b-instant")
    quota.observe(p, httpx.Response(200, headers={
        "x-ratelimit-limit-requests": "14400",
        "x-ratelimit-remaining-requests": "14000",
        "x-ratelimit-reset-requests": "2m0s"},
        request=httpx.Request("POST", "https://api.groq.com")))
    return p


def test_the_reset_said_is_the_spent_models_not_a_healthy_ones():
    healthy, spent = _healthy_groq_with_headers(), _spent_groq()
    llm.ROUTES[Need.TRIVIAL] = [healthy, spent]
    reset = quota.next_reset([healthy, spent])
    assert reset is not None
    assert reset == quota.next_reset([spent])            # the 1 h one, not 2 min
    request = httpx.Request("POST", "https://api.groq.com")
    rationed = httpx.HTTPStatusError(
        "429", request=request, response=httpx.Response(429, request=request))
    try:
        raise RuntimeError("all providers failed") from rationed
    except RuntimeError as exc:
        line = voice.no_model_line(exc)
    assert reset.strftime("%H:%M") in line


async def test_a_timeout_with_one_model_spent_still_asks_the_agent(client, monkeypatch):
    """One spent model among several is an ordinary afternoon: a slow fast
    path still goes to the agent, and is never called an allowance."""
    llm.ROUTES[Need.TRIVIAL] = [_healthy_groq_with_headers(), _spent_groq()]
    asked: list[str] = []

    async def slow(*_a, **_k):
        raise TimeoutError

    async def route(question, *_a, **_k):
        asked.append(question)
        return "Here's what I know."

    monkeypatch.setattr("atlas.kernel.llm.think", slow)
    monkeypatch.setattr("atlas.kernel.agents.route", route)
    body = client.post("/api/voice/ask",
                       json={"transcript": "how is cloudhost looking"}).json()
    assert asked, "the agent was asked"
    assert body["answer"] == "Here's what I know."
    assert not voice.out_of_allowance(None)


async def test_a_plain_mac_command_runs_with_no_model(client, monkeypatch):
    """"open Notes" is not what is_order calls an order, and still runs."""
    seen: dict = {}

    async def no_answer(*_a, **_k):
        raise RuntimeError("all providers failed for trivial")

    async def run(transcript, **kw):
        seen.update(transcript=transcript, **kw)
        return spoken.Heard(True, "Opened Notes.")

    monkeypatch.setattr("atlas.kernel.llm.think", no_answer)
    monkeypatch.setattr("atlas.kernel.agents.route", no_answer)
    monkeypatch.setattr(spoken, "run_spoken_commands", run)
    assert not voice.is_order("open Notes")
    body = client.post("/api/voice/ask", json={"transcript": "open Notes"}).json()
    assert body["answer"] == "Opened Notes." and seen["origin"] == spoken.DASHBOARD
