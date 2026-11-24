"""The /healthz heartbeat (RA-F4, docs/watchdog.md).

Two ages the off-box watchdog reads: how long since the scheduler loop last
finished a pass, and how old the newest observation is. A value that cannot
be established is null, never 0 -- a 0 would read as "perfectly fresh" and
hide exactly the stall the field exists to show (invariant 7). "ok" keeps
its old meaning whatever the ages say.
"""
from __future__ import annotations

import asyncio
import time
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from atlas.api import main
from atlas.kernel import scheduler, state


@pytest.fixture
def fresh(monkeypatch):
    monkeypatch.setattr(main, "_obs_read_at", None)
    monkeypatch.setattr(main, "_obs_at", None)
    monkeypatch.setattr(scheduler, "LAST_CYCLE_AT", None)
    return monkeypatch


def _stub_newest(monkeypatch, answer):
    """fetch_one answering the max(at) query; `answer` is a value or an
    exception. Returns the list of queries made."""
    calls: list[str] = []

    async def fetch_one(sql: str, *args):
        calls.append(sql)
        assert "max(at)" in sql and "observations" in sql
        if isinstance(answer, BaseException):
            raise answer
        return {"at": answer}

    monkeypatch.setattr(state, "fetch_one", fetch_one)
    return calls


async def test_before_the_first_cycle_the_cycle_age_is_null(fresh):
    _stub_newest(fresh, datetime.now(UTC) - timedelta(seconds=40))
    hb = await main._heartbeat()
    assert hb["cycle_age_s"] is None
    assert 39 <= hb["observation_age_s"] <= 41


async def test_ages_are_measured(fresh):
    fresh.setattr(scheduler, "LAST_CYCLE_AT", time.monotonic() - 12)
    _stub_newest(fresh, datetime.now(UTC) - timedelta(seconds=99))
    hb = await main._heartbeat()
    assert 11 <= hb["cycle_age_s"] <= 13
    assert 98 <= hb["observation_age_s"] <= 100


@pytest.mark.parametrize("error", [RuntimeError("pool closed"), TimeoutError()])
async def test_a_database_error_is_null_never_zero(fresh, error):
    fresh.setattr(scheduler, "LAST_CYCLE_AT", time.monotonic())
    _stub_newest(fresh, error)
    hb = await main._heartbeat()
    assert hb["observation_age_s"] is None
    assert hb["cycle_age_s"] == 0          # the positive control: a real 0


async def test_a_hung_database_is_null_within_the_timeout(fresh):
    async def hangs(sql: str, *args):
        await asyncio.sleep(3600)

    fresh.setattr(state, "fetch_one", hangs)
    fresh.setattr(main, "HEARTBEAT_DB_TIMEOUT_S", 0.05)
    started = time.monotonic()
    hb = await main._heartbeat()
    assert hb["observation_age_s"] is None
    assert time.monotonic() - started < 1


async def test_an_empty_table_is_null(fresh):
    _stub_newest(fresh, None)
    assert (await main._heartbeat())["observation_age_s"] is None


async def test_the_query_is_cached_so_public_hits_cannot_hammer_it(fresh):
    at = datetime.now(UTC) - timedelta(seconds=5)
    calls = _stub_newest(fresh, at)
    first = await main._heartbeat()
    await asyncio.gather(*(main._heartbeat() for _ in range(20)))
    assert len(calls) == 1
    # The cached value is the row's time, so the age still grows.
    fresh.setattr(main, "datetime", _Shifted)
    later = await main._heartbeat()
    assert len(calls) == 1
    assert later["observation_age_s"] >= first["observation_age_s"] + 9
    # Past the cache window it is read again.
    fresh.setattr(main, "_obs_read_at", time.monotonic() - main.HEARTBEAT_CACHE_S - 1)
    await main._heartbeat()
    assert len(calls) == 2


async def test_an_error_is_cached_too(fresh):
    calls = _stub_newest(fresh, RuntimeError("down"))
    for _ in range(5):
        await main._heartbeat()
    assert len(calls) == 1


class _Shifted(datetime):
    """datetime whose now() is ten seconds ahead."""
    @classmethod
    def now(cls, tz=None):                              # type: ignore[override]
        return datetime.now(tz) + timedelta(seconds=10)


async def test_the_loop_stamps_each_finished_pass(fresh, quiet_loop):
    started = time.monotonic()
    await quiet_loop.run(passes=3)
    finished = time.monotonic()
    # stamps[k] is what pass k left behind; stamps[0] is from before the loop.
    stamped = quiet_loop.stamps[1:4]
    assert quiet_loop.stamps[0] is None
    assert None not in stamped, f"a finished pass left no stamp: {stamped}"
    # Each pass writes its own, later stamp: interval=0.01 lies between two.
    assert started <= stamped[0] < stamped[1] < stamped[2] <= finished


def test_healthz_carries_the_heartbeat_and_ok_keeps_its_meaning(
        fresh, memory_backend):
    """Stale and null ages do not turn "ok" false: Docker's HEALTHCHECK and
    the deploy gate read "ok", and a stalled scheduler must not make a
    deploy roll back a healthy process."""
    fresh.setenv("MESH_ALLOWED_CIDRS", "127.0.0.0/8")

    async def no_boot() -> None:
        return None

    fresh.setattr("atlas.api.main.boot", no_boot)
    fresh.setattr("atlas.api.main.scheduler.loop", no_boot)
    fresh.setattr(scheduler, "LAST_CYCLE_AT", time.monotonic() - 5000)
    _stub_newest(fresh, RuntimeError("down"))
    with TestClient(main.app, client=("203.0.113.9", 51000)) as client:
        body = client.get("/healthz").json()
    assert body["ok"] is True
    # The menu-bar app grants the camera/microphone only after /healthz
    # names this exact service (bootstrap/mac/menubar/main.swift).
    assert body["service"] == "atlas-agency"
    assert body["heartbeat"]["observation_age_s"] is None
    assert body["heartbeat"]["cycle_age_s"] >= 5000
    assert set(body["heartbeat"]) == {"cycle_age_s", "observation_age_s"}
