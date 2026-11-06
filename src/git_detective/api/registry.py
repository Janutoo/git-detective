"""The allowlist of repositories the API may read.

A request only ever carries a repository id; the path comes from the server's
configuration. Each path is pinned to its real location at startup and checked
again on every use, because directories and symlinks can change while the
server runs.
"""

import logging
import os
import stat
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from git_detective.exceptions import GitDetectiveError

logger = logging.getLogger(__name__)


class RepositoryNotFoundError(GitDetectiveError):
    """The repository id is not on the allowlist."""


class RepositoryUnavailableError(GitDetectiveError):
    """An allowed repository cannot be used right now.

    The message may contain absolute paths, so it is for the server log only.
    """


@dataclass(frozen=True, slots=True)
class _Entry:
    configured: Path  # as written in the config file
    canonical: Path  # its real location when the server started


class RepositoryRegistry:
    def __init__(self, repositories: Mapping[str, Path]) -> None:
        self._entries = {
            repository_id: _Entry(path, Path(os.path.realpath(path)))
            for repository_id, path in repositories.items()
        }

    def ids(self) -> list[str]:
        return sorted(self._entries)

    def is_available(self, repository_id: str) -> bool:
        try:
            self.resolve(repository_id)
        except RepositoryUnavailableError as exc:
            logger.warning("Repository %r is unavailable: %s", repository_id, exc)
            return False
        return True

    def resolve(self, repository_id: str) -> Path:
        """Return the checked, real path of an allowed repository.

        Raises:
            RepositoryNotFoundError: If the id is not on the allowlist.
            RepositoryUnavailableError: If the directory disappeared, now
                resolves to a different location, or is not a plain repository.
        """
        entry = self._entries.get(repository_id)
        if entry is None:
            raise RepositoryNotFoundError(f"Unknown repository: {repository_id}")
        _check(entry)
        return entry.canonical


def _check(entry: _Entry) -> None:
    try:
        current = Path(os.path.realpath(entry.configured, strict=True))
    except OSError as exc:
        raise RepositoryUnavailableError(
            f"Cannot access {entry.configured}: {exc}"
        ) from exc
    if current != entry.canonical:
        raise RepositoryUnavailableError(
            f"{entry.configured} now resolves to {current}, "
            f"not to {entry.canonical} as it did at startup"
        )

    # A `.git` file or symlink can point at a Git directory anywhere on the
    # disk, and without any `.git` Git would search the parent directories.
    git_dir = current / ".git"
    try:
        is_plain_git_dir = stat.S_ISDIR(os.lstat(git_dir).st_mode)
    except OSError as exc:
        raise RepositoryUnavailableError(f"Cannot access {git_dir}: {exc}") from exc
    if not is_plain_git_dir:
        raise RepositoryUnavailableError(f"{git_dir} is not a directory")
