"""Tests for file coupling. No Git repository is needed here."""

from datetime import UTC, datetime

import pytest

from git_detective.core.coupling import compute_coupling
from git_detective.core.filters import PathFilter, filter_commits
from git_detective.models import CommitInfo, FileChange, FileCoupling

WHEN = datetime(2024, 6, 1, tzinfo=UTC)

_sha_counter = 0


def commit(*paths: str) -> CommitInfo:
    global _sha_counter
    _sha_counter += 1
    return CommitInfo(
        hexsha=f"{_sha_counter:040x}",
        author_name="Alice",
        author_email="alice@example.com",
        authored_at=WHEN,
        message="msg",
        files=tuple(FileChange(path, 1, 0) for path in paths),
    )


def pairs(
    commits: list[CommitInfo], **kwargs: int
) -> dict[tuple[str, str], FileCoupling]:
    result = compute_coupling(commits, **kwargs)
    return {(p.file_a, p.file_b): p for p in result.pairs}


class TestCounting:
    def test_counts_cochanges_and_per_file_commits(self) -> None:
        commits = [
            commit("a.py", "b.py"),
            commit("a.py", "b.py", "c.py"),
            commit("a.py"),
        ]

        result = pairs(commits, min_cochanges=1)

        assert result[("a.py", "b.py")] == FileCoupling(
            "a.py", "b.py", cochanges=2, commits_a=3, commits_b=2
        )
        assert result[("a.py", "c.py")].cochanges == 1
        assert result[("b.py", "c.py")].cochanges == 1
        assert len(result) == 3

    def test_jaccard(self) -> None:
        commits = [commit("a.py", "b.py")] * 2 + [commit("a.py")] * 3 + [commit("b.py")]

        pair = pairs(commits, min_cochanges=1)[("a.py", "b.py")]

        # 2 together / (5 with a + 3 with b - 2 together) = 2 / 6
        assert pair.jaccard == pytest.approx(1 / 3)

    def test_always_together_has_jaccard_one(self) -> None:
        commits = [commit("x.py", "y.py")] * 3

        assert pairs(commits)[("x.py", "y.py")].jaccard == 1.0

    def test_pair_uses_canonical_order(self) -> None:
        result = pairs([commit("z.py", "a.py")], min_cochanges=1)

        assert list(result) == [("a.py", "z.py")]

    def test_duplicate_paths_in_a_commit_count_once(self) -> None:
        result = pairs([commit("a.py", "a.py", "b.py")], min_cochanges=1)

        assert result[("a.py", "b.py")].commits_a == 1
        assert ("a.py", "a.py") not in result

    def test_single_file_commits_create_no_pairs(self) -> None:
        result = compute_coupling([commit("a.py"), commit("b.py")], min_cochanges=1)

        assert result.pairs == ()
        assert result.commits_considered == 2

    def test_empty_input(self) -> None:
        result = compute_coupling([])

        assert result.pairs == ()
        assert (result.commits_considered, result.commits_skipped) == (0, 0)

    def test_commit_without_files(self) -> None:
        result = compute_coupling([commit()], min_cochanges=1)

        assert result.pairs == ()
        assert result.commits_considered == 1


class TestMinCochanges:
    def test_threshold_is_inclusive(self) -> None:
        commits = [commit("a.py", "b.py")] * 3 + [commit("c.py", "d.py")] * 2

        result = pairs(commits, min_cochanges=3)

        assert list(result) == [("a.py", "b.py")]

    def test_default_threshold_is_three(self) -> None:
        commits = [commit("a.py", "b.py")] * 2

        assert compute_coupling(commits).pairs == ()


class TestLargeCommits:
    def test_large_commit_is_skipped_entirely(self) -> None:
        big = commit(*[f"f{i}.py" for i in range(10)], "a.py", "b.py")
        commits = [commit("a.py", "b.py")] * 3 + [big]

        result = compute_coupling(commits, max_files_per_commit=5)
        (pair,) = result.pairs

        assert (result.commits_considered, result.commits_skipped) == (3, 1)
        # The skipped commit counts neither as a co-change nor per file.
        assert (pair.cochanges, pair.commits_a, pair.commits_b) == (3, 3, 3)
        assert pair.jaccard == 1.0

    def test_commit_at_the_limit_is_considered(self) -> None:
        commits = [commit("a.py", "b.py", "c.py")] * 3

        result = compute_coupling(commits, max_files_per_commit=3)

        assert result.commits_skipped == 0
        assert len(result.pairs) == 3

    def test_limit_counts_unique_files(self) -> None:
        commits = [commit("a.py", "a.py", "a.py", "b.py")] * 3

        result = compute_coupling(commits, max_files_per_commit=2)

        assert result.commits_skipped == 0

    def test_without_limit_bulk_commits_link_everything(self) -> None:
        bulk = [commit(*[f"f{i}.py" for i in range(6)]) for _ in range(3)]

        noisy = compute_coupling(bulk, max_files_per_commit=100)
        filtered = compute_coupling(bulk, max_files_per_commit=5)

        assert len(noisy.pairs) == 15  # 6 choose 2
        assert filtered.pairs == ()


class TestRanking:
    def test_ranked_by_jaccard_then_cochanges_then_path(self) -> None:
        commits = [
            *[commit("strong_a.py", "strong_b.py")] * 3,  # jaccard 1.0, 3x
            *[commit("big_a.py", "big_b.py")] * 5,  # jaccard 1.0, 5x
            *[commit("weak_a.py", "weak_b.py")] * 3,
            *[commit("weak_a.py")] * 3,  # jaccard 0.5
            *[commit("tie_b.py", "tie_c.py")] * 3,  # same as strong, path order
        ]

        result = compute_coupling(commits)

        assert [(p.file_a, p.file_b) for p in result.pairs] == [
            ("big_a.py", "big_b.py"),
            ("strong_a.py", "strong_b.py"),
            ("tie_b.py", "tie_c.py"),
            ("weak_a.py", "weak_b.py"),
        ]

    def test_input_order_does_not_matter(self) -> None:
        commits = [
            commit("a.py", "b.py", "c.py"),
            commit("a.py", "b.py"),
            commit("b.py", "c.py"),
            commit("c.py", "a.py"),
        ]

        forward = compute_coupling(commits, min_cochanges=1)
        backward = compute_coupling(commits[::-1], min_cochanges=1)

        assert forward == backward


class TestValidation:
    @pytest.mark.parametrize("value", [0, -1])
    def test_min_cochanges(self, value: int) -> None:
        with pytest.raises(ValueError, match="min_cochanges"):
            compute_coupling([], min_cochanges=value)

    @pytest.mark.parametrize("value", [1, 0])
    def test_max_files_per_commit(self, value: int) -> None:
        with pytest.raises(ValueError, match="max_files_per_commit"):
            compute_coupling([], max_files_per_commit=value)


def test_filtering_changes_coupling() -> None:
    commits = [commit("app.py", "poetry.lock")] * 3 + [commit("app.py", "util.py")] * 3
    commits += [commit("poetry.lock")] * 2

    before = pairs(commits)
    after = pairs(filter_commits(commits, PathFilter(["*.lock"])))

    assert ("app.py", "poetry.lock") in before
    assert list(after) == [("app.py", "util.py")]
    # Filtering removes files, not commits: app.py still has 6 commits.
    assert after[("app.py", "util.py")].jaccard == before[("app.py", "util.py")].jaccard


def test_filtering_can_bring_a_commit_under_the_size_limit() -> None:
    generated = [f"gen/{i}.js" for i in range(4)]
    commits = [commit("api.py", "client.py", *generated)] * 3

    before = compute_coupling(commits, max_files_per_commit=3)
    after = compute_coupling(
        filter_commits(commits, PathFilter(["gen/"])), max_files_per_commit=3
    )

    assert (before.commits_skipped, before.pairs) == (3, ())
    assert after.commits_skipped == 0
    assert [(p.file_a, p.file_b) for p in after.pairs] == [("api.py", "client.py")]
