"""Hotspot ranking result for a single file."""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Hotspot:
    """A file ranked by how actively and heavily it has been changed.

    ``score`` is a relative activity indicator in the range 0-100. It shows
    where change is concentrated in the analyzed history; it is *not* the
    probability that the file contains a bug.
    """

    path: str
    commits: int
    churn: int
    recent_commits: int
    score: float
