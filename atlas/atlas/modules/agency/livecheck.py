"""The 20-item launch check against a site that is already live.

`launch_audit` reads a site ATLAS built, off disk. A site another builder
published (Webild, 2026-09-29) has no files on this box, so this fetches
what a visitor would get and hands it to the same 20 checks
(`launchcheck.live_audit`):

* the home page (the URL given), then same-host pages it links to, and the
  pages those link to, up to `MAX_PAGES` in all;
* `/robots.txt` and `/sitemap.xml` -- counted only when the host answers
  200 with something that is not an HTML page (a single-page app answers
  every path with its index, and that is not a robots.txt);
* a path that cannot exist, to see whether a missing page is a real 404;
* the share image and the team photo, so their size and bytes can be
  checked rather than assumed.

Every request goes through `_netguard.get_public` on a client that does not
follow redirects itself, so every hop is asked "is this the public web?"
first: a published URL whose name resolves to this box, the mesh or a
metadata address is refused, not fetched. A home page that redirects to a
host outside `allowed_host` is refused too. Bodies are capped at
`MAX_BYTES`; a page that cannot be read is named, never skipped silently,
and a home page that cannot be read makes every item an ISSUE.

Read-only: GETs at a public site, nothing else.
"""
from __future__ import annotations

import asyncio
import secrets
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urljoin, urlsplit, urlunsplit

import httpx

from .._http import explain
from .._netguard import get_public
from .launchcheck import (
    LaunchReport,
    LiveSite,
    is_page_path,
    live_audit,
    live_name,
    page_from_markup,
    unreadable,
)
from .sitekit import INDEX

MAX_PAGES = 10
MAX_ASSETS = 10          # same-host non-page links (a PDF menu) checked for a 200
MAX_BYTES = 3_000_000
TIMEOUT = 15.0
USER_AGENT = "ATLAS launch check (read-only)"


def _origin(url: str) -> str:
    parts = urlsplit(url)
    return urlunsplit((parts.scheme, parts.netloc, "", "", ""))


def _host(url: str) -> str:
    return (urlsplit(url).hostname or "").lower().rstrip(".")


def _is_html(response: Any) -> bool:
    kind = response.headers.get("content-type", "").lower()
    if kind:
        return "html" in kind
    return response.content[:200].lstrip().lower().startswith((b"<!doctype", b"<html"))


async def _get(client: httpx.AsyncClient, url: str) -> tuple[Any, str | None]:
    try:
        response, reason = await get_public(client, url,
                                            headers={"user-agent": USER_AGENT})
    except Exception as exc:  # noqa: BLE001
        return None, explain(exc)
    if response is None:
        return None, reason
    if len(response.content) > MAX_BYTES:
        return None, f"{url} is larger than {MAX_BYTES} bytes; not read"
    return response, None


def _links(name: str, html: str, host: str, page_url: str,
           pages: bool = True) -> list[tuple[str, str]]:
    """(live name, absolute URL, fragment and query dropped) for every
    same-host page `html` links to -- or, with `pages=False`, every
    same-host file that is not a page. Blocking (parsing)."""
    from .launchcheck import _resolve
    page = page_from_markup(name, html)
    out = []
    for a in page.root.find_all("a"):
        href = a.get("href")
        rel = _resolve(name, href, host)
        if rel is None or is_page_path(rel) is not pages:
            continue
        parts = urlsplit(urljoin(page_url, href.strip()))
        out.append((live_name(rel),
                    urlunsplit((parts.scheme, parts.netloc, parts.path or "/", "", ""))))
    return out


def _assets(pages: dict[str, str]) -> list[str]:
    """The share image and the team photo's src, the two images the checks
    read the bytes of. Blocking (parsing)."""
    wanted = []
    index = pages.get(INDEX)
    if index is not None:
        page = page_from_markup(INDEX, index)
        image = page.meta("og:image")
        if image:
            wanted.append(image)
    for name, html in pages.items():
        page = page_from_markup(name, html)
        for node in page.root.iter():
            if node.get("id") == "team":
                imgs = node.find_all("img")
                if imgs and imgs[0].get("src"):
                    wanted.append(imgs[0].get("src"))
                break
    return wanted


async def fetch_site(url: str, *, allowed_host: Callable[[str], bool] | None = None,
                     client: httpx.AsyncClient | None = None) -> LiveSite:
    """Everything the launch check reads, fetched from `url`. Never raises:
    what could not be read is in `errors`, and with no home page `pages` is
    empty."""
    own = client is None
    if client is None:
        client = httpx.AsyncClient(timeout=TIMEOUT, follow_redirects=False)
    try:
        return await _fetch(client, url, allowed_host)
    finally:
        if own:
            await client.aclose()


async def _fetch(client: httpx.AsyncClient, url: str,
                 allowed_host: Callable[[str], bool] | None) -> LiveSite:
    errors: list[str] = []
    home, reason = await _get(client, url)
    if home is None:
        return LiveSite(url, {}, {}, "was not probed", (f"{url}: {reason}",))
    final = str(home.url)
    host = _host(final)
    if allowed_host is not None and not allowed_host(host):
        why = (f"{url} redirected to {host or final}, which is not an allowed "
               "host; not read")
        return LiveSite(url, {}, {}, "was not probed", (why,))
    if home.status_code != 200 or not _is_html(home):
        why = (f"{url} answered HTTP {home.status_code}"
               f"{'' if _is_html(home) else ' with no HTML page'}")
        return LiveSite(url, {}, {}, "was not probed", (why,))
    base = _origin(final)
    pages: dict[str, str] = {INDEX: home.text}
    urls: dict[str, str] = {INDEX: final}
    queue: list[tuple[str, str, str]] = [(INDEX, home.text, final)]

    unfetched: set[str] = set()
    while queue:
        name, html, here = queue.pop(0)
        for target, page_url in await asyncio.to_thread(_links, name, html, host, here):
            if target in urls:
                continue
            if len(pages) >= MAX_PAGES:
                unfetched.add(target)
                continue
            urls[target] = page_url
            response, reason = await _get(client, page_url)
            if response is None:
                errors.append(f"{page_url}: {reason}")
                continue
            if _host(str(response.url)) != host:
                errors.append(f"{page_url} redirected off the site; not read")
                continue
            if response.status_code != 200 or not _is_html(response):
                errors.append(f"{page_url} answered HTTP {response.status_code}")
                continue
            pages[target] = response.text
            queue.append((target, response.text, str(response.url)))

    present: set[str] = set()
    wanted: dict[str, str] = {}
    for name, html in pages.items():
        for target, file_url in await asyncio.to_thread(
                _links, name, html, host, urls.get(name, final), False):
            wanted.setdefault(target, file_url)
    for target, file_url in list(wanted.items())[:MAX_ASSETS]:
        response, reason = await _get(client, file_url)
        if (response is not None and response.status_code == 200
                and _host(str(response.url)) == host):
            present.add(target)
    unfetched |= set(list(wanted)[MAX_ASSETS:])

    files: dict[str, bytes] = {}
    for name in ("robots.txt", "sitemap.xml"):
        response, _reason = await _get(client, f"{base}/{name}")
        if (response is not None and response.status_code == 200
                and _host(str(response.url)) == host and not _is_html(response)):
            files[name] = response.content

    probe = f"{base}/atlas-launch-check-{secrets.token_hex(6)}"
    response, reason = await _get(client, probe)
    if response is None:
        not_found = f"could not be probed ({reason})"
    elif response.status_code == 404 and _is_html(response):
        pages["404.html"] = response.text
        not_found = "answered 404 with a page"
    elif response.status_code == 404:
        not_found = "answered 404 with no HTML page"
    else:
        not_found = f"answered HTTP {response.status_code} (a soft 404)"

    for src in await asyncio.to_thread(_assets, pages):
        absolute = urljoin(urls.get(INDEX, final), src)
        response, reason = await _get(client, absolute)
        if response is None or response.status_code != 200:
            errors.append(f"image {absolute}: "
                          f"{reason or f'HTTP {response.status_code}'}")
            continue
        files[absolute] = response.content
        if _host(absolute) == host:
            files[live_name(urlsplit(absolute).path)] = response.content

    return LiveSite(final, pages, files, not_found, tuple(errors),
                    frozenset(present), frozenset(unfetched - set(pages)))


async def live_launch_audit(url: str, provenance: dict[str, Any] | None, *,
                            allowed_host: Callable[[str], bool] | None = None,
                            client: httpx.AsyncClient | None = None) -> LaunchReport:
    """Fetch `url` and run the 20 items over it."""
    try:
        site = await fetch_site(url, allowed_host=allowed_host, client=client)
    except Exception as exc:  # noqa: BLE001
        return unreadable(url, f"{url} could not be read: {explain(exc)}",
                          datetime.now(UTC))
    return await asyncio.to_thread(live_audit, site, provenance)
