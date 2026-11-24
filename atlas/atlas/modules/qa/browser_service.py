"""The qa-browser service: screenshots and PDFs of an allowed public page.

It listens on a Unix socket in a volume shared with the api only (no TCP
port at all) and sits alone on its own compose network: it has no route to
the api, Postgres, Redis or the mesh. Pages it may open are public https
hosts on a static allowlist -- never a compose service name. A local
preview the QA gate needs is checked inside the api process, not here.

Runs in the Playwright image with the standard library and Playwright only;
it imports nothing from ATLAS, reads no env file and holds no secret.

  GET /shot?url=<https url>&width=<320..1920>  -> image/png
  GET /pdf?url=<https url>                     -> application/pdf
  GET /health                                  -> ok

A fresh browser context per request (no cookies, no storage, service
workers blocked), a 20 s deadline. All traffic leaves through a proxy that
always applies: QA_PROXY (the allowlisting `qa-proxy`, the only way off the
internal `qa_egress` network) or, unset, a dead address so nothing leaves
at all. WebRTC may not use UDP outside that proxy, and TURN/TCP has only the
proxy to go to. The network is the control; the in-page rules below are
belt and braces.
The rules sit on the browser CONTEXT, set before the first page exists, so
a popup is held to them too and is closed (and the render refused). No
WebSocket is ever connected. Every request goes through the route and is fetched by Playwright with redirects off: a
host outside ALLOWED is aborted, and a 3xx whose Location is off the
allowlist is aborted too. Every request the browser issues (redirect hops
included) is also recorded; if any one was off the allowlist, or the final
page URL is, nothing is returned.
"""
from __future__ import annotations

import json
import os
import socketserver
import sys
from http.server import BaseHTTPRequestHandler
from urllib.parse import parse_qs, urljoin, urlsplit

SOCKET = "/run/qa/qa-browser.sock"
TIMEOUT_MS = 20_000
MIN_W, MAX_W = 320, 1920
def _store_host() -> str:
    """The store's host from ACADEMY_PUBLIC_ORIGIN (default agency.storefront.test).
    Inlined, not imported: this file runs alone in its container."""
    raw = os.environ.get("ACADEMY_PUBLIC_ORIGIN", "").strip().rstrip("/").lower()
    host = raw.removeprefix("https://") if raw.startswith("https://") else ""
    ok = (host and "." in host
          and all(c in "abcdefghijklmnopqrstuvwxyz0123456789.-" for c in host))
    return host if ok else "agency.storefront.test"


# Public https hosts only, never a compose service name or an address. The
# agency landing, the store and the client previews are one host.
ALLOWED = frozenset({"agency.storefront.test", _store_host()})
# Inline content a page builds itself; it opens no connection.
INLINE_SCHEMES = ("data:", "blob:", "about:")


def allowed(url: str) -> bool:
    try:
        parts = urlsplit(url)
        port = parts.port
    except ValueError:
        return False
    host = (parts.hostname or "").lower().rstrip(".")
    if parts.scheme != "https" or parts.username or parts.password or not host:
        return False
    if port not in (None, 443):
        return False
    return host in ALLOWED


def request_allowed(url: str) -> bool:
    """A request the page may make: an allowed https URL or inline data."""
    return url.startswith(INLINE_SCHEMES) or allowed(url)


def redirect_allowed(base: str, status: int, location: str | None) -> bool:
    """A 3xx may be followed only to an allowed URL; anything else passes."""
    if status not in (301, 302, 303, 307, 308):
        return True
    return bool(location) and allowed(urljoin(base, location or ""))


def width_of(raw: str | None) -> int | None:
    try:
        value = int(raw or "1440")
    except ValueError:
        return None
    return value if MIN_W <= value <= MAX_W else None


class Refused(Exception):
    """The page left the allowlist; nothing is returned."""


def verdict(final_url: str, chain: list[str], seen: list[str]) -> str | None:
    """Why a rendered page may not be returned, or None: the final URL, every
    hop of the redirect chain and every request the browser issued must be
    on the allowlist."""
    if not allowed(final_url):
        return "the page ended off the allowlist"
    if any(not allowed(u) for u in chain):
        return "a redirect left the allowlist"
    if any(not request_allowed(u) for u in seen):
        return "the page asked for a host off the allowlist"
    return None


# Where a proxy is not configured: a non-routable SOCKS address, so every
# connection (TURN over TCP included) fails instead of going direct.
DEAD_PROXY = "socks5://127.0.0.1:1"
# Loopback is proxied too (Chromium otherwise bypasses it).
PROXY_BYPASS = "<-loopback>"
# Chromium flags: WebRTC may use no UDP path outside the proxy, and the
# proxy only forwards CONNECT to allowlisted hosts on 443.
LAUNCH_ARGS = (
    "--force-webrtc-ip-handling-policy=disable_non_proxied_udp",
    "--webrtc-ip-handling-policy=disable_non_proxied_udp",
)


def proxy_server() -> str:
    """The proxy every connection uses: QA_PROXY when it is an http:// or
    socks5:// URL with a host and port, else DEAD_PROXY. Never none."""
    raw = os.environ.get("QA_PROXY", "").strip()
    try:
        parts = urlsplit(raw)
        port = parts.port
    except ValueError:
        return DEAD_PROXY
    if (parts.scheme in ("http", "socks5") and parts.hostname and port
            and not parts.username and not parts.path.strip("/")):
        return f"{parts.scheme}://{parts.hostname}:{port}"
    return DEAD_PROXY


def launch_options() -> dict:
    """Playwright launch options: the proxy applies to the browser and to
    `route.fetch` alike, plus the WebRTC flags."""
    server = proxy_server()
    return {"args": [*LAUNCH_ARGS, f"--proxy-server={server}",
                     f"--proxy-bypass-list={PROXY_BYPASS}"],
            "proxy": {"server": server, "bypass": PROXY_BYPASS}}


# Removed before any page script runs. Not a control (a page can find other
# handles); the proxy and the internal network are.
NO_WEBRTC = """
for (const k of ["RTCPeerConnection", "webkitRTCPeerConnection",
                 "RTCDataChannel", "RTCSessionDescription"]) {
  try { Object.defineProperty(window, k, {value: undefined, configurable: false}); }
  catch (e) {}
}
"""


class Guard:
    """Everything the page's browser context may do, set on the CONTEXT
    before any page exists, so a popup or a second page is held to the same
    rules: every request is recorded and routed through `gate`, every
    WebSocket is closed without ever connecting, and any page beyond the
    first is closed and marks the render refused."""

    def __init__(self) -> None:
        self.seen: list[str] = []
        self.refused: list[str] = []
        self.main = None

    def gate(self, route) -> None:                      # type: ignore[no-untyped-def]
        url = route.request.url
        if url.startswith(INLINE_SCHEMES):
            route.continue_()
            return
        if not allowed(url):
            route.abort()
            return
        response = route.fetch(max_redirects=0, timeout=TIMEOUT_MS)
        if not redirect_allowed(url, response.status, response.headers.get("location")):
            route.abort()
            return
        route.fulfill(response=response)

    def socket(self, ws) -> None:                       # type: ignore[no-untyped-def]
        """No WebSocket is ever opened: a screenshot needs none."""
        self.refused.append("the page tried to open a WebSocket")
        ws.close()

    def on_page(self, page) -> None:                    # type: ignore[no-untyped-def]
        if self.main is None:
            self.main = page
            return
        self.refused.append("the page opened a popup or a second page")
        page.close()

    def install(self, context) -> None:                 # type: ignore[no-untyped-def]
        context.add_init_script(NO_WEBRTC)
        context.on("request", lambda req: self.seen.append(req.url))
        context.on("page", self.on_page)
        context.route("**/*", self.gate)
        context.route_web_socket("**/*", self.socket)

    def verdict(self, final_url: str, chain: list[str]) -> str | None:
        if self.refused:
            return self.refused[0]
        return verdict(final_url, chain, self.seen)


def render(url: str, width: int | None, as_pdf: bool) -> bytes:
    from playwright.sync_api import sync_playwright  # only in the image

    guard = Guard()
    with sync_playwright() as p:
        browser = p.chromium.launch(**launch_options())
        try:
            mobile = bool(width and width < 768)
            context = browser.new_context(
                viewport={"width": width or 1280, "height": 900},
                is_mobile=mobile, has_touch=mobile, accept_downloads=False,
                java_script_enabled=True, service_workers="block")
            guard.install(context)              # before the first page exists
            page = context.new_page()
            response = page.goto(url, timeout=TIMEOUT_MS, wait_until="networkidle")
            chain = []
            req = response.request if response is not None else None
            while req is not None:
                chain.append(req.url)
                req = req.redirected_from
            refused = guard.verdict(page.url, chain)
            if refused:
                raise Refused(refused)
            if as_pdf:
                body = page.pdf(format="A4", print_background=True)
            else:
                body = page.screenshot(full_page=True, timeout=TIMEOUT_MS)
            refused = guard.verdict(page.url, chain)     # anything since
            if refused:
                raise Refused(refused)
            return body
        finally:
            browser.close()


class Handler(BaseHTTPRequestHandler):
    server_version = "qa-browser"

    def _send(self, code: int, body: bytes, ctype: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _error(self, code: int, why: str) -> None:
        self._send(code, json.dumps({"error": why}).encode(), "application/json")

    def do_GET(self) -> None:
        parts = urlsplit(self.path)
        if parts.path == "/health":
            self._send(200, b"ok", "text/plain")
            return
        if parts.path not in ("/shot", "/pdf"):
            self._error(404, "no such endpoint")
            return
        query = parse_qs(parts.query)
        url = (query.get("url") or [""])[0]
        if not allowed(url):
            self._error(403, "host not allowed")
            return
        as_pdf = parts.path == "/pdf"
        width = None if as_pdf else width_of((query.get("width") or [None])[0])
        if not as_pdf and width is None:
            self._error(400, f"width must be {MIN_W}-{MAX_W}")
            return
        try:
            body = render(url, width, as_pdf)
        except Refused as exc:
            self._error(403, str(exc))
            return
        except Exception as exc:  # noqa: BLE001
            self._error(502, f"not rendered: {type(exc).__name__}")
            return
        self._send(200, body, "application/pdf" if as_pdf else "image/png")

    def log_message(self, fmt: str, *args) -> None:    # type: ignore[no-untyped-def]
        # The path only, never a query string.
        sys.stderr.write(f"qa-browser {self.command} {urlsplit(self.path).path}\n")


class Server(socketserver.ThreadingMixIn, socketserver.UnixStreamServer):
    daemon_threads = True

    def get_request(self):                              # type: ignore[no-untyped-def]
        conn, _addr = super().get_request()
        return conn, ("qa", 0)       # BaseHTTPRequestHandler wants a tuple


def main(path: str = SOCKET) -> None:
    if os.path.exists(path):
        os.unlink(path)
    server = Server(path, Handler)
    os.chmod(path, 0o666)            # the api runs as another uid
    server.serve_forever()


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else SOCKET)
