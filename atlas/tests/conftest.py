"""Test fixtures. No database, no network, no LLM."""
from __future__ import annotations

import os
import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
os.environ.setdefault("DATABASE_URL", "postgresql://test/test")

from atlas.kernel import state


@pytest.fixture
def memory_backend():
    """Swap in the in-memory backend and restore the real one afterwards."""
    fake = state.MemoryBackend()
    previous = state.set_backend(fake)
    try:
        yield fake
    finally:
        state.set_backend(previous)


@pytest.fixture
def local_model_down(monkeypatch):
    """Real routes with Ollama configured but refusing connections, and an
    Anthropic key set. Both transports are faked: Ollama at
    `httpx.AsyncClient.post` (every POST raises ConnectError), Anthropic at
    its SDK. Yields the list of Anthropic requests -- a PRIVATE caller must
    leave it empty."""
    import sys
    import types

    import httpx

    from atlas.kernel import llm

    cloud: list[dict] = []

    class Messages:
        async def create(self, **kwargs):
            cloud.append(kwargs)
            raise AssertionError("a PRIVATE call reached Anthropic")

    class AsyncAnthropic:
        def __init__(self, **_kw):
            self.messages = Messages()

    fake = types.ModuleType("anthropic")
    fake.AsyncAnthropic = AsyncAnthropic
    monkeypatch.setitem(sys.modules, "anthropic", fake)

    async def refused(self, url, *_a, **_kw):
        raise httpx.ConnectError("connection refused",
                                 request=httpx.Request("POST", url))

    monkeypatch.setattr(httpx.AsyncClient, "post", refused)
    monkeypatch.setenv("OLLAMA_URL", "http://ollama:11434")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.delenv("ATLAS_PRIVATE_ALLOW_CLOUD", raising=False)
    monkeypatch.setattr(llm, "ROUTES", {})
    monkeypatch.setattr(llm, "LOCAL_TOOLS", {})
    llm.build_routes()
    yield cloud


@pytest.fixture(autouse=True)
def _voice_cache_in_tmp(monkeypatch, tmp_path):
    """Fixed Fish lines a test renders are kept in its own directory, never
    in the checkout's data/voice/cache (atlas/api/voice_cache.py)."""
    monkeypatch.setenv("ATLAS_VOICE_CACHE_DIR", str(tmp_path / "voice-cache"))
    from atlas.api import voice
    monkeypatch.setattr(voice, "_WARMED", [True])     # no background renders


@pytest.fixture(autouse=True)
def _closed_breakers():
    """Every test starts with every model breaker closed: the breaker's
    state is per process, and one test's failing fake provider must not
    make the next test's router skip a deployment of the same name."""
    from atlas.kernel import breaker, outage, quota
    breaker.reset()
    outage.reset()
    quota.reset()
    yield
    breaker.reset()
    outage.reset()
    quota.reset()
