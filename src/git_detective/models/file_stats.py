"""Aggregated per-file statistics across many commits."""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class FileStats:
    """How often and how heavily a file was changed in the analyzed history."""

    path: str
    commits: int
    added: int
    deleted: int

    @property
    def churn(self) -> int:
        """Total number of changed lines (added + deleted)."""
        return self.added + self.deleted
