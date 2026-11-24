"""Per-action idempotency for external writes (reliability plan, RA-B7).

A retried run must not repeat its side effect: a second charge, a second DM,
a second Matrix message. Two pieces make that possible without touching
`executor.execute` (which passes an action only its params):

  * **The run.** `running(key)` names the logical run an action is executing
    under -- `proposal:<id>` for an approved card (`approvals.decide`) and
    `outbox:<id>` for an outbox row (`outbox.dispatch_once`) -- in a
    ContextVar, so `action.run` can read it with `run_key()`. A path that
    names no run (a Tier 1 step, a principal's direct request) falls back to
    a digest of the action and its canonical params. Never the time: a key
    made from the clock is a new key on every retry, which is no key at all.

  * **The claim.** For a provider with no idempotency of its own (Instagram
    and WhatsApp DMs), `claim()` records the effect in `effect_claims`
    BEFORE the send, atomically. A second attempt with the same key finds
    the row and does not send. A send that definitely did not leave is
    `release`d so a retry may send; one whose outcome is unknown keeps its
    claim -- reconcile, never resend. There is deliberately no stale
    re-claim (unlike `occurrence.claim`): a claim left 'sending' by a dead
    process is exactly the uncertain effect this exists to stop repeating.

A provider that does honour a key (Stripe's `Idempotency-Key`) just gets
`key_for(...)` in its request header.
"""
from __future__ import annotations

import hashlib
import json
import logging
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any

log = logging.getLogger("atlas.idempotency")

_RUN: ContextVar[str | None] = ContextVar("atlas_run", default=None)

CLAIMED, SENT, IN_DOUBT, UNREADABLE = "claimed", "sent", "in doubt", "unreadable"


@contextmanager
def running(key: str) -> Iterator[None]:
    """Name the logical run for everything awaited inside the block."""
    token = _RUN.set(key)
    try:
        yield
    finally:
        _RUN.reset(token)


def current_run() -> str | None:
    return _RUN.get()


def _digest(*parts: str) -> str:
    raw = "\x1f".join(parts).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def run_key(action: str, params: dict[str, Any]) -> str:
    """The run this effect belongs to: the named run when there is one,
    otherwise the action and its exact params -- the same intent retried is
    the same key, a different intent is a different one."""
    named = current_run()
    if named:
        return named
    canonical = json.dumps(params, sort_keys=True, default=str)
    return "params:" + _digest(action, canonical)[:32]


def key_for(action: str, *parts: str, length: int = 40) -> str:
    """A deterministic, opaque key from an action and its identifying parts
    (a provider's own id, the run key). Safe for a header or a URL path."""
    return _digest(action, *parts)[:length]


async def claim(action: str, key: str) -> str:
    """Record the effect before it is attempted.

    CLAIMED: this caller may send. SENT: an earlier attempt already did --
    do nothing. IN_DOUBT: an earlier attempt claimed it and never settled
    (in flight, or its process died mid-send) -- do not send. UNREADABLE: the
    claim could not be made -- do not send (a missed reply is retried; a
    duplicate cannot be taken back)."""
    from .state import fetch_one
    try:
        row = await fetch_one(
            """INSERT INTO effect_claims (action, key, status, claimed_at)
                    VALUES ($1, $2, 'sending', now())
               ON CONFLICT (action, key) DO NOTHING
               RETURNING key""", action, key)
        if row is not None:
            return CLAIMED
        prior = await fetch_one(
            "SELECT status FROM effect_claims WHERE action = $1 AND key = $2",
            action, key)
    except Exception as exc:  # noqa: BLE001
        log.warning("%s: the effect could not be claimed (%s); not sending",
                    action, type(exc).__name__)
        return UNREADABLE
    if prior is not None and prior["status"] == "sent":
        return SENT
    return IN_DOUBT


async def settle(action: str, key: str, detail: str = "") -> None:
    """The effect happened. Best effort: an unsettled claim still blocks a
    resend, it only reads as in doubt instead of sent."""
    from .state import execute_sql
    try:
        await execute_sql(
            """UPDATE effect_claims SET status = 'sent', settled_at = now(),
                      detail = $3
                WHERE action = $1 AND key = $2""", action, key, detail[:300])
    except Exception as exc:  # noqa: BLE001
        log.warning("%s: sent, but the claim was not settled (%s)", action,
                    type(exc).__name__)


async def release(action: str, key: str) -> None:
    """The effect certainly did not happen (the provider refused it): drop
    the claim so a retry may send."""
    from .state import execute_sql
    try:
        await execute_sql(
            "DELETE FROM effect_claims WHERE action = $1 AND key = $2 "
            "AND status = 'sending'", action, key)
    except Exception as exc:  # noqa: BLE001
        log.warning("%s: a refused send kept its claim (%s); a retry will "
                    "read it as in doubt", action, type(exc).__name__)


def uncertain(reason: str) -> bool:
    """Could a send that came back with `reason` (a `_http` failure, already
    through `explain`) still have reached the provider? A timeout, a 5xx and
    a 2xx whose body was not JSON might have; a 4xx, a refused connection or
    a missing setting did not."""
    reason = (reason or "").strip()
    if reason.startswith(("timed out", "HTTP 5", "expected JSON")):
        return True
    return reason.startswith("unreachable:") and "ConnectError" not in reason


async def finish(action: str, key: str, reason: str | None,
                 detail: str = "") -> None:
    """Settle, release or keep a claim after one send attempt: sent ->
    settled; certainly not sent -> released, so a retry may send; unknown ->
    kept, so a retry does not."""
    if reason is None:
        await settle(action, key, detail)
    elif not uncertain(reason):
        await release(action, key)
    else:
        log.warning("%s: the send's outcome is unknown (%s); its claim stays, "
                    "so a retry will not resend", action, reason)


def refusal(action: str, state: str) -> str:
    """The detail for a send that was not attempted because of `state`."""
    if state == SENT:
        return "already sent for this run; not sent again"
    if state == IN_DOUBT:
        return ("an earlier attempt for this run never confirmed; not sending "
                "again -- check by hand")
    return "the send could not be recorded first, so it was not attempted"
