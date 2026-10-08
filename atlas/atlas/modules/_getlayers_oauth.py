"""GetLayers' OAuth token source for the server's MCP client.

GetLayers has no API keys ("One connection, no API key"). Its MCP server at
`https://mcp.getlayers.ai/mcp` answers 401 with a `WWW-Authenticate` naming
RFC 9728 resource metadata, whose authorization server publishes RFC 8414
metadata, RFC 7591 dynamic client registration and the authorization-code
+ PKCE flow. Read on 2026-09-29 (`/.well-known/oauth-authorization-server`):

    token_endpoint           https://mcp.getlayers.ai/token
    registration_endpoint    https://mcp.getlayers.ai/register
    grant_types_supported    authorization_code, refresh_token
    token_endpoint_auth      client_secret_basic, client_secret_post, none
    code_challenge_methods   S256
    scopes_supported         mcp

The browser half of that flow cannot run on a headless server, so it runs
once on the Mac (`bootstrap/getlayers-connect.py`), which leaves the server
a client id and a refresh token in `.env`. From then on this module trades
the refresh token for short-lived access tokens:

  * The access token is cached in memory, process-wide (`mockup` and
    `build_site` build a fresh `McpClient()` per call, so a per-instance
    cache would never cache anything), until `REFRESH_MARGIN` before its
    `expires_in`.
  * One refresh at a time: an `asyncio.Lock` inside the process, and an
    `fcntl` lock on a file beside the state file across processes (the API
    and a `docker compose exec api python -m atlas.cli connect` are two
    processes sharing one refresh token). Under both locks the cache and
    the state file are read again before anything is sent, so two waiters
    never present the same refresh token twice -- with rotation, a reused
    refresh token can get the whole grant revoked.
  * A rotated refresh token is written to `GETLAYERS_OAUTH_STORE` (default
    `data/secrets/getlayers_oauth.json`, which is `/app/data/secrets/...`
    in the container and survives a rebuild), created 0600 in a 0700
    directory and swapped in with `os.replace`. That file wins over `.env`
    -- but only while it was derived from the refresh token `.env` holds
    now: it records a SHA-256 of that env value and the client id. Re-run
    the connect script and `.env` changes, the fingerprint no longer
    matches, and the fresh `.env` token is used (and the file overwritten
    on the next rotation) instead of a dead rotated one winning forever.

No token value is ever logged, returned in a reason, or put in an
exception message. A refused refresh reads exactly `REFUSED`, plus at most
the authorization server's `error` code (`invalid_grant`), never its body.
"""
from __future__ import annotations

import asyncio
import fcntl
import hashlib
import json
import logging
import os
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import httpx

from ._http import explain

log = logging.getLogger("atlas.mcp.oauth")

CLIENT_ID_ENV = "GETLAYERS_OAUTH_CLIENT_ID"
REFRESH_TOKEN_ENV = "GETLAYERS_OAUTH_REFRESH_TOKEN"
CLIENT_SECRET_ENV = "GETLAYERS_OAUTH_CLIENT_SECRET"
TOKEN_URL_ENV = "GETLAYERS_OAUTH_TOKEN_URL"
STORE_ENV = "GETLAYERS_OAUTH_STORE"
DEFAULT_STORE = "data/secrets/getlayers_oauth.json"

CONNECT = "reconnect with bootstrap/getlayers-connect.py"
REFUSED = f"GetLayers refused the refresh token — {CONNECT}"
ACCESS_REFUSED = f"GetLayers refused the OAuth access token — {CONNECT}"

# Refresh this long before the access token's stated expiry, so a call that
# takes its token at 59:59 does not land at the server at 60:01.
REFRESH_MARGIN = 60.0
# A token response without `expires_in` is cached this long, not forever.
DEFAULT_LIFETIME = 300.0
TIMEOUT = 20.0


def oauth_configured() -> bool:
    return bool(os.getenv(CLIENT_ID_ENV, "").strip()
                and os.getenv(REFRESH_TOKEN_ENV, "").strip())


def configured() -> bool:
    """Either way of authenticating to GetLayers is set up: OAuth (the one
    that works) or the legacy static `GETLAYERS_TOKEN`."""
    return oauth_configured() or bool(os.getenv("GETLAYERS_TOKEN", "").strip())


def store_path() -> Path:
    return Path(os.getenv(STORE_ENV, "").strip() or DEFAULT_STORE)


def _fingerprint(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


class RefreshRefused(Exception):
    """No access token could be had. `str()` is safe to show. `auth` is
    True when GetLayers itself said no (or OAuth is not set up) -- an owner
    action, not an outage -- and False when it could not be asked."""

    def __init__(self, message: str, *, auth: bool = False) -> None:
        super().__init__(message)
        self.auth = auth


@dataclass
class _Cached:
    token: str
    expires_at: float           # time.monotonic()


def _read_store(path: Path) -> dict[str, Any] | None:
    try:
        data = json.loads(path.read_text())
    except FileNotFoundError:
        return None
    except (OSError, ValueError):
        log.warning("GetLayers OAuth state file %s is unreadable; using .env", path)
        return None
    return data if isinstance(data, dict) else None


def _write_store(path: Path, record: dict[str, Any]) -> None:
    """Atomically, and never readable by anyone else for even a moment: the
    temp file is created 0600 by `mkstemp`, filled, fsynced, then renamed
    over the old one."""
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(path.parent, 0o700)
    except OSError:
        pass
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".getlayers_oauth.")
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w") as fh:
            json.dump(record, fh)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


LOCK_WAIT = 45.0


async def _lock_file(path: Path) -> int:
    """The cross-process refresh lock; returns the fd holding it. Polled
    non-blocking rather than a blocking flock in a thread: a cancelled
    waiter (a check's timeout) must not leave a thread that later takes the
    lock and never lets go. Gives up after `LOCK_WAIT`."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(path) + ".lock", os.O_RDWR | os.O_CREAT, 0o600)
    deadline = time.monotonic() + LOCK_WAIT
    try:
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                return fd
            except BlockingIOError:
                if time.monotonic() > deadline:
                    raise RefreshRefused(
                        "another ATLAS process has held the GetLayers refresh "
                        "lock too long") from None
                await asyncio.sleep(0.1)
    except BaseException:
        os.close(fd)
        raise


def _unlock_file(fd: int) -> None:
    try:
        fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
        os.close(fd)


class TokenSource:
    """Hands out a GetLayers access token. One per process (`source()`)."""

    def __init__(self, mcp_url: str) -> None:
        self.mcp_url = mcp_url
        self._cached: _Cached | None = None
        self._lock: asyncio.Lock | None = None
        self._lock_loop: asyncio.AbstractEventLoop | None = None
        self._token_url: str | None = None

    # ------------------------------------------------------------ config

    @staticmethod
    def _client_id() -> str:
        return os.getenv(CLIENT_ID_ENV, "").strip()

    @staticmethod
    def _client_secret() -> str:
        return os.getenv(CLIENT_SECRET_ENV, "").strip()

    def _current_refresh_token(self) -> str:
        """The state file's rotated token while it still descends from the
        `.env` token, else the `.env` token."""
        env_token = os.getenv(REFRESH_TOKEN_ENV, "").strip()
        record = _read_store(store_path())
        if (record and record.get("client_id") == self._client_id()
                and record.get("env_sha256") == _fingerprint(env_token)
                and isinstance(record.get("refresh_token"), str)
                and record["refresh_token"]):
            return record["refresh_token"]
        return env_token

    def _persist(self, refresh_token: str) -> None:
        env_token = os.getenv(REFRESH_TOKEN_ENV, "").strip()
        _write_store(store_path(), {
            "client_id": self._client_id(),
            "env_sha256": _fingerprint(env_token),
            "refresh_token": refresh_token,
            "updated_at": int(time.time()),
        })
        log.info("GetLayers rotated the refresh token; the new one is stored")

    def _async_lock(self) -> asyncio.Lock:
        loop = asyncio.get_running_loop()
        if self._lock is None or self._lock_loop is not loop:
            self._lock = asyncio.Lock()
            self._lock_loop = loop
        return self._lock

    # --------------------------------------------------------- discovery

    async def _discover_token_url(self, client: httpx.AsyncClient) -> str:
        """RFC 9728 resource metadata -> first authorization server ->
        RFC 8414 metadata -> `token_endpoint`. Only used when the connect
        script did not leave `GETLAYERS_OAUTH_TOKEN_URL` behind."""
        parts = urlsplit(self.mcp_url)
        origin = f"{parts.scheme}://{parts.netloc}"
        issuer = origin
        try:
            r = await client.get(
                f"{origin}/.well-known/oauth-protected-resource{parts.path}")
            if r.status_code == 200:
                servers = r.json().get("authorization_servers") or []
                if servers and isinstance(servers[0], str):
                    issuer = servers[0].rstrip("/")
        except (httpx.HTTPError, ValueError, AttributeError):
            pass
        r = await client.get(f"{issuer}/.well-known/oauth-authorization-server")
        if r.status_code != 200:
            raise RefreshRefused(
                f"GetLayers authorization-server metadata answered HTTP "
                f"{r.status_code}")
        token_url = r.json().get("token_endpoint")
        if not isinstance(token_url, str) or not token_url.startswith("https://"):
            raise RefreshRefused("GetLayers metadata names no https token endpoint")
        return token_url

    async def _token_endpoint(self, client: httpx.AsyncClient) -> str:
        explicit = os.getenv(TOKEN_URL_ENV, "").strip()
        if explicit:
            return explicit
        if self._token_url is None:
            self._token_url = await self._discover_token_url(client)
        return self._token_url

    # ----------------------------------------------------------- refresh

    def _fresh(self) -> str | None:
        cached = self._cached
        if cached and time.monotonic() < cached.expires_at - REFRESH_MARGIN:
            return cached.token
        return None

    async def _refresh(self) -> str:
        refresh_token = self._current_refresh_token()
        if not refresh_token or not self._client_id():
            raise RefreshRefused(f"GetLayers OAuth is not configured — {CONNECT}",
                                 auth=True)
        form = {
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
            "client_id": self._client_id(),
            # RFC 8707: the audience the MCP spec requires on every request.
            "resource": self.mcp_url,
        }
        secret = self._client_secret()
        if secret:
            form["client_secret"] = secret                 # client_secret_post
        try:
            async with httpx.AsyncClient(timeout=TIMEOUT) as client:
                url = await self._token_endpoint(client)
                r = await client.post(url, data=form,
                                      headers={"Accept": "application/json"})
        except RefreshRefused:
            raise
        except Exception as exc:                           # noqa: BLE001
            raise RefreshRefused(
                f"could not reach GetLayers' token endpoint: {explain(exc)}"
            ) from None
        try:
            body = json.loads(r.text)
        except ValueError:
            body = None
        if r.status_code in (400, 401, 403):
            code = body.get("error") if isinstance(body, dict) else None
            suffix = f" ({code})" if isinstance(code, str) and code.isascii() \
                and code.replace("_", "").isalnum() and len(code) < 40 else ""
            raise RefreshRefused(f"{REFUSED}{suffix}", auth=True)
        if r.status_code >= 400 or not isinstance(body, dict):
            raise RefreshRefused(
                f"GetLayers' token endpoint answered HTTP {r.status_code}")
        access = body.get("access_token")
        if not isinstance(access, str) or not access:
            raise RefreshRefused("GetLayers' token response carried no access token")
        try:
            lifetime = float(body.get("expires_in") or DEFAULT_LIFETIME)
        except (TypeError, ValueError):
            lifetime = DEFAULT_LIFETIME
        new_refresh = body.get("refresh_token")
        if isinstance(new_refresh, str) and new_refresh and new_refresh != refresh_token:
            await asyncio.to_thread(self._persist, new_refresh)
        self._cached = _Cached(access, time.monotonic() + lifetime)
        log.info("GetLayers access token refreshed; valid for %d s", int(lifetime))
        return access

    async def token(self, *, stale: str | None = None) -> str:
        """A usable access token. `stale` is the token a request just had
        refused: it is dropped only if it is still the cached one, so a
        401 racing another coroutine's refresh does not refresh twice.
        Raises `RefreshRefused` (safe text) when none can be had."""
        if stale is None:
            token = self._fresh()
            if token:
                return token
        async with self._async_lock():
            if stale is not None and self._cached and self._cached.token == stale:
                self._cached = None
            token = self._fresh()
            if token:
                return token
            fd = await _lock_file(store_path())
            try:
                return await self._refresh()
            finally:
                _unlock_file(fd)


_SOURCE: TokenSource | None = None


def source(mcp_url: str) -> TokenSource:
    global _SOURCE
    if _SOURCE is None or _SOURCE.mcp_url != mcp_url:
        _SOURCE = TokenSource(mcp_url)
    return _SOURCE


def reset() -> None:
    """Forget the cached token (tests; a reconnect in-process)."""
    global _SOURCE
    _SOURCE = None
