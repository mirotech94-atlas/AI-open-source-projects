from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy.orm import Session as DbSession

from ..db import get_db
from .. import audit
from .. import models as m
from .. import sensors as sensors_lib
from ..deps import require_role

router = APIRouter(prefix="/api/v1/sensors", tags=["sensors"])

ADMIN = Depends(require_role("admin"))


def _ip(request: Request) -> str:
    return request.client.host if request.client else ""


class EnrolCodeIn(BaseModel):
    # Round 2 of Task 51: optional, additive -- a code minted without it binds
    # nothing, exactly as before this field existed.
    network_id: str | None = None


@router.post("/enrol-code")
def enrol_code(request: Request, body: EnrolCodeIn = EnrolCodeIn(), ctx=ADMIN,
               db: DbSession = Depends(get_db)):
    """Let a tenant admin (not just a superadmin) mint an enrol code for
    their own active tenant. Mirrors `tenants.enrol_code` but scoped to the
    signed-in admin's active tenant instead of any tenant id in the URL.

    `network_id` (round 2) is validated tenant-scoped here, at mint time --
    not at redemption -- so a bad id 404s the admin who is looking at the
    response, rather than surfacing as a mysterious unbound sensor days later
    when some unrelated device finally redeems the code.
    """
    user, tenant, role = ctx
    network_id = body.network_id or None
    if network_id is not None:
        net = db.query(m.Network).filter_by(tenant_id=tenant.id, id=network_id).first()
        if not net:
            raise HTTPException(404, "no such network")
    s = sensors_lib.new_enrol_code_for(db, tenant, network_id=network_id)
    audit.log(db, tenant.id, user.username, role, "sensor.enrol_code", target=s.id, ip=_ip(request))
    db.commit()
    return {"code": s.enrol_code, "expires": s.enrol_expires.isoformat(), "sensor_id": s.id,
            "network_id": s.network_id}
