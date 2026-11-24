"""GET /public/ping: the one liveness answer the public edge may give.

Caddyfile.public rewrites exactly `GET /healthz` here. The body is a fixed
shape, status and the time, so a monitor outside can see the site is up and
learn nothing else: no module or check counts, no heartbeat ages (those stay on
the mesh-only /healthz). Read-only; no database, no input.
"""
from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter
from fastapi.responses import JSONResponse

router = APIRouter()
HEADERS = {"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"}


@router.get("/public/ping")
async def public_ping() -> JSONResponse:
    # "ok" is what the off-box watchdog's healthz check requires of the body.
    return JSONResponse({"ok": True, "status": "ok",
                         "time": datetime.now(UTC).isoformat(timespec="seconds")},
                        headers=HEADERS)
