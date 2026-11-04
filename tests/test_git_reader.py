from collections.abc import Callable
from pathlib import Path

import pytest
from git import Actor, Repo

from git_detective.core.git_reader import GitReader, parse_numstat
from git_detective.exceptions import InvalidRepositoryError
from git_detective.models import CommitInfo, FileChange

CommitFn = Callable[..., str]


def read(repo: Repo, limit: int = 100) -> list[CommitInfo]:
    with GitReader(repo.working_tree_dir) as reader:
        return reader.read_commits(limit=limit)


class TestReadCommits:
    def test_reads_commit_metadata(self, repo: Repo, make_commit: CommitFn) -> None:
        author = Actor("Bob Builder", "bob@example.com")
        sha = make_commit(
            {"README.md": "hello\n"},
            message="Initial commit\n\nLonger body.",
            author=author,
        )

        commits = read(repo)

        assert len(commits) == 1
        commit = commits[0]
        assert commit.hexsha == sha
        assert len(commit.hexsha) == 40
        assert commit.short_sha == sha[:7]
        assert commit.author_name == "Bob Builder"
        assert commit.author_email == "bob@example.com"
        assert commit.message == "Initial commit\n\nLonger body."
        assert commit.summary == "Initial commit"
        assert commit.authored_at.tzinfo is not None

    def test_commits_are_ordered_newest_first(
        self, repo: Repo, make_commit: CommitFn
    ) -> None:
        first = make_commit({"a.txt": "1\n"}, message="first")
        second = make_commit({"a.txt": "2\n"}, message="second")

        commits = read(repo)

        assert [c.hexsha for c in commits] == [second, first]

    def test_respects_limit(self, repo: Repo, make_commit: CommitFn) -> None:
        shas = [make_commit({"a.txt": f"{i}\n"}, message=f"c{i}") for i in range(5)]

        commits = read(repo, limit=2)

        assert [c.hexsha for c in commits] == [shas[4], shas[3]]

    def test_limit_larger_than_history_returns_all(
        self, repo: Repo, make_commit: CommitFn
    ) -> None:
        make_commit({"a.txt": "1\n"})
        make_commit({"a.txt": "2\n"})

        assert len(read(repo, limit=100)) == 2

    @pytest.mark.parametrize("limit", [0, -1])
    def test_rejects_non_positive_limit(
        self, repo: Repo, make_commit: CommitFn, limit: int
    ) -> None:
        make_commit({"a.txt": "1\n"})

        with pytest.raises(ValueError):
            read(repo, limit=limit)


class TestFileChanges:
    def test_initial_commit_counts_added_lines(
        self, repo: Repo, make_commit: CommitFn
    ) -> None:
        make_commit({"src/app.py": "a\nb\nc\n", "README.md": "hi\n"})

        (commit,) = read(repo)

        assert sorted(commit.files, key=lambda f: f.path) == [
            FileChange(path="README.md", added=1, deleted=0),
            FileChange(path="src/app.py", added=3, deleted=0),
        ]

    def test_modification_counts_added_and_deleted_lines(
        self, repo: Repo, make_commit: CommitFn
    ) -> None:
        make_commit({"app.py": "a\nb\nc\n"})
        make_commit({"app.py": "a\nB\nc\nd\n"})  # 1 line changed, 1 line added

        latest = read(repo)[0]

        assert latest.files == (FileChange(path="app.py", added=2, deleted=1),)
        assert latest.files[0].churn == 3

    def test_only_changed_files_are_listed(
        self, repo: Repo, make_commit: CommitFn
    ) -> None:
        make_commit({"a.txt": "1\n", "b.txt": "1\n"})
        make_commit({"b.txt": "2\n"})

        latest = read(repo)[0]

        assert [f.path for f in latest.files] == ["b.txt"]

    def test_deleted_file_counts_removed_lines(
        self, repo: Repo, make_commit: CommitFn
    ) -> None:
        make_commit({"old.txt": "1\n2\n"})
        make_commit({"old.txt": None})

        latest = read(repo)[0]

        assert latest.files == (FileChange(path="old.txt", added=0, deleted=2),)

    def test_binary_file_counts_as_zero_lines(
        self, repo: Repo, make_commit: CommitFn
    ) -> None:
        make_commit({"logo.png": b"\x89PNG\x00\x01\x02"})

        (commit,) = read(repo)

        assert commit.files == (FileChange(path="logo.png", added=0, deleted=0),)

    def test_path_with_spaces_and_unicode(
        self, repo: Repo, make_commit: CommitFn
    ) -> None:
        make_commit({"docs/zażółć gęślą.md": "x\n"})

        (commit,) = read(repo)

        assert commit.files[0].path == "docs/zażółć gęślą.md"


class TestRepositoryErrors:
    def test_empty_repository_returns_no_commits(self, repo: Repo) -> None:
        assert read(repo) == []

    def test_nonexistent_path_raises(self, tmp_path: Path) -> None:
        with pytest.raises(InvalidRepositoryError, match="does not exist"):
            GitReader(tmp_path / "missing")

    def test_directory_without_git_raises(self, tmp_path: Path) -> None:
        plain_dir = tmp_path / "plain"
        plain_dir.mkdir()

        with pytest.raises(InvalidRepositoryError, match="Not a Git repository"):
            GitReader(plain_dir)

    def test_subdirectory_of_repository_is_accepted(
        self, repo: Repo, make_commit: CommitFn
    ) -> None:
        make_commit({"src/app.py": "x\n"})
        subdir = Path(repo.working_tree_dir) / "src"

        with GitReader(subdir) as reader:
            assert reader.root == Path(repo.working_tree_dir)
            assert len(reader.read_commits()) == 1


def test_parse_numstat_handles_empty_output() -> None:
    assert parse_numstat("") == ()
