from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session as DbSession

from ..db import get_db
from ..deps import require_role
from ..payloads import updates as updates_payload
from .. import schemas_dash as sd

router = APIRouter(prefix="/api/v1", tags=["dashboard-updates"])

VIEWER = Depends(require_role("viewer"))


@router.get("/updates", response_model=sd.UpdatesPayload)
def updates(days: int = 30, ctx=VIEWER, db: DbSession = Depends(get_db)):
    user, tenant, role = ctx
    # `days` is the cost bound on cves()/calendar()'s Scan/Fix history scans
    # (payloads/updates.py) -- clamp it here so a caller can't dial an
    # unbounded scan-history load back in via the query string.
    days = max(1, min(days, 90))
    return updates_payload.build(db, tenant, days=days)
