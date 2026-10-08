# The Integrations page's "AI failover" section: the ladder with which rungs
# are configured, and every Groq model with its limits, role, and whether it
# is parked. It is rendered to admins, so it must never carry a key.

import time

from guardian_hub.chat import providers
from guardian_hub.chat.providers import GROQ_MODELS, _GROQ_COOLDOWN, failover_status


def test_status_lists_the_ladder_and_every_model_without_any_key(monkeypatch):
    monkeypatch.setattr(providers, "_groq_available", lambda: True)
    monkeypatch.setattr(providers, "_anthropic_available", lambda: False)
    monkeypatch.setattr(providers.settings, "groq_api_key", "gsk_supersecret_value")
    monkeypatch.setattr(providers.settings, "chat_model_first", "openai/gpt-oss-120b")
    monkeypatch.setattr(providers.settings, "chat_model_tools", "openai/gpt-oss-20b")
    _GROQ_COOLDOWN.clear()
    st = failover_status()
    assert [r["name"] for r in st["ladder"]] == ["groq", "anthropic", "rules"]
    assert [r["configured"] for r in st["ladder"]] == [True, False, True]
    assert {m["model"] for m in st["models"]} == set(GROQ_MODELS)
    by = {m["model"]: m for m in st["models"]}
    assert by["openai/gpt-oss-120b"]["preferred"] == "first" and by["openai/gpt-oss-120b"]["in_pool"]
    assert by["openai/gpt-oss-20b"]["preferred"] == "tools"
    assert not by["meta-llama/llama-prompt-guard-2-22m"]["in_pool"]
    assert "supersecret" not in repr(st)


def test_a_parked_model_reports_how_long_it_is_parked():
    _GROQ_COOLDOWN.clear()
    _GROQ_COOLDOWN["openai/gpt-oss-120b"] = time.monotonic() + 25
    by = {m["model"]: m for m in failover_status()["models"]}
    assert 20 < by["openai/gpt-oss-120b"]["cooling_s"] <= 25
    assert by["qwen/qwen3.8-27b"]["cooling_s"] == 0
    _GROQ_COOLDOWN.clear()
