"""Lead capture for the public site on guarder.io.

Two routes, at opposite ends of the trust scale:

- `POST /api/v1/public/lead` is unauthenticated and internet-facing -- the
  "Request access" form on the marketing site posts to it through nginx, from
  the marketing origin. There is no billing and no self-serve signup yet, so
  this form is the whole funnel and it must survive being found by a bot.
- `GET /api/v1/leads` is superadmin-only. A lead has no tenant (the person
  has no account yet) and is the product owner's pipeline, not customer data,
  so it is never exposed to a tenant admin.

The abuse defences on the public route, in the order a request meets them:

1. nginx caps the body (`client_max_body_size 16k` on that exact location).
2. `schemas.LeadIn` caps every field, so anything oversized is a 422 at parse
   time -- before the limiter, before the DB.
3. The rate limiter, twice: per source IP, and a hub-wide ceiling so a
   distributed flood still cannot fill the table.
4. The honeypot: a field no person can reach, dropped silently if filled.
5. `models.Lead`'s columns are capped to the same lengths, so a write that
   somehow skipped the schema still cannot store an unbounded string.

And one thing that is deliberately *not* done: nothing the submitter typed is
ever returned to them. `LeadOut` is a fixed message.
"""
import datetime as dt

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
from sqlalchemy.orm import Session as DbSession

from .. import mailer, models as m, ratelimit, schemas as sc
from ..accounts import provision_from_lead, slugify
from ..clientip import client_ip
from ..db import get_db
from ..deps import require_superadmin
from .. import adminnotify

router = APIRouter(prefix="/api/v1", tags=["leads"])

# Per-IP: five submissions in ten minutes is far more than a person filling in
# a form once, and leaves room for a genuine retype after a typo.
LEAD_PER_IP = (5, 600)
# Hub-wide ceiling, because the per-IP bucket alone is no defence against a
# botnet. Sized so a real launch day (a few dozen genuine requests an hour)
# never touches it. Both windows stay <= ratelimit._STALE_AFTER_S.
LEAD_GLOBAL = (60, 3600)


def _client_ip(request: Request) -> str:
    """Thin alias for `clientip.client_ip`, which the public API's own auth
    dependency shares -- see that module for why `X-Real-IP` is only believed
    from a loopback peer. Kept as a name here because this module's tests and
    docstring both refer to it."""
    return client_ip(request)


@router.post("/public/lead", response_model=sc.LeadOut)
def submit_lead(body: sc.LeadIn, request: Request, bg: BackgroundTasks, db: DbSession = Depends(get_db)):
    ip = _client_ip(request)

    # The honeypot is checked before the limiter deliberately: a caught bot
    # gets the ordinary confirmation and no row, and does not get told
    # anything it could use to tell "caught" from "accepted" -- including by
    # hammering until it sees a 429 and inferring that its posts counted.
    if body.website.strip():
        return sc.LeadOut()

    # Checked after the honeypot (so a caught bot still gets the silent 200,
    # never a distinguishable 422) but before the limiter (like the honeypot,
    # a refusal here should not spend a rate-limit slot).
    if not body.accept_terms:
        raise HTTPException(422, "please accept the Terms of Service")

    if not ratelimit.allow(f"lead:ip:{ip}", *LEAD_PER_IP) or \
       not ratelimit.allow("lead:global", *LEAD_GLOBAL):
        # 429 rather than a silent success: a person who mistyped their email
        # and resubmitted twice deserves to be told the form is throttled,
        # and site/app.js has copy for exactly this.
        raise HTTPException(429, "too many requests -- try again shortly")

    # Lower-cased so the owner's list does not show the same person twice in
    # two capitalisations; not otherwise validated beyond LeadIn's length
    # cap, because rejecting a real address is worse than storing a bad one
    # nobody will reply to.
    email = body.email.strip().lower()
    lead = (db.query(m.Lead)
              .filter(m.Lead.email == email, m.Lead.status == "pending")
              .order_by(m.Lead.created.desc()).first())
    if lead is None:
        lead = m.Lead(email=email)
        db.add(lead)
    # A resubmission (a typo fixed, a note added) replaces the pending row's
    # details rather than adding a second row for the owner to reconcile.
    lead.name = body.name.strip()
    lead.company = body.company.strip()
    lead.network_size = body.network_size.strip()
    lead.note = body.note.strip()
    lead.site_name = body.site_name.strip()
    lead.ip = ip
    lead.created = dt.datetime.now(dt.timezone.utc)
    lead.terms_accepted_at = lead.created
    db.commit()
    adminnotify.notify("lead", f"Access request from {body.name.strip() or body.email.strip()}",
                       f"{body.email.strip().lower()} · {body.company.strip() or 'no company'} · "
                       f"{body.network_size.strip() or 'size not given'}" + (f"\n> {body.note.strip()[:300]}" if body.note.strip() else ""))
    for addr in mailer.owner_addresses(db):
        bg.add_task(mailer.send_lead_notice, addr, lead)
    return sc.LeadOut()


def _row(db: DbSession, r: m.Lead) -> sc.LeadRow:
    slug = ""
    if r.tenant_id:
        t = db.get(m.Tenant, r.tenant_id)
        slug = t.slug if t else ""
    return sc.LeadRow(
        id=r.id, name=r.name, email=r.email, company=r.company,
        network_size=r.network_size, note=r.note, ip=r.ip,
        created=(r.created or dt.datetime.now(dt.timezone.utc)).isoformat(),
        site_name=r.site_name or "", status=r.status or "pending",
        decided_at=r.decided_at.isoformat() if r.decided_at else "",
        decided_by=r.decided_by or "", tenant_slug=slug,
    )


@router.get("/leads", response_model=sc.LeadsOut)
def list_leads(db: DbSession = Depends(get_db), _: m.User = Depends(require_superadmin)):
    """Newest first. No pagination: this is a hand-run funnel with tens of
    rows, and the global rate limit above bounds how fast it can grow."""
    rows = db.query(m.Lead).order_by(m.Lead.created.desc()).limit(500).all()
    return sc.LeadsOut(leads=[_row(db, r) for r in rows])


def _lead_or_404(db: DbSession, lead_id: str) -> m.Lead:
    lead = db.get(m.Lead, lead_id)
    if lead is None:
        raise HTTPException(404, "lead not found")
    return lead


@router.post("/leads/{lead_id}/approve", response_model=sc.LeadRow)
def approve_lead(lead_id: str, body: sc.LeadApproveIn, request: Request, bg: BackgroundTasks,
                 db: DbSession = Depends(get_db), user: m.User = Depends(require_superadmin)):
    lead = _lead_or_404(db, lead_id)
    if lead.status == "approved":
        return _row(db, lead)                      # idempotent
    if lead.status == "rejected":
        raise HTTPException(409, "this request was rejected")
    tenant_name = (body.tenant_name.strip() or lead.site_name or lead.company
                   or f"{lead.name}'s network")
    slug = body.slug.strip() or slugify(tenant_name)
    username = (body.username.strip() or lead.email).lower()
    display_name = body.display_name.strip() or lead.name
    tenant, u, existed = provision_from_lead(
        db, lead, tenant_name=tenant_name, slug=slug, username=username,
        display_name=display_name, by=user.username, ip=_client_ip(request))
    db.commit()
    adminnotify.notify("tenant", f"Approved: {lead.name or lead.email} → {tenant.slug}",
                       f"{u.username} · admin · trial started", tenant=tenant.slug)
    bg.add_task(mailer.send_account_ready, lead.email, u.username, tenant.name, user_existed=existed)
    return _row(db, lead)


@router.post("/leads/{lead_id}/reject", response_model=sc.LeadRow)
def reject_lead(lead_id: str, request: Request, bg: BackgroundTasks,
                db: DbSession = Depends(get_db), user: m.User = Depends(require_superadmin)):
    lead = _lead_or_404(db, lead_id)
    if lead.status == "rejected":
        return _row(db, lead)
    if lead.status == "approved":
        raise HTTPException(409, "this request was already approved")
    lead.status = "rejected"
    lead.decided_at = dt.datetime.now(dt.timezone.utc)
    lead.decided_by = user.username
    db.commit()
    bg.add_task(mailer.send_lead_declined, lead.email)
    return _row(db, lead)
