"""The "Have Mapfront call me" form's public door (atlas/modules/agency/callme.py).

    OPTIONS /public/call-me/{slug}    CORS preflight
    POST    /public/call-me/{slug}    {"name", "phone", "consent": true,
                                       "version", "page", "website" (honeypot)}

Public, with no session, like `/public/site-chat/`: a visitor on a preview
site has none. Only `/public/call-me/<one DNS label>` is let past the mesh
guard (`main.PUBLIC_PATTERNS`). In order, each refusal saying nothing about
the next:

    404  not a label, or no deployed site (with a lead) by that label
    403  an Origin other than https://agency.example.com (or the site's own
         domain), or `null` for a site that is not deployed (see below); a POST
         needs one
    429  this address past 3 in ten minutes, or this site past 20 today
    413  a body over 4 KB
    415  not application/json
    400  not a JSON object
    422  a missing or bad field: `name` 1-80 characters, `phone` a real US
         number, `consent` not literally true, `page` not on this site
    409  a consent `version` this server never showed (the page is stale)
    200  {"status": "ok", "message": "..."} -- the same answer whether the
         consent was recorded, the number is suppressed, or the honeypot was
         filled, so the form cannot be used to learn who opted out

Then, only after a recorded consent: `agency.call_lead` through the kernel
(`kernel.lead_followup.call_due`, Tier 1 since 2026-10-06, and only while
the consent guard passes -- inside calling hours, the number not
suppressed, the lead verified; a refusal is the Tier 2 card) and a Slack
alert. Nothing here reaches the executor itself; the kernel does.

`Origin: null` (weakened check, the owner 2026-11-14): previews are served at
`agency.example.com/preview/<slug>/` under a CSP `sandbox` WITHOUT
`allow-same-origin`, so the browser sends `Origin: null` from them. It is
accepted only for a slug in the URL path that is a deployed site with a lead
(`_site`); any sandboxed page can send it, so it no longer proves which page
called. The rate limits, the honeypot, the explicit consent and the stricter
`page` check stay: `page` must be `/preview/<this slug>/...` on the preview
origin, or a page on the site's own domain.

The client address is the one Caddy forwards, believed only from Caddy
itself (`peers.effective_client`), and it is stored only as an HMAC.
"""
from __future__ import annotations

import json
import logging
from urllib.parse import urlsplit

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, Response

from ..modules.agency import callme, hosting
from .peers import PerAddressLimiter, effective_client

router = APIRouter()
log = logging.getLogger("atlas.call_me")

PATH = "/public/call-me/{slug}"
PREFIX = "/public/call-me/"
_per_ip = PerAddressLimiter(callme.PER_IP, callme.PER_IP_WINDOW_S)
_per_site = PerAddressLimiter(callme.PER_SITE, 86400.0)


def _headers(origin: str | None) -> dict[str, str]:
    headers = {"Cache-Control": "no-store", "Vary": "Origin",
               "X-Content-Type-Options": "nosniff"}
    if origin:
        headers["Access-Control-Allow-Origin"] = origin
    return headers


def _json(body: dict, status: int, origin: str | None) -> Response:
    return JSONResponse(body, status_code=status, headers=_headers(origin))


def _bare(status: int, text: str) -> Response:
    return JSONResponse({"error": text}, status_code=status,
                        headers={"Cache-Control": "no-store"})


async def _site(slug: str) -> tuple[dict, dict] | None:
    try:
        return await callme.site_lead(slug)
    except callme.NotHere:
        return None
    except Exception as exc:                            # noqa: BLE001
        log.warning("call-me %s: sites unreadable (%s)", slug, type(exc).__name__)
        return None


def _origin(request: Request, site: dict) -> tuple[str | None, bool]:
    given = request.headers.get("origin")
    if given is None:
        return None, True
    given = given.strip().lower().rstrip("/")
    if given == "null":                      # a sandboxed preview; `site` is deployed
        return given, True
    return (given, True) if given in callme.origins(site) else (None, False)


async def _body(request: Request) -> bytes | None:
    declared = request.headers.get("content-length")
    if declared is not None:
        try:
            if int(declared) > callme.BODY_MAX:
                return None
        except ValueError:
            return None
    chunks: list[bytes] = []
    size = 0
    async for chunk in request.stream():
        size += len(chunk)
        if size > callme.BODY_MAX:
            return None
        chunks.append(chunk)
    return b"".join(chunks)


def _no_constants(name: str) -> float:
    raise ValueError(name)


def _page_ok(page: str, site: dict) -> bool:
    parts = urlsplit(page)
    origin = f"{parts.scheme}://{(parts.hostname or '').lower()}"
    if parts.scheme != "https" or origin not in callme.origins(site):
        return False
    if origin == hosting.preview_origin():       # shared host: the path names the site
        return hosting.slug_of_preview_path(parts.path) == site.get("slug")
    return True


@router.options(PATH)
async def call_me_preflight(slug: str, request: Request) -> Response:
    found = await _site(slug)
    if found is None:
        return _bare(404, "not here")
    origin, allowed = _origin(request, found[0])
    if not allowed or origin is None:
        return _bare(403, "origin not allowed")
    headers = _headers(origin)
    headers.update({"Access-Control-Allow-Methods": "POST",
                    "Access-Control-Allow-Headers": "Content-Type",
                    "Access-Control-Max-Age": "600"})
    return Response(status_code=204, headers=headers)


@router.post(PATH)
async def call_me(slug: str, request: Request) -> Response:
    found = await _site(slug)
    if found is None:
        return _bare(404, "not here")
    site, lead = found
    origin, allowed = _origin(request, site)
    if not allowed or origin is None:
        return _bare(403, "origin not allowed")
    client = effective_client(request)
    if not _per_ip.allow(client) or not _per_site.allow(f"site:{slug}"):
        log.info("call-me %s: rate limited", slug)
        return _json({"status": "busy"}, 429, origin)

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

    ok = {"status": "ok", "message": callme.DONE_MESSAGE}
    if payload.get("website"):
        log.info("call-me %s: honeypot filled, nothing recorded", slug)
        return _json(ok, 200, origin)
    name = callme.clean_text(payload.get("name"), callme.NAME_MAX)
    if name is None:
        return _json({"error": "invalid field", "field": "name"}, 422, origin)
    phone = callme.us_phone(payload.get("phone"))
    if phone is None:
        return _json({"error": "invalid field", "field": "phone"}, 422, origin)
    if payload.get("consent") is not True:
        return _json({"error": "invalid field", "field": "consent"}, 422, origin)
    page = callme.clean_text(payload.get("page"), callme.PAGE_MAX)
    if page is None or not _page_ok(page, site):
        return _json({"error": "invalid field", "field": "page"}, 422, origin)
    version = payload.get("version")
    if not isinstance(version, str) or version not in callme.CONSENT_VERSIONS:
        return _json({"error": "stale page", "field": "version"}, 409, origin)

    try:
        sub = await callme.submit(
            slug, name=name, phone=phone, page_url=page, ip=client,
            user_agent=request.headers.get("user-agent", ""), version=version)
    except callme.NotHere:
        return _bare(404, "not here")
    if not sub.recorded or sub.consent_id is None:
        log.info("call-me %s: nothing recorded (%s)", slug, sub.why)
        return _json(ok, 200, origin)
    from ..kernel.lead_followup import call_due
    try:
        card = await call_due(sub.consent_id)
    except Exception as exc:                            # noqa: BLE001
        log.warning("call-me %s: the call could not be placed (%s)", slug,
                    type(exc).__name__)
        card = "the call could not be placed; the next tick tries again"
    await callme.alert(lead, sub, card, name=name, page_url=page)
    log.info("call-me %s: consent recorded (%s)", slug, card.split(":")[0])
    return _json(ok, 200, origin)
