"""Read-back for the calendar writes (RA-D1): the event by the id the run
recorded, from the primary calendar, compared with what was approved --
start and end, attendee scope, and the timezone the times resolved in.

  * `calendar.create_block`: the owner's own block, no other attendee;
  * `calendar.book_with`: exactly the approved attendee (plus the owner);
  * `calendar.decline_invite`: the owner's own attendee row reads declined;
  * `calendar.offer_slots`: the sent Gmail message, by id, addressed to the
    approved recipient (metadata only).

A naive ISO `start` (no offset) is resolved in the calendar's own timezone
by `zone.resolve_start` -- the same resolver the write used, so the two
agree on a daylight-saving day -- and the evidence says so (RA-E2). When
that zone cannot be read the result is UNKNOWN, never a guess at UTC.
Instants are compared in UTC (inter-zone == is always False inside the
repeated fall-back hour). A run that found the event already on the
calendar records its id in `evidence["event_id"]` and no rollback token;
the read-back checks that event. Reads only. See docs/verification.md.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import httpx

from ...kernel.contracts import ActionResult
from ...kernel.verify import (
    FailureClass,
    Verification,
    classify,
    contradicted,
    reconciler,
    unknown,
    verified,
    verifier,
)

GMAIL_API = "https://gmail.googleapis.com/gmail/v1/users/me"


async def _read(path: str, **params) -> Any:
    from . import _api
    return await _api(path, **params)


async def _calendar_zone() -> str | None:
    try:
        cal = await _read("/calendars/primary")
    except Exception:                                   # noqa: BLE001
        return None
    if not isinstance(cal, dict):
        return None
    return str(cal.get("timeZone") or "") or None


def _when(raw: dict[str, Any] | None) -> datetime | None:
    value = (raw or {}).get("dateTime")
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value))
    except ValueError:
        return None


def _approved_start(start: str, zone: str | None) -> tuple[datetime | None, bool]:
    """(the approved start as an aware instant, whether it was naive). None
    when it cannot be resolved: unparseable, in the DST gap, or naive with
    no readable zone."""
    from .zone import resolve_start
    try:
        naive = datetime.fromisoformat(str(start)).tzinfo is None
    except ValueError:
        return None, False
    if not naive:
        return datetime.fromisoformat(str(start)), False
    try:
        begin, _, _ = resolve_start(start, zone)
    except ValueError:
        return None, True
    return begin, True


def _others(event: dict[str, Any]) -> list[str]:
    return sorted(str(a.get("email") or "").lower()
                  for a in event.get("attendees") or []
                  if not a.get("self") and not a.get("organizer"))


async def _event(event_id: str) -> tuple[dict[str, Any] | None, Verification | None]:
    try:
        event = await _read(f"/calendars/primary/events/{event_id}")
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code in (404, 410):
            return None, contradicted(f"event {event_id} is not on the calendar",
                                      event_id=event_id, calendar="primary")
        return None, unknown(f"could not read event {event_id}", classify(exc))
    if not isinstance(event, dict):
        return None, unknown("Calendar answered with an unexpected shape")
    if event.get("status") == "cancelled":
        return None, contradicted(f"event {event_id} is cancelled",
                                  event_id=event_id, calendar="primary")
    return event, None


class _NoZone(Exception):
    """A naive approved start and no readable calendar zone: no instant."""


async def _compare_times(event: dict[str, Any], params: dict[str, Any],
                         default_minutes: int) -> tuple[dict[str, Any], str | None]:
    """(evidence, why it disagrees or None)."""
    zone = (event.get("start") or {}).get("timeZone") or await _calendar_zone()
    got_start, got_end = _when(event.get("start")), _when(event.get("end"))
    want_start, naive = _approved_start(str(params.get("start") or ""), zone)
    try:
        minutes = int(params.get("minutes") or default_minutes)
    except (TypeError, ValueError):
        minutes = default_minutes
    evidence = {"event_id": event.get("id"), "calendar": "primary",
                "html_link": event.get("htmlLink"),
                "start": got_start.isoformat() if got_start else None,
                "end": got_end.isoformat() if got_end else None,
                "timezone": zone, "approved_start": params.get("start"),
                "approved_naive": naive}
    if got_start is None or got_end is None:
        return evidence, "the event has no timed start and end"
    if want_start is None and naive and not zone:
        raise _NoZone
    if want_start is None:
        return evidence, "the approved start could not be resolved to an instant"
    if got_start.astimezone(UTC) != want_start.astimezone(UTC):
        return evidence, (f"starts {got_start.isoformat()}, approved "
                          f"{want_start.isoformat()}")
    if got_end - got_start != timedelta(minutes=minutes):
        return evidence, (f"runs {int((got_end - got_start).total_seconds() // 60)} "
                          f"min, approved {minutes}")
    return evidence, None


async def _verify_timed(params: dict[str, Any], event_id: str, *,
                        attendees: list[str], default_minutes: int) -> Verification:
    event, failed = await _event(event_id)
    if failed is not None:
        return failed
    assert event is not None
    try:
        evidence, why = await _compare_times(event, params, default_minutes)
    except _NoZone:
        return unknown("the calendar's timezone could not be read, so the "
                       "approved start has no instant to compare",
                       event_id=event.get("id"), calendar="primary")
    others = _others(event)
    evidence["attendees"] = others
    evidence["summary"] = event.get("summary")
    if why:
        return contradicted(why, **evidence)
    if str(event.get("summary") or "") != str(params.get("summary") or ""):
        return contradicted("the event carries a different title", **evidence)
    if others != sorted(a.lower() for a in attendees):
        return contradicted(f"attendees are {others or 'none'}, approved "
                            f"{attendees or 'none'}", FailureClass.POLICY, **evidence)
    return verified(**evidence)


def _event_id(result: ActionResult) -> str:
    """The event the run made, or the one it found already there."""
    return str(result.rollback_token
               or (result.evidence or {}).get("event_id") or "")


@verifier("calendar.create_block")
async def verify_create_block(params: dict[str, Any], result: ActionResult
                              ) -> Verification:
    if not (event_id := _event_id(result)):
        return unknown("the run recorded no event id")
    return await _verify_timed(params, event_id, attendees=[],
                               default_minutes=60)


@verifier("calendar.book_with")
async def verify_book_with(params: dict[str, Any], result: ActionResult
                           ) -> Verification:
    if not (event_id := _event_id(result)):
        return unknown("the run recorded no event id")
    return await _verify_timed(params, event_id,
                               attendees=[str(params.get("to") or "")],
                               default_minutes=30)


@verifier("calendar.decline_invite")
async def verify_decline(params: dict[str, Any], result: ActionResult) -> Verification:
    event_id = str(params.get("event_id") or "")
    if not event_id:
        return unknown("no event id was approved")
    event, failed = await _event(event_id)
    if failed is not None:
        return failed
    assert event is not None
    me = next((a for a in event.get("attendees") or [] if a.get("self")), None)
    evidence = {"event_id": event_id, "calendar": "primary",
                "response": (me or {}).get("responseStatus")}
    if me is None:
        return contradicted("the event has no attendee marked self", **evidence)
    if me.get("responseStatus") != "declined":
        return contradicted(f"the owner's response reads {me.get('responseStatus')!r}",
                            **evidence)
    return verified(**evidence)


@verifier("calendar.offer_slots")
async def verify_offer_slots(params: dict[str, Any], result: ActionResult
                             ) -> Verification:
    message_id = str((result.evidence or {}).get("message_id") or "")
    if not message_id:
        return unknown("the run recorded no message id")
    from . import _token
    try:
        tok = await _token()
        async with httpx.AsyncClient(timeout=20) as c:
            r = await c.get(f"{GMAIL_API}/messages/{message_id}",
                            headers={"Authorization": f"Bearer {tok}"},
                            params={"format": "metadata",
                                    "metadataHeaders": ["To", "Subject"]})
        r.raise_for_status()
        msg = r.json()
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code == 404:
            return contradicted(f"message {message_id} is not in the mailbox",
                                message_id=message_id)
        return unknown(f"could not read message {message_id}", classify(exc))
    headers = {h.get("name", "").lower(): str(h.get("value") or "")
               for h in (msg.get("payload") or {}).get("headers") or []}
    evidence = {"message_id": msg.get("id"), "labels": msg.get("labelIds"),
                "to": headers.get("to"), "subject": headers.get("subject")}
    if "SENT" not in (msg.get("labelIds") or []):
        return contradicted(f"message {message_id} is not labelled SENT", **evidence)
    if str(params.get("to") or "").lower() not in headers.get("to", "").lower():
        return contradicted("the message went to a different recipient",
                            FailureClass.POLICY, **evidence)
    return verified(**evidence)


# ------------------------------------------------------------------ reconcile

async def _find_event(params: dict[str, Any], *, attendees: list[str],
                      default_minutes: int) -> Verification:
    zone = await _calendar_zone()
    want, naive = _approved_start(str(params.get("start") or ""), zone)
    summary = str(params.get("summary") or "")
    if want is None and naive and not zone:
        return unknown("the calendar's timezone could not be read")
    if want is None or not summary:
        return unknown("the start row names no start and title", FailureClass.VALIDATION)
    try:
        found = await _read("/calendars/primary/events", q=summary,
                            timeMin=(want - timedelta(minutes=1)).isoformat(),
                            timeMax=(want + timedelta(minutes=1)).isoformat(),
                            singleEvents="true", maxResults=10)
    except httpx.HTTPError as exc:
        return unknown("could not search the calendar", classify(exc))
    hits = [e for e in (found or {}).get("items", [])
            if str(e.get("summary") or "") == summary and e.get("status") != "cancelled"]
    if not hits:
        return contradicted("no event with this title at this time is on the calendar",
                            calendar="primary")
    if len(hits) > 1:
        return unknown(f"{len(hits)} matching events; a person should pick",
                       event_ids=[e.get("id") for e in hits])
    return await _verify_timed(params, str(hits[0].get("id")), attendees=attendees,
                               default_minutes=default_minutes)


@reconciler("calendar.create_block")
async def reconcile_create_block(params: dict[str, Any], started: datetime
                                 ) -> Verification:
    return await _find_event(params, attendees=[], default_minutes=60)


@reconciler("calendar.book_with")
async def reconcile_book_with(params: dict[str, Any], started: datetime
                              ) -> Verification:
    return await _find_event(params, attendees=[str(params.get("to") or "")],
                             default_minutes=30)


@reconciler("calendar.decline_invite")
async def reconcile_decline(params: dict[str, Any], started: datetime) -> Verification:
    return await verify_decline(params, ActionResult(False))
