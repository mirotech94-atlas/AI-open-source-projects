"""The one-host layout of https://agency.storefront.test and the Caddyfiles behind it.

Decision 2026-11-14: every mapfront-domain name is gone. ONE host serves
  /                 the agency landing (atlas/modules/agency/landing, /srv/landing)
  /thanks/          the landing's confirmation page
  /academy/         the store home (the rest of the store keeps its old paths)
  /preview/<slug>/  a prospect's preview site (/srv/sites/<slug>, sandboxed)
Structural tests always run. `caddy validate` runs when docker and the
caddy:2-alpine image are already on the machine (it never pulls).
"""
from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
MAIN = (ROOT / "Caddyfile").read_text()
PUBLIC = (ROOT / "Caddyfile.public").read_text()
LOCAL = (ROOT / "Caddyfile.local").read_text()
COMPOSE = (ROOT / "docker-compose.yml").read_text()
LOCAL_COMPOSE = (ROOT / "docker-compose.local.yml").read_text()
OLD = "mapfront" + ".io"          # spelled in two parts so this file stays clean


def _code(text: str) -> str:
    return "\n".join(line for line in text.splitlines()
                     if not line.lstrip().startswith("#"))


def _handle(text: str, header: str) -> str:
    """The body of the tab-indented `handle ...` block that starts with `header`."""
    m = re.search(rf"^\thandle {re.escape(header)} \{{\n(.*?)\n\t\}}$", text,
                  re.DOTALL | re.MULTILINE)
    assert m, f"no handle block {header!r}"
    return m.group(1)


def _services(compose: str, name: str) -> str:
    m = re.search(rf"^  {re.escape(name)}:\n(.*?)(?=^  \S|^\S|\Z)", compose,
                  re.DOTALL | re.MULTILINE)
    assert m, f"no service {name}"
    return m.group(1)


# ------------------------------------------------------------ the main Caddyfile

def test_no_file_names_the_old_domain():
    for name in ("Caddyfile", "Caddyfile.public", "Caddyfile.local",
                 "docker-compose.yml", "docker-compose.local.yml"):
        assert OLD not in (ROOT / name).read_text(), name


def test_the_main_caddyfile_has_no_on_demand_tls_left():
    code = _code(MAIN)
    assert "on_demand" not in code and "tls-ask" not in code
    assert "reverse_proxy api:8080" in code            # the atlas block is untouched
    for route in ("handle /webhooks/*", "handle /healthz", "handle /media/*",
                  "handle /deliver/*", "handle /hooks/tradingview"):
        assert route in MAIN
    assert "\natlas.storefront.test {" in MAIN and "\nexamplesalon.storefront.test {" in MAIN
    assert "/srv/landing" not in code and "/srv/sites" not in code


def test_the_dns_note_is_gone():
    assert not (ROOT / "bootstrap" / "mapfront-dns.md").exists()
    for name in ("Caddyfile", "Caddyfile.public", "Caddyfile.local",
                 "docker-compose.yml", "docker-compose.local.yml"):
        assert "mapfront-dns" not in (ROOT / name).read_text(), name


def test_compose_no_longer_mounts_the_landing_or_sites_into_the_main_caddy():
    caddy = _services(COMPOSE, "caddy")
    assert "/srv/landing" not in caddy and "/srv/sites" not in caddy
    assert "- ./data/app/demos:/srv/demos:ro" in caddy


# ------------------------------------------------------- Caddyfile.public layout

def test_the_landing_is_served_at_the_root_with_its_strict_csp_and_templates():
    body = _handle(PUBLIC, "@landing")
    assert "root * /srv/landing" in body and "\n\t\ttemplates" in body
    csp = re.search(r'Content-Security-Policy "([^"]+)"', body)
    assert csp and csp.group(1) == (
        "default-src 'none'; style-src 'unsafe-inline'; script-src 'self'; "
        "connect-src 'self'; img-src 'self' data:; font-src 'self'; base-uri 'none'; "
        "form-action 'self'; frame-ancestors 'none'")
    assert 'X-Frame-Options "DENY"' in body
    assert "file_server" in body and "browse" not in body
    # exactly the landing's own paths; nothing of the store matches
    matcher = re.search(r"@landing \{\n(.*?)\n\t\}", PUBLIC, re.DOTALL).group(1)
    for path in ("/agency", "/agency/", "/thanks", "/thanks/*", "/robots.txt", "/sitemap.xml",
                 "/llms.txt", "/mapfront-icon.png", "/contact.js", "/fonts/*"):
        assert path in matcher.split(), path
    for store in ("/academy", "/join", "/assets", "/catalog.json", "/help"):
        assert store not in matcher.split(), store


def test_templates_run_on_the_landing_only():
    """Client HTML is partly model output; `templates` there could print any
    variable in Caddy's environment."""
    code = _code(PUBLIC)
    assert code.count("templates") == 1
    assert "templates" in _handle(PUBLIC, "@landing")
    assert "templates" not in _handle(PUBLIC, "@preview")


def test_the_store_is_the_fallback_so_every_old_path_still_resolves():
    # the last handle is the store: no matcher, root /srv/shop/current
    last = PUBLIC[PUBLIC.rindex("\thandle {"):]
    assert "root * /srv/shop/current" in last and "file_server" in last
    # the landing and preview handles come before it
    assert PUBLIC.index("handle @landing") < PUBLIC.rindex("\thandle {")
    assert PUBLIC.index("handle @preview") < PUBLIC.rindex("\thandle {")
    # nothing in the landing block can shadow a store directory
    assert "/academy" not in re.search(r"@landing \{\n(.*?)\n\t\}", PUBLIC,
                                       re.DOTALL).group(1)


def test_the_webhooks_and_public_api_routes_are_unchanged():
    assert "handle /public/academy/*" in PUBLIC
    assert "path /webhooks/stripe-academy" in PUBLIC
    assert "path /webhooks/twilio/whatsapp" in PUBLIC
    assert r"path_regexp clipv ^/media/clipv\.[A-Za-z0-9_-]{43}$" in PUBLIC
    assert "handle /public/* {\n\t\trespond \"not found\" 404" in PUBLIC
    # the API routes come before the landing and preview handles
    for needle in ("handle /public/academy/*", "handle @stripe", "handle @whatsapp",
                   "handle @clipvideo"):
        assert PUBLIC.index(needle) < PUBLIC.index("handle @landing"), needle


def test_the_preview_handle_serves_one_slug_dir_read_only_and_sandboxed():
    body = _handle(PUBLIC, "@preview")
    assert "root * /srv/sites/{re.preview.1}" in body
    assert "file_server {\n\t\t\thide .*\n\t\t}" in body and "browse" not in body
    assert "@hidden path */.*" in body and "respond @hidden" in body
    assert 'Content-Security-Policy "sandbox allow-scripts allow-forms allow-popups"' in body
    assert 'X-Frame-Options "SAMEORIGIN"' in body
    assert "reverse_proxy" not in body
    m = re.search(r"@preview path_regexp preview (\S+)", PUBLIC)
    assert m
    rx = re.compile(m.group(1))
    assert rx.match("/preview/joes-7/") and rx.match("/preview/joes-7/menu/a.css")
    assert rx.match("/preview/joes-7/").group(1) == "joes-7"
    for bad in ("/preview/", "/preview//x", "/preview/-joes/", "/preview/JOES/",
                "/preview/a.b/", "/preview/" + "x" * 41 + "/", "/preview/../etc/",
                "/preview/joes-7", "/previews/joes/", "/x/preview/joes/"):
        assert not rx.match(bad), bad
    # a slug with no trailing slash is sent to the directory form
    assert "redir @slashless /preview/{re.slashless.1}/ 308" in PUBLIC
    assert 'respond @hidden "not here" 404' in body


def test_the_preview_csp_is_not_the_landings_and_has_no_same_origin_token():
    body = _handle(PUBLIC, "@preview")
    csp = re.search(r'Content-Security-Policy "([^"]+)"', body).group(1)
    assert "allow-same-origin" not in csp
    assert csp.split()[0] == "sandbox"


def test_the_public_listener_still_misdirects_other_hosts():
    assert 'respond "misdirected" 421' in PUBLIC
    assert "@wrong not host agency.storefront.test" in PUBLIC


def test_a_comment_documents_the_merged_robots_sitemap_and_llms():
    comments = "\n".join(line for line in PUBLIC.splitlines() if line.lstrip().startswith("#"))
    for word in ("robots.txt", "sitemap.xml", "llms.txt", "sitemap-academy.xml",
                 "/academy/llms.txt"):
        assert word in comments, word


# ---------------------------------------------------------- compose mounts

def test_shop_public_mounts_the_landing_and_the_sites_read_only():
    svc = _services(LOCAL_COMPOSE, "shop-public")
    assert "- ./atlas/modules/agency/landing:/srv/landing:ro" in svc
    assert "- ./data/app/sites:/srv/sites:ro" in svc
    assert "- ./data/app/academy/build:/srv/shop:ro" in svc


def test_the_local_store_hosts_also_serve_the_landing_at_the_root():
    """Caddyfile.local has no root index any more, so it serves the landing too."""
    assert "root * /srv/landing" in LOCAL and "handle @landing" in LOCAL
    for name in ("shop", "shop-netbird"):
        assert "- ./atlas/modules/agency/landing:/srv/landing:ro" in _services(
            LOCAL_COMPOSE, name), name


# ----------------------------------------------------------- caddy validate

def _caddy_image() -> bool:
    if not shutil.which("docker"):
        return False
    try:
        return subprocess.run(["docker", "image", "inspect", "caddy:2-alpine"],
                              capture_output=True, timeout=20, check=False).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


@pytest.mark.skipif(not _caddy_image(), reason="docker with caddy:2-alpine not present")
@pytest.mark.parametrize("name", ["Caddyfile", "Caddyfile.public", "Caddyfile.local"])
def test_caddy_validates_the_caddyfile(name):
    proc = subprocess.run(
        ["docker", "run", "--rm", "-v", f"{ROOT / name}:/etc/caddy/Caddyfile:ro",
         "caddy:2-alpine", "caddy", "validate", "--config", "/etc/caddy/Caddyfile",
         "--adapter", "caddyfile"],
        capture_output=True, text=True, timeout=120, check=False)
    assert proc.returncode == 0, proc.stderr[-2000:]
    assert "Valid configuration" in proc.stdout + proc.stderr
