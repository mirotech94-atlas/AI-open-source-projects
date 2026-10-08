"""Manual vulnerability scans -- the Security tab's "look at this one hard"
button. A scan is a sensor queue job on `/api/vuln-scan` (see the sensor's
`vulnscan.py` for what it does and, more importantly, what it never does:
no logins, no exploits, only connect / read / HEAD). The hub queues it,
audits who asked, and keeps the report as the queue row's result.
"""
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy.orm import Session as DbSession

from ..db import get_db
from ..deps import require_role
from ..payloads import vulnscans as vs_payload
from ..schemas_dash import VulnScanRow, VulnScansPayload
from .write import _ip, queue_and_audit

router = APIRouter(prefix="/api/v1", tags=["dashboard-write"])
VIEWER = Depends(require_role("viewer"))
OPERATOR = Depends(require_role("operator"))
ADMIN = Depends(require_role("admin"))

KINDS = ("device", "host", "site", "lan")
#: Item 24. `standard` is the middle depth -- see
#: `sensor/guardian_sensor/vulnscan.PROFILE_PORTS`. Kept in the same order
#: the UI offers them.
PROFILES = ("quick", "standard", "full")


class VulnScanIn(BaseModel):
    target: str = "lan"
    kind: str = ""
    profile: str = "quick"


@router.get("/security/vuln-scans", response_model=VulnScansPayload)
def list_scans(ctx=VIEWER, db: DbSession = Depends(get_db)):
    _user, tenant, _role = ctx
    return vs_payload.build(db, tenant)


@router.get("/security/vuln-scans/{qid}", response_model=VulnScanRow)
def one_scan(qid: str, ctx=VIEWER, db: DbSession = Depends(get_db)):
    _user, tenant, _role = ctx
    row = vs_payload.one(db, tenant, qid)
    if row is None:
        raise HTTPException(404, "no such scan")
    return row


@router.post("/security/vuln-scans")
def start_scan(body: VulnScanIn, request: Request, ctx=OPERATOR, db: DbSession = Depends(get_db)):
    """Queue one scan. A whole-LAN scan is admin-only: it touches every
    device in the house at once, which is a decision, not a click."""
    user, tenant, role = ctx
    target = (body.target or "lan").strip()
    kind = (body.kind or "").strip() or ("lan" if target in ("lan", "all", "") else "")
    if kind and kind not in KINDS:
        raise HTTPException(422, "kind must be device|host|site|lan")
    if body.profile not in PROFILES:
        raise HTTPException(422, f"profile must be {'|'.join(PROFILES)}")
    if kind == "lan" and role != "admin":
        raise HTTPException(403, "a whole-network scan needs an admin")
    if kind != "lan" and not target:
        raise HTTPException(422, "target required")
    job = {"target": "lan" if kind == "lan" else target, "profile": body.profile}
    if kind:
        job["kind"] = kind
    return queue_and_audit(db, tenant, user.username, role, vs_payload.QUEUE_PATH, "vulnscan.start",
                           body=job, target=job["target"], ip=_ip(request))
