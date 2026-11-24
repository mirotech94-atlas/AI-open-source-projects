"""Move the whole storefront under /academy/ (Miro, 2026-11-16).

https://agency.storefront.test/academy/ is the ATLAS Academy website, not one page:
every store page lives at /academy/<path> (/academy/blog/, /academy/join/,
/academy/safe-ai-operator/, /academy/assets/style.css ...). The page builders
in site.py, blog_pages.py, lesson_pages.py and support_site.py still write the
pages with the relative links of the old layout, where the store sat at the
host root. `relocate` is the one place that turns that layout into the new
one, so no builder needs to know where it is mounted:

* every output path gets the `academy/` prefix, except the two files the host
  serves from its root: 404.html and sitemap-academy.xml (robots.txt names the
  sitemap at that address);
* every internal link, in HTML attributes and in quoted strings of the page
  scripts and JSON, that points at a store path becomes root-absolute under
  /academy/; a relative link is first resolved against the page's old place;
* every absolute https://<host>/<store path> (canonical, Open Graph, JSON-LD,
  the sitemap, llms.txt, the feeds, catalog.json) gets the prefix too;
* the agency landing (/), /preview/, /public/*, /media/, /healthz and the
  webhooks are not store paths and are never touched.

Old bare store paths (paid, receipt, recover and unsubscribe links already
sent) are not served by this build: Caddyfile.public 308-redirects them.

Pure standard library: run by site.render, tested on its output."""
from __future__ import annotations

import posixpath
import re
from urllib.parse import urlsplit

MOUNT = "academy"
# Served from the host root, not under /academy/.
ROOT_FILES = frozenset({"404.html", "sitemap-academy.xml"})
# Output directories whose files are data, shown verbatim in the videos; never
# rewritten (the free lab files must stay the strings the tutorials print).
VERBATIM_DIRS = ("free/", "assets/fonts/")
TEXT_SUFFIXES = (".html", ".js", ".json", ".xml", ".txt", ".css", ".svg")

_SCHEME = re.compile(r"^(?:[a-zA-Z][a-zA-Z0-9+.-]*:|//)")
_URL_ATTR = re.compile(
    r"""(\b(?:href|src|poster|action|data-src|data-href)=)(["'])(.*?)\2""", re.DOTALL)
_QUOTED_ROOT = re.compile(r"""(["'])/([A-Za-z0-9][A-Za-z0-9._-]*)(?=[/"'?#])""")


def moved_tops(paths: list[str]) -> frozenset[str]:
    """The first path segment of every file that moves under /academy/: the
    store's top-level names, which a link may start with."""
    return frozenset(p.split("/")[0] for p in paths
                     if p.split("/")[0] != MOUNT and p not in ROOT_FILES)


def new_path(path: str) -> str:
    if path in ROOT_FILES or path.split("/")[0] == MOUNT:
        return path
    return f"{MOUNT}/{path}"


def _target(value: str, page: str, tops: frozenset[str]) -> str:
    """One link value of the page at old path `page`, in the new layout."""
    if not value or value.startswith("#") or _SCHEME.match(value):
        return value
    parts = urlsplit(value)
    if not parts.path:
        return value
    suffix = value[len(parts.path):]
    path = parts.path
    if not path.startswith("/"):
        path = posixpath.join(posixpath.dirname("/" + page), path)
    resolved = posixpath.normpath(path)
    if resolved.startswith("/.."):
        return value
    if path.endswith("/") and resolved != "/":
        resolved += "/"
    seg = resolved.split("/")[1] if resolved != "/" else ""
    if seg in tops:
        resolved = f"/{MOUNT}{resolved}"
    return resolved + suffix


def _absolute(text: str, host: str, tops: frozenset[str]) -> str:
    pat = re.compile(r"(?<![\w.-])((?:https?://)?" + re.escape(host) + r")"
                     r"(/[^\s\"'<>)\\]*)")

    def fix(m: re.Match[str]) -> str:
        rest = m.group(2)
        seg = rest.split("/")[1].split("?")[0].split("#")[0]
        return m.group(1) + (f"/{MOUNT}{rest}" if seg in tops else rest)

    return pat.sub(fix, text)


def _text(path: str, html: str, host: str, tops: frozenset[str]) -> str:
    if path.endswith(".html"):
        html = _URL_ATTR.sub(
            lambda m: f"{m.group(1)}{m.group(2)}{_target(m.group(3), path, tops)}"
                      f"{m.group(2)}", html)
    html = _QUOTED_ROOT.sub(
        lambda m: (f"{m.group(1)}/{MOUNT}/{m.group(2)}"
                   if m.group(2) in tops else m.group(0)), html)
    return _absolute(html, host, tops)


def relocate(files: dict[str, bytes], host: str) -> dict[str, bytes]:
    """The old-layout `files` ({path: bytes}) in the new layout. `host` is the
    store's host name (agency.storefront.test): absolute URLs on it are rewritten."""
    tops = moved_tops(list(files))
    out: dict[str, bytes] = {}
    for path, data in files.items():
        if path.endswith(TEXT_SUFFIXES) and not path.startswith(VERBATIM_DIRS):
            try:
                data = _text(path, data.decode(), host, tops).encode()
            except UnicodeDecodeError:
                pass
        out[new_path(path)] = data
    return out
