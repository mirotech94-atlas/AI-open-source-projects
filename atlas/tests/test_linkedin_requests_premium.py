"""The Guardian Requests tab's premium layout, as read on 2026-10-12
(tests/fixtures/linkedin_requests_premium.json: the outline the scan saved,
plus what the page scripts returned, personal details beyond the screenshot
replaced).

What must hold: the layout is recognised (ok, not UNKNOWN) by its data-test
hooks; each request is recorded with requester, service, age and location,
its Q&A as words and never its age; a locked request is held with the
locked note, never drafted or answered, and alerted once; the only clicks
are on the list rows; an unlocked one still takes the existing draft path.
"""
from __future__ import annotations

import copy
import json
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from atlas.kernel.contracts import ActionResult, FailureClass, Health
from atlas.modules import linkedin
from atlas.modules.linkedin import requests as lr
from tests.test_linkedin_requests import (
    DB,
    INBOX_EMPTY,
    NAV,
    PROVIDER_EMPTY,
    FakePage,
    World,
    clean_reply,
    fake_browser,
)


@pytest.fixture(autouse=True)
def _guardian_as_fixture_page(monkeypatch):
    """Guardian is the example page here. It is retired by default (the owner,
    2026-10-29); that default is pinned in test_linkedin_retired.py."""
    monkeypatch.setitem(linkedin.PAGES, "guardian", linkedin.KNOWN_PAGES["guardian"])

FIXTURE = json.loads((Path(__file__).parent / "fixtures"
                      / "linkedin_requests_premium.json").read_text())
TAB_TEXT = NAV + FIXTURE["text"]


def premium() -> dict[str, Any]:
    return copy.deepcopy(FIXTURE["premium"])


def aged(p: dict[str, Any], age: str) -> dict[str, Any]:
    """The same page a day later: list and panes show the new age."""
    for row, pane in zip(p["items"], p["panes"], strict=True):
        row["age"] = pane["age"] = age
    return p


def tab() -> lr.Surface:
    return {s.label: s for s in lr.surfaces()}["guardian-requests"]


# ------------------------------------------------------- the selector contract

def test_every_hook_the_page_scripts_use_is_on_the_real_page():
    hooks = {t.split("=")[0] for el in FIXTURE["outline"] for t in el["tests"]}
    used = set(re.findall(r"data-test-[a-z0-9_-]+", lr.PREMIUM_JS + lr.DETAIL_JS))
    used |= {f"data-test-service-requests-detail__{name}" for name in re.findall(
        r"f\('([a-z-]+)'\)", lr.DETAIL_JS)}
    used.discard("data-test-service-requests-detail__")      # the f() prefix itself
    assert used and used <= hooks, sorted(used - hooks)
    assert {"data-test-service-marketplace-premium-service-requests__list-item",
            "data-test-service-requests-detail__location",
            "data-test-service-requests-detail__description-answer"} <= used


def test_the_page_offers_no_reply_control_only_the_upsell():
    """The real outline: one button on the page (the info icon), no form,
    no text box; the only links are the upsell and links back to the list."""
    tags = [el["tag"] for el in FIXTURE["outline"]]
    assert "form" not in tags and "textarea" not in tags
    assert not any(el["editable"] for el in FIXTURE["outline"])
    buttons = [el for el in FIXTURE["outline"] if el["tag"] == "button"]
    assert [b["label"] for b in buttons] == ["Service request types information"]
    for pane in FIXTURE["premium"]["panes"]:
        assert lr.pane_lock(pane) == (True, False)


# ------------------------------------------------------------- the parse

def test_the_premium_layout_is_recognised_and_every_request_recorded():
    read = lr.interpret(tab(), TAB_TEXT, [], [], premium())
    assert read.ok and not read.unverified_layout and not read.empty, read.reason
    got = [(i.requester, i.service_raw, i.age, i.location, i.locked) for i in read.items]
    assert got == [
        ("Ralph Listy", "Computer Networking", "2d ago", "Worcester, Massachusetts", True),
        ("--", "Cybersecurity", "5d ago", "Placeholder City, Washington", True),
        ("connecting contracts, software delivery & acceptance", "Cybersecurity",
         "6d ago", "Placeholder Metropolitan Area", True)]
    ralph = read.items[0]
    assert ralph.words.startswith("Location: Worcester, Massachusetts\n")
    assert ("What is the scope of this project? One time project" in ralph.words)
    for item in read.items:
        assert item.source == "service_request" and item.page == "guardian"
        assert "ago" not in item.words, "the age changes daily; it never reaches words"
        assert item.url == tab().url
    assert len({i.external_key for i in read.items}) == 3


def test_the_key_survives_age_name_and_order():
    """A day later the ages move, an unlocked request shows its real name,
    and a new request lands on top: the same requests keep the same keys."""
    before = {i.external_key for i in lr.interpret(tab(), TAB_TEXT, [], [],
                                                   premium()).items}
    later = aged(premium(), "3d ago")
    later["items"][1]["name"] = later["panes"][1]["creator"] = "Dana Lee"
    later["items"].reverse()
    later["panes"].reverse()
    after = {i.external_key for i in lr.interpret(tab(), TAB_TEXT, [], [],
                                                  later).items}
    assert after == before


def test_a_pane_that_did_not_load_is_unknown_and_not_recorded():
    p = premium()
    p["panes"][1] = None
    read = lr.interpret(tab(), TAB_TEXT, [], [], p)
    assert not read.ok and "request 2" in read.reason
    assert [i.requester for i in read.items] == [
        "Ralph Listy", "connecting contracts, software delivery & acceptance"]
    # A pane still showing the previous request is not this one's.
    p = premium()
    p["panes"][2] = copy.deepcopy(p["panes"][0])
    assert not lr.interpret(tab(), TAB_TEXT, [], [], p).ok


def test_a_pane_left_over_from_the_same_service_is_not_taken():
    """Requests 2 and 3 are both Cybersecurity. If the pane has not switched
    when row 3 is read, it still shows request 2: never recorded as row 3,
    and request 2 never overwritten by row 3's name."""
    p = premium()
    p["panes"][2] = copy.deepcopy(p["panes"][1])
    read = lr.interpret(tab(), TAB_TEXT, [], [], p)
    assert not read.ok and "request 3" in read.reason
    assert [i.requester for i in read.items] == ["Ralph Listy", "--"]
    # Two rows that really read the same are recorded once and said to be.
    p = premium()
    p["items"][2] = {**p["items"][1]}
    p["panes"][2] = copy.deepcopy(p["panes"][1])
    read = lr.interpret(tab(), TAB_TEXT, [], [], p)
    assert not read.ok and "exactly like another" in read.reason
    assert len(read.items) == 2


def test_something_in_direct_is_still_unknown_beside_a_known_premium_list():
    p = premium()
    p["direct_empty"] = False
    text = TAB_TEXT.replace("No direct requests.", "Sam Ortiz")
    read = lr.interpret(tab(), text, [], [], p)
    assert not read.ok and read.unverified_layout and "Direct" in read.reason
    assert len(read.items) == 3, "the premium ones are still recorded"


def test_a_reply_control_or_no_lock_is_told_apart():
    pane = premium()["panes"][0]
    assert lr.pane_lock(pane) == (True, False)
    with_button = {**pane, "controls": pane["controls"] + [
        {"tag": "button", "label": "", "text": "Message", "editable": False}]}
    assert lr.pane_lock(with_button) == (True, True)
    open_one = {**pane, "text": pane["text"].split("Unlock this request")[0]
                + "Ralph Listy\nProject details",
                "controls": [{"tag": "button", "label": "", "text": "Submit proposal",
                              "editable": False}]}
    assert lr.pane_lock(open_one) == (False, True)
    # Both at once has never been seen: recorded locked, read as unknown.
    p = premium()
    p["panes"][0] = with_button
    read = lr.interpret(tab(), TAB_TEXT, [], [], p)
    assert not read.ok and read.items[0].locked


def test_the_named_service_picks_the_label():
    """Ralph asked for Computer Networking and mentions security in his
    answers; the service LinkedIn names wins."""
    item = lr.interpret(tab(), TAB_TEXT, [], [], premium()).items[0]
    assert "security" in item.words.lower()
    assert lr.keyword_label("guardian", item.service_raw) == "computer_networking"


# ------------------------------------------------- the scan, the check, the alert

class PremiumPage(FakePage):
    """The Requests tab with its premium list: the list and pane scripts
    answer from the fixture, and a click on a list row opens its pane."""

    def __init__(self, data: dict[str, Any], lag: int = 0):
        super().__init__({s.url: "" for s in lr.surfaces()})
        # LinkedIn re-renders late: the pane keeps showing the previous
        # request for `lag` reads after a click.
        self.lag, self.stale_reads, self.shown = lag, 0, 0
        self.pages[lr.GUARDIAN_REQUESTS_URL] = TAB_TEXT
        self.pages[lr.PROVIDER_REQUESTS_URL] = PROVIDER_EMPTY
        for s in lr.surfaces():
            if s.kind == "inbox":
                self.pages[s.url] = INBOX_EMPTY
        self.data, self.selected = data, 0
        self.clicks: list[tuple[str, int]] = []

    async def evaluate(self, js):
        on_tab = self.url == lr.GUARDIAN_REQUESTS_URL
        if js == lr.PREMIUM_JS:
            if not on_tab:
                return {"list": False, "items": [], "direct_empty": False}
            items = [{**row, "selected": n == self.selected}
                     for n, row in enumerate(self.data["items"])]
            return {**self.data, "items": items, "panes": None}
        if js == lr.DETAIL_JS:
            if not on_tab:
                return None
            if self.stale_reads:
                self.stale_reads -= 1
            else:
                self.shown = self.selected
            return self.data["panes"][self.shown]
        return await super().evaluate(js)

    def locator(self, selector):
        page = self

        class Row:
            def __init__(self, n):
                self.n = n

            async def click(self):
                page.clicks.append((selector, self.n))
                page.selected, page.stale_reads = self.n, page.lag

        class Rows:
            def nth(self, n):
                return Row(n)
        return Rows()


def store(db: DB, monkeypatch) -> None:
    """The fake store with upsert_seen's locked rules (the SQL is tested
    against Postgres in test_linkedin_requests_pg.py)."""
    async def upsert_seen(item, label, how, budget, timeline):
        for r in db.requests.values():
            if (r["page"], r["source"], r["external_key"]) == (
                    item.page, item.source, item.external_key):
                prior = (r["status"], r["note"])
                if r["status"] == "suppressed":
                    pass
                elif item.locked and r["status"] in ("new", "drafted", "held"):
                    r.update(status="held", note=lr.locked_note(item))
                elif (not item.locked and r["status"] == "held"
                      and r["note"].startswith("locked:")):
                    r.update(status="new", note="")
                r.update(words=item.words, inbound_sha=item.inbound_sha)
                return {"id": r["id"], "inserted": False, "status": r["status"],
                        "note": r["note"], "prior_status": prior[0],
                        "prior_note": prior[1]}
        rid = db.add(page=item.page, source=item.source, url=item.url,
                     external_key=item.external_key, requester=item.requester,
                     words=item.words, label=label, label_by=how,
                     service_raw=item.service_raw,
                     status="held" if item.locked else "new",
                     note=lr.locked_note(item) if item.locked else "")
        r = db.requests[rid]
        return {"id": rid, "inserted": True, "status": r["status"], "note": r["note"],
                "prior_status": None, "prior_note": None}
    monkeypatch.setattr(lr, "upsert_seen", upsert_seen)


@pytest.fixture
def world(tmp_path, monkeypatch):
    path = tmp_path / "state.json"
    path.write_text(json.dumps({"last_run": datetime.now(UTC).isoformat()}))
    monkeypatch.setattr(linkedin, "STATE", path)
    monkeypatch.setattr(lr, "_prior_scan", DB._async(dict))
    monkeypatch.delenv("LINKEDIN_REPLY_AUTOSEND", raising=False)
    monkeypatch.setattr(lr, "answered_by_card", lambda: False)
    lr._LAST_SCAN.clear()
    db = DB(monkeypatch)
    store(db, monkeypatch)
    alerts: list[Any] = []

    async def notify(out):
        alerts.append(out)
    monkeypatch.setattr("atlas.kernel.comms.notify", notify)
    yield db, alerts
    lr._LAST_SCAN.clear()


async def test_the_check_records_three_locked_requests_and_alerts_once(
        world, monkeypatch, tmp_path):
    db, alerts = world
    page = PremiumPage(premium())
    fake_browser(monkeypatch, page, tmp_path)
    obs = await lr.requests_check()
    assert obs.health is Health.OK, obs.detail
    assert "3 locked" in obs.detail and obs.evidence["locked"] == 3
    rows = sorted(db.requests.values(), key=lambda r: r["id"])
    assert [(r["requester"], r["service_raw"], r["status"], r["label"]) for r in rows] == [
        ("Ralph Listy", "Computer Networking", "held", "computer_networking"),
        ("--", "Cybersecurity", "held", "cybersecurity"),
        ("connecting contracts, software delivery & acceptance", "Cybersecurity",
         "held", "cybersecurity")]
    for r in rows:
        assert r["note"].startswith(lr.LOCKED_NOTE)
    assert "listed 2d ago" in rows[0]["note"]

    # Only the list rows were clicked, and not the one already open.
    assert page.clicks == [(lr.PREMIUM_ITEM, 1), (lr.PREMIUM_ITEM, 2)]

    [alert] = alerts
    assert alert.topic == "guardian" and alert.kind == "alert"
    assert "3 Guardian service request(s) locked" in alert.title
    assert "Ralph Listy · Computer Networking · 2d ago" in alert.body
    assert "name hidden · Cybersecurity · 5d ago" in alert.body
    assert "never clicks it" in alert.body

    # The next scan (a day later: ages moved) records nothing new and says
    # nothing again.
    lr._LAST_SCAN.clear()
    later = aged(premium(), "3d ago")
    fake_browser(monkeypatch, PremiumPage(later), tmp_path)
    again = await lr.requests_check()
    assert again.health is Health.OK and len(db.requests) == 3
    assert len(alerts) == 1
    assert all(r["status"] == "held" for r in db.requests.values())


async def test_a_pane_that_renders_late_is_waited_for(world, monkeypatch, tmp_path):
    db, _alerts = world
    fake_browser(monkeypatch, PremiumPage(premium(), lag=2), tmp_path)
    obs = await lr.requests_check()
    assert obs.health is Health.OK, obs.detail
    assert len(db.requests) == 3
    fake_browser(monkeypatch, PremiumPage(premium(), lag=lr.PANE_TRIES), tmp_path)
    lr._LAST_SCAN.clear()
    late = await lr.requests_check()
    assert late.health is Health.UNKNOWN and "did not load" in late.detail
    assert len(db.requests) == 3, "nothing recorded from a pane that never switched"


async def test_a_locked_request_is_never_drafted_or_answered(world, monkeypatch,
                                                             tmp_path):
    from atlas.kernel import linkedin_replies
    db, _alerts = world
    fake_browser(monkeypatch, PremiumPage(premium()), tmp_path)
    await lr.requests_check()
    ran = World(monkeypatch, {})
    assert await linkedin_replies.tick() == 0
    assert ran.ran == [] and ran.cards == []
    for rid in db.requests:
        assert not (await lr.draft_request_reply_dry(rid)).ok


async def test_an_unlocked_request_takes_the_existing_path(world, monkeypatch,
                                                           tmp_path):
    """Premium back on: the lock box is gone. The request reopens and the
    draft and card path runs as for any service request (its tiers
    unchanged: the send is refused, the draft goes to the owner on a card)."""
    from atlas.kernel import linkedin_replies
    db, alerts = world
    fake_browser(monkeypatch, PremiumPage(premium()), tmp_path)
    await lr.requests_check()
    unlocked = premium()
    for pane in unlocked["panes"]:
        pane["text"] = pane["text"].replace("Unlock this request", "").replace(
            "Reactivate Premium", "")
        pane["controls"] = pane["controls"][1:]
    lr._LAST_SCAN.clear()
    fake_browser(monkeypatch, PremiumPage(unlocked), tmp_path)
    await lr.requests_check()
    assert [r["status"] for r in db.requests.values()] == ["new"] * 3
    assert len(alerts) == 1
    ran = World(monkeypatch, {
        "linkedin.draft_request_reply": ActionResult(
            True, "drafted", evidence={"draft_id": 21, "text": clean_reply()}),
        "linkedin.send_request_reply": ActionResult(
            False, f"dry run refused: {lr.SERVICE_FORM_UNSEEN}",
            failure_class=FailureClass.VALIDATION, dispatched=False)})
    assert await linkedin_replies.tick() == 3
    assert {a for a, _p in ran.ran} == {"linkedin.draft_request_reply",
                                        "linkedin.send_request_reply"}
    assert all(c[2].startswith("Held for you: the proposal form") for c in ran.cards)
