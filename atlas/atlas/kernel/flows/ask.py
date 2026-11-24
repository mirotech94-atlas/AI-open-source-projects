"""Slice 1: a message to ATLAS in any Slack channel, handled once, answered
once (reliability plan p. 14; docs/acceptance.md).

`api/slack_events.events` keeps everything it already does -- the signature,
the approver gate, `_seen_before` -- and, with ATLAS_DURABLE_CORE=on and
`ask` in ATLAS_DURABLE_FLOWS, adds a workflow record before it hands the
message to a handler:

    RECEIVED -> VALIDATING -> READY           intake(): recorded and claimed
    READY -> RUNNING                          handled(): the handler runs
    RUNNING -> VERIFYING -> SUCCEEDED         one reply went out
                         -> FAILED            no reply was delivered
                         -> NEEDS_HUMAN       the reply only queued for the brief
    RUNNING -> FAILED                         the handler raised

One workflow per Slack `event_id` (the UNIQUE (type, correlation) row), and
only the worker that claims its lease runs the handler. So two copies of an
event that both get past `_seen_before` -- two workers racing its audit
lookup -- are handled once (R01). A copy that finds the row still RECEIVED
with a lapsed lease may take it over; a Slack RETRY never gets that far,
because `_seen_before` records the event id before intake and drops it, so
an event whose first worker died after that point is not resumed by this
flow.

The reply goes through the handlers' own path (`_reply` / `notify`), in the
channel and thread it was asked in. `note_reply` records what `notify`
answered, and the workflow ends SUCCEEDED only when a channel took the
reply: "sent" is not claimed from a call that returned nothing (S10).

A question's answer is produced live, bounded by ANSWER_S. If it raises,
times out or comes back empty, the reply says it does not know and why --
never a guess (`answer`, `UNKNOWN`).

The record holds hashes and references, never the text (`durable.receive`).
If the database cannot take the record, the message goes down the legacy
path exactly as with the slice off: this flow only replies, and
`_seen_before` already fails open for the same reason ("refusing every
message because the database blinked is worse"). Effects keep their own
guarantees: an approval is the proposals claim, a request `intent.act`.
"""
from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable, Coroutine
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any

from ..durable import DurableUnavailable, State
from ..leases import Fence

log = logging.getLogger("atlas.flows.ask")

S = State
WORKFLOW = "slack.event"
# Long enough for the slowest handler (a routed conversation, a solve); a
# handler that outruns it commits nothing (the fence), and the row stays
# where it was for a person to see.
LEASE = timedelta(minutes=15)
# Above what `_converse` can already spend (a 40 s fast call, then the
# routed fallback), so the bound cuts off only a genuinely stuck answer.
ANSWER_S = 120.0

OFF, TAKEN, DUPLICATE, UNAVAILABLE = "off", "taken", "duplicate", "unavailable"

UNKNOWN = ("I don't know right now: I couldn't get a fresh answer ({why}), and "
           "I won't guess. Ask again in a few minutes. Nothing was executed.")


@dataclass(slots=True)
class Taken:
    """This worker's claim on one Slack event's workflow."""

    workflow_id: int
    fence: Fence
    replies: list[list[str]] = field(default_factory=list)
    answered: list[str] = field(default_factory=list)


_CURRENT: ContextVar[Taken | None] = ContextVar("atlas_flows_ask", default=None)


def active() -> bool:
    return _CURRENT.get() is not None


def note_reply(used: list[str] | None) -> None:
    """What `notify` answered for a reply sent while a workflow is handled.
    A no-op outside one (the slice off, or another caller)."""
    taken = _CURRENT.get()
    if taken is not None:
        taken.replies.append(list(used or []))


async def intake(event_id: str, *, text: str, user: str, channel: str,
                 thread: str) -> tuple[str, Taken | None]:
    """(OFF | TAKEN | DUPLICATE | UNAVAILABLE, the claim when TAKEN).

    OFF touches nothing: the slice is off, or the event has no id to key on.
    """
    from .. import durable
    from . import enabled, walk
    if not event_id or not enabled("ask"):
        return OFF, None
    try:
        got = await durable.receive(
            WORKFLOW, event_id,
            payload={"text": text, "user": user, "channel": channel,
                     "thread": thread},
            input_refs=[f"slack:event:{event_id}"], actor=f"slack:{user}",
            channel=f"slack:{channel}", thread=thread,
            desired_outcome="handled once, one reply in the thread it came from",
            classification="private")
    except DurableUnavailable as exc:
        log.warning("slack event %s: no workflow record (%s); legacy path",
                    event_id, exc)
        return UNAVAILABLE, None
    if not got.created and got.state is not S.RECEIVED:
        return DUPLICATE, None
    fence = await durable.claim(got.workflow_id, LEASE)
    if fence is None:
        # Held by the worker handling it now (or unreadable: fail closed,
        # since a live claim elsewhere is the likelier reason).
        return DUPLICATE, None
    try:
        moved = await walk(got.workflow_id, fence, (S.RECEIVED, S.VALIDATING),
                           (S.VALIDATING, S.READY), step="intake")
    except DurableUnavailable:
        await durable.release(fence)
        return UNAVAILABLE, None
    if not moved:
        await durable.release(fence)
        return DUPLICATE, None
    return TAKEN, Taken(got.workflow_id, fence)


async def handled(taken: Taken, work: Coroutine[Any, Any, Any],
                  route: str) -> None:
    """Run the handler under the workflow, then record how it ended."""
    from .. import durable
    wid, fence = taken.workflow_id, taken.fence
    token = _CURRENT.set(taken)
    try:
        try:
            started = await durable.transition(wid, S.READY, S.RUNNING,
                                               fence=fence, step=f"route:{route}")
        except DurableUnavailable:
            # The record blinked after intake. This flow only replies, so the
            # message is still handled -- unrecorded, as with the slice off --
            # rather than dropped after Slack already has its 200.
            log.warning("workflow %s: RUNNING not recorded; handling it "
                        "unrecorded", wid)
            _CURRENT.reset(token)
            token = _CURRENT.set(None)
            await work
            return
        if not started:
            work.close()
            log.warning("workflow %s not started (stale claim); handler skipped",
                        wid)
            return
        try:
            await work
        except Exception as exc:
            log.exception("slack handler %s raised under workflow %s", route, wid)
            await _move(wid, fence, S.RUNNING, S.FAILED,
                        summary=f"{route}: the handler raised {type(exc).__name__}")
            return
        await _conclude(taken, route)
    finally:
        _CURRENT.reset(token)
        await durable.release(fence)


async def _conclude(taken: Taken, route: str) -> None:
    wid, fence = taken.workflow_id, taken.fence
    sent = [used for used in taken.replies if used and used != ["queued"]]
    queued = [used for used in taken.replies if used == ["queued"]]
    refs = [f"reply:{'+'.join(used)}" for used in taken.replies if used]
    refs += [f"answer:{how}" for how in taken.answered]
    if not await _move(wid, fence, S.RUNNING, S.VERIFYING, evidence_refs=refs):
        return
    count = len(taken.replies)
    if sent:
        unknowns = [f"{count} replies were sent, not one"] if count > 1 else []
        await _move(wid, fence, S.VERIFYING, S.SUCCEEDED, unknowns=unknowns,
                    summary=f"{route}: the reply was delivered ({'+'.join(sent[0])})")
    elif queued:
        await _move(wid, fence, S.VERIFYING, S.NEEDS_HUMAN,
                    unknowns=["the reply waits for the morning brief"],
                    summary=f"{route}: the reply was queued, not delivered")
    else:
        await _move(wid, fence, S.VERIFYING, S.FAILED,
                    unknowns=["no channel took the reply"],
                    summary=(f"{route}: no reply was delivered" if count
                             else f"{route}: the handler sent no reply"))


async def _move(wid: int, fence: Fence, src: State, dst: State,
                **kw: Any) -> bool:
    from ..durable import transition
    try:
        return await transition(wid, src, dst, fence=fence, **kw)
    except DurableUnavailable:
        log.warning("workflow %s: %s -> %s not recorded", wid, src, dst)
        return False


async def answer(produce: Callable[[], Awaitable[str]], source: str) -> str:
    """A question's answer. Outside a workflow this is `produce()`, exactly
    as before. Inside one it is bounded by ANSWER_S, and a failure, a
    timeout or an empty answer becomes an honest "I don't know", with the
    reason -- never a guess and never a traceback."""
    taken = _CURRENT.get()
    if taken is None:
        return await produce()
    why = ""
    try:
        async with asyncio.timeout(ANSWER_S):
            text = (await produce() or "").strip()
    except TimeoutError:
        text, why = "", f"no answer within {ANSWER_S:.0f} s"
    except Exception as exc:  # noqa: BLE001
        from ...modules._http import explain
        text, why = "", f"{source} failed: {explain(exc)}"
    if not text:
        taken.answered.append(f"{source}:unknown")
        return UNKNOWN.format(why=why or f"{source} returned nothing")
    taken.answered.append(f"{source}:fresh")
    return text
