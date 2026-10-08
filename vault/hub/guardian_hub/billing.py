"""Subscription state for a tenant: the trial lifecycle, the Stripe webhook
reducer, and the one function that decides what a non-paying account loses.

Read `plans.py` first -- every price comes from there. This module holds no
numbers, only state transitions.

The two rules that shape everything here
----------------------------------------

**A billing problem never stops the watchdog.** `past_due` -- a paid plan
whose card was declined -- changes nothing about what Vault does. The
sensor keeps reporting, the alerts keep firing, the automatic repairs keep
running. The account sees a banner and gets email from Stripe. Cutting
monitoring off mid-incident because a renewal failed would turn a billing
annoyance into an outage nobody was watching, and no amount of revenue
protection is worth that.

**A trial that ends is a different thing entirely.** It was never paid for,
no card was ever taken, and it cannot auto-charge -- so at the end of three
months the account `paused`s: everything already collected stays readable and
exportable, nothing is deleted, and the ONLY thing that stops is fresh sensor
ingest (`ingest_allowed` below). That is the difference between "we could not
collect from you" and "you never agreed to pay us", and only the second one
justifies switching the collection off.

Nothing in this module deletes anything, ever.
"""

from __future__ import annotations

import datetime as dt

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session as DbSession

from . import models as m, plans, platform_settings
from .config import settings

# How far ahead of the trial's end the in-app notice starts. Two weeks: long
# enough to talk to somebody about a purchase order, short enough that the
# banner still means something when it appears.
#
# This is the DEFAULT. The live value is `platform_settings.trial_warn_days()`,
# which the owner console can change without a deploy -- so nothing may read
# this constant on a code path that is supposed to honour that setting. It was
# previously a default ARGUMENT on `trials_to_warn`, which is evaluated once at
# import and would therefore have ignored every edit.
TRIAL_WARN_DAYS = 14

#: Statuses in which the hub accepts new sensor data. `past_due` is in here
#: on purpose -- see the module docstring. So is `incomplete`: an abandoned
#: checkout is not a reason to stop watching somebody's network.
INGESTING_STATUSES = frozenset({"incomplete", "trialing", "active", "past_due"})

#: Statuses that do not represent an agreement to pay for anything. A row in
#: one of these claims no current plan, and can still be converted into the
#: free trial -- closing the Stripe tab must not cost somebody the trial,
#: which is the entire funnel.
UNCOMMITTED_STATUSES = frozenset({"incomplete"})

#: Statuses in which the account is read-only: data stays, collection stops.
PAUSED_STATUSES = frozenset({"paused", "canceled"})

# Stripe subscription.status -> Vault status. `unpaid` maps to `past_due`
# rather than to a cut-off of its own: by the time Stripe says `unpaid` it has
# already given up retrying, and the customer still gets monitored while a
# human sorts it out. `paused` is deliberately absent from this map -- it is
# only ever reached by a trial expiring, never by Stripe.
_STRIPE_STATUS = {
    "active": "active",
    "trialing": "active",       # a Stripe-side trial is still a *paid* plan
    "past_due": "past_due",
    "unpaid": "past_due",
    "incomplete": "past_due",
    "incomplete_expired": "canceled",
    "canceled": "canceled",
    "paused": "paused",
}


def _now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def _ts(value: object) -> dt.datetime | None:
    """A Stripe unix timestamp as an aware UTC datetime, or None."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if value <= 0:
        return None
    return dt.datetime.fromtimestamp(int(value), dt.timezone.utc)


# --------------------------------------------------------------------------
# Reading
# --------------------------------------------------------------------------

def get(db: DbSession, tenant_id: str) -> m.Subscription | None:
    return db.query(m.Subscription).filter_by(tenant_id=tenant_id).first()


def is_uncommitted(sub: m.Subscription | None) -> bool:
    """True for a placeholder row from a checkout nobody finished.

    Such a row exists only so the webhook can find the tenant if the customer
    completes the purchase later on another device. It has no Stripe
    subscription behind it, so it is not a subscription in any sense the
    customer would recognise, and every surface treats it as "none yet".
    """
    return (sub is not None
            and sub.status in UNCOMMITTED_STATUSES
            and not sub.stripe_subscription_id)


def ingest_allowed(sub: m.Subscription | None) -> bool:
    """May this tenant's sensors still store what they collected?

    A tenant with **no** subscription row may: every tenant the owner set up
    by hand before billing existed is in that state, and a migration is not
    an acceptable way to switch somebody's monitoring off.
    """
    if sub is None:
        return True
    return sub.status in INGESTING_STATUSES


def trial_days_left(sub: m.Subscription | None, now: dt.datetime | None = None) -> int | None:
    """Whole days until the trial ends; 0 on the last day. None if not on trial."""
    if sub is None or sub.status != "trialing" or sub.trial_end is None:
        return None
    delta = sub.trial_end - (now or _now())
    return max(0, delta.days)


def notice(sub: m.Subscription | None, now: dt.datetime | None = None) -> str:
    """One sentence for the in-app banner, or "" when there is nothing to say.

    Deliberately plain: this is the only place the product nags about money,
    and it says what happens next rather than pressing.
    """
    if sub is None or is_uncommitted(sub):
        # Nothing to say about a checkout that was closed rather than
        # finished. Saying "the last payment did not go through" about a
        # payment that was never attempted is a false statement about money.
        return ""
    if sub.status == "trialing":
        left = trial_days_left(sub, now)
        if left is None or left > platform_settings.trial_warn_days():
            return ""
        # On a trial-only hub there is no plan to pick, so the banner says how
        # it actually gets sorted out -- by replying to the email -- instead of
        # pointing at a chooser that is not there.
        how = ("Reply to the email we have sent you if you want to keep going"
               if platform_settings.trial_only() else "Pick a plan to keep collecting new data")
        if left == 0:
            return f"Your free trial ends today. {how} -- everything already collected stays either way."
        day = "day" if left == 1 else "days"
        return (f"Your free trial ends in {left} {day}. {how} -- nothing is charged "
                f"automatically and no card is on file.")
    if sub.status == "paused":
        resume = ("reply to the email we sent and collection resumes"
                  if platform_settings.trial_only() else "choose a plan and collection resumes")
        return ("Your free trial has ended, so the sensors have stopped reporting. "
                f"Everything already collected is still here and still exportable -- {resume}.")
    if sub.status == "past_due":
        return ("The last payment did not go through. Monitoring is unaffected and "
                "carries on as normal -- update your card in the billing portal when "
                "you get a moment.")
    if sub.status == "canceled":
        return ("This subscription is cancelled. Your data is still here and still "
                "exportable; choose a plan to start collecting again.")
    if sub.status == "active" and sub.cancel_at_period_end:
        return ("This subscription is set to end at the end of the current period. "
                "Monitoring runs normally until then.")
    return ""


# --------------------------------------------------------------------------
# The trial
# --------------------------------------------------------------------------

def start_trial(db: DbSession, tenant_id: str, *, days: int | None = None,
                now: dt.datetime | None = None) -> m.Subscription:
    """Put a tenant on the free trial. Creates NO Stripe objects.

    No customer, no subscription, no `trial_period_days` on anything Stripe
    can see -- which is the whole point. A Stripe subscription with a trial
    attached exists to convert into a charge when the trial ends, and this
    trial must not convert into anything; it also means the trial works
    identically on a hub with no Stripe keys at all.

    Idempotent: a tenant that already has a subscription of any kind is
    returned unchanged. Re-running this must never hand somebody a second
    three months, and must never overwrite a paid plan with a trial.
    """
    at = now or _now()
    existing = get(db, tenant_id)
    if existing is not None:
        if not is_uncommitted(existing):
            return existing
        # An abandoned checkout is not a subscription. Convert the placeholder
        # in place (rather than adding a second row, which the unique index
        # would refuse anyway) so somebody who changed their mind at Stripe's
        # page can still claim the three free months.
        existing.plan = plans.TRIAL.id
        existing.interval = "none"
        existing.status = "trialing"
        existing.trial_end = at + dt.timedelta(
            days=platform_settings.trial_days() if days is None else days)
        existing.updated = at
        db.commit()
        return existing
    sub = m.Subscription(
        tenant_id=tenant_id,
        plan=plans.TRIAL.id,
        interval="none",
        status="trialing",
        trial_end=at + dt.timedelta(
            days=platform_settings.trial_days() if days is None else days),
        created=at,
        updated=at,
    )
    db.add(sub)
    db.commit()
    return sub


class StripeOwnsThis(RuntimeError):
    """The owner tried to set a plan or trial on a customer Stripe is billing.

    Refused rather than applied, because `apply_event` writes `plan`,
    `interval` and `status` from Stripe's own events: a console edit to one
    of those rows would hold only until the next webhook, and the ledger
    would be wrong in between without anyone being told. The refusal carries
    the place to go instead."""

    def __init__(self, sub: m.Subscription):
        self.dashboard_url = stripe_dashboard_url(sub.stripe_customer_id)
        super().__init__(
            f"This customer is billed by Stripe ({sub.stripe_subscription_id}); "
            f"change their plan there: {self.dashboard_url}")


def stripe_dashboard_url(customer_id: str) -> str:
    """Where this customer lives in the Stripe dashboard -- under /test/ when
    the hub's key is a test key, since a link into the wrong mode is a hunt."""
    from .config import settings
    if not customer_id:
        return ""
    mode = "test/" if settings.stripe_secret_key.strip().startswith("sk_test_") else ""
    return f"https://dashboard.stripe.com/{mode}customers/{customer_id}"


def stripe_is_billing(sub: m.Subscription | None) -> bool:
    """True while Stripe holds a live subscription for this row. `canceled`
    still carries the old ids but Stripe is no longer charging anyone, so the
    customer is Vault's again and may be put back on a trial."""
    return bool(sub and sub.stripe_subscription_id and sub.status in ("active", "past_due"))


def extend_trial(db: DbSession, tenant_id: str, *, days: int,
                 now: dt.datetime | None = None) -> m.Subscription:
    """Give a customer `days` more of the free trial, from the console.

    Counts from the later of now and the current trial end: a trial still
    running keeps what it has and gains `days`; a trial that lapsed (`paused`)
    starts over from today, because "give them two more weeks" means two
    weeks of service, not two weeks minus however long they sat paused.
    Re-arms the ending-soon letter, since a longer trial has a new two-weeks-
    left. Also the way a comped plan is taken away: back to a trial.

    Creates no Stripe objects, like `start_trial`. Refuses a customer Stripe
    is billing -- see `StripeOwnsThis`. Commits.
    """
    if not 1 <= int(days) <= 365:
        raise ValueError("days must be between 1 and 365")
    now = now or _now()
    sub = get(db, tenant_id)
    if stripe_is_billing(sub):
        raise StripeOwnsThis(sub)
    if sub is None:
        sub = start_trial(db, tenant_id, days=int(days), now=now)
        return sub
    base = sub.trial_end if (sub.trial_end and sub.trial_end > now and sub.status == "trialing") else now
    sub.status, sub.plan, sub.interval = "trialing", "trial", "none"
    sub.trial_end = base + dt.timedelta(days=int(days))
    sub.trial_warned = None
    sub.current_period_end = None
    sub.cancel_at_period_end = False
    db.commit()
    return sub


def grant_plan(db: DbSession, tenant_id: str, *, plan: str, interval: str) -> m.Subscription:
    """Put a customer on a paid plan with nothing to pay, from the console:
    a partner arrangement, goodwill, an account invoiced by hand.

    It is Vault's own record, like the trial -- no Stripe customer, no
    subscription, and so no charge and no revenue: the ledger counts a
    comped plan at zero because `payloads` price only statuses Stripe is
    collecting for. Refuses a customer Stripe is billing (see
    `StripeOwnsThis`); the trial is not grantable here, `extend_trial` is
    how a trial is given. Commits.
    """
    from . import plans
    p = plans.get(plan)
    if p is None or p.is_trial:
        raise ValueError("plan must be one of: " + ", ".join(x.id for x in plans.PAID_PLANS))
    if interval not in plans.INTERVALS:
        raise ValueError("interval must be 'month' or 'year'")
    sub = get(db, tenant_id)
    if stripe_is_billing(sub):
        raise StripeOwnsThis(sub)
    if sub is None:
        sub = m.Subscription(tenant_id=tenant_id)
        db.add(sub)
    sub.plan, sub.interval, sub.status = p.id, interval, "active"
    sub.trial_end = None
    sub.trial_warned = None
    sub.current_period_end = None
    sub.cancel_at_period_end = False
    db.commit()
    return sub


def expire_trials(db: DbSession, now: dt.datetime | None = None) -> list[m.Subscription]:
    """Move every elapsed trial to `paused`. Returns the rows it moved.

    The rows, not a count, because the `trialing -> paused` transition is the
    only latch there is on the end-of-trial email: the caller mails exactly
    what this pass moved, and a second pass moves nothing, so nobody is
    mailed twice. Counting would force the caller to re-query "who is
    paused", which is every paused tenant, every hour, forever.

    Runs from the scheduler (hourly) and is safe to run as often as you like:
    it only ever touches rows that are still `trialing` with an elapsed
    `trial_end`, so a second pass in the same hour finds nothing.

    Nothing is deleted and nothing is charged. `paused` is defined in
    `models.Subscription`: data readable, ingest refused.
    """
    at = now or _now()
    rows = (db.query(m.Subscription)
              .filter(m.Subscription.status == "trialing",
                      m.Subscription.trial_end.isnot(None),
                      m.Subscription.trial_end <= at)
              .all())
    for sub in rows:
        sub.status = "paused"
        sub.updated = at
    if rows:
        db.commit()
    return rows


def trials_to_warn(db: DbSession, now: dt.datetime | None = None,
                   within_days: int | None = None) -> list[m.Subscription]:
    """Live trials ending within `within_days` that have not been warned yet.

    The `trial_warned` latch is stamped by `mark_trial_warned`, so this list
    empties itself as the caller works through it.
    """
    at = now or _now()
    # Read at call time, never as a default argument: a default is bound once
    # at import, so an owner who shortened the warning window would have
    # watched it save and changed nothing.
    if within_days is None:
        within_days = platform_settings.trial_warn_days()
    return (db.query(m.Subscription)
              .filter(m.Subscription.status == "trialing",
                      m.Subscription.trial_warned.is_(None),
                      m.Subscription.trial_end.isnot(None),
                      m.Subscription.trial_end > at,
                      m.Subscription.trial_end <= at + dt.timedelta(days=within_days))
              .all())


def trial_recipients(db: DbSession, tenant_id: str) -> list[str]:
    """Who to write to about this tenant's trial, best address first.

    The profile address the owner typed into Settings is the one they expect
    billing mail at; failing that, the admins who can actually act on it.
    Viewers and operators are deliberately not written to about money.
    """
    out: list[str] = []
    tenant = db.query(m.Tenant).filter_by(id=tenant_id).first()
    if tenant is not None:
        profile = (tenant.settings or {}).get("profile") or {}
        addr = (profile.get("email") or "").strip()
        if addr:
            out.append(addr)
    admins = (db.query(m.User.email)
                .join(m.Membership, m.Membership.user_id == m.User.id)
                .filter(m.Membership.tenant_id == tenant_id,
                        m.Membership.role == "admin")
                .all())
    for (addr,) in admins:
        addr = (addr or "").strip()
        if addr and addr not in out:
            out.append(addr)
    return out


def mark_trial_warned(db: DbSession, sub: m.Subscription,
                      now: dt.datetime | None = None) -> bool:
    """Record that the tenant has been shown the end-of-trial notice once.

    Returns True the first time only, so a caller that wants to send exactly
    one out-of-app nudge (email, ntfy) can use it as a latch. The in-app
    banner in `notice()` does not consult this -- a banner should keep showing
    for as long as it is true.
    """
    if sub.trial_warned is not None:
        return False
    sub.trial_warned = now or _now()
    db.commit()
    return True


# --------------------------------------------------------------------------
# The webhook reducer
# --------------------------------------------------------------------------

class Applied:
    """What `apply_event` did, for the route's response and the audit line."""

    __slots__ = ("handled", "duplicate", "stale", "tenant_id", "detail")

    def __init__(self, *, handled: bool = False, duplicate: bool = False,
                 stale: bool = False, tenant_id: str | None = None, detail: str = ""):
        self.handled = handled
        self.duplicate = duplicate
        self.stale = stale
        self.tenant_id = tenant_id
        self.detail = detail


def _tenant_for(db: DbSession, obj: dict, event: dict) -> m.Subscription | None:
    """Find the subscription an event is about, by every id Stripe gives us.

    Three routes to it, in decreasing reliability: the subscription id we
    already stored, the tenant id we put in metadata when the checkout was
    created, and the customer id. An event that matches none of them belongs
    to some other system sharing the Stripe account, and is recorded as seen
    and otherwise ignored -- never used to create a subscription row, because
    a tenant id we cannot verify is not a tenant.
    """
    sub_id = obj.get("subscription") if isinstance(obj.get("subscription"), str) else None
    if not sub_id and event.get("type", "").startswith("customer.subscription."):
        sub_id = obj.get("id") if isinstance(obj.get("id"), str) else None
    if sub_id:
        row = db.query(m.Subscription).filter_by(stripe_subscription_id=sub_id).first()
        if row:
            return row

    meta = obj.get("metadata") if isinstance(obj.get("metadata"), dict) else {}
    tid = meta.get("tenant_id") or obj.get("client_reference_id")
    if isinstance(tid, str) and tid:
        row = get(db, tid)
        if row:
            return row

    cust = obj.get("customer")
    if isinstance(cust, str) and cust:
        row = db.query(m.Subscription).filter_by(stripe_customer_id=cust).first()
        if row:
            return row
    return None


def _plan_from_items(obj: dict) -> tuple[str, str]:
    """(plan_id, interval) from a Stripe subscription's first line item.

    Read off the price's `lookup_key`, which `plans.Plan.lookup_key()`
    minted, so the mapping back from Stripe to a Vault plan needs no
    second table. Anything unrecognised leaves the stored plan alone (returns
    ("", "")) rather than guessing -- a wrong plan id on the account is worse
    than a stale one, because it is what the UI shows the customer they bought.
    """
    items = ((obj.get("items") or {}).get("data") or []) if isinstance(obj.get("items"), dict) else []
    for item in items:
        price = item.get("price") if isinstance(item, dict) else None
        if not isinstance(price, dict):
            continue
        key = price.get("lookup_key")
        if isinstance(key, str) and key.startswith("guardian_"):
            for interval in plans.INTERVALS:
                suffix = f"_{interval}"
                if key.endswith(suffix):
                    plan_id = key[len("guardian_"):-len(suffix)]
                    if plans.get(plan_id):
                        return plan_id, interval
        recurring = price.get("recurring") if isinstance(price.get("recurring"), dict) else {}
        interval = recurring.get("interval")
        if interval in plans.INTERVALS:
            return "", interval
    return "", ""


def _item_period_end(obj: dict) -> object:
    """`current_period_end` as newer Stripe API versions render it.

    Older payloads carry it on the subscription; from 2025-ish it moved onto
    each line item (`items.data[0].current_period_end`). Which shape arrives
    is decided by the API version configured on the WEBHOOK ENDPOINT, not by
    the `Stripe-Version` header this hub sends on its outbound calls -- so
    both are read, and the caller takes whichever is present. Getting this
    wrong costs a renewal date on the Billing page, silently.
    """
    items = ((obj.get("items") or {}).get("data") or []) if isinstance(obj.get("items"), dict) else []
    for item in items:
        if isinstance(item, dict) and item.get("current_period_end") is not None:
            return item["current_period_end"]
    return None


def apply_event(db: DbSession, event: dict) -> Applied:
    """Fold one verified Stripe event into the subscription table.

    Called only with an event whose signature already verified. Two guards,
    for two different failure modes Stripe genuinely produces:

    * **Duplicates.** Stripe retries for days. The event id is inserted into
      `billing_event` first; a collision means "already applied", and the
      route answers 200 so the retries stop. A 4xx/5xx here would have Stripe
      redelivering a handled event indefinitely.
    * **Out-of-order delivery.** Stripe does not promise order. An event
      older than the newest one already applied to that subscription is
      recorded and dropped, so a late `payment_failed` cannot un-do the
      `payment_succeeded` that followed it.
    """
    event_id = event.get("id")
    if not isinstance(event_id, str) or not event_id:
        return Applied(detail="event has no id")
    etype = str(event.get("type") or "")
    created = int(event.get("created") or 0)

    if db.get(m.BillingEvent, event_id) is not None:
        return Applied(duplicate=True, detail=f"{etype} already applied")

    obj = ((event.get("data") or {}).get("object") or {})
    if not isinstance(obj, dict):
        obj = {}

    row = _tenant_for(db, obj, event)
    # Recorded before any state change, and committed even when nothing else
    # happens: an event this hub has *seen* must never be reprocessed, even
    # if it turned out to belong to nobody.
    db.add(m.BillingEvent(id=event_id, type=etype, created=created,
                          tenant_id=row.tenant_id if row else None))

    if row is None:
        try:
            db.commit()
        except IntegrityError:
            db.rollback()
            return Applied(duplicate=True, detail=f"{etype} already applied")
        return Applied(handled=False, detail=f"{etype}: no matching tenant")

    # `checkout.session.completed` neither consults this watermark nor
    # advances it, and that exemption is the whole point of it being here.
    #
    # The guard exists so an OLD subscription snapshot cannot overwrite a
    # newer one -- a late `customer.subscription.updated` must not resurrect
    # a status that has since changed. But it compared `created` across every
    # event type against one watermark, and Stripe stamps the session AFTER
    # the subscription it creates: measured on a live sandbox checkout,
    # subscription.created at 1790013891 and session.completed at 1790013892.
    # Deliver the session first -- which Stripe may, it guarantees no order --
    # and the subscription event, the only one carrying status, plan, interval
    # and period end, was dropped as stale. The account stayed `incomplete`
    # while every webhook answered 200 and Stripe showed it active and paid.
    #
    # The session is exempt because it carries no subscription STATE: it
    # records the customer and subscription ids and nothing else, so applying
    # it early, late or twice reaches the same row. Only the events that set
    # `status` need ordering among themselves, and they still have it.
    orders = etype != "checkout.session.completed"
    if orders and created and row.last_event_created and created < row.last_event_created:
        try:
            db.commit()
        except IntegrityError:
            db.rollback()
            return Applied(duplicate=True, tenant_id=row.tenant_id,
                           detail=f"{etype} already applied")
        return Applied(stale=True, tenant_id=row.tenant_id,
                       detail=f"{etype} predates the last applied event")

    detail = _reduce(row, etype, obj)
    if orders and created:
        row.last_event_created = max(row.last_event_created or 0, created)
    row.updated = _now()
    try:
        db.commit()
    except IntegrityError:
        # Two of Stripe's retries arrived at once and both got past the
        # `db.get` above; the primary key on `billing_event` decided it. The
        # other one applied the change, so this is a duplicate, not a
        # failure -- and it must answer 200, or Stripe retries forever.
        db.rollback()
        return Applied(duplicate=True, tenant_id=row.tenant_id,
                       detail=f"{etype} already applied")
    return Applied(handled=True, tenant_id=row.tenant_id, detail=detail)


def _reduce(row: m.Subscription, etype: str, obj: dict) -> str:
    """The state machine. Mutates `row`; the caller commits."""
    if etype == "checkout.session.completed":
        cust = obj.get("customer")
        sub_id = obj.get("subscription")
        if isinstance(cust, str) and cust:
            row.stripe_customer_id = cust
        if isinstance(sub_id, str) and sub_id:
            row.stripe_subscription_id = sub_id
        # The plan and period arrive on the `customer.subscription.created`
        # event that follows; all this one can say for certain is that the
        # customer got through checkout. Status is not forced to `active`
        # here -- an unconfirmed SEPA/3DS payment is `incomplete` at this
        # point, and claiming otherwise would show a paid dashboard for a
        # payment that has not happened.
        return "checkout completed"

    if etype in ("customer.subscription.created", "customer.subscription.updated",
                 "customer.subscription.resumed"):
        sub_id = obj.get("id")
        if isinstance(sub_id, str) and sub_id:
            row.stripe_subscription_id = sub_id
        cust = obj.get("customer")
        if isinstance(cust, str) and cust:
            row.stripe_customer_id = cust
        plan_id, interval = _plan_from_items(obj)
        if plan_id:
            row.plan = plan_id
        if interval:
            row.interval = interval
        status = _STRIPE_STATUS.get(str(obj.get("status") or ""))
        if status:
            row.status = status
        row.cancel_at_period_end = bool(obj.get("cancel_at_period_end"))
        end = _ts(obj.get("current_period_end")) or _ts(_item_period_end(obj))
        if end:
            row.current_period_end = end
        # A tenant that has bought something is no longer on a trial, so the
        # trial's end date stops being meaningful -- cleared so no stale
        # countdown can surface on a paid account.
        if row.status in ("active", "past_due"):
            row.trial_end = None
        return f"subscription {row.status}"

    if etype == "customer.subscription.deleted":
        row.status = "canceled"
        row.cancel_at_period_end = False
        return "subscription cancelled"

    if etype == "invoice.payment_failed":
        # Only ever from a paying state: a `paused` trial has no invoices, and
        # a late-arriving failure must not drag a re-subscribed account back.
        if row.status == "active":
            row.status = "past_due"
        return "payment failed"

    if etype in ("invoice.payment_succeeded", "invoice.paid"):
        if row.status == "past_due":
            row.status = "active"
        end = _ts(((obj.get("lines") or {}).get("data") or [{}])[0].get("period", {}).get("end")
                  if isinstance(obj.get("lines"), dict) else None)
        if end:
            row.current_period_end = end
        return "payment succeeded"

    return f"{etype} ignored"
