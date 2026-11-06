"""GitReader additions: HEAD hash, explicit revision and the lazy fetch switch."""

import subprocess
from collections.abc import Callable
from pathlib import Path

import pytest
from git import Repo

from git_detective.core import GitReader
from git_detective.exceptions import GitReadError


class TestHeadSha:
    def test_empty_repository_has_no_head(self, repo_path: Path) -> None:
        with GitReader(repo_path) as reader:
            assert reader.head_sha is None

    def test_returns_full_hash_of_head(
        self, repo_path: Path, make_commit: Callable[..., str]
    ) -> None:
        make_commit({"a.txt": "1\n"})
        last = make_commit({"a.txt": "2\n"})

        with GitReader(repo_path) as reader:
            assert reader.head_sha == last


class TestReadCommitsFromRevision:
    def test_default_is_head(
        self, repo_path: Path, make_commit: Callable[..., str]
    ) -> None:
        shas = [make_commit({"a.txt": str(i)}) for i in range(3)]

        with GitReader(repo_path) as reader:
            commits = reader.read_commits()
        assert [c.hexsha for c in commits] == shas[::-1]

    def test_walks_from_the_given_revision(
        self, repo_path: Path, make_commit: Callable[..., str]
    ) -> None:
        first = make_commit({"a.txt": "1\n"})
        second = make_commit({"a.txt": "2\n"})
        make_commit({"a.txt": "3\n"})

        with GitReader(repo_path) as reader:
            commits = reader.read_commits(rev=second)
        assert [c.hexsha for c in commits] == [second, first]

    def test_limit_stays_a_positional_argument(
        self, repo_path: Path, make_commit: Callable[..., str]
    ) -> None:
        make_commit({"a.txt": "1\n"})
        last = make_commit({"a.txt": "2\n"})

        with GitReader(repo_path) as reader:
            commits = reader.read_commits(1)
        assert [c.hexsha for c in commits] == [last]


class TestRepositoryConfigCannotRunCommands:
    def test_fsmonitor_is_not_started(
        self,
        repo: Repo,
        repo_path: Path,
        make_commit: Callable[..., str],
        tmp_path: Path,
    ) -> None:
        make_commit({"a.txt": "1\n"})
        sentinel = tmp_path / "fired"
        with repo.config_writer() as config:
            config.set_value("core", "fsmonitor", f"touch '{sentinel}'")

        with GitReader(repo_path) as reader:
            commits = reader.read_commits()

        assert commits[0].files[0].path == "a.txt"
        assert not sentinel.exists()


class TestLazyFetch:
    @pytest.fixture
    def partial_clone(
        self, repo: Repo, make_commit: Callable[..., str], tmp_path: Path
    ) -> Path:
        """A clone that has commits and trees but no file contents."""
        make_commit({"a.txt": "1\n2\n"})
        repo.git.config("uploadpack.allowFilter", "true")
        clone = tmp_path / "partial"
        subprocess.run(
            [
                "git",
                "clone",
                "--quiet",
                "--no-checkout",
                "--filter=blob:none",
                f"file://{repo.working_tree_dir}",
                str(clone),
            ],
            check=True,
        )
        return clone

    def test_disabled_fails_instead_of_downloading(self, partial_clone: Path) -> None:
        with (
            GitReader(partial_clone, allow_lazy_fetch=False) as reader,
            pytest.raises(GitReadError),
        ):
            reader.read_commits()

    def test_enabled_by_default(self, partial_clone: Path) -> None:
        with GitReader(partial_clone) as reader:
            commits = reader.read_commits()
        assert commits[0].files[0].added == 2
