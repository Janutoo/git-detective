"""Hotspot ranking result for a single file."""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Hotspot:
    """A file ranked by how actively and heavily it has been changed.

    ``score`` is a relative activity indicator in the range 0-100. It shows
    where change is concentrated in the analyzed history; it is *not* the
    probability that the file contains a bug.

    The ``*_norm`` fields are the normalized (0-1) metrics the score was
    computed from, kept so reports can explain each score.
    """

    path: str
    commits: int
    churn: int
    recent_commits: int
    score: float
    frequency_norm: float
    churn_norm: float
    recency_norm: float
