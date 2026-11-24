"""The website contact form: POST /public/agency/contact.

One plain form post (or the same post made by fetch, which asks for JSON). The
request does exactly three things: validate, store a row, answer. It sends
nothing: delivery to the owner (email with Reply-To the visitor, WhatsApp) and the
visitor's confirmation are the executor's, driven by `kernel/agency_contact.py`
(modules/agency/contact.py has the whole story).

Public, so: a body cap, form posts only (no cookies, so nothing to forge),
a honeypot (`website`) a person never fills, a per-source flood guard and a per-
address limit (the address is hashed in the limiter, never kept), identical
double posts collapse to one row. A bot and a throttled visitor get the same
"received" answer as everyone else; only a validation problem or a failed write
says otherwise, and a failed write says plainly that nothing was saved.
"""
from __future__ import annotations

import hashlib
import json
import logging
from html import escape
from urllib.parse import parse_qs

from fastapi import APIRouter, Request, Response
from fastapi.responses import (
    HTMLResponse,
    JSONResponse,
    PlainTextResponse,
    RedirectResponse,
)

from ..modules import store_origin
from ..modules.agency import contact
from .peers import PerAddressLimiter, effective_client

log = logging.getLogger("atlas.agency.contact")
router = APIRouter()

MAX_BODY = 12 * 1024
NO_STORE = {"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"}
# Behind the public edge every visitor shares one bucket, so the per-source
# limiter is a flood guard for the whole site; the per-address one holds a hash.
_ip = PerAddressLimiter(40, 3600.0)
_email = PerAddressLimiter(3, 86400.0)
THANKS = {"landing": "/thanks/"}
HOME = "/"
MAIL = store_origin.CONTACT_MAIL


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _wants_json(request: Request) -> bool:
    return "application/json" in request.headers.get("accept", "")


def _page(title: str, body: str, status: int) -> HTMLResponse:
    html = (f'<!doctype html><html lang="en"><head><meta charset="utf-8">'
            f'<meta name="viewport" content="width=device-width,initial-scale=1">'
            f'<meta name="robots" content="noindex"><title>{escape(title)}</title>'
            "<style>body{font:16px/1.5 system-ui,sans-serif;max-width:34rem;margin:12vh "
            "auto;padding:0 1rem;color:#14282a}a{color:inherit}</style></head><body>"
            f"<h1>{escape(title)}</h1>{body}"
            f'<p><a href="{HOME}">Back</a></p></body></html>')
    return HTMLResponse(html, status_code=status, headers=NO_STORE)


def _json(payload: dict, status: int) -> JSONResponse:
    return JSONResponse(payload, status_code=status, headers=NO_STORE)


def _received(request: Request, source: str) -> Response:
    """The one answer to every accepted post: new, duplicate, throttled or bot."""
    if _wants_json(request):
        return _json({"ok": True, "message": "Got it. Miro will reply to your email "
                      "soon."}, 200)
    target = THANKS.get(source)
    if target:
        return RedirectResponse(target, status_code=303, headers=NO_STORE)
    return _page("Message received", "<p>Thank you. Miro will reply to the email "
                 "address you gave, usually within a day.</p>", 200)


def _problem(request: Request, message: str, field: str, status: int) -> Response:
    if _wants_json(request):
        return _json({"ok": False, "error": message, "field": field}, status)
    return _page("Please check the form", f"<p>{escape(message)}</p>", status)


@router.post(contact.PATH)
async def post_contact(request: Request) -> Response:
    ctype = request.headers.get("content-type", "").split(";")[0].strip().lower()
    if ctype not in ("application/x-www-form-urlencoded", "application/json"):
        return PlainTextResponse("form posts only", status_code=415)
    chunks, size = [], 0
    async for chunk in request.stream():
        size += len(chunk)
        if size > MAX_BODY:
            return PlainTextResponse("too large", status_code=413)
        chunks.append(chunk)
    raw = b"".join(chunks).decode("utf-8", "replace")
    form: dict[str, str]
    if ctype == "application/json":
        try:
            data = json.loads(raw)
        except ValueError:
            data = None
        if not isinstance(data, dict):
            return PlainTextResponse("bad request", status_code=400)
        form = {str(k): v for k, v in data.items() if isinstance(v, str)}
    else:
        form = {k: (v[0] if v else "") for k, v in
                parse_qs(raw, keep_blank_values=True).items()}
    source = form.get("source", "")
    if not _ip.allow(effective_client(request)):
        return _problem(request, "Too many messages right now. Please try again in a "
                                 f"while, or write to {MAIL}.", "", 429)
    if form.get("website", "").strip():                      # a bot: same answer, no row
        return _received(request, source)
    sub, error, field = contact.validate(form)
    if sub is None:
        return _problem(request, error or "Please check the form.", field, 400)
    if not _email.allow(_hash(sub["email"])):
        return _received(request, sub["source"])
    try:
        await contact.submit(sub, _hash(effective_client(request)))
    except Exception as exc:  # noqa: BLE001
        log.error("agency contact: a message could not be stored (%s)", type(exc).__name__)
        return _problem(request, f"We could not save that right now. Nothing was sent. "
                                 f"Please email {MAIL} instead.", "", 503)
    return _received(request, sub["source"])
