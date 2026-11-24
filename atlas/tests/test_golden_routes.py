"""Golden routes: `llm.ROUTES` for the key environment combinations, as
(need -> [provider:model]). With every new assistant flag off this must stay
identical; a change here is a routing change and needs Miro's say-so.

Regenerate deliberately: `ATLAS_UPDATE_GOLDEN=1 pytest tests/test_golden_routes.py`.
Keys are made-up markers, not credentials."""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from atlas.kernel import llm

SNAPSHOT = Path(__file__).with_name("golden_routes.json")
BASE = "http://host.docker.internal:18000/v1"

_CLEAR = ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "OLLAMA_URL", "OPENROUTER_API_KEY",
          "ATLAS_PRIVATE_ALLOW_CLOUD", "GROQ_API_KEY", "GEMINI_API_KEY",
          "COHERE_API_KEY", "CEREBRAS_API_KEY", "MISTRAL_API_KEY",
          "ATLAS_PREFER_FREE", "HF_TOKEN", "CLOUDFLARE_API_TOKEN",
          "CLOUDFLARE_ACCOUNT_ID", "LOCAL_LLM_BASE_URL", "LOCAL_LLM_BASE_URL_2",
          "LOCAL_LLM_MODEL", "LOCAL_LLM_API_KEY", "ATLAS_FREE_ONLY",
          "ATLAS_MODEL_LOCAL", "ATLAS_CRITICAL_PAIR", "ATLAS_LOCAL_FIRST",
          "ATLAS_MODELS_OPENROUTER_FREE", "ATLAS_MODEL_GROQ_SMALL",
          "ATLAS_MODEL_GROQ_BIG", "LOCAL_LLM_ALLOWED_HOSTS")

COMBOS: dict[str, dict[str, str]] = {
    "anthropic_only": {"ANTHROPIC_API_KEY": "k"},
    "anthropic_local": {"ANTHROPIC_API_KEY": "k", "LOCAL_LLM_BASE_URL": BASE},
    "anthropic_local_cloud_ok": {"ANTHROPIC_API_KEY": "k", "LOCAL_LLM_BASE_URL": BASE,
                                 "ATLAS_PRIVATE_ALLOW_CLOUD": "true"},
    "ollama_only": {"OLLAMA_URL": "http://ollama:11434"},
    "anthropic_free_stack": {"ANTHROPIC_API_KEY": "k", "OPENAI_API_KEY": "k",
                             "GEMINI_API_KEY": "k", "GROQ_API_KEY": "k"},
    "free_only_stack": {"ANTHROPIC_API_KEY": "k", "GROQ_API_KEY": "k",
                        "GEMINI_API_KEY": "k", "ATLAS_FREE_ONLY": "true",
                        "LOCAL_LLM_BASE_URL": BASE},
    "mac_groq_openrouter_local_free_only": {
        "GROQ_API_KEY": "k", "OPENROUTER_API_KEY": "k", "ATLAS_FREE_ONLY": "true",
        "LOCAL_LLM_BASE_URL": BASE, "LOCAL_LLM_MODEL": "qwen3.8:27b",
        "LOCAL_LLM_API_KEY": "k"},
    "non_local_url": {"ANTHROPIC_API_KEY": "k",
                      "LOCAL_LLM_BASE_URL": "https://api.example.com/v1"},
}


def _dump(monkeypatch, env: dict[str, str]) -> dict[str, list[str]]:
    for key in _CLEAR:
        monkeypatch.delenv(key, raising=False)
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    llm.ROUTES.clear()
    llm.build_routes()
    out = {str(need): [f"{p.name}:{getattr(p, 'model', '')}" for p in chain]
           for need, chain in sorted(llm.ROUTES.items(), key=lambda kv: str(kv[0]))}
    llm.ROUTES.clear()
    return out


@pytest.fixture(autouse=True)
def _clean():
    llm.ROUTES.clear()
    yield
    llm.ROUTES.clear()


def test_routes_match_the_golden_snapshot(monkeypatch):
    got = {name: _dump(monkeypatch, env) for name, env in COMBOS.items()}
    if os.getenv("ATLAS_UPDATE_GOLDEN"):
        SNAPSHOT.write_text(json.dumps(got, indent=1, sort_keys=True) + "\n")
    want = json.loads(SNAPSHOT.read_text())
    assert got == want


def test_new_flags_set_but_off_change_nothing(monkeypatch):
    """The Spark-2 endpoint unset and the pair flag false leave ROUTES alone."""
    want = json.loads(SNAPSHOT.read_text())
    for name, env in COMBOS.items():
        got = _dump(monkeypatch, {**env, "ATLAS_CRITICAL_PAIR": "false"})
        assert got == want[name], name
