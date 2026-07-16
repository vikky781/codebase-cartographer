"""Contract checks for the MCP surface that Codex consumes."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
from fastmcp import Client
from fastmcp.client.transports import StdioTransport

from codebase_cartographer import server
from codebase_cartographer.server import mcp


@pytest.mark.asyncio
async def test_mcp_server_exposes_all_seven_tools() -> None:
    """FastMCP must advertise the complete server API to an MCP client."""
    async with Client(mcp) as client:
        tools = await client.list_tools()

    assert {tool.name for tool in tools} == {
        "analyze_repo",
        "search_graph",
        "trace_flow",
        "find_issues",
        "get_metrics",
        "visualize",
        "get_git_context",
    }


@pytest.mark.asyncio
async def test_mcp_client_can_analyze_and_query_the_fixture() -> None:
    """Advertising tools is insufficient: an MCP client must complete an evidence round trip."""
    fixture = Path(__file__).parent.parent / "fixtures" / "sample_repo"
    server._reset_analysis_state()
    try:
        async with Client(mcp) as client:
            analysis = await client.call_tool(
                "analyze_repo",
                {"repo_path": str(fixture.resolve()), "use_cache": False},
            )
            payload = json.loads(analysis.data)
            search = await client.call_tool("search_graph", {"query": "authenticate"})
            entities = json.loads(search.data)
    finally:
        server._reset_analysis_state()

    assert analysis.is_error is False
    assert payload["status"] == "success"
    assert payload["files_analyzed"] == 11
    assert payload["coverage"]["tree_sitter_files"] == 11
    assert payload["coverage"]["call_edges_observed"] >= payload["coverage"]["call_edges_resolved"]
    assert any(entity["name"] == "AuthService.authenticate" for entity in entities)


@pytest.mark.asyncio
async def test_mcp_main_starts_a_clean_stdio_server() -> None:
    """The entry-point main function must be usable by Codex's stdio transport."""
    transport = StdioTransport(
        command=sys.executable,
        args=["-m", "codebase_cartographer.server"],
        cwd=str(Path.cwd()),
    )
    async with Client(transport, timeout=20) as client:
        tools = await client.list_tools()

    assert "analyze_repo" in {tool.name for tool in tools}


@pytest.mark.asyncio
async def test_cli_mcp_launcher_starts_the_stdio_server() -> None:
    """The compatibility CLI launcher must be usable during editable development."""
    executable_name = "cartographer.exe" if sys.platform == "win32" else "cartographer"
    command = Path(sys.executable).parent / executable_name
    assert command.is_file()

    transport = StdioTransport(command=str(command), args=["mcp"], cwd=str(Path.cwd()))
    async with Client(transport, timeout=20) as client:
        tools = await client.list_tools()

    assert "analyze_repo" in {tool.name for tool in tools}
