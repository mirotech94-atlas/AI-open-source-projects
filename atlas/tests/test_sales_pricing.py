"""Pricing in code (six agents, phase 6; Miro 2026-10-27): a new client is
$500 only, a returning client $300 only, a free demo first. The old $100
first-sale tier is gone. Returning means a won/paid `agency_deals` row on
the same lead, or a lead Miro linked (`returning_of_lead_id`); never "same
email domain". Unreadable deals: no figure at all, and a send is refused.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from atlas.modules.agency import _store, pricing

ROOT = Path(__file__).resolve().parent.parent


def _returns(value):
    async def fake(*_a, **_kw):
        return value
    return fake


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    for name in ("AGENCY_PRICE_LIST", "AGENCY_PRICE_FLOOR", "AGENCY_FIRST_SALE_PRICE",
                 "AGENCY_PRICE_NEW", "AGENCY_PRICE_RETURNING", "AGENCY_CONCESSIONS"):
        monkeypatch.delenv(name, raising=False)


def test_a_new_client_may_be_quoted_500_only():
    terms = pricing.Terms(500, 300, returning=False)
    assert terms.allowed() == frozenset({500})
    assert pricing.price_refusal("It's $500, all in.", terms) is None
    assert "not a price" in pricing.price_refusal("It's $300.", terms)
    assert pricing.price_refusal("It's $100.", terms)


def test_a_returning_client_may_be_quoted_300_only():
    terms = pricing.Terms(500, 300, returning=True)
    assert terms.allowed() == frozenset({300})
    assert pricing.price_refusal("For you it's $300.", terms) is None
    assert pricing.price_refusal("It's $500.", terms)


def test_unreadable_deals_name_no_figure_and_refuse_any_price():
    terms = pricing.Terms(500, 300, returning=None)
    assert terms.allowed() == frozenset()
    text = terms.describe()
    assert not re.search(r"\$\s?\d", text) and pricing.PLACEHOLDER in text
    assert pricing.price_refusal("It's $500.", terms)


def test_describe_says_the_two_prices_and_the_demo():
    text = pricing.Terms(500, 300, returning=False).describe()
    assert "$500 for a new client, $300 for a returning client, a free demo first" \
        in text
    assert "100" not in text


async def test_terms_reads_returning_from_the_lead_fresh(monkeypatch):
    monkeypatch.setattr(_store, "lead_returning", _returns(False))
    assert (await pricing.terms(7)).returning is False
    assert (await pricing.terms(7)).allowed() == {500}
    monkeypatch.setattr(_store, "lead_returning", _returns(True))
    assert (await pricing.terms(7)).allowed() == {300}

    async def boom(_lead_id):
        raise RuntimeError("db down")
    monkeypatch.setattr(_store, "lead_returning", boom)
    terms = await pricing.terms(7)
    assert terms.returning is None and terms.allowed() == frozenset()


async def test_no_lead_means_no_figure(monkeypatch):
    monkeypatch.setattr(_store, "lead_returning", _returns(True))
    assert (await pricing.terms()).returning is None


async def test_returning_is_a_won_or_paid_deal_or_a_link_never_a_domain(memory_backend):
    """A gmail.com lead with no deal and no link is new, even when another
    gmail.com lead has paid: the SQL reads this lead's deals and its link
    only, never an email domain."""
    memory_backend.responses["FROM agency_leads l WHERE l.id"] = {"returning": False}
    assert await _store.lead_returning(7) is False
    sql = " ".join(s for s, _a in memory_backend.sql_log)
    assert "returning_of_lead_id" in sql and "'won'" in sql and "'paid'" in sql
    assert "domain" not in sql.lower() and "split_part" not in sql.lower()
    assert "gmail" not in sql.lower()
    memory_backend.responses["FROM agency_leads l WHERE l.id"] = None
    with pytest.raises(RuntimeError):
        await _store.lead_returning(7)


def test_the_first_sale_env_is_ignored(monkeypatch, caplog):
    monkeypatch.setenv("AGENCY_FIRST_SALE_PRICE", "100")
    import logging
    with caplog.at_level(logging.INFO):
        pricing.warn_retired_env()
    assert "AGENCY_FIRST_SALE_PRICE" in caplog.text and "ignored" in caplog.text
    assert pricing.Terms(500, 300, returning=False).allowed() == {500}


def test_no_100_tier_anywhere_in_atlas():
    hits = []
    for path in (ROOT / "atlas").rglob("*"):
        if path.suffix not in (".py", ".md", ".txt", ".j2", ".html", ".json"):
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        for needle in ("first_sale", "DEFAULT_FIRST_SALE", "first-sale price"):
            if needle in text:
                hits.append(f"{path.relative_to(ROOT)}: {needle}")
        if path.parts[-3:-1] == ("modules", "agency") and re.search(r"\$100\b", text):
            hits.append(f"{path.relative_to(ROOT)}: $100")
    assert hits == []


def test_the_schema_has_the_sales_columns():
    schema = (ROOT / "db" / "schema.sql").read_text()
    for column in ("sales_stage TEXT", "source TEXT", "segment TEXT",
                   "returning_of_lead_id BIGINT"):
        assert f"ALTER TABLE agency_leads ADD COLUMN IF NOT EXISTS {column}" in schema
