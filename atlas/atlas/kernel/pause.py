"""The kill switch: pause everything, or one module, durably.

Miro's reliability plan (2026-09-27), Phase C: "global and per-module pause
controls enforced near every dispatch, plus a durable watch-only mode.
Pausing prevents new effects but cannot erase an already submitted external
request... Resume must be explicit and scoped. Do not auto-disable the kill
switch after a restart or when a model suggests recovery."

* Enforced in `executor.execute`, the one path out (invariant 1), so no
  caller can route around it. A rollback is NOT blocked: undoing an effect
  is what a pause is often for.
* Stored in Postgres (`pauses`), so a restart keeps it. A row is never
  deleted; resuming stamps `resumed_at`, which keeps the history.
* Fails closed: if the pause state cannot be read, nothing dispatches.
* Set and lifted only by Miro -- the `/atlas pause|resume` slash command
  (signed, approver-checked, parsed here, never by a model) or the CLI on
  the server. Nothing in the catalog can resume, and no model can.
* One automatic SET, never a lift (R09, `block_capability`): when a provider
  refuses a credential (401/403, a revoked token), the outbox pauses THAT
  credential, so its queued rows hold instead of each trying the dead login.
  Stopping is the fail-closed direction; resuming stays Miro's.
* A scope is `all`, a module (`instagram`), or one credential of a module
  that holds several, `module:account` (`instagram:clips`), for the modules
  in CREDENTIAL_PARAM. A `module:account` pause stops only the actions whose
  account param names that account; a plain module pause still stops all.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any

ALL = "all"
# Modules whose actions post as one of several accounts, each with its own
# token, and the action param that names the account (with its default when
# the param is left out). A refused login pauses `module:account`, never the
# other accounts' posting.
CREDENTIAL_PARAM: dict[str, tuple[str, str | None]] = {
    "instagram": ("account", "main"),     # INSTAGRAM_<X>_TOKEN per account
    "youtube": ("channel", None),         # YOUTUBE_<X>_REFRESH_TOKEN per channel
    "shop": ("account", None),            # STRIPE_SHOP_<X>_KEY per account
}
# Modules that hold several credentials with no action param saying which
# one a call used (content's publisher posts through Instagram, Facebook
# and Postiz in one run; clips posts through three platforms; gitlab has
# two instances; mail has a token per mailbox; ...). A refused login there
# is TOLD to Miro and never auto-paused: pausing the whole module would
# stop work on credentials that are fine.
NOTIFY_ONLY = frozenset({
    "content", "clips", "gitlab", "mail", "messages", "comms", "messaging",
    "nightshift", "agency", "school", "stripe_billing", "research"})
_ACCOUNT = re.compile(r"[a-z0-9_-]{1,40}")
# What `account_of` returns for a CREDENTIAL_PARAM action whose account
# value cannot be read as one ("../clips", "clips;main", a list, ...). The
# executor validates param NAMES, not values, so such a value reaches the
# pause lookup; there it matches EVERY account-scoped pause of the module
# (fail closed), so a paused `instagram:clips` cannot be slipped past with
# a malformed value. Never a valid account (the pattern has no '*').
ANY_ACCOUNT = "*"
# Scope prefix of a shadow row (kernel/shadow.py). Never a module name: the
# Slack command's scope pattern has no colon, so only the CLI sets or lifts one.
SHADOW = "shadow:"


class PauseUnreadable(Exception):
    """The pause state could not be read; the caller must not dispatch."""


@dataclass(frozen=True, slots=True)
class Pause:
    scope: str
    paused_at: datetime
    paused_by: str
    reason: str

    @property
    def shadow(self) -> bool:
        """A shadow row (kernel/shadow.py): a stop that is also a record."""
        return self.scope.startswith(SHADOW)

    def says(self) -> str:
        what = _what(self.scope.removeprefix(SHADOW))
        why = f" ({self.reason})" if self.reason else ""
        state = ("is in shadow mode (recorded as would-have-run, not dispatched)"
                 if self.shadow else "is paused")
        return (f"{what} {state} since {self.paused_at:%a %d %b %H:%M} UTC by "
                f"{self.paused_by}{why}")


def _what(scope: str) -> str:
    """A scope in words: everything, the X module, the X module's Y account."""
    if scope == ALL:
        return "everything"
    if ":" in scope:
        module, account = scope.split(":", 1)
        return f"the {module} module's {account} account"
    return f"the {scope} module"


def valid_scope(scope: str) -> str | None:
    """The scope normalised, or None when it names nothing that exists. A
    `module:account` scope is valid only for a CREDENTIAL_PARAM module (so
    never `shadow:...`: shadow rows are the CLI's alone)."""
    from .registry import REGISTRY
    s = scope.strip().lower()
    if s in (ALL, "everything", "atlas"):
        return ALL
    if ":" in s:
        module, account = s.split(":", 1)
        ok = (module in CREDENTIAL_PARAM and module in REGISTRY.modules
              and _ACCOUNT.fullmatch(account) is not None)
        return s if ok else None
    return s if s in REGISTRY.modules else None


def account_of(action: str, params: dict[str, Any] | None) -> str | None:
    """The account `action` would act as, for a CREDENTIAL_PARAM module:
    its account param (or that param's default), normalised; ANY_ACCOUNT
    when that value cannot be read as an account. None for any other
    module. Pure; never raises."""
    try:
        spec = CREDENTIAL_PARAM.get(action.split(".", 1)[0])
    except Exception:                                   # noqa: BLE001
        return None
    if spec is None:
        return None
    try:
        name, default = spec
        value = (params or {}).get(name) if isinstance(params, dict) else None
        if value is not None and not isinstance(value, str):
            return ANY_ACCOUNT                          # a list, a number, ...
        account = (value or default or "").strip().lower()
        return account if _ACCOUNT.fullmatch(account) else ANY_ACCOUNT
    except Exception:                                   # noqa: BLE001
        return ANY_ACCOUNT


def credential_scope(action: str, params: dict[str, Any] | None) -> str | None:
    """The pause scope that blocks the credential `action` used: the module,
    or `module:account` for a CREDENTIAL_PARAM module. None when it cannot
    be told which credential it was (a NOTIFY_ONLY module, or a
    CREDENTIAL_PARAM action whose account is unknown): then nothing is
    auto-paused."""
    module = action.split(".", 1)[0]
    if module in NOTIFY_ONLY:
        return None
    if module in CREDENTIAL_PARAM:
        account = account_of(action, params)
        return (f"{module}:{account}" if account and account != ANY_ACCOUNT
                else None)
    return module


async def active_for(module: str, account: str | None = None) -> Pause | None:
    """The pause that stops `module` -- acting as `account`, when given --
    now: a real pause before a shadow row (a pause records nothing, and
    outranks a shadow), the global one first, then the module, then the
    account. A `module:account` row stops only that account; ANY_ACCOUNT
    (an unreadable account value) is stopped by every one of them.

    A shadow row stops dispatch exactly like a pause -- `executor.execute`
    reads it here and audits `execute.paused` with scope `shadow:<module>`,
    which is the executor-path "would have run" record (kernel/shadow.py)."""
    from .state import fetch_one
    try:
        row = await fetch_one(
            """SELECT scope, paused_at, paused_by, reason FROM pauses
                WHERE resumed_at IS NULL AND scope = ANY($1::text[])
                   OR (resumed_at IS NULL AND $2::text IS NOT NULL
                       AND starts_with(scope, $2::text))
                ORDER BY (scope LIKE 'shadow:%') ASC,
                         (scope IN ('all', 'shadow:all')) DESC,
                         (scope LIKE '%:%' AND scope NOT LIKE 'shadow:%') ASC,
                         paused_at
                LIMIT 1""",
            [ALL, module, SHADOW + ALL, SHADOW + module]
            + ([f"{module}:{account}"] if account and account != ANY_ACCOUNT
               else []),
            f"{module}:" if account == ANY_ACCOUNT else None)
    except Exception as exc:
        raise PauseUnreadable(type(exc).__name__) from exc
    if row is None:
        return None
    return Pause(str(row["scope"]), row["paused_at"], str(row["paused_by"]),
                 str(row["reason"] or ""))


async def active() -> list[Pause]:
    from .state import fetch_all
    rows = await fetch_all(
        """SELECT scope, paused_at, paused_by, reason FROM pauses
            WHERE resumed_at IS NULL ORDER BY paused_at""")
    return [Pause(str(r["scope"]), r["paused_at"], str(r["paused_by"]),
                  str(r["reason"] or "")) for r in rows]


async def pause(scope: str, by: str, reason: str = "") -> str:
    from .audit import audit
    from .state import execute_sql
    s = valid_scope(scope)
    if s is None:
        return f"there is no module called {scope!r}; nothing was paused"
    await execute_sql(
        """INSERT INTO pauses (scope, paused_by, reason) VALUES ($1, $2, $3)
           ON CONFLICT (scope) WHERE resumed_at IS NULL DO NOTHING""",
        s, by, reason[:300])
    await audit("pause.set", s, {"by": by, "reason": reason[:300]})
    what = _what(s)
    return (f"{what[0].upper()}{what[1:]} is paused: nothing new will run until you resume it. "
            f"Anything already sent to a provider is not recalled.")


AUTH_REFUSED_BY = "atlas:auth-refused"


async def block_capability(action: str, params: dict[str, Any] | None,
                           status: int | None) -> str | None:
    """A provider refused `action`'s login (R09). Pause that credential --
    `credential_scope` -- and tell Miro once. Returns "paused" when this
    call set the pause, "notified" when the credential cannot be told apart
    (NOTIFY_ONLY, or an unknown account) and he was told instead, None when
    it was already paused or already told recently, or nothing could be
    written. The reason names the status code only, never the provider's
    words, which can carry a token. Never raises."""
    import logging

    from .audit import audit
    from .state import fetch_one
    log = logging.getLogger("atlas.pause")
    module = action.split(".", 1)[0]
    scope = credential_scope(action, params)
    code = f"HTTP {status}" if status else "an auth failure"
    if scope is None:
        # Several credentials, and nothing says which one failed: pausing
        # the module would stop the ones that work. Tell him, once in a
        # while, and let the row's own outcome stand (notice: failed,
        # effect: reconciled).
        try:
            seen = await fetch_one(
                """SELECT id FROM actions_log
                    WHERE event = 'capability.auth_refused' AND subject = $1
                      AND at > now() - interval '6 hours' LIMIT 1""", action)
            if seen is not None:
                return None
            await audit("capability.auth_refused", action,
                        {"status": status, "paused": False})
        except Exception as exc:                        # noqa: BLE001
            log.warning("%s: login refused, not recorded (%s)", action,
                        type(exc).__name__)
            return None
        await _tell(module, f"{action}'s login was refused ({code}). I didn't "
                            f"pause {module}: it uses several logins. Reconnect "
                            f"the one that failed.")
        return "notified"
    reason = f"{action}'s login was refused ({code}); reconnect it, then resume"
    try:
        row = await fetch_one(
            """INSERT INTO pauses (scope, paused_by, reason) VALUES ($1, $2, $3)
               ON CONFLICT (scope) WHERE resumed_at IS NULL DO NOTHING
               RETURNING id""",
            scope, AUTH_REFUSED_BY, reason)
    except Exception as exc:                            # noqa: BLE001
        log.warning("%s: login refused, but the pause was not written (%s)",
                    scope, type(exc).__name__)
        return None
    if row is None:
        return None                                     # already paused
    try:
        await audit("pause.set", scope, {"by": AUTH_REFUSED_BY, "reason": reason})
    except Exception:                                   # noqa: BLE001
        log.warning("pause of %s not audited", scope)
    await _tell(module, f"Paused {scope}: {action}'s login was refused. "
                        f"Reconnect it, then `/atlas resume {scope}`.")
    return "paused"


async def _tell(topic: str, body: str) -> None:
    """One line to Miro. Best effort."""
    import logging
    try:
        from . import comms
        from .contracts import Severity
        await comms.notify(comms.Outbound(
            title="A login was refused", body=body, severity=Severity.HIGH,
            topic=topic))
    except Exception:                                   # noqa: BLE001
        logging.getLogger("atlas.pause").warning("notice for %s not sent", topic)


async def lines() -> list[str]:
    """What is paused now, one line each (shadow rows excluded: those are
    the shadow report's). For the brief and the weekly: a pause set by a
    refused login waits for him, so it is said until he resumes it."""
    return [p.says() for p in await active() if not p.shadow]


async def resume(scope: str, by: str) -> str:
    from .audit import audit
    from .state import fetch_one
    s = valid_scope(scope)
    if s is None:
        return f"there is no module called {scope!r}; nothing was resumed"
    row = await fetch_one(
        """UPDATE pauses SET resumed_at = now(), resumed_by = $2
            WHERE scope = $1 AND resumed_at IS NULL RETURNING scope""", s, by)
    if row is None:
        return f"{_what(s)} was not paused"
    await audit("pause.lifted", s, {"by": by})
    return f"Resumed {'everything' if s == ALL else _what(s)}."


_COMMAND = re.compile(
    r"^\s*(?P<verb>pause|resume|unpause)\s+"
    r"(?P<scope>(?!shadow:)[a-z_][a-z0-9_]*(?::[a-z0-9_-]+)?)"
    r"(?:\s+(?:because|for|:)\s*(?P<why>.+))?\s*$", re.IGNORECASE)


def parse(text: str) -> tuple[str, str, str] | None:
    """('pause'|'resume', scope, reason) for an exact pause/resume command,
    else None. Parsed here, never by a model: "don't pause yet" or "what
    happens if I pause reels?" is not a command."""
    m = _COMMAND.match(text or "")
    if not m:
        return None
    verb = "resume" if m["verb"].lower() in ("resume", "unpause") else "pause"
    return verb, m["scope"].lower(), (m["why"] or "").strip()


async def command(text: str, by: str) -> str | None:
    """Run an exact pause/resume/pauses command; None when it is not one."""
    if text.strip().lower() in ("pauses", "paused", "what is paused"):
        now = await active()
        return ("Nothing is paused." if not now
                else "\n".join(p.says() for p in now))
    parsed = parse(text)
    if parsed is None:
        return None
    verb, scope, why = parsed
    return await (pause(scope, by, why) if verb == "pause" else resume(scope, by))
