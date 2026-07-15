import pytest

from codebase_cartographer.issue_detector import IssueDetector


class TestIssueDetector:
    @pytest.fixture
    def detector(self, built_graph):
        return IssueDetector(built_graph)

    def test_detect_all_runs(self, detector):
        issues = detector.detect_all()
        assert isinstance(issues, list)

    def test_finds_circular_dependencies(self, detector):
        issues = detector.detect_all(issue_types=["circular_dependency"])
        assert len(issues) > 0, "Expected circular dependency between models and utils"
        assert all(i.type == "circular_dependency" for i in issues)

    def test_finds_dead_code(self, detector):
        issues = detector.detect_all(issue_types=["dead_code"])
        assert len(issues) > 0, "Expected dead code issues"

    def test_finds_orphan_files(self, detector):
        issues = detector.detect_all(issue_types=["orphan_file"])
        assert len(issues) > 0, "Expected unused.py to be detected as orphan"

    def test_issues_have_required_fields(self, detector):
        issues = detector.detect_all()
        for issue in issues:
            assert issue.type
            assert issue.severity in ("high", "medium", "low")
            assert issue.description
            assert issue.source

    def test_filter_by_type(self, detector):
        issues = detector.detect_all(issue_types=["dead_code"])
        assert all(i.type == "dead_code" for i in issues)

    def test_issues_sorted_by_severity(self, detector):
        issues = detector.detect_all()
        severity_order = {"high": 0, "medium": 1, "low": 2}
        for i in range(len(issues) - 1):
            assert severity_order[issues[i].severity] <= severity_order[issues[i + 1].severity]
