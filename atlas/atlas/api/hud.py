"""The portal HUD's read endpoint: `GET /api/hud`.

What ATLAS is doing now -- tasks, surfaces, Tier 2 cards waiting, jobs --
from `kernel/hud.py`, every section stamped with when it was read. GET only:
the HUD has no approve button and no way to start anything; a card is decided
where it always was, `POST /api/approvals/{pid}/decision`. Behind the same
session and mesh rule as every other dashboard route.

The page is `/hud` in the SPA (`ui/src/pages/Hud.tsx`).
"""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends

from ..kernel import hud
from . import auth

router = APIRouter(prefix="/api/hud")
SignedIn = Annotated[auth.Session, Depends(auth.require)]


@router.get("")
async def read(_s: SignedIn) -> dict[str, Any]:
    served_at = datetime.now(UTC).isoformat()
    snap = await hud.snapshot()
    snap["served_at"] = served_at
    return snap
