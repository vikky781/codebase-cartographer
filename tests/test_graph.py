from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest

from codebase_cartographer.config import get_config
from codebase_cartographer.graph import CodeGraph
from codebase_cartographer.models import CodeEntity


class TestGraphBuild:
    def test_graph_is_built(self, built_graph):
        assert built_graph.is_built is True

    def test_has_nodes(self, built_graph):
        assert built_graph.graph.number_of_nodes() > 0

    def test_has_edges(self, built_graph):
        assert built_graph.graph.number_of_edges() > 0

    def test_entity_counts(self, built_graph):
        counts = built_graph.get_entity_counts()
        assert counts.functions > 0
        assert counts.classes > 0
        assert counts.modules > 0


class TestPageRank:
    def test_returns_results(self, built_graph):
        results = built_graph.get_pagerank(top_n=5)
        assert len(results) > 0

    def test_results_are_ranked(self, built_graph):
        results = built_graph.get_pagerank(top_n=10)
        if len(results) > 1:
            assert results[0].score >= results[1].score

    def test_results_have_required_fields(self, built_graph):
        results = built_graph.get_pagerank(top_n=3)
        for r in results:
            assert r.entity_name
            assert r.file_path
            assert r.score >= 0
            assert r.rank > 0
            assert r.interpretation
            assert r.source == "networkx-pagerank"


class TestCentrality:
    def test_returns_results(self, built_graph):
        results = built_graph.get_centrality(top_n=5)
        assert isinstance(results, list)


class TestCycleDetection:
    def test_finds_circular_dependency(self, built_graph):
        """The fixture has a circular dep: models/user.py <-> utils/helpers.py"""
        cycles = built_graph.find_cycles()
        # There should be at least one cycle
        assert len(cycles) > 0, "Expected to find the circular dependency between models and utils"


class TestDeadCode:
    def test_finds_dead_functions(self, built_graph):
        """The fixture has unused_helper_function and another_unused_function."""
        dead = built_graph.find_dead_code()
        # At least some dead code should be found
        assert len(dead) > 0, "Expected to find dead code in the fixture"


class TestGodClasses:
    def test_detection_runs(self, built_graph):
        """God class detection should run without crashing."""
        result = built_graph.find_god_classes()
        assert isinstance(result, list)


class TestOrphanFiles:
    def test_finds_orphan_files(self, built_graph):
        """unused.py should be detected as an orphan."""
        orphans = built_graph.find_orphan_files()
        assert len(orphans) > 0, "Expected to find unused.py as an orphan"
        assert any("unused" in o for o in orphans)


class TestSearch:
    def test_search_by_name(self, built_graph):
        results = built_graph.search("auth")
        assert len(results) > 0

    def test_search_by_type(self, built_graph):
        results = built_graph.search("", entity_type="class")
        assert all(e.type == "class" for e in results)

    def test_search_no_results(self, built_graph):
        results = built_graph.search("nonexistent_xyz_123")
        assert len(results) == 0

    def test_search_treats_regular_expression_characters_as_literal_text(self, built_graph):
        """Tool queries are user input, so metacharacters must not change search semantics."""
        literal = CodeEntity(
            name="literal[needle]",
            type="function",
            file_path="special.py",
            line_start=1,
            line_end=1,
        )
        regex_only_match = CodeEntity(
            name="literaln",
            type="function",
            file_path="other.py",
            line_start=1,
            line_end=1,
        )
        for entity in (literal, regex_only_match):
            node_id = f"{entity.file_path}::{entity.name}"
            built_graph.entities[node_id] = entity
            built_graph.graph.add_node(
                node_id,
                type=entity.type,
                file_path=entity.file_path,
                name=entity.name,
            )

        results = built_graph.search("literal[needle]")

        assert [entity.name for entity in results] == ["literal[needle]"]

    def test_search_rejects_overlong_queries_without_regex_processing(self, built_graph):
        """Bounded literal search prevents pathological input from consuming unbounded resources."""
        query = "x" * (get_config().max_search_query_length + 1)

        with pytest.raises(ValueError, match="character limit"):
            built_graph.search(query)


class TestTrace:
    def test_trace_forward(self, built_graph):
        result = built_graph.trace("login", direction="forward")
        assert isinstance(result.steps, list)

    def test_trace_backward(self, built_graph):
        result = built_graph.trace("find_user", direction="backward")
        assert isinstance(result.steps, list)

    def test_trace_preserves_the_detected_entity_source(self):
        """Trace provenance must not claim AST evidence for fallback-parsed entities."""
        graph = CodeGraph()
        caller = CodeEntity(
            name="caller",
            type="function",
            file_path="legacy.java",
            line_start=1,
            line_end=1,
            source="regex-fallback",
        )
        callee = CodeEntity(
            name="callee",
            type="function",
            file_path="legacy.java",
            line_start=2,
            line_end=2,
            source="regex-fallback",
        )
        graph.entities = {
            "legacy.java::caller": caller,
            "legacy.java::callee": callee,
        }
        graph.graph.add_node("legacy.java::caller", type="function", name="caller")
        graph.graph.add_node("legacy.java::callee", type="function", name="callee")
        graph.graph.add_edge("legacy.java::caller", "legacy.java::callee", type="calls")

        result = graph.trace("caller")

        assert result.steps[0].source == "regex-fallback"


class TestHealthSummary:
    def test_health_summary(self, built_graph):
        health = built_graph.get_health_summary()
        assert health.circular_dependencies >= 0
        assert health.dead_functions >= 0
        assert health.avg_complexity >= 0


class TestDetectLayers:
    def test_detects_layers(self, built_graph):
        layers = built_graph.detect_layers()
        assert isinstance(layers, list)
        assert len(layers) > 0


class TestModuleProjection:
    def test_module_metrics_project_cross_file_calls(self):
        """A call across files must influence module-level structural metrics."""
        graph = CodeGraph()
        graph.graph.add_node("a.py", type="module", file_path="a.py", name="a")
        graph.graph.add_node("b.py", type="module", file_path="b.py", name="b")
        graph.graph.add_node("a.py::caller", type="function", file_path="a.py", name="caller")
        graph.graph.add_node("b.py::callee", type="function", file_path="b.py", name="callee")
        graph.graph.add_edge("a.py::caller", "b.py::callee", type="calls", count=2)

        module_graph = graph._module_graph()
        coupling = graph.get_coupling(top_n=1)

        assert module_graph.has_edge("a.py", "b.py")
        assert module_graph["a.py"]["b.py"]["call_count"] == 2
        assert module_graph["a.py"]["b.py"]["weight"] == 2
        assert coupling[0].entity_name == "a.py <-> b.py"
        assert coupling[0].score == 2


class TestGodClassMethodCoupling:
    def test_god_class_counts_relationships_to_its_methods(self):
        """Classes should not evade detection merely because calls target their methods."""
        graph = CodeGraph()
        god_class = CodeEntity(
            name="God",
            type="class",
            file_path="service.py",
            line_start=1,
            line_end=80,
        )
        graph.entities["service.py::God"] = god_class
        graph.graph.add_node("service.py::God", type="class", file_path="service.py", name="God")

        for index in range(6):
            method_entity = CodeEntity(
                name=f"God.method_{index}",
                type="function",
                file_path="service.py",
                line_start=index + 2,
                line_end=index + 2,
            )
            method = f"service.py::God.method_{index}"
            caller = f"client.py::caller_{index}"
            dependency = f"dependency.py::dependency_{index}"
            graph.entities[method] = method_entity
            graph.graph.add_node(
                method,
                type="function",
                file_path="service.py",
                name=f"God.method_{index}",
            )
            graph.graph.add_node(
                caller,
                type="function",
                file_path="client.py",
                name=f"caller_{index}",
            )
            graph.graph.add_edge(caller, method, type="calls")
            if index < 5:
                graph.graph.add_node(
                    dependency,
                    type="function",
                    file_path="dependency.py",
                    name=f"dependency_{index}",
                )
                graph.graph.add_edge(method, dependency, type="calls")

        detected = graph.find_god_classes()

        assert any(entity.name == "God" for entity, _, _ in detected)


class TestGraphCacheValidation:
    @pytest.fixture
    def writable_repo_dir(self):
        """Use the workspace instead of the sandbox-restricted system temp directory."""
        with tempfile.TemporaryDirectory(dir=Path.cwd(), prefix="cache-test-") as directory:
            yield Path(directory)

    @staticmethod
    def _write_cache(tmp_path, payload: dict) -> None:
        cache_path = get_config().get_cache_path(tmp_path)
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_text(json.dumps(payload), encoding="utf-8")

    @staticmethod
    def _minimal_payload(**overrides: object) -> dict:
        payload = {
            "schema_version": get_config().cache_schema_version,
            "repo_hash": "known-head",
            "nodes": [],
            "edges": [],
            "entities": {},
            "modules": {},
        }
        payload.update(overrides)
        return payload

    def test_cache_rejects_missing_schema_version(self, writable_repo_dir, monkeypatch):
        """A cache from an unknown graph schema must never be deserialized."""
        self._write_cache(
            writable_repo_dir,
            self._minimal_payload(),
        )
        cache_path = get_config().get_cache_path(writable_repo_dir)
        payload = json.loads(cache_path.read_text(encoding="utf-8"))
        payload.pop("schema_version")
        cache_path.write_text(json.dumps(payload), encoding="utf-8")
        monkeypatch.setattr(
            "codebase_cartographer.graph.GitAnalyzer.get_head_hash",
            lambda _: "known-head",
        )

        assert CodeGraph().load_cache(str(writable_repo_dir)) is False

    def test_cache_rejects_empty_hash_when_git_head_is_unavailable(
        self, writable_repo_dir, monkeypatch
    ):
        """An empty Git hash cannot prove freshness for non-Git repositories."""
        self._write_cache(writable_repo_dir, self._minimal_payload(repo_hash=""))
        monkeypatch.setattr(
            "codebase_cartographer.graph.GitAnalyzer.get_head_hash",
            lambda _: "",
        )

        assert CodeGraph().load_cache(str(writable_repo_dir)) is False
