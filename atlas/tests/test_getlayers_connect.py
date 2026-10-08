"""bootstrap/getlayers-connect.py -- its pure parts, no network, no ssh."""
from __future__ import annotations

import base64
import hashlib
import importlib.util
import pathlib
import re
import urllib.parse

import pytest

SCRIPT = (pathlib.Path(__file__).resolve().parent.parent
          / "bootstrap" / "getlayers-connect.py")
_spec = importlib.util.spec_from_file_location("getlayers_connect", SCRIPT)
assert _spec and _spec.loader
gc = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(gc)


def test_pkce_pair_is_s256_of_a_valid_verifier():
    verifier, challenge = gc.pkce_pair()
    assert 43 <= len(verifier) <= 128
    assert re.fullmatch(r"[A-Za-z0-9\-._~]+", verifier)
    expected = base64.urlsafe_b64encode(
        hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    assert challenge == expected
    assert "=" not in challenge
    assert gc.pkce_pair()[0] != verifier


def test_well_known_urls_insert_before_the_path():
    assert gc.resource_metadata_url("https://mcp.getlayers.ai/mcp") == (
        "https://mcp.getlayers.ai/.well-known/oauth-protected-resource/mcp")
    assert gc.as_metadata_url("https://mcp.getlayers.ai") == (
        "https://mcp.getlayers.ai/.well-known/oauth-authorization-server")
    assert gc.as_metadata_url("https://as.example/tenant1/") == (
        "https://as.example/.well-known/oauth-authorization-server/tenant1")


def test_authorize_url_carries_pkce_state_and_resource():
    url = gc.authorize_url(
        "https://mcp.getlayers.ai/authorize", client_id="cid",
        redirect_uri="http://127.0.0.1:5555/callback", challenge="ch",
        state="st", resource="https://mcp.getlayers.ai/mcp", scope="mcp")
    parts = urllib.parse.urlsplit(url)
    assert parts.netloc == "mcp.getlayers.ai" and parts.path == "/authorize"
    q = dict(urllib.parse.parse_qsl(parts.query))
    assert q == {"response_type": "code", "client_id": "cid",
                 "redirect_uri": "http://127.0.0.1:5555/callback",
                 "code_challenge": "ch", "code_challenge_method": "S256",
                 "state": "st", "resource": "https://mcp.getlayers.ai/mcp",
                 "scope": "mcp"}


def test_env_lines_format_skip_empties_and_refuse_newlines():
    assert gc.env_lines({"GETLAYERS_OAUTH_CLIENT_ID": "cid",
                         "GETLAYERS_OAUTH_REFRESH_TOKEN": "u:g:$ecret",
                         "GETLAYERS_OAUTH_CLIENT_SECRET": None,
                         "GETLAYERS_OAUTH_TOKEN_URL": "https://mcp.getlayers.ai/token"}) \
        == ("GETLAYERS_OAUTH_CLIENT_ID=cid\n"
            "GETLAYERS_OAUTH_REFRESH_TOKEN=u:g:$ecret\n"
            "GETLAYERS_OAUTH_TOKEN_URL=https://mcp.getlayers.ai/token\n")
    with pytest.raises(gc.ConnectError) as err:
        gc.env_lines({"GETLAYERS_OAUTH_REFRESH_TOKEN": "abc\nX=1"})
    assert "abc" not in str(err.value)


def test_parse_callback_checks_state_and_issuer():
    ok = "/callback?code=the-code&state=s1&iss=https%3A%2F%2Fmcp.getlayers.ai"
    assert gc.parse_callback(ok, expected_state="s1",
                             issuer="https://mcp.getlayers.ai") == "the-code"
    with pytest.raises(gc.ConnectError, match="state mismatch"):
        gc.parse_callback(ok, expected_state="s2", issuer=None)
    with pytest.raises(gc.ConnectError, match="different issuer"):
        gc.parse_callback(ok, expected_state="s1", issuer="https://evil.example")
    with pytest.raises(gc.ConnectError, match="access_denied"):
        gc.parse_callback("/callback?error=access_denied&state=s1",
                          expected_state="s1", issuer=None)
    with pytest.raises(gc.ConnectError, match="no authorization code"):
        gc.parse_callback("/callback?state=s1", expected_state="s1", issuer=None)


def test_dry_run_stops_before_registering(monkeypatch, capsys):
    calls: list[str] = []

    def fake_request(url, *, data=None, headers=None):
        calls.append(url)
        if "oauth-protected-resource" in url:
            return 200, {"resource": "https://mcp.getlayers.ai/mcp",
                         "authorization_servers": ["https://mcp.getlayers.ai"]}
        return 200, {"issuer": "https://mcp.getlayers.ai",
                     "authorization_endpoint": "https://mcp.getlayers.ai/authorize",
                     "token_endpoint": "https://mcp.getlayers.ai/token",
                     "registration_endpoint": "https://mcp.getlayers.ai/register",
                     "scopes_supported": ["mcp"],
                     "code_challenge_methods_supported": ["S256"]}

    monkeypatch.setattr(gc, "_request", fake_request)
    assert gc.main(["--dry-run"]) == 0
    out = capsys.readouterr().out
    assert "https://mcp.getlayers.ai/token" in out
    assert len(calls) == 2
    assert not any("register" in c for c in calls)


def test_a_connect_prints_one_line_and_no_token(monkeypatch, capsys):
    endpoints = {"resource": "https://mcp.getlayers.ai/mcp",
                 "issuer": "https://mcp.getlayers.ai",
                 "authorization_endpoint": "https://mcp.getlayers.ai/authorize",
                 "token_endpoint": "https://mcp.getlayers.ai/token",
                 "registration_endpoint": "https://mcp.getlayers.ai/register",
                 "scopes": ["mcp"], "auth_methods": ["none"], "iss_param": True}
    pushed: list[str] = []
    monkeypatch.setattr(gc, "discover", lambda url: endpoints)
    monkeypatch.setattr(gc, "register", lambda e, r: {"client_id": "cid-1"})
    monkeypatch.setattr(gc, "_wait_for_code", lambda server, **kw: "code-1")
    monkeypatch.setattr(gc, "exchange", lambda *a, **kw: {
        "access_token": "AT-secret-xyz", "refresh_token": "RT-secret-xyz",
        "expires_in": 3600})
    monkeypatch.setattr(gc, "push_to_server", lambda lines, host: pushed.append(lines))
    assert gc.main(["--no-browser"]) == 0
    out, err = capsys.readouterr()
    assert out == ("connected; access token expires in 60 min; "
                   "refresh token stored\n")
    assert "secret-xyz" not in out + err
    assert pushed == [("GETLAYERS_OAUTH_CLIENT_ID=cid-1\n"
                       "GETLAYERS_OAUTH_REFRESH_TOKEN=RT-secret-xyz\n"
                       "GETLAYERS_OAUTH_TOKEN_URL=https://mcp.getlayers.ai/token\n")]


def test_push_sends_values_on_stdin_never_argv(monkeypatch):
    runs: list[dict] = []

    class Done:
        returncode = 0

    def fake_run(argv, **kw):
        runs.append({"argv": argv, **kw})
        return Done()

    monkeypatch.setattr(gc.subprocess, "run", fake_run)
    gc.push_to_server("GETLAYERS_OAUTH_REFRESH_TOKEN=sekrit\n", "root@host")
    assert runs[0]["input"] == "GETLAYERS_OAUTH_REFRESH_TOKEN=sekrit\n"
    assert all("sekrit" not in " ".join(r["argv"]) for r in runs)
    assert "envlock.py" in runs[0]["argv"][-1]
    assert "docker compose up -d api" in runs[1]["argv"][-1]


def test_the_callback_handler_does_not_log_the_request_line():
    assert gc._Callback.log_message is not \
        gc.http.server.BaseHTTPRequestHandler.log_message
