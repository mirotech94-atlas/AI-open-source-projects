"""A circuit breaker per model deployment, so a dead provider is skipped
instead of waited on.

`think()` walks its chain in order and, before this, tried every provider on
every call: with Anthropic out of credit and two others rate-limited
(prod, 2026-10-05) each turn spent seconds failing through the same dead
endpoints, and a phone turn has eight. the owner's voice-partner brief asks for
breakers; this is the small version that fits the router already here.

  * Keyed by (provider, model): one model being limited says nothing about
    another on the same account.
  * A 429 that names `Retry-After` opens the breaker for exactly that long
    (capped). An auth or billing answer (401, 402, 403) opens it for the
    long cooldown at once: retrying a spent credit balance is noise.
  * Otherwise three failures in a row open it for a minute, doubling each
    time it re-opens, to ten minutes at most.
  * When the cooldown ends, one call is let through (half-open). Success
    closes the breaker and resets the backoff; failure re-opens it.
  * A breaker never leaves `think()` with nothing to try: if every provider
    in a chain is open, the chain is tried anyway, in order. "No model
    answered" must mean the models did not answer, not that ATLAS declined
    to ask.

In memory and per process, like the router's own state. It changes which
provider is asked first; it never changes what a caller is allowed to do.
"""
from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass

FAILS_TO_OPEN = 3
BASE_COOLDOWN_S = 60.0
MAX_COOLDOWN_S = 600.0
HARD_STATUSES = frozenset({401, 402, 403})

Key = tuple[str, str]


@dataclass(slots=True)
class _State:
    fails: int = 0
    open_until: float = 0.0
    cooldown: float = BASE_COOLDOWN_S
    probing: bool = False
    why: str = ""


_STATES: dict[Key, _State] = {}
_clock: Callable[[], float] = time.monotonic


def reset(clock: Callable[[], float] | None = None) -> None:
    """Forget everything (tests; and a redeploy does this by itself)."""
    global _clock
    _STATES.clear()
    _clock = clock or time.monotonic


def key_of(provider: object) -> Key:
    return (str(getattr(provider, "name", "?")), str(getattr(provider, "model", "") or ""))


def allow(key: Key) -> bool:
    """May this deployment be asked now? True when closed, or when its
    cooldown has ended and nobody else is already probing it."""
    st = _STATES.get(key)
    if st is None or st.open_until == 0.0:
        return True
    if _clock() < st.open_until:
        return False
    if st.probing:
        return False
    st.probing = True
    return True


def success(key: Key) -> None:
    _STATES.pop(key, None)


def _retry_after(exc: BaseException) -> float | None:
    resp = getattr(exc, "response", None)
    headers = getattr(resp, "headers", None)
    if headers is None:
        return None
    try:
        value = headers.get("retry-after")
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _status(exc: BaseException) -> int | None:
    resp = getattr(exc, "response", None)
    code = getattr(resp, "status_code", None) or getattr(exc, "status_code", None)
    return int(code) if isinstance(code, int) else None


def failure(key: Key, exc: BaseException) -> float:
    """Record a failed call. Returns how long the breaker is now open for
    (0 when it stays closed)."""
    st = _STATES.setdefault(key, _State())
    was_probe, st.probing = st.probing, False
    st.fails += 1
    status = _status(exc)
    wait = 0.0
    if status == 429 and (named := _retry_after(exc)) is not None:
        wait = max(1.0, min(MAX_COOLDOWN_S, named))
        st.why = "rate limited"
    elif status in HARD_STATUSES:
        wait = MAX_COOLDOWN_S
        st.why = f"HTTP {status}"
    elif was_probe:
        st.cooldown = min(MAX_COOLDOWN_S, st.cooldown * 2)
        wait = st.cooldown
        st.why = st.why or "still failing"
    elif st.fails >= FAILS_TO_OPEN:
        wait = st.cooldown
        st.why = "failing repeatedly"
    if wait:
        st.open_until = _clock() + wait
    return wait


def is_open(key: Key) -> bool:
    st = _STATES.get(key)
    return bool(st and st.open_until and _clock() < st.open_until)


def state_of(key: Key, wall: Callable[[], float] = time.time) -> dict[str, object]:
    """One deployment's breaker as it stands, for a status screen: `state`
    is "closed", "open" or "half-open" (the cooldown is over and the next
    call is the probe), with `why`, `until` (wall-clock epoch seconds while
    open, else None) and `fails` in a row.

    Read-only. Unlike `allow()` it never claims the half-open probe, so
    looking at the screen cannot steal the one call that would close it."""
    st = _STATES.get(key)
    if st is None:
        return {"state": "closed", "why": "", "until": None, "fails": 0}
    if st.open_until == 0.0:
        return {"state": "closed", "why": st.why, "until": None, "fails": st.fails}
    left = st.open_until - _clock()
    if left > 0:
        return {"state": "open", "why": st.why, "until": wall() + left,
                "fails": st.fails}
    return {"state": "half-open", "why": st.why, "until": None, "fails": st.fails}


def snapshot() -> dict[str, dict[str, object]]:
    """What is open right now, for a health line or the brief."""
    now = _clock()
    return {f"{name}:{model}": {"seconds_left": round(st.open_until - now), "why": st.why}
            for (name, model), st in _STATES.items() if st.open_until and now < st.open_until}
