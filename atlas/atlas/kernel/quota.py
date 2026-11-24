"""What each free model has left today, and who may spend it.

the owner, 2026-10-06: "ATLAS has nothing to respond with when talking with
him." Prod that afternoon: every free deployment answered 429 within three
hours -- Groq's 200,000-token day spent on gpt-oss-120b, gpt-oss-20b and
qwen3.8-27b, OpenRouter's 50 free requests for the key gone -- and nearly
all of it by background work (scheduled reasoning, missions, captions)
while he was not talking at all. Then he spoke, and there was nothing left.

So each free quota pool's remaining daily allowance is tracked from what
the providers themselves report, and the last part of it is kept for him:

  * A call is INTERACTIVE when the owner is waiting on it: a voice turn, a
    Slack message he typed, the assistant. Everything else is background.
    The flag is a context variable (`interactive()`), so a caller marks
    its whole turn once and every `think()` beneath it inherits it;
    `background.spawn` resets it, so a job started during his turn is
    background again.
  * When a pool's known remaining allowance falls under the reserve, only
    interactive calls may use it. Background calls skip it and fall
    through to the next deployment; when every deployment is reserved,
    `think()` raises `ReservedForOwner` without asking anyone, and the job
    waits for its next cycle.
  * A pool the provider says is spent (a daily-cap 429 with its own "try
    again in", or zero requests left with a reset time) is skipped by
    everyone until then.

What is read, and from where (Groq docs read 2026-10-06,
console.groq.com/docs/rate-limits; OpenRouter docs read 2026-10-06,
openrouter.ai/docs/api-reference/limits):

  Groq, every response   x-ratelimit-limit-requests / -remaining-requests
                         are REQUESTS PER DAY, x-ratelimit-reset-requests
                         when that resets ("2m59.56s"). The -tokens
                         headers are per MINUTE and are not a daily figure,
                         so they are not used here.
  Groq, a 429 body       "... tokens per day (TPD): Limit 200000, Used N,
                         Requested M. Please try again in 4m56.7s." -- the
                         only place the daily token count appears.
  Groq, docs             TPD 200K for the three free chat models (table
                         below), the limit used before any 429 is read.
  Any, -day headers      x-ratelimit-{limit,remaining}-{requests,tokens}-day
                         and x-ratelimit-reset-{requests,tokens}-day, when a
                         provider labels them that way itself.
  OpenRouter, a 429      X-RateLimit-Limit / -Remaining / -Reset (epoch ms).
  OpenRouter, GET /api/v1/key
                         free_model_daily_requests {used, limit, remaining}:
                         one pool for every :free model on the key.

Tokens used are counted from each answer's own `usage` and held for 24 h.
A count that was never read is unknown, and unknown is never treated as
spent or as plenty: the call is allowed. After a restart the local count
starts again at zero, so the first figure is the provider's next 429.

In memory, per process. Deliberately not Redis: the scheduler and the API
run in one process (kernel/events.py says why), and nothing else reads it.
It changes which deployment is asked; it never changes what a call may do.
"""
from __future__ import annotations

import contextlib
import contextvars
import functools
import logging
import os
import re
import time
from collections import deque
from collections.abc import Awaitable, Callable, Iterator, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

log = logging.getLogger("atlas.quota")

# Groq's published free-plan tokens per day, read 2026-10-06 from
# console.groq.com/docs/rate-limits. A model not listed has no known daily
# token figure until a 429 says one (allam-2-7b, or a chat model
# discover_groq_models() adds later).
DOCUMENTED_TPD: dict[tuple[str, str], int] = {
    ("groq", "openai/gpt-oss-120b"): 200_000,
    ("groq", "openai/gpt-oss-20b"): 200_000,
    ("groq", "qwen/qwen3.8-27b"): 200_000,
}

DAY_S = 86_400.0
OPENROUTER_REFRESH_S = 600.0

Pool = tuple[str, str]

OK, RESERVED, EXHAUSTED = "ok", "reserved", "exhausted"


class ReservedForOwner(RuntimeError):
    """Background work found only deployments whose remaining allowance is
    kept for the owner. Nothing was asked; the job waits for its next cycle.
    A RuntimeError, so callers that treat "no model" as a skip keep doing
    so."""


# ------------------------------------------------------- interactive or not

_INTERACTIVE: contextvars.ContextVar[bool] = contextvars.ContextVar(
    "atlas_interactive", default=False)


def is_interactive() -> bool:
    return _INTERACTIVE.get()


@contextlib.contextmanager
def interactive(value: bool = True) -> Iterator[None]:
    """Mark everything awaited inside as the owner waiting on it (or, with
    False, as background)."""
    token = _INTERACTIVE.set(value)
    try:
        yield
    finally:
        _INTERACTIVE.reset(token)


def for_owner[**P, R](fn: Callable[P, Awaitable[R]]) -> Callable[P, Awaitable[R]]:
    """Decorate a coroutine function that IS one of the owner's turns (a voice
    turn, a Slack message he typed, an assistant question): every model
    call beneath it is interactive. The signature is kept, so a FastAPI
    route may wear it."""
    @functools.wraps(fn)
    async def turn(*args: P.args, **kwargs: P.kwargs) -> R:
        with interactive():
            return await fn(*args, **kwargs)
    return turn


def background_context() -> contextvars.Context:
    """A copy of the current context with the flag off: what
    `background.spawn` starts a task in."""
    ctx = contextvars.copy_context()
    ctx.run(_INTERACTIVE.set, False)
    return ctx


# ------------------------------------------------------------------ settings

def _num(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, "") or default)
    except ValueError:
        return default


def reserve_tokens(limit: int) -> int:
    """Tokens kept for the owner in a pool of `limit` a day:
    ATLAS_FREE_RESERVE_TOKENS (40,000) or ATLAS_FREE_RESERVE_FRACTION
    (20%) of the day, whichever is larger -- but never more than half the
    pool, so a small one is not reserved whole."""
    fixed = _num("ATLAS_FREE_RESERVE_TOKENS", 40_000)
    share = _num("ATLAS_FREE_RESERVE_FRACTION", 0.2) * limit
    return int(min(limit * 0.5, max(fixed, share)))


def reserve_requests(limit: int) -> int:
    """Requests kept for the owner: ATLAS_FREE_RESERVE_FRACTION of the day's
    requests, at least one."""
    return max(1, round(_num("ATLAS_FREE_RESERVE_FRACTION", 0.2) * limit))


# --------------------------------------------------------------------- state

@dataclass(slots=True)
class _State:
    req_limit: int | None = None
    req_left: int | None = None
    req_reset_at: float | None = None
    req_read_at: float = 0.0
    tok_limit: int | None = None
    # A provider's own count of tokens used (a TPD 429), when one was read:
    # (when, used then). Our usage since is added to it.
    tok_anchor: tuple[float, int] | None = None
    used: deque[tuple[float, int]] = field(default_factory=deque)
    blocked_until: float = 0.0
    why: str = ""


_STATES: dict[Pool, _State] = {}
_clock: Callable[[], float] = time.time
_or_read_at = 0.0


def reset(clock: Callable[[], float] | None = None) -> None:
    """Forget everything (tests; a restart does this by itself)."""
    global _clock, _or_read_at
    _STATES.clear()
    _clock = clock or time.time
    _or_read_at = 0.0


def pool_of(provider: object) -> Pool:
    """Which allowance a deployment draws on. OpenRouter's free models
    share one per key; Cloudflare's neurons are one per account; anything
    else is metered per model (Groq: "each model has its own")."""
    name = str(getattr(provider, "name", "?"))
    model = str(getattr(provider, "model", "") or "")
    if name == "openrouter" and model.endswith(":free"):
        return ("openrouter", ":free")
    if name == "cloudflare":
        return ("cloudflare", "account")
    return (name, model)


def _state(pool: Pool) -> _State:
    st = _STATES.get(pool)
    if st is None:
        st = _STATES[pool] = _State(tok_limit=DOCUMENTED_TPD.get(pool))
    return st


# ------------------------------------------------------------------ parsing

_DURATION = re.compile(
    r"^\s*(?:(?P<h>\d+(?:\.\d+)?)h)?(?:(?P<m>\d+(?:\.\d+)?)m(?!s))?"
    r"(?:(?P<s>\d+(?:\.\d+)?)s)?(?:(?P<ms>\d+(?:\.\d+)?)ms)?\s*$")


def parse_duration(raw: str | None) -> float | None:
    """Seconds in "2m59.56s", "7.66s", "1h2m3s", "250ms" or "42"."""
    if raw is None:
        return None
    text = str(raw).strip()
    if not text:
        return None
    try:
        return max(0.0, float(text))
    except ValueError:
        pass
    found = _DURATION.match(text)
    if not found or not any(found.groupdict().values()):
        return None
    parts = {k: float(v) for k, v in found.groupdict().items() if v}
    return (parts.get("h", 0.0) * 3600 + parts.get("m", 0.0) * 60
            + parts.get("s", 0.0) + parts.get("ms", 0.0) / 1000)


def _int(value: Any) -> int | None:
    try:
        return int(float(str(value).strip()))
    except (TypeError, ValueError):
        return None


_TPD = re.compile(
    r"tokens per day \(TPD\):\s*Limit (\d+), Used (\d+), Requested (\d+)", re.IGNORECASE)
_RPD = re.compile(r"requests per day \(RPD\):\s*Limit (\d+), Used (\d+)", re.IGNORECASE)
_TRY_AGAIN = re.compile(r"try again in ((?:\d+(?:\.\d+)?(?:ms|h|m|s))+)",
                        re.IGNORECASE)


def _headers(response: Any) -> Any:
    return getattr(response, "headers", None) or {}


def _text(response: Any) -> str:
    try:
        return str(getattr(response, "text", "") or "")
    except Exception:                                   # noqa: BLE001
        return ""


def observe(provider: object, response: Any) -> None:
    """Read whatever the provider said about its allowance on this response
    (success or failure). Never raises: a header that cannot be read is a
    figure not known, not an error."""
    try:
        _observe(provider, response)
    except Exception as exc:                            # noqa: BLE001
        log.debug("quota headers unreadable: %s", type(exc).__name__)


def _observe(provider: object, response: Any) -> None:
    pool = pool_of(provider)
    now = _clock()
    headers = _headers(response)
    get = headers.get
    status = getattr(response, "status_code", None)
    st = _state(pool)

    # Headers a provider labels as daily itself.
    if (left := _int(get("x-ratelimit-remaining-requests-day"))) is not None:
        st.req_left, st.req_read_at = left, now
        st.req_limit = _int(get("x-ratelimit-limit-requests-day")) or st.req_limit
        if (after := parse_duration(get("x-ratelimit-reset-requests-day"))) is not None:
            st.req_reset_at = now + after
    if (left := _int(get("x-ratelimit-remaining-tokens-day"))) is not None:
        st.tok_limit = _int(get("x-ratelimit-limit-tokens-day")) or st.tok_limit
        if st.tok_limit is not None:
            st.tok_anchor, st.used = (now, st.tok_limit - left), deque()

    # Groq: -requests is per DAY, -tokens per MINUTE (docs, 2026-10-06).
    if pool[0] == "groq" and (
            left := _int(get("x-ratelimit-remaining-requests"))) is not None:
        st.req_left, st.req_read_at = left, now
        st.req_limit = _int(get("x-ratelimit-limit-requests")) or st.req_limit
        if (after := parse_duration(get("x-ratelimit-reset-requests"))) is not None:
            st.req_reset_at = now + after

    if status == 429:
        _observe_429(pool, st, response, now)


def _observe_429(pool: Pool, st: _State, response: Any, now: float) -> None:
    body = _text(response)
    get = _headers(response).get
    again = _TRY_AGAIN.search(body)
    after = parse_duration(again.group(1)) if again else None
    if after is None:
        after = parse_duration(get("retry-after"))
    if (tpd := _TPD.search(body)) is not None:
        limit, used = int(tpd.group(1)), int(tpd.group(2))
        st.tok_limit, st.tok_anchor, st.used = limit, (now, used), deque()
        st.why = "daily tokens spent"
        if after is not None:
            st.blocked_until = max(st.blocked_until, now + after)
        log.warning("%s:%s daily tokens spent (%s of %s used); the provider "
                    "says try again in %s s", pool[0], pool[1], used, limit,
                    None if after is None else round(after))
        return
    if (rpd := _RPD.search(body)) is not None:
        st.req_limit, st.req_left = int(rpd.group(1)), 0
        st.req_read_at, st.why = now, "daily requests spent"
        if after is not None:
            st.req_reset_at = now + after
            st.blocked_until = max(st.blocked_until, now + after)
        return
    # OpenRouter: the limit that was hit, with an epoch-ms reset.
    limit, left = _int(get("x-ratelimit-limit")), _int(get("x-ratelimit-remaining"))
    reset_ms = _int(get("x-ratelimit-reset"))
    if left is not None and reset_ms is not None:
        reset_at = reset_ms / 1000.0
        if reset_at - now > 120:
            # A window longer than a couple of minutes is the daily one;
            # the per-minute limit is the breaker's to sit out.
            st.req_limit, st.req_left, st.req_read_at = limit, left, now
            st.req_reset_at = reset_at
            if left <= 0:
                st.blocked_until = max(st.blocked_until, reset_at)
                st.why = "daily requests spent"
        return
    if after is not None and after > 120:
        # Any other 429 that names a wait longer than the per-minute windows:
        # the provider's own word for when to come back.
        st.blocked_until = max(st.blocked_until, now + after)
        st.why = "rate limited"


def spent(provider: object, tokens: int) -> None:
    """An answer arrived: count its tokens, and one request where the
    provider did not just report its own remaining count."""
    pool = pool_of(provider)
    st = _state(pool)
    now = _clock()
    if tokens > 0:
        st.used.append((now, int(tokens)))
    if st.req_left is not None and st.req_read_at < now - 1:
        st.req_left = max(0, st.req_left - 1)


def read_openrouter_key(payload: dict[str, Any]) -> bool:
    """`GET /api/v1/key`'s `free_model_daily_requests`, as read. True when
    it carried the figures."""
    global _or_read_at
    data = payload.get("data", payload) if isinstance(payload, dict) else {}
    daily = data.get("free_model_daily_requests") if isinstance(data, dict) else None
    if not isinstance(daily, dict):
        return False
    remaining, limit = _int(daily.get("remaining")), _int(daily.get("limit"))
    if remaining is None:
        return False
    st = _state(("openrouter", ":free"))
    now = _clock()
    st.req_left, st.req_limit, st.req_read_at = remaining, limit, now
    _or_read_at = now
    if remaining <= 0 and st.req_reset_at and st.req_reset_at > now:
        st.blocked_until = max(st.blocked_until, st.req_reset_at)
    return True


def openrouter_due() -> bool:
    return bool(os.getenv("OPENROUTER_API_KEY")) and (
        _clock() - _or_read_at >= OPENROUTER_REFRESH_S)


async def refresh_openrouter(force: bool = False) -> bool:
    """Ask OpenRouter how many free requests the key has left today. No
    model is called. At most once every ten minutes unless forced; never
    raises; the key goes only into the request header."""
    global _or_read_at
    key = os.getenv("OPENROUTER_API_KEY", "")
    if not key or (not force and _clock() - _or_read_at < OPENROUTER_REFRESH_S):
        return False
    _or_read_at = _clock()
    try:
        import httpx
        async with httpx.AsyncClient(timeout=10) as c:
            r = await c.get("https://openrouter.ai/api/v1/key",
                            headers={"Authorization": f"Bearer {key}"})
            r.raise_for_status()
            return read_openrouter_key(r.json())
    except Exception as exc:                            # noqa: BLE001
        log.info("openrouter allowance unread (%s)", type(exc).__name__)
        return False


# ----------------------------------------------------------------- verdicts

def _tokens_used(st: _State, now: float) -> int:
    while st.used and st.used[0][0] < now - DAY_S:
        st.used.popleft()
    local = sum(n for _t, n in st.used)
    if st.tok_anchor is not None:
        at, was = st.tok_anchor
        if at < now - DAY_S:
            st.tok_anchor = None                # a whole day on: stale
        else:
            since = sum(n for t, n in st.used if t >= at)
            return max(local, was + since)
    return local


def tokens_left(provider: object) -> int | None:
    """Known daily tokens left, or None when no daily figure is known."""
    st = _STATES.get(pool_of(provider)) or _State(
        tok_limit=DOCUMENTED_TPD.get(pool_of(provider)))
    if st.tok_limit is None:
        return None
    return max(0, st.tok_limit - _tokens_used(st, _clock()))


def requests_left(provider: object) -> int | None:
    st = _STATES.get(pool_of(provider))
    if st is None or st.req_left is None:
        return None
    now = _clock()
    if st.req_reset_at is not None and now >= st.req_reset_at:
        return None                             # reset since it was read
    if st.req_reset_at is None and now - st.req_read_at > OPENROUTER_REFRESH_S:
        # A count with no reset time (OpenRouter's /api/v1/key gives none)
        # is a reading, not a state: once it is old it is unknown, never a
        # pool shut until the next restart.
        return None
    return st.req_left


def verdict(provider: object) -> str:
    """OK, RESERVED (only the owner may use it) or EXHAUSTED (nobody, until the
    provider's own reset)."""
    pool = pool_of(provider)
    st = _STATES.get(pool)
    if st is None and pool not in DOCUMENTED_TPD:
        return OK
    st = _state(pool)
    now = _clock()
    if st.blocked_until > now:
        return EXHAUSTED
    left = requests_left(provider)
    if left is not None and left <= 0:
        return EXHAUSTED
    tokens = tokens_left(provider)
    if tokens is not None and st.tok_limit and tokens < reserve_tokens(st.tok_limit):
        return RESERVED
    if left is not None and st.req_limit and left < reserve_requests(st.req_limit):
        return RESERVED
    return OK


def allowed(provider: object, *, interactive_call: bool | None = None) -> bool:
    who = is_interactive() if interactive_call is None else interactive_call
    v = verdict(provider)
    return v == OK or (v == RESERVED and who)


def next_reset(providers: Sequence[object]) -> datetime | None:
    """The soonest time a provider reported for one of these deployments'
    SPENT allowance to come back, or None when none reported one. Never a
    guess: ATLAS's own breaker cooldowns are not provider resets, and a
    healthy model's next request-window reset (Groq sends one on every
    answer) is not the return of a spent one."""
    now = _clock()
    times: list[float] = []
    for p in providers:
        st = _STATES.get(pool_of(p))
        if st is None or verdict(p) != EXHAUSTED:
            continue
        if st.blocked_until > now:
            times.append(st.blocked_until)
        if st.req_reset_at and st.req_reset_at > now and (st.req_left or 0) <= 0:
            times.append(st.req_reset_at)
    return datetime.fromtimestamp(min(times)).astimezone() if times else None


def spent_out(providers: Sequence[object]) -> bool:
    """Whether every one of these deployments is known to be out of
    allowance (and there is at least one). One spent model among ten is
    an ordinary afternoon, not "the models are out"."""
    return bool(providers) and all(verdict(p) == EXHAUSTED for p in providers)


def snapshot() -> dict[str, dict[str, Any]]:
    """Per pool, what is known: for a health line or the self-model."""
    out: dict[str, dict[str, Any]] = {}
    now = _clock()
    for (name, model), st in _STATES.items():
        out[f"{name}:{model}"] = {
            "requests_left": st.req_left, "requests_limit": st.req_limit,
            "tokens_limit": st.tok_limit,
            "tokens_used_24h": _tokens_used(st, now),
            "blocked_s": max(0, round(st.blocked_until - now)),
            "why": st.why,
        }
    return out
