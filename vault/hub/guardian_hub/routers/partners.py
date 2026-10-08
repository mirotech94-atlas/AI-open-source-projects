"""Partners: the MSP level above tenants, managed by the platform owner.

Superadmin-only end to end. A partner's own staff do not administer the
partner through this API -- they reach their customers through the
ordinary tenant switcher, because `deps.memberships` expands a
`PartnerMembership` into a membership on every customer. What the owner
does here is the bookkeeping that expansion reads: create the partner,
set the brand its customers inherit, attach and detach customers, and
grant staff a role across all of them.

Every "not found" is a 404 whether the id is unknown or merely not the
caller's to see, so an id from another partner's world is
indistinguishable from a typo. The routes are superadmin-only today, so
that distinction is moot for them; it is written this way so the day a
partner-facing route is added on top, it inherits the rule rather than
learning it from a leak.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session as DbSession

from .. import audit, branding, models as m
from ..accounts import upsert_user
from ..db import get_db
from ..deps import ROLE_ORDER, require_superadmin

router = APIRouter(prefix="/api/v1/partners", tags=["partners"], dependencies=[Depends(require_superadmin)])


class PartnerIn(BaseModel):
    slug: str = Field(min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=120)
    brand: dict = Field(default_factory=dict)


class PartnerPatch(BaseModel):
    name: str | None = None
    brand: dict | None = None
    settings: dict | None = None


class StaffIn(BaseModel):
    username: str
    email: str
    display_name: str = ""
    role: str = "viewer"


def _404():
    raise HTTPException(404, "not found")


def _ip(request: Request) -> str:
    return request.client.host if request.client else ""


def _out(db: DbSession, p: m.Partner) -> dict:
    tenants = db.query(m.Tenant).filter_by(partner_id=p.id).order_by(m.Tenant.name).all()
    staff = (db.query(m.PartnerMembership, m.User)
             .join(m.User, m.User.id == m.PartnerMembership.user_id)
             .filter(m.PartnerMembership.partner_id == p.id).all())
    return {
        "id": p.id, "slug": p.slug, "name": p.name, "brand": p.brand or {}, "settings": p.settings or {},
        "tenants": [{"id": t.id, "slug": t.slug, "name": t.name} for t in tenants],
        "staff": [{"username": u.username, "role": pm.role} for pm, u in staff],
    }


@router.get("")
def list_partners(db: DbSession = Depends(get_db)):
    return [_out(db, p) for p in db.query(m.Partner).order_by(m.Partner.name)]


@router.post("")
def create(body: PartnerIn, request: Request, user: m.User = Depends(require_superadmin),
           db: DbSession = Depends(get_db)):
    if db.query(m.Partner).filter_by(slug=body.slug).first():
        raise HTTPException(409, "slug exists")
    p = m.Partner(slug=body.slug, name=body.name, brand=branding._clean(body.brand))
    db.add(p); db.flush()
    audit.log(db, None, user.username, "superadmin", "partner.create", target=p.slug, ip=_ip(request),
              body={"slug": p.slug, "name": p.name})
    db.commit()
    return _out(db, p)


@router.get("/{pid}")
def get_partner(pid: str, db: DbSession = Depends(get_db)):
    p = db.get(m.Partner, pid) or _404()
    return _out(db, p)


@router.patch("/{pid}")
def patch(pid: str, body: PartnerPatch, request: Request, user: m.User = Depends(require_superadmin),
          db: DbSession = Depends(get_db)):
    p = db.get(m.Partner, pid) or _404()
    payload = body.model_dump(exclude_unset=True, exclude_none=True)
    if "name" in payload:
        p.name = payload["name"]
    if "brand" in payload:
        # Replace, not merge: the brand is three known keys and the form
        # sends all of them, so a cleared field must clear.
        p.brand = branding._clean(payload["brand"])
    if "settings" in payload:
        p.settings = payload["settings"]
    audit.log(db, None, user.username, "superadmin", "partner.update", target=p.slug, ip=_ip(request), body=payload)
    db.commit()
    return _out(db, p)


@router.delete("/{pid}")
def delete(pid: str, request: Request, user: m.User = Depends(require_superadmin), db: DbSession = Depends(get_db)):
    """Deleting a partner orphans its customers back to direct customers;
    it never deletes a tenant. The FK is SET NULL, but SQLite only honours
    that with foreign keys switched on, so the detach is done explicitly."""
    p = db.get(m.Partner, pid) or _404()
    db.query(m.Tenant).filter_by(partner_id=p.id).update({"partner_id": None}, synchronize_session=False)
    db.query(m.PartnerMembership).filter_by(partner_id=p.id).delete(synchronize_session=False)
    audit.log(db, None, user.username, "superadmin", "partner.delete", target=p.slug, ip=_ip(request))
    db.delete(p)
    db.commit()
    return {"ok": True}


@router.post("/{pid}/tenants/{tid}")
def attach_tenant(pid: str, tid: str, request: Request, user: m.User = Depends(require_superadmin),
                  db: DbSession = Depends(get_db)):
    p = db.get(m.Partner, pid) or _404()
    t = db.get(m.Tenant, tid) or _404()
    if t.partner_id and t.partner_id != p.id:
        # A customer belongs to one partner. Moving it is detach-then-attach
        # on purpose, so the audit trail shows the two steps a customer
        # would ask about ("when did my data become visible to them?").
        raise HTTPException(409, "tenant already belongs to another partner")
    t.partner_id = p.id
    audit.log(db, t.id, user.username, "superadmin", "partner.attach", target=p.slug, ip=_ip(request),
              body={"partner": p.slug, "tenant": t.slug})
    db.commit()
    return _out(db, p)


@router.delete("/{pid}/tenants/{tid}")
def detach_tenant(pid: str, tid: str, request: Request, user: m.User = Depends(require_superadmin),
                  db: DbSession = Depends(get_db)):
    p = db.get(m.Partner, pid) or _404()
    t = db.get(m.Tenant, tid) or _404()
    if t.partner_id != p.id:
        _404()
    t.partner_id = None
    audit.log(db, t.id, user.username, "superadmin", "partner.detach", target=p.slug, ip=_ip(request),
              body={"partner": p.slug, "tenant": t.slug})
    db.commit()
    return _out(db, p)


@router.post("/{pid}/staff")
def add_staff(pid: str, body: StaffIn, request: Request, user: m.User = Depends(require_superadmin),
              db: DbSession = Depends(get_db)):
    if body.role not in ROLE_ORDER:
        raise HTTPException(422, "bad role")
    p = db.get(m.Partner, pid) or _404()
    u = upsert_user(db, body.username, body.email, body.display_name)
    pm = db.query(m.PartnerMembership).filter_by(user_id=u.id, partner_id=p.id).first()
    if pm:
        pm.role = body.role
    else:
        db.add(m.PartnerMembership(user_id=u.id, partner_id=p.id, role=body.role))
    audit.log(db, None, user.username, "superadmin", "partner.staff_add", target=u.username, ip=_ip(request),
              body={"partner": p.slug, "username": u.username, "role": body.role})
    db.commit()
    return {"username": u.username, "role": body.role}


@router.delete("/{pid}/staff/{username}")
def remove_staff(pid: str, username: str, request: Request, user: m.User = Depends(require_superadmin),
                 db: DbSession = Depends(get_db)):
    p = db.get(m.Partner, pid) or _404()
    u = db.query(m.User).filter_by(username=username.lower()).first() or _404()
    rows = db.query(m.PartnerMembership).filter_by(user_id=u.id, partner_id=p.id).delete()
    if not rows:
        _404()
    audit.log(db, None, user.username, "superadmin", "partner.staff_remove", target=u.username, ip=_ip(request),
              body={"partner": p.slug})
    db.commit()
    return {"ok": True}
