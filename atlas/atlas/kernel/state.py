"""Postgres access. Thin on purpose — the schema is the interface.

Two things live here that are not just SQL passthrough:

  * A swappable backend. Production is asyncpg; tests install an in-memory
    one. Without this seam the audit chain and the detector cannot be tested
    without a live Postgres, and the parts of ATLAS where a bug is expensive
    are exactly the parts that touch the database.
  * The audit append, which must be serialised. It is not a plain INSERT —
    see `append_audit`.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, ClassVar, Protocol

log = logging.getLogger("atlas.state")

_pool: Any | None = None

# Guards creation of the pool above, not its use. One per event loop: a Lock
# binds to the loop that first waits on it, so a module-level one built at
# import broke the moment a second loop contended for it ("bound to a
# different event loop" -- every full test run, where each test has its own
# loop). The live process has one loop, so this is one lock there.
_POOL_LOCK: asyncio.Lock | None = None
_POOL_LOCK_LOOP: asyncio.AbstractEventLoop | None = None


def _pool_lock() -> asyncio.Lock:
    global _POOL_LOCK, _POOL_LOCK_LOOP
    loop = asyncio.get_running_loop()
    if _POOL_LOCK is None or _POOL_LOCK_LOOP is not loop:
        _POOL_LOCK, _POOL_LOCK_LOOP = asyncio.Lock(), loop
    return _POOL_LOCK


@dataclass(frozen=True, slots=True)
class AuditRow:
    """One link in the hash chain, as stored."""

    id: int
    at: datetime
    event: str
    subject: str
    payload: str
    prev_digest: str
    digest: str


# A callable that, given the previous digest and the canonical timestamp,
# returns this entry's digest. The chain maths lives in audit.py; the backend
# only guarantees it runs while holding the append lock.
DigestFn = Callable[[str, str], str]


class Backend(Protocol):
    async def execute(self, sql: str, *args: Any) -> Any: ...
    async def fetch(self, sql: str, *args: Any) -> list[Any]: ...
    async def fetchrow(self, sql: str, *args: Any) -> Any | None: ...
    async def append_audit(
        self, at: datetime, event: str, subject: str, payload: str,
        digest_fn: DigestFn,
    ) -> None: ...
    async def read_audit(self) -> list[AuditRow]: ...


def canonical_at(at: datetime) -> str:
    """The timestamp form the digest is computed over.

    Always UTC. The column is TIMESTAMPTZ, which Postgres returns normalised,
    so hashing a local-offset string on the way in and re-deriving it on the
    way out produced two different strings and `verify_chain` reported an
    intact log as tampered. Normalising both ends removes the round trip.
    """
    if at.tzinfo is None:
        at = at.replace(tzinfo=UTC)
    return at.astimezone(UTC).isoformat()


class PostgresBackend:
    """asyncpg. The only backend that runs in production."""

    async def _pool(self) -> Any:
        """The one connection pool, created once however many ask at once.

        This used to be a bare `if _pool is None: _pool = await create_pool()`,
        which is a race with teeth. `await` yields, so every coroutine that
        reached the check before the first assignment landed built its own
        pool. The last one won the global and the rest were orphaned —
        unreferenced, never closed, still holding their connections.

        It happens on exactly the busiest moment there is: boot, when the
        scheduler fires its first checks while the dashboard's SSE stream and
        the API's own queries all open at once. Ten pools at `max_size=10`
        filled Postgres's hundred and every query afterwards returned
        `TooManyConnectionsError` — which the dashboard rendered as a system
        watching nothing at all, because a 500 from every endpoint and an
        estate with no modules look identical from the browser.

        Double-checked: the fast path stays lock-free once the pool exists.
        """
        global _pool
        if _pool is not None:
            return _pool
        async with _pool_lock():
            if _pool is None:
                import asyncpg
                _pool = await asyncpg.create_pool(
                    _dsn(), min_size=2, max_size=10,
                    # Recycle rather than hold forever, so a pool that is
                    # briefly busy does not keep its peak for the life of
                    # the process.
                    max_inactive_connection_lifetime=300.0,
                )
        return _pool

    async def execute(self, sql: str, *args: Any) -> Any:
        return await (await self._pool()).execute(sql, *args)

    async def fetch(self, sql: str, *args: Any) -> list[Any]:
        return await (await self._pool()).fetch(sql, *args)

    async def fetchrow(self, sql: str, *args: Any) -> Any | None:
        return await (await self._pool()).fetchrow(sql, *args)

    async def append_audit(
        self, at: datetime, event: str, subject: str, payload: str,
        digest_fn: DigestFn,
    ) -> None:
        """Read the tip and write the next link atomically.

        A plain SELECT-then-INSERT lets two concurrent writers read the same
        tip and insert siblings, forking the chain — which `verify_chain` then
        reports as tampering. The advisory lock is transaction-scoped, so it is
        released on commit or rollback without a cleanup path.
        """
        pool = await self._pool()
        async with pool.acquire() as conn, conn.transaction():
            await conn.execute(
                "SELECT pg_advisory_xact_lock(hashtext('atlas.audit'))")
            prev = await conn.fetchval(
                "SELECT digest FROM actions_log ORDER BY id DESC LIMIT 1")
            prev = prev or GENESIS
            await conn.execute(
                """INSERT INTO actions_log
                         (at, event, subject, payload, prev_digest, digest)
                       VALUES ($1, $2, $3, $4, $5, $6)""",
                at, event, subject, payload, prev,
                digest_fn(prev, canonical_at(at)),
            )

    async def read_audit(self) -> list[AuditRow]:
        rows = await self.fetch(
            """SELECT id, at, event, subject, payload, prev_digest, digest
               FROM actions_log ORDER BY id"""
        )
        return [
            AuditRow(r["id"], r["at"], r["event"], r["subject"],
                     r["payload"], r["prev_digest"], r["digest"])
            for r in rows
        ]


class MemoryBackend:
    """In-memory stand-in for tests and for `selftest` without a database.

    It implements the audit chain faithfully — including the serialisation, so
    the concurrency test is meaningful — and deliberately refuses arbitrary
    SQL. A test that needs a query states so by stubbing it, rather than
    silently getting an empty result and passing for the wrong reason.
    """

    def __init__(self) -> None:
        self.audit_rows: list[AuditRow] = []
        self.sql_log: list[tuple[str, tuple[Any, ...]]] = []
        self.responses: dict[str, Any] = {}
        import asyncio
        self._lock = asyncio.Lock()

    # asyncpg types a `$n::date` / `$n::timestamptz` parameter from the cast
    # and then refuses a Python str for it ("invalid input for query
    # argument"). Checked here too, so a test cannot pass on a bind the real
    # driver rejects (Task 55 review, C1).
    _TYPED = re.compile(r"\$(\d+)::(date|timestamptz|timestamp)\b", re.IGNORECASE)

    def _typecheck(self, sql: str, args: tuple[Any, ...]) -> None:
        from datetime import date
        for match in self._TYPED.finditer(sql):
            n = int(match.group(1))
            if n <= len(args):
                value = args[n - 1]
                if value is not None and not isinstance(value, date):
                    raise TypeError(
                        f"invalid input for query argument ${n}: {value!r} is "
                        f"a {type(value).__name__}, but ${n}::{match.group(2)} "
                        f"needs a date/datetime (asyncpg refuses it)")

    # Answers every test gets unless it sets its own: nothing is paused
    # (kernel.pause is read on every dispatch, and an unanswerable read
    # fails closed).
    _DEFAULTS: ClassVar[dict[str, Any]] = {
        "FROM pauses WHERE resumed_at IS NULL AND scope": None,
        # kernel.pause.active (the brief's and weekly's pause lines): none.
        "FROM pauses WHERE resumed_at IS NULL ORDER BY paused_at": [],
        # kernel.restore: no restore awaiting its fence (R12).
        "FROM restores WHERE fenced_at IS NULL) AS due": {"due": False},
        "SELECT id, status, decided_at, expires_at FROM proposals WHERE dedup_key": None,
        # kernel.idempotency: a test that does not script the claim gets a
        # fresh one (a test of the retry states it with a stateful fake).
        "INSERT INTO effect_claims": {"key": "claimed"},
        # content.crosspost: a reel no test has posted has no reel_posts row.
        "FROM reel_posts WHERE media_id = $1 ORDER BY at LIMIT 1": None,
        # content.legsync: no reel is a later series part and none is missing
        # a leg, unless a test says so.
        "AS legsync_source FROM content_media": None,
        "AS legsync_prev FROM reel_series_parts": None,
        "AS legsync_job FROM reel_calendar": None,
        "FROM actions_log WHERE event IN ('execute.start', 'execute.finish')": [],
    }

    def _canned(self, sql: str) -> Any:
        flat = " ".join(sql.split())
        for fragment, value in self.responses.items():
            if fragment in flat:
                return value
        for fragment, value in self._DEFAULTS.items():
            if fragment in flat:
                return value
        raise NotImplementedError(
            f"MemoryBackend has no canned response for: {' '.join(sql.split())[:90]}"
        )

    async def execute(self, sql: str, *args: Any) -> Any:
        self._typecheck(sql, args)
        self.sql_log.append((" ".join(sql.split()), args))
        return "OK"

    async def fetch(self, sql: str, *args: Any) -> list[Any]:
        self._typecheck(sql, args)
        self.sql_log.append((" ".join(sql.split()), args))
        return self._canned(sql)

    async def fetchrow(self, sql: str, *args: Any) -> Any | None:
        self._typecheck(sql, args)
        self.sql_log.append((" ".join(sql.split()), args))
        return self._canned(sql)

    async def append_audit(
        self, at: datetime, event: str, subject: str, payload: str,
        digest_fn: DigestFn,
    ) -> None:
        async with self._lock:
            prev = self.audit_rows[-1].digest if self.audit_rows else GENESIS
            self.audit_rows.append(AuditRow(
                id=len(self.audit_rows) + 1, at=at, event=event,
                subject=subject, payload=payload, prev_digest=prev,
                digest=digest_fn(prev, canonical_at(at)),
            ))

    async def read_audit(self) -> list[AuditRow]:
        return list(self.audit_rows)


GENESIS = "0" * 64

_backend: Backend = PostgresBackend()


def backend() -> Backend:
    return _backend


def set_backend(new: Backend) -> Backend:
    """Swap the backend. Returns the previous one so a test can restore it."""
    global _backend
    old, _backend = _backend, new
    return old


def _dsn() -> str:
    return os.environ["DATABASE_URL"].replace("postgresql+asyncpg://", "postgresql://")


async def pool() -> Any:
    return await PostgresBackend()._pool()


async def execute_sql(sql: str, *args: Any) -> Any:
    return await _backend.execute(sql, *args)


async def fetch_all(sql: str, *args: Any) -> list[Any]:
    return await _backend.fetch(sql, *args)


async def fetch_one(sql: str, *args: Any) -> Any | None:
    return await _backend.fetchrow(sql, *args)


async def fetch_one_locked(lock: str, sql: str, *args: Any) -> Any | None:
    """One statement, run while holding a transaction-scoped advisory lock
    named `lock`, so a count-then-claim inside `sql` is serialised against
    every other caller of the same lock.

    A single UPDATE whose WHERE counts other rows is not enough on its own:
    under READ COMMITTED each statement counts from its own snapshot, so two
    concurrent claims of DIFFERENT rows both see the old count and both pass
    a cap of one (measured against a real Postgres, markets.alert,
    2026-09-29). The statement here starts after the lock is held, so its
    snapshot includes every claim committed before it. The lock is released
    on commit or rollback, like `append_audit`'s. A backend that is not
    Postgres runs the statement as `fetch_one` would."""
    if not isinstance(_backend, PostgresBackend):
        return await _backend.fetchrow(sql, *args)
    pool = await _backend._pool()
    async with pool.acquire() as conn, conn.transaction():
        await conn.execute("SELECT pg_advisory_xact_lock(hashtext($1))", lock)
        return await conn.fetchrow(sql, *args)


async def record_observation(check_id: int, obs) -> None:
    import json

    # A second net under every collector's own `explain`: the detail is read
    # back into the reasoner's prompt, and the evidence beside it.
    from ..modules._http import redact_stored
    await execute_sql(
        """INSERT INTO observations (check_id, at, health, value, unit, detail, evidence)
           VALUES ($1,$2,$3,$4,$5,$6,$7)""",
        check_id, obs.at, str(obs.health), obs.value, obs.unit,
        redact_stored(obs.detail) if obs.detail else obs.detail,
        redact_stored(json.dumps(obs.evidence, default=str)),
    )


async def baseline(check_id: int, hour: int, dow: int) -> tuple[float, float] | None:
    """Mean and stddev for this check at this hour-of-week, over 30 days.
    Hour-of-week matters: 3am Sunday traffic is not 3pm Tuesday traffic.

    Bucketing is done in UTC. `extract(hour from at)` is evaluated in the
    session timezone, so on a box in local time a DST change double-counts one
    bucket and empties another.
    """
    row = await fetch_one(
        """SELECT avg(value) AS mu, stddev_pop(value) AS sigma, count(*) AS n
           FROM observations
           WHERE check_id = $1 AND value IS NOT NULL
             AND at > now() - interval '30 days'
             AND extract(hour from at at time zone 'UTC') = $2
             AND extract(dow  from at at time zone 'UTC') = $3""",
        check_id, hour, dow,
    )
    if not row or not row["n"] or row["n"] < 5 or row["sigma"] in (None, 0):
        return None                     # not enough history to judge
    return float(row["mu"]), float(row["sigma"])


async def record_finding(finding, check_key: str) -> int | None:
    """Persist a finding and return its id.

    Findings previously lived only in memory for one scheduler iteration, so
    the timeline had nothing to read and a permalink had nothing to point at.
    """
    import json

    # The same second net as record_observation: a finding copies the
    # observation's detail, and the page and signal read it from here.
    from ..modules._http import redact_stored
    obs = finding.observation
    row = await fetch_one(
        """INSERT INTO findings
             (check_key, property, severity, kind, health, title, detail,
              value, unit, evidence, observed_at)
           VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11) RETURNING id""",
        check_key, check_key.split(".")[0], int(finding.severity), finding.kind,
        str(obs.health), redact_stored(finding.title),
        redact_stored(finding.detail) if finding.detail else finding.detail,
        obs.value, obs.unit,
        redact_stored(json.dumps(obs.evidence, default=str)), obs.at,
    )
    return row["id"] if row else None


async def link_finding_to_proposal(finding_id: int, proposal_id: int) -> None:
    await execute_sql("UPDATE findings SET proposal_id=$2 WHERE id=$1",
                      finding_id, proposal_id)


async def record_schedule(check_id: int, last_run, next_run) -> None:
    """Absolute times, so a request handler can render 'next check in 4m'."""
    await execute_sql(
        "UPDATE checks SET last_run_at=$2, next_run_at=$3 WHERE id=$1",
        check_id, last_run, next_run)


async def queue_for_brief(msg) -> None:
    await execute_sql(
        "INSERT INTO brief_queue (title, body, severity) VALUES ($1,$2,$3)",
        msg.title, msg.body, int(msg.severity),
    )


async def last_evidence(property_name: str, check_name: str) -> dict[str, Any]:
    """The `evidence` of the most recent observation for one check.

    A check that must answer "is this new?" needs to know what it saw last
    time. The alternative is trusting a timestamp field on the remote object,
    and those usually mean "last seen" rather than "first seen" -- which makes
    a peer that reconnects every morning look like a peer that joined every
    morning. A security signal that fires daily is one nobody reads.

    Only readings that measured something count. An UNKNOWN observation
    carries no evidence, so a single failed run between two good ones would
    otherwise answer `{}` -- which a caller reads as "no history" and
    re-baselines on, absorbing whatever appeared during the gap.

    Returns `{}` when there is no prior observation, when the backend is not
    Postgres, or on any error. A caller must treat an empty result as "no
    history" and say so, never as "nothing has changed".
    """
    try:
        row = await fetch_one(
            """SELECT o.evidence
                 FROM observations o
                 JOIN checks c ON c.id = o.check_id
                 JOIN properties p ON p.id = c.property_id
                WHERE p.name = $1 AND c.name = $2
                  -- Skip readings that carry nothing. An UNKNOWN observation
                  -- stores no evidence (verified: 272 unknown rows in the
                  -- live database, none with evidence), so taking the newest
                  -- row regardless would answer {} whenever a check failed to
                  -- run once. For a caller diffing against "what I saw last
                  -- time" that reads as no history, and it re-baselines --
                  -- absorbing anything that appeared during the gap and
                  -- never reporting it. The last reading that actually
                  -- measured something is the one being asked for.
                  AND o.health <> 'unknown'
                  AND o.evidence <> '{}'::jsonb
                ORDER BY o.at DESC
                LIMIT 1""",
            property_name, check_name)
    except Exception:
        log.debug("last_evidence unavailable for %s.%s",
                  property_name, check_name, exc_info=True)
        return {}
    if row is None:
        return {}
    value = row[0] if not isinstance(row, dict) else row.get("evidence")
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            return {}
    return value if isinstance(value, dict) else {}
