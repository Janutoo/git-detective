from datetime import UTC, datetime

from git_detective.core.analyzer import aggregate_file_stats
from git_detective.models import CommitInfo, FileChange, FileStats


def make_commit_info(sha: str, *changes: FileChange) -> CommitInfo:
    return CommitInfo(
        hexsha=sha * 40,
        author_name="Alice",
        author_email="alice@example.com",
        authored_at=datetime(2024, 1, 1, tzinfo=UTC),
        message="msg",
        files=changes,
    )


def test_aggregates_commits_and_churn_per_file() -> None:
    commits = [
        make_commit_info(
            "a", FileChange("app.py", 10, 2), FileChange("README.md", 1, 0)
        ),
        make_commit_info("b", FileChange("app.py", 3, 5)),
    ]

    stats = aggregate_file_stats(commits)

    assert stats == [
        FileStats(path="app.py", commits=2, added=13, deleted=7),
        FileStats(path="README.md", commits=1, added=1, deleted=0),
    ]
    assert stats[0].churn == 20


def test_sorts_by_commits_then_churn_then_path() -> None:
    commits = [
        make_commit_info("a", FileChange("b.py", 1, 0), FileChange("big.py", 50, 0)),
        make_commit_info("b", FileChange("a.py", 1, 0), FileChange("often.py", 1, 0)),
        make_commit_info("c", FileChange("often.py", 1, 0)),
    ]

    paths = [s.path for s in aggregate_file_stats(commits)]

    # often.py: most commits; big.py: highest churn among 1-commit files;
    # a.py and b.py tie on everything and fall back to alphabetical order.
    assert paths == ["often.py", "big.py", "a.py", "b.py"]


def test_no_commits_gives_no_stats() -> None:
    assert aggregate_file_stats([]) == []
