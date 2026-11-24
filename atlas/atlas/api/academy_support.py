"""ATLAS Academy support's public doors (modules/academy/support*.py).

    OPTIONS /public/academy/chat      CORS preflight, the store's origin only
    POST    /public/academy/chat      {"message": "..."} -> one grounded answer

`/api/...` is mesh-only, so this lives under `/public/academy/`, one exact
path in `main.PUBLIC_EXACT` and its own Caddy handle block.

The Help page's form opens the visitor's mail app since 2026-10-29 (ATLAS
is local-only), so there is no POST /public/academy/support; mail to the
support address still becomes a ticket through the support mailbox.

**The chat** answers from the KB's public chunks only, under the same rules
as an email reply, and says when it cannot (`handoff`). It is single-turn:
no history is accepted. The words are never stored or logged -- the log
line is the outcome. Rate limits: 6 a minute and 60 a day per address, and
ACADEMY_CHAT_DAILY_CAP model calls a day in all. Origin must be the store.
"""
from __future__ import annotations

import json
import logging
import re

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, Response

from ..modules import store_origin
from ..modules.academy import support
from .peers import PerAddressLimiter, effective_client

router = APIRouter()
log = logging.getLogger("atlas.api.academy_support")

CHAT_PATH = "/public/academy/chat"
# The store's origin (ACADEMY_PUBLIC_ORIGIN, default https://agency.example.com):
# the only origin the chat answers, exactly, never a wildcard.
STORE_ORIGIN = store_origin.origin()
CHAT_BODY_MAX = 4 * 1024
CHAT_MAX = 500
_CONTROL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")

_chat_minute = PerAddressLimiter(6, 60.0)
_chat_day = PerAddressLimiter(60, 86400.0)


async def _read(request: Request, limit: int) -> bytes | None:
    chunks, size = [], 0
    async for chunk in request.stream():
        size += len(chunk)
        if size > limit:
            return None
        chunks.append(chunk)
    return b"".join(chunks)


def _cors(origin: str | None) -> dict[str, str]:
    headers = {"Cache-Control": "no-store", "Vary": "Origin",
               "X-Content-Type-Options": "nosniff"}
    if origin:
        headers["Access-Control-Allow-Origin"] = origin
    return headers


def _origin_ok(request: Request) -> bool:
    return (request.headers.get("origin") or "").strip().rstrip("/").lower() == STORE_ORIGIN


@router.options(CHAT_PATH)
async def chat_preflight(request: Request) -> Response:
    if not _origin_ok(request):
        return JSONResponse({"error": "origin not allowed"}, status_code=403)
    headers = _cors(STORE_ORIGIN)
    headers.update({"Access-Control-Allow-Methods": "POST",
                    "Access-Control-Allow-Headers": "Content-Type",
                    "Access-Control-Max-Age": "600"})
    return Response(status_code=204, headers=headers)


def _json(body: dict, status: int) -> Response:
    return JSONResponse(body, status_code=status, headers=_cors(STORE_ORIGIN))


@router.post(CHAT_PATH)
async def chat(request: Request) -> Response:
    if not _origin_ok(request):
        return JSONResponse({"error": "origin not allowed"}, status_code=403)
    client = effective_client(request)
    if not _chat_minute.allow(client) or not _chat_day.allow(client):
        return _json({"status": "busy", "reply": "Too many questions at once. Please "
                      "wait a minute, or use the form below.", "handoff": True}, 429)
    if request.headers.get("content-type", "").split(";")[0].strip().lower() != \
            "application/json":
        return _json({"error": "json only"}, 415)
    raw = await _read(request, CHAT_BODY_MAX)
    if raw is None:
        return _json({"error": "too large"}, 413)
    try:
        payload = json.loads(raw)
    except (ValueError, UnicodeDecodeError):
        return _json({"error": "invalid json"}, 400)
    message = payload.get("message") if isinstance(payload, dict) else None
    if not isinstance(message, str):
        return _json({"error": "invalid field", "field": "message"}, 422)
    message = " ".join(_CONTROL.sub("", message).split())
    if not 0 < len(message) <= CHAT_MAX:
        return _json({"error": "invalid field", "field": "message"}, 422)
    answer = await support.answer_chat(message)
    log.info("academy chat: %s", answer.status)
    status = 503 if answer.status == "resting" else 200
    return _json({"status": answer.status, "reply": answer.reply,
                  "handoff": answer.handoff}, status)
