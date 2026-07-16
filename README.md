# CodebaseCartographer

> **Before Codex changes unfamiliar code, give it evidence.** CodebaseCartographer is a local MCP evidence layer that helps Codex trace a change's static blast radius, ownership context, and uncertainty before it proposes a plan.

`Python 3.11+` · `Codex MCP plugin` · `Local analysis` · `No application-level API calls` · `MIT`

## The decision it improves

A senior engineer entering a private or unfamiliar repository often has one expensive question: **“What could break if I change this?”** Reading files one by one leaves gaps around imports, call paths, ownership, and hidden coupling. CodebaseCartographer turns locally computed repository facts into an evidence packet that Codex can use before it writes code.

The server is deliberately deterministic: it parses source, examines local Git history, and runs graph algorithms on the machine where it is launched. Codex or another MCP-compatible host performs the reasoning over those structured results. The server does not execute the repository it analyzes and does not make application-level network calls.

## From question to an auditable plan

1. Ask Codex to analyze an explicitly approved repository.
2. Start with `analyze_repo`, then read its `coverage` and `warnings` before trusting graph results.
3. Trace the target with `search_graph` and `trace_flow`; add ownership/change context with `get_git_context` when it matters.
4. Have Codex respond in four sections: **verified facts**, **inferences**, **unknowns**, and **recommended validation**.

For example, prompt Codex:

```text
Before changing AuthService.authenticate, build an evidence-backed change-impact plan.
Show exact graph paths and source lines, identify unresolved or inferred relationships,
include Git context where available, then separate verified facts from inferences and tests
I should run.
```

That keeps the model from treating a missing static edge as proof that a runtime dependency does not exist.

```mermaid
flowchart LR
    repo["Approved local repository"] --> parse["Tree-sitter parsing<br/>regex inventory where needed"]
    repo --> git["Local Git history"]
    parse --> graph["Local NetworkX graph<br/>with coverage and provenance"]
    git --> graph
    graph --> mcp["Local stdio MCP tools"]
    mcp --> codex["Codex / GPT-5.6<br/>evidence-backed plan"]
    codex --> plan["Facts · inferences · unknowns<br/>recommended validation"]
```

The repository source and graph stay on the local machine during analysis. Tool results may be provided to the selected MCP host/model according to that host's settings and your account plan.

## Judge path: try it in under a minute

The repository includes a deliberately small, safe Python fixture with known graph conditions. This is the quickest reproducible way to see the engine work without configuring a marketplace first.

```bash
git clone https://github.com/vikky781/codebase-cartographer.git
cd codebase-cartographer
python -m pip install -e ".[dev]"
python -m pytest tests/ -v --tb=short
```

Analyze the fixture without reading or writing a cache:

```powershell
$repo = (Get-Location).Path
python -m codebase_cartographer.cli analyze "$repo\fixtures\sample_repo" --no-cache
```

```bash
python -m codebase_cartographer.cli analyze "$(pwd)/fixtures/sample_repo" --no-cache
```

On the current fixture, the expected high-level result is 11 Python files, 28 functions, 3 classes, 11 modules, 9 resolved call edges, 9 import edges, and a coverage report of 29 observed / 9 resolved / 20 unresolved calls. The fixture intentionally includes a cycle and unused-looking code so that results are easy to inspect; they are static-analysis candidates, not runtime proof.

## Install it in Codex

CodebaseCartographer's primary integration is a local Codex plugin. Install the Python runtime first, because a plugin bundle should not silently modify a desktop host's Python environment.

```bash
pipx install "git+https://github.com/vikky781/codebase-cartographer.git"
python -c "import shutil; assert shutil.which('cartographer-mcp'), 'cartographer-mcp is not on PATH'"
codex plugin marketplace add vikky781/codebase-cartographer --ref main
codex plugin marketplace list
```

Then restart the ChatGPT/Codex desktop app, open **Plugins**, select **Codebase Cartographer Local**, and install `codebase-cartographer`. Start a fresh task and use one of the evidence-first prompts above.

For local plugin development from a checkout, use the catalog already committed in this repository—do not copy a second marketplace file:

```bash
codex plugin marketplace add ./
```

If the app cannot find `cartographer-mcp`, point the plugin/MCP configuration at the absolute installed executable path. Platform-specific commands, direct stdio setup, and troubleshooting live in [docs/LOCAL_MCP.md](docs/LOCAL_MCP.md).

### Other local MCP hosts

Claude Code and Antigravity wrappers are included for local testing, but the hackathon product story and first-class workflow are Codex.

| Host | Supported route |
| --- | --- |
| Codex desktop app | Repository marketplace plugin in `plugins/codebase-cartographer/` |
| Codex CLI / direct MCP | Root [`.mcp.json`](.mcp.json) or the installed `cartographer-mcp` stdio launcher |
| Claude Code | [integrations/claude-code](integrations/claude-code) wrapper |
| Antigravity | [integrations/antigravity](integrations/antigravity) wrapper |

## What the seven tools provide

| Tool | Decision evidence it returns |
| --- | --- |
| `analyze_repo` | A local graph, language/coverage breakdown, warnings, layers, and health candidates. Call it first. |
| `search_graph` | Functions, classes, and modules matching a name or path, with immediate graph neighbors. |
| `trace_flow` | Forward, backward, or bidirectional paths with relationship provenance, resolution status, and source lines. |
| `find_issues` | Candidate cycles, unused code, god classes, bottlenecks, orphan files, and high coupling—not runtime verdicts. |
| `get_metrics` | PageRank, centrality, static line span, Git hotspots, coupling, and ownership. |
| `visualize` | Focused Mermaid architecture, dependency, layer, call-flow, and Git-backed hotspot diagrams. |
| `get_git_context` | Local authorship, commits, age, and co-change context for an analyzed file. |

## Capability and confidence matrix

| Source files | Parser | Entities/imports | Calls and source-line provenance | Confidence notes |
| --- | --- | --- | --- | --- |
| Python (`.py`) | Tree-sitter | Yes | Yes, for a legal lexical/import binding with one local target | Deepest supported path; dynamic dispatch/framework wiring can still be missed. |
| JavaScript (`.js`, `.jsx`, `.mjs`) | Tree-sitter | Yes | Yes, for a legal ES-module binding with one local target | CommonJS and dynamic module loading are outside this graph. |
| TypeScript (`.ts`) | Tree-sitter | Yes | Yes, for a legal ES-module binding with one local target | Type aliases, decorators, and framework injection are outside this graph. |
| TSX (`.tsx`) | Dedicated TSX Tree-sitter grammar | Yes | Yes, for a legal ES-module binding with one local target | JSX is parsed with the TSX grammar rather than generic TypeScript. |
| Java, Go, Rust, Ruby, PHP, C/C++, C#, Swift, Kotlin, Scala | Regex fallback | Basic declaration/import inventory | No call graph or static line-span evidence | Experimental inventory only; do not use absence of an edge as evidence of safety. |

`complexity` in the API is retained for compatibility, but its current Tree-sitter value is a **static line span**, not cyclomatic complexity or a defect-risk score. A fallback entity has a score of `0` because that measurement is unavailable.

## Trust and privacy contract

- **Local by design:** parsing, Git inspection, graph construction, cache reads, and cache writes happen locally. The server does not execute analyzed code.
- **Precise claims:** ambiguous and unresolved local relationships are counted in `coverage` and are not silently chosen. A graph edge carries `exact` or `inferred` resolution and available source lines.
- **Bounded results are disclosed:** a scoped analysis sets `is_partial`; Git frequency uses a bounded recent-history window; and cycle analysis reports any graph components skipped for safety limits.
- **Heuristics are candidates:** dead-code, bottleneck, coupling, and orphan-file findings need validation against framework routes, reflection, plugins, generated code, callbacks, and runtime configuration.
- **Cache control:** the default disposable `.cartographer_cache/` lives inside the approved repository. Set `use_cache=false` through MCP or pass `--no-cache` to the CLI to avoid both cache reads and writes.
- **Host boundary:** an MCP host may include returned tool data in model context. Review the host's data and account settings; “no application-level API calls” describes CodebaseCartographer itself, not every service a host may use.

## Built with Codex and GPT-5.6

This project is designed so that the deterministic local layer and the model have distinct jobs:

- CodebaseCartographer produces inspectable repository facts, provenance, coverage, and explicit uncertainty.
- Codex/GPT-5.6 chooses the evidence queries, reconciles the results with the developer's intent, and turns them into a grounded change plan instead of guessing from flat text.

The build workflow used Codex for iterative parser, graph, packaging, test, and release-readiness work. The project does not claim that its local analysis engine calls GPT-5.6: it does not. For a submission-ready proof trail and the operator-supplied items that cannot honestly be generated by the repository (a real `/feedback` session ID and a public voiceover video), see [HACKATHON.md](HACKATHON.md).

## Reproducible case study

`fixtures/sample_repo` is a small, deterministic demonstration target. Its `AuthService.authenticate` path yields a graph-backed packet with resolved local calls to `find_user`, `hash_password`, and `_create_session`; receiver-based calls such as `User.check_password` remain unresolved rather than guessed. It is a compact way to show the key product behavior: useful evidence plus clear limits.

The project does **not** yet claim a benchmarked reduction in regressions or engineering time. A future evaluation should compare evidence-backed Codex plans with ordinary repository exploration on representative private-code tasks.

## Develop and verify

```bash
python -m pip install -e ".[dev]"
python -m ruff check src tests
python -m pytest tests/ -v --tb=short
python -m build
```

GitHub Actions runs lint and tests on Python 3.11 and 3.12, builds a wheel, installs it in a clean virtual environment, analyzes the fixture through the stdio MCP surface, and then queries the resulting graph. After editing the Codex plugin bundle, validate it and refresh its cache-busting manifest version:

```bash
python <CODEX_HOME>/skills/.system/plugin-creator/scripts/validate_plugin.py plugins/codebase-cartographer
python <CODEX_HOME>/skills/.system/plugin-creator/scripts/update_plugin_cachebuster.py plugins/codebase-cartographer
```

## Project structure

```text
src/codebase_cartographer/       Local MCP server and analysis engine
plugins/codebase-cartographer/   Codex plugin bundle
.agents/plugins/                 Canonical repository marketplace catalog
fixtures/sample_repo/            Safe, deterministic judge/demo repository
integrations/                    Secondary Claude Code and Antigravity wrappers
docs/LOCAL_MCP.md                Client-neutral stdio configuration
HACKATHON.md                     Submission narrative, demo storyboard, and checklist
```

## Open source

CodebaseCartographer is released under the [MIT License](LICENSE). See [CONTRIBUTING.md](CONTRIBUTING.md) and [SECURITY.md](SECURITY.md) for contribution and security-reporting guidance.
