"""Local embeddings on the Spark, and the checks that say whether they answer.

Memory holds personal content (PRIVATE), so the embedder is local only:
mxbai-embed-large on a second vLLM on the Spark, reached through the Mac's
tunnel (`ATLAS_EMBED_URL=http://host.docker.internal:18001/v1`). These pin:

* the request shape (OpenAI `/embeddings`, bearer key, the model's 512-token
  limit enforced by the server, not by a 400 that stops the backfill);
* the privacy line: a URL that is not local is refused before any request;
* the column fit: anything but a 1024-number vector is no vector at all, so
  a misconfigured model never breaks `remember()` on INSERT;
* invariant 7: the tunnel and embedding checks are UNKNOWN when they cannot
  measure, never OK.
"""
from __future__ import annotations

import json

import httpx
import pytest

from atlas.kernel import llm, memory
from atlas.kernel.contracts import Health
from atlas.modules import localstack

EMBED = "http://host.docker.internal:18001/v1"
CHAT = "http://host.docker.internal:18000/v1"


@pytest.fixture
def wire(monkeypatch):
    """Route the local client through a MockTransport; record requests."""
    seen: list[httpx.Request] = []
    replies: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        reply = replies.get(request.url.path, httpx.ConnectError("refused"))
        if isinstance(reply, Exception):
            raise reply
        assert isinstance(reply, httpx.Response)
        return reply

    def client(timeout: float) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=httpx.MockTransport(handler),
                                 timeout=timeout, trust_env=False,
                                 follow_redirects=False)

    monkeypatch.setattr(llm, "_local_client", client)
    return seen, replies


@pytest.fixture(autouse=True)
def env(monkeypatch):
    monkeypatch.delenv("OLLAMA_URL", raising=False)
    monkeypatch.delenv("ATLAS_EMBED_API_KEY", raising=False)
    monkeypatch.delenv("LOCAL_LLM_ALLOWED_HOSTS", raising=False)
    monkeypatch.setenv("ATLAS_EMBED_URL", EMBED)
    monkeypatch.setenv("LOCAL_LLM_BASE_URL", CHAT)
    monkeypatch.setenv("LOCAL_LLM_API_KEY", "k-test")
    monkeypatch.setenv("ATLAS_EMBED_MODEL", "mxbai-embed-large")


def vector(n: int = memory.EMBED_DIM) -> httpx.Response:
    return httpx.Response(200, json={"data": [{"embedding": [0.01] * n}]})


async def test_embed_posts_openai_shape_with_the_bearer_key(wire):
    seen, replies = wire
    replies["/v1/embeddings"] = vector()
    vec = await memory._embed("x" * 9000)
    assert vec is not None and len(vec) == 1024
    (req,) = seen
    assert str(req.url) == f"{EMBED}/embeddings"
    assert req.headers["authorization"] == "Bearer k-test"
    body = json.loads(req.content)
    assert body["model"] == "mxbai-embed-large"
    assert body["truncate_prompt_tokens"] == memory.EMBED_MAX_TOKENS == 512
    assert len(body["input"]) == 8000


async def test_a_dedicated_embed_key_wins_over_the_llm_key(wire, monkeypatch):
    seen, replies = wire
    monkeypatch.setenv("ATLAS_EMBED_API_KEY", "k-embed")
    replies["/v1/embeddings"] = vector()
    assert await memory._embed("hello")
    assert seen[0].headers["authorization"] == "Bearer k-embed"


@pytest.mark.parametrize("url", ["https://api.openai.com/v1",
                                 "http://embed.example.com:18001/v1",
                                 "http://user@127.0.0.1:18001/v1"])
async def test_a_url_that_is_not_local_is_refused_before_any_request(
        wire, monkeypatch, url):
    seen, replies = wire
    monkeypatch.setenv("ATLAS_EMBED_URL", url)
    replies["/v1/embeddings"] = vector()
    assert await memory._embed("my lease renews in May") is None
    assert seen == []


@pytest.mark.parametrize("bad", [
    httpx.Response(200, json={"data": [{"embedding": [0.1] * 768}]}),
    httpx.Response(200, json={"data": [{"embedding": ["a"] * 1024}]}),
    httpx.Response(200, json={"data": []}),
    httpx.Response(200, text="not json"),
    httpx.Response(401, json={"error": "bad key"}),
    httpx.Response(400, json={"error": "too long"}),
])
async def test_anything_but_a_1024_vector_is_no_vector(wire, bad):
    _seen, replies = wire
    replies["/v1/embeddings"] = bad
    assert await memory._embed("hello") is None


async def test_an_unreachable_embedder_is_no_vector(wire):
    assert await memory._embed("hello") is None      # ConnectError


async def test_the_key_never_reaches_the_log(wire, caplog):
    _seen, replies = wire
    replies["/v1/embeddings"] = httpx.Response(401, json={"error": "k-test"})
    await memory._embed("hello")
    assert "k-test" not in caplog.text


def test_embed_unset_names_the_variable(monkeypatch):
    assert memory.embed_unset() is None
    monkeypatch.delenv("ATLAS_EMBED_URL")
    assert memory.embed_unset() == "ATLAS_EMBED_URL unset"
    monkeypatch.setenv("OLLAMA_URL", "http://ollama:11434")   # legacy path
    assert memory.embed_unset() is None


# --- checks ---------------------------------------------------------------


async def test_tunnel_check_probes_only_the_model_forward(wire):
    seen, replies = wire
    replies["/health"] = httpx.Response(200)
    obs = await localstack.check_tunnel()
    assert obs.health is Health.OK
    hosts = sorted(f"{r.url.port}{r.url.path}" for r in seen)
    assert hosts == ["18000/health"]


async def test_tunnel_check_is_unknown_and_names_the_dead_forward(wire, monkeypatch):
    _seen, _replies = wire

    def client(timeout: float) -> httpx.AsyncClient:
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.port == 18000:
                raise httpx.ConnectError("refused")
            return httpx.Response(200)
        return httpx.AsyncClient(transport=httpx.MockTransport(handler),
                                 trust_env=False)

    monkeypatch.setattr(llm, "_local_client", client)
    obs = await localstack.check_tunnel()
    assert obs.health is Health.UNKNOWN
    assert "18000" in obs.detail and "model" in obs.detail


async def test_tunnel_check_is_unknown_when_nothing_is_configured(wire, monkeypatch):
    seen, _replies = wire
    monkeypatch.delenv("ATLAS_EMBED_URL")
    monkeypatch.delenv("LOCAL_LLM_BASE_URL")
    obs = await localstack.check_tunnel()
    assert obs.health is Health.UNKNOWN
    assert seen == []


async def test_tunnel_check_never_probes_a_url_that_is_not_local(wire, monkeypatch):
    seen, replies = wire
    replies["/health"] = httpx.Response(200)
    monkeypatch.setenv("LOCAL_LLM_BASE_URL", "https://api.example.com/v1")
    obs = await localstack.check_tunnel()
    assert obs.health is Health.UNKNOWN
    assert all(r.url.host == "host.docker.internal" for r in seen)


def test_the_module_registers_its_checks():
    names = {c.name for c in localstack.MODULE.checks()}
    assert names == {"tunnel", "model", "speed", "answers"}
    assert localstack.MODULE.actions() == ()


async def test_the_answers_check_is_unknown_until_a_person_was_answered():
    from atlas.kernel import llm
    llm.reset_answer_state()
    obs = await localstack.check_answers()
    assert obs.health == Health.UNKNOWN
    llm._note_answer(llm.Completion("x", "anthropic", "m"), "tunnel down")
    assert (await localstack.check_answers()).health == Health.DEGRADED
    llm._note_answer(llm.Completion("x", "local", "qwen3.8:27b"), None)
    assert (await localstack.check_answers()).health == Health.OK
    llm.reset_answer_state()
