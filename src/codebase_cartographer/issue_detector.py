from __future__ import annotations

import logging

from .config import get_config
from .graph import CodeGraph
from .models import CodeEntity, Issue

logger = logging.getLogger(__name__)


class IssueDetector:
    """Format raw graph findings as clear, actionable architectural issues."""

    def __init__(self, graph: CodeGraph):
        """Create an issue detector for an already-built code graph."""
        self.graph = graph

    def detect_all(self, issue_types: list[str] | None = None) -> list[Issue]:
        """Detect all requested issue types and return severity-sorted results."""
        try:
            all_types = [
                "circular_dependency",
                "dead_code",
                "god_class",
                "bottleneck",
                "orphan_file",
                "high_coupling",
            ]
            types_to_check = issue_types if issue_types is not None else all_types
            valid_types = [issue_type for issue_type in types_to_check if issue_type in all_types]
            detectors = {
                "circular_dependency": self._detect_circular_dependencies,
                "dead_code": self._detect_dead_code,
                "god_class": self._detect_god_classes,
                "bottleneck": self._detect_bottlenecks,
                "orphan_file": self._detect_orphan_files,
                "high_coupling": self._detect_high_coupling,
            }

            results: list[Issue] = []
            for issue_type in valid_types:
                try:
                    results.extend(detectors[issue_type]())
                except Exception as exc:
                    logger.exception("Issue detection failed for %s", issue_type)
                    results.extend(self._error_issue(issue_type, exc))

            severity_order = {"high": 0, "medium": 1, "low": 2}
            results.sort(key=lambda issue: severity_order.get(issue.severity, 3))
            return results[: get_config().max_issues]
        except Exception as exc:
            logger.exception("Unable to run issue detection")
            return self._error_issue("dead_code", exc)

    def _detect_circular_dependencies(self) -> list[Issue]:
        """Format module import cycles as high-severity issues."""
        try:
            issues: list[Issue] = []
            for cycle in self.graph.find_cycles():
                if not cycle:
                    continue
                cycle_path = [*cycle, cycle[0]]
                suggestion = (
                    "Extract shared logic into a new module that both can import."
                    if len(cycle) == 2
                    else (
                        "Break the cycle by introducing an interface or moving shared types "
                        "to a common module."
                    )
                )
                issues.append(
                    Issue(
                        type="circular_dependency",
                        severity="high",
                        description=f"Circular dependency: {' → '.join(cycle_path)}",
                        file_paths=list(cycle),
                        suggestion=suggestion,
                        source="networkx-scc",
                    )
                )
            return issues
        except Exception as exc:
            logger.exception("Circular-dependency detection failed")
            return self._error_issue("circular_dependency", exc)

    def _detect_dead_code(self) -> list[Issue]:
        """Group unreferenced functions and classes into one issue per file."""
        try:
            grouped: dict[str, list[CodeEntity]] = {}
            for entity in self.graph.find_dead_code():
                grouped.setdefault(entity.file_path, []).append(entity)

            issues: list[Issue] = []
            for file_path, entities in sorted(grouped.items()):
                severity = "medium" if len(entities) >= 3 else "low"
                names = ", ".join(entity.name for entity in entities)
                issues.append(
                    Issue(
                        type="dead_code",
                        severity=severity,
                        description=(
                            f"Potential dead code: {len(entities)} function(s)/class(es) with "
                            "no inbound static call edge in "
                            f"{file_path}: {names}"
                        ),
                        entities=entities,
                        file_paths=[file_path],
                        suggestion=(
                            "Verify framework wiring, dynamic dispatch, reflection, and external "
                            "entry points before considering removal."
                        ),
                        source="networkx-indegree",
                    )
                )
            return issues
        except Exception as exc:
            logger.exception("Dead-code detection failed")
            return self._error_issue("dead_code", exc)

    def _detect_god_classes(self) -> list[Issue]:
        """Format high-fan-in/high-fan-out classes as coupling issues."""
        try:
            issues: list[Issue] = []
            for entity, fan_in, fan_out in self.graph.find_god_classes():
                total = fan_in + fan_out
                issues.append(
                    Issue(
                        type="god_class",
                        severity="high" if total > 20 else "medium",
                        description=(
                            f"High-coupling class candidate '{entity.name}' in "
                            f"{entity.file_path}: {fan_in} "
                            f"dependents, {fan_out} dependencies (total coupling: {total})"
                        ),
                        entities=[entity],
                        file_paths=[entity.file_path],
                        suggestion=(
                            "Consider applying the Single Responsibility Principle. "
                            "Split this class "
                            "into smaller, focused classes."
                        ),
                        source="networkx-degree",
                    )
                )
            return issues
        except Exception as exc:
            logger.exception("God-class detection failed")
            return self._error_issue("god_class", exc)

    def _detect_bottlenecks(self) -> list[Issue]:
        """Format high-centrality modules as potential single points of failure."""
        try:
            issues: list[Issue] = []
            for result in self.graph.find_bottlenecks():
                issues.append(
                    Issue(
                        type="bottleneck",
                        severity="high" if result.score > 0.3 else "medium",
                        description=(
                            f"Potential bottleneck: '{result.entity_name}' has high betweenness "
                            "centrality "
                            f"({result.score:.3f}). Many paths through the codebase go through "
                            "this module."
                        ),
                        file_paths=[result.file_path],
                        suggestion=(
                            "This module may be a structural bridge. Validate runtime traffic, "
                            "then consider splitting responsibilities or introducing abstractions "
                            "to reduce centrality."
                        ),
                        source="networkx-centrality",
                    )
                )
            return issues
        except Exception as exc:
            logger.exception("Bottleneck detection failed")
            return self._error_issue("bottleneck", exc)

    def _detect_orphan_files(self) -> list[Issue]:
        """Format unimported source files as a concise low-severity issue."""
        try:
            orphans = self.graph.find_orphan_files()
            if not orphans:
                return []

            displayed = orphans[:10]
            listed_names = ", ".join(displayed)
            if len(orphans) > len(displayed):
                listed_names += f", and {len(orphans) - len(displayed)} more"
            return [
                Issue(
                    type="orphan_file",
                        severity="low",
                        description=(
                            f"Found {len(orphans)} potential orphan file(s) not imported by "
                            f"another static module: {listed_names}"
                    ),
                    file_paths=orphans,
                    suggestion=(
                        "These files may be entry points, scripts, or genuinely unused. "
                        "Verify and remove if unnecessary."
                    ),
                    source="networkx-indegree",
                )
            ]
        except Exception as exc:
            logger.exception("Orphan-file detection failed")
            return self._error_issue("orphan_file", exc)

    def _detect_high_coupling(self) -> list[Issue]:
        """Format heavily cross-referenced module pairs as coupling issues."""
        try:
            issues: list[Issue] = []
            for result in self.graph.get_coupling(top_n=10):
                if result.score <= 5:
                    continue
                file_paths = [
                    path.strip() for path in result.entity_name.split(" <-> ", 1) if path.strip()
                ]
                issues.append(
                    Issue(
                        type="high_coupling",
                        severity="high" if result.score > 10 else "medium",
                        description=(
                            f"High static coupling candidate between {result.entity_name}: "
                            f"{int(result.score)} cross-references"
                        ),
                        file_paths=file_paths,
                        suggestion=(
                            "Consider introducing an interface or event system to decouple "
                            "these modules."
                        ),
                        source="networkx-edge-count",
                    )
                )
            return issues
        except Exception as exc:
            logger.exception("High-coupling detection failed")
            return self._error_issue("high_coupling", exc)

    @staticmethod
    def _error_issue(issue_type: str, error: Exception) -> list[Issue]:
        """Return a safe issue describing a failed individual detection pass."""
        try:
            return [
                Issue(
                    type=issue_type,  # type: ignore[arg-type]
                    severity="low",
                    description=f"Unable to detect {issue_type}: {error}",
                    suggestion="Review the repository analysis and try again.",
                    source="issue-detector",
                )
            ]
        except Exception:
            return []
