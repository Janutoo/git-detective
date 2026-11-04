"""Core logic: reading Git history and analyzing it."""

from git_detective.core.analyzer import aggregate_file_stats
from git_detective.core.git_reader import DEFAULT_COMMIT_LIMIT, GitReader

__all__ = ["DEFAULT_COMMIT_LIMIT", "GitReader", "aggregate_file_stats"]
