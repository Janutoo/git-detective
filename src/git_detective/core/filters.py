"""Exclude files from analysis using ``.gitignore``-style patterns.

Patterns are matched against repository-relative paths with forward slashes,
exactly as Git reports them (e.g. ``src/app/main.py``). The rules are the
ones you know from ``.gitignore``:

* ``*.lock`` - a pattern without a slash matches a file name at any depth;
* ``dist/**`` or ``/dist/`` - a pattern with a slash is anchored to the root;
* ``node_modules/`` - a trailing slash matches a directory (at any depth when
  there is no other slash) together with everything inside it;
* ``*`` does not cross ``/``, while ``**`` does;
* ``!pattern`` re-includes a path excluded by an earlier pattern;
* lines starting with ``#`` and blank lines are ignored.

Git Detective has no built-in exclusions: only files matched by patterns from
``--exclude`` or ``.gitdetectiveignore`` are skipped.
"""

import dataclasses
from collections.abc import Iterable
from pathlib import Path

from pathspec import GitIgnoreSpec

from git_detective.core.safe_files import read_repo_text
from git_detective.exceptions import IgnorePatternError
from git_detective.models import CommitInfo

IGNORE_FILE_NAME = ".gitdetectiveignore"


class PathFilter:
    """Decides whether a repository-relative path is excluded from analysis."""

    def __init__(self, patterns: Iterable[str] = ()) -> None:
        self.patterns: tuple[str, ...] = tuple(patterns)
        try:
            self._spec = GitIgnoreSpec.from_lines(self.patterns)
        except ValueError as exc:
            raise IgnorePatternError(f"Invalid exclude pattern: {exc}") from exc

    def is_excluded(self, path: str) -> bool:
        """Return True if ``path`` matches the exclude patterns."""
        return self._spec.match_file(path)

    def __bool__(self) -> bool:
        return bool(self.patterns)

    def __repr__(self) -> str:
        return f"PathFilter({list(self.patterns)!r})"


def read_ignore_file(path: Path) -> list[str]:
    """Read patterns from an ignore file; a missing file yields no patterns.

    Blank lines and ``#`` comments are dropped so the result lists only real
    patterns (use ``\\#`` for a pattern that starts with ``#``). The patterns
    end up in reports, so symlinks and other non-regular files are refused.
    """
    try:
        lines = read_repo_text(path).splitlines()
    except FileNotFoundError:
        return []
    except (OSError, UnicodeDecodeError) as exc:
        raise IgnorePatternError(f"Cannot read ignore file {path}: {exc}") from exc
    return [line for line in lines if line.strip() and not line.startswith("#")]


def filter_commits(
    commits: Iterable[CommitInfo], path_filter: PathFilter
) -> list[CommitInfo]:
    """Return copies of ``commits`` without the excluded files.

    Commits themselves are kept, even if every file they touched was excluded,
    so the number of analyzed commits keeps matching ``--limit``.
    """
    if not path_filter:
        return list(commits)
    return [
        dataclasses.replace(
            commit,
            files=tuple(
                change
                for change in commit.files
                if not path_filter.is_excluded(change.path)
            ),
        )
        for commit in commits
    ]
