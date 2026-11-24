"""A secret in the path is never written to an access log (audit #9).

/media/{token}, /deliver/{checkout_session} and /webhooks/twilio/audio/{token}
are gated by nothing but the path. Caddy skips logging them; uvicorn's access
log, which still sees every request, has the secret redacted.
"""
from __future__ import annotations

import logging
import pathlib
import re

import pytest

from atlas.api import main

CADDYFILE = pathlib.Path(main.__file__).resolve().parents[2] / "Caddyfile"


def _skipped_paths() -> set[str]:
    text = CADDYFILE.read_text()
    skipped = set(re.findall(r"^\s*log_skip\s+@(\w+)\s*$", text, re.MULTILINE))
    paths: set[str] = set()
    for name in skipped:
        m = re.search(rf"^\s*@{name}\s+path\s+(.+)$", text, re.MULTILINE)
        assert m, f"log_skip names @{name}, which has no path matcher"
        paths.update(m.group(1).split())
    return paths


@pytest.mark.parametrize("path", ["/media/*", "/deliver/*", "/webhooks/twilio/audio/*",
                                  "/public/academy/access", "/public/academy/file/*"])
def test_caddy_skips_logging_every_secret_path(path):
    assert path in _skipped_paths()


def test_caddy_still_logs_everything_else():
    """Skipping is scoped to the secret routes, not the whole log."""
    assert "/webhooks/*" not in _skipped_paths()
    assert "/*" not in _skipped_paths()
    assert re.search(r"^\s*log\s*\{", CADDYFILE.read_text(), re.MULTILINE)


def test_the_security_headers_are_unchanged():
    text = CADDYFILE.read_text()
    assert 'Strict-Transport-Security "max-age=31536000; includeSubDomains; preload"' \
        in text
    assert "Content-Security-Policy \"default-src 'none';" in text
    assert 'X-Frame-Options "DENY"' in text


def _access_record(path: str) -> logging.LogRecord:
    # uvicorn's own access-log call shape.
    return logging.LogRecord(
        "uvicorn.access", logging.INFO, __file__, 1,
        '%s - "%s %s HTTP/%s" %d', ("100.84.1.7:5000", "GET", path, "1.1", 200),
        None)


@pytest.mark.parametrize("path,shown", [
    ("/media/tok_abc123", "/media/<token>"),
    ("/deliver/cs_live_a1B2c3D4e5F6g7", "/deliver/<token>"),
    ("/webhooks/twilio/audio/xyz", "/webhooks/twilio/audio/<token>"),
    ("/media/tok_abc123?x=1", "/media/<token>?x=1"),
    ("/public/academy/file/v1.kit.1790000000.cs_live_a1B2c3D4e5F6g7.ab12",
     "/public/academy/file/<token>"),
])
def test_uvicorn_access_log_redacts_the_secret(path, shown):
    record = _access_record(path)
    assert main._NoAudioTokens().filter(record) is True
    line = record.getMessage()
    assert shown in line
    secret = path.split("/")[-1].split("?")[0]
    assert secret not in line


@pytest.mark.parametrize("path", ["/api/session", "/healthz", "/api/media/list"])
def test_uvicorn_access_log_leaves_other_paths_alone(path):
    record = _access_record(path)
    main._NoAudioTokens().filter(record)
    assert path in record.getMessage()


def test_uvicorn_access_log_redacts_the_academy_checkout_session():
    record = _access_record("/public/academy/access?session_id=cs_live_a1B2c3D4e5F6g7")
    main._NoAudioTokens().filter(record)
    line = record.getMessage()
    assert "/public/academy/access?session_id=<token>" in line
    assert "a1B2c3D4e5F6g7" not in line
