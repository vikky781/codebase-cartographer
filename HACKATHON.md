# Codebase Cartographer — Build Week submission kit

## Submission title

**Codebase Cartographer — evidence-backed change planning for Codex**

## One-line description

> A local MCP evidence engine that helps GPT-5.6/Codex trace a change's static blast radius before it writes code.

## Devpost-ready project description

Changing unfamiliar code is risky because an agent or engineer can miss a call path, a hidden import relationship, or the history that explains why a file keeps changing. Codebase Cartographer gives Codex a local, inspectable evidence layer before it proposes a change. It parses supported source files with Tree-sitter, inspects local Git history, builds a NetworkX graph, and exposes seven MCP tools for discovery, traces, metrics, issue candidates, diagrams, and ownership context.

The server never executes the analyzed repository and makes no application-level API calls. It labels unresolved and ambiguous static relationships rather than choosing a convenient answer. GPT-5.6/Codex then uses that evidence to produce a plan that distinguishes verified facts, inferences, unknowns, and recommended tests. The result is not “AI understands every codebase”; it is a safer, auditable starting point for a high-stakes change in a private repository.

## What a judge can verify today

| Claim | Reproducible evidence |
| --- | --- |
| The project is a working local MCP server | `python -m pytest tests/ -v --tb=short` verifies the FastMCP surface and seven tools. |
| It works without marketplace setup | Run the [README judge path](README.md#judge-path-try-it-in-under-a-minute) on `fixtures/sample_repo`. |
| The local engine exposes uncertainty | `analyze_repo` returns parsing and edge-resolution coverage; `trace_flow` returns provenance, resolution, and source lines. |
| Codex is the primary user experience | The canonical marketplace is [`.agents/plugins/marketplace.json`](.agents/plugins/marketplace.json), and the plugin skill prompts an evidence-to-plan workflow. |
| The data flow is local inside the server | See the [README trust and privacy contract](README.md#trust-and-privacy-contract). |

## Demo scenario

Use the included fixture for a predictable demonstration.

1. Run the fixture analysis with `--no-cache`.
2. Ask Codex: “Before changing `AuthService.authenticate`, build an evidence-backed change-impact plan.”
3. Show `search_graph` identifying `AuthService.authenticate` in `auth/service.py`.
4. Show `trace_flow` returning its local path evidence, including source lines and `exact`/`inferred` resolution.
5. Show the analysis coverage: **29 observed calls, 9 resolved, 20 unresolved, 0 ambiguous**. Explain that unresolved does not mean safe.
6. Ask Codex to label **verified facts**, **inferences**, **unknowns**, and **recommended validation** before it edits code.

The fixture has 11 Python files, 28 functions, 3 classes, 11 modules, 9 resolved call edges, and 9 import edges on the current main branch. It also deliberately contains a cycle and unused-looking code. These are controlled graph conditions, not claims about a production repository.

## Suggested 2:30 recording outline

| Time | Screen and voiceover beat |
| --- | --- |
| 0:00–0:12 | “I need to change an auth path in an unfamiliar private repository. Before Codex writes code, I need to know what could break.” |
| 0:12–0:30 | Run the one-command fixture analysis and point out the local-only/no-cache path. |
| 0:30–1:05 | Use `search_graph` and `trace_flow` for `AuthService.authenticate`; show graph path source lines and coverage. |
| 1:05–1:35 | Show `get_metrics`/`get_git_context` where available, then explain what the static graph cannot resolve. |
| 1:35–2:05 | Ask GPT-5.6/Codex for a change plan with facts, inferences, unknowns, and tests. Show that it does not overclaim. |
| 2:05–2:30 | Recap: local evidence, Codex-native MCP workflow, capability limits, and how judges can reproduce it. |

Use clear English voiceover. Keep the publicly visible video under three minutes. Do not claim that an absent static edge proves there is no runtime dependency.

## How Codex and GPT-5.6 are meaningfully used

The local engine is intentionally non-LLM. It produces data that a model should not have to infer from raw text: dependency candidates, resolution status, source lines, Git context, and documented blind spots. Codex/GPT-5.6 is the decision layer: it selects which evidence to inspect, interprets it against the requested change, separates evidence from inference, and recommends a test plan.

For the final submission, show that interaction in the public video and describe the actual Codex/GPT-5.6 task in the Devpost entry. Do not represent the deterministic parser as an OpenAI model call.

## Operator checklist before submission

These are external submission actions. They cannot be truthfully generated or completed by this repository.

- [ ] Run a real Codex task using GPT-5.6 on the evidence-to-plan workflow.
- [ ] Run `/feedback` in that primary Codex task and copy its real session ID into the submission.
- [ ] Record and publish a publicly visible YouTube video under three minutes with English voiceover.
- [ ] Add the video link, Codex `/feedback` session ID, and this repository URL to the Devpost entry.
- [ ] Re-run the judge path and CI checks on the exact commit being submitted.
- [ ] Verify the README describes the real demo rather than an aspirational one.

Consult the current [OpenAI Build Week rules](https://openai.devpost.com/rules) and [FAQ](https://openai.devpost.com/details/faqs) before submission; event requirements can change.

## Honest boundaries

- Deep static parsing is currently for Python, JavaScript, TypeScript, and TSX. Other listed languages are regex-based inventory only.
- The current `complexity` API field is a static line span, not cyclomatic complexity.
- Dynamic dispatch, reflection, aliases, callbacks, re-exports, code generation, dependency injection, and framework wiring may be missing from the graph.
- The server does not make application-level API calls, but MCP results can become model context under the selected host's settings.
- The active graph is process-local and intended for one local repository analysis session, not enterprise multi-repository indexing.
