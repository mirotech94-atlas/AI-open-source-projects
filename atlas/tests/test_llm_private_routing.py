"""PRIVATE work stays on the box, or it does not run.

Written from a live misconfiguration, not a hypothetical. `ATLAS_MODEL_LOCAL`
defaulted to `llama3.1:8b`; the box had only `llama3.2:3b` pulled. Every local
call failed, every one fell through to Haiku, and the only sign was one warning
per call that printed `provider ollama failed ()` — httpx's ConnectError and a
200-with-an-error-body both stringify to nothing useful. Personal mail and
browser history went to an API provider for days while the routing table
claimed they never would.

Three promises are pinned here:

* a named-but-unpulled local model is detected at boot, not on every call,
* PRIVATE refuses rather than borrowing a cloud chain, and
* a provider failure names the provider, the model and the reason.
"""
from __future__ import annotations

import pytest

from atlas.kernel import llm
from atlas.kernel.llm import Message, Need


@pytest.fixture(autouse=True)
def clean_routes():
    llm.ROUTES.clear()
    yield
    llm.ROUTES.clear()


def _env(monkeypatch, **kw) -> None:
    for key in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "OLLAMA_URL",
                "OPENROUTER_API_KEY", "ATLAS_PRIVATE_ALLOW_CLOUD",
                "GROQ_API_KEY", "GEMINI_API_KEY", "COHERE_API_KEY",
                "CEREBRAS_API_KEY", "MISTRAL_API_KEY", "ATLAS_PREFER_FREE",
                "HF_TOKEN", "CLOUDFLARE_API_TOKEN", "CLOUDFLARE_ACCOUNT_ID"):
        monkeypatch.delenv(key, raising=False)
    for key, value in kw.items():
        monkeypatch.setenv(key, value)


def test_private_does_not_fall_back_to_the_api_by_default(monkeypatch):
    _env(monkeypatch, OLLAMA_URL="http://ollama:11434",
         ANTHROPIC_API_KEY="sk-test")
    llm.build_routes()
    assert [p.name for p in llm.ROUTES[Need.PRIVATE]] == ["ollama"]


def test_private_may_be_let_out_deliberately(monkeypatch):
    _env(monkeypatch, OLLAMA_URL="http://ollama:11434",
         ANTHROPIC_API_KEY="sk-test", ATLAS_PRIVATE_ALLOW_CLOUD="true")
    llm.build_routes()
    assert [p.name for p in llm.ROUTES[Need.PRIVATE]] == ["ollama", "anthropic"]


def test_no_local_model_means_private_is_refused_not_rerouted(monkeypatch):
    _env(monkeypatch, ANTHROPIC_API_KEY="sk-test")
    llm.build_routes()
    assert Need.PRIVATE not in llm.ROUTES
    assert llm.ROUTES[Need.TRIVIAL], "other work still runs"


async def test_an_empty_private_chain_never_borrows_the_reason_chain(
        monkeypatch):
    """`ROUTES.get(need) or ROUTES.get(Need.REASON)` was the whole hole."""
    _env(monkeypatch, ANTHROPIC_API_KEY="sk-test")
    llm.build_routes()
    assert llm.ROUTES.get(Need.REASON), "REASON is configured and tempting"

    with pytest.raises(RuntimeError, match="refusing to send personal"):
        await llm.think(Need.PRIVATE, "s", [Message("user", "my inbox")])


async def test_an_unpulled_local_model_is_taken_out_of_every_chain(
        monkeypatch):
    _env(monkeypatch, OLLAMA_URL="http://ollama:11434",
         ANTHROPIC_API_KEY="sk-test")
    llm.build_routes()
    # 2026-09-25: the local model left TRIVIAL (it held a classify eleven
    # minutes while Ollama swapped); it serves PRIVATE, and TRIVIAL only
    # when no cloud key exists.
    assert any(p.name == "ollama" for p in llm.ROUTES[Need.PRIVATE])

    async def not_pulled(self) -> bool:
        return False

    monkeypatch.setattr(llm.OllamaProvider, "available", not_pulled)
    assert await llm.verify_local() is False

    assert not any(p.name == "ollama"
                   for chain in llm.ROUTES.values() for p in chain)
    # and PRIVATE, whose only provider was the local one, is gone rather than
    # silently served by the API.
    assert Need.PRIVATE not in llm.ROUTES


async def test_ollama_reports_a_missing_model_instead_of_an_empty_answer():
    """A 200 carrying {"error": ...} used to become an empty Completion."""
    import httpx

    provider = llm.OllamaProvider("llama3.1:8b")

    class FakeResponse:
        status_code = 200

        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict:
            return {"error": "model 'llama3.1:8b' not found"}

    class FakeClient:
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def post(self, *a, **kw): return FakeResponse()

    original = httpx.AsyncClient
    httpx.AsyncClient = lambda *a, **kw: FakeClient()   # type: ignore[assignment]
    try:
        with pytest.raises(RuntimeError, match="not found"):
            await provider.complete("s", [Message("user", "hi")])
    finally:
        httpx.AsyncClient = original                     # type: ignore[assignment]


# ------------------------------------------------- the free bench and failover

def test_cheap_work_reaches_a_free_provider_before_a_paid_one(monkeypatch):
    """A free tier that sits behind the paid model never saves anything."""
    _env(monkeypatch, OLLAMA_URL="http://ollama:11434",
         ANTHROPIC_API_KEY="sk-test", GROQ_API_KEY="gsk-test",
         GEMINI_API_KEY="g-test")
    llm.build_routes()
    # TRIVIAL (and voice) leads with the small free models, then spreads
    # over the other free allowances before the paid model.
    assert [(p.name, p.model) for p in llm.ROUTES[Need.TRIVIAL]] == [
        ("gemini", "gemini-flash-lite-latest"),
        ("groq", "openai/gpt-oss-20b"), ("groq", "qwen/qwen3.8-27b"),
        ("anthropic", "claude-haiku-4-5")]
    assert [p.name for p in llm.ROUTES[Need.DRAFT]] == [
        "groq", "gemini", "groq", "anthropic", "anthropic"]


def test_cerebras_leads_the_fast_free_bench_when_its_key_is_set(monkeypatch):
    """Cerebras is the fastest inference of the free providers, so it goes
    first in TRIVIAL and DRAFT -- but local still leads TRIVIAL (PRIVATE's
    only provider is never outrun by a cloud one in the chain it shares)."""
    _env(monkeypatch, OLLAMA_URL="http://ollama:11434",
         ANTHROPIC_API_KEY="sk-test", GROQ_API_KEY="gsk-test",
         GEMINI_API_KEY="g-test", CEREBRAS_API_KEY="csk-test")
    llm.build_routes()
    assert [p.name for p in llm.ROUTES[Need.TRIVIAL]] == [
        "cerebras", "gemini", "groq", "groq", "anthropic"]
    assert [p.name for p in llm.ROUTES[Need.DRAFT]] == [
        "cerebras", "groq", "gemini", "groq", "anthropic", "anthropic"]


def test_no_cerebras_key_leaves_the_bench_exactly_as_it_was(monkeypatch):
    """Same chain as with no Cerebras key at all: it is simply absent, not a
    silent no-op entry."""
    _env(monkeypatch, OLLAMA_URL="http://ollama:11434",
         ANTHROPIC_API_KEY="sk-test", GROQ_API_KEY="gsk-test",
         GEMINI_API_KEY="g-test")
    llm.build_routes()
    # TRIVIAL (and voice) leads with the small free models, then spreads
    # over the other free allowances before the paid model.
    assert [(p.name, p.model) for p in llm.ROUTES[Need.TRIVIAL]] == [
        ("gemini", "gemini-flash-lite-latest"),
        ("groq", "openai/gpt-oss-20b"), ("groq", "qwen/qwen3.8-27b"),
        ("anthropic", "claude-haiku-4-5")]
    assert [p.name for p in llm.ROUTES[Need.DRAFT]] == [
        "groq", "gemini", "groq", "anthropic", "anthropic"]


def test_failover_extension_adds_mistral_and_cohere_to_critical_only(
        monkeypatch):
    """`build_routes()` already threads Cerebras, Mistral and Cohere through
    `extra_fast`/`extra_mid` into TRIVIAL, DRAFT, REASON and HARD -- CRITICAL
    is the one Need it leaves them out of (Anthropic, Gemini, OpenAI only).
    `extend_routes_with_failover` is where Mistral and Cohere join CRITICAL
    as further independent-vendor depth, each only when its own key is set,
    and it must not duplicate them into a chain build_routes() already
    placed them in."""
    _env(monkeypatch, ANTHROPIC_API_KEY="sk-test",
         MISTRAL_API_KEY="m-test", COHERE_API_KEY="co-test")
    llm.build_routes()
    for need in (Need.DRAFT, Need.REASON, Need.HARD):
        names = [p.name for p in llm.ROUTES[need]]
        assert "mistral" in names and "cohere" in names, (need, names)

    llm.extend_routes_with_failover()

    assert [p.name for p in llm.ROUTES[Need.CRITICAL]].count("mistral") == 1
    assert [p.name for p in llm.ROUTES[Need.CRITICAL]].count("cohere") == 1
    # Unchanged elsewhere -- extend_routes_with_failover must not re-add a
    # provider build_routes() already placed.
    for need in (Need.DRAFT, Need.REASON, Need.HARD):
        names = [p.name for p in llm.ROUTES[need]]
        assert names.count("mistral") == 1 and names.count("cohere") == 1


def test_failover_extension_adds_nothing_for_a_provider_with_no_key(monkeypatch):
    """Neither key present means no change to CRITICAL at all."""
    _env(monkeypatch, ANTHROPIC_API_KEY="sk-test")
    llm.build_routes()
    before = {need: list(chain) for need, chain in llm.ROUTES.items()}
    llm.extend_routes_with_failover()
    assert llm.ROUTES == before


def test_judgment_still_leads_with_anthropic(monkeypatch):
    """REASON and HARD decide things. Free models are depth, not the first
    answer — an outage should degrade quality, not relocate judgment."""
    _env(monkeypatch, ANTHROPIC_API_KEY="sk-test", GROQ_API_KEY="gsk-test",
         GEMINI_API_KEY="g-test")
    llm.build_routes()
    for need in (Need.REASON, Need.HARD):
        chain = [p.name for p in llm.ROUTES[need]]
        assert chain[0] == "anthropic", need
        assert {"gemini", "groq"} <= set(chain), f"{need} has no free depth"


def test_prefer_free_can_be_turned_off(monkeypatch):
    _env(monkeypatch, ANTHROPIC_API_KEY="sk-test", GROQ_API_KEY="gsk-test",
         ATLAS_PREFER_FREE="false")
    llm.build_routes()
    assert next(p.name for p in llm.ROUTES[Need.DRAFT]) == "anthropic"


def test_critical_gets_a_second_vendor_that_is_not_out_of_credits(monkeypatch):
    """`think_twice` needs two INDEPENDENT providers or it escalates. OpenAI
    answers 429 'no credits remaining', so Gemini goes ahead of it."""
    _env(monkeypatch, ANTHROPIC_API_KEY="sk-test", OPENAI_API_KEY="sk-oa",
         GEMINI_API_KEY="g-test")
    llm.build_routes()
    chain = [p.name for p in llm.ROUTES[Need.CRITICAL]]
    assert chain == ["anthropic", "gemini", "openai"]
    assert len({*chain}) > 1, "a model agreeing with itself is not a check"


def test_no_free_keys_leaves_the_chains_as_they_were(monkeypatch):
    _env(monkeypatch, ANTHROPIC_API_KEY="sk-test")
    llm.build_routes()
    assert [p.name for p in llm.ROUTES[Need.DRAFT]] == ["anthropic", "anthropic"]
    assert all(p.name == "anthropic" for p in llm.ROUTES[Need.CRITICAL])


async def test_gemini_refuses_an_empty_answer_that_spent_its_budget_thinking(
        monkeypatch):
    """gemini-pro-latest returns 200 with MAX_TOKENS and no text at all."""
    import httpx

    monkeypatch.setenv("GEMINI_API_KEY", "g-test")

    provider = llm.GeminiProvider("gemini-pro-latest")

    class FakeResponse:
        status_code = 200

        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict:
            return {"candidates": [{"content": {"parts": []},
                                    "finishReason": "MAX_TOKENS"}]}

    class FakeClient:
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def post(self, *a, **kw): return FakeResponse()

    original = httpx.AsyncClient
    httpx.AsyncClient = lambda *a, **kw: FakeClient()   # type: ignore[assignment]
    try:
        with pytest.raises(RuntimeError, match="MAX_TOKENS"):
            await provider.complete("s", [Message("user", "hi")])
    finally:
        httpx.AsyncClient = original                     # type: ignore[assignment]


# ------------------------------------------- a present key is not a working one

class _Refuses:
    """A provider whose key authenticates but whose account will not serve."""

    name = "refuses"
    model = "x"
    optional = True

    def __init__(self, status: int) -> None:
        self.status = status

    async def complete(self, *a, **kw):
        import httpx
        request = httpx.Request("POST", "https://example.invalid")
        response = httpx.Response(self.status, text="nope", request=request)
        raise httpx.HTTPStatusError("refused", request=request,
                                    response=response)


class _TimesOut:
    name = "flaky"
    model = "x"
    optional = True

    async def complete(self, *a, **kw):
        import httpx
        raise httpx.ConnectTimeout("no route")


class _Serves:
    name = "serves"
    model = "x"
    optional = True

    async def complete(self, *a, **kw):
        return llm.Completion(text="ready", provider=self.name, model=self.model)


@pytest.mark.parametrize("status", [401, 402, 403, 404])
async def test_a_provider_that_refuses_at_boot_leaves_every_chain(status):
    """402 payment_required (Cerebras) and 404 no-endpoints (hermes-4-70b) sat
    in the chain looking like failover and delivering none. These four are
    configuration: they will not fix themselves before the next restart."""
    llm.ROUTES[Need.DRAFT] = [_Refuses(status), _Serves()]
    llm.ROUTES[Need.HARD] = [_Refuses(status)]

    verdict = await llm.verify_cloud()

    assert verdict["refuses"].startswith("dropped")
    assert [p.name for p in llm.ROUTES[Need.DRAFT]] == ["serves"]
    assert llm.ROUTES[Need.HARD] == []


async def test_a_rationed_provider_is_kept_because_tomorrow_restores_it():
    """The distinction the status code cannot make: a free tier that has spent
    its daily cap answers 429, and so does one that was never enabled.
    Dropping on it would take a merely-rationed provider out of the chain until
    the next restart, when the per-call fallthrough already handles a spent cap
    and the cap resets on its own."""
    llm.ROUTES[Need.DRAFT] = [_Refuses(429), _Serves()]

    verdict = await llm.verify_cloud()

    assert verdict["refuses"] == "kept: rationed (HTTP 429)"
    assert [p.name for p in llm.ROUTES[Need.DRAFT]] == ["refuses", "serves"]


async def test_an_outage_is_not_a_misconfiguration():
    """A timeout is the thing the chain exists for. Dropping a good provider
    on one bad request would turn a blip into a permanent downgrade."""
    llm.ROUTES[Need.DRAFT] = [_TimesOut(), _Serves()]

    verdict = await llm.verify_cloud()

    assert "kept" in verdict["flaky"]
    assert [p.name for p in llm.ROUTES[Need.DRAFT]] == ["flaky", "serves"]


async def test_paid_primaries_are_never_probed(monkeypatch):
    """`optional` is what marks a provider worth proving. Anthropic is not
    dropped on the strength of one request."""
    _env(monkeypatch, ANTHROPIC_API_KEY="sk-test")
    llm.build_routes()
    probed = await llm.verify_cloud()
    assert probed == {}, probed
    assert llm.ROUTES[Need.REASON], "the paid chain survived untouched"


def test_the_three_new_free_providers_join_the_bench(monkeypatch):
    _env(monkeypatch, ANTHROPIC_API_KEY="sk-test", COHERE_API_KEY="c-test",
         CEREBRAS_API_KEY="cs-test", MISTRAL_API_KEY="m-test")
    llm.build_routes()
    names = [p.name for p in llm.ROUTES[Need.DRAFT]]
    assert {"cohere", "cerebras", "mistral"} <= set(names), names
    assert names[-1] == "anthropic" or "anthropic" in names


async def test_gemini_is_never_asked_for_a_budget_too_small_to_answer_in(
        monkeypatch):
    """Thinking tokens count against maxOutputTokens. Measured on both
    gemini-3.8-flash and gemini-pro-latest: a one-word reply needs 256 and
    returns nothing at 64. A caller asking for a short answer would get an
    empty one every time, from a provider that looks perfectly healthy."""
    import httpx

    sent: list[dict] = []

    class FakeResponse:
        status_code = 200

        def raise_for_status(self) -> None: return None

        def json(self) -> dict:
            return {"candidates": [{"content": {"parts": [{"text": "ready"}]},
                                    "finishReason": "STOP"}]}

    class FakeClient:
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False

        async def post(self, url, **kw):
            sent.append(kw["json"])
            return FakeResponse()

    monkeypatch.setenv("GEMINI_API_KEY", "g-test")
    provider = llm.GeminiProvider("gemini-3.8-flash")
    original = httpx.AsyncClient
    httpx.AsyncClient = lambda *a, **kw: FakeClient()   # type: ignore[assignment]
    try:
        await provider.complete("s", [Message("user", "hi")], max_tokens=16)
        await provider.complete("s", [Message("user", "hi")], max_tokens=4096)
    finally:
        httpx.AsyncClient = original                     # type: ignore[assignment]

    assert sent[0]["generationConfig"]["maxOutputTokens"] == 256, "floor"
    assert sent[1]["generationConfig"]["maxOutputTokens"] == 4096, (
        "the floor must not cap a caller that asked for more")
