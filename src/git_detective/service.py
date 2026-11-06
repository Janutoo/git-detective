"""Run analyses: the orchestration shared by the CLI and the REST API.

Each ``run_*`` function reads the history, applies the exclude patterns, calls
one analysis algorithm and builds its report. Callers decide how to present the
result and whether to store it, so this module depends on neither FastAPI nor
SQLite.

It can take a long time: reading history starts one Git process per commit.
"""

from collections.abc import Sequence
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path, PurePath
from typing import Any

from git_detective.core import (
    DEFAULT_COMMIT_LIMIT,
    DEFAULT_MAX_FILES_PER_COMMIT,
    DEFAULT_MIN_COCHANGES,
    DEFAULT_RECENT_DAYS,
    DEFAULT_WEIGHTS,
    IGNORE_FILE_NAME,
    MAILMAP_FILE_NAME,
    AuthorSort,
    GitReader,
    PathFilter,
    aggregate_file_stats,
    compute_author_stats,
    compute_coupling,
    compute_hotspots,
    filter_commits,
    read_ignore_file,
    read_mailmap,
)
from git_detective.exceptions import InvalidRepositoryError
from git_detective.models import (
    AuthorStats,
    CommitInfo,
    CouplingResult,
    FileStats,
    Hotspot,
)
from git_detective.reporting import (
    Report,
    ReportContext,
    build_authors_report,
    build_coupling_report,
    build_hotspots_report,
    build_scan_report,
)

DEFAULT_TOP = 10
DEFAULT_COUPLING_TOP = 20
# History-oriented analyses (authors, coupling) need a longer window by default.
DEFAULT_HISTORY_LIMIT = 500


@dataclass(frozen=True, slots=True, kw_only=True)
class ScanConfig:
    limit: int = DEFAULT_COMMIT_LIMIT
    top: int = DEFAULT_TOP
    exclude: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True, kw_only=True)
class HotspotsConfig:
    limit: int = DEFAULT_COMMIT_LIMIT
    top: int = DEFAULT_TOP
    exclude: tuple[str, ...] = ()
    recent_days: int = DEFAULT_RECENT_DAYS


@dataclass(frozen=True, slots=True, kw_only=True)
class AuthorsConfig:
    limit: int = DEFAULT_HISTORY_LIMIT
    top: int = DEFAULT_TOP
    exclude: tuple[str, ...] = ()
    sort: AuthorSort = AuthorSort.COMMITS
    show_emails: bool = False


@dataclass(frozen=True, slots=True, kw_only=True)
class CouplingConfig:
    limit: int = DEFAULT_HISTORY_LIMIT
    top: int = DEFAULT_COUPLING_TOP
    exclude: tuple[str, ...] = ()
    min_cochanges: int = DEFAULT_MIN_COCHANGES
    max_files_per_commit: int = DEFAULT_MAX_FILES_PER_COMMIT


AnalysisConfig = ScanConfig | HotspotsConfig | AuthorsConfig | CouplingConfig


@dataclass(frozen=True, slots=True)
class AnalysisResult[T]:
    """One finished analysis: models for tables plus a ready-to-export report."""

    data: T
    report: Report
    root: Path
    head_sha: str | None  # None if the repository has no commits yet
    commits_analyzed: int
    exclude_patterns: tuple[str, ...]
    mailmap_used: bool = False


@dataclass(frozen=True, slots=True)
class _History:
    """Commits ready for analysis, after applying exclude patterns."""

    root: Path
    head_sha: str | None
    commits: list[CommitInfo]
    path_filter: PathFilter
    ignore_file_used: bool


def run_scan(
    path: Path,
    config: ScanConfig,
    *,
    now: datetime,
    label: PurePath | None = None,
    strict: bool = False,
) -> AnalysisResult[list[FileStats]]:
    """The most frequently changed files. See :func:`run_analysis` for arguments."""
    history = _load_history(path, config.limit, config.exclude, strict=strict)
    stats = aggregate_file_stats(history.commits)
    parameters = {"limit": config.limit, "top": config.top}
    context = _report_context(history, parameters, now, label)
    report = build_scan_report(stats, context, top=config.top)
    return _result(stats, report, history)


def run_hotspots(
    path: Path,
    config: HotspotsConfig,
    *,
    now: datetime,
    label: PurePath | None = None,
    strict: bool = False,
) -> AnalysisResult[list[Hotspot]]:
    """Files ranked by Hotspot Score. See :func:`run_analysis` for arguments."""
    history = _load_history(path, config.limit, config.exclude, strict=strict)
    ranked = compute_hotspots(
        history.commits, reference_time=now, recent_days=config.recent_days
    )
    parameters = {
        "limit": config.limit,
        "top": config.top,
        "recent_days": config.recent_days,
        "reference_time": now,
        "weights": asdict(DEFAULT_WEIGHTS),
    }
    context = _report_context(history, parameters, now, label)
    report = build_hotspots_report(ranked, context, top=config.top)
    return _result(ranked, report, history)


def run_authors(
    path: Path,
    config: AuthorsConfig,
    *,
    now: datetime,
    label: PurePath | None = None,
    strict: bool = False,
) -> AnalysisResult[list[AuthorStats]]:
    """Contributor activity. See :func:`run_analysis` for arguments."""
    history = _load_history(path, config.limit, config.exclude, strict=strict)
    mailmap = read_mailmap(history.root / MAILMAP_FILE_NAME)
    ranked = compute_author_stats(history.commits, mailmap=mailmap, sort_by=config.sort)
    parameters = {
        "limit": config.limit,
        "top": config.top,
        "sort": config.sort,
        "show_emails": config.show_emails,
        "mailmap": MAILMAP_FILE_NAME if mailmap else None,
    }
    context = _report_context(history, parameters, now, label)
    report = build_authors_report(
        ranked, context, top=config.top, show_emails=config.show_emails
    )
    return _result(ranked, report, history, mailmap_used=bool(mailmap))


def run_coupling(
    path: Path,
    config: CouplingConfig,
    *,
    now: datetime,
    label: PurePath | None = None,
    strict: bool = False,
) -> AnalysisResult[CouplingResult]:
    """File pairs that change together. See :func:`run_analysis` for arguments."""
    history = _load_history(path, config.limit, config.exclude, strict=strict)
    coupling = compute_coupling(
        history.commits,
        min_cochanges=config.min_cochanges,
        max_files_per_commit=config.max_files_per_commit,
    )
    parameters = {
        "limit": config.limit,
        "top": config.top,
        "min_cochanges": config.min_cochanges,
        "max_files_per_commit": config.max_files_per_commit,
    }
    context = _report_context(history, parameters, now, label)
    report = build_coupling_report(coupling, context, top=config.top)
    return _result(coupling, report, history)


def run_analysis(
    path: Path,
    config: AnalysisConfig,
    *,
    now: datetime,
    label: PurePath | None = None,
    strict: bool = False,
) -> AnalysisResult[Any]:
    """Run the analysis selected by the type of ``config``.

    Args:
        path: The repository or, unless ``strict``, any directory inside it.
        config: What to analyze and how.
        now: Timezone-aware time of the analysis; it is the report's timestamp
            and the reference point for recent activity.
        label: Shown in the report instead of the repository's absolute path.
        strict: For callers that must not trust the repository location:
            ``path`` has to be the repository's top-level directory, and Git
            must not download missing objects.

    Raises:
        GitDetectiveError: If the repository or its configuration files
            cannot be read.
    """
    match config:
        case ScanConfig():
            return run_scan(path, config, now=now, label=label, strict=strict)
        case HotspotsConfig():
            return run_hotspots(path, config, now=now, label=label, strict=strict)
        case AuthorsConfig():
            return run_authors(path, config, now=now, label=label, strict=strict)
        case CouplingConfig():
            return run_coupling(path, config, now=now, label=label, strict=strict)
    raise TypeError(f"Unknown analysis config: {type(config).__name__}")


def _load_history(
    path: Path, limit: int, exclude: Sequence[str], *, strict: bool
) -> _History:
    """Read commits and drop files matched by the ignore file or ``exclude``."""
    with GitReader(path, allow_lazy_fetch=not strict) as reader:
        root = reader.root
        if strict and root.resolve() != Path(path).resolve():
            raise InvalidRepositoryError(f"Not a repository root: {path}")
        # Read HEAD once and walk from that hash, so the stored hash always
        # describes the analyzed commits even if someone commits meanwhile.
        head_sha = reader.head_sha
        commits = reader.read_commits(limit=limit, rev=head_sha or "HEAD")

    file_patterns = read_ignore_file(root / IGNORE_FILE_NAME)
    path_filter = PathFilter([*file_patterns, *exclude])
    return _History(
        root=root,
        head_sha=head_sha,
        commits=filter_commits(commits, path_filter),
        path_filter=path_filter,
        ignore_file_used=bool(file_patterns),
    )


def _report_context(
    history: _History,
    parameters: dict[str, Any],
    now: datetime,
    label: PurePath | None,
) -> ReportContext:
    return ReportContext(
        repository=label or history.root,
        generated_at=now,
        commits=history.commits,
        parameters={
            **parameters,
            "exclude": list(history.path_filter.patterns),
            "ignore_file": IGNORE_FILE_NAME if history.ignore_file_used else None,
        },
    )


def _result[T](
    data: T, report: Report, history: _History, *, mailmap_used: bool = False
) -> AnalysisResult[T]:
    return AnalysisResult(
        data=data,
        report=report,
        root=history.root,
        head_sha=history.head_sha,
        commits_analyzed=len(history.commits),
        exclude_patterns=history.path_filter.patterns,
        mailmap_used=mailmap_used,
    )
