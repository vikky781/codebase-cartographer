from pathlib import Path
from tempfile import TemporaryDirectory

import pytest

from codebase_cartographer.code_parser import parse_repository, scan_repository
from codebase_cartographer.config import get_config
from codebase_cartographer.graph import CodeGraph


@pytest.fixture
def local_tmp_path():
    """Create test files in the writable workspace, not the sandboxed system temp folder."""
    with TemporaryDirectory(dir=Path.cwd(), prefix="parser-test-") as directory:
        yield Path(directory)


class TestScanRepository:
    def test_finds_python_files(self, sample_repo_path):
        files = scan_repository(sample_repo_path)
        paths = [f["path"] for f in files]
        assert any("main.py" in p for p in paths)
        assert any("auth" in p and "service.py" in p for p in paths)
        assert any("models" in p and "user.py" in p for p in paths)

    def test_skips_pycache(self, sample_repo_path):
        files = scan_repository(sample_repo_path)
        paths = [f["path"] for f in files]
        assert not any("__pycache__" in p for p in paths)

    def test_returns_file_info(self, sample_repo_path):
        files = scan_repository(sample_repo_path)
        assert len(files) > 0
        for f in files:
            assert "path" in f
            assert "abs_path" in f
            assert "extension" in f

    def test_rejects_scope_that_escapes_the_repository(self, local_tmp_path):
        """Parser-level scanning must be safe even without the MCP server validator."""
        (local_tmp_path / "inside.py").write_text("pass\n", encoding="utf-8")

        with pytest.raises(ValueError, match="within the repository root"):
            scan_repository(local_tmp_path, "..")

    def test_stops_after_file_limit_instead_of_collecting_the_whole_tree(
        self, local_tmp_path, monkeypatch
    ):
        """Reject a huge repository as soon as the configured file limit is exceeded."""
        (local_tmp_path / "one.py").write_text("pass\n", encoding="utf-8")
        (local_tmp_path / "two.py").write_text("pass\n", encoding="utf-8")
        monkeypatch.setattr(get_config(), "max_files", 1)

        with pytest.raises(ValueError, match="more than 1 supported files"):
            scan_repository(local_tmp_path)


class TestParseRepository:
    def test_parses_without_crashing(self, sample_repo_path):
        files, warnings = parse_repository(sample_repo_path)
        assert len(files) > 0

    def test_finds_functions(self, parsed_files):
        all_entities = []
        for pf in parsed_files:
            all_entities.extend(pf.entities)
        func_names = [e.name for e in all_entities if e.type == "function"]
        assert "main" in func_names
        assert "login" in func_names
        assert "hash_password" in func_names
        assert "find_user" in func_names

    def test_finds_classes(self, parsed_files):
        all_entities = []
        for pf in parsed_files:
            all_entities.extend(pf.entities)
        class_names = [e.name for e in all_entities if e.type == "class"]
        assert "AuthService" in class_names
        assert "User" in class_names

    def test_finds_imports(self, parsed_files):
        all_imports = []
        for pf in parsed_files:
            all_imports.extend(pf.imports)
        # At least some imports should be found
        assert len(all_imports) > 0
        # Check that auth/routes.py imports from auth/service
        target_modules = [imp.target_module for imp in all_imports]
        assert any("auth" in t and "service" in t for t in target_modules) or any(
            "auth.service" in t for t in target_modules
        )

    def test_entities_have_line_numbers(self, parsed_files):
        for pf in parsed_files:
            for entity in pf.entities:
                assert entity.line_start > 0
                assert entity.line_end >= entity.line_start

    def test_creates_module_entities(self, parsed_files):
        module_entities = []
        for pf in parsed_files:
            module_entities.extend([e for e in pf.entities if e.type == "module"])
        # Each parsed file should have a module entity
        assert len(module_entities) >= len(parsed_files)

    def test_nested_function_calls_are_not_attributed_to_the_outer_function(self, local_tmp_path):
        """Nested declarations must own their own calls for a truthful call graph."""
        (local_tmp_path / "nested.py").write_text(
            "def outer():\n    nested()\n    def nested():\n        child()\n",
            encoding="utf-8",
        )

        parsed_files, _ = parse_repository(local_tmp_path)
        functions = {
            entity.name: entity for entity in parsed_files[0].entities if entity.type == "function"
        }

        assert functions["outer"].calls == ["nested"]
        assert functions["outer.nested"].calls == ["child"]

    def test_nested_same_named_functions_keep_distinct_lexical_graph_nodes(self, local_tmp_path):
        """Functions with the same terminal name must not collapse into one graph node."""
        (local_tmp_path / "nested.py").write_text(
            "def first():\n"
            "    def helper():\n"
            "        return 1\n"
            "    return helper()\n\n"
            "def second():\n"
            "    def helper():\n"
            "        return 2\n"
            "    return helper()\n",
            encoding="utf-8",
        )

        parsed_files, _ = parse_repository(local_tmp_path)
        graph = CodeGraph()
        graph.build(parsed_files, str(local_tmp_path), repo_hash="")

        function_names = {
            entity.name
            for entity in parsed_files[0].entities
            if entity.type == "function"
        }
        assert {"first", "first.helper", "second", "second.helper"} <= function_names
        assert graph.get_entity_counts().functions == 4
        assert graph.graph.has_edge("nested.py::first", "nested.py::first.helper")
        assert graph.graph.has_edge("nested.py::second", "nested.py::second.helper")

    def test_python_import_aliases_preserve_exported_and_local_names(self, local_tmp_path):
        """Alias data is necessary for conservative graph resolution later in the pipeline."""
        (local_tmp_path / "aliases.py").write_text(
            "from library import Foo as LocalFoo\nimport package.child as child_alias\n",
            encoding="utf-8",
        )

        parsed_files, _ = parse_repository(local_tmp_path)
        imports = parsed_files[0].imports

        assert imports[0].imported_names == ["Foo"]
        assert imports[0].local_names == ["LocalFoo"]
        assert imports[0].is_from_import is True
        assert imports[1].imported_names == ["package.child"]
        assert imports[1].local_names == ["child_alias"]
        assert imports[1].is_from_import is False

    def test_multiple_python_imports_create_individual_dependency_edges(self, local_tmp_path):
        """``import a, b`` must not silently omit the second module dependency."""
        (local_tmp_path / "imports.py").write_text("import alpha, beta\n", encoding="utf-8")

        parsed_files, _ = parse_repository(local_tmp_path)

        assert {edge.target_module for edge in parsed_files[0].imports} == {"alpha", "beta"}

    def test_python_signature_preserves_type_annotations(self, local_tmp_path):
        """A signature is evidence for a change plan and must not stop at an annotation colon."""
        (local_tmp_path / "typed.py").write_text(
            "def login(username: str, password: str) -> Token:\n    return Token()\n",
            encoding="utf-8",
        )

        parsed_files, _ = parse_repository(local_tmp_path)
        login = next(
            entity
            for entity in parsed_files[0].entities
            if entity.type == "function" and entity.name == "login"
        )

        assert login.signature == "def login(username: str, password: str) -> Token"

    @pytest.mark.parametrize(
        ("filename", "source", "expected_entities"),
        [
            (
                "module.js",
                'import { format } from "./format";\n'
                "export function greet(name) { return format(name); }\n"
                "const trim = (value) => value.trim();\n",
                {"greet", "trim"},
            ),
            (
                "module.ts",
                'import { format } from "./format";\n'
                "export function greet(name: string): string { return format(name); }\n"
                "const trim = (value: string) => value.trim();\n",
                {"greet", "trim"},
            ),
            (
                "component.tsx",
                'import React from "react";\n'
                "type Props = { name: string };\n"
                "export const Greeting = ({ name }: Props) => <div>{name.toUpperCase()}</div>;\n",
                {"Greeting"},
            ),
        ],
    )
    def test_parses_javascript_family_files_with_tree_sitter(
        self, local_tmp_path, filename, source, expected_entities
    ):
        """Supported JavaScript-family extensions must use their correct grammar."""
        (local_tmp_path / filename).write_text(source, encoding="utf-8")

        parsed_files, warnings = parse_repository(local_tmp_path)

        assert warnings == []
        assert len(parsed_files) == 1
        parsed_file = parsed_files[0]
        assert parsed_file.parse_method == "tree-sitter"
        assert parsed_file.language in {"javascript", "typescript"}
        assert expected_entities <= {
            entity.name for entity in parsed_file.entities if entity.type == "function"
        }

    def test_reports_tree_sitter_syntax_recovery_without_skipping_the_file(self, local_tmp_path):
        """Malformed source must produce a clear coverage warning instead of a silent graph."""
        (local_tmp_path / "broken.py").write_text("def incomplete(:\n", encoding="utf-8")

        parsed_files, warnings = parse_repository(local_tmp_path)

        assert len(parsed_files) == 1
        assert parsed_files[0].syntax_recovered is True
        assert any("recovered from syntax errors" in warning for warning in warnings)

    @pytest.mark.parametrize(
        ("filename", "source", "expected_functions"),
        [
            (
                "Auth.java",
                "public class Auth {\n"
                "    public void authenticate(String user) {}\n"
                "    if (enabled) {}\n"
                "}\n",
                {"authenticate"},
            ),
            (
                "auth.cpp",
                "void login(std::string user) {}\n"
                "if (enabled) {}\n",
                {"login"},
            ),
        ],
    )
    def test_regex_fallback_ignores_control_flow_and_keeps_c_style_declarations(
        self, local_tmp_path, filename, source, expected_functions
    ):
        """Fallback parsing must remain conservative rather than inventing declarations."""
        (local_tmp_path / filename).write_text(source, encoding="utf-8")

        parsed_files, _ = parse_repository(local_tmp_path)

        parsed_file = parsed_files[0]
        assert parsed_file.parse_method == "regex-fallback"
        function_names = {
            entity.name for entity in parsed_file.entities if entity.type == "function"
        }
        assert expected_functions <= function_names
        assert "if" not in function_names
