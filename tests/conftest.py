"""Shared fixtures that build throwaway Git repositories inside ``tmp_path``.

Tests never touch the user's real repositories and do not depend on the
global ``git config`` (author and committer are always set explicitly).
"""

from collections.abc import Callable, Iterator
from pathlib import Path

import pytest
from git import Actor, Repo

DEFAULT_AUTHOR = Actor("Alice Example", "alice@example.com")
BASE_TIMESTAMP = 1_704_110_400  # 2024-01-01 12:00:00 UTC

# Maps a relative file path to its new content (str/bytes) or None to delete it.
FileSpec = dict[str, str | bytes | None]


@pytest.fixture
def repo(tmp_path: Path) -> Iterator[Repo]:
    """An empty Git repository with no commits."""
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()
    git_repo = Repo.init(repo_dir)
    yield git_repo
    git_repo.close()


@pytest.fixture
def make_commit(repo: Repo) -> Callable[..., str]:
    """Return a helper that writes files and commits them, returning the SHA."""
    counter = 0

    def _commit(
        files: FileSpec,
        message: str = "Update files",
        author: Actor = DEFAULT_AUTHOR,
        timestamp: int | None = None,
    ) -> str:
        nonlocal counter
        root = Path(repo.working_tree_dir)
        for rel_path, content in files.items():
            target = root / rel_path
            if content is None:
                repo.index.remove([rel_path], working_tree=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            if isinstance(content, bytes):
                target.write_bytes(content)
            else:
                target.write_text(content, encoding="utf-8")
            repo.index.add([rel_path])

        # Distinct, increasing dates keep commit order deterministic unless the
        # test pins an explicit timestamp.
        # Git's raw date format: "<unix timestamp> <utc offset>".
        if timestamp is None:
            timestamp = BASE_TIMESTAMP + counter * 60
        date = f"{timestamp} +0000"
        counter += 1
        commit = repo.index.commit(
            message,
            author=author,
            committer=author,
            author_date=date,
            commit_date=date,
        )
        return commit.hexsha

    return _commit
