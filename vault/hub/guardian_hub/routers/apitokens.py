"""The app's own API surfaces: token management, and the API guide's content.

Both are session-authenticated dashboard routes under `/api/v1`, reachable only
from the app's hostname — deliberately *not* from api.guarder.io, whose nginx
block serves `/v1/` and denies everything else. A token can therefore never
mint another token, extend itself, or read the list of its siblings, which is
what keeps a leaked credential a bounded problem.

- `GET/POST /api/v1/settings/api-tokens`, `DELETE .../{id}` — admin only.
- `GET /api/v1/api-guide` — any signed-in member, because the guide is
  documentation and a viewer wiring up a read-only client needs it.
"""

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session as DbSession

from .. import apidocs, apikeys, audit, models as m, ratelimit, schemas_api as sa
from ..clientip import client_ip
from ..db import get_db
from ..deps import ROLE_ORDER, require_role

router = APIRouter(prefix="/api/v1", tags=["api-tokens"])

ADMIN = Depends(require_role("admin"))
VIEWER = Depends(require_role("viewer"))

#: Minting is the one action here that produces a credential, so it gets its own
#: ceiling on top of the admin role check: ten new tokens in an hour is far more
#: than any real administration, and a hijacked admin session cannot turn into a
#: token factory.
MINT_LIMIT = (10, 3600)


class TokenCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=apikeys.MAX_NAME,
                      description="What this token is for, so it can be recognised later.")
    scope: str = Field("read", description="read or write.")
    role: str = Field("viewer", description="viewer, operator or admin. Clamped to your own role.")
    days: int | None = Field(apikeys.DEFAULT_DAYS,
                             description="Lifetime in days, or null for a token that never expires.")


def _rows(db: DbSession, tenant_id: str) -> list[dict]:
    rows = (db.query(m.ApiToken).filter_by(tenant_id=tenant_id)
            .order_by(m.ApiToken.created.desc()).all())
    return [apikeys.out(r) for r in rows]


@router.get("/settings/api-tokens", response_model=sa.ApiTokensOut)
def list_tokens(ctx=ADMIN, db: DbSession = Depends(get_db)):
    """Every token this account has ever had, revoked ones included.

    Revoked rows stay visible on purpose: an audit entry names the token that
    wrote it, and a reader needs to be able to look that name up afterwards.
    """
    user, tenant, role = ctx
    return sa.ApiTokensOut(tokens=_rows(db, tenant.id), max_role=role,
                           base_url=apidocs.base_url())


@router.post("/settings/api-tokens", response_model=sa.ApiTokenCreated, status_code=201)
def create_token(body: TokenCreate, request: Request, ctx=ADMIN,
                 db: DbSession = Depends(get_db)):
    """Mint one token and return its secret — the only time the secret exists
    outside the caller's hand.

    The audit row records the name, the prefix, the scope and the role, and
    never the secret: an audit log is read by more people than a credential
    should be.
    """
    user, tenant, role = ctx
    ip = client_ip(request)
    if not ratelimit.allow(f"apitoken:mint:{tenant.id}", *MINT_LIMIT):
        raise HTTPException(429, "too many tokens created recently — try again later")

    if body.role not in ROLE_ORDER:
        raise HTTPException(422, "role must be viewer, operator or admin")
    try:
        row, secret = apikeys.mint(
            db, tenant, name=body.name, scope=body.scope, role=body.role,
            creator_role=role, created_by=user.username, days=body.days,
        )
    except apikeys.ApiTokenError as exc:
        raise HTTPException(422, str(exc)) from exc

    audit.log(db, tenant.id, user.username, role, "apitoken.create", target=row.prefix, ip=ip,
              body={"name": row.name, "scope": row.scope, "role": row.role,
                    "expires": apikeys.out(row)["expires"]})
    db.commit()
    return sa.ApiTokenCreated(token=sa.ApiTokenRow(**apikeys.out(row)), secret=secret)


@router.delete("/settings/api-tokens/{token_id}", response_model=sa.ApiTokenRow)
def revoke_token(token_id: str, request: Request, ctx=ADMIN, db: DbSession = Depends(get_db)):
    """Turn one token off, permanently. Tenant-scoped in the lookup, so another
    account's token id is a 404, not a 403."""
    user, tenant, role = ctx
    row = apikeys.revoke(db, tenant.id, token_id)
    if row is None:
        raise HTTPException(404, "token not found")
    audit.log(db, tenant.id, user.username, role, "apitoken.revoke", target=row.prefix,
              ip=client_ip(request), body={"name": row.name})
    db.commit()
    return sa.ApiTokenRow(**apikeys.out(row))


@router.get("/api-guide", response_model=sa.ApiGuide)
def api_guide(request: Request, ctx=VIEWER):
    """The content of the app's API-guide section, generated from the very same
    OpenAPI document the reference at the API host renders.

    Nothing here is hand-written per endpoint: add a route to the public API and
    it appears in this payload, in the reference, and in the OpenAPI document,
    at once. That is the whole point — a hand-maintained guide drifts the first
    time someone forgets it.

    Viewer-readable rather than admin-only: the guide describes an API, not the
    account, and a developer holding a read-only login is exactly who needs it.
    Creating a token is still admin-only.
    """
    return apidocs.guide(request.app)
