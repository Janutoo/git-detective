"""Command-line interface for Git Detective.

This is the presentation layer: it parses arguments, calls the core logic and
renders results with Rich (tables) or the reporting module (JSON/Markdown).
It contains no Git or analysis logic of its own.
"""

from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from pathlib import Path
from typing import Any, NoReturn

import typer
from rich.console import Console
from rich.markup import escape

from git_detective import __version__
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
    Mailmap,
    PathFilter,
    aggregate_file_stats,
    compute_author_stats,
    compute_coupling,
    compute_hotspots,
    filter_commits,
    read_ignore_file,
    read_mailmap,
)
from git_detective.exceptions import GitDetectiveError
from git_detective.models import CommitInfo
from git_detective.reporting import (
    Report,
    ReportContext,
    build_authors_report,
    build_coupling_report,
    build_hotspots_report,
    build_scan_report,
    to_json,
    to_markdown,
)
from git_detective.tables import (
    build_authors_table,
    build_coupling_table,
    build_hotspot_table,
    build_scan_table,
)

DEFAULT_TOP = 10
# History-oriented analyses (authors, coupling) need a longer window by default.
DEFAULT_HISTORY_LIMIT = 500


class OutputFormat(StrEnum):
    TABLE = "table"
    JSON = "json"
    MARKDOWN = "markdown"


# Shared argument/option definitions so all commands behave the same way.
PATH_ARGUMENT = typer.Argument(
    Path("."), help="Path to a local Git repository (or any directory in it)."
)
LIMIT_OPTION = typer.Option(
    DEFAULT_COMMIT_LIMIT, "--limit", "-l", min=1, help="Max commits to analyze."
)
TOP_OPTION = typer.Option(
    DEFAULT_TOP, "--top", "-t", min=1, help="Number of files to display."
)
EXCLUDE_OPTION = typer.Option(
    None,
    "--exclude",
    "-e",
    metavar="PATTERN",
    help=f"Skip files matching a .gitignore-style pattern. Repeatable. "
    f"Patterns from {IGNORE_FILE_NAME} in the repository root are applied first.",
)
FORMAT_OPTION = typer.Option(
    OutputFormat.TABLE, "--format", "-f", case_sensitive=False, help="Output format."
)
OUTPUT_OPTION = typer.Option(
    None,
    "--output",
    "-o",
    help="Write the report to this file instead of stdout (json/markdown only).",
)

app = typer.Typer(
    help="Analyze Git history to find code hotspots and contributor activity.",
    no_args_is_help=True,
    add_completion=False,
)
console = Console()
error_console = Console(stderr=True)


@dataclass(frozen=True, slots=True)
class _Analysis:
    """Commits ready for analysis, after applying exclude patterns."""

    root: Path
    commits: list[CommitInfo]
    path_filter: PathFilter
    ignore_file_used: bool


def _version_callback(value: bool) -> None:
    if value:
        console.print(f"git-detective {__version__}")
        raise typer.Exit()


@app.callback()
def main(
    version: bool = typer.Option(
        False,
        "--version",
        help="Show the version and exit.",
        callback=_version_callback,
        is_eager=True,
    ),
) -> None:
    """Git Detective - investigate your repository history."""


@app.command()
def scan(
    path: Path = PATH_ARGUMENT,
    limit: int = LIMIT_OPTION,
    top: int = TOP_OPTION,
    exclude: list[str] | None = EXCLUDE_OPTION,
    output_format: OutputFormat = FORMAT_OPTION,
    output: Path | None = OUTPUT_OPTION,
) -> None:
    """Show the most frequently changed files in recent history."""
    _check_output_option(output_format, output)
    analysis = _prepare(path, limit, exclude)
    stats = aggregate_file_stats(analysis.commits)

    if output_format is OutputFormat.TABLE:
        if not analysis.commits:
            console.print(
                f"[yellow]No commits found in[/] {escape(str(analysis.root))}"
            )
            return
        _print_header(analysis, f"[bold]{len(stats)}[/] files changed")
        console.print(build_scan_table(stats[:top]))
        return

    context = _report_context(analysis, {"limit": limit, "top": top})
    _emit(build_scan_report(stats, context, top=top), output_format, output)


@app.command()
def hotspots(
    path: Path = PATH_ARGUMENT,
    limit: int = LIMIT_OPTION,
    top: int = TOP_OPTION,
    recent_days: int = typer.Option(
        DEFAULT_RECENT_DAYS,
        "--recent-days",
        "-r",
        min=1,
        help="Size of the recent activity window, in days.",
    ),
    exclude: list[str] | None = EXCLUDE_OPTION,
    output_format: OutputFormat = FORMAT_OPTION,
    output: Path | None = OUTPUT_OPTION,
) -> None:
    """Rank files by Hotspot Score (frequency, churn and recent activity)."""
    _check_output_option(output_format, output)
    analysis = _prepare(path, limit, exclude)
    now = _utc_now()
    ranked = compute_hotspots(
        analysis.commits, reference_time=now, recent_days=recent_days
    )

    if output_format is OutputFormat.TABLE:
        if not analysis.commits:
            console.print(
                f"[yellow]No commits found in[/] {escape(str(analysis.root))}"
            )
            return
        since = (now - timedelta(days=recent_days)).date().isoformat()
        _print_header(analysis, f"[bold]{len(ranked)}[/] files changed")
        console.print(f"Recent window: last {recent_days} days (since {since})")
        console.print(build_hotspot_table(ranked[:top]))
        return

    parameters = {
        "limit": limit,
        "top": top,
        "recent_days": recent_days,
        "reference_time": now,
        "weights": asdict(DEFAULT_WEIGHTS),
    }
    context = _report_context(analysis, parameters, generated_at=now)
    _emit(build_hotspots_report(ranked, context, top=top), output_format, output)


@app.command()
def authors(
    path: Path = PATH_ARGUMENT,
    limit: int = typer.Option(
        DEFAULT_HISTORY_LIMIT, "--limit", "-l", min=1, help="Max commits to analyze."
    ),
    top: int = typer.Option(
        DEFAULT_TOP, "--top", "-t", min=1, help="Number of authors to display."
    ),
    sort: AuthorSort = typer.Option(
        AuthorSort.COMMITS,
        "--sort",
        "-s",
        case_sensitive=False,
        help="Rank authors by commit count or by churn.",
    ),
    show_emails: bool = typer.Option(
        False,
        "--show-emails",
        help="Include author e-mail addresses (hidden by default for privacy).",
    ),
    exclude: list[str] | None = EXCLUDE_OPTION,
    output_format: OutputFormat = FORMAT_OPTION,
    output: Path | None = OUTPUT_OPTION,
) -> None:
    """Show contributor activity: commits, churn, files and active period."""
    _check_output_option(output_format, output)
    analysis = _prepare(path, limit, exclude)
    mailmap = _load_mailmap(analysis.root)
    ranked = compute_author_stats(analysis.commits, mailmap=mailmap, sort_by=sort)

    if output_format is OutputFormat.TABLE:
        if not analysis.commits:
            console.print(
                f"[yellow]No commits found in[/] {escape(str(analysis.root))}"
            )
            return
        _print_header(analysis, f"[bold]{len(ranked)}[/] authors")
        if mailmap:
            console.print(f"[dim]Aliases resolved with {MAILMAP_FILE_NAME}[/]")
        console.print(build_authors_table(ranked[:top], show_emails=show_emails))
        return

    parameters = {
        "limit": limit,
        "top": top,
        "sort": sort,
        "show_emails": show_emails,
        "mailmap": MAILMAP_FILE_NAME if mailmap else None,
    }
    context = _report_context(analysis, parameters)
    report = build_authors_report(ranked, context, top=top, show_emails=show_emails)
    _emit(report, output_format, output)


@app.command()
def coupling(
    path: Path = PATH_ARGUMENT,
    limit: int = typer.Option(
        DEFAULT_HISTORY_LIMIT, "--limit", "-l", min=1, help="Max commits to analyze."
    ),
    top: int = typer.Option(
        20, "--top", "-t", min=1, help="Number of file pairs to display."
    ),
    min_cochanges: int = typer.Option(
        DEFAULT_MIN_COCHANGES,
        "--min-cochanges",
        min=1,
        help="Ignore pairs changed together fewer times than this.",
    ),
    max_files_per_commit: int = typer.Option(
        DEFAULT_MAX_FILES_PER_COMMIT,
        "--max-files-per-commit",
        min=2,
        help="Skip commits that change more files than this (bulk changes).",
    ),
    exclude: list[str] | None = EXCLUDE_OPTION,
    output_format: OutputFormat = FORMAT_OPTION,
    output: Path | None = OUTPUT_OPTION,
) -> None:
    """Find pairs of files that frequently change in the same commits."""
    _check_output_option(output_format, output)
    analysis = _prepare(path, limit, exclude)
    result = compute_coupling(
        analysis.commits,
        min_cochanges=min_cochanges,
        max_files_per_commit=max_files_per_commit,
    )

    if output_format is OutputFormat.TABLE:
        if not analysis.commits:
            console.print(
                f"[yellow]No commits found in[/] {escape(str(analysis.root))}"
            )
            return
        _print_header(analysis, f"[bold]{len(result.pairs)}[/] coupled pairs")
        console.print(
            f"Considered {result.commits_considered} commits; skipped "
            f"{result.commits_skipped} with more than {max_files_per_commit} files. "
            f"Minimum co-changes: {min_cochanges}."
        )
        console.print(build_coupling_table(result.pairs[:top]))
        return

    parameters = {
        "limit": limit,
        "top": top,
        "min_cochanges": min_cochanges,
        "max_files_per_commit": max_files_per_commit,
    }
    context = _report_context(analysis, parameters)
    _emit(build_coupling_report(result, context, top=top), output_format, output)


def _utc_now() -> datetime:
    """Current time (whole seconds); a separate function so tests can freeze it."""
    return datetime.now(UTC).replace(microsecond=0)


def _fail(message: object) -> NoReturn:
    """Print an error to stderr and exit with code 1."""
    error_console.print(f"[bold red]Error:[/] {escape(str(message))}")
    raise typer.Exit(code=1)


def _check_output_option(output_format: OutputFormat, output: Path | None) -> None:
    if output is not None and output_format is OutputFormat.TABLE:
        raise typer.BadParameter(
            "requires --format json or --format markdown", param_hint="'--output'"
        )


def _load_commits(path: Path, limit: int) -> tuple[list[CommitInfo], Path]:
    """Read commits for a CLI command, turning known errors into exit code 1."""
    try:
        with GitReader(path) as reader:
            return reader.read_commits(limit=limit), reader.root
    except GitDetectiveError as exc:
        _fail(exc)


def _prepare(path: Path, limit: int, exclude: list[str] | None) -> _Analysis:
    """Load commits and drop files matched by the ignore file or --exclude."""
    commits, root = _load_commits(path, limit)
    ignore_file = root / IGNORE_FILE_NAME
    try:
        file_patterns = read_ignore_file(ignore_file)
        path_filter = PathFilter([*file_patterns, *(exclude or [])])
    except GitDetectiveError as exc:
        _fail(exc)
    return _Analysis(
        root=root,
        commits=filter_commits(commits, path_filter),
        path_filter=path_filter,
        ignore_file_used=bool(file_patterns),
    )


def _load_mailmap(root: Path) -> Mailmap:
    try:
        return read_mailmap(root / MAILMAP_FILE_NAME)
    except GitDetectiveError as exc:
        _fail(exc)


def _report_context(
    analysis: _Analysis,
    parameters: dict[str, Any],
    *,
    generated_at: datetime | None = None,
) -> ReportContext:
    return ReportContext(
        repository=analysis.root,
        generated_at=generated_at or _utc_now(),
        commits=analysis.commits,
        parameters={
            **parameters,
            "exclude": list(analysis.path_filter.patterns),
            "ignore_file": IGNORE_FILE_NAME if analysis.ignore_file_used else None,
        },
    )


def _emit(report: Report, output_format: OutputFormat, output: Path | None) -> None:
    """Write a JSON/Markdown report to stdout or to a file.

    Plain ``typer.echo`` is used instead of Rich so the output is never
    wrapped, colored or interpreted as markup.
    """
    text = (
        to_json(report) if output_format is OutputFormat.JSON else to_markdown(report)
    )
    if output is None:
        typer.echo(text, nl=False)
        return
    try:
        output.write_text(text, encoding="utf-8")
    except OSError as exc:
        _fail(f"Cannot write report to {output}: {exc.strerror or exc}")
    error_console.print(f"Report written to {escape(str(output))}")


def _print_header(analysis: _Analysis, detail: str) -> None:
    """Print "Analyzed N commits in <repo> (<detail>)" and active exclusions."""
    console.print(
        f"Analyzed [bold cyan]{len(analysis.commits)}[/] commits in "
        f"[bold]{escape(str(analysis.root))}[/] ({detail})"
    )
    if analysis.path_filter:
        patterns = ", ".join(analysis.path_filter.patterns)
        console.print(f"[dim]Excluding: {escape(patterns)}[/]")
