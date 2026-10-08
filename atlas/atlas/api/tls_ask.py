"""Caddy's on-demand TLS question: may it obtain a certificate for this name?

    GET /internal/tls-ask?domain=<name>

`*.mapfront.io` is served with on-demand TLS (Caddyfile), so Caddy mints a
certificate the first time a client asks for a name under it. Without a
gate, anyone could point a random label at the box and make Caddy spend
Let's Encrypt's rate limit and fill `/data` with certificates. Caddy asks
this endpoint first and proceeds only on a 200 -- given for the apex, `www`,
and a slug `agency.deploy_site` has actually published
(`hosting.tls_allowed`). Every other answer is a bare 404.

Reachable only from Caddy's own ask, twice over:

* it is not in `PUBLIC_PATHS`, so the mesh middleware refuses it to any
  source outside the mesh and the Docker bridge, and the Caddyfile proxies
  nothing under `/internal/` on any public host;
* here, the immediate peer must be on the Docker bridge (Caddy's
  container) AND the request must carry no `X-Forwarded-For`. Caddy's
  `reverse_proxy` always sets that header, and its on-demand `ask` is a
  plain GET that never does -- so a mesh client whose request Caddy
  proxied is refused here too.

Read-only: a stat of the publish root, nothing else. It logs nothing, so
a scan of random names cannot fill the log.
"""
from __future__ import annotations

import asyncio

from fastapi import APIRouter, Request
from fastapi.responses import PlainTextResponse

from ..modules.agency import hosting
from .peers import from_proxy

router = APIRouter()

PATH = "/internal/tls-ask"
MAX_DOMAIN = 253


def _from_caddy_ask(request: Request) -> bool:
    return from_proxy(request) and "x-forwarded-for" not in request.headers


@router.get(PATH, include_in_schema=False)
async def tls_ask(request: Request, domain: str = "") -> PlainTextResponse:
    if not _from_caddy_ask(request) or not domain or len(domain) > MAX_DOMAIN:
        return PlainTextResponse("no", status_code=404)
    allowed = await asyncio.to_thread(hosting.tls_allowed, domain)
    if not allowed:
        return PlainTextResponse("no", status_code=404)
    return PlainTextResponse("ok", status_code=200)
