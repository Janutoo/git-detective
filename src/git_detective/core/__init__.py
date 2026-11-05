"""Core logic: reading Git history and analyzing it."""

from git_detective.core.analyzer import aggregate_file_stats
from git_detective.core.authors import (
    DEFAULT_TOP_FILES,
    AuthorSort,
    compute_author_stats,
)
from git_detective.core.filters import (
    IGNORE_FILE_NAME,
    PathFilter,
    filter_commits,
    read_ignore_file,
)
from git_detective.core.git_reader import DEFAULT_COMMIT_LIMIT, GitReader
from git_detective.core.hotspots import (
    DEFAULT_RECENT_DAYS,
    DEFAULT_WEIGHTS,
    HotspotWeights,
    compute_hotspots,
)
from git_detective.core.mailmap import MAILMAP_FILE_NAME, Mailmap, read_mailmap

__all__ = [
    "DEFAULT_COMMIT_LIMIT",
    "DEFAULT_RECENT_DAYS",
    "DEFAULT_TOP_FILES",
    "DEFAULT_WEIGHTS",
    "IGNORE_FILE_NAME",
    "MAILMAP_FILE_NAME",
    "AuthorSort",
    "GitReader",
    "HotspotWeights",
    "Mailmap",
    "PathFilter",
    "aggregate_file_stats",
    "compute_author_stats",
    "compute_hotspots",
    "filter_commits",
    "read_ignore_file",
    "read_mailmap",
]
