# CodebaseCartographer

> An MCP plugin that gives Codex deep, structured understanding of any codebase - powered by static analysis and graph algorithms. Zero API calls. Zero cost.

`Python 3.11+` | `MCP Compatible` | `Local Analysis` | `Zero Server API Calls` | `MIT License`

## What it does

CodebaseCartographer is a local MCP server that analyzes a code repository using Tree-sitter AST parsing, Git history analysis, and NetworkX graph algorithms. It builds a knowledge graph of functions, classes, modules, imports, and resolvable calls, then exposes seven tools to an MCP-compatible host such as Codex, Claude Code, or Antigravity. The server performs all analysis locally and makes no application-level API calls. Your chosen host LLM reasons over the structured results; the server does not upload source code on its own.

## Why it exists

Developers spend a large portion of their time reading existing code, and joining an unfamiliar codebase can take months. LLMs are powerful readers, but raw files are flat text: architectural boundaries, hidden dependencies, and conventions are easy to miss. CodebaseCartographer gives the host a structured map so it can ask focused questions instead of reading every file repeatedly.

## Features

| Tool | What it does |
| --- | --- |
| `analyze_repo` | Scans a repository and builds the knowledge graph with Tree-sitter, Git, and NetworkX. |
| `search_graph` | Finds functions, classes, and modules by name or path. |
| `trace_flow` | Traces call chains forward, backward, or in both directions. |
| `find_issues` | Detects circular dependencies, dead code, god classes, bottlenecks, orphan files, and coupling. |
| `get_metrics` | Computes PageRank, betweenness centrality, complexity, Git hotspots, coupling, and ownership. |
| `visualize` | Generates Mermaid diagrams that render in MCP-capable chat clients. |
| `get_git_context` | Shows local authorship, commits, age, and co-change information for an analyzed file. |

## Installation contract

The host-specific bundles configure an MCP client, while the Python package provides the local server executable. Install the runtime once, then enable the bundle for the host you use.

```bash
# Recommended: puts cartographer-mcp on your user PATH.
pipx install "git+https://github.com/vikky781/codebase-cartographer.git"

# Alternative: install into an active Python environment.
python -m pip install "git+https://github.com/vikky781/codebase-cartographer.git"
```

Verify that the host will be able to find the canonical stdio launcher:

```bash
python -c "import shutil; assert shutil.which('cartographer-mcp'), 'cartographer-mcp is not on PATH'"
```

On Windows, use the absolute path to `cartographer-mcp.exe` in the client configuration if the desktop application does not inherit your Python scripts directory. See [the local MCP guide](docs/LOCAL_MCP.md) for the portable configuration and troubleshooting details.

### Codex

The repository includes a Codex plugin at `plugins/codebase-cartographer/` and a local marketplace definition at `marketplace.json`.

```bash
git clone https://github.com/vikky781/codebase-cartographer.git
cd codebase-cartographer
codex plugin marketplace add /absolute/path/to/codebase-cartographer
codex plugin add codebase-cartographer@codebase-cartographer-local
```

Restart or reload Codex after installation. The plugin starts the local `cartographer-mcp` stdio server. Its runtime prerequisite is intentionally explicit: plugin configuration alone cannot safely install Python dependencies into a desktop host environment.

### Claude Code

This repository is also a Claude Code plugin marketplace. After installing the Python runtime, add it and enable the plugin:

```bash
claude plugin marketplace add vikky781/codebase-cartographer
claude plugin install codebase-cartographer@codebase-cartographer
```

For development from a checkout, Claude Code can load the wrapper directly:

```bash
claude --plugin-dir ./integrations/claude-code
```

The same server can be configured without the plugin using the root [`.mcp.json`](.mcp.json).

### Antigravity

The `integrations/antigravity/` directory is a native Antigravity plugin wrapper. Install it after the Python runtime:

```bash
agy plugin install /absolute/path/to/codebase-cartographer/integrations/antigravity
```

For the Antigravity IDE or 2.0, copy or symlink that directory to `.agents/plugins/codebase-cartographer/` in a workspace, or to `~/.gemini/config/plugins/codebase-cartographer/` globally. The equivalent direct MCP configuration is `integrations/antigravity/mcp_config.json`.

## How it works

```text
User-approved repository
        |
        +-- Tree-sitter parsing --> entities, imports, calls
        +-- Regex fallback ------> lower-fidelity support for other languages
        +-- Local Git history ---> ownership, commits, co-change data
                                   |
                                   v
                     NetworkX directed knowledge graph
                                   |
                                   v
             Search | tracing | health checks | metrics | Mermaid
```

The graph applies PageRank, betweenness centrality, community detection, cycle detection, degree analysis, and coupling analysis. These results are evidence for a developer or host LLM to interpret, not a substitute for runtime tests or human review.

## Local-first safety model

- The server parses source text; it does not execute analyzed repository code.
- Source files, ASTs, Git data, and graph data remain local. The server itself makes no application-level network requests.
- Analysis requires a repository path the user has explicitly approved and rejects paths outside that repository for Git-context operations.
- A refresh may write only `.cartographer_cache/` under the analyzed repository; it is a disposable cache and is excluded from version control.
- Static analysis can miss reflection, generated code, dynamic imports, framework wiring, external dependencies, and runtime dispatch.

## Development and release checks

```bash
python -m pip install -e ".[dev]"
python -m pytest tests/ -v --tb=short
ruff check src tests
python -m build
```

The GitHub Actions workflow performs the test and lint checks, builds a wheel, installs that wheel into a clean virtual environment, and verifies that `cartographer-mcp` advertises the seven MCP tools over stdio.

Validate the Codex bundle after modifying it:

```bash
python <CODEX_HOME>/skills/.system/plugin-creator/scripts/validate_plugin.py plugins/codebase-cartographer
```

When developing the Codex plugin, refresh its cache-busting manifest version before reinstalling it:

```bash
python <CODEX_HOME>/skills/.system/plugin-creator/scripts/update_plugin_cachebuster.py plugins/codebase-cartographer
```

## Project structure

```text
src/codebase_cartographer/       Local MCP server and analysis engine
plugins/codebase-cartographer/   Codex plugin bundle
integrations/claude-code/        Claude Code plugin wrapper
integrations/antigravity/        Antigravity plugin wrapper
docs/LOCAL_MCP.md                Client-neutral stdio configuration
```

## Open source

CodebaseCartographer is released under the [MIT License](LICENSE). Contributions and security reports are covered by [CONTRIBUTING.md](CONTRIBUTING.md) and [SECURITY.md](SECURITY.md).
