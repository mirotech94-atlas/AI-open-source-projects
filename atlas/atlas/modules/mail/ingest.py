"""Reading NEW mail, so ATLAS can tell him about something he has not seen.

`ingest_message()` existed and nothing called it. The webhook it was written
for was never wired, so every message that arrived was counted by
`check_unreplied` and read by nobody -- a Guardian customer writing in, a
Fiverr order, a Stripe dispute notice all sat there as "1 unread". Counting
is not reading.

This polls instead. It is honest polling: every account, every few minutes,
only messages newer than the last one seen, only ones Miro has not opened.
That last part is the point. He asked to be told about things he has NOT
seen, not about everything -- so anything he has already read is skipped
and the watermark is per account, so a busy inbox does not starve a quiet
one.

Each new message is classified once by the local model and remembered.
Anything that classifies as needing him -- a client, a bill, a reply owed --
becomes a Signal into the channel for that mailbox. It goes out once: the
watermark advances whether or not the signal delivered, because
re-announcing the same email every cycle is how he stops reading Slack.

Mail the local model could not classify (it was down, or answered with
something that was not JSON) is never filed as fyi. Each sweep raises one
alert per account naming it, and the watermark is moved back so that mail
is read again on the next sweep -- a guess is not a reading, and mail
nobody read must not go quiet.

Urgent is a separate word. A message the classifier marks urgency 3 raises
at HIGH, which is the severity that can ring the phone.
"""
from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

from ...kernel.contracts import Severity, Signal
from .accounts import Account, all_accounts

log = logging.getLogger("atlas.mail.ingest")

# Per-account "newest message id we have looked at". Process-local: a
# restart re-reads the last window, which is harmless because remember()
# and the signal dedup both key on content. Missing a message is not.
_seen: dict[str, str] = {}
_started = datetime.now(UTC)

# What the classifier can say that is worth interrupting him for.
TELL_HIM = {"needs_reply", "client", "bill"}
# What the sender can be that is worth interrupting him for regardless.
WATCHED_SENDERS = ("fiverr.com", "stripe.com", "guarder.example.com", "flycheap.example.com",
                   "gitlab", "cloudhost.example", "linkedin.com")


def _topic_for(account: Account) -> str:
    """Which Slack channel this mailbox's findings belong in."""
    low = account.email.lower()
    if "guarder" in low:
        return "guardian"
    if "flycheap" in low:
        return "flycheap"
    return "mail"


async def _new_google(account: Account) -> list[dict]:
    from . import _api, _body_text, _header, spam

    since = int((datetime.now(UTC) - timedelta(days=2)).timestamp())
    data = await _api("/messages", account=account,
                      q=f"is:unread after:{since} -category:promotions "
                        f"-category:social", maxResults=25)
    ids = [m["id"] for m in data.get("messages", [])]
    if not ids:
        return []
    out = []
    for mid in ids:
        if mid == _seen.get(account.name):
            break
        full = await _api(f"/messages/{mid}", account=account, format="full")
        # UNREAD is the label Gmail removes when he opens it. Filtering on it
        # in the query is not enough: the list is what was unread when the
        # query ran, and he may have read it since.
        if "UNREAD" not in (full.get("labelIds") or []):
            continue
        out.append({
            "id": mid,
            "subject": _header(full, "Subject"),
            "sender": _header(full, "From"),
            "body": _body_text(full.get("payload", {}))[:6000],
            "at": datetime.fromtimestamp(
                int(full.get("internalDate", 0)) / 1000, UTC),
            # the whole message, for the spam verdict: headers, links, stamp
            "spam_item": spam.item_from_gmail(full, full=True),
        })
    if ids:
        _seen[account.name] = ids[0]
    return out


async def _new_imap(account: Account) -> list[dict]:
    import asyncio
    import email
    import imaplib
    import ssl

    from . import spam
    from .imap import _decode

    def sync() -> list[dict]:
        conn = imaplib.IMAP4_SSL(account.host, account.port,
                                 ssl_context=ssl.create_default_context(),
                                 timeout=25)
        try:
            conn.login(account.email, account.password)
            conn.select("INBOX", readonly=True)
            validity = spam.uidvalidity(conn)
            since = (datetime.now(UTC) - timedelta(days=2)).strftime("%d-%b-%Y")
            typ, data = conn.search(None, f"(UNSEEN SINCE {since})")
            ids = (data[0] or b"").split()
            newest_seen = _seen.get(account.name, "")
            out: list[dict] = []
            for mid in reversed(ids[-25:]):
                key = mid.decode()
                if key == newest_seen:
                    break
                typ, payload = conn.fetch(mid, "(UID BODY.PEEK[])")
                if typ != "OK" or not payload or not isinstance(payload[0], tuple):
                    continue
                msg = email.message_from_bytes(payload[0][1])
                body = ""
                for part in msg.walk():
                    if part.get_content_type() == "text/plain":
                        body = part.get_payload(decode=True).decode(
                            "utf-8", "replace")
                        break
                try:
                    at = email.utils.parsedate_to_datetime(msg.get("Date", ""))
                    if at.tzinfo is None:
                        at = at.replace(tzinfo=UTC)
                except (TypeError, ValueError):
                    at = datetime.now(UTC)
                # the server's own ids, never the sender-written Message-ID
                uid = spam.uid_of(payload[0][0])
                out.append({"id": key, "subject": _decode(msg.get("Subject")),
                            "sender": _decode(msg.get("From")),
                            "body": body[:6000], "at": at,
                            "spam_item": spam.item_from_message(
                                msg, key=spam.imap_key(account.name, validity, uid,
                                                       msg.get("Message-ID")),
                                trust_auth=spam.authserv_for_host(account.host))})
            if ids:
                _seen[account.name] = ids[-1].decode()
            return out
        finally:
            try:
                conn.logout()
            except Exception as exc:  # noqa: BLE001
                log.debug("imap logout failed for %s: %s", account.name, exc)

    return await asyncio.to_thread(sync)


async def sweep() -> list[Signal]:
    """Every account, every new unread message, one Signal per one he needs."""
    from ...kernel.memory import Kind, remember
    from ...kernel.permissions import scan_for_injection
    from .._http import explain
    from . import UNCLASSIFIED_HEADING, classify, spam

    signals: list[Signal] = []
    for account in all_accounts():
        if not account.configured:
            continue
        before = _seen.get(account.name)
        unread: list[tuple[int, dict, str]] = []     # (position, mail, reason)
        unsure: list[tuple[dict, spam.SpamResult]] = []
        unchecked = 0
        try:
            fresh = (await _new_imap(account) if account.kind == "imap"
                     else await _new_google(account))
        except Exception as exc:  # noqa: BLE001
            log.warning("mail sweep failed for %s: %s", account.name, exc)
            continue

        for position, m in enumerate(fresh):
            if account.is_me(m["sender"]):
                continue
            # Instruction-shaped text is logged, never obeyed.
            if scan_for_injection(f"{m['subject']}\n{m['body']}",
                                  f"mail:{m['sender']}"):
                log.warning("instruction-shaped email from %s", m["sender"])
            # Every ingested message gets a spam verdict first. Spam is never
            # read by a model for triage and never raised; unsure is held back
            # and named once below; a check that could not run is UNKNOWN and
            # the mail is read as before. Nothing in the mailbox is touched.
            flag = await spam.check(m.get("spam_item") or spam.item_from_row(
                m, key=spam.mid_key(account.name, str(m["id"]))), account)
            if flag.verdict == spam.SPAM:
                log.info("spam held back (%s): %s", account.name, flag.reason)
                continue
            if flag.verdict == spam.UNSURE:
                unsure.append((m, flag))
                continue
            if flag.verdict == spam.UNKNOWN:
                unchecked += 1
            try:
                verdict = await classify(m["subject"], m["sender"], m["body"])
            except Exception as exc:  # noqa: BLE001
                verdict = {"unclassified": explain(exc)}
            if "unclassified" in verdict or not verdict.get("category"):
                reason = str(verdict.get("unclassified") or "no category")
                unread.append((position, m, reason))
                continue
            try:
                await remember(
                    Kind.EPISODIC, "threads",
                    f"[{account.email}] {m['sender']} — {m['subject']}: "
                    f"{verdict.get('summary', '')}",
                    meta={"category": verdict.get("category"),
                          "account": account.email}, importance=0.4)
            except Exception as exc:  # noqa: BLE001
                log.warning("could not remember mail from %s: %s",
                            m["sender"], exc)

            sender_low = m["sender"].lower()
            watched = any(w in sender_low for w in WATCHED_SENDERS)
            category = str(verdict["category"])
            urgency = int(verdict.get("urgency", 1) or 1)
            if category not in TELL_HIM and not watched and urgency < 2:
                continue

            severity = (Severity.HIGH if urgency >= 3
                        else Severity.NORMAL if urgency == 2 or watched
                        else Severity.INFO)
            signals.append(Signal(
                title=f"{account.email}: {m['subject'][:80] or '(no subject)'}",
                body=(f"From {m['sender']}\n{verdict.get('summary', '')}"
                      f"\n\nUnread in {account.email}. "
                      + ("Reads as urgent." if urgency >= 3 else "")),
                severity=severity,
                params={"topic": _topic_for(account)},
            ))

        held = spam.flagged_signal(account, unsure, unchecked, _topic_for(account))
        if held is not None:
            signals.append(held)
        if unread:
            signals.append(_unclassified_alert(account, unread, UNCLASSIFIED_HEADING))
            _rewind(account, fresh, before, max(pos for pos, _, _ in unread))
    return signals


def _unclassified_alert(account: Account, unread: list[tuple[int, dict, str]],
                        heading: str) -> Signal:
    """One alert for every message in this account the model could not read."""
    lines = [f"- {m['sender'][:60]}: {m['subject'][:80] or '(no subject)'}"
             for _, m, _ in unread]
    reasons = sorted({reason for _, _, reason in unread})
    return Signal(
        title=f"{account.email}: {len(unread)} {heading}",
        body=("\n".join(lines)
              + f"\n\nNot read by any model: {'; '.join(reasons)}. "
                "They will be read again on the next sweep."),
        severity=Severity.NORMAL,
        params={"topic": _topic_for(account)},
    )


def _rewind(account: Account, fresh: list[dict], before: str | None,
            oldest_unread: int) -> None:
    """Move the watermark back so the oldest unclassified message is fetched
    again next sweep. Both fetchers return newest first and stop at the
    watermark, so it becomes the next older message this sweep fetched, or
    what it was before this sweep if there is none."""
    if oldest_unread + 1 < len(fresh):
        _seen[account.name] = fresh[oldest_unread + 1]["id"]
    elif before is None:
        _seen.pop(account.name, None)
    else:
        _seen[account.name] = before
