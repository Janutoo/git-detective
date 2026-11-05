"""Per-author activity statistics."""

from dataclasses import dataclass
from datetime import datetime

from git_detective.models.file_stats import FileStats


@dataclass(frozen=True, slots=True)
class AuthorStats:
    """What one contributor changed in the analyzed commits, and when.

    These numbers describe *activity*; they say nothing about productivity
    or the value of anyone's work.
    """

    name: str
    emails: tuple[str, ...]  # personal data: only shown when explicitly requested
    commits: int
    added: int
    deleted: int
    files_touched: int
    first_commit: datetime
    last_commit: datetime
    active_days: int  # distinct calendar days (UTC) with at least one commit
    top_files: tuple[FileStats, ...]  # the author's most frequently changed files
    activity_by_month: tuple[tuple[str, int], ...]  # (("2026-09", 4), ...) UTC

    @property
    def churn(self) -> int:
        """Total number of changed lines (added + deleted)."""
        return self.added + self.deleted
