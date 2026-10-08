"""Task 53: each business agent owns its model, Hermes by default.

`businesses.resolved_model()` is the only place a business's `"provider:
model"` pin is decided; this file is about that decision, not about `think()`
actually reaching a provider (see `test_llm.py`'s pin-resolution and
fallback-prefix tests for that).
"""
from __future__ import annotations

import logging
from dataclasses import replace

import pytest

from atlas.kernel import businesses
from atlas.kernel.agents import ROSTER, build_roster
from atlas.kernel.businesses import BUSINESSES, BY_NAME, DEFAULT_MODEL
from atlas.kernel.llm import Need
from atlas.tools.builtin import register_all

NON_PRIVATE = [b for b in BUSINESSES if b.need is not Need.PRIVATE]


@pytest.fixture(autouse=True)
def _fresh(monkeypatch):
    """Every test gets the real tool catalog, a clean roster, no leftover
    `ATLAS_AGENT_MODEL_*` override, and a fresh once-per-process warning set
    (a module-level list, so it survives across tests unless cleared)."""
    register_all()
    for biz in BUSINESSES:
        monkeypatch.delenv(f"ATLAS_AGENT_MODEL_{biz.name.upper()}", raising=False)
    businesses._WARNED_PRIVATE_MODEL.clear()
    build_roster()


def test_every_non_private_business_defaults_to_hermes_on_openrouter():
    assert DEFAULT_MODEL == "openrouter:qwen/qwen3-235b-a22b-2507"
    for biz in NON_PRIVATE:
        assert ROSTER[biz.name].model == DEFAULT_MODEL, biz.name


def test_the_env_override_wins_over_the_default(monkeypatch):
    monkeypatch.setenv("ATLAS_AGENT_MODEL_FLYCHEAP", "anthropic:claude-haiku-4-5")
    build_roster()
    assert ROSTER["flycheap"].model == "anthropic:claude-haiku-4-5"
    # An untouched business is unaffected by another one's override.
    assert ROSTER["guardian"].model == DEFAULT_MODEL


def test_the_env_override_wins_over_the_businesss_own_field(monkeypatch):
    pinned = replace(BY_NAME["flycheap"], model="groq:openai/gpt-oss-120b")
    assert businesses.resolved_model(pinned) == "groq:openai/gpt-oss-120b"

    monkeypatch.setenv("ATLAS_AGENT_MODEL_FLYCHEAP", "anthropic:claude-haiku-4-5")
    assert businesses.resolved_model(pinned) == "anthropic:claude-haiku-4-5"


def test_a_businesss_own_field_beats_the_hermes_default():
    pinned = replace(BY_NAME["mirotech"], model="gemini:gemini-pro-latest")
    assert businesses.resolved_model(pinned) == "gemini:gemini-pro-latest"


def test_the_school_agent_ignores_a_cloud_pin_with_one_warning(monkeypatch, caplog):
    monkeypatch.setenv("ATLAS_AGENT_MODEL_SCHOOL",
                       "openrouter:nousresearch/hermes-4-405b")
    with caplog.at_level(logging.WARNING, logger="atlas.businesses"):
        build_roster()

    assert ROSTER["school"].model is None
    warnings = [r for r in caplog.records if r.levelname == "WARNING"
               and "school" in r.getMessage()]
    assert len(warnings) == 1
    assert "ignored" in warnings[0].getMessage()


def test_the_school_warning_fires_once_per_process_not_once_per_boot(
        monkeypatch, caplog):
    monkeypatch.setenv("ATLAS_AGENT_MODEL_SCHOOL", "openrouter:x")
    with caplog.at_level(logging.WARNING, logger="atlas.businesses"):
        build_roster()
        build_roster()
    warnings = [r for r in caplog.records if "school" in r.getMessage()
               and "ignored" in r.getMessage()]
    assert len(warnings) == 1


def test_school_with_no_pin_set_warns_not_at_all(caplog):
    with caplog.at_level(logging.WARNING, logger="atlas.businesses"):
        build_roster()
    assert not any("school" in r.getMessage() for r in caplog.records)
    assert ROSTER["school"].model is None


def test_resolved_model_direct_for_every_private_business():
    """Belt and braces: whatever PRIVATE business exists, resolved_model()
    refuses a cloud pin for it, by Need, not by name."""
    for biz in BUSINESSES:
        if biz.need is Need.PRIVATE:
            pinned = replace(biz, model="anthropic:claude-opus-4-1")
            assert businesses.resolved_model(pinned) is None
