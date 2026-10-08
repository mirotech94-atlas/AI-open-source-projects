"""Public-API credentials: minting, revoking and authenticating an `ApiToken`.

Everything about how a token is shaped, stored, clamped and identified lives
here so there is exactly one answer to each of those questions.

Token shape
-----------
A raw token reads `gapi_<8 hex>_<48 hex>` -- for example
`gapi_9f2c41ab_1d3e...`. Three properties are deliberate:

- **`gapi_` prefix.** A credential that leaks into a log, a shell history or a
  pasted config is recognisable at a glance as a Vault API token, so it can
  be found and revoked. This is the same reasoning behind Stripe's `sk_` and
  GitHub's `ghp_`.
- **A public head (`PREFIX_LEN` hex after `gapi_`) that is stored in clear.**
  `ApiToken.prefix` is the handle an admin matches a log line back to a row
  with. It is not a secret and not enough to authenticate with.
- **The secret in the tail.** Only `sha256(whole raw token)` is stored
  (`security.hash_token`), in a unique, indexed column, so authentication is a
  single indexed lookup and a database dump yields nothing usable.

The raw token is returned by `mint()` and never again. There is no recovery
route, by design: a lost token is revoked and replaced.

Scope and role
--------------
Two independent axes, both frozen on the row at creation:

- `scope` is `read` (GET only) or `write` (GET plus the v1 writes).
- `role` mirrors the dashboard's own `viewer|operator|admin` ladder, and is
  **clamped to the creating admin's own role** by `mint()`. A token can never
  exceed the authority of whoever created it, and because the dependency reads
  `role` off the row rather than re-deriving it from `Membership`, a token
  cannot later gain authority when its creator is promoted -- nor keep an
  authority path open once its creator is removed from the tenant, because the
  row is still bound to one tenant and one frozen role.

Audit identity
--------------
`identity()` returns `token:<prefix>`, which is what every audit row a token
writes carries as its `user`. `m.Audit.user` and `m.Edit.who` are `String(64)`
and `token:gapi_########` is 19 characters, so it can never truncate -- which
is why the identity is built from the prefix rather than the (user-chosen,
non-unique, up-to-64-character) `name`.
"""

import datetime as dt

from sqlalchemy.orm import Session as DbSession

from . import models as m
from .deps import ROLE_ORDER
from .security import hash_token, new_token

#: Human-recognisable head of every raw token.
TOKEN_PREFIX = "gapi_"
#: Hex characters of public id after `TOKEN_PREFIX`. Stored in clear.
PREFIX_LEN = 8
#: Hex characters of secret after the public id.
SECRET_LEN = 48

SCOPES = ("read", "write")
#: Longest name the UI accepts. Kept at the column width so a name can never
#: be silently truncated on the way in.
MAX_NAME = 64
#: How many live (unrevoked, unexpired) tokens one tenant may hold. Generous
#: for the intended use -- a handful of integrations -- and low enough that a
#: compromised admin session cannot quietly mint thousands.
MAX_LIVE_PER_TENANT = 25
#: Longest life an admin may ask for, and the default. A token with no expiry
#: is allowed (`days=None`) because a long-lived integration credential is a
#: real need, but the UI defaults to a bounded one.
MAX_DAYS = 730
DEFAULT_DAYS = 365


def _now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


class ApiTokenError(Exception):
    """A token could not be minted: bad scope, bad name, or the per-tenant
    cap is full. Raised only on the admin-facing mint path, never on the
    authenticating path -- an authentication failure must not distinguish its
    reasons to the caller."""


def identity(row: m.ApiToken) -> str:
    """The `Audit.user` / `Edit.who` string for writes this token makes."""
    return f"token:{row.prefix}"


def clamp_role(requested: str, creator_role: str) -> str:
    """The role a token may actually carry: the lower of what was asked for
    and what its creator holds. An unrecognised `requested` falls back to
    `viewer` rather than to the creator's role -- fail closed."""
    if requested not in ROLE_ORDER:
        requested = "viewer"
    if creator_role not in ROLE_ORDER:
        return "viewer"
    return requested if ROLE_ORDER[requested] <= ROLE_ORDER[creator_role] else creator_role


def live_tokens(db: DbSession, tenant_id: str) -> list[m.ApiToken]:
    """This tenant's usable tokens: not revoked, not past their expiry."""
    now = _now()
    rows = db.query(m.ApiToken).filter_by(tenant_id=tenant_id, revoked=False).all()
    return [r for r in rows if r.expires is None or r.expires > now]


def mint(db: DbSession, tenant, *, name: str, scope: str, role: str, creator_role: str,
         created_by: str, days: int | None = DEFAULT_DAYS) -> tuple[m.ApiToken, str]:
    """Create one token for `tenant` and return `(row, raw_secret)`.

    Does not commit -- same convention as `acttokens.mint` and `audit.log`:
    the caller commits alongside its own audit row, so a minted token and the
    record of who minted it are one transaction or neither.

    The returned raw secret is the ONLY time it exists outside the caller's
    hand. Callers must put it in the response body and nowhere else -- never a
    log line, never an audit `body`.
    """
    name = (name or "").strip()[:MAX_NAME]
    if not name:
        raise ApiTokenError("a token needs a name")
    if scope not in SCOPES:
        raise ApiTokenError("scope must be read or write")
    if days is not None and not 1 <= days <= MAX_DAYS:
        raise ApiTokenError(f"days must be between 1 and {MAX_DAYS}")
    if len(live_tokens(db, tenant.id)) >= MAX_LIVE_PER_TENANT:
        raise ApiTokenError(
            f"this account already holds {MAX_LIVE_PER_TENANT} live API tokens -- "
            "revoke one before creating another"
        )

    effective_role = clamp_role(role, creator_role)
    # `new_token()` is 64 hex characters; the public id and the secret are cut
    # from independent draws so the stored-in-clear prefix reveals nothing
    # about the secret half.
    public = new_token()[:PREFIX_LEN]
    secret = new_token()[:SECRET_LEN]
    prefix = f"{TOKEN_PREFIX}{public}"
    raw = f"{prefix}_{secret}"

    row = m.ApiToken(
        tenant_id=tenant.id, name=name, prefix=prefix, token_hash=hash_token(raw),
        scope=scope, role=effective_role, created_by=created_by[:64],
        expires=(_now() + dt.timedelta(days=days)) if days else None,
    )
    db.add(row)
    return row, raw


def revoke(db: DbSession, tenant_id: str, token_id: str) -> m.ApiToken | None:
    """Turn one of `tenant_id`'s tokens off, or return None if this tenant has
    no such token. Tenant-scoped in the query itself, so another tenant's id
    is indistinguishable from one that does not exist.

    The row is kept (`revoked=True`) rather than deleted: audit rows the token
    wrote name it by prefix, and those names must stay resolvable. It also
    keeps the hash on file, so the retired secret can never be re-minted.
    """
    row = db.query(m.ApiToken).filter_by(tenant_id=tenant_id, id=token_id).first()
    if not row:
        return None
    if not row.revoked:
        row.revoked = True
        row.revoked_at = _now()
    return row


def authenticate(db: DbSession, raw: str) -> m.ApiToken | None:
    """Resolve a raw bearer token to its row, or None.

    One indexed lookup on the unique `token_hash`. Every failure mode --
    malformed, unknown, revoked, expired -- returns None with no distinction,
    and callers must answer all of them identically: a probe must not be able
    to learn that a prefix exists, or that a token was once valid.

    Does not touch `last_used`; that is `touch()`, called only after the
    request is authorised, so a rejected call leaves no trace on the row.
    """
    raw = (raw or "").strip()
    if not raw.startswith(TOKEN_PREFIX):
        return None
    row = db.query(m.ApiToken).filter_by(token_hash=hash_token(raw)).first()
    if not row or row.revoked:
        return None
    if row.expires is not None and row.expires <= _now():
        return None
    return row


#: Only record a `last_used` bump when the stored value is at least this old.
#: Without it every single API call becomes a write, and a polling client would
#: turn a read-only endpoint into one UPDATE per poll on a shared SQLite file.
TOUCH_INTERVAL_S = 60


def touch(db: DbSession, row: m.ApiToken, ip: str = "") -> None:
    """Record that `row` was just used, at most once per `TOUCH_INTERVAL_S`.

    Commits its own bump: it is bookkeeping, not part of whatever the request
    goes on to do, and must not be rolled back with a failing write nor left
    pending for a read-only handler that never commits.
    """
    now = _now()
    if row.last_used is not None and (now - row.last_used).total_seconds() < TOUCH_INTERVAL_S:
        return
    row.last_used = now
    row.last_used_ip = (ip or "")[:45]
    db.commit()


def out(row: m.ApiToken) -> dict:
    """The row as the app's token list shows it. No hash, and no secret --
    there is no code path that can produce the secret after `mint` returned."""
    return {
        "id": row.id,
        "name": row.name,
        "prefix": row.prefix,
        "scope": row.scope,
        "role": row.role,
        "created_by": row.created_by,
        "created": row.created.isoformat() if row.created else None,
        "expires": row.expires.isoformat() if row.expires else None,
        "last_used": row.last_used.isoformat() if row.last_used else None,
        "last_used_ip": row.last_used_ip,
        "revoked": bool(row.revoked),
    }
