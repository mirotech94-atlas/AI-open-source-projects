"""Where the agent on the owner's Mac collects jobs and answers (modules/mac).

    GET  /mac/next                 long-poll, up to 25 s: 204, or one job
    POST /mac/jobs/{id}/result     {"ok", "detail", "token"}

Both need the bearer MAC_AGENT_TOKEN and sit behind the mesh/trusted-address
rule like every non-public path. A job is claimed in one UPDATE that also
checks it has not expired, so a request made while the lid was shut never
runs when it opens. The agent's own clock is not trusted for anything.
"""
from __future__ import annotations

import asyncio
import hmac
import json
import os

from fastapi import APIRouter, Header, HTTPException, Request, Response

router = APIRouter(prefix="/mac")

LONG_POLL_S = 25.0


def _authorised(header: str) -> bool:
    expected = os.getenv("MAC_AGENT_TOKEN", "")
    if len(expected) < 24:
        return False
    supplied = header[7:] if header.lower().startswith("bearer ") else ""
    return hmac.compare_digest(supplied, expected)


@router.get("/next")
async def next_job(request: Request, authorization: str = Header(""),
                   x_mac_agent: str = Header("")):
    if not _authorised(authorization):
        raise HTTPException(401, "bad mac agent token")
    from ..kernel.state import execute_sql, fetch_one
    host = (x_mac_agent or "mac")[:60]
    loop = asyncio.get_running_loop()
    deadline = loop.time() + LONG_POLL_S
    while True:
        await execute_sql(
            """INSERT INTO mac_agent (host, last_seen) VALUES ($1, now())
               ON CONFLICT (host) DO UPDATE SET last_seen = now()""", host)
        row = await fetch_one(
            """UPDATE mac_jobs SET status = 'claimed', claimed_at = now()
                WHERE id = (SELECT id FROM mac_jobs
                             WHERE status = 'queued' AND expires_at > now()
                             ORDER BY id LIMIT 1)
                  AND status = 'queued' AND expires_at > now()
            RETURNING id, op, params""")
        if row is not None:
            params = row["params"]
            if isinstance(params, str):
                params = json.loads(params or "{}")
            return {"id": int(row["id"]), "op": row["op"], "params": params}
        if await request.is_disconnected() or loop.time() >= deadline:
            return Response(status_code=204)
        await asyncio.sleep(0.5)


@router.post("/jobs/{jid}/result")
async def result(jid: int, request: Request, authorization: str = Header("")) -> dict:
    if not _authorised(authorization):
        raise HTTPException(401, "bad mac agent token")
    try:
        body = json.loads(await request.body() or b"{}")
    except ValueError:
        raise HTTPException(400, "not JSON") from None
    from ..kernel.audit import audit
    from ..kernel.state import fetch_one
    row = await fetch_one(
        """UPDATE mac_jobs SET status = 'done', ok = $2, detail = $3, token = $4,
                               finished_at = now()
            WHERE id = $1 AND status IN ('claimed', 'unknown') RETURNING op""",
        jid, bool(body.get("ok")), str(body.get("detail") or "")[:500],
        (str(body["token"])[:500] if body.get("token") is not None else None))
    if row is None:
        raise HTTPException(409, "no such claimed job")
    await audit("mac.job", str(row["op"]), {"id": jid, "ok": bool(body.get("ok"))})
    return {"ok": True}
