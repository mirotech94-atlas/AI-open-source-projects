"""One-shot action tokens: ntfy / Telegram push buttons *and* Vault
chat's tier-3 confirmations.

Two shapes share one table and one burn primitive:

- `kind` in ("device", "alert"): the public `/act/<token>/<action>` route
  (routers/public.py) authenticates on the row alone -- no session, no
  tenant context from the caller. `mint()` creates it, `consume()`
  validates + burns it for exactly one of trust/snooze/ack.
- `kind == "chat"`: a chat tier-3 (or escalated tier-2) proposal.
  `mint_chat()` binds the row to one tool, one canonicalised argument set
  and one user id; `consume_chat()` requires an exact match of all four
  (tenant, tool, args, user) before burning it.

`_burn()` is the single atomic primitive both consume paths use -- chat
code must never read-modify-write a token itself.
"""
import datetime as dt
import json

from sqlalchemy import bindparam, text
from sqlalchemy.orm import Session as DbSession

from . import models as m
from .db import TZDateTime
from .security import new_token


def _now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


class ActTokenError(Exception):
    """Raised by `consume()` for an unknown, expired, action-mismatched or
    already-used token. Callers must not use its message to distinguish
    those cases in the response -- the public route never reveals which one
    it was, or which tenant (if any) the token belonged to."""


#: Push-button actions a mint()ed token may carry. "fix" is deliberately
#: excluded -- the controller ruling for the fix-round is that a public,
#: unauthenticated push-button link must never be able to trigger a fix
#: run (an actual command execution), only the reversible trust/snooze/ack
#: writes. Fix stays a dashboard-only, authenticated action (routers/write.py).
ALLOWED_ACTIONS = frozenset({"trust", "snooze", "ack"})

#: `kind` of a chat confirmation row, and the value written into `used`
#: when one is burnt. `ActToken.used` is String(16), so it can never carry
#: a tool name (`decide_recommendation` alone is 21 chars) -- a burnt chat
#: row always reads exactly "chat".
CHAT_KIND = "chat"
_CHAT_USED = "chat"

#: How many unconsumed chat confirmations one user may hold at once. On
#: mint, anything older beyond this is expired (not deleted -- the row
#: stays as an audit trace) and every already-expired chat row for the
#: tenant is purged opportunistically.
CHAT_TOKEN_CAP = 5

# `now` is bound through TZDateTime so the comparison goes through the same
# adapter the ORM uses, rather than sqlite3's deprecated default datetime
# adapter (removed in 3.12+).
_BURN_SQL = text(
    "UPDATE act_token SET used = :used WHERE token = :token AND used = '' AND expires > :now"
).bindparams(bindparam("now", type_=TZDateTime))


def _burn(db: DbSession, token: str, used: str) -> bool:
    """Atomically mark `token` used, returning whether this caller won.

    A single `UPDATE ... WHERE token=? AND used='' AND expires>?` guarded
    by its rowcount: under N concurrent callers racing the same token, the
    WHERE clause (re-checked by SQLite for every row it considers, under
    that connection's write lock) only ever matches for one caller --
    every other caller's UPDATE affects zero rows and returns False.
    Commits on a win precisely so that win is durable before the caller's
    own follow-on write begins. Never rolls back on a loss: the caller
    owns whatever else its session had pending.
    """
    result = db.execute(_BURN_SQL, {"used": used, "token": token, "now": _now()})
    if result.rowcount != 1:
        return False
    db.commit()
    return True


def mint(db: DbSession, tenant, kind: str, target: str, actions: list[str], hours: int = 48) -> str:
    """Insert a fresh ActToken for `tenant` and return the raw token.

    `kind` is "device" or "alert"; `target` is the mac or alert key the
    token acts on; `actions` is the subset of trust/snooze/ack this token
    is allowed to perform (each usable once -- see `consume`). Raises
    ValueError if any requested action is not in ALLOWED_ACTIONS (e.g.
    "fix" -- push-button links can never trigger a fix run)."""
    bad = set(actions) - ALLOWED_ACTIONS
    if bad:
        raise ValueError(f"act tokens cannot mint action(s): {sorted(bad)}")
    token = new_token()
    row = m.ActToken(
        token=token, tenant_id=tenant.id, kind=kind, target=target,
        actions=list(actions), expires=_now() + dt.timedelta(hours=hours),
    )
    db.add(row)
    return token


def consume(db: DbSession, token: str, action: str) -> m.ActToken:
    """Validate `token` for `action` and atomically mark it used.

    The read (existence + action-membership) is racy by nature -- it's the
    write that matters, and that is `_burn`. A `kind="chat"` row can never
    be spent here: its `actions` holds one dict, so `action not in
    row.actions` is always true for the "trust"/"snooze"/"ack" strings the
    public route passes.
    """
    row = db.get(m.ActToken, token)
    if not row or action not in (row.actions or []):
        raise ActTokenError("unknown token or action not permitted for it")
    if not _burn(db, token, action):
        raise ActTokenError("expired or already used")
    db.refresh(row)
    return row


# --------------------------------------------------------------------------
# kind="chat" -- Vault chat tier-3 confirmations
# --------------------------------------------------------------------------

def canon_args(args: dict | None) -> str:
    """The canonical form a chat confirmation is bound to: `json.dumps`
    with `sort_keys=True`, so `{"a":1,"b":2}` and `{"b":2,"a":1}` bind
    identically while any change of value or key does not."""
    return json.dumps(args or {}, sort_keys=True, default=str)


def _chat_blob(row: "m.ActToken") -> dict:
    actions = row.actions if isinstance(row.actions, list) else []
    first = actions[0] if actions else {}
    return first if isinstance(first, dict) else {}


def _purge_expired_chat(db: DbSession, tenant_id: str) -> None:
    now = _now()
    rows = db.query(m.ActToken).filter_by(tenant_id=tenant_id, kind=CHAT_KIND).all()
    for row in rows:
        # Expiry stops execution immediately. Keep the proposal for the
        # same 30-day review window as operational history.
        if row.expires and row.expires < now - dt.timedelta(days=30):
            db.delete(row)


def _cap_chat_tokens(db: DbSession, tenant_id: str, user_id: str) -> None:
    """Expire this user's oldest unconsumed chat confirmations so that,
    counting the one about to be minted, no more than CHAT_TOKEN_CAP are
    live at once. Expired rather than deleted -- an expired row still
    shows a reviewer that a proposal was made."""
    now = _now()
    live = [
        row for row in db.query(m.ActToken).filter_by(tenant_id=tenant_id, kind=CHAT_KIND, used="").all()
        if row.expires and row.expires > now and _chat_blob(row).get("user_id") == user_id
    ]
    live.sort(key=lambda r: (r.created or now))
    for row in live[: max(0, len(live) - (CHAT_TOKEN_CAP - 1))]:
        row.expires = now - dt.timedelta(seconds=1)


def mint_chat(db: DbSession, tenant, tool: str, args: dict, user_id: str, payload: dict,
              minutes: int = 10) -> tuple[str, dt.datetime]:
    """Insert a chat confirmation bound to (tenant, tool, args, user) and
    return `(token, expires)`. Does not commit -- same convention as
    `mint`/`audit.log`, the caller commits alongside its own writes."""
    _purge_expired_chat(db, tenant.id)
    _cap_chat_tokens(db, tenant.id, user_id)
    token = new_token()
    expires = _now() + dt.timedelta(minutes=minutes)
    blob = {**payload, "tool": tool, "user_id": user_id, "args_canon": canon_args(args)}
    db.add(m.ActToken(token=token, tenant_id=tenant.id, kind=CHAT_KIND, target=tool[:200],
                      actions=[blob], expires=expires))
    return token, expires


def consume_chat(db: DbSession, token: str, *, tenant_id: str, tool: str, args: dict,
                 user_id: str) -> bool:
    """Burn a chat confirmation, but only for the exact proposal it was
    minted for: same tenant, same tool, same canonicalised args, same
    user. Returns False (never raises) for anything else -- a wrong-tenant,
    wrong-tool, wrong-args, wrong-user, expired or already-burnt token.

    The four bindings are immutable on the row, so reading them before the
    atomic `_burn` is safe: no concurrent caller can change what this token
    is bound to, only whether it is still unspent -- which is exactly what
    `_burn` decides, once, for everyone.
    """
    row = db.get(m.ActToken, token)
    if not row or row.kind != CHAT_KIND or row.tenant_id != tenant_id:
        return False
    blob = _chat_blob(row)
    if blob.get("tool") != tool or blob.get("user_id") != user_id:
        return False
    if blob.get("args_canon") != canon_args(args):
        return False
    return _burn(db, token, _CHAT_USED)


def chat_proposal(db: DbSession, token: str, tenant_id: str) -> dict:
    """The stored proposal blob for an unspent chat token, or `{}`. Read
    only -- `routers/chat.py` uses it to learn which tool + args a
    confirmation names without ever asking the model again."""
    row = db.get(m.ActToken, token)
    if not row or row.kind != CHAT_KIND or row.tenant_id != tenant_id:
        return {}
    return dict(_chat_blob(row))
