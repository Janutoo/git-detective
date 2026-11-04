"""Plain data models shared across Git Detective layers."""

from git_detective.models.commit import CommitInfo, FileChange
from git_detective.models.file_stats import FileStats
from git_detective.models.hotspot import Hotspot

__all__ = ["CommitInfo", "FileChange", "FileStats", "Hotspot"]
