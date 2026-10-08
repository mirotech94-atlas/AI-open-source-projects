"""The "Reply today" card: which emails need Miro's answer today.

One card a day, after BRIEF_HOUR, raised as a Signal by `MailModule.signals()`
(no action: it tells him, it does nothing). Read-only against every mailbox.

What decides a line, in order:

1. **He replied last** (any of the account's addresses): nothing is owed.
2. **A machine sent it.** Sorted by its headers alone, never by a model:
   a receipt (a receipt/invoice/order word in the subject from an automated
   or bulk sender), a newsletter (`List-Unsubscribe`, `Precedence: bulk`,
   or a newsletter/digest sender), or a notification (any other automated
   sender). These are COUNTED and never listed.
3. **A person sent it.** The local model's reading, through the same
   cached, capped `_classify_capped()` and `bucket()` the inbox uses, so one
   email gets one answer everywhere. `reply_today`, or a `reply_this_week`
   that has already waited 48h (the unreplied check's own threshold), is
   listed. The rest is counted as "can wait" or "no reply needed".

Mail the model did not read -- it was down, or this read's classification
budget ran out -- is its own count, said as "not known", never folded into
zero. A mailbox that could not be read is named, and the card carries the
time it was read (invariants 6 and 7).
"""
from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from ...kernel.contracts import Severity, Signal

log = logging.getLogger("atlas.mail.reply_today")

OVERDUE_H = 48.0
# Receipt-shaped subjects. Only a machine's mail is sorted on these: a
# person's "Invoice for September" may well need an answer.
RECEIPT_WORDS = ("receipt", "invoice", "your order", "order confirm",
                 "payment received", "payment confirm", "billing statement",
                 "renewal", "renewed", "subscription", "charged", "refund")
NEWSLETTER_WORDS = ("newsletter", "digest", "weekly", "news@", "updates@")

COUNTED = ("receipts", "newsletters", "notifications", "can_wait", "no_reply",
           "not_read")

# The last card, for `brief_line()`: brief() is synchronous and must not
# query anything itself, so it says what the last read found, and when.
_last: dict[str, Any] = {}


@dataclass
class ReplyToday:
    at: datetime
    reply: list[dict] = field(default_factory=list)
    counts: dict[str, int] = field(default_factory=lambda: dict.fromkeys(COUNTED, 0))
    unreadable: list[str] = field(default_factory=list)   # "who: why"
    looked: int = 0


def machine_kind(sender: str, subject: str, *, list_unsubscribe: bool = False,
                 bulk: bool = False) -> str | None:
    """receipts | newsletters | notifications, or None for a person.
    Headers only; deterministic."""
    from .accounts import automated

    low_sender = (sender or "").lower()
    low_subject = (subject or "").lower()
    machine = automated(sender) or list_unsubscribe or bulk
    if not machine:
        return None
    if any(w in low_subject for w in RECEIPT_WORDS):
        return "receipts"
    if list_unsubscribe or bulk or any(w in low_sender for w in NEWSLETTER_WORDS):
        return "newsletters"
    return "notifications"


def sort_person(classification: dict, age_h: float) -> str:
    """reply | can_wait | no_reply | not_read, from the classifier's reading."""
    from .followups import bucket

    if not classification or "unclassified" in classification \
            or not classification.get("category"):
        return "not_read"
    where = bucket(classification, age_h)
    if where == "reply_today":
        return "reply"
    if where == "reply_this_week":
        return "reply" if age_h >= OVERDUE_H else "can_wait"
    return "no_reply"


async def _google_items(account) -> list[dict]:
    from . import _api, _header

    data = await _api("/threads", account=account,
                      q="is:unread -category:promotions -category:social "
                        "newer_than:7d", maxResults=50)
    threads = data.get("threads", [])
    gate = asyncio.Semaphore(6)

    async def one(tid: str) -> dict:
        async with gate:
            return await _api(f"/threads/{tid}", account=account, format="metadata")

    fulls = await asyncio.gather(*(one(t["id"]) for t in threads[:40]))
    snippets = {t["id"]: t.get("snippet", "") for t in threads}
    out = []
    for thread, full in zip(threads, fulls, strict=False):
        msgs = full.get("messages", [])
        if not msgs:
            continue
        last = msgs[-1]
        out.append({
            "id": thread["id"],
            "sender": _header(last, "From"),
            "subject": _header(last, "Subject"),
            "at": datetime.fromtimestamp(int(last.get("internalDate", 0)) / 1000, UTC),
            "list_unsubscribe": bool(_header(last, "List-Unsubscribe")),
            "bulk": _header(last, "Precedence").lower() in ("bulk", "list"),
            "snippet": snippets.get(thread["id"], ""),
        })
    return out


async def _items(account) -> list[dict]:
    if account.kind == "imap":
        from .imap import unread_headers
        return await unread_headers(account)
    return await _google_items(account)


async def read(accounts=None, now: datetime | None = None) -> ReplyToday:
    """Every configured mailbox, read now. Never raises for one mailbox:
    it is named in `unreadable` instead."""
    from .._http import redact
    from . import _CLASSIFY_CAP_PER_CALL, _classify_capped
    from .accounts import all_accounts

    now = now or datetime.now(UTC)
    result = ReplyToday(at=now)
    accounts = all_accounts() if accounts is None else accounts
    if not accounts:
        result.unreadable.append("no mailboxes configured (MAIL_ACCOUNTS)")
        return result
    budget = [_CLASSIFY_CAP_PER_CALL]
    for account in accounts:
        who = account.email or account.name
        if not account.configured:
            result.unreadable.append(f"{who}: not configured ({account.missing})")
            continue
        try:
            items = await _items(account)
        except Exception as exc:                        # noqa: BLE001
            result.unreadable.append(f"{who}: {redact(exc)}"[:160])
            continue
        for item in items:
            sender, subject = item["sender"], item["subject"] or "(no subject)"
            if account.is_me(sender):
                continue                     # he replied last; nothing owed
            result.looked += 1
            kind = machine_kind(sender, subject,
                                list_unsubscribe=item.get("list_unsubscribe", False),
                                bulk=item.get("bulk", False))
            if kind is not None:
                result.counts[kind] += 1
                continue
            at = item.get("at") or now
            age_h = max(0.0, (now - at).total_seconds() / 3600)
            # The inbox keys the shared cache on the bare Gmail thread id;
            # the same key here means one email gets one reading.
            key = (item["id"] if account.kind != "imap"
                   else f"imap:{account.name}:{item['id']}")
            try:
                verdict = await _classify_capped(key, subject, sender,
                                                 item.get("snippet", ""), now, budget)
            except Exception as exc:                    # noqa: BLE001
                verdict = {"unclassified": redact(exc)}
            where = sort_person(verdict, age_h)
            if where != "reply":
                result.counts[where] += 1
                continue
            result.reply.append({
                "account": who,
                "sender": sender.split("<")[0].strip().strip('"')[:40] or sender[:40],
                "subject": subject[:80],
                "age_h": age_h,
                "urgency": int(verdict.get("urgency") or 0),
            })
    result.reply.sort(key=lambda r: (-r["urgency"], -r["age_h"]))
    return result


def _age(hours: float) -> str:
    return f"{hours:.0f}h" if hours < 48 else f"{hours / 24:.0f}d"


def render(card: ReplyToday) -> tuple[str, str]:
    """(first line, body). The count leads; every number carries its time."""
    stamp = f"{card.at:%H:%M} UTC"
    n = len(card.reply)
    if card.unreadable and not card.looked:
        head = (f"Could not read the mail at {stamp}: how many need your reply "
                f"today is not known.")
    else:
        head = f"{n} email(s) need your reply today (read {stamp})."
    lines = [head]
    for r in card.reply[:15]:
        lines.append(f"- {r['sender']}: {r['subject']} ({_age(r['age_h'])} old, "
                     f"{r['account']})")
    if n > 15:
        lines.append(f"- and {n - 15} more")
    c = card.counts
    lines.append("")
    lines.append(f"Not listed: {c['receipts']} receipt(s), {c['newsletters']} "
                 f"newsletter(s), {c['notifications']} notification(s), "
                 f"{c['can_wait']} that can wait, {c['no_reply']} needing no reply.")
    if c["not_read"]:
        lines.append(f"{c['not_read']} not read by the local model: whether they "
                     f"need you is not known.")
    for u in card.unreadable:
        lines.append(f"Not read: {u}")
    return head, "\n".join(lines)


def title_for(now: datetime) -> str:
    """One stable title per local day: the signals table dedups on it, so a
    restart cannot send the same day's card twice."""
    return f"Reply today — {now.astimezone():%a %d %b}"


def _brief_hour() -> int:
    try:
        return max(0, min(23, int(os.getenv("BRIEF_HOUR", "7"))))
    except ValueError:
        return 7


_task: asyncio.Task | None = None
_pending: list[Signal] = []


async def maybe_start(now: datetime | None = None) -> bool:
    """The day's card, once, after BRIEF_HOUR local time. The window is
    claimed in Postgres (`kernel.occurrence`) so a restart does not re-read
    every mailbox. The read itself -- up to a dozen local-model calls -- runs
    in the background, off the scheduler's shared signals deadline; its
    Signal is handed over by `take_signals()` on a later cycle."""
    global _task
    from ...kernel import background, occurrence

    now = now or datetime.now(UTC)
    local = now.astimezone()
    if local.hour < _brief_hour():
        return False
    if _task is not None and not _task.done():
        return False
    window = f"{local:%Y-%m-%d}"
    if not await occurrence.claim("mail.reply_today", window):
        return False
    _task = background.spawn(_run(window, now), "mail reply-today card")
    return True


async def _run(window: str, now: datetime) -> None:
    from ...kernel import occurrence
    try:
        card = await read(now=now)
    except Exception as exc:                            # noqa: BLE001
        from .._http import redact
        log.warning("reply-today card failed, retried later: %s", redact(exc))
        await occurrence.release("mail.reply_today", window)
        return
    await occurrence.done("mail.reply_today", window)
    _head, body = render(card)
    _last.update(count=None if (card.unreadable and not card.looked)
                 else len(card.reply), at=card.at)
    _pending.append(Signal(title=title_for(now), body=body,
                           severity=Severity.NORMAL if card.reply else Severity.INFO))


def take_signals() -> list[Signal]:
    out = list(_pending)
    _pending.clear()
    return out


def brief_line(now: datetime | None = None) -> str:
    """One clause for `MailModule.brief()`. Nothing if no card was read in
    the last day; never a number without its time."""
    at = _last.get("at")
    now = now or datetime.now(UTC)
    if at is None or now - at > timedelta(hours=24):
        return ""
    if _last.get("count") is None:
        return f"reply today: not known, mail unreadable ({at:%H:%M} UTC)"
    return f"reply today: {_last['count']} (as of {at:%H:%M} UTC)"
