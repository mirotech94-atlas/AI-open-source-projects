"""Client demo hosting: the Caddy snippet that serves a demo, its compose
mount, and the two env vars behind `agency.deploy_demo`.

Pure file reads, no network. A demo is a static build dropped into the
intake directory, published by `agency.deploy_demo` (Tier ASK) under
`AGENCY_DEMO_PUBLISH_DIR` (= host `./data/app/demos`) and served by Caddy
from `/srv/demos/<slug>`, read-only, one explicit site block per hostname.
"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CADDYFILE = (ROOT / "Caddyfile").read_text()
COMPOSE = (ROOT / "docker-compose.yml").read_text()
ENV_EXAMPLE = (ROOT / ".env.example").read_text()
CONFIGURE = (ROOT / "CONFIGURE.md").read_text()


def _top_level(header: str) -> str:
    """The body of the top-level block opened by `header {`."""
    m = re.search(rf"^{re.escape(header)} \{{\n(.*?)\n\}}$", CADDYFILE,
                  re.DOTALL | re.MULTILINE)
    assert m, f"no top-level block {header!r}"
    return m.group(1)


def _uncommented(text: str) -> str:
    return "\n".join(line for line in text.splitlines()
                     if not line.lstrip().startswith("#"))


def test_the_demo_snippet_serves_files_only():
    snippet = _uncommented(_top_level("(demo_site)"))
    assert "root * /srv/demos/{args[0]}" in snippet
    assert "file_server" in snippet and "browse" not in snippet
    assert "hide .*" in snippet
    assert "@hidden path */.*" in snippet and "respond @hidden" in snippet
    assert 'X-Robots-Tag "noindex, nofollow"' in snippet
    for header in ('Strict-Transport-Security "max-age=31536000"',
                   'X-Content-Type-Options "nosniff"',
                   'X-Frame-Options "SAMEORIGIN"',
                   'Referrer-Policy "strict-origin-when-cross-origin"',
                   'Permissions-Policy "camera=(), microphone=(), geolocation=()"'):
        assert header in snippet, header
    for forbidden in ("on_demand", "reverse_proxy", "api:8080", "templates",
                      "php_fastcgi", "{$"):
        assert forbidden not in snippet, forbidden


def test_a_missing_demo_is_a_noindex_404():
    """Before any content is published the hostname answers 404 "not here",
    and the error response carries the noindex header too."""
    snippet = _top_level("(demo_site)")
    errors = snippet[snippet.index("handle_errors"):]
    assert 'respond "not here" 404' in errors
    assert 'X-Robots-Tag "noindex, nofollow"' in errors


def test_the_royal_demo_block_imports_the_snippet():
    block = _uncommented(_top_level("client-site.example.com"))
    assert block.strip() == "import demo_site examplehair"


def test_the_snippet_is_defined_before_it_is_used():
    """Caddy reads a snippet before an import can use it. (The old mapfront
    section is gone from the main Caddyfile, tests/test_agency_caddy.py.)"""
    snippet_at = CADDYFILE.index("\n(demo_site) {")
    block_at = CADDYFILE.index("\nexamplehair.example.com {")
    assert CADDYFILE.index("\natlas.example.com {") < snippet_at < block_at


def test_compose_mounts_the_demos_read_only_into_caddy():
    caddy = COMPOSE[COMPOSE.index("\n  caddy:"):]
    assert "- ./data/app/demos:/srv/demos:ro" in caddy


def test_env_example_names_the_demo_dirs_and_configure_documents_them():
    assert re.search(r"^AGENCY_DEMO_PUBLISH_DIR=data/demos$", ENV_EXAMPLE, re.MULTILINE)
    assert re.search(r"^AGENCY_DEMO_INTAKE_DIR=data/demos-intake$", ENV_EXAMPLE,
                     re.MULTILINE)
    assert "`AGENCY_DEMO_PUBLISH_DIR`" in CONFIGURE
    assert "`AGENCY_DEMO_INTAKE_DIR`" in CONFIGURE


def test_deploy_site_is_documented_as_tier_auto():
    line = next(row for row in CONFIGURE.splitlines()
                if row.startswith("| `AGENCY_DEPLOY_HOST=local`"))
    assert "`agency.deploy_site` (Tier AUTO)" in line
    hosting_doc = (ROOT / "atlas/modules/agency/hosting.py").read_text()
    assert "`agency.deploy_site` (Tier AUTO)" in hosting_doc.split('"""')[1]
