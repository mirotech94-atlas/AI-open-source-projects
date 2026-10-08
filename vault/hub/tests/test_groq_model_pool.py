# Groq rate-limits per model, and the 120b chat model's 8K TPM window fits
# about one turn a minute. Sleeping behind a long 429 left the user waiting
# and then fell to `rules` anyway. Now the round has a pool: the preferred
# model first, then every other chat-role model; a long 429 parks the model
# and the next one takes the turn.

import io
import json
import urllib.error

import pytest

from guardian_hub.chat import providers
from guardian_hub.chat.providers import (
    GROQ_MODELS, QUICK_RETRY_S, GroqRateLimited, _GROQ_COOLDOWN, _groq_pool,
)

CONSOLE_IDS = {
    "allam-2-7b", "groq/compound", "groq/compound-mini",
    "meta-llama/llama-prompt-guard-2-22m", "meta-llama/llama-prompt-guard-2-86m",
    "openai/gpt-oss-120b", "openai/gpt-oss-20b", "openai/gpt-oss-safeguard-20b",
    "qwen/qwen3.8-27b",
}


def _ok(model: str) -> bytes:
    return json.dumps({"choices": [{"message": {"content": f"hi from {model}", "tool_calls": []}}]}).encode()


def _429(model: str, wait: str) -> urllib.error.HTTPError:
    body = json.dumps({"error": {"message": f"Rate limit reached for model `{model}` on tokens per minute (TPM). "
                                            f"Please try again in {wait}.", "type": "tokens"}})
    return urllib.error.HTTPError("https://api.groq.com/x", 429, "Too Many Requests", {}, io.BytesIO(body.encode()))


class _Resp:
    def __init__(self, data: bytes): self._d = data
    def __enter__(self): return self
    def __exit__(self, *a): return False
    def read(self): return self._d


@pytest.fixture
def groq(monkeypatch):
    """Fake Groq: `limited[model]` = list of waits to 429 with before answering.
    Records the order of models attempted."""
    attempts: list[str] = []
    limited: dict[str, list[str]] = {}

    def urlopen(req, timeout=None, context=None):
        model = json.loads(req.data)["model"]
        attempts.append(model)
        queue = limited.get(model) or []
        if queue:
            raise _429(model, queue.pop(0))
        return _Resp(_ok(model))

    monkeypatch.setattr(providers.urllib.request, "urlopen", urlopen)
    monkeypatch.setattr(providers.time, "sleep", lambda s: None)
    monkeypatch.setattr(providers.settings, "chat_model_first", "openai/gpt-oss-120b")
    monkeypatch.setattr(providers.settings, "chat_model_tools", "openai/gpt-oss-20b")
    monkeypatch.setattr(providers.settings, "groq_api_key", "test-key")
    _GROQ_COOLDOWN.clear()
    yield attempts, limited
    _GROQ_COOLDOWN.clear()


def test_the_table_carries_every_model_from_the_console_with_its_limits():
    assert set(GROQ_MODELS) == CONSOLE_IDS
    for spec in GROQ_MODELS.values():
        assert spec["rpm"] == 30 and spec["rpd"] and spec["tpm"]
    assert GROQ_MODELS["openai/gpt-oss-120b"]["tpm"] == 8_000
    assert GROQ_MODELS["groq/compound"]["tpm"] == 70_000 and GROQ_MODELS["groq/compound"]["tpd"] is None


def test_only_chat_models_answer_users_preferred_first(monkeypatch):
    monkeypatch.setattr(providers.settings, "chat_model_first", "openai/gpt-oss-120b")
    monkeypatch.setattr(providers.settings, "chat_model_tools", "openai/gpt-oss-20b")
    pool = _groq_pool(0)
    assert pool[0] == "openai/gpt-oss-120b"
    assert pool == ["openai/gpt-oss-120b"] + [m for m in pool[1:] if GROQ_MODELS[m]["role"] == "chat"]
    for m in ("allam-2-7b", "openai/gpt-oss-safeguard-20b",
              "meta-llama/llama-prompt-guard-2-22m", "meta-llama/llama-prompt-guard-2-86m"):
        assert m not in pool
    assert _groq_pool(1)[0] == "openai/gpt-oss-20b"


def test_a_long_429_hands_the_turn_to_the_next_model_and_parks_the_first(groq):
    attempts, limited = groq
    limited["openai/gpt-oss-120b"] = ["30s"]
    reply = providers._call_groq([{"role": "user", "content": "x"}], [], False, 0)
    assert reply.provider == "groq/qwen/qwen3.8-27b"          # next chat model in table order
    assert attempts == ["openai/gpt-oss-120b", "qwen/qwen3.8-27b"]
    assert "openai/gpt-oss-120b" in _GROQ_COOLDOWN
    # Next turn inside the window never even asks 120b.
    attempts.clear()
    providers._call_groq([{"role": "user", "content": "y"}], [], False, 0)
    assert attempts == ["qwen/qwen3.8-27b"]


def test_a_short_429_is_waited_out_on_the_same_model(groq):
    attempts, limited = groq
    limited["openai/gpt-oss-120b"] = ["1s"]
    assert 1.0 + providers.RETRY_MARGIN_S <= QUICK_RETRY_S
    reply = providers._call_groq([{"role": "user", "content": "x"}], [], False, 0)
    assert reply.provider == "groq/openai/gpt-oss-120b"
    assert attempts == ["openai/gpt-oss-120b", "openai/gpt-oss-120b"]
    assert "openai/gpt-oss-120b" not in _GROQ_COOLDOWN


def test_when_every_chat_model_is_limited_the_ladder_falls_through(groq):
    attempts, limited = groq
    for m, spec in GROQ_MODELS.items():
        if spec["role"] == "chat":
            limited[m] = ["30s"]
    with pytest.raises(GroqRateLimited):
        providers._call_groq([{"role": "user", "content": "x"}], [], False, 0)
    assert set(attempts) == {m for m, s in GROQ_MODELS.items() if s["role"] == "chat"}
