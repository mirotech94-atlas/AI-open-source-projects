"""Reply drafts for a mailbox ATLAS reads over IMAP (owner).

`mail.draft_reply` takes a thread id. A plain id is a Gmail-API thread (the
clips mailbox, OAuth token). `imap:<account>:<Message-ID>` is a message in an
IMAP-read account (`reply_today` hands those ids out); the draft for it is an
IMAP APPEND to that account's Drafts folder with the \\Draft flag. No OAuth.

It only ever creates a draft. There is no send path in this file, and the
original message is read with the INBOX opened read-only and BODY.PEEK. The
rollback deletes the draft it made, found by its own Message-ID, in Drafts
only. The password (the account's own, else GMAIL_APP_PASSWORD for
imap.gmail.com) goes to `login` and nowhere else: not into a result, a log
line or the evidence.
"""
from __future__ import annotations

import asyncio
import email
import imaplib
import logging
import os
import ssl
import time
from dataclasses import dataclass
from email import policy
from email.message import EmailMessage
from email.utils import make_msgid, parseaddr

from ...kernel.contracts import ActionResult

log = logging.getLogger("atlas.mail.imap_draft")

PREFIX = "imap:"
DRAFTS = '"[Gmail]/Drafts"'          # quoted: "]" is an IMAP atom-special
GMAIL_HOST = "imap.gmail.com"
_WIRE = policy.default.clone(linesep="\r\n")   # CRLF on the wire
_BAD = ('"', "\\", "\r", "\n", "\x00")


def parse_thread_id(thread_id: str) -> tuple[str, str] | None:
    """(account name, Message-ID) for `imap:<account>:<Message-ID>`, else None."""
    tid = str(thread_id or "")
    if not tid.startswith(PREFIX):
        return None
    name, _, msgid = tid[len(PREFIX):].partition(":")
    if not name or not msgid.strip():
        return None
    return name, msgid.strip()


def _safe_id(msgid: str) -> bool:
    """A Message-ID goes inside an IMAP quoted string: no quote, backslash or
    line break may get through, or it could add commands."""
    return bool(msgid) and not any(c in msgid for c in _BAD)


@dataclass(frozen=True, slots=True)
class _Login:
    host: str
    port: int
    user: str
    password: str
    email: str


def _login_for(name: str) -> tuple[_Login | None, str]:
    from .accounts import all_accounts
    account = next((a for a in all_accounts() if a.name == name), None)
    if account is None:
        return None, f"no mailbox named {name!r} in MAIL_ACCOUNTS"
    if account.kind != "imap" or not account.email or not account.host:
        return None, f"mailbox {name!r} is not an IMAP-read account"
    password = account.password
    if not password and account.host.lower() == GMAIL_HOST:
        password = os.getenv("GMAIL_APP_PASSWORD", "").strip()
    if not password:
        return None, (f"no password for {name!r}: set MAIL_{name.upper()}_PASSWORD "
                      f"or GMAIL_APP_PASSWORD")
    return _Login(account.host, account.port, account.email, password,
                  account.email), ""


def _connect(login: _Login) -> imaplib.IMAP4_SSL:
    conn = imaplib.IMAP4_SSL(login.host, login.port,
                             ssl_context=ssl.create_default_context(), timeout=25)
    conn.login(login.user, login.password)
    return conn


def _logout(conn) -> None:
    try:
        conn.logout()
    except Exception as exc:                            # noqa: BLE001
        log.debug("imap logout failed: %s", type(exc).__name__)


def _find(conn, msgid: str) -> bytes | None:
    typ, data = conn.uid("SEARCH", None, "HEADER", "Message-ID", f'"{msgid}"')
    if typ != "OK":
        raise RuntimeError(f"IMAP search failed: {typ}")
    ids = (data[0] or b"").split()
    return ids[-1] if ids else None


def _fetched(payload) -> bytes:
    for part in payload or []:
        if isinstance(part, tuple):
            return part[1]
    return b""


def _sync_draft(login: _Login, orig_id: str, body: str) -> tuple[str, str, str]:
    """(draft Message-ID, To, Subject). Raises LookupError when the original
    message is not in INBOX."""
    conn = _connect(login)
    try:
        conn.select("INBOX", readonly=True)
        uid = _find(conn, orig_id)
        if uid is None:
            raise LookupError("the message being answered is not in INBOX")
        typ, payload = conn.uid(
            "FETCH", uid, "(BODY.PEEK[HEADER.FIELDS (FROM REPLY-TO SUBJECT "
                          "MESSAGE-ID REFERENCES)])")
        if typ != "OK":
            raise RuntimeError(f"IMAP fetch failed: {typ}")
        hdr = email.message_from_bytes(_fetched(payload))
        to = str(hdr.get("Reply-To") or hdr.get("From") or "").strip()
        if not parseaddr(to)[1]:
            raise ValueError("the message has no sender to reply to")
        subject = " ".join(str(hdr.get("Subject") or "").split())
        if not subject.lower().startswith("re:"):
            subject = f"Re: {subject}".strip()
        refs = str(hdr.get("References") or "").split()
        if orig_id not in refs:
            refs.append(orig_id)

        domain = login.email.rpartition("@")[2] or None
        draft_id = make_msgid(domain=domain)
        msg = EmailMessage()
        msg["From"] = login.email
        msg["To"] = to
        msg["Subject"] = subject
        msg["Message-ID"] = draft_id
        msg["In-Reply-To"] = orig_id
        msg["References"] = " ".join(refs)
        msg.set_content(body)
        raw = msg.as_bytes(policy=_WIRE)

        stamp = imaplib.Time2Internaldate(time.time())
        typ, _ = conn.append(DRAFTS, "(\\Draft)", stamp, raw)
        if typ != "OK":
            raise RuntimeError(f"IMAP append failed: {typ}")
        return draft_id, to, subject
    finally:
        _logout(conn)


def _sync_delete(login: _Login, draft_id: str) -> bool:
    conn = _connect(login)
    try:
        conn.select(DRAFTS, readonly=False)
        uid = _find(conn, draft_id)
        if uid is None:
            return False
        conn.uid("STORE", uid, "+FLAGS", "(\\Deleted)")
        conn.expunge()
        return True
    finally:
        _logout(conn)


def _sync_read(login: _Login, draft_id: str) -> dict | None:
    conn = _connect(login)
    try:
        conn.select(DRAFTS, readonly=True)
        uid = _find(conn, draft_id)
        if uid is None:
            return None
        typ, payload = conn.uid("FETCH", uid, "(BODY.PEEK[])")
        if typ != "OK":
            raise RuntimeError(f"IMAP fetch failed: {typ}")
        msg = email.message_from_bytes(_fetched(payload), policy=policy.default)
        part = msg.get_body(preferencelist=("plain",))
        return {"to": str(msg.get("To") or ""),
                "subject": str(msg.get("Subject") or ""),
                "in_reply_to": str(msg.get("In-Reply-To") or "").strip(),
                "body": part.get_content() if part is not None else ""}
    finally:
        _logout(conn)


def _fail(exc: BaseException) -> str:
    from .._http import redact
    return redact(f"{type(exc).__name__}: {exc}")[:300]


def check_params(thread_id: str) -> tuple[_Login | None, str, str, str]:
    """(login, account, Message-ID, refusal) for an imap thread id; no network."""
    parsed = parse_thread_id(thread_id)
    if parsed is None:
        return None, "", "", "not an imap thread id"
    name, orig = parsed
    if not _safe_id(orig):
        return None, name, orig, "the Message-ID has characters IMAP cannot take"
    login, why = _login_for(name)
    return login, name, orig, why


async def draft(thread_id: str, body: str) -> ActionResult:
    from . import _address, _digest
    login, name, orig, why = check_params(thread_id)
    if login is None:
        return ActionResult(False, why)
    try:
        draft_id, to, subject = await asyncio.to_thread(_sync_draft, login, orig, body)
    except LookupError as exc:
        return ActionResult(False, str(exc))
    except Exception as exc:                            # noqa: BLE001
        return ActionResult(False, f"draft not saved: {_fail(exc)}")
    token = f"{PREFIX}{name}:{draft_id}"
    return ActionResult(
        True, f"Draft saved in {login.email} Drafts for {to} -- open it to "
              f"review and send.",
        evidence={"draft_id": token, "thread_id": thread_id,
                  "to_sha256": _digest(_address(to)),
                  "subject_sha256": _digest(subject),
                  "body_sha256": _digest(body)},
        rollback_token=token)


async def delete(token: str) -> ActionResult:
    """Rollback: remove the draft this action made, by its Message-ID."""
    login, _name, draft_id, why = check_params(token)
    if login is None:
        return ActionResult(False, f"cannot undo: {why}")
    try:
        gone = await asyncio.to_thread(_sync_delete, login, draft_id)
    except Exception as exc:                            # noqa: BLE001
        return ActionResult(False, f"draft not deleted: {_fail(exc)}")
    if not gone:
        return ActionResult(False, "the draft is not in Drafts (already gone?)")
    return ActionResult(True, f"deleted draft {draft_id}")


async def read_draft(token: str) -> dict | None:
    """The saved draft's To, Subject, In-Reply-To and text, or None if it is
    not in Drafts. Raises on a connection failure."""
    login, _name, draft_id, why = check_params(token)
    if login is None:
        raise ValueError(why)
    return await asyncio.to_thread(_sync_read, login, draft_id)
