from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class CodeEntity(BaseModel):
    """Represent a function, class, or module discovered in a codebase."""

    name: str = Field(description="Function, class, or module name")
    type: Literal["function", "class", "module"]
    file_path: str = Field(description="Relative path from repo root")
    line_start: int
    line_end: int
    signature: str | None = Field(
        default=None,
        description="Full signature e.g. 'def login(username: str, password: str) -> Token'",
    )
    complexity: int = Field(default=0, description="Lines of code + max nesting depth")
    calls: list[str] = Field(default_factory=list, description="Names of entities this calls")
    called_by: list[str] = Field(
        default_factory=list, description="Names of entities that call this"
    )
    source: str = Field(
        default="tree-sitter-ast",
        description="How this was detected: tree-sitter-ast or regex-fallback",
    )


class EntityCounts(BaseModel):
    """Store counts of discovered entities by type."""

    functions: int = 0
    classes: int = 0
    modules: int = 0


class EdgeCounts(BaseModel):
    """Store counts of graph relationships by type."""

    calls: int = 0
    imports: int = 0


class HealthSummary(BaseModel):
    """Summarize codebase health indicators."""

    circular_dependencies: int = 0
    dead_functions: int = 0
    god_classes: int = 0
    avg_complexity: float = 0.0
    bottleneck_count: int = 0
    orphan_files: int = 0


class AnalyzeInput(BaseModel):
    """Specify a repository or optional subsection to analyze."""

    repo_path: str = Field(description="Absolute path to the repository root")
    scope: str | None = Field(
        default=None,
        description="Optional subdirectory to analyze, e.g. 'src/' for large repos",
    )


class AnalyzeOutput(BaseModel):
    """Return the aggregate results of a repository analysis."""

    status: str = Field(default="success")
    files_analyzed: int = 0
    languages: dict[str, int] = Field(
        default_factory=dict, description="Language name to file count mapping"
    )
    entities: EntityCounts = Field(default_factory=EntityCounts)
    edges: EdgeCounts = Field(default_factory=EdgeCounts)
    detected_layers: list[str] = Field(
        default_factory=list,
        description="Inferred architectural layers like routes, services, models",
    )
    health: HealthSummary = Field(default_factory=HealthSummary)
    repo_hash: str = Field(default="", description="Git HEAD SHA for cache validation")
    warnings: list[str] = Field(
        default_factory=list, description="Non-fatal warnings like skipped files"
    )


class SearchInput(BaseModel):
    """Define an entity search query and optional result filters."""

    query: str = Field(description="Search term e.g. 'auth', 'payment', 'User'")
    entity_type: Literal["function", "class", "module"] | None = None
    limit: int = Field(default=20, ge=1, le=100)


class TraceStep(BaseModel):
    """Describe one relationship traversed during a code graph trace."""

    depth: int
    entity: CodeEntity
    relationship: Literal["calls", "imports", "inherits"]
    source: str = "tree-sitter-ast"


class TraceInput(BaseModel):
    """Specify the starting entity and traversal settings for a trace."""

    entity_name: str = Field(description="Name of the starting function, class, or module")
    direction: Literal["forward", "backward", "both"] = "forward"
    max_depth: int = Field(default=5, ge=1, le=15)


class TraceOutput(BaseModel):
    """Return the relationship steps discovered from a graph trace."""

    start: str = Field(description="Starting entity name")
    direction: str
    steps: list[TraceStep] = Field(default_factory=list)
    truncated: bool = Field(default=False, description="True if trace was cut off at max_depth")


class FindIssuesInput(BaseModel):
    """Optionally limit code-health analysis to selected issue types."""

    issue_types: list[str] | None = Field(
        default=None, description="Filter to specific types. None means check all types."
    )


class Issue(BaseModel):
    """Describe a code-quality issue detected by local analysis."""

    type: Literal[
        "circular_dependency",
        "dead_code",
        "god_class",
        "bottleneck",
        "orphan_file",
        "high_coupling",
    ]
    severity: Literal["high", "medium", "low"]
    description: str
    entities: list[CodeEntity] = Field(default_factory=list)
    file_paths: list[str] = Field(default_factory=list)
    suggestion: str = Field(default="", description="Actionable fix recommendation")
    source: str = Field(
        description="Algorithm that detected this, e.g. 'networkx-scc', 'networkx-pagerank'"
    )


class MetricsInput(BaseModel):
    """Specify a metric calculation and the number of results to return."""

    metric: Literal[
        "pagerank", "centrality", "complexity", "hotspots", "coupling", "ownership", "summary"
    ]
    top_n: int = Field(default=15, ge=1, le=50)


class MetricResult(BaseModel):
    """Represent a ranked result returned by a codebase metric."""

    entity_name: str
    entity_type: str
    file_path: str
    score: float
    rank: int
    interpretation: str = Field(
        description="Human-readable explanation e.g. '3rd most imported module — core dependency'"
    )
    source: str = Field(
        description="Algorithm: networkx-pagerank, networkx-centrality, git-log, etc."
    )


class VisualizeInput(BaseModel):
    """Define the graph visualization to generate and its scope."""

    diagram_type: Literal["architecture", "call_flow", "dependencies", "layers", "hotspot_map"]
    scope: str | None = Field(
        default=None, description="Focus on a specific module or function name"
    )
    max_nodes: int = Field(default=25, ge=5, le=50)


class GitContextInput(BaseModel):
    """Identify a file and optional entity for git-history analysis."""

    file_path: str = Field(description="Relative path from repo root")
    entity_name: str | None = Field(
        default=None, description="Specific function or class name within the file"
    )


class AuthorInfo(BaseModel):
    """Capture an author's ownership of lines in a file."""

    name: str
    email: str
    lines_owned: int
    percentage: float


class CommitInfo(BaseModel):
    """Represent a commit relevant to a file's history."""

    sha: str
    date: str
    author: str
    message: str


class CoChangeInfo(BaseModel):
    """Describe how often another file changes with the queried file."""

    file_path: str
    co_change_count: int
    co_change_percentage: float = Field(
        description="How often this file changes together with the queried file, 0-100"
    )


class FileAge(BaseModel):
    """Summarize creation and modification timing for a file."""

    created_date: str
    last_modified_date: str
    days_since_last_change: int


class GitContextOutput(BaseModel):
    """Return ownership and change-history context for a file."""

    file_path: str
    authors: list[AuthorInfo] = Field(default_factory=list)
    change_frequency: int = Field(default=0, description="Total commits touching this file")
    recent_commits: list[CommitInfo] = Field(default_factory=list, description="Last 10 commits")
    co_changed_files: list[CoChangeInfo] = Field(
        default_factory=list, description="Top 5 co-changed files"
    )
    file_age: FileAge | None = None
    source: str = "git-log"


class ToolError(BaseModel):
    """Provide a consistent error response for all MCP tools."""

    status: str = "error"
    error_type: Literal["not_analyzed", "not_found", "too_large", "parse_error", "invalid_input"]
    message: str = Field(description="Human-readable error description")
    suggestion: str = Field(default="", description="What the user should do to fix this")
