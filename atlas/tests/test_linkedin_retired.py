"""Retired LinkedIn pages. FlyCheap's left on 2026-10-09 (Miro: "delete
flycheap page and only keep guardian for now"); Guardian's (Vault) left on
2026-10-29 (Miro: "delete flycheap and guardian, we don't use them
anymore"). Deleting a page is Tier 3 and is never automated; ATLAS stops
using it. Its state is left untouched."""
from __future__ import annotations

import asyncio

from atlas.modules import linkedin
from atlas.modules.linkedin import requests as lr


def test_default_active_pages_leave_flycheap_and_guardian_out():
    assert "flycheap" not in linkedin.PAGES
    assert "guardian" not in linkedin.PAGES
    assert linkedin.PAGES_ACTIVE_DEFAULT == "academy"
    # the data still has a home
    assert "flycheap" in linkedin.KNOWN_PAGES
    assert "guardian" in linkedin.KNOWN_PAGES


def test_active_keys_follow_the_env_list():
    assert linkedin.active_keys("guardian") == ("guardian",)
    assert linkedin.active_keys("guardian, flycheap") == ("guardian", "flycheap")
    assert linkedin.active_keys("nonsense,guardian,guardian") == ("guardian",)
    # academy only counts when its page id made it a known page
    assert ("academy" in linkedin.active_keys("guardian,academy")) == (
        "academy" in linkedin.KNOWN_PAGES)


def test_no_retired_page_check_is_registered():
    names = [c.name for c in linkedin.LinkedInModule().checks()]
    assert not any(n.startswith(("flycheap", "guardian")) for n in names)
    # With no page that has a surface, the requests check would read nothing
    # and say OK; it is not registered at all (invariant 7).
    assert "requests" not in names


def test_a_retired_inbox_is_never_scanned():
    labels = [s.label for s in lr.surfaces()]
    assert not any(lb.startswith(("flycheap", "guardian")) for lb in labels)
    assert "provider-requests" not in labels


def test_an_active_page_registers_the_requests_check(monkeypatch):
    monkeypatch.setitem(linkedin.PAGES, "guardian", linkedin.KNOWN_PAGES["guardian"])
    names = [c.name for c in linkedin.LinkedInModule().checks()]
    assert "requests" in names and "guardian_post_runway" in names


def test_publish_gate_refuses_a_retired_page():
    from atlas.modules.linkedin.publish import gate
    entry, why = gate({}, "flycheap")
    assert entry is None and "no such page" in why


def test_retired_page_requests_are_neither_drafted_nor_sent(monkeypatch):
    async def get_request(_id):
        return {"id": 1, "page": "flycheap", "status": "new", "source": "page_message",
                "inbound_sha": "x", "replied_sha": None, "url": "", "words": "hi"}

    async def get_draft(_id):
        return {"id": 9, "request_id": 1, "status": "draft",
                "send_attempted_at": None, "inbound_sha": "x", "body": "hello"}

    monkeypatch.setattr(lr, "get_request", get_request)
    monkeypatch.setattr(lr, "get_draft", get_draft)
    req, why = asyncio.run(lr._draftable(1))
    assert req is None and "not active" in why
    draft, why = asyncio.run(lr._guard_send(9))
    assert draft is None and "not active" in why


def test_awaiting_reply_skips_a_retired_page(monkeypatch):
    # guardian stands in for an active page here.
    monkeypatch.setitem(linkedin.PAGES, "guardian", linkedin.KNOWN_PAGES["guardian"])
    async def _all(sql, *args):
        return [{"id": 1, "page": "flycheap", "source": "page_message"},
                {"id": 2, "page": "guardian", "source": "page_message"}]

    monkeypatch.setattr(lr, "_all", _all)
    rows = asyncio.run(lr.awaiting_reply(10))
    assert [r["id"] for r in rows] == [2]


def test_open_requests_leave_a_retired_page_out(monkeypatch):
    monkeypatch.setitem(linkedin.PAGES, "guardian", linkedin.KNOWN_PAGES["guardian"])
    async def _all(sql, *args):
        return [{"id": 1, "page": "flycheap"}, {"id": 2, "page": "guardian"}]

    monkeypatch.setattr(lr, "_all", _all)
    assert [r["id"] for r in asyncio.run(lr.open_requests())] == [2]
