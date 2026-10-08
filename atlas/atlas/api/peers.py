"""Who is on the other end of a request, and a small per-address limiter.

Shared by the mesh guard (main.py), the login throttle (auth.py) and the
public download route (deliver.py). It lives here rather than in main.py
because those modules are imported BY main.py.
"""
from __future__ import annotations

import ipaddress
import time
from collections import deque

from fastapi import Request

# The Docker network our own reverse proxy sits on. A request whose immediate
# peer is in this range came from Caddy on this host, and nothing outside the
# host can occupy it.
PROXY_NETWORK = ipaddress.ip_network("172.16.0.0/12")


def from_proxy(request: Request) -> bool:
    """Whether the immediate peer is our own proxy on the Docker network."""
    peer = request.client.host if request.client else ""
    try:
        return ipaddress.ip_address(peer) in PROXY_NETWORK
    except ValueError:
        return False


def effective_client(request: Request) -> str:
    """The address to judge this request by.

    Caddy proxies mesh clients, so the immediate peer is Caddy's container and
    every forwarded request would otherwise look local. We take the forwarded
    address instead -- but ONLY when the immediate peer is our own proxy on the
    Docker network, which nothing outside this host can occupy. A header from
    any other peer is ignored, so a public client cannot claim a mesh address.
    """
    peer = request.client.host if request.client else ""
    if from_proxy(request):
        first = request.headers.get("x-forwarded-for", "").split(",")[0].strip()
        if first:
            return first
    return peer


class PerAddressLimiter:
    """At most `limit` events per address in a sliding `window` of seconds.

    In-process and in-memory: ATLAS runs one API process, and a restart
    forgetting the counts is acceptable for a throttle. Memory is bounded --
    an address with no event inside the window is dropped, and past
    `max_addresses` the stalest address is evicted -- so a spray of source
    addresses cannot grow it without limit.
    """

    def __init__(self, limit: int, window: float, max_addresses: int = 4096) -> None:
        self.limit = limit
        self.window = window
        self.max_addresses = max_addresses
        self._events: dict[str, deque[float]] = {}

    def _prune(self, key: str, now: float) -> deque[float] | None:
        events = self._events.get(key)
        if events is None:
            return None
        while events and now - events[0] > self.window:
            events.popleft()
        if not events:
            del self._events[key]
            return None
        return events

    def limited(self, key: str) -> bool:
        """Whether `key` has used up its window, without recording anything."""
        events = self._prune(key, time.monotonic())
        return events is not None and len(events) >= self.limit

    def hit(self, key: str) -> None:
        """Record one event for `key`."""
        now = time.monotonic()
        events = self._prune(key, now)
        if events is None:
            if len(self._events) >= self.max_addresses:
                self._evict(now)
            events = self._events[key] = deque()
        events.append(now)

    def allow(self, key: str) -> bool:
        """Record an event and say whether it is within the limit."""
        if self.limited(key):
            return False
        self.hit(key)
        return True

    def _evict(self, now: float) -> None:
        for key in list(self._events):
            self._prune(key, now)
        while len(self._events) >= self.max_addresses:
            stalest = min(self._events, key=lambda k: self._events[k][-1])
            del self._events[stalest]

    def clear(self) -> None:
        self._events.clear()

    def __len__(self) -> int:
        return len(self._events)
