"""Agent and client tags on agency cards (kernel/board.py).

Miro, 2026-10-27: every card from the six agency agents is headed
`[Agent · Client] #id · what`, so he knows which agent asked and for whom.
Every other card keeps `[Area] #id · what`. The renderer is pure.
"""
from __future__ import annotations

import pytest

from atlas.kernel import board, state


@pytest.mark.parametrize(("action", "agent"), [
    ("sales.send_email", "Sales"),
    ("academy.send_comment_reply", "Content"),
    ("youtube.publish_short", "Content"),
    ("community.publish_post", "Content"),
    ("reddit.submit_post", "Content"),
    ("bizops.send_invoice", "BizOps"),
    ("projects.close", "BizOps"),
    ("seo.apply_change", "SEO"),
    ("research.add_competitor", "Research"),
    ("qa.rerun", "QA"),
    ("agency.publish_site", "Websites"),
])
def test_each_agency_namespace_names_its_agent(action, agent):
    header = board.card_header(7, action, {}, None)
    assert header.startswith(f"[{agent} · "), header
    assert "] #7 · " in header


def test_the_evidence_client_beats_the_namespace_default():
    h = board.card_header(9, "sales.send_email", {"business_name": "Param Co"}, None,
                          {"client": "Joe's Plumbing"})
    assert h.startswith("[Sales · Joe's Plumbing] #9 · ")


def test_a_client_naming_param_beats_the_default():
    assert board.client_of("sales.send_email", {"lead_name": "Acme Dental"}, None) \
        == "Acme Dental"


@pytest.mark.parametrize(("action", "client"), [
    ("academy.x", "Atlas Academy"), ("youtube.x", "Atlas Academy"),
    ("community.x", "AMSM"), ("reddit.x", "Atlas Agency"), ("sales.x", "Mapfront"),
])
def test_namespace_defaults(action, client):
    assert board.client_of(action, None, None) == client


def test_the_renderer_never_reads_the_database(monkeypatch):
    async def boom(*a, **kw):
        raise AssertionError("card_header read the database")

    for name in ("pool", "fetch_all", "fetch_one", "execute_sql"):
        monkeypatch.setattr(state, name, boom)
    # A coroutine that is never awaited would not raise: prove none is made.
    h = board.card_header(1, "bizops.send_invoice", {"client": "X"}, None, None)
    assert h.startswith("[BizOps · X] #1")


def test_other_cards_keep_the_area_header():
    assert board.card_header(320, "linkedin.publish_post",
                             {"page": "flycheap", "entry_id": "B15"}, None) \
        .startswith("[LinkedIn] #320 · ")
    assert board.card_header(313, "school.draft_work", {"item_id": 587}, None) \
        .startswith("[School] #313 · ")
    assert board.agent_of("linkedin.publish_post") is None


def test_a_long_client_name_is_trimmed():
    h = board.client_of("sales.x", None, {"client": "A" * 200})
    assert len(h) <= 41
