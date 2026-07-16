from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

from git import GitCommandError, GitCommandNotFound, InvalidGitRepositoryError, Repo

from .config import get_config
from .models import AuthorInfo, CoChangeInfo, CommitInfo, FileAge, GitContextOutput


class GitAnalyzer:
    """Wrap defensive GitPython history analysis for one repository."""

    def __init__(self, repo_path: str | Path):
        """Initialize the repository connection without allowing git failures to escape."""
        self.repo_path = Path(repo_path).resolve()
        self.repo: Repo | None = None
        self.available: bool = False
        self._commit_file_map: dict[str, list[str]] | None = None
        self._file_commit_count: dict[str, int] | None = None

        try:
            self.repo = Repo(self.repo_path)
            self.available = True
        except (InvalidGitRepositoryError, GitCommandNotFound, GitCommandError, OSError):
            self.repo = None
            self.available = False
        except Exception:
            self.repo = None
            self.available = False

    def close(self) -> None:
        """Release GitPython resources when an analyzed repository is replaced."""
        try:
            if self.repo is not None:
                self.repo.close()
        except Exception:
            pass
        finally:
            self.repo = None
            self.available = False
            self._commit_file_map = None
            self._file_commit_count = None

    def get_file_blame(self, file_path: str) -> list[AuthorInfo]:
        """Return author ownership percentages calculated from git blame data."""
        try:
            if not self.available or self.repo is None:
                return []
            target = self._resolve_repo_file(file_path)
            if target is None or target.stat().st_size > get_config().max_git_blame_file_size_bytes:
                return []
            relative_path = target.relative_to(self.repo_path).as_posix()

            ownership: dict[str, dict[str, int | str]] = {}
            for commit, lines in self.repo.blame(self.repo.head.commit, relative_path):
                author_name = commit.author.name
                author_email = commit.author.email
                if author_name not in ownership:
                    ownership[author_name] = {"email": author_email, "lines_owned": 0}
                ownership[author_name]["lines_owned"] = int(
                    ownership[author_name]["lines_owned"]
                ) + len(lines)

            total_lines = sum(int(author["lines_owned"]) for author in ownership.values())
            if total_lines == 0:
                return []

            authors = [
                AuthorInfo(
                    name=name,
                    email=str(data["email"]),
                    lines_owned=int(data["lines_owned"]),
                    percentage=(int(data["lines_owned"]) / total_lines) * 100,
                )
                for name, data in ownership.items()
            ]
            return sorted(authors, key=lambda author: author.lines_owned, reverse=True)
        except Exception:
            return []

    def get_file_commits(self, file_path: str, max_commits: int | None = None) -> list[CommitInfo]:
        """Return the most recent commits that touched a file."""
        try:
            if not self.available or self.repo is None:
                return []

            target = self._resolve_repo_file(file_path)
            if target is None:
                return []
            relative_path = target.relative_to(self.repo_path).as_posix()
            config = get_config()
            requested_limit = max_commits if max_commits is not None else config.max_git_commits
            limit = max(1, min(requested_limit, config.max_git_history_commits))
            return [
                CommitInfo(
                    sha=commit.hexsha[:8],
                    date=datetime.fromtimestamp(commit.committed_date, tz=timezone.utc).strftime(
                        "%Y-%m-%d"
                    ),
                    author=commit.author.name,
                    message=commit.message.strip().split("\n")[0][:120],
                )
                for commit in self.repo.iter_commits(paths=relative_path, max_count=limit)
            ]
        except Exception:
            return []

    def get_file_change_frequency(self, file_path: str) -> int:
        """Return a bounded-history count of commits that have touched a file."""
        try:
            if not self.available or self.repo is None:
                return 0
            target = self._resolve_repo_file(file_path)
            if target is None:
                return 0
            limit = get_config().max_git_history_commits
            relative_path = target.relative_to(self.repo_path).as_posix()
            return sum(1 for _ in self.repo.iter_commits(paths=relative_path, max_count=limit))
        except Exception:
            return 0

    def get_file_age(self, file_path: str) -> FileAge | None:
        """Return the earliest known and latest dates within the configured history window."""
        try:
            if not self.available or self.repo is None:
                return None

            target = self._resolve_repo_file(file_path)
            if target is None:
                return None
            relative_path = target.relative_to(self.repo_path).as_posix()
            commits = list(
                self.repo.iter_commits(
                    paths=relative_path,
                    max_count=get_config().max_git_history_commits,
                )
            )
            if not commits:
                return None

            first_commit = commits[-1]
            last_commit = commits[0]
            last_modified = datetime.fromtimestamp(last_commit.committed_date, tz=timezone.utc)
            return FileAge(
                created_date=datetime.fromtimestamp(
                    first_commit.committed_date, tz=timezone.utc
                ).strftime("%Y-%m-%d"),
                last_modified_date=last_modified.strftime("%Y-%m-%d"),
                days_since_last_change=max(0, (datetime.now(timezone.utc) - last_modified).days),
            )
        except Exception:
            return None

    def _build_commit_file_map(self) -> None:
        """Build and cache mappings of commits to their changed files."""
        try:
            if self._commit_file_map is not None:
                return

            self._commit_file_map = {}
            self._file_commit_count = defaultdict(int)
            if not self.available or self.repo is None:
                return

            for commit in self.repo.iter_commits(max_count=get_config().max_git_history_commits):
                if commit.parents:
                    changed_files = [
                        diff.a_path or diff.b_path
                        for diff in commit.diff(commit.parents[0])
                        if diff.a_path or diff.b_path
                    ]
                else:
                    changed_files = [
                        item.path for item in commit.tree.traverse() if item.type == "blob"
                    ]

                unique_files = sorted(set(changed_files))
                self._commit_file_map[commit.hexsha] = unique_files
                for changed_file in unique_files:
                    self._file_commit_count[changed_file] += 1
        except Exception:
            self._commit_file_map = {}
            self._file_commit_count = {}

    def get_co_changed_files(self, file_path: str) -> list[CoChangeInfo]:
        """Return files frequently modified in the same commits as a target file."""
        try:
            if not self.available:
                return []
            target = self._resolve_repo_file(file_path)
            if target is None:
                return []

            self._build_commit_file_map()
            if self._commit_file_map is None or self._file_commit_count is None:
                return []

            normalized_path = target.relative_to(self.repo_path).as_posix()
            commits_for_file = [
                changed_files
                for changed_files in self._commit_file_map.values()
                if normalized_path in changed_files
            ]
            total_commits_for_file = len(commits_for_file)
            if total_commits_for_file == 0:
                return []

            co_change_counts: Counter[str] = Counter(
                changed_file
                for changed_files in commits_for_file
                for changed_file in changed_files
                if changed_file != normalized_path
            )
            config = get_config()
            results = [
                CoChangeInfo(
                    file_path=changed_file,
                    co_change_count=count,
                    co_change_percentage=(count / total_commits_for_file) * 100,
                )
                for changed_file, count in co_change_counts.items()
                if count >= config.co_change_min_commits
                and (count / total_commits_for_file) * 100 >= config.co_change_min_percentage
            ]
            results.sort(key=lambda result: result.co_change_count, reverse=True)
            return results[: config.max_co_changed_files]
        except Exception:
            return []

    def get_all_file_change_frequencies(self) -> dict[str, int]:
        """Return cached change frequencies for every file in the repository."""
        try:
            if not self.available:
                return {}
            self._build_commit_file_map()
            return dict(self._file_commit_count or {})
        except Exception:
            return {}

    def get_full_context(self, file_path: str, entity_name: str | None = None) -> GitContextOutput:
        """Return all available git context for a file in one structured response."""
        try:
            history_limit = get_config().max_git_history_commits
            source = f"git-log (up to {history_limit} most recent commits)"
            if entity_name:
                source += "; file-level; entity filtering unavailable"
            return GitContextOutput(
                file_path=file_path,
                authors=self.get_file_blame(file_path),
                change_frequency=self.get_file_change_frequency(file_path),
                recent_commits=self.get_file_commits(file_path),
                co_changed_files=self.get_co_changed_files(file_path),
                file_age=self.get_file_age(file_path),
                source=source,
            )
        except Exception:
            return GitContextOutput(file_path=file_path)

    def get_head_hash(self) -> str:
        """Return the full current HEAD SHA for graph-cache validation."""
        try:
            if not self.available or self.repo is None:
                return ""
            return self.repo.head.commit.hexsha
        except Exception:
            return ""

    def _resolve_repo_file(self, file_path: str) -> Path | None:
        """Resolve one repository-contained file path without accepting Git pathspecs."""
        try:
            requested_path = Path(file_path)
            if not file_path or requested_path.is_absolute() or requested_path.drive:
                return None
            if any(part.startswith(":") for part in requested_path.parts):
                return None
            resolved_path = (self.repo_path / requested_path).resolve()
            resolved_path.relative_to(self.repo_path)
            return resolved_path if resolved_path.is_file() else None
        except Exception:
            return None
