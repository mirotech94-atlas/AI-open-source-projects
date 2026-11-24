"""RA-F3: drafts ATLAS starts by itself stop at the review backlog cap; one
the owner asked for (a voice or Slack source) is never held back."""
from __future__ import annotations

import pytest

from atlas.modules.content import drafts


@pytest.fixture
def backlog(memory_backend):
    def set_n(n):
        memory_backend.responses["SELECT count(*) AS n FROM content_drafts"] = {"n": n}
    return set_n


@pytest.mark.parametrize("fn", [drafts.draft, drafts.draft_dry])
async def test_a_full_backlog_refuses_an_automatic_draft(backlog, fn):
    backlog(10)
    out = await fn("short_script", "weekly")
    assert not out.ok and "already waiting for review" in out.detail


@pytest.mark.parametrize("source", ["voice:12", "slack:1790.55"])
async def test_what_he_asked_for_is_never_held_back(backlog, source):
    backlog(50)
    assert await drafts._backlog_full(source) is None


async def test_under_the_cap_is_not_held(backlog):
    backlog(3)
    assert await drafts._backlog_full("weekly") is None
