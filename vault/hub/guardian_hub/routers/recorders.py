"""Camera recorder (NVR/DVR) registration -- Task 73.

Reads are viewer+, writes are admin, and every write is audited with user,
role, tenant and IP.

Three rules this router exists to hold:

1. **A password goes in and never comes out.** `RecorderIn.password` is
   write-only; every response is `recorders.out`, which reports
   `has_credentials` and never the value. The audit body records the fields
   that changed by NAME (`["password"]`), never their contents.
2. **Only an address inside the tenant's own networks may be registered.** An
   address that matches no configured network's `subnet_prefix` is a 422 that
   says so, rather than a recorder Vault would refuse to contact later
   without explaining why.
3. **No `Edit` row.** A recorder is not sensor config: the sensor pulls the
   inventory (with the credential) from `GET /api/v1/sensor/recorders` on
   every cycle. Putting it in an `/api/config` edit would park the password in
   the hub's `edit` table in plaintext for ever, replay it into the sensor's
   world-readable `config.json`, and re-send it to every sensor on the site on
   every unrelated config write.
"""

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy.orm import Session as DbSession

from .. import audit, models as m, recorders, schemas_dash as sd
from ..db import get_db
from ..deps import require_role
from ..live import bus

router = APIRouter(prefix="/api/v1", tags=["dashboard-recorders"])

VIEWER = Depends(require_role("viewer"))
ADMIN = Depends(require_role("admin"))


def _ip(request: Request) -> str:
    return request.client.host if request.client else ""


class RecorderIn(BaseModel):
    name: str = ""
    vendor: str = "reolink"
    host: str = ""
    port: int = 80
    scheme: str = "http"
    username: str = ""
    password: str = ""              # write-only; never returned
    expected_channels: int = 0
    enabled: bool = True


class RecorderPatch(BaseModel):
    name: str | None = None
    vendor: str | None = None
    host: str | None = None
    port: int | None = None
    scheme: str | None = None
    username: str | None = None
    password: str | None = None     # "" clears the stored credential
    expected_channels: int | None = None
    enabled: bool | None = None


def _validate(db: DbSession, tenant_id: str, host: str, vendor: str, port: int, scheme: str,
              expected: int, exclude_id: str | None = None) -> None:
    if vendor not in recorders.ALL_VENDORS:
        raise HTTPException(422, "vendor must be one of " + ", ".join(recorders.ALL_VENDORS))
    if scheme not in ("http", "https"):
        raise HTTPException(422, "scheme must be http or https")
    if not 1 <= int(port) <= 65535:
        raise HTTPException(422, "port must be between 1 and 65535")
    if int(expected) < 0 or int(expected) > 256:
        raise HTTPException(422, "expected_channels must be between 0 and 256")
    if recorders.lan_network_for(db, tenant_id, host) is None:
        raise HTTPException(422, "the recorder's address must be an IPv4 address inside one of this "
                                 "site's configured networks -- add that network under Networks first")
    dup = db.query(m.Recorder).filter_by(tenant_id=tenant_id, host=host)
    if exclude_id:
        dup = dup.filter(m.Recorder.id != exclude_id)
    if dup.first():
        raise HTTPException(422, f"a recorder at {host} is already registered")


@router.get("/recorders", response_model=sd.RecordersPayload)
def list_recorders(ctx=VIEWER, db: DbSession = Depends(get_db)):
    user, tenant, role = ctx
    return {"recorders": recorders.listing(db, tenant.id),
            "vendors": [{"key": k, "label": v, "supported": k in recorders.VENDORS}
                        for k, v in recorders.ALL_VENDORS.items()]}


@router.post("/recorders", response_model=sd.RecorderRow)
def create_recorder(body: RecorderIn, request: Request, ctx=ADMIN,
                    db: DbSession = Depends(get_db)):
    user, tenant, role = ctx
    vendor = (body.vendor or "").strip().lower()
    host = (body.host or "").strip()
    _validate(db, tenant.id, host, vendor, body.port, body.scheme, body.expected_channels)
    lan = recorders.lan_network_for(db, tenant.id, host)
    rec = m.Recorder(
        tenant_id=tenant.id, name=(body.name or "").strip() or host, vendor=vendor,
        host=host, port=int(body.port), scheme=body.scheme,
        username=(body.username or "").strip(),
        expected_channels=int(body.expected_channels), enabled=bool(body.enabled),
        lan_network_id=lan.id if lan else None, status="pending")
    recorders.set_password(rec, body.password or "")
    db.add(rec)
    db.flush()
    recorders.camera_network(db, tenant.id, rec)
    audit.log(db, tenant.id, user.username, role, "recorder.create", target=rec.id,
              ip=_ip(request), body={"host": host, "vendor": vendor, "name": rec.name,
                                     "credentials_set": bool(body.password)})
    db.commit()
    bus.publish(tenant.id, "recorders", {"id": rec.id, "action": "create"})
    return recorders.out(db, rec)


@router.patch("/recorders/{rid}", response_model=sd.RecorderRow)
def patch_recorder(rid: str, body: RecorderPatch, request: Request, ctx=ADMIN,
                   db: DbSession = Depends(get_db)):
    user, tenant, role = ctx
    rec = db.query(m.Recorder).filter_by(tenant_id=tenant.id, id=rid).first()
    if rec is None:
        raise HTTPException(404, "no such recorder")
    patch = body.model_dump(exclude_unset=True)
    vendor = (patch.get("vendor") or rec.vendor or "").lower()
    host = (patch.get("host") or rec.host or "").strip()
    _validate(db, tenant.id, host, vendor, patch.get("port", rec.port),
              patch.get("scheme", rec.scheme), patch.get("expected_channels", rec.expected_channels),
              exclude_id=rec.id)
    for field in ("name", "port", "expected_channels", "enabled", "scheme"):
        if field in patch and patch[field] is not None:
            setattr(rec, field, patch[field])
    if "username" in patch and patch["username"] is not None:
        rec.username = patch["username"].strip()
    rec.vendor, rec.host = vendor, host
    lan = recorders.lan_network_for(db, tenant.id, host)
    rec.lan_network_id = lan.id if lan else None
    if "password" in patch and patch["password"] is not None:
        recorders.set_password(rec, patch["password"])
    if {"host", "username", "password", "vendor", "port", "scheme"} & set(patch):
        # Anything that changes how the recorder is reached invalidates the
        # last verdict: it is re-read on the next scan, and until then the row
        # says "not read yet" rather than showing a stale error or a stale OK.
        rec.status, rec.error_kind, rec.error = "pending", "", ""
    recorders.camera_network(db, tenant.id, rec)
    # Field NAMES only: a password must never reach the audit log.
    audit.log(db, tenant.id, user.username, role, "recorder.update", target=rec.id,
              ip=_ip(request), body={"fields": sorted(patch), "host": rec.host})
    db.commit()
    bus.publish(tenant.id, "recorders", {"id": rec.id, "action": "update"})
    return recorders.out(db, rec)


@router.delete("/recorders/{rid}")
def delete_recorder(rid: str, request: Request, ctx=ADMIN, db: DbSession = Depends(get_db)):
    """Forget a recorder. Its camera network and the device rows on it are
    LEFT in place: they are history the owner may still want (a camera that
    was offline for a week is a record), and deleting a network is its own
    deliberate action under Networks."""
    user, tenant, role = ctx
    rec = db.query(m.Recorder).filter_by(tenant_id=tenant.id, id=rid).first()
    if rec is None:
        raise HTTPException(404, "no such recorder")
    net_id = rec.network_id
    audit.log(db, tenant.id, user.username, role, "recorder.delete", target=rec.id,
              ip=_ip(request), body={"host": rec.host, "name": rec.name, "network_id": net_id})
    db.delete(rec)
    db.commit()
    bus.publish(tenant.id, "recorders", {"id": rid, "action": "delete"})
    return {"ok": True, "network_id": net_id or ""}
