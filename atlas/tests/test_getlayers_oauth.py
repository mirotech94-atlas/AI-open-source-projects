"""GetLayers OAuth on the server: `_getlayers_oauth` + `McpClient` + the
`getlayers_auth` check. No network: `httpx.AsyncClient.post/get` are faked,
routing the token endpoint and the MCP endpoint to separate handlers."""
from __future__ import annotations

import asyncio
import json
import logging
import os
import stat

import httpx
import pytest

from atlas.kernel.contracts import Health
from atlas.modules import _getlayers_oauth as oauth
from atlas.modules import agency
from atlas.modules._mcp import McpClient

MCP = "https://mcp.getlayers.ai/mcp"
TOKEN_URL = "https://mcp.getlayers.ai/token"
ENV_REFRESH = "rt-ENV-never-print-4f1c"
ROTATED = "rt-ROTATED-never-print-9a2b"
ACCESS_1 = "at-FIRST-never-print-11aa"
ACCESS_2 = "at-SECOND-never-print-22bb"
SECRETS = (ENV_REFRESH, ROTATED, ACCESS_1, ACCESS_2)


class Resp:
    def __init__(self, status: int = 200, body: object = None,
                 headers: dict | None = None) -> None:
        self.status_code = status
        self.text = json.dumps(body) if body is not None else ""
        self.headers = {"content-type": "application/json", **(headers or {})}

    def json(self):
        return json.loads(self.text)


def _ok_rpc(body: dict) -> Resp:
    if "id" not in body:                      # notifications/initialized
        return Resp(202)
    return Resp(200, {"jsonrpc": "2.0", "id": body["id"],
                      "result": {"content": [{"type": "text", "text": "hi"}]}})


class Fake:
    """Token endpoint answers from `token_replies` in order; MCP accepts
    only bearers in `accepted`."""

    def __init__(self, monkeypatch, token_replies: list[Resp],
                 accepted: set[str] | None = None) -> None:
        self.token_replies = list(token_replies)
        self.accepted = accepted if accepted is not None else {ACCESS_1, ACCESS_2}
        self.token_posts: list[dict] = []
        self.mcp_bearers: list[str | None] = []
        fake = self

        async def post(client, url, *, headers=None, json=None, data=None, **kw):
            if url == TOKEN_URL:
                fake.token_posts.append(dict(data or {}))
                await asyncio.sleep(0)
                return fake.token_replies.pop(0)
            auth = (headers or {}).get("Authorization", "")
            bearer = auth[len("Bearer "):] if auth.startswith("Bearer ") else None
            if json is not None and "id" in json:
                fake.mcp_bearers.append(bearer)
            if bearer not in fake.accepted:
                return Resp(401, headers={"WWW-Authenticate": 'Bearer realm="OAuth"'})
            return _ok_rpc(json)

        monkeypatch.setattr(httpx.AsyncClient, "post", post)


def _tokens(access: str, refresh: str | None = None, expires: int = 3600) -> Resp:
    body: dict = {"access_token": access, "token_type": "bearer",
                  "expires_in": expires}
    if refresh:
        body["refresh_token"] = refresh
    return Resp(200, body)


@pytest.fixture(autouse=True)
def _env(monkeypatch, tmp_path):
    oauth.reset()
    for name in ("GETLAYERS_TOKEN", "GETLAYERS_MCP_URL", oauth.CLIENT_ID_ENV,
                 oauth.REFRESH_TOKEN_ENV, oauth.CLIENT_SECRET_ENV):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv(oauth.TOKEN_URL_ENV, TOKEN_URL)
    monkeypatch.setenv(oauth.STORE_ENV, str(tmp_path / "secrets" / "getlayers.json"))
    yield
    oauth.reset()


@pytest.fixture
def oauth_env(monkeypatch):
    monkeypatch.setenv(oauth.CLIENT_ID_ENV, "client-abc")
    monkeypatch.setenv(oauth.REFRESH_TOKEN_ENV, ENV_REFRESH)


def _no_secret_anywhere(caplog, *texts: object) -> None:
    blob = "\n".join(r.getMessage() for r in caplog.records)
    blob += "\n".join(str(t) for t in texts)
    for secret in SECRETS:
        assert secret not in blob


# ----------------------------------------------------------- refresh flow

async def test_refresh_flow_gets_an_access_token_and_sends_it(
        monkeypatch, oauth_env, caplog):
    caplog.set_level(logging.DEBUG)
    fake = Fake(monkeypatch, [_tokens(ACCESS_1)])
    client = McpClient()
    assert client.uses_oauth
    result, reason = await client.call("getlayers_search", {"query": "plumber"})
    assert (result, reason) == ("hi", None)
    assert fake.mcp_bearers == [ACCESS_1]
    [form] = fake.token_posts
    assert form["grant_type"] == "refresh_token"
    assert form["refresh_token"] == ENV_REFRESH
    assert form["client_id"] == "client-abc"
    assert form["resource"] == MCP
    assert "client_secret" not in form
    assert "access token refreshed" in caplog.text     # records were captured
    _no_secret_anywhere(caplog, result, reason)


async def test_a_client_secret_is_sent_when_registration_issued_one(
        monkeypatch, oauth_env):
    monkeypatch.setenv(oauth.CLIENT_SECRET_ENV, "cs-xyz")
    fake = Fake(monkeypatch, [_tokens(ACCESS_1)])
    await McpClient().call("getlayers_search", {})
    assert fake.token_posts[0]["client_secret"] == "cs-xyz"


async def test_the_access_token_is_cached_across_clients_until_expiry(
        monkeypatch, oauth_env):
    fake = Fake(monkeypatch, [_tokens(ACCESS_1, expires=3600),
                              _tokens(ACCESS_2, expires=3600)])
    await McpClient().call("a", {})
    await McpClient().call("b", {})
    assert len(fake.token_posts) == 1
    assert fake.mcp_bearers == [ACCESS_1, ACCESS_1]

    # Within REFRESH_MARGIN of expiry it is refreshed before use.
    cached = oauth.source(MCP)._cached
    assert cached is not None
    cached.expires_at = oauth.time.monotonic() + oauth.REFRESH_MARGIN - 1
    await McpClient().call("c", {})
    assert len(fake.token_posts) == 2
    assert fake.mcp_bearers[-1] == ACCESS_2


async def test_concurrent_cold_calls_refresh_exactly_once(monkeypatch, oauth_env):
    fake = Fake(monkeypatch, [_tokens(ACCESS_1)])
    results = await asyncio.gather(*(McpClient().call("x", {}) for _ in range(5)))
    assert all(reason is None for _r, reason in results)
    assert len(fake.token_posts) == 1


async def test_a_401_refreshes_once_and_retries(monkeypatch, oauth_env, caplog):
    caplog.set_level(logging.DEBUG)
    fake = Fake(monkeypatch, [_tokens(ACCESS_1), _tokens(ACCESS_2)],
                accepted={ACCESS_2})
    result, reason = await McpClient().call("x", {})
    assert (result, reason) == ("hi", None)
    assert fake.mcp_bearers == [ACCESS_1, ACCESS_2]
    assert len(fake.token_posts) == 2
    _no_secret_anywhere(caplog, result, reason)


async def test_a_second_401_is_final_and_names_the_fix(monkeypatch, oauth_env, caplog):
    caplog.set_level(logging.DEBUG)
    fake = Fake(monkeypatch, [_tokens(ACCESS_1), _tokens(ACCESS_2)], accepted=set())
    result, reason = await McpClient().call("x", {})
    assert result is None
    assert reason == oauth.ACCESS_REFUSED
    assert "getlayers-connect.py" in reason
    assert len(fake.token_posts) == 2          # one refresh, not a loop
    assert len(fake.mcp_bearers) == 2
    _no_secret_anywhere(caplog, reason)


async def test_a_refused_refresh_token_says_reconnect_never_the_value(
        monkeypatch, oauth_env, caplog):
    caplog.set_level(logging.DEBUG)
    Fake(monkeypatch, [Resp(400, {"error": "invalid_grant",
                                  "error_description": f"bad {ENV_REFRESH}"})])
    result, reason = await McpClient().call("x", {})
    assert result is None
    assert reason.startswith(oauth.REFUSED)
    assert "(invalid_grant)" in reason
    assert "reconnect with bootstrap/getlayers-connect.py" in reason
    _no_secret_anywhere(caplog, reason)


# -------------------------------------------------------------- rotation

async def test_a_rotated_refresh_token_is_stored_0600_and_preferred(
        monkeypatch, oauth_env, caplog, tmp_path):
    caplog.set_level(logging.DEBUG)
    fake = Fake(monkeypatch, [_tokens(ACCESS_1, refresh=ROTATED),
                              _tokens(ACCESS_2, refresh=ROTATED)])
    await McpClient().call("x", {})

    path = oauth.store_path()
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o600
    assert stat.S_IMODE(os.stat(path.parent).st_mode) == 0o700
    record = json.loads(path.read_text())
    assert record["refresh_token"] == ROTATED
    assert record["client_id"] == "client-abc"
    assert ENV_REFRESH not in path.read_text()      # a hash, not the env value
    assert not [p for p in path.parent.iterdir() if p.name.startswith(".getlayers")]

    # A new process (cache gone) uses the stored token over the env one.
    oauth.reset()
    await McpClient().call("y", {})
    assert fake.token_posts[1]["refresh_token"] == ROTATED
    assert "rotated the refresh token" in caplog.text
    _no_secret_anywhere(caplog)


async def test_a_reconnect_in_env_beats_the_stored_rotated_token(
        monkeypatch, oauth_env):
    fake = Fake(monkeypatch, [_tokens(ACCESS_1, refresh=ROTATED),
                              _tokens(ACCESS_2)])
    await McpClient().call("x", {})
    # Miro re-ran the connect script: new client, new refresh token in .env.
    monkeypatch.setenv(oauth.CLIENT_ID_ENV, "client-new")
    monkeypatch.setenv(oauth.REFRESH_TOKEN_ENV, "rt-fresh-from-reconnect")
    oauth.reset()
    await McpClient().call("y", {})
    assert fake.token_posts[1]["refresh_token"] == "rt-fresh-from-reconnect"
    assert fake.token_posts[1]["client_id"] == "client-new"


async def test_an_unchanged_refresh_token_writes_no_file(monkeypatch, oauth_env):
    Fake(monkeypatch, [_tokens(ACCESS_1, refresh=ENV_REFRESH)])
    await McpClient().call("x", {})
    assert not oauth.store_path().exists()


async def test_the_token_endpoint_is_discovered_when_not_configured(
        monkeypatch, oauth_env):
    monkeypatch.delenv(oauth.TOKEN_URL_ENV)
    gets: list[str] = []

    async def get(client, url, **kw):
        gets.append(url)
        if url.endswith("/.well-known/oauth-protected-resource/mcp"):
            return Resp(200, {"resource": MCP,
                              "authorization_servers": ["https://mcp.getlayers.ai"]})
        if url == "https://mcp.getlayers.ai/.well-known/oauth-authorization-server":
            return Resp(200, {"issuer": "https://mcp.getlayers.ai",
                              "token_endpoint": TOKEN_URL})
        return Resp(404)

    monkeypatch.setattr(httpx.AsyncClient, "get", get)
    fake = Fake(monkeypatch, [_tokens(ACCESS_1)])
    _result, reason = await McpClient().call("x", {})
    assert reason is None
    assert len(fake.token_posts) == 1
    assert len(gets) == 2


# ----------------------------------------------------- static mode intact

async def test_static_token_mode_is_unchanged(monkeypatch):
    monkeypatch.setenv("GETLAYERS_TOKEN", "static-tok")
    fake = Fake(monkeypatch, [], accepted={"static-tok"})
    client = McpClient()
    assert not client.uses_oauth
    assert await client.call("x", {}) == ("hi", None)
    assert fake.mcp_bearers == ["static-tok"]
    assert fake.token_posts == []


async def test_static_token_401_is_final_with_the_old_wording(monkeypatch):
    monkeypatch.setenv("GETLAYERS_TOKEN", "static-tok")
    fake = Fake(monkeypatch, [], accepted=set())
    _result, reason = await McpClient().call("x", {})
    assert reason == "GetLayers refused the token — check GETLAYERS_TOKEN"
    assert fake.token_posts == []
    assert len(fake.mcp_bearers) == 1


async def test_an_explicit_token_never_uses_oauth(monkeypatch, oauth_env):
    fake = Fake(monkeypatch, [], accepted={"openart-tok"})
    client = McpClient("https://mcp.openart.ai/mcp", "openart-tok",
                       service="OpenArt", token_env="OPENART_TOKEN")
    assert not client.uses_oauth
    assert await client.call_tool("x", {}) == (
        {"content": [{"type": "text", "text": "hi"}]}, None)
    assert fake.token_posts == []


async def test_oauth_wins_over_a_static_token_when_both_are_set(
        monkeypatch, oauth_env):
    monkeypatch.setenv("GETLAYERS_TOKEN", "static-tok")
    fake = Fake(monkeypatch, [_tokens(ACCESS_1)])
    await McpClient().call("x", {})
    assert fake.mcp_bearers == [ACCESS_1]


# ------------------------------------------------------------------ check

async def test_check_is_unknown_when_nothing_is_configured(monkeypatch):
    async def boom(*_a, **_k):
        raise AssertionError("no network when unconfigured")
    monkeypatch.setattr(httpx.AsyncClient, "post", boom)
    obs = await agency.check_getlayers()
    assert obs.health is Health.UNKNOWN
    assert "getlayers-connect.py" in obs.detail


async def test_check_is_ok_only_after_a_real_handshake(monkeypatch, oauth_env):
    Fake(monkeypatch, [_tokens(ACCESS_1)])
    obs = await agency.check_getlayers()
    assert obs.health is Health.OK
    assert obs.evidence["mode"] == "oauth"


async def test_check_fails_when_the_refresh_token_is_refused(
        monkeypatch, oauth_env, caplog):
    caplog.set_level(logging.DEBUG)
    Fake(monkeypatch, [Resp(400, {"error": "invalid_grant"})])
    obs = await agency.check_getlayers()
    assert obs.health is Health.FAILING
    assert "reconnect with bootstrap/getlayers-connect.py" in obs.detail
    _no_secret_anywhere(caplog, obs.detail, obs.evidence)


async def test_check_fails_on_a_static_token_and_points_at_oauth(monkeypatch):
    monkeypatch.setenv("GETLAYERS_TOKEN", "static-tok")
    Fake(monkeypatch, [], accepted=set())
    obs = await agency.check_getlayers()
    assert obs.health is Health.FAILING
    assert "getlayers-connect.py" in obs.detail


async def test_check_is_unknown_when_the_token_endpoint_is_unreachable(
        monkeypatch, oauth_env):
    async def refused(client, url, **kw):
        raise httpx.ConnectError("refused", request=httpx.Request("POST", url))
    monkeypatch.setattr(httpx.AsyncClient, "post", refused)
    obs = await agency.check_getlayers()
    assert obs.health is Health.UNKNOWN
    assert obs.health is not Health.OK


def test_the_check_is_registered_daily_without_a_baseline():
    [check] = [c for c in agency.MODULE.checks() if c.name == "getlayers_auth"]
    from datetime import timedelta
    assert check.interval == timedelta(days=1)
    assert check.baseline_sigma is None


async def test_a_cancelled_refresh_does_not_keep_the_file_lock(monkeypatch, oauth_env):
    """A check timeout cancelling a waiter must leave the lock free."""
    started = asyncio.Event()

    async def slow(client, url, **kw):
        started.set()
        await asyncio.sleep(10)

    monkeypatch.setattr(httpx.AsyncClient, "post", slow)
    task = asyncio.create_task(McpClient().call("x", {}))
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    monkeypatch.setattr(oauth, "LOCK_WAIT", 0.5)
    fd = await oauth._lock_file(oauth.store_path())     # times out if leaked
    oauth._unlock_file(fd)


def test_configured_covers_both_modes(monkeypatch):
    assert not oauth.configured()
    monkeypatch.setenv("GETLAYERS_TOKEN", "x")
    assert oauth.configured()
    monkeypatch.delenv("GETLAYERS_TOKEN")
    monkeypatch.setenv(oauth.CLIENT_ID_ENV, "c")
    assert not oauth.configured()               # needs the refresh token too
    monkeypatch.setenv(oauth.REFRESH_TOKEN_ENV, "r")
    assert oauth.configured()
