"""Append-only, hash-chained audit log.

Every observation that led to a proposal, every decision, every execution and
every rollback. When something goes wrong at 4am you need to reconstruct what
ATLAS saw and why it acted — and you need to be able to tell whether the record
itself was altered.

Two properties this file is responsible for:

  * The digest is computed over a canonical UTC timestamp, so a digest survives
    the round trip through a TIMESTAMPTZ column.
  * Appends are serialised by the backend, so concurrent writers cannot fork
    the chain into two entries claiming the same predecessor.
"""
from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from typing import Any

from .state import GENESIS, AuditRow, backend, canonical_at


def _digest(prev: str, payload: str, at: str) -> str:
    return hashlib.sha256(f"{prev}|{at}|{payload}".encode()).hexdigest()


async def audit(event: str, subject: str, data: dict[str, Any] | None = None) -> None:
    at = datetime.now(UTC)
    # Redacted BEFORE the digest: a row in this chain can never be scrubbed
    # afterwards without breaking it, so a credential that reached `data`
    # (an httpx error with a token in its URL, say) must not be written at
    # all. The second net; callers already `explain` their errors.
    from ..modules._http import redact_stored
    payload = redact_stored(json.dumps(
        {"event": event, "subject": subject, "data": data or {}},
        sort_keys=True, default=str,
    ))
    await backend().append_audit(
        at, event, subject, payload,
        lambda prev, at_text: _digest(prev, payload, at_text),
    )


def verify_rows(rows: list[AuditRow]) -> tuple[bool, str]:
    """Pure chain check, so it can be exercised without a database."""
    prev = GENESIS
    for row in rows:
        if row.prev_digest != prev:
            return False, f"chain breaks at entry {row.id}: prev_digest mismatch"
        expect = _digest(prev, row.payload, canonical_at(row.at))
        if expect != row.digest:
            return False, f"chain breaks at entry {row.id}: payload altered"
        prev = row.digest
    return True, f"{len(rows)} entries verified"


async def verify_chain() -> tuple[bool, str]:
    """Walk the chain and report the first break. Run from the CLI, and nightly."""
    return verify_rows(await backend().read_audit())
