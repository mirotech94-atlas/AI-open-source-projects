"""First-run wizard state, per user per tenant (task 71).

The wizard used to be decided by the tenant: the overview payload reported no
scan and no sensor, and the browser redirected to `/welcome`. That gate asked
the wrong question. "Has this site been set up?" is not "has this person been
shown around?", and the second person to join an established tenant -- the one
who needs orientation most -- never saw the wizard at all.

So completion is stored per `(user, tenant)` in `models.UserOnboarding`, and
this module is the only place that reads or writes it. It answers three
things:

1. **Should the wizard show?** Not once this user has completed or skipped it
   on this tenant. Nothing about the tenant's own state can suppress it.
2. **Which walkthrough?** `setup` when nothing has ever reported here -- the
   installing path. `tour` when the site is already reporting -- the
   orienting path, which never asks a newcomer to install a sensor they have
   no business installing.
3. **Where did they get to?** The resumable step name, so a wizard closed
   half-way resumes on any machine rather than only on the browser that
   happened to hold the `localStorage` key.
"""

import datetime as dt

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session as DbSession

from . import models as m
from .networks import SENSOR_STALE_S
from .scans import latest_scan
from .schemas_dash import OnboardingNetwork, OnboardingNext, OnboardingState

#: The two walkthroughs. `setup` installs, `tour` orients -- see the module
#: docstring, and `web/src/pages/Onboarding.tsx`, which draws both.
PATH_SETUP = "setup"
PATH_TOUR = "tour"


def tenant_ready(db: DbSession, tenant_id: str) -> bool:
    """True when this tenant has anything reporting: a sensor row, or a scan.

    Either is enough. A tenant can hold a scan imported from a Nexus config
    with no sensor enrolled yet, and a tenant can hold a freshly enrolled
    sensor that has not pushed its first scan -- in both cases the newcomer
    is joining something that exists, not standing up something new.
    """
    if db.query(m.Sensor.id).filter_by(tenant_id=tenant_id).first() is not None:
        return True
    return latest_scan(db, tenant_id) is not None


#: Worst to best. `_sensor_state` takes the best state any sensor bound to a
#: network is in, so a spare pending code beside a working sensor cannot make
#: the network look uncovered.
_SENSOR_ORDER = ("none", "pending", "quiet", "active")


def _sensor_state(sensors: list) -> str:
    """The best state any sensor bound to one network is in.

    Best rather than first: `Sensor.network_id` is not unique, a redundant
    sensor is a legitimate thing to own, and reading the first row would make
    a network's coverage depend on insertion order.

    A `revoked` sensor is skipped entirely -- it is as good as absent, and
    counting it would tell somebody a revoked sensor is watching them.
    """
    now = dt.datetime.now(dt.timezone.utc)
    best = "none"
    for s in sensors:
        if s.status == "active":
            fresh = s.last_seen is not None and (now - s.last_seen).total_seconds() <= SENSOR_STALE_S
            state_ = "active" if fresh else "quiet"
        elif s.status == "pending":
            state_ = "pending"
        else:
            continue
        if _SENSOR_ORDER.index(state_) > _SENSOR_ORDER.index(best):
            best = state_
    return best


def progress(db: DbSession, tenant: m.Tenant) -> list[OnboardingNetwork]:
    """Every declared network and how far along it is.

    Grouped queries, never one per network. A tenant with a dozen networks must
    not cost a dozen round trips -- `payloads/platform` makes the same argument
    for the ledger, and `tests/test_onboarding_progress.py` counts the
    statements to keep it true here.
    """
    nets = db.query(m.Network).filter_by(tenant_id=tenant.id).order_by(m.Network.name).all()
    if not nets:
        return []
    ids = [n.id for n in nets]

    # Local, to keep a payload module out of this one's import graph.
    from .payloads.assets import group_of, kind_of
    from .scans import parse_ts

    by_net: dict[str, list] = {i: [] for i in ids}
    net_of_sensor: dict[str, str] = {}
    for s in db.query(m.Sensor).filter(m.Sensor.tenant_id == tenant.id,
                                       m.Sensor.network_id.in_(ids)).all():
        by_net.setdefault(s.network_id, []).append(s)
        net_of_sensor[s.id] = s.network_id

    # The first scan, found through the SENSOR that pushed it: `Scan` carries a
    # `sensor_id` and no network, and `Sensor.network_id` is the link. That is
    # the whole reason enrol codes are minted per network.
    #
    # Compared as instants, not as strings. `Scan.ts` carries whatever UTC
    # offset the sensor wrote, so a SQL MIN() over it is lexicographic: a scan
    # stamped 22:00-04:00 is 02:00Z the next day -- later in time, earlier as
    # text. `demo_trickle._tenant_offset` documents the same trap from the
    # other end.
    first: dict[str, str] = {}
    if net_of_sensor:
        rows = db.query(m.Scan.sensor_id, m.Scan.ts).filter(
            m.Scan.tenant_id == tenant.id,
            m.Scan.sensor_id.in_(list(net_of_sensor)),
        ).all()
        best: dict[str, dt.datetime] = {}
        for sensor_id, ts in rows:
            when = parse_ts(ts)
            if when is None:
                continue
            net_id = net_of_sensor.get(sensor_id)
            if net_id is None:
                continue
            if net_id not in best or when < best[net_id]:
                best[net_id] = when
                first[net_id] = ts

    # Counted through the SAME classifier the Assets page uses. A summary that
    # grouped a customer's devices differently from the screen it links to
    # would be found by the customer.
    counts: dict[str, int] = {}
    groups: dict[str, dict[str, int]] = {}
    for d in db.query(m.Device).filter(m.Device.tenant_id == tenant.id,
                                       m.Device.network_id.in_(ids)).all():
        counts[d.network_id] = counts.get(d.network_id, 0) + 1
        g = groups.setdefault(d.network_id, {})
        name = group_of(kind_of(d))
        g[name] = g.get(name, 0) + 1

    return [
        OnboardingNetwork(id=n.id, name=n.name, slug=n.slug,
                          sensor=_sensor_state(by_net.get(n.id) or []),
                          first_scan_at=first.get(n.id, ""),
                          devices=counts.get(n.id, 0),
                          by_group=groups.get(n.id) or {})
        for n in nets
    ]


def next_action(nets: list[OnboardingNetwork]) -> OnboardingNext | None:
    """The one thing to do now, or nothing.

    The order is the order in which a network becomes useful: it has to exist,
    then be watched, then have actually reported, and only then is there any
    point offering DNS filtering for it. Each stage is checked across EVERY
    network before moving to the next, so a second network that has no sensor
    outranks a first network that merely has no DNS.
    """
    if not nets:
        return OnboardingNext(action="declare_network")
    for n in nets:
        if n.sensor in ("none", "pending"):
            return OnboardingNext(network_id=n.id, action="enrol_sensor")
    for n in nets:
        if not n.first_scan_at:
            return OnboardingNext(network_id=n.id, action="await_first_scan")
    for n in nets:
        if n.dns != "working":
            return OnboardingNext(network_id=n.id, action="offer_dns")
    return None


def _row(db: DbSession, user_id: str, tenant_id: str) -> "m.UserOnboarding | None":
    return db.query(m.UserOnboarding).filter_by(user_id=user_id, tenant_id=tenant_id).first()


def state(db: DbSession, user: m.User, tenant: m.Tenant) -> OnboardingState:
    """This user's wizard state on this tenant. Never writes -- a user who has
    never opened the wizard simply has no row, and reads as "show it"."""
    row = _row(db, user.id, tenant.id)
    ready = tenant_ready(db, tenant.id)
    completed = bool(row and row.completed)
    skipped = bool(row and row.skipped)
    nets = progress(db, tenant)
    return OnboardingState(
        path=PATH_TOUR if ready else PATH_SETUP,
        step=(row.step if row else "") or "",
        completed=completed,
        skipped=skipped,
        show=not (completed or skipped),
        tenant_ready=ready,
        site_named=bool((tenant.name or "").strip()),
        networks=nets,
        next=next_action(nets),
    )


def save(db: DbSession, user: m.User, tenant: m.Tenant, *, step: str | None = None,
         completed: bool | None = None, skipped: bool | None = None) -> OnboardingState:
    """Upsert this user's wizard state on this tenant and return the fresh
    reading. Every field is optional: the wizard saves `step` as it advances,
    "Skip for now" saves `skipped`, "Done" saves `completed`, and Settings'
    "Re-run setup" saves both back to false.

    Does not commit -- the caller owns the transaction, same as every other
    write path in this hub.
    """
    row = _row(db, user.id, tenant.id)
    if row is None:
        # The wizard saves its step as it opens while "Skip for now" may post
        # in the same instant, so two requests can both read "no row" and
        # both insert. The insert runs in a savepoint: the loser of that race
        # hits the (user, tenant) unique key, rolls back only the savepoint,
        # and updates the winner's row instead of answering 500.
        try:
            with db.begin_nested():
                row = m.UserOnboarding(user_id=user.id, tenant_id=tenant.id)
                db.add(row)
        except IntegrityError:
            row = db.query(m.UserOnboarding).filter_by(user_id=user.id, tenant_id=tenant.id).one()
    if step is not None:
        row.step = step[:32]
    if completed is not None:
        row.completed = completed
    if skipped is not None:
        row.skipped = skipped
    db.flush()
    return state(db, user, tenant)
