"""Inbound webhooks for the call-screening line (Phase 13.6).

Three routes, all on the public 443 surface Caddy forwards, all refused
before anything is read or written unless Twilio's signature verifies: the
first turn of a call, every turn after it, and the status callback that says
the line dropped. The signature check is `main._verify_twilio` over
`main._public_url` — imported rather than re-implemented, because a second
copy of that HMAC is a second place for it to go quietly wrong (and both are
imported inside the handlers: `main` imports this router at line 37, long
before it defines either of them).

A webhook may not call a module's `run` — the tier check, the dry run and
the audit entry all live in `execute`, and a route that skipped them would
be a second way to act (invariant 1). Nor may it call `execute` itself: no
file under `atlas/api` does, and `tests/test_api.py::
test_no_route_anywhere_can_reach_the_executor` keeps it that way. Every
other surface goes through a kernel function — `main.py`'s Slack buttons and
`phone.py`'s keypad both reach the executor through `approvals.decide` — and
this one goes through `kernel.unattended.run_tier1`, which refuses anything
that is not Tier 1 before `execute` sees it.

Nothing the executor returns is spoken. Its refusals name the action — the
watch-only one names the environment variable too — and a stranger on the
phone hears a fixed sentence either way.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Header, HTTPException, Request
from fastapi.responses import Response

from ..kernel.state import execute_sql
from ..kernel.unattended import run_tier1
from ..modules.screening.availability import next_open_slots
from ..modules.screening.flow import (
    COULD_NOT_HOLD,
    GREETING,
    SLOT_MINUTES,
    TURN_ACTION,
    append_turn,
    booked,
    chosen,
    forget,
    handle_turn,
    take_message,
    twiml_gather,
    twiml_say_hangup,
)

log = logging.getLogger("atlas.screening_webhooks")

router = APIRouter(prefix="/webhooks")


def _xml(twiml: str) -> Response:
    return Response(content=twiml, media_type="application/xml")


async def _verified(request: Request, signature: str) -> dict:
    """The form, or a 401 — before a row is written or a word is spoken."""
    from .main import _public_url, _verify_twilio  # see the module docstring

    form = dict(await request.form())
    url = _public_url(request)
    if url is None:
        log.error("ATLAS_HOST is unset — refusing an unverifiable screening call")
        raise HTTPException(503, "not configured")
    if not _verify_twilio(url, form, signature):
        raise HTTPException(401, "bad signature")
    return form


@router.post("/twilio/screen")
async def screen_inbound(request: Request,
                         x_twilio_signature: str = Header("")) -> Response:
    """Someone rang the screening number.

    The greeting says whose assistant this is and that the call may be
    recorded, in the first sentence, before the caller has said anything —
    CAPABILITIES §3 rules 1 and 2. It says nothing else: not a business
    name, not a status, not whether Miro is around.
    """
    form = await _verified(request, x_twilio_signature)
    call_sid = str(form.get("CallSid") or "")
    caller = str(form.get("From") or "")
    forget(call_sid)
    await execute_sql(
        """INSERT INTO screening_calls (call_sid, caller)
                VALUES ($1, $2) ON CONFLICT (call_sid) DO NOTHING""",
        call_sid, caller)
    return _xml(twiml_gather(GREETING, TURN_ACTION))


@router.post("/twilio/screen/turn")
async def screen_turn(request: Request,
                      x_twilio_signature: str = Header("")) -> Response:
    """One turn of speech.

    `?choose=1` marks the turn that answers an offer of times. The slots are
    re-derived rather than round-tripped through the query string: a query
    parameter is caller-controllable, and `book_slot` re-checks the window
    anyway, so re-deriving is both simpler and the only version where a
    forged parameter cannot name a time nobody offered.
    """
    form = await _verified(request, x_twilio_signature)
    call_sid = str(form.get("CallSid") or "")
    caller = str(form.get("From") or "")
    speech = str(form.get("SpeechResult") or "").strip()

    if request.query_params.get("choose") != "1":
        return _xml(await handle_turn(call_sid, caller, speech))

    await append_turn(call_sid, speech)
    slots = await next_open_slots(3, SLOT_MINUTES)
    pick = chosen(speech, slots)
    forget(call_sid)
    if pick is None:
        # Not an answer to the question asked, and a near miss is not a
        # licence to guess at a time on his calendar.
        await take_message(call_sid, caller,
                           "asked to book, then did not pick one of the "
                           "offered times")
        return _xml(twiml_say_hangup(COULD_NOT_HOLD))

    result = await run_tier1(
        "screening.book_slot",
        {"call_sid": call_sid, "start": pick.isoformat()},
        reason="a screened caller chose a slot inside the pre-approved "
               "availability")
    if not result.ok:
        # Whatever the executor said, it is not for a stranger: its refusals
        # name the action, and the watch-only one names the switch that
        # would give ATLAS hands. Miro hears about it instead.
        log.info("a screened booking did not run")
        await take_message(call_sid, caller,
                           "asked to book a call; the booking did not run")
        return _xml(twiml_say_hangup(COULD_NOT_HOLD))
    return _xml(twiml_say_hangup(booked(pick)))


@router.post("/twilio/screen/status")
async def screen_status(request: Request,
                        x_twilio_signature: str = Header("")) -> Response:
    """Twilio's status callback. A call that ended with no outcome recorded
    is a hang-up, and says so rather than staying open forever."""
    form = await _verified(request, x_twilio_signature)
    call_sid = str(form.get("CallSid") or "")
    forget(call_sid)
    await execute_sql(
        """UPDATE screening_calls SET outcome = 'hung_up'
            WHERE call_sid = $1 AND outcome IS NULL""", call_sid)
    return Response(content="<Response/>", media_type="application/xml")
