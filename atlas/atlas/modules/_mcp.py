"""The one MCP client shared by every module that talks to a remote MCP
server: `agency.mockup` (GetLayers, AGENCY.md §2.4) and `content.render_video`
(OpenArt). Task 3 (agency) and Task 35 (content) each wrote one in parallel;
Task 39 reconciled them here, keeping Task 3's more complete implementation
(it matches the JSON-RPC response `id`, skips notifications, sends
`notifications/initialized`, keeps `Mcp-Session-Id`, and returns
`(None, reason)` on 401/403 without the token). `atlas/modules/agency/_mcp.py`
is now a re-export of this module, kept so nothing importing it breaks.

Streamable HTTP transport, JSON-RPC 2.0: one POST per call, `Content-Type:
application/json`, `Accept: application/json, text/event-stream`. The
server may answer with a plain JSON body or with `text/event-stream`
(Server-Sent Events) — this client parses either from `response.text`
directly (never `response.json()` or `response.raise_for_status()`, so a
minimal stand-in for a real `httpx.Response` in a test is enough — both
`tests/test_agency_mcp.py` and `tests/test_mcp.py` use different fakes).
An SSE body is not always one message: a server may emit a
`notifications/...` line (no `id` at all) before the actual response, so
`_parse_sse` is given the request's own id and returns the first `data:`
message that carries it, never just the first line. `initialize()` also
sends the `notifications/initialized` notification the MCP spec requires
right after a successful handshake, before any `tools/list` or
`tools/call`.

This does not use `atlas.modules._http.post_json`: that helper calls
`response.json()` and throws away the response, so it cannot hand back the
`Mcp-Session-Id` header the server expects on every call after
`initialize`, and it cannot read an SSE body at all. The same reasoning
`audit.py` gives for not using `_web.fetch_text` applies here — the shape
this caller needs is richer than what the generic helper keeps.

Two callers, two calling conventions, reconciled with thin, documented
compatibility rather than by picking one and breaking the other:

  * `agency.mockup` constructs `McpClient()` with no arguments (falling
    back to `GETLAYERS_MCP_URL` / `GETLAYERS_TOKEN`) and calls `call()`,
    which joins a `tools/call` reply's `content` text items into one
    string — right for GetLayers, whose tools answer in prose that then
    gets wrapped with `wrap_untrusted()` before it reaches a prompt
    (invariant 4).
  * `content.render_video` constructs `McpClient(url, token)` with both
    positional (falling back to nothing — it always supplies OpenArt's own
    values) and calls `call_tool()`, kept as a separate method rather than
    an alias of `call()` because it reads structured fields
    (`result["credits"]`, `result["video_url"]`) straight off the reply.
    OpenArt's `tools/call` shape has never been confirmed against the real
    service (see that module's docstring), so `call_tool()` returns the
    raw `result` unprocessed — joining `content` text the way `call()`
    does would silently break that field access the day OpenArt's reply
    turns out not to carry a `content` list the way a prose-answering
    GetLayers tool does.

GetLayers authenticates with OAuth, not an API key (it has none). When
`GETLAYERS_OAUTH_CLIENT_ID` and `GETLAYERS_OAUTH_REFRESH_TOKEN` are set, a
`McpClient()` built for GetLayers without an explicit token takes its
bearer from `_getlayers_oauth.source()` -- a process-wide cache refreshed
through the refresh_token grant -- and on a 401 drops that token, refreshes
once and retries once. Precedence: a token passed to the constructor (every
OpenArt and Perplexity caller), then OAuth, then the legacy static
`GETLAYERS_TOKEN`, which behaves exactly as before (a 401 is final, reported
against `GETLAYERS_TOKEN`).

Everything a tool call returns is text written by a third party (GetLayers
or OpenArt). It is not sanitised here — the caller wraps it with
`wrap_untrusted()` before it reaches a prompt, the same as any other
ingested byte (invariant 4).
"""
from __future__ import annotations

import json
import logging
import os
from typing import Any

import httpx

from . import _getlayers_oauth as oauth
from ._http import explain

log = logging.getLogger("atlas.mcp")

DEFAULT_URL = "https://mcp.getlayers.ai/mcp"
PROTOCOL_VERSION = "2025-03-26"


def _parse_sse(text: str, request_id: int) -> Any | None:
    """The JSON-RPC message whose `id` matches `request_id`, out of an SSE
    body's events — never just the first one. Per the SSE spec, one event's
    `data:` field can be spread over several consecutive `data:` lines,
    which a receiver must join with `\\n` before parsing as one payload;
    other field lines (`event:`, `id:`, `retry:`, comments) belong to the
    same event but carry nothing this client reads, so they are ignored
    without ending the event. An event ends at a blank line (dispatched
    there) or at the end of the body (dispatched once more after the
    loop, in case the server omitted the trailing blank line). A
    notification (no `id` field at all) or a message answering some OTHER
    in-flight request is skipped rather than mistaken for this call's
    result. Returns None if no matching message ever arrives; never raises
    on a line this client did not expect."""
    data_lines: list[str] = []

    def _dispatch() -> Any | None:
        if not data_lines:
            return None
        try:
            message = json.loads("\n".join(data_lines))
        except ValueError:
            return None
        if isinstance(message, dict) and message.get("id") == request_id:
            return message
        return None

    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            result = _dispatch()
            data_lines.clear()
            if result is not None:
                return result
            continue
        if line.startswith("data:"):
            data_lines.append(line[len("data:"):].strip())
    return _dispatch()


class McpClient:
    """One session against one MCP server. `initialize()` records the
    `Mcp-Session-Id` the server hands back; every later call sends it.

    `url=None` falls back to `GETLAYERS_MCP_URL` (GetLayers is the only
    caller that constructs without an explicit url); `token=None` falls
    back to `GETLAYERS_TOKEN` the same way. `service` and `token_env` name
    whichever server this instance actually talks to, so a refused token
    is reported against the right environment variable — `content.
    render_video` passes `service="OpenArt", token_env="OPENART_TOKEN"`
    rather than inheriting GetLayers' wording. `timeout` defaults to
    GetLayers' 30s; `content.render_video` passes 60s, matching the slower
    client it replaces (a video render is not a prose reply)."""

    def __init__(self, url: str | None = None, token: str | None = None, *,
                timeout: float = 30.0, service: str = "GetLayers",
                token_env: str = "GETLAYERS_TOKEN") -> None:
        self.url = url if url is not None else os.getenv("GETLAYERS_MCP_URL", DEFAULT_URL)
        self._oauth: oauth.TokenSource | None = None
        if token is not None:
            self.token: str | None = token
        elif service == "GetLayers" and oauth.oauth_configured():
            self.token = None
            self._oauth = oauth.source(self.url)
        else:
            self.token = os.getenv("GETLAYERS_TOKEN")
        self.timeout = timeout
        self._refused = f"{service} refused the token — check {token_env}"
        self.session_id: str | None = None
        self._next_id = 1

    @property
    def uses_oauth(self) -> bool:
        return self._oauth is not None

    async def _bearer(self, stale: str | None = None) -> tuple[str | None, str | None]:
        """`(token, None)` or `(None, safe reason)`. Static mode hands back
        the configured token (possibly none); OAuth mode asks the shared
        token source, dropping `stale` first when a request just had it
        refused."""
        if self._oauth is None:
            return self.token, None
        try:
            return await self._oauth.token(stale=stale), None
        except oauth.RefreshRefused as exc:
            return None, str(exc)

    def _headers(self, token: str | None) -> dict[str, str]:
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        }
        if token:
            headers["Authorization"] = f"Bearer {token}"
        if self.session_id:
            headers["Mcp-Session-Id"] = self.session_id
        return headers

    async def _rpc(self, method: str, params: dict[str, Any]) -> tuple[Any, str | None]:
        """One JSON-RPC request. Never raises: a broken MCP server is a
        `(None, reason)` for the caller to turn into `Health.UNKNOWN` or a
        failed `ActionResult`, not a stack trace in a scheduler loop.

        Parses the body from `response.text` and checks `status_code`
        directly rather than calling `response.json()` /
        `response.raise_for_status()` — both real `httpx.Response` objects
        and the minimal fakes each test file stands in for one support
        `.status_code`, `.headers` and `.text`, but not every test fake
        implements the other two."""
        request_id = self._next_id
        self._next_id += 1
        body = {"jsonrpc": "2.0", "id": request_id, "method": method, "params": params}
        token, reason = await self._bearer()
        if reason is not None:
            return None, reason
        response, reason = await self._post(body, token)
        if response is not None and response.status_code == 401 and self._oauth:
            # The cached access token may have been revoked or expired
            # early: refresh once, retry once. A second 401 is final.
            token, reason = await self._bearer(stale=token)
            if reason is not None:
                return None, reason
            response, reason = await self._post(body, token)
        if response is None:
            return None, reason

        if response.status_code in (401, 403):
            return None, oauth.ACCESS_REFUSED if self._oauth else self._refused
        if response.status_code >= 400:
            return None, f"MCP server returned HTTP {response.status_code}"

        session_id = response.headers.get("Mcp-Session-Id")
        if session_id:
            self.session_id = session_id

        content_type = response.headers.get("content-type", "")
        if "text/event-stream" in content_type:
            message = _parse_sse(response.text, request_id)
        else:
            try:
                message = json.loads(response.text)
            except ValueError:
                return None, "MCP response was neither JSON nor SSE"

        if not isinstance(message, dict):
            return None, "MCP response had no JSON-RPC message"
        if "error" in message:
            err = message["error"]
            detail = err.get("message", err) if isinstance(err, dict) else err
            return None, f"MCP error: {detail}"
        if "result" not in message:
            return None, "no result in response"
        return message["result"], None

    async def _post(self, body: dict[str, Any], token: str | None
                    ) -> tuple[Any | None, str | None]:
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                return await client.post(self.url, headers=self._headers(token),
                                         json=body), None
        except Exception as exc:                            # noqa: BLE001
            return None, explain(exc)

    async def _notify(self, method: str, params: dict[str, Any]) -> None:
        """A JSON-RPC notification: no `id`, no response expected. Fire and
        forget — a client that failed to tell the server it finished
        initializing is a protocol wrinkle, not a reason to fail the whole
        flow before it has done anything."""
        body = {"jsonrpc": "2.0", "method": method, "params": params}
        token, reason = await self._bearer()
        if reason is not None:
            log.debug("MCP notification %s skipped: %s", method, reason)
            return
        _response, reason = await self._post(body, token)
        if reason is not None:
            log.debug("MCP notification %s failed: %s", method, reason)

    async def initialize(self) -> tuple[dict | None, str | None]:
        result, reason = await self._rpc("initialize", {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {},
            "clientInfo": {"name": "atlas", "version": "0.1"},
        })
        if reason is not None:
            return None, reason
        await self._notify("notifications/initialized", {})
        return result, None

    async def list_tools(self) -> tuple[list[dict], str | None]:
        result, reason = await self._rpc("tools/list", {})
        if reason is not None:
            return [], reason
        tools = result.get("tools") if isinstance(result, dict) else None
        if not isinstance(tools, list):
            return [], "tools/list response has no 'tools' list — the assumed shape is wrong"
        return tools, None

    async def call(self, name: str, arguments: dict) -> tuple[Any, str | None]:
        """`result.content`'s text items joined with newlines, when the
        tool answered in that shape; the raw result otherwise. This is the
        GetLayers-facing method — see `call_tool()` for the OpenArt one,
        which never joins."""
        result, reason = await self._rpc("tools/call", {"name": name, "arguments": arguments})
        if reason is not None:
            return None, reason
        if not isinstance(result, dict):
            return result, None
        content = result.get("content")
        if isinstance(content, list):
            texts = [item.get("text", "") for item in content
                    if isinstance(item, dict) and item.get("type") == "text"]
            if texts:
                return "\n".join(texts), None
        return result, None

    async def call_tool(self, name: str, arguments: dict[str, Any] | None = None
                        ) -> tuple[Any, str | None]:
        """The pre-merge OpenArt-facing call: the raw `result` object,
        never joined into text. Kept as its own method (not an alias of
        `call()`) because `content.render_video` reads `result["credits"]`
        and `result["video_url"]` directly off the reply, and OpenArt's
        real `tools/call` shape has not been confirmed to wrap them in a
        GetLayers-style `content` list the way `call()` assumes."""
        return await self._rpc("tools/call", {"name": name, "arguments": arguments or {}})
