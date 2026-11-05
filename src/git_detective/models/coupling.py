"""File coupling: pairs of files that tend to change in the same commits."""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class FileCoupling:
    """How often two files changed together.

    Co-change is a hint worth investigating (hidden dependencies, duplicated
    logic, code and its tests), not proof that the files depend on each other.
    """

    file_a: str  # always sorts before file_b, so each pair has one spelling
    file_b: str
    cochanges: int  # commits that changed both files
    commits_a: int  # commits that changed file_a
    commits_b: int  # commits that changed file_b

    @property
    def jaccard(self) -> float:
        """Share of commits touching either file that touched both (0-1]."""
        return self.cochanges / (self.commits_a + self.commits_b - self.cochanges)


@dataclass(frozen=True, slots=True)
class CouplingResult:
    """Ranked file pairs plus how many commits the analysis used."""

    pairs: tuple[FileCoupling, ...]
    commits_considered: int
    commits_skipped: int  # commits with more files than max_files_per_commit
