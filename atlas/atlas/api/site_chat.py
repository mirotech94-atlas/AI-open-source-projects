"""The AI chat assistant's public door (atlas/modules/agency/sitechat.py).

    OPTIONS /public/site-chat/{slug}    CORS preflight
    GET     /public/site-chat/{slug}    {"status": "ready" | "resting"}
    POST    /public/site-chat/{slug}    {"message": "...", "history": [...]}

Public, with no session: a visitor on a client's site has none, and never
should (no cookies, `credentials: "omit"` in the widget). Only a path that
is exactly `/public/site-chat/<one DNS label>` is let past the mesh guard
(`main._is_public`); anything else under `/public/` stays mesh-only. In
order, each refusal saying nothing about the next:

    404  not a slug, or no chat facts for it (not built, not a $500/$300
         package, or its package changed)
    403  an Origin other than https://agency.storefront.test (`AGENCY_PREVIEW_ORIGIN`),
         the client's own `domain` from client.json, or `null` (see below);
         POST needs one, GET may omit it
    429  one address past SITE_CHAT_PER_IP_PER_MIN (default 10) a minute;
         or the site past SITE_CHAT_PER_SITE_PER_DAY (default 300) today
    413  a body over 8 KB
    415  not application/json
    400  not a JSON object
    422  `message` not 1-500 characters, or `history` not a list of at most
         6 {"role": "user"|"assistant", "text": 1-500 characters}
    503  resting: SITE_CHAT=off, no model configured or answering, the spend
         ledger unreadable, or SITE_CHAT_MONTHLY_CAP_USD reached. The widget
         shows "Chat is resting -- call/email us"; nothing is made up.
    200  {"status": "ok", "reply": "..."}, or {"status": "refused", "reply":
         "..."} when the message carried a card or ID number (no model was
         asked, and the widget drops that turn from its history)

`Origin: null` (weakened check, Miro 2026-11-14): previews are served at
`agency.storefront.test/preview/<slug>/` under a CSP `sandbox` WITHOUT
`allow-same-origin`, so their opaque origin makes the browser send
`Origin: null`. It is accepted only here, where the slug in the URL path has
chat facts (a published, chat-enabled site), and every other limit stays. It
no longer proves WHICH page called: any sandboxed page can send `null`. What
bounds the damage is unchanged: no cookies or credentials (the widget sends
none), the per-address and per-site daily limits, the spend cap, the kill
switch and the 8 KB / 500-character bounds.

The client address is the one Caddy forwards, believed only from Caddy
itself (`peers.effective_client`). The words are never logged or stored:
the log line is the slug and the outcome.
"""
from __future__ import annotations

import asyncio
import json
import logging

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, Response

from ..modules.agency import hosting, sitechat
from .peers import PerAddressLimiter, effective_client

router = APIRouter()
log = logging.getLogger("atlas.site_chat")

PATH = "/public/site-chat/{slug}"
PREFIX = "/public/site-chat/"
STATUS_PER_MIN = 30
_posts = PerAddressLimiter(sitechat.DEFAULT_PER_IP_PER_MIN, 60.0)
_reads = PerAddressLimiter(STATUS_PER_MIN, 60.0)
FACTS_TIMEOUT_S = 2.0


def _cors(origin: str | None) -> dict[str, str]:
    headers = {"Cache-Control": "no-store", "Vary": "Origin",
               "X-Content-Type-Options": "nosniff"}
    if origin:
        headers["Access-Control-Allow-Origin"] = origin
    return headers


def _json(body: dict, status: int, origin: str | None) -> Response:
    return JSONResponse(body, status_code=status, headers=_cors(origin))


def _bare(status: int, text: str) -> Response:
    return JSONResponse({"error": text}, status_code=status,
                        headers={"Cache-Control": "no-store"})


async def _facts(slug: str) -> sitechat.Facts | None:
    if not sitechat.valid_slug(slug):
        return None
    try:
        return await asyncio.wait_for(asyncio.to_thread(sitechat.load_facts, slug),
                                      FACTS_TIMEOUT_S)
    except Exception:  # noqa: BLE001
        return None


def _origin(request: Request, facts: sitechat.Facts) -> tuple[str | None, bool]:
    """(the Origin to echo, whether it is allowed). A missing Origin is
    (None, True): the caller decides whether that is enough."""
    given = request.headers.get("origin")
    if given is None:
        return None, True
    given = given.strip().lower().rstrip("/")
    if given == "null":
        # `facts` exists only for a published slug taken from the URL path.
        return given, True
    if given == hosting.preview_origin() or given in facts.origins:
        return given, True
    return None, False


async def _body(request: Request) -> bytes | None:
    """The body, or None when over BODY_MAX (Content-Length first, then the
    stream capped as it is read: a chunked body has no length)."""
    declared = request.headers.get("content-length")
    if declared is not None:
        try:
            if int(declared) > sitechat.BODY_MAX:
                return None
        except ValueError:
            return None
    chunks: list[bytes] = []
    size = 0
    async for chunk in request.stream():
        size += len(chunk)
        if size > sitechat.BODY_MAX:
            return None
        chunks.append(chunk)
    return b"".join(chunks)


def _no_constants(name: str) -> float:
    raise ValueError(name)


def _shape(payload: dict) -> tuple[str, list[sitechat.Turn]] | str:
    """(message, history), or the name of what is wrong."""
    message = payload.get("message")
    if not isinstance(message, str):
        return "message"
    message = " ".join(message.split())
    if not 0 < len(message) <= sitechat.MESSAGE_MAX:
        return "message"
    raw = payload.get("history", [])
    if raw is None:
        raw = []
    if not isinstance(raw, list) or len(raw) > sitechat.HISTORY_TURNS:
        return "history"
    history: list[sitechat.Turn] = []
    for turn in raw:
        if not isinstance(turn, dict):
            return "history"
        role, text = turn.get("role"), turn.get("text")
        if role not in ("user", "assistant") or not isinstance(text, str):
            return "history"
        text = " ".join(text.split())
        if not 0 < len(text) <= sitechat.MESSAGE_MAX:
            return "history"
        history.append(sitechat.Turn(role, text))
    return message, history


@router.options(PATH)
async def site_chat_preflight(slug: str, request: Request) -> Response:
    facts = await _facts(slug)
    if facts is None:
        return _bare(404, "not here")
    origin, allowed = _origin(request, facts)
    if not allowed or origin is None:
        return _bare(403, "origin not allowed")
    headers = _cors(origin)
    headers.update({"Access-Control-Allow-Methods": "GET, POST",
                    "Access-Control-Allow-Headers": "Content-Type",
                    "Access-Control-Max-Age": "600"})
    return Response(status_code=204, headers=headers)


@router.get(PATH)
async def site_chat_status(slug: str, request: Request) -> Response:
    facts = await _facts(slug)
    if facts is None:
        return _bare(404, "not here")
    origin, allowed = _origin(request, facts)
    if not allowed:
        return _bare(403, "origin not allowed")
    if not _reads.allow(effective_client(request)):
        return _json({"status": "busy"}, 429, origin)
    code, why = await sitechat.status(facts)
    if code != 200:
        log.info("site chat %s: status resting (%s)", slug, why)
        return _json({"status": "resting"}, 503, origin)
    return _json({"status": "ready"}, 200, origin)


@router.post(PATH)
async def site_chat(slug: str, request: Request) -> Response:
    facts = await _facts(slug)
    if facts is None:
        return _bare(404, "not here")
    origin, allowed = _origin(request, facts)
    if not allowed or origin is None:
        return _bare(403, "origin not allowed")
    _posts.limit = sitechat.per_ip_per_min()
    if not _posts.allow(effective_client(request)):
        log.info("site chat %s: per-address limit", slug)
        return _json({"status": "busy", "reply": None}, 429, origin)

    body = await _body(request)
    if body is None:
        return _json({"error": "too large"}, 413, origin)
    media = request.headers.get("content-type", "").split(";")[0].strip().lower()
    if media != "application/json":
        return _json({"error": "json only"}, 415, origin)
    try:
        payload = json.loads(body, parse_constant=_no_constants)
    except (ValueError, UnicodeDecodeError):
        return _json({"error": "invalid json"}, 400, origin)
    if not isinstance(payload, dict):
        return _json({"error": "invalid json"}, 400, origin)
    shaped = _shape(payload)
    if isinstance(shaped, str):
        return _json({"error": "invalid field", "field": shaped}, 422, origin)
    message, history = shaped

    reply = await sitechat.converse(facts, message, history)
    log.info("site chat %s: %s%s", slug, reply.outcome,
             f" ({reply.detail})" if reply.detail else "")
    if reply.text is None:
        state = "busy" if reply.status == 429 else "resting"
        return _json({"status": state, "reply": None}, reply.status, origin)
    # "refused": the visitor's message carried a card or ID number. The widget
    # keeps neither turn, so the number is not sent back as history.
    state = "refused" if reply.outcome == "refused" else "ok"
    return _json({"status": state, "reply": reply.text}, 200, origin)
