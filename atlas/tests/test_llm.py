"""The routing core: the local model calls tools or says it cannot, and
PRIVATE work never leaves the box.

No network. Ollama is faked at `httpx.AsyncClient.post`; Anthropic is faked
at its SDK (`anthropic.AsyncAnthropic`), so a test that says "no Anthropic
request was made" is watching the transport a real request would use.
"""
from __future__ import annotations

import sys
import types
from typing import Any

import httpx
import pytest

from atlas.kernel import llm
from atlas.kernel.llm import (
    Completion,
    LocalModelUnavailable,
    Message,
    Need,
    OllamaProvider,
    ToolsUnsupported,
)

TOOL = {
    "name": "gmail_search",
    "description": "Search Miro's mail.",
    "input_schema": {"type": "object",
                     "properties": {"query": {"type": "string"}},
                     "required": ["query"]},
}
NO_TOOLS_BODY = ('{"error":"registry.ollama.ai/library/gemma:2b '
                 'does not support tools"}')


@pytest.fixture(autouse=True)
def fresh_routes(monkeypatch):
    """ROUTES and the tools-support record are process globals."""
    monkeypatch.setattr(llm, "ROUTES", {})
    monkeypatch.setattr(llm, "LOCAL_TOOLS", {})
    monkeypatch.delenv("ATLAS_PRIVATE_ALLOW_CLOUD", raising=False)


def _ollama(monkeypatch, *replies: httpx.Response | Exception) -> list[dict]:
    """Fake Ollama: each POST gets the next reply. Returns what was sent."""
    sent: list[dict] = []
    queue = list(replies)

    async def post(self, url, json=None, **_kw):
        sent.append({"url": url, "json": json})
        reply = queue.pop(0)
        if isinstance(reply, Exception):
            raise reply
        reply.request = httpx.Request("POST", url)
        return reply

    monkeypatch.setattr(httpx.AsyncClient, "post", post)
    return sent


def _chat(content: str = "", calls: list[dict] | None = None) -> httpx.Response:
    message: dict[str, Any] = {"role": "assistant", "content": content}
    if calls is not None:
        message["tool_calls"] = calls
    return httpx.Response(200, json={
        "model": "llama3.1:8b", "message": message, "done": True,
        "prompt_eval_count": 31, "eval_count": 7})


def _anthropic(monkeypatch) -> list[dict]:
    """Fake Anthropic SDK. Returns every messages.create call it saw."""
    calls: list[dict] = []

    class Messages:
        async def create(self, **kwargs):
            calls.append(kwargs)
            return types.SimpleNamespace(
                content=[types.SimpleNamespace(type="text", text="from the cloud")],
                usage=types.SimpleNamespace(input_tokens=1, output_tokens=1))

    class AsyncAnthropic:
        def __init__(self, **_kw):
            self.messages = Messages()

    fake = types.ModuleType("anthropic")
    fake.AsyncAnthropic = AsyncAnthropic
    monkeypatch.setitem(sys.modules, "anthropic", fake)
    return calls


# ------------------------------------------------------ Ollama tool calling

async def test_ollama_sends_tools_and_maps_the_calls_back(monkeypatch):
    sent = _ollama(monkeypatch, _chat(calls=[
        {"function": {"name": "gmail_search", "arguments": {"query": "invoice"}}},
    ]))
    reply = await OllamaProvider("llama3.1:8b").complete(
        "sys", [Message("user", "any invoices?")], [TOOL], 300)

    body = sent[0]["json"]
    assert sent[0]["url"].endswith("/api/chat")
    assert body["stream"] is False
    assert body["options"]["num_predict"] == 300
    assert body["messages"][0] == {"role": "system", "content": "sys"}
    assert body["messages"][1] == {"role": "user", "content": "any invoices?"}
    assert body["tools"] == [{"type": "function", "function": {
        "name": "gmail_search", "description": "Search Miro's mail.",
        "parameters": TOOL["input_schema"]}}]

    assert reply.provider == "ollama" and reply.model == "llama3.1:8b"
    assert reply.tool_calls is not None and len(reply.tool_calls) == 1
    call = reply.tool_calls[0]
    assert call["name"] == "gmail_search"
    assert call["input"] == {"query": "invoice"}
    assert call["id"]
    assert (reply.input_tokens, reply.output_tokens) == (31, 7)
    assert llm.LOCAL_TOOLS == {"llama3.1:8b": True}


async def test_ollama_arguments_sent_as_a_string_are_still_parsed(monkeypatch):
    _ollama(monkeypatch, _chat(calls=[
        {"function": {"name": "gmail_search", "arguments": '{"query": "rent"}'}},
    ]))
    reply = await OllamaProvider("m").complete("s", [Message("user", "q")], [TOOL])
    assert reply.tool_calls == [
        {"name": "gmail_search", "input": {"query": "rent"}, "id": "ollama-0"}]


async def test_ollama_without_tools_keeps_the_plain_generate_path(monkeypatch):
    sent = _ollama(monkeypatch, httpx.Response(200, json={"response": "hello"}))
    reply = await OllamaProvider("m").complete("s", [Message("user", "hi")])
    assert sent[0]["url"].endswith("/api/generate")
    assert "tools" not in sent[0]["json"]
    assert reply.text == "hello" and reply.tool_calls is None
    # A call that did not ask for tools says nothing about tool support.
    assert llm.LOCAL_TOOLS == {}


async def test_a_model_without_tools_is_a_typed_answer_not_a_silent_drop(
        monkeypatch):
    _ollama(monkeypatch, httpx.Response(400, text=NO_TOOLS_BODY))
    reply = await OllamaProvider("gemma:2b").complete(
        "s", [Message("user", "q")], [TOOL])
    assert isinstance(reply, ToolsUnsupported)
    assert reply.model == "gemma:2b"
    assert llm.LOCAL_TOOLS == {"gemma:2b": False}


async def test_any_other_400_raises(monkeypatch):
    _ollama(monkeypatch, httpx.Response(400, text='{"error":"bad request"}'))
    with pytest.raises(httpx.HTTPStatusError):
        await OllamaProvider("m").complete("s", [Message("user", "q")], [TOOL])
    assert llm.LOCAL_TOOLS == {}


# ------------------------------------------------- PRIVATE never leaves the box

async def test_private_with_no_ollama_makes_no_anthropic_request(monkeypatch):
    monkeypatch.delenv("OLLAMA_URL", raising=False)
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    cloud = _anthropic(monkeypatch)
    local = _ollama(monkeypatch)
    llm.build_routes()

    with pytest.raises(LocalModelUnavailable) as err:
        await llm.think(Need.PRIVATE, "s", [Message("user", "my mail")])
    assert str(err.value).startswith("local model unavailable: ")
    assert cloud == [] and local == []

    # The fake is live: the same routes DO reach Anthropic for REASON work.
    reply = await llm.think(Need.REASON, "s", [Message("user", "why")])
    assert reply.text == "from the cloud" and len(cloud) == 1


async def test_private_with_ollama_down_does_not_fall_through(monkeypatch):
    monkeypatch.setenv("OLLAMA_URL", "http://ollama:11434")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    cloud = _anthropic(monkeypatch)
    _ollama(monkeypatch, httpx.ConnectError("connection refused"))
    llm.build_routes()

    with pytest.raises(LocalModelUnavailable) as err:
        await llm.think(Need.PRIVATE, "s", [Message("user", "my mail")])
    assert "ConnectError" in err.value.reason
    assert cloud == []


def test_the_private_route_is_only_ever_local(monkeypatch):
    monkeypatch.setenv("OLLAMA_URL", "http://ollama:11434")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    llm.build_routes()
    assert [p.name for p in llm.ROUTES[Need.PRIVATE]] == ["ollama"]

    monkeypatch.delenv("OLLAMA_URL")
    llm.build_routes()
    assert Need.PRIVATE not in llm.ROUTES


async def test_a_cloud_provider_wedged_into_private_is_never_called(monkeypatch):
    """Defence in depth: even if a later edit puts a cloud provider in the
    PRIVATE chain, think() will not send private work to it."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    cloud = _anthropic(monkeypatch)
    llm.ROUTES[Need.PRIVATE] = [llm.AnthropicProvider("claude-haiku-4-5")]
    with pytest.raises(LocalModelUnavailable):
        await llm.think(Need.PRIVATE, "s", [Message("user", "my mail")])
    assert cloud == []


def test_local_model_tools_is_unknown_until_tried(monkeypatch):
    assert llm.local_model() is None
    assert llm.local_model_tools() is None
    llm.ROUTES[Need.PRIVATE] = [OllamaProvider("llama3.1:8b")]
    assert llm.local_model() == "llama3.1:8b"
    assert llm.local_model_tools() is None
    llm.LOCAL_TOOLS["llama3.1:8b"] = False
    assert llm.local_model_tools() is False


async def test_private_named_as_a_plain_string_is_still_private(monkeypatch):
    """Need is a StrEnum; "private" must hit the same gate as Need.PRIVATE,
    not slip past it to the REASON fallback."""
    monkeypatch.delenv("OLLAMA_URL", raising=False)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    cloud = _anthropic(monkeypatch)
    llm.build_routes()
    with pytest.raises(LocalModelUnavailable):
        await llm.think("private", "s", [Message("user", "my mail")])
    assert cloud == []


# ------------------------------------------------ malformed tool calls

@pytest.mark.parametrize("function", [
    {"name": "gmail_search"},                              # no arguments
    {"name": "gmail_search", "arguments": None},
    {"name": "gmail_search", "arguments": "not json"},
    {"name": "gmail_search", "arguments": "[1, 2]"},        # JSON, not an object
])
async def test_malformed_arguments_become_an_empty_call(monkeypatch, function):
    _ollama(monkeypatch, _chat(calls=[{"function": function}]))
    reply = await OllamaProvider("m").complete("s", [Message("user", "q")], [TOOL])
    assert reply.tool_calls == [{"name": "gmail_search", "input": {},
                                 "id": "ollama-0"}]


async def test_a_tool_call_that_is_not_an_object_is_skipped_with_a_warning(
        monkeypatch, caplog):
    _ollama(monkeypatch, _chat("done", calls=[
        "gmail_search",
        {"function": {"name": "gmail_search", "arguments": {"query": "x"}}}]))
    reply = await OllamaProvider("m").complete("s", [Message("user", "q")], [TOOL])
    assert reply.tool_calls == [{"name": "gmail_search", "input": {"query": "x"},
                                 "id": "ollama-1"}]
    assert any("not an object" in r.getMessage() for r in caplog.records
               if r.levelname == "WARNING")


# ------------------------------------------- what the merge must not lose

async def test_a_200_carrying_an_error_is_a_failure_not_tool_support(monkeypatch):
    _ollama(monkeypatch, httpx.Response(200, json={"error": "model 'm' not found"}))
    with pytest.raises(RuntimeError, match="not found"):
        await OllamaProvider("m").complete("s", [Message("user", "q")], [TOOL])
    assert llm.LOCAL_TOOLS == {}


async def test_the_tools_turn_uses_the_local_timeout(monkeypatch):
    seen: list[Any] = []
    real_init = httpx.AsyncClient.__init__

    def init(self, *a, **kw):
        seen.append(kw.get("timeout"))
        real_init(self, *a, **kw)

    monkeypatch.setattr(httpx.AsyncClient, "__init__", init)
    _ollama(monkeypatch, _chat("ok"))
    monkeypatch.delenv("ATLAS_LOCAL_TIMEOUT_S", raising=False)
    assert llm._local_timeout() == 600.0
    monkeypatch.setenv("ATLAS_LOCAL_TIMEOUT_S", "900")
    await OllamaProvider("m").complete("s", [Message("user", "q")], [TOOL])
    assert seen == [900.0]


async def test_the_failure_reason_is_redacted(monkeypatch):
    monkeypatch.setenv("SOME_API_TOKEN", "tok-0123456789abcdef")

    class Leaky:
        name, model, local = "ollama", "m", True

        async def complete(self, *a, **k):
            raise RuntimeError("GET http://u:tok-0123456789abcdef@ollama/api failed")

    llm.ROUTES[Need.PRIVATE] = [Leaky()]
    with pytest.raises(LocalModelUnavailable) as err:
        await llm.think(Need.PRIVATE, "s", [Message("user", "q")])
    assert "tok-0123456789abcdef" not in err.value.reason
    assert "tok-0123456789abcdef" not in str(err.value)
    assert "RuntimeError" in err.value.reason


# ------------------------------------- the explicit opt-in to the cloud

async def test_private_cloud_is_off_unless_explicitly_allowed(monkeypatch):
    monkeypatch.delenv("ATLAS_PRIVATE_ALLOW_CLOUD", raising=False)
    assert llm.private_cloud_allowed() is False
    for no in ("", "0", "false", "no", "off"):
        monkeypatch.setenv("ATLAS_PRIVATE_ALLOW_CLOUD", no)
        assert llm.private_cloud_allowed() is False
    monkeypatch.setenv("ATLAS_PRIVATE_ALLOW_CLOUD", "true")
    assert llm.private_cloud_allowed() is True


async def test_the_opt_in_lets_private_fall_through_and_warns_once(
        monkeypatch, caplog):
    monkeypatch.setattr(llm, "_WARNED_PRIVATE_CLOUD", [])
    monkeypatch.setenv("ATLAS_PRIVATE_ALLOW_CLOUD", "true")
    monkeypatch.setenv("OLLAMA_URL", "http://ollama:11434")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    cloud = _anthropic(monkeypatch)
    _ollama(monkeypatch, httpx.ConnectError("refused"), httpx.ConnectError("refused"))
    llm.build_routes()
    llm.build_routes()                    # a second build does not warn again
    assert [p.name for p in llm.ROUTES[Need.PRIVATE]] == ["ollama", "anthropic"]

    reply = await llm.think(Need.PRIVATE, "s", [Message("user", "my mail")])
    assert reply.text == "from the cloud" and len(cloud) == 1
    warnings = [r for r in caplog.records if r.levelname == "WARNING"
                and "ATLAS_PRIVATE_ALLOW_CLOUD" in r.getMessage()]
    assert len(warnings) == 1


async def test_self_status_says_whether_private_may_reach_the_cloud(monkeypatch):
    from atlas.kernel import selfmodel

    async def no_mesh():
        return {"home": None, "peers_seen": [], "read_at": "now", "reason": "x"}

    async def blind(*_a, **_k):
        raise RuntimeError("no database")

    monkeypatch.setattr(selfmodel, "presence", no_mesh)
    monkeypatch.setattr(selfmodel, "fetch_one", blind)
    monkeypatch.setattr(selfmodel, "fetch_all", blind)
    monkeypatch.delenv("ATLAS_PRIVATE_ALLOW_CLOUD", raising=False)
    assert (await selfmodel.status())["private_cloud_allowed"] is False
    monkeypatch.setenv("ATLAS_PRIVATE_ALLOW_CLOUD", "1")
    assert (await selfmodel.status())["private_cloud_allowed"] is True


# ------------------------------------ per-agent model pins (Task 53)

def _openai_compatible_post(monkeypatch, content: str = "ok") -> list[dict]:
    """Fake httpx.AsyncClient.post for any _OpenAICompatible provider.
    Returns what was sent, headers included -- the request shape (base URL,
    bearer token, model field) is exactly what these tests check."""
    sent: list[dict] = []

    async def post(self, url, headers=None, json=None, **_kw):
        sent.append({"url": url, "headers": headers, "json": json})
        resp = httpx.Response(200, json={
            "choices": [{"message": {"role": "assistant", "content": content}}],
            "usage": {"prompt_tokens": 3, "completion_tokens": 2},
        })
        resp.request = httpx.Request("POST", url)
        return resp

    monkeypatch.setattr(httpx.AsyncClient, "post", post)
    return sent


@pytest.mark.parametrize("cls, env, url, model", [
    (llm.CerebrasProvider, "CEREBRAS_API_KEY",
     "https://api.cerebras.ai/v1/chat/completions", "gpt-oss-120b"),
    (llm.MistralProvider, "MISTRAL_API_KEY",
     "https://api.mistral.ai/v1/chat/completions", "mistral-large"),
    (llm.CohereProvider, "COHERE_API_KEY",
     "https://api.cohere.ai/compatibility/v1/chat/completions",
     "command-a-03-2025"),
    (llm.HuggingFaceProvider, "HF_TOKEN",
     "https://router.huggingface.co/v1/chat/completions",
     "openai/gpt-oss-120b:cheapest"),
])
async def test_the_three_new_providers_send_the_openai_compatible_shape(
        monkeypatch, cls, env, url, model):
    """Cerebras, Mistral and Cohere all reuse `_OpenAICompatible`: a base URL
    and a key name is the whole integration, same as OpenRouter and Groq."""
    monkeypatch.setenv(env, "test-key-0123456789")
    sent = _openai_compatible_post(monkeypatch)
    reply = await cls(model).complete("sys", [Message("user", "hi")])

    assert sent[0]["url"] == url
    assert sent[0]["headers"]["Authorization"] == "Bearer test-key-0123456789"
    assert sent[0]["json"]["model"] == model
    assert sent[0]["json"]["messages"][0] == {"role": "system", "content": "sys"}
    assert sent[0]["json"]["messages"][1] == {"role": "user", "content": "hi"}
    assert reply.text == "ok" and reply.model == model
    assert (reply.input_tokens, reply.output_tokens) == (3, 2)


def test_resolve_pin_recognises_the_three_new_prefixes(monkeypatch):
    monkeypatch.setenv("CEREBRAS_API_KEY", "c")
    monkeypatch.setenv("MISTRAL_API_KEY", "m")
    monkeypatch.setenv("COHERE_API_KEY", "co")
    for pin, cls in (("cerebras:gpt-oss-120b", llm.CerebrasProvider),
                     ("mistral:codestral", llm.MistralProvider),
                     ("cohere:command-a-03-2025", llm.CohereProvider)):
        provider, reason = llm._resolve_pin(pin)
        assert reason is None
        assert isinstance(provider, cls)
        assert provider.model == pin.partition(":")[2]


def test_resolve_pin_reports_a_missing_key_without_calling_anything(monkeypatch):
    monkeypatch.delenv("MISTRAL_API_KEY", raising=False)
    provider, reason = llm._resolve_pin("mistral:mistral-large")
    assert provider is None
    assert reason == "MISTRAL_API_KEY not set"


def test_resolve_pin_rejects_an_unrecognised_provider():
    provider, reason = llm._resolve_pin("madeup:x")
    assert provider is None
    assert "madeup" in reason


def test_resolve_pin_rejects_a_pin_with_no_model_name():
    provider, reason = llm._resolve_pin("anthropic:")
    assert provider is None and reason


async def test_a_failing_pin_falls_back_and_labels_who_actually_answered(
        monkeypatch):
    """The core Task 53 promise: a pin that cannot be reached does not fail
    the turn -- the Need's own route answers, and the answer says so."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    cloud = _anthropic(monkeypatch)
    llm.build_routes()

    class DeadPin:
        name = "openrouter"
        model = "nousresearch/hermes-4-405b"

        async def complete(self, *a, **k):
            raise httpx.ConnectError("refused")

    monkeypatch.setattr(llm, "_resolve_pin", lambda model: (DeadPin(), None))

    reply = await llm.think(
        Need.REASON, "s", [Message("user", "q")],
        model="openrouter:nousresearch/hermes-4-405b")

    assert reply.provider == "anthropic"          # who actually answered
    assert reply.text.startswith("(answered by anthropic:")
    assert "openrouter:nousresearch/hermes-4-405b was unavailable" in reply.text
    assert "ConnectError" in reply.text
    assert reply.text.endswith(") from the cloud")
    assert len(cloud) == 1


async def test_a_pin_that_resolves_is_tried_before_the_need_route(monkeypatch):
    """The pin, when it answers, is used as-is -- no fallback note, and the
    Need's own chain is never even called."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    cloud = _anthropic(monkeypatch)
    llm.build_routes()

    class LivePin:
        name = "openrouter"
        model = "nousresearch/hermes-4-405b"

        async def complete(self, system, messages, tools=None, max_tokens=2048):
            return llm.Completion(text="from Hermes", provider=self.name,
                                  model=self.model)

    monkeypatch.setattr(llm, "_resolve_pin", lambda model: (LivePin(), None))
    reply = await llm.think(
        Need.REASON, "s", [Message("user", "q")],
        model="openrouter:nousresearch/hermes-4-405b")

    assert reply.text == "from Hermes" and reply.provider == "openrouter"
    assert cloud == [], "the Need route must not run when the pin answers"


def test_an_unresolvable_pin_still_falls_back_without_touching_pin_identity(
        monkeypatch):
    """An unrecognised or unkeyed pin is (None, reason) -- there is no
    provider identity to dedupe the chain against, and the chain runs
    unmodified."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    llm.build_routes()
    assert llm._resolve_pin("madeup:x") == (None, "'madeup' is not a recognised provider")


# --------------------------- a pin verify_cloud() already proved dead (M3)

def test_resolve_pin_skips_a_provider_verify_cloud_dropped(monkeypatch):
    monkeypatch.setenv("CEREBRAS_API_KEY", "c-test")
    monkeypatch.setattr(llm, "DEAD_PROVIDERS", {"cerebras"})
    provider, reason = llm._resolve_pin("cerebras:gpt-oss-120b")
    assert provider is None
    assert reason == "cerebras was dropped by verify_cloud() at boot"


async def test_a_pin_to_a_dropped_provider_falls_back_without_touching_the_network(
        monkeypatch):
    """M3: `_resolve_pin()` builds a fresh provider on every call, and
    `verify_cloud()` only filters `ROUTES` -- so a pin naming a provider it
    already dropped at boot (the docstring's own example: Cerebras on 402)
    was retried against the same dead endpoint on every turn, up to the
    client's 120s timeout each time. `DEAD_PROVIDERS` closes that: no
    network attempt at all, straight to the fallback, labelled why."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    monkeypatch.setenv("CEREBRAS_API_KEY", "c-test")
    cloud = _anthropic(monkeypatch)
    llm.build_routes()
    monkeypatch.setattr(llm, "DEAD_PROVIDERS", {"cerebras"})

    async def forbidden(self, url, **_kw):
        raise AssertionError("a pin verify_cloud() dropped must never reach the network")

    monkeypatch.setattr(httpx.AsyncClient, "post", forbidden)

    reply = await llm.think(Need.REASON, "s", [Message("user", "q")],
                            model="cerebras:gpt-oss-120b")

    assert "cerebras:gpt-oss-120b was unavailable" in reply.text
    assert "dropped by verify_cloud() at boot" in reply.text
    assert len(cloud) == 1


@pytest.mark.parametrize("pin", [
    "cerebras:gpt-oss-120b", "mistral:mistral-large", "cohere:command-a-03-2025",
    "openrouter:nousresearch/hermes-4-405b", "anthropic:claude-haiku-4-5",
])
async def test_private_never_reaches_a_pinned_cloud_provider(monkeypatch, pin):
    """Task 53's addendum: none of the three new providers, nor any cloud
    provider, is ever used for Need.PRIVATE -- the pin is not even
    inspected, because `think()` branches to `_think_private` first.

    Recorded, then asserted, rather than raised inside the fake transport:
    `think()`'s pin path catches ANY `Exception` a failed pin attempt
    raises (llm.py's `except Exception as exc:` around `pinned.complete`)
    and falls through to the Need's own route, which for PRIVATE still ends
    in `_think_private` and still raises `LocalModelUnavailable` -- so a
    fake `post` that raises on a leaked request is invisible to
    `pytest.raises(LocalModelUnavailable)`: the leak happened, the pin's
    own exception was swallowed same as always, and the test still passed.
    `requested.append(url)` runs before the fake raises, so it survives
    that swallow; asserting on it afterwards is what lets this test fail
    (fix round 1, review item I2 -- verified against a mutant that tries
    the pin before the PRIVATE check: RED without this, GREEN with it).

    Two transports are faked, not one: `AnthropicProvider` and `OpenAIProvider`
    go through their own SDKs, not `httpx.AsyncClient.post` directly (the
    `_OpenAICompatible` providers -- Cerebras, Mistral, Cohere, OpenRouter,
    Groq -- do). A first version of this test faked only `httpx` and missed
    the `anthropic:` pin case entirely: the SDK's own connection failed on
    its own (no network in the sandbox) *before* ever reaching the faked
    `post`, so `requested` stayed empty whether or not the pin had leaked --
    passing for the wrong reason on every pin that uses the SDK path.
    """
    monkeypatch.delenv("OLLAMA_URL", raising=False)
    monkeypatch.delenv("ATLAS_PRIVATE_ALLOW_CLOUD", raising=False)
    for key in ("CEREBRAS_API_KEY", "MISTRAL_API_KEY", "COHERE_API_KEY",
               "OPENROUTER_API_KEY", "ANTHROPIC_API_KEY"):
        monkeypatch.setenv(key, "test-key-0123456789")
    llm.build_routes()

    requested: list[str] = []

    async def record(self, url, **_kw):
        requested.append(url)
        raise httpx.ConnectError("should never be reached")

    monkeypatch.setattr(httpx.AsyncClient, "post", record)
    sdk_calls = _anthropic(monkeypatch)      # Anthropic's own SDK transport

    with pytest.raises(LocalModelUnavailable):
        await llm.think(Need.PRIVATE, "s", [Message("user", "my mail")], model=pin)

    assert sdk_calls == [], f"PRIVATE reached Anthropic's SDK: {sdk_calls}"

    assert requested == [], f"PRIVATE reached the network: {requested}"


@pytest.mark.parametrize("pin", [
    "cerebras:gpt-oss-120b", "mistral:mistral-small-latest",
    "cohere:command-a-03-2025",
])
async def test_private_ignores_the_pin_even_with_a_real_chain_and_cloud_opt_in(
        monkeypatch, pin):
    """The stronger case: PRIVATE has somewhere real to go (local, plus the
    Anthropic fallback Miro explicitly opted into) and a real chain to
    filter a pin's identity out of -- and still never reaches Cerebras,
    Mistral or Cohere, because the pin is not looked at for PRIVATE at all.

    Every URL is recorded, not raised on -- see
    `test_private_never_reaches_a_pinned_cloud_provider`'s docstring for why
    raising inside the fake transport is invisible here: `think()`'s pin
    path swallows the exception and PRIVATE answers via Ollama regardless,
    so a raise-based probe would pass whether or not the pin leaked first.
    """
    monkeypatch.setenv("OLLAMA_URL", "http://ollama:11434")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    monkeypatch.setenv("ATLAS_PRIVATE_ALLOW_CLOUD", "true")
    for key in ("CEREBRAS_API_KEY", "MISTRAL_API_KEY", "COHERE_API_KEY"):
        monkeypatch.setenv(key, "test-key-0123456789")
    llm.build_routes()
    llm.extend_routes_with_failover()
    assert [p.name for p in llm.ROUTES[Need.PRIVATE]] == ["ollama", "anthropic"]

    requested: list[str] = []

    async def record(self, url, json=None, **_kw):
        requested.append(url)
        if not url.startswith("http://ollama:11434"):
            raise httpx.ConnectError("should never be reached")
        resp = httpx.Response(200, json={"response": "from the local model"})
        resp.request = httpx.Request("POST", url)
        return resp

    monkeypatch.setattr(httpx.AsyncClient, "post", record)
    _anthropic(monkeypatch)          # would answer if PRIVATE ever reached it

    reply = await llm.think(Need.PRIVATE, "s", [Message("user", "my mail")],
                            model=pin)
    assert reply.provider == "ollama"
    assert all(u.startswith("http://ollama:11434") for u in requested), requested


def test_providers_present_lists_only_keys_that_are_set(monkeypatch):
    for key in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "OPENROUTER_API_KEY",
               "GROQ_API_KEY", "GEMINI_API_KEY", "CEREBRAS_API_KEY",
               "MISTRAL_API_KEY", "COHERE_API_KEY"):
        monkeypatch.delenv(key, raising=False)
    assert llm.providers_present() == []

    monkeypatch.setenv("CEREBRAS_API_KEY", "csk-should-never-appear-anywhere")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-also-secret")
    present = llm.providers_present()
    assert present == ["anthropic", "cerebras"]
    for name in present:
        assert "secret" not in name and "never-appear" not in name


async def test_trivial_never_waits_on_the_local_model_when_a_cloud_key_exists(
        monkeypatch):
    """2026-09-25 on prod: the local 3B model, first in TRIVIAL with a 600 s
    deadline, held a reel-format extract for eleven minutes while Ollama
    swapped. TRIVIAL is the free bench then Haiku; local only when there is
    no cloud key at all."""
    from atlas.kernel import llm
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
    monkeypatch.setenv("GROQ_API_KEY", "g")
    monkeypatch.setenv("OLLAMA_URL", "http://ollama:11434")
    llm.ROUTES.clear()
    llm.build_routes()
    names = [type(p).__name__ for p in llm.ROUTES[llm.Need.TRIVIAL]]
    assert "OllamaProvider" not in names
    assert names[0] == "GroqProvider"
    # PRIVATE still starts local, and nowhere else.
    assert type(llm.ROUTES[llm.Need.PRIVATE][0]).__name__ == "OllamaProvider"


async def test_trivial_falls_to_the_local_model_only_with_no_cloud_key(monkeypatch):
    from atlas.kernel import llm
    for k in ("ANTHROPIC_API_KEY", "GROQ_API_KEY", "GEMINI_API_KEY", "OPENAI_API_KEY",
              "OPENROUTER_API_KEY", "CEREBRAS_API_KEY", "MISTRAL_API_KEY",
              "COHERE_API_KEY"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("OLLAMA_URL", "http://ollama:11434")
    llm.ROUTES.clear()
    llm.build_routes()
    assert [type(p).__name__ for p in llm.ROUTES[llm.Need.TRIVIAL]] == ["OllamaProvider"]


async def test_an_empty_completion_falls_through_the_chain(monkeypatch):
    """Prod, 2026-09-25: TRIVIAL's reasoning model spent an 800-token budget
    thinking and answered '' while looking healthy, so reel_research filed
    0 formats. An empty answer is a provider failure, not an answer."""
    from atlas.kernel.llm import Completion

    class Silent:
        name, model = "groq", "openai/gpt-oss-20b"

        async def complete(self, *a, **k):
            return Completion(text="   ", provider="groq", model=self.model)

    class Speaks:
        name, model = "anthropic", "haiku"

        async def complete(self, *a, **k):
            return Completion(text='{"formats": []}', provider="anthropic",
                              model=self.model)

    llm.ROUTES[Need.TRIVIAL] = [Silent(), Speaks()]
    reply = await llm.think(Need.TRIVIAL, "s", [Message("user", "q")], max_tokens=800)
    assert reply.provider == "anthropic" and reply.text == '{"formats": []}'


async def test_every_provider_silent_is_a_failure_not_an_empty_answer(monkeypatch):
    from atlas.kernel.llm import Completion

    class Silent:
        name, model = "groq", "openai/gpt-oss-20b"

        async def complete(self, *a, **k):
            return Completion(text="", provider="groq", model=self.model)

    llm.ROUTES[Need.TRIVIAL] = [Silent()]
    with pytest.raises(RuntimeError, match="all providers failed"):
        await llm.think(Need.TRIVIAL, "s", [Message("user", "q")], max_tokens=800)


def _free_only_routes(monkeypatch, **env):
    from atlas.kernel import llm
    for k in ("GROQ_API_KEY", "GEMINI_API_KEY", "OPENAI_API_KEY", "OPENROUTER_API_KEY",
              "CEREBRAS_API_KEY", "MISTRAL_API_KEY", "COHERE_API_KEY",
              "ATLAS_MODELS_OPENROUTER_FREE"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
    monkeypatch.setenv("OLLAMA_URL", "http://ollama:11434")
    monkeypatch.setenv("ATLAS_FREE_ONLY", "true")
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    llm.ROUTES.clear()
    llm.build_routes()
    llm.extend_routes_with_failover()
    return llm


def test_free_only_leaves_no_paid_model_in_any_cloud_chain(monkeypatch):
    """Miro, 2026-10-05: free tiers, not paid ones. Every cloud Need is the
    free bench; nothing Anthropic, and no paid OpenRouter model."""
    llm = _free_only_routes(monkeypatch, GROQ_API_KEY="g", OPENROUTER_API_KEY="o")
    for need in (llm.Need.TRIVIAL, llm.Need.DRAFT, llm.Need.REASON, llm.Need.HARD,
                 llm.Need.CRITICAL):
        chain = llm.ROUTES[need]
        assert chain, need
        assert not any(isinstance(p, llm.AnthropicProvider) for p in chain)
        assert all(p.model.endswith(":free") for p in chain
                   if isinstance(p, llm.OpenRouterProvider))
    assert [type(p).__name__ for p in llm.ROUTES[llm.Need.PRIVATE]] == ["OllamaProvider"]


def test_free_only_with_no_free_key_keeps_the_usual_chains(monkeypatch):
    llm = _free_only_routes(monkeypatch)
    assert any(isinstance(p, llm.AnthropicProvider)
               for p in llm.ROUTES[llm.Need.REASON])


def test_a_paid_name_in_the_free_list_is_left_out(monkeypatch):
    llm = _free_only_routes(
        monkeypatch, OPENROUTER_API_KEY="o",
        ATLAS_MODELS_OPENROUTER_FREE="qwen/qwen3-235b-a22b-2507, a/b:free")
    assert [p.model for p in llm.openrouter_free()] == ["a/b:free"]


async def test_a_413_does_not_open_the_breaker(monkeypatch):
    """Groq's free tier answers 413 to a prompt over its per-minute token
    cap. That is about the request; small prompts still get an answer."""
    import httpx

    from atlas.kernel import breaker, llm

    class TooBig:
        name, model = "groq", "m"

        async def complete(self, *_a, **_kw):
            req = httpx.Request("POST", "https://x")
            raise httpx.HTTPStatusError(
                "413", request=req, response=httpx.Response(413, request=req))

    class Fine:
        name, model = "openrouter", "a/b:free"

        async def complete(self, *_a, **_kw):
            return llm.Completion(text="ok", provider=self.name, model=self.model)

    breaker.reset()
    big = TooBig()
    monkeypatch.setitem(llm.ROUTES, llm.Need.DRAFT, [big, Fine()])
    for _ in range(6):
        reply = await llm.think(llm.Need.DRAFT, "s", [llm.Message("user", "q")])
        assert reply.text == "ok"
    assert breaker.allow(breaker.key_of(big))


async def test_groq_asks_gpt_oss_for_low_reasoning_effort(monkeypatch):
    """At the default effort gpt-oss spent the whole budget thinking and
    answered nothing; only gpt-oss is sent the field."""
    import httpx

    from atlas.kernel import llm
    sent = []

    async def post(self, url, **kw):
        sent.append(kw["json"])
        return httpx.Response(200, request=httpx.Request("POST", url), json={
            "choices": [{"message": {"content": "ok"}}], "usage": {}})
    monkeypatch.setattr(httpx.AsyncClient, "post", post)
    monkeypatch.setenv("GROQ_API_KEY", "g")
    monkeypatch.delenv("ATLAS_GROQ_REASONING", raising=False)
    await llm.GroqProvider("openai/gpt-oss-120b").complete("s", [llm.Message("user", "q")])
    await llm.GroqProvider("qwen/qwen3.8-27b").complete("s", [llm.Message("user", "q")])
    assert sent[0]["reasoning_effort"] == "low"
    assert "reasoning_effort" not in sent[1]


# --- free tiers: the request that is too large, more Groq models, Cloudflare ---

GROQ_413 = ('{"error":{"message":"Request too large for model `openai/gpt-oss-120b` '
            'on tokens per minute (TPM): Limit 8000, Requested 8970, please reduce '
            'your message size and try again.","type":"tokens","code":"rate_limit_exceeded"}}')


def _replies(monkeypatch, *replies: httpx.Response) -> list[dict]:
    sent: list[dict] = []
    queue = list(replies)

    async def post(self, url, headers=None, json=None, **_kw):
        sent.append({"url": url, "max_tokens": json["max_tokens"]})
        reply = queue.pop(0)
        reply.request = httpx.Request("POST", url)
        return reply

    monkeypatch.setattr(httpx.AsyncClient, "post", post)
    return sent


def _ok() -> httpx.Response:
    return httpx.Response(200, json={
        "choices": [{"message": {"role": "assistant", "content": "ok"}}],
        "usage": {"prompt_tokens": 3, "completion_tokens": 2}})


async def test_a_request_over_a_free_tiers_limit_is_asked_again_with_less_room(
        monkeypatch):
    """Groq counts prompt + max_tokens against 8,000. 8,970 asked with 2,048
    reserved is 970 over: the same prompt fits with 2048 - 970 - 64 = 1014."""
    monkeypatch.setenv("GROQ_API_KEY", "gsk-test")
    sent = _replies(monkeypatch, httpx.Response(413, text=GROQ_413), _ok())
    reply = await llm.GroqProvider("openai/gpt-oss-120b").complete(
        "sys", [Message("user", "hi")])
    assert [s["max_tokens"] for s in sent] == [2048, 1014]
    assert reply.text == "ok"


async def test_a_prompt_that_leaves_no_room_for_an_answer_falls_through(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "gsk-test")
    body = GROQ_413.replace("Requested 8970", "Requested 9800")
    sent = _replies(monkeypatch, httpx.Response(413, text=body))
    with pytest.raises(httpx.HTTPStatusError):
        await llm.GroqProvider("openai/gpt-oss-120b").complete(
            "sys", [Message("user", "hi")])
    assert len(sent) == 1, "asked once; a 184-token answer is not an answer"


async def test_the_smaller_request_is_tried_once_not_in_a_loop(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "gsk-test")
    sent = _replies(monkeypatch, httpx.Response(413, text=GROQ_413),
                    httpx.Response(413, text=GROQ_413))
    with pytest.raises(httpx.HTTPStatusError):
        await llm.GroqProvider("openai/gpt-oss-120b").complete(
            "sys", [Message("user", "hi")])
    assert len(sent) == 2


async def test_cloudflare_puts_the_account_in_the_url(monkeypatch):
    monkeypatch.setenv("CLOUDFLARE_API_TOKEN", "test-key-0123456789")
    monkeypatch.setenv("CLOUDFLARE_ACCOUNT_ID", "acc123")
    sent = _openai_compatible_post(monkeypatch)
    model = "@cf/meta/llama-3.3-70b-instruct-fp8-fast"
    reply = await llm.CloudflareProvider(model).complete("sys", [Message("user", "hi")])
    assert sent[0]["url"] == ("https://api.cloudflare.com/client/v4/accounts/acc123"
                              "/ai/v1/chat/completions")
    assert sent[0]["headers"]["Authorization"] == "Bearer test-key-0123456789"
    assert reply.text == "ok" and reply.provider == "cloudflare"


def test_free_only_asks_every_groq_model_and_cloudflare_when_keyed(monkeypatch):
    """Miro, 2026-10-05: "pick all the free tiers all of them"."""
    for key in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "OLLAMA_URL", "GEMINI_API_KEY",
                "OPENROUTER_API_KEY", "COHERE_API_KEY", "CEREBRAS_API_KEY",
                "MISTRAL_API_KEY"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("GROQ_API_KEY", "gsk-test")
    monkeypatch.setenv("CLOUDFLARE_API_TOKEN", "cf-test")
    monkeypatch.setenv("CLOUDFLARE_ACCOUNT_ID", "acc123")
    monkeypatch.setenv("ATLAS_FREE_ONLY", "true")
    llm.build_routes()
    hard = [(p.name, p.model) for p in llm.ROUTES[Need.HARD]]
    assert hard == [("groq", "openai/gpt-oss-120b"), ("groq", "qwen/qwen3.8-27b"),
                    ("groq", "openai/gpt-oss-20b"),
                    ("cloudflare", "@cf/meta/llama-3.3-70b-instruct-fp8-fast")]
    # The voice/TRIVIAL lane leads with Workers AI's small non-reasoning 8B,
    # before any reasoning model. Groq's Llama models are retired (404).
    fast = [(p.name, p.model) for p in llm.ROUTES[Need.TRIVIAL]]
    assert fast[:2] == [("cloudflare", "@cf/meta/llama-3.1-8b-instruct-fp8-fast"),
                        ("groq", "openai/gpt-oss-20b")]
    assert all(not p.model.startswith("llama-3.")
               for c in llm.ROUTES.values() for p in c if p.name == "groq")


def test_cloudflare_needs_both_its_token_and_its_account(monkeypatch):
    for key in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "OLLAMA_URL", "GEMINI_API_KEY",
                "OPENROUTER_API_KEY", "COHERE_API_KEY", "CEREBRAS_API_KEY",
                "MISTRAL_API_KEY", "CLOUDFLARE_ACCOUNT_ID"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("GROQ_API_KEY", "gsk-test")
    monkeypatch.setenv("CLOUDFLARE_API_TOKEN", "cf-test")
    monkeypatch.setenv("ATLAS_FREE_ONLY", "true")
    llm.build_routes()
    assert "cloudflare" not in {p.name for p in llm.ROUTES[Need.HARD]}


# --- more free capacity: Hugging Face, Groq's other models, per-model probes ---

_NO_KEYS = ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "OLLAMA_URL", "GEMINI_API_KEY",
            "OPENROUTER_API_KEY", "COHERE_API_KEY", "CEREBRAS_API_KEY",
            "MISTRAL_API_KEY", "CLOUDFLARE_API_TOKEN", "CLOUDFLARE_ACCOUNT_ID",
            "HF_TOKEN", "GROQ_API_KEY")


def test_hugging_face_is_absent_without_its_token_and_last_with_it(monkeypatch):
    for key in _NO_KEYS:
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("GROQ_API_KEY", "gsk-test")
    monkeypatch.setenv("ATLAS_FREE_ONLY", "true")
    llm.build_routes()
    assert "huggingface" not in {p.name for c in llm.ROUTES.values() for p in c}
    monkeypatch.setenv("HF_TOKEN", "hf-test")
    llm.build_routes()
    for need in (Need.TRIVIAL, Need.DRAFT, Need.HARD):
        assert llm.ROUTES[need][-1].name == "huggingface", need
    assert "huggingface" in llm.providers_present()


def test_only_hugging_face_is_still_a_free_bench(monkeypatch):
    for key in _NO_KEYS:
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("HF_TOKEN", "hf-test")
    monkeypatch.setenv("ATLAS_FREE_ONLY", "true")
    llm.build_routes()
    assert [p.name for p in llm.ROUTES[Need.TRIVIAL]] == ["huggingface"]


def _groq_models(monkeypatch, ids: list[str]) -> None:
    async def get(self, url, headers=None, **_kw):
        assert url == "https://api.groq.com/openai/v1/models"
        assert headers["Authorization"] == "Bearer gsk-test"
        return httpx.Response(200, json={"data": [
            {"id": i, "active": True} for i in ids]},
            request=httpx.Request("GET", url))
    monkeypatch.setattr(httpx.AsyncClient, "get", get)


async def test_groq_chat_models_it_lists_join_the_voice_lane(monkeypatch):
    for key in _NO_KEYS:
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("GROQ_API_KEY", "gsk-test")
    monkeypatch.setenv("OPENROUTER_API_KEY", "or-test")
    monkeypatch.setenv("ATLAS_FREE_ONLY", "true")
    llm.build_routes()
    _groq_models(monkeypatch, [
        "openai/gpt-oss-20b", "some/new-chat-model",
        "whisper-large-v3", "canopylabs/orpheus-v1-english",
        "meta-llama/llama-prompt-guard-2-86m", "openai/gpt-oss-safeguard-20b",
        "minimaxai/minimax-m2.7"])
    added = await llm.discover_groq_models()
    assert added == ["some/new-chat-model"]
    lane = [(p.name, p.model) for p in llm.ROUTES[Need.TRIVIAL]]
    at = lane.index(("groq", "some/new-chat-model"))
    # Ahead of the last-resort OpenRouter entries, behind the known models.
    assert all(name != "openrouter" for name, _m in lane[:at])
    assert lane[at + 1][0] == "openrouter"


async def test_an_unreadable_groq_list_changes_nothing(monkeypatch):
    for key in _NO_KEYS:
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("GROQ_API_KEY", "gsk-test")
    monkeypatch.setenv("ATLAS_FREE_ONLY", "true")
    llm.build_routes()
    before = list(llm.ROUTES[Need.TRIVIAL])

    async def get(self, url, **_kw):
        raise httpx.ConnectError("no route")
    monkeypatch.setattr(httpx.AsyncClient, "get", get)
    assert await llm.discover_groq_models() == []
    assert llm.ROUTES[Need.TRIVIAL] == before


class _GroqProbe:
    name = "groq"
    optional = True
    probe_each_model = True

    def __init__(self, model: str, status: int | None) -> None:
        self.model, self.status = model, status

    async def complete(self, *a, **kw):
        if self.status is None:
            return Completion(text="ready", provider="groq", model=self.model)
        request = httpx.Request("POST", "https://api.groq.com")
        response = httpx.Response(self.status, text="model_not_found",
                                  request=request)
        raise httpx.HTTPStatusError("no", request=request, response=response)


async def test_one_groq_model_refused_at_boot_leaves_the_others(monkeypatch):
    """Groq allows each model separately: a model this key cannot use is
    dropped alone, and a pin to groq still resolves."""
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    llm.ROUTES.clear()
    llm.ROUTES[Need.TRIVIAL] = [_GroqProbe("small", 404), _GroqProbe("big", None)]
    probed = await llm.verify_cloud()
    assert probed["groq:small"].startswith("dropped")
    assert probed["groq:big"] == "ok"
    assert [p.model for p in llm.ROUTES[Need.TRIVIAL]] == ["big"]
    assert "groq" not in llm.DEAD_PROVIDERS


def test_mistrals_free_models_join_the_lanes_and_gemini_uses_its_free_ones(monkeypatch):
    """Prod, 2026-10-06: on free keys ministral-*-2512 answer (mistral-small
    is 429, mistral-large tier_not_allowed) and gemini-pro-latest has no
    free quota. The 3B leads Mistral's share of the voice lane."""
    for key in _NO_KEYS:
        monkeypatch.delenv(key, raising=False)
    for key in ("ATLAS_MODEL_MISTRAL", "ATLAS_MODEL_MISTRAL_FAST",
                "ATLAS_MODEL_GEMINI_FAST", "ATLAS_MODEL_GEMINI_DEEP"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("GROQ_API_KEY", "gsk-test")
    monkeypatch.setenv("MISTRAL_API_KEY", "m-test")
    monkeypatch.setenv("GEMINI_API_KEY", "g-test")
    monkeypatch.setenv("ATLAS_FREE_ONLY", "true")
    llm.build_routes()
    fast = [(p.name, p.model) for p in llm.ROUTES[Need.TRIVIAL]]
    assert fast[:2] == [("mistral", "ministral-3b-2512"),
                        ("gemini", "gemini-flash-lite-latest")]
    assert ("mistral", "ministral-14b-2512") in fast
    assert ("mistral", "ministral-14b-2512") in [
        (p.name, p.model) for p in llm.ROUTES[Need.DRAFT]]
    assert ("gemini", "gemini-flash-latest") == (
        llm.ROUTES[Need.HARD][0].name, llm.ROUTES[Need.HARD][0].model)
    assert all(p.model != "gemini-pro-latest"
               for c in llm.ROUTES.values() for p in c)
