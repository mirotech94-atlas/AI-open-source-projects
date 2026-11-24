"""Concept Shorts (academy/concept_short.py): the owner, 2026-10-12. Every
factual line says only what its product's catalog entry says."""
from __future__ import annotations

import pytest

from atlas.modules.academy import concept_short as cs
from atlas.modules.academy import reel, reel_design
from atlas.modules.clips import promo
from atlas.modules.content import guard

ALL = cs.concepts()


def test_there_are_concepts_and_their_keys_are_unique():
    assert len(ALL) >= 5
    assert len({c.key for c in ALL}) == len(ALL)


@pytest.mark.parametrize("c", ALL, ids=lambda c: c.id)
def test_every_evidenced_line_is_in_the_products_catalog_entry(c):
    assert cs.evidence_refusal(c, cs.product_text(c.product)) is None


@pytest.mark.parametrize("c", ALL, ids=lambda c: c.id)
def test_every_concept_passes_the_post_gates(c):
    beats, caption, youtube = cs.build(c)
    for text in (caption, youtube):
        assert promo.vet({"media_id": 1, "caption": text}) is None
        assert guard.refusal(text) is None
    assert 2 <= len(promo.youtube_title(youtube)) <= 100
    assert "link in bio" not in caption.lower()
    words = sum(len(b.text.split()) for b in beats)
    assert 25 <= words <= 120, words              # ~12-50 s at the voice's pace


@pytest.mark.parametrize("c", ALL, ids=lambda c: c.id)
def test_no_first_person_experience_in_the_synthetic_voice(c):
    said = " ".join([b["say"] for b in c.beats] + [c.close]).lower()
    for phrase in (" i ", "i'm", "i've", "my own", "i keep", "i changed", "i built"):
        assert phrase not in f" {said} ", (c.id, phrase)


def test_a_line_the_catalog_does_not_say_is_refused():
    c = ALL[0]
    bad = cs.Concept(c.id, c.plan, c.product, c.title, c.motion,
                     ({"say": "It sends the reply for you.", "evidence": "It sends the reply for you."},),
                     c.close)
    assert "is not in the" in cs.evidence_refusal(bad, cs.product_text(c.product))


def test_the_panels_are_labelled_and_never_show_a_send():
    for motion in ("draft", "gate", "files", "checklist"):
        for step in range(3):
            panel = reel_design.concept_panel(reel_design.BRAND, motion, step=step,
                                              files=["a.json", "b.json"])
            assert reel_design.CONCEPT_TAG in panel
            assert "sent: true" not in panel.lower() and ">sent<" not in panel.lower()
    assert "sent: false" in reel_design.concept_panel(reel_design.BRAND, "draft", step=2)


@pytest.mark.parametrize(("env", "on"), [("", False), ("off", False), ("on", True)])
def test_concepts_wait_for_owner(monkeypatch, env, on):
    monkeypatch.setenv(cs.ENABLE_ENV, env)
    assert cs.enabled() is on


def test_rotation_shows_the_concept_shown_longest_ago():
    from datetime import UTC, datetime, timedelta
    now = datetime.now(UTC)
    assert cs.next_concept({}) == ALL[0].key
    shown = {c.key: now - timedelta(days=i) for i, c in enumerate(ALL)}
    assert cs.next_concept(shown) == ALL[-1].key


async def test_the_dry_run_knows_a_concept_key(monkeypatch):
    # A concept Short renders only with the NotebookLM fallback on (2026-10-19).
    monkeypatch.setenv("ACADEMY_NOTEBOOKLM_FALLBACK", "true")
    dry = await reel.render_promo_reel_dry(ALL[0].key)
    assert dry.ok and ALL[0].title in dry.detail
    assert not (await reel.render_promo_reel_dry("concept:nope")).ok


def test_cues_reach_the_voice_and_never_the_screen(monkeypatch):
    """the owner, 2026-10-12: a human voice with feelings and humour. The scripts
    carry Fish S2 cues; the S2 voice gets them, captions and checks never."""
    from atlas.modules.academy import concept_short as cs
    from atlas.modules.academy.tutorial_series import CUE, plain
    from atlas.modules.academy.tutorials import voiced
    monkeypatch.delenv(cs.MODEL_ENV, raising=False)
    assert cs.fish_model() == "s2.1-pro"
    cued = [b["say"] for c in cs.concepts() for b in c.beats]
    assert any(CUE.search(s) for s in cued)
    for s in cued:
        assert "[" not in plain(s) and "]" not in plain(s), s
        assert voiced(s, "s2.1-pro") == s and voiced(s, "s1") == plain(s)


def test_a_machine_sounding_phrase_is_refused():
    from dataclasses import replace

    from atlas.modules.academy import concept_short as cs
    c = cs.concepts()[0]
    bad = replace(c, close="Let's dive in and unlock the pack at agency.example.com.")
    why = cs.evidence_refusal(bad, cs.product_text(c.product))
    assert why is not None and "machine-written" in why
