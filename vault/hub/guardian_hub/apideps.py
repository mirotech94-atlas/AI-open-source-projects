"""Authentication, rate limiting and tenant scoping for the public REST API.

This is the whole trust boundary of api.guarder.io, and it is deliberately
separate from `deps.py`:

- `deps.py` authenticates a **browser session** from the `guardian_session`
  cookie and derives the role from `Membership` at request time.
- this module authenticates a **bearer token** from `Authorization`, and reads
  the role off the token row where `apikeys.mint` froze it.

The cookie is never consulted here and a session can never authenticate an API
call. That is not a stylistic preference: the API answers on a different
hostname, so a cookie-authenticated API would be a cross-site request forgery
surface on every write in it.

What a dependency returns
-------------------------
`ApiPrincipal`, whose `.tenant` / `.role` / `.who` are exactly the three things
the hub's existing payload builders and write helpers already take
(`overview_payload.build(db, tenant)`,
`apply_alert_op(db, tenant, key, op, who, role, hours, ip)`). An API read or
write is therefore a call into the same code the dashboard uses, with a
different principal -- not a second implementation that could drift from it.

Tenant scoping
--------------
`ApiPrincipal.tenant` is loaded from `ApiToken.tenant_id` and there is no way
for a caller to name a different one: no tenant path parameter, no tenant
query parameter, no header. Every route filters on `principal.tenant.id`, so
another tenant's identifier is simply a row that does not exist -- **404, never
403**. A 403 would confirm the identifier is real, which is a cross-tenant
information leak in itself.

Rate limiting
-------------
Four buckets, all through the hub's existing limiter:

- `apiv1:tok:<token id>` -- the per-credential ceiling. Keyed on the row id,
  never on the raw secret, so no bucket key is ever a credential.
- `apiv1:tok:<token id>:w` -- a tighter ceiling on writes, since a write is far
  more expensive and far rarer than a read.
- `apiv1:bad:<addr>` -- failed credentials only, per address.
- `apiv1:bad` -- failed credentials, hub-wide, because a per-address bucket is
  no defence against a distributed spray.

The failure buckets are charged **only when a credential actually fails**, which
means the lookup happens first. That ordering is deliberate, and it is a real
trade:

- Charging before the lookup, or gating on the bucket before it, would make a
  full failure bucket refuse *everyone* from that address -- and behind NAT, or
  a corporate egress, "that address" is a whole office. One person's misconfigured
  script would take out their colleagues' working ones. An earlier version of this
  module did exactly that with a shared anonymous bucket, and capped authenticated
  callers at half their documented ceiling.
- The cost of the chosen order is that a throttled probe still spends one indexed
  lookup on `token_hash` before being refused. That is cheap and bounded (the
  server's own concurrency bounds it further), and it buys the property that
  matters: a valid token's only ceiling is its own.

The genuinely anonymous routes -- `/v1/health`, `/v1/openapi.json`, `/v1/docs`
-- have no credential to key on, so they use `apiv1:ip:<addr>` via
`anon_guard`.
"""

import datetime as dt
from dataclasses import dataclass

from fastapi import Depends, HTTPException, Request
from sqlalchemy.orm import Session as DbSession

from . import apikeys, models as m, ratelimit
from .clientip import client_ip
from .db import get_db
from .deps import ROLE_ORDER

#: Per-token ceiling: 120 calls a minute is roughly two a second sustained,
#: which is generous for a polling integration and far below what would make a
#: single hub's SQLite file unhappy.
PER_TOKEN = (120, 60)
#: Per-token write ceiling. Writes queue sensor work and append audit rows;
#: nothing legitimate needs thirty a minute.
PER_TOKEN_WRITE = (30, 60)
#: Per-address ceiling for the documentation routes, which have no credential to
#: key on. Generous for a person reading the reference, low enough that the
#: rendered page cannot be used as an amplifier.
PER_IP_ANON = (60, 60)
#: Failed credentials, per address. Twenty wrong tokens a minute from one place
#: is already nothing a real client does.
PER_IP_BAD = (20, 60)
#: Failed credentials, hub-wide. A per-address bucket is no defence against a
#: spray from a thousand addresses; this is. Sized so it could only be reached by
#: an actual attack -- a whole deployment's worth of clients misconfiguring
#: themselves at once would not come close.
GLOBAL_BAD = (600, 60)

#: Sent with every 429 so a client backs off deterministically rather than
#: hammering. One window, in seconds.
_RETRY_AFTER = str(PER_TOKEN[1])

#: The `WWW-Authenticate` challenge on a 401. Named scheme only -- no realm
#: string that could hint at internal naming.
_CHALLENGE = {"WWW-Authenticate": "Bearer"}


@dataclass
class ApiPrincipal:
    """One authenticated API caller.

    `tenant` and `role` are the same two values `deps.require_role` hands a
    dashboard route, so a handler can be written once against either.

    `who` is the audit identity (`token:gapi_########`) and `ip` the caller's
    address -- both are passed straight into `audit.log` by every write, which
    is how "audit-logged with the token's identity, the role, the tenant and
    the IP" is satisfied without each route remembering to assemble it.
    """

    token: m.ApiToken
    tenant: m.Tenant
    role: str
    scope: str
    who: str
    ip: str

    def as_ctx(self) -> tuple[None, m.Tenant, str]:
        """The `(user, tenant, role)` triple shape `deps.require_role` returns.

        `user` is None: there is no `m.User` behind a token, and any helper
        that needs a name must take `who` instead. Handlers that only unpack
        `tenant`/`role` can then be shared verbatim with the dashboard.
        """
        return None, self.tenant, self.role


def _too_many() -> HTTPException:
    return HTTPException(429, "rate limit exceeded", headers={"Retry-After": _RETRY_AFTER})


def _bearer(request: Request) -> str:
    """The raw token from `Authorization: Bearer <token>`, or "".

    Only this header. Not a query parameter -- a credential in a URL ends up in
    every access log, every proxy log and every browser history along the way --
    and not a cookie.
    """
    header = request.headers.get("authorization") or ""
    scheme, _, value = header.partition(" ")
    if scheme.lower() != "bearer":
        return ""
    return value.strip()


def _reject(bad_key: str, *, missing: bool):
    """Charge the failure buckets and raise. Never returns.

    `ratelimit.allow` records a hit only when it returns True, so a sustained
    flood keeps being refused off the same recorded window rather than pushing it
    forward for ever. Both buckets are charged on every failure: the per-address
    one catches one wrong client, the hub-wide one catches a spray from a
    thousand addresses that no per-address bucket can see.

    The 401 message distinguishes "you sent no credential" from "the credential
    you sent did not work", and nothing finer -- malformed, unknown, revoked,
    expired and orphaned all read identically, so a probe cannot learn that a
    token exists or once did.
    """
    ip_ok = ratelimit.allow(bad_key, *PER_IP_BAD)
    global_ok = ratelimit.allow("apiv1:bad", *GLOBAL_BAD)
    if not (ip_ok and global_ok):
        raise _too_many()
    if missing:
        raise HTTPException(401, "an API token is required", headers=_CHALLENGE)
    raise HTTPException(401, "invalid API token", headers=_CHALLENGE)


def anon_guard(request: Request) -> str:
    """Rate-limit an uncredentialed call by address and return that address.

    Used directly by the public documentation routes (`/v1/openapi.json`,
    `/v1/docs`, `/v1/health`), which have no token to key on.
    """
    ip = client_ip(request)
    if not ratelimit.allow(f"apiv1:ip:{ip}", *PER_IP_ANON):
        raise _too_many()
    return ip


def api_principal(request: Request, db: DbSession = Depends(get_db)) -> ApiPrincipal:
    """Authenticate a bearer token, or raise.

    A valid token spends nothing but its own per-token bucket. The failure
    buckets are charged only by attempts that actually failed -- see the module
    docstring for why that ordering is worth one indexed lookup per refused
    probe.
    """
    ip = client_ip(request)
    raw = _bearer(request)

    row = apikeys.authenticate(db, raw) if raw else None
    if row is None:
        return _reject(f"apiv1:bad:{ip}", missing=not raw)

    if not ratelimit.allow(f"apiv1:tok:{row.id}", *PER_TOKEN):
        raise _too_many()

    tenant = db.get(m.Tenant, row.tenant_id)
    if tenant is None:
        # The tenant was deleted under a live token. Treated as an invalid
        # credential, not a 500: there is nothing this token can now be scoped
        # to, and saying more would confirm the token itself was real.
        return _reject(f"apiv1:bad:{ip}", missing=False)

    apikeys.touch(db, row, ip)
    return ApiPrincipal(token=row, tenant=tenant, role=row.role, scope=row.scope,
                        who=apikeys.identity(row), ip=ip)


def require_api(min_role: str = "viewer", *, write: bool = False):
    """Dependency factory: authenticate, then enforce scope and role.

    `write=True` demands `scope == "write"` and charges the write bucket;
    `min_role` is checked against the role frozen on the token row. Both must
    pass -- a `write`-scope token whose role is `viewer` can read everything a
    viewer can read and write nothing, which is exactly what "a token must
    never exceed the role of whoever created it" means when a viewer creates
    one.
    """

    def dep(principal: ApiPrincipal = Depends(api_principal)) -> ApiPrincipal:
        if write:
            if principal.scope != "write":
                raise HTTPException(403, "this token is read-only")
            if not ratelimit.allow(f"apiv1:tok:{principal.token.id}:w", *PER_TOKEN_WRITE):
                raise _too_many()
        if ROLE_ORDER.get(principal.role, -1) < ROLE_ORDER[min_role]:
            raise HTTPException(403, f"this token needs the {min_role} role")
        return principal

    return dep


def page(limit: int, offset: int, *, max_limit: int = 200) -> tuple[int, int]:
    """Validate and clamp a `?limit=&offset=` pair.

    Clamped rather than 422'd on the upper bound so a client asking for more
    than the ceiling gets the ceiling and a truthful `limit` back in the
    envelope, instead of an error it has to special-case. A negative value is
    a real mistake, though, and 422s.
    """
    if limit < 1 or offset < 0:
        raise HTTPException(422, "limit must be >= 1 and offset >= 0")
    return min(limit, max_limit), offset


def envelope(items: list, total: int, limit: int, offset: int) -> dict:
    """The one list shape every paginated v1 route returns."""
    return {"items": items, "total": total, "limit": limit, "offset": offset}


def iso(value: dt.datetime | None) -> str | None:
    return value.isoformat() if value else None
