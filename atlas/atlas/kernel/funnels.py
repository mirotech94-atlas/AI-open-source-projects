"""The sending half of the Instagram comment-keyword funnels (scheduler job
`funnels`), and the `/atlas funnel ...` command.

`modules.instagram.funnel` reads comments and records the keyword matches
as queued sends; it never acts (a module is a collector, and
tests/test_instagram_accounts.py pins that nothing in that package imports
the executor). This file is the one caller that turns a queued send into an
`instagram.funnel_reply` through `executor.execute` -- every tier check,
dry run, pause, ceiling and audit row still happens there (invariant 1) --
and reads what came back:

* ok                            -> the run recorded the send; count it
* refused before it ran         -> a hold (watch-only, a pause, a ceiling,
                                   a refused dry run: quiet hours, the cap,
                                   a hash mismatch): the row waits; stop
                                   this funnel for the tick
* auth (401/403, code 190)      -> `pause.block_capability`, scope
                                   `instagram:<account>`; stop
* policy (code 368, 10, 2xx)    -> the funnel is stopped and the owner told
* transient (429, code 4/17/32) -> the run already backed the row off;
                                   stop this funnel for the tick
* anything else                 -> recorded on the funnel; next row

The command is parsed in code (`funnel.parse_command`), never by a model:
`set` raises the Tier 2 `instagram.funnel_set` card with the exact texts,
`pause` runs the Tier 1 pause through the executor, `list` reads.
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from .contracts import FailureClass

log = logging.getLogger("atlas.funnels")


async def _dispatch(funnel: dict[str, Any], now: datetime) -> tuple[int, str]:
    """Send this funnel's due rows, one `execute` each, until a reason to
    stop. Returns (sent, why it stopped)."""
    from ..modules.instagram import funnel as fn
    from .executor import ExecutionRequest, execute
    from .state import fetch_all
    account = str(funnel["account"])
    tz = str(funnel["timezone"])
    local = fn.local_now(tz, now)
    if fn.in_quiet_hours(local):
        return 0, f"quiet hours until {fn.next_open(local):%H:%M %Z}"
    rows = await fetch_all(
        """SELECT id, comment_id, username FROM ig_funnel_sends
            WHERE funnel_id = $1 AND status = 'queued' AND next_attempt_at <= $2
            ORDER BY id LIMIT 50""", int(funnel["id"]), now)
    sent = 0
    for r in rows:
        done = await fn.sent_today(account, tz, now)
        if done >= int(funnel["daily_cap"]):
            return sent, f"daily cap {funnel['daily_cap']} reached"
        params = {"account": account, "funnel_id": int(funnel["id"]),
                  "comment_id": str(r["comment_id"])}
        result = await execute(ExecutionRequest(
            action=fn.REPLY, params=params,
            reason=f"funnel #{funnel['id']}: @{r['username']} commented "
                   f"“{funnel['keyword']}”"))
        if result.ok:
            sent += 1
            continue
        cls = result.failure_class
        if result.dispatched is False:
            if "hash mismatch" in result.detail:
                await fn._record_error(int(funnel["id"]), result.detail)
            return sent, f"held: {result.detail[:160]}"
        if cls is FailureClass.AUTH:
            await _auth_refused(account, result.detail)
            await fn._record_error(int(funnel["id"]), result.detail)
            return sent, "login refused; the account is paused"
        if cls is FailureClass.POLICY:
            await fn.stop_funnel(int(funnel["id"]), result.detail)
            return sent, "policy error; the funnel is stopped"
        await fn._record_error(int(funnel["id"]), result.detail)
        if cls is FailureClass.TRANSIENT:
            return sent, "rate limited; backing off"
    return sent, ""


async def _auth_refused(account: str, detail: str, status: int | None = None) -> None:
    """R09: the credential is paused (`instagram:<account>`), once."""
    from ..modules.instagram import funnel as fn
    from . import pause as _pause
    from .contracts import _HTTP_STATUS
    if status is None:
        m = _HTTP_STATUS.search(detail or "")
        status = int(m.group(1)) if m else None
    await _pause.block_capability(fn.REPLY, {"account": account}, status)


async def tick(now: datetime | None = None) -> dict[str, Any]:
    """One sweep: read and queue (the module), then send what is due
    (here). Never raises."""
    from ..modules.instagram import funnel as fn
    now = now or fn._clock()
    ready, summary = await fn.sweep(now)
    summary["sent"] = 0
    for account, status in summary.pop("auth_refused", []):
        await _auth_refused(account, "", status)
    for f in ready:
        try:
            sent, stopped = await _dispatch(f, now)
        except Exception as exc:
            log.exception("funnel #%s: dispatch failed", f["id"])
            summary["errors"].append(f"#{f['id']}: dispatch {type(exc).__name__}")
            continue
        summary["sent"] += sent
        if stopped:
            summary.setdefault("stopped", {})[str(f["id"])] = stopped
    return summary


async def command(text: str, *, channel: str, who: str) -> Any:
    """`kernel.intent.act`'s code-parsed hook for `funnel ...`: an
    `Outcome`, or None when `text` is not a funnel command."""
    from ..modules.instagram import funnel as fn
    from .intent import Outcome
    parsed = fn.parse_command(text)
    if parsed is None:
        return None
    verb = parsed["verb"]
    if verb == "error":
        return Outcome(False, parsed["why"])
    if verb == "list":
        funnels = await fn.list_funnels()
        if not funnels:
            return Outcome(True, f"No funnels yet. {fn.USAGE}")
        lines = []
        for f in funnels:
            state = ("stopped" if f["stopped_at"] else
                     "active" if f["active"] and f["approved_at"] else "paused")
            lines.append(f"#{f['id']} {f['account']} “{f['keyword']}” on "
                         f"{fn.scope_words(str(f['media_id']))}: {state}, "
                         f"{f['daily_cap']}/day")
        return Outcome(True, "\n".join(lines))
    if verb == "pause":
        from .executor import ExecutionRequest, execute
        result = await execute(ExecutionRequest(
            action=fn.PAUSE, params={"funnel_id": parsed["funnel_id"]},
            reason=f"Asked directly over {channel}: {text[:200]}",
            approved_by=f"{channel}:{who}"))
        return Outcome(result.ok, result.detail, action=fn.PAUSE, ran=result.ok)
    params = parsed["params"]
    why = fn.vet_set(params)
    if why:
        return Outcome(False, f"Not set: {why}. {fn.USAGE}", action=fn.SET)
    from .approvals import Unrunnable, propose
    try:
        pid = await propose(
            fn.SET, params,
            reason=f"Funnel asked over {channel} by {who}: approve the exact texts "
                   f"below and it starts",
            evidence={"channel": channel, "request": text[:500]},
            dedup_key=f"{fn.SET}:{params['account']}:{params['keyword'].lower()}:"
                      f"{params.get('media_id', '')}")
    except Unrunnable as exc:
        return Outcome(False, exc.spoken, action=fn.SET)
    except ValueError as exc:
        return Outcome(False, f"I can't raise that card: {exc}", action=fn.SET)
    return Outcome(True, f"Card #{pid} raised in #{fn.TOPIC} with the exact public "
                         f"reply and DM text. Approve it there and the funnel "
                         f"starts; nothing is sent until then.",
                   action=fn.SET, proposal_id=pid)
