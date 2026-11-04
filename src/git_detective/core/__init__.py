"""Core logic: reading Git history and analyzing it."""

from git_detective.core.analyzer import aggregate_file_stats
from git_detective.core.git_reader import DEFAULT_COMMIT_LIMIT, GitReader
from git_detective.core.hotspots import (
    DEFAULT_RECENT_DAYS,
    DEFAULT_WEIGHTS,
    HotspotWeights,
    compute_hotspots,
)

__all__ = [
    "DEFAULT_COMMIT_LIMIT",
    "DEFAULT_RECENT_DAYS",
    "DEFAULT_WEIGHTS",
    "GitReader",
    "HotspotWeights",
    "aggregate_file_stats",
    "compute_hotspots",
]
