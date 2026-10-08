"""The Caddyfile's Mapfront blocks and the compose mounts behind them.

Structural tests always run. `caddy validate` -- the same command
bootstrap/deploy/deploy.sh runs before it copies a new Caddyfile into place
-- runs when docker and the caddy:2-alpine image are already on the machine
(it never pulls).
"""
from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

from atlas.modules.agency import hosting

ROOT = Path(__file__).resolve().parents[1]
CADDYFILE = (ROOT / "Caddyfile").read_text()
COMPOSE = (ROOT / "docker-compose.yml").read_text()


def _block(name: str) -> str:
    """The body of the top-level site block `name { ... }`."""
    m = re.search(rf"^{re.escape(name)} \{{\n(.*?)\n\}}$", CADDYFILE,
                  re.DOTALL | re.MULTILINE)
    assert m, f"no site block {name!r}"
    return m.group(1)


def _mapfront_text() -> str:
    """Everything from the apex block on, comments dropped."""
    tail = CADDYFILE[CADDYFILE.index("\nmapfront.io {"):]
    return "\n".join(line for line in tail.splitlines()
                     if not line.lstrip().startswith("#"))


def test_the_ask_endpoint_is_the_global_on_demand_guard():
    glob = CADDYFILE[: CADDYFILE.index("\natlas.example.com {")]
    assert "on_demand_tls {\n\t\task http://api:8080/internal/tls-ask\n\t}" in glob
    assert CADDYFILE.count("on_demand_tls") == 1


@pytest.mark.parametrize("name", ["mapfront.io", "www.mapfront.io", "*.mapfront.io"])
def test_every_mapfront_block_is_on_demand(name):
    assert "\ttls {\n\t\ton_demand\n\t}" in _block(name)


def test_atlas_block_is_not_on_demand_and_mapfront_comes_after_it():
    atlas = _block("atlas.example.com")
    assert "on_demand" not in atlas and "mapfront" not in atlas
    assert CADDYFILE.index("\natlas.example.com {") < CADDYFILE.index("\nmapfront.io {")
    # The atlas block's own public routes are still there, unchanged in kind.
    for route in ("handle /webhooks/*", "handle /healthz", "handle /media/*",
                  "handle /deliver/*", "handle /hooks/tradingview",
                  'respond "not here" 404'):
        assert route in atlas


def test_mapfront_is_static_only():
    text = _mapfront_text()
    for forbidden in ("reverse_proxy", "api:8080", "browse", "php_fastcgi",
                      "/internal", "import ", "{$"):
        assert forbidden not in text, forbidden


def test_templates_run_on_the_landing_block_only():
    """Client HTML is partly model output; `templates` there could print any
    variable in Caddy's environment."""
    assert "templates" in _block("mapfront.io")
    assert "templates" not in _block("*.mapfront.io")
    assert "templates" not in _block("atlas.example.com")


def test_client_sites_serve_the_slug_link_and_hide_dotfiles():
    sites = _block("*.mapfront.io")
    assert "root * /srv/sites/{labels.2}" in sites
    assert "file_server {\n\t\thide .*\n\t}" in sites
    assert "@hidden path */.*" in sites and "respond @hidden" in sites
    host_rule = re.search(r"@badhost not header_regexp Host (\S+)", sites)
    assert host_rule
    rx = re.compile(host_rule.group(1))
    assert rx.match("joes-7.mapfront.io") and rx.match("joes-7.mapfront.io:443")
    for bad in ("-joes.mapfront.io", "a.b.mapfront.io", "JOES.mapfront.io",
                "x" * 41 + ".mapfront.io", "joes.mapfront.io.evil"):
        assert not rx.match(bad), bad
    assert "handle_errors" in sites and "/404.html" in sites


def test_www_redirects_to_the_apex():
    assert "redir https://mapfront.io{uri} permanent" in _block("www.mapfront.io")


def test_the_hardcoded_names_agree_with_the_default_sites_domain(monkeypatch):
    monkeypatch.delenv("AGENCY_SITES_DOMAIN", raising=False)
    assert hosting.sites_domain() == "mapfront.io"
    assert "www" in hosting.RESERVED


def test_compose_mounts_sites_and_landing_read_only_into_caddy():
    caddy = COMPOSE[COMPOSE.index("\n  caddy:"):]
    assert "- ./data/app/sites:/srv/sites:ro" in caddy
    assert "- ./atlas/modules/agency/landing:/srv/landing:ro" in caddy
    api = COMPOSE[COMPOSE.index("\n  api:"):COMPOSE.index("\n  caddy:")]
    assert "- ./data/app:/app/data" in api      # the api writes /app/data/sites


def test_the_publish_root_default_lands_in_the_mounted_directory():
    """api cwd is /app, so data/sites is /app/data/sites == ./data/app/sites."""
    assert str(hosting.PUBLISH_ROOT) in ("data/sites", "/app/data/sites")


def _caddy_image() -> bool:
    if not shutil.which("docker"):
        return False
    try:
        return subprocess.run(["docker", "image", "inspect", "caddy:2-alpine"],
                              capture_output=True, timeout=20, check=False).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


@pytest.mark.skipif(not _caddy_image(), reason="docker with caddy:2-alpine not present")
def test_caddy_validates_the_caddyfile():
    proc = subprocess.run(
        ["docker", "run", "--rm", "-v", f"{ROOT / 'Caddyfile'}:/etc/caddy/Caddyfile:ro",
         "caddy:2-alpine", "caddy", "validate", "--config", "/etc/caddy/Caddyfile",
         "--adapter", "caddyfile"],
        capture_output=True, text=True, timeout=120, check=False)
    assert proc.returncode == 0, proc.stderr[-2000:]
    assert "Valid configuration" in proc.stdout + proc.stderr
