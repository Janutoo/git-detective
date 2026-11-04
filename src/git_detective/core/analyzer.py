"""Aggregate raw commit data into per-file statistics.

This module is pure Python: it knows nothing about Git, which keeps the
analysis logic easy to test and reuse with data from other sources.
"""

from collections.abc import Iterable
from dataclasses import dataclass

from git_detective.models import CommitInfo, FileStats


@dataclass(slots=True)
class _Counter:
    commits: int = 0
    added: int = 0
    deleted: int = 0


def aggregate_file_stats(commits: Iterable[CommitInfo]) -> list[FileStats]:
    """Count commits and churn for every file touched by ``commits``.

    The result is sorted by change frequency (most commits first), then by
    churn, then alphabetically by path so the order is deterministic.
    """
    counters: dict[str, _Counter] = {}
    for commit in commits:
        for change in commit.files:
            counter = counters.setdefault(change.path, _Counter())
            counter.commits += 1
            counter.added += change.added
            counter.deleted += change.deleted

    stats = [
        FileStats(path=path, commits=c.commits, added=c.added, deleted=c.deleted)
        for path, c in counters.items()
    ]
    stats.sort(key=lambda s: (-s.commits, -s.churn, s.path))
    return stats
