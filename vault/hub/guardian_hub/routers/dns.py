from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session as DbSession

from ..db import get_db
from ..deps import require_role
from ..payloads import dnsview as dns_payload
from .. import networks, retention
from .. import schemas_dash as sd

router = APIRouter(prefix="/api/v1", tags=["dashboard-dns"])

VIEWER = Depends(require_role("viewer"))


@router.get("/dns", response_model=sd.DnsPayload)
def dns(hours: int = 24, network: str = "", ctx=VIEWER, db: DbSession = Depends(get_db)):
    user, tenant, role = ctx
    # `hours` is the cost bound on clients()/lists()/the rollup merge
    # (payloads/dnsview.py) -- clamp it here so a caller can't dial an
    # unbounded DnsQuery/DnsRollup scan back in via the query string. The
    # upper bound is the tenant's own retained window (raw + rollup days),
    # per Task 55 -- past that there is nothing left to read anyway.
    pol = retention.policy(tenant)
    max_hours = (pol["dns_raw_days"] + pol["dns_rollup_days"]) * 24
    hours = max(1, min(hours, max_hours))
    return dns_payload.build(db, tenant, hours=hours,
                             net=networks.resolve(db, tenant.id, network))
