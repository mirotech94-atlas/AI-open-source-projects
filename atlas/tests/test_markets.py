"""Markets: watch only. No action can trade; a price that can't be read is
UNKNOWN, never guessed."""
from __future__ import annotations

from datetime import UTC, datetime

import httpx

from atlas.modules import markets


def test_there_is_nothing_that_can_trade():
    """The alert sends the owner a message; the two paper actions move PAPER books
    on his approval: Tier 2, card-only (never offered to a model),
    reversible. No name says trade, and none may be added: money out is
    Tier 3, permanently denied. That the paper ones make no network write
    is proven in tests/test_markets_playlist.py and _ideas.py."""
    from atlas.kernel.contracts import Tier
    from atlas.kernel.permissions import classify
    acts = markets.MODULE.actions()
    assert [a.name for a in acts] == [
        "markets.alert", "markets.paper_rebalance", "markets.paper_idea"]
    for a in acts:
        assert not any(w in a.name for w in ("trade", "order", "buy", "sell",
                                             "transfer", "purchase", "withdraw"))
        assert a.rollback is not None and not a.irreversible
    alert, *paper = acts
    assert alert.tier is Tier.AUTO and set(alert.params) == {"key"}
    assert alert.dry_run is not None
    assert classify(alert).allowed and classify(alert).tier is Tier.AUTO
    for a in paper:
        assert a.tier is Tier.ASK and a.card_only


async def test_nothing_readable_is_unknown(monkeypatch):
    async def none(client, sym):
        return None

    monkeypatch.setattr(markets, "quote", none)
    obs = await markets.check_basket()
    assert obs.health.value == "unknown" and obs.value is None


async def test_the_basket_move_is_measured_with_its_read_time(monkeypatch):
    at = datetime(2026, 9, 28, 20, 0, tzinfo=UTC)

    async def q(client, sym):
        return {"last": 110.0, "prev": 100.0, "at": at}

    async def paper(quotes):
        return 110.0, at

    monkeypatch.setattr(markets, "quote", q)
    monkeypatch.setattr(markets, "_paper", paper)
    monkeypatch.setenv("MARKETS_WATCHLIST", "AAA,BBB")
    obs = await markets.check_basket()
    assert obs.value == 10.0 and "+10.00%" in obs.detail
    assert "20:00 UTC" in obs.detail and "never a trade" in obs.detail


async def test_a_bad_answer_reads_as_none():
    async def handler(request):
        return httpx.Response(200, json={"chart": {"result": None}})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as c:
        assert await markets.quote(c, "MSFT") is None


