"""Billing API: the plan catalogue, the trial, Stripe Checkout, the Stripe
customer portal, and the webhook Stripe calls back on.

Trust boundary, because it is not uniform across this file:

* `GET /billing`, `POST /billing/trial`, `POST /billing/checkout`,
  `POST /billing/portal` are **admin-only** on the tenant in session. An
  admin is who buys and cancels; an operator or viewer cannot see the page
  and cannot reach these routes.
* `POST /billing/stripe/webhook` is **unauthenticated and internet-facing**
  and is treated as hostile input from the first line: the raw body is read
  before anything parses it, the signature is verified against it, an
  unsigned or stale call is rejected, and a replay is answered 200 without
  being applied twice. There is no pydantic body model on that route on
  purpose -- see `stripe_api.verify_signature`.

What a hub with no Stripe keys does: everything except talk to Stripe. The
payload still answers (with `configured: false`), the trial still starts and
still expires, and the two routes that need Stripe return 503 with a plain
message. Billing being unconfigured must never be able to 500 a page or stop
the product working, because the free trial is the product's whole funnel
right now.
"""

import datetime as dt

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session as DbSession

from .. import audit, billing, models as m, plans, platform_settings, ratelimit
from .. import schemas as sc, schemas_dash as sd, stripe_api
from ..config import settings
from ..db import get_db
from ..deps import require_role

router = APIRouter(prefix="/api/v1", tags=["billing"])

ADMIN = Depends(require_role("admin"))

# The webhook is unauthenticated. A signature check is cheap, but not free,
# and a flood of unsigned posts should cost the hub as little as possible --
# so it is also rate-limited hub-wide. Sized far above anything Stripe does:
# a busy account might see a few events a second during a batch invoice run.
WEBHOOK_GLOBAL = (600, 60)


def _iso(value: dt.datetime | None) -> str:
    return value.isoformat() if value else ""


def _ip(request: Request) -> str:
    return request.client.host if request.client else ""


def _base_url() -> str:
    return settings.base_url.rstrip("/")


# --------------------------------------------------------------------------
# The payload
# --------------------------------------------------------------------------

def build_payload(db: DbSession, tenant: m.Tenant) -> sd.BillingPayload:
    """Everything the Billing page renders, derived from `plans` every time.

    Nothing here is cached and nothing is stored: if the owner changes a price
    in `plans.py`, the next request shows the new one. A price copied into the
    database at signup is a price that goes stale silently, and that is the
    class of bug this whole design is arranged to avoid.
    """
    sub = billing.get(db, tenant.id)
    # A placeholder row from a checkout nobody finished claims no plan: the
    # customer agreed to nothing, so no card may render as "Current plan"
    # (which would also disable the very button they need to retry with).
    current_plan = sub.plan if sub and not billing.is_uncommitted(sub) else ""

    cards = [
        sd.BillingPlanCard(
            id=p.id,
            name=p.name,
            blurb=p.blurb,
            monthly_cents=p.monthly_cents,
            yearly_cents=p.yearly_cents,
            monthly_label=plans.price_label(p, "month"),
            yearly_label=plans.price_label(p, "year"),
            per_site=p.per_site,
            is_trial=p.is_trial,
            current=p.id == current_plan,
        )
        for p in plans.PLANS
        if not platform_settings.trial_only() or p.is_trial
    ]

    sub_out = None
    if sub is not None:
        plan = plans.get(sub.plan)
        sub_out = sd.BillingSubscription(
            plan=sub.plan,
            plan_name=plan.name if plan else sub.plan,
            interval=sub.interval,
            status=sub.status,
            price_label=plans.price_label(plan, sub.interval) if plan else "",
            trial_end=_iso(sub.trial_end),
            trial_days_left=billing.trial_days_left(sub),
            current_period_end=_iso(sub.current_period_end),
            cancel_at_period_end=bool(sub.cancel_at_period_end),
            ingesting=billing.ingest_allowed(sub),
        )

    return sd.BillingPayload(
        trial_only=platform_settings.trial_only(),
        configured=settings.billing_configured(),
        currency=plans.CURRENCY,
        trial_days=platform_settings.trial_days(),
        yearly_months_charged=plans.YEARLY_MONTHS_CHARGED,
        plans=cards,
        subscription=sub_out,
        notice=billing.notice(sub),
        portal_available=bool(
            settings.billing_configured() and sub is not None and sub.stripe_customer_id
        ),
    )


@router.get("/billing", response_model=sd.BillingPayload)
def get_billing(db: DbSession = Depends(get_db), auth=ADMIN):
    _user, tenant, _role = auth
    return build_payload(db, tenant)


# --------------------------------------------------------------------------
# The trial
# --------------------------------------------------------------------------

@router.post("/billing/trial", response_model=sd.BillingPayload)
def start_trial(request: Request, db: DbSession = Depends(get_db), auth=ADMIN):
    """Claim the three free months. No card, no Stripe call, no conversion.

    409 when the tenant already has a subscription of any kind -- including a
    trial that has already ended. A second trial is not something this route
    hands out: `billing.start_trial` is idempotent, so the guard here exists
    to tell the admin why nothing happened rather than to protect the data.
    """
    user, tenant, role = auth
    existing = billing.get(db, tenant.id)
    # An abandoned checkout is not a subscription -- `billing.start_trial`
    # converts that placeholder rather than refusing, because losing the
    # trial by closing a Stripe tab would cost the whole funnel.
    if existing is not None and not billing.is_uncommitted(existing):
        raise HTTPException(409, "this account already has a subscription")
    try:
        sub = billing.start_trial(db, tenant.id)
    except IntegrityError:
        # Two admins clicked at the same moment; the unique index on
        # `tenant_id` decided it. Roll back and report the one that won.
        db.rollback()
        raise HTTPException(409, "this account already has a subscription") from None
    audit.log(db, tenant.id, user.username, role, "billing.trial_start",
              target=plans.TRIAL.id, ip=_ip(request),
              body={"trial_end": _iso(sub.trial_end)})
    db.commit()
    return build_payload(db, tenant)


# --------------------------------------------------------------------------
# Stripe Checkout and the customer portal
# --------------------------------------------------------------------------

@router.post("/billing/checkout", response_model=sc.CheckoutOut)
def checkout(body: sc.CheckoutIn, request: Request,
             db: DbSession = Depends(get_db), auth=ADMIN):
    """A hosted Stripe Checkout URL for the chosen plan and interval.

    The trial is not purchasable (422): it is claimed through
    `POST /billing/trial` and takes no payment method, so offering it here
    would mean opening a checkout for a zero-price subscription and putting a
    card on file for something that must never be charged.
    """
    user, tenant, role = auth
    if platform_settings.trial_only():
        raise HTTPException(409, "this hub offers the free trial only -- there are no plans to buy")
    plan = plans.get(body.plan)
    if plan is None or plan.is_trial:
        raise HTTPException(422, "unknown plan -- pick one of: "
                                 + ", ".join(p.id for p in plans.PAID_PLANS))
    if body.interval not in plans.INTERVALS:
        raise HTTPException(422, "interval must be 'month' or 'year'")
    if not settings.billing_configured():
        raise HTTPException(503, "billing is not configured on this hub")

    sub = billing.get(db, tenant.id)
    # A row has to exist before the redirect: `billing._tenant_for` finds the
    # account from it when the webhook lands, and a customer who finishes
    # checkout on their phone after closing the tab that started it must still
    # land somewhere. `incomplete` is what that row says until Stripe reports
    # otherwise -- fully monitored, no plan claimed, no "payment failed"
    # notice, and still convertible to the free trial, because closing the
    # Stripe tab is the ordinary case and must cost nothing.
    if sub is None:
        sub = m.Subscription(tenant_id=tenant.id, plan=plan.id,
                             interval=body.interval, status="incomplete")
        db.add(sub)
        db.commit()
    elif billing.is_uncommitted(sub):
        # They abandoned one checkout and started another, possibly for a
        # different plan. Keep the placeholder pointed at what they are
        # actually buying now.
        sub.plan = plan.id
        sub.interval = body.interval
        db.commit()

    # The site count Stripe is actually being asked to charge for. A flat plan
    # is one subscription however many sites the caller claims to have, so the
    # quantity it sends is pinned to 1 here rather than trusted; every use
    # below -- the line item, the idempotency key, the audit row -- has to be
    # this same number or they describe different purchases.
    qty = body.quantity if plan.per_site else 1

    try:
        price_id = stripe_api.price_id_for_lookup_key(plan.lookup_key(body.interval))
        session = stripe_api.create_checkout_session(
            price_id=price_id,
            quantity=qty,
            tenant_id=tenant.id,
            customer_id=sub.stripe_customer_id,
            customer_email=(user.email or ""),
            success_url=f"{_base_url()}/settings/billing?checkout=done",
            cancel_url=f"{_base_url()}/settings/billing?checkout=cancelled",
            # Same admin, same plan, same interval, same quantity, same UTC
            # hour => one session. The quantity belongs in this key because
            # Stripe does not merely ignore a repeat of it -- it replays the
            # *first* session's response verbatim and never looks at the new
            # parameters. On a per-site plan (`business_essentials`,
            # `partner`) the quantity is the price, so leaving it out is a
            # billing error, not a caching nicety: an admin who opens checkout
            # for 3 sites, closes the tab, and re-opens for 8 within the hour
            # would be handed the 3-site session back, pay it believing it is
            # the 8-site one, and only find out on the invoice. Keying on the
            # effective quantity rather than `body.quantity` is deliberate: a
            # flat plan sends 1 whatever the caller asked for, so its retries
            # stay collapsed, which is what the key is here for.
            idempotency_key=f"co:{tenant.id}:{plan.id}:{body.interval}:{qty}:"
                            f"{dt.datetime.now(dt.timezone.utc):%Y%m%d%H}",
        )
    except stripe_api.StripeNotConfigured:
        raise HTTPException(503, "billing is not configured on this hub") from None
    except stripe_api.StripeError as exc:
        # Stripe's own message is written for end users; the request that
        # produced it (which carries the Authorization header) is not logged.
        raise HTTPException(502, f"Stripe could not start the checkout: {exc}") from None

    url = session.get("url")
    if not isinstance(url, str) or not url:
        raise HTTPException(502, "Stripe did not return a checkout URL")

    audit.log(db, tenant.id, user.username, role, "billing.checkout_start",
              target=f"{plan.id}/{body.interval}", ip=_ip(request),
              body={"quantity": qty})
    db.commit()
    return sc.CheckoutOut(url=url)


@router.post("/billing/portal", response_model=sc.PortalOut)
def portal(request: Request, db: DbSession = Depends(get_db), auth=ADMIN):
    """A Stripe customer-portal URL: change plan, update card, cancel.

    Cancelling deliberately happens in Stripe rather than through a route
    here: one authority on subscription state, and it is the one that also
    knows about invoices, proration and dunning.
    """
    user, tenant, role = auth
    if platform_settings.trial_only():
        raise HTTPException(409, "this hub offers the free trial only -- there is no billing portal")
    if not settings.billing_configured():
        raise HTTPException(503, "billing is not configured on this hub")
    sub = billing.get(db, tenant.id)
    if sub is None or not sub.stripe_customer_id:
        raise HTTPException(409, "this account has no Stripe customer yet -- "
                                 "start a subscription first")
    try:
        session = stripe_api.create_portal_session(
            customer_id=sub.stripe_customer_id,
            return_url=f"{_base_url()}/settings/billing",
        )
    except stripe_api.StripeNotConfigured:
        raise HTTPException(503, "billing is not configured on this hub") from None
    except stripe_api.StripeError as exc:
        raise HTTPException(502, f"Stripe could not open the portal: {exc}") from None
    url = session.get("url")
    if not isinstance(url, str) or not url:
        raise HTTPException(502, "Stripe did not return a portal URL")
    audit.log(db, tenant.id, user.username, role, "billing.portal_open",
              target=sub.plan, ip=_ip(request))
    db.commit()
    return sc.PortalOut(url=url)


# --------------------------------------------------------------------------
# The webhook
# --------------------------------------------------------------------------

@router.post("/billing/stripe/webhook", include_in_schema=False)
async def stripe_webhook(request: Request, db: DbSession = Depends(get_db)):
    """Stripe's callback. Unauthenticated, internet-facing, hostile by default.

    Order of business, and every step of it matters:

    1. Rate-limit, so an unsigned flood costs the hub as little as possible.
    2. Read the **raw body**. The signature covers those exact bytes; no
       pydantic model is declared on this route because FastAPI would parse
       and re-serialize, and the MAC would then never match.
    3. Verify the signature and the timestamp (`stripe_api.verify_signature`):
       any `v1` in the header may match (secret rotation), the window is
       `GUARDIAN_STRIPE_WEBHOOK_TOLERANCE` seconds, and a hub with no signing
       secret rejects everything rather than accepting unsigned events.
    4. Apply it exactly once (`billing.apply_event`), which records the event
       id first and refuses both duplicates and out-of-order deliveries.

    Answers 200 for anything it has handled OR already handled, so Stripe's
    retries stop. 400 only for a signature that does not verify -- which
    Stripe never produces, so a 400 here means somebody else is posting.
    """
    if not ratelimit.allow("stripe_webhook", *WEBHOOK_GLOBAL):
        raise HTTPException(429, "too many requests")

    raw = await request.body()
    try:
        event = stripe_api.verify_signature(
            raw, request.headers.get("stripe-signature") or "")
    except stripe_api.SignatureError as exc:
        # The reason is safe to return: it names which check failed, never
        # any part of the secret, and Stripe's own CLI shows the same.
        raise HTTPException(400, str(exc)) from None

    result = billing.apply_event(db, event)
    return {
        "ok": True,
        "handled": result.handled,
        "duplicate": result.duplicate,
        "stale": result.stale,
        "detail": result.detail,
    }
