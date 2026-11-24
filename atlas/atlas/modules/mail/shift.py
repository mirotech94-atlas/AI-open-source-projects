"""The Inbox shift: daily, 07:00 New York (scheduler `inbox_shift`).

Reads every configured mailbox -- owner@example.com over IMAP, read-only
(plan 2026-10-30, B1) -- through `reply_today.read`, and reports what waits
on the owner. It sends nothing and drafts nothing: `mail.draft_reply` writes a
Gmail draft through the clips mailbox's OAuth token and Gmail thread ids,
and an IMAP thread has neither, so drafting is named as blocked rather than
reported as zero drafts done. Commitments are the ones `classify()` already
stored; they are counted, never re-extracted.

Health: an unreadable mailbox is UNKNOWN, never a zero (invariant 7)."""
from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

from .accounts import all_accounts

log = logging.getLogger("atlas.mail.shift")

DRAFTS_BLOCKED = ("drafting needs Gmail API access to the mailbox; owner is "
                  "read over IMAP with an app password, so no draft is written")


async def _commitments(days: int) -> int | None:
    """Commitments `classify()` stored in the last `days` days; None if unread."""
    from ...kernel.state import fetch_one
    try:
        row = await fetch_one(
            """SELECT count(*) AS n FROM memories
                WHERE namespace = 'commitments'
                  AND at > now() - ($1 || ' days')::interval""", str(days))
    except Exception as exc:                            # noqa: BLE001
        log.warning("inbox shift: commitments unread (%s)", type(exc).__name__)
        return None
    return int(row["n"]) if row else None


async def run(now: datetime | None = None) -> dict[str, Any]:
    """One shift. Pure reads; returns what it found and when."""
    from . import reply_today
    now = now or datetime.now(UTC)
    accounts = all_accounts()
    card = await reply_today.read(accounts, now)
    read_any = bool(accounts) and len(card.unreadable) < len(accounts)
    unclassified = int(card.counts.get("not_read", 0))
    if not read_any:
        health = "unknown"
    elif card.unreadable or unclassified:
        health = "degraded"
    else:
        health = "ok"
    blocked = (DRAFTS_BLOCKED if any(a.kind == "imap" for a in accounts)
               else "the shift does not draft; mail.draft_reply is asked per thread")
    out: dict[str, Any] = {
        "read": card.looked if read_any else None,
        "reply_today": len(card.reply) if read_any else None,
        "drafted": 0,
        "drafts_blocked": blocked,
        "commitments": await _commitments(7),
        "unread_unclassified": unclassified if read_any else None,
        "unreadable": list(card.unreadable),
        "health": health,
        "held": None,
        "at": now.isoformat(),
    }
    log.info("inbox shift: %s", out)
    return out
