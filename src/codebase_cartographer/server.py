from __future__ import annotations

import json
from collections.abc import Callable
from functools import wraps
from pathlib import Path
from threading import RLock

from fastmcp import FastMCP

from .code_parser import parse_repository
from .config import get_config
from .git_analyzer import GitAnalyzer
from .graph import CodeGraph, get_graph
from .issue_detector import IssueDetector
from .models import (
    AnalysisCoverage,
    AnalyzeInput,
    AnalyzeOutput,
    CodeEntity,
    EdgeCounts,
    EntityCounts,
    FindIssuesInput,
    GitContextInput,
    GitContextOutput,
    HealthSummary,
    Issue,
    MetricResult,
    MetricsInput,
    SearchInput,
    ToolError,
    TraceInput,
    TraceOutput,
    VisualizeInput,
)
from .visualizer import MermaidVisualizer

mcp = FastMCP(
    "CodebaseCartographer",
    instructions=(
        "MCP server that builds a knowledge graph of any codebase using Tree-sitter AST "
        "parsing, git history analysis, and NetworkX graph algorithms. Provides architectural "
        "insights, dependency tracing, issue detection, and visualization. Zero API calls — "
        "all computation is local."
    ),
)

_git_analyzer: GitAnalyzer | None = None
_visualizer: MermaidVisualizer | None = None
_state_lock = RLock()


def _with_state_lock(function: Callable[..., str]) -> Callable[..., str]:
    """Serialize tool calls around the process-local active repository state."""

    @wraps(function)
    def locked(*args: object, **kwargs: object) -> str:
        with _state_lock:
            return function(*args, **kwargs)

    return locked


def _tool_error(error_type: str, message: str, suggestion: str = "") -> str:
    """Serialize a consistent tool error without allowing error handling to raise."""
    try:
        return ToolError(
            error_type=error_type,  # type: ignore[arg-type]
            message=message,
            suggestion=suggestion,
        ).model_dump_json(indent=2)
    except Exception:
        return json.dumps(
            {
                "status": "error",
                "error_type": "parse_error",
                "message": message,
                "suggestion": suggestion,
            },
            indent=2,
        )


def _reset_analysis_state() -> None:
    """Clear process-wide state so a failed analysis cannot expose prior results."""
    global _git_analyzer, _visualizer
    if _git_analyzer is not None:
        try:
            _git_analyzer.close()
        except Exception:
            pass
    _git_analyzer = None
    _visualizer = None

    try:
        graph = get_graph()
        graph.graph.clear()
        graph.entities.clear()
        graph.modules.clear()
        graph.repo_path = ""
        graph.repo_hash = ""
        graph.is_built = False
        graph._pagerank_cache = None
        graph._centrality_cache = None
        graph._communities_cache = None
        graph.analysis_coverage = AnalysisCoverage()
    except Exception:
        # Error handling must never turn a failed tool request into a server crash.
        return


def _validate_repository_path(repo_path: str) -> Path:
    """Resolve an existing absolute repository directory or raise ``ValueError``."""
    repository = Path(repo_path).expanduser()
    if not repository.is_absolute():
        raise ValueError("Repository path must be absolute.")
    if not repository.exists() or not repository.is_dir():
        raise ValueError(f"Repository path does not exist or is not a directory: {repo_path}")
    return repository.resolve()


def _resolve_scope(repository: Path, scope: str | None) -> str | None:
    """Normalize a scope while preventing absolute paths and repository escapes."""
    if scope is None or not scope.strip():
        return None

    scope_path = Path(scope).expanduser()
    if scope_path.is_absolute() or scope_path.drive:
        raise ValueError("Scope must be a relative subdirectory of the repository root.")

    resolved_scope = (repository / scope_path).resolve()
    try:
        relative_scope = resolved_scope.relative_to(repository)
    except ValueError as exc:
        raise ValueError("Scope must stay within the repository root.") from exc

    if not resolved_scope.is_dir():
        raise ValueError(f"Scope does not exist or is not a directory: {scope}")

    normalized_scope = relative_scope.as_posix()
    return None if normalized_scope in {"", "."} else normalized_scope


def _can_reuse_cache(git_analyzer: GitAnalyzer, repo_hash: str) -> bool:
    """Return whether a Git-validated cache can safely represent the working tree."""
    if not repo_hash or not git_analyzer.available or git_analyzer.repo is None:
        return False
    try:
        if git_analyzer.repo.is_dirty(untracked_files=False):
            return False

        cache_dir = get_config().cache_dir_name.strip("/")
        return not any(
            path.replace("\\", "/") != cache_dir
            and not path.replace("\\", "/").startswith(f"{cache_dir}/")
            for path in git_analyzer.repo.untracked_files
        )
    except Exception:
        return False


def _cache_bypass_warning(git_analyzer: GitAnalyzer, repo_hash: str) -> str:
    """Explain why cache reuse is disabled without leaking an internal failure."""
    if not repo_hash or not git_analyzer.available or git_analyzer.repo is None:
        return "Graph cache was skipped because no Git HEAD is available for validation."
    return "Graph cache was skipped because the repository has uncommitted or untracked changes."


def _language_counts(graph: CodeGraph) -> dict[str, int]:
    """Infer a language breakdown from cached or freshly built module nodes."""
    languages: dict[str, int] = {}
    config = get_config()
    for node_id, attributes in graph.graph.nodes(data=True):
        if attributes.get("type") != "module":
            continue
        extension = Path(str(attributes.get("file_path", node_id))).suffix.lower()
        language = (
            config.get_language_for_extension(extension) or extension.lstrip(".") or "unknown"
        )
        languages[language] = languages.get(language, 0) + 1
    return languages


def _module_paths(graph: CodeGraph) -> list[str]:
    """Return stable repository-relative file paths for graph module nodes."""
    return sorted(
        str(attributes.get("file_path", node_id))
        for node_id, attributes in graph.graph.nodes(data=True)
        if attributes.get("type") == "module"
    )


def _validate_analyzed_file_path(graph: CodeGraph, file_path: str) -> str:
    """Require a repository-contained path that was included in the active graph."""
    if not file_path or not file_path.strip():
        raise ValueError("File path must be a non-empty relative path from the repository root.")

    requested_path = Path(file_path)
    if requested_path.is_absolute() or requested_path.drive:
        raise ValueError("File path must be relative to the repository root.")
    if not graph.repo_path:
        raise ValueError("The analyzed repository root is unavailable.")

    repository = Path(graph.repo_path).resolve()
    resolved_path = (repository / requested_path).resolve()
    try:
        normalized_path = resolved_path.relative_to(repository).as_posix()
    except ValueError as exc:
        raise ValueError("File path must stay within the analyzed repository root.") from exc

    if normalized_path not in set(_module_paths(graph)):
        raise FileNotFoundError(f"File was not part of the analyzed graph: {normalized_path}")
    return normalized_path


@mcp.tool()
@_with_state_lock
def analyze_repo(repo_path: str, scope: str | None = None, use_cache: bool = True) -> str:
    """Analyze a code repository and build a knowledge graph of its structure.
    Call this FIRST before using any other CodebaseCartographer tool.

    This parses all source files using Tree-sitter AST analysis, extracts
    git history, builds a dependency/call graph, and computes structural
    metrics (PageRank, centrality, static line span, coupling).

    Parameters:
    - repo_path (required): absolute path to the repository root
    - scope (optional): subdirectory to analyze (e.g. "src/") for large repos
    - use_cache (optional): set false to avoid reading or writing `.cartographer_cache`

    Returns: summary statistics including file count, language breakdown,
    entity counts, detected architectural layers, and health indicators
    (circular dependencies, dead code count, god classes).

    Takes 5-30 seconds depending on repo size. Repos over 5000 files should
    use the scope parameter to focus on a subdirectory.

    IMPORTANT: Always call this tool before using other CodebaseCartographer
    tools. If the repo has changed, call this again to refresh the graph."""
    global _git_analyzer, _visualizer
    _reset_analysis_state()
    try:
        try:
            request = AnalyzeInput(repo_path=repo_path, scope=scope, use_cache=use_cache)
            repository = _validate_repository_path(request.repo_path)
            normalized_scope = _resolve_scope(repository, request.scope)
        except Exception as exc:
            return _tool_error(
                "invalid_input",
                str(exc),
                "Provide an absolute repository path and an optional relative scope inside it.",
            )

        git_analyzer = GitAnalyzer(repository)
        repo_hash = git_analyzer.get_head_hash()
        graph = get_graph()
        warnings: list[str] = []
        cache_is_safe = _can_reuse_cache(git_analyzer, repo_hash)
        cache_loaded = (
            request.use_cache
            and normalized_scope is None
            and cache_is_safe
            and graph.load_cache(str(repository))
        )
        if not cache_loaded:
            if not request.use_cache:
                warnings.append(
                    "Graph cache was disabled for this analysis; no cache was read or written."
                )
            elif normalized_scope is None and not cache_is_safe:
                warnings.append(_cache_bypass_warning(git_analyzer, repo_hash))
            try:
                parsed_files, parse_warnings = parse_repository(repository, normalized_scope)
            except ValueError as exc:
                return _tool_error(
                    "too_large",
                    str(exc),
                    "Use the scope parameter to analyze a smaller subdirectory.",
                )
            warnings.extend(parse_warnings)
            graph.build(parsed_files, str(repository), repo_hash)
            if request.use_cache and normalized_scope is None and cache_is_safe:
                graph.save_cache(str(repository))

        if normalized_scope is not None:
            warnings.append(
                f"Analysis was limited to '{normalized_scope}'; files outside this scope were not "
                "analyzed, so missing relationships and issue candidates are incomplete."
            )

        _git_analyzer = git_analyzer
        _visualizer = MermaidVisualizer(
            graph, git_analyzer.get_all_file_change_frequencies()
        )
        entity_counts: EntityCounts = graph.get_entity_counts()
        edge_counts: EdgeCounts = graph.get_edge_counts()
        health: HealthSummary = graph.get_health_summary()
        coverage = graph.get_analysis_coverage()
        if coverage.regex_fallback_files:
            warnings.append(
                f"{coverage.regex_fallback_files} file(s) used regex fallback: declaration and "
                "import inventory only; no call-graph or complexity evidence was produced."
            )
        if coverage.call_edges_ambiguous:
            warnings.append(
                f"{coverage.call_edges_ambiguous} call edge(s) were left unresolved because "
                "multiple local targets were plausible."
            )
        if coverage.import_edges_ambiguous:
            warnings.append(
                f"{coverage.import_edges_ambiguous} import edge(s) were left unresolved because "
                "multiple local modules were plausible."
            )
        output = AnalyzeOutput(
            analysis_scope=normalized_scope,
            is_partial=normalized_scope is not None,
            files_analyzed=entity_counts.modules,
            languages=_language_counts(graph),
            entities=entity_counts,
            edges=edge_counts,
            detected_layers=graph.detect_layers(),
            health=health,
            coverage=coverage,
            repo_hash=graph.repo_hash or repo_hash,
            warnings=warnings,
        )
        return output.model_dump_json(indent=2)
    except Exception as exc:
        _reset_analysis_state()
        return _tool_error(
            "parse_error",
            f"Unable to analyze repository: {exc}",
            "Check the repository path and try again, optionally using a narrower scope.",
        )


@mcp.tool()
@_with_state_lock
def search_graph(query: str, entity_type: str | None = None, limit: int = 20) -> str:
    """Search the codebase knowledge graph for functions, classes, or modules
    matching a query. Use this to find code entities related to a concept
    or to discover what exists in a particular area of the codebase.

    Supports: name matching (fuzzy), type filtering, file path pattern.

    Parameters:
    - query (required): search term (e.g. "auth", "payment", "User")
    - entity_type (optional): filter by "function", "class", or "module"
    - limit (optional): max results, default 20

    Returns: list of matching entities with file location, type, signature,
    complexity score, and immediate connections (calls and called_by lists).

    IMPORTANT: Always use this tool to verify facts about the codebase.
    Do not rely on your memory of previous tool calls — re-query to confirm."""
    try:
        graph = get_graph()
        if not graph.is_built:
            return _tool_error(
                "not_analyzed", "No repo has been analyzed yet", "Call analyze_repo first"
            )
        clamped_limit = max(1, min(limit, get_config().max_output_entities))
        request = SearchInput(query=query, entity_type=entity_type, limit=clamped_limit)
        results: list[CodeEntity] = graph.search(request.query, request.entity_type, request.limit)
        return json.dumps([entity.model_dump(mode="json") for entity in results], indent=2)
    except Exception as exc:
        return _tool_error(
            "invalid_input", f"Unable to search graph: {exc}", "Check the query and try again."
        )


@mcp.tool()
@_with_state_lock
def trace_flow(entity_name: str, direction: str = "forward", max_depth: int = 5) -> str:
    """Trace the call chain or dependency flow starting from a specific
    function or module. Use this to understand how data or control flows
    through the system.

    Parameters:
    - entity_name (required): name of the starting function/class/module
    - direction (optional): "forward" (what it calls), "backward" (what calls it),
      or "both". Default: "forward"
    - max_depth (optional): how many levels to trace. Default: 5

    Returns: ordered list of steps in the chain. Each step includes the
    entity name, type, file location, line number, and relationship type
    (calls/imports/inherits).

    IMPORTANT: Always use this tool to verify facts about the codebase.
    Do not rely on your memory of previous tool calls — re-query to confirm."""
    try:
        graph = get_graph()
        if not graph.is_built:
            return _tool_error(
                "not_analyzed", "No repo has been analyzed yet", "Call analyze_repo first"
            )
        if direction not in {"forward", "backward", "both"}:
            return _tool_error(
                "invalid_input",
                f"Invalid trace direction: {direction}",
                "Use one of: forward, backward, both.",
            )
        clamped_depth = max(1, min(max_depth, 15))
        request = TraceInput(
            entity_name=entity_name,
            direction=direction,
            max_depth=clamped_depth,
        )
        result: TraceOutput = graph.trace(request.entity_name, request.direction, request.max_depth)
        return result.model_dump_json(indent=2)
    except Exception as exc:
        return _tool_error(
            "invalid_input", f"Unable to trace flow: {exc}", "Check the entity name and try again."
        )


@mcp.tool()
@_with_state_lock
def find_issues(issue_types: str | None = None) -> str:
    """Detect architectural issues and code smells using graph analysis.
    Uses proven graph algorithms — no AI guessing.

    Parameters:
    - issue_types (optional): comma-separated list of types to check.
      If omitted, checks all types.

    Available issue types:
    - circular_dependency: modules that form import cycles
    - dead_code: functions/classes never called or imported
    - god_class: classes with very high fan-in AND fan-out
    - bottleneck: modules with high betweenness centrality
    - orphan_file: source files not imported by anything
    - high_coupling: module pairs with excessive cross-references

    Returns: categorized list of issues with severity (high/medium/low),
    affected files, and specific entities involved.

    IMPORTANT: Always use this tool to verify facts about the codebase.
    Do not rely on your memory of previous tool calls — re-query to confirm."""
    try:
        graph = get_graph()
        if not graph.is_built:
            return _tool_error(
                "not_analyzed", "No repo has been analyzed yet", "Call analyze_repo first"
            )
        parsed_types = (
            [issue_type.strip() for issue_type in issue_types.split(",") if issue_type.strip()]
            if issue_types is not None
            else None
        )
        request = FindIssuesInput(issue_types=parsed_types)
        detector = IssueDetector(graph)
        issues: list[Issue] = detector.detect_all(request.issue_types)
        return json.dumps([issue.model_dump(mode="json") for issue in issues], indent=2)
    except Exception as exc:
        return _tool_error(
            "invalid_input",
            f"Unable to find issues: {exc}",
            "Check the requested issue types and try again.",
        )


@mcp.tool()
@_with_state_lock
def get_metrics(metric: str = "summary", top_n: int = 15) -> str:
    """Get quantitative metrics about codebase structure. Returns computed
    scores from graph algorithms revealing which code is most critical,
    complex, or problematic.

    Parameters:
    - metric (required): one of:
      - "pagerank": structural importance ranking (like Google PageRank for code)
      - "centrality": betweenness centrality (bridge/bottleneck files)
      - "complexity": static line-span size per function/class (not cyclomatic complexity)
      - "hotspots": most frequently changed files (from git history)
      - "coupling": coupling scores between module pairs
      - "ownership": code ownership by author (from git blame)
      - "summary": overview of all key metrics in one response
    - top_n (optional): how many results to return. Default: 15

    Returns: ranked list of entities with scores, file locations, and
    human-readable interpretation of what the score means.

    IMPORTANT: Always use this tool to verify facts about the codebase.
    Do not rely on your memory of previous tool calls — re-query to confirm."""
    try:
        graph = get_graph()
        if not graph.is_built:
            return _tool_error(
                "not_analyzed", "No repo has been analyzed yet", "Call analyze_repo first"
            )

        valid_metrics = {
            "pagerank",
            "centrality",
            "complexity",
            "hotspots",
            "coupling",
            "ownership",
            "summary",
        }
        if metric not in valid_metrics:
            return _tool_error(
                "invalid_input",
                f"Unknown metric: {metric}",
                (
                    "Use one of: pagerank, centrality, complexity, hotspots, coupling, "
                    "ownership, summary."
                ),
            )
        clamped_top_n = max(1, min(top_n, get_config().max_metrics))
        request = MetricsInput(metric=metric, top_n=clamped_top_n)

        if request.metric == "pagerank":
            results: list[MetricResult] = graph.get_pagerank(request.top_n)
        elif request.metric == "centrality":
            results = graph.get_centrality(request.top_n)
        elif request.metric == "complexity":
            results = graph.get_complexity(request.top_n)
        elif request.metric == "coupling":
            results = graph.get_coupling(request.top_n)
        elif request.metric == "hotspots":
            if _git_analyzer is None:
                return _tool_error(
                    "not_analyzed", "Git history is not available yet", "Call analyze_repo first."
                )
            results = _hotspot_metrics(graph, _git_analyzer, request.top_n)
        elif request.metric == "ownership":
            if _git_analyzer is None:
                return _tool_error(
                    "not_analyzed", "Git history is not available yet", "Call analyze_repo first."
                )
            results = _ownership_metrics(graph, _git_analyzer, request.top_n)
        else:
            summary = {
                "top_important": [
                    result.model_dump(mode="json") for result in graph.get_pagerank(top_n=5)
                ],
                "top_bottlenecks": [
                    result.model_dump(mode="json") for result in graph.get_centrality(top_n=5)
                ],
                "top_complex": [
                    result.model_dump(mode="json") for result in graph.get_complexity(top_n=5)
                ],
            }
            return json.dumps(summary, indent=2)

        return json.dumps([result.model_dump(mode="json") for result in results], indent=2)
    except Exception as exc:
        return _tool_error(
            "invalid_input", f"Unable to get metrics: {exc}", "Check the metric name and try again."
        )


def _hotspot_metrics(graph: CodeGraph, git_analyzer: GitAnalyzer, top_n: int) -> list[MetricResult]:
    """Combine Git change frequency with static line span into hotspot scores."""
    frequencies = git_analyzer.get_all_file_change_frequencies()
    analyzed_modules = set(_module_paths(graph))
    complexity_by_file: dict[str, float] = {}
    for result in graph.get_complexity(top_n=max(1, len(graph.entities))):
        complexity_by_file[result.file_path] = (
            complexity_by_file.get(result.file_path, 0.0) + result.score
        )

    results: list[MetricResult] = []
    for file_path, frequency in frequencies.items():
        if file_path not in analyzed_modules:
            continue
        complexity = complexity_by_file.get(file_path, 0.0)
        results.append(
            MetricResult(
                entity_name=Path(file_path).stem,
                entity_type="module",
                file_path=file_path,
                score=float(frequency * complexity),
                rank=0,
                interpretation=(
                    f"Touched {frequency} time(s) within the most recent "
                    f"{get_config().max_git_history_commits} commits; cumulative static line "
                    f"span {complexity:.0f}"
                ),
                source=(
                    f"git-log-last-{get_config().max_git_history_commits}"
                    "+tree-sitter-line-span"
                ),
            )
        )
    ranked = sorted(results, key=lambda result: result.score, reverse=True)
    for rank, result in enumerate(ranked, start=1):
        result.rank = rank
    return ranked[:top_n]


def _ownership_metrics(
    graph: CodeGraph, git_analyzer: GitAnalyzer, top_n: int
) -> list[MetricResult]:
    """Rank a bounded set of important modules by their top-owner percentage."""
    max_files = min(
        get_config().max_git_files_for_ownership,
        max(1, top_n),
    )
    selected_paths = [result.file_path for result in graph.get_pagerank(top_n=max_files)]
    results: list[MetricResult] = []
    for file_path in selected_paths:
        authors = git_analyzer.get_file_blame(file_path)
        if not authors:
            continue
        owner = authors[0]
        results.append(
            MetricResult(
                entity_name=Path(file_path).stem,
                entity_type="module",
                file_path=file_path,
                score=owner.percentage,
                rank=0,
                interpretation=f"Owned by {owner.name} ({owner.percentage:.0f}%)",
                source="git-blame",
            )
        )
    ranked = sorted(results, key=lambda result: result.score, reverse=True)
    for rank, result in enumerate(ranked, start=1):
        result.rank = rank
    return ranked[:top_n]


@mcp.tool()
@_with_state_lock
def visualize(
    diagram_type: str = "architecture", scope: str | None = None, max_nodes: int = 25
) -> str:
    """Generate a Mermaid diagram visualizing codebase architecture.
    The diagram renders directly in Codex/ChatGPT.

    Parameters:
    - diagram_type (required): one of:
      - "architecture": module-level dependency overview
      - "call_flow": function call chain from a starting point
      - "dependencies": imports for a specific module
      - "layers": detected architectural layers
      - "hotspot_map": modules colored by change frequency
    - scope (optional): focus on a specific module or function name
    - max_nodes (optional): maximum nodes in diagram. Default: 25

    The diagram uses PageRank-based filtering to show only the most
    important nodes, ensuring readability.

    Returns: Mermaid code block ready to render.

    IMPORTANT: Always use this tool to verify facts about the codebase.
    Do not rely on your memory of previous tool calls — re-query to confirm."""
    global _visualizer
    try:
        graph = get_graph()
        if not graph.is_built:
            return _tool_error(
                "not_analyzed", "No repo has been analyzed yet", "Call analyze_repo first"
            )
        if _visualizer is None:
            change_frequencies = (
                _git_analyzer.get_all_file_change_frequencies()
                if _git_analyzer is not None
                else {}
            )
            _visualizer = MermaidVisualizer(graph, change_frequencies)
        clamped_nodes = max(5, min(max_nodes, get_config().max_mermaid_nodes))
        request = VisualizeInput(
            diagram_type=diagram_type,
            scope=scope,
            max_nodes=clamped_nodes,
        )
        return _visualizer.generate(request)
    except Exception as exc:
        return _tool_error(
            "invalid_input",
            f"Unable to generate visualization: {exc}",
            "Check the diagram type and scope.",
        )


@mcp.tool()
@_with_state_lock
def get_git_context(file_path: str, entity_name: str | None = None) -> str:
    """Get git history context for a file or specific function/class within
    a file. Use this to understand who wrote code, when and why it changed.

    Parameters:
    - file_path (required): relative path from repo root
    - entity_name (optional): specific function or class name within the file

    Returns:
    - authors: who contributed to this file and their line counts
    - change_frequency: how many times this file changed in the configured recent Git history window
    - recent_commits: last 10 commits touching this file (SHA, date, message, author)
    - co_changed_files: files that usually change in the same commit
    - file_age: earliest known and last modified dates in the configured recent history window

    Commit messages often explain WHY code was written or changed.
    Co-changed files reveal hidden coupling not visible in imports.

    IMPORTANT: Always use this tool to verify facts about the codebase.
    Do not rely on your memory of previous tool calls — re-query to confirm."""
    try:
        graph = get_graph()
        if not graph.is_built or _git_analyzer is None:
            return _tool_error("not_analyzed", "No repo analyzed yet", "Call analyze_repo first")
        request = GitContextInput(file_path=file_path, entity_name=entity_name)
        try:
            normalized_path = _validate_analyzed_file_path(graph, request.file_path)
        except FileNotFoundError as exc:
            return _tool_error(
                "not_found",
                str(exc),
                "Analyze the file first or provide an analyzed path.",
            )
        except ValueError as exc:
            return _tool_error(
                "invalid_input",
                str(exc),
                "Provide a relative path inside the analyzed repository.",
            )
        result: GitContextOutput = _git_analyzer.get_full_context(
            normalized_path, request.entity_name
        )
        return result.model_dump_json(indent=2)
    except Exception as exc:
        return _tool_error(
            "invalid_input",
            f"Unable to get git context: {exc}",
            "Check the file path and try again.",
        )


def main():
    """Run the MCP server."""
    mcp.run(show_banner=False)


if __name__ == "__main__":
    main()
