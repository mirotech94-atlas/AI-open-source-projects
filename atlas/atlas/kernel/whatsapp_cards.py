"""Every pending Tier 2 approval card also reaches the owner's WhatsApp.

A card is already copied to WhatsApp the moment it is raised (approvals.propose
-> `_whatsapp_card`), but that copy is fire-and-forget in one process: a restart,
a refused send or a card raised before WhatsApp was configured left a pending card
on the portal that the owner never heard about. This is the catch-up sweep behind it.

Off unless `WHATSAPP_CARDS=on` (default off).

How it stays inside CLAUDE.md:
  * read-only scan of `proposals`; the only send is the Tier 1 action
    `mmt.notify_card`, reached through `executor.execute` (invariant 1);
  * the message is built in code from the card (never the reasoner's text). Every
    Tier 2 card `wa_approvals.eligibility` accepts carries a one-time code and Approve /
    Decline buttons (plain YES/NO text if the template is unavailable); a card it refuses
    has no code and says it can only be decided on the portal (chat never
    approves it, invariant 3). Silence is never consent: nothing here decides;
  * `ATLAS_WATCH_ONLY` sends nothing;
  * once per card: the sweep claims `whatsapp.card.try` before it sends and keeps
    it once a message may have gone out; a certain non-send (not configured, a
    refused send) frees it, at most 3 tries per card per process. It skips any card
    `mmt.notify_card` already claimed (so the at-creation copy and the sweep never
    double up);
  * first run only: of the newest 40 pending cards seen, at most the 5 newest are
    sent, the rest are marked as handled and never messaged (the marker is taken
    only once a real send pass starts, never while unconfigured, capped at 0,
    watch-only, quiet or with the window closed or unreadable);
  * per-hour cap (`WHATSAPP_CARDS_PER_HOUR`, default 5); quiet hours (`QUIET_HOURS`)
    send nothing (the card stays pending and goes in the morning); a closed 24 h
    window (free-form text would be refused) leaves the card waiting on the portal
    (one Slack note only with APPROVALS_SLACK=on);
  * expired, decided or suppressed cards are never sent.
"""
from __future__ import annotations

import logging
import os
from datetime import UTC, datetime
from typing import Any

from .contracts import Severity, Tier
from .executor import ExecutionRequest, execute, watch_only

log = logging.getLogger("atlas.whatsapp_cards")

ACTION = "mmt.notify_card"
TRY_ACTION = "whatsapp.card.try"
SLACK_ACTION = "whatsapp.card.slack"
CATCHUP_KEY = ("whatsapp.cards.catchup", "done")
DEFAULT_PER_HOUR = 5
CATCHUP_MAX = 5
SCAN_MAX = 40


def enabled() -> bool:
    return os.getenv("WHATSAPP_CARDS", "off").strip().lower() in (
        "1", "on", "true", "yes")


def per_hour() -> int:
    try:
        return max(0, int(os.getenv("WHATSAPP_CARDS_PER_HOUR", str(DEFAULT_PER_HOUR))))
    except ValueError:
        return DEFAULT_PER_HOUR


def quiet_now() -> bool:
    from .comms import _quiet_now
    return _quiet_now()


async def window_state(now: datetime) -> bool | None:
    """True open, False truly closed, None unreadable (do nothing, retry next pass:
    a database hiccup must not read as 'closed')."""
    from .whatsapp_alerts import WINDOW, _last_inbound
    try:
        at = await _last_inbound()
    except Exception as exc:                                # noqa: BLE001
        log.warning("cards: last inbound unreadable: %s", type(exc).__name__)
        return None
    return at is not None and now - at <= WINDOW


def setup_problem() -> str | None:
    """Why no card can be sent at all (no owner number or Twilio sender)."""
    from ..modules.mmt import notify
    return notify._problem("post_decision_pending", "card")


_attempts: dict[int, int] = {}
MAX_TRIES = 3
_watch_logged = False


async def _pending(now: datetime) -> list[dict[str, Any]]:
    """Pending, unexpired Tier 2 cards nobody has messaged about, newest first."""
    from .state import fetch_all
    rows = await fetch_all(
        """SELECT p.id, p.action, p.created_at FROM proposals p
            WHERE p.status = 'pending' AND p.tier >= $1 AND p.expires_at > $2
              AND NOT EXISTS (SELECT 1 FROM effect_claims e
                               WHERE e.action = 'mmt.notify_card'
                                 AND e.key = 'card:' || p.id::text)
              AND NOT EXISTS (SELECT 1 FROM effect_claims e
                               WHERE e.action = $3 AND e.key = p.id::text)
            ORDER BY p.id DESC LIMIT $4""", int(Tier.ASK), now, TRY_ACTION, SCAN_MAX)
    return [dict(r) for r in rows or []]


async def sent_last_hour() -> int:
    from .state import fetch_one
    row = await fetch_one(
        "SELECT count(*) AS n FROM effect_claims WHERE action = $1 AND "
        "claimed_at > now() - interval '1 hour'", ACTION)
    return int(row["n"]) if row else 0


async def _claim(action: str, key: str) -> bool:
    from . import idempotency
    return await idempotency.claim(action, key) == idempotency.CLAIMED


async def _first_run() -> bool:
    """True exactly once: the call that wins the marker claim."""
    return await _claim(*CATCHUP_KEY)


async def _skip_silently(pid: int) -> None:
    from . import idempotency
    if await _claim(TRY_ACTION, str(pid)):
        await idempotency.settle(TRY_ACTION, str(pid), "catch-up: older than the newest 5")


async def _free_if_not_sent(pid: int, detail: str) -> None:
    """A certain non-send gives the card back to the next pass (bounded)."""
    from . import idempotency
    if detail.startswith("already notified"):
        return
    reason = detail.split(": ", 1)[-1]
    if idempotency.uncertain(reason):
        return                                              # may have been delivered
    _attempts[pid] = _attempts.get(pid, 0) + 1
    if _attempts[pid] < MAX_TRIES:
        await idempotency.release(TRY_ACTION, str(pid))


async def _slack_note(pid: int, why: str) -> bool:
    """One Slack note per card: only with APPROVALS_SLACK=on (default off). Off,
    the card simply waits (the portal shows it, with a banner for any card that
    never reached WhatsApp)."""
    from . import idempotency, wa_approvals
    from .comms import Outbound, notify
    if not wa_approvals.slack_on():
        return False
    if not await _claim(SLACK_ACTION, str(pid)):
        return False
    try:
        used = await notify(Outbound(
            title=f"Card #{pid} is waiting",
            body=f"Card #{pid} is waiting for your decision on the portal "
                 f"({why}).", severity=Severity.NORMAL, kind="alert", topic="alerts"))
    except Exception as exc:                                # noqa: BLE001
        log.warning("cards: the Slack note failed: %s", type(exc).__name__)
        return False
    if used:
        await idempotency.settle(SLACK_ACTION, str(pid), "noted")
    return bool(used)


async def run(now: datetime | None = None) -> dict[str, Any]:
    global _watch_logged
    if not enabled():
        return {"enabled": False}
    now = now or datetime.now(UTC)
    try:
        cards = await _pending(now)
    except Exception as exc:                                # noqa: BLE001
        log.warning("cards: proposals unreadable: %s", type(exc).__name__)
        return {"error": "unreadable"}
    if not cards:
        _watch_logged = False
        return {"cards": 0}
    if watch_only():
        if not _watch_logged:
            _watch_logged = True
            log.info("whatsapp cards: ATLAS_WATCH_ONLY is on, would tell the owner about "
                     "%d pending card(s)", len(cards))
        return {"watch_only": True, "would_send": len(cards)}
    _watch_logged = False
    if setup_problem():
        return {"not_configured": True, "waiting": len(cards)}
    if quiet_now():
        return {"quiet_hours": True, "waiting": len(cards)}
    if per_hour() == 0:
        return {"cap": 0, "waiting": len(cards)}
    state = await window_state(now)
    if state is None:
        return {"window": "unreadable", "waiting": len(cards)}
    if state is False:
        # Closed for real: one Slack note per card (own claim), newest few only.
        # The card is NOT marked handled: it goes out when the window opens.
        slacked = 0
        for card in cards[:CATCHUP_MAX]:
            if await _slack_note(int(card["id"]), "WhatsApp is closed until you "
                                                  "message me"):
                slacked += 1
        return {"window": "closed", "slack_notes": slacked}
    skipped = 0
    try:
        if await _first_run():
            for old in cards[CATCHUP_MAX:]:
                await _skip_silently(int(old["id"]))
                skipped += 1
            cards = cards[:CATCHUP_MAX]
    except Exception as exc:                                # noqa: BLE001
        log.warning("cards: catch-up marker unreadable: %s", type(exc).__name__)
        return {"error": "unreadable"}
    sent = 0
    for card in reversed(cards):                            # oldest first
        pid = int(card["id"])
        try:
            if await sent_last_hour() >= per_hour():
                log.info("whatsapp cards: hourly cap reached")
                break
        except Exception as exc:                            # noqa: BLE001
            log.warning("cards: cap unreadable: %s", type(exc).__name__)
            break
        if not await _claim(TRY_ACTION, str(pid)):
            continue
        result = await execute(ExecutionRequest(
            action=ACTION, params={"proposal_id": pid},
            reason=f"card #{pid} is pending and was not yet sent"))
        if result.ok:
            sent += 1
        else:
            log.info("whatsapp cards: #%s not sent: %s", pid, result.detail[:120])
            await _free_if_not_sent(pid, result.detail)
    return {"cards": len(cards), "sent": sent, "skipped": skipped}
