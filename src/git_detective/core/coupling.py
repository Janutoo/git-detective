"""Find pairs of files that frequently change in the same commits.

For files A and B:

* ``cochanges`` - commits that changed both files,
* ``Jaccard = cochanges / (commits_A + commits_B - cochanges)`` - the share of
  commits touching either file that touched both (1.0 = always together).

**Noise control.** Very large commits (mass refactors, reformatting,
dependency bumps, vendored code) would link every file to every other file.
A commit that changes more than ``max_files_per_commit`` files is therefore
skipped *entirely* - it counts neither towards pairs nor towards per-file
commit counts - so Jaccard is always computed over one consistent set of
commits. ``min_cochanges`` drops pairs seen together too rarely to mean
anything, and exclude patterns (applied before this module) remove lockfiles
and generated files. The limit also bounds the quadratic number of pairs per
commit.

Co-change does not prove that two modules depend on each other; treat strong
pairs as hints worth a closer look. This module does not depend on Git or
the CLI.
"""

from collections import Counter
from collections.abc import Iterable
from itertools import combinations

from git_detective.models import CommitInfo, CouplingResult, FileCoupling

DEFAULT_MIN_COCHANGES = 3
DEFAULT_MAX_FILES_PER_COMMIT = 50


def compute_coupling(
    commits: Iterable[CommitInfo],
    *,
    min_cochanges: int = DEFAULT_MIN_COCHANGES,
    max_files_per_commit: int = DEFAULT_MAX_FILES_PER_COMMIT,
) -> CouplingResult:
    """Count co-changes for every file pair and rank the pairs.

    Pairs are ranked by Jaccard similarity, then by number of co-changes,
    then alphabetically by path, so the order is deterministic.
    """
    if min_cochanges < 1:
        raise ValueError(f"min_cochanges must be at least 1, got {min_cochanges}")
    if max_files_per_commit < 2:
        raise ValueError(
            f"max_files_per_commit must be at least 2, got {max_files_per_commit}"
        )

    file_commits: Counter[str] = Counter()
    pair_counts: Counter[tuple[str, str]] = Counter()
    considered = skipped = 0

    for commit in commits:
        files = sorted({change.path for change in commit.files})
        if len(files) > max_files_per_commit:
            skipped += 1
            continue
        considered += 1
        file_commits.update(files)
        # `files` is sorted, so every pair comes out as (smaller, larger).
        pair_counts.update(combinations(files, 2))

    pairs = [
        FileCoupling(
            file_a=a,
            file_b=b,
            cochanges=count,
            commits_a=file_commits[a],
            commits_b=file_commits[b],
        )
        for (a, b), count in pair_counts.items()
        if count >= min_cochanges
    ]
    pairs.sort(key=lambda p: (-p.jaccard, -p.cochanges, p.file_a, p.file_b))
    return CouplingResult(
        pairs=tuple(pairs), commits_considered=considered, commits_skipped=skipped
    )
