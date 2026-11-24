"""Reading the ATLAS Academy support mailbox into tickets. A collector: it
reads, it never sends, and it marks nothing read.

The address is ACADEMY_SUPPORT_ADDRESS (default owner@example.com, since
2026-11-14). The mail must reach a Gmail ATLAS reads (ACADEMY_SUPPORT_MAILBOX,
default "clips", one of MAIL_ACCOUNTS, connected with ATLAS_GOOGLE_MAILBOX=CLIPS
bootstrap/google-login.sh), by a forward from the support address into it, and
this sweep asks Gmail for `to:<address>` from the last seven days. Each message
becomes one ticket, keyed by its Gmail id, so a second sweep never makes a
second ticket.

Never a ticket: mail from a machine (`mail.accounts.automated`, an
Auto-Submitted or bulk/list header), mail from our own addresses, and a
message that only says STOP -- that address is suppressed instead.
`authenticated` records Gmail's DKIM/DMARC verdict; it is shown to the owner,
and a reply only ever goes back to the From address, never to an address
named inside the message.

`inbox_state()` is what the SLA check reads: None when the last sweep read
the mailbox, otherwise why it could not -- so the check says UNKNOWN rather
than "nothing waiting" about an inbox nobody read.
"""
from __future__ import annotations

import logging
import os
from datetime import UTC, datetime, timedelta
from email.utils import parseaddr
from typing import Any

from . import support
from .support_guard import is_stop

log = logging.getLogger("academy.example.support_mail")

ACCOUNT_ENV = "ACADEMY_SUPPORT_MAILBOX"
DEFAULT_MAILBOX = "clips"
STALE_AFTER = timedelta(minutes=45)
_last: dict[str, Any] = {"at": None, "ok": False, "detail": "the support inbox has not "
                         "been read since ATLAS started"}


def mailbox_name() -> str:
    """The MAIL_ACCOUNTS name of the support mailbox (ACADEMY_SUPPORT_MAILBOX)."""
    return os.getenv(ACCOUNT_ENV, "").strip().lower() or DEFAULT_MAILBOX


def account():
    """The configured Gmail account the support address forwards into, or None."""
    from ..mail.accounts import all_accounts
    name = mailbox_name()
    for acct in all_accounts():
        if acct.name == name and acct.kind == "google" and acct.configured:
            return acct
    return None


def inbox_state(now: datetime | None = None) -> str | None:
    """None when the support inbox was read recently; else why not."""
    if account() is None:
        return (f"the support inbox can't be read: no configured Gmail account "
                f"{mailbox_name()!r} ({ACCOUNT_ENV}) for "
                f"{support.support_address()}")
    now = now or datetime.now(UTC)
    at = _last.get("at")
    if not _last.get("ok"):
        return str(_last.get("detail"))
    if at is None or now - at > STALE_AFTER:
        return "the support inbox has not been read in the last 45 minutes"
    return None


def _note(ok: bool, detail: str) -> None:
    _last.update(at=datetime.now(UTC), ok=ok, detail=detail)


def _ours(sender: str) -> bool:
    from ..agency.leads import reply_addresses
    low = sender.lower()
    return low == support.support_address() or low in reply_addresses()


async def sweep(days: int = 7, limit: int = 25) -> dict[str, Any]:
    """New mail to the support address -> tickets. Returns counts; a mailbox
    that cannot be read is recorded for the check and re-raised to the tick."""
    from ..agency.leads import authenticated, their_words
    from ..mail import _api, _body_text, _header
    from ..mail.accounts import automated
    acct = account()
    if acct is None:
        _note(False, inbox_state() or "no support inbox")
        return {"configured": False}
    address = support.support_address()
    try:
        listing = await _api("/messages", account=acct,
                             q=f"to:{address} newer_than:{days}d -from:{address}",
                             maxResults=limit)
        new = skipped = stopped = 0
        for item in listing.get("messages", []) or []:
            gid = str(item.get("id") or "")
            source_id = f"gmail:{gid}"
            if not gid or await support.ticket_exists(source_id):
                continue
            full = await _api(f"/messages/{gid}", account=acct, format="full")
            sender = parseaddr(_header(full, "From"))[1].strip().lower()
            auto = _header(full, "Auto-Submitted").strip().lower()
            bulk = _header(full, "Precedence").strip().lower() in ("bulk", "list", "junk")
            if (not sender or "@" not in sender or automated(sender) or _ours(sender)
                    or (auto and auto != "no") or bulk
                    or _header(full, "List-Unsubscribe")):
                skipped += 1
                continue
            words = their_words(_body_text(full.get("payload", {})))
            subject = _header(full, "Subject")[:300]
            if is_stop(words) or is_stop(subject):
                await support.suppress(sender, "asked to stop (support mailbox)")
                stopped += 1
                continue
            if not words.strip():
                skipped += 1
                continue
            name = parseaddr(_header(full, "From"))[0].strip()
            ticket = await support.open_ticket(
                "email", source_id, name=name, email=sender, product="",
                subject=subject, message=words,
                authenticated=authenticated(_header(full, "Authentication-Results")))
            new += ticket is not None
    except Exception as exc:
        from .._http import explain
        _note(False, f"the support inbox could not be read: {explain(exc)}")
        raise
    _note(True, f"read at {datetime.now(UTC):%H:%M UTC}")
    return {"configured": True, "new": new, "skipped": skipped, "stopped": stopped}
