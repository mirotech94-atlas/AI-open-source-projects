from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy.orm import Session as DbSession
from ..db import get_db
from .. import models as m, audit, tenant_defaults
from ..accounts import upsert_membership, upsert_user
from ..deps import require_superadmin
from .. import sensors as sensors_lib

router = APIRouter(prefix="/api/v1/tenants", tags=["tenants"], dependencies=[Depends(require_superadmin)])

class TenantIn(BaseModel):
    slug: str; name: str; brand: dict = {}; ntfy_topic: str = ""
class TenantPatch(BaseModel):
    name: str | None = None; brand: dict | None = None; ntfy_topic: str | None = None; settings: dict | None = None; config: dict | None = None
class UserIn(BaseModel):
    username: str; email: str; display_name: str = ""; role: str = "viewer"

def _out(t: m.Tenant): return {"id": t.id, "slug": t.slug, "name": t.name, "brand": t.brand, "ntfy_topic": t.ntfy_topic, "settings": t.settings, "config": t.config}

def _ip(request: Request) -> str:
    return request.client.host if request.client else ""

@router.get("")
def list_tenants(db: DbSession = Depends(get_db)): return [_out(t) for t in db.query(m.Tenant).order_by(m.Tenant.name)]

# Every write handler below re-declares `user: m.User = Depends(require_superadmin)`
# (rather than relying on the router-level dependency, whose return value the
# route function never sees) so it has a username/role to pass to audit.log.

@router.post("")
def create(body: TenantIn, request: Request, user: m.User = Depends(require_superadmin), db: DbSession = Depends(get_db)):
    if db.query(m.Tenant).filter_by(slug=body.slug).first(): raise HTTPException(409, "slug exists")
    t = m.Tenant(slug=body.slug, name=body.name, brand=body.brand, ntfy_topic=body.ntfy_topic,
                 config=tenant_defaults.new_tenant_config())
    db.add(t); db.flush()
    audit.log(db, t.id, user.username, "superadmin", "tenant.create", target=t.slug, ip=_ip(request), body={"slug": t.slug, "name": t.name})
    db.commit()
    return _out(t)

@router.patch("/{tid}")
def patch(tid: str, body: TenantPatch, request: Request, user: m.User = Depends(require_superadmin), db: DbSession = Depends(get_db)):
    t = db.get(m.Tenant, tid) or _404()
    payload = body.model_dump(exclude_none=True)
    for k, v in payload.items(): setattr(t, k, v)
    audit.log(db, t.id, user.username, "superadmin", "tenant.update", target=t.slug, ip=_ip(request), body=payload)
    db.commit()
    return _out(t)

@router.post("/{tid}/users")
def add_user(tid: str, body: UserIn, request: Request, user: m.User = Depends(require_superadmin), db: DbSession = Depends(get_db)):
    if body.role not in ("viewer", "operator", "admin"): raise HTTPException(422, "bad role")
    t = db.get(m.Tenant, tid) or _404()
    u = upsert_user(db, body.username, body.email, body.display_name)
    upsert_membership(db, u, t, body.role)
    audit.log(db, t.id, user.username, "superadmin", "tenant.user_add", target=u.username, ip=_ip(request), body={"username": u.username, "role": body.role})
    db.commit()
    return {"username": u.username, "role": body.role}

@router.delete("/{tid}/users/{username}")
def remove_user(tid: str, username: str, request: Request, user: m.User = Depends(require_superadmin), db: DbSession = Depends(get_db)):
    t = db.get(m.Tenant, tid) or _404()
    u = db.query(m.User).filter_by(username=username.lower()).first() or _404()
    rows = db.query(m.Membership).filter_by(user_id=u.id, tenant_id=tid).delete()
    if not rows: _404()
    audit.log(db, t.id, user.username, "superadmin", "tenant.user_remove", target=u.username, ip=_ip(request))
    db.commit()
    return {"ok": True}

@router.post("/{tid}/enrol-code")
def enrol_code(tid: str, request: Request, user: m.User = Depends(require_superadmin), db: DbSession = Depends(get_db)):
    t = db.get(m.Tenant, tid) or _404()
    s = sensors_lib.new_enrol_code_for(db, t)
    db.flush()  # assign s.id before it is used as the audit target
    audit.log(db, t.id, user.username, "superadmin", "sensor.enrol_code", target=str(s.id), ip=_ip(request))
    db.commit()
    return {"code": s.enrol_code, "expires": s.enrol_expires.isoformat(), "sensor_id": s.id}

def _404(): raise HTTPException(404, "not found")
