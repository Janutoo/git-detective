"""Command-line interface for Git Detective.

This is the presentation layer: it parses arguments, calls the core logic and
renders results with Rich. It contains no Git or analysis logic of its own.
"""

from datetime import UTC, datetime, timedelta
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from git_detective import __version__
from git_detective.core import (
    DEFAULT_COMMIT_LIMIT,
    DEFAULT_RECENT_DAYS,
    GitReader,
    aggregate_file_stats,
    compute_hotspots,
)
from git_detective.exceptions import GitDetectiveError
from git_detective.models import CommitInfo, FileStats, Hotspot

DEFAULT_TOP = 10

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
) -> None:
    """Show the most frequently changed files in recent history."""
    commits, repo_root = _load_commits(path, limit)
    if not commits:
        console.print(f"[yellow]No commits found in[/] {repo_root}")
        return

    stats = aggregate_file_stats(commits)
    console.print(
        f"Analyzed [bold cyan]{len(commits)}[/] commits in [bold]{repo_root}[/] "
        f"([bold]{len(stats)}[/] files changed)"
    )
    console.print(_build_table(stats[:top]))


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
) -> None:
    """Rank files by Hotspot Score (frequency, churn and recent activity)."""
    commits, repo_root = _load_commits(path, limit)
    if not commits:
        console.print(f"[yellow]No commits found in[/] {repo_root}")
        return

    now = _utc_now()
    ranked = compute_hotspots(commits, reference_time=now, recent_days=recent_days)
    since = (now - timedelta(days=recent_days)).date().isoformat()
    console.print(
        f"Analyzed [bold cyan]{len(commits)}[/] commits in [bold]{repo_root}[/] "
        f"([bold]{len(ranked)}[/] files changed)"
    )
    console.print(f"Recent window: last {recent_days} days (since {since})")
    console.print(_build_hotspot_table(ranked[:top]))


def _utc_now() -> datetime:
    """Current time; a separate function so tests can freeze it."""
    return datetime.now(UTC)


def _load_commits(path: Path, limit: int) -> tuple[list[CommitInfo], Path]:
    """Read commits for a CLI command, turning known errors into exit code 1."""
    try:
        with GitReader(path) as reader:
            return reader.read_commits(limit=limit), reader.root
    except GitDetectiveError as exc:
        error_console.print(f"[bold red]Error:[/] {exc}")
        raise typer.Exit(code=1) from exc


def _build_hotspot_table(ranked: list[Hotspot]) -> Table:
    table = Table(
        title="Change hotspots",
        title_justify="left",
        caption="Hotspot Score (0-100) = relative change activity, "
        "not a probability of bugs.",
        caption_justify="left",
    )
    table.add_column("#", justify="right", style="dim")
    table.add_column("File", overflow="fold")
    table.add_column("Commits", justify="right")
    table.add_column("Churn", justify="right")
    table.add_column("Recent", justify="right")
    table.add_column("Hotspot Score", justify="right", style="bold")

    for rank, item in enumerate(ranked, start=1):
        table.add_row(
            str(rank),
            item.path,
            str(item.commits),
            str(item.churn),
            str(item.recent_commits),
            f"[{_score_style(item.score)}]{item.score:.1f}[/]",
        )
    return table


def _score_style(score: float) -> str:
    if score >= 70:
        return "red"
    if score >= 40:
        return "yellow"
    return "green"


def _build_table(stats: list[FileStats]) -> Table:
    table = Table(title="Most frequently changed files", title_justify="left")
    table.add_column("#", justify="right", style="dim")
    table.add_column("File", overflow="fold")
    table.add_column("Commits", justify="right", style="bold")
    table.add_column("Churn", justify="right")
    table.add_column("+", justify="right", style="green")
    table.add_column("-", justify="right", style="red")

    for rank, item in enumerate(stats, start=1):
        table.add_row(
            str(rank),
            item.path,
            str(item.commits),
            str(item.churn),
            str(item.added),
            str(item.deleted),
        )
    return table
