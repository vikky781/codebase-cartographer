# Contributing to CodebaseCartographer

Thanks for helping improve the local codebase-analysis engine and its host integrations.

## Development setup

1. Use Python 3.11 or newer.
2. Create an isolated environment and install development dependencies:

   ```bash
   python -m pip install -e ".[dev]"
   ```

3. Before opening a pull request, run:

   ```bash
   ruff check src tests
   python -m pytest tests/ -v --tb=short
   ```

## Contributions

- Keep the MCP server local-first: do not add source-code uploads or external analysis APIs without an explicit, documented product decision.
- Preserve per-file error isolation, repository-boundary checks, and output limits.
- Add or update fixture-based tests for parser, graph, Git, or client-integration changes.
- Keep client wrappers thin. Codex, Claude Code, and Antigravity must launch the same `cartographer-mcp` stdio server.
- Use clear, focused commits so changes are easy to review and revert.

## Reporting bugs

Include the operating system, Python version, client host, installation method, and a minimal reproducible repository layout. Do not paste proprietary source code, credentials, or Git history into a public issue.
