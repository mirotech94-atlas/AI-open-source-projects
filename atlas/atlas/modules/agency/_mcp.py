"""Re-export of the shared MCP client.

This client (AGENCY.md §2.4) started here as Task 3's implementation. Task
39 reconciled it with `content.render_video`'s parallel, smaller client
(Task 35) into one shared client at `atlas.modules._mcp`, keeping this
module's semantics. This file now only re-exports `McpClient` so
`from atlas.modules.agency._mcp import McpClient` — and anything else
already importing this path — keeps working. See `atlas/modules/_mcp.py`
for the client itself and the two callers' compatibility notes.
"""
from __future__ import annotations

from .._mcp import McpClient

__all__ = ["McpClient"]
