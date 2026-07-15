---
name: codebase-cartographer
description: Analyze an explicitly user-approved local repository with CodebaseCartographer before answering questions about its architecture, dependencies, code flow, health, or Git history.
---

# CodebaseCartographer workflow

Use the CodebaseCartographer MCP tools to inspect the repository the user has explicitly asked to analyze.

1. Start with `analyze_repo` and an absolute repository path. Use a narrow `scope` when the repository is large.
2. Use `search_graph` to establish facts about functions, classes, and modules before making claims.
3. Use `trace_flow` for control-flow and dependency questions; use `find_issues` and `get_metrics` for structural health questions.
4. Use `get_git_context` only for a file in the analyzed repository and explain that it reads local Git history.
5. Use `visualize` when a Mermaid diagram will make the relationship clearer.

Safety rules:

- Analyze only a repository path the user has named or approved.
- Never infer facts from an earlier analysis if the user may have changed repositories; analyze again.
- Explain that results are static-analysis heuristics and may miss dynamic dispatch, reflection, generated code, or external dependencies.
- Do not request or expose files outside the analyzed repository.
