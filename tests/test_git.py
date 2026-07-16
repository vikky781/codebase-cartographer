from pathlib import Path
from tempfile import TemporaryDirectory

import pytest
from git import Repo

from codebase_cartographer.config import get_config
from codebase_cartographer.git_analyzer import GitAnalyzer


class TestGitAnalyzerNonGitRepo:
    """The fixture is NOT a git repo, so all methods should return safe defaults."""

    def test_initializes_without_crash(self, sample_repo_path):
        analyzer = GitAnalyzer(sample_repo_path)
        assert analyzer.available is False

    def test_blame_returns_empty(self, sample_repo_path):
        analyzer = GitAnalyzer(sample_repo_path)
        result = analyzer.get_file_blame("main.py")
        assert result == []

    def test_commits_returns_empty(self, sample_repo_path):
        analyzer = GitAnalyzer(sample_repo_path)
        result = analyzer.get_file_commits("main.py")
        assert result == []

    def test_change_frequency_returns_zero(self, sample_repo_path):
        analyzer = GitAnalyzer(sample_repo_path)
        result = analyzer.get_file_change_frequency("main.py")
        assert result == 0

    def test_file_age_returns_none(self, sample_repo_path):
        analyzer = GitAnalyzer(sample_repo_path)
        result = analyzer.get_file_age("main.py")
        assert result is None

    def test_co_changed_returns_empty(self, sample_repo_path):
        analyzer = GitAnalyzer(sample_repo_path)
        result = analyzer.get_co_changed_files("main.py")
        assert result == []

    def test_head_hash_returns_empty(self, sample_repo_path):
        analyzer = GitAnalyzer(sample_repo_path)
        result = analyzer.get_head_hash()
        assert result == ""

    def test_full_context_returns_defaults(self, sample_repo_path):
        analyzer = GitAnalyzer(sample_repo_path)
        result = analyzer.get_full_context("main.py")
        assert result.change_frequency == 0
        assert result.authors == []
        assert result.recent_commits == []


@pytest.fixture
def local_git_repo():
    """Create a small tracked repository in the writable workspace."""
    with TemporaryDirectory(dir=Path.cwd(), prefix="git-analyzer-test-") as directory:
        root = Path(directory)
        repo = Repo.init(root)
        with repo.config_writer() as config:
            config.set_value("user", "name", "Test Author")
            config.set_value("user", "email", "tests@example.com")

        source = root / "tracked.py"
        source.write_text("def hello():\n    return 'one'\n", encoding="utf-8")
        repo.index.add(["tracked.py"])
        repo.index.commit("Initial version")
        source.write_text("def hello():\n    return 'two'\n", encoding="utf-8")
        repo.index.add(["tracked.py"])
        repo.index.commit("Second version")
        repo.close()
        yield root


@pytest.fixture
def local_git_analyzer(local_git_repo):
    """Open the fixture repository and reliably release its Windows file handles."""
    analyzer = GitAnalyzer(local_git_repo)
    yield analyzer
    analyzer.close()


class TestGitAnalyzerSafetyLimits:
    """Git paths and history work must remain bounded and repository-contained."""

    def test_rejects_path_outside_the_repository(self, local_git_analyzer):
        analyzer = local_git_analyzer

        assert analyzer.available is True
        assert analyzer.get_file_blame("../tracked.py") == []
        assert analyzer.get_file_commits("../tracked.py") == []
        assert analyzer.get_file_change_frequency("../tracked.py") == 0
        assert analyzer.get_file_age("../tracked.py") is None
        assert analyzer.get_co_changed_files("../tracked.py") == []

    def test_skips_oversize_blame_and_bounds_history(self, local_git_analyzer, monkeypatch):
        analyzer = local_git_analyzer
        config = get_config()
        monkeypatch.setattr(config, "max_git_blame_file_size_bytes", 1)
        monkeypatch.setattr(config, "max_git_history_commits", 1)

        assert analyzer.get_file_blame("tracked.py") == []
        assert len(analyzer.get_file_commits("tracked.py", max_commits=100)) == 1
        assert analyzer.get_file_change_frequency("tracked.py") == 1

    def test_entity_context_explicitly_reports_file_level_history(self, local_git_analyzer):
        analyzer = local_git_analyzer

        context = analyzer.get_full_context("tracked.py", entity_name="hello")

        assert "up to" in context.source
        assert "file-level; entity filtering unavailable" in context.source
