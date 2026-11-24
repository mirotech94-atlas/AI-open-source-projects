"""Client of the `qa-browser` service (browser_service.py, compose profile
`qa-browser`): desktop and iPhone screenshots and PDFs of a page.

`QA_BROWSER_URL` is `unix:/run/qa/qa-browser.sock` (the socket in the
qa_sock volume; the service has no TCP port and no network path to the
api). UNKNOWN, never OK, when it is unset, not a unix: path, or the service
does not answer: a screenshot that was not taken is said as not taken. The api side
asks only for a URL whose host the caller already allowed (qa.check_site
uses the security allowlist); the service holds its own static allowlist
too.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from urllib.parse import urlsplit

import httpx

ENV = "QA_BROWSER_URL"
TIMEOUT = 30.0
MAX_BYTES = 8 * 1024 * 1024
DESKTOP, IPHONE = 1440, 390


@dataclass(frozen=True, slots=True)
class Render:
    ok: bool                 # False: UNKNOWN (not rendered), with why
    detail: str
    data: bytes = b""
    content_type: str = ""


ROOT = "http://qa-browser"      # the name in the request line only


def socket_path() -> str | None:
    """The service's Unix socket from QA_BROWSER_URL, or None."""
    raw = (os.getenv(ENV) or "").strip()
    if not raw.startswith("unix:"):
        return None
    path = urlsplit(raw).path or raw[len("unix:"):]
    return path if path.startswith("/") and ".." not in path else None


async def _call(path: str, params: dict[str, str | int],
                client: httpx.AsyncClient | None = None) -> Render:
    sock = socket_path()
    if sock is None:
        return Render(False, f"not rendered: {ENV} is not set to a unix: socket "
                             "(UNKNOWN)")
    own = client is None
    if client is None:
        client = httpx.AsyncClient(timeout=TIMEOUT, follow_redirects=False,
                                   trust_env=False,
                                   transport=httpx.AsyncHTTPTransport(uds=sock))
    try:
        r = await client.get(f"{ROOT}{path}", params=params)
    except (httpx.HTTPError, OSError) as exc:
        return Render(False, f"not rendered: the qa-browser service did not answer "
                             f"({type(exc).__name__}) (UNKNOWN)")
    finally:
        if own:
            await client.aclose()
    if r.status_code != 200:
        return Render(False, f"not rendered: qa-browser said HTTP {r.status_code} "
                             f"({r.text[:120]}) (UNKNOWN)")
    if len(r.content) > MAX_BYTES:
        return Render(False, "not rendered: the image was over the size cap (UNKNOWN)")
    return Render(True, "rendered", r.content,
                  r.headers.get("content-type", ""))


async def shot(url: str, width: int, client: httpx.AsyncClient | None = None) -> Render:
    return await _call("/shot", {"url": url, "width": int(width)}, client)


async def pdf(url: str, client: httpx.AsyncClient | None = None) -> Render:
    return await _call("/pdf", {"url": url}, client)
