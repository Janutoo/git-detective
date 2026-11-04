"""Data models describing commits and the files they touch."""

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True, slots=True)
class FileChange:
    """Line changes made to a single file in a single commit."""

    path: str
    added: int
    deleted: int

    @property
    def churn(self) -> int:
        """Total number of changed lines (added + deleted)."""
        return self.added + self.deleted


@dataclass(frozen=True, slots=True)
class CommitInfo:
    """A single commit together with the files it changed."""

    hexsha: str
    author_name: str
    author_email: str
    authored_at: datetime
    message: str
    files: tuple[FileChange, ...] = ()

    @property
    def short_sha(self) -> str:
        """Abbreviated commit hash, as shown by ``git log --oneline``."""
        return self.hexsha[:7]

    @property
    def summary(self) -> str:
        """First line of the commit message."""
        return self.message.split("\n", 1)[0]
