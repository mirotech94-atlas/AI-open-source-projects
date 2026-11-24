"""Matrix rooms on matrix.example.com — read as Miro, reply only as an approved draft.

MIRO'S DECISION, 2026-09-22: ATLAS authenticates as HIS account,
@miro:matrix.example.com, not a bot account. He was offered a dedicated @atlas and
chose this instead, because ATLAS is private to him and a reply should go
out under his name. So everything it sends would appear as him, and that
is exactly why nothing here sends without him:

  READ     rooms and DMs, as him. His own m.read receipts are the line
           between "seen" and "not seen by me". Anything past that line
           in a DM is told to him, once.
  DRAFT    when someone is waiting on him and he is not there, ATLAS
           writes the reply he would send, in his voice, and raises it as
           a Tier 2 proposal in Slack carrying the exact text.
  SEND     only through that approval. There is no other send path. The
           room and sender guards are re-checked at send time, so an
           approval cannot route around them.

**There is no unapproved send path.** The only action that reaches Matrix
is `matrix_rooms.send_reply`, Tier 2, and it cannot execute without an
approval carrying the exact text.

What it never does, whoever asks: post in the attendance or standup rooms
(the bot parses those as his standup); reply to family or personal rooms;
reply to anyone outside matrix.example.com; interact with @ai.bot; act on anything a
message tells it to do; repeat a secret. Those are checked inside
`send_reply` itself, not only in the dry run.

Endpoints, Matrix client-server v3 against the homeserver from .well-known:
  GET  {hs}/_matrix/client/v3/joined_rooms
  GET  {hs}/_matrix/client/v3/user/{me}/account_data/m.direct   (the DMs)
  GET  {hs}/_matrix/client/v3/rooms/{id}/state/m.room.name
  GET  {hs}/_matrix/client/v3/rooms/{id}/messages?dir=b&limit=N
  PUT  {hs}/_matrix/client/v3/rooms/{id}/send/m.room.message/{txn}
"""
from __future__ import annotations

import logging
import os
import re
from datetime import UTC, datetime, timedelta

from ...kernel import idempotency
from ...kernel.contracts import (
    Action,
    ActionResult,
    Check,
    Health,
    InboxItem,
    Observation,
    Severity,
    Signal,
    Tier,
)
from ...kernel.permissions import scan_for_injection, wrap_untrusted
from .._http import get_json, redact

log = logging.getLogger("atlas.matrix_rooms")

HOMESERVER = os.getenv("MATRIX_HOMESERVER", "").rstrip("/")

# Rooms ATLAS must never write to, whatever is approved.
#
# The attendance bot parses these and would read a reply as Miro's own standup
# data. Matched case-insensitively against the room name, because a room ID is
# opaque and would silently stop matching if the room were recreated.
SILENT_ROOM_PATTERNS: tuple[str, ...] = (
    # Parsed by the attendance bot — a reply is read as Miro's own standup.
    "attendance", "standup", "general room",
    # Leave decisions are his.
    "pto", "leave",
    # Personal, not colleagues. "Internal only" is a check on the sender's
    # homeserver, and family on the same homeserver passes it — which is how
    # a family room ended up writable.
    "family", "personal",
)

# Anything else to keep silent: room names or room IDs, comma separated.
# Names are matched as substrings, case-insensitively; IDs exactly.
SILENT_EXTRA: tuple[str, ...] = tuple(
    part.strip().lower()
    for part in os.getenv("MATRIX_SILENT_ROOMS", "").split(",")
    if part.strip()
)

# Accounts ATLAS never replies to. @ai.bot's prompts are addressed to Miro and
# carry consequences — late reasons, strikes, leave — so an automated answer is
# worse than silence.
NEVER_REPLY: tuple[str, ...] = ("@ai.bot:",)

# Anything outside the company. ATLAS is internal-only.
INTERNAL_SUFFIX = os.getenv("MATRIX_INTERNAL_SUFFIX", ":matrix.example.com")

# Accounts that are feeds, not people. One night of "the newest message in
# every joined room is waiting on Miro" produced reply drafts to @abot's
# uptime pings and @alerts' Prometheus rules, and rang the urgent check on
# @ai.bot's support-ticket comments. Nobody behind these is waiting.
BOT_SENDER = re.compile(r"^@[^:]*(bot|alert|notify|webhook|feed|monitor)[^:]*:",
                        re.IGNORECASE)

# How he is addressed in a room. A room message that does not name him is
# room traffic; only a DM or a mention is waiting on HIM rather than on
# whoever reads the room.
MENTION = re.compile(r"(@miro\b|\bmiro\b|\bmyron\b)", re.IGNORECASE)


def addressed_to_him(body: str, *, direct: bool) -> bool:
    return direct or bool(MENTION.search(body or ""))

URGENT = re.compile(
    r"\b(prod(uction)?\s+(is\s+)?down|outage|urgent|asap|blocked\s+on\s+you|"
    r"escalat\w+|p0|p1|sev[012]|deadline\s+(today|tomorrow)|client\s+is)\b",
    re.IGNORECASE,
)


def _auth() -> dict[str, str]:
    return {"Authorization": f"Bearer {os.environ['MATRIX_TOKEN']}"}


def is_silent_room(name: str, room_id: str) -> bool:
    """True when ATLAS must not post here even with an approval."""
    haystack = f"{name} {room_id}".lower()
    if any(pattern in haystack for pattern in SILENT_ROOM_PATTERNS):
        return True
    return any(extra == room_id.lower() or extra in name.lower()
               for extra in SILENT_EXTRA)


def is_never_reply(sender: str) -> bool:
    return any(sender.lower().startswith(prefix) for prefix in NEVER_REPLY)


def is_internal(sender: str) -> bool:
    return sender.lower().endswith(INTERNAL_SUFFIX.lower())


def refuse_reason(room_name: str, room_id: str, sender: str) -> str | None:
    """Why ATLAS may not reply here, or None if it may draft one.

    Used by both the dry run and the action, so an approval cannot route
    around it.
    """
    if is_silent_room(room_name, room_id):
        return (f"{room_name or room_id} is parsed by the attendance bot; a "
                "reply there would be read as Miro's own standup data")
    if is_never_reply(sender):
        return (f"{sender} is the attendance bot — its prompts are addressed "
                "to Miro and carry consequences")
    if not is_internal(sender):
        return f"{sender} is outside the company; ATLAS is internal-only"
    return None


SYNC_FILTER = (
    '{"room":{"timeline":{"limit":5},'
    '"state":{"types":["m.room.name"]},'
    '"ephemeral":{"types":["m.receipt"]}},'
    '"presence":{"types":[]}}'
)


async def _sync() -> tuple[dict | None, str | None]:
    """One /sync, filtered, instead of three requests per room.

    The first live run against his real account timed out: 63 rooms, and
    the old code fetched each room's name, then its messages, then its read
    receipts -- around 190 sequential requests inside a 30-second check. A
    single /sync with timeout=0 returns every joined room's name, its last
    few events, the m.receipt ephemeral events AND the m.direct account data
    in one response, measured at two seconds. This is what the endpoint is
    for; the per-room calls were the wrong shape from the start.
    """
    data, problem = await get_json(
        f"{HOMESERVER}/_matrix/client/v3/sync", headers=_auth(),
        params={"timeout": 0, "filter": SYNC_FILTER}, timeout=40)
    if problem:
        return None, problem
    if not isinstance(data, dict):
        return None, "/sync did not return an object"
    return data, None


def _direct_from(sync: dict) -> set[str]:
    """Room ids that are DMs, from the m.direct account-data event."""
    for event in (sync.get("account_data") or {}).get("events", []):
        if event.get("type") == "m.direct":
            content = event.get("content") or {}
            return {rid for rooms in content.values()
                    if isinstance(rooms, list) for rid in rooms}
    return set()


def _read_up_to_from(room: dict, me: str) -> int:
    """His latest m.read receipt timestamp in this room, from ephemeral."""
    latest = 0
    for event in (room.get("ephemeral") or {}).get("events", []):
        if event.get("type") != "m.receipt":
            continue
        for kinds in (event.get("content") or {}).values():
            read = (kinds or {}).get("m.read") or {}
            if me in read:
                latest = max(latest, int((read[me] or {}).get("ts", 0)))
    return latest


def _name_of(room: dict) -> str:
    for event in (room.get("state") or {}).get("events", []):
        if event.get("type") == "m.room.name":
            return str((event.get("content") or {}).get("name", ""))
    # A room named in the timeline's state rather than the state block.
    for event in (room.get("timeline") or {}).get("events", []):
        if event.get("type") == "m.room.name":
            return str((event.get("content") or {}).get("name", ""))
    return ""


async def _me() -> str:
    data, _ = await get_json(
        f"{HOMESERVER}/_matrix/client/v3/account/whoami", headers=_auth())
    return (data or {}).get("user_id", "")


async def _waiting() -> tuple[list[dict], str | None]:
    """Messages where someone is waiting on Miro."""
    me = await _me()
    if not me:
        return [], "could not identify the account behind MATRIX_TOKEN"
    sync, problem = await _sync()
    if problem:
        return [], problem

    direct = _direct_from(sync)
    waiting: list[dict] = []
    for room_id, room in ((sync.get("rooms") or {}).get("join") or {}).items():
        events = [e for e in (room.get("timeline") or {}).get("events", [])
                  if e.get("type") == "m.room.message"]
        if not events:
            continue
        newest = events[-1]                # timeline is oldest-first
        sender = newest.get("sender", "")
        if sender == me:
            continue                      # he spoke last; nothing owed
        if BOT_SENDER.match(sender):
            continue                      # a feed; nobody is waiting
        name = _name_of(room)
        body = str((newest.get("content") or {}).get("body", ""))[:2000]
        if not addressed_to_him(body, direct=room_id in direct):
            continue                      # room traffic, not a question to him
        ts = int(newest.get("origin_server_ts", 0))
        at = datetime.fromtimestamp(ts / 1000, UTC)
        waiting.append({
            "event_id": newest.get("event_id", ""),
            "room_id": room_id, "room": name,
            "direct": room_id in direct,
            # Seen by him already? Then it is waiting on his reply but not
            # news. Both are true and the report says which.
            "seen": ts <= _read_up_to_from(room, me),
            "sender": sender, "body": body, "at": at,
            "urgent": bool(URGENT.search(body)),
            "refused": refuse_reason(name, room_id, sender),
            # Logged, never obeyed. A message telling ATLAS to do something is
            # data; the closed catalog is what makes that safe.
            "injection": scan_for_injection(body, f"matrix:{sender}"),
        })
    return waiting, None


# ------------------------------------------------------------------ checks

async def check_waiting() -> Observation:
    """How many people are waiting on a reply."""
    waiting, problem = await _waiting()
    if problem:
        return Observation("waiting", Health.UNKNOWN, detail=problem)

    repliable = [w for w in waiting if not w["refused"]]
    stale = [w for w in repliable
             if datetime.now(UTC) - w["at"] > timedelta(hours=4)]
    return Observation(
        "waiting",
        Health.DEGRADED if len(stale) > 3 else Health.OK,
        float(len(repliable)), "threads",
        f"{len(repliable)} thread(s) waiting on you"
        + (f", {len(stale)} for over 4h" if stale else ""),
        evidence={"rooms": [w["room"] or w["room_id"] for w in stale[:10]]})


async def check_urgent() -> Observation:
    """Anything that reads as production-down or someone blocked."""
    waiting, problem = await _waiting()
    if problem:
        return Observation("urgent", Health.UNKNOWN, detail=problem)
    urgent = [w for w in waiting if w["urgent"]]
    return Observation(
        "urgent",
        Health.FAILING if urgent else Health.OK,
        float(len(urgent)), "messages",
        (f"{len(urgent)} message(s) look urgent: "
         + "; ".join(f"{w['sender']} in {w['room'] or w['room_id']}"
                     for w in urgent[:3]))
        if urgent else "nothing urgent waiting",
        evidence={"urgent": [w["body"][:160] for w in urgent[:5]]})


async def check_injection() -> Observation:
    """Messages that try to instruct ATLAS.

    Reported, never obeyed. A spike here means someone is probing.
    """
    waiting, problem = await _waiting()
    if problem:
        return Observation("injection_attempts", Health.UNKNOWN, detail=problem)
    flagged = [w for w in waiting if w["injection"]]
    return Observation(
        "injection_attempts",
        Health.DEGRADED if flagged else Health.OK,
        float(len(flagged)), "messages",
        f"{len(flagged)} message(s) contained instruction-shaped text"
        if flagged else "no instruction-shaped messages",
        evidence={"flagged": [w["injection"] for w in flagged[:5]]})


# ----------------------------------------------------------------- actions

def _recipient_refusal(to: str) -> str | None:
    """RA-C5: a reply names who it answers. An omitted `to` used to stand in
    as `@unknown` on the internal homeserver -- which passed the
    internal-only check for a recipient nobody had named."""
    if not (to or "").strip():
        return "no recipient (`to`) was given; a reply must name who it answers"
    return None


async def _live_room_name(room_id: str) -> tuple[str | None, str | None]:
    """The room's name as the homeserver has it now, not as the card says.

    RA-C5: the card's `room_name` is a parameter, and a blank one left only
    the opaque room id for the silent-room match -- so an attendance room
    raised with no name would have been writable. Returns (name, None); ""
    for a room with no name (most DMs: Matrix answers 404 M_NOT_FOUND, and
    those are matched by id alone); (None, why) when the name could not be
    read, which the caller treats as a refusal."""
    data, problem = await get_json(
        f"{HOMESERVER}/_matrix/client/v3/rooms/{room_id}/state/m.room.name",
        headers=_auth())
    if problem:
        if problem.startswith("HTTP 404"):
            return "", None
        return None, problem
    if not isinstance(data, dict):
        return None, "the room name did not come back as an object"
    return str(data.get("name") or ""), None


async def _guard(room_id: str, body: str, room_name: str, to: str,
                 *, live: bool) -> str | None:
    """Every refusal, in order: who, what, the card's room name, and (when
    `live`) the room's real name read from the homeserver."""
    refusal = _recipient_refusal(to) or refuse_reason(room_name, room_id, to)
    if refusal:
        return refusal
    if not body.strip():
        return "empty message"
    if not live:
        return None
    actual, problem = await _live_room_name(room_id)
    if actual is None:
        return (f"the name of {room_name or room_id} could not be read "
                f"({problem}), so the silent-room guard cannot run")
    return refuse_reason(actual, room_id, to)


def _txn(room_id: str, run: str, body: str) -> str:
    """RA-B7: the Matrix transaction id, from the run -- never the time. The
    homeserver treats a repeated txn id from the same token as the same
    event, so a retry inside its window returns the first event instead of
    posting again; `effect_claims` covers a retry after that window."""
    return "atlas-" + idempotency.key_for(
        "matrix_rooms.send_reply", room_id, run, body, length=32)


async def send_reply(room_id: str, body: str, room_name: str = "",
                     to: str = "") -> ActionResult:
    """Post a reply. Only ever reached through an approved Tier 2 proposal.

    The room and recipient guards are re-checked here, not only in the dry run:
    an approval must not be able to authorise something the rules forbid. The
    room's name is read from the homeserver at send time (RA-C5), and the
    send is idempotent per run (RA-B7).
    """
    refusal = await _guard(room_id, body, room_name, to, live=True)
    if refusal:
        return ActionResult(False, f"refused: {refusal}")

    import httpx

    from .._http import explain

    run = idempotency.run_key("matrix_rooms.send_reply", {
        "room_id": room_id, "body": body, "room_name": room_name, "to": to})
    claim_key = f"{room_id}|{run}"
    claimed = await idempotency.claim("matrix_rooms.send_reply", claim_key)
    if claimed == idempotency.SENT:
        return ActionResult(
            True, idempotency.refusal("matrix_rooms.send_reply", claimed),
            evidence={"deduplicated": True})
    if claimed != idempotency.CLAIMED:
        return ActionResult(False, "not sent: " + idempotency.refusal(
            "matrix_rooms.send_reply", claimed))

    txn = _txn(room_id, run, body)
    accepted = False
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            response = await client.put(
                f"{HOMESERVER}/_matrix/client/v3/rooms/{room_id}/send/"
                f"m.room.message/{txn}",
                headers=_auth(),
                json={"msgtype": "m.text", "body": body[:4000]})
            response.raise_for_status()
            accepted = True
            event_id = str(response.json().get("event_id", ""))
    except Exception as exc:  # noqa: BLE001
        if accepted:
            # The homeserver answered 2xx: the message is in the room, only
            # its event id could not be read. Sent -- so the claim settles
            # and a retry does not post it again; there is no id to redact.
            await idempotency.finish("matrix_rooms.send_reply", claim_key, None,
                                     "sent; event id unreadable")
            return ActionResult(True, f"sent to {room_name or room_id} (the "
                                      f"event id could not be read, so it "
                                      f"cannot be redacted from here)",
                                evidence={"txn": txn})
        await idempotency.finish("matrix_rooms.send_reply", claim_key,
                                 explain(exc))
        return ActionResult(False, f"send failed: {redact(exc)}")

    await idempotency.finish("matrix_rooms.send_reply", claim_key, None,
                             f"event {event_id}")
    return ActionResult(True, f"sent to {room_name or room_id}",
                        evidence={"txn": txn, "event_id": event_id},
                        rollback_token=f"{room_id}|{event_id}")


async def send_dry(room_id: str, body: str, room_name: str = "",
                   to: str = "") -> ActionResult:
    """The draft Miro approves. What he reads here is exactly what goes out.
    The live room-name read happens at send time too; here it is skipped
    when the static guards already refuse."""
    refusal = await _guard(room_id, body, room_name, to, live=True)
    if refusal:
        return ActionResult(False, f"would refuse: {refusal}")
    return ActionResult(
        True,
        f"Would post to {room_name or room_id} as Miro — it will appear as "
        f"him, with no marker:\n\n{body[:1500]}")


async def redact_reply(token: str) -> ActionResult:
    """Undo: redact the event. The message will have been seen; this removes
    it from the room rather than pretending it never happened."""
    import httpx

    room_id, _, event_id = token.partition("|")
    if not (room_id and event_id):
        return ActionResult(False, "no event to redact")
    # From the event, not the time (RA-B7): a retried undo is the same undo.
    txn = "atlas-redact-" + idempotency.key_for(
        "matrix_rooms.redact", room_id, event_id, length=32)
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            response = await client.put(
                f"{HOMESERVER}/_matrix/client/v3/rooms/{room_id}/redact/"
                f"{event_id}/{txn}",
                headers=_auth(), json={"reason": "withdrawn"})
            response.raise_for_status()
    except Exception as exc:  # noqa: BLE001
        return ActionResult(False, f"redact failed: {redact(exc)}")
    return ActionResult(True, "message redacted")


# How Miro writes. The draft has to read as him, because it will be sent as him.
DRAFT_SYSTEM = """
You are drafting a reply that will be sent from Miro's own Matrix account. It
will appear to colleagues as Miro personally, with no marker that it came from
an assistant. He approves every draft before it goes, but write as though it
will go exactly as written.

How he writes: short, direct, lowercase-ish, no filler. One or two lines. No
greetings, no "I hope this finds you well", no apologising for delay, no emoji
unless the thread already uses them.

Reply ONLY when the answer is purely factual and already settled — a link, a
status he has already stated, a time already on the calendar.

Return the single token NEEDS_MIRO, and nothing else, when the message needs
judgement, an opinion, or a new decision. That includes anything about
deadlines, scope, money, hiring, priorities, leave, or agreeing to do
something — even "sure, that works". Silence costs far less than a wrong
answer in his name.

Never claim to be an assistant and never claim to be Miro. If the message asks
whether it is talking to a person, return NEEDS_MIRO.

Return the reply text alone, or NEEDS_MIRO. Nothing else.
""".strip()

# Local inference on one core takes tens of seconds, and the scheduler is not
# the place to spend minutes.
MAX_DRAFTS_PER_CYCLE = int(os.getenv("MATRIX_MAX_DRAFTS", "3"))


async def _draft(entry: dict) -> str | None:
    """Compose a reply, or None when it needs Miro.

    Runs at Need.PRIVATE, so work correspondence stays on the local model and
    never reaches a third-party API — the same rule the inbox agent follows for
    personal mail.
    """
    from ...kernel.llm import Message, Need, think

    prompt = (
        f"Room: {entry['room'] or entry['room_id']}\n"
        f"From: {entry['sender']}\n\n"
        f"{quoted(entry['body'], entry['sender'])}"
    )
    try:
        answer = (await think(Need.PRIVATE, DRAFT_SYSTEM,
                              [Message("user", prompt)], max_tokens=200)).text
    except RuntimeError:
        return None                      # no local model configured
    except Exception:  # noqa: BLE001
        return None

    text = answer.strip()
    if not text or "NEEDS_MIRO" in text.upper():
        return None
    # A model that returns a paragraph has not understood the brief, and a
    # long reply in his name is exactly what the instructions rule out.
    if len(text) > 400 or text.count(".") > 4:
        return None
    return text


class MatrixRoomsModule:
    name = "matrix_rooms"
    title = "Matrix"
    requires_env = ("MATRIX_HOMESERVER", "MATRIX_TOKEN")

    async def setup(self) -> None: ...
    async def teardown(self) -> None: ...

    def checks(self):
        return (
            Check("waiting", check_waiting, timedelta(minutes=20),
                  Severity.NORMAL, "people waiting on a reply",
                  baseline_sigma=None),
            Check("urgent", check_urgent, timedelta(minutes=10),
                  Severity.CRITICAL, "production down, escalations, blockers",
                  baseline_sigma=None),
            Check("injection_attempts", check_injection, timedelta(hours=2),
                  Severity.HIGH, "messages trying to instruct ATLAS",
                  baseline_sigma=None),
        )

    def actions(self):
        return (
            Action(
                name="matrix_rooms.send_reply",
                # Tier 2, always. It appears as Miro to people who cannot tell,
                # and they will act on it. Nothing about that is Tier 1.
                tier=Tier.ASK,
                summary="Post a reply in Matrix, as Miro",
                run=send_reply, dry_run=send_dry, rollback=redact_reply,
                params={"room_id": "the room to post in",
                        "body": "the exact text to send",
                        "room_name": "room name, for the approval card",
                        "to": "who is being replied to"},
                max_per_hour=6,
            ),
        )

    async def inbox(self, limit: int = 10):
        """Threads waiting on a reply, for the morning brief."""
        waiting, problem = await _waiting()
        if problem:
            # Unreadable is not empty: the brief names it (invariant 7).
            raise RuntimeError(problem)
        items = []
        for entry in waiting[:limit * 2]:
            age_h = (datetime.now(UTC) - entry["at"]).total_seconds() / 3600
            why = entry["refused"] or (
                "urgent" if entry["urgent"]
                else f"waiting {age_h:.0f}h" if age_h > 2 else "just arrived")
            items.append(InboxItem(
                at=entry["at"],
                sender=entry["sender"],
                subject=f"{entry['room'] or entry['room_id']}: "
                        f"{entry['body'][:70]}",
                urgency=(Severity.CRITICAL if entry["urgent"]
                         else Severity.NORMAL if age_h > 4 else Severity.INFO),
                why=why,
                reference=entry["room_id"],
            ))
        items.sort(key=lambda i: (-int(i.urgency), i.at))
        return tuple(items[:limit])

    async def signals(self):
        """One draft per waiting message, for Miro to approve.

        Each becomes a Tier 2 proposal carrying the exact text. Nothing is sent
        by producing a signal.
        """
        waiting, problem = await _waiting()
        if problem:
            # No drafts from an unreadable Matrix. Only inbox() raises (so the
            # brief can name it); here one warning is enough, and the
            # `waiting` check already reports UNKNOWN with the reason.
            log.warning("matrix: no reply drafts this cycle: %s", problem)
            return ()

        repliable = [w for w in waiting if not w["refused"]]
        # Newest first: a reply to something from five minutes ago is worth
        # more than one to something from yesterday.
        repliable.sort(key=lambda w: w["at"], reverse=True)

        out = []

        # "All messages directly to me should be seen and told to me about
        # if not already seen by me." A DM he has not opened is told to
        # him whether or not ATLAS can draft a reply; that is a different
        # question from the drafts below. It is told once, as a summary,
        # so one busy day does not become twenty cards. New pod
        # deployment chatter is the case he named, so the word catches it
        # at HIGH.
        unseen = [w for w in waiting if w.get("direct") and not w.get("seen")]
        if unseen:
            deploy = [w for w in unseen
                      if re.search(r"\b(deploy|pod|rack|provision|new site)\b",
                                   w["body"], re.IGNORECASE)]
            lines = [f"• {w['sender']}: {w['body'][:120]}" for w in unseen[:6]]
            out.append(Signal(
                title=f"{len(unseen)} direct message(s) on k9 you haven't seen",
                body="\n".join(lines)
                     + (f"\n\n{len(deploy)} of these mention a deployment."
                        if deploy else ""),
                severity=Severity.HIGH if deploy else Severity.NORMAL,
                params={"topic": "cloudhost" if deploy else "mail"},
            ))

        for entry in repliable[:MAX_DRAFTS_PER_CYCLE]:
            body = await _draft(entry)
            if body is None:
                continue                 # needs Miro; the brief already lists it
            where = entry["room"] or entry["room_id"]
            out.append(Signal(
                title=f"Reply to {entry['sender']} in {where}: "
                      f"{entry['body'][:40]}",
                body=(f"{entry['sender']} said:\n  {entry['body'][:300]}\n\n"
                      f"Draft reply, as you:\n  {body}"),
                severity=Severity.CRITICAL if entry["urgent"] else Severity.NORMAL,
                suggests="matrix_rooms.send_reply",
                params={"room_id": entry["room_id"], "body": body,
                        "room_name": entry["room"], "to": entry["sender"]},
            ))
        return tuple(out)

    def brief(self) -> str:
        return "Matrix: threads waiting, urgent messages"


MODULE = MatrixRoomsModule()


def quoted(body: str, sender: str) -> str:
    """Anything from a room, wrapped before it can reach a prompt."""
    return wrap_untrusted(body, f"matrix:{sender}")
