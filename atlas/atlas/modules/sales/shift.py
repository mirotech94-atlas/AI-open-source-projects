"""The Sales shift: daily, 09:00 New York (scheduler `sales_shift`).

Every step is an executor call, so watch-only, the limits and the audit log
apply to each: import new CSV files, verify -> research -> qualify each new
lead, then for qualified leads within the daily cap find the published email,
draft the outreach, have QA check the draft, and raise the
`agency.send_outreach` card only when QA passed. Nothing here sends; a hold
from the executor ends the shift. A lead whose demo site is not live yet is
handed to Websites (stage `needs_demo`)."""
from __future__ import annotations

import hashlib
import logging
import os
from pathlib import Path
from typing import Any

from . import store as sstore

log = logging.getLogger("atlas.sales.shift")
BATCH = 20


class _Held(Exception):
    pass


async def _run(action: str, params: dict[str, Any], why: str) -> Any:
    from ...kernel import executor
    from ...kernel.lead_followup import _held
    got = await executor.execute(executor.ExecutionRequest(action, params, reason=why))
    if _held(got):
        raise _Held(action)
    return got


async def _imports(out: dict[str, Any]) -> None:
    for path in sstore.inbox_files():
        try:
            sha = hashlib.sha256(Path(path).read_bytes()).hexdigest()
        except OSError:
            continue
        if await sstore.import_seen(sha):
            continue
        got = await _run("sales.import_csv", {"path": path}, f"new lead file {path}")
        out["imported"] += len((got.evidence or {}).get("inserted") or [])


def find_segments() -> list[str]:
    """SALES_FIND_SEGMENTS, comma-separated (e.g. local_nyc); empty = off."""
    return [s.strip() for s in os.getenv("SALES_FIND_SEGMENTS", "").split(",")
            if s.strip()]


async def _find(out: dict[str, Any]) -> None:
    """Once a shift, one `sales.find_leads` per configured segment (plan
    2026-10-30, C1). Off by default; needs BRAVE_API_KEY or Places to find
    anything -- a missing key is the action's own stated refusal."""
    area = os.getenv("SALES_FIND_AREA", "").strip()
    for segment in find_segments():
        params: dict[str, Any] = {"segment": segment}
        if area:
            params["area"] = area
        got = await _run("sales.find_leads", params, f"new {segment} leads for today")
        out["found"] += len((got.evidence or {}).get("inserted") or [])


async def _new_leads(out: dict[str, Any]) -> None:
    for lead in await sstore.leads_at_stage("new", BATCH):
        lead_id, name = int(lead["id"]), str(lead.get("name") or "")
        profile = lead.get("segment") or "local_nyc"
        checked = await _run("agency.verify", {"lead_id": lead_id, "profile": profile},
                             f"is {name} a real, active business")
        verdict = (checked.evidence or {}).get("verdict")
        if verdict == "verified":
            out["verified"] += 1
            await _run("sales.research_lead", {"lead_id": lead_id},
                       f"what {name}'s site shows")
        if verdict in ("verified", "disqualified"):
            got = await _run("sales.qualify", {"lead_id": lead_id},
                             f"qualify {name} by the rules")
            if (got.evidence or {}).get("stage") == "qualified":
                out["qualified"] += 1


async def _pitches(out: dict[str, Any]) -> None:
    """Sales hands each qualified lead to the Websites lane, which owns the
    preview card and the email (modules/agency/preview_card.py, 2026-10-28)."""
    from ...kernel.lead_followup import pitch_preview
    from ..agency import preview_card
    from . import daily_cards
    left = daily_cards() - await sstore.cards_today()
    if left <= 0:
        return
    for lead in await sstore.leads_at_stage("qualified", left):
        lead_id, name = int(lead["id"]), str(lead.get("name") or f"lead {lead['id']}")
        got = await pitch_preview({**lead, "id": lead_id, "name": name},
                                  f"first email to {name}", find=True)
        if got == preview_card.HELD:
            raise _Held("agency.send_outreach card")
        if got == preview_card.NEEDS_DEMO:
            await sstore.set_stage(lead_id, "needs_demo")
            out["needs_demo"] += 1
        elif got == preview_card.NO_EMAIL:
            # No published address: never a guess. Out of the daily queue so
            # it does not take the next lead's place every morning.
            await sstore.set_stage(lead_id, "no_email")
            out["no_email"] += 1
        elif got == preview_card.QA_FAILED:
            out["qa_failed"] += 1
        elif got == preview_card.CARD:
            out["cards"] += 1
            await sstore.set_stage(lead_id, "pitched")


async def run() -> dict[str, Any]:
    """One shift. Returns what it did; `held` when the executor held a step."""
    out: dict[str, Any] = {"imported": 0, "found": 0, "verified": 0, "qualified": 0,
                           "cards": 0,
                           "qa_failed": 0, "needs_demo": 0, "no_email": 0,
                           "held": None}
    try:
        await _imports(out)
        await _find(out)
        await _new_leads(out)
        await _pitches(out)
    except _Held as held:
        out["held"] = str(held)
        log.info("sales shift held at %s", held)
    return out
