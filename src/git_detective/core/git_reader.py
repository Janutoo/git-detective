"""Read commit history from a local Git repository.

Safety note: the reader only inspects Git *objects* (``rev-list`` via GitPython
and the ``diff-tree`` plumbing command). It never checks out files, runs
``git status``, or invokes hooks. The repository's own ``.git/config`` is
untrusted, so everything in it that could run a command on these code paths is
turned off: external diff drivers, textconv filters and the file system monitor
(``core.fsmonitor``, which ``diff-tree`` would otherwise start).
"""

from pathlib import Path
from types import TracebackType
from typing import Self

from git import Commit, Repo
from git.exc import GitCommandError, InvalidGitRepositoryError, NoSuchPathError

from git_detective.exceptions import GitReadError, InvalidRepositoryError
from git_detective.models import CommitInfo, FileChange

DEFAULT_COMMIT_LIMIT = 100

# Flags for `git diff-tree` that yield a stable, machine-readable list of
# per-file line counts for a single commit.
_DIFF_TREE_ARGS = (
    "--root",  # also diff the very first commit against an empty tree
    "-r",  # recurse into subdirectories
    "--numstat",  # "<added>\t<deleted>\t<path>" per file
    "-z",  # NUL-separated output, paths are never quoted
    "--no-commit-id",  # do not print the commit hash before the file list
    "--no-renames",  # treat renames as delete + add (simpler, predictable)
    "--no-ext-diff",  # never run external diff programs from git config
    "--no-textconv",  # never run textconv filters from git config
)


class GitReader:
    """Reads commits and per-file line changes from a local repository.

    Use it as a context manager so the underlying Git processes are released::

        with GitReader(".") as reader:
            commits = reader.read_commits(limit=50)
    """

    def __init__(self, path: Path | str, *, allow_lazy_fetch: bool = True) -> None:
        """Open the repository containing ``path``.

        Args:
            path: The repository or any directory inside it.
            allow_lazy_fetch: In a partial clone, Git downloads missing objects
                on demand, using the transport set in the repository's config.
                Disable this where reading a repository must never reach the
                network or run a configured command; reading then fails instead.
        """
        try:
            self._repo = Repo(path, search_parent_directories=True)
        except NoSuchPathError as exc:
            raise InvalidRepositoryError(f"Path does not exist: {path}") from exc
        except InvalidGitRepositoryError as exc:
            raise InvalidRepositoryError(f"Not a Git repository: {path}") from exc
        # Command-line configuration takes precedence over the repository's.
        self._repo.git.set_persistent_git_options(c="core.fsmonitor=false")
        if not allow_lazy_fetch:
            self._repo.git.update_environment(GIT_NO_LAZY_FETCH="1")

    @property
    def root(self) -> Path:
        """Top-level directory of the repository (or the git dir if bare)."""
        return Path(self._repo.working_tree_dir or self._repo.git_dir)

    @property
    def head_sha(self) -> str | None:
        """Full hash of the HEAD commit, or None if there are no commits yet."""
        head = self._repo.head
        return head.commit.hexsha if head.is_valid() else None

    def read_commits(
        self, limit: int = DEFAULT_COMMIT_LIMIT, *, rev: str = "HEAD"
    ) -> list[CommitInfo]:
        """Return up to ``limit`` most recent commits reachable from ``rev``.

        Commits are ordered from newest to oldest. Merge commits are skipped:
        their changes already appear in the commits being merged, so counting
        them again would inflate churn.

        ``rev`` is passed to Git as a revision, so it must come from trusted
        code, never directly from user input.

        Raises:
            ValueError: If ``limit`` is smaller than 1.
            GitReadError: If Git fails while reading history.
        """
        if limit < 1:
            raise ValueError(f"limit must be a positive integer, got {limit}")

        # A freshly initialized repository has no commits, so HEAD points to
        # a branch that does not exist yet.
        if not self._repo.head.is_valid():
            return []

        try:
            return [
                self._to_commit_info(commit)
                for commit in self._repo.iter_commits(
                    rev, max_count=limit, no_merges=True
                )
            ]
        except GitCommandError as exc:
            message = (exc.stderr or str(exc)).strip()
            raise GitReadError(f"Failed to read Git history: {message}") from exc

    def close(self) -> None:
        """Release Git subprocesses held by GitPython."""
        self._repo.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()

    def _to_commit_info(self, commit: Commit) -> CommitInfo:
        message = commit.message
        if isinstance(message, bytes):  # undecodable messages come back as bytes
            message = message.decode("utf-8", errors="replace")

        return CommitInfo(
            hexsha=commit.hexsha,
            author_name=commit.author.name or "",
            author_email=commit.author.email or "",
            authored_at=commit.authored_datetime,
            message=message.strip(),
            files=self._read_file_changes(commit.hexsha),
        )

    def _read_file_changes(self, hexsha: str) -> tuple[FileChange, ...]:
        output = self._repo.git.diff_tree(*_DIFF_TREE_ARGS, hexsha)
        return parse_numstat(output)


def parse_numstat(output: str) -> tuple[FileChange, ...]:
    """Parse ``git diff-tree --numstat -z --no-renames`` output.

    Each record looks like ``"<added>\\t<deleted>\\t<path>"`` and records are
    separated by NUL bytes. Binary files report ``-`` instead of numbers;
    they are counted as zero changed lines.
    """
    changes: list[FileChange] = []
    for record in output.split("\0"):
        if not record.strip():
            continue
        added, deleted, path = record.split("\t", 2)
        changes.append(
            FileChange(path=path, added=_to_int(added), deleted=_to_int(deleted))
        )
    return tuple(changes)


def _to_int(value: str) -> int:
    return 0 if value == "-" else int(value)
