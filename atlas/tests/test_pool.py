"""One connection pool, however many coroutines ask for it at once.

This is not a hypothetical. The bare `if _pool is None: _pool = await
create_pool()` raced on the live box: boot fires the scheduler's first checks
while the dashboard's SSE stream and the API's own queries all open together,
every one of them saw None, and each built its own pool. The last assignment
won the global and the rest were orphaned — unreferenced, never closed, still
holding ten connections each.

Postgres filled at a hundred. Every query after that returned
TooManyConnectionsError, so every dashboard endpoint returned 500, and the
browser drew a system that was watching nothing — which is indistinguishable
from an estate with no modules in it. That is the part worth a test: the
failure did not look like a database failure.
"""
from __future__ import annotations

import asyncio

import pytest

from atlas.kernel import state


@pytest.fixture(autouse=True)
def no_pool(monkeypatch):
    monkeypatch.setattr(state, "_pool", None)
    monkeypatch.setenv("DATABASE_URL", "postgresql://test/test")
    yield
    state._pool = None


class FakePool:
    def __init__(self, n: int) -> None:
        self.n = n
        self.closed = False

    async def close(self) -> None:
        self.closed = True


async def test_concurrent_callers_build_exactly_one_pool(monkeypatch):
    """The regression. Twenty simultaneous first calls must yield one pool,
    not twenty — each orphan holds its connections until the process dies."""
    built: list[FakePool] = []

    async def slow_create(*_a, **_k):
        # The yield is the whole bug: without it the race cannot happen, and
        # a create_pool that returns instantly would let the old code pass.
        await asyncio.sleep(0.01)
        pool = FakePool(len(built))
        built.append(pool)
        return pool

    import asyncpg
    monkeypatch.setattr(asyncpg, "create_pool", slow_create)

    backend = state.PostgresBackend()
    pools = await asyncio.gather(*(backend._pool() for _ in range(20)))

    assert len(built) == 1, f"built {len(built)} pools; each orphan leaks"
    assert all(p is built[0] for p in pools), "callers got different pools"


async def test_the_fast_path_does_not_take_the_lock(monkeypatch):
    """Once the pool exists every query goes through here, so it must not
    serialise on a lock that is only needed for creation."""
    sentinel = FakePool(0)
    monkeypatch.setattr(state, "_pool", sentinel)

    async with state._pool_lock():
        # Holding the lock must not block a caller that only needs to read
        # the already-built pool.
        got = await asyncio.wait_for(state.PostgresBackend()._pool(), timeout=1)
    assert got is sentinel


async def test_a_failed_creation_does_not_wedge_the_lock(monkeypatch):
    """If the database is down at boot, the next attempt must still be able
    to try — a lock left held would turn a transient outage into a permanent
    one."""
    attempts = []

    async def failing(*_a, **_k):
        attempts.append(1)
        raise OSError("postgres is not up yet")

    import asyncpg
    monkeypatch.setattr(asyncpg, "create_pool", failing)

    backend = state.PostgresBackend()
    for _ in range(3):
        with pytest.raises(OSError):
            await backend._pool()
    assert len(attempts) == 3, "the lock was not released after a failure"
    assert not state._pool_lock().locked()
