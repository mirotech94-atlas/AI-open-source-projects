"""Support tickets over HTTP: the customer's side and the operator's side.

Two surfaces, one state machine (`guardian_hub.tickets`):

* `/api/v1/support/*` is tenant-scoped. Any signed-in member may raise a
  ticket and read their own tenant's threads -- asking for help is not an
  administrative act, and a viewer who cannot see a problem's fix can still
  be the person who noticed it.
* `/api/v1/platform/tickets*` is superadmin-only and crosses tenants, like
  the rest of the owner console.

Ownership is checked on every single read and write by filtering on
`tenant_id` in the query itself, not by fetching a row and comparing
afterwards: a ticket belonging to another customer must be indistinguishable
from one that does not exist.
"""
import datetime as dt

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy.orm import Session as DbSession

from ..config import settings
from .. import audit, models as m, platform_settings, ratelimit, schemas_dash as sd, tickets as svc
from ..db import get_db
from ..deps import require_role, require_superadmin
from ..live import bus

router = APIRouter(prefix="/api/v1", tags=["tickets"])

VIEWER = Depends(require_role("viewer"))


def _ip(request: Request) -> str:
    return request.client.host if request.client else ""


def _iso(v: dt.datetime | None) -> str:
    return v.isoformat(timespec="seconds") if v else ""


def _row(t: m.Ticket, *, count: int = 0, tenant_name: str = "") -> sd.TicketRow:
    return sd.TicketRow(
        id=t.id, subject=t.subject or "", status=t.status or svc.OPEN,
        priority=t.priority or "normal", opened_by=t.opened_by or "",
        created=_iso(t.created), last_message_at=_iso(t.last_message_at),
        last_author=t.last_author or "customer", messages=count,
        resolved_at=_iso(t.resolved_at), resolved_by=t.resolved_by or "",
        tenant_id=t.tenant_id if tenant_name else "", tenant_name=tenant_name,
        category=t.category or "other",
        context_kind=t.context_kind or "", context_id=t.context_id or "",
        context_label=t.context_label or "",
    )


def _messages(db: DbSession, ticket_id: str, *, show_delivery: bool) -> list[sd.TicketMessageRow]:
    return [
        sd.TicketMessageRow(
            id=msg.id, side=msg.author_side or "customer", author=msg.author_name or "",
            body=msg.body or "", created=_iso(msg.created),
            # Delivery is the operator's business: a customer seeing
            # "emailed: no" on their own message would read it as a failure,
            # when the thread is the system of record either way.
            emailed=bool(msg.emailed_to) if show_delivery else False,
        )
        for msg in svc.messages(db, ticket_id)
    ]


# --------------------------------------------------------------------------
# The customer's side
# --------------------------------------------------------------------------

class TicketIn(BaseModel):
    subject: str = ""
    body: str = ""
    priority: str = "normal"
    #: See tickets.CATEGORIES. Anything unrecognised becomes "other" rather
    #: than a 422: a customer describing a problem should never be stopped by
    #: a taxonomy, and the operator can recategorise.
    category: str = "other"
    #: What the customer was looking at when they wrote in. Validated as a
    #: triple and dropped whole if incomplete -- see tickets.clean_context.
    context_kind: str = ""
    context_id: str = ""
    context_label: str = ""


class ReplyIn(BaseModel):
    body: str = ""


#: (calls, window seconds) per person per tenant. Every ticket and every
#: customer reply e-mails the support mailbox, and both are viewer-level on
#: purpose (see the module docstring) -- which also makes them the writes a
#: public demo visitor or a chat loop can reach. A person with a real problem
#: opens one ticket and writes a handful of replies; these bounds sit far
#: above that and far below a mailbox flood.
TICKET_OPEN_LIMIT = (5, 600)
TICKET_REPLY_LIMIT = (20, 600)


def _limit(kind: str, tenant: m.Tenant, user, limit: tuple[int, int]) -> None:
    """One bucket per person whether they write from the Support page or ask
    Vault to raise it: chat calls these routes as `chat:<username>` (see
    `chat/tools._chat_ctx`), so the prefix is dropped before keying."""
    who = (getattr(user, "username", "") or "").removeprefix("chat:")
    if not ratelimit.allow(f"{kind}:{tenant.id}:{who}", *limit):
        raise HTTPException(429, "That is a lot of messages in a few minutes -- wait a little "
                                 "and try again. Everything you already sent has arrived.")


@router.get("/support/tickets", response_model=sd.TicketsPayload)
def my_tickets(ctx=VIEWER, db: DbSession = Depends(get_db)):
    user, tenant, role = ctx
    rows = (db.query(m.Ticket).filter(m.Ticket.tenant_id == tenant.id)
              .order_by(m.Ticket.last_message_at.desc()).all())
    counts = svc.message_counts(db, [t.id for t in rows])
    return sd.TicketsPayload(
        tickets=[_row(t, count=counts.get(t.id, 0)) for t in rows],
        open_count=sum(1 for t in rows if t.status != svc.RESOLVED),
        support_email=platform_settings.support_email(),
        support_direct_email=platform_settings.support_direct_email(),
        # Item 47: whether a reply will actually leave the building. With no
        # SMTP host configured `mailer.send` logs a warning and returns --
        # so the addresses on the page were decoration, and a customer who
        # replied and waited for an email waited forever. The page says
        # which of the two it is rather than implying the happy one.
        mail_works=bool(settings.smtp_host),
        support_reply_sla=platform_settings.support_reply_sla(),
    )


@router.post("/support/tickets", response_model=sd.TicketDetail)
def open_ticket(body: TicketIn, request: Request, ctx=VIEWER, db: DbSession = Depends(get_db)):
    user, tenant, role = ctx
    _limit("ticket_open", tenant, user, TICKET_OPEN_LIMIT)
    subject, text = svc.clean_subject(body.subject), svc.clean_body(body.body)
    if not subject:
        raise HTTPException(422, "Give the ticket a subject")
    if not text:
        raise HTTPException(422, "Describe what is happening")
    t = svc.raise_ticket(
        db, tenant, user, subject=subject, body=text, priority=body.priority,
        category=body.category,
        context=svc.clean_context(body.context_kind, body.context_id, body.context_label),
    )
    audit.log(db, tenant.id, user.username, role, "ticket.open", target=t.id,
              ip=_ip(request), body={"subject": t.subject, "priority": t.priority,
                                     "category": t.category,
                                     "about": t.context_label or ""})
    db.commit()
    bus.publish(tenant.id, "ticket", {"action": "open", "id": t.id})
    return sd.TicketDetail(ticket=_row(t, count=1),
                           messages=_messages(db, t.id, show_delivery=False))


def _my_ticket(db: DbSession, tenant_id: str, ticket_id: str) -> m.Ticket:
    """This tenant's ticket, or a 404. Another customer's ticket is a 404 and
    not a 403: the owner console is the only place cross-tenant existence is
    a fact anyone is entitled to."""
    t = (db.query(m.Ticket)
           .filter(m.Ticket.id == ticket_id, m.Ticket.tenant_id == tenant_id)
           .one_or_none())
    if t is None:
        raise HTTPException(404, "No such ticket")
    return t


@router.get("/support/tickets/{ticket_id}", response_model=sd.TicketDetail)
def my_ticket(ticket_id: str, ctx=VIEWER, db: DbSession = Depends(get_db)):
    user, tenant, role = ctx
    t = _my_ticket(db, tenant.id, ticket_id)
    msgs = _messages(db, t.id, show_delivery=False)
    return sd.TicketDetail(ticket=_row(t, count=len(msgs)), messages=msgs)


@router.post("/support/tickets/{ticket_id}/reply", response_model=sd.TicketDetail)
def my_reply(ticket_id: str, body: ReplyIn, request: Request, ctx=VIEWER,
             db: DbSession = Depends(get_db)):
    user, tenant, role = ctx
    _limit("ticket_reply", tenant, user, TICKET_REPLY_LIMIT)
    t = _my_ticket(db, tenant.id, ticket_id)
    text = svc.clean_body(body.body)
    if not text:
        raise HTTPException(422, "Write a reply first")
    svc.add_reply(db, tenant, t, from_owner=False, author=user.username, body=text)
    audit.log(db, tenant.id, user.username, role, "ticket.reply", target=t.id,
              ip=_ip(request), body=None)
    db.commit()
    bus.publish(tenant.id, "ticket", {"action": "reply", "id": t.id})
    msgs = _messages(db, t.id, show_delivery=False)
    return sd.TicketDetail(ticket=_row(t, count=len(msgs)), messages=msgs)


# --------------------------------------------------------------------------
# The operator's side (owner console)
# --------------------------------------------------------------------------

class OwnerReplyIn(BaseModel):
    body: str = ""


class StatusIn(BaseModel):
    status: str = ""     # resolved | open
    note: str = ""       # posted into the thread when resolving


def _tenant_names(db: DbSession) -> dict[str, str]:
    return {t.id: (t.name or t.slug) for t in db.query(m.Tenant).all()}


@router.get("/platform/tickets", response_model=sd.TicketsPayload)
def owner_tickets(status: str = "", db: DbSession = Depends(get_db),
                  _owner=Depends(require_superadmin)):
    """Every customer's tickets, the ones waiting on us first.

    Sorted by status before recency on purpose: the queue's job is to answer
    the question "what needs me", and a freshly-answered thread is not it.
    """
    q = db.query(m.Ticket)
    if status in (svc.OPEN, svc.ANSWERED, svc.RESOLVED):
        q = q.filter(m.Ticket.status == status)
    rows = q.order_by(m.Ticket.last_message_at.desc()).all()
    counts = svc.message_counts(db, [t.id for t in rows])
    names = _tenant_names(db)
    order = {svc.OPEN: 0, svc.ANSWERED: 1, svc.RESOLVED: 2}
    rows.sort(key=lambda t: (order.get(t.status, 3),
                             -(t.last_message_at.timestamp() if t.last_message_at else 0)))
    return sd.TicketsPayload(
        tickets=[_row(t, count=counts.get(t.id, 0),
                      tenant_name=names.get(t.tenant_id, "(deleted customer)"))
                 for t in rows],
        open_count=sum(1 for t in rows if t.status == svc.OPEN),
        support_email=platform_settings.support_email(),
        mail_works=bool(settings.smtp_host),
    )


def _any_ticket(db: DbSession, ticket_id: str) -> tuple[m.Ticket, m.Tenant]:
    t = db.query(m.Ticket).filter(m.Ticket.id == ticket_id).one_or_none()
    if t is None:
        raise HTTPException(404, "No such ticket")
    tenant = db.query(m.Tenant).filter(m.Tenant.id == t.tenant_id).one_or_none()
    if tenant is None:
        raise HTTPException(404, "That ticket's customer no longer exists")
    return t, tenant


def _opener(db: DbSession, t: m.Ticket) -> m.User | None:
    if not t.opened_by:
        return None
    return db.query(m.User).filter(m.User.username == t.opened_by).one_or_none()


@router.get("/platform/tickets/{ticket_id}", response_model=sd.TicketDetail)
def owner_ticket(ticket_id: str, db: DbSession = Depends(get_db),
                 _owner=Depends(require_superadmin)):
    t, tenant = _any_ticket(db, ticket_id)
    msgs = _messages(db, t.id, show_delivery=True)
    return sd.TicketDetail(ticket=_row(t, count=len(msgs), tenant_name=tenant.name or tenant.slug),
                           messages=msgs)


@router.post("/platform/tickets/{ticket_id}/reply", response_model=sd.TicketDetail)
def owner_reply(ticket_id: str, body: OwnerReplyIn, request: Request,
                db: DbSession = Depends(get_db), owner=Depends(require_superadmin)):
    """Answer a customer. The reply lands in the in-app thread AND is emailed
    to them, which is the arrangement the customer was promised."""
    t, tenant = _any_ticket(db, ticket_id)
    text = svc.clean_body(body.body)
    if not text:
        raise HTTPException(422, "Write a reply first")
    svc.add_reply(db, tenant, t, from_owner=True, author=owner.username, body=text,
                  opener=_opener(db, t))
    audit.log(db, tenant.id, owner.username, "superadmin", "ticket.owner_reply",
              target=t.id, ip=_ip(request), body=None)
    db.commit()
    bus.publish(tenant.id, "ticket", {"action": "reply", "id": t.id})
    msgs = _messages(db, t.id, show_delivery=True)
    return sd.TicketDetail(ticket=_row(t, count=len(msgs), tenant_name=tenant.name or tenant.slug),
                           messages=msgs)


@router.post("/platform/tickets/{ticket_id}/status", response_model=sd.TicketDetail)
def owner_status(ticket_id: str, body: StatusIn, request: Request,
                 db: DbSession = Depends(get_db), owner=Depends(require_superadmin)):
    """Close a ticket whose issue is fixed, or put one back in the queue.

    Only the operator closes: a resolved ticket is a claim that the problem is
    dealt with, and that is Vault's claim to make. The customer's recourse
    is a reply, which reopens it.
    """
    t, tenant = _any_ticket(db, ticket_id)
    want = (body.status or "").strip().lower()
    if want == svc.RESOLVED:
        svc.resolve(db, tenant, t, by=owner.username, note=body.note,
                    opener=_opener(db, t))
        action = "ticket.resolve"
    elif want == svc.OPEN:
        svc.reopen(db, t, by=owner.username)
        action = "ticket.reopen"
    else:
        raise HTTPException(422, "Status must be 'resolved' or 'open'")
    audit.log(db, tenant.id, owner.username, "superadmin", action, target=t.id,
              ip=_ip(request), body=None)
    db.commit()
    bus.publish(tenant.id, "ticket", {"action": want, "id": t.id})
    msgs = _messages(db, t.id, show_delivery=True)
    return sd.TicketDetail(ticket=_row(t, count=len(msgs), tenant_name=tenant.name or tenant.slug),
                           messages=msgs)
