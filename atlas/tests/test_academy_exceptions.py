"""The "awaiting access" check (plan F20): paid purchases whose price is
not in the catalog and files on sale missing on disk, in the Monday
summary; UNKNOWN when it can't be read, never "none"."""
from __future__ import annotations

from atlas.modules.academy import delivery
from tests.academy_shop_fakes import SID
from tests.test_academy_access import _catalog


async def test_exceptions_list_unknown_prices_and_missing_files(memory_backend, tmp_path,
                                                                monkeypatch):
    monkeypatch.setenv(delivery.FILES_ENV, str(tmp_path))
    cat = _catalog()
    memory_backend.responses["WHERE status = 'unknown_price'"] = [
        {"session_id": SID, "first_seen_at": None, "amount_minor": 1900,
         "currency": "usd", "livemode": False}]
    ex = await delivery.exceptions(cat)
    assert ex.status == "ok" and len(ex.unknown_price) == 1
    assert ex.missing == tuple(delivery.missing_files(cat, live_only=True))
    text = delivery.exceptions_text(ex, "Mon 09:00 UTC")
    assert "19.00 USD (test mode)" in text and SID[:14] in text and SID not in text
    assert "@" not in text


async def test_an_unreadable_record_is_unknown_not_none(memory_backend):
    ex = await delivery.exceptions(_catalog())         # no canned answer: raises
    assert ex.status == "unknown"
    text = delivery.exceptions_text(ex, "Mon 09:00 UTC")
    assert "UNKNOWN, not none" in text and "None:" not in text


async def test_nothing_waiting_says_so(memory_backend, monkeypatch):
    memory_backend.responses["WHERE status = 'unknown_price'"] = []
    monkeypatch.setattr(delivery, "missing_files", lambda *a, **k: [])
    ex = await delivery.exceptions(_catalog())
    assert ex.count == 0
    assert "None: every recorded paid purchase" in delivery.exceptions_text(ex, "x")


async def test_the_monday_summary_carries_the_awaiting_access_section(monkeypatch):
    from atlas.modules.academy import growth_log

    async def found(days, now=None):
        return [], []
    monkeypatch.setattr(growth_log, "rows", found)
    monkeypatch.setattr(growth_log, "tutorial_rows", found)

    async def broken(cat):
        raise RuntimeError("db")
    monkeypatch.setattr(delivery, "exceptions", broken)
    text = await growth_log.week_section()
    assert "*Academy: awaiting access*" in text and "UNKNOWN, not none" in text

