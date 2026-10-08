"""Support tickets: the conversation between a customer and the operator.

Everything about a ticket that is not HTTP lives here, so the tenant-facing
routes and the owner console's routes cannot drift into two different state
machines over the same two tables.

Three rules this module exists to keep:

1. **The thread is the system of record; the letter is a tap on the
   shoulder.** guarder.io does not accept inbound mail into the app (Resend
   receiving is off, Zoho holds the apex MX), so a reply to a Vault letter
   reaches a mailbox, never the ticket. Every notification therefore links
   into the thread and says to answer there.

2. **Write, commit, then send.** `mailer.send` never raises, so ordering is
   the only protection there is: the message row is committed before its
   notification goes out. A letter quoting a message the database does not
   have would be unanswerable.

3. **No contact address is the ordinary case.** A customer's address comes
   from `Tenant.settings["profile"]["email"]`, which is blank for demo
   tenants and for anyone who never filled the profile in. Blank means "post
   it in the thread and send no letter" -- it must never raise, and it must
   never stop the reply from being recorded.

4. **`emailed_to` records what happened, not what was attempted.** It is
   stamped only when `mailer.send` reports the letter actually went. The
   first version stamped it regardless, so a reply to a demo account at
   `demoadmin@guardian.invalid` -- a reserved domain that can never receive
   mail -- looked exactly like one that arrived, and the operator's view said
   so.
"""
from __future__ import annotations

import datetime as dt

from sqlalchemy import func
from sqlalchemy.orm import Session as DbSession

from . import mailer, models as m, platform_settings

#: open      -- the customer spoke last; waiting on us
#: answered  -- we spoke last; waiting on them
#: resolved  -- the owner has closed it
OPEN, ANSWERED, RESOLVED = "open", "answered", "resolved"

#: What a customer may tell us about urgency. Advisory: it orders the owner's
#: queue and promises no response time.
PRIORITIES = ("low", "normal", "high")

#: What a ticket is ABOUT, in the vocabulary of a security product rather
#: than a generic help desk. "Billing / Technical / Other" tells an operator
#: nothing; "Vault did not catch this" tells them the product failed and
#: the ticket outranks everything else in the queue.
#:
#: `missed` is first on purpose. It is the reason a customer of a security
#: product writes in at all, and it is the only category that is also a bug
#: report about Vault itself.
CATEGORIES = ("missed", "finding", "device", "network", "account", "other")

#: Which collection `Ticket.context_id` indexes into. Anything else is
#: dropped rather than stored: an unknown kind cannot be rendered as a link,
#: and a chip that goes nowhere is worse than no chip.
CONTEXT_KINDS = ("device", "alert", "site", "network", "scan")

SUBJECT_MAX = 160
BODY_MAX = 8000
LABEL_MAX = 160


def _now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def clean_subject(s: str) -> str:
    return " ".join((s or "").split())[:SUBJECT_MAX]


def clean_category(s: str) -> str:
    v = (s or "").strip().lower()
    return v if v in CATEGORIES else "other"


def clean_context(kind: str, ref: str, label: str) -> tuple[str, str, str]:
    """The (kind, id, label) triple a ticket may carry, or three blanks.

    All three move together. A label with no kind is a sentence the operator
    cannot click, and a kind with no label is an id nobody can read once the
    row it named is gone -- so a triple that is not complete and known is
    discarded whole.
    """
    k = (kind or "").strip().lower()
    ident = " ".join((ref or "").split())[:64]
    text = " ".join((label or "").split())[:LABEL_MAX]
    if k not in CONTEXT_KINDS or not ident or not text:
        return "", "", ""
    return k, ident, text


def clean_body(s: str) -> str:
    # Trailing whitespace goes; interior newlines stay, because a person
    # describing a problem uses paragraphs.
    return (s or "").strip()[:BODY_MAX]


def tenant_email(db: DbSession, tenant: m.Tenant, *, opener: m.User | None = None) -> str:
    """Where a letter to this customer goes, or "" when there is nowhere.

    The profile address the customer set is preferred, because it is the one
    they chose for Vault's mail. Falling back to the address of the person
    who raised the ticket is deliberate and narrow: they are demonstrably
    involved in this conversation. Neither existing is normal and returns "".
    """
    prof = (tenant.settings or {}).get("profile") or {}
    email = (prof.get("email") or "").strip()
    if email:
        return email
    if opener is not None and (opener.email or "").strip():
        return opener.email.strip()
    return ""


def raise_ticket(db: DbSession, tenant: m.Tenant, user: m.User, *, subject: str,
                 body: str, priority: str = "normal", category: str = "other",
                 context: tuple[str, str, str] = ("", "", "")) -> m.Ticket:
    """A customer opens a ticket. Returns the committed row.

    The operator is notified at the support mailbox (`platform_settings`); a hub with that
    unset simply records the ticket, which still reaches the owner console.
    """
    t = m.Ticket(
        tenant_id=tenant.id, subject=clean_subject(subject),
        status=OPEN, priority=priority if priority in PRIORITIES else "normal",
        opened_by=user.username, last_author="customer", last_message_at=_now(),
        category=clean_category(category),
        context_kind=context[0], context_id=context[1], context_label=context[2],
    )
    db.add(t)
    db.flush()
    msg = m.TicketMessage(
        ticket_id=t.id, tenant_id=tenant.id, author_side="customer",
        author_name=user.username, body=clean_body(body),
    )
    db.add(msg)
    db.commit()

    to = platform_settings.support_email()
    if to and mailer.send_ticket_raised(
            to, ticket_id=t.id, subject_line=t.subject, tenant_name=tenant.name or tenant.slug,
            who=user.username, body=msg.body, priority=t.priority):
        msg.emailed_to = to[:160]
        db.commit()
    return t


def add_reply(db: DbSession, tenant: m.Tenant, ticket: m.Ticket, *, from_owner: bool,
              author: str, body: str, opener: m.User | None = None) -> m.TicketMessage:
    """One reply, from either side, posted to the thread and then mailed on.

    A customer's reply to a RESOLVED ticket reopens it. That is the whole
    reason `resolved` is not a terminal state: a customer who is told their
    problem is fixed when it is not needs to say so in the same thread, not
    open a second ticket about the same thing.
    """
    msg = m.TicketMessage(
        ticket_id=ticket.id, tenant_id=tenant.id,
        author_side="owner" if from_owner else "customer",
        author_name=author, body=clean_body(body),
    )
    db.add(msg)
    ticket.last_message_at = _now()
    ticket.last_author = msg.author_side
    # Answering moves the ball; a customer speaking (even on a resolved
    # ticket) moves it back and reopens.
    ticket.status = ANSWERED if from_owner else OPEN
    if not from_owner:
        ticket.resolved_at, ticket.resolved_by = None, ""
    db.commit()

    to = tenant_email(db, tenant, opener=opener) if from_owner else platform_settings.support_email()
    if to and mailer.send_ticket_reply(
            to, ticket_id=ticket.id, subject_line=ticket.subject,
            tenant_name=tenant.name or tenant.slug, body=msg.body,
            from_owner=from_owner, author=author):
        msg.emailed_to = to[:160]
        db.commit()
    return msg


def resolve(db: DbSession, tenant: m.Tenant, ticket: m.Ticket, *, by: str,
            note: str = "", opener: m.User | None = None) -> m.Ticket:
    """The owner closes a ticket whose issue is fixed.

    An optional note is posted into the thread as an owner message first, so
    "resolved" is never a status change with no explanation attached -- and so
    the customer's letter can quote the reason.
    """
    note = clean_body(note)
    if note:
        msg = m.TicketMessage(
            ticket_id=ticket.id, tenant_id=tenant.id, author_side="owner",
            author_name=by, body=note,
        )
        db.add(msg)
    ticket.status = RESOLVED
    ticket.resolved_at = _now()
    ticket.resolved_by = by
    ticket.last_message_at = _now()
    ticket.last_author = "owner"
    db.commit()

    to = tenant_email(db, tenant, opener=opener)
    if to:
        mailer.send_ticket_resolved(
            to, ticket_id=ticket.id, subject_line=ticket.subject,
            tenant_name=tenant.name or tenant.slug, note=note)
    return ticket


def reopen(db: DbSession, ticket: m.Ticket, *, by: str) -> m.Ticket:
    """Put a resolved ticket back in the queue without adding a message."""
    ticket.status = OPEN
    ticket.resolved_at, ticket.resolved_by = None, ""
    ticket.last_message_at = _now()
    db.commit()
    return ticket


def messages(db: DbSession, ticket_id: str) -> list[m.TicketMessage]:
    return (db.query(m.TicketMessage)
              .filter(m.TicketMessage.ticket_id == ticket_id)
              .order_by(m.TicketMessage.created.asc(), m.TicketMessage.id.asc())
              .all())


def message_counts(db: DbSession, ticket_ids: list[str]) -> dict[str, int]:
    """One grouped query for a whole queue, rather than a count per row."""
    if not ticket_ids:
        return {}
    rows = (db.query(m.TicketMessage.ticket_id, func.count(m.TicketMessage.id))
              .filter(m.TicketMessage.ticket_id.in_(ticket_ids))
              .group_by(m.TicketMessage.ticket_id).all())
    return {tid: int(n) for tid, n in rows}
