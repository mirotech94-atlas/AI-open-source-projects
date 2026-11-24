"""The comment-keyword funnels on the signed-in dashboard.

    GET  /api/instagram/funnels             every funnel, with its counts
    POST /api/instagram/funnels             {"account", "keyword", "public_reply",
                                             "dm_text", "media_id"?, "daily_cap"?,
                                             "timezone"?}  -> the Tier 2 card

The POST never writes a funnel: it raises the same `instagram.funnel_set`
card the Slack command raises, through `approvals.propose` (the card shows
the exact texts; the owner approves it in Slack). Nothing here reaches the
executor (tests/test_api.py pins that no API route can): pausing is
`/atlas funnel pause <id>` from Slack, or the set card's rollback. JSON
only, so a cross-site form post cannot reach it.

Mounted by two lines in atlas/api/main.py:
    from .ig_funnels import router as ig_funnel_router
    app.include_router(ig_funnel_router)
"""
from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request

from ..modules.instagram import funnel
from . import auth

router = APIRouter(prefix="/api/instagram")
SignedIn = Annotated[auth.Session, Depends(auth.require)]


async def _json_object(request: Request) -> dict[str, Any]:
    if "application/json" not in request.headers.get("content-type", ""):
        raise HTTPException(415, "send JSON")
    try:
        body: Any = await request.json()
    except ValueError:
        raise HTTPException(400, "not JSON") from None
    if not isinstance(body, dict):
        raise HTTPException(400, "send an object")
    return body


def _row(f: dict[str, Any]) -> dict[str, Any]:
    out = {k: v for k, v in f.items()
           if k not in ("text_hash",)}
    for k in ("created_at", "updated_at", "approved_at", "stopped_at",
              "last_error_at"):
        out[k] = f[k].isoformat() if f.get(k) else None
    out["state"] = ("stopped" if f["stopped_at"] else
                    "active" if f["active"] and f["approved_at"] else "paused")
    return out


@router.get("/funnels")
async def list_funnels(_s: SignedIn) -> dict:
    from ..kernel.state import fetch_all
    funnels = [_row(f) for f in await funnel.list_funnels()]
    counts = await fetch_all(
        """SELECT funnel_id, status, count(*) AS n FROM ig_funnel_sends
            GROUP BY funnel_id, status""")
    by_id: dict[int, dict[str, int]] = {}
    for c in counts:
        by_id.setdefault(int(c["funnel_id"]), {})[str(c["status"])] = int(c["n"])
    for f in funnels:
        f["sends"] = by_id.get(int(f["id"]), {})
    return {"funnels": funnels, "usage": funnel.USAGE}


@router.post("/funnels")
async def post_funnel(s: SignedIn, request: Request) -> dict:
    body = await _json_object(request)
    params = {k: v for k, v in body.items()
              if k in funnel.PARAMS_SET and v not in (None, "")}
    why = funnel.vet_set(params)
    if why:
        raise HTTPException(422, why)
    from ..kernel.approvals import Unrunnable, propose
    try:
        pid = await propose(
            funnel.SET, params,
            reason=f"Funnel defined on the dashboard by {s.user}: approve the exact "
                   f"texts below and it starts",
            evidence={"channel": "dashboard", "user": s.user},
            dedup_key=f"{funnel.SET}:{params['account']}:"
                      f"{str(params['keyword']).lower()}:{params.get('media_id', '')}")
    except Unrunnable as exc:
        raise HTTPException(409, exc.spoken) from None
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from None
    return {"ok": True, "proposal_id": pid,
            "card": funnel.card_detail_set(params),
            "detail": f"Card #{pid} raised in #{funnel.TOPIC}; approve it in Slack "
                      f"and the funnel starts. Nothing is sent until then."}
