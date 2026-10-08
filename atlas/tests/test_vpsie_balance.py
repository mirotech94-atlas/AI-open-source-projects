"""VPSie runway is computed from what is actually billed (2026-09-25: a
top-up to 791 still paged Miro as "1.1 months" because the list price was
used; 791 / 249 billed is 3.2 months)."""
from __future__ import annotations

from atlas.kernel.contracts import Health
from atlas.modules import vpsie


def _dashboard(balance, listed, actual):
    async def fake(path, **params):
        assert path == "/dashboard"
        return {"data": {"balanceData": {"current_balance": balance,
                                         "monthly_charge": listed,
                                         "actual_monthly_charge": actual}}}
    return fake


async def test_runway_is_on_the_billed_charge_not_the_list_price(monkeypatch):
    monkeypatch.setattr(vpsie, "_get", _dashboard(791.73, 694.15, 249.10))
    monkeypatch.delenv("VPSIE_RUNWAY_MONTHS", raising=False)
    obs = await vpsie.check_balance()
    assert obs.health is Health.OK
    assert obs.value == 3.18 and obs.unit == "months"
    # One basis, stated once; the arithmetic uses it (791.73 / 249.10 = 3.18).
    assert ("billed 249.10/month (basis: the amount actually billed; list "
            "price 694.15 not used)") in obs.detail
    assert "3.2 months of runway" in obs.detail
    assert "instances suspend" not in obs.detail


async def test_a_short_runway_on_the_billed_charge_still_pages(monkeypatch):
    monkeypatch.setattr(vpsie, "_get", _dashboard(200.0, 694.15, 249.10))
    monkeypatch.delenv("VPSIE_RUNWAY_MONTHS", raising=False)
    obs = await vpsie.check_balance()
    assert obs.health is Health.FAILING            # 0.8 months
    assert "instances suspend" in obs.detail


async def test_without_an_actual_charge_the_list_price_is_used(monkeypatch):
    async def fake(path, **params):
        return {"data": {"balanceData": {"current_balance": 100.0,
                                         "monthly_charge": 50.0}}}
    monkeypatch.setattr(vpsie, "_get", fake)
    obs = await vpsie.check_balance()
    assert obs.value == 2.0 and obs.health is Health.OK


async def test_the_off_switch_silences_the_signal_as_well_as_the_check(monkeypatch):
    """Miro turned balance watching off on 2026-09-24. The check obeyed; the
    module's signal measured the balance itself and still posted "VPSie: not
    much balance left" to #vpsie the next day."""
    monkeypatch.setattr(vpsie, "_get", _dashboard(200.0, 694.15, 249.10))

    async def fine():
        from atlas.kernel.contracts import Observation
        return Observation("x", Health.OK, detail="fine")

    monkeypatch.setattr(vpsie, "check_protection", fine)
    monkeypatch.setattr(vpsie, "check_resource_ledger", fine)

    monkeypatch.setenv("VPSIE_WATCH_BALANCE", "1")
    titles = [s.title for s in await vpsie.MODULE.signals()]
    assert "VPSie: not much balance left" in titles

    monkeypatch.setenv("VPSIE_WATCH_BALANCE", "0")
    assert [s.title for s in await vpsie.MODULE.signals()] == []
    assert "balance" not in [c.name for c in vpsie.MODULE.checks()]
