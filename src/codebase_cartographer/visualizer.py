from __future__ import annotations

import re
from collections.abc import Mapping

from .config import get_config
from .graph import CodeGraph
from .models import VisualizeInput

_MERMAID_RESERVED_IDS = frozenset(
    {
        "class",
        "classdef",
        "click",
        "direction",
        "end",
        "flowchart",
        "graph",
        "linkstyle",
        "style",
        "subgraph",
    }
)

_LAYER_DIRECTORY_NAMES = {
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


class MermaidVisualizer:
    """Generate compact, valid Mermaid diagrams from a code knowledge graph."""

    def __init__(
        self, graph: CodeGraph, change_frequencies: Mapping[str, int] | None = None
    ) -> None:
        """Initialize the visualizer for a built code graph."""
        self.graph = graph
        self.change_frequencies = {
            str(file_path).replace("\\", "/"): max(0, int(frequency))
            for file_path, frequency in (change_frequencies or {}).items()
        }

    def generate(self, input: VisualizeInput) -> str:
        """Route a visualization request to its diagram generator."""
        try:
            max_nodes = min(max(1, input.max_nodes), 30)
            generators = {
                "architecture": lambda: self._architecture_diagram(input.scope, max_nodes),
                "call_flow": lambda: self._call_flow_diagram(input.scope, max_nodes),
                "dependencies": lambda: self._dependencies_diagram(input.scope, max_nodes),
                "layers": lambda: self._layers_diagram(max_nodes),
                "hotspot_map": lambda: self._hotspot_diagram(max_nodes),
            }
            generator = generators.get(input.diagram_type)
            if generator is None:
                return self._error_diagram(f"Unknown diagram type: {input.diagram_type}")
            return generator()
        except Exception as exc:
            return self._error_diagram(f"Error generating diagram: {exc}")

    def _sanitize_id(self, name: str) -> str:
        """Convert a path or entity name into an alphanumeric Mermaid node id."""
        sanitized = re.sub(r"[^a-zA-Z0-9_]", "_", name).strip("_")
        if not sanitized:
            return "unknown"
        if sanitized[0].isdigit() or sanitized.casefold() in _MERMAID_RESERVED_IDS:
            return f"node_{sanitized}"
        return sanitized

    @staticmethod
    def _truncate_label(label: str, max_len: int = 30) -> str:
        """Shorten a Mermaid label while keeping it readable."""
        if len(label) <= max_len:
            return label
        return label[: max_len - 3] + "..."

    def _get_node_shape(self, node_type: str, node_id: str, label: str) -> str:
        """Return a valid Mermaid node definition for a graph node type."""
        safe_label = self._escape_label(self._truncate_label(label))
        if node_type == "class":
            return f'{node_id}(["{safe_label}"])'
        if node_type == "function":
            return f'{node_id}("{safe_label}")'
        return f'{node_id}["{safe_label}"]'

    def _select_top_nodes(self, node_ids: list[str], max_nodes: int) -> list[str]:
        """Select important nodes by PageRank, falling back to graph degree."""
        unique_node_ids = list(dict.fromkeys(node_ids))
        limit = max(0, max_nodes)
        if len(unique_node_ids) <= limit:
            return unique_node_ids
        if limit == 0:
            return []

        scores = getattr(self.graph, "_pagerank_cache", None)
        if scores is None:
            try:
                self.graph.get_pagerank(top_n=max(len(unique_node_ids), limit))
                scores = getattr(self.graph, "_pagerank_cache", None)
            except Exception:
                scores = None

        def importance(node_id: str) -> tuple[float, int, str]:
            score = scores.get(node_id, 0.0) if scores else 0.0
            degree = self.graph.graph.in_degree(node_id) + self.graph.graph.out_degree(node_id)
            return (-score, -degree, node_id)

        return sorted(unique_node_ids, key=importance)[:limit]

    def _architecture_diagram(self, scope: str | None, max_nodes: int) -> str:
        """Generate a community-grouped module dependency overview."""
        module_nodes = self._module_nodes()
        if scope:
            normalized_scope = scope.casefold()
            module_nodes = [
                node_id
                for node_id in module_nodes
                if normalized_scope
                in str(self.graph.graph.nodes[node_id].get("file_path", node_id)).casefold()
            ]
        selected = self._select_top_nodes(module_nodes, max_nodes)
        if not selected:
            return self._error_diagram("No matching modules found")

        node_ids = self._unique_node_ids(selected)
        communities = self.graph.get_communities()
        grouped: dict[int, list[str]] = {}
        for node_id in selected:
            grouped.setdefault(communities.get(node_id, -1), []).append(node_id)

        community_ids = sorted(grouped)
        layer_by_community = self._community_layer_labels(
            community_ids, communities, fallback_prefix="Group"
        )

        lines = ["flowchart TD"]
        for index, community_id in enumerate(community_ids):
            subgraph_id = f"Community_{index}"
            lines.append(
                f'subgraph {subgraph_id}["{self._escape_label(layer_by_community[community_id])}"]'
            )
            for node_id in sorted(grouped[community_id]):
                lines.append(f"    {self._node_definition(node_id, node_ids[node_id])}")
            lines.append("end")

        lines.extend(self._edge_lines(set(selected), node_ids))
        return self._build_mermaid_string(lines)

    def _call_flow_diagram(self, scope: str | None, max_nodes: int) -> str:
        """Generate a left-to-right breadth-first function call flow."""
        if not scope:
            return self._error_diagram(
                "scope parameter required for call_flow diagram. Provide a function or module name."
            )

        start = self._find_node(scope, module_only=False, prefer_non_module=True)
        if start is None:
            return self._error_diagram(f"No entity found for scope: {scope}")

        selected: list[str] = []
        queue = [start]
        visited = {start}
        limit = max(1, max_nodes)
        index = 0
        while index < len(queue) and len(selected) < limit:
            node_id = queue[index]
            index += 1
            selected.append(node_id)
            for _, target, attributes in self.graph.graph.out_edges(node_id, data=True):
                if attributes.get("type") != "calls" or target in visited:
                    continue
                visited.add(target)
                queue.append(target)

        node_ids = self._unique_node_ids(selected)
        lines = ["flowchart LR"]
        lines.extend(self._node_definition(node_id, node_ids[node_id]) for node_id in selected)
        for source, target, attributes in self.graph.graph.edges(data=True):
            if source in node_ids and target in node_ids and attributes.get("type") == "calls":
                lines.append(f"{node_ids[source]} -->|calls| {node_ids[target]}")
        lines.append(f"style {node_ids[start]} fill:#f96,stroke:#333,stroke-width:3px")
        return self._build_mermaid_string(lines)

    def _dependencies_diagram(self, scope: str | None, max_nodes: int) -> str:
        """Generate a focused import neighborhood around one module."""
        if not scope:
            return self._error_diagram(
                "scope parameter required for dependencies diagram. Provide a module name."
            )

        center = self._find_node(scope, module_only=True)
        if center is None:
            return self._error_diagram(f"No module found for scope: {scope}")

        importers = [
            source
            for source, _, attributes in self.graph.graph.in_edges(center, data=True)
            if attributes.get("type") == "imports"
        ]
        dependencies = [
            target
            for _, target, attributes in self.graph.graph.out_edges(center, data=True)
            if attributes.get("type") == "imports"
        ]
        neighbors = [
            node_id for node_id in dict.fromkeys([*importers, *dependencies]) if node_id != center
        ]
        selected = [center, *self._select_top_nodes(neighbors, max(0, max_nodes - 1))]
        node_ids = self._unique_node_ids(selected)

        lines = ["flowchart LR"]
        lines.extend(self._node_definition(node_id, node_ids[node_id]) for node_id in selected)
        for source, target, attributes in self.graph.graph.edges(data=True):
            if (
                source in node_ids
                and target in node_ids
                and attributes.get("type") == "imports"
                and (source == center or target == center)
            ):
                lines.append(f"{node_ids[source]} -->|imports| {node_ids[target]}")
        lines.append(f"style {node_ids[center]} fill:#6f9,stroke:#333,stroke-width:3px")
        return self._build_mermaid_string(lines)

    def _layers_diagram(self, max_nodes: int) -> str:
        """Generate a layered architecture view with inter-layer import edges."""
        module_nodes = self._module_nodes()
        if not module_nodes:
            return self._error_diagram("No modules available for layer detection")

        communities = self.graph.get_communities()
        grouped: dict[int, list[str]] = {}
        for node_id in module_nodes:
            grouped.setdefault(communities.get(node_id, -1), []).append(node_id)

        limit = max(1, max_nodes)
        group_ids = sorted(grouped)
        if len(group_ids) > limit:
            group_ids = sorted(
                group_ids,
                key=lambda group_id: len(grouped[group_id]),
                reverse=True,
            )[:limit]

        selected: list[str] = []
        total_nodes = sum(len(grouped[group_id]) for group_id in group_ids)
        remaining = limit
        for index, group_id in enumerate(group_ids):
            groups_left = len(group_ids) - index - 1
            proportional = round(limit * len(grouped[group_id]) / max(1, total_nodes))
            allocation = min(len(grouped[group_id]), max(1, proportional))
            allocation = min(allocation, max(1, remaining - groups_left))
            chosen = self._select_top_nodes(grouped[group_id], allocation)
            selected.extend(chosen)
            remaining -= len(chosen)
        if remaining > 0:
            extras = [node_id for node_id in module_nodes if node_id not in selected]
            selected.extend(self._select_top_nodes(extras, remaining))
        selected = list(dict.fromkeys(selected))[:limit]

        node_ids = self._unique_node_ids(selected)
        selected_groups: dict[int, list[str]] = {}
        for node_id in selected:
            selected_groups.setdefault(communities.get(node_id, -1), []).append(node_id)

        layer_groups = sorted(selected_groups)
        layer_names = self._community_layer_labels(
            layer_groups, communities, fallback_prefix="Layer"
        )

        lines = ["flowchart TD"]
        for index, group_id in enumerate(layer_groups):
            subgraph_id = f"Layer_{index}"
            layer_name = layer_names[group_id]
            lines.append(f'subgraph {subgraph_id}["{self._escape_label(layer_name)}"]')
            for node_id in sorted(selected_groups[group_id]):
                lines.append(f"    {self._node_definition(node_id, node_ids[node_id])}")
            lines.append("end")
            lines.append(f"style {subgraph_id} fill:{self._layer_color(layer_name)}")

        for source, target, attributes in self.graph.graph.edges(data=True):
            if (
                source in node_ids
                and target in node_ids
                and attributes.get("type") == "imports"
                and communities.get(source, -1) != communities.get(target, -1)
            ):
                lines.append(f"{node_ids[source]} -->|imports| {node_ids[target]}")
        return self._build_mermaid_string(lines)

    def _hotspot_diagram(self, max_nodes: int) -> str:
        """Generate a module map colored by local Git change frequency."""
        module_nodes = self._module_nodes()
        if not module_nodes:
            return self._error_diagram("No modules available for hotspot analysis")
        if not self.change_frequencies:
            return self._error_diagram(
                "No local Git change-frequency data is available for a hotspot map"
            )

        pagerank = getattr(self.graph, "_pagerank_cache", None)
        if pagerank is None:
            self._select_top_nodes(module_nodes, max_nodes)
            pagerank = getattr(self.graph, "_pagerank_cache", None) or {}
        selected = sorted(
            module_nodes,
            key=lambda node_id: (
                -self.change_frequencies.get(
                    str(self.graph.graph.nodes[node_id].get("file_path", node_id)), 0.0
                ),
                -pagerank.get(node_id, 0.0),
                node_id,
            ),
        )[: max(1, max_nodes)]
        node_ids = self._unique_node_ids(selected)

        lines = [
            "flowchart TD",
            (
                "%% Colors show local Git touch frequency in the most recent "
                f"{get_config().max_git_history_commits} commits (red=more frequent)."
            ),
        ]
        lines.extend(self._node_definition(node_id, node_ids[node_id]) for node_id in selected)
        for source, target, attributes in self.graph.graph.edges(data=True):
            if source in node_ids and target in node_ids and attributes.get("type") == "imports":
                lines.append(f"{node_ids[source]} -->|imports| {node_ids[target]}")

        total = len(selected)
        for index, node_id in enumerate(
            sorted(
                selected,
                key=lambda item: (
                    -self.change_frequencies.get(
                        str(self.graph.graph.nodes[item].get("file_path", item)), 0.0
                    )
                ),
            )
        ):
            percentile = (index + 1) / total
            if percentile <= 0.2:
                color = "#ff5252"
            elif percentile <= 0.5:
                color = "#ffab40"
            elif percentile <= 0.8:
                color = "#fff176"
            else:
                color = "#69f0ae"
            lines.append(f"style {node_ids[node_id]} fill:{color}")
        return self._build_mermaid_string(lines)

    def _module_nodes(self) -> list[str]:
        """Return the graph ids for all module nodes."""
        return [
            node_id
            for node_id, attributes in self.graph.graph.nodes(data=True)
            if attributes.get("type") == "module"
        ]

    def _find_node(
        self, scope: str, module_only: bool, prefer_non_module: bool = False
    ) -> str | None:
        """Find a graph node by id, name, path, or case-insensitive partial match."""
        if scope in self.graph.graph and (
            not module_only or self.graph.graph.nodes[scope].get("type") == "module"
        ):
            return scope

        normalized_scope = scope.casefold()
        candidates = [
            node_id
            for node_id, attributes in self.graph.graph.nodes(data=True)
            if (not module_only or attributes.get("type") == "module")
            and (
                str(attributes.get("name", "")).casefold() == normalized_scope
                or str(attributes.get("file_path", "")).casefold() == normalized_scope
            )
        ]
        if candidates:
            if prefer_non_module:
                non_module_candidates = [
                    node_id
                    for node_id in candidates
                    if self.graph.graph.nodes[node_id].get("type") != "module"
                ]
                if non_module_candidates:
                    return sorted(non_module_candidates)[0]
            return sorted(candidates)[0]

        partial_matches = [
            node_id
            for node_id, attributes in self.graph.graph.nodes(data=True)
            if (not module_only or attributes.get("type") == "module")
            and (
                normalized_scope in str(attributes.get("name", "")).casefold()
                or normalized_scope in str(attributes.get("file_path", "")).casefold()
            )
        ]
        if prefer_non_module:
            non_module_matches = [
                node_id
                for node_id in partial_matches
                if self.graph.graph.nodes[node_id].get("type") != "module"
            ]
            if non_module_matches:
                return sorted(non_module_matches)[0]
        return sorted(partial_matches)[0] if partial_matches else None

    def _unique_node_ids(self, node_ids: list[str]) -> dict[str, str]:
        """Map graph ids to unique Mermaid-safe identifiers."""
        mapped_ids: dict[str, str] = {}
        used_ids: set[str] = set()
        for original_id in node_ids:
            base = self._sanitize_id(original_id)
            candidate = base
            suffix = 2
            while candidate in used_ids:
                candidate = f"{base}_{suffix}"
                suffix += 1
            used_ids.add(candidate)
            mapped_ids[original_id] = candidate
        return mapped_ids

    def _node_definition(self, graph_node_id: str, mermaid_node_id: str) -> str:
        """Create a Mermaid node definition from graph attributes."""
        attributes = self.graph.graph.nodes[graph_node_id]
        node_type = str(attributes.get("type", "module"))
        label = str(attributes.get("name", graph_node_id))
        return self._get_node_shape(node_type, mermaid_node_id, label)

    def _edge_lines(self, selected: set[str], node_ids: dict[str, str]) -> list[str]:
        """Generate Mermaid edge lines for relationships inside a selected node set."""
        lines: list[str] = []
        for source, target, attributes in self.graph.graph.edges(data=True):
            if source not in selected or target not in selected:
                continue
            if attributes.get("type") == "calls":
                lines.append(f"{node_ids[source]} -.->|calls| {node_ids[target]}")
            elif attributes.get("type") == "imports":
                lines.append(f"{node_ids[source]} -->|imports| {node_ids[target]}")
        return lines

    @staticmethod
    def _escape_label(label: str) -> str:
        """Escape text that would otherwise terminate a quoted Mermaid label."""
        normalized = re.sub(r"[\x00-\x1f\x7f]+", " ", str(label))
        return (
            normalized.replace("&", "&amp;")
            .replace("\\", "&#92;")
            .replace('"', "&quot;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
        )

    def _community_layer_labels(
        self,
        community_ids: list[int],
        communities: dict[str, int],
        fallback_prefix: str,
    ) -> dict[int, str]:
        """Return an independently inferred label for each displayed community."""
        members_by_community: dict[int, list[str]] = {}
        for node_id in self._module_nodes():
            members_by_community.setdefault(communities.get(node_id, -1), []).append(node_id)

        return {
            community_id: self._layer_name_for_members(
                members_by_community.get(community_id, []),
                fallback=f"{fallback_prefix} {index + 1}",
            )
            for index, community_id in enumerate(community_ids)
        }

    def _layer_name_for_members(self, members: list[str], fallback: str) -> str:
        """Infer a layer name from the common directory prefix of a community."""
        directory_parts: list[list[str]] = []
        for node_id in members:
            file_path = str(self.graph.graph.nodes[node_id].get("file_path", node_id))
            parts = [part for part in file_path.replace("\\", "/").split("/")[:-1] if part]
            if parts:
                directory_parts.append(parts)

        if not directory_parts:
            return fallback

        common_parts = directory_parts[0]
        for parts in directory_parts[1:]:
            prefix: list[str] = []
            for part, other in zip(common_parts, parts):
                if part != other:
                    break
                prefix.append(part)
            common_parts = prefix
            if not common_parts:
                return fallback

        for part in reversed(common_parts):
            layer_name = _LAYER_DIRECTORY_NAMES.get(part.casefold())
            if layer_name is not None:
                return layer_name

        directory_name = common_parts[-1].replace("_", " ").replace("-", " ").title()
        if directory_name.casefold() in {"src", "app", "lib"}:
            return fallback
        return directory_name

    @staticmethod
    def _layer_color(layer_name: str) -> str:
        """Return the requested fill color for a named architectural layer."""
        if "API" in layer_name:
            return "#e1f5fe"
        if "Service" in layer_name:
            return "#fff3e0"
        if "Data" in layer_name:
            return "#e8f5e9"
        if "Utility" in layer_name:
            return "#f3e5f5"
        if "Test" in layer_name:
            return "#fce4ec"
        return "#f5f5f5"

    def _error_diagram(self, message: str) -> str:
        """Return a valid single-node Mermaid error diagram."""
        label = self._truncate_label(f"Error: {message}")
        comment = re.sub(r"[\r\n]+", " ", message).replace("`", "")
        return self._build_mermaid_string(
            ["flowchart TD", self._get_node_shape("module", "error_node", label), f"%% {comment}"]
        )

    @staticmethod
    def _build_mermaid_string(lines: list[str]) -> str:
        """Wrap Mermaid source lines in a complete fenced code block."""
        content = "\n".join(lines)
        return f"```mermaid\n{content}\n```"
