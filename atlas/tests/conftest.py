"""Test fixtures. No database, no network, no LLM."""
from __future__ import annotations

import asyncio
import os
import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
FAKE_DATABASE_URL = "postgresql://test/test"
os.environ.setdefault("DATABASE_URL", FAKE_DATABASE_URL)

import asyncpg

from atlas.kernel import state

_real_create_pool = asyncpg.create_pool


@pytest.fixture(autouse=True)
def _the_fake_database_refuses_at_once(monkeypatch):
    """Code a test did not stub that reaches for Postgres finds it refused at
    once. Without this the pool resolves the host `test` over real DNS, and
    the resolver -- a CI runner's above all -- decides how long that takes:
    a test that budgets its loop in wall-clock time then fails at random.
    A real DSN (the *_pg tests set one) is passed through untouched."""
    def create_pool(dsn=None, *args, **kwargs):
        if dsn == FAKE_DATABASE_URL:
            raise ConnectionRefusedError("no database in the unit tests")
        return _real_create_pool(dsn, *args, **kwargs)

    monkeypatch.setattr(asyncpg, "create_pool", create_pool)


@pytest.fixture
def memory_backend():
    """Swap in the in-memory backend and restore the real one afterwards."""
    fake = state.MemoryBackend()
    previous = state.set_backend(fake)
    try:
        yield fake
    finally:
        state.set_backend(previous)


@pytest.fixture
def quiet_loop(monkeypatch):
    """`scheduler.loop` with every step a no-op, run for a counted number of
    passes (tests/scheduler_support.py)."""
    from atlas.kernel import scheduler
    from tests.scheduler_support import QuietLoop

    scheduler._tasks.clear()
    yield QuietLoop(monkeypatch)
    # A detached task outliving its test would still be pending in the next
    # one, holding objects bound to an event loop that has since closed.
    for task in scheduler._tasks.values():
        task.cancel()
    scheduler._tasks.clear()


@pytest.fixture
def local_model_down(monkeypatch):
    """Real routes with Ollama configured but refusing connections, and an
    Anthropic key set. Both transports are faked: Ollama at
    `httpx.AsyncClient.post` (every POST raises ConnectError), Anthropic at
    its SDK. Yields the list of Anthropic requests -- a PRIVATE caller must
    leave it empty."""
    import sys
    import types

    import httpx

    from atlas.kernel import llm

    cloud: list[dict] = []

    class Messages:
        async def create(self, **kwargs):
            cloud.append(kwargs)
            raise AssertionError("a PRIVATE call reached Anthropic")

    class AsyncAnthropic:
        def __init__(self, **_kw):
            self.messages = Messages()

    fake = types.ModuleType("anthropic")
    fake.AsyncAnthropic = AsyncAnthropic
    monkeypatch.setitem(sys.modules, "anthropic", fake)

    async def refused(self, url, *_a, **_kw):
        raise httpx.ConnectError("connection refused",
                                 request=httpx.Request("POST", url))

    monkeypatch.setattr(httpx.AsyncClient, "post", refused)
    monkeypatch.setenv("OLLAMA_URL", "http://ollama:11434")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.delenv("ATLAS_PRIVATE_ALLOW_CLOUD", raising=False)
    monkeypatch.setattr(llm, "ROUTES", {})
    monkeypatch.setattr(llm, "LOCAL_TOOLS", {})
    llm.build_routes()
    yield cloud


@pytest.fixture(autouse=True)
def _voice_cache_in_tmp(monkeypatch, tmp_path):
    """Fixed Fish lines a test renders are kept in its own directory, never
    in the checkout's data/voice/cache (atlas/api/voice_cache.py)."""
    monkeypatch.setenv("ATLAS_VOICE_CACHE_DIR", str(tmp_path / "voice-cache"))
    from atlas.api import voice
    monkeypatch.setattr(voice, "_WARMED", [True])     # no background renders


@pytest.fixture(autouse=True)
def _no_presenter_clips(monkeypatch, tmp_path):
    """Miro's committed presenter clips (academy/presenter.py) stay out of a
    test unless it asks for them: every test sees an empty presenter
    directory, the narrated fallback."""
    empty = tmp_path / "presenter-none"
    empty.mkdir()
    monkeypatch.setenv("ACADEMY_PRESENTER_DIR", str(empty))


@pytest.fixture(autouse=True)
def _closed_breakers():
    """Every test starts with every model breaker closed: the breaker's
    state is per process, and one test's failing fake provider must not
    make the next test's router skip a deployment of the same name."""
    from atlas.kernel import breaker, outage, quota
    breaker.reset()
    outage.reset()
    quota.reset()
    yield
    breaker.reset()
    outage.reset()
    quota.reset()


@pytest.fixture(autouse=True)
def _the_boot_reap_retries_do_not_sleep(monkeypatch):
    """`atlas.api.main.lifespan` retries its orphan reap 12 times, 10 s apart,
    when Postgres is unreachable -- right for a database still recovering,
    120 s of nothing for the unit tests' fake one (every `with TestClient(app)`
    paid it: five tests at 120 s each). Only that one sleep, found by the
    calling function, is skipped; every other sleep is the real one."""
    real_sleep = asyncio.sleep

    def sleep(delay, *args, **kwargs):
        caller = sys._getframe(1)
        if caller.f_code.co_name == "lifespan" and \
                caller.f_globals.get("__name__") == "atlas.api.main":
            return real_sleep(0)
        return real_sleep(delay, *args, **kwargs)

    monkeypatch.setattr(asyncio, "sleep", sleep)


# --- parallel runs (-n N, docs/test-loop.md) ---------------------------------

def _split_dsn(dsn: str, database: str) -> str:
    from urllib.parse import urlsplit, urlunsplit
    return urlunsplit(urlsplit(dsn)._replace(path=f"/{database}"))


async def _create_database(dsn: str) -> None:
    from urllib.parse import urlsplit
    name = urlsplit(dsn).path.lstrip("/")
    conn = await asyncpg.connect(_split_dsn(dsn, "postgres"))
    try:
        if not await conn.fetchval(
                "SELECT 1 FROM pg_database WHERE datname = $1", name):
            await conn.execute(f'CREATE DATABASE "{name}"')
    finally:
        await conn.close()


def pytest_configure(config):
    """Under xdist each worker gets its own throwaway database, `<db>_<worker>`,
    created on demand: the *_pg fixtures TRUNCATE, so two workers must never
    share one. ATLAS_TEST_PG_DSN is read at import by those files, which is
    after this hook. A serial run is untouched."""
    worker = os.environ.get("PYTEST_XDIST_WORKER")
    dsn = os.environ.get("ATLAS_TEST_PG_DSN", "")
    if worker and dsn:
        from urllib.parse import urlsplit
        base = urlsplit(dsn).path.lstrip("/") or "atlas"
        mine = _split_dsn(dsn, f"{base}_{worker}")
        import asyncio
        asyncio.run(_create_database(mine))
        os.environ["ATLAS_TEST_PG_DSN"] = mine
    if getattr(config.option, "numprocesses", None) and \
            getattr(config.option, "dist", "") == "load":
        config.option.dist = "loadgroup"     # lets `serial` pin a group to a worker


def pytest_collection_modifyitems(items):
    """Every `serial` test joins one xdist group, so it runs on a single worker,
    one after another. Without -n this does nothing."""
    for item in items:
        if item.get_closest_marker("serial") and \
                not item.get_closest_marker("xdist_group"):
            item.add_marker(pytest.mark.xdist_group("serial"))


_MEDIA_GONE = ("the 3D kit is missing", "templates/media/", "lesson video",
               "presenter clips", ".mp4'")


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    """The lesson and template videos are not part of the public export. A
    test that fails only because one of those .mp4 files is absent is
    reported as skipped, never as a pass."""
    outcome = yield
    rep = outcome.get_result()
    if rep.failed and call.excinfo is not None:
        text = str(call.excinfo.value)
        if any(m in text for m in _MEDIA_GONE) and ".mp4" in text:
            rep.outcome = "skipped"
            rep.wasxfail = None
            rep.longrepr = (str(item.fspath), item.location[1] or 0,
                            "Skipped: videos are not shipped in the public export")
