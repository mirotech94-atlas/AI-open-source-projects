"""Leases with fencing tokens (reliability plan RA-B4, docs/durable-core.md).

A lease is a claim with an expiry and a token. The token only ever grows:
every acquire bumps it, and a row is never deleted (release stamps the
expiry instead), so a worker holding an old token can never match again.

    fence = await leases.acquire("scheduler:review", ttl)
    if fence is None:
        return                      # someone else holds it, or unreadable
    try:
        ...                         # the work
        if not await leases.holds(fence):
            return                  # lost it: do not commit
    finally:
        await leases.release(fence)

Three rules, all fail closed:

* An expired lease is not held. The check uses the database clock, inside
  the same statement as the claim, so two processes with skewed clocks
  cannot both believe they hold it.
* An unreadable lease is not held: any database error answers None/False.
  A job that does not run this tick runs on the next one; a duplicate
  effect cannot be taken back.
* A commit that must not land after the lease is lost carries the fence in
  its own WHERE clause (see `kernel.durable` and `kernel.outbox`, whose
  rows are their own leases). `holds` is for work that has no such row.

Workflows and outbox rows keep their lease on the row itself
(`lease_owner`, `lease_expiry`, `fencing_token`); this table is for named
recurring jobs, which have no row of their own.
"""
from __future__ import annotations

import logging
import os
import socket
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta

log = logging.getLogger("atlas.leases")

# One per process: the hostname alone is shared by every worker in a
# container restart loop, and a recycled pid is not an identity either.
OWNER = f"{socket.gethostname()}:{os.getpid()}:{uuid.uuid4().hex[:8]}"


@dataclass(frozen=True, slots=True)
class Fence:
    """Proof of a claim: what was claimed, by whom, with which token."""

    kind: str               # 'lease' | 'workflow' | 'outbox'
    key: str                # lease name, or the row id as text
    owner: str
    token: int
    expires_at: datetime | None = None


def _token(row: object) -> tuple[int, datetime | None] | None:
    if row is None:
        return None
    token = row["fencing_token"]                        # type: ignore[index]
    expires = row["expires_at"]                         # type: ignore[index]
    return int(token), expires


async def acquire(name: str, ttl: timedelta, owner: str = OWNER) -> Fence | None:
    """Claim `name` for `ttl`, or None. Refused while anyone holds it,
    this process included: a holder extends with `renew`. None also when
    the lease cannot be read at all."""
    from .state import fetch_one
    try:
        row = await fetch_one(
            """INSERT INTO leases (name, owner, fencing_token, acquired_at,
                                   expires_at)
                    VALUES ($1, $2, 1, now(), now() + $3::interval)
               ON CONFLICT (name) DO UPDATE
                  SET owner = EXCLUDED.owner,
                      fencing_token = leases.fencing_token + 1,
                      acquired_at = now(),
                      expires_at = EXCLUDED.expires_at
                WHERE leases.expires_at IS NULL OR leases.expires_at <= now()
            RETURNING fencing_token, expires_at""",
            name, owner, ttl)
    except Exception as exc:  # noqa: BLE001
        log.warning("lease %s could not be claimed (%s); not acting",
                    name, type(exc).__name__)
        return None
    got = _token(row)
    if got is None:
        return None
    return Fence("lease", name, owner, got[0], got[1])


async def renew(fence: Fence, ttl: timedelta) -> Fence | None:
    """Extend a lease still held; None when it lapsed or was taken."""
    from .state import fetch_one
    try:
        row = await fetch_one(
            """UPDATE leases SET expires_at = now() + $4::interval
                WHERE name = $1 AND owner = $2 AND fencing_token = $3
                  AND expires_at > now()
            RETURNING fencing_token, expires_at""",
            fence.key, fence.owner, fence.token, ttl)
    except Exception as exc:  # noqa: BLE001
        log.warning("lease %s could not be renewed (%s)", fence.key,
                    type(exc).__name__)
        return None
    got = _token(row)
    if got is None:
        return None
    return Fence("lease", fence.key, fence.owner, got[0], got[1])


async def holds(fence: Fence) -> bool:
    """Is this fence still the current, unexpired holder? False on any
    doubt, including a database that cannot answer."""
    from .state import fetch_one
    try:
        row = await fetch_one(
            """SELECT fencing_token FROM leases
                WHERE name = $1 AND owner = $2 AND fencing_token = $3
                  AND expires_at > now()""",
            fence.key, fence.owner, fence.token)
    except Exception as exc:  # noqa: BLE001
        log.warning("lease %s could not be read (%s); treating it as lost",
                    fence.key, type(exc).__name__)
        return False
    return row is not None


async def release(fence: Fence) -> bool:
    """Give the lease back early. Only the current holder can; the row stays
    (with its token) so the next acquire bumps it rather than restarting."""
    from .state import fetch_one
    try:
        row = await fetch_one(
            """UPDATE leases SET owner = NULL, expires_at = now()
                WHERE name = $1 AND owner = $2 AND fencing_token = $3
            RETURNING name""",
            fence.key, fence.owner, fence.token)
    except Exception as exc:  # noqa: BLE001
        log.warning("lease %s could not be released (%s); it lapses on its own",
                    fence.key, type(exc).__name__)
        return False
    return row is not None
