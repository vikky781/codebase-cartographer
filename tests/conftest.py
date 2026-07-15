import sys
from pathlib import Path

import pytest

# Add src to path so imports work
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))


@pytest.fixture
def sample_repo_path():
    """Path to the sample test repository."""
    path = Path(__file__).parent.parent / "fixtures" / "sample_repo"
    assert path.exists(), f"Sample repo not found at {path}"
    return str(path.resolve())


@pytest.fixture
def parsed_files(sample_repo_path):
    """Parse the sample repo and return parsed files."""
    from codebase_cartographer.code_parser import parse_repository

    files, warnings = parse_repository(sample_repo_path)
    return files


@pytest.fixture
def built_graph(parsed_files, sample_repo_path):
    """Build and return a CodeGraph from the sample repo."""
    from codebase_cartographer.graph import CodeGraph

    graph = CodeGraph()
    graph.build(parsed_files, sample_repo_path, "test-hash-123")
    return graph
