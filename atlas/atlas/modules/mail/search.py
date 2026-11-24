"""Finding older mail by what it is about -- read-only.

The ingest sweep and the unreplied check read what is NEW. The travel
organiser and the money-leak finder need what ARRIVED over months: booking
confirmations, receipts, renewal notices. This is that one reader, for both
kinds of mailbox:

* Google: `messages.list` with a Gmail query, then `messages.get`. GET only.
* IMAP: `select(readonly=True)`, `UID SEARCH`, `BODY.PEEK[]` -- the server
  refuses a flag change on this session, so nothing is ever marked read.

Each message also carries `auth`: the Authentication-Results header that
Gmail itself added (authserv-id mx.google.com, the topmost one -- a sender
can write its own below it), "" when Gmail added none, and None over IMAP,
where no receiving server is known to be trusted.

Each message comes back as a dict with a STABLE id (Gmail's message id, or
the Message-ID header over IMAP), so a caller can remember what it has
already read and never pay a model to read it twice. The text is plain:
HTML-only mail (most booking confirmations) has its tags stripped here.
"""
from __future__ import annotations

import asyncio
import email
import email.utils
import html
import imaplib
import logging
import re
import ssl
from datetime import UTC, datetime, timedelta

log = logging.getLogger("atlas.mail.search")

BODY_LIMIT = 8000

_TAGS = re.compile(r"<(script|style)\b.*?</\1>|<[^>]+>", re.IGNORECASE | re.DOTALL)
_BLANKS = re.compile(r"[^\S\n]+")
_LINES = re.compile(r"\n\s*\n+")


def plain(text: str) -> str:
    """Text as a person would read it: tags gone, entities decoded, runs of
    whitespace collapsed. Plain text passes through nearly unchanged."""
    text = text or ""
    if re.search(r"<(html|body|div|table|p|br|td)\b", text, re.IGNORECASE):
        text = re.sub(r"<br\s*/?>|</(p|div|tr|li|h\d)>", "\n", text,
                      flags=re.IGNORECASE)
        text = _TAGS.sub(" ", text)
    text = html.unescape(text)
    text = _BLANKS.sub(" ", text)
    return _LINES.sub("\n\n", text).strip()


def _html_part(payload: dict) -> str:
    import base64
    if payload.get("mimeType") == "text/html" and payload.get("body", {}).get("data"):
        return base64.urlsafe_b64decode(
            payload["body"]["data"] + "==").decode("utf-8", "replace")
    for part in payload.get("parts", []):
        found = _html_part(part)
        if found:
            return found
    return ""


GMAIL_AUTHSERV = "mx.google.com"


def gmail_auth(full: dict) -> str:
    """The Authentication-Results Gmail itself stamped on this message: the
    first (topmost) one whose authserv-id is mx.google.com, else "". One the
    sender wrote into the message sits below Gmail's and is never read."""
    for h in full.get("payload", {}).get("headers", []):
        if h.get("name", "").lower() != "authentication-results":
            continue
        value = " ".join(str(h.get("value") or "").split())
        if value.split(";", 1)[0].strip().lower() == GMAIL_AUTHSERV:
            return value
    return ""


async def _google(account, query: str, days: int, limit: int) -> list[dict]:
    from . import _api, _body_text, _header

    data = await _api("/messages", account=account,
                      q=f"{query} newer_than:{days}d", maxResults=limit)
    ids = [m["id"] for m in data.get("messages", [])][:limit]
    gate = asyncio.Semaphore(4)

    async def one(mid: str) -> dict:
        async with gate:
            return await _api(f"/messages/{mid}", account=account, format="full")

    out = []
    for full in await asyncio.gather(*(one(m) for m in ids)):
        payload = full.get("payload", {})
        body = _body_text(payload) or _html_part(payload)
        out.append({
            "id": f"gmail:{full.get('id', '')}",
            "account": account.email or account.name,
            "subject": _header(full, "Subject"),
            "sender": _header(full, "From"),
            "at": datetime.fromtimestamp(int(full.get("internalDate", 0)) / 1000, UTC),
            "body": plain(body)[:BODY_LIMIT],
            "auth": gmail_auth(full),
        })
    return out


def _imap_criteria(words: tuple[str, ...], since: str) -> str:
    """`(SINCE d OR SUBJECT "a" OR SUBJECT "b" SUBJECT "c")` -- IMAP's OR
    takes exactly two keys, so n words nest n-1 ORs."""
    keys = [f'SUBJECT "{w}"' for w in words if w and '"' not in w]
    if not keys:
        return f"(SINCE {since})"
    expr = keys[-1]
    for key in reversed(keys[:-1]):
        expr = f"OR {key} {expr}"
    return f"(SINCE {since} {expr})"


def _message_text(msg) -> str:
    text, fallback = "", ""
    for part in msg.walk():
        ctype = part.get_content_type()
        if ctype not in ("text/plain", "text/html"):
            continue
        raw = part.get_payload(decode=True) or b""
        charset = part.get_content_charset() or "utf-8"
        try:
            decoded = raw.decode(charset, "replace")
        except LookupError:
            decoded = raw.decode("utf-8", "replace")
        if ctype == "text/plain" and not text:
            text = decoded
        elif ctype == "text/html" and not fallback:
            fallback = decoded
    return text or fallback


def _sync_imap(account, words: tuple[str, ...], days: int, limit: int) -> list[dict]:
    from .imap import _decode

    conn = imaplib.IMAP4_SSL(account.host, account.port,
                             ssl_context=ssl.create_default_context(), timeout=25)
    try:
        conn.login(account.email, account.password)
        conn.select("INBOX", readonly=True)
        from . import spam
        validity = spam.uidvalidity(conn)
        since = (datetime.now(UTC) - timedelta(days=days)).strftime("%d-%b-%Y")
        typ, data = conn.uid("SEARCH", None, _imap_criteria(words, since))
        if typ != "OK":
            raise RuntimeError(f"IMAP search failed: {typ}")
        uids = (data[0] or b"").split()
        out: list[dict] = []
        for uid in reversed(uids[-limit:]):
            typ, payload = conn.uid("FETCH", uid, "(BODY.PEEK[])")
            if typ != "OK" or not payload or not isinstance(payload[0], tuple):
                continue
            msg = email.message_from_bytes(payload[0][1])
            try:
                at = email.utils.parsedate_to_datetime(msg.get("Date", ""))
                if at.tzinfo is None:
                    at = at.replace(tzinfo=UTC)
            except (TypeError, ValueError):
                at = None
            stable = (msg.get("Message-ID") or "").strip() or f"uid:{uid.decode()}"
            out.append({
                "id": f"imap:{account.name}:{stable}",
                "key": spam.imap_key(account.name, validity, uid.decode(),
                                     msg.get("Message-ID")),
                "account": account.email or account.name,
                "subject": _decode(msg.get("Subject")),
                "sender": _decode(msg.get("From")),
                "at": at,
                "body": plain(_message_text(msg))[:BODY_LIMIT],
                "auth": None,           # no receiving server we can trust
            })
        return out
    finally:
        try:
            conn.logout()
        except Exception as exc:  # noqa: BLE001
            log.debug("imap logout failed for %s: %s", account.name, exc)


async def find(account, *, gmail_query: str, imap_subjects: tuple[str, ...],
               days: int, limit: int) -> list[dict]:
    """Messages in one mailbox matching the query, newest first. Raises on
    a mailbox that cannot be read -- the caller names it."""
    if account.kind == "imap":
        return await asyncio.to_thread(_sync_imap, account, imap_subjects, days,
                                       limit)
    return await _google(account, gmail_query, days, limit)
