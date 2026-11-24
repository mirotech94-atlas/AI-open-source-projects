"""The audit chain. A tampered payload must be detectable, and the chain must
survive both the round trip through storage and concurrent appends."""
from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import UTC, datetime, timedelta, timezone

import pytest

from atlas.kernel.audit import audit, verify_chain, verify_rows
from atlas.kernel.state import AuditRow


@pytest.mark.asyncio
async def test_intact_chain_verifies(memory_backend):
    await audit("execute.start", "flycheap.refresh", {"n": 1})
    await audit("execute.finish", "flycheap.refresh", {"ok": True})
    ok, detail = await verify_chain()
    assert ok, detail
    assert "2 entries" in detail


@pytest.mark.asyncio
async def test_tampered_payload_is_detected(memory_backend):
    await audit("execute.start", "cloudhost.reboot", {"host": "a"})
    await audit("execute.finish", "cloudhost.reboot", {"ok": True})

    # Someone edits the record to hide what actually happened.
    rows = memory_backend.audit_rows
    rows[0] = replace(rows[0], payload=rows[0].payload.replace('"a"', '"b"'))

    ok, detail = await verify_chain()
    assert not ok
    assert "payload altered" in detail
    assert "entry 1" in detail


@pytest.mark.asyncio
async def test_reordered_chain_is_detected(memory_backend):
    await audit("one", "s")
    await audit("two", "s")
    memory_backend.audit_rows.reverse()
    ok, detail = await verify_chain()
    assert not ok
    assert "prev_digest mismatch" in detail


@pytest.mark.asyncio
async def test_digest_survives_a_timezone_round_trip():
    """A3: the digest is computed over canonical UTC.

    Storage returns TIMESTAMPTZ normalised to UTC. If the digest were taken
    over a local-offset string, an untouched row would read as tampered.
    """
    at_local = datetime.now(timezone(timedelta(hours=-4)))
    at_utc = at_local.astimezone(UTC)
    assert at_local.isoformat() != at_utc.isoformat()      # the trap

    from atlas.kernel.audit import _digest
    from atlas.kernel.state import canonical_at
    digest = _digest("0" * 64, '{"a":1}', canonical_at(at_local))

    stored = AuditRow(1, at_utc, "e", "s", '{"a":1}', "0" * 64, digest)
    ok, detail = verify_rows([stored])
    assert ok, detail


@pytest.mark.asyncio
async def test_concurrent_appends_do_not_fork_the_chain(memory_backend):
    """A4: 50 interleaved appends must produce one linear chain."""
    await asyncio.gather(*(audit("concurrent", f"s{i}") for i in range(50)))
    rows = memory_backend.audit_rows
    assert len(rows) == 50
    assert len({r.prev_digest for r in rows}) == 50, "a predecessor was reused"
    ok, detail = await verify_chain()
    assert ok, detail
