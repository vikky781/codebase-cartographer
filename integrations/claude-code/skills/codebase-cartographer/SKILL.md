---
name: codebase-cartographer
description: Analyze an explicitly user-approved local repository with CodebaseCartographer before answering questions about its architecture, dependencies, code flow, health, or Git history.
---

# CodebaseCartographer workflow

1. Begin with `analyze_repo` and an absolute path to the repository the user approved.
2. Use `search_graph` and `trace_flow` to establish facts before making architectural claims.
3. Use `find_issues`, `get_metrics`, and `visualize` for structural analysis.
4. Use `get_git_context` only for paths inside the analyzed repository.

The server performs static analysis only. Explain that dynamic dispatch, reflection, generated code, and external dependencies can make results incomplete.
