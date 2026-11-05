"""Tests for contributor statistics. No Git repository is needed here."""

from datetime import UTC, datetime, timedelta, timezone

import pytest

from git_detective.core.authors import AuthorSort, compute_author_stats
from git_detective.core.filters import PathFilter, filter_commits
from git_detective.core.mailmap import Mailmap
from git_detective.models import CommitInfo, FileChange, FileStats

DAY = datetime(2024, 6, 10, 12, 0, tzinfo=UTC)

_sha_counter = 0


def commit(
    author: str,
    *changes: tuple[str, int, int],
    email: str | None = None,
    when: datetime = DAY,
) -> CommitInfo:
    global _sha_counter
    _sha_counter += 1
    return CommitInfo(
        hexsha=f"{_sha_counter:040x}",
        author_name=author,
        author_email=email if email is not None else f"{author.lower()}@example.com",
        authored_at=when,
        message="msg",
        files=tuple(FileChange(p, a, d) for p, a, d in changes),
    )


def by_name(commits: list[CommitInfo], **kwargs: object) -> dict:
    return {a.name: a for a in compute_author_stats(commits, **kwargs)}


class TestAggregation:
    def test_counts_commits_churn_and_files_per_author(self) -> None:
        commits = [
            commit("Alice", ("a.py", 10, 2), ("b.py", 1, 0)),
            commit("Alice", ("a.py", 3, 3)),
            commit("Bob", ("c.py", 5, 0)),
        ]

        authors = by_name(commits)

        alice = authors["Alice"]
        assert (alice.commits, alice.added, alice.deleted) == (2, 14, 5)
        assert alice.churn == 19
        assert alice.files_touched == 2
        assert authors["Bob"].commits == 1
        assert authors["Bob"].files_touched == 1

    def test_top_files_reuse_file_stats_ordering(self) -> None:
        commits = [
            commit("Alice", ("often.py", 1, 0), ("big.py", 100, 0)),
            commit("Alice", ("often.py", 1, 0), ("small.py", 1, 0)),
            commit("Alice", ("often.py", 1, 0)),
            commit("Alice", ("rare.py", 1, 0)),
        ]

        (alice,) = compute_author_stats(commits, top_files=3)

        assert alice.top_files == (
            FileStats("often.py", commits=3, added=3, deleted=0),
            FileStats("big.py", commits=1, added=100, deleted=0),
            FileStats("rare.py", commits=1, added=1, deleted=0),
        )

    def test_top_files_limit(self) -> None:
        commits = [commit("Alice", *[(f"f{i}.py", 1, 0) for i in range(10)])]

        (alice,) = compute_author_stats(commits, top_files=2)

        assert len(alice.top_files) == 2
        assert alice.files_touched == 10

    def test_empty_input(self) -> None:
        assert compute_author_stats([]) == []

    def test_commit_without_files_still_counts(self) -> None:
        (alice,) = compute_author_stats([commit("Alice")])

        assert alice.commits == 1
        assert alice.churn == 0
        assert alice.files_touched == 0
        assert alice.top_files == ()

    def test_rejects_invalid_top_files(self) -> None:
        with pytest.raises(ValueError, match="top_files"):
            compute_author_stats([commit("Alice")], top_files=0)


class TestIdentity:
    def test_same_name_different_emails_is_one_author(self) -> None:
        commits = [
            commit("Alice", ("a.py", 1, 0), email="alice@work.com"),
            commit("Alice", ("a.py", 1, 0), email="Alice@Home.org"),
        ]

        (alice,) = compute_author_stats(commits)

        assert alice.commits == 2
        assert alice.emails == ("alice@home.org", "alice@work.com")

    def test_mailmap_merges_aliases(self) -> None:
        mailmap = Mailmap.parse(
            "Alice Smith <alice@work.com> <alice@home.org>\n"
            "Alice Smith <alice@work.com> asmith <ci@build.local>\n"
        )
        commits = [
            commit("Alice Smith", ("a.py", 1, 0), email="alice@work.com"),
            commit("alice", ("b.py", 1, 0), email="alice@home.org"),
            commit("asmith", ("c.py", 1, 0), email="ci@build.local"),
            commit("ci-bot", ("d.py", 1, 0), email="ci@build.local"),
        ]

        authors = by_name(commits, mailmap=mailmap)

        assert set(authors) == {"Alice Smith", "ci-bot"}
        assert authors["Alice Smith"].commits == 3
        assert authors["Alice Smith"].emails == ("alice@work.com",)

    def test_without_mailmap_aliases_stay_separate(self) -> None:
        commits = [
            commit("Alice Smith", ("a.py", 1, 0)),
            commit("alice", ("a.py", 1, 0)),
        ]

        assert set(by_name(commits)) == {"Alice Smith", "alice"}

    def test_blank_name_becomes_unknown(self) -> None:
        (author,) = compute_author_stats([commit("  ", ("a.py", 1, 0), email="")])

        assert author.name == "(unknown)"
        assert author.emails == ()


class TestActivityOverTime:
    def test_first_last_and_active_days(self) -> None:
        commits = [
            commit("Alice", ("a.py", 1, 0), when=DAY),
            commit("Alice", ("a.py", 1, 0), when=DAY + timedelta(hours=3)),
            commit("Alice", ("a.py", 1, 0), when=DAY - timedelta(days=20)),
        ]

        (alice,) = compute_author_stats(commits)

        assert alice.first_commit == DAY - timedelta(days=20)
        assert alice.last_commit == DAY + timedelta(hours=3)
        assert alice.active_days == 2

    def test_activity_by_month_is_chronological(self) -> None:
        commits = [
            commit("Alice", when=datetime(2024, 3, 5, tzinfo=UTC)),
            commit("Alice", when=datetime(2024, 1, 5, tzinfo=UTC)),
            commit("Alice", when=datetime(2024, 3, 20, tzinfo=UTC)),
        ]

        (alice,) = compute_author_stats(commits)

        assert alice.activity_by_month == (("2024-01", 1), ("2024-03", 2))

    def test_days_and_months_use_utc(self) -> None:
        # 23:30 on Oct 31 at UTC-2 is already Nov 1 in UTC.
        late_evening = datetime(
            2024, 10, 31, 23, 30, tzinfo=timezone(timedelta(hours=-2))
        )
        commits = [
            commit("Alice", when=late_evening),
            commit("Alice", when=datetime(2024, 11, 1, 9, 0, tzinfo=UTC)),
        ]

        (alice,) = compute_author_stats(commits)

        assert alice.activity_by_month == (("2024-11", 2),)
        assert alice.active_days == 1
        assert alice.first_commit == late_evening  # original offset is kept


class TestSorting:
    @pytest.fixture
    def commits(self) -> list[CommitInfo]:
        return [
            commit("Busy", ("a.py", 1, 0)),
            commit("Busy", ("a.py", 1, 0)),
            commit("Busy", ("a.py", 1, 0)),
            commit("Heavy", ("big.py", 500, 100)),
        ]

    def test_sort_by_commits(self, commits: list[CommitInfo]) -> None:
        ranked = compute_author_stats(commits, sort_by=AuthorSort.COMMITS)

        assert [a.name for a in ranked] == ["Busy", "Heavy"]

    def test_sort_by_churn(self, commits: list[CommitInfo]) -> None:
        ranked = compute_author_stats(commits, sort_by=AuthorSort.CHURN)

        assert [a.name for a in ranked] == ["Heavy", "Busy"]

    def test_ties_are_broken_by_name(self) -> None:
        commits = [commit(name, ("a.py", 1, 0)) for name in ("Carol", "alice", "Bob")]

        ranked = compute_author_stats(commits)

        assert [a.name for a in ranked] == ["Bob", "Carol", "alice"]

    def test_input_order_does_not_matter(self, commits: list[CommitInfo]) -> None:
        assert compute_author_stats(commits) == compute_author_stats(commits[::-1])


def test_filtering_changes_churn_but_not_commit_count() -> None:
    commits = [
        commit("Alice", ("poetry.lock", 900, 100), ("app.py", 5, 0)),
        commit("Alice", ("poetry.lock", 50, 50)),
    ]

    (before,) = compute_author_stats(commits)
    (after,) = compute_author_stats(filter_commits(commits, PathFilter(["*.lock"])))

    assert (before.commits, before.churn, before.files_touched) == (2, 1105, 2)
    assert (after.commits, after.churn, after.files_touched) == (2, 5, 1)
