"""The repository allowlist: ids, paths and symlinks."""

import os
import shutil
from collections.abc import Callable
from pathlib import Path

import pytest
from git import Repo

from git_detective.api.registry import (
    RepositoryNotFoundError,
    RepositoryRegistry,
    RepositoryUnavailableError,
)


class TestAllowlist:
    def test_resolves_an_allowed_repository(self, repo_path: Path) -> None:
        registry = RepositoryRegistry({"demo": repo_path})

        assert registry.resolve("demo") == Path(os.path.realpath(repo_path))
        assert registry.is_available("demo")

    @pytest.mark.parametrize(
        "repository_id", ["other", "", "../demo", "/etc/passwd", "demo/"]
    )
    def test_refuses_anything_else(self, repo_path: Path, repository_id: str) -> None:
        registry = RepositoryRegistry({"demo": repo_path})

        with pytest.raises(RepositoryNotFoundError):
            registry.resolve(repository_id)

    def test_id_is_never_used_as_a_path(self, tmp_path: Path, repo_path: Path) -> None:
        Repo.init(tmp_path / "other").close()
        registry = RepositoryRegistry({"demo": repo_path})

        with pytest.raises(RepositoryNotFoundError):
            registry.resolve(str(tmp_path / "other"))

    def test_ids_are_sorted(self, repo_path: Path) -> None:
        registry = RepositoryRegistry({"b": repo_path, "a": repo_path})

        assert registry.ids() == ["a", "b"]

    def test_unknown_id_raises_from_is_available(self, repo_path: Path) -> None:
        registry = RepositoryRegistry({"demo": repo_path})

        with pytest.raises(RepositoryNotFoundError):
            registry.is_available("other")


class TestPathChecks:
    def test_directory_removed_after_startup(self, repo_path: Path) -> None:
        registry = RepositoryRegistry({"demo": repo_path})
        shutil.rmtree(repo_path)

        with pytest.raises(RepositoryUnavailableError):
            registry.resolve("demo")
        assert not registry.is_available("demo")

    def test_directory_missing_at_startup_and_created_later(
        self, tmp_path: Path
    ) -> None:
        path = tmp_path / "later"
        registry = RepositoryRegistry({"demo": path})
        assert not registry.is_available("demo")

        Repo.init(path).close()

        assert registry.is_available("demo")

    def test_symlink_to_repository_is_pinned_to_its_target(
        self, tmp_path: Path, repo_path: Path
    ) -> None:
        link = tmp_path / "link"
        link.symlink_to(repo_path)

        registry = RepositoryRegistry({"demo": link})

        assert registry.resolve("demo") == Path(os.path.realpath(repo_path))

    def test_symlink_retargeted_after_startup(
        self, tmp_path: Path, repo_path: Path
    ) -> None:
        other = tmp_path / "other"
        Repo.init(other).close()
        link = tmp_path / "link"
        link.symlink_to(repo_path)
        registry = RepositoryRegistry({"demo": link})

        link.unlink()
        link.symlink_to(other)

        with pytest.raises(RepositoryUnavailableError, match="now resolves"):
            registry.resolve("demo")

    def test_directory_replaced_by_symlink_after_startup(
        self, tmp_path: Path, repo_path: Path
    ) -> None:
        other = tmp_path / "other"
        Repo.init(other).close()
        registry = RepositoryRegistry({"demo": repo_path})

        shutil.rmtree(repo_path)
        repo_path.symlink_to(other)

        with pytest.raises(RepositoryUnavailableError, match="now resolves"):
            registry.resolve("demo")

    def test_symlink_loop(self, tmp_path: Path) -> None:
        link = tmp_path / "loop"
        link.symlink_to(link)

        registry = RepositoryRegistry({"demo": link})

        with pytest.raises(RepositoryUnavailableError):
            registry.resolve("demo")


class TestGitDirectory:
    def test_subdirectory_of_a_repository_is_refused(
        self, repo_path: Path, make_commit: Callable[..., str]
    ) -> None:
        make_commit({"sub/file.txt": "x\n"})
        registry = RepositoryRegistry({"demo": repo_path / "sub"})

        with pytest.raises(RepositoryUnavailableError):
            registry.resolve("demo")

    def test_plain_directory_is_refused(self, tmp_path: Path) -> None:
        registry = RepositoryRegistry({"demo": tmp_path})

        with pytest.raises(RepositoryUnavailableError):
            registry.resolve("demo")

    def test_git_file_pointing_elsewhere_is_refused(
        self, tmp_path: Path, repo_path: Path
    ) -> None:
        worktree = tmp_path / "worktree"
        worktree.mkdir()
        (worktree / ".git").write_text(
            f"gitdir: {repo_path / '.git'}\n", encoding="utf-8"
        )
        registry = RepositoryRegistry({"demo": worktree})

        with pytest.raises(RepositoryUnavailableError, match="not a directory"):
            registry.resolve("demo")

    def test_git_symlink_is_refused(self, tmp_path: Path, repo_path: Path) -> None:
        linked = tmp_path / "linked"
        linked.mkdir()
        (linked / ".git").symlink_to(repo_path / ".git")
        registry = RepositoryRegistry({"demo": linked})

        with pytest.raises(RepositoryUnavailableError, match="not a directory"):
            registry.resolve("demo")
