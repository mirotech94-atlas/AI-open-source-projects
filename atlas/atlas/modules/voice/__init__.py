"""Voice — whether Fish Audio can still speak for ATLAS.

What it watches: the Fish Audio **API credit** (separate from the platform
credit, and the one `/v1/tts` spends). When it runs out, Fish answers HTTP
402 "Insufficient API credit" and every line on a call falls back to
Twilio's `<Say>` (`api/phone.py`). That fallback is the right behaviour -- a
CRITICAL call never goes silent -- but it says nothing about itself, so:

  * `fish_credit`, every six hours: the balance, OK with the number and the
    time it was read, DEGRADED below `FISH_AUDIO_LOW_CREDIT` (default 5.00),
    UNKNOWN when the key is unset, the wallet cannot be read, or the answer
    is not the shape expected. Never a guessed number.
  * one INFO signal a day while `/v1/tts` keeps answering 402, recorded by
    `api.phone.clip` through `note_tts_status`: audio again clears it.

What it may do: nothing. It has no actions; topping up is Miro's.

Endpoints it assumes, so Miro can correct them:
  * `POST https://api.fish.audio/v1/tts` -- verified live on 2026-09-24
    (200 with MP3; 402 when the API credit is spent).
  * `GET https://api.fish.audio/wallet/self/api-credit` with the bearer --
    ASSUMED until a response is seen. The balance is read from the first of
    BALANCE_FIELDS present (at the top level or under `data`); anything else
    is UNKNOWN "unexpected shape" naming the fields that were there, never
    their values.

The key rides only in the Authorization header. Every failure reason comes
from `_http.explain`/`get_json`, which redact it.
"""
from __future__ import annotations

import math
import os
from datetime import UTC, date, datetime, timedelta
from typing import Any

from ...kernel.contracts import Check, Health, Observation, Severity, Signal
from .._http import get_json

WALLET = "https://api.fish.audio/wallet/self/api-credit"
BALANCE_FIELDS = ("credit", "api_credit", "balance", "amount")
DEFAULT_LOW_CREDIT = 5.00

CREDIT_EXHAUSTED = ("Fish Audio API credit is exhausted; calls are spoken by "
                    "Twilio's voice until topped up")

NO_KEY = ("no FISH_AUDIO_API_KEY set, so this measured nothing -- and every "
          "call is spoken by Twilio's voice.")

# In memory on purpose, like the rest of the call path: a restart forgets a
# 402, and the next call that hits one says so again.
_EXHAUSTED_AT: datetime | None = None
_SIGNALLED_ON: date | None = None


def _now() -> datetime:
    return datetime.now(UTC)


def reset() -> None:
    """For tests."""
    global _EXHAUSTED_AT, _SIGNALLED_ON
    _EXHAUSTED_AT = None
    _SIGNALLED_ON = None


def note_tts_status(status: int | None) -> None:
    """What `/v1/tts` just answered: 402 marks the credit exhausted; audio
    (None, meaning success) clears it. Anything else says nothing about
    the credit and changes nothing."""
    global _EXHAUSTED_AT
    if status == 402:
        _EXHAUSTED_AT = _EXHAUSTED_AT or _now()
    elif status is None:
        _EXHAUSTED_AT = None


def _low_mark() -> float:
    raw = os.getenv("FISH_AUDIO_LOW_CREDIT", "").strip()
    try:
        value = float(raw) if raw else DEFAULT_LOW_CREDIT
    except ValueError:
        return DEFAULT_LOW_CREDIT
    return value if math.isfinite(value) else DEFAULT_LOW_CREDIT


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        number = float(value)
    elif isinstance(value, str):
        try:
            number = float(value.strip())
        except ValueError:
            return None
    else:
        return None
    return number if math.isfinite(number) else None


def _balance(data: Any) -> tuple[float, str] | None:
    """(balance, the field it came from), or None when the shape is not the
    one assumed."""
    for layer in (data, data.get("data") if isinstance(data, dict) else None):
        if not isinstance(layer, dict):
            continue
        for field in BALANCE_FIELDS:
            if field in layer:
                number = _number(layer[field])
                return (number, field) if number is not None else None
    return None


def _fields(data: Any) -> str:
    if isinstance(data, dict):
        return ", ".join(sorted(str(k) for k in data)[:12]) or "none"
    return type(data).__name__


async def check_fish_credit() -> Observation:
    key = os.getenv("FISH_AUDIO_API_KEY", "").strip()
    if not key:
        return Observation("fish_credit", Health.UNKNOWN, detail=NO_KEY)
    data, reason = await get_json(
        WALLET, headers={"Authorization": f"Bearer {key}"}, timeout=15.0)
    if reason:
        return Observation("fish_credit", Health.UNKNOWN,
                           detail=f"could not read the Fish Audio API credit: "
                                  f"{reason}")
    found = _balance(data)
    if found is None:
        return Observation(
            "fish_credit", Health.UNKNOWN,
            detail=(f"unexpected shape from {WALLET} (fields: {_fields(data)}); "
                    f"expected one of {', '.join(BALANCE_FIELDS)}"))
    balance, field = found
    read_at = _now()
    low = _low_mark()
    health = Health.DEGRADED if balance < low else Health.OK
    detail = (f"{balance:.2f} Fish Audio API credit, read "
              f"{read_at.strftime('%H:%M UTC')}")
    if health is Health.DEGRADED:
        detail += (f" — below {low:.2f}; when it runs out calls are spoken by "
                   f"Twilio's voice")
    return Observation("fish_credit", health, balance, "credit", detail,
                       evidence={"read_at": read_at.isoformat(), "field": field,
                                 "low_mark": low},
                       at=read_at)


class VoiceModule:
    name = "voice"
    title = "Voice (Fish Audio)"
    # Never gated on the key: "Fish Audio is not configured" is itself the
    # finding, and it reads UNKNOWN rather than vanishing from the board.
    requires_env = ()
    optional_env = ("FISH_AUDIO_API_KEY", "FISH_AUDIO_LOW_CREDIT",
                    "FISH_AUDIO_VOICE_ID", "FISH_AUDIO_MODEL")

    async def setup(self) -> None: ...
    async def teardown(self) -> None: ...

    def checks(self):
        return (
            Check("fish_credit", check_fish_credit, timedelta(hours=6),
                  Severity.NORMAL, "Fish Audio API credit left for speaking",
                  baseline_sigma=None, timeout=timedelta(seconds=20)),
        )

    def actions(self):
        return ()             # topping up is his; watching never spends

    async def signals(self):
        """One INFO signal a day while the last word from `/v1/tts` was 402."""
        global _SIGNALLED_ON
        if _EXHAUSTED_AT is None:
            return ()
        today = _now().date()
        if _SIGNALLED_ON == today:
            return ()
        _SIGNALLED_ON = today
        return (Signal(
            title=CREDIT_EXHAUSTED,
            body=(f"Fish Audio answered HTTP 402 (insufficient API credit) at "
                  f"{_EXHAUSTED_AT.strftime('%H:%M UTC on %d %b')}. Nothing is "
                  f"lost -- every line is still said, in Twilio's voice -- "
                  f"until the API credit is topped up."),
            severity=Severity.INFO),)

    def brief(self) -> str:
        return ("Voice: Fish Audio API credit is spent — calls use Twilio's voice"
                if _EXHAUSTED_AT is not None else "")


MODULE = VoiceModule()
