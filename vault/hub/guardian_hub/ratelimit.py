"""A tiny in-memory sliding-window rate limiter.

One process-wide bucket per key, each a deque of monotonic timestamps.
Thread-safe (a single lock guards every bucket) and dependency-free -- this
guards a handful of auth-adjacent routes (POST /auth/start, POST
/sensor/enrol), not a general-purpose limiter, so a single lock is plenty:
call volume there is low and each call does O(window size) work.

Not persisted and not shared across processes: fine for a single-hub
deployment, resets on restart. `reset()` exists for tests only -- it clears
every bucket so unrelated tests never see another test's call history.

Bounded (Task 41 fix round 1, finding 12): the keys are partly
attacker-chosen -- `routers/inbound.py` limits on `inbound:<token[:8]>`,
which an unauthenticated prober can vary freely -- so the bucket dict is
capped. Once it is full, buckets with no hit inside the longest window any
caller uses are dropped first, and if that is not enough the
least-recently-hit keys go. Evicting a bucket can only ever *forgive* a
caller its recorded history, never block a legitimate one, and a flood big
enough to trigger eviction is exactly the case where the memory matters
more than the precision.
"""
import threading
import time
from collections import deque

_lock = threading.Lock()
_buckets: dict[str, deque] = {}

# ~4k live keys is far more than a real deployment's handful of routes x
# tenants, and bounds this dict at a few hundred KB.
_MAX_BUCKETS = 4096
# Longest window any caller passes (auth/enrol/hook limiters all use <= 1h);
# a bucket with no hit in this long cannot affect any future decision.
_STALE_AFTER_S = 3600


def _prune_locked(now: float) -> None:
    """Called only when the dict is at its cap, with `_lock` held."""
    for k in [k for k, dq in _buckets.items() if not dq or dq[-1] < now - _STALE_AFTER_S]:
        del _buckets[k]
    if len(_buckets) < _MAX_BUCKETS:
        return
    # Still full: drop the oldest-touched half in one pass rather than one
    # key per call, so an ongoing flood does not re-sort the dict every hit.
    victims = sorted(_buckets, key=lambda k: _buckets[k][-1])[: max(1, len(_buckets) // 2)]
    for k in victims:
        del _buckets[k]


def allow(key: str, limit: int, window_s: int) -> bool:
    """True and records a hit if `key` has made fewer than `limit` calls in
    the trailing `window_s` seconds; False (no hit recorded) otherwise."""
    now = time.monotonic()
    cutoff = now - window_s
    with _lock:
        dq = _buckets.get(key)
        if dq is None:
            if len(_buckets) >= _MAX_BUCKETS:
                _prune_locked(now)
            dq = _buckets[key] = deque()
        while dq and dq[0] < cutoff:
            dq.popleft()
        if len(dq) >= limit:
            return False
        dq.append(now)
        return True


def reset() -> None:
    """Test-only: clear all buckets so tests never leak rate-limit state
    into each other."""
    with _lock:
        _buckets.clear()


def bucket_count() -> int:
    """Live bucket count -- for the finding-12 test (and any future
    /metrics gauge) to assert the cap actually holds."""
    with _lock:
        return len(_buckets)
