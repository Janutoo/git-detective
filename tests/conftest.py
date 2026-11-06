"""Shared fixtures that build throwaway Git repositories inside ``tmp_path``.

Tests never touch the user's real repositories and do not depend on the
global ``git config`` (author and committer are always set explicitly).
"""

import os
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from git import Actor, Repo

from git_detective.api.app import create_app
from git_detective.api.config import ApiSettings

# CLI tests compare plain text. Rich reads these variables when the CLI's
# consoles are created (at import time), so drop them before any test module
# imports git_detective.cli; otherwise output would contain ANSI color codes.
for _var in ("FORCE_COLOR", "PY_COLORS"):
    os.environ.pop(_var, None)

DEFAULT_AUTHOR = Actor("Alice Example", "alice@example.com")
BASE_TIMESTAMP = 1_704_110_400  # 2024-01-01 12:00:00 UTC

# Maps a relative file path to its new content (str/bytes) or None to delete it.
FileSpec = dict[str, str | bytes | None]


@pytest.fixture(autouse=True)
def isolated_home(
    tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch
) -> Path:
    """Point the user's home at a throwaway directory for every test.

    The default database lives under the home directory, so no test can reach
    a real database, even by accident.
    """
    home = tmp_path_factory.mktemp("home")
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("XDG_DATA_HOME", raising=False)
    return home


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


@pytest.fixture
def repo_path(repo: Repo) -> Path:
    return Path(repo.working_tree_dir)


@pytest.fixture
def seeded_repo(make_commit: Callable[..., str]) -> str:
    """Two authors and a file pair changed together 3 times. Returns the HEAD SHA."""
    bob = Actor("Bob Builder", "bob@secret.example.com")
    make_commit({"src/a.py": "1\n", "src/b.py": "1\n"})
    make_commit({"src/a.py": "1\n2\n", "src/b.py": "1\n2\n"}, author=bob)
    return make_commit({"src/a.py": "3\n", "src/b.py": "3\n", "c.lock": "x\n"})


@pytest.fixture
def database_path(tmp_path: Path) -> Path:
    return tmp_path / "data" / "analyses.db"


@pytest.fixture
def api_settings(repo_path: Path, database_path: Path) -> ApiSettings:
    return ApiSettings(repositories={"demo": repo_path}, database=database_path)


@pytest.fixture
def api_app(api_settings: ApiSettings) -> FastAPI:
    return create_app(api_settings)


@pytest.fixture
def client(api_app: FastAPI) -> Iterator[TestClient]:
    with TestClient(api_app, base_url="http://127.0.0.1") as test_client:
        yield test_client
