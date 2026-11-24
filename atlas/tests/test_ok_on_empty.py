"""BUILD_ORDER 21.E.1 / FINDINGS RA-E1: a check proves its zero or reads UNKNOWN.

Each fixed check has a negative case (the empty answer that used to read OK)
and a positive control (a real zero still reads OK), so the fix is not simply
"everything is UNKNOWN now".
"""
from __future__ import annotations

import pytest

from atlas.modules.linkedin import sweep


def _doc(pending_names):
    return {"sent_invites": [{"name": n, "status": "pending"} for n in pending_names]}


async def _run(monkeypatch, state, page_text):
    async def fake_goto(page, url):
        return page_text

    monkeypatch.setattr(sweep, "goto", fake_goto)
    s = sweep.Sweep(state)
    await s.sent_invites(object())
    return s


async def test_blank_sent_page_with_pending_invites_is_not_read_as_all_accepted(
        monkeypatch):
    state = _doc(["Ada Lovelace"])
    with pytest.raises(RuntimeError, match="did not render"):
        await _run(monkeypatch, state, "LinkedIn  Sent  People")
    assert state["sent_invites"][0]["status"] == "pending"      # nothing flipped


async def test_blank_sent_page_through_the_surface_counts_as_unread(monkeypatch):
    async def fake_goto(page, url):
        return ""

    monkeypatch.setattr(sweep, "goto", fake_goto)

    async def no_shot(page, label):
        return None

    monkeypatch.setattr(sweep, "snapshot", no_shot)
    s = sweep.Sweep(_doc(["Ada Lovelace"]))
    await s.surface(object(), "sent_invites", s.sent_invites)
    assert s.unread == ["sent_invites"] and s.read == 0


async def test_a_rendered_list_without_the_person_is_a_real_change(monkeypatch):
    """Positive control: a list that rendered (a Withdraw button on another
    row) and no longer has Ada is the genuine accepted-or-withdrawn."""
    state = _doc(["Ada Lovelace"])
    s = await _run(monkeypatch, state, "Grace Hopper  Withdraw")
    assert state["sent_invites"][0]["status"] == "accepted_or_withdrawn"
    assert s.news


async def test_a_still_pending_invite_is_left_alone(monkeypatch):
    state = _doc(["Ada Lovelace"])
    await _run(monkeypatch, state, "Ada Lovelace  Withdraw")
    assert state["sent_invites"][0]["status"] == "pending"


async def test_no_pending_invites_needs_no_list(monkeypatch):
    state = _doc([])
    s = await _run(monkeypatch, state, "")
    assert not s.news
