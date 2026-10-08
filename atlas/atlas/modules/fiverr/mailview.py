"""Fiverr, read from Fiverr's own notification emails.

WHY THIS EXISTS (Miro, 2026-09-28). Fiverr's PerimeterX bot wall blocks
every seller page ATLAS asks for, and getting past bot detection is not on
the table. Fiverr does, however, mail him about every buyer message, every
order and every dispute, and ATLAS already reads his Gmail. This is the
read-only view of the business those emails make possible.

Read-only, structurally. It reuses the mail module's Gmail client
(`mail._api`: GET requests with the account's existing refresh token) and
nothing else. No second mail login, no reply, no label, no "mark read" --
Gmail's own UNREAD flag is left exactly as he left it. `mail.ingest._seen`,
the new-mail watermark, is not touched either.

WHAT EMAIL CAN AND CANNOT PROVE.

* A buyer wrote. Provable: the subject names them.
* He replied. NOT provable: Fiverr sends no email when he answers, and
  "opened the notification" is not "answered the buyer". So the inbox check
  reports how many buyer-message emails arrived, as information, and never
  calls them unanswered.
* An order is due in under 12 hours, or a buyer opened a dispute. Provable:
  Fiverr says so in the subject. Those are the only things that make a
  check DEGRADED.

Formats, verified against real notifications in his inbox on 2026-09-28
(all from noreply@e.fiverr.com):

    You’ve got new messages from {name}           (U+2019 apostrophe)
    You've received messages from {name}          (older, order threads)
    Great news: You've received an order from {user}
    Action required: Your order is due in less than 12 hours.
    {user} opened a dispute regarding your order
    Hi {username}   + body "Your Gigs have been paused ..."

Mail about orders Miro PLACED as a buyer ("Your order is ready", "your
order has started!", "A custom offer, just for you", receipts, invoices,
"rate your recent order") is filed as buyer-side and kept out of every
seller count. announce./affiliates./notifications. mail is marketing or
service notices and is ignored.

NOT VERIFIED, and so not modelled: an email for "buyer submitted the
requirements", a seller-side review received, or a buyer brief. None was in
the inbox. The new-order email says "review the requirements below", which
is not a waiting-on-requirements state, so none is invented; anything
unrecognised is kept as "other" with its raw subject.

GIG IMPRESSIONS AND CLICKS ARE NOT IN EMAIL. They still need the Fiverr
seller analytics page (`check_gig_stats`, UNKNOWN while the wall stands).
The only "performance stats" email in the inbox is Fiverr Affiliates'
monthly one, which is affiliate referrals, not gig stats -- so nothing here
parses it, and no figure is derived from anything else.

Every subject, display name and body is written by an outside party.
Nothing here puts one in a prompt except through `wrap_untrusted()`; raw
subjects stay in `evidence`, which the agents' `module_status` read does
not return.
"""
from __future__ import annotations

import asyncio
import html
import os
import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from email.utils import parseaddr

from ...kernel.contracts import Health, Observation, Severity, Signal
from ...kernel.permissions import wrap_untrusted

#: Transactional Fiverr mail. announce./affiliates./notifications. are not
#: about his gigs.
SENDER = "e.fiverr.com"
SOURCE = "fiverr-email"
WINDOW_DAYS = int(os.getenv("FIVERR_MAIL_DAYS", "7") or 7)
MAX_MESSAGES = 40

# Kinds. Seller-side first; BUYER_SIDE is Miro buying from someone else.
MESSAGE = "message"
NEW_ORDER = "new_order"
DUE_SOON = "due_soon"
DISPUTE = "dispute"
GIGS_PAUSED = "gigs_paused"
BUYER_SIDE = "buyer_side"
OTHER = "other"

_BUYER_SIDE = ("your order is ready", "your order has started",
               "custom offer, just for you", "receipt of doing",
               "your invoice", "rate your recent order", "there’s still time to review",
               "there's still time to review")

_I = re.IGNORECASE

_WHO = (
    re.compile(r"(?:new messages|received messages) from (?P<who>.+)$", _I),
    re.compile(r"received an order from (?P<who>.+)$", _I),
    re.compile(r"^(?P<who>.+?) opened a dispute", _I),
)


def _norm(subject: str) -> str:
    # ’ is not folded by NFKC; fold it here so one pattern covers both forms.
    return (subject or "").replace("’", "'").strip()


def is_fiverr(sender: str) -> bool:
    """The ADDRESS is Fiverr's -- not the display name, which anyone can set
    to "noreply@e.fiverr.com"."""
    _, addr = parseaddr(sender or "")
    dom = addr.rpartition("@")[2].lower()
    return dom == SENDER or dom.endswith(f".{SENDER}")


def classify(sender: str, subject: str, body: str = "") -> str:
    """Which kind of Fiverr notification this is. Sender, then subject."""
    if not is_fiverr(sender):
        return OTHER
    low = _norm(subject).lower()
    if any(k.replace("’", "'") in low for k in _BUYER_SIDE):
        return BUYER_SIDE
    if "new messages from" in low or "received messages from" in low:
        return MESSAGE
    if "received an order from" in low:
        return NEW_ORDER
    if "your order is due in less than" in low:
        return DUE_SOON
    if "opened a dispute" in low:
        return DISPUTE
    if "gigs have been paused" in (body or "").lower():
        return GIGS_PAUSED
    return OTHER


def counterpart(subject: str) -> str:
    """The buyer the subject names, or "" when it names none."""
    text = _norm(subject)
    for pattern in _WHO:
        if m := pattern.search(text):
            return m.group("who").strip()[:60]
    return ""


# ------------------------------------------------------------ off-platform

# The notification body is the buyer's words wrapped in Fiverr's template.
# The template itself says "twitter facebook pinterest linkedin instagram"
# and "This email was intended for ...", so matching the whole body flags
# every message. Only the buyer's part is scanned.
_LEAD_IN = re.compile(
    r"(?:messaged you:|we put them all here for you|in regards to the "
    r"(?:gig|order),[^:]*:)", _I)
_LEAD_OUT = re.compile(r"go to your inbox|this email was intended for", _I)

_TAG = re.compile(r"<[^>]+>")
_STYLE = re.compile(r"<(style|script)\b.*?</\1>", _I | re.DOTALL)

_OFF_PLATFORM = {
    "whatsapp": re.compile(r"whats\s*app|\bwa\.me\b", _I),
    "telegram": re.compile(r"telegram|\bt\.me/", _I),
    "skype": re.compile(r"\bskype\b", _I),
    "discord": re.compile(r"\bdiscord\b", _I),
    "signal": re.compile(r"\bsignal app\b|\bon signal\b", _I),
    "email": re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+|\bg\s*mail\b|"
                        r"\bemail me\b|\bmy email\b", _I),
    "phone": re.compile(r"\+?\d[\d\s().-]{7,}\d|\bcall me\b|\btext me\b|"
                        r"\bphone number\b", _I),
    "outside": re.compile(r"outside (?:of )?fiverr|off[- ]?platform|"
                          r"contact me directly", _I),
}


def buyer_text(body: str) -> str:
    """The part of a message notification the buyer wrote."""
    text = body or ""
    if _TAG.search(text):
        # No text/plain part: mail._body_text hands back the HTML, whose
        # tracking URLs and ids would read as phone numbers.
        text = html.unescape(_TAG.sub(" ", _STYLE.sub(" ", text)))
    if m := _LEAD_IN.search(text):
        text = text[m.end():]
    if m := _LEAD_OUT.search(text):
        text = text[:m.start()]
    return text[:3000]


def off_platform(body: str) -> tuple[str, ...]:
    """Which ways of leaving Fiverr the buyer's words mention. Fiverr's terms
    forbid taking contact or payment off the platform, and asking to is the
    commonest opening of a scam -- so this flags, and nothing ever replies."""
    text = buyer_text(body)
    hits = []
    for name, pattern in _OFF_PLATFORM.items():
        for m in pattern.finditer(text):
            found = m.group(0)
            if name == "email" and "fiverr.com" in found.lower():
                continue
            # A date or an order total is digits too; a phone number has
            # at least nine.
            if (name == "phone" and (found[0].isdigit() or found[0] == "+")
                    and sum(ch.isdigit() for ch in found) < 9):
                continue
            hits.append(name)
            break
    return tuple(hits)


# ---------------------------------------------------------------- reading

@dataclass(frozen=True, slots=True)
class FiverrMail:
    id: str
    kind: str
    subject: str                 # raw, untrusted
    who: str                     # from the subject, untrusted
    at: datetime                 # when Gmail received it
    off_platform: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class Reading:
    mails: tuple[FiverrMail, ...]
    read_at: datetime
    accounts: tuple[str, ...]


class Unreadable(Exception):
    """Gmail could not be read, so there is no reading -- not an empty one."""


def _accounts():
    """The Gmail accounts to read: FIVERR_MAIL_ACCOUNT if set, else every
    configured one (a search for Fiverr's sender is cheap, and the wrong
    mailbox is caught by the "ever" probe in `read`)."""
    from ..mail.accounts import all_accounts

    wanted = os.getenv("FIVERR_MAIL_ACCOUNT", "").strip()
    google = [a for a in all_accounts() if a.kind == "google"]
    if wanted:
        google = [a for a in google if a.name == wanted]
        if not google:
            raise Unreadable(f"FIVERR_MAIL_ACCOUNT={wanted} names no Gmail "
                             f"account in MAIL_ACCOUNTS")
    ready = [a for a in google if a.configured]
    if ready:
        return ready
    if google:
        raise Unreadable(f"Gmail account {google[0].name} is not configured: "
                         f"set {google[0].missing}")
    raise Unreadable("no Gmail account is configured (MAIL_ACCOUNTS), so "
                     "Fiverr's emails cannot be read")


async def _read_account(account, days: int) -> list[FiverrMail] | None:
    """This account's Fiverr mail in the window, or None when the account
    has never received any at all."""
    from ..mail import _api, _body_text, _header

    # Has this mailbox EVER had Fiverr mail? If not, it is the wrong
    # mailbox, and "0 messages" from it would be a quiet week that is not.
    ever = await _api("/messages", account=account, q=f"from:{SENDER}",
                      maxResults=1)
    if not ever.get("messages"):
        return None

    data = await _api("/messages", account=account,
                      q=f"from:{SENDER} newer_than:{days}d",
                      maxResults=MAX_MESSAGES)
    gate = asyncio.Semaphore(6)

    async def one(mid: str) -> dict:
        async with gate:
            return await _api(f"/messages/{mid}", account=account, format="full")

    fulls = await asyncio.gather(*(one(m["id"])
                                   for m in data.get("messages", [])))
    out = []
    for full in fulls:
        sender = _header(full, "From")
        subject = _header(full, "Subject")
        body = _body_text(full.get("payload", {}))
        kind = classify(sender, subject, body)
        at = datetime.fromtimestamp(int(full.get("internalDate", 0)) / 1000, UTC)
        out.append(FiverrMail(
            id=str(full.get("id", "")), kind=kind, subject=subject[:200],
            who=counterpart(subject), at=at,
            off_platform=off_platform(body) if kind == MESSAGE else ()))
    return out


async def read(days: int = WINDOW_DAYS) -> Reading:
    """Every Fiverr notification in the window, newest first. Raises
    `Unreadable` when Gmail could not be read -- a partial read is not a
    reading, because the account that failed may be the one Fiverr mails."""
    from .._http import redact

    accounts = _accounts()
    mails: list[FiverrMail] = []
    carrying: list[str] = []
    for account in accounts:
        try:
            got = await _read_account(account, days)
        except Exception as exc:
            raise Unreadable(f"Gmail ({account.name}): {redact(exc)}") from exc
        if got is None:
            continue
        mails.extend(got)
        carrying.append(account.name)
    if not carrying:
        # Zero from a mailbox Fiverr has never written to is not a quiet
        # week; it is the wrong mailbox.
        raise Unreadable(
            f"no Fiverr email has ever reached "
            f"{', '.join(a.name for a in accounts)} -- the wrong mailbox? Set "
            f"FIVERR_MAIL_ACCOUNT to the Gmail account Fiverr writes to")
    return Reading(tuple(sorted(mails, key=lambda m: m.at, reverse=True)),
                   datetime.now(UTC), tuple(carrying))


def _quoted(lines: list[str]) -> str:
    """Names and subjects are the outside party's words: quoted, never obeyed."""
    return wrap_untrusted("\n".join(lines), SOURCE) if lines else ""


def _stamp(reading: Reading) -> str:
    return f"read from Gmail at {reading.read_at:%Y-%m-%d %H:%M} UTC"


# ------------------------------------------------------------------ checks

async def check_inbox_mail(days: int = WINDOW_DAYS) -> Observation:
    name = "fiverr_inbox_mail"
    try:
        reading = await read(days)
    except Unreadable as exc:
        return Observation(name, Health.UNKNOWN, detail=str(exc))
    msgs = [m for m in reading.mails if m.kind == MESSAGE]
    buyers = sorted({m.who for m in msgs if m.who})
    flagged = [m for m in msgs if m.off_platform]
    detail = (f"{len(msgs)} buyer-message email(s) from {len(buyers)} buyer(s) "
              f"in the last {days} days, {_stamp(reading)}. Fiverr sends no "
              f"email when you reply, so whether these are answered cannot be "
              f"seen from here.")
    if flagged:
        detail += (f" {len(flagged)} ask(s) to move off Fiverr -- a likely "
                   f"scam under Fiverr's terms.")
    if buyers:
        detail += "\n" + _quoted([f"buyers: {', '.join(buyers)}"])
    return Observation(
        name, Health.OK, float(len(msgs)), "messages", detail,
        evidence={
            "read_at": reading.read_at.isoformat(),
            "accounts": list(reading.accounts),
            "messages": [{"id": m.id, "who": m.who, "subject": m.subject,
                          "at": m.at.isoformat(),
                          "off_platform": list(m.off_platform)}
                         for m in msgs[:20]],
            "replies_detectable": False,
        },
        at=reading.read_at)


async def check_orders_mail(days: int = WINDOW_DAYS) -> Observation:
    name = "fiverr_orders_mail"
    try:
        reading = await read(days)
    except Unreadable as exc:
        return Observation(name, Health.UNKNOWN, detail=str(exc))
    now = reading.read_at
    orders = [m for m in reading.mails if m.kind == NEW_ORDER]
    # "Due in less than 12 hours" is only true for the 12 hours after it
    # was sent; an older one proves nothing about now.
    due = [m for m in reading.mails
           if m.kind == DUE_SOON and now - m.at < timedelta(hours=12)]
    # Fiverr gives 2 days to answer a dispute; after that the email no
    # longer says anything about now. Older ones stay in evidence.
    all_disputes = [m for m in reading.mails if m.kind == DISPUTE]
    disputes = [m for m in all_disputes if now - m.at < timedelta(hours=48)]
    paused = [m for m in reading.mails if m.kind == GIGS_PAUSED]

    parts = [f"{len(orders)} new order(s) in the last {days} days"]
    if due:
        parts.append(f"{len(due)} due-in-under-12h notice(s) in the last 12h")
    if disputes:
        parts.append(f"{len(disputes)} dispute(s) opened -- Fiverr gives 2 days "
                     f"to respond")
    if paused:
        parts.append("Fiverr paused the gigs for unavailability")
    detail = "; ".join(parts) + f", {_stamp(reading)}."
    named = [f"order from {m.who}" for m in orders if m.who] + \
            [f"dispute by {m.who}" for m in disputes if m.who]
    if named:
        detail += "\n" + _quoted(named)
    return Observation(
        name, Health.DEGRADED if (due or disputes or paused) else Health.OK,
        float(len(orders)), "orders", detail,
        evidence={
            "read_at": now.isoformat(),
            "orders": [{"id": m.id, "who": m.who, "subject": m.subject,
                        "at": m.at.isoformat()} for m in orders[:20]],
            "due_soon": [m.subject for m in due],
            "disputes": [{"who": m.who, "subject": m.subject,
                          "at": m.at.isoformat(), "open_window": m in disputes}
                         for m in all_disputes],
            "gigs_paused": [m.at.isoformat() for m in paused],
        },
        at=now)


async def signals() -> tuple[Signal, ...]:
    """Off-platform asks and disputes, from the last day's mail.

    One day, because the scheduler dedups a signal on its title for 24h:
    a wider window would raise the same message again tomorrow. No signal
    suggests an action -- nothing replies to a buyer, least of all to one
    who looks like a scam."""
    try:
        reading = await read(1)
    except Unreadable:
        return ()                # the checks already say UNKNOWN, with why
    out: list[Signal] = []
    for m in reading.mails:
        if m.kind == MESSAGE and m.off_platform:
            out.append(Signal(
                title=f"Fiverr: a buyer asked to move off-platform ({m.id[-8:]})",
                body=(f"Asks for: {', '.join(m.off_platform)}. Taking contact or "
                      f"payment off Fiverr breaks its terms and is how most "
                      f"scams start. Do not reply off-platform; report it in "
                      f"Fiverr if it is. Nothing was sent. Email received "
                      f"{m.at:%Y-%m-%d %H:%M} UTC, {_stamp(reading)}.\n"
                      + _quoted([f"from: {m.who}", f"subject: {m.subject}"])),
                severity=Severity.NORMAL))
        elif m.kind == DISPUTE:
            out.append(Signal(
                title=f"Fiverr: a buyer opened a dispute ({m.id[-8:]})",
                body=(f"Fiverr gives 2 days to respond. Email received "
                      f"{m.at:%Y-%m-%d %H:%M} UTC, {_stamp(reading)}.\n"
                      + _quoted([f"subject: {m.subject}"])),
                severity=Severity.HIGH))
    return tuple(out)
