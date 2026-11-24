"""Miro, 2026-11-17: "use the qwen3.8:27B model always to answer and voice".

Every call that answers a person (`think(..., answer=True)`) asks the Spark's
qwen3.8:27b first, whatever Need it names. The Need's own chain is only the
fallback when the local model does not answer, and the switch is never silent:
`llm.answer_health()` says which model last answered and why it was not local
(invariant 7: no data reads UNKNOWN, a fallback reads DEGRADED, never OK).

The bearer key is a made-up marker, not a credential.
"""
from __future__ import annotations

import pytest

from atlas.kernel import breaker, llm
from atlas.kernel.contracts import Health
from atlas.kernel.llm import Message, Need

BASE = "http://host.docker.internal:18000/v1"


@pytest.fixture(autouse=True)
def routes(monkeypatch):
    for key in ("OPENAI_API_KEY", "OLLAMA_URL", "OPENROUTER_API_KEY",
                "GROQ_API_KEY", "GEMINI_API_KEY", "COHERE_API_KEY",
                "CEREBRAS_API_KEY", "MISTRAL_API_KEY", "HF_TOKEN",
                "CLOUDFLARE_API_TOKEN", "ATLAS_FREE_ONLY"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("LOCAL_LLM_BASE_URL", BASE)
    monkeypatch.setenv("LOCAL_LLM_API_KEY", "fake-local-key-not-a-secret")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    llm.ROUTES.clear()
    llm.reset_answer_state()
    breaker.reset()
    llm.build_routes()
    yield
    llm.ROUTES.clear()
    llm.reset_answer_state()


def _fake(monkeypatch, local_ok=True):
    asked: list[str] = []

    async def local(self, system, messages, tools=None, max_tokens=2048):
        asked.append("local")
        if not local_ok:
            raise ConnectionError("tunnel down")
        return llm.Completion("from qwen", "local", self.model)

    async def cloud(self, system, messages, tools=None, max_tokens=2048):
        asked.append(self.name)
        return llm.Completion("from cloud", self.name, self.model)

    monkeypatch.setattr(llm.LocalOpenAIProvider, "complete", local)
    monkeypatch.setattr(llm.AnthropicProvider, "complete", cloud)
    return asked


@pytest.mark.parametrize("need", [Need.TRIVIAL, Need.DRAFT, Need.REASON])
async def test_an_answer_goes_to_qwen_first_for_any_need(monkeypatch, need):
    asked = _fake(monkeypatch)
    reply = await llm.think(need, "sys", [Message("user", "hi")], answer=True)
    assert asked == ["local"]
    assert (reply.provider, reply.text) == ("local", "from qwen")
    assert llm.answer_health()[0] == Health.OK


async def test_without_answer_the_need_chain_is_unchanged(monkeypatch):
    asked = _fake(monkeypatch)
    reply = await llm.think(Need.REASON, "sys", [Message("user", "hi")])
    assert asked == ["anthropic"] and reply.text == "from cloud"


async def test_a_dead_tunnel_falls_back_and_says_degraded(monkeypatch):
    asked = _fake(monkeypatch, local_ok=False)
    reply = await llm.think(Need.DRAFT, "sys", [Message("user", "hi")],
                            answer=True)
    assert asked[0] == "local" and reply.text == "from cloud"
    health, detail = llm.answer_health()
    assert health == Health.DEGRADED and "tunnel down" in detail


async def test_tools_unsupported_locally_falls_through(monkeypatch):
    _fake(monkeypatch)

    async def no_tools(self, system, messages, tools=None, max_tokens=2048):
        return llm.ToolsUnsupported(self.model, provider=self.name)

    monkeypatch.setattr(llm.LocalOpenAIProvider, "complete", no_tools)
    reply = await llm.think(Need.REASON, "sys", [Message("user", "hi")],
                            tools=[{"name": "t", "description": "d",
                                    "input_schema": {"type": "object"}}],
                            answer=True)
    assert reply.text == "from cloud"
    assert llm.answer_health()[0] == Health.DEGRADED


async def test_an_open_breaker_skips_local_and_is_still_degraded(monkeypatch):
    asked = _fake(monkeypatch)
    key = breaker.key_of(llm.ANSWER_LOCAL)
    for _ in range(10):
        breaker.failure(key, RuntimeError("down"))
    reply = await llm.think(Need.DRAFT, "sys", [Message("user", "hi")],
                            answer=True)
    assert "local" not in asked and reply.text == "from cloud"
    assert llm.answer_health()[0] == Health.DEGRADED


def test_no_answer_yet_reads_unknown_never_ok():
    health, detail = llm.answer_health()
    assert health == Health.UNKNOWN and "no answer" in detail


async def test_no_local_url_means_no_answer_lane(monkeypatch):
    monkeypatch.delenv("LOCAL_LLM_BASE_URL")
    llm.ROUTES.clear()
    llm.build_routes()
    asked = _fake(monkeypatch)
    reply = await llm.think(Need.DRAFT, "sys", [Message("user", "hi")],
                            answer=True)
    assert asked == ["anthropic"] and reply.text == "from cloud"
    assert llm.answer_health()[0] == Health.DEGRADED
