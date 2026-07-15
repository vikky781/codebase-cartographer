"""Contract tests for the portable MCP client wrappers."""

from __future__ import annotations

import json
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _read_json(relative_path: str) -> dict:
    return json.loads((PROJECT_ROOT / relative_path).read_text(encoding="utf-8"))


def _assert_stdio_server(config: dict, *, declares_transport: bool) -> None:
    server = config["mcpServers"]["codebase-cartographer"]
    assert server["command"] == "cartographer-mcp"
    assert server["args"] == []
    if declares_transport:
        assert server["type"] == "stdio"


def test_client_neutral_mcp_config_uses_the_canonical_launcher() -> None:
    _assert_stdio_server(_read_json(".mcp.json"), declares_transport=True)


def test_claude_code_plugin_and_marketplace_are_wired() -> None:
    manifest = _read_json("integrations/claude-code/.claude-plugin/plugin.json")
    marketplace = _read_json(".claude-plugin/marketplace.json")

    assert manifest["name"] == "codebase-cartographer"
    _assert_stdio_server(_read_json("integrations/claude-code/.mcp.json"), declares_transport=True)
    assert marketplace["plugins"][0]["source"] == "./integrations/claude-code"


def test_antigravity_plugin_uses_a_local_stdio_server() -> None:
    manifest = _read_json("integrations/antigravity/plugin.json")

    assert manifest["name"] == "codebase-cartographer"
    _assert_stdio_server(
        _read_json("integrations/antigravity/mcp_config.json"), declares_transport=False
    )
