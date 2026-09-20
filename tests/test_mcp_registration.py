"""Integration tests: register() wires the expected MCP tool names.

We do NOT spin up a real MCP transport — we just call ``mcp.list_tools()``
and ``mcp.call_tool()`` on a fresh ``MCPServer`` instance.
"""
from __future__ import annotations

import json

import pytest
from mcp.server.mcpserver import MCPServer

from architecture_mcp import sources as S

_EXPECTED_SOURCE_TOOL_NAMES = {
    "source_create",
    "source_get",
    "source_list",
    "npa_attach",
    "npa_get",
    "architecture_create",
    "architecture_get",
    "architecture_list",
}


@pytest.mark.asyncio
async def test_sources_register_exposes_expected_tools(db) -> None:
    mcp = MCPServer("test")
    S.register(mcp, db)
    tools = await mcp.list_tools()
    names = {t.name for t in tools}
    missing = _EXPECTED_SOURCE_TOOL_NAMES - names
    assert missing == set(), f"missing tools: {sorted(missing)}"


@pytest.mark.asyncio
async def test_source_create_via_mcp_tool(db) -> None:
    mcp = MCPServer("test")
    S.register(mcp, db)
    res = await mcp.call_tool(
        "source_create",
        {"source_type": "TD", "title": "via-mcp-tool"},
    )
    assert res.is_error is False
    # MCP wraps dict results as a JSON text block.
    first = res.content[0]
    assert getattr(first, "type", None) == "text"
    payload = json.loads(first.text)
    assert payload["ok"] is True
    assert payload["title"] == "via-mcp-tool"

    # And the record must be visible through the domain API too.
    got = S.list_sources(db, limit=5)
    assert any(item["title"] == "via-mcp-tool" for item in got["items"])


@pytest.mark.asyncio
async def test_source_get_via_mcp_tool_missing(db) -> None:
    mcp = MCPServer("test")
    S.register(mcp, db)
    res = await mcp.call_tool("source_get", {"source_id": 99999})
    first = res.content[0]
    payload = json.loads(first.text)
    assert payload["ok"] is False
    assert payload["code"] == "NOT_FOUND"
