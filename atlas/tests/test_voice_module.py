"""The `voice` module: whether Fish Audio can still speak for ATLAS.

Fish Audio's API credit is separate from its platform credit and runs out on
its own; when it does, `/v1/tts` answers HTTP 402 and every call falls back
to Twilio's `<Say>`. That fallback is right -- a CRITICAL call never goes
silent -- but it is silent about itself. So a 402 raises one INFO signal a
day, and `voice.fish_credit` reads the balance every six hours: OK with the
number and its read time, DEGRADED below FISH_AUDIO_LOW_CREDIT, UNKNOWN when
it cannot be read. Never a guess, and never the key.
"""
from __future__ import annotations

import ast
import pathlib
from datetime import UTC, datetime, timedelta

import httpx
import pytest

from atlas.api import phone
from atlas.kernel.contracts import Health, Severity
from atlas.modules import voice

KEY = "fish-live-key-0123456789abcdef"


@pytest.fixture(autouse=True)
def clean(monkeypatch, tmp_path):
    monkeypatch.setenv("ATLAS_VOICE_DIR", str(tmp_path / "voice"))
    voice.reset()
    phone._CLIPS.clear()
    yield
    voice.reset()
    phone._CLIPS.clear()


def _status_error(code: int) -> httpx.HTTPStatusError:
    request = httpx.Request("POST", "https://api.fish.audio/v1/tts",
                            headers={"Authorization": f"Bearer {KEY}"})
    response = httpx.Response(code, request=request,
                              text="Insufficient API credit")
    return httpx.HTTPStatusError(f"{code} for {KEY}", request=request,
                                 response=response)


def _fish(monkeypatch, *outcomes):
    """Fish Audio answering each render with the next outcome in turn."""
    monkeypatch.setenv("FISH_AUDIO_API_KEY", KEY)
    queue = list(outcomes)

    async def render(_text):
        outcome = queue.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome

    monkeypatch.setattr("atlas.api.voice._fish_tts", render)


# -------------------------------------------------------- a 402 mid-call

async def test_a_402_falls_back_to_say_and_raises_one_info_signal(monkeypatch):
    _fish(monkeypatch, _status_error(402))
    assert await phone.clip("Hey Miro.") is None, "<Say> speaks it instead"
    (signal,) = await voice.MODULE.signals()
    assert signal.severity is Severity.INFO
    assert signal.title == voice.CREDIT_EXHAUSTED
    assert signal.title == ("Fish Audio API credit is exhausted; calls are "
                            "spoken by Twilio's voice until topped up")
    assert "UTC" in signal.body and KEY not in signal.body
    assert signal.suggests is None, "nothing to approve: it is a top-up"


async def test_one_signal_a_day_however_many_calls_hit_it(monkeypatch):
    _fish(monkeypatch, _status_error(402), _status_error(402), _status_error(402))
    for _ in range(3):
        await phone.clip("line")
    assert len(await voice.MODULE.signals()) == 1
    await phone.clip("line")
    assert await voice.MODULE.signals() == ()

    tomorrow = datetime.now(UTC) + timedelta(days=1)
    monkeypatch.setattr(voice, "_now", lambda: tomorrow)
    assert len(await voice.MODULE.signals()) == 1, "still exhausted the next day"


async def test_audio_again_means_topped_up_and_no_signal(monkeypatch):
    _fish(monkeypatch, _status_error(402), b"mp3")
    await phone.clip("line")
    assert await phone.clip("line") is not None
    assert await voice.MODULE.signals() == ()


@pytest.mark.parametrize("failure", [
    _status_error(500), _status_error(401), httpx.ConnectTimeout("slow"),
    RuntimeError("boom")])
async def test_any_other_failure_falls_back_without_a_credit_signal(
        monkeypatch, failure):
    _fish(monkeypatch, failure)
    assert await phone.clip("line") is None
    assert await voice.MODULE.signals() == ()


# -------------------------------------------------------- voice.fish_credit

def _wallet(monkeypatch, data, reason=None) -> list:
    seen: list = []

    async def fake_get_json(url, *, headers=None, timeout=25.0, **_kw):
        seen.append((url, headers, timeout))
        return data, reason

    monkeypatch.setattr(voice, "get_json", fake_get_json)
    return seen


def test_the_check_is_registered_every_six_hours():
    (check,) = voice.MODULE.checks()
    assert check.name == "fish_credit"
    assert check.interval == timedelta(hours=6)
    assert voice.MODULE.name == "voice"
    assert voice.MODULE.requires_env == ()
    assert voice.MODULE.actions() == ()


async def test_no_key_is_unknown_and_calls_nothing(monkeypatch):
    monkeypatch.delenv("FISH_AUDIO_API_KEY", raising=False)
    seen = _wallet(monkeypatch, {"credit": "10"})
    obs = await voice.check_fish_credit()
    assert obs.health is Health.UNKNOWN and obs.value is None
    assert "FISH_AUDIO_API_KEY" in obs.detail
    assert seen == []


async def test_the_balance_is_read_with_the_bearer(monkeypatch):
    monkeypatch.setenv("FISH_AUDIO_API_KEY", KEY)
    seen = _wallet(monkeypatch, {"_id": "x", "credit": "12.50",
                                 "has_free_credit": False})
    obs = await voice.check_fish_credit()
    (url, headers, _timeout), = seen
    assert url == "https://api.fish.audio/wallet/self/api-credit"
    assert headers == {"Authorization": f"Bearer {KEY}"}
    assert obs.health is Health.OK
    assert obs.value == 12.5
    # The number and when it was read, on screen (invariant 6).
    assert "12.50" in obs.detail and "UTC" in obs.detail
    assert obs.evidence["read_at"] and obs.evidence["field"] == "credit"
    assert KEY not in obs.detail and KEY not in str(obs.evidence)


@pytest.mark.parametrize("balance, low, health", [
    (4.99, None, Health.DEGRADED),
    (5.00, None, Health.OK),
    (12.5, "20", Health.DEGRADED),
    (12.5, "not-a-number", Health.OK),
    (0, None, Health.DEGRADED),
])
async def test_below_the_low_mark_is_degraded(monkeypatch, balance, low, health):
    monkeypatch.setenv("FISH_AUDIO_API_KEY", KEY)
    if low is None:
        monkeypatch.delenv("FISH_AUDIO_LOW_CREDIT", raising=False)
    else:
        monkeypatch.setenv("FISH_AUDIO_LOW_CREDIT", low)
    _wallet(monkeypatch, {"credit": balance})
    obs = await voice.check_fish_credit()
    assert obs.health is health
    assert obs.value == float(balance)


async def test_an_unreachable_wallet_is_unknown_never_a_guess(monkeypatch):
    monkeypatch.setenv("FISH_AUDIO_API_KEY", KEY)
    _wallet(monkeypatch, None, "HTTP 401 from /wallet/self/api-credit — "
                               "credentials rejected")
    obs = await voice.check_fish_credit()
    assert obs.health is Health.UNKNOWN and obs.value is None
    assert "HTTP 401" in obs.detail and KEY not in obs.detail


@pytest.mark.parametrize("data", [
    {"weird": KEY}, {"credit": "lots"}, {"credit": True}, {"credit": None},
    {"credit": float("nan")}, [], "12.5", {"data": {"nothing": 1}},
])
async def test_an_unexpected_shape_is_unknown_and_says_so(monkeypatch, data):
    monkeypatch.setenv("FISH_AUDIO_API_KEY", KEY)
    _wallet(monkeypatch, data)
    obs = await voice.check_fish_credit()
    assert obs.health is Health.UNKNOWN and obs.value is None
    assert "unexpected shape" in obs.detail
    assert KEY not in obs.detail


async def test_a_nested_balance_is_still_found(monkeypatch):
    monkeypatch.setenv("FISH_AUDIO_API_KEY", KEY)
    _wallet(monkeypatch, {"data": {"credit": "7.25"}})
    obs = await voice.check_fish_credit()
    assert obs.health is Health.OK and obs.value == 7.25


def test_the_module_never_reaches_the_executor():
    root = pathlib.Path(voice.__file__).parent
    for f in root.rglob("*.py"):
        tree = ast.parse(f.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                mod = ("." * node.level) + (node.module or "")
                assert "executor" not in mod, f"{f.name} imports {mod}"
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    assert "executor" not in alias.name, f.name
