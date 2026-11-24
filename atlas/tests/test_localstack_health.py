"""localstack `model` and `speed` checks (and the 1-minute tunnel cadence).

Read-only: they read what the server lists and what real answers measured, and
never touch `llm.ROUTES`. Nothing readable is UNKNOWN, never OK (invariant 7).
Keys are made-up markers."""
from __future__ import annotations

import time
from datetime import timedelta

import httpx
import pytest

from atlas.kernel import llm
from atlas.kernel.contracts import Health
from atlas.modules import localstack

BASE = "http://host.docker.internal:18000/v1"
BASE2 = "http://10.2.6.9:8000/v1"


@pytest.fixture(autouse=True)
def env(monkeypatch):
    for key in ("LOCAL_LLM_BASE_URL_2", "LOCAL_LLM_MODEL", "ATLAS_SPEED_WINDOW_MIN",
                "ATLAS_SPEED_FLOOR_TPS"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("LOCAL_LLM_BASE_URL", BASE)
    monkeypatch.setenv("LOCAL_LLM_API_KEY", "fake-local-key-not-a-secret")
    llm.LOCAL_SPEED.clear()
    yield
    llm.LOCAL_SPEED.clear()


def _serve(monkeypatch, handler):
    monkeypatch.setattr(llm, "_local_client", lambda timeout: httpx.AsyncClient(
        transport=httpx.MockTransport(handler), trust_env=False))


def _models(*names) -> httpx.Response:
    return httpx.Response(200, json={"data": [{"id": n} for n in names]})


def test_cadences():
    checks = {c.name: c for c in localstack.MODULE.checks()}
    assert checks["tunnel"].interval == timedelta(minutes=1)
    assert {"tunnel", "model", "speed", "answers"} <= set(checks)


async def test_model_ok_when_served(monkeypatch):
    _serve(monkeypatch, lambda r: _models("qwen3.8:27b"))
    obs = await localstack.check_model()
    assert obs.health is Health.OK


async def test_model_unknown_when_the_list_cannot_be_read(monkeypatch):
    def boom(r):
        raise httpx.ConnectError("refused")
    _serve(monkeypatch, boom)
    assert (await localstack.check_model()).health is Health.UNKNOWN
    _serve(monkeypatch, lambda r: httpx.Response(401))
    assert (await localstack.check_model()).health is Health.UNKNOWN


async def test_model_degraded_names_both(monkeypatch):
    _serve(monkeypatch, lambda r: _models("llama3"))
    obs = await localstack.check_model()
    assert obs.health is Health.DEGRADED
    assert "qwen3.8:27b" in obs.detail and "llama3" in obs.detail


async def test_model_with_two_endpoints_one_down_is_degraded_not_ok(monkeypatch):
    monkeypatch.setenv("LOCAL_LLM_BASE_URL_2", BASE2)

    def handler(r):
        if r.url.host == "10.2.6.9":
            raise httpx.ConnectError("refused")
        return _models("qwen3.8:27b")

    _serve(monkeypatch, handler)
    obs = await localstack.check_model()
    assert obs.health is Health.DEGRADED and "10.2.6.9" in obs.detail


async def test_speed_unknown_with_no_sample_in_the_window():
    obs = await localstack.check_speed()
    assert obs.health is Health.UNKNOWN and obs.value is None
    llm.LOCAL_SPEED.append((time.time() - 3600, 100, 5.0, None))   # old
    assert (await localstack.check_speed()).health is Health.UNKNOWN


async def test_speed_ok_reports_median_ttft_and_read_time():
    llm.LOCAL_SPEED.append((time.time(), 100, 5.0, 1.0))      # 100/4 = 25 tok/s
    obs = await localstack.check_speed()
    assert obs.health is Health.OK
    assert obs.value == pytest.approx(25.0)
    assert "first token 1.0s" in obs.detail
    assert abs((obs.at.timestamp()) - time.time()) < 5


async def test_speed_degraded_below_the_floor(monkeypatch):
    monkeypatch.setenv("ATLAS_SPEED_FLOOR_TPS", "10")
    llm.LOCAL_SPEED.append((time.time(), 20, 10.0, None))     # 2 tok/s
    obs = await localstack.check_speed()
    assert obs.health is Health.DEGRADED and "floor" in obs.detail


async def test_checks_never_touch_the_routes(monkeypatch):
    llm.ROUTES.clear()
    before = dict(llm.ROUTES)
    _serve(monkeypatch, lambda r: _models("other"))
    await localstack.check_model()
    await localstack.check_speed()
    assert llm.ROUTES == before
