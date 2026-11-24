"""The weekly report says why a card came back (kernel-owner review of
S08/P04, 2026-09-29): approvals refused as `execute.superseded` are counted,
so a fresh card for something already approved is never a mystery."""
from __future__ import annotations

from atlas.kernel import weekly
from atlas.kernel.audit import audit
from tests.acceptance_support import (
    needs_pg,
    pg_fixture,  # noqa: F401
    registries_fixture,  # noqa: F401
)


def _asked_again(report) -> list[str]:
    return [p for p in report.paragraphs if p.startswith("*Asked again*")]


@needs_pg
async def test_superseded_approvals_are_counted_in_the_weekly(pg):
    await audit("execute.superseded", "stub.act", {"stale": "version"})
    await audit("execute.superseded", "stub.act", {"stale": "params"})
    [line] = _asked_again(await weekly.build())
    assert line.startswith("*Asked again*\n2 approvals came back to you")
    assert line.endswith("after you approved them.")


@needs_pg
async def test_one_superseded_approval_reads_in_the_singular(pg):
    await audit("execute.superseded", "stub.act", {"stale": "version"})
    [line] = _asked_again(await weekly.build())
    assert "1 approval came back" in line and line.endswith("approved it.")


@needs_pg
async def test_no_superseded_approvals_add_no_section(pg):
    await audit("execute.start", "stub.act", {})
    assert _asked_again(await weekly.build()) == []


@needs_pg
async def test_a_scoped_pause_is_in_the_weekly_until_resumed(pg):
    """R09: a pause set by a refused login is listed until he resumes it."""
    from atlas.kernel import pause, state
    from atlas.kernel.registry import REGISTRY
    await state.execute_sql(
        "INSERT INTO pauses (scope, paused_by, reason) VALUES ($1, $2, $3)",
        "instagram:clips", pause.AUTH_REFUSED_BY, "login refused (HTTP 401)")
    [line] = [p for p in (await weekly.build()).paragraphs
              if p.startswith("*Paused until you resume*")]
    assert "the instagram module's clips account is paused" in line
    REGISTRY.modules.setdefault("instagram", object())
    await pause.resume("instagram:clips", "slack:UMIRO")
    assert not [p for p in (await weekly.build()).paragraphs
                if p.startswith("*Paused until you resume*")]
