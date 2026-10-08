"""One customer, as the owner needs to see them.

The ledger answers "how are all of them"; this answers "what is actually set up
for this one" -- their networks, their sensors, who can sign in and as what,
which integrations they have wired, and what has recently been done to them.
It is the screen you open before answering a support ticket, because almost
every question a customer asks ("why did nobody tell me?", "why is that device
missing?") is really a question about their own configuration.

WHAT THIS DELIBERATELY DOES NOT RETURN

`CLAUDE.md` is explicit that Vault's own output is a disclosure surface, and
this payload is the one most tempted to leak: three of the rows it reads carry
credentials.

  * `Integration.secret` -- a Slack token, a webhook URL with a key in it. The
    owner needs to know Slack IS configured, never what the token is, so only
    `kind` and a boolean cross the boundary.
  * `Sensor.token_hash` and `Sensor.enrol_code` -- an unexpired enrol code is a
    bearer credential that adds a sensor to that tenant. Neither is selected.
  * `Audit.body` -- the free-form detail of an act, which is where a changed
    setting's old and new values live. The list here is who/what/when; reading
    the body is a different, narrower question than "what happened lately".

The columns are named explicitly in each query rather than loading whole ORM
rows, so adding a credential column to one of those models cannot silently
widen this payload later.
"""
import datetime as dt

from sqlalchemy import func, select
from sqlalchemy.orm import Session as DbSession

from .. import billing as billing_mod, models as m, plans, schemas_dash as sd
from .platform import _PAYING, _per_site


def _iso(v: dt.datetime | None) -> str:
    return v.isoformat() if v else ""


def _billing(db: DbSession, tenant: m.Tenant) -> sd.AccountBilling | None:
    """This customer's money, on exactly the roster's terms.

    `_PAYING`, `plans.monthly_cents` and `_per_site` are imported from
    `payloads/platform` rather than re-derived here. The owner reaches this
    page BY CLICKING a row on the Billing roster, so the two are read one after
    the other, about the same account, seconds apart -- a second definition of
    "is this customer paying" would be discovered by the owner mid-sentence
    with that customer on the phone. Same reason `remedy` ports its tables from
    `fix.FIX_ALERT_KEY` verbatim rather than paraphrasing them.
    """
    sub = db.query(m.Subscription).filter_by(tenant_id=tenant.id).first()
    if sub is None:
        return None
    paying = billing_mod.stripe_is_billing(sub)   # same predicate as the ledger
    plan = plans.get(sub.plan)
    return sd.AccountBilling(
        plan=sub.plan or "",
        plan_label=(plan.name if plan else (sub.plan or "no subscription").replace("_", " ")),
        interval=sub.interval or "",
        status=sub.status or "",
        mrr_cents=plans.monthly_cents(sub.plan, sub.interval) if paying else 0,
        per_site=_per_site(sub.plan),
        trial_days_left=billing_mod.trial_days_left(sub, dt.datetime.now(dt.timezone.utc)),
        period_end=_iso(sub.current_period_end),
        cancel_at_period_end=bool(sub.cancel_at_period_end),
        stripe_customer_id=sub.stripe_customer_id or "",
        stripe_dashboard_url=billing_mod.stripe_dashboard_url(sub.stripe_customer_id or ""),
    )


def build_payload(db: DbSession, tenant: m.Tenant) -> sd.AccountPayload:
    """Everything configured for one tenant, and nothing that authenticates as them."""

    # --- networks, with how many devices each actually carries -------------
    # A tenant with three networks and every device on one of them is a
    # different (and much more common) shape than one with them spread evenly,
    # and it is the shape that explains "why does the map look empty".
    counts = dict(
        db.execute(
            select(m.Device.network_id, func.count(m.Device.id))
            .where(m.Device.tenant_id == tenant.id)
            .group_by(m.Device.network_id)
        ).all()
    )
    networks = [
        sd.AccountNetwork(id=n.id, name=n.name, slug=n.slug, devices=int(counts.get(n.id, 0)))
        for n in db.execute(
            select(m.Network).where(m.Network.tenant_id == tenant.id).order_by(m.Network.name)
        ).scalars()
    ]
    # Devices on no network at all. Not an error -- a device seen before its
    # network was named lands here -- but it is invisible on the map, so the
    # count is worth showing rather than silently dropping.
    unassigned = int(counts.get(None, 0))

    # --- sensors: what is enrolled, on what, and when it last spoke --------
    sensors = [
        sd.AccountSensor(
            id=s_id, name=name, platform=platform or "", version=version or "",
            status=status or "", last_seen=_iso(last_seen),
        )
        for s_id, name, platform, version, status, last_seen in db.execute(
            select(m.Sensor.id, m.Sensor.name, m.Sensor.platform, m.Sensor.version,
                   m.Sensor.status, m.Sensor.last_seen)
            .where(m.Sensor.tenant_id == tenant.id)
            .order_by(m.Sensor.name)
        ).all()
    ]

    # --- who can sign in, and as what -------------------------------------
    people = [
        sd.AccountUser(username=username, email=email or "", display_name=display or "",
                       role=role, superadmin=bool(sa))
        for username, email, display, role, sa in db.execute(
            select(m.User.username, m.User.email, m.User.display_name,
                   m.Membership.role, m.User.is_superadmin)
            .join(m.Membership, m.Membership.user_id == m.User.id)
            .where(m.Membership.tenant_id == tenant.id)
            .order_by(m.Membership.role, m.User.username)
        ).all()
    ]

    # --- integrations: that they exist, never what they authenticate with --
    integrations = [
        sd.AccountIntegration(kind=kind, configured=True)
        for (kind,) in db.execute(
            select(m.Integration.kind)
            .where(m.Integration.tenant_id == tenant.id)
            .order_by(m.Integration.kind)
        ).all()
    ]

    # --- what has been done to this customer lately ------------------------
    recent = [
        sd.AccountAuditRow(ts=_iso(ts), user=user, role=role or "", action=action, target=target or "")
        for ts, user, role, action, target in db.execute(
            select(m.Audit.ts, m.Audit.user, m.Audit.role, m.Audit.action, m.Audit.target)
            .where(m.Audit.tenant_id == tenant.id)
            .order_by(m.Audit.ts.desc())
            .limit(25)
        ).all()
    ]

    return sd.AccountPayload(
        id=tenant.id,
        slug=tenant.slug,
        name=tenant.name,
        created=_iso(getattr(tenant, "created", None)),
        networks=networks,
        devices_unassigned=unassigned,
        sensors=sensors,
        users=people,
        integrations=integrations,
        recent=recent,
        billing=_billing(db, tenant),
    )


def build_audit(db: DbSession, limit: int = 200) -> sd.PlatformAuditPayload:
    """The platform's own trail, across every tenant.

    `tenant_id IS NULL` marks an act on the PLATFORM rather than on a customer
    -- the owner changing a platform setting. `audit.log`'s docstring is
    explicit that such a row must not borrow a customer's id, so the two are
    genuinely distinguishable here and the console labels them differently
    rather than showing a blank customer column.
    """
    names = dict(db.execute(select(m.Tenant.id, m.Tenant.name)).all())
    rows = [
        sd.PlatformAuditRow(
            ts=_iso(ts), user=user, role=role or "", action=action, target=target or "",
            tenant_id=tid or "", tenant=names.get(tid, "") if tid else "",
            platform=tid is None,
        )
        for ts, user, role, action, target, tid in db.execute(
            select(m.Audit.ts, m.Audit.user, m.Audit.role, m.Audit.action,
                   m.Audit.target, m.Audit.tenant_id)
            .order_by(m.Audit.ts.desc())
            .limit(limit)
        ).all()
    ]
    return sd.PlatformAuditPayload(rows=rows)


# --- how a customer has actually been doing ---------------------------------
#
# Which of history's charts the owner console draws, and how to read each one.
#
# `agg` is how a window of the series collapses to one number: alerts are a
# COUNT per bucket, so a window is their sum; a score and a device count are
# LEVELS, so a window is their mean. Summing a level would produce a figure
# that grows with how many buckets happened to land in the window, which is a
# property of the clock rather than of the customer.
#
# `good` is which direction is an improvement, and it is `None` for devices on
# purpose. Fewer devices is not better and more is not worse -- it is a fact
# about the customer's estate, and a console that told them their network got
# "worse" because they plugged in a printer would be wrong in a way they would
# remember. Only alerts and score have a direction; devices gets its sentence
# without a verdict.
_TRENDS: dict[str, tuple[str, str | None]] = {
    "alerts": ("sum", "down"),
    "score": ("mean", "up"),
    "devices": ("mean", None),
}

#: Below this relative change, a series is reported as steady. Two hours of
#: normal wobble should not be narrated to a customer as a trend, and this
#: page exists to be read aloud.
_MOVED = 0.10


def _halves(points) -> tuple[list[float], list[float]]:
    """The series' first and second half, as plain values.

    Four points is the floor: with fewer, "the first half" is one reading, and
    one reading against one reading is not a trend -- it is two numbers.
    """
    vals = [p.value for p in points if p.value is not None]
    if len(vals) < 4:
        return [], []
    mid = len(vals) // 2
    return vals[:mid], vals[mid:]


def _say(label: str, agg: str, good: str | None,
         first: list[float], second: list[float]) -> tuple[str, str]:
    """One sentence comparing the halves, and a verdict where there is one."""
    if not first or not second:
        return "", ""
    a = sum(first) if agg == "sum" else sum(first) / len(first)
    b = sum(second) if agg == "sum" else sum(second) / len(second)
    fmt = (lambda v: f"{v:.0f}") if agg == "sum" else (lambda v: f"{v:.0f}")

    # Relative to the earlier window, which is the one being compared against.
    # A zero first half has no percentage -- "up from nothing" is infinite, and
    # the honest phrasing is the two numbers.
    if a == 0:
        moved = 1.0 if b > 0 else 0.0
    else:
        moved = abs(b - a) / a

    if moved < _MOVED:
        return f"{label}: steady, around {fmt(b)}.", "steady" if good else ""
    way = "up" if b > a else "down"
    if good is None:
        return f"{label}: {way} from {fmt(a)} to {fmt(b)}.", ""
    verdict = "better" if way == good else "worse"
    return f"{label}: {way} from {fmt(a)} to {fmt(b)}.", verdict


def build_trends(db: DbSession, tenant: m.Tenant, days: int = 30) -> sd.AccountTrendsPayload:
    """What to show a customer about their own last `days`.

    The series are not computed here. `history.build_charts` is called and its
    output filtered, so the line the owner points at on this page is the same
    line the customer sees on their own History tab -- see that function's
    docstring for why a second implementation is the thing to avoid rather
    than the obvious shortcut.

    A chart the tenant has no data for is absent rather than empty. An axis
    with no line on it reads as "zero", and zero alerts and no alert data are
    very different things to say to a paying customer.
    """
    from . import history        # local: history imports payload helpers too

    _days, _hours, cutoff, bucket = history.window(days=days)
    by_key = {c.key: c for c in history.build_charts(db, tenant.id, cutoff, None, bucket)}

    trends: list[sd.AccountTrend] = []
    for key, (agg, good) in _TRENDS.items():
        chart = by_key.get(key)
        if chart is None or not chart.series:
            continue
        # The sentence is about ONE series -- the chart's first, which for
        # alerts is `critical` when there are any and `warning` otherwise,
        # because `_alerts_series` appends CRIT before WARN.
        #
        # So it has to say which. "New alerts: down from 10 to 8" read off a
        # tenant whose only series is warnings claims a fall in ALL alerts,
        # which is not what was measured, and this page is read aloud to the
        # customer those numbers belong to. The series is named whenever its
        # name is not already inside the chart's label -- "critical" is not in
        # "New alerts" so it is named; "score" is already in "Security score",
        # and "Security score (score)" is noise.
        drawn = chart.series[0]
        label = chart.label
        if drawn.name and drawn.name.lower() not in label.lower():
            label = f"{label} ({drawn.name})"
        first, second = _halves(drawn.points)
        says, direction = _say(label, agg, good, first, second)
        trends.append(sd.AccountTrend(chart=chart, says=says, direction=direction))

    return sd.AccountTrendsPayload(days=_days, trends=trends, headline=_headline(trends))


def _headline(trends: list[sd.AccountTrend]) -> str:
    """One sentence for the top of the section, or nothing.

    Empty when no chart reached a verdict. A customer reading "everything is
    improving" off a tenant whose sensor has been down all month would be
    reading a bug, and silence is the correct output for "not enough to say".
    """
    verdicts = [t.direction for t in trends if t.direction in ("better", "worse", "steady")]
    if not verdicts:
        return ""
    if "worse" in verdicts:
        return "Some of this is going the wrong way — worth walking through together."
    if "better" in verdicts:
        return "The direction of travel is good."
    return "Steady month, nothing moving much either way."


#: The audit actions that mean a piece of work was SETTLED, as opposed to
#: looked at. `fix` is a repair running; `alert.resolve` is an alert being
#: closed, whether by a repair verifying or by a person deciding. Reading and
#: chatting are not work closed and would swamp the chart if counted.
WORK_ACTIONS = ("fix", "alert.resolve")

#: The audit's actor when Vault acted for itself. Anything else is a
#: person -- `chat:miro` included, because a fix run from chat is still one
#: somebody asked for.
MACHINE_ACTOR = "guardian"


def is_person(by: str | None) -> bool:
    """Whether a `Fix.by` stamp names a person's yes rather than Vault
    acting alone. The hub stamps a requested fix `hub:<user>` (or
    `hub:chat:<user>`, `dashboard`); the sensor stamps its own `auto`, and
    stamps `accepted-recommendation` for a fix it ran BECAUSE the owner
    accepted the recommendation -- a yes, arriving by a different door
    (`sensor/guardian_sensor/fix.py`). An unknown stamp is Vault's: the
    chart's claim is "fixed without anyone being asked", and a person's
    involvement is the thing that has to be proven, not assumed.

    Every surface that splits repairs by who said yes calls this, so that
    two panels on one screen cannot disagree about the same rows -- see
    `overview.fixed_without_you` and `value.build`."""
    s = (by or "").strip().lower()
    return (s.startswith("hub:") or s.startswith("chat:")
            or s in ("dashboard", "accepted-recommendation"))


def build_work(db: DbSession, days: int = 14, *, tenant_id: str | None = None,
               hours: int = 0) -> sd.PlatformWorkPayload:
    """Who has been settling the work -- Vault on its own, or after a
    person's yes -- per day, across every customer; or,
    with `tenant_id`, on one customer's network; and with `hours`, per hour
    over the last `hours` hours instead of per day.

    The console's Overview draws one customer's self-healing chart from this
    rather than from the event log: the event log carries a `fix` event only
    when the sensor reported one, while the audit trail has every fix Vault
    or a person settled, which is the figure the chart claims to show. One
    source, so the chart and the Audit screen cannot disagree.

    `hours` exists for the bar's 24h range. A day-bucketed read of one day is
    a single bar, which is a number drawn as a chart; twenty-four hourly
    buckets is the shape the reference draws. `day` then carries the hour as
    `YYYY-MM-DDTHH`, which the web formats -- the field keeps its name so the
    payload's shape (and every reader of it) is unchanged.

    One query, bucketed in Python.

    `Audit.ts` is a real `TZDateTime`, NOT a string -- unlike `Alert.first_seen`
    and `Event.ts`, which are plain strings carrying whatever offset the sensor
    wrote and which this codebase re-checks in Python for exactly that reason.
    The first draft of this function applied `substr(ts, 1, 10)` to it on that
    assumption and could not even insert a fixture, let alone group one. So the
    window is an ordinary datetime comparison, and the day a row belongs to is
    taken from the value itself rather than from its text.

    Bucketing in Python rather than with a SQL date function keeps this off
    dialect-specific ground, and the row count is bounded by the window and by
    retention. It stays ONE query either way: a query per day would make this
    the one endpoint on the console that gets slower the further back the
    owner looks.
    """
    now = dt.datetime.now(dt.timezone.utc)
    hours = max(0, min(hours, 168))
    if hours:
        first_hour = now.replace(minute=0, second=0, microsecond=0) - dt.timedelta(hours=hours - 1)
        since = first_hour
        keys = [(first_hour + dt.timedelta(hours=i)).strftime("%Y-%m-%dT%H") for i in range(hours)]
        key_of = lambda ts: ts.astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H")  # noqa: E731
    else:
        days = max(1, min(days, 90))
        start = now.date() - dt.timedelta(days=days - 1)
        since = dt.datetime.combine(start, dt.time.min, tzinfo=dt.timezone.utc)
        keys = [(start + dt.timedelta(days=i)).isoformat() for i in range(days)]
        key_of = lambda ts: ts.date().isoformat()  # noqa: E731

    # Two sources, one for each kind of settled work.
    #
    # A repair is a `Fix` row. It is NOT an audit row: the audit trail records
    # the hub request that asked for a fix (so a person's name), and the
    # sensor's own automatic repairs never pass through a request at all --
    # on the live hub that read as one automatic fix against forty-nine
    # approved, when the fix table held fifty-nine automatic and nineteen
    # asked-for. The row's `by` says who: `hub:<user>` / `hub:chat:<user>` /
    # `dashboard` is a person's yes; anything else (`auto`, the sensor's
    # own stamps) is Vault acting alone. Only a fix that worked settled
    # anything.
    #
    # A person closing an alert by hand is an `alert.resolve` audit row and
    # stays one: no fix ran, a person decided.
    #
    # `Fix.ts` is a STRING carrying whatever offset the sensor wrote (see
    # CLAUDE.md on `first_seen`), so the SQL cutoff is a day lax and every
    # row is re-checked in Python on its parsed time.
    from ..scans import parse_ts
    lax = (since - dt.timedelta(days=1)).isoformat(timespec="seconds")
    fq = select(m.Fix.ts, m.Fix.by).where(m.Fix.ok.is_(True), m.Fix.ts >= lax)
    rq = select(m.Audit.ts, m.Audit.user).where(m.Audit.action == "alert.resolve", m.Audit.ts >= since)
    if tenant_id is not None:
        fq = fq.where(m.Fix.tenant_id == tenant_id)
        rq = rq.where(m.Audit.tenant_id == tenant_id)

    # Every bucket in the window, including the empty ones: dropping a quiet
    # day would draw the chart straight from Monday to Wednesday and hide it.
    buckets: dict[str, dict[str, int]] = {k: {"auto": 0, "human": 0} for k in keys}
    for ts_s, by in db.execute(fq).all():
        when = parse_ts(ts_s)
        if when is None or when < since:
            continue
        b = buckets.get(key_of(when))
        if b is None:
            continue
        b["human" if is_person(by) else "auto"] += 1
    for ts, _user in db.execute(rq).all():
        if ts is None:
            continue
        b = buckets.get(key_of(ts))
        if b is not None:
            b["human"] += 1

    out = [sd.PlatformWorkDay(day=d, auto=v["auto"], human=v["human"])
           for d, v in sorted(buckets.items())]
    return sd.PlatformWorkPayload(
        days=out,
        auto_total=sum(d.auto for d in out),
        human_total=sum(d.human for d in out),
    )
