"""Reading a non-Google mailbox over IMAP.

Zoho carries FlyCheap and Guardian. There is no Gmail API here, so the same
question -- "which threads are waiting on a reply" -- is answered from IMAP
headers instead.

Read-only, deliberately and structurally: the mailbox is opened with
`select(readonly=True)`, so the server itself refuses a write and nothing in
this file could mark a message read even by accident. A monitoring tool that
silently marks mail as read is worse than no monitoring tool, because the
damage is to the thing it was watching.

Blocking sockets, run in a thread. imaplib has no async form, and holding
the event loop for the length of a mailbox fetch would stall every other
check on the schedule.
"""
from __future__ import annotations

import asyncio
import email.utils
import imaplib
import logging
import ssl
from datetime import UTC, datetime, timedelta
from email.header import decode_header, make_header


def _decode(raw: str | None) -> str:
    if not raw:
        return ""
    try:
        return str(make_header(decode_header(raw)))
    except Exception:                                   # noqa: BLE001
        return raw


def _sync_unreplied(host: str, port: int, user: str, password: str,
                    days: int, hours: int,
                    mine: tuple[str, ...] = ()) -> tuple[int, list[str], int]:
    """(stale, subjects, looked_at). Raises on connection or login failure."""
    context = ssl.create_default_context()
    conn = imaplib.IMAP4_SSL(host, port, ssl_context=context, timeout=25)
    try:
        conn.login(user, password)
        # readonly: the server will not let this session change a flag.
        conn.select("INBOX", readonly=True)
        since = (datetime.now(UTC) - timedelta(days=days)).strftime("%d-%b-%Y")
        typ, data = conn.search(None, f'(UNSEEN SINCE {since})')
        if typ != "OK":
            raise RuntimeError(f"IMAP search failed: {typ}")
        ids = (data[0] or b"").split()

        cutoff = datetime.now(UTC) - timedelta(hours=hours)
        stale, subjects = 0, []
        # Newest first, and capped: an inbox with thousands of unread is a
        # different problem, and fetching all of it would time out the check
        # rather than report that.
        for mid in reversed(ids[-60:]):
            typ, payload = conn.fetch(
                mid, "(BODY.PEEK[HEADER.FIELDS (FROM SUBJECT DATE)])")
            if typ != "OK" or not payload or not isinstance(payload[0], tuple):
                continue
            headers = payload[0][1].decode("utf-8", "replace")
            fields: dict[str, str] = {}
            for line in headers.splitlines():
                if ":" in line:
                    key, _, value = line.partition(":")
                    fields[key.strip().lower()] = value.strip()

            frm = _decode(fields.get("from")).lower()
            # Any address that delivers here means he wrote it; nothing is
            # owed. Matching only the login address counts his own replies
            # sent from an alias as mail still waiting on him.
            from .accounts import automated
            if automated(frm):
                continue                 # a machine wrote it; nobody is waiting
            if any(a.lower() in frm for a in (mine or (user,))):
                continue
            try:
                when = email.utils.parsedate_to_datetime(fields.get("date", ""))
            except (TypeError, ValueError):
                continue
            if when is None:
                continue
            if when.tzinfo is None:
                when = when.replace(tzinfo=UTC)
            if when < cutoff:
                stale += 1
                subjects.append(_decode(fields.get("subject"))[:80])
        return stale, subjects, len(ids)
    finally:
        try:
            conn.logout()
        except Exception:                               # noqa: BLE001
            pass


async def unreplied(account, *, days: int = 7,
                    hours: int = 48) -> tuple[int, list[str], int]:
    return await asyncio.to_thread(
        _sync_unreplied, account.host, account.port, account.email,
        account.password, days, hours, account.addresses)


def _sync_unread_headers(host: str, port: int, user: str, password: str,
                         days: int, limit: int) -> list[dict]:
    """Unread messages from the last `days`, newest first: the headers the
    reply-today card sorts on, and the first part of the text. Read-only:
    `select(readonly=True)` and `BODY.PEEK`, so nothing is marked read."""
    context = ssl.create_default_context()
    conn = imaplib.IMAP4_SSL(host, port, ssl_context=context, timeout=25)
    try:
        conn.login(user, password)
        conn.select("INBOX", readonly=True)
        since = (datetime.now(UTC) - timedelta(days=days)).strftime("%d-%b-%Y")
        # UIDs, not sequence numbers: a sequence number shifts when mail is
        # removed from the mailbox, and the id keys a cached classification.
        typ, data = conn.uid("SEARCH", None, f"(UNSEEN SINCE {since})")
        if typ != "OK":
            raise RuntimeError(f"IMAP search failed: {typ}")
        ids = (data[0] or b"").split()
        out: list[dict] = []
        for mid in reversed(ids[-limit:]):
            typ, payload = conn.uid(
                "FETCH", mid,
                "(BODY.PEEK[HEADER.FIELDS (FROM SUBJECT DATE LIST-UNSUBSCRIBE "
                "PRECEDENCE MESSAGE-ID)] BODY.PEEK[TEXT]<0.1500>)")
            if typ != "OK" or not payload:
                continue
            header_raw, text_raw = b"", b""
            for part in payload:
                if not isinstance(part, tuple):
                    continue
                if b"HEADER" in part[0].upper():
                    header_raw = part[1]
                else:
                    text_raw = part[1]
            fields: dict[str, str] = {}
            for line in header_raw.decode("utf-8", "replace").splitlines():
                if ":" in line and not line[:1].isspace():
                    key, _, value = line.partition(":")
                    fields[key.strip().lower()] = value.strip()
            try:
                when = email.utils.parsedate_to_datetime(fields.get("date", ""))
            except (TypeError, ValueError):
                when = None
            if when is not None and when.tzinfo is None:
                when = when.replace(tzinfo=UTC)
            out.append({
                "id": fields.get("message-id") or f"uid:{mid.decode()}",
                "sender": _decode(fields.get("from")),
                "subject": _decode(fields.get("subject")),
                "at": when,
                "list_unsubscribe": bool(fields.get("list-unsubscribe")),
                "bulk": fields.get("precedence", "").lower() in ("bulk", "list"),
                "snippet": text_raw.decode("utf-8", "replace")[:1500],
            })
        return out
    finally:
        try:
            conn.logout()
        except Exception as exc:                        # noqa: BLE001
            logging.getLogger("atlas.mail.imap").debug("imap logout failed: %s", exc)


async def unread_headers(account, *, days: int = 7, limit: int = 40) -> list[dict]:
    return await asyncio.to_thread(
        _sync_unread_headers, account.host, account.port, account.email,
        account.password, days, limit)
