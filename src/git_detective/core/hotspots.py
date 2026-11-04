"""Rank files by Hotspot Score - how actively and heavily they change.

For every file touched by the analyzed commits we take three raw metrics:

* **F** - commit frequency: number of commits that touched the file,
* **C** - code churn: added + deleted lines,
* **R** - recent activity: commits within ``recent_days`` before a reference time.

Each metric is normalized to 0-1 with :func:`normalize_log` and combined::

    score = 100 * (w_f * F + w_c * C + w_r * R)

Log normalization keeps one huge file (e.g. a generated file with 50k lines of
churn) from squashing every other file's metric to almost zero.

The score is *relative*: it is normalized against the most active file in the
same data set, so it is not comparable between repositories or runs with a
different commit limit. It measures concentration of change activity and is
not a probability of defects.

This module is pure Python and does not depend on Git or the CLI.
"""

import math
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta

from git_detective.core.analyzer import aggregate_file_stats
from git_detective.models import CommitInfo, Hotspot

DEFAULT_RECENT_DAYS = 30


@dataclass(frozen=True, slots=True)
class HotspotWeights:
    """Relative importance of each metric. Must be non-negative and sum to 1."""

    frequency: float = 0.45
    churn: float = 0.35
    recency: float = 0.20

    def __post_init__(self) -> None:
        weights = (self.frequency, self.churn, self.recency)
        if any(w < 0 for w in weights):
            raise ValueError(f"Weights must be non-negative, got {weights}")
        if not math.isclose(sum(weights), 1.0, abs_tol=1e-9):
            raise ValueError(f"Weights must sum to 1, got {sum(weights):.6g}")


DEFAULT_WEIGHTS = HotspotWeights()


def normalize_log(values: Sequence[int]) -> list[float]:
    """Scale non-negative values to 0-1 using ``log1p(x) / log1p(max)``.

    The largest value maps to 1.0 and zero maps to 0.0. Compared with plain
    ``x / max`` this compresses large outliers, e.g. for ``[100, 10_000]``
    the first value becomes ~0.50 instead of 0.01. If every value is zero,
    all results are zero.
    """
    if any(v < 0 for v in values):
        raise ValueError("Values to normalize must be non-negative")
    if not values:
        return []

    denominator = math.log1p(max(values))
    if denominator == 0:
        return [0.0] * len(values)
    return [math.log1p(v) / denominator for v in values]


def count_recent_commits(
    commits: Iterable[CommitInfo], *, since: datetime, until: datetime
) -> dict[str, int]:
    """Count, per file, commits authored in the inclusive window [since, until]."""
    counts: dict[str, int] = {}
    for commit in commits:
        if since <= commit.authored_at <= until:
            for change in commit.files:
                counts[change.path] = counts.get(change.path, 0) + 1
    return counts


def compute_hotspots(
    commits: Sequence[CommitInfo],
    *,
    reference_time: datetime,
    recent_days: int = DEFAULT_RECENT_DAYS,
    weights: HotspotWeights = DEFAULT_WEIGHTS,
) -> list[Hotspot]:
    """Score every file touched by ``commits`` and rank them, highest first.

    Args:
        commits: Commits to analyze (any order).
        reference_time: "Now" for recent activity. It must be timezone-aware;
            passing it explicitly keeps results reproducible.
        recent_days: Size of the recent activity window, in days.
        weights: Metric weights; see :class:`HotspotWeights`.

    Ties are broken by commits, churn, recent commits (all descending) and
    finally by path, so the order is fully deterministic.
    """
    if reference_time.tzinfo is None:
        raise ValueError("reference_time must be timezone-aware")
    if recent_days < 1:
        raise ValueError(f"recent_days must be at least 1, got {recent_days}")

    stats = aggregate_file_stats(commits)
    if not stats:
        return []

    recent = count_recent_commits(
        commits,
        since=reference_time - timedelta(days=recent_days),
        until=reference_time,
    )
    recent_counts = [recent.get(s.path, 0) for s in stats]

    frequency_norm = normalize_log([s.commits for s in stats])
    churn_norm = normalize_log([s.churn for s in stats])
    recency_norm = normalize_log(recent_counts)

    hotspots = [
        Hotspot(
            path=s.path,
            commits=s.commits,
            churn=s.churn,
            recent_commits=recent_count,
            score=_score(f, c, r, weights),
            frequency_norm=f,
            churn_norm=c,
            recency_norm=r,
        )
        for s, recent_count, f, c, r in zip(
            stats, recent_counts, frequency_norm, churn_norm, recency_norm, strict=True
        )
    ]
    hotspots.sort(
        key=lambda h: (-h.score, -h.commits, -h.churn, -h.recent_commits, h.path)
    )
    return hotspots


def _score(f: float, c: float, r: float, weights: HotspotWeights) -> float:
    raw = 100 * (weights.frequency * f + weights.churn * c + weights.recency * r)
    # Guard against tiny floating point overshoots such as 100.00000000000001.
    return min(100.0, max(0.0, raw))
