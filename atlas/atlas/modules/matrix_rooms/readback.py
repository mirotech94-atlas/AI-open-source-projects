"""Read-back for `matrix_rooms.send_reply` (RA-D1). Reads only, Matrix
client-server v3:

  GET {hs}/_matrix/client/v3/account/whoami
  GET {hs}/_matrix/client/v3/rooms/{room}/event/{event}
  GET {hs}/_matrix/client/v3/rooms/{room}/messages?dir=b&limit=N

The event the send returned must be in the room, sent by this account, an
m.room.message with the approved body, and not redacted. The reconciler
reads the room's newest messages for one from this account with that body,
sent at or after the run started; absence is a contradiction only when the
response has no `end` token (the whole room) or reaches back past the start. Never sends, never redacts. See
docs/verification.md.
"""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from urllib.parse import quote

from ...kernel.contracts import ActionResult
from ...kernel.verify import (
    FailureClass,
    Verification,
    contradicted,
    reconciler,
    unknown,
    verified,
    verifier,
)
from .. import matrix_rooms as mr
from .._http import get_json

PAGE = 50


def _url(path: str) -> str:
    return f"{mr.HOMESERVER}/_matrix/client/v3/{path}"


def _class(reason: str) -> FailureClass:
    return (FailureClass.AUTH if "401" in reason or "403" in reason
            else FailureClass.TRANSIENT)


async def _me() -> tuple[str | None, str | None]:
    data, reason = await get_json(_url("account/whoami"), headers=mr._auth())
    if reason:
        return None, reason
    user = data.get("user_id") if isinstance(data, dict) else None
    return (str(user), None) if user else (None, "whoami named no user")


def _body(params: dict[str, Any]) -> str:
    return str(params.get("body") or "")[:4000]


@verifier("matrix_rooms.send_reply")
async def verify_send(params: dict[str, Any], result: ActionResult) -> Verification:
    room_id, _, event_id = (result.rollback_token or "").partition("|")
    if not room_id or not event_id:
        return unknown("the run recorded no room and event id")
    if room_id != str(params.get("room_id") or room_id):
        return contradicted("the reply went to a different room than approved",
                            room_id=room_id)
    me, reason = await _me()
    if me is None:
        return unknown(f"could not read which account this is: {reason}",
                       _class(reason or ""))
    event, reason = await get_json(
        _url(f"rooms/{quote(room_id, safe='')}/event/{quote(event_id, safe='')}"),
        headers=mr._auth())
    evidence = {"room_id": room_id, "event_id": event_id}
    if reason:
        if "HTTP 404" in reason:
            return contradicted(f"event {event_id} is not in the room", **evidence)
        return unknown(f"could not read the event: {reason}", _class(reason),
                       **evidence)
    if not isinstance(event, dict):
        return unknown("the homeserver answered with an unexpected shape", **evidence)
    evidence["origin_server_ts"] = event.get("origin_server_ts")
    if (event.get("unsigned") or {}).get("redacted_because"):
        return contradicted(f"event {event_id} was redacted", **evidence)
    if event.get("sender") != me:
        return contradicted(f"event {event_id} was not sent by this account", **evidence)
    content = event.get("content") or {}
    if event.get("type") != "m.room.message" or content.get("body") != _body(params):
        return contradicted(f"event {event_id} carries a different message", **evidence)
    return verified(**evidence)


@reconciler("matrix_rooms.send_reply")
async def reconcile_send(params: dict[str, Any], started: datetime) -> Verification:
    room_id, body = str(params.get("room_id") or ""), _body(params)
    if not room_id or not body.strip():
        return unknown("the start row names no room and body", FailureClass.VALIDATION)
    me, reason = await _me()
    if me is None:
        return unknown(f"could not read which account this is: {reason}",
                       _class(reason or ""))
    data, reason = await get_json(
        _url(f"rooms/{quote(room_id, safe='')}/messages"), headers=mr._auth(),
        params={"dir": "b", "limit": PAGE})
    if reason:
        return unknown(f"could not read the room: {reason}", _class(reason),
                       room_id=room_id)
    events = data.get("chunk") if isinstance(data, dict) else None
    if not isinstance(events, list):
        return unknown("the room answered with no event list", room_id=room_id)
    since = started.timestamp() * 1000
    hits = [e for e in events if isinstance(e, dict) and e.get("sender") == me
            and e.get("type") == "m.room.message"
            and (e.get("content") or {}).get("body") == body
            and isinstance(e.get("origin_server_ts"), int)
            and e["origin_server_ts"] >= since]
    if len(hits) > 1:
        return unknown(f"{len(hits)} matching events; a person should pick",
                       room_id=room_id, event_ids=[h.get("event_id") for h in hits])
    if hits:
        return verified(room_id=room_id, event_id=hits[0].get("event_id"),
                        origin_server_ts=hits[0].get("origin_server_ts"))
    stamps = [e.get("origin_server_ts") for e in events if isinstance(e, dict)
              and isinstance(e.get("origin_server_ts"), int)]
    # A short chunk is not the whole room (a server may filter); only a
    # response with no `end` token is (client-server spec, /messages).
    reaches_back = ("end" not in data) or bool(stamps and min(stamps) <= since)
    if reaches_back:
        return contradicted("no reply with this text was sent to the room after "
                            "the run started", room_id=room_id,
                            read_back_to=datetime.fromtimestamp(
                                min(stamps) / 1000, UTC).isoformat() if stamps else None)
    return unknown("the newest page of the room does not reach back to the run's "
                   "start", FailureClass.STALE, room_id=room_id)
