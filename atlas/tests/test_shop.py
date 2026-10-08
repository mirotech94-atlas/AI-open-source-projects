"""The Seller: every reel's caption ends with its product's call to action,
never a link that doesn't exist; a download only after Stripe says paid."""
from __future__ import annotations

import pytest

from atlas.modules import shop
from atlas.modules.content.drafts import AI_LABEL


@pytest.mark.parametrize("source,want", [
    ("calendar:business-reels-v1-d03", "business"),
    ("calendar:reels-20260928-v2-d01", "stories"),
    ("series:2:1", "stories"),
    ("import:The Parking Spot:part 2", "stories"),
    ("weekly flycheap fares", "travel"),
])
def test_the_reel_type_comes_from_the_draft_source(source, want):
    assert shop.reel_type_of(source) == want


@pytest.fixture
def media(memory_backend):
    def set_up(source, product):
        memory_backend.responses["JOIN content_drafts d ON d.id = m.draft_id"] = {
            "source": source, "kind": "story_script"}
        memory_backend.responses["FROM shop_products WHERE reel_type"] = product
    return set_up


async def test_a_live_product_gets_its_call_to_action_before_the_label(media):
    media("calendar:business-reels-v1-d03", {"key": "business_guide", "name": "g",
          "payment_link_url": "https://buy.stripe.com/x", "status": "live"})
    out = await shop.caption_with_cta(1, f"Sell a result.\n\n{AI_LABEL}")
    assert out.endswith(AI_LABEL)
    assert shop.CTA["business"] in out


async def test_a_product_not_on_sale_yet_gets_the_follow_line(media):
    media("calendar:business-reels-v1-d03", {"key": "business_guide", "name": "g",
          "payment_link_url": None, "status": "draft"})
    out = await shop.caption_with_cta(1, f"Sell a result.\n\n{AI_LABEL}")
    assert shop.FOLLOW in out and "link in bio" not in out.lower()


async def test_a_caption_that_already_has_one_is_left_alone(media):
    media("calendar:business-reels-v1-d03", None)
    cap = f"Hi. Link in bio.\n\n{AI_LABEL}"
    assert await shop.caption_with_cta(1, cap) == cap


def test_the_payment_link_card_is_tier_2_card_only_with_an_off_switch():
    from atlas.kernel.contracts import Tier
    [a] = shop.MODULE.actions()
    assert a.tier is Tier.ASK and a.card_only and a.rollback is shop.deactivate_link


async def test_no_shop_key_means_no_card(monkeypatch):
    monkeypatch.delenv("STRIPE_SHOP_FLYCHEAP_KEY", raising=False)
    out = await shop.create_link_dry("business_guide", "flycheap", 1900)
    assert not out.ok and "STRIPE_SHOP_FLYCHEAP_KEY" in out.detail


def _buyer(ip: str = "203.0.113.9"):
    from starlette.requests import Request
    return Request({"type": "http", "client": (ip, 40000), "headers": []})


async def test_a_download_needs_a_real_session_id():
    from fastapi import HTTPException

    from atlas.api import deliver
    deliver._requests.clear()
    with pytest.raises(HTTPException):
        await deliver.deliver("../../etc/passwd", _buyer())
    with pytest.raises(HTTPException):
        await deliver.deliver("anything", _buyer())


async def test_an_unpaid_session_gets_nothing(monkeypatch):
    from fastapi import HTTPException

    from atlas.api import deliver

    async def unpaid(_sid):
        return None

    monkeypatch.setattr(deliver, "_paid_product", unpaid)
    deliver._requests.clear()
    with pytest.raises(HTTPException):
        await deliver.deliver("cs_live_" + "a" * 30, _buyer())


async def test_the_sales_count_is_its_own_number_and_unknown_when_unread(monkeypatch):
    """`sales_7d` carries dollars; the count is a check of its own, from its
    own Stripe read -- never a number lifted out of the dollars' sentence,
    and never zero for a read that failed."""
    async def read(days=7, **_kw):
        assert days == 7
        return {"a": {"name": "A", "sales": 2, "revenue_cents": 3800},
                "b": {"name": "B", "sales": 1, "revenue_cents": 1900}}, None

    monkeypatch.setattr(shop, "sales_since", read)
    obs = await shop.check_sales_count()
    assert obs.check == "sales_count_7d"
    assert obs.health.value == "ok" and obs.value == 3.0 and obs.unit == "sales, 7 d"
    dollars = await shop.check_sales()
    assert dollars.value == 57.0 and dollars.unit == "usd"

    async def refused(days=7, **_kw):
        return {}, "Stripe answered 401 for A"

    monkeypatch.setattr(shop, "sales_since", refused)
    obs = await shop.check_sales_count()
    assert obs.health.value == "unknown" and obs.value is None
    assert "401" in obs.detail
    assert [c.name for c in shop.MODULE.checks()] == ["sales_7d", "sales_count_7d"]
