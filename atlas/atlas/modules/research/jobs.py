"""The research jobs (six agents phase 11). The only file in this package
that reaches the executor: every step is an `executor.execute` call, so
watch-only and the tier gates hold all of it. The other files only return
results to the executor."""
from __future__ import annotations

from typing import Any

from . import store

MAX_REFRESH = 10


async def _run(action: str, params: dict[str, Any], why: str) -> str:
    from ...kernel import executor
    got = await executor.execute(executor.ExecutionRequest(action, params, reason=why))
    return str(got.detail)


async def run_scan() -> dict[str, str]:
    """The research_scan job (06:00 New York)."""
    return {"scan": await _run("research.daily_scan", {}, "the daily competitor scan")}


async def run_weekly() -> dict[str, str]:
    """The research_weekly job (Sunday 18:00 New York)."""
    out: dict[str, str] = {}
    try:
        comps = await store.competitors()
    except Exception as exc:                            # noqa: BLE001
        comps = []
        out["competitors"] = f"unreadable: {type(exc).__name__}"
    for c in comps[:MAX_REFRESH]:
        if c.get("homepage") or c.get("youtube_channel_id"):
            out[f"profile:{c['id']}"] = await _run(
                "research.refresh_profile", {"competitor_id": int(c["id"])},
                "the weekly profile refresh")
    out["report"] = await _run("research.weekly_report", {}, "the weekly report")
    return out
