"""`notify_owner(kind, text)`: ATLAS texts Miro on the one WhatsApp chat.

The linked device can message only the owner's own number, so there is ONE chat; a
short tag at the start of each message keeps the sources apart:

  [Contact]  website contact-form messages (`contact.py`, its own rows)
  [Portal]   agency events: payments, a lead asking to be called or replying
  [Alert]    delivery failures and health problems

How it stays calm (Miro's chatter policy):
  * `notify_owner` only writes a row and asks the executor (`agency.notify_owner`,
    Tier 1, recipient fixed to the owner by `contact.owner_whatsapp`). It never
    raises into its caller: an event must not break because WhatsApp is down.
  * Identical text of the same kind inside one hour is stored once (dedupe).
  * Quiet hours 23:00-07:00 America/New_York: only the urgent kind (`alert`) goes;
    the rest waits for 07:00.
  * Low-value kinds (`portal_digest`) are folded into ONE message: sent when eight
    are waiting or the oldest is an hour old.
  * One shared hourly cap with contact messages (`contact.wa_hourly_cap`); portal
    kinds leave RESERVE slots free for contact messages, [Alert] may use them.
  * No route (bridge down or not linked) is not an attempt: the row waits.

The text is composed by ATLAS code from records it already holds; words that came
from outside (a lead's name) are defanged and length-capped. No model writes it.
"""
from __future__ import annotations

import hashlib
import logging
import re
from datetime import UTC, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from ...kernel.contracts import Action, ActionResult, FailureClass, Tier
from .._http import explain

log = logging.getLogger("atlas.agency.owner_wa")

ACTION = "agency.notify_owner"
NY = ZoneInfo("America/New_York")
# kind -> (tag, urgent, digest)
KINDS: dict[str, tuple[str, bool, bool]] = {
    "portal": ("[Portal]", False, False),
    "portal_digest": ("[Portal]", False, True),
    "alert": ("[Alert]", True, False),
}
DEDUPE_FOR = timedelta(hours=1)
QUIET_FROM, QUIET_TO = 23, 7
TEXT_MAX = 600
RESERVE = 5                       # slots a non-urgent notice leaves for contact messages
DIGEST_COUNT = 8
DIGEST_AGE = timedelta(hours=1)
MAX_ATTEMPTS = 3
STALE_AFTER = timedelta(minutes=15)
_SPACES = re.compile(r"\s+")


def clean(text: object, limit: int = TEXT_MAX) -> str:
    """One flat line, no control or bidi characters, links defanged, capped."""
    from .contact import _CONTROL, _defang
    if not isinstance(text, str):
        return ""
    return _defang(_SPACES.sub(" ", _CONTROL.sub(" ", text)).strip())[:limit]


def is_quiet(now: datetime) -> bool:
    hour = now.astimezone(NY).hour
    return hour >= QUIET_FROM or hour < QUIET_TO


def dedupe_key(kind: str, text: str) -> str:
    return hashlib.sha256(f"{kind}\n{text}".encode()).hexdigest()


def body_for(kind: str, text: str) -> str:
    return f"{KINDS[kind][0]} {text}"


class Store:
    """SQL, one atomic statement per step (tests substitute a fake)."""

    async def insert(self, kind: str, text: str, status: str, now: datetime) -> int | None:
        """The new id, or None when the same text of the same kind was stored in the
        last hour."""
        from ...kernel.state import fetch_one
        key = dedupe_key(kind, text)
        row = await fetch_one(
            """INSERT INTO agency_owner_wa (kind, text, dedupe_key, status, created_at, updated_at)
               SELECT $1, $2, $3, $4, $5, $5
               WHERE NOT EXISTS (SELECT 1 FROM agency_owner_wa
                                 WHERE dedupe_key = $3 AND created_at > $6)
               RETURNING id""", kind, text, key, status, now, now - DEDUPE_FOR)
        return int(row["id"]) if row else None

    async def peek(self, msg_id: int) -> dict[str, Any] | None:
        from ...kernel.state import fetch_one
        row = await fetch_one("SELECT * FROM agency_owner_wa WHERE id = $1", int(msg_id))
        return dict(row) if row else None

    async def waiting(self, limit: int, now: datetime) -> list[int]:
        from ...kernel.state import fetch_all
        rows = await fetch_all(
            "SELECT id FROM agency_owner_wa WHERE status = 'pending' OR "
            "(status = 'sending' AND updated_at < $2) ORDER BY id LIMIT $1",
            limit, now - STALE_AFTER)
        return [int(r["id"]) for r in rows or []]

    async def claim(self, msg_id: int, now: datetime) -> dict[str, Any] | None:
        from ...kernel.state import fetch_one
        row = await fetch_one(
            "UPDATE agency_owner_wa SET status = 'sending', attempts = attempts + 1, "
            "updated_at = $2 WHERE id = $1 AND (status = 'pending' OR "
            "(status = 'sending' AND updated_at < $3)) RETURNING *",
            int(msg_id), now, now - STALE_AFTER)
        return dict(row) if row else None

    async def sent(self, msg_id: int, wa_id: str, now: datetime) -> None:
        from ...kernel.state import execute_sql
        await execute_sql("UPDATE agency_owner_wa SET status = 'sent', wa_id = $2, "
                          "sent_at = $3, error = NULL, updated_at = $3 WHERE id = $1",
                          int(msg_id), wa_id, now)

    async def failed(self, msg_id: int, reason: str, now: datetime) -> None:
        from ...kernel.state import execute_sql
        await execute_sql(
            "UPDATE agency_owner_wa SET error = $2, updated_at = $3, status = "
            "CASE WHEN attempts >= $4 THEN 'failed' ELSE 'pending' END WHERE id = $1",
            int(msg_id), reason[:300], now, MAX_ATTEMPTS)

    async def sent_in_last_hour(self, now: datetime) -> int:
        from ...kernel.state import fetch_one
        row = await fetch_one("SELECT count(*) AS n FROM agency_owner_wa WHERE sent_at > $1",
                              now - timedelta(hours=1))
        return int(row["n"]) if row else 0

    async def digest_rows(self) -> list[dict[str, Any]]:
        from ...kernel.state import fetch_all
        rows = await fetch_all("SELECT id, text, created_at FROM agency_owner_wa "
                               "WHERE status = 'digest' ORDER BY id LIMIT 30")
        return [dict(r) for r in rows or []]

    async def fold(self, ids: list[int], now: datetime) -> None:
        from ...kernel.state import execute_sql
        await execute_sql("UPDATE agency_owner_wa SET status = 'folded', updated_at = $2 "
                          "WHERE id = ANY($1::bigint[]) AND status = 'digest'", ids, now)


_store: Any = Store()


def use_store(new: Any) -> Any:
    global _store
    old, _store = _store, new
    return old


def _now() -> datetime:
    return datetime.now(UTC)


async def sent_in_last_hour(now: datetime) -> int:
    return await _store.sent_in_last_hour(now)


# ------------------------------------------------------------------ the helper

async def notify_owner(kind: str, text: str) -> int | None:
    """Queue one message to Miro and, unless it is digest or quiet, send it now
    through the executor. Returns the row id (None: duplicate, bad input or error).
    Never raises."""
    try:
        if kind not in KINDS:
            log.warning("owner_wa: unknown kind %r", kind)
            return None
        text = clean(text)
        if not text:
            return None
        _, _, digest = KINDS[kind]
        msg_id = await _store.insert(kind, text, "digest" if digest else "pending", _now())
        if msg_id is None or digest:
            return msg_id
        await _run(msg_id)
        return msg_id
    except Exception as exc:  # noqa: BLE001
        log.warning("owner_wa: not queued (%s)", type(exc).__name__)
        return None


async def _run(msg_id: int) -> None:
    """Through the executor (kernel side: this module never imports it)."""
    from ...kernel import agency_contact
    await agency_contact.send_owner_notice(msg_id)


async def drain(limit: int = 10) -> int:
    """Retry waiting rows (quiet hours over, link back) and fold the digest."""
    now = _now()
    done = 0
    for msg_id in await _store.waiting(limit, now):
        await _run(msg_id)
        done += 1
    rows = await _store.digest_rows()
    if rows and not is_quiet(now) and (
            len(rows) >= DIGEST_COUNT or now - rows[0]["created_at"] >= DIGEST_AGE):
        lines = "; ".join(r["text"] for r in rows)
        head = f"Digest of {len(rows)} updates: "
        combined = (head + lines)[:TEXT_MAX]
        msg_id = await _store.insert("portal", combined, "pending", now)
        await _store.fold([r["id"] for r in rows], now)
        if msg_id is not None:
            await _run(msg_id)
            done += 1
    return done


# ------------------------------------------------------------------ the action

def vet(params: dict[str, Any]) -> str | None:
    mid = params.get("msg_id")
    if not isinstance(mid, int) or isinstance(mid, bool) or mid < 1:
        return "msg_id must be a positive integer"
    return None


async def _gate(msg_id: int, now: datetime) -> tuple[dict[str, Any] | None, str]:
    from . import contact
    row = await _store.peek(msg_id)
    if row is None:
        return None, "no such message"
    if row["status"] not in ("pending", "sending") or row["kind"] not in KINDS:
        return None, f"message is already {row['status']}"
    _, urgent, _ = KINDS[row["kind"]]
    if is_quiet(now) and not urgent:
        return None, "quiet hours (23:00-07:00 ET): only [Alert] goes now"
    why = contact.whatsapp_problem()
    if why:
        return None, why
    route, why = await contact.wa_route()
    if route is None:
        return None, why
    cap = contact.wa_hourly_cap()
    limit = cap if urgent else max(0, cap - RESERVE)
    if await contact.wa_sent_in_last_hour(now) >= limit:
        return None, "the shared hourly WhatsApp cap is reached"
    return row, ""


async def run(msg_id: int = 0) -> ActionResult:
    from . import contact
    now = _now()
    row, why = await _gate(int(msg_id), now)
    if row is None:
        return ActionResult(False, f"refused: {why}; it waits",
                            failure_class=FailureClass.VALIDATION)
    taken = await _store.claim(int(msg_id), now)
    if taken is None:
        return ActionResult(False, "refused: this attempt is already taken")
    try:
        ok, got = await contact._send_wa(body_for(taken["kind"], taken["text"]))
    except Exception as exc:  # noqa: BLE001
        ok, got = False, explain(exc)
    if not ok:
        await _store.failed(int(msg_id), str(got), _now())
        return ActionResult(False, f"owner WhatsApp failed: {got}")
    await _store.sent(int(msg_id), str(got), _now())
    return ActionResult(True, f"told the owner ({taken['kind']}) #{msg_id}",
                        evidence={"kind": taken["kind"], "id": str(got)},
                        rollback_token=str(msg_id))


async def dry(msg_id: int = 0) -> ActionResult:
    row, why = await _gate(int(msg_id), _now())
    if row is None:
        return ActionResult(False, f"would not send: {why}")
    return ActionResult(True, f"would send a [{row['kind']}] notice to the owner's WhatsApp",
                        evidence={"kind": row["kind"]})


async def rollback(token: str) -> ActionResult:
    return ActionResult(True, "a delivered message cannot be unsent; nothing further is sent",
                        evidence={"unsent": False})


def actions() -> tuple[Action, ...]:
    return (
        Action(name=ACTION, tier=Tier.AUTO,
               summary="Send one tagged notice ([Portal] / [Alert]) to the owner's own "
                       "WhatsApp chat",
               run=run, dry_run=dry, rollback=rollback, vet=vet,
               params={"msg_id": "the agency_owner_wa row id"},
               max_per_hour=40),
    )
