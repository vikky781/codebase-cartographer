---
name: codebase-cartographer
description: Use CodebaseCartographer to inspect an explicitly user-approved local repository before answering questions about its architecture, dependencies, code flow, health, or Git history.
---

# CodebaseCartographer workflow

1. Call `analyze_repo` with the absolute path to the approved repository.
2. Use `search_graph` and `trace_flow` to verify relationships before drawing conclusions.
3. Use `find_issues`, `get_metrics`, `visualize`, and `get_git_context` as appropriate.

This is static analysis. It does not execute repository code and may not observe runtime-only behavior.
