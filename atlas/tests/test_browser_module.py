"""modules/browser — eyes on the web, never a login, never a submit without a gate.

What is pinned here:
* the deny policy: the extension's list mirrored exactly, host-only matching,
  Google's results page, literal local addresses, credentials in a query,
  `BROWSER_DENY` extending rather than replacing
* a fresh, credential-free browser context per call, one browser at a time,
  every request checked (policy + resolved address) and denied ones aborted,
  redirect hops and frames checked after load and a page that went anywhere
  it may not refused with no text or screenshot kept
* every page byte wrapped before it can reach a prompt, saved under
  data/browser/, recorded in browser_sessions without its query
* search through Brave or DuckDuckGo, never Google; find_business skips
  directories and never silently skips a denied hit
* `browser.submit_form` is the only write: Tier ASK, irreversible, a dry run
  that reports which selectors exist and where the form posts, never a form
  with a password field, and a blocked submission is a failure
* the tools (URLs wrapped, the asking agent recorded), the agents that hold
  them, and the 24 h purge
"""
from __future__ import annotations

import ast
import asyncio
import os
import pathlib
import re
import socket
import sys
import time
import types
from collections import OrderedDict

import pytest

from atlas.kernel import privacy
from atlas.kernel.contracts import Health, Tier
from atlas.kernel.permissions import denial_reason
from atlas.modules import _netguard, browser
from atlas.modules.browser import policy, session

ROOT = pathlib.Path(__file__).resolve().parent.parent
PKG = ROOT / "atlas" / "modules" / "browser"
MISSING = ("playwright not installed: pip install atlas[browser] && "
           "playwright install chromium")
PUBLIC = "93.184.216.34"

# What the fake resolver answers. Anything not listed resolves public.
DNS: dict[str, list[str] | Exception] = {
    "localtest.me": ["127.0.0.1"],
    "127.0.0.1.nip.io": ["127.0.0.1"],
    "169.254.169.254.nip.io": ["169.254.169.254"],
    "meta.nip.io": ["169.254.169.254"],
    "mixed.example": [PUBLIC, "10.0.0.7"],
    "mesh.example": ["100.92.10.4"],
    "ula.example": ["fd12:3456::1"],
    "nxdomain.example": socket.gaierror(8, "nodename nor servname provided"),
}


@pytest.fixture(autouse=True)
def resolver(monkeypatch):
    """No network in tests: names resolve from DNS above, and every lookup
    is counted so the cache can be checked."""
    calls: list[str] = []

    async def fake(host):
        calls.append(host)
        answer = DNS.get(host, [PUBLIC])
        if isinstance(answer, Exception):
            raise answer
        return list(answer)

    monkeypatch.setattr(_netguard, "_getaddrinfo", fake)
    monkeypatch.setattr(_netguard, "_CACHE", OrderedDict())
    monkeypatch.setattr(session, "_ONE_AT_A_TIME", None)
    monkeypatch.setattr(session, "SETTLE_S", 0)
    return calls


# ------------------------------------------------------------ fake playwright

class FakeWorld:
    """What the fake web looks like, and a record of what was done to it."""

    def __init__(self) -> None:
        self.title = "Joe's Pizza"
        self.text = "Joe's Pizza. Open 11-22. Call 0161 000 0000."
        self.status = 200
        self.chains: dict[str, list[str]] = {}     # asked -> redirect hops, last lands
        self.frames: list[str] = []
        self.subresources: list[str] = []
        self.sub_outcomes: dict[str, str] = {}
        # Redirect hops of a sub-resource (fetch/XHR/img): emitted as request
        # events, never routed — as Playwright does.
        self.sub_hops: dict[str, list[str]] = {}
        # What happens WHILE the text and screenshot are being taken: a
        # main-frame hop (a timer navigating through a redirect), and frames
        # that were blank at load and navigate late.
        self.capture_hops: list[str] = []
        self.late_frames: list[str] = []
        self.websockets: list[str] = []
        self.ws_outcomes: dict[str, str] = {}
        # A WebSocket a dedicated Worker opens: route_web_socket never sees
        # it, page.on("websocket") does. Emitted during capture.
        self.worker_sockets: list[str] = []
        # Sub-resource hops that produce a request event and nothing else (no
        # response), and frame navigations with no request event at all: each
        # is caught by exactly one of _refusal's two loops.
        self.silent_hops: dict[str, list[str]] = {}
        self.bare_frame_navs: list[str] = []
        # Windows the page opens during capture: (url, sockets its worker
        # opens). Emitted through the context's `page` event, as Playwright does.
        self.popups: list[tuple[str, list[str]]] = []
        self.closed_popups: list[str] = []
        self.selectors: dict[str, str] = {}        # selector -> input type
        self.form_password = False
        self.form_action: str | None = "https://joespizza.co.uk/contact/send"
        self.post_to: str | None = None            # where the submit really goes
        self.launches: list[dict] = []
        self.in_flight = 0
        self.max_in_flight = 0
        self.contexts: list[FakeContext] = []
        self.gotos: list[tuple[str, dict]] = []
        self.filled: list[tuple[str, str]] = []
        self.submitted = 0
        self.goto_delay = 0.0
        self.after_submit_delay = 0.0
        self.launch_error: Exception | None = None


class FakeFrame:
    def __init__(self, url: str, parent=None) -> None:
        self.url, self.parent_frame = url, parent


class FakeRequest:
    def __init__(self, url: str, *, nav: bool, main: bool, method: str = "GET"):
        self.url, self._nav, self.method = url, nav, method
        self.frame = FakeFrame(url, None if main else FakeFrame("https://parent/"))

    def is_navigation_request(self) -> bool:
        return self._nav


class FakeResponseEvent:
    def __init__(self, url: str) -> None:
        self.url = url


class FakeWebSocket:
    def __init__(self, world: FakeWorld, url: str) -> None:
        self.world, self.url = world, url

    def connect_to_server(self):
        self.world.ws_outcomes[self.url] = "connected"
        return self

    async def close(self, **kw) -> None:
        self.world.ws_outcomes[self.url] = "closed"


class FakeRoute:
    def __init__(self, request: FakeRequest) -> None:
        self.request = request
        self.outcome: str | None = None

    async def abort(self, *a) -> None:
        self.outcome = "abort"

    async def continue_(self, *a, **kw) -> None:
        self.outcome = "continue"


class FakeResponse:
    def __init__(self, status: int) -> None:
        self.status = status


class FakeElement:
    def __init__(self, page: FakePage, selector: str) -> None:
        self.page, self.world, self.selector = page, page.world, selector

    async def fill(self, value: str) -> None:
        self.world.filled.append((self.selector, value))

    async def evaluate(self, script: str):
        if "requestSubmit" in script:
            if not self.world.form_action:
                return False
            self.world.submitted += 1
            target = self.world.post_to or self.world.form_action
            await self.page.context.through_route(target, nav=True, main=True,
                                                  method="POST")
            if self.world.after_submit_delay:
                await asyncio.sleep(self.world.after_submit_delay)
            return True
        return {"type": self.world.selectors[self.selector],
                "formPassword": self.world.form_password,
                "action": self.world.form_action,
                "method": "post" if self.world.form_action else None}


class FakePage:
    def __init__(self, world: FakeWorld, context: FakeContext) -> None:
        self.world, self.context = world, context
        self.url = "about:blank"
        self.frame_listeners: list = []
        self.socket_listeners: list = []
        self.late_loaded = False

    def on(self, event: str, fn) -> None:
        fn._pw_impl_instance_seen = True
        {"framenavigated": self.frame_listeners,
         "websocket": self.socket_listeners}[event].append(fn)

    @property
    def frames(self):
        main = FakeFrame(self.url)
        late = [FakeFrame(u if self.late_loaded else "", main)
                for u in self.world.late_frames]
        return [main] + [FakeFrame(u, main) for u in self.world.frames] + late

    def _during_capture(self) -> None:
        """What lands while the page is being captured, once."""
        if self.late_loaded:
            return
        self.late_loaded = True
        for hop in self.world.capture_hops:
            self.context.emit(FakeRequest(hop, nav=True, main=True))
        for url in self.world.late_frames:
            self.context.emit(FakeRequest(url, nav=True, main=False))
            for fn in self.frame_listeners:
                fn(FakeFrame(url, FakeFrame(self.url)))
        for url in self.world.bare_frame_navs:
            for fn in self.frame_listeners:
                fn(FakeFrame(url, FakeFrame(self.url)))
        for url in self.world.worker_sockets:
            for fn in self.socket_listeners:
                fn(types.SimpleNamespace(url=url))
        for url, sockets in self.world.popups:
            popup = FakePage(self.world, self.context)
            popup.url = url
            popup.late_loaded = True
            self.context.emit_page(popup)
            for socket_url in sockets:
                for fn in popup.socket_listeners:
                    fn(types.SimpleNamespace(url=socket_url))

    async def close(self) -> None:
        self.world.closed_popups.append(self.url)

    async def goto(self, url: str, **kw):
        self.world.gotos.append((url, kw))
        if self.world.goto_delay:
            await asyncio.sleep(self.world.goto_delay)
        if url == "about:blank":
            self.url = url
            return FakeResponse(200)
        chain = self.world.chains.get(url, [url])
        # Playwright routes only the first URL of a redirect chain; every hop
        # still raises a request event.
        route = await self.context.through_route(chain[0], nav=True, main=True)
        if route is not None and route.outcome == "abort":
            raise RuntimeError(f"net::ERR_FAILED at {chain[0]}\nCall log: ...")
        for hop in chain[1:]:
            self.context.emit(FakeRequest(hop, nav=True, main=True))
        for sub in self.world.subresources:
            r = await self.context.through_route(sub, nav=False, main=False)
            self.world.sub_outcomes[sub] = r.outcome if r else "none"
            if r is not None and r.outcome == "continue":
                for hop in self.world.sub_hops.get(sub, []):
                    self.context.emit(FakeRequest(hop, nav=False, main=False))
                for hop in self.world.silent_hops.get(sub, []):
                    self.context.emit(FakeRequest(hop, nav=False, main=False),
                                      respond=False)
        for ws_url in self.world.websockets:
            for _pattern, handler in self.context.ws_routes:
                await handler(FakeWebSocket(self.world, ws_url))
        self.url = chain[-1]
        for fn in self.frame_listeners:
            fn(FakeFrame(self.url))
        return FakeResponse(self.world.status)

    async def title(self) -> str:
        return self.world.title

    async def inner_text(self, selector: str) -> str:
        self._during_capture()
        return self.world.text

    async def screenshot(self, **kw) -> bytes:
        self._during_capture()
        return b"\x89PNG fake"

    async def query_selector(self, selector: str):
        if selector == "input[type=password]":
            return FakeElement(self, "pw") if "password" in \
                self.world.selectors.values() else None
        if selector in self.world.selectors:
            return FakeElement(self, selector)
        return None

    async def wait_for_load_state(self, *a, **kw) -> None:
        return None


class FakeContext:
    def __init__(self, world: FakeWorld, kwargs: dict) -> None:
        self.world, self.kwargs = world, kwargs
        self.routes: list = []
        self.ws_routes: list = []
        self.listeners: list = []
        self.response_listeners: list = []
        self.page_listeners: list = []
        self.closed = False

    def set_default_timeout(self, ms: float) -> None:
        self.default_timeout = ms

    def on(self, event: str, fn) -> None:
        # Real Playwright tags every listener with an attribute; a bound
        # method of a slotted object refuses it (found live, not in a fake).
        fn._pw_impl_instance_seen = True
        {"request": self.listeners, "response": self.response_listeners,
         "page": self.page_listeners}[event].append(fn)

    def emit_page(self, page: FakePage) -> None:
        for fn in self.page_listeners:
            fn(page)

    def emit(self, request: FakeRequest, *, respond: bool = True) -> None:
        for fn in self.listeners:
            fn(request)
        if respond:
            for fn in self.response_listeners:
                fn(FakeResponseEvent(request.url))

    async def through_route(self, url, *, nav, main, method="GET"):
        request = FakeRequest(url, nav=nav, main=main, method=method)
        for fn in self.listeners:
            fn(request)
        if not self.routes:
            return None
        route = FakeRoute(request)
        await self.routes[0][1](route)
        if route.outcome == "continue":
            for fn in self.response_listeners:
                fn(FakeResponseEvent(url))
        return route

    async def route_web_socket(self, pattern: str, handler) -> None:
        self.ws_routes.append((pattern, handler))

    async def route(self, pattern: str, handler) -> None:
        self.routes.append((pattern, handler))

    async def new_page(self) -> FakePage:
        page = FakePage(self.world, self)
        self.emit_page(page)
        return page

    async def close(self) -> None:
        self.closed = True


class FakeBrowser:
    def __init__(self, world: FakeWorld) -> None:
        self.world = world

    async def new_context(self, **kwargs) -> FakeContext:
        ctx = FakeContext(self.world, kwargs)
        self.world.contexts.append(ctx)
        return ctx

    async def close(self) -> None:
        self.world.in_flight -= 1


class FakeChromium:
    def __init__(self, world: FakeWorld) -> None:
        self.world = world

    async def launch(self, **kwargs) -> FakeBrowser:
        if self.world.launch_error is not None:
            raise self.world.launch_error
        self.world.launches.append(kwargs)
        self.world.in_flight += 1
        self.world.max_in_flight = max(self.world.max_in_flight, self.world.in_flight)
        return FakeBrowser(self.world)


class _Manager:
    def __init__(self, world: FakeWorld) -> None:
        self.world = world

    async def __aenter__(self):
        return types.SimpleNamespace(chromium=FakeChromium(self.world))

    async def __aexit__(self, *exc) -> None:
        return None


@pytest.fixture
def world(monkeypatch, tmp_path):
    w = FakeWorld()
    fake = types.ModuleType("playwright.async_api")
    fake.async_playwright = lambda: _Manager(w)
    monkeypatch.setitem(sys.modules, "playwright.async_api", fake)
    monkeypatch.setenv("BROWSER_DATA_DIR", str(tmp_path / "browser"))
    monkeypatch.delenv("BROWSER_DENY", raising=False)
    return w


@pytest.fixture
def rows(monkeypatch):
    """browser_sessions writes, recorded rather than sent to a database."""
    log: list[tuple[str, tuple]] = []

    async def fake_fetch_one(sql, *args):
        log.append((" ".join(sql.split()), args))
        return {"id": 41 + len(log)}

    async def fake_execute(sql, *args):
        log.append((" ".join(sql.split()), args))
        return "UPDATE 1"

    monkeypatch.setattr(session, "fetch_one", fake_fetch_one)
    monkeypatch.setattr(session, "execute_sql", fake_execute)
    return log


@pytest.fixture
def no_playwright(monkeypatch):
    monkeypatch.setitem(sys.modules, "playwright.async_api", None)


def _files(tmp_path) -> list[str]:
    root = tmp_path / "browser"
    return sorted(p.name for p in root.iterdir()) if root.exists() else []


# ------------------------------------------------------------------- policy

def test_deny_defaults_are_the_extensions_list_plus_auth_and_payment_hosts():
    assert policy.DENY_DEFAULT == (
        "bank", "chase.com", "paypal.com", "health", "nhs.uk", "pornhub.com",
        "xvideos.com", "mail.google.com", "web.whatsapp.com", "instagram.com/direct",
        "accounts.google.com", "login", "signin", "checkout", "cart")


def test_the_extension_list_is_mirrored_exactly():
    source = (ROOT / "extension" / "config.js").read_text()
    block = re.search(r"denylist:\s*\[(.*?)\]", source, re.DOTALL).group(1)
    entries = set(re.findall(r'"([^"]+)"', block))
    assert entries and entries <= set(policy.DENY_DEFAULT)


@pytest.mark.parametrize("url", [
    "https://chase.com/",
    "https://secure.chase.com/accounts",
    "https://www.mybank.co.uk/",                     # category word, host label
    "https://www.foodbank.org/",                     # the extension's rule, as is
    "https://stmaryshealth.org/",
    "https://www.paypal.com/myaccount",
    "https://www.pornhub.com/",
    "https://xvideos.com/",
    "https://mail.google.com/mail/u/0/",
    "https://www.instagram.com/direct/inbox/",
    "https://accounts.google.com/ServiceLogin",
    "https://login.example.com/",                    # auth word, whole label
    "https://checkout.shop.example/",
    "https://cart.example.com/",
    "https://www.nhs.uk/conditions/",
    "https://web.whatsapp.com/",
])
def test_denied_by_host(monkeypatch, url):
    monkeypatch.delenv("BROWSER_DENY", raising=False)
    ok, why = policy.allowed(url)
    assert ok is False and why


@pytest.mark.parametrize("url", [
    "https://www.joespizza.co.uk/menu",
    "https://www.instagram.com/joespizza/",
    "http://example.com/",
    "https://www.google.com/maps/place/Joe's",
    "https://carterplumbing.com/",
    "https://www.mccartneys.co.uk/",
    "https://example.com/cartoons",
    "https://example.com/blogin",
    "https://example.com/checkout-guide",
    "https://example.com/health-library",
    "https://example.com/locations/health-center",
    "https://example.com/login",                     # paths are not matched
    "https://example.com/search?keywords=best pizza in ancoats tonight",
    "https://example.com/offer?code=SPRING10",
    "https://example.com/search?keyword=plumbersleedscentral",
    "https://example.com/list?keywords=plumbers-leeds-central-2026",
    "https://example.com/blog/how-to-choose-a-plumber-in-leeds-city-centre-2026",
    "https://example.com/p/0123456789abcdef0123456789abcdef",   # no word beside it
    "https://example.com/verify/how-to-verify-your-business-2026",  # a slug
    "https://example.com/auth/getting-started-with-oauth",
])
def test_ordinary_pages_are_allowed(monkeypatch, url):
    monkeypatch.delenv("BROWSER_DENY", raising=False)
    assert policy.allowed(url) == (True, "")


@pytest.mark.parametrize("url", [
    "https://app.example.com/auth/magic?token=Zx8rT0pQv2LmN4kW9sYb",
    "https://example.com/reset?reset_key=a1b2c3d4e5f6a7b8c9d0",
    "https://example.com/oauth/callback?code=4/0AY0e-g7abcdefghijklmn&state=x",
    "https://example.com/confirm?sig=deadbeefdeadbeefdeadbeef",
    "https://example.com/x?session_id=0123456789abcdef0123",
    "https://example.com/x?api_key=0123456789abcdef0123",
    "https://example.com/x?access_token=0123456789abcdef0123",
    "https://example.com/accounts/reset/MQ/c5x2a8f9b1d3e4f5a6b7c8d9e0f1a2b3c4/",
    "https://example.com/auth/magic/eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxIn0.abcdefghij",
    "https://example.com/verify/0123456789abcdef0123456789abcdef",
    "https://example.com/accounts/reset/MQ/c5x2a8f9b1d3e4f5a6b7c8/",   # 22 chars
    "https://example.com/confirm/a1b2c3d4e5f6g7h8",                     # 16 chars
    "https://example.com/reset/550e8400-e29b-41d4-a716-446655440000",   # a UUID
    "https://example.com/reset/xkqz-mvtr-8h2k-p3nd",                 # a code, no words
    ("https://bucket.s3.amazonaws.com/invoice.pdf?X-Amz-Algorithm=AWS4-HMAC-SHA256"
     "&X-Amz-Credential=AKIAIOSFODNN7EXAMPLE%2F20260924%2Fus-east-1%2Fs3%2Faws4_request"
     "&X-Amz-Date=20260924T120000Z&X-Amz-Expires=3600&X-Amz-SignedHeaders=host"
     "&X-Amz-Signature=fe5f80f77d5fa3beca038a248ff027d0445342fe2855ddc963176630326f1024"),
    ("https://storage.googleapis.com/b/o.pdf?X-Goog-Algorithm=GOOG4-RSA-SHA256"
     "&X-Goog-Signature=0123456789abcdef0123456789abcdef"),
])
def test_a_url_that_carries_a_credential_is_refused(url):
    """Opening a magic link, a reset link or an OAuth callback IS signing in."""
    assert policy.allowed(url) == (False, "URL carries a credential")


def test_without_query_strips_query_fragment_and_userinfo():
    assert policy.without_query(
        "https://u:p@example.com/a/b?token=secret#frag") == "https://example.com/a/b"


@pytest.mark.parametrize("url", [
    "https://www.google.com/search?q=pizza+manchester",
    "https://google.co.uk/search?q=x",
])
def test_googles_results_page_is_denied(url):
    ok, why = policy.allowed(url)
    assert ok is False and "Google" in why


@pytest.mark.parametrize("url", [
    "file:///etc/passwd", "javascript:alert(1)", "ftp://example.com/x",
    "not a url", "",
])
def test_anything_but_http_is_denied(url):
    assert policy.allowed(url)[0] is False


@pytest.mark.parametrize("url", [
    "http://localhost:8080/api/health",
    "http://127.0.0.1/", "http://10.0.0.5/", "http://192.168.1.1/",
    "http://100.64.1.2:8080/", "http://169.254.169.254/latest/meta-data/",
    "http://[::1]/", "http://atlas.local/",
    "http://0x7f000001/", "http://2130706433/", "http://0/", "http://127.1/",
    "http://0177.0.0.1/",
])
def test_literal_local_and_private_addresses_are_denied(url):
    ok, why = policy.allowed(url)
    assert ok is False and "public web" in why


def test_browser_deny_extends_the_defaults(monkeypatch):
    monkeypatch.setenv("BROWSER_DENY", "example.org, facebook ,")
    assert policy.allowed("https://example.org/")[0] is False
    assert policy.allowed("https://sub.example.org/x")[0] is False
    assert policy.allowed("https://www.facebook.com/joes")[0] is False
    assert policy.allowed("https://chase.com/")[0] is False       # still denied
    assert policy.allowed("https://joespizza.co.uk/")[0] is True


def test_browser_deny_is_read_on_every_call(monkeypatch):
    monkeypatch.delenv("BROWSER_DENY", raising=False)
    assert policy.allowed("https://example.org/")[0] is True
    monkeypatch.setenv("BROWSER_DENY", "example.org")
    assert policy.allowed("https://example.org/")[0] is False


def test_host_allowed_checks_the_host_only(monkeypatch):
    monkeypatch.delenv("BROWSER_DENY", raising=False)
    assert policy.host_allowed("https://cdn.example.com/login.css") is True
    assert policy.host_allowed("https://maps.example.com/embed?key=AIzaSyAbcdefghij1234") \
        is True
    assert policy.host_allowed("https://tracker.chase.com/pixel.gif") is False
    assert policy.host_allowed("http://127.0.0.1/x.js") is False


# ---------------------------------------------------------------- netguard

@pytest.mark.parametrize("url", [
    "http://localtest.me:18440/api/memory",
    "http://127.0.0.1.nip.io:18440/api/memory",
    "http://169.254.169.254.nip.io/latest/meta-data/",
    "http://mixed.example/",                         # ANY private address refuses
    "http://mesh.example/",
    "http://ula.example/",
    "http://nxdomain.example/",
    "http://127.0.0.1:8080/", "http://2130706433/", "http://0x7f.1/",
    "http://[fd00:ec2::254]/", "http://[::ffff:127.0.0.1]/", "http://224.0.0.1/",
    "http://[fe80::1]/", "http://0.0.0.0/",
])
async def test_assert_public_refuses_anything_not_on_the_public_web(url):
    assert await _netguard.assert_public(url) is not None


async def test_assert_public_passes_a_public_name(resolver):
    assert await _netguard.assert_public("https://carterplumbing.com/") is None
    assert await _netguard.assert_public("https://8.8.8.8/") is None


async def test_a_literal_ip_is_checked_without_dns(resolver):
    await _netguard.assert_public("http://127.0.0.1/")
    await _netguard.assert_public("http://2130706433/")
    await _netguard.assert_public("http://8.8.8.8/")
    assert resolver == []


async def test_resolutions_are_cached_for_a_minute(resolver, monkeypatch):
    await _netguard.assert_public("https://joespizza.co.uk/a")
    await _netguard.assert_public("https://joespizza.co.uk/b")
    assert resolver == ["joespizza.co.uk"]
    real = time.monotonic()
    monkeypatch.setattr(_netguard.time, "monotonic", lambda: real + 61)
    await _netguard.assert_public("https://joespizza.co.uk/c")
    assert resolver == ["joespizza.co.uk", "joespizza.co.uk"]


async def test_the_resolution_cache_is_bounded(monkeypatch):
    monkeypatch.setattr(_netguard, "CACHE_SIZE", 3)
    for i in range(5):
        await _netguard.assert_public(f"https://h{i}.example/")
    assert list(_netguard._CACHE) == ["h2.example", "h3.example", "h4.example"]
    await _netguard.assert_public("https://h2.example/")        # used: kept
    await _netguard.assert_public("https://h5.example/")
    assert list(_netguard._CACHE) == ["h4.example", "h2.example", "h5.example"]


class _Resp:
    def __init__(self, status=200, location=None, text="ok"):
        self.status_code, self.text = status, text
        self.headers = {"location": location} if location else {}

    def raise_for_status(self):
        return None


class _Client:
    def __init__(self, answers):
        self.answers, self.asked = answers, []

    async def get(self, url, headers=None):
        self.asked.append(url)
        return self.answers[url]


async def test_get_public_checks_every_redirect_hop():
    client = _Client({"https://evil.example/r": _Resp(302, "http://localtest.me:8080/api")})
    response, reason = await _netguard.get_public(client, "https://evil.example/r")
    assert response is None and "localtest.me" in reason
    assert client.asked == ["https://evil.example/r"]


async def test_get_public_follows_a_public_redirect():
    client = _Client({"https://a.example/": _Resp(301, "/b"),
                      "https://a.example/b": _Resp(200, text="here")})
    response, reason = await _netguard.get_public(client, "https://a.example/")
    assert reason is None and response.text == "here"


async def test_fetch_text_refuses_the_local_api(monkeypatch):
    from atlas.modules import _web

    class C:
        def __init__(self, *a, **k): ...
        async def __aenter__(self): return self
        async def __aexit__(self, *a): ...
        async def get(self, url, headers=None):
            raise AssertionError(f"fetched {url}")

    monkeypatch.setattr(_web.httpx, "AsyncClient", C)
    for url in ("http://127.0.0.1:8080/api/memory", "http://localtest.me:8080/api/memory"):
        text, reason = await _web.fetch_text(url)
        assert text is None and "public web" in reason


async def test_the_web_fetch_tool_refuses_the_local_api(monkeypatch):
    from atlas.tools import builtin

    class C:
        def __init__(self, *a, **k): ...
        async def __aenter__(self): return self
        async def __aexit__(self, *a): ...
        async def get(self, url, headers=None):
            if url == "https://evil.example/":
                return _Resp(302, "http://127.0.0.1.nip.io:8080/api/memory")
            raise AssertionError(f"fetched {url}")

    monkeypatch.setattr(builtin.httpx, "AsyncClient", C)
    out = await builtin.web_fetch("https://evil.example/")
    assert "error" in out and "<untrusted_data" in out["error"]


# ------------------------------------------------------------------ open()

async def test_open_without_playwright_says_how_to_install(no_playwright, rows):
    assert await session.open("https://joespizza.co.uk/") == (None, MISSING)


async def test_open_refuses_a_denied_url_before_launching(world, rows):
    page, reason = await session.open("https://login.example.com/", agent="concierge")
    assert page is None and "login" in reason
    assert world.launches == []
    inserts = [a for s, a in rows if s.startswith("INSERT INTO browser_sessions")]
    assert inserts and inserts[0][0] == "https://login.example.com/"


@pytest.mark.parametrize("url", ["http://localtest.me:18440/api/memory",
                                 "http://127.0.0.1.nip.io:18440/api/memory"])
async def test_open_refuses_a_name_that_resolves_locally(world, rows, url):
    page, reason = await session.open(url)
    assert page is None and "public web" in reason
    assert world.launches == []


async def test_open_refuses_a_magic_link(world, rows):
    page, reason = await session.open(
        "https://app.example.com/auth/magic?token=Zx8rT0pQv2LmN4kW9sYb")
    assert page is None and reason == "URL carries a credential"
    assert world.launches == []
    stored = [a[0] for s, a in rows if s.startswith("INSERT")]
    assert stored == ["https://app.example.com/auth/magic"]


async def test_carter_plumbing_is_opened(world, rows):
    page, reason = await session.open("https://carterplumbing.com/")
    assert reason is None and page["status"] == 200


async def test_open_reads_a_page_wraps_it_and_keeps_a_record(world, rows, tmp_path):
    page, reason = await session.open("https://joespizza.co.uk/?utm_source=x",
                                      agent="concierge")
    assert reason is None
    assert page["status"] == 200
    assert "<untrusted_data source='https://joespizza.co.uk/'>" in page["text"]
    assert "Open 11-22" in page["text"]
    assert "<untrusted_data" in page["title"] and "Joe's Pizza" in page["title"]

    shot = pathlib.Path(page["shot_path"])
    assert shot.parent == tmp_path / "browser" and shot.read_bytes().startswith(b"\x89PNG")
    assert shot.name == "browser-42.png"
    assert shot.with_suffix(".txt").read_text() == world.text

    sql = [s for s, _ in rows]
    assert sql[0].startswith("INSERT INTO browser_sessions") and "RETURNING id" in sql[0]
    assert rows[0][1][0] == "https://joespizza.co.uk/"          # no query stored
    assert rows[0][1][4] == "concierge"
    assert any(s.startswith("UPDATE browser_sessions") for s in sql)


async def test_every_open_gets_a_fresh_context_with_nothing_in_it(world, rows):
    await session.open("https://joespizza.co.uk/")
    await session.open("https://joespizza.co.uk/menu")
    assert len(world.launches) == 2 and len(world.contexts) == 2
    for launch in world.launches:
        assert launch.get("headless", True) is True
    for ctx in world.contexts:
        assert ctx.kwargs.get("viewport") == {"width": 1280, "height": 800}
        assert ctx.kwargs.get("service_workers") == "block"
        assert "storage_state" not in ctx.kwargs
        assert "http_credentials" not in ctx.kwargs
        assert ctx.routes and ctx.listeners, "no request guard installed"
    assert world.gotos[0][1].get("timeout") == 20_000


async def test_one_browser_at_a_time(world, rows):
    world.goto_delay = 0.02
    results = await asyncio.gather(*(session.open(f"https://joespizza.co.uk/{i}")
                                     for i in range(4)))
    assert all(reason is None for _page, reason in results)
    assert world.max_in_flight == 1 and len(world.launches) == 4


async def test_a_call_that_only_waited_is_told_the_browser_is_busy(world, rows,
                                                                   monkeypatch):
    monkeypatch.setattr(session, "OPEN_WALL_S", 0.05)
    async with session.lock():                      # another page, in use
        page, reason = await session.open("https://joespizza.co.uk/b")
    assert page is None and reason == session.BUSY
    assert world.launches == []


async def test_requests_are_checked_and_denied_ones_aborted(world, rows):
    world.subresources = [
        "https://tracker.chase.com/p.gif",           # denied host
        "https://cdn.example.com/app.js",
        "https://cdn.example.com/login.css",         # a path, not a login page
        "http://localtest.me:8080/api/memory",       # resolves to loopback
        "https://mixed.example/x.js",
    ]
    _page, reason = await session.open("https://joespizza.co.uk/")
    assert reason is None
    assert world.sub_outcomes == {
        "https://tracker.chase.com/p.gif": "abort",
        "https://cdn.example.com/app.js": "continue",
        "https://cdn.example.com/login.css": "continue",
        "http://localtest.me:8080/api/memory": "abort",
        "https://mixed.example/x.js": "abort",
    }


async def test_a_redirect_to_a_sign_in_page_is_refused_not_read(world, rows, tmp_path):
    world.chains["https://portal.example.com/"] = [
        "https://portal.example.com/",
        "https://accounts.google.com/ServiceLogin?continue=x"]
    page, reason = await session.open("https://portal.example.com/")
    assert page is None
    assert "sign" in reason.lower() and "accounts.google.com" in reason
    assert "continue=x" not in reason
    assert _files(tmp_path) == []


async def test_a_302_through_a_denied_hop_is_refused(world, rows, tmp_path):
    """The hop never reaches the route guard; it is caught after load."""
    world.chains["https://joespizza.co.uk/go"] = [
        "https://joespizza.co.uk/go", "http://localtest.me:8080/api/memory",
        "https://joespizza.co.uk/landed"]
    page, reason = await session.open("https://joespizza.co.uk/go")
    assert page is None and "localtest.me" in reason
    assert _files(tmp_path) == []


async def test_a_frame_on_a_denied_host_is_refused_with_no_screenshot(world, rows,
                                                                      tmp_path):
    world.frames = ["https://secure.chase.com/widget"]
    page, reason = await session.open("https://joespizza.co.uk/")
    assert page is None and "chase.com" in reason and "part of the page" in reason
    assert _files(tmp_path) == []


async def test_a_frame_that_resolves_locally_is_refused(world, rows, tmp_path):
    world.frames = ["http://127.0.0.1.nip.io:8080/api/memory"]
    page, reason = await session.open("https://joespizza.co.uk/")
    assert page is None and "127.0.0.1.nip.io" in reason and _files(tmp_path) == []


async def test_a_maps_embed_with_a_key_is_not_a_refusal(world, rows):
    world.frames = ["https://www.google.com/maps/embed/v1/place?key=AIzaSyAbcdefghij1234"]
    page, reason = await session.open("https://joespizza.co.uk/")
    assert reason is None and page["status"] == 200


async def test_a_late_redirect_during_capture_discards_the_capture(world, rows, tmp_path):
    """R1: a timer navigates through a redirect to a private address while
    the text and screenshot are being taken. Checked before the capture, it
    was missed; checked after, the capture is thrown away."""
    world.capture_hops = ["https://joespizza.co.uk/r", "http://localtest.me:8080/api/memory"]
    page, reason = await session.open("https://joespizza.co.uk/late")
    assert page is None and "localtest.me" in reason
    assert _files(tmp_path) == []


async def test_a_frame_that_navigates_late_is_checked(world, rows, tmp_path):
    """R2: the frame's URL is '' at load and becomes a private hop while the
    screenshot is taken."""
    world.late_frames = ["http://127.0.0.1.nip.io:8080/secret"]
    page, reason = await session.open("https://joespizza.co.uk/iframe")
    assert page is None and "127.0.0.1.nip.io" in reason
    assert _files(tmp_path) == []


async def test_a_sub_resource_redirect_hop_is_checked(world, rows, tmp_path):
    """R3: fetch('/r') is same-origin and allowed; its hop to a private
    address is never routed. Recorded, it refuses the read."""
    world.subresources = ["https://joespizza.co.uk/r"]
    world.sub_hops = {"https://joespizza.co.uk/r": ["http://localtest.me:8080/api/memory"]}
    page, reason = await session.open("https://joespizza.co.uk/fetch")
    assert world.sub_outcomes["https://joespizza.co.uk/r"] == "continue"
    assert page is None and "localtest.me" in reason
    assert _files(tmp_path) == []


async def test_a_hop_to_an_already_aborted_url_is_still_checked(world, rows, tmp_path):
    """S1: the page asks for the private URL directly (aborted), then reaches
    the same URL through a redirect hop, which is a different request and is
    never routed. Aborts are judged per request, not per URL."""
    private = "http://localtest.me:8080/api/memory"
    world.subresources = [private, "https://joespizza.co.uk/r"]
    world.sub_hops = {"https://joespizza.co.uk/r": [private]}
    page, reason = await session.open("https://joespizza.co.uk/collide")
    assert world.sub_outcomes[private] == "abort"
    assert page is None and "localtest.me" in reason
    assert _files(tmp_path) == []


async def test_a_frame_on_an_already_aborted_url_is_still_checked(world, rows, tmp_path):
    """S1, frame variant: the image to the private URL was aborted; the frame
    that reached it through /r was not."""
    private = "http://127.0.0.1.nip.io:8080/secret"
    world.subresources = [private]
    world.late_frames = [private]
    page, reason = await session.open("https://joespizza.co.uk/collide-iframe")
    assert world.sub_outcomes[private] == "abort"
    assert page is None and "127.0.0.1.nip.io" in reason
    assert _files(tmp_path) == []


async def test_s1_the_requests_loop_alone_judges_aborts_per_request(world, rows,
                                                                  tmp_path):
    """Only the requests loop can catch this: the hop to the already-aborted
    URL has a request event and nothing else — no response, no frame. A
    regression to URL-based skipping in that loop fails here."""
    private = "http://localtest.me:8080/api/memory"
    world.subresources = [private, "https://joespizza.co.uk/r"]
    world.silent_hops = {"https://joespizza.co.uk/r": [private]}
    page, reason = await session.open("https://joespizza.co.uk/collide")
    assert page is None and "localtest.me" in reason
    assert _files(tmp_path) == []


async def test_s1_the_frames_loop_never_skips_an_aborted_url(world, rows, tmp_path):
    """Only the second loop can catch this: a frame navigates to the URL the
    guard aborted as an image, with no request event of its own. A
    regression to URL-based skipping in that loop fails here."""
    private = "http://127.0.0.1.nip.io:8080/secret"
    world.subresources = [private]
    world.bare_frame_navs = [private]
    page, reason = await session.open("https://joespizza.co.uk/collide-frame")
    assert world.sub_outcomes[private] == "abort"
    assert page is None and "127.0.0.1.nip.io" in reason
    assert _files(tmp_path) == []


async def test_a_workers_websocket_is_checked_after_capture(world, rows, tmp_path):
    """T2: a dedicated Worker's socket skips route_web_socket; the page's
    websocket event still records it, and a private one refuses the read."""
    world.worker_sockets = ["ws://127.0.0.1.nip.io:8080/ws-from-worker"]
    page, reason = await session.open("https://joespizza.co.uk/worker-ws")
    assert page is None and "127.0.0.1.nip.io" in reason
    assert _files(tmp_path) == []


async def test_a_public_workers_websocket_is_fine(world, rows):
    world.worker_sockets = ["wss://chat.joespizza.co.uk/ws"]
    page, reason = await session.open("https://joespizza.co.uk/")
    assert reason is None and page["status"] == 200


async def test_a_popups_worker_socket_refuses_the_read(world, rows, tmp_path):
    """U1: a window the page opened runs a worker whose WebSocket reaches a
    private address. The window is closed and the read refused."""
    world.popups = [("https://joespizza.co.uk/popws-inner",
                     ["ws://127.0.0.1.nip.io:8080/ws-from-popup-worker"])]
    page, reason = await session.open("https://joespizza.co.uk/open-ws")
    assert page is None and "page opened a new window" in reason
    assert world.closed_popups == ["https://joespizza.co.uk/popws-inner"]
    assert _files(tmp_path) == []


async def test_any_window_the_page_opens_refuses_the_read(world, rows, tmp_path):
    """A read-only browser has no use for windows the page opens, even a
    blank, harmless-looking one."""
    world.popups = [("about:blank", [])]
    page, reason = await session.open("https://joespizza.co.uk/")
    assert page is None and "page opened a new window" in reason
    assert world.closed_popups == ["about:blank"]
    assert _files(tmp_path) == []


async def test_every_page_gets_the_frame_and_socket_listeners(world, rows):
    """Registered on the context before the first page opens, so recording
    never depends on which page fired."""
    await session.open("https://joespizza.co.uk/")
    ctx = world.contexts[0]
    assert len(ctx.page_listeners) == 1


async def test_a_submit_that_opens_a_window_keeps_no_capture(world, rows):
    world.selectors = {"#name": "text"}
    world.popups = [("https://joespizza.co.uk/thanks", [])]
    result = await session.submit_form("https://joespizza.co.uk/contact", "#name=M")
    assert result.ok is True                          # it was sent; that stands
    assert "shot_path" not in result.evidence
    assert "new window" in result.evidence["after_refused"]


async def test_shared_workers_are_disabled_at_launch(world, rows):
    """T1: Playwright neither routes nor reports a SharedWorker's requests,
    so they are switched off in Chromium itself, for every launch."""
    await session.open("https://joespizza.co.uk/")
    await session.check_engine()
    assert len(world.launches) == 2
    for launch in world.launches:
        assert "--disable-features=SharedWorker" in launch.get("args", [])


async def test_an_aborted_request_does_not_refuse_the_page(world, rows):
    """What the guard stopped never loaded: a blocked tracker is not a reason
    to refuse the page."""
    world.subresources = ["https://tracker.chase.com/p.gif"]
    page, reason = await session.open("https://joespizza.co.uk/")
    assert reason is None and page["status"] == 200


async def test_websockets_are_guarded(world, rows):
    """R4: a WebSocket never passes context.route and CORS does not bind it."""
    world.websockets = ["ws://127.0.0.1.nip.io:8080/ws", "wss://secure.chase.com/ws",
                        "wss://chat.joespizza.co.uk/ws"]
    page, reason = await session.open("https://joespizza.co.uk/")
    assert reason is None and page["status"] == 200
    assert world.ws_outcomes == {"ws://127.0.0.1.nip.io:8080/ws": "closed",
                                 "wss://secure.chase.com/ws": "closed",
                                 "wss://chat.joespizza.co.uk/ws": "connected"}


async def test_a_late_hop_after_submit_discards_the_capture(world, rows):
    world.selectors = {"#name": "text"}
    world.capture_hops = ["http://localtest.me:8080/api/memory"]
    result = await session.submit_form("https://joespizza.co.uk/contact", "#name=M")
    assert result.ok is True                         # it was sent; that stands
    assert "shot_path" not in result.evidence
    assert "localtest.me" in result.evidence["after_refused"]


async def test_a_page_with_a_password_box_says_it_cannot_read_past_it(world, rows):
    world.selectors["#pw"] = "password"
    page, reason = await session.open("https://joespizza.co.uk/")
    assert reason is None
    assert "never signs in" in page["note"]


async def test_page_text_is_trimmed_to_20k(world, rows):
    world.text = "x" * 50_000
    page, _ = await session.open("https://joespizza.co.uk/")
    assert pathlib.Path(page["shot_path"]).with_suffix(".txt").read_text() == "x" * 20_000


async def test_a_page_that_fails_to_load_is_a_reason_not_an_exception(world, rows):
    async def boom(self, url, **kw):
        raise RuntimeError("net::ERR_NAME_NOT_RESOLVED at https://nope.example/")

    original, FakePage.goto = FakePage.goto, boom
    try:
        page, reason = await session.open("https://nope.example/")
    finally:
        FakePage.goto = original
    assert page is None and "ERR_NAME_NOT_RESOLVED" in reason


async def test_a_page_that_hangs_is_a_reason_not_a_stall(world, rows, monkeypatch):
    world.goto_delay = 0.5
    monkeypatch.setattr(session, "OPEN_WALL_S", 0.05)
    page, reason = await session.open("https://joespizza.co.uk/")
    assert page is None and "within" in reason


# ------------------------------------------------------------------ search

async def test_search_uses_brave_when_there_is_a_key(monkeypatch):
    monkeypatch.setenv("BRAVE_API_KEY", "k")
    seen = {}

    async def fake_search(query, limit=5):
        seen["q"] = query
        return [{"title": "Joe's Pizza", "url": "https://joespizza.co.uk/",
                 "snippet": "Best pizza in Ancoats"}], None

    async def no_ddg(query):
        raise AssertionError("DuckDuckGo must not be asked when Brave is configured")

    monkeypatch.setattr(session._web, "search", fake_search)
    monkeypatch.setattr(session, "_duckduckgo_html", no_ddg)
    hits, reason = await session.search("pizza ancoats")
    assert reason is None and seen["q"] == "pizza ancoats"
    assert hits[0]["url"] == "https://joespizza.co.uk/"
    assert hits[0]["engine"] == "brave"
    assert "<untrusted_data" in hits[0]["text"] and "Best pizza" in hits[0]["text"]


DDG_HTML = """
<div class="result results_links_ad">
  <a rel="nofollow" class="result__a" href="https://duckduckgo.com/y.js?ad_domain=x&amp;u3=1">Sponsored</a>
</div>
<div class="result">
  <h2><a rel="nofollow" class="result__a"
     href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fwww.joespizza.co.uk%2F&amp;rut=abc">Joe&#x27;s <b>Pizza</b></a></h2>
  <a class="result__snippet" href="//duckduckgo.com/l/?uddg=x">Wood-fired pizza in <b>Ancoats</b></a>
</div>
<div class="result">
  <a class="result__a" href="https://www.yelp.co.uk/biz/joes-pizza">Joe's Pizza - Yelp</a>
  <a class="result__snippet" href="#">Reviews</a>
</div>
<div class="result">
  <a class="result__a" href="https://www.chase.com/offers">Chase</a>
</div>
<div class="result">
  <a class="result__a" href="http://localtest.me:8080/api/memory">Local</a>
</div>
"""


async def test_search_falls_back_to_duckduckgo_and_unwraps_its_links(monkeypatch):
    monkeypatch.delenv("BRAVE_API_KEY", raising=False)
    monkeypatch.delenv("BROWSER_DENY", raising=False)

    async def fake_html(query):
        assert query == "joes pizza ancoats"
        return DDG_HTML, None

    monkeypatch.setattr(session, "_duckduckgo_html", fake_html)
    hits, reason = await session.search("joes pizza ancoats", limit=5)
    assert reason is None
    urls = [h["url"] for h in hits]
    assert urls == ["https://www.joespizza.co.uk/",
                    "https://www.yelp.co.uk/biz/joes-pizza",
                    "https://www.chase.com/offers",
                    "http://localtest.me:8080/api/memory"]
    assert all(h["engine"] == "duckduckgo" for h in hits)
    assert "Joe's Pizza" in hits[0]["text"] and "Ancoats" in hits[0]["text"]
    assert "<b>" not in hits[0]["text"]
    assert [h["openable"] for h in hits] == [True, True, False, False]
    assert "public web" in hits[3]["not_opened"]


async def test_search_respects_the_limit(monkeypatch):
    monkeypatch.delenv("BRAVE_API_KEY", raising=False)

    async def fake_html(query):
        return DDG_HTML, None

    monkeypatch.setattr(session, "_duckduckgo_html", fake_html)
    hits, _ = await session.search("q", limit=1)
    assert len(hits) == 1


async def test_a_search_that_cannot_reach_its_engine_says_so(monkeypatch):
    monkeypatch.delenv("BRAVE_API_KEY", raising=False)

    async def down(query):
        return None, "timed out"

    monkeypatch.setattr(session, "_duckduckgo_html", down)
    assert await session.search("q") == ([], "timed out")


# ----------------------------------------------------------- find_business

async def test_find_business_skips_directories_and_opens_the_real_site(monkeypatch):
    async def fake_search(query, limit=5):
        assert query == "Joe's Pizza Ancoats"
        return [
            {"url": "https://www.yelp.co.uk/biz/joes", "openable": True},
            {"url": "https://www.facebook.com/joespizza", "openable": True},
            {"url": "https://www.google.com/maps/place/joes", "openable": True},
            {"url": "https://www.tripadvisor.co.uk/x", "openable": True},
            {"url": "https://www.joespizza.co.uk/", "openable": True},
            {"url": "https://www.other.co.uk/", "openable": True},
        ], None

    opened = []

    async def fake_open(url, *, agent=None):
        opened.append((url, agent))
        return {"title": "t", "text": "x", "shot_path": "p", "status": 200}, None

    monkeypatch.setattr(session, "search", fake_search)
    monkeypatch.setattr(session, "open", fake_open)
    page, reason = await session.find_business("Joe's Pizza", "Ancoats", agent="agency")
    assert reason is None
    assert opened == [("https://www.joespizza.co.uk/", "agency")]
    assert page["query"] == "Joe's Pizza Ancoats" and page["status"] == 200


async def test_find_business_never_skips_a_denied_hit_in_silence(monkeypatch):
    """The likely site is denied: nothing is opened, and the next site is not
    passed off as the business."""
    async def fake_search(query, limit=5):
        return [
            {"url": "https://www.yelp.com/biz/x", "openable": True},
            {"url": "https://www.stmaryshealth.org/", "openable": False,
             "not_opened": "denied by policy (health)"},
            {"url": "https://www.competitor.co.uk/", "openable": True},
        ], None

    async def never(url, *, agent=None):
        raise AssertionError("nothing should be opened")

    monkeypatch.setattr(session, "search", fake_search)
    monkeypatch.setattr(session, "open", never)
    result, reason = await session.find_business("St Mary's", "Leeds")
    assert reason is None
    assert result["denied"] == "denied: www.stmaryshealth.org"
    assert [h["url"] for h in result["hits"]] == ["https://www.competitor.co.uk/"]
    assert "title" not in result and "text" not in result


async def test_find_business_with_only_directories_says_so(monkeypatch):
    async def fake_search(query, limit=5):
        return [{"url": "https://www.yelp.com/biz/x", "openable": True}], None

    async def never(url, *, agent=None):
        raise AssertionError("nothing should be opened")

    monkeypatch.setattr(session, "search", fake_search)
    monkeypatch.setattr(session, "open", never)
    page, reason = await session.find_business("X", "Y")
    assert page is None and "X Y" in reason


async def test_find_business_passes_a_search_failure_through(monkeypatch):
    async def fake_search(query, limit=5):
        return [], "no network"

    monkeypatch.setattr(session, "search", fake_search)
    assert await session.find_business("X", "Y") == (None, "no network")


# -------------------------------------------------------------- submit_form

def _submit_action():
    actions = browser.MODULE.actions()
    assert [a.name for a in actions] == ["browser.submit_form"], (
        "submit_form is the only write this module has")
    return actions[0]


def test_submit_form_is_the_only_write_and_it_is_gated():
    action = _submit_action()
    assert action.tier is Tier.ASK
    assert action.irreversible is True
    assert action.rollback is None
    assert action.dry_run is not None
    assert set(action.params) == {"url", "fields"}
    assert denial_reason(action.name) is None


def test_fields_parse_around_brackets_and_equals():
    pairs, reason = session.parse_fields(
        "input[name=email]=joe@example.com; #msg=Hello=there;textarea[name='a;b']=x")
    assert reason is None
    assert pairs == [("input[name=email]", "joe@example.com"),
                     ("#msg", "Hello=there"),
                     ("textarea[name='a;b']", "x")]


@pytest.mark.parametrize("bad", ["", "   ", "#name", "=value", ";;"])
def test_fields_that_name_nothing_are_refused(bad):
    pairs, reason = session.parse_fields(bad)
    assert pairs == [] and reason


async def test_dry_run_reports_which_selectors_exist_and_touches_nothing(world, rows):
    world.selectors = {"#name": "text", "#email": "email"}
    result = await session.submit_form_dry(
        "https://joespizza.co.uk/contact", "#name=Miro;#email=m@x.com;#phone=1")
    assert result.ok is False                       # one selector is missing
    assert "#phone" in result.detail and "#name" in result.detail
    assert result.evidence["found"] == ["#name", "#email"]
    assert result.evidence["missing"] == ["#phone"]
    assert world.filled == [] and world.submitted == 0


async def test_dry_run_shows_where_the_form_posts(world, rows):
    world.selectors = {"#name": "text"}
    result = await session.submit_form_dry("https://joespizza.co.uk/contact", "#name=M")
    assert result.ok is True and world.submitted == 0
    assert "POSTs to https://joespizza.co.uk/contact/send" in result.detail
    assert result.evidence["action"] == "https://joespizza.co.uk/contact/send"
    assert result.evidence["method"] == "post"


@pytest.mark.parametrize("action", ["https://www.chase.com/collect",
                                    "http://localtest.me:8080/api/request"])
async def test_a_form_that_posts_somewhere_denied_is_refused(world, rows, action):
    world.selectors = {"#name": "text"}
    world.form_action = action
    for fn in (session.submit_form_dry, session.submit_form):
        result = await fn("https://joespizza.co.uk/contact", "#name=M")
        assert result.ok is False and "not opened" in result.detail
    assert world.filled == [] and world.submitted == 0


async def test_submit_fills_and_submits(world, rows):
    world.selectors = {"#name": "text", "#msg": "textarea"}
    result = await session.submit_form(
        "https://joespizza.co.uk/contact", "#name=Miro;#msg=Table for 4?")
    assert result.ok is True, result.detail
    assert world.filled == [("#name", "Miro"), ("#msg", "Table for 4?")]
    assert world.submitted == 1
    assert pathlib.Path(result.evidence["shot_path"]).exists()


async def test_a_blocked_submission_is_a_failure(world, rows):
    """The page's script changed where the form posts; the guard stopped it,
    so the result must not say it was sent."""
    world.selectors = {"#name": "text"}
    world.post_to = "https://www.chase.com/collect"
    result = await session.submit_form("https://joespizza.co.uk/contact", "#name=M")
    assert result.ok is False and "blocked" in result.detail


async def test_submit_never_types_into_a_password_field(world, rows):
    world.selectors = {"#user": "text", "#pw": "password"}
    for fn in (session.submit_form, session.submit_form_dry):
        result = await fn("https://joespizza.co.uk/contact", "#user=m;#pw=hunter2")
        assert result.ok is False and "password" in result.detail
    assert world.filled == [] and world.submitted == 0


async def test_submit_never_submits_a_form_that_holds_a_password(world, rows):
    """#user=miro on a login form would sign in with an empty password."""
    world.selectors = {"#user": "text"}
    world.form_password = True
    for fn in (session.submit_form, session.submit_form_dry):
        result = await fn("https://joespizza.co.uk/signup", "#user=miro")
        assert result.ok is False and "password" in result.detail
    assert world.filled == [] and world.submitted == 0


async def test_submit_refuses_a_denied_url_without_launching(world, rows):
    result = await session.submit_form("https://checkout.shop.example/", "#a=1")
    assert result.ok is False and world.launches == []


async def test_submit_with_a_missing_selector_submits_nothing(world, rows):
    world.selectors = {"#name": "text"}
    result = await session.submit_form("https://joespizza.co.uk/c", "#name=M;#x=1")
    assert result.ok is False and world.filled == [] and world.submitted == 0


async def test_submit_without_a_form_reports_nothing_was_sent(world, rows):
    world.selectors = {"#name": "text"}
    world.form_action = None
    result = await session.submit_form("https://joespizza.co.uk/c", "#name=M")
    assert result.ok is False and "nothing was submitted" in result.detail
    assert world.submitted == 0


async def test_a_form_that_hangs_before_submitting_submitted_nothing(world, rows,
                                                                    monkeypatch):
    world.selectors = {"#name": "text"}
    world.goto_delay = 0.5
    monkeypatch.setattr(session, "FORM_WALL_S", 0.05)
    result = await session.submit_form("https://joespizza.co.uk/c", "#name=M")
    assert result.ok is False and "nothing was submitted" in result.detail


async def test_a_form_that_hangs_after_submitting_says_it_was_sent(world, rows,
                                                                   monkeypatch):
    world.selectors = {"#name": "text"}
    world.after_submit_delay = 0.5
    monkeypatch.setattr(session, "FORM_WALL_S", 0.1)
    result = await session.submit_form("https://joespizza.co.uk/c", "#name=M")
    assert result.ok is True and "submitted" in result.detail
    assert world.submitted == 1


async def test_submit_without_playwright_says_how_to_install(no_playwright):
    result = await session.submit_form("https://joespizza.co.uk/c", "#name=M")
    assert result.ok is False and result.detail == MISSING


# ------------------------------------------------------------ check_engine

async def test_engine_ok_when_chromium_loads_about_blank(world):
    obs = await session.check_engine()
    assert obs.health is Health.OK
    assert world.gotos[0][0] == "about:blank"


async def test_engine_unknown_without_playwright(no_playwright):
    obs = await session.check_engine()
    assert obs.health is Health.UNKNOWN and obs.detail == MISSING


async def test_engine_unknown_when_chromium_will_not_launch(world):
    world.launch_error = RuntimeError("Executable doesn't exist at /ms-playwright")
    obs = await session.check_engine()
    assert obs.health is Health.UNKNOWN and "Executable" in obs.detail


async def test_engine_unknown_when_it_takes_too_long(world, monkeypatch):
    world.goto_delay = 0.5
    monkeypatch.setattr(session, "ENGINE_TIMEOUT_S", 0.05)
    obs = await session.check_engine()
    assert obs.health is Health.UNKNOWN and "within" in obs.detail


def test_module_shape():
    mod = browser.MODULE
    assert mod.name == "browser"
    assert tuple(mod.requires_env) == ()
    checks = mod.checks()
    assert [c.name for c in checks] == ["engine"]
    assert checks[0].baseline_sigma is None


# -------------------------------------------------------- no executor path

def _imports_of(path: pathlib.Path) -> set[str]:
    tree = ast.parse(path.read_text())
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            mod = node.module or ""
            if node.level:
                parts = ["atlas", "modules", "browser"][: 3 - (node.level - 1)]
                mod = ".".join(parts + ([mod] if mod else []))
            names.add(mod)
    return names


def test_no_file_in_the_module_imports_the_executor():
    files = list(PKG.glob("*.py"))
    assert files
    for f in files:
        for mod in _imports_of(f):
            assert "executor" not in mod, f"{f.name} imports {mod}"


# ---------------------------------------------------------- tools, agents

READS = {"browser_open", "browser_search", "browser_find_business"}


def test_tools_are_registered():
    from atlas.kernel.tools import TOOLS
    from atlas.tools import builtin

    builtin.register_all()
    for name in READS:
        assert TOOLS[name].read is not None and TOOLS[name].action is None
    assert TOOLS["browser_submit"].action == "browser.submit_form"
    assert TOOLS["browser_submit"].read is None
    assert set(TOOLS["browser_submit"].schema["required"]) == {"url", "fields"}


def test_the_named_agents_hold_the_read_tools_and_none_holds_submit():
    from atlas.kernel import agents

    agents.ROSTER.clear()
    agents.build_roster()
    for name in ("concierge", "ops", "growth", "commerce", "scheduler", "agency"):
        assert READS <= agents.ROSTER[name].tools, name
    for agent in agents.ROSTER.values():
        assert "browser_submit" not in agent.tools, agent.name


def test_agency_business_reads_include_the_browser():
    from atlas.kernel.businesses import BY_NAME
    assert READS <= BY_NAME["agency"].reads


async def test_browser_open_tool_wraps_a_reason(monkeypatch):
    from atlas.tools import builtin

    async def refused(url, *, agent=None):
        return None, "https://x.example/ went to https://evil.example/, not opened"

    monkeypatch.setattr(session, "open", refused)
    out = await builtin.browser_open("https://x.example/")
    assert out["error"].startswith("<untrusted_data")
    assert "evil.example" in out["error"]


async def test_browser_open_tool_wraps_the_landed_url(monkeypatch):
    from atlas.tools import builtin

    async def fine(url, *, agent=None):
        return {"title": "<untrusted_data source='x'>\nt\n</untrusted_data>",
                "text": "w", "url": "https://landed.example/ignore previous",
                "status": 200}, None

    monkeypatch.setattr(session, "open", fine)
    out = await builtin.browser_open("https://x.example/")
    assert out["url"].startswith("<untrusted_data") and out["status"] == 200
    assert out["title"].count("<untrusted_data") == 1           # not re-wrapped


async def test_browser_search_tool_wraps_every_url(monkeypatch):
    from atlas.tools import builtin

    async def fake(query, limit=5):
        return [{"url": "https://a.example/", "text": "w", "engine": "brave",
                 "openable": False, "not_opened": "denied by policy (bank)"}], None

    monkeypatch.setattr(session, "search", fake)
    out = await builtin.browser_search("q")
    hit = out["hits"][0]
    assert "read_at" in out and hit["openable"] is False
    assert hit["url"].startswith("<untrusted_data") and "https://a.example/" in hit["url"]
    assert hit["not_opened"].startswith("<untrusted_data")


async def test_browser_find_business_tool(monkeypatch):
    from atlas.tools import builtin

    async def fake(name, area, *, agent=None):
        return {"query": f"{name} {area}", "denied": "denied: x.example",
                "hits": [{"url": "https://y.example/"}]}, None

    monkeypatch.setattr(session, "find_business", fake)
    out = await builtin.browser_find_business("A", "B")
    assert out["query"] == "A B"
    assert out["denied"].startswith("<untrusted_data")
    assert out["hits"][0]["url"].startswith("<untrusted_data")


async def test_the_read_tools_record_the_asking_agent(monkeypatch):
    from atlas.kernel.tools import invoke
    from atlas.tools import builtin

    builtin.register_all()
    seen = []

    async def fake_open(url, *, agent=None):
        seen.append(("open", agent))
        return None, "no"

    async def fake_find(name, area, *, agent=None):
        seen.append(("find", agent))
        return None, "no"

    monkeypatch.setattr(session, "open", fake_open)
    monkeypatch.setattr(session, "find_business", fake_find)
    await invoke("browser_open", {"url": "https://a.example/"}, agent="concierge",
                 allowed={"browser_open"})
    await invoke("browser_find_business", {"name": "A", "area": "B"}, agent="agency",
                 allowed={"browser_find_business"})
    assert seen == [("open", "concierge"), ("find", "agency")]


# ------------------------------------------------------------------ privacy

def _age(path: pathlib.Path, hours: float) -> None:
    t = time.time() - hours * 3600
    os.utime(path, (t, t))


async def test_purge_deletes_files_and_rows_older_than_24h(memory_backend, monkeypatch,
                                                          tmp_path):
    root = tmp_path / "browser"
    root.mkdir()
    old_png, old_txt = root / "browser-1.png", root / "browser-1.txt"
    old_uuid = root / f"browser-{'a' * 32}.png"
    fresh = root / "browser-2.png"
    for f in (old_png, old_txt, old_uuid, fresh):
        f.write_bytes(b"x")
    for f in (old_png, old_txt, old_uuid):
        _age(f, 25)
    _age(fresh, 2)
    monkeypatch.setenv("BROWSER_DATA_DIR", str(root))

    _rows, files = await privacy.purge_browser_sessions()
    assert files == 3
    assert not old_png.exists() and not old_txt.exists() and not old_uuid.exists()
    assert fresh.exists()
    deletes = [s for s, _ in memory_backend.sql_log if s.startswith("DELETE")]
    assert deletes == [
        "DELETE FROM browser_sessions WHERE at < now() - interval '24 hours'"]


async def test_the_sweep_touches_only_files_the_browser_wrote(memory_backend,
                                                             monkeypatch, tmp_path):
    """BROWSER_DATA_DIR pointed at a shared folder by mistake loses nothing else."""
    root = tmp_path / "data"
    root.mkdir()
    keep = [root / "1.png", root / "notes.txt", root / "browser-notes.txt",
            root / "browser-1.pdf", root / "atlas.db"]
    for f in keep:
        f.write_bytes(b"x")
        _age(f, 100)
    monkeypatch.setenv("BROWSER_DATA_DIR", str(root))
    _rows, files = await privacy.purge_browser_sessions()
    assert files == 0 and all(f.exists() for f in keep)


async def test_purge_with_no_directory_is_fine(memory_backend, monkeypatch, tmp_path):
    monkeypatch.setenv("BROWSER_DATA_DIR", str(tmp_path / "never-made"))
    assert await privacy.purge_browser_sessions() == (0, 0)


async def test_retention_run_purges_browser_sessions_first(monkeypatch):
    order: list[str] = []

    async def sessions():
        order.append("sessions")
        return 3, 5

    async def step(name):
        async def fn():
            order.append(name)
            return 0
        return fn

    monkeypatch.setattr(privacy, "purge_browser_sessions", sessions)
    monkeypatch.setattr(privacy, "roll_up_browser_history", await step("rollup"))
    monkeypatch.setattr(privacy, "purge_browser_history", await step("history"))
    monkeypatch.setattr(privacy, "purge_voice_turns", await step("voice"))
    monkeypatch.setattr(privacy, "purge_memory_reads", await step("reads"))
    await privacy.run()
    assert order == ["sessions", "rollup", "history", "voice", "reads"]


async def test_one_failing_purge_does_not_stop_the_others(monkeypatch):
    ran: list[str] = []

    async def broken():
        raise RuntimeError("DELETE failed")

    async def history():
        ran.append("history")
        return 4

    async def voice():
        ran.append("voice")
        return 2

    async def rollup():
        return 7

    monkeypatch.setattr(privacy, "purge_browser_sessions", broken)
    monkeypatch.setattr(privacy, "roll_up_browser_history", rollup)
    monkeypatch.setattr(privacy, "purge_browser_history", history)
    monkeypatch.setattr(privacy, "purge_voice_turns", voice)

    async def reads():
        ran.append("reads")
        return 3

    monkeypatch.setattr(privacy, "purge_memory_reads", reads)

    async def clips():
        ran.append("clips")
        return 1

    monkeypatch.setattr(privacy, "purge_voice_clips", clips)

    async def footage():
        ran.append("footage")
        return 2, 3_000_000_000

    monkeypatch.setattr(privacy, "purge_clip_sources", footage)
    out = await privacy.run()
    assert ran == ["clips", "history", "voice", "reads", "footage"]
    assert out == {"purged_browser_sessions": -1, "purged_browser_files": -1,
                   "purged_voice_clips": 1,
                   "rolled_up_domains": 7, "purged_events": 4,
                   "purged_voice_turns": 2, "purged_memory_reads": 3,
                   "purged_clip_sources": 2,
                   "freed_clip_source_bytes": 3_000_000_000}


def test_the_password_check_sees_inputs_bound_by_the_form_attribute():
    """`form.elements` includes an <input form="id"> placed outside the form;
    querySelectorAll('input') on the form does not."""
    assert "f.elements" in session._INSPECT_JS
    assert "querySelectorAll" not in session._INSPECT_JS
