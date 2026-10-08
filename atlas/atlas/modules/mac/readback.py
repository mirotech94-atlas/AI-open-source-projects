"""Read-back for the Mac actions (RA-D1): only what is verifiable.

The Mac is reached through a job queue its agent long-polls
(`mac_jobs`, bootstrap/mac/mac_agent.py). The agent runs a closed list of
operations and none of them reads anything back: there is no "list
reminders", no "is this app open", no "now playing". Asking the Mac would
mean queuing a job, which is a write. So nothing here can say verified:

  * `mac.add_reminder`: the reminder lives in Reminders on the laptop, which
    no read-only path reaches;
  * `mac.open_app`, `mac.music_control`: leave no record at all;
  * `mac.play_music`: opens a YouTube Music search; there is no read-only
    now-playing query.

What IS checked is ATLAS's own record of the dispatch, the `mac_jobs` row by
the job id the run recorded (one SELECT): the job carried the approved
operation and parameters and the Mac answered done and ok. Any of those
wrong is contradicted; otherwise unknown with the reason and the row as
evidence. The module off (no MAC_AGENT_TOKEN) is unknown, class disabled,
before any read. See docs/verification.md.
"""
from __future__ import annotations

import json
import os
from typing import Any

from ...kernel.contracts import ActionResult
from ...kernel.verify import (
    FailureClass,
    Verification,
    contradicted,
    unknown,
    verifier,
)

WHY = {
    "add_reminder": ("the reminder lives in Reminders on the Mac, and the Mac agent "
                     "has no read-only way to list it"),
    "open_app": "opening an app leaves no record ATLAS can read back",
    "music_control": "a play/pause/next/previous key press leaves no record ATLAS "
                     "can read back",
    "music_search": ("the Mac agent has no read-only now-playing query; it only "
                     "opens a YouTube Music search"),
}


async def _job(op: str, want: dict[str, Any], result: ActionResult) -> Verification:
    if not os.getenv("MAC_AGENT_TOKEN"):
        return unknown("the Mac module is off (no MAC_AGENT_TOKEN)",
                       FailureClass.DISABLED)
    try:
        job = int((result.evidence or {}).get("job"))
    except (TypeError, ValueError):
        return unknown(f"{WHY[op]}; and the run recorded no job id")
    from ...kernel import state
    row = await state.fetch_one(
        "SELECT op, params, status, ok FROM mac_jobs WHERE id = $1", job)
    if row is None:
        return unknown(f"{WHY[op]}; and job #{job} is not in mac_jobs", job=job)
    got = row["params"]
    if isinstance(got, str):
        try:
            got = json.loads(got)
        except ValueError:
            got = {}
    got = got if isinstance(got, dict) else {}
    evidence = {"job": job, "op": row["op"], "status": row["status"], "ok": row["ok"]}
    if row["op"] != op:
        return contradicted(f"job #{job} ran {row['op']!r}, not {op!r}",
                            FailureClass.POLICY, **evidence)
    if differ := sorted(k for k, v in want.items() if str(got.get(k, "")) != str(v)):
        return contradicted(f"job #{job} carried different {', '.join(differ)} than "
                            "approved", FailureClass.POLICY, **evidence)
    if row["status"] != "done" or row["ok"] is not True:
        return contradicted(f"job #{job} is {row['status']!r} with ok={row['ok']}",
                            **evidence)
    if op == "add_reminder" and result.rollback_token:
        evidence["reminder_id"] = str(result.rollback_token)[:80]
    return unknown(WHY[op], **evidence)


@verifier("mac.add_reminder")
async def verify_add_reminder(params: dict[str, Any], result: ActionResult
                              ) -> Verification:
    from . import local_due
    return await _job("add_reminder",
                      {"title": str(params.get("title") or "").strip(),
                       "due": local_due(str(params.get("due") or ""))}, result)


@verifier("mac.open_app")
async def verify_open_app(params: dict[str, Any], result: ActionResult
                          ) -> Verification:
    return await _job("open_app", {"app": str(params.get("app") or "").strip()},
                      result)


@verifier("mac.music_control")
async def verify_music_control(params: dict[str, Any], result: ActionResult
                               ) -> Verification:
    return await _job("music_control", {"command": str(params.get("command") or "")},
                      result)


@verifier("mac.play_music")
async def verify_play_music(params: dict[str, Any], result: ActionResult
                            ) -> Verification:
    return await _job("music_search", {"query": str(params.get("query") or "").strip()},
                      result)
