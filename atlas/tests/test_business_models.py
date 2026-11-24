"""Task 53: each business owns its model, Hermes by default.

`businesses.resolved_model()` is the only place a business's `"provider:
model"` pin is decided; this file is about that decision, not about `think()`
actually reaching a provider (see `test_llm.py`'s pin-resolution and
fallback-prefix tests for that).
"""
from __future__ import annotations

from dataclasses import replace

import pytest

from atlas.kernel import businesses
from atlas.kernel.businesses import BUSINESSES, BY_NAME, DEFAULT_MODEL
from atlas.kernel.llm import Need

NON_PRIVATE = [b for b in BUSINESSES if b.need is not Need.PRIVATE]


@pytest.fixture(autouse=True)
def _fresh(monkeypatch):
    """Every test gets the real tool catalog, a clean roster, no leftover
    `ATLAS_AGENT_MODEL_*` override, and a fresh once-per-process warning set
    (a module-level list, so it survives across tests unless cleared)."""
    for biz in BUSINESSES:
        monkeypatch.delenv(f"ATLAS_AGENT_MODEL_{biz.name.upper()}", raising=False)
    businesses._WARNED_PRIVATE_MODEL.clear()


def _model(name: str) -> str | None:
    """The pin the business would run on. The business agents left the roster
    on 2026-10-28; their data and the resolution rule stay."""
    return businesses.resolved_model(BY_NAME[name])


def test_every_non_private_business_defaults_to_hermes_on_openrouter():
    assert DEFAULT_MODEL == "openrouter:qwen/qwen3-235b-a22b-2507"
    for biz in NON_PRIVATE:
        assert businesses.resolved_model(biz) == DEFAULT_MODEL, biz.name


def test_the_env_override_wins_over_the_default(monkeypatch):
    monkeypatch.setenv("ATLAS_AGENT_MODEL_FIVERR", "anthropic:claude-haiku-4-5")
    assert _model("fiverr") == "anthropic:claude-haiku-4-5"
    # An untouched business is unaffected by another one's override.
    assert _model("agency") == DEFAULT_MODEL


def test_the_env_override_wins_over_the_businesss_own_field(monkeypatch):
    pinned = replace(BY_NAME["fiverr"], model="groq:openai/gpt-oss-120b")
    assert businesses.resolved_model(pinned) == "groq:openai/gpt-oss-120b"

    monkeypatch.setenv("ATLAS_AGENT_MODEL_FIVERR", "anthropic:claude-haiku-4-5")
    assert businesses.resolved_model(pinned) == "anthropic:claude-haiku-4-5"


def test_a_businesss_own_field_beats_the_hermes_default():
    pinned = replace(BY_NAME["agency"], model="gemini:gemini-pro-latest")
    assert businesses.resolved_model(pinned) == "gemini:gemini-pro-latest"


def test_resolved_model_direct_for_every_private_business():
    """Belt and braces: whatever PRIVATE business exists, resolved_model()
    refuses a cloud pin for it, by Need, not by name."""
    for biz in BUSINESSES:
        if biz.need is Need.PRIVATE:
            pinned = replace(biz, model="anthropic:claude-opus-4-1")
            assert businesses.resolved_model(pinned) is None
