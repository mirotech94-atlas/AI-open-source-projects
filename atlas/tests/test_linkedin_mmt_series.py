"""The TutorSite (MMT) LinkedIn page: a fixed, fact-backed post series.

What must hold: the page is known but never active by default; it never
publishes itself and its drafts are the owner's card; every post cites facts whose
quotes are verbatim in a dated snapshot of the public site; no planned feature,
no invented number, no model in the refill; a fact that left the site holds
its posts; an unreadable site is UNKNOWN, never OK.
"""
from __future__ import annotations

import re
from datetime import UTC, datetime
from pathlib import Path

import pytest

from atlas.kernel.contracts import Health
from atlas.modules import _scrape, linkedin
from atlas.modules.linkedin import mmt_facts, mmt_series, publish, store, sweep

FIX = Path(__file__).parent / "fixtures" / "mmt_site"


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip()


@pytest.fixture
def mmt_active(monkeypatch):
    monkeypatch.setitem(linkedin.PAGES, "mmt", linkedin.KNOWN_PAGES["mmt"])


# ----------------------------------------------------------- page + locks

def test_mmt_page_is_known_but_not_active_by_default(monkeypatch):
    spec = linkedin.KNOWN_PAGES["mmt"]
    assert spec["id"] == "146926885"
    assert spec["link"] == "https://tutoring.example"
    monkeypatch.delenv(linkedin.PAGES_ACTIVE_ENV, raising=False)
    assert "mmt" not in linkedin.active_keys()


def test_mmt_never_autopublishes(mmt_active, monkeypatch):
    monkeypatch.setenv(publish.AUTOPUBLISH_ENV, "1")
    assert publish.autopublish_owns("mmt") is False
    entry, why, _kind = publish.autopublish_refusal({}, "mmt")
    assert entry is None and why


def test_mmt_drafts_are_not_auto_approved(mmt_active, monkeypatch):
    monkeypatch.setenv(publish.AUTOAPPROVE_ENV, "1")
    why = publish.autoapprove_refusal({}, "mmt")
    assert why and "read and approve" in why


def test_mmt_reshare_to_profile_defaults_off(mmt_active, tmp_path, monkeypatch):
    import asyncio

    async def fake_pending(key):
        return set()

    async def boom(*a, **k):
        raise AssertionError("the model must not be used")

    monkeypatch.setattr(sweep, "pending_draft_ids", fake_pending)
    monkeypatch.setattr(sweep, "draft_posts", boom)
    s = sweep.Sweep({})
    asyncio.run(s.refill("mmt"))
    _entries, posting, _holder = store.bank(s.state, "mmt")
    assert posting["reshare_to_profile"] is False
    assert posting["enabled"] is True


# ----------------------------------------------------------------- facts

def test_every_fact_quote_is_in_the_site_snapshot():
    assert mmt_facts.FACTS
    for fact in mmt_facts.FACTS.values():
        page = _norm((FIX / f"{fact.source}.txt").read_text())
        assert _norm(fact.quote) in page, fact.id


def test_every_fact_has_source_url_and_read_time():
    for fact in mmt_facts.FACTS.values():
        assert fact.source_url.startswith("https://tutoring.example"), fact.id
        assert fact.read_at.tzinfo is not None, fact.id


# ---------------------------------------------------------------- series

def test_every_post_cites_facts_and_only_known_ones():
    for post in mmt_series.SERIES:
        assert post.fact_ids, post.id
        assert set(post.fact_ids) <= set(mmt_facts.FACTS), post.id


def test_series_order_starts_from_the_beginning():
    assert [p.id for p in mmt_series.SERIES] == [f"M{n:02d}" for n in range(1, 13)]
    titles = [p.title.lower() for p in mmt_series.SERIES]
    assert "what" in titles[0] and "problem" in titles[1]
    assert "how it works" in titles[6] and "what we" in titles[9]


@pytest.mark.parametrize("post", mmt_series.SERIES, ids=lambda p: p.id)
def test_posts_pass_the_publish_gate(post, mmt_active):
    state = {"mmt_page": {"post_bank": [
        {"id": post.id, "status": "approved", "text": post.text}],
        "posting": {"enabled": True}}}
    entry, why = publish.gate(state, "mmt")
    assert entry is not None, why
    assert post.text.count("https://tutoring.example") == 1
    assert "http" not in post.text.replace("https://tutoring.example", "")


def test_posts_carry_no_planned_features():
    for post in mmt_series.SERIES:
        for pat in mmt_facts.PLANNED_DENY:
            assert not pat.search(post.text), (post.id, pat.pattern)


OWNER_BLOCKED = [
    "Join our WhatsApp community today.", "Tap the join link below.",
    "Book group classes in the portal.", "Per-group billing, per-group pricing.",
    "A weekly group digest for parents.", "Our group mailer reaches everyone.",
    "The portal mirrors the WhatsApp community.", "Bookable groups for every term.",
    "Group billing is built in.", "Community chat for every centre.",
]
OWNER_SAFE = [
    "The 1:1 WhatsApp bot tells the parent.", "Four connected portals.",
    "Take a 14-day free trial or a guided walkthrough.", "Request a seat.",
]


def _blocked(text):
    return [p.pattern for p in mmt_facts.BLOCKLIST if p.search(text)]


@pytest.mark.parametrize("text", OWNER_BLOCKED)
def test_blocklist_confirmed_by_platform_owner_catches_it(text):
    assert _blocked(text), text
    assert any(p.search(text) for p in mmt_facts.PLANNED_DENY)


@pytest.mark.parametrize("text", OWNER_SAFE)
def test_blocklist_does_not_hit_the_safe_items(text):
    assert not _blocked(text), text


def test_the_twelve_posts_pass_the_blocklist():
    assert len(mmt_series.SERIES) == 12
    for post in mmt_series.SERIES:
        assert not _blocked(post.text), (post.id, _blocked(post.text))


def test_series_validation_refuses_a_post_with_a_blocked_term(monkeypatch):
    import dataclasses
    good = mmt_series.SERIES[0]
    bad = dataclasses.replace(good, id="X99",
                              text=good.text + " Join our WhatsApp community.")
    monkeypatch.setattr(mmt_series, "SERIES", (good, bad))
    ids = [e["series_id"] for e in mmt_series.series_due({"mmt_page": {}})]
    assert ids == [good.id]


def test_posts_carry_no_numbers_without_a_fact():
    for post in mmt_series.SERIES:
        allowed = set()
        for fid in post.fact_ids:
            allowed |= set(re.findall(r"\d+", mmt_facts.FACTS[fid].quote))
        for run in re.findall(r"\d+", post.text):
            assert run in allowed, (post.id, run)


def test_posts_have_no_em_dash_and_fit_the_length():
    for post in mmt_series.SERIES:
        assert "—" not in post.text and "–" not in post.text, post.id
        assert len(post.text) <= 1300, post.id


# ---------------------------------------------------------------- refill

async def test_refill_for_mmt_uses_the_series_not_the_model(mmt_active, monkeypatch):
    async def fake_pending(key):
        return set()

    async def boom(*a, **k):
        raise AssertionError("the model must not be used")

    from atlas.kernel import llm
    monkeypatch.setattr(llm, "think", boom)
    monkeypatch.setattr(sweep, "pending_draft_ids", fake_pending)
    s = sweep.Sweep({"mmt_page": {"post_bank": [], "posting": {"enabled": True}}})
    await s.refill("mmt")
    entries, posting, _ = store.bank(s.state, "mmt")
    assert [e["series_id"] for e in entries] == ["M01", "M02", "M03"]
    for e in entries:
        assert e["kind"] == "text" and e["status"] == "draft"
        assert e["drafted_by"] == "atlas-mmt-series"
        assert e["fact_ids"] and e["text"]
    assert posting.get("reshare_to_profile") is False


async def test_refill_never_repeats_a_series_post(mmt_active, monkeypatch):
    async def fake_pending(key):
        return set()

    monkeypatch.setattr(sweep, "pending_draft_ids", fake_pending)
    bank = [{"id": f"X{p.id}", "series_id": p.id, "status": "published", "text": "x"}
            for p in mmt_series.SERIES]
    s = sweep.Sweep({"mmt_page": {"post_bank": bank, "posting": {"enabled": True}}})
    await s.refill("mmt")
    entries, _, _ = store.bank(s.state, "mmt")
    assert len(entries) == len(bank)
    assert any("series complete" in line for line in s.log)

    s2 = sweep.Sweep({"mmt_page": {"post_bank": [
        {"id": "M01", "series_id": "M01", "status": "rejected", "text": "x"}],
        "posting": {"enabled": True}}})
    await s2.refill("mmt")
    ids = [e.get("series_id") for e in store.bank(s2.state, "mmt")[0]]
    assert ids.count("M01") == 1 and "M02" in ids


def test_a_post_whose_fact_left_the_site_is_held():
    first = mmt_series.SERIES[0]
    gone = first.fact_ids[0]
    live_ok = {fid: (fid != gone) for fid in mmt_facts.FACTS}
    due = mmt_series.series_due({}, live_ok=live_ok)
    ids = [d["series_id"] for d in due]
    assert first.id not in ids and ids
    assert all(d["kind"] == "text" and d["fact_ids"] for d in due)


# ------------------------------------------------------------ facts check

def _site(monkeypatch, *, blocked=False, drop: str | None = None):
    async def fake_fetch(url, *, agent, client=""):
        assert agent == "mmt"
        if blocked:
            return _scrape._blocked(url, "robots")
        for stem in ("home", "pricing", "tour", "trust"):
            fx = mmt_facts.SOURCES[stem]
            if url == fx:
                text = (FIX / f"{stem}.txt").read_text()
                if drop:
                    text = text.replace(drop, "")
                return _scrape.Scraped(url, url, 200, text, datetime.now(UTC))
        return _scrape.Scraped(url, url, 404, "", datetime.now(UTC))

    monkeypatch.setattr(_scrape, "fetch", fake_fetch)


async def test_facts_check_is_unknown_when_the_site_cannot_be_read(monkeypatch):
    _site(monkeypatch, blocked=True)
    obs = await mmt_series.mmt_facts_fresh()
    assert obs.health is Health.UNKNOWN


async def test_facts_check_degraded_names_the_missing_fact(monkeypatch):
    fact = mmt_facts.FACTS["trial.fourteen"]
    _site(monkeypatch, drop=fact.quote)
    obs = await mmt_series.mmt_facts_fresh()
    assert obs.health is Health.DEGRADED and "trial.fourteen" in obs.detail


async def test_facts_check_ok_carries_the_read_time(monkeypatch):
    _site(monkeypatch)
    obs = await mmt_series.mmt_facts_fresh()
    assert obs.health is Health.OK
    assert re.search(r"\d{4}-\d{2}-\d{2}", obs.detail)


# ----------------------------------------- MMT post cards: kernel path only

@pytest.fixture
def mmt_state(tmp_path, monkeypatch, mmt_active):
    import json
    from datetime import timedelta
    monkeypatch.setattr(store, "RUNS", tmp_path / "runs")
    monkeypatch.setattr(linkedin, "STATE", tmp_path / "state.json")
    monkeypatch.delenv(publish.AUTOPUBLISH_ENV, raising=False)
    post = mmt_series.SERIES[0]
    (tmp_path / "state.json").write_text(json.dumps({
        "last_run": (datetime.now(UTC) - timedelta(hours=1)).isoformat(),
        "last_run_result": {"outcome": "ok"}, "runs": 9, "alerts": [],
        "mmt_page": {"post_bank": [
            {"id": post.id, "status": "approved", "text": post.text}],
            "posting": {"enabled": True, "hours": [0, 24]}}}))
    return post


async def test_mmt_post_goes_out_only_as_a_tier2_card(mmt_state, monkeypatch):
    from atlas.kernel.contracts import Tier
    # Even with every automatic switch on, the page is never auto-published.
    monkeypatch.setenv(publish.AUTOPUBLISH_ENV, "1")
    monkeypatch.setenv(publish.AUTOAPPROVE_ENV, "1")
    assert "mmt" not in publish.AUTOPUBLISH_PAGES
    assert publish.autopublish_owns("mmt") is False
    from atlas.kernel import linkedin_autopost
    assert "mmt" not in getattr(linkedin_autopost, "AUTOPUBLISH_PAGES", ())
    acts = {a.name: a for a in linkedin.MODULE.actions()}
    assert acts["linkedin.publish_post"].tier is Tier.ASK
    assert acts["linkedin.publish_post"].irreversible
    # The daily signal is the card: it names the Tier 2 action and nothing else.
    sigs = [s for s in await linkedin.MODULE.signals() if "mmt" in str(s.params)]
    assert [s.suggests for s in sigs] == ["linkedin.publish_post"]
    assert sigs[0].params["page"] == "mmt"
    assert mmt_state.id in sigs[0].title
    # And an unapproved draft is never approved by ATLAS.
    assert "read and approve" in publish.autoapprove_refusal({}, "mmt")


def test_mmt_card_is_answered_through_slack_decisions_only():
    from atlas.api import slack_decisions
    d = slack_decisions.classify("approve 41")
    assert d and d.kind == "approve" and d.ids == (41,)
    # Silence is not a decision.
    assert slack_decisions.classify("") is None


async def test_an_unanswered_mmt_card_expires_never_runs(monkeypatch):
    from atlas.kernel import approvals, judgment
    seen: list[str] = []

    async def fetch_all(sql, *args):
        seen.append(" ".join(sql.split()))
        return [{"id": 77, "action": "linkedin.publish_post"}]

    async def nothing(*a, **k):
        return None

    monkeypatch.setattr(approvals, "fetch_all", fetch_all)
    monkeypatch.setattr(approvals, "audit", nothing)
    monkeypatch.setattr(approvals, "_expiry_notice", nothing)
    monkeypatch.setattr(approvals, "_open_cards_notice", nothing)
    monkeypatch.setattr(judgment, "record_outcome", nothing)
    assert await approvals.expire_stale() == 1
    assert "SET status='expired'" in seen[0]
    assert "status='pending'" in seen[0] and "expires_at < now()" in seen[0]
    assert "executed" not in seen[0] and "approved" not in seen[0]
