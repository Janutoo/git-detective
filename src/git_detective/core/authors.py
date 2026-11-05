"""Contributor statistics: who changed how much, where and when.

Authors are grouped by name - like ``git shortlog`` - after resolving aliases
with an optional :class:`~git_detective.core.mailmap.Mailmap`. Commits made
with different e-mail addresses but the same name therefore belong to one
author, and ``.mailmap`` can merge different spellings of a name.

Exclude patterns remove *files*, not commits: an author's commit count is not
affected by filtering, while churn and touched files are.

Day and month buckets use UTC, so results do not depend on the time zone
each author worked in. This module does not depend on Git or the CLI.
"""

from collections import Counter
from collections.abc import Iterable, Sequence
from datetime import UTC
from enum import StrEnum

from git_detective.core.analyzer import aggregate_file_stats
from git_detective.core.mailmap import Mailmap
from git_detective.models import AuthorStats, CommitInfo

DEFAULT_TOP_FILES = 3
UNKNOWN_AUTHOR = "(unknown)"


class AuthorSort(StrEnum):
    COMMITS = "commits"
    CHURN = "churn"


def compute_author_stats(
    commits: Iterable[CommitInfo],
    *,
    mailmap: Mailmap | None = None,
    sort_by: AuthorSort = AuthorSort.COMMITS,
    top_files: int = DEFAULT_TOP_FILES,
) -> list[AuthorStats]:
    """Aggregate commits per author and rank the authors.

    Args:
        commits: Commits to analyze (any order).
        mailmap: Optional alias mapping applied before grouping.
        sort_by: ``commits`` or ``churn``; ties fall back to the other metric
            and then to the author name, so the order is deterministic.
        top_files: How many of each author's most changed files to keep.
    """
    if top_files < 1:
        raise ValueError(f"top_files must be at least 1, got {top_files}")

    by_author: dict[str, list[CommitInfo]] = {}
    emails: dict[str, set[str]] = {}
    for commit in commits:
        name, email = commit.author_name, commit.author_email
        if mailmap is not None:
            name, email = mailmap.resolve(name, email)
        name = name.strip() or UNKNOWN_AUTHOR
        by_author.setdefault(name, []).append(commit)
        if email:
            emails.setdefault(name, set()).add(email.lower())

    authors = [
        _summarize(name, author_commits, emails.get(name, set()), top_files)
        for name, author_commits in by_author.items()
    ]
    if sort_by is AuthorSort.CHURN:
        authors.sort(key=lambda a: (-a.churn, -a.commits, a.name))
    else:
        authors.sort(key=lambda a: (-a.commits, -a.churn, a.name))
    return authors


def _summarize(
    name: str, commits: Sequence[CommitInfo], emails: set[str], top_files: int
) -> AuthorStats:
    file_stats = aggregate_file_stats(commits)
    dates = [commit.authored_at for commit in commits]
    utc_dates = [date.astimezone(UTC) for date in dates]
    months = Counter(date.strftime("%Y-%m") for date in utc_dates)

    return AuthorStats(
        name=name,
        emails=tuple(sorted(emails)),
        commits=len(commits),
        added=sum(s.added for s in file_stats),
        deleted=sum(s.deleted for s in file_stats),
        files_touched=len(file_stats),
        first_commit=min(dates),
        last_commit=max(dates),
        active_days=len({date.date() for date in utc_dates}),
        top_files=tuple(file_stats[:top_files]),
        activity_by_month=tuple(sorted(months.items())),
    )
