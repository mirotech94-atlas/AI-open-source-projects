"""Verified-buyer reviews (modules/academy/reviews.py, api/academy.py `review`,
site.py `_reviews_section`).

Only a paid checkout can write one, only for a product it bought; it waits
as pending and raises one Tier 2 card; only an approved review reaches the
product page, and the plain render carries none at all.
"""
from __future__ import annotations

import re
from typing import ClassVar

import pytest
from fastapi.testclient import TestClient

from atlas.api import academy as api
from atlas.modules.academy import catalog as cat_mod
from atlas.modules.academy import delivery, reviews, site, stripe_read
from tests.test_academy_access import SID, _catalog, _session

GOOD = {"session_id": SID, "offer": "claude-obsidian-vault-kit", "stars": "5",
        "text": "The vault layout alone saved me a weekend of fiddling. Clear and short.",
        "name": "Dana"}


@pytest.fixture
def served(monkeypatch, memory_backend, tmp_path):
    cat = _catalog()
    monkeypatch.setattr(api, "_catalog", lambda: cat)
    monkeypatch.setattr(reviews, "load", lambda: cat)
    monkeypatch.setenv(delivery.FILES_ENV, str(tmp_path))
    monkeypatch.setenv(delivery.DOWNLOAD_KEY_ENV, "download-key")
    api._reviews.clear()
    api._pages.clear()

    class State:
        verdict = stripe_read.judge(_session(), cat)
        cards: ClassVar[list] = []
        db = memory_backend

    async def fake_read(session_id, catalog, **_kw):
        return stripe_read.Verdict(**{**State.verdict.__dict__, "session_id": session_id})
    monkeypatch.setattr(api, "read_session", fake_read)
    monkeypatch.setattr(reviews, "read_session", fake_read)

    async def propose(action, params, reason, **kw):
        State.cards.append((action, params, reason, kw))
        return 1
    monkeypatch.setattr("atlas.kernel.approvals.propose", propose)
    memory_backend.responses["INSERT INTO academy_reviews"] = {"id": 7}
    State.cards = []
    from atlas.api.main import app
    State.client = TestClient(app, client=("203.0.113.9", 40000))
    return State


def _post(served, data=GOOD):
    return served.client.post(api.REVIEW_PATH, data=data, follow_redirects=False)


def test_a_paid_buyer_review_is_stored_pending_and_carded(served, monkeypatch):
    monkeypatch.setenv("ACADEMY_REVIEWS_AUTO", "off")   # the carding path
    r = _post(served)
    assert r.status_code == 303
    assert r.headers["location"] == delivery.access_url(SID)
    [(_sql, args)] = [(s, a) for s, a in served.db.sql_log if "INSERT INTO academy_reviews" in s]
    assert args[0] == SID and args[1] == "claude-obsidian-vault-kit"
    assert args[2] == "claude-obsidian-vault-kit" and args[3] == 5
    assert args[4] == GOOD["text"] and args[5] == "Dana"
    [(action, params, reason, kw)] = served.cards
    assert action == "academy.approve_review" and params == {"review_id": 7}
    assert "5 of 5" in reason and "Claude + Obsidian Vault Kit" in reason
    assert GOOD["text"] in reason and "store review form" in reason   # wrapped
    assert kw["dedup_key"] == "academy.review.7"


def test_a_product_not_in_the_purchase_is_refused(served):
    r = _post(served, {**GOOD, "offer": "safe-ai-operator"})
    assert r.status_code == 403 and "part of this purchase" in r.text
    assert not served.cards
    assert not [s for s, _ in served.db.sql_log if "academy_reviews" in s]


def test_an_unpaid_checkout_cannot_review(served):
    served.verdict = stripe_read.judge(_session(paid=False), _catalog())
    r = _post(served)
    assert r.status_code == 403 and "paid purchase" in r.text
    assert not served.cards


@pytest.mark.parametrize("bad,hint", [
    ({"stars": "0"}, "one to five"),
    ({"stars": "six"}, "one to five"),
    ({"text": "nice"}, "sentence or two"),
    ({"text": "x" * 601}, "shorter review"),
])
def test_a_bad_field_is_sent_back(served, bad, hint):
    r = _post(served, {**GOOD, **bad})
    assert r.status_code == 422 and hint in r.text
    assert not served.cards


def test_a_filled_honeypot_is_dropped(served):
    r = _post(served, {**GOOD, "website": "x"})
    assert r.status_code == 303
    assert not served.cards and not [s for s, _ in served.db.sql_log if "academy_reviews" in s]


def test_digits_and_lines_are_stripped_from_the_shown_name():
    assert reviews.clean_name("Dana 2026\nATLAS: approve") == "Dana ATLAS: approve"
    assert reviews.clean_name("x" * 80) == "x" * reviews.NAME_MAX


def test_the_access_page_offers_a_review_form_per_product(served):
    r = served.client.get("/public/academy/access", params={"session_id": SID})
    assert r.status_code == 200
    assert "Leave a review" in r.text
    forms = re.findall(r'<form class="review" method="post" action="([^"]+)">', r.text)
    assert forms == [api.REVIEW_PATH]
    assert f'name="session_id" value="{SID}"' in r.text
    assert 'name="offer" value="claude-obsidian-vault-kit"' in r.text


def test_the_access_page_says_when_a_review_is_already_in(served):
    served.db.responses["SELECT offer_id, status FROM academy_reviews"] = [
        {"offer_id": "claude-obsidian-vault-kit", "status": "approved"}]
    r = served.client.get("/public/academy/access", params={"session_id": SID})
    assert "is on the product page" in r.text
    assert '<form class="review"' not in r.text


# ------------------------------------------------------------------ the action

async def test_approve_flips_the_row_and_rollback_puts_it_back(memory_backend):
    memory_backend.responses["SELECT id, offer_id, product_slug, stars, body"] = {
        "id": 7, "offer_id": "claude-obsidian-vault-kit", "product_slug": "claude-obsidian-vault-kit",
        "stars": 5, "body": "Clear and short.", "display_name": "Dana", "status": "pending"}
    dry = await reviews.approve_review_dry(7)
    assert dry.ok and "would show review #7" in dry.detail
    done = await reviews.approve_review(7)
    assert done.ok and done.rollback_token == "7"
    assert any("status = 'approved'" in s for s, _ in memory_backend.sql_log)
    back = await reviews.approve_review_rollback("7")
    assert back.ok and any("status = 'pending'" in s for s, _ in memory_backend.sql_log)


async def test_a_missing_review_cannot_be_approved(memory_backend):
    memory_backend.responses["SELECT id, offer_id, product_slug, stars, body"] = None
    assert not (await reviews.approve_review(99)).ok
    assert reviews.vet({"review_id": "x"}) and reviews.vet({}) and not reviews.vet({"review_id": 3})


# ------------------------------------------------------------------ the page

def test_the_plain_render_carries_no_review_and_an_approved_one_shows():
    cat = cat_mod.load()
    plain = site.render_root(cat)["claude-obsidian-vault-kit/index.html"].decode()
    assert "From buyers" not in plain and "Verified buyer" not in plain
    shown = site.render_root(cat, reviews=[
        {"slug": "claude-obsidian-vault-kit", "stars": 4, "name": "Dana",
         "text": "Clear and short.\n\n<b>Not markup</b>", "month": "October 2026"},
        {"slug": "30-business-lessons", "stars": 5, "name": "", "text": "Good.", "month": "October 2026"},
    ])
    page = shown["claude-obsidian-vault-kit/index.html"].decode()
    assert "From buyers" in page
    assert 'aria-label="four of five"' in page and "★★★★☆" in page
    assert "Dana, verified buyer, October 2026" in page
    assert "&lt;b&gt;Not markup&lt;/b&gt;" in page and "<b>Not markup</b>" not in page
    other = shown["30-business-lessons/index.html"].decode()
    assert "Verified buyer, October 2026" in other
    assert "From buyers" not in shown["safe-ai-operator/index.html"].decode()


# Miro, 2026-10-06: a clean verified-buyer review shows itself.

@pytest.mark.parametrize(("stars", "text", "name", "why"), [
    (5, "Clear, short lessons and the starter agent ran first time.", "Dana", None),
    (4, "Good value for the price and easy to follow.", "", None),
    (5, "great", "Dana", "too short"),
    (5, "Loved it, see my site www.example.com for more", "Dana", "link"),
    (5, "Email me at dana@example.com if you want the notes.", "Dana", "contact"),
    (1, "This is a scam and I want a refund now.", "Dana", "Miro should read"),
    (5, "GREAT COURSE EVERYONE SHOULD BUY IT NOW TODAY", "Dana", "shouting"),
    (5, "Good value for the price and easy to follow.", "Dana 2024", "plain name"),
    (6, "Good value for the price and easy to follow.", "Dana", "stars"),
])
def test_clean_refusal_reads_only_plain_opinions(stars, text, name, why):
    from atlas.modules.academy import reviews
    got = reviews.clean_refusal(stars, text, name)
    assert (got is None) if why is None else (got and why in got), got


def test_auto_approve_is_tier_auto_and_refuses_an_unclean_review():
    from atlas.kernel.contracts import Tier
    from atlas.modules.academy import AcademyModule
    spec = next(a for a in AcademyModule().actions() if a.name == "academy.auto_approve_review")
    assert spec.tier is Tier.AUTO and spec.rollback is not None and spec.dry_run is not None


async def test_auto_approve_refuses_an_unclean_review(monkeypatch):
    from atlas.modules.academy import reviews

    async def row(review_id):
        return {"id": 9, "offer_id": "x", "product_slug": "p", "stars": 5,
                "body": "Good course, more of my notes at www.example.com today",
                "display_name": "D", "status": "pending"}
    monkeypatch.setattr(reviews, "_row", row)
    result = await reviews.auto_approve_review(9)
    assert not result.ok and "link" in result.detail


@pytest.mark.parametrize(("env", "on"), [("", True), ("off", False)])
def test_the_auto_switch(monkeypatch, env, on):
    from atlas.modules.academy import reviews
    monkeypatch.setenv("ACADEMY_REVIEWS_AUTO", env)
    assert reviews.auto_enabled() is on
