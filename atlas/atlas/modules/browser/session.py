"""The browser itself: open a page, search, find a business, submit a form.

"Google" in Miro's words means "search the web". Google's own results page
blocks headless browsers and is denied by policy, so search goes through Brave
(when `BRAVE_API_KEY` is set) or DuckDuckGo's HTML endpoint, and the browser
opens the results. The browser never signs in anywhere — a page that needs a
login is a page ATLAS cannot read, and it says so.

Every call gets a fresh Chromium context: no cookies, no storage, no
credentials, no service workers, ever. One browser runs at a time (`lock()`).
Before a page is opened its URL passes the deny policy and
`_netguard.assert_public` (the host must resolve to public addresses only).
Inside the page every routed request is checked the same way and aborted
when it fails, and so is every WebSocket (`route_web_socket`). Redirect hops
never reach that check — Playwright routes only the first URL of a chain — so
every request of every type (main frame, frames, fetch/XHR, images), every
response and every frame navigation is recorded as it happens. The text and
screenshot are taken FIRST, and the check runs AFTER, over everything
recorded plus every current frame: a page that went anywhere it may not is
refused and both are discarded (the same after a form submit). That detects
the hop; it cannot un-send the one GET the hop made, nor stop page script
sending what it read — only an egress rule or a filtering proxy on the box
closes those, which is Miro's call (see the module docstring and
`_netguard`). WebSockets opened by any page's worker skip
`route_web_socket`; they are recorded (a `websocket` listener on every page
of the context) and refuse the read, but their handshake has already been
sent. SharedWorkers are disabled at launch, because Playwright can neither
route nor see their requests. A window the page opens (`window.open`,
`target=_blank`, an about:blank popup) is closed at once and refuses the
read: a read-only browser has no use for one. And a redirect that a worker
(or any script) follows after the check has run can still send one private
GET; by then the capture is taken, so nothing from it is read.

Every byte a page supplies — its text and its title — is wrapped as untrusted
before it is returned; the tools wrap the URLs too. The text (trimmed to 20k
characters) and a 1280×800 screenshot are kept under
`data/browser/browser-<id>.*` and in `browser_sessions` (URL stored without
its query) for 24 hours; the purge lives in `kernel.privacy`.

Playwright is optional (`pip install atlas[browser] && playwright install
chromium`) and is imported inside each function, so a box without it loads
this module and reports why it cannot browse.

Endpoints assumed: `https://html.duckduckgo.com/html/?q=` for the keyless
search, with results as `<a class="result__a" href="//duckduckgo.com/l/?uddg=…">`
anchors followed by `<a class="result__snippet">`.
"""
from __future__ import annotations

import asyncio
import html
import logging
import os
import re
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urljoin, urlsplit

import httpx

from ...kernel.contracts import ActionResult, Health, Observation
from ...kernel.permissions import wrap_untrusted
from ...kernel.privacy import BROWSER_FILE_PREFIX, browser_dir
from ...kernel.state import execute_sql, fetch_one
from .. import _web
from .._http import explain
from .._netguard import assert_public
from . import policy

log = logging.getLogger("atlas.browser")

MISSING = ("playwright not installed: pip install atlas[browser] && "
           "playwright install chromium")
BUSY = "the browser is busy with another page; nothing was opened — ask again shortly"
TIMEOUT_MS = 20_000
# Playwright neither routes nor reports a SharedWorker's traffic, so page
# script could fetch a private URL through one unseen by every check here.
# Disabled outright: nothing ATLAS reads needs one.
LAUNCH_ARGS = ("--disable-features=SharedWorker",)
# 30 s, not 10: a standalone launch takes about 3 s on the box (measured
# 2026-09-25, three runs: 3.0, 2.7, 2.5 s), but this check runs inside a
# sweep of 118 others and under that load a 10 s budget read UNKNOWN every
# time -- which is not what "chromium is broken" should mean.
ENGINE_TIMEOUT_S = 30.0
OPEN_WALL_S = 60.0
FORM_WALL_S = 90.0
SETTLE_S = 2.0
TEXT_LIMIT = 20_000
VIEWPORT = {"width": 1280, "height": 800}
DDG_URL = "https://html.duckduckgo.com/html/"
_UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
       "Chrome/126.0 Safari/537.36")
_NEVER_SIGNS_IN = ("the page shows a sign-in form; ATLAS never signs in, so "
                   "anything behind it is unread")

# Listings, maps, social and review sites: where a business is mentioned, not
# where it speaks for itself. Matched against every label of the host.
DIRECTORIES = frozenset({
    "yelp", "tripadvisor", "yell", "yellowpages", "facebook", "instagram",
    "linkedin", "twitter", "x", "tiktok", "youtube", "google", "bing",
    "duckduckgo", "wikipedia", "foursquare", "trustpilot", "checkatrade",
    "thomsonlocal", "192", "opentable", "justeat", "just-eat", "deliveroo",
    "ubereats", "treatwell", "bark", "nextdoor", "mapquest", "cylex", "hotfrog",
    "freeindex", "scoot", "bizapedia", "companieshouse", "apple", "waze",
})


def _now() -> datetime:
    return datetime.now(UTC)


def _first_line(exc: BaseException) -> str:
    """Playwright errors carry a call log; the first line is the reason."""
    return explain(exc).splitlines()[0][:300]


def _playwright():
    try:
        from playwright.async_api import async_playwright
    except ImportError:
        return None
    return async_playwright


async def may_open(url: str) -> str | None:
    """Why the browser may not go to `url` (policy, then the address check),
    or None."""
    ok, why = policy.allowed(url)
    if not ok:
        return why
    return await assert_public(url)


# ------------------------------------------------------- one at a time

# One Chromium at a time. Six agents asking at once would otherwise start six
# browsers on a small VPS. Created lazily: a Lock made at import binds to
# whichever event loop exists then (the linkedin module's pattern).
_ONE_AT_A_TIME: asyncio.Lock | None = None


def lock() -> asyncio.Lock:
    global _ONE_AT_A_TIME
    if _ONE_AT_A_TIME is None:
        _ONE_AT_A_TIME = asyncio.Lock()
    return _ONE_AT_A_TIME


class Busy(Exception):
    """The wall clock ran out while waiting for the browser, not while using it."""


async def _exclusive(work: Callable[[], Awaitable[Any]], wall: float) -> Any:
    """Run `work` holding `lock()`, all of it — the wait included — inside
    `wall` seconds. Raises Busy when the time went on waiting."""
    held = {"got": False}

    async def run() -> Any:
        async with lock():
            held["got"] = True
            return await work()

    try:
        return await asyncio.wait_for(run(), timeout=wall)
    except TimeoutError:
        if not held["got"]:
            raise Busy from None
        raise


# ---------------------------------------------------------------- recording

async def _record(url: str, *, agent: str | None, title: str | None = None,
                  status: int | None = None, reason: str | None = None) -> int | None:
    """One browser_sessions row. The URL is stored without its query."""
    try:
        row = await fetch_one(
            """INSERT INTO browser_sessions (url, title, status, reason, agent)
               VALUES ($1, $2, $3, $4, $5) RETURNING id""",
            policy.without_query(url), title, status, reason, agent)
    except Exception:
        log.exception("could not record a browser session")
        return None
    return int(row["id"]) if row else None


def _write_files(stem: Path, text: str, shot: bytes) -> None:
    """Blocking; always through `asyncio.to_thread`."""
    stem.parent.mkdir(parents=True, exist_ok=True)
    stem.with_suffix(".txt").write_text(text)
    stem.with_suffix(".png").write_bytes(shot)


async def _keep(row_id: int | None, text: str, shot: bytes) -> str:
    """Write the text and screenshot, point the row at them, return the PNG
    path. Named `browser-<id>` (or `browser-<uuid>` when no row could be
    written): the 24 h sweep removes only files named that way."""
    name = str(row_id) if row_id is not None else uuid.uuid4().hex
    stem = browser_dir() / f"{BROWSER_FILE_PREFIX}{name}"
    await asyncio.to_thread(_write_files, stem, text, shot)
    text_path, shot_path = str(stem.with_suffix(".txt")), str(stem.with_suffix(".png"))
    if row_id is not None:
        try:
            await execute_sql(
                "UPDATE browser_sessions SET text_path = $2, shot_path = $3 WHERE id = $1",
                row_id, text_path, shot_path)
        except Exception:
            log.exception("could not update browser session %s", row_id)
    return shot_path


# --------------------------------------------------------------------- page

@dataclass(slots=True)
class Aborted:
    url: str
    why: str
    main: bool
    method: str


@dataclass(slots=True)
class Seen:
    url: str
    main: bool          # a main-frame navigation (the whole policy applies)
    request: Any        # held, so identity (not the URL) says what was aborted


@dataclass
class Watch:
    """Everything one page did, recorded as it happened: every request of
    every resource type — main frame, frames, fetch/XHR, images — including
    the redirect hops the route guard never sees; every response; every frame
    navigation; and what the guards aborted. Playwright fires `request`
    before a request is sent, so anything whose content reached a capture was
    recorded before the check that follows the capture."""
    requests: list[Seen] = field(default_factory=list)
    responses: list[str] = field(default_factory=list)
    frames: list[str] = field(default_factory=list)
    aborted: list[Aborted] = field(default_factory=list)
    # The request OBJECTS the guard aborted. Never their URLs: a page can have
    # the private URL aborted once and then reach the very same URL through a
    # redirect hop, which is a different request and is never routed.
    aborted_ids: set[int] = field(default_factory=set)
    # Every WebSocket the page or any of its workers opened, as http(s) URLs.
    # A dedicated Worker's socket skips route_web_socket; page.on("websocket")
    # still sees it. Checked after capture with no skip.
    sockets: list[str] = field(default_factory=list)
    # Pages the page opened (window.open, target=_blank, about:blank). A
    # read-only browser has no use for them: each is closed at once and the
    # read is refused after capture. The first page in the fresh context is
    # the one ATLAS opened itself.
    popups: list[Any] = field(default_factory=list)
    has_main: bool = False
    closing: list[Any] = field(default_factory=list)

    def on_request(self, request: Any) -> None:
        self.requests.append(Seen(request.url, _is_main(request), request))

    def was_aborted(self, seen: Seen) -> bool:
        if getattr(seen.request, "redirected_from", None) is not None:
            return False                    # a hop is never routed, so never aborted
        return id(seen.request) in self.aborted_ids

    def on_response(self, response: Any) -> None:
        self.responses.append(response.url)

    def on_frame(self, frame: Any) -> None:
        self.frames.append(frame.url)

    def on_socket(self, ws: Any) -> None:
        self.sockets.append(_socket_as_http(ws.url))

    def on_page(self, page: Any) -> None:
        """Every page in the context, registered before the first opens, so
        frame and socket recording never depends on which page fired."""
        page.on("framenavigated", lambda frame: self.on_frame(frame))
        page.on("websocket", lambda ws: self.on_socket(ws))
        if not self.has_main:
            self.has_main = True
            return
        self.popups.append(page)
        from ...kernel.background import track
        self.closing.append(track(
            asyncio.get_running_loop().create_task(_close_quietly(page)),
            "browser popup close"))


async def _close_quietly(page: Any) -> None:
    try:
        await page.close()
    except Exception:                                   # noqa: BLE001
        log.debug("a popup was already gone when closed")


def _socket_as_http(url: str) -> str:
    if url.startswith("wss:"):
        return "https" + url[3:]
    if url.startswith("ws:"):
        return "http" + url[2:]
    return url


def _is_main(request: Any) -> bool:
    try:
        return bool(request.is_navigation_request()) and \
            request.frame.parent_frame is None
    except Exception:                                   # noqa: BLE001
        return False


async def _host_public(url: str) -> str | None:
    """The check for anything that is not a main-frame navigation: the host
    may not be denied, and it must resolve to public addresses."""
    if not policy.host_allowed(url):
        return "denied host"
    return await assert_public(url)


def _guard_for(watch: Watch) -> Callable[[Any], Awaitable[None]]:
    async def guard(route: Any) -> None:
        """Every routed request. A main-frame navigation is judged on the
        whole policy; anything else (sub-resources, frames) on its host.
        Both must resolve to public addresses."""
        request = route.request
        url = request.url
        main = _is_main(request)
        if main:
            ok, why = policy.allowed(url)
            reason = None if ok else why
            if reason is None:
                reason = await assert_public(url)
        else:
            reason = await _host_public(url)
        if reason is None:
            await route.continue_()
            return
        watch.aborted.append(Aborted(url, reason, main, getattr(request, "method", "GET")))
        watch.aborted_ids.add(id(request))
        await route.abort()
    return guard


def _socket_guard_for(watch: Watch) -> Callable[[Any], Awaitable[None]]:
    async def guard(ws: Any) -> None:
        """WebSockets never pass `context.route`, and CORS does not bind them:
        the same host and address check, or the socket is closed unconnected."""
        url = ws.url
        reason = await _host_public(_socket_as_http(url))
        if reason is None:
            ws.connect_to_server()
            return
        watch.aborted.append(Aborted(url, reason, False, "WEBSOCKET"))
        await ws.close()
    return guard


def _refused(asked: str, went: str, why: str) -> str:
    went_plain = policy.without_query(went)
    lowered = went.lower()
    if any(w in lowered for w in ("login", "signin", "sign-in", "accounts.")):
        return (f"{policy.without_query(asked)} leads to a sign-in page "
                f"({went_plain}); ATLAS never signs in, so it cannot read it")
    return (f"{policy.without_query(asked)} went to {went_plain}, which is not "
            f"opened: {why}")


def _http(url: str) -> bool:
    return url.startswith(("http://", "https://"))


async def _refusal(page: Any, watch: Watch, asked: str) -> str | None:
    """Run AFTER a capture, over everything recorded up to now: every
    request the guard did not itself abort (judged per request, never per
    URL; redirect hops of any type included), every
    response, every frame navigation, every current frame and the landed URL.
    Any failure means the capture is discarded. A page that went anywhere it
    may not is not read."""
    if watch.popups:
        where = ", ".join(policy.without_query(getattr(p, "url", "") or "about:blank")
                          for p in watch.popups)
        return (f"{policy.without_query(asked)}: page opened a new window ({where}); "
                "a read-only browser does not follow pages a page opens, so "
                "nothing was read")
    for seen in list(watch.requests):
        if watch.was_aborted(seen) or not _http(seen.url):
            continue
        why = await may_open(seen.url) if seen.main else await _host_public(seen.url)
        if why:
            return _refused(asked, seen.url, why)
    if _http(page.url):
        why = await may_open(page.url)
        if why:
            return _refused(asked, page.url, why)
    frames = [*watch.responses, *watch.frames, *watch.sockets,
              *(f.url for f in (getattr(page, "frames", ()) or ()))]
    # No abort skip here: an aborted request has no response, and an aborted
    # frame shows as chrome-error://, so anything http(s) listed here loaded.
    for url in frames:
        if not _http(url):
            continue
        why = await _host_public(url)
        if why:
            return _refused(asked, url, f"part of the page: {why}")
    return None


def _aborted_main(watch: Watch, asked: str) -> str | None:
    for hit in watch.aborted:
        if hit.main:
            return _refused(asked, hit.url, hit.why)
    return None


async def _page(async_playwright: Any, url: str, work: Any, watch: Watch) -> Any:
    """Launch, open a fresh context, go to `url`, hand the page to `work`.
    The context is new every call and is never given a storage state or
    credentials. Callers hold `lock()`."""
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True, args=list(LAUNCH_ARGS))
        try:
            context = await browser.new_context(viewport=VIEWPORT,
                                                accept_downloads=False,
                                                service_workers="block")
            try:
                context.set_default_timeout(TIMEOUT_MS)
                # A plain function: Playwright tags the listener with its
                # own attributes, which a slotted bound method cannot take.
                context.on("request", lambda request: watch.on_request(request))
                context.on("response", lambda response: watch.on_response(response))
                await context.route("**/*", _guard_for(watch))
                await context.route_web_socket("**/*", _socket_guard_for(watch))
                context.on("page", lambda new_page: watch.on_page(new_page))
                page = await context.new_page()
                response = await page.goto(url, timeout=TIMEOUT_MS,
                                           wait_until="domcontentloaded")
                return await work(page, response)
            finally:
                if watch.closing:
                    await asyncio.gather(*watch.closing, return_exceptions=True)
                await context.close()
        finally:
            await browser.close()


async def open(url: str, *, agent: str | None = None) -> tuple[dict | None, str | None]:
    """Read one page. Returns ({title, text, shot_path, status, url, read_at
    [, note]}, None) with title and text wrapped as untrusted, or (None, why).

    The name shadows the builtin inside this file, which is why files here
    are written with `Path.write_*`."""
    why = await may_open(url)
    if why is not None:
        await _record(url, agent=agent, reason=why)
        return None, why
    async_playwright = _playwright()
    if async_playwright is None:
        return None, MISSING
    watch = Watch()

    async def read(page: Any, response: Any) -> dict[str, Any]:
        # Capture first, check second: a hop that lands while the text or
        # screenshot is being taken was recorded before the check runs, and a
        # failure discards the whole capture.
        got = {
            "final": page.url,
            "status": getattr(response, "status", None),
            "title": (await page.title() or "")[:300],
            "text": (await page.inner_text("body") or "")[:TEXT_LIMIT],
            "shot": await page.screenshot(type="png"),
            "signin": await page.query_selector("input[type=password]") is not None,
        }
        refused = await _refusal(page, watch, url)
        if refused:
            return {"refused": refused}
        return got

    try:
        # Each step has its own 20 s timeout; this bounds the whole read,
        # waiting for the browser included, so nothing stalls the agent.
        got = await _exclusive(lambda: _page(async_playwright, url, read, watch),
                               OPEN_WALL_S)
    except Busy:
        return None, BUSY
    except TimeoutError:
        reason = (f"could not open {policy.without_query(url)}: no answer within "
                  f"{OPEN_WALL_S:g} s")
        await _record(url, agent=agent, reason=reason)
        return None, reason
    except Exception as exc:                            # noqa: BLE001
        reason = _aborted_main(watch, url) or (
            f"could not open {policy.without_query(url)}: {_first_line(exc)}")
        await _record(url, agent=agent, reason=reason)
        return None, reason

    if "refused" in got:
        await _record(url, agent=agent, reason=got["refused"])
        return None, got["refused"]

    row_id = await _record(got["final"], agent=agent, title=got["title"],
                           status=got["status"])
    shot_path = await _keep(row_id, got["text"], got["shot"])
    source = policy.without_query(got["final"])
    out: dict[str, Any] = {
        "title": wrap_untrusted(got["title"], source),
        "text": wrap_untrusted(got["text"], source),
        "shot_path": shot_path,
        "status": got["status"],
        "url": got["final"],
        "read_at": _now().isoformat(),
    }
    if got["signin"]:
        out["note"] = _NEVER_SIGNS_IN
    return out, None


# ------------------------------------------------------------------- search

_ANCHOR = re.compile(r"<a\s([^>]*)>(.*?)</a>", re.DOTALL | re.IGNORECASE)
_HREF = re.compile(r'href\s*=\s*"([^"]*)"', re.IGNORECASE)
_CLASS = re.compile(r'class\s*=\s*"([^"]*)"', re.IGNORECASE)
_TAG = re.compile(r"<[^>]+>")


def _clean(fragment: str) -> str:
    return " ".join(html.unescape(_TAG.sub("", fragment)).split())


def _unwrap(href: str) -> str | None:
    """DuckDuckGo links go through its own redirector (`/l/?uddg=<target>`).
    Its ads go through `/y.js` and have no target: those come back None."""
    url = urljoin("https://duckduckgo.com/", html.unescape(href))
    parts = urlsplit(url)
    if (parts.hostname or "").endswith("duckduckgo.com"):
        return parse_qs(parts.query).get("uddg", [None])[0]
    return url


def parse_duckduckgo(page: str) -> list[dict[str, str]]:
    """Result anchors (`result__a`) in order, each with the snippet anchor
    that follows it, if any."""
    hits: list[dict[str, str]] = []
    current: dict[str, str] | None = None
    for attrs, inner in _ANCHOR.findall(page):
        cls = _CLASS.search(attrs)
        classes = cls.group(1).split() if cls else []
        if "result__a" in classes:
            href = _HREF.search(attrs)
            url = _unwrap(href.group(1)) if href else None
            if not url or not url.startswith(("http://", "https://")):
                current = None
                continue
            current = {"title": _clean(inner), "url": url, "snippet": ""}
            hits.append(current)
        elif "result__snippet" in classes and current is not None \
                and not current["snippet"]:
            current["snippet"] = _clean(inner)
    return hits


async def _duckduckgo_html(query: str) -> tuple[str | None, str | None]:
    try:
        async with httpx.AsyncClient(timeout=20, follow_redirects=True) as c:
            r = await c.get(DDG_URL, params={"q": query}, headers={"User-Agent": _UA})
            r.raise_for_status()
    except Exception as exc:                            # noqa: BLE001
        return None, explain(exc)
    return r.text, None


async def search(query: str, limit: int = 5) -> tuple[list[dict], str | None]:
    """Search the web — Brave with a key, DuckDuckGo without — never Google.

    Each hit is {url, text, engine, openable, read_at}: `text` is the title and
    snippet wrapped as untrusted; `openable` says whether `open` would read the
    URL (a denied hit is still listed, with `not_opened`, so an answer about a
    bank is not silently empty — it just is not opened). The raw `url` is for
    `find_business`; the tools wrap it before it reaches a prompt."""
    if os.getenv("BRAVE_API_KEY"):
        engine = "brave"
        raw, reason = await _web.search(query, limit)
    else:
        engine = "duckduckgo"
        page, reason = await _duckduckgo_html(query)
        raw = parse_duckduckgo(page) if page is not None else []
        if reason is None and not raw and "anomaly" in (page or "").lower():
            reason = "DuckDuckGo answered with a bot challenge instead of results"
    if reason is not None:
        return [], reason

    read_at = _now().isoformat()
    hits: list[dict] = []
    for h in raw[:limit]:
        url = h.get("url") or ""
        if not url:
            continue
        why = await may_open(url)
        body = f"{h.get('title', '')}\n{h.get('snippet', '')}".strip()
        hit = {"url": url, "text": wrap_untrusted(body, policy.without_query(url)),
               "engine": engine, "openable": why is None, "read_at": read_at}
        if why is not None:
            hit["not_opened"] = why
        hits.append(hit)
    return hits, None


def is_directory(url: str) -> bool:
    host = (urlsplit(url).hostname or "").lower()
    return any(label in DIRECTORIES for label in host.split("."))


async def find_business(name: str, area: str, *, agent: str | None = None
                        ) -> tuple[dict | None, str | None]:
    """Search "<name> <area>", then open the first result that is the
    business's own site rather than a directory, map or social profile.
    Returns what `open` returns plus the query used.

    A denied hit is never skipped in silence: if the likely site is one the
    browser may not open, nothing is opened and the answer says
    "denied: <host>" with the remaining hits, unopened — substituting the next
    site would report a competitor as the business."""
    query = " ".join(f"{name} {area}".split())
    hits, reason = await search(query, limit=10)
    if reason is not None:
        return None, reason
    for i, hit in enumerate(hits):
        if is_directory(hit["url"]):
            continue
        if not hit.get("openable"):
            host = urlsplit(hit["url"]).hostname or hit["url"]
            return {"query": query, "denied": f"denied: {host}",
                    "why": hit.get("not_opened", ""), "url": hit["url"],
                    "hits": hits[i + 1:]}, None
        page, why = await open(hit["url"], agent=agent)
        if page is None:
            return None, why
        return {**page, "query": query}, None
    return None, f"no result for {query!r} other than directories and listings"


# --------------------------------------------------------------------- form

_SUBMIT_JS = ("el => { const f = el.form || el.closest('form'); if (!f) return false;"
              " if (f.requestSubmit) { f.requestSubmit(); } else { f.submit(); }"
              " return true; }")
# The element's live `type` property (a type set by script counts), whether
# its form holds a password field anywhere (`form.elements` includes inputs
# bound from outside by a `form="id"` attribute), and where that form posts.
_INSPECT_JS = (
    "el => { const f = el.form || el.closest('form');"
    " const pw = !!f && Array.from(f.elements)"
    ".some(i => String(i.type || '').toLowerCase() === 'password');"
    " return { type: String(el.type || '').toLowerCase(), formPassword: pw,"
    " action: f ? (f.action || location.href) : null,"
    " method: f ? String(f.method || 'get').toLowerCase() : null }; }")


def _split_top(text: str, sep: str, once: bool = False) -> list[str]:
    """Split on `sep` outside [brackets], (parens) and quotes inside them, so
    `input[name='a;b']=x` stays one selector."""
    parts, buf, depth, quote = [], [], 0, ""
    for ch in text:
        if quote:
            quote = "" if ch == quote else quote
        elif depth and ch in "'\"":
            quote = ch
        elif ch in "[(":
            depth += 1
        elif ch in "])":
            depth = max(0, depth - 1)
        elif ch == sep and depth == 0 and not (once and parts):
            parts.append("".join(buf))
            buf = []
            continue
        buf.append(ch)
    parts.append("".join(buf))
    return parts


def parse_fields(fields: str) -> tuple[list[tuple[str, str]], str | None]:
    """"selector=value;selector=value" -> [(selector, value), ...]."""
    pairs: list[tuple[str, str]] = []
    for item in _split_top(fields or "", ";"):
        item = item.strip()
        if not item:
            continue
        halves = _split_top(item, "=", once=True)
        if len(halves) != 2:
            return [], f"{item!r} has no '=': fields are 'selector=value;…'"
        selector, value = halves[0].strip(), halves[1].strip()
        if not selector:
            return [], f"{item!r} names no selector"
        pairs.append((selector, value))
    if not pairs:
        return [], "no fields given: fields are 'selector=value;…'"
    return pairs, None


async def _form(url: str, fields: str, *, commit: bool) -> ActionResult:
    why = await may_open(url)
    if why is not None:
        return ActionResult(False, f"not touching a form on "
                                   f"{policy.without_query(url)}: {why}")
    pairs, reason = parse_fields(fields)
    if reason is not None:
        return ActionResult(False, reason)
    async_playwright = _playwright()
    if async_playwright is None:
        return ActionResult(False, MISSING)
    watch = Watch()
    # What has happened so far, for a wall-clock timeout to report honestly:
    # once the form is submitted, a timeout is not a failure to send.
    sent: dict[str, Any] = {"submitted": False, "evidence": {}}

    async def work(page: Any, response: Any) -> tuple[ActionResult, bytes | None, str]:
        refused = await _refusal(page, watch, url)
        if refused:
            return ActionResult(False, refused), None, ""
        final = policy.without_query(page.url)
        found: list[str] = []
        missing: list[str] = []
        elements: list[tuple[Any, str]] = []
        forms: set[tuple[str | None, str | None]] = set()
        for selector, value in pairs:
            el = await page.query_selector(selector)
            if el is None:
                missing.append(selector)
                continue
            info = await el.evaluate(_INSPECT_JS) or {}
            if info.get("type") == "password" or info.get("formPassword"):
                return ActionResult(
                    False, f"{selector} is in a form with a password field; ATLAS "
                           "never signs in or signs up, so nothing was filled"), None, ""
            found.append(selector)
            elements.append((el, value))
            forms.add((info.get("action"), info.get("method")))
        action, method = next(iter(forms)) if len(forms) == 1 else (None, None)
        evidence: dict[str, Any] = {
            "url": final, "found": found, "missing": missing,
            "action": policy.without_query(action) if action else None,
            "method": method}
        if missing:
            return ActionResult(
                False, f"on {final}: found {', '.join(found) or 'none'}; missing "
                       f"{', '.join(missing)} — nothing was filled or submitted",
                evidence), None, ""
        if len(forms) > 1:
            return ActionResult(False, f"on {final}: the fields belong to different "
                                       "forms; nothing was filled or submitted",
                                evidence), None, ""
        if not action:
            return ActionResult(False, f"{found[0]} is not inside a form on {final}; "
                                       "nothing was submitted", evidence), None, ""
        refused_action = await may_open(action)
        if refused_action is not None:
            return ActionResult(
                False, f"the form on {final} {str(method).upper()}s to "
                       f"{policy.without_query(action)}, which is not opened: "
                       f"{refused_action}; nothing was filled or submitted",
                evidence), None, ""
        where = (f"the form {str(method).upper()}s to "
                 f"{policy.without_query(action)}")
        if not commit:
            return ActionResult(
                True, f"on {final}: all {len(found)} selectors exist "
                      f"({', '.join(found)}); {where}; nothing was filled or "
                      "submitted", evidence), None, ""
        for el, value in elements:
            await el.fill(value)
        mark = len(watch.aborted)
        # requestSubmit runs the moment this is issued: from here a timeout
        # reports a send, never "nothing was submitted" (a retry would send
        # it twice).
        sent["submitted"], sent["evidence"] = True, evidence
        if not await elements[0][0].evaluate(_SUBMIT_JS):
            sent["submitted"] = False
            return ActionResult(False, f"{found[0]} is not inside a form on {final}; "
                                       "nothing was submitted", evidence), None, ""
        # Submitted in the browser. From here on nothing may turn this into a
        # failure unless the request itself was stopped on its way out.
        shot, text = None, ""
        try:
            await asyncio.sleep(SETTLE_S)
            await page.wait_for_load_state("domcontentloaded", timeout=TIMEOUT_MS)
        except Exception as exc:                        # noqa: BLE001
            evidence["after_error"] = _first_line(exc)
        blocked = [a for a in watch.aborted[mark:]
                   if a.main or a.method not in ("GET", "WEBSOCKET")]
        if blocked:
            first = blocked[0]
            return ActionResult(
                False, f"the form was submitted in the browser, but the request to "
                       f"{policy.without_query(first.url)} was blocked ({first.why}); "
                       "nothing reached it", evidence), None, ""
        evidence["after"] = policy.without_query(page.url)
        # Capture, then check what the page did meanwhile; a failure discards
        # the capture (the submission itself stands: it was sent).
        try:
            shot = await page.screenshot(type="png")
            text = (await page.inner_text("body") or "")[:TEXT_LIMIT]
        except Exception as exc:                        # noqa: BLE001
            evidence["after_error"] = _first_line(exc)
        landed = await _refusal(page, watch, url)
        if landed:
            evidence["after_refused"] = landed
            shot, text = None, ""
        return ActionResult(
            True, f"submitted {len(found)} fields on {final}; {where}; the page is "
                  f"now {evidence['after']}", evidence), shot, text

    try:
        result, shot, text = await _exclusive(
            lambda: _page(async_playwright, url, work, watch), FORM_WALL_S)
    except Busy:
        return ActionResult(False, BUSY)
    except TimeoutError:
        if sent["submitted"]:
            return ActionResult(True, f"submitted on {policy.without_query(url)}; the "
                                      f"page did not settle within {FORM_WALL_S:g} s",
                                sent["evidence"])
        return ActionResult(False, f"no answer from {policy.without_query(url)} within "
                                   f"{FORM_WALL_S:g} s; nothing was submitted")
    except Exception as exc:                            # noqa: BLE001
        if sent["submitted"]:
            return ActionResult(True, f"submitted on {policy.without_query(url)}; "
                                      f"then: {_first_line(exc)}", sent["evidence"])
        reason = _aborted_main(watch, url) or (
            f"could not open the form at {policy.without_query(url)}: "
            f"{_first_line(exc)}")
        return ActionResult(False, reason)
    if commit and result.ok:
        row_id = await _record(url, agent="browser.submit_form",
                               reason="form submitted")
        if shot is not None:
            path = await _keep(row_id, text, shot)
            result = ActionResult(True, result.detail,
                                  {**result.evidence, "shot_path": path})
    return result


async def submit_form(url: str, fields: str) -> ActionResult:
    """Fill `fields` ("selector=value;…") on `url` and submit the form. Tier
    ASK and irreversible: a sent form cannot be unsent."""
    return await _form(url, fields, commit=True)


async def submit_form_dry(url: str, fields: str) -> ActionResult:
    """Open the page and report which selectors exist and where the form
    posts. Fills nothing."""
    return await _form(url, fields, commit=False)


# ------------------------------------------------------------------- health

async def check_engine() -> Observation:
    """OK when Chromium launches and loads about:blank within `ENGINE_TIMEOUT_S`."""
    async_playwright = _playwright()
    if async_playwright is None:
        return Observation("engine", Health.UNKNOWN, detail=MISSING)

    async def blank() -> None:
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True, args=list(LAUNCH_ARGS))
            try:
                context = await browser.new_context()
                page = await context.new_page()
                await page.goto("about:blank")
                await context.close()
            finally:
                await browser.close()

    try:
        await _exclusive(blank, ENGINE_TIMEOUT_S)
    except Busy:
        return Observation("engine", Health.UNKNOWN, detail=(
            "the browser was busy with a page for the whole check; not measured"))
    except TimeoutError:
        return Observation("engine", Health.UNKNOWN, detail=(
            f"chromium did not load about:blank within {ENGINE_TIMEOUT_S:g} s"))
    except Exception as exc:                            # noqa: BLE001
        return Observation("engine", Health.UNKNOWN,
                           detail=f"chromium would not start: {_first_line(exc)}")
    return Observation("engine", Health.OK,
                       detail="chromium launched and loaded about:blank")
