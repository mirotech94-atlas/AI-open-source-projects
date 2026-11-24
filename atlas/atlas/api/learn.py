"""POST /api/learn: queue one YouTube video for ATLAS to listen to.

Behind the dashboard login (`auth.require`) plus the Origin and CSRF checks. The same strict path as the chat's
"learn <url>" (`kernel/video_learn.queue`): it only queues a row; the daily job
reads the transcript and raises the Tier 2 lessons card.
"""
from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from ..kernel import video_learn
from . import auth

router = APIRouter(prefix="/api/learn")
SignedIn = Annotated[auth.Session, Depends(auth.require)]


class LearnBody(BaseModel):
    url: str = Field(max_length=300)


@router.post("")
async def learn(body: LearnBody, request: Request, _s: SignedIn) -> dict[str, str]:
    from .main import DEFAULT_ALLOWED_HOSTS, _allowed_hosts

    # Same gate as the dashboard decision route: a foreign or missing Origin, or
    # a missing CSRF token, is refused before anything is queued.
    hosts = _allowed_hosts() or frozenset(DEFAULT_ALLOWED_HOSTS)
    if not auth.same_origin(request, hosts):
        raise HTTPException(403, "this request did not come from the dashboard page")
    if not auth.csrf_ok(request):
        raise HTTPException(403, "missing or wrong CSRF token; reload the page")
    return {"reply": await video_learn.queue(body.url)}
