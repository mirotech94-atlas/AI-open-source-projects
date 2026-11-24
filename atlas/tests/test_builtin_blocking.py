"""cert_check and dns_check never block the event loop.

Both are reached from the flycheap and guardian CHECKS, which run on the
scheduler's loop. A synchronous connect + TLS handshake (up to ten seconds)
or a resolver call inside `async def` froze every other check with it.
"""
from __future__ import annotations

import asyncio
import threading

import pytest

from atlas.modules import _netguard
from atlas.tools import builtin


@pytest.fixture(autouse=True)
def public_hosts(monkeypatch):
    """These hosts are fictional; cert_check's public-address guard (plan
    S03) is tested in tests/test_acceptance_security.py."""
    async def public(_url):
        return None

    monkeypatch.setattr(_netguard, "assert_public", public)


async def test_the_tls_handshake_runs_off_the_event_loop(monkeypatch):
    loop_thread = threading.get_ident()
    seen: list[int] = []

    def fake_peer_cert(hostname, port):
        seen.append(threading.get_ident())
        return {"notAfter": "Dec 31 23:59:59 2099 GMT",
                "issuer": ((("organizationName", "Test CA"),),)}

    monkeypatch.setattr(builtin, "_peer_cert", fake_peer_cert)
    got = await builtin.cert_check("flycheap.example")
    assert got["host"] == "flycheap.example" and got["urgent"] is False
    assert seen and seen[0] != loop_thread


async def test_a_slow_handshake_leaves_the_loop_free(monkeypatch):
    import time

    def slow(hostname, port):
        time.sleep(0.3)
        raise TimeoutError("timed out")

    monkeypatch.setattr(builtin, "_peer_cert", slow)
    ticks = 0

    async def ticker():
        nonlocal ticks
        for _ in range(5):
            await asyncio.sleep(0.02)
            ticks += 1

    got, _ = await asyncio.gather(builtin.cert_check("slow.example"), ticker())
    assert "error" in got
    assert ticks == 5, "the loop kept running while the handshake waited"


async def test_the_resolver_runs_off_the_event_loop(monkeypatch):
    loop_thread = threading.get_ident()
    seen: list[int] = []

    def fake_getaddrinfo(host, port):
        seen.append(threading.get_ident())
        return [(2, 1, 6, "", ("203.0.113.7", 0))]

    monkeypatch.setattr(builtin.socket, "getaddrinfo", fake_getaddrinfo)
    got = await builtin.dns_check("vault.example")
    assert got == {"host": "vault.example", "addresses": ["203.0.113.7"]}
    assert seen and seen[0] != loop_thread



async def test_module_brief_names_an_unreadable_inbox(monkeypatch):
    """Inboxes raise on a failed read now (so the brief can name them); the
    module_brief tool keeps the rest of its answer and says which part failed."""
    from atlas.kernel.registry import REGISTRY

    class Mod:
        def brief(self):
            return "messages: 2 open"

        async def inbox(self, limit=10):
            raise RuntimeError("messages_inbound unreadable")

    monkeypatch.setitem(REGISTRY.modules, "quiet_bridge", Mod())
    got = await builtin.module_brief("quiet_bridge")
    assert got["brief"] == "messages: 2 open"
    assert got["inbox"] is None and "unreadable" in got["inbox_unreadable"]
