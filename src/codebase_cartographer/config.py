from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

TREE_SITTER_LANGUAGES: dict[str, list[str]] = {
    "python": [".py"],
    "javascript": [".js", ".jsx", ".mjs"],
    "typescript": [".ts", ".tsx"],
}

REGEX_FALLBACK_EXTENSIONS: list[str] = [
    ".java",
    ".go",
    ".rs",
    ".rb",
    ".php",
    ".c",
    ".cpp",
    ".h",
    ".hpp",
    ".cs",
    ".swift",
    ".kt",
    ".scala",
]

NON_CODE_EXTENSIONS: list[str] = [
    ".md",
    ".txt",
    ".rst",
    ".yaml",
    ".yml",
    ".toml",
    ".json",
    ".xml",
    ".html",
    ".css",
    ".scss",
    ".sql",
    ".graphql",
]

IGNORE_DIRS: set[str] = {
    "node_modules",
    ".git",
    "__pycache__",
    ".venv",
    "venv",
    "env",
    "dist",
    "build",
    ".next",
    ".nuxt",
    "target",
    "vendor",
    ".tox",
    ".mypy_cache",
    ".pytest_cache",
    ".eggs",
    ".cache",
    ".cartographer_cache",
    "egg-info",
}

IGNORE_FILES: set[str] = {
    "package-lock.json",
    "yarn.lock",
    "pnpm-lock.yaml",
    "poetry.lock",
    "Pipfile.lock",
    ".DS_Store",
    "thumbs.db",
}

ENTRY_POINT_PATTERNS: list[str] = [
    "^main$",
    "^app$",
    "^cli$",
    "^__main__$",
    "^test_.*",
    "^conftest$",
    r"^(?:get|post|put|delete|patch)(?:_|[A-Z]|$)",
    r"^handle(?:_|[A-Z]|$)",
    "^on_",
    "^setup$",
    "^teardown$",
    "^index$",
    "^route_",
]


@dataclass
class AppConfig:
    """Hold internal analysis limits, cache settings, and scanning filters."""

    max_file_size_bytes: int = 500_000
    max_files: int = 5000
    max_files_warn: int = 2000
    max_output_entities: int = 20
    max_trace_steps: int = 15
    max_issues: int = 30
    max_metrics: int = 15
    max_mermaid_nodes: int = 25
    max_git_commits: int = 10
    max_git_history_commits: int = 500
    max_git_blame_file_size_bytes: int = 500_000
    max_git_files_for_ownership: int = 200
    max_co_changed_files: int = 5
    co_change_min_commits: int = 3
    co_change_min_percentage: float = 50.0
    god_class_fan_threshold: int = 10
    max_cycles: int = 100
    max_search_query_length: int = 120
    max_exact_centrality_nodes: int = 250
    centrality_sample_nodes: int = 100
    cache_dir_name: str = ".cartographer_cache"
    cache_file_name: str = "graph_cache.json"
    cache_schema_version: int = 2

    def get_cache_path(self, repo_path: str | Path) -> Path:
        """Return the cache file path for a repository."""
        return Path(repo_path) / self.cache_dir_name / self.cache_file_name

    def get_language_for_extension(self, ext: str) -> str | None:
        """Return the Tree-sitter language that handles an extension, if any."""
        for language, extensions in TREE_SITTER_LANGUAGES.items():
            if ext in extensions:
                return language
        return None

    def is_code_file(self, ext: str) -> bool:
        """Return whether an extension is supported by either parser strategy."""
        return self.get_language_for_extension(ext) is not None or ext in REGEX_FALLBACK_EXTENSIONS

    def should_skip_dir(self, dirname: str) -> bool:
        """Return whether a directory should be excluded from scanning."""
        return dirname in IGNORE_DIRS or dirname.endswith(".egg-info")

    def should_skip_file(self, filename: str, file_size: int) -> bool:
        """Return whether a file is ignored or exceeds the maximum supported size."""
        return filename in IGNORE_FILES or file_size > self.max_file_size_bytes


_config: AppConfig | None = None


def get_config() -> AppConfig:
    """Return the process-wide application configuration."""
    global _config
    if _config is None:
        _config = AppConfig()
    return _config


def set_config(config: AppConfig) -> None:
    """Replace the process-wide application configuration."""
    global _config
    _config = config


def get_all_supported_extensions() -> list[str]:
    """Return all code extensions supported by Tree-sitter or regex parsing."""
    return [
        extension for extensions in TREE_SITTER_LANGUAGES.values() for extension in extensions
    ] + REGEX_FALLBACK_EXTENSIONS
