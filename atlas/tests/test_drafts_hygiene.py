"""2026-09-27: the drafts pile read "15 waiting — he stopped reading" when
eight were reels already rendered and seven were short scripts written from
notes nobody wrote, two of them refusals."""
from __future__ import annotations

from atlas.kernel.contracts import Health
from atlas.modules import content
from atlas.modules.content import drafts


def test_a_refusal_is_recognised_and_a_draft_is_not():
    for said in (("I’d love to turn that FlyCheap deal into a snappy video script, "
                  "but I don't have the deal."),
                 "I'm happy to turn Miro's quick-tip into a script, but",
                 "Sorry, I can't write that.",
                 "I can't draft this without the footage, but here is"):
        assert drafts._REFUSAL.search(said), said
    for real in ("Ever wonder how we turn a boring case study into a client-winning story?",
                 "FORMAT: original insane plot\nSCENES: 4",
                 "I booked 3 flights for $40. Here's how."):
        assert not drafts._REFUSAL.search(real), real


async def test_a_refusal_is_never_stored(memory_backend, monkeypatch):
    async def gather(source):
        return "<untrusted>weekly</untrusted>", None

    async def think(*a, **k):
        class C:
            text = "I'm ready to turn the agency's footage into a script, but I need it."
        return C()

    monkeypatch.setattr(drafts, "gather", gather)
    monkeypatch.setattr(drafts, "think", think)
    result = await drafts.draft("short_script", "weekly")
    assert result.ok is False and "declined" in result.detail
    assert not any("INSERT INTO content_drafts" in s for s, _ in memory_backend.sql_log)


async def test_reels_are_not_counted_in_the_review_pile(memory_backend):
    memory_backend.responses["FROM content_drafts"] = {"n": 3}
    obs = await content.check_drafts()
    sql = next(s for s, _ in memory_backend.sql_log if "FROM content_drafts" in s)
    assert "kind <> 'story_script'" in sql
    assert obs.health is Health.OK and obs.value == 3.0


def test_the_experiment_prompt_never_offers_a_note_source():
    from atlas.kernel.marketing import experiments
    assert "Never note:<text>" in experiments.SYSTEM
    assert "weekly, voice:<n>" in experiments.SYSTEM


async def test_an_agent_may_not_draft_from_a_note(memory_backend):
    """Marketing holds content_draft (Tier 1): its shift must not write a
    draft from a note nobody wrote."""
    from atlas.kernel.tools import CURRENT_AGENT
    token = CURRENT_AGENT.set("marketing")
    try:
        result = await drafts.draft("short_script", "note:tutorial on the Miro board")
        dry = await drafts.draft_dry("short_script", "note:anything")
    finally:
        CURRENT_AGENT.reset(token)
    assert result.ok is False and "only Miro writes notes" in result.detail
    assert dry.ok is False
    assert not any("INSERT INTO content_drafts" in s for s, _ in memory_backend.sql_log)


async def test_miro_himself_may_still_draft_from_a_note(memory_backend, monkeypatch):
    async def gather(source):
        return "<untrusted>note</untrusted>", None

    async def think(*a, **k):
        class C:
            text = "Ever wonder how a fare drops 40% overnight?"
        return C()

    monkeypatch.setattr(drafts, "gather", gather)
    monkeypatch.setattr(drafts, "think", think)
    memory_backend.responses["INSERT INTO content_drafts"] = {"id": 99}
    result = await drafts.draft("short_script", "note:fare drop explainer")
    assert result.ok is True
