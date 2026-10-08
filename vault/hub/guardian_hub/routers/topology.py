from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session as DbSession

from ..db import get_db
from ..deps import require_role
from ..payloads import topology as topology_payload
from .. import networks, schemas_dash as sd

router = APIRouter(prefix="/api/v1", tags=["dashboard-topology"])

VIEWER = Depends(require_role("viewer"))


@router.get("/topology", response_model=sd.TopologyPayload)
def topology(cluster_by: str = "kind", network: str = "", ctx=VIEWER, db: DbSession = Depends(get_db)):
    user, tenant, role = ctx
    return topology_payload.build(db, tenant, cluster_by=cluster_by,
                                  net=networks.resolve(db, tenant.id, network))
