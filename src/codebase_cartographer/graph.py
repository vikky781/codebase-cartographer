from __future__ import annotations

import json
import re
from collections import defaultdict
from dataclasses import dataclass, field
from itertools import islice
from pathlib import Path
from tempfile import NamedTemporaryFile

import networkx as nx

from .code_parser import CallEdge, ParsedFile
from .config import ENTRY_POINT_PATTERNS, get_config
from .git_analyzer import GitAnalyzer
from .models import (
    CodeEntity,
    EdgeCounts,
    EntityCounts,
    HealthSummary,
    Issue,
    MetricResult,
    TraceOutput,
    TraceStep,
)

__all__ = ["CodeGraph", "Issue", "get_graph"]


@dataclass
class _GraphCache:
    """Represent the JSON-serializable pieces of a cached code graph."""

    nodes: list[dict] = field(default_factory=list)
    edges: list[dict] = field(default_factory=list)
    schema_version: int = 1
    repo_hash: str = ""
    entities: dict[str, dict] = field(default_factory=dict)
    modules: dict[str, str] = field(default_factory=dict)


class CodeGraph:
    """Build and query a directed knowledge graph for one analyzed repository."""

    def __init__(self) -> None:
        """Create an empty code graph and algorithm caches."""
        self.graph: nx.DiGraph = nx.DiGraph()
        self.entities: dict[str, CodeEntity] = {}
        self.modules: dict[str, str] = {}
        self.repo_path: str = ""
        self.repo_hash: str = ""
        self.is_built: bool = False
        self._pagerank_cache: dict[str, float] | None = None
        self._centrality_cache: dict[str, float] | None = None
        self._communities_cache: dict[str, int] | None = None

    def build(self, parsed_files: list[ParsedFile], repo_path: str, repo_hash: str) -> None:
        """Build the graph from parsed files and their extracted relationships."""
        self.graph.clear()
        self.entities.clear()
        self.modules.clear()
        self.repo_path = str(Path(repo_path).resolve())
        self.repo_hash = repo_hash
        self.is_built = False
        self._clear_algorithm_caches()

        # Step A: module nodes have bare file-path identifiers.
        for parsed_file in parsed_files:
            file_path = parsed_file.file_path.replace("\\", "/")
            module_entity = next(
                (entity for entity in parsed_file.entities if entity.type == "module"),
                CodeEntity(
                    name=Path(file_path).stem,
                    type="module",
                    file_path=file_path,
                    line_start=1,
                    line_end=1,
                ),
            )
            self.graph.add_node(
                file_path,
                type="module",
                file_path=file_path,
                name=module_entity.name,
                complexity=module_entity.complexity,
                line_start=module_entity.line_start,
                line_end=module_entity.line_end,
                signature=module_entity.signature,
            )
            self.entities[file_path] = module_entity
            self._register_module_aliases(file_path)

        # Step B: add only function and class nodes. Module entities already use their file path.
        for parsed_file in parsed_files:
            file_path = parsed_file.file_path.replace("\\", "/")
            for entity in parsed_file.entities:
                if entity.type == "module":
                    continue
                node_id = f"{file_path}::{entity.name}"
                self.entities[node_id] = entity
                self.graph.add_node(
                    node_id,
                    type=entity.type,
                    file_path=entity.file_path,
                    name=entity.name,
                    complexity=entity.complexity,
                    line_start=entity.line_start,
                    line_end=entity.line_end,
                    signature=entity.signature,
                )

        # Step C: module-to-module import edges.
        for parsed_file in parsed_files:
            source_file = parsed_file.file_path.replace("\\", "/")
            for import_edge in parsed_file.imports:
                target_file = self._resolve_module(import_edge.target_module, source_file)
                if target_file is None and import_edge.target_module.strip(".") == "":
                    # ``from . import name`` often refers to a sibling module.
                    for imported_name in import_edge.imported_names:
                        target_file = self._resolve_module(
                            f"{import_edge.target_module}{imported_name}", source_file
                        )
                        if target_file is not None:
                            break
                if target_file is not None:
                    self._add_counted_edge(source_file, target_file, "imports")

        # Step D: function/class call edges, resolved locally before imported modules.
        for parsed_file in parsed_files:
            source_file = parsed_file.file_path.replace("\\", "/")
            for call_edge in parsed_file.calls:
                caller = call_edge.caller.replace("\\", "/")
                if caller not in self.entities:
                    continue
                callee = self._resolve_callee(call_edge, source_file)
                if callee is not None:
                    self._add_counted_edge(caller, callee, "calls")

        self.is_built = True

    def _clear_algorithm_caches(self) -> None:
        """Clear all cached graph-algorithm results."""
        self._pagerank_cache = None
        self._centrality_cache = None
        self._communities_cache = None

    def _register_module_aliases(self, file_path: str) -> None:
        """Register flexible import aliases for one repository-relative file path."""
        normalized = file_path.replace("\\", "/").strip("/")
        no_suffix = (
            normalized.rsplit(".", 1)[0] if "." in normalized.rsplit("/", 1)[-1] else normalized
        )
        dotted = no_suffix.replace("/", ".")
        stem = no_suffix.rsplit("/", 1)[-1]
        aliases = {no_suffix, dotted, stem}

        if no_suffix.startswith("src/"):
            without_src = no_suffix[4:]
            aliases.update({without_src, without_src.replace("/", ".")})
        else:
            aliases.update({f"src/{no_suffix}", f"src.{dotted}"})

        if stem == "__init__":
            package_path = no_suffix.rsplit("/", 1)[0] if "/" in no_suffix else ""
            if package_path:
                aliases.update({package_path, package_path.replace("/", ".")})

        for alias in aliases:
            if alias:
                self.modules.setdefault(alias, normalized)

    def _resolve_module(self, import_target: str, source_file: str) -> str | None:
        """Resolve an import target to a module file path in the built graph."""
        raw_target = import_target.strip().strip("\"'").replace("\\", "/")
        if not raw_target:
            return None

        candidates: list[str] = []

        # Relative JavaScript-style imports (./thing and ../thing).
        if raw_target.startswith("./") or raw_target.startswith("../"):
            source_parts = source_file.replace("\\", "/").split("/")[:-1]
            relative_target = raw_target
            while relative_target.startswith("../"):
                if source_parts:
                    source_parts.pop()
                relative_target = relative_target[3:]
            if relative_target.startswith("./"):
                relative_target = relative_target[2:]
            candidates.append("/".join([*source_parts, relative_target]).strip("/"))

        # Relative Python imports (.module and ..module).
        leading_dots = len(raw_target) - len(raw_target.lstrip("."))
        if leading_dots:
            source_parts = source_file.replace("\\", "/").split("/")[:-1]
            for _ in range(max(0, leading_dots - 1)):
                if source_parts:
                    source_parts.pop()
            relative_target = raw_target[leading_dots:].lstrip("/")
            if relative_target:
                candidates.append("/".join([*source_parts, relative_target]).strip("/"))

        cleaned = raw_target.lstrip(".").lstrip("/")
        if cleaned:
            candidates.append(cleaned)

        expanded_candidates: list[str] = []
        for candidate in candidates:
            candidate = candidate.strip("/")
            if not candidate:
                continue
            without_suffix = (
                candidate.rsplit(".", 1)[0] if "." in candidate.rsplit("/", 1)[-1] else candidate
            )
            expanded_candidates.extend(
                [
                    candidate,
                    without_suffix,
                    without_suffix.replace(".", "/"),
                    without_suffix.replace("/", "."),
                ]
            )

        for candidate in expanded_candidates:
            resolved = self.modules.get(candidate)
            if resolved is not None:
                return resolved

        target_tail = cleaned.replace("/", ".").strip(".")
        if target_tail:
            matches = sorted(
                {
                    file_path
                    for module_name, file_path in self.modules.items()
                    if module_name.replace("/", ".").endswith(target_tail)
                }
            )
            if matches:
                return matches[0]
        return None

    def _resolve_callee(self, call_edge: CallEdge, source_file: str) -> str | None:
        """Resolve a simple call name first locally, then through imported modules."""
        same_file = self._find_entity_by_name(call_edge.callee, source_file)
        if same_file is not None:
            return same_file

        imported_files = [
            target
            for target in self.graph.successors(source_file)
            if self.graph.get_edge_data(source_file, target, {}).get("type") == "imports"
        ]
        for imported_file in imported_files:
            resolved = self._find_entity_by_name(call_edge.callee, imported_file)
            if resolved is not None:
                return resolved
        return None

    def _find_entity_by_name(self, name: str, file_path: str | None = None) -> str | None:
        """Find the most specific function or class node matching a name."""
        exact_matches: list[str] = []
        terminal_matches: list[str] = []
        for node_id, entity in self.entities.items():
            if entity.type == "module" or (file_path is not None and entity.file_path != file_path):
                continue
            if entity.name == name:
                exact_matches.append(node_id)
            elif entity.name.rsplit(".", 1)[-1] == name:
                terminal_matches.append(node_id)
        if exact_matches:
            return sorted(exact_matches)[0]
        if terminal_matches:
            return sorted(terminal_matches)[0]
        return None

    def _add_counted_edge(self, source: str, target: str, edge_type: str) -> None:
        """Add an edge while retaining repeated relationships in a DiGraph count attribute."""
        existing = self.graph.get_edge_data(source, target)
        if existing is not None and existing.get("type") == edge_type:
            existing["count"] = int(existing.get("count", 1)) + 1
            return
        self.graph.add_edge(source, target, type=edge_type, count=1)

    def search(
        self, query: str, entity_type: str | None = None, limit: int = 20
    ) -> list[CodeEntity]:
        """Search entities by a bounded, literal name or file-path substring."""
        if len(query) > get_config().max_search_query_length:
            raise ValueError(
                f"Search query exceeds the {get_config().max_search_query_length}-character limit."
            )
        normalized_query = query.casefold()

        matches: list[CodeEntity] = []
        for entity in self.entities.values():
            if entity_type is not None and entity.type != entity_type:
                continue
            if (
                normalized_query in entity.name.casefold()
                or normalized_query in entity.file_path.casefold()
            ):
                matches.append(entity)

        def relevance(entity: CodeEntity) -> tuple[int, str, str]:
            name = entity.name.casefold()
            file_path = entity.file_path.casefold()
            if name == normalized_query:
                return (0, name, file_path)
            if normalized_query in name:
                return (1, name, file_path)
            return (2, name, file_path)

        results: list[CodeEntity] = []
        for entity in sorted(matches, key=relevance)[: max(0, limit)]:
            node_id = self._node_id_for_entity(entity)
            copied = entity.model_copy(deep=True)
            if node_id is not None:
                copied.calls = [
                    self.entities[target].name
                    for target in self.graph.successors(node_id)
                    if target in self.entities
                    and self.graph.get_edge_data(node_id, target, {}).get("type") == "calls"
                ]
                copied.called_by = [
                    self.entities[source].name
                    for source in self.graph.predecessors(node_id)
                    if source in self.entities
                    and self.graph.get_edge_data(source, node_id, {}).get("type") == "calls"
                ]
            results.append(copied)
        return results

    def _node_id_for_entity(self, entity: CodeEntity) -> str | None:
        """Return the graph node id associated with an entity object."""
        if entity.type == "module":
            return entity.file_path if entity.file_path in self.entities else None
        node_id = f"{entity.file_path}::{entity.name}"
        return node_id if node_id in self.entities else None

    def trace(
        self, entity_name: str, direction: str = "forward", max_depth: int = 5
    ) -> TraceOutput:
        """Trace graph relationships from an entity with breadth-first traversal."""
        start_node = self._find_trace_start(entity_name)
        if start_node is None:
            return TraceOutput(start=entity_name, direction=direction)

        config = get_config()
        steps: list[TraceStep] = []
        queue: list[tuple[str, int]] = [(start_node, 0)]
        visited = {start_node}
        index = 0
        truncated = False

        while index < len(queue):
            current, depth = queue[index]
            index += 1
            neighbors = self._trace_neighbors(current, direction)
            if depth >= max_depth:
                if any(neighbor not in visited for neighbor, _ in neighbors):
                    truncated = True
                continue

            for neighbor, relationship in neighbors:
                if neighbor in visited:
                    continue
                visited.add(neighbor)
                queue.append((neighbor, depth + 1))
                entity = self.entities.get(neighbor)
                if entity is None:
                    continue
                steps.append(
                    TraceStep(
                        depth=depth + 1,
                        entity=entity.model_copy(deep=True),
                        relationship=relationship,
                    )
                )
                if len(steps) >= config.max_trace_steps:
                    truncated = index < len(queue) or bool(neighbors)
                    break
            if len(steps) >= config.max_trace_steps:
                break

        return TraceOutput(start=entity_name, direction=direction, steps=steps, truncated=truncated)

    def _find_trace_start(self, entity_name: str) -> str | None:
        """Find an exact node id or the most relevant fuzzy entity match."""
        if entity_name in self.entities:
            return entity_name

        exact = [node_id for node_id, entity in self.entities.items() if entity.name == entity_name]
        if exact:
            return sorted(exact)[0]

        lowered = entity_name.casefold()
        fuzzy = [
            node_id
            for node_id, entity in self.entities.items()
            if lowered in entity.name.casefold() or lowered in entity.file_path.casefold()
        ]
        return sorted(fuzzy)[0] if fuzzy else None

    def _trace_neighbors(self, node_id: str, direction: str) -> list[tuple[str, str]]:
        """Return neighboring nodes with the relationship traversed to reach them."""
        neighbors: list[tuple[str, str]] = []
        if direction in {"forward", "both"}:
            neighbors.extend(
                (target, str(data.get("type", "calls")))
                for _, target, data in self.graph.out_edges(node_id, data=True)
            )
        if direction in {"backward", "both"}:
            neighbors.extend(
                (source, str(data.get("type", "calls")))
                for source, _, data in self.graph.in_edges(node_id, data=True)
            )
        return neighbors

    def _new_module_graph(self) -> nx.DiGraph:
        """Create a module-only graph while preserving module-node attributes."""
        module_graph = nx.DiGraph()
        for node_id, attributes in self.graph.nodes(data=True):
            if attributes.get("type") == "module":
                module_graph.add_node(node_id, **dict(attributes))
        return module_graph

    @staticmethod
    def _add_module_relationship(
        module_graph: nx.DiGraph,
        source_module: str,
        target_module: str,
        relationship: str,
        count: int,
    ) -> None:
        """Accumulate an import or resolved cross-module call into a weighted edge."""
        if source_module == target_module:
            return
        if source_module not in module_graph or target_module not in module_graph:
            return

        attributes = module_graph.get_edge_data(source_module, target_module)
        if attributes is None:
            module_graph.add_edge(
                source_module,
                target_module,
                import_count=0,
                call_count=0,
                weight=0,
                distance=1.0,
            )
            attributes = module_graph[source_module][target_module]
        count_key = "import_count" if relationship == "imports" else "call_count"
        attributes[count_key] = int(attributes.get(count_key, 0)) + max(1, count)
        attributes["weight"] = int(attributes.get("import_count", 0)) + int(
            attributes.get("call_count", 0)
        )
        attributes["distance"] = 1.0 / max(1, int(attributes["weight"]))

    def _module_graph(self) -> nx.DiGraph:
        """Return a weighted module graph of imports and resolved cross-module calls."""
        module_graph = self._new_module_graph()
        for source, target, attributes in self.graph.edges(data=True):
            source_file = str(self.graph.nodes[source].get("file_path", ""))
            target_file = str(self.graph.nodes[target].get("file_path", ""))
            relationship = str(attributes.get("type", ""))
            if relationship not in {"imports", "calls"}:
                continue
            self._add_module_relationship(
                module_graph,
                source_file,
                target_file,
                relationship,
                int(attributes.get("count", 1)),
            )
        return module_graph

    def _import_module_graph(self) -> nx.DiGraph:
        """Return a module graph containing only direct import relationships."""
        module_graph = self._new_module_graph()
        for source, target, attributes in self.graph.edges(data=True):
            if attributes.get("type") != "imports":
                continue
            if (
                self.graph.nodes[source].get("type") != "module"
                or self.graph.nodes[target].get("type") != "module"
            ):
                continue
            self._add_module_relationship(
                module_graph,
                str(self.graph.nodes[source].get("file_path", source)),
                str(self.graph.nodes[target].get("file_path", target)),
                "imports",
                int(attributes.get("count", 1)),
            )
        return module_graph

    def get_pagerank(self, top_n: int = 15) -> list[MetricResult]:
        """Rank modules by PageRank on import and resolved call relationships."""
        if self._pagerank_cache is None:
            module_graph = self._module_graph()
            if not module_graph.nodes:
                self._pagerank_cache = {}
            else:
                try:
                    self._pagerank_cache = nx.pagerank(module_graph, alpha=0.85, weight="weight")
                except Exception:
                    # NetworkX 3.6 delegates PageRank to NumPy, which is intentionally
                    # not a required dependency for this local-only MCP server.
                    self._pagerank_cache = self._pagerank_fallback(module_graph, alpha=0.85)

        ranked = sorted(self._pagerank_cache.items(), key=lambda item: item[1], reverse=True)
        results: list[MetricResult] = []
        for rank, (node_id, score) in enumerate(ranked[: max(0, top_n)], start=1):
            if rank == 1:
                interpretation = "Most imported module — core dependency"
            elif rank <= 5:
                interpretation = "Heavily imported — key infrastructure"
            else:
                interpretation = "Important dependency in the module graph"
            results.append(
                MetricResult(
                    entity_name=self.graph.nodes[node_id].get("name", Path(node_id).stem),
                    entity_type="module",
                    file_path=node_id,
                    score=round(score, 6),
                    rank=rank,
                    interpretation=interpretation,
                    source="networkx-pagerank",
                )
            )
        return results

    @staticmethod
    def _pagerank_fallback(
        graph: nx.DiGraph, alpha: float = 0.85, max_iter: int = 100, tol: float = 1.0e-6
    ) -> dict[str, float]:
        """Compute weighted PageRank without NumPy for a small module graph."""
        nodes = list(graph.nodes)
        node_count = len(nodes)
        if node_count == 0:
            return {}

        scores = {node_id: 1.0 / node_count for node_id in nodes}
        base_score = (1.0 - alpha) / node_count
        for _ in range(max_iter):
            next_scores = {node_id: base_score for node_id in nodes}
            dangling_score = sum(
                scores[node_id] for node_id in nodes if graph.out_degree(node_id) == 0
            )
            dangling_share = alpha * dangling_score / node_count
            for node_id in nodes:
                next_scores[node_id] += dangling_share

            for source in nodes:
                successors = list(graph.successors(source))
                if not successors:
                    continue
                total_weight = sum(
                    max(0.0, float(graph[source][target].get("weight", 1))) for target in successors
                )
                if total_weight <= 0:
                    total_weight = float(len(successors))
                for target in successors:
                    weight = max(0.0, float(graph[source][target].get("weight", 1)))
                    if weight == 0:
                        weight = 1.0
                    next_scores[target] += alpha * scores[source] * (weight / total_weight)

            error = sum(abs(next_scores[node_id] - scores[node_id]) for node_id in nodes)
            scores = next_scores
            if error < node_count * tol:
                break
        return scores

    def get_centrality(self, top_n: int = 15) -> list[MetricResult]:
        """Rank modules by betweenness centrality."""
        if self._centrality_cache is None:
            try:
                module_graph = self._module_graph()
                if not module_graph.nodes:
                    self._centrality_cache = {}
                elif len(module_graph) > get_config().max_exact_centrality_nodes:
                    self._centrality_cache = nx.betweenness_centrality(
                        module_graph,
                        k=min(get_config().centrality_sample_nodes, len(module_graph)),
                        weight="distance",
                        seed=0,
                    )
                else:
                    self._centrality_cache = nx.betweenness_centrality(
                        module_graph, weight="distance"
                    )
            except Exception:
                self._centrality_cache = {}

        ranked = sorted(self._centrality_cache.items(), key=lambda item: item[1], reverse=True)
        results: list[MetricResult] = []
        for rank, (node_id, score) in enumerate(ranked[: max(0, top_n)], start=1):
            if score > 0.1:
                interpretation = "Critical bridge between subsystems"
            elif score > 0:
                interpretation = "Moderate connector between modules"
            else:
                interpretation = "Limited bridge role in the module graph"
            results.append(
                MetricResult(
                    entity_name=self.graph.nodes[node_id].get("name", Path(node_id).stem),
                    entity_type="module",
                    file_path=node_id,
                    score=round(score, 6),
                    rank=rank,
                    interpretation=interpretation,
                    source="networkx-centrality",
                )
            )
        return results

    def get_complexity(self, top_n: int = 15) -> list[MetricResult]:
        """Rank functions and classes by their extracted complexity score."""
        code_entities = [
            (node_id, entity)
            for node_id, entity in self.entities.items()
            if entity.type in {"function", "class"}
        ]
        ranked = sorted(code_entities, key=lambda item: item[1].complexity, reverse=True)
        results: list[MetricResult] = []
        for rank, (_, entity) in enumerate(ranked[: max(0, top_n)], start=1):
            if entity.complexity > 50:
                interpretation = "Very complex — consider refactoring"
            elif entity.complexity >= 20:
                interpretation = "Moderate complexity"
            else:
                interpretation = "Low complexity"
            results.append(
                MetricResult(
                    entity_name=entity.name,
                    entity_type=entity.type,
                    file_path=entity.file_path,
                    score=float(entity.complexity),
                    rank=rank,
                    interpretation=interpretation,
                    source="tree-sitter-complexity",
                )
            )
        return results

    def get_coupling(self, top_n: int = 15) -> list[MetricResult]:
        """Rank cross-module pairs by the number of import and call relationships."""
        pair_counts: defaultdict[tuple[str, str], int] = defaultdict(int)
        for source, target, attributes in self.graph.edges(data=True):
            source_file = self.graph.nodes[source].get("file_path")
            target_file = self.graph.nodes[target].get("file_path")
            if not source_file or not target_file or source_file == target_file:
                continue
            pair = tuple(sorted((str(source_file), str(target_file))))
            pair_counts[pair] += int(attributes.get("count", 1))

        ranked = sorted(pair_counts.items(), key=lambda item: item[1], reverse=True)
        results: list[MetricResult] = []
        for rank, ((module_a, module_b), count) in enumerate(ranked[: max(0, top_n)], start=1):
            results.append(
                MetricResult(
                    entity_name=f"{module_a} <-> {module_b}",
                    entity_type="module_pair",
                    file_path=f"{module_a}, {module_b}",
                    score=float(count),
                    rank=rank,
                    interpretation=f"Tightly coupled — {count} cross-references",
                    source="networkx-edge-count",
                )
            )
        return results

    def get_communities(self) -> dict[str, int]:
        """Detect module communities with a Louvain-style modularity algorithm."""
        if self._communities_cache is not None:
            return self._communities_cache

        module_graph = self._module_graph()
        if not module_graph.nodes:
            self._communities_cache = {}
            return self._communities_cache

        try:
            undirected = module_graph.to_undirected()
            if len(undirected) == 1:
                communities = [set(undirected.nodes)]
            elif hasattr(nx.community, "louvain_communities"):
                communities = nx.community.louvain_communities(undirected, seed=0)
            else:
                communities = nx.community.greedy_modularity_communities(undirected)
            self._communities_cache = {
                node_id: index
                for index, community in enumerate(communities)
                for node_id in community
            }
        except Exception:
            self._communities_cache = {
                node_id: index for index, node_id in enumerate(module_graph.nodes)
            }
        return self._communities_cache

    def find_cycles(self) -> list[list[str]]:
        """Return module import cycles up to ten modules long, shortest first."""
        module_graph = self._import_module_graph()
        try:
            limit = get_config().max_cycles
            try:
                cycles = list(islice(nx.simple_cycles(module_graph, length_bound=10), limit))
            except TypeError:
                cycles = list(
                    islice(
                        (cycle for cycle in nx.simple_cycles(module_graph) if len(cycle) <= 10),
                        limit,
                    )
                )
            return sorted(cycles, key=lambda cycle: (len(cycle), cycle))
        except Exception:
            return []

    def find_dead_code(self) -> list[CodeEntity]:
        """Return functions and classes with no inbound calls and no entry-point role."""
        dead_entities: list[CodeEntity] = []
        for node_id, entity in self.entities.items():
            if entity.type not in {"function", "class"}:
                continue
            if self.graph.in_degree(node_id) == 0 and not self._is_entry_point(entity.name):
                dead_entities.append(entity)
        return dead_entities

    def _is_entry_point(self, name: str) -> bool:
        """Return whether a name matches a non-empty configured entry-point pattern."""
        candidates = {name, name.rsplit(".", 1)[-1]}
        for candidate in candidates:
            for pattern in ENTRY_POINT_PATTERNS:
                try:
                    match = re.match(pattern, candidate)
                    # A malformed permissive pattern must not classify every name as an entry point.
                    if match is not None and match.end() > match.start():
                        return True
                except re.error:
                    continue
        return False

    def find_god_classes(self) -> list[tuple[CodeEntity, int, int]]:
        """Return classes whose class-and-method fan-in/out exceeds the configured threshold."""
        threshold = get_config().god_class_fan_threshold
        results: list[tuple[CodeEntity, int, int]] = []
        for node_id, entity in self.entities.items():
            if entity.type != "class":
                continue
            class_members = {
                node_id,
                *(
                    candidate_id
                    for candidate_id, candidate in self.entities.items()
                    if candidate.type == "function"
                    and candidate.file_path == entity.file_path
                    and candidate.name.startswith(f"{entity.name}.")
                ),
            }
            incoming = {
                source
                for member in class_members
                for source in self.graph.predecessors(member)
                if source not in class_members
            }
            outgoing = {
                target
                for member in class_members
                for target in self.graph.successors(member)
                if target not in class_members
            }
            fan_in = len(incoming)
            fan_out = len(outgoing)
            if fan_in + fan_out > threshold:
                results.append((entity, fan_in, fan_out))
        return sorted(results, key=lambda result: result[1] + result[2], reverse=True)

    def find_bottlenecks(self, top_n: int = 5) -> list[MetricResult]:
        """Return highly central modules that may be single points of failure."""
        return [result for result in self.get_centrality(top_n=top_n) if result.score > 0.1][
            : max(0, top_n)
        ]

    def find_orphan_files(self) -> list[str]:
        """Return non-entry modules that have no inbound import relationships."""
        common_entry_names = {"__init__", "main", "setup", "conftest"}
        orphan_files: list[str] = []
        for node_id, attributes in self.graph.nodes(data=True):
            if attributes.get("type") != "module":
                continue
            incoming_imports = sum(
                1
                for source, _, edge_data in self.graph.in_edges(node_id, data=True)
                if edge_data.get("type") == "imports"
            )
            module_name = str(attributes.get("name", Path(node_id).stem))
            if (
                incoming_imports == 0
                and module_name not in common_entry_names
                and not self._is_entry_point(module_name)
            ):
                orphan_files.append(str(attributes.get("file_path", node_id)))
        return sorted(orphan_files)

    def get_entity_counts(self) -> EntityCounts:
        """Count function, class, and module nodes in the graph."""
        return EntityCounts(
            functions=sum(entity.type == "function" for entity in self.entities.values()),
            classes=sum(entity.type == "class" for entity in self.entities.values()),
            modules=sum(entity.type == "module" for entity in self.entities.values()),
        )

    def get_edge_counts(self) -> EdgeCounts:
        """Count call and import relationships, including repeated occurrences."""
        return EdgeCounts(
            calls=sum(
                int(attributes.get("count", 1))
                for _, _, attributes in self.graph.edges(data=True)
                if attributes.get("type") == "calls"
            ),
            imports=sum(
                int(attributes.get("count", 1))
                for _, _, attributes in self.graph.edges(data=True)
                if attributes.get("type") == "imports"
            ),
        )

    def get_health_summary(self) -> HealthSummary:
        """Compute aggregate graph and structural health indicators."""
        complexity_scores = [
            entity.complexity
            for entity in self.entities.values()
            if entity.type in {"function", "class"}
        ]
        return HealthSummary(
            circular_dependencies=len(self.find_cycles()),
            dead_functions=len(self.find_dead_code()),
            god_classes=len(self.find_god_classes()),
            avg_complexity=(sum(complexity_scores) / len(complexity_scores))
            if complexity_scores
            else 0.0,
            bottleneck_count=len(self.find_bottlenecks()),
            orphan_files=len(self.find_orphan_files()),
        )

    def detect_layers(self) -> list[str]:
        """Infer named architectural layers from module communities and directories."""
        layer_names = {
            "routes": "API Layer",
            "api": "API Layer",
            "endpoints": "API Layer",
            "views": "API Layer",
            "handlers": "API Layer",
            "services": "Service Layer",
            "business": "Service Layer",
            "logic": "Service Layer",
            "core": "Service Layer",
            "models": "Data/Model Layer",
            "schemas": "Data/Model Layer",
            "entities": "Data/Model Layer",
            "domain": "Data/Model Layer",
            "repositories": "Data Access Layer",
            "db": "Data Access Layer",
            "database": "Data Access Layer",
            "dal": "Data Access Layer",
            "store": "Data Access Layer",
            "utils": "Utility Layer",
            "helpers": "Utility Layer",
            "common": "Utility Layer",
            "shared": "Utility Layer",
            "lib": "Utility Layer",
            "tests": "Test Layer",
            "test": "Test Layer",
            "spec": "Test Layer",
            "config": "Configuration Layer",
            "settings": "Configuration Layer",
        }
        community_members: defaultdict[int, list[str]] = defaultdict(list)
        for node_id, community_id in self.get_communities().items():
            community_members[community_id].append(node_id)

        layers: set[str] = set()
        for members in community_members.values():
            directory_parts = [member.replace("\\", "/").split("/")[:-1] for member in members]
            if not directory_parts:
                continue
            common_parts = directory_parts[0]
            for parts in directory_parts[1:]:
                prefix: list[str] = []
                for part, other in zip(common_parts, parts):
                    if part != other:
                        break
                    prefix.append(part)
                common_parts = prefix
            selected = next(
                (part.lower() for part in reversed(common_parts) if part.lower() in layer_names),
                None,
            )
            if selected is not None:
                layers.add(layer_names[selected])
            elif common_parts:
                layers.add(common_parts[-1].replace("_", " ").title())
        return sorted(layers)

    def save_cache(self, repo_path: str) -> None:
        """Serialize graph nodes, edges, and entities to the configured JSON cache file."""
        temporary_path: Path | None = None
        try:
            config = get_config()
            if not self.repo_hash:
                return
            cache_path = config.get_cache_path(repo_path)
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            cache = _GraphCache(
                nodes=[
                    {"id": node_id, "attributes": dict(attributes)}
                    for node_id, attributes in self.graph.nodes(data=True)
                ],
                edges=[
                    {"source": source, "target": target, "attributes": dict(attributes)}
                    for source, target, attributes in self.graph.edges(data=True)
                ],
                repo_hash=self.repo_hash,
                entities={
                    node_id: entity.model_dump(mode="json")
                    for node_id, entity in self.entities.items()
                },
                modules=dict(self.modules),
                schema_version=config.cache_schema_version,
            )
            payload = {
                "schema_version": cache.schema_version,
                "nodes": cache.nodes,
                "edges": cache.edges,
                "repo_hash": cache.repo_hash,
                "entities": cache.entities,
                "modules": cache.modules,
            }
            with NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                dir=cache_path.parent,
                prefix=f"{cache_path.name}.",
                suffix=".tmp",
                delete=False,
            ) as cache_file:
                temporary_path = Path(cache_file.name)
                json.dump(
                    payload,
                    cache_file,
                    indent=2,
                )
                cache_file.flush()
            temporary_path.replace(cache_path)
        except Exception:
            if temporary_path is not None:
                try:
                    temporary_path.unlink(missing_ok=True)
                except Exception:
                    pass
            return

    def load_cache(self, repo_path: str) -> bool:
        """Load a valid graph cache when its stored hash matches the repository HEAD."""
        try:
            config = get_config()
            cache_path = config.get_cache_path(repo_path)
            if not cache_path.exists():
                return False

            with cache_path.open("r", encoding="utf-8") as cache_file:
                payload = json.load(cache_file)
            if not isinstance(payload, dict):
                return False
            if payload.get("schema_version") != config.cache_schema_version:
                return False
            cached_hash = str(payload.get("repo_hash", ""))
            current_hash = GitAnalyzer(repo_path).get_head_hash()
            if not cached_hash or not current_hash or cached_hash != current_hash:
                return False

            cached_nodes = payload.get("nodes")
            cached_edges = payload.get("edges")
            cached_entities = payload.get("entities")
            if not isinstance(cached_nodes, list) or not isinstance(cached_edges, list):
                return False
            if not isinstance(cached_entities, dict):
                return False

            rebuilt_graph = nx.DiGraph()
            for node in cached_nodes:
                if not isinstance(node, dict) or not isinstance(node.get("id"), str):
                    return False
                attributes = node.get("attributes", {})
                if not isinstance(attributes, dict):
                    return False
                rebuilt_graph.add_node(node["id"], **attributes)
            for edge in cached_edges:
                if not isinstance(edge, dict) or "source" not in edge or "target" not in edge:
                    return False
                if edge["source"] not in rebuilt_graph or edge["target"] not in rebuilt_graph:
                    return False
                attributes = edge.get("attributes", {})
                if not isinstance(attributes, dict):
                    return False
                rebuilt_graph.add_edge(edge["source"], edge["target"], **attributes)

            rebuilt_entities = {
                str(node_id): CodeEntity.model_validate(entity_data)
                for node_id, entity_data in cached_entities.items()
            }
            if any(node_id not in rebuilt_graph for node_id in rebuilt_entities):
                return False
            cached_modules = payload.get("modules", {})
            if not isinstance(cached_modules, dict):
                return False

            self.graph = rebuilt_graph
            self.entities = rebuilt_entities
            self.modules = {
                str(alias): str(path)
                for alias, path in cached_modules.items()
                if isinstance(alias, str)
                and isinstance(path, str)
                and path in self.graph
                and self.graph.nodes[path].get("type") == "module"
            }
            if not self.modules:
                for node_id, attributes in self.graph.nodes(data=True):
                    if attributes.get("type") == "module":
                        self._register_module_aliases(str(attributes.get("file_path", node_id)))
            self.repo_path = str(Path(repo_path).resolve())
            self.repo_hash = cached_hash
            self.is_built = True
            self._clear_algorithm_caches()
            return True
        except Exception:
            return False


_graph: CodeGraph | None = None


def get_graph() -> CodeGraph:
    """Return the process-wide code graph instance."""
    global _graph
    if _graph is None:
        _graph = CodeGraph()
    return _graph
