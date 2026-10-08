"""agency._mcp — the GetLayers MCP client (AGENCY.md §2.4).

No real network: every test monkeypatches `httpx.AsyncClient.post` with a
fake that hands back a canned status code, headers and body (JSON or SSE),
the same seam the brief calls out for testing this client.
"""
from __future__ import annotations

import json

import httpx

from atlas.modules.agency._mcp import McpClient


class FakeResponse:
    def __init__(self, status_code=200, json_data=None, text_data=None,
                headers=None, content_type="application/json"):
        self.status_code = status_code
        self._json_data = json_data
        if text_data is not None:
            self.text = text_data
        else:
            self.text = json.dumps(json_data) if json_data is not None else ""
        self.headers = {"content-type": content_type, **(headers or {})}

    def json(self):
        if self._json_data is None:
            raise ValueError("no JSON body")
        return self._json_data


def _patch_post(monkeypatch, response: FakeResponse, *, captured: dict | None = None):
    async def fake_post(self, url, *, headers=None, json=None, **kwargs):
        if captured is not None:
            captured["url"] = url
            captured["headers"] = headers
            captured["body"] = json
        return response
    monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)


# ------------------------------------------------------------------- JSON

async def test_call_parses_a_plain_json_body(monkeypatch):
    response = FakeResponse(json_data={
        "jsonrpc": "2.0", "id": 1,
        "result": {"content": [{"type": "text", "text": "hello"}]},
    })
    _patch_post(monkeypatch, response)

    client = McpClient(url="https://mcp.getlayers.ai/mcp", token="tok")
    result, reason = await client.call("getlayers_search", {"query": "plumber"})
    assert reason is None
    assert result == "hello"


async def test_call_joins_multiple_text_content_items(monkeypatch):
    response = FakeResponse(json_data={
        "jsonrpc": "2.0", "id": 1,
        "result": {"content": [
            {"type": "text", "text": "line one"},
            {"type": "text", "text": "line two"},
        ]},
    })
    _patch_post(monkeypatch, response)

    client = McpClient()
    result, reason = await client.call("getlayers_compositions", {})
    assert reason is None
    assert result == "line one\nline two"


async def test_call_returns_the_raw_result_when_it_is_not_content_shaped(monkeypatch):
    response = FakeResponse(json_data={"jsonrpc": "2.0", "id": 1, "result": {"ok": True}})
    _patch_post(monkeypatch, response)

    client = McpClient()
    result, reason = await client.call("some_tool", {})
    assert reason is None
    assert result == {"ok": True}


# -------------------------------------------------------------------- SSE

async def test_call_parses_an_sse_body(monkeypatch):
    message = {"jsonrpc": "2.0", "id": 1,
              "result": {"content": [{"type": "text", "text": "from sse"}]}}
    sse_body = f"event: message\ndata: {json.dumps(message)}\n\n"
    response = FakeResponse(text_data=sse_body, content_type="text/event-stream")
    _patch_post(monkeypatch, response)

    client = McpClient()
    result, reason = await client.call("getlayers_materialize", {})
    assert reason is None
    assert result == "from sse"


async def test_sse_body_with_no_data_line_is_a_clean_failure(monkeypatch):
    response = FakeResponse(text_data="event: ping\n\n", content_type="text/event-stream")
    _patch_post(monkeypatch, response)

    client = McpClient()
    result, reason = await client.call("some_tool", {})
    assert result is None
    assert reason is not None


async def test_call_skips_a_notification_before_the_real_response_on_sse(monkeypatch):
    """A server may emit `notifications/...` (no `id` at all) on the same
    SSE stream before the actual response to this call's request id --
    the first `data:` line must not be mistaken for the answer."""
    notification = {"jsonrpc": "2.0", "method": "notifications/progress", "params": {}}
    real = {"jsonrpc": "2.0", "id": 1,
           "result": {"content": [{"type": "text", "text": "the real answer"}]}}
    sse_body = (f"data: {json.dumps(notification)}\n\n"
               f"data: {json.dumps(real)}\n\n")
    response = FakeResponse(text_data=sse_body, content_type="text/event-stream")
    _patch_post(monkeypatch, response)

    client = McpClient()
    result, reason = await client.call("getlayers_search", {})
    assert reason is None
    assert result == "the real answer"


async def test_a_message_with_no_result_and_no_error_is_a_reason(monkeypatch):
    response = FakeResponse(json_data={"jsonrpc": "2.0", "id": 1})
    _patch_post(monkeypatch, response)

    client = McpClient()
    result, reason = await client.call("some_tool", {})
    assert result is None
    assert "no result" in reason


# ------------------------------------------------------------ 401/403/error

async def test_401_is_refused_without_the_token_in_the_reason(monkeypatch):
    response = FakeResponse(status_code=401)
    _patch_post(monkeypatch, response)

    client = McpClient(url="https://mcp.getlayers.ai/mcp", token="super-secret-token")
    result, reason = await client.list_tools()
    assert result == []
    assert reason == "GetLayers refused the token — check GETLAYERS_TOKEN"
    assert "super-secret-token" not in reason


async def test_403_is_refused_the_same_way(monkeypatch):
    response = FakeResponse(status_code=403)
    _patch_post(monkeypatch, response)

    client = McpClient(token="another-secret")
    _result, reason = await client.initialize()
    assert reason == "GetLayers refused the token — check GETLAYERS_TOKEN"
    assert "another-secret" not in reason


async def test_a_jsonrpc_error_field_is_surfaced_as_a_reason(monkeypatch):
    response = FakeResponse(json_data={
        "jsonrpc": "2.0", "id": 1,
        "error": {"code": -32601, "message": "method not found"},
    })
    _patch_post(monkeypatch, response)

    client = McpClient()
    result, reason = await client.call("nonexistent_tool", {})
    assert result is None
    assert "method not found" in reason


# ------------------------------------------------------------- list_tools

async def test_list_tools_reports_a_reason_when_the_shape_is_wrong(monkeypatch):
    response = FakeResponse(json_data={"jsonrpc": "2.0", "id": 1, "result": {}})
    _patch_post(monkeypatch, response)

    client = McpClient()
    tools, reason = await client.list_tools()
    assert tools == []
    assert reason is not None


async def test_list_tools_returns_the_tool_list(monkeypatch):
    response = FakeResponse(json_data={
        "jsonrpc": "2.0", "id": 1,
        "result": {"tools": [{"name": "getlayers_search"}, {"name": "getlayers_compositions"}]},
    })
    _patch_post(monkeypatch, response)

    client = McpClient()
    tools, reason = await client.list_tools()
    assert reason is None
    assert {t["name"] for t in tools} == {"getlayers_search", "getlayers_compositions"}


# --------------------------------------------------------- session header

async def test_session_id_from_initialize_is_sent_on_the_next_call(monkeypatch):
    """`initialize()` makes two requests -- the `initialize` RPC itself,
    then the `notifications/initialized` notification the MCP spec
    requires -- and the session id the server hands back on the first
    must be carried on both the notification and every later call."""
    init_response = FakeResponse(
        json_data={"jsonrpc": "2.0", "id": 1, "result": {"protocolVersion": "2025-03-26"}},
        headers={"Mcp-Session-Id": "sess-123"})

    calls: list[tuple[dict, dict]] = []

    async def fake_post(self, url, *, headers=None, json=None, **kwargs):
        calls.append((json, headers))
        if len(calls) == 1:
            return init_response
        return FakeResponse(json_data={"jsonrpc": "2.0", "id": json.get("id", 0),
                                       "result": {"tools": []}})

    monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)

    client = McpClient()
    await client.initialize()
    await client.list_tools()

    _init_body, init_headers = calls[0]
    notify_body, notify_headers = calls[1]
    list_body, list_headers = calls[2]

    assert init_headers.get("Mcp-Session-Id") is None
    assert notify_body["method"] == "notifications/initialized"
    assert "id" not in notify_body
    assert notify_headers["Mcp-Session-Id"] == "sess-123"
    assert list_body["method"] == "tools/list"
    assert list_headers["Mcp-Session-Id"] == "sess-123"


# ----------------------------------------------------- request envelope

async def test_call_sends_the_full_jsonrpc_envelope_and_auth_header(monkeypatch):
    response = FakeResponse(json_data={"jsonrpc": "2.0", "id": 1, "result": {"content": []}})
    captured: dict = {}
    _patch_post(monkeypatch, response, captured=captured)

    client = McpClient(url="https://mcp.getlayers.ai/mcp", token="tok-123")
    await client.call("getlayers_search", {"query": "plumber"})

    assert captured["url"] == "https://mcp.getlayers.ai/mcp"
    assert captured["body"]["jsonrpc"] == "2.0"
    assert captured["body"]["method"] == "tools/call"
    assert captured["body"]["params"] == {
        "name": "getlayers_search", "arguments": {"query": "plumber"}}
    assert captured["headers"]["Authorization"] == "Bearer tok-123"
    assert captured["headers"]["Content-Type"] == "application/json"


# ------------------------------------------------------- network failure

async def test_a_connection_failure_never_raises(monkeypatch):
    async def fake_post(self, url, *, headers=None, json=None, **kwargs):
        raise httpx.ConnectError("connection refused", request=httpx.Request("POST", url))
    monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)

    client = McpClient()
    result, reason = await client.call("getlayers_search", {})
    assert result is None
    assert reason is not None
