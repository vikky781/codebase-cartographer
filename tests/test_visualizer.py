import re

import pytest

from codebase_cartographer.graph import CodeGraph
from codebase_cartographer.models import VisualizeInput
from codebase_cartographer.visualizer import MermaidVisualizer


class TestMermaidVisualizer:
    @pytest.fixture
    def visualizer(self, built_graph):
        return MermaidVisualizer(built_graph)

    def test_architecture_diagram(self, visualizer):
        inp = VisualizeInput(diagram_type="architecture")
        result = visualizer.generate(inp)
        assert result.startswith("```mermaid\nflowchart TD\n")
        assert result.endswith("\n```")

    def test_layers_diagram(self, visualizer):
        inp = VisualizeInput(diagram_type="layers")
        result = visualizer.generate(inp)
        assert "mermaid" in result

    def test_call_flow_needs_scope(self, visualizer):
        inp = VisualizeInput(diagram_type="call_flow", scope=None)
        result = visualizer.generate(inp)
        # Should return an error message or empty diagram
        assert isinstance(result, str)

    def test_call_flow_with_scope(self, visualizer):
        inp = VisualizeInput(diagram_type="call_flow", scope="login")
        result = visualizer.generate(inp)
        assert "mermaid" in result
        assert "-->|calls|" in result
        assert "style " in result

    def test_max_nodes_respected(self, visualizer):
        graph = CodeGraph()
        for index in range(12):
            file_path = f"src/features/feature-{index}.py"
            graph.graph.add_node(
                file_path,
                type="module",
                file_path=file_path,
                name=f"feature-{index}",
            )

        result = MermaidVisualizer(graph).generate(
            VisualizeInput(diagram_type="architecture", max_nodes=5)
        )
        node_ids = re.findall(r'^\s*([A-Za-z][A-Za-z0-9_]*)\["', result, flags=re.MULTILINE)
        assert 1 <= len(node_ids) <= 5
        assert len(node_ids) == len(set(node_ids))

    def test_dependencies_diagram(self, visualizer):
        inp = VisualizeInput(diagram_type="dependencies", scope="service")
        result = visualizer.generate(inp)
        assert isinstance(result, str)

    def test_hotspot_diagram(self, visualizer):
        inp = VisualizeInput(diagram_type="hotspot_map")
        result = visualizer.generate(inp)
        assert "mermaid" in result
        assert "static line span" in result.lower()
        assert "change-frequency data is unavailable" in result.lower()

    def test_ids_are_safe_unique_and_not_mermaid_keywords(self, visualizer):
        mapped = visualizer._unique_node_ids(
            ["end", "foo.bar", "foo/bar", "foo_bar_2", "123-start", "!!!"]
        )
        identifiers = list(mapped.values())

        assert mapped["end"] == "node_end"
        assert len(identifiers) == len(set(identifiers))
        assert all(re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*", node_id) for node_id in identifiers)
        assert "end" not in identifiers

    def test_labels_escape_control_characters_and_mermaid_delimiters(self, visualizer):
        node = visualizer._get_node_shape("module", "safe_id", '"quoted"\n<markup>&path\\segment')

        assert node.startswith('safe_id["')
        assert node.endswith('"]')
        assert "\n" not in node
        assert "&quot;quoted&quot;" in node
        assert "&lt;markup&gt;&amp;path&#92;segment" in node

    def test_community_labels_are_inferred_per_community(self, monkeypatch):
        graph = CodeGraph()
        route_file = "src/routes/users.py"
        service_file = "src/services/accounts.py"
        for file_path in (route_file, service_file):
            graph.graph.add_node(
                file_path,
                type="module",
                file_path=file_path,
                name=file_path.rsplit("/", 1)[-1].removesuffix(".py"),
            )

        monkeypatch.setattr(graph, "get_communities", lambda: {route_file: 9, service_file: 2})
        monkeypatch.setattr(graph, "detect_layers", lambda: ["Incorrect First", "Incorrect Second"])

        result = MermaidVisualizer(graph).generate(VisualizeInput(diagram_type="architecture"))

        assert 'subgraph Community_0["Service Layer"]' in result
        assert 'subgraph Community_1["API Layer"]' in result
        assert "Incorrect First" not in result

    def test_dependencies_cap_nodes_and_do_not_duplicate_the_center(self):
        graph = CodeGraph()
        center = "src/core/service.py"
        graph.graph.add_node(center, type="module", file_path=center, name="service")
        graph.graph.add_edge(center, center, type="imports")
        for index in range(10):
            file_path = f"src/dependencies/dependency-{index}.py"
            graph.graph.add_node(
                file_path,
                type="module",
                file_path=file_path,
                name=f"dependency-{index}",
            )
            graph.graph.add_edge(center, file_path, type="imports")

        result = MermaidVisualizer(graph).generate(
            VisualizeInput(diagram_type="dependencies", scope="service", max_nodes=5)
        )
        node_ids = re.findall(r'^\s*([A-Za-z][A-Za-z0-9_]*)\["', result, flags=re.MULTILINE)

        assert len(node_ids) <= 5
        assert len(node_ids) == len(set(node_ids))
        assert sum("service" in line for line in result.splitlines() if '["service"]' in line) == 1
