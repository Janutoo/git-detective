"""Command-line interface for Git Detective.

This is the presentation layer: it parses arguments, calls the core logic and
renders results with Rich (tables) or the reporting module (JSON/Markdown).
It contains no Git or analysis logic of its own.
"""

import ipaddress
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from pathlib import Path
from typing import NoReturn

import typer
from rich.console import Console
from rich.markup import escape

from git_detective import __version__
from git_detective.core import (
    DEFAULT_COMMIT_LIMIT,
    DEFAULT_MAX_FILES_PER_COMMIT,
    DEFAULT_MIN_COCHANGES,
    DEFAULT_RECENT_DAYS,
    IGNORE_FILE_NAME,
    MAILMAP_FILE_NAME,
    AuthorSort,
)
from git_detective.exceptions import GitDetectiveError
from git_detective.reporting import Report, to_json, to_markdown
from git_detective.service import (
    DEFAULT_COUPLING_TOP,
    DEFAULT_HISTORY_LIMIT,
    DEFAULT_TOP,
    AnalysisResult,
    AuthorsConfig,
    CouplingConfig,
    HotspotsConfig,
    ScanConfig,
    run_authors,
    run_coupling,
    run_hotspots,
    run_scan,
)
from git_detective.tables import (
    build_authors_table,
    build_coupling_table,
    build_hotspot_table,
    build_scan_table,
)

LOCAL_HOST = "127.0.0.1"
DEFAULT_PORT = 8000


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
    config = ScanConfig(limit=limit, top=top, exclude=tuple(exclude or ()))
    result = _run(run_scan, path, config, _utc_now())

    if output_format is OutputFormat.TABLE:
        if _report_no_commits(result):
            return
        _print_header(result, f"[bold]{len(result.data)}[/] files changed")
        console.print(build_scan_table(result.data[:top]))
        return

    _emit(result.report, output_format, output)


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
    now = _utc_now()
    config = HotspotsConfig(
        limit=limit, top=top, exclude=tuple(exclude or ()), recent_days=recent_days
    )
    result = _run(run_hotspots, path, config, now)

    if output_format is OutputFormat.TABLE:
        if _report_no_commits(result):
            return
        since = (now - timedelta(days=recent_days)).date().isoformat()
        _print_header(result, f"[bold]{len(result.data)}[/] files changed")
        console.print(f"Recent window: last {recent_days} days (since {since})")
        console.print(build_hotspot_table(result.data[:top]))
        return

    _emit(result.report, output_format, output)


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
    config = AuthorsConfig(
        limit=limit,
        top=top,
        exclude=tuple(exclude or ()),
        sort=sort,
        show_emails=show_emails,
    )
    result = _run(run_authors, path, config, _utc_now())

    if output_format is OutputFormat.TABLE:
        if _report_no_commits(result):
            return
        _print_header(result, f"[bold]{len(result.data)}[/] authors")
        if result.mailmap_used:
            console.print(f"[dim]Aliases resolved with {MAILMAP_FILE_NAME}[/]")
        console.print(build_authors_table(result.data[:top], show_emails=show_emails))
        return

    _emit(result.report, output_format, output)


@app.command()
def coupling(
    path: Path = PATH_ARGUMENT,
    limit: int = typer.Option(
        DEFAULT_HISTORY_LIMIT, "--limit", "-l", min=1, help="Max commits to analyze."
    ),
    top: int = typer.Option(
        DEFAULT_COUPLING_TOP,
        "--top",
        "-t",
        min=1,
        help="Number of file pairs to display.",
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
    config = CouplingConfig(
        limit=limit,
        top=top,
        exclude=tuple(exclude or ()),
        min_cochanges=min_cochanges,
        max_files_per_commit=max_files_per_commit,
    )
    result = _run(run_coupling, path, config, _utc_now())

    if output_format is OutputFormat.TABLE:
        if _report_no_commits(result):
            return
        pairs = result.data
        _print_header(result, f"[bold]{len(pairs.pairs)}[/] coupled pairs")
        console.print(
            f"Considered {pairs.commits_considered} commits; skipped "
            f"{pairs.commits_skipped} with more than {max_files_per_commit} files. "
            f"Minimum co-changes: {min_cochanges}."
        )
        console.print(build_coupling_table(pairs.pairs[:top]))
        return

    _emit(result.report, output_format, output)


@app.command()
def serve(
    config: Path = typer.Option(
        ...,
        "--config",
        "-c",
        envvar="GIT_DETECTIVE_CONFIG",
        help="TOML file listing the repositories the API may read.",
    ),
    host: str = typer.Option(LOCAL_HOST, "--host", help="Address to listen on."),
    port: int = typer.Option(
        DEFAULT_PORT, "--port", "-p", min=1, max=65535, help="Port to listen on."
    ),
    allow_remote: bool = typer.Option(
        False,
        "--allow-remote",
        help="Allow listening on a non-loopback address. "
        "The API has no authentication: anyone who can reach it can use it.",
    ),
) -> None:
    """Start the REST API server (requires the "api" extra)."""
    try:
        _check_bind_address(host, allow_remote=allow_remote)
    except ValueError as exc:
        _fail(exc)

    try:
        import uvicorn

        from git_detective.api.app import LOOPBACK_HOSTS, create_app
        from git_detective.api.config import load_settings
    except ImportError:
        _fail(
            "The API server needs extra dependencies. "
            'Install them with: pip install "git-detective[api]"'
        )

    if allow_remote:
        error_console.print(
            f"[bold yellow]Warning:[/] listening on {escape(host)} without "
            "authentication. Anyone who can reach this address can use the API."
        )
    try:
        application = create_app(
            load_settings(config),
            allowed_hosts=sorted({*LOOPBACK_HOSTS, host}),
        )
    except GitDetectiveError as exc:
        _fail(exc)
    uvicorn.run(application, host=host, port=port)


def _check_bind_address(host: str, *, allow_remote: bool) -> None:
    """Refuse addresses that would expose the API without an explicit request.

    Raises:
        ValueError: If the server must not listen on ``host``.
    """
    if host == "localhost":
        return
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        # A host name, which could resolve to anything.
        address = None

    if address is None and not host.strip():
        raise ValueError("--host must not be empty")
    if address is not None and address.version == 6:
        raise ValueError("IPv6 addresses are not supported; use an IPv4 address")
    if address is not None and address.is_unspecified:
        raise ValueError(
            f"Refusing to listen on all network interfaces ({host}); "
            "name one specific address"
        )
    if (address is None or not address.is_loopback) and not allow_remote:
        raise ValueError(
            f"{host} is not a loopback address and the API has no authentication; "
            "pass --allow-remote if this is really what you want"
        )


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


def _run[C, T](
    run: Callable[..., AnalysisResult[T]], path: Path, config: C, now: datetime
) -> AnalysisResult[T]:
    """Run an analysis, turning known errors into exit code 1."""
    try:
        return run(path, config, now=now)
    except GitDetectiveError as exc:
        _fail(exc)


def _report_no_commits(result: AnalysisResult) -> bool:
    if result.commits_analyzed:
        return False
    console.print(f"[yellow]No commits found in[/] {escape(str(result.root))}")
    return True


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


def _print_header(result: AnalysisResult, detail: str) -> None:
    """Print "Analyzed N commits in <repo> (<detail>)" and active exclusions."""
    console.print(
        f"Analyzed [bold cyan]{result.commits_analyzed}[/] commits in "
        f"[bold]{escape(str(result.root))}[/] ({detail})"
    )
    if result.exclude_patterns:
        patterns = ", ".join(result.exclude_patterns)
        console.print(f"[dim]Excluding: {escape(patterns)}[/]")
