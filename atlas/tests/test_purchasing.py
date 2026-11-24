"""purchasing — prepares, never pays.

The hard limit is the contract: no import path to the executor, no action
whose verb could pass for a payment, and Tier 3 (`purchase`, `subscribe`,
`create_charge`, ...) permanently denied by the kernel's own deny-list. A
gap in that deny-list is called out explicitly (`pay_now` slips past it),
because the fix for a gap like that is a second, narrower guard here — not
an assumption that the kernel already covers it.
"""
from __future__ import annotations

import ast
import json
import pathlib

import pytest

from atlas.kernel.contracts import ActionResult, Tier
from atlas.kernel.permissions import denial_reason
from atlas.modules import purchasing
from atlas.modules.purchasing import carts, flights

ROOT = pathlib.Path(__file__).resolve().parent.parent / "atlas"
PKG = ROOT / "modules" / "purchasing"


# ------------------------------------------------------------ hard limits

def _imports_of(path: pathlib.Path) -> set[str]:
    tree = ast.parse(path.read_text())
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            mod = node.module or ""
            if node.level:
                parts = ["atlas", "modules", "purchasing"][: 3 - (node.level - 1)]
                mod = ".".join(parts + ([mod] if mod else []))
            names.add(mod)
    return names


def test_no_file_in_the_module_imports_the_executor():
    files = list(PKG.glob("*.py"))
    assert files
    for f in files:
        for mod in _imports_of(f):
            assert "executor" not in mod, f"{f.name} imports {mod}"


def test_prepare_cart_is_not_denied():
    assert denial_reason("purchasing.prepare_cart") is None


def test_prepare_flight_is_not_denied():
    assert denial_reason("purchasing.prepare_flight") is None


def test_purchase_itself_is_permanently_denied():
    """Sanity check on the kernel's own list: 'purchase' is a DENY_VERB
    fragment, so a hypothetical purchasing.purchase could never register."""
    assert denial_reason("purchasing.purchase") is not None


def test_pay_now_is_not_caught_by_the_kernel_deny_list():
    """Named explicitly rather than assumed: 'pay' is not a DENY_VERB
    fragment (only 'payout', 'capture_payment', 'create_charge', ... are), so
    a badly named action like purchasing.pay_now would sail past
    `denial_reason`. That gap is exactly why this module needs its own
    guard, tested below, instead of trusting the kernel's list alone."""
    assert denial_reason("purchasing.pay_now") is None


_FORBIDDEN_VERB_FRAGMENTS = ("pay", "buy", "order", "checkout")


def test_no_action_verb_could_be_mistaken_for_a_payment():
    """A module-level guard, independent of the kernel's deny-list (which is
    the kernel's to own and change). Every action this module registers must
    stay clear of verbs that even look like pressing pay."""
    for action in purchasing.MODULE.actions():
        _, _, verb = action.name.partition(".")
        for fragment in _FORBIDDEN_VERB_FRAGMENTS:
            assert fragment not in verb, (
                f"{action.name}: verb contains {fragment!r}, which reads "
                f"like a payment action")


# --------------------------------------------------------------- flights

def test_search_fares_reports_no_lookup_rather_than_inventing_one():
    """flycheap has no fare-search endpoint (its own module docstring says
    so). search_fares must say that honestly, not guess a shape."""
    import asyncio
    options, reason = asyncio.run(flights.search_fares("JFK", "LAX", "2026-10-01"))
    assert options == []
    assert reason is not None
    assert "flycheap fare search not available" in reason


@pytest.mark.parametrize(
    "history, now_price, expected_fragment",
    [
        ([100.0, 150.0, 200.0], 120.0, "below the 30-day median (120 vs 150)"),
        ([100.0, 150.0, 200.0], 180.0, "above the 30-day median (180 vs 150)"),
        ([100.0, 150.0, 200.0], 150.0, "above the 30-day median (150 vs 150)"),
    ],
)
def test_good_time_three_branches(history, now_price, expected_fragment):
    assert flights.good_time(history, now_price) == expected_fragment


def test_good_time_with_no_history():
    assert flights.good_time([], 199.0) == "no history"


def test_checkout_link_is_unchanged():
    option = {"url": "https://flycheap.example.com/checkout?id=abc123&pre=filled"}
    assert flights.checkout_link(option) == option["url"]


# ------------------------------------------------------------------ carts

async def test_find_prices_without_ebay_env_gives_a_reason(monkeypatch):
    monkeypatch.delenv("EBAY_CLIENT_ID", raising=False)
    monkeypatch.delenv("EBAY_CLIENT_SECRET", raising=False)
    monkeypatch.delenv("EBAY_REFRESH_TOKEN", raising=False)
    options, reason = await carts.find_prices(["240GB SSD"])
    assert options == []
    assert reason is not None
    assert "EBAY_CLIENT_ID" in reason


async def test_find_prices_reports_the_best_match_per_item(monkeypatch):
    monkeypatch.setenv("EBAY_CLIENT_ID", "id")
    monkeypatch.setenv("EBAY_CLIENT_SECRET", "secret")
    monkeypatch.setenv("EBAY_REFRESH_TOKEN", "refresh")

    async def fake_token():
        return "tok"

    async def fake_search_one(token, query):
        assert token == "tok"
        return {"title": f"best {query}", "price": 9.99, "currency": "USD",
                "url": f"https://ebay.example/{query}", "source": "ebay",
                "seen_at": "2026-09-24T00:00:00+00:00"}

    monkeypatch.setattr(carts, "_token", fake_token)
    monkeypatch.setattr(carts, "_search_one", fake_search_one)

    options, reason = await carts.find_prices(["SSD", "RAM"])
    assert reason is None
    assert [o["title"] for o in options] == ["best SSD", "best RAM"]


async def test_find_prices_skips_items_with_no_listings(monkeypatch):
    monkeypatch.setenv("EBAY_CLIENT_ID", "id")
    monkeypatch.setenv("EBAY_CLIENT_SECRET", "secret")
    monkeypatch.setenv("EBAY_REFRESH_TOKEN", "refresh")

    async def fake_token():
        return "tok"

    async def fake_search_one(token, query):
        return None if query == "unobtainium" else {
            "title": query, "price": 1.0, "currency": "USD",
            "url": "https://ebay.example/x", "source": "ebay",
            "seen_at": "2026-09-24T00:00:00+00:00"}

    monkeypatch.setattr(carts, "_token", fake_token)
    monkeypatch.setattr(carts, "_search_one", fake_search_one)

    options, reason = await carts.find_prices(["unobtainium", "SSD"])
    assert reason is None
    assert [o["title"] for o in options] == ["SSD"]


async def test_find_prices_all_items_empty_is_a_reason_not_an_empty_success(monkeypatch):
    monkeypatch.setenv("EBAY_CLIENT_ID", "id")
    monkeypatch.setenv("EBAY_CLIENT_SECRET", "secret")
    monkeypatch.setenv("EBAY_REFRESH_TOKEN", "refresh")

    async def fake_token():
        return "tok"

    async def fake_search_one(token, query):
        return None

    monkeypatch.setattr(carts, "_token", fake_token)
    monkeypatch.setattr(carts, "_search_one", fake_search_one)

    options, reason = await carts.find_prices(["unobtainium"])
    assert options == []
    assert reason is not None
    assert "no eBay results" in reason


async def test_find_prices_token_failure_is_a_reason(monkeypatch):
    monkeypatch.setenv("EBAY_CLIENT_ID", "id")
    monkeypatch.setenv("EBAY_CLIENT_SECRET", "secret")
    monkeypatch.setenv("EBAY_REFRESH_TOKEN", "refresh")

    async def failing_token():
        raise RuntimeError("token endpoint down")

    monkeypatch.setattr(carts, "_token", failing_token)

    options, reason = await carts.find_prices(["SSD"])
    assert options == []
    assert "eBay token request failed" in reason


# -------------------------------------------------------- prepare_flight

async def test_prepare_flight_fails_and_writes_nothing_when_search_cannot_run(
    memory_backend, monkeypatch,
):
    async def fake_search_fares(origin, destination, date):
        return [], "flycheap fare search not available: no endpoint"

    monkeypatch.setattr(flights, "search_fares", fake_search_fares)

    result = await purchasing.prepare_flight("JFK", "LAX", "2026-10-01")

    assert result.ok is False
    assert result.detail == "flycheap fare search not available: no endpoint"
    assert memory_backend.sql_log == []


async def test_prepare_flight_fails_when_no_fares_found(memory_backend, monkeypatch):
    async def fake_search_fares(origin, destination, date):
        return [], None

    monkeypatch.setattr(flights, "search_fares", fake_search_fares)

    result = await purchasing.prepare_flight("JFK", "LAX", "2026-10-01")

    assert result.ok is False
    assert memory_backend.sql_log == []


async def test_prepare_flight_stamps_every_option_with_seen_at_and_source(
    memory_backend, monkeypatch,
):
    async def fake_search_fares(origin, destination, date):
        return [
            {"title": "Cheap Air 100", "price": 210.0, "currency": "USD",
             "url": "https://flycheap.example.com/go/1"},
            {"title": "Cheap Air 200", "price": 250.0, "currency": "USD",
             "url": "https://flycheap.example.com/go/2"},
        ], None

    monkeypatch.setattr(flights, "search_fares", fake_search_fares)
    memory_backend.responses["INSERT INTO purchasing_prepared"] = {"id": 11}

    result = await purchasing.prepare_flight("JFK", "LAX", "2026-10-01")

    assert result.ok is True
    sql, args = memory_backend.sql_log[-1]
    assert "INSERT INTO purchasing_prepared" in sql
    stored = json.loads(args[1])
    assert len(stored) == 2
    for option in stored:
        assert option["seen_at"]
        assert option["source"] == "flycheap"
    assert result.rollback_token == "11"
    # The checkout link is the cheapest option's own url, unchanged.
    assert args[2] == "https://flycheap.example.com/go/1"
    assert result.evidence["checkout_url"] == "https://flycheap.example.com/go/1"
    # good_time runs on every call; with no history source yet it always
    # says so, honestly, rather than being computed and discarded.
    assert result.evidence["good_time"] == "no history"
    assert "no history" in result.detail
    # Invariant 6: a figure on screen carries the time it was read.
    best = min(stored, key=lambda o: o["price"])
    assert best["seen_at"] in result.detail


async def test_prepare_flight_dry_run_does_not_write(memory_backend, monkeypatch):
    async def fake_search_fares(origin, destination, date):
        return [{"title": "x", "price": 1.0, "currency": "USD",
                  "url": "https://x"}], None

    monkeypatch.setattr(flights, "search_fares", fake_search_fares)

    result = await purchasing.prepare_flight_dry("JFK", "LAX", "2026-10-01")

    assert result.ok is True
    assert "would prepare" in result.detail
    assert memory_backend.sql_log == []


# ---------------------------------------------------------- prepare_cart

async def test_prepare_cart_rejects_empty_items(memory_backend):
    result = await purchasing.prepare_cart("   ,  ,")
    assert result.ok is False
    assert memory_backend.sql_log == []


async def test_prepare_cart_fails_and_writes_nothing_when_search_cannot_run(
    memory_backend, monkeypatch,
):
    async def fake_find_prices(items):
        return [], "eBay search not available: missing env EBAY_CLIENT_ID"

    monkeypatch.setattr(carts, "find_prices", fake_find_prices)

    result = await purchasing.prepare_cart("240GB SSD, Cat6 cable")

    assert result.ok is False
    assert "EBAY_CLIENT_ID" in result.detail
    assert memory_backend.sql_log == []


async def test_prepare_cart_stamps_every_option_and_stores(memory_backend, monkeypatch):
    async def fake_find_prices(items):
        assert items == ["240GB SSD", "Cat6 cable 50ft"]
        return [
            {"title": "240GB SSD", "price": 19.99, "currency": "USD",
             "url": "https://ebay.example/ssd"},
            {"title": "Cat6 cable 50ft", "price": 12.5, "currency": "USD",
             "url": "https://ebay.example/cable"},
        ], None

    monkeypatch.setattr(carts, "find_prices", fake_find_prices)
    memory_backend.responses["INSERT INTO purchasing_prepared"] = {"id": 42}

    result = await purchasing.prepare_cart("240GB SSD, Cat6 cable 50ft")

    assert result.ok is True
    sql, args = memory_backend.sql_log[-1]
    assert "INSERT INTO purchasing_prepared" in sql
    stored = json.loads(args[1])
    assert len(stored) == 2
    for option in stored:
        assert option["seen_at"]
        assert option["source"] == "ebay"
    assert result.rollback_token == "42"
    # No single cart link: eBay has no cart-build endpoint, and picking one
    # item's listing to stand in for the whole cart would be misleading.
    assert args[2] is None
    assert result.evidence["checkout_url"] is None
    # Invariant 6: a figure on screen (the total) carries the time it was
    # read — here, the latest of the per-item reads that fed it.
    latest = max(o["seen_at"] for o in stored)
    assert latest in result.detail


async def test_prepare_cart_dry_run_does_not_write(memory_backend):
    result = await purchasing.prepare_cart_dry("240GB SSD")
    assert result.ok is True
    assert memory_backend.sql_log == []


# ----------------------------------------------------------- rollback

async def test_rollback_marks_the_row_withdrawn(memory_backend):
    result = await purchasing._withdraw("11")
    assert result.ok is True
    sql, args = memory_backend.sql_log[-1]
    assert "UPDATE purchasing_prepared" in sql
    assert "withdrawn" in sql.lower()
    assert args == (11,)


async def test_rollback_of_a_token_that_is_not_an_id_fails_cleanly(memory_backend):
    """`rollback` is typed to take a str, but a row insert that returned no
    id would hand this a rollback_token of None. That must not raise."""
    result = await purchasing._withdraw(None)
    assert result.ok is False
    assert memory_backend.sql_log == []


# --------------------------------------------------------------- module

def test_module_identity_and_env():
    assert purchasing.MODULE.name == "purchasing"
    assert purchasing.MODULE.requires_env == ()
    assert set(purchasing.MODULE.optional_env) == {
        "EBAY_CLIENT_ID", "EBAY_CLIENT_SECRET", "EBAY_REFRESH_TOKEN",
    }


def test_module_has_no_checks():
    assert purchasing.MODULE.checks() == ()


def test_both_actions_are_tier_auto_with_dry_run_and_rollback():
    actions = {a.name: a for a in purchasing.MODULE.actions()}
    assert set(actions) == {"purchasing.prepare_flight", "purchasing.prepare_cart"}
    for action in actions.values():
        assert action.tier is Tier.AUTO
        assert action.dry_run is not None
        assert action.rollback is not None
        assert action.irreversible is False


async def test_action_run_callables_match_the_module_functions():
    actions = {a.name: a for a in purchasing.MODULE.actions()}
    assert actions["purchasing.prepare_flight"].run is purchasing.prepare_flight
    assert actions["purchasing.prepare_cart"].run is purchasing.prepare_cart


def test_action_result_is_the_declared_contract():
    """Belt and braces: run/dry_run always hand back an ActionResult, never
    a bare tuple or dict, so the kernel's Tier-1 checks always have one."""
    import inspect
    for action in purchasing.MODULE.actions():
        sig = inspect.signature(action.run)
        assert sig.return_annotation in (ActionResult, "ActionResult")
