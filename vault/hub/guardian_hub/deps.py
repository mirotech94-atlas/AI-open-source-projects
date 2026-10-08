import datetime as dt
from fastapi import Depends, HTTPException, Request
from sqlalchemy.orm import Session as DbSession
from .db import get_db
from . import models as m

COOKIE = "guardian_session"
ROLE_ORDER = {"viewer": 0, "operator": 1, "admin": 2}

def current_session(request: Request, db: DbSession = Depends(get_db)) -> m.Session:
    tok = request.cookies.get(COOKIE)
    s = db.get(m.Session, tok) if tok else None
    if not s or s.expires < dt.datetime.now(dt.timezone.utc):
        raise HTTPException(401, "not signed in")
    _touch(db, s)
    return s


# How stale `session.last_seen` is allowed to get. It exists so an admin can
# see which sessions are active and so an idle one can be reaped; nothing reads
# it to the second.
_TOUCH_EVERY = dt.timedelta(seconds=60)


def _touch(db: DbSession, s: m.Session) -> None:
    """Record that this session is still in use, at most once a minute.

    This used to write on every authenticated request. Every dashboard tab
    polls /live every four seconds and /overview alongside it, so a single open
    tab wrote to the database fifteen to thirty times a minute for a column
    nobody reads more than once. On SQLite that put a write on the read path,
    and once an hour a bulk background job held the write lock long enough that
    the writes lost: `UPDATE session SET last_seen` raised `database is locked`
    and the overview, the inbox and the updates page all returned 500 together.
    A longer busy_timeout makes that collision survivable; not writing at all
    makes it not happen.

    The failure mode of skipping a write is a `last_seen` up to a minute old,
    which no caller can tell apart from a session that was simply idle for that
    minute.
    """
    now = dt.datetime.now(dt.timezone.utc)
    last = s.last_seen
    if last is not None:
        # Rows written before this column carried a timezone come back naive.
        if last.tzinfo is None:
            last = last.replace(tzinfo=dt.timezone.utc)
        if now - last < _TOUCH_EVERY:
            return
    s.last_seen = now
    db.commit()

def current_user(s: m.Session = Depends(current_session), db: DbSession = Depends(get_db)) -> m.User:
    return db.get(m.User, s.user_id)

def memberships(db: DbSession, user: m.User) -> list[tuple[m.Tenant, str]]:
    """Every tenant this user may open, with the role they hold there.

    This is the one function the whole tenant boundary rests on: sign-in
    picks the first entry, the switcher offers exactly these, and
    `require_role` refuses any session tenant not in the list. A partner's
    staff are therefore not a special case anywhere else -- a
    `PartnerMembership` is expanded HERE into one entry per tenant carrying
    that `partner_id`, and every downstream check sees a plain membership.
    A customer that joins the partner tomorrow is reachable tomorrow; one
    that leaves it (`partner_id` cleared) disappears from the list on the
    next request, with no membership rows to chase.

    Direct and partner-derived grants on the same tenant collapse to the
    higher role, so a single customer can be promoted for one partner
    engineer without editing the partner-wide grant.
    """
    if user.is_superadmin:
        return [(t, "admin") for t in db.query(m.Tenant).order_by(m.Tenant.name)]
    best: dict[str, tuple[m.Tenant, str]] = {}
    rows = db.query(m.Membership, m.Tenant).join(m.Tenant, m.Tenant.id == m.Membership.tenant_id).filter(m.Membership.user_id == user.id).all()
    for mb, t in rows:
        best[t.id] = (t, mb.role)
    via_partner = (db.query(m.PartnerMembership, m.Tenant)
                   .join(m.Tenant, m.Tenant.partner_id == m.PartnerMembership.partner_id)
                   .filter(m.PartnerMembership.user_id == user.id).all())
    for pm, t in via_partner:
        held = best.get(t.id)
        if held is None or ROLE_ORDER.get(pm.role, 0) > ROLE_ORDER.get(held[1], 0):
            best[t.id] = (t, pm.role)
    # Insertion order, not sorted: sign-in lands on `ms[0]`, and a user's
    # own direct memberships should come before the ones a partner grants.
    return list(best.values())

#: Request methods that change nothing.
_READ_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})


def require_role(min_role: str):
    def dep(request: Request, s: m.Session = Depends(current_session), db: DbSession = Depends(get_db)):
        user = db.get(m.User, s.user_id)
        for t, role in memberships(db, user):
            if t.id == s.tenant_id:
                if ROLE_ORDER[role] < ROLE_ORDER[min_role]:
                    raise HTTPException(403, f"{min_role} role required")
                # A session opened with the public demo code may look at
                # everything above viewer but change nothing: no fixes,
                # integrations, tokens or settings (audit 2026-09-28, issue 3).
                if (s.via_demo and min_role != "viewer"
                        and request.method not in _READ_METHODS):
                    raise HTTPException(403, "the demo is read-only")
                return user, t, role
        raise HTTPException(403, "no active tenant")
    return dep

def require_superadmin(user: m.User = Depends(current_user)) -> m.User:
    if not user.is_superadmin: raise HTTPException(403, "superadmin only")
    return user
