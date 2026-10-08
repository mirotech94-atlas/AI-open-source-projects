"""`GET /api/tasks/board`: the work board (`kernel/workboard.py`).

What every agent has in hand, what waits on Miro, what it finished this
week and how far its own tables say it has got -- the same read the
spoken context uses, so the Tasks tab and a voice answer can never
disagree. `GET /api/tasks` (dashboard.py) is unchanged: the cards and the
nightshift, as before.

Read-only, behind the sign-in. A read that fails as a whole is a 503 that
says why; a section that fails inside the board is `errors[section]` and
the seats it fed say `unread`, never zeros.
"""
from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query

from ..kernel import workboard
from ..modules._http import explain
from . import auth

log = logging.getLogger("atlas.workboard")

router = APIRouter(prefix="/api/tasks")
SignedIn = Annotated[auth.Session, Depends(auth.require)]


@router.get("/board")
async def board(_s: SignedIn,
                days: Annotated[int, Query(ge=1, le=90)] = workboard.WINDOW_DAYS
                ) -> dict[str, Any]:
    served_at = datetime.now(UTC).isoformat()
    try:
        built = await workboard.board(days)
    except Exception as exc:                                # noqa: BLE001
        why = explain(exc)
        log.warning("the work board could not be built: %s", why)
        raise HTTPException(503, f"I could not read the work board — {why}") from None
    return {"served_at": served_at, "as_of": built["read_at"], **built}
