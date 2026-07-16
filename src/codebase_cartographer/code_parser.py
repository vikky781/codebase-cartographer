from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass
from pathlib import Path

import tree_sitter_javascript as tsjavascript
import tree_sitter_python as tspython
import tree_sitter_typescript as tstypescript
from tree_sitter import Language, Node, Parser

from .config import (
    IGNORE_DIRS,
    IGNORE_FILES,
    NON_CODE_EXTENSIONS,
    REGEX_FALLBACK_EXTENSIONS,
    TREE_SITTER_LANGUAGES,
    get_config,
)
from .models import CodeEntity

logger = logging.getLogger(__name__)


@dataclass
class ParsedFile:
    """Store structured parsing results for one source file."""

    file_path: str
    language: str
    entities: list[CodeEntity]
    imports: list[ImportEdge]
    calls: list[CallEdge]
    parse_method: str
    error: str | None = None
    syntax_recovered: bool = False


@dataclass
class ImportEdge:
    """Represent an import relationship between modules."""

    source_module: str
    target_module: str
    imported_names: list[str]


@dataclass
class CallEdge:
    """Represent a function call discovered in a source file."""

    caller: str
    callee: str
    line: int


def _node_text(node: Node | None) -> str:
    """Decode a Tree-sitter node's source text without propagating errors."""
    if node is None:
        return ""
    try:
        return node.text.decode("utf-8")
    except (AttributeError, UnicodeDecodeError):
        return ""


def _walk_nodes(node: Node) -> list[Node]:
    """Return a depth-first list without risking Python recursion exhaustion."""
    nodes: list[Node] = []
    pending = [node]
    while pending:
        current = pending.pop()
        nodes.append(current)
        pending.extend(reversed(current.children))
    return nodes


def _first_named_child(node: Node, types: set[str]) -> Node | None:
    """Find the first direct child whose type is in ``types``."""
    return next((child for child in node.children if child.type in types), None)


def _entity_name(node: Node) -> str:
    """Extract an entity identifier from a declaration node."""
    named = node.child_by_field_name("name")
    if named is not None:
        return _node_text(named)

    identifier = _first_named_child(node, {"identifier", "property_identifier", "type_identifier"})
    return _node_text(identifier)


def _callee_name(call_node: Node) -> str:
    """Return the final identifier in a call expression."""
    callable_node = call_node.child_by_field_name("function")
    if callable_node is None and call_node.children:
        callable_node = call_node.children[0]
    if callable_node is None:
        return ""

    identifiers = [
        descendant
        for descendant in _walk_nodes(callable_node)
        if descendant.type in {"identifier", "property_identifier", "field_identifier"}
    ]
    return _node_text(identifiers[-1]) if identifiers else _node_text(callable_node)


def _source_module(file_path: str) -> str:
    """Return a repository-relative module name without its file extension."""
    return str(Path(file_path).with_suffix("")).replace("\\", "/")


def _line_complexity(node: Node) -> int:
    """Calculate the simple line-span complexity measure for an AST node."""
    return node.end_point[0] - node.start_point[0] + 1


class TreeSitterManager:
    """Create and cache Tree-sitter parsers for supported languages."""

    def __init__(self) -> None:
        self._parsers: dict[str, Parser] = {}
        self._languages: dict[str, Language] = {}

    def get_parser(self, language: str) -> Parser | None:
        """Return a cached parser, creating it lazily when possible."""
        # ``tsx`` is a TypeScript dialect selected from the ``.tsx`` extension.
        # It intentionally shares the public language label ``typescript``.
        if language not in TREE_SITTER_LANGUAGES and language != "tsx":
            return None
        if language in self._parsers:
            return self._parsers[language]

        try:
            if language not in self._languages:
                if language == "python":
                    self._languages[language] = Language(tspython.language())
                elif language == "javascript":
                    self._languages[language] = Language(tsjavascript.language())
                elif language == "typescript":
                    self._languages[language] = Language(tstypescript.language_typescript())
                elif language == "tsx":
                    self._languages[language] = Language(tstypescript.language_tsx())
                else:
                    return None

            parser = Parser()
            parser.language = self._languages[language]
            self._parsers[language] = parser
            return parser
        except Exception:
            logger.debug("Unable to initialize Tree-sitter parser for %s", language, exc_info=True)
            return None

    def parse_file(self, source_bytes: bytes, language: str) -> Node | None:
        """Parse source bytes and return the root node, if parsing succeeds."""
        try:
            parser = self.get_parser(language)
            if parser is None:
                return None
            return parser.parse(source_bytes).root_node
        except Exception:
            logger.debug("Tree-sitter parsing failed for %s", language, exc_info=True)
            return None


class PythonExtractor:
    """Extract Python entities, imports, and calls from a Tree-sitter AST."""

    def extract(
        self, root_node: Node, file_path: str, source_bytes: bytes
    ) -> tuple[list[CodeEntity], list[ImportEdge], list[CallEdge]]:
        """Extract structured code graph data from a Python syntax tree."""
        del source_bytes  # Node text contains the source slices needed by this extractor.
        entities: list[CodeEntity] = []
        imports: list[ImportEdge] = []
        calls: list[CallEdge] = []
        source_module = _source_module(file_path)
        all_nodes = _walk_nodes(root_node)

        for node in all_nodes:
            if node.type == "class_definition":
                class_name = _entity_name(node)
                if class_name:
                    entities.append(
                        CodeEntity(
                            name=class_name,
                            type="class",
                            file_path=file_path,
                            line_start=node.start_point[0] + 1,
                            line_end=node.end_point[0] + 1,
                            complexity=_line_complexity(node),
                        )
                    )

        for node in all_nodes:
            if node.type == "function_definition":
                function_name = _entity_name(node)
                if not function_name:
                    continue

                class_name = self._containing_class_name(node)
                qualified_name = f"{class_name}.{function_name}" if class_name else function_name
                function_calls, function_edges = self._extract_calls(
                    node, file_path, qualified_name
                )
                calls.extend(function_edges)
                first_line = _node_text(node).splitlines()[0] if _node_text(node) else ""
                signature = first_line.split(":", 1)[0].strip()
                entities.append(
                    CodeEntity(
                        name=qualified_name,
                        type="function",
                        file_path=file_path,
                        line_start=node.start_point[0] + 1,
                        line_end=node.end_point[0] + 1,
                        signature=signature or None,
                        complexity=_line_complexity(node),
                        calls=function_calls,
                    )
                )

            elif node.type in {"import_statement", "import_from_statement"}:
                imports.extend(self._extract_imports(node, source_module))

        return entities, imports, calls

    @staticmethod
    def _containing_class_name(node: Node) -> str | None:
        """Return the enclosing class name unless another function intervenes."""
        parent = node.parent
        while parent is not None:
            if parent.type == "function_definition":
                return None
            if parent.type == "class_definition":
                return _entity_name(parent) or None
            parent = parent.parent
        return None

    @staticmethod
    def _extract_calls(
        node: Node, file_path: str, function_name: str
    ) -> tuple[list[str], list[CallEdge]]:
        """Extract call names and graph edges from a function declaration."""
        call_names: list[str] = []
        edges: list[CallEdge] = []
        caller = f"{file_path}::{function_name}"
        for descendant in _walk_nodes(node):
            if descendant.type != "call" or not PythonExtractor._belongs_to_function(
                descendant, node
            ):
                continue
            callee = _callee_name(descendant)
            if not callee:
                continue
            call_names.append(callee)
            edges.append(CallEdge(caller=caller, callee=callee, line=descendant.start_point[0] + 1))
        return call_names, edges

    @staticmethod
    def _belongs_to_function(call_node: Node, function_node: Node) -> bool:
        """Return whether a call belongs to this function rather than a nested function."""
        parent = call_node.parent
        while parent is not None and parent != function_node:
            if parent.type == "function_definition":
                return False
            parent = parent.parent
        return parent == function_node

    @staticmethod
    def _extract_imports(node: Node, source_module: str) -> list[ImportEdge]:
        """Turn one Python import statement into one or more dependency edges."""
        source = _node_text(node).strip()
        from_match = re.match(r"^from\s+(\S+)\s+import\s+(.+)$", source, re.DOTALL)
        if from_match:
            target = from_match.group(1)
            names = [
                name.strip().split(" as ", 1)[0]
                for name in from_match.group(2).replace("(", "").replace(")", "").split(",")
                if name.strip()
            ]
            return [
                ImportEdge(
                    source_module=source_module,
                    target_module=target,
                    imported_names=names,
                )
            ]

        import_match = re.match(r"^import\s+(.+)$", source, re.DOTALL)
        if import_match:
            names = [
                name.strip().split(" as ", 1)[0]
                for name in import_match.group(1).split(",")
                if name.strip()
            ]
            return [
                ImportEdge(source_module=source_module, target_module=name, imported_names=[name])
                for name in names
            ]
        return []


class JavaScriptExtractor:
    """Extract JavaScript and TypeScript graph data from Tree-sitter ASTs."""

    def extract(
        self, root_node: Node, file_path: str, source_bytes: bytes
    ) -> tuple[list[CodeEntity], list[ImportEdge], list[CallEdge]]:
        """Extract structured code graph data from a JavaScript-family syntax tree."""
        del source_bytes
        entities: list[CodeEntity] = []
        imports: list[ImportEdge] = []
        calls: list[CallEdge] = []
        source_module = _source_module(file_path)
        all_nodes = _walk_nodes(root_node)

        for node in all_nodes:
            if node.type == "class_declaration":
                class_name = _entity_name(node)
                if class_name:
                    entities.append(
                        CodeEntity(
                            name=class_name,
                            type="class",
                            file_path=file_path,
                            line_start=node.start_point[0] + 1,
                            line_end=node.end_point[0] + 1,
                            complexity=_line_complexity(node),
                        )
                    )

        for node in all_nodes:
            function_name = self._function_name(node)
            if function_name:
                class_name = self._containing_class_name(node)
                qualified_name = f"{class_name}.{function_name}" if class_name else function_name
                function_calls, function_edges = self._extract_calls(
                    node, file_path, qualified_name
                )
                calls.extend(function_edges)
                signature = _node_text(node).splitlines()[0].strip() if _node_text(node) else None
                entities.append(
                    CodeEntity(
                        name=qualified_name,
                        type="function",
                        file_path=file_path,
                        line_start=node.start_point[0] + 1,
                        line_end=node.end_point[0] + 1,
                        signature=signature,
                        complexity=_line_complexity(node),
                        calls=function_calls,
                    )
                )
            elif node.type == "import_statement":
                edge = self._extract_import(node, source_module)
                if edge is not None:
                    imports.append(edge)

        return entities, imports, calls

    @staticmethod
    def _function_name(node: Node) -> str | None:
        """Return a name for a supported JavaScript function node."""
        if node.type in {"function_declaration", "method_definition"}:
            return _entity_name(node) or None
        if node.type != "arrow_function" or node.parent is None:
            return None
        if node.parent.type != "variable_declarator":
            return None
        return _entity_name(node.parent) or None

    @staticmethod
    def _containing_class_name(node: Node) -> str | None:
        """Return the enclosing class name unless a nested function intervenes."""
        parent = node.parent
        while parent is not None:
            if parent.type in {"function_declaration", "arrow_function", "method_definition"}:
                return None
            if parent.type == "class_declaration":
                return _entity_name(parent) or None
            parent = parent.parent
        return None

    @staticmethod
    def _extract_calls(
        node: Node, file_path: str, function_name: str
    ) -> tuple[list[str], list[CallEdge]]:
        """Extract call names and graph edges from a JavaScript function."""
        call_names: list[str] = []
        edges: list[CallEdge] = []
        caller = f"{file_path}::{function_name}"
        for descendant in _walk_nodes(node):
            if descendant.type != "call_expression" or not JavaScriptExtractor._belongs_to_function(
                descendant, node
            ):
                continue
            callee = _callee_name(descendant)
            if not callee:
                continue
            call_names.append(callee)
            edges.append(CallEdge(caller=caller, callee=callee, line=descendant.start_point[0] + 1))
        return call_names, edges

    @staticmethod
    def _belongs_to_function(call_node: Node, function_node: Node) -> bool:
        """Return whether a call belongs to this JS function rather than a nested function."""
        parent = call_node.parent
        nested_types = {"function_declaration", "arrow_function", "method_definition"}
        while parent is not None and parent != function_node:
            if parent.type in nested_types:
                return False
            parent = parent.parent
        return parent == function_node

    @staticmethod
    def _extract_import(node: Node, source_module: str) -> ImportEdge | None:
        """Turn an ES module import node into an import edge."""
        statement = _node_text(node).strip()
        match = re.search(r"(?:from\s+)?[\"']([^\"']+)[\"']", statement)
        if not match:
            return None

        target = match.group(1)
        imported_names: list[str] = []
        names_match = re.search(r"\{([^}]+)\}", statement)
        if names_match:
            imported_names.extend(
                name.strip().split(" as ")[-1]
                for name in names_match.group(1).split(",")
                if name.strip()
            )
        else:
            default_match = re.match(r"^import\s+([\w$]+)", statement)
            if default_match:
                imported_names.append(default_match.group(1))

        return ImportEdge(
            source_module=source_module,
            target_module=target,
            imported_names=imported_names,
        )


class RegexExtractor:
    """Provide a best-effort parser for languages without Tree-sitter grammars."""

    _KEYWORD_FUNCTION_PATTERN = re.compile(
        r"^\s*(?:def|func|fn|fun|function)\s+([A-Za-z_]\w*)\s*\("
    )
    _C_STYLE_FUNCTION_PATTERN = re.compile(
        r"^\s*(?:(?:public|private|protected|internal|static|final|abstract|virtual|"
        r"override|async|extern|unsafe|inline|constexpr|friend|sealed|partial|readonly|"
        r"synchronized)\s+)*(?:[A-Za-z_]\w*(?:\s*<[\w\s,.:?*&]+>)?\s+)+"
        r"([A-Za-z_]\w*)\s*\("
    )
    _CLASS_PATTERN = re.compile(r"^\s*(?:class|struct|interface|enum)\s+(\w+)")
    _PYTHON_IMPORT_PATTERN = re.compile(r"^(?:from\s+(\S+)\s+)?import\s+(.+)$")
    _JAVASCRIPT_IMPORT_PATTERN = re.compile(r"^import\s+.*from\s+[\"'](.+)[\"']")
    _JAVA_GO_IMPORT_PATTERN = re.compile(r"^import\s+[\"']?(.+?)[\"']?\s*;?\s*$")
    _CONTROL_FLOW_PREFIXES = frozenset(
        {
            "if",
            "else",
            "for",
            "while",
            "switch",
            "case",
            "catch",
            "return",
            "throw",
            "new",
            "delete",
            "sizeof",
        }
    )

    def extract(
        self, source_text: str, file_path: str, language: str
    ) -> tuple[list[CodeEntity], list[ImportEdge], list[CallEdge]]:
        """Extract declaration and import data with language-neutral regex patterns."""
        entities: list[CodeEntity] = []
        imports: list[ImportEdge] = []
        source_module = _source_module(file_path)

        for line_number, line in enumerate(source_text.splitlines(), start=1):
            function_name = self._extract_function_name(line)
            if function_name:
                entities.append(
                    CodeEntity(
                        name=function_name,
                        type="function",
                        file_path=file_path,
                        line_start=line_number,
                        line_end=line_number,
                        signature=line.strip() or None,
                        complexity=0,
                        source="regex-fallback",
                    )
                )
                continue

            class_match = self._CLASS_PATTERN.match(line)
            if class_match:
                entities.append(
                    CodeEntity(
                        name=class_match.group(1),
                        type="class",
                        file_path=file_path,
                        line_start=line_number,
                        line_end=line_number,
                        complexity=0,
                        source="regex-fallback",
                    )
                )

            edge = self._extract_import(line, language, source_module)
            if edge is not None:
                imports.append(edge)

        return entities, imports, []

    def _extract_function_name(self, line: str) -> str | None:
        """Return a conservative declaration name without mistaking control flow for code."""
        stripped = line.lstrip()
        first_token = stripped.split(None, 1)[0].rstrip("(") if stripped else ""
        if first_token.casefold() in self._CONTROL_FLOW_PREFIXES:
            return None

        keyword_match = self._KEYWORD_FUNCTION_PATTERN.match(line)
        if keyword_match:
            return keyword_match.group(1)

        c_style_match = self._C_STYLE_FUNCTION_PATTERN.match(line)
        return c_style_match.group(1) if c_style_match else None

    def _extract_import(self, line: str, language: str, source_module: str) -> ImportEdge | None:
        """Create an import edge from one line of fallback-parsed source."""
        if language in {"javascript", "typescript"}:
            match = self._JAVASCRIPT_IMPORT_PATTERN.match(line)
            if match:
                return ImportEdge(source_module, match.group(1), [])
            return None

        if language in {"java", "go"}:
            match = self._JAVA_GO_IMPORT_PATTERN.match(line)
            if match:
                return ImportEdge(source_module, match.group(1), [])
            return None

        match = self._PYTHON_IMPORT_PATTERN.match(line)
        if not match:
            return None
        target_module = match.group(1) or match.group(2).split(",", 1)[0].strip()
        imported_names = [name.strip() for name in match.group(2).split(",") if name.strip()]
        return ImportEdge(source_module, target_module, imported_names)


def scan_repository(repo_path: str | Path, scope: str | None = None) -> list[dict]:
    """Return metadata for supported, non-ignored files in a repository."""
    config = get_config()
    repo_root = Path(repo_path).expanduser().resolve()
    if not repo_root.is_dir():
        raise ValueError(f"Repository path does not exist or is not a directory: {repo_path}")

    scan_root = repo_root
    if scope is not None and scope.strip():
        scope_path = Path(scope).expanduser()
        if scope_path.is_absolute() or scope_path.drive:
            raise ValueError("Scope must be a relative subdirectory of the repository root.")
        scan_root = (repo_root / scope_path).resolve()
        try:
            scan_root.relative_to(repo_root)
        except ValueError as exc:
            raise ValueError("Scope must stay within the repository root.") from exc
        if not scan_root.is_dir():
            raise ValueError(f"Scope does not exist or is not a directory: {scope}")

    files: list[dict] = []

    for root, dirs, filenames in os.walk(scan_root, followlinks=False):
        dirs[:] = [
            dirname
            for dirname in dirs
            if not config.should_skip_dir(dirname) and dirname not in IGNORE_DIRS
        ]

        for filename in filenames:
            if filename in IGNORE_FILES:
                continue

            absolute_path = Path(root) / filename
            try:
                file_size = absolute_path.stat().st_size
            except OSError as exc:
                logger.warning("Skipping unreadable file %s: %s", absolute_path, exc)
                continue

            if config.should_skip_file(filename, file_size):
                continue

            extension = absolute_path.suffix.lower()
            if extension in NON_CODE_EXTENSIONS or not config.is_code_file(extension):
                continue

            try:
                relative_path = absolute_path.resolve().relative_to(repo_root).as_posix()
            except ValueError:
                logger.warning("Skipping file outside repository root: %s", absolute_path)
                continue

            files.append(
                {
                    "path": relative_path,
                    "abs_path": str(absolute_path.resolve()),
                    "extension": extension,
                    "size": file_size,
                }
            )
            if len(files) > config.max_files:
                raise ValueError(
                    f"Repository has more than {config.max_files} supported files. "
                    "Use the scope parameter to analyze a smaller subdirectory."
                )

    files.sort(key=lambda file_info: file_info["path"])
    if len(files) > config.max_files_warn:
        logger.warning(
            "Repository has %s supported files; analysis may take longer than usual.", len(files)
        )
    return files


def parse_repository(
    repo_path: str | Path, scope: str | None = None
) -> tuple[list[ParsedFile], list[str]]:
    """Scan and parse a repository without allowing individual files to abort analysis."""
    config = get_config()
    files = scan_repository(repo_path, scope)
    manager = TreeSitterManager()
    python_extractor = PythonExtractor()
    javascript_extractor = JavaScriptExtractor()
    regex_extractor = RegexExtractor()
    warnings: list[str] = []
    results: list[ParsedFile] = []

    if len(files) > config.max_files_warn:
        warnings.append(
            f"Repository contains {len(files)} supported files; "
            "analysis may take longer than usual."
        )

    for file_info in files:
        file_path = file_info["path"]
        extension = file_info["extension"]
        language = config.get_language_for_extension(extension)
        parse_method = "tree-sitter" if language else "regex-fallback"
        syntax_recovered = False

        try:
            source_bytes = Path(file_info["abs_path"]).read_bytes()
            try:
                source_text = source_bytes.decode("utf-8")
            except UnicodeDecodeError:
                warning = f"Skipped binary or non-UTF-8 file: {file_path}"
                logger.warning(warning)
                warnings.append(warning)
                continue

            if language:
                parser_language = "tsx" if extension == ".tsx" else language
                root_node = manager.parse_file(source_bytes, parser_language)
                if root_node is None:
                    message = f"Tree-sitter could not parse {file_path}"
                    results.append(
                        ParsedFile(
                            file_path=file_path,
                            language=language,
                            entities=[],
                            imports=[],
                            calls=[],
                            parse_method="tree-sitter",
                            error=message,
                        )
                    )
                    warnings.append(message)
                    continue

                syntax_recovered = root_node.has_error
                if syntax_recovered:
                    warnings.append(
                        f"Tree-sitter recovered from syntax errors in {file_path}; "
                        "results may be incomplete."
                    )

                if language == "python":
                    entities, imports, calls = python_extractor.extract(
                        root_node, file_path, source_bytes
                    )
                else:
                    entities, imports, calls = javascript_extractor.extract(
                        root_node, file_path, source_bytes
                    )
            elif extension in REGEX_FALLBACK_EXTENSIONS:
                language = extension.lstrip(".")
                entities, imports, calls = regex_extractor.extract(source_text, file_path, language)
            else:
                continue

            line_count = max(1, source_text.count("\n") + 1)
            entities.insert(
                0,
                CodeEntity(
                    name=Path(file_path).stem,
                    type="module",
                    file_path=file_path,
                    line_start=1,
                    line_end=line_count,
                    source="tree-sitter-ast" if parse_method == "tree-sitter" else "regex-fallback",
                ),
            )
            results.append(
                ParsedFile(
                    file_path=file_path,
                    language=language or "unknown",
                    entities=entities,
                    imports=imports,
                    calls=calls,
                    parse_method=parse_method,
                    syntax_recovered=syntax_recovered,
                )
            )
        except Exception as exc:
            message = f"Failed to parse {file_path}: {exc}"
            logger.warning(message)
            warnings.append(message)
            results.append(
                ParsedFile(
                    file_path=file_path,
                    language=language or "unknown",
                    entities=[],
                    imports=[],
                    calls=[],
                    parse_method=parse_method,
                    error=str(exc),
                )
            )

    return results, warnings
