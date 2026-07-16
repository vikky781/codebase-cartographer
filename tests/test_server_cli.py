from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest
from click.testing import CliRunner

from codebase_cartographer import server
from codebase_cartographer.cli import main
from codebase_cartographer.graph import get_graph


def _response(raw_result: str) -> dict | list:
    """Decode a JSON response from a server tool."""
    return json.loads(raw_result)


@pytest.fixture(autouse=True)
def clear_server_state():
    """Ensure global MCP state cannot leak between integration tests."""
    server._reset_analysis_state()
    yield
    server._reset_analysis_state()


@pytest.fixture
def git_repo(tmp_path: Path) -> Path:
    """Create a small, clean Git repository suitable for cache tests."""
    if shutil.which("git") is None:
        pytest.skip("Git is required for cache integration tests")

    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "app.py").write_text(
        "def hello() -> str:\n    return 'hello'\n",
        encoding="utf-8",
    )
    for command in (
        ["git", "init"],
        ["git", "config", "user.email", "tests@example.com"],
        ["git", "config", "user.name", "Test User"],
        ["git", "add", "app.py"],
        ["git", "commit", "-m", "Initial commit"],
    ):
        subprocess.run(command, cwd=repo, check=True, capture_output=True, text=True)
    return repo


def test_fastmcp_server_imports_with_fastmcp_v3():
    """Server construction must not use FastMCP 3's removed ``description`` keyword."""
    assert server.mcp.name == "CodebaseCartographer"


def test_analyze_requires_an_absolute_repository_path(sample_repo_path: str):
    relative_path = os.path.relpath(sample_repo_path, Path.cwd())

    result = _response(server.analyze_repo(relative_path))

    assert result["status"] == "error"
    assert result["error_type"] == "invalid_input"
    assert "absolute" in result["message"].lower()
    assert get_graph().is_built is False


def test_analyze_rejects_scope_outside_repository(sample_repo_path: str):
    result = _response(server.analyze_repo(sample_repo_path, ".."))

    assert result["status"] == "error"
    assert result["error_type"] == "invalid_input"
    assert "within" in result["message"].lower()
    assert get_graph().is_built is False


def test_scoped_analysis_discloses_that_the_graph_is_partial(sample_repo_path: str):
    result = _response(server.analyze_repo(sample_repo_path, "auth", use_cache=False))

    assert result["status"] == "success"
    assert result["analysis_scope"] == "auth"
    assert result["is_partial"] is True
    assert any("limited to 'auth'" in warning for warning in result["warnings"])


def test_failed_analysis_clears_existing_graph(sample_repo_path: str, tmp_path: Path):
    successful = _response(server.analyze_repo(sample_repo_path))
    assert successful["status"] == "success"
    assert get_graph().is_built is True

    failed = _response(server.analyze_repo(str(tmp_path / "missing-repository")))
    subsequent_query = _response(server.search_graph("login"))

    assert failed["status"] == "error"
    assert get_graph().is_built is False
    assert subsequent_query["error_type"] == "not_analyzed"


def test_git_context_accepts_only_analyzed_relative_paths(sample_repo_path: str):
    assert _response(server.analyze_repo(sample_repo_path))["status"] == "success"

    absolute_file = str((Path(sample_repo_path) / "main.py").resolve())
    absolute_result = _response(server.get_git_context(absolute_file))
    missing_result = _response(server.get_git_context("missing.py"))
    valid_result = _response(server.get_git_context("main.py"))

    assert absolute_result["error_type"] == "invalid_input"
    assert missing_result["error_type"] == "not_found"
    assert valid_result["file_path"] == "main.py"


def test_cli_query_requires_repo_option():
    result = CliRunner().invoke(main, ["search", "login"])

    assert result.exit_code != 0
    assert "Pass --repo" in result.output


def test_cli_query_reuses_a_clean_git_cache(git_repo: Path, monkeypatch: pytest.MonkeyPatch):
    runner = CliRunner()
    analysis = runner.invoke(main, ["analyze", str(git_repo)])
    assert analysis.exit_code == 0, analysis.output
    assert (git_repo / ".cartographer_cache" / "graph_cache.json").exists()

    server._reset_analysis_state()

    def unexpected_parse(*args, **kwargs):
        raise AssertionError("The clean Git cache should have been reused")

    monkeypatch.setattr(server, "parse_repository", unexpected_parse)
    search = runner.invoke(main, ["--repo", str(git_repo), "search", "hello"])

    assert search.exit_code == 0, search.output
    assert "hello" in search.output


def test_analyze_can_disable_cache_writes(git_repo: Path):
    """Privacy-sensitive analysis must have a no-write path even for clean Git repositories."""
    result = _response(server.analyze_repo(str(git_repo), use_cache=False))

    assert result["status"] == "success"
    assert not (git_repo / ".cartographer_cache").exists()
    assert any("cache was disabled" in warning for warning in result["warnings"])


def test_dirty_git_repository_bypasses_cache(git_repo: Path):
    assert _response(server.analyze_repo(str(git_repo)))["status"] == "success"
    server._reset_analysis_state()
    (git_repo / "app.py").write_text(
        "def hello() -> str:\n    return 'changed'\n",
        encoding="utf-8",
    )

    result = _response(server.analyze_repo(str(git_repo)))

    assert result["status"] == "success"
    assert any("uncommitted or untracked" in warning for warning in result["warnings"])
