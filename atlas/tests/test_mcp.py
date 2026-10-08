"""`atlas.modules._mcp.McpClient` -- the JSON-RPC-over-HTTP client. Task 39
reconciled this with `atlas.modules.agency._mcp` (Task 3): the shared client
now lives here with Task 3's semantics, and `call_tool()` is the thin,
documented compatibility method `content.render_video` still calls.
`content.render_video` depends on this contract, so its parsing and
error-shaping are tested like any other module.

No network: every test stubs `httpx.AsyncClient` at the transport level via
`monkeypatch`, per the repo's no-network test policy.
"""
from __future__ import annotations

import json

import httpx

from atlas.modules._mcp import McpClient


class _FakeResponse:
    def __init__(self, status_code, json_body=None, text=None,
                headers=None):
        self.status_code = status_code
        self.headers = headers or {}
        if text is not None:
            self.text = text
        else:
            self.text = json.dumps(json_body)


def _install(monkeypatch, response: _FakeResponse):
    async def fake_post(self, url, json=None, headers=None):
        return response

    monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)


async def test_call_tool_returns_result_on_plain_json(monkeypatch):
    _install(monkeypatch, _FakeResponse(
        200, {"jsonrpc": "2.0", "id": 1, "result": {"credits": 4}},
        headers={"content-type": "application/json"}))

    client = McpClient("https://mcp.example/mcp", "secret-token")
    result, reason = await client.call_tool("render_video", {"script": "hi"})

    assert reason is None
    assert result == {"credits": 4}


async def test_401_is_reported_without_the_token(monkeypatch):
    _install(monkeypatch, _FakeResponse(401, text="unauthorized"))

    client = McpClient("https://mcp.example/mcp", "super-secret-token")
    result, reason = await client.call_tool("render_video", {})

    assert result is None
    assert reason is not None
    assert "super-secret-token" not in reason
    # Task 39: the unified client reports a fixed "<service> refused the
    # token" message instead of the status code (real content.render_video
    # calls pass service="OpenArt", so this default-constructed client's
    # wording is GetLayers'; the token is still never included).
    assert "refused the token" in reason


async def test_403_is_reported_without_the_token(monkeypatch):
    _install(monkeypatch, _FakeResponse(403, text="forbidden"))

    client = McpClient("https://mcp.example/mcp", "another-secret")
    result, reason = await client.call_tool("render_video", {})

    assert result is None
    assert "another-secret" not in reason
    # Task 39: see the 401 test above -- same fixed-message change.
    assert "refused the token" in reason


async def test_session_id_header_is_captured_and_echoed_back(monkeypatch):
    seen_headers = []

    async def fake_post(self, url, json=None, headers=None):
        seen_headers.append(dict(headers or {}))
        return _FakeResponse(
            200, {"jsonrpc": "2.0", "id": 1, "result": {}},
            headers={"content-type": "application/json",
                     "Mcp-Session-Id": "sess-123"})

    monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)

    client = McpClient("https://mcp.example/mcp", "tok")
    await client.initialize()
    await client.list_tools()

    assert client.session_id == "sess-123"
    assert "Mcp-Session-Id" not in seen_headers[0]
    assert seen_headers[1]["Mcp-Session-Id"] == "sess-123"


async def test_sse_body_is_parsed_from_its_data_line(monkeypatch):
    sse = 'event: message\ndata: {"jsonrpc": "2.0", "id": 1, "result": {"ok": true}}\n\n'
    _install(monkeypatch, _FakeResponse(
        200, text=sse, headers={"content-type": "text/event-stream"}))

    client = McpClient("https://mcp.example/mcp")
    result, reason = await client.call_tool("x")

    assert reason is None
    assert result == {"ok": True}


async def test_sse_body_joins_a_multi_line_data_field(monkeypatch):
    """Per the SSE spec, one event's `data:` field can be spread over
    several consecutive `data:` lines, joined with `\n` before parsing --
    not parsed line by line, which would try (and fail) to `json.loads`
    each half on its own and silently lose the message."""
    sse = ('data: {"jsonrpc": "2.0", "id": 1,\n'
          'data: "result": {"ok": true}}\n\n')
    _install(monkeypatch, _FakeResponse(
        200, text=sse, headers={"content-type": "text/event-stream"}))

    client = McpClient("https://mcp.example/mcp")
    result, reason = await client.call_tool("x")

    assert reason is None
    assert result == {"ok": True}


async def test_sse_body_with_an_interleaved_event_line_still_joins(monkeypatch):
    """An `event:` line between two `data:` lines of the same message
    belongs to the event but is not part of its data and must not end the
    accumulation or break the join."""
    sse = ('data: {"jsonrpc": "2.0", "id": 1,\n'
          'event: message\n'
          'data: "result": {"ok": true}}\n\n')
    _install(monkeypatch, _FakeResponse(
        200, text=sse, headers={"content-type": "text/event-stream"}))

    client = McpClient("https://mcp.example/mcp")
    result, reason = await client.call_tool("x")

    assert reason is None
    assert result == {"ok": True}


async def test_jsonrpc_error_is_surfaced_as_a_reason(monkeypatch):
    _install(monkeypatch, _FakeResponse(
        200, {"jsonrpc": "2.0", "id": 1,
             "error": {"code": -32000, "message": "insufficient credits"}},
        headers={"content-type": "application/json"}))

    client = McpClient("https://mcp.example/mcp")
    result, reason = await client.call_tool("render_video", {})

    assert result is None
    assert "insufficient credits" in reason


async def test_unreachable_server_is_a_reason_not_an_exception(monkeypatch):
    async def fake_post(self, url, json=None, headers=None):
        raise httpx.ConnectError("boom")

    monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)

    client = McpClient("https://mcp.example/mcp")
    result, reason = await client.call_tool("x")

    assert result is None
    assert reason is not None
