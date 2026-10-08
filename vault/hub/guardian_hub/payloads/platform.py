"""The owner's ledger: every tenant on the platform, on one screen.

This is the only payload in the app that deliberately crosses tenant
boundaries, so it lives behind `require_superadmin` and nothing else reads
it. Everything else Vault serves is scoped to one tenant by construction;
this is the product owner looking at the business rather than at a network.

Written as a handful of grouped queries rather than a loop over tenants: with
one query per tenant the page would issue 6N round trips and slow down as the
business grows, which is the wrong way round for a page whose whole purpose is
to stay readable as the list gets longer.
"""
import datetime as dt

from sqlalchemy import func
from sqlalchemy.orm import Session as DbSession

from .. import billing, models as m, plans, platform_settings
from ..config import settings
from ..networks import SENSOR_STALE_S
from ..schemas_dash import PlatformPayload, PlatformTenantRow, PlatformTotals

#: Subscription statuses that CAN be charged. `past_due` is in here on
#: purpose -- see the note at its use site. Status alone is not the test,
#: though: a plan the owner granted from the console is `active` with no
#: Stripe subscription behind it, and books nothing. `billing.stripe_is_billing`
#: is the one predicate -- status in this set AND a live Stripe subscription --
#: and both this ledger and `payloads/account` price through it, so the two
#: pages the owner reads seconds apart cannot disagree about who is paying.
_PAYING = frozenset({"active", "past_due"})

#: A trial this close to its end is worth the owner's attention now. Same
#: window `billing.TRIAL_WARN_DAYS` uses for the customer's own warning, so
#: the ledger lights up exactly when the letter goes out, not before.
#: The default only. Read the live value through `platform_settings`: this was
#: a module-level constant bound at import, so an owner who changed the warning
#: window in the console would have seen the ledger keep counting "ending soon"
#: by the old number.
ENDING_SOON_DAYS = billing.TRIAL_WARN_DAYS


def _iso(value: dt.datetime | None) -> str:
    return value.isoformat() if value else ""


def _per_site(plan_id: str) -> bool:
    """Is this plan priced per monitored site?

    Asked of `plans` rather than hard-coded here, so adding a per-site plan
    does not silently start reporting its customers at one site.

    This is a FLAG, not a multiplier. The hub genuinely does not know how many
    sites a customer bought: `plans.Plan.per_site` says so in as many words --
    "the hub does not meter sites, Stripe's quantity does" -- and the
    `subscription` table has no quantity column, because the quantity is
    chosen at checkout and lives on the Stripe subscription item. The nearest
    thing in the schema, `models.Site`, is a monitored URL rather than a
    location, so a dental practice watching twelve pages from one office would
    price as twelve offices.

    So a per-site plan is reported at its single-site price and the row says
    the figure is a floor. An understated number that is labelled is
    recoverable; a plausible number that is wrong is not, and a revenue figure
    is exactly the kind a reader stops checking once it has looked right once.
    """
    p = plans.get(plan_id)
    return bool(p and p.per_site)


def build_payload(db: DbSession) -> PlatformPayload:
    now = dt.datetime.now(dt.timezone.utc)
    fresh_since = now - dt.timedelta(seconds=SENSOR_STALE_S)
    # The live warning window, so "ending soon" on the ledger means the same
    # number of days the customer's own banner is counting.
    ending_soon_days = platform_settings.trial_warn_days()

    tenants = db.query(m.Tenant).order_by(m.Tenant.name).all()

    # --- grouped counts, one query each ---------------------------------
    sensors_total: dict[str, int] = dict(
        db.query(m.Sensor.tenant_id, func.count(m.Sensor.id))
          .filter(m.Sensor.status != "revoked").group_by(m.Sensor.tenant_id).all())
    sensors_fresh: dict[str, int] = dict(
        db.query(m.Sensor.tenant_id, func.count(m.Sensor.id))
          .filter(m.Sensor.status == "active", m.Sensor.last_seen.isnot(None),
                  m.Sensor.last_seen >= fresh_since)
          .group_by(m.Sensor.tenant_id).all())
    last_scan: dict[str, dt.datetime] = dict(
        db.query(m.Sensor.tenant_id, func.max(m.Sensor.last_seen))
          .group_by(m.Sensor.tenant_id).all())
    devices: dict[str, int] = dict(
        db.query(m.Device.tenant_id, func.count(m.Device.id))
          .group_by(m.Device.tenant_id).all())
    users: dict[str, int] = dict(
        db.query(m.Membership.tenant_id, func.count(m.Membership.id))
          .group_by(m.Membership.tenant_id).all())
    # Open alerts, split by severity in one pass.
    sev_rows = (db.query(m.Alert.tenant_id, m.Alert.sev, func.count(m.Alert.id))
                  .filter(m.Alert.status == "open", m.Alert.sev.in_(["WARN", "CRIT"]))
                  .group_by(m.Alert.tenant_id, m.Alert.sev).all())
    warn: dict[str, int] = {}
    crit: dict[str, int] = {}
    for tid, sev, n in sev_rows:
        (crit if sev == "CRIT" else warn)[tid] = n
    subs: dict[str, m.Subscription] = {s.tenant_id: s for s in db.query(m.Subscription).all()}

    rows: list[PlatformTenantRow] = []

    paying_ids: set[str] = set()
    ending_soon = 0
    for t in tenants:
        sub = subs.get(t.id)
        left = billing.trial_days_left(sub, now) if sub else None
        if left is not None and left <= ending_soon_days:
            ending_soon += 1
        # Only a subscription being CHARGED contributes revenue. `trialing`
        # pays nothing yet, `paused` and `canceled` pay nothing any more, and
        # `incomplete` is the placeholder row a checkout writes before anyone
        # has paid -- counting that one would book revenue for a customer who
        # closed the Stripe tab, which is the failure mode this guard exists
        # for. `past_due` DOES count: the subscription is live and the money
        # is owed; writing it off the moment a card bounces would make the
        # figure jump around on retries that usually succeed.
        paying_now = billing.stripe_is_billing(sub)
        mrr = plans.monthly_cents(sub.plan, sub.interval) if paying_now else 0
        if paying_now:
            paying_ids.add(t.id)
        rows.append(PlatformTenantRow(
            id=t.id, slug=t.slug, name=t.name,
            plan=(sub.plan if sub else ""),
            plan_status=(sub.status if sub else ""),
            interval=(sub.interval if sub else ""),
            per_site=_per_site(sub.plan) if sub else False,
            mrr_cents=mrr,
            trial_ends=_iso(sub.trial_end) if sub else "",
            trial_days_left=left,
            ingesting=billing.ingest_allowed(sub),
            sensors_total=sensors_total.get(t.id, 0),
            sensors_online=sensors_fresh.get(t.id, 0),
            last_scan=_iso(last_scan.get(t.id)),
            devices=devices.get(t.id, 0),
            users=users.get(t.id, 0),
            open_warn=warn.get(t.id, 0),
            open_crit=crit.get(t.id, 0),
            created=_iso(t.created),
        ))

    totals = PlatformTotals(
        tenants=len(rows),
        sensors_online=sum(r.sensors_online for r in rows),
        sensors_total=sum(r.sensors_total for r in rows),
        open_crit=sum(r.open_crit for r in rows),
        open_warn=sum(r.open_warn for r in rows),
        trials_ending=ending_soon,
        open_requests=db.query(func.count(m.Lead.id)).filter(m.Lead.status == "pending").scalar() or 0,
        open_tickets=db.query(func.count(m.Ticket.id)).filter(m.Ticket.status == "open").scalar() or 0,
        paused=sum(1 for r in rows if r.plan_status == "paused"),
        mrr_cents=sum(r.mrr_cents for r in rows),
        paying=sum(1 for r in rows if r.id in paying_ids),
        past_due=sum(1 for r in rows if r.plan_status == "past_due"),
        mrr_is_floor=any(r.per_site and r.mrr_cents > 0 for r in rows),
    )
    return PlatformPayload(
        totals=totals,
        tenants=rows,
        # Read-only facts about how the platform itself is wired, so the owner
        # can see at a glance whether mail and chat are actually working
        # without opening a shell. Never the values themselves.
        mail_configured=bool(settings.smtp_host),
        billing_mode=platform_settings.billing_mode(),
        auto_heal=platform_settings.auto_heal(),
        base_url=settings.base_url,
    )


def build_trends(db: DbSession, days: int = 30) -> "PlatformTrendsPayload":
    """One alert sparkline per customer, for the Ledger's trend column.

    A ledger row says what is true NOW -- open alerts, sensors reporting,
    devices. That is the right answer to "how is this customer" and the wrong
    one to "is this getting better or worse", which is the question that
    decides who to call. Two customers both showing three open criticals are
    not the same customer when one of them is coming down from twenty.

    ONE QUERY, NOT ONE PER TENANT
    -----------------------------
    This module's docstring is explicit that the ledger is grouped queries
    rather than a loop over tenants, because at one query per tenant the page
    slows down as the business grows -- the wrong way round for the screen
    whose purpose is to stay readable as the list gets longer. So this asks
    `history.alert_first_sightings` for every tenant's rows in a single
    statement and buckets them here. `tests/test_platform_trends.py` counts the
    statements so that a later "just fetch it per row" cannot creep back in.

    WHY IT SHARES THE CUSTOMER'S OWN QUERY
    --------------------------------------
    `alert_first_sightings` is the same function behind the per-customer
    charts, and the reason is the one that governs this whole console: the
    owner opens a customer's page from this row, and a sparkline here that
    counted different alerts than the chart there would be discovered by the
    customer, not by us. The two bucket at different widths -- a day here, an
    hour there -- but they are never arguing about which rows exist.
    """
    from ..schemas_dash import PlatformTrend, PlatformTrendsPayload
    from . import history

    days = max(1, min(days, history.MAX_DAYS))
    cutoff = _now_utc() - dt.timedelta(days=days)

    # The day axis every series is drawn against, oldest first. Built from the
    # window rather than from the rows, so a customer whose alerts all landed
    # on one day still gets a full-width line instead of a single point, and
    # every row in the column spans the same dates.
    axis = [(cutoff + dt.timedelta(days=i)).date() for i in range(days)]
    index = {day: i for i, day in enumerate(axis)}

    counts: dict[str, list[float]] = {}
    for tid, when, _sev in history.alert_first_sightings(db, cutoff):
        slot = index.get(when.date())
        if slot is None:
            # Past the last whole day in the axis -- today's partial day. It is
            # dropped rather than added to the final point, which would draw a
            # part-day next to full ones and read as a fall every morning.
            continue
        counts.setdefault(tid, [0.0] * days)[slot] += 1

    trends = [
        PlatformTrend(tenant_id=tid, points=points, total=int(sum(points)))
        for tid, points in sorted(counts.items())
    ]
    return PlatformTrendsPayload(days=days, trends=trends)


def _now_utc() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)
