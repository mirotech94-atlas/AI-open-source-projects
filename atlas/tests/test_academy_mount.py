"""The whole store is served under /academy/ (Miro, 2026-11-16).

Checks the shipped layout (`site.render`), not the content of any page:
every internal link resolves to a file in the render, canonical and sitemap
agree and live under /academy/, the old bare paths are redirected by both
Caddyfiles, and every generator of a public store URL emits /academy/.
"""
from __future__ import annotations

import json
import posixpath
import re
from datetime import UTC, datetime
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlsplit

from atlas.modules import store_origin
from atlas.modules.academy import catalog as cat_mod
from atlas.modules.academy import mount, site

ROOT = Path(__file__).resolve().parents[1]
NOW = datetime(2026, 9, 30, 12, 0, 0, tzinfo=UTC)
HOST = "agency.storefront.test"
VIDEOS = [site.Video("channel", 1, "uPzByskDRXw", datetime(2026, 10, 10, 18, 5, tzinfo=UTC)),
          site.Video("claude", 0, "M1a9Rd9mN6E", datetime(2026, 10, 11, 17, 20, tzinfo=UTC))]
LESSONS = [{"series": "claude", "episode": 0, "video_id": "M1a9Rd9mN6E",
            "title": "Claude, first steps", "summary": "A first lesson.", "mark": ""}]


def _cat() -> cat_mod.Catalog:
    return cat_mod.parse(json.loads(cat_mod.CATALOG_PATH.read_text()))


def _files() -> dict[str, bytes]:
    return site.render(_cat(), NOW, videos=VIDEOS, lessons=LESSONS)


class _Links(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.links: list[str] = []

    def handle_starttag(self, tag, attrs):
        for k, v in attrs:
            if k in ("href", "src", "poster", "action") and v:
                self.links.append(v)


def _resolves(files: dict[str, bytes], page: str, link: str) -> bool:
    if link.startswith(("#", "mailto:", "tel:", "data:", "javascript:")):
        return True
    if re.match(r"^(?:[a-zA-Z][a-zA-Z0-9+.-]*:|//)", link):
        return True                                     # an outside address
    path = urlsplit(link).path
    if not path:
        return True
    if not path.startswith("/"):
        path = posixpath.join(posixpath.dirname("/" + page), path)
    path = posixpath.normpath(path)
    if link.split("#")[0].split("?")[0].endswith("/") or path == "/":
        path = path.rstrip("/") + "/index.html"
    rel = path.lstrip("/")
    if rel in files:
        return True
    # Served by the host, not by the store build: the landing, the API, previews.
    return path in ("/", "/index.html") or path.startswith(
        ("/public/", "/preview/", "/media/", "/thanks", "/fonts/", "/contact.js", "/agency"))


def test_every_page_and_asset_is_under_academy_except_the_two_root_files():
    files = _files()
    outside = sorted(p for p in files if not p.startswith("academy/"))
    assert outside == sorted(mount.ROOT_FILES)
    for need in ("academy/index.html", "academy/blog/index.html", "academy/join/index.html",
                 "academy/help/index.html", "academy/assets/style.css",
                 "academy/catalog.json", "academy/llms.txt", "academy/lessons/claude-0/index.html",
                 "academy/safe-ai-operator/index.html"):
        assert need in files, need


def test_every_internal_link_resolves_to_a_file_under_academy():
    files = _files()
    bad: list[str] = []
    for page, data in files.items():
        if not page.endswith(".html"):
            continue
        parser = _Links()
        parser.feed(data.decode())
        for link in parser.links:
            if not _resolves(files, page, link):
                bad.append(f"{page}: {link}")
            elif link.startswith("/") and not link.startswith("//"):
                top = link.split("/")[1].split("?")[0].split("#")[0]
                if top in mount.moved_tops([p.removeprefix("academy/") for p in files
                                            if p.startswith("academy/")]):
                    bad.append(f"{page}: bare store path {link}")
    assert not bad, bad[:20]


def test_no_page_links_the_old_relative_layout():
    for page, data in _files().items():
        if page.endswith(".html") and page != "404.html":
            assert "../academy/" not in data.decode(), page


def test_canonical_urls_and_the_sitemap_use_academy():
    files = _files()
    canon = {}
    for page, data in files.items():
        m = re.search(r'<link rel="canonical" href="([^"]+)"', data.decode()) \
            if page.endswith(".html") else None
        if m:
            canon[page] = m.group(1)
            assert m.group(1).startswith(f"https://{HOST}/academy/"), (page, m.group(1))
    assert canon
    sitemap = files["sitemap-academy.xml"].decode()
    locs = re.findall(r"<loc>([^<]+)</loc>", sitemap)
    assert locs and all(u.startswith(f"https://{HOST}/academy/") for u in locs), locs
    assert set(locs) <= set(canon.values()), set(locs) - set(canon.values())
    assert "https://agency.storefront.test/academy/" in locs


def test_no_absolute_bare_store_url_is_left_in_the_render():
    files = _files()
    tops = mount.moved_tops([p.removeprefix("academy/") for p in files if p.startswith("academy/")])
    pat = re.compile(r"https?://" + re.escape(HOST) + r"/([A-Za-z0-9][A-Za-z0-9._-]*)")
    bad = []
    for page, data in files.items():
        if not page.endswith(mount.TEXT_SUFFIXES) or page.startswith(mount.VERBATIM_DIRS):
            continue
        for m in pat.finditer(data.decode(errors="ignore")):
            if m.group(1) in tops:
                bad.append((page, m.group(0)))
    assert not bad, bad[:10]


def test_robots_names_the_store_sitemap_at_the_root():
    robots = (ROOT / "atlas/modules/agency/landing/robots.txt").read_text()
    assert "/sitemap-academy.xml" in robots
    assert "sitemap-academy.xml" in _files()


def test_the_relocation_is_idempotent_on_the_new_layout():
    once = _files()
    twice = mount.relocate(once, HOST)
    assert once == twice


def test_both_caddyfiles_redirect_every_old_store_path_and_keep_the_query():
    files = _files()
    tops = mount.moved_tops([p.removeprefix("academy/") for p in files
                             if p.startswith("academy/")])
    for name in ("Caddyfile.public", "Caddyfile.local"):
        text = (ROOT / name).read_text()
        block = re.search(r"@oldstore \{\n\s*path (.*?)\n\t\}", text, re.DOTALL)
        assert block, name
        listed = set(block.group(1).replace("\\\n", " ").split())
        assert "redir @oldstore /academy{uri} 308" in text, name
        # a root file the host serves itself is the landing's, not the store's
        landing = {"llms.txt", "robots.txt", "sitemap.xml", "contact.js", "fonts",
                   "thanks", "index.html", "mapfront-icon.png", "agency"}
        for top in sorted(tops - landing):
            assert f"/{top}" in listed or f"/{top}/*" in listed, (name, top)
        # never an API, preview, media, health or landing path
        for forbidden in ("/public", "/preview", "/media", "/healthz", "/thanks",
                          "/fonts", "/contact.js", "/robots.txt", "/sitemap.xml",
                          "/llms.txt", "/"):
            assert not any(p == forbidden or p.startswith(forbidden + "/")
                           for p in listed), (name, forbidden)


def test_the_redirect_runs_before_the_catch_all_store_handle():
    text = (ROOT / "Caddyfile.public").read_text()
    assert text.index("redir @oldstore") < text.index("\thandle {\n\t\troot * /srv/shop/current")


def test_public_store_urls_are_generated_under_academy(monkeypatch):
    monkeypatch.setenv("ACADEMY_PUBLIC_ORIGIN", "https://agency.storefront.test")
    assert store_origin.url() == "https://agency.storefront.test/academy/"
    assert store_origin.url("join/").endswith("/academy/join/")
    assert _cat().store_url.endswith("/academy")
