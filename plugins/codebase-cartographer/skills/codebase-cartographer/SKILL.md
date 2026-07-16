---
name: codebase-cartographer
description: Analyze an explicitly user-approved local repository with CodebaseCartographer before answering questions about its architecture, dependencies, code flow, health, or Git history.
---

# CodebaseCartographer workflow

Use the CodebaseCartographer MCP tools to inspect the repository the user has explicitly asked to analyze.

1. Start with `analyze_repo` and an absolute repository path. Use a narrow `scope` when the repository is large.
2. Read the returned `coverage`, `warnings`, `analysis_scope`, and `is_partial` before making claims.
   Treat unresolved or ambiguous relationships as unknown, never as evidence that a dependency does
   not exist. When `is_partial` is true, say that unscanned files may change the conclusion.
3. Use `search_graph` to establish facts about functions, classes, and modules before making claims.
4. For a proposed change, build an evidence packet with `trace_flow`, `get_metrics`, and, when
   relevant, `get_git_context`. Include the exact graph paths, relationship source, resolution,
   source lines, ownership/change context, and static-analysis limits.
5. Separate the response into **verified facts**, **inferences**, **unknowns**, and **recommended
   validation**. Ask for or identify tests before proposing an implementation.
6. Use `find_issues` only as a source of candidates. Do not state that a function is dead or a file
   is unused without confirming framework wiring, runtime dispatch, and entry points.
7. Use `visualize` when a Mermaid diagram will make the relationship clearer.

Safety rules:

- Analyze only a repository path the user has named or approved.
- Never infer facts from an earlier analysis if the user may have changed repositories; analyze again.
- Explain that results are static-analysis heuristics and may miss dynamic dispatch, reflection,
  generated code, external dependencies, aliases, callbacks, and framework wiring.
- Do not request or expose files outside the analyzed repository.
