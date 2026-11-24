"""NETBIRD_OPTIONAL_PEERS: a phone whose NetBird app sleeps is not an outage
(2026-09-27; presence comes from the home Wi-Fi now)."""
from __future__ import annotations

from atlas.kernel.contracts import Health
from atlas.modules import netbird


def _mesh(monkeypatch, peers):
    async def get(path):
        return peers, None
    monkeypatch.setattr(netbird, "_get", get)


PEERS = [{"name": "atlas", "connected": True},
         {"name": "iPhone-owner", "connected": False},
         {"name": "owner", "connected": True}]


async def test_an_optional_peer_offline_is_ok_and_named(monkeypatch):
    monkeypatch.setenv("NETBIRD_OPTIONAL_PEERS", "iphone-owner")
    _mesh(monkeypatch, PEERS)
    obs = await netbird.check_peers()
    assert obs.health is Health.OK
    assert "optional and off: iPhone-owner" in obs.detail


async def test_any_other_peer_offline_still_degrades(monkeypatch):
    monkeypatch.setenv("NETBIRD_OPTIONAL_PEERS", "iphone-owner")
    _mesh(monkeypatch, [*PEERS[:2], {"name": "owner", "connected": False}])
    obs = await netbird.check_peers()
    assert obs.health is Health.DEGRADED
    assert obs.detail.startswith("1 of 3 peers offline: owner")


async def test_without_the_setting_nothing_changes(monkeypatch):
    monkeypatch.delenv("NETBIRD_OPTIONAL_PEERS", raising=False)
    _mesh(monkeypatch, PEERS)
    assert (await netbird.check_peers()).health is Health.DEGRADED
