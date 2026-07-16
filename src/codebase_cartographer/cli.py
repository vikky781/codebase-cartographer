from __future__ import annotations

import json
import sys
from pathlib import Path

import click
from rich.console import Console
from rich.panel import Panel
from rich.syntax import Syntax
from rich.table import Table

console = Console()


def _parse_result(raw_result: str) -> dict | list:
    """Decode a server JSON response and raise a clear error response when present."""
    result = json.loads(raw_result)
    if isinstance(result, dict) and result.get("status") == "error":
        suggestion = result.get("suggestion", "")
        message = result.get("message", "Unknown server error")
        raise RuntimeError(f"{message}{f' Suggestion: {suggestion}' if suggestion else ''}")
    return result


def _exit_with_error(error: Exception) -> None:
    """Print a Rich error message and terminate the command with a failure status."""
    console.print(f"[red]Error:[/red] {error}")
    console.print_exception()
    sys.exit(1)


def _render_json(value: object) -> None:
    """Render an unexpected JSON-shaped response without losing its structure."""
    console.print(Syntax(json.dumps(value, indent=2), "json", word_wrap=True))


def _ensure_repository_loaded(ctx: click.Context) -> None:
    """Load a repository graph for a standalone CLI query, reusing a valid cache."""
    repo_path = (ctx.obj or {}).get("repo_path")
    if repo_path is None:
        raise click.UsageError(
            "This command needs a repository. Pass --repo /absolute/path/to/repository "
            "before the command so its graph can be loaded.",
            ctx,
        )

    from .server import analyze_repo

    result = _parse_result(analyze_repo(str(Path(repo_path).resolve())))
    if not isinstance(result, dict):
        raise RuntimeError("Analysis returned an unexpected response.")


@click.group()
@click.option(
    "--repo",
    "repo_path",
    type=click.Path(exists=True, file_okay=False, dir_okay=True, path_type=Path),
    default=None,
    help="Repository to load for query commands; a valid graph cache is reused when available.",
)
@click.version_option(version="0.1.0")
@click.pass_context
def main(ctx: click.Context, repo_path: Path | None) -> None:
    """CodebaseCartographer — Codebase intelligence via static analysis and graph algorithms."""
    ctx.ensure_object(dict)
    ctx.obj["repo_path"] = repo_path


@main.command()
@click.argument(
    "repo_path",
    type=click.Path(exists=True, file_okay=False, dir_okay=True, path_type=Path),
)
@click.option("--scope", default=None, help="Subdirectory to analyze")
@click.option("--no-cache", is_flag=True, help="Do not read or write .cartographer_cache")
def analyze(repo_path: Path, scope: str | None, no_cache: bool):
    """Analyze a repository and build the knowledge graph."""
    try:
        from .server import analyze_repo

        result = _parse_result(
            analyze_repo(str(Path(repo_path).resolve()), scope, use_cache=not no_cache)
        )
        if not isinstance(result, dict):
            raise RuntimeError("Analysis returned an unexpected response.")

        entities = result.get("entities", {})
        edges = result.get("edges", {})
        languages = result.get("languages", {})
        table = Table(show_header=False, box=None)
        table.add_column("Metric", style="bold cyan")
        table.add_column("Value")
        table.add_row("Files analyzed", str(result.get("files_analyzed", 0)))
        table.add_row(
            "Languages",
            ", ".join(f"{language}: {count}" for language, count in languages.items()) or "None",
        )
        table.add_row(
            "Entities",
            ", ".join(f"{name}: {count}" for name, count in entities.items()) or "None",
        )
        table.add_row(
            "Edges",
            ", ".join(f"{name}: {count}" for name, count in edges.items()) or "None",
        )
        console.print(Panel(table, title="Analysis Complete", border_style="green"))

        health = result.get("health", {})
        issue_fields = [
            "circular_dependencies",
            "dead_functions",
            "god_classes",
            "bottleneck_count",
            "orphan_files",
        ]
        issue_count = sum(int(health.get(field, 0)) for field in issue_fields)
        health_style = "red" if issue_count else "green"
        console.print(
            f"[{health_style}]Health:[/{health_style}] "
            f"{issue_count} issue candidate(s), average static line span "
            f"{health.get('avg_complexity', 0):.1f}"
        )
        for field in issue_fields:
            value = health.get(field, 0)
            if value:
                console.print(
                    f"[{health_style}]{field.replace('_', ' ').title()}: {value}[/{health_style}]"
                )

        for warning in result.get("warnings", []):
            console.print(f"[yellow]Warning:[/yellow] {warning}")
    except Exception as exc:
        _exit_with_error(exc)


@main.command(name="mcp", hidden=True)
def run_mcp() -> None:
    """Run the MCP server over standard input/output for the Codex plugin."""
    from .server import main as run_server

    run_server()


@main.command()
@click.argument("query")
@click.option(
    "--type", "entity_type", default=None, type=click.Choice(["function", "class", "module"])
)
@click.option("--limit", default=20)
@click.pass_context
def search(ctx: click.Context, query: str, entity_type: str | None, limit: int):
    """Search the knowledge graph for entities."""
    try:
        _ensure_repository_loaded(ctx)
        from .server import search_graph

        result = _parse_result(search_graph(query, entity_type, limit))
        if not isinstance(result, list):
            raise RuntimeError("Search returned an unexpected response.")

        table = Table(title=f"Search: {query}")
        for column in ("Name", "Type", "File", "Lines", "Complexity", "Calls", "Called By"):
            table.add_column(column)
        for entity in result:
            table.add_row(
                str(entity.get("name", "")),
                str(entity.get("type", "")),
                str(entity.get("file_path", "")),
                f"{entity.get('line_start', '?')}-{entity.get('line_end', '?')}",
                str(entity.get("complexity", 0)),
                ", ".join(entity.get("calls", [])) or "—",
                ", ".join(entity.get("called_by", [])) or "—",
            )
        console.print(table)
    except Exception as exc:
        _exit_with_error(exc)


@main.command()
@click.argument("entity_name")
@click.option("--direction", default="forward", type=click.Choice(["forward", "backward", "both"]))
@click.option("--depth", default=5)
@click.pass_context
def trace(ctx: click.Context, entity_name: str, direction: str, depth: int):
    """Trace call chains from an entity."""
    try:
        _ensure_repository_loaded(ctx)
        from .server import trace_flow

        result = _parse_result(trace_flow(entity_name, direction, depth))
        if not isinstance(result, dict):
            raise RuntimeError("Trace returned an unexpected response.")

        console.print(f"[bold]{result.get('start', entity_name)}[/bold]")
        for step in result.get("steps", []):
            entity = step.get("entity", {})
            indentation = "    " * max(0, int(step.get("depth", 1)) - 1)
            console.print(
                f"{indentation}└── {entity.get('name', '?')} "
                f"[dim]({step.get('relationship', 'calls')})[/dim]"
            )
        if result.get("truncated"):
            console.print(
                "[yellow]Trace truncated at the configured maximum depth or step count.[/yellow]"
            )
    except Exception as exc:
        _exit_with_error(exc)


@main.command()
@click.option("--types", default=None, help="Comma-separated issue types to check")
@click.pass_context
def issues(ctx: click.Context, types: str | None):
    """Detect architectural issues."""
    try:
        _ensure_repository_loaded(ctx)
        from .server import find_issues

        result = _parse_result(find_issues(types))
        if not isinstance(result, list):
            raise RuntimeError("Issue detection returned an unexpected response.")

        grouped: dict[str, list[dict]] = {}
        for issue in result:
            grouped.setdefault(str(issue.get("type", "unknown")), []).append(issue)
        severity_styles = {"high": "red", "medium": "yellow", "low": "dim"}
        for issue_type, type_issues in grouped.items():
            console.print(f"\n[bold cyan]{issue_type.replace('_', ' ').title()}[/bold cyan]")
            for issue in type_issues:
                severity = str(issue.get("severity", "low"))
                style = severity_styles.get(severity, "white")
                console.print(
                    f"[{style}]{severity.upper()}[/{style}] {issue.get('description', '')}"
                )
                console.print(f"  [dim]Suggestion:[/dim] {issue.get('suggestion', '')}")
    except Exception as exc:
        _exit_with_error(exc)


@main.command()
@click.argument("metric", default="summary")
@click.option("--top", "top_n", default=15)
@click.pass_context
def metrics(ctx: click.Context, metric: str, top_n: int):
    """Get codebase metrics."""
    try:
        _ensure_repository_loaded(ctx)
        from .server import get_metrics

        result = _parse_result(get_metrics(metric, top_n))
        if isinstance(result, dict):
            for title, entries in result.items():
                console.print(f"\n[bold cyan]{title.replace('_', ' ').title()}[/bold cyan]")
                _metrics_table(entries)
        elif isinstance(result, list):
            _metrics_table(result)
        else:
            _render_json(result)
    except Exception as exc:
        _exit_with_error(exc)


def _metrics_table(entries: object) -> None:
    """Display a sequence of metric objects in a consistent Rich table."""
    if not isinstance(entries, list):
        _render_json(entries)
        return
    table = Table()
    for column in ("Rank", "Entity", "File", "Score", "Interpretation"):
        table.add_column(column)
    for result in entries:
        table.add_row(
            str(result.get("rank", "")),
            str(result.get("entity_name", "")),
            str(result.get("file_path", "")),
            f"{float(result.get('score', 0)):.6g}",
            str(result.get("interpretation", "")),
        )
    console.print(table)


@main.command()
@click.argument("diagram_type", default="architecture")
@click.option("--scope", default=None)
@click.option("--max-nodes", default=25)
@click.pass_context
def viz(ctx: click.Context, diagram_type: str, scope: str | None, max_nodes: int):
    """Generate a Mermaid diagram (prints to stdout)."""
    try:
        _ensure_repository_loaded(ctx)
        from .server import visualize

        result = visualize(diagram_type, scope, max_nodes)
        try:
            _parse_result(result)
        except json.JSONDecodeError:
            pass
        click.echo(result)
        click.echo(
            "Copy the output above into any Mermaid renderer to view the diagram.",
            err=True,
        )
    except Exception as exc:
        _exit_with_error(exc)


@main.command(name="git-context")
@click.argument("file_path")
@click.option("--entity", default=None, help="Specific function or class name")
@click.pass_context
def git_context(ctx: click.Context, file_path: str, entity: str | None):
    """Get git history context for a file."""
    try:
        _ensure_repository_loaded(ctx)
        from .server import get_git_context

        result = _parse_result(get_git_context(file_path, entity))
        if not isinstance(result, dict):
            raise RuntimeError("Git context returned an unexpected response.")

        authors = Table(title="Authors")
        for column in ("Name", "Email", "Lines", "Ownership"):
            authors.add_column(column)
        for author in result.get("authors", []):
            authors.add_row(
                str(author.get("name", "")),
                str(author.get("email", "")),
                str(author.get("lines_owned", 0)),
                f"{float(author.get('percentage', 0)):.1f}%",
            )
        console.print(authors)

        commits = Table(title="Recent Commits")
        for column in ("SHA", "Date", "Author", "Message"):
            commits.add_column(column)
        for commit in result.get("recent_commits", []):
            commits.add_row(
                str(commit.get("sha", "")),
                str(commit.get("date", "")),
                str(commit.get("author", "")),
                str(commit.get("message", "")),
            )
        console.print(commits)

        co_changed = Table(title="Co-Changed Files")
        co_changed.add_column("File")
        co_changed.add_column("Changes")
        co_changed.add_column("Co-Change %")
        for item in result.get("co_changed_files", []):
            co_changed.add_row(
                str(item.get("file_path", "")),
                str(item.get("co_change_count", 0)),
                f"{float(item.get('co_change_percentage', 0)):.1f}%",
            )
        console.print(co_changed)

        file_age = result.get("file_age")
        if file_age:
            console.print(
                Panel(
                    (
                        f"Created: {file_age.get('created_date', 'Unknown')}\n"
                        f"Last modified: {file_age.get('last_modified_date', 'Unknown')}\n"
                        "Days since last change: "
                        f"{file_age.get('days_since_last_change', 'Unknown')}"
                    ),
                    title="File Age",
                )
            )
    except Exception as exc:
        _exit_with_error(exc)


if __name__ == "__main__":
    main()
