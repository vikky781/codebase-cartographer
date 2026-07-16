"""Regression tests for the installable Codex plugin bundle."""

from __future__ import annotations

import json
import tomllib
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
PLUGIN_ROOT = PROJECT_ROOT / "plugins" / "codebase-cartographer"


def test_plugin_manifest_declares_the_mcp_server_and_usage_skill() -> None:
    manifest = json.loads(
        (PLUGIN_ROOT / ".codex-plugin" / "plugin.json").read_text(encoding="utf-8")
    )

    assert manifest["name"] == "codebase-cartographer"
    assert manifest["mcpServers"] == "./.mcp.json"
    assert manifest["skills"] == "./skills/"
    assert manifest["interface"]["defaultPrompt"]
    assert (PLUGIN_ROOT / "skills" / "codebase-cartographer" / "SKILL.md").is_file()


def test_plugin_mcp_configuration_launches_the_packaged_server_command() -> None:
    config = json.loads((PLUGIN_ROOT / ".mcp.json").read_text(encoding="utf-8"))
    server = config["mcpServers"]["codebase-cartographer"]
    project = tomllib.loads((PROJECT_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    manifest = json.loads(
        (PLUGIN_ROOT / ".codex-plugin" / "plugin.json").read_text(encoding="utf-8")
    )

    assert server["command"] == "cartographer-mcp"
    assert server["args"] == []
    assert project["project"]["scripts"]["cartographer-mcp"] == (
        "codebase_cartographer.server:main"
    )
    assert manifest["version"].split("+", 1)[0] == project["project"]["version"]


def test_canonical_project_marketplace_points_to_the_plugin_source() -> None:
    marketplace_path = PROJECT_ROOT / ".agents" / "plugins" / "marketplace.json"
    marketplace = json.loads(marketplace_path.read_text(encoding="utf-8"))
    entry = next(item for item in marketplace["plugins"] if item["name"] == "codebase-cartographer")

    assert entry["source"] == {"source": "local", "path": "./plugins/codebase-cartographer"}
    assert entry["policy"]["installation"] == "AVAILABLE"
    assert entry["policy"]["authentication"] == "ON_INSTALL"
    assert not (PROJECT_ROOT / "marketplace.json").exists()
